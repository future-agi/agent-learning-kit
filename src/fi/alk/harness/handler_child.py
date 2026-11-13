"""Generated tool handlers in a process of their own, as a declared user.

A bound handler imports the agent's own code (`GeneratedWorld.reach`). Under per-user isolation
that code must not run as the harness, which can read held-back cases, so the control process
starts one handler child per conversation as a declared user and the conversation's world calls
it over a socketpair. The child gets the handler sources, the refusal convention, the world's
database address and the import root on stdin: no model credentials, no target secrets and no
bundle path. It runs the unchanged `GeneratedWorld.call` and returns each call's outcome; the
world on the other side records the call and reads state itself.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import struct
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .bundle_v2 import ProcessUser
from .hosted_scheduler import CallAborted
from .process_runtime import (
    _as_declared_user,
    default_user_resolver,
    world_scratch_dir,
)
from .world.runtime import Call, GeneratedWorld

# The declared user a handler child runs as: the agent's own, since the agent's code is what it
# runs. It gets no secret through its environment, so the agent's processes find nothing there.
HANDLER_USER = ProcessUser.SVC_AGENT
MAX_MESSAGE_BYTES = 4 * 1024 * 1024
CALL_TIMEOUT_SECONDS = 300.0
_LENGTH = struct.Struct(">I")


def _encode(message: Mapping[str, Any]) -> bytes:
    return json.dumps(message, default=str).encode("utf-8")


def _send(sock: socket.socket, body: bytes) -> None:
    sock.sendall(_LENGTH.pack(len(body)) + body)


def _receive_exactly(sock: socket.socket, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = sock.recv(size - len(chunks))
        if not chunk:
            raise EOFError("the handler channel closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _receive(sock: socket.socket) -> dict[str, Any]:
    (size,) = _LENGTH.unpack(_receive_exactly(sock, _LENGTH.size))
    if size > MAX_MESSAGE_BYTES:
        raise ValueError("handler message exceeds the limit")
    message = json.loads(_receive_exactly(sock, size))
    if not isinstance(message, dict):
        raise ValueError("handler message must be an object")
    return message


class HandlerChannel:
    """The conversation's end of a handler child's socket: one call out, its outcome back."""

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock
        self._sock.settimeout(CALL_TIMEOUT_SECONDS)

    def call(self, name: str, arguments: Mapping[str, Any]) -> Call:
        args = dict(arguments)
        request = _encode({"name": name, "arguments": args})
        if len(request) > MAX_MESSAGE_BYTES:
            # Arguments that large are the agent's own doing; the world did not fail.
            return Call(
                name=name,
                arguments=args,
                ok=False,
                error="tool arguments exceed the handler message limit",
            )
        try:
            _send(self._sock, request)
            outcome = _receive(self._sock)
        except (OSError, EOFError, ValueError) as exc:
            # The handlers' process is gone or misbehaving: the world fell over, not the agent.
            raise CallAborted(
                f"handler_child_failed: {type(exc).__name__}", code="call_failed"
            ) from exc
        # Only the outcome crosses; the call's name and arguments are this side's own.
        return Call(
            name=name,
            arguments=args,
            result=outcome.get("result"),
            ok=bool(outcome.get("ok", False)),
            refused=bool(outcome.get("refused", False)),
            error=str(outcome.get("error") or ""),
        )

    def close(self) -> None:
        self._sock.close()


class ChildWorld(GeneratedWorld):
    """A conversation's world whose tool calls run in the handler child.

    Nothing of the agent's code is imported here: `reach` is never called. State is read
    through this side's own store connection, as the harness.
    """

    def __init__(
        self,
        *,
        store: Any,
        channel: HandlerChannel,
        tools: Sequence[Mapping[str, Any]],
        refusal_signature: str,
    ) -> None:
        super().__init__(store=store)
        self._channel = channel
        self.tools = [dict(tool) for tool in tools]
        self.refusal_signature = refusal_signature

    def reach(self, source_root: str) -> None:
        raise RuntimeError(
            "a handler child's world never imports the agent's code here"
        )

    def call(self, name: str, arguments: Mapping[str, Any] | None = None) -> Call:
        return self._record(self._channel.call(name, dict(arguments or {})))


@dataclass
class HandlerChild:
    """A started handler child and the end of its socket for whoever runs the conversation."""

    process: subprocess.Popen
    sock: socket.socket

    def close(self) -> None:
        try:
            self.sock.close()
        finally:
            _stop(self.process)


def _stop(process: subprocess.Popen) -> None:
    # The handlers keep nothing worth a graceful exit, and a child that ignored SIGTERM would
    # hold up the conversation's event loop.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _open_source_to(source: Path, gid: int) -> None:
    """The child imports the agent's code from the upload, which the harness keeps to itself.

    Its group reads it, and nobody else; the harness keeps ownership. Opened once per job: a
    root already in the group was opened whole by an earlier conversation.
    """
    if source.stat().st_gid == gid:
        return
    for path in (*source.rglob("*"), source):
        if path.is_symlink():
            continue
        os.chown(path, -1, gid, follow_symlinks=False)
        path.chmod(path.stat().st_mode | (0o050 if path.is_dir() else 0o040))


def start_handler_child(
    *,
    handlers: Mapping[str, str],
    tools: Sequence[Mapping[str, Any]],
    refusal_signature: str,
    dsn: str,
    source: Path | None,
    work_directory: Path,
    world_index: int,
) -> HandlerChild:
    """Start the child for one conversation. A user that does not resolve is refused."""
    resolved = default_user_resolver(HANDLER_USER.value)
    if resolved is None:
        # The image guarantees the user; without it there is no safe place to run handlers.
        raise CallAborted(
            f"handler_user_unavailable: {HANDLER_USER.value} does not resolve in this sandbox",
            code="call_failed",
        )
    # No bundle process can be named `_handlers`, so this never meets a process's own directory.
    scratch = world_scratch_dir(work_directory, world_index, "_handlers")
    scratch.mkdir(parents=True, exist_ok=True)
    os.chown(scratch, resolved.pw_uid, resolved.pw_gid)
    scratch.chmod(0o700)
    if source is not None:
        _open_source_to(source, resolved.pw_gid)
    # What the agent's code prints stays out of the harness's own log. The file sits beside the
    # scratch directory, where only the harness writes: inside it the agent could plant a link.
    log = os.open(
        scratch.parent / "_handlers.log",
        os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
    )
    try:
        parent_end, child_end = socket.socketpair()
    except BaseException:
        os.close(log)
        raise
    try:
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(scratch),
            "TMPDIR": str(scratch),
            "LANG": "C.UTF-8",
        }
        # The child runs the same harness code as this process, wherever that was loaded from.
        if os.environ.get("PYTHONPATH"):
            environment["PYTHONPATH"] = os.environ["PYTHONPATH"]
        process = subprocess.Popen(
            [sys.executable, "-m", __name__, str(child_end.fileno())],
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=log,
            env=environment,
            cwd=scratch,
            pass_fds=(child_end.fileno(),),
            start_new_session=True,
            **_as_declared_user({"user": resolved.pw_uid, "group": resolved.pw_gid}),
        )
    except BaseException:
        parent_end.close()
        raise
    finally:
        child_end.close()
        os.close(log)
    try:
        assert process.stdin is not None
        process.stdin.write(
            json.dumps(
                {
                    "handlers": dict(handlers),
                    "tools": [dict(tool) for tool in tools],
                    "refusal_signature": refusal_signature,
                    "dsn": dsn,
                    "import_root": str(source) if source is not None else None,
                }
            ).encode("utf-8")
        )
        process.stdin.close()
    except BaseException:
        HandlerChild(process, parent_end).close()
        raise
    return HandlerChild(process, parent_end)


def serve(sock: socket.socket, world: GeneratedWorld) -> None:
    """Answer each call until the conversation's side closes."""
    while True:
        try:
            request = _receive(sock)
        except EOFError:
            return
        call = world.call(
            str(request.get("name") or ""), request.get("arguments") or {}
        )
        body = _encode(
            {
                "result": call.result,
                "ok": call.ok,
                "refused": call.refused,
                "error": call.error,
            }
        )
        if len(body) > MAX_MESSAGE_BYTES:
            body = _encode(
                {"ok": False, "error": "tool result exceeds the handler message limit"}
            )
        _send(sock, body)


def main() -> None:
    from .chat_call_runner import _HostedToolStore

    sock = socket.socket(fileno=int(sys.argv[1]))
    init = json.load(sys.stdin)
    world = GeneratedWorld(store=_HostedToolStore(init["dsn"]))
    world.tools = list(init.get("tools") or [])
    world.handlers = dict(init.get("handlers") or {})
    world.refusal_signature = str(init.get("refusal_signature") or "")
    if init.get("import_root"):
        world.reach(init["import_root"])
    try:
        serve(sock, world)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
