"""Supervised JSON workers with private environments and process-group cleanup."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import socket
import struct
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

MAX_RESULT_BYTES = 16 * 1024 * 1024
GRACEFUL_SHUTDOWN_SECONDS = 90.0
# The worker finds its end of the parent's channel under this name, when the parent serves one.
CHANNEL_FD_ENV = "ALK_WORKER_CHANNEL_FD"
# A 1 MiB workspace file, JSON-escaped, stays under it.
MAX_CHANNEL_MESSAGE_BYTES = 8 * 1024 * 1024
_LENGTH = struct.Struct(">I")


class IsolatedWorkerError(RuntimeError):
    """The worker exited without a valid result."""


# -- one channel message: a 4-byte length, then a JSON object --------------------------------


def encode_message(message: Mapping[str, Any]) -> bytes:
    return json.dumps(message, default=str).encode("utf-8")


def _decode_message(body: bytes) -> dict[str, Any]:
    message = json.loads(body)
    if not isinstance(message, dict):
        raise ValueError("a channel message must be an object")
    return message


def send_message(sock: socket.socket, body: bytes) -> None:
    sock.sendall(_LENGTH.pack(len(body)) + body)


def _receive_exactly(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise EOFError("the channel closed")
        chunks.extend(chunk)
    return bytes(chunks)


def receive_message(sock: socket.socket, *, limit: int) -> dict[str, Any]:
    """The next message; EOFError when the other end closed, ValueError when it broke the frame."""
    (size,) = _LENGTH.unpack(_receive_exactly(sock, _LENGTH.size))
    if size > limit:
        raise ValueError("channel message exceeds the limit")
    return _decode_message(_receive_exactly(sock, size))


async def _serve(
    sock: socket.socket, serve: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    """Answer the worker's requests in order until its end closes or breaks the frame."""
    reader, writer = await asyncio.open_unix_connection(sock=sock)
    try:
        while True:
            try:
                (size,) = _LENGTH.unpack(await reader.readexactly(_LENGTH.size))
                if size > MAX_CHANNEL_MESSAGE_BYTES:
                    return
                request = _decode_message(await reader.readexactly(size))
            except (asyncio.IncompleteReadError, ValueError, OSError, RecursionError):
                # A worker that closed or broke the frame loses its channel, not its phase.
                return
            try:
                reply = encode_message(await asyncio.to_thread(serve, request))
            except Exception:
                # The worker sees its channel close; the cause stays in this process's log.
                logging.getLogger(__name__).exception("isolated_worker_channel_failed")
                return
            if len(reply) > MAX_CHANNEL_MESSAGE_BYTES:
                logging.getLogger(__name__).error(
                    "isolated_worker_channel_reply_too_large"
                )
                return
            try:
                writer.write(_LENGTH.pack(len(reply)) + reply)
                await writer.drain()
            except OSError:
                return
    finally:
        writer.close()


async def _finish(task: asyncio.Task) -> bool:
    canceled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled = True
    task.result()
    return canceled


def run_worker(main: Callable[[], Awaitable[None]]) -> None:
    """Translate termination into one cancellation so async cleanup can finish."""

    logging.basicConfig(
        level=logging.WARNING,
        format=f"%(asctime)s %(levelname)s job={os.environ.get('ALK_LOG_JOB_ID', '-')} %(name)s: %(message)s",
    )
    logging.getLogger("fi").setLevel(logging.INFO)

    async def supervised() -> None:
        task = asyncio.create_task(main())
        loop = asyncio.get_running_loop()
        termination_requested = False

        def cancel_once() -> None:
            nonlocal termination_requested
            if termination_requested:
                return
            termination_requested = True
            if not getattr(task, "cancelling", lambda: 0)():
                task.cancel()

        loop.add_signal_handler(signal.SIGTERM, cancel_once)
        try:
            await task
        finally:
            loop.remove_signal_handler(signal.SIGTERM)

    asyncio.run(supervised())


def _job_id() -> str:
    for handler in logging.getLogger().handlers:
        for log_filter in handler.filters:
            if isinstance(getattr(log_filter, "job_id", None), str):
                return log_filter.job_id
    return "-"


async def _reap(
    process: asyncio.subprocess.Process,
    *,
    grace_seconds: float = GRACEFUL_SHUTDOWN_SECONDS,
) -> None:
    # Leave descendants alive while the leader finalizes remote calls and rooms.
    try:
        process.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=grace_seconds)
    except asyncio.TimeoutError:
        pass
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


async def run_json_worker(
    module: str,
    payload: Mapping[str, Any],
    *,
    environ: Mapping[str, str],
    work_directory: Path,
    suppress_output: bool = False,
    pass_fds: tuple[int, ...] = (),
    serve: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a fresh interpreter; cancellation waits for process-group termination.

    The worker inherits `pass_fds`; the caller keeps its own copies and closes them.
    With ``serve``, the worker also gets a channel to this process (its descriptor is named by
    ``CHANNEL_FD_ENV``), and ``serve`` answers each of its requests while it runs.
    """
    if os.name != "posix":
        raise IsolatedWorkerError("isolated workers require POSIX process groups")
    work_directory.mkdir(parents=True, exist_ok=True)
    parent_end, child_end = socket.socketpair() if serve is not None else (None, None)
    try:
        return await _run_json_worker(
            module,
            payload,
            environ,
            work_directory,
            suppress_output,
            pass_fds,
            serve,
            parent_end,
            child_end,
        )
    finally:
        for end in (parent_end, child_end):
            if end is not None:
                end.close()


async def _run_json_worker(
    module,
    payload,
    environ,
    work_directory,
    suppress_output,
    pass_fds,
    serve,
    parent_end,
    child_end,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="worker-", dir=work_directory) as directory:
        root = Path(directory)
        result_path = root / "result.json"
        child_env = dict(environ)
        # Runtime scratch is invocation-private, including libraries using tempfile.
        child_env.update(
            TMPDIR=str(root),
            TMP=str(root),
            TEMP=str(root),
            HOME=str(root),
            XDG_CACHE_HOME=str(root / "cache"),
        )
        # The worker logs into this process's stream, tagged with the same job id.
        child_env.setdefault("ALK_LOG_JOB_ID", _job_id())
        # It imports the same harness code as this process, wherever that code was loaded from.
        if os.environ.get("PYTHONPATH"):
            child_env.setdefault("PYTHONPATH", os.environ["PYTHONPATH"])
        if child_end is not None:
            child_env[CHANNEL_FD_ENV] = str(child_end.fileno())
        creation = asyncio.create_task(
            asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                module,
                str(result_path),
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL if suppress_output else None,
                stderr=asyncio.subprocess.DEVNULL if suppress_output else None,
                env=child_env,
                cwd=root,
                start_new_session=True,
                pass_fds=(
                    *pass_fds,
                    *(() if child_end is None else (child_end.fileno(),)),
                ),
            )
        )
        try:
            canceled = await _finish(creation)
        finally:
            if child_end is not None:
                # The worker holds its own copy; this one would keep the channel open after it.
                child_end.close()
        process = creation.result()
        channel = (
            None if serve is None else asyncio.create_task(_serve(parent_end, serve))
        )
        try:
            if canceled:
                raise asyncio.CancelledError
            await process.communicate(json.dumps(payload).encode())
            if process.returncode != 0:
                logging.getLogger(__name__).error(
                    "isolated_worker_failed module=%s exit_status=%s result_present=%s",
                    module, process.returncode, result_path.is_file(),
                )
                raise IsolatedWorkerError(
                    f"isolated worker exited with status {process.returncode}"
                )
            if (
                result_path.is_symlink()
                or not result_path.is_file()
                or result_path.stat().st_size > MAX_RESULT_BYTES
            ):
                raise IsolatedWorkerError("isolated worker result missing or oversized")
            try:
                result = json.loads(result_path.read_text())
            except (ValueError, OSError) as exc:
                raise IsolatedWorkerError("isolated worker result invalid") from exc
            if not isinstance(result, dict):
                raise IsolatedWorkerError("isolated worker result must be an object")
            return result
        finally:
            cleanup = asyncio.create_task(_reap(process))
            # Do not allow repeated cancellation to release a world before cleanup.
            canceled = await _finish(cleanup)
            if channel is not None:
                # End the channel even if a process that left the group still holds the
                # worker's copy, and let a request in flight finish before the world returns.
                with contextlib.suppress(OSError):
                    parent_end.shutdown(socket.SHUT_RDWR)
                canceled = await _finish(channel) or canceled
            if canceled:
                raise asyncio.CancelledError
