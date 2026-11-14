"""Supervised JSON workers with private environments and process-group cleanup."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import signal
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any

MAX_RESULT_BYTES = 16 * 1024 * 1024
GRACEFUL_SHUTDOWN_SECONDS = 90.0
# The person stopped the run: the worker seals what its call kept and writes its result, unlike SIGTERM.
STOP_SIGNAL = signal.SIGUSR1
_STOP_POLL_SECONDS = 0.5
_stop_requested = False


def stop_requested() -> bool:
    """Inside a worker: whether its parent passed on the person's stop."""
    return _stop_requested


class IsolatedWorkerError(RuntimeError):
    """The worker exited without a valid result."""


async def _finish(task: asyncio.Task) -> bool:
    canceled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            canceled = True
    task.result()
    return canceled


def run_worker(main: Callable[[], Awaitable[None]], *, cancel_on_stop: bool = False) -> None:
    """Translate termination into one cancellation so async cleanup can finish.

    The stop signal sets `stop_requested()`; with `cancel_on_stop` it also cancels `main` once, for a call that seals
    its partial evidence when cancelled on a stop."""

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

        def stop() -> None:
            global _stop_requested
            if _stop_requested:
                return
            _stop_requested = True
            logging.getLogger(__name__).info("isolated_worker_stop_received")
            if cancel_on_stop and not termination_requested and not task.done():
                task.cancel()

        loop.add_signal_handler(signal.SIGTERM, cancel_once)
        loop.add_signal_handler(STOP_SIGNAL, stop)
        finished = False
        try:
            await task
            finished = True
        finally:
            loop.remove_signal_handler(signal.SIGTERM)
            loop.remove_signal_handler(STOP_SIGNAL)
            if _stop_requested:
                # A stopped call can leave a thread blocked on the agent, and the loop's shutdown would wait for it
                # while the parent waits for this process: exit now with what was written; the parent reaps the rest.
                logging.shutdown()
                sys.stdout.flush()
                sys.stderr.flush()
                os._exit(0 if finished else 1)

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


async def _communicate(
    process: asyncio.subprocess.Process, payload: bytes, stop_requested: Callable[[], bool] | None
) -> None:
    exchange = asyncio.ensure_future(process.communicate(payload))
    try:
        stopped = stop_requested is None
        while not exchange.done():
            await asyncio.wait({exchange}, timeout=_STOP_POLL_SECONDS)
            if not stopped and not exchange.done() and stop_requested():
                stopped = True
                logging.getLogger(__name__).info("isolated_worker_stop_sent pid=%s", process.pid)
                try:
                    process.send_signal(STOP_SIGNAL)
                except ProcessLookupError:
                    pass
        exchange.result()
    finally:
        if not exchange.done():
            exchange.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await exchange


async def run_json_worker(
    module: str,
    payload: Mapping[str, Any],
    *,
    environ: Mapping[str, str],
    work_directory: Path,
    suppress_output: bool = False,
    stop_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Run a fresh interpreter; cancellation waits for process-group termination.

    Once `stop_requested()` reads true the worker gets the stop signal once and still returns its result."""
    if os.name != "posix":
        raise IsolatedWorkerError("isolated workers require POSIX process groups")
    work_directory.mkdir(parents=True, exist_ok=True)
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
            )
        )
        canceled = await _finish(creation)
        process = creation.result()
        try:
            if canceled:
                raise asyncio.CancelledError
            await _communicate(process, json.dumps(payload).encode(), stop_requested)
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
            if await _finish(cleanup):
                raise asyncio.CancelledError
