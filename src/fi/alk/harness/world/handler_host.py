"""Run environment-authored handlers outside the process that calls them.

A designed environment's handlers are written by a model, and a document the person uploaded can
steer that model. In process, a handler can reach everything the calling process holds: the
builder's model credentials, its platform capability, a run's provider secrets. A source check
of the handler cannot close that (`collections._sys.modules['os']` is one of many ways round
it), so with `ALK_HANDLER_ISOLATION` set the handler runs in a child process instead:

- `namespace` (the hosted image): the child starts in a user namespace as a uid the sandbox user
  does not own, with an empty environment, no network, and empty mounts over `/work`, `/run`,
  `/home` and `/tmp`. It cannot read the builder's files, its environment or its peers' `/proc`.
  When the namespace cannot be created the call fails; it never falls back.
- `process`: the same child without the namespace, for a host that has none (a laptop). It does
  not see the caller's environment, and nothing more.
- unset: handlers run in process, as before.

The child reaches the world only through its `db`: each operation is a request this process
answers on the real store. Messages are JSON lines (see `handler_child`); nothing the child sends
is unpickled. One child serves one thread, and a call that runs past its timeout kills it.
"""

from __future__ import annotations

import atexit
import json
import os
import selectors
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .handler_child import decode, encode

MODES = ("namespace", "process")
# A uid inside the sandbox user's subordinate range (/etc/subuid in the hosted image): it owns
# nothing, so the builder's 0700 directories and 0600 files are closed to it.
_MAPPED_ID = 100000
_HIDDEN = ("/work", "/run", "/home", "/tmp")
_CHILD = (Path(__file__).with_name("handler_child.py")).read_text(encoding="utf-8")
# A handler is a few queries; a proof calls hundreds. Long enough for neither to trip it.
DEFAULT_TIMEOUT_SECONDS = 60.0
# What one message from the child may hold, so a handler cannot exhaust the caller's memory.
_MOST_MESSAGE_BYTES = 32 * 1024 * 1024


class HandlerFault(Exception):
    """A handler that crashed in the child. Subclassed per call with the handler's own exception
    names, so an outcome reads (and a refusal signature matches) as it would in process."""


class HandlerTimeout(HandlerFault):
    pass


class HandlerHostError(RuntimeError):
    """The child could not be started or stopped answering: an infrastructure fault."""


def isolation_mode() -> str | None:
    """`ALK_HANDLER_ISOLATION`, checked: `namespace`, `process`, or None when unset."""
    mode = os.environ.get("ALK_HANDLER_ISOLATION", "").strip()
    if not mode:
        return None
    if mode not in MODES:
        raise HandlerHostError(
            f"ALK_HANDLER_ISOLATION={mode!r}; use one of {', '.join(MODES)}"
        )
    return mode


def _command(mode: str) -> list[str]:
    python = [sys.executable, "-I", "-c", _CHILD]
    if mode == "process":
        return python
    hide = "; ".join(
        f"mount -t tmpfs -o size=1m,mode=0755 none {path}" for path in _HIDDEN
    )
    return [
        "unshare",
        "--user",
        f"--map-users=0:{_MAPPED_ID}:1",
        f"--map-groups=0:{_MAPPED_ID}:1",
        "--setuid=0",
        "--setgid=0",
        "--net",
        "--mount",
        "--",
        "/bin/sh",
        "-ec",
        f'{hide}; exec "$@"',
        "handler-child",
        *python,
    ]


class _Child:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        try:
            self.process = subprocess.Popen(
                _command(mode),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
                cwd="/",
                close_fds=True,
            )
        except OSError as exc:
            raise HandlerHostError(
                f"could not start the isolated handler process ({mode}): {exc}"
            ) from exc
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.buffer = b""

    def send(self, message: dict[str, Any]) -> None:
        try:
            self.process.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise HandlerHostError(self._died(exc)) from exc

    def receive(self, deadline: float) -> dict[str, Any]:
        while b"\n" not in self.buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HandlerTimeout("the handler did not finish in time")
            if not self.selector.select(remaining):
                continue
            chunk = os.read(self.process.stdout.fileno(), 1024 * 1024)
            if not chunk:
                raise HandlerHostError(self._died(None))
            self.buffer += chunk
            if len(self.buffer) > _MOST_MESSAGE_BYTES:
                raise HandlerHostError("the isolated handler sent an oversized message")
        line, self.buffer = self.buffer.split(b"\n", 1)
        message = json.loads(line)
        if not isinstance(message, dict):
            raise HandlerHostError("the isolated handler sent a malformed message")
        return message

    def _died(self, exc: BaseException | None) -> str:
        try:
            self.process.wait(timeout=2)
            stderr = self.process.stderr.read().decode("utf-8", "replace")[-600:]
        except (subprocess.TimeoutExpired, OSError, ValueError):
            stderr = ""
        return (
            f"the isolated handler process ({self.mode}) exited"
            + (f": {exc}" if exc else "")
            + (f"; {stderr.strip()}" if stderr.strip() else "")
        )

    def close(self) -> None:
        self.selector.close()
        if self.process.poll() is None:
            self.process.kill()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            try:
                stream.close()
            except OSError:
                pass


_local = threading.local()
_children: list[_Child] = []
_children_lock = threading.Lock()


def _child(mode: str) -> _Child:
    child = getattr(_local, "child", None)
    if child is not None and (child.mode != mode or child.process.poll() is not None):
        _discard(child)
        child = None
    if child is None:
        child = _Child(mode)
        _local.child = child
        with _children_lock:
            _children.append(child)
    return child


def _discard(child: _Child) -> None:
    child.close()
    if getattr(_local, "child", None) is child:
        _local.child = None
    with _children_lock:
        if child in _children:
            _children.remove(child)


@atexit.register
def _close_all() -> None:
    with _children_lock:
        children = list(_children)
    for child in children:
        child.close()


def _answer(db: Any, request: dict[str, Any]) -> dict[str, Any]:
    method = request.get("method")
    if method not in {
        "query",
        "one",
        "execute",
        "collections",
        "records",
        "find",
        "add",
    }:
        return {"error": {"type": "AttributeError", "message": f"db has no {method!r}"}}
    try:
        value = getattr(db, method)(
            *decode(request["args"]), **decode(request["kwargs"])
        )
    except Exception as exc:  # noqa: BLE001 - the handler sees the store's error, as in process
        return {"error": {"type": type(exc).__name__, "message": str(exc)}}
    return {"reply": encode(value)}


def _rebuild(raised: dict[str, Any]) -> Exception:
    from .runtime import ToolError

    names = [str(name) for name in raised.get("mro") or []]
    message = str(raised.get("message") or "")
    if "ToolError" in names:
        try:
            return ToolError(message, category=raised.get("category"))
        except ValueError as exc:
            # In process the bad category raised inside the handler: a crash, not a refusal.
            return type("ValueError", (HandlerFault,), {})(str(exc))
    built: type[Exception] = HandlerFault
    for name in reversed(names):
        if name not in {"object", "BaseException", "Exception"}:
            built = type(name, (built,), {})
    return built(message)


def run_handler(
    source: str,
    name: str,
    arguments: dict[str, Any],
    db: Any,
    *,
    mode: str,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> Any:
    """Run one handler in an isolated child: its value, or the exception it raised (a refusal as
    the world's ToolError, anything else as a HandlerFault named like the original)."""
    child = _child(mode)
    deadline = time.monotonic() + timeout
    try:
        child.send(
            {"call": {"source": source, "name": name, "args": encode(arguments)}}
        )
        while True:
            message = child.receive(deadline)
            if "db" in message:
                child.send(_answer(db, message["db"]))
            elif "result" in message:
                return decode(message["result"])
            elif "raised" in message:
                raise _rebuild(message["raised"])
            else:
                raise HandlerHostError("the isolated handler sent a malformed message")
    except (HandlerTimeout, HandlerHostError, ValueError):
        # A child that ran out its time or broke the protocol cannot be trusted with the next
        # call; the next call starts a fresh one.
        _discard(child)
        raise
