"""A phase worker's workspace files, served by the harness process that holds the world.

Under per-user isolation a workspace belongs to its declared user, and a phase worker runs as the
harness's user with no capabilities, so it cannot open one. The harness process can. The worker
asks it over the channel `run_json_worker` passes: read a file, write a file, or take or restore
a snapshot of the world's workspaces. The harness answers from its own world, by workspace name,
through the same descriptor-safe file code. Nothing the worker sends names a root, and a
snapshot never leaves the harness: the worker gets its digest.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..isolated_process import (
    MAX_CHANNEL_MESSAGE_BYTES,
    encode_message,
    receive_message,
    send_message,
)
from .errors import (
    WorldError,
    WorldReadOnly,
    WorldStateTooLarge,
    WorldUnavailable,
    WorldUsageError,
)
from .files import FileSnapshot

if TYPE_CHECKING:
    from .handle import HostedWorld

_ERRORS = {
    error.__name__: error
    for error in (WorldReadOnly, WorldStateTooLarge, WorldUnavailable, WorldUsageError)
}
# An observation takes two snapshots and may restore the first.
_SNAPSHOTS_KEPT = 2


def _digest(saved: Mapping[str, Mapping[str, FileSnapshot]]) -> str:
    digest = hashlib.sha256()
    for name in sorted(saved):
        for path in sorted(saved[name]):
            entry = saved[name][path]
            size = None if entry.data is None else len(entry.data)
            digest.update(json.dumps([name, path, entry.mode, size]).encode() + b"\0")
            digest.update(entry.data or b"")
    return digest.hexdigest()


def _text(request: Mapping[str, Any], key: str) -> str:
    value = request.get(key)
    if not isinstance(value, str):
        raise WorldUsageError(f"a workspace request needs a text {key}")
    return value


class WorkspaceService:
    """The harness's side: one phase worker's requests, answered from one world."""

    def __init__(self, world: HostedWorld, *, read_only: bool) -> None:
        self._world = world
        self._read_only = read_only
        self._snapshots: dict[str, dict[str, dict[str, FileSnapshot]]] = {}

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._answer(request)
        except WorldError as error:
            return {"error": type(error).__name__, "message": str(error)}
        except UnicodeError:
            # A path or content no file name or UTF-8 file can hold: the scenario's mistake.
            return {"error": "WorldUsageError", "message": "workspace text is not valid UTF-8"}

    def _answer(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request.get("op")
        if operation == "read_file":
            content = self._world.read_file(
                _text(request, "workspace"), _text(request, "path")
            )
            return {"content": content}
        if operation == "write_file":
            if self._read_only:
                raise WorldReadOnly("write_file reached a read-only handle")
            self._world.write_file(
                _text(request, "workspace"),
                _text(request, "path"),
                _text(request, "content"),
            )
            return {}
        if operation == "checkpoint":
            saved = self._world._workspace_snapshot()
            digest = _digest(saved)
            self._snapshots.pop(digest, None)
            self._snapshots[digest] = saved
            while len(self._snapshots) > _SNAPSHOTS_KEPT:
                self._snapshots.pop(next(iter(self._snapshots)))
            return {"digest": digest}
        if operation == "revert":
            saved = self._snapshots.get(_text(request, "digest"))
            if saved is None:
                raise WorldUsageError("revert names no snapshot this phase took")
            self._world._workspace_restore(saved)
            return {}
        raise WorldUsageError("unknown workspace request")


class WorkspaceClient:
    """The phase worker's side: one request at a time over the channel it was given."""

    def __init__(self, fd: int) -> None:
        self._socket = socket.socket(fileno=fd)
        self._lock = threading.Lock()

    def request(self, operation: str, **fields: str) -> dict[str, Any]:
        body = encode_message({"op": operation, **fields})
        if len(body) > MAX_CHANNEL_MESSAGE_BYTES:
            raise WorldStateTooLarge("workspace request exceeds the channel limit")
        with self._lock:
            try:
                send_message(self._socket, body)
                reply = receive_message(self._socket, limit=MAX_CHANNEL_MESSAGE_BYTES)
            except (OSError, EOFError, ValueError) as error:
                raise WorldUnavailable("the workspace channel failed") from error
        if "error" in reply:
            raise _ERRORS.get(reply["error"], WorldError)(str(reply.get("message", "")))
        return reply
