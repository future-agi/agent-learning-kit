"""Runs the contract save's checks of the customer's files in a child process, under one deadline.

The checks parse the customer's code, and Python's parser holds the GIL, so in a thread a crafted
repository holds the whole conversation for as long as they run. A child can be killed when a
save's deadline passes; a thread can't. A save the deadline cuts saves nothing. When no child can
be had, the same checks run in a thread, as they did before there was a child.

The child answers two jobs: the session job (the browser scan and instrumentation, which depend
only on the session) once, cached here; and the save job once per save. Every limit inside the
checks stays: they decide the answers and bound memory, and the deadline only stops what they
don't.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import multiprocessing
import os
import queue
import subprocess
import sys
import threading
import time
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any, Literal

from .contract import AgentContract
from .forkserver_preload import PRELOAD
from .reading_entry import MaskSecrets

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

Mode = Literal["child", "thread"]

# The whole of one save's wait, from the call's start: the session job, the lock, the child's
# start-up and its own checks.
SAVE_DEADLINE = 1.5
# The session job's own bound. No save waits on it past its own deadline, so this only decides
# how long a crafted repository answers "not checked in time".
SESSION_BOUND = 30.0
# From the child's `up`, so the fork server's start-up isn't charged to the trial.
TRIAL_SECONDS = 2.0
# A killed child is gone almost at once; the bound only stops a wedged reap holding a thread.
JOIN_SECONDS = 5.0
# A child that sends no `up` by then is taken as a failed start: the fork server's preload took
# 0.3-1.6 s when measured, so this only catches one that never comes up.
START_SECONDS = 15.0
# Deaths in a row before the checks move into this process for the rest of the checker's life.
DEATHS = 2
WATCH_INTERVAL = 0.05
# Measured child peaks were 57-209 MB; this catches slow growth, not a burst.
WATCH_CAP = 400 * 1024 * 1024

STAGES = ("contract", "tool_evidence", "prompt_copy", "model", "runtime")
_PARTS = ("scan", "instrumentation")
_SCAN_LATE = "not checked in time: the folder's checks are still running; submit it once, alone"
_SAVE_LATE = "not checked in time: submit it once, alone"
_GONE: dict[str, Any] = {"id": None, "kind": "gone"}

# Every checker started in this process, so the tests can close what they leave open.
_OPEN: weakref.WeakSet[SaveChecker] = weakref.WeakSet()


class SessionChecksFailed(Exception):
    """A part of the session job raised in the child; the call fails and nothing is saved."""

    def __init__(self, part: str, error: str, message: str) -> None:
        super().__init__(
            f"The folder's {part} check raised {error} in the save checker's child: {message}. "
            "Nothing was saved; the next save runs it again."
        )
        self.part, self.error, self.message = part, error, message


class SaveChecksFailed(Exception):
    """The save job raised in the child; the call fails and nothing is saved."""

    def __init__(self, error: str, message: str) -> None:
        super().__init__(
            f"The contract's checks raised {error} in the save checker's child: {message}. "
            "Nothing was saved."
        )
        self.error, self.message = error, message


class StandInFailed(Exception):
    """The stand-in module named for the child's checks could not be put in place."""

    def __init__(self, module: str, detail: str) -> None:
        super().__init__(
            f"The save checks' stand-in {module!r} could not be put in place in the child: "
            f"{detail}. Check that the module imports and that its STAGES keys name real "
            "attributes."
        )
        self.module, self.detail = module, detail


@dataclass(frozen=True)
class ScanAnswer:
    found: list[str]
    cut: frozenset[str]


@dataclass(frozen=True)
class Saved:
    """Every check answered; the caller writes it."""

    contract: AgentContract


@dataclass(frozen=True)
class Refused:
    """The response to send back, as `_problems` builds it."""

    response: dict[str, Any]


@dataclass(frozen=True)
class NotInTime:
    """Cut with no problem in; nothing is saved."""

    response: dict[str, Any]


async def _by(awaitable: Any, deadline: float) -> Any:
    """``awaitable``'s result, or ``asyncio.TimeoutError`` once the running loop's clock passes
    ``deadline``. On 3.11 and later through ``asyncio.timeout_at``: there ``wait_for`` returns a
    result that arrived in the same tick as a cancel, and the cancelled call would go on to save."""
    if sys.version_info >= (3, 11):
        async with asyncio.timeout_at(deadline):
            return await awaitable
    return await asyncio.wait_for(awaitable, max(deadline - asyncio.get_running_loop().time(), 0.0))


def _log_failed(part: str, message: dict[str, Any]) -> None:
    # The type alone at ERROR: the message and trace can quote the customer's paths, which
    # masking doesn't hide, so they go to DEBUG.
    logger.error("save checker: %s failed in the child: %s", part, message.get("error"))
    logger.debug(
        "save checker: %s failed in the child: %s\n%s", part, message.get("message"), message.get("trace")
    )


def default_mode() -> Mode:
    """"thread" when ALK_SAVE_CHECKS == "thread" or forkserver isn't available; else "child"."""
    if os.environ.get("ALK_SAVE_CHECKS") == "thread":
        return "thread"
    if "forkserver" not in multiprocessing.get_all_start_methods():
        return "thread"
    return "child"


def rss_bytes(source: Literal["statm", "ps"], text: str, page_size: int) -> int:
    """A process's resident memory in bytes, from one raw reading.

    ``statm``'s second field counts resident pages; ``ps -o rss=`` prints KiB. Raises
    ``ValueError`` on a reading it can't parse, which the watchdog skips.
    """
    fields = text.split()
    try:
        if source == "statm":
            return int(fields[1]) * page_size
        if source == "ps":
            return int(fields[0]) * 1024
    except (IndexError, ValueError) as unreadable:
        raise ValueError(f"unreadable {source} reading") from unreadable
    raise ValueError(f"unknown memory reading source {source!r}")


def _rss(pid: int) -> int | None:
    try:
        if sys.platform.startswith("linux"):
            text = Path(f"/proc/{pid}/statm").read_text(encoding="ascii")
            return rss_bytes("statm", text, os.sysconf("SC_PAGE_SIZE"))
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=True,
        ).stdout
        return rss_bytes("ps", out, 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        # A child that just ended, or a busy `ps`: the next tick reads again.
        return None


def _late(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _which(parts: list[str]) -> str:
    if len(parts) == 2:
        return "the scan and instrumentation"
    return "the scan" if parts == ["scan"] else "instrumentation"


def _text(path: Path | None) -> str | None:
    return None if path is None else os.fspath(path)


def _settle(future: asyncio.Future[None]) -> None:
    if not future.done():
        future.set_result(None)


class _Thread:
    """Marks a wait that must give way to thread mode."""


_THREAD = _Thread()


@dataclass(eq=False)
class _Child:
    process: BaseProcess
    conn: Connection
    pid: int
    # `up`, and the start thread's trial and stand_in answers.
    inbox: queue.SimpleQueue[dict[str, Any]] = field(default_factory=queue.SimpleQueue)
    replies: dict[int, Callable[[dict[str, Any]], None]] = field(default_factory=dict)
    # The id of the request in the child; the watchdog reads only while there is one.
    request: int | None = None
    ready: bool = False
    # Why the parent killed it; None when it ended on its own.
    killed: str | None = None
    gone: bool = False
    handled: bool = False
    reaped: bool = False
    reader: threading.Thread | None = None
    stop: threading.Event = field(default_factory=threading.Event)


@dataclass(eq=False)
class _Session:
    id: int
    child: _Child
    pending: set[str]
    timer: threading.Timer | None = None


class SaveChecker:
    """One child per builder session, serving the event loop of the first call that reaches it."""

    # The child's entry point; tests put a fake child here before `start()`.
    _target: Callable[[Connection], None] | None = None
    ran_in_child: int

    def __init__(
        self,
        *,
        scan_root: Path | None,
        source_root: Path | None,
        subfolder: str | None,
        mode: Mode | None = None,
        stand_in: str | None = None,
    ) -> None:
        self._scan_root, self._source_root, self._subfolder = scan_root, source_root, subfolder
        self._mode: Mode = mode or default_mode()
        self._stand_in = stand_in
        self.ran_in_child = 0
        # Every child this checker started, so a test can check none outlives it.
        self.pids: list[int] = []
        # Guards the cache and the child's state; never held across a send, a join or a wait.
        self._cache_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._scan: ScanAnswer | None = None
        self._instrumentation: str | None = None
        self._failures = {part: 0 for part in _PARTS}
        self._failure: dict[str, tuple[str, str]] = {}
        self._watchers: list[tuple[asyncio.AbstractEventLoop, asyncio.Future[None]]] = []
        self._child: _Child | None = None
        self._session: _Session | None = None
        self._ids = itertools.count(1)
        self._started = False
        self._closed = False
        self._thread_reason: str | None = None
        self._stand_in_error: tuple[str, str] | None = None
        self._trial_passed = False
        self._deaths = 0
        self._start_thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self._warned: set[str] = set()
        # Thread mode's own scan, one shared task per loop, as the contract tool kept it.
        self._thread_task: asyncio.Future[tuple[list[str], frozenset[str]]] | None = None
        self._reach: list[Path] = []
        self._scanned_here = False
        wanted = scan_root is not None or source_root is not None
        if (
            wanted
            and mode is None
            and self._mode == "thread"
            and os.environ.get("ALK_SAVE_CHECKS") != "thread"
        ):
            self._fallback("forkserver is not available on this platform")

    # -- the API ---------------------------------------------------------------------------

    def start(self) -> None:
        with self._cache_lock:
            if self._started or self._closed or not self._wants_child():
                return
            self._started = True
        _OPEN.add(self)
        # The preload import blocks for up to a second or two, so never on the caller's loop.
        self._spawn(self._launch)

    async def scan(self, *, deadline: float) -> ScanAnswer | NotInTime:
        loop = asyncio.get_running_loop()
        if self._in_thread(loop):
            return await self._thread_scan()
        self.start()
        seen = self._seen()
        answer = await self._until(lambda: self._scan_ready(seen), deadline, session=True)
        if isinstance(answer, _Thread):
            self._note_closed()
            return await self._thread_scan()
        if answer is None:
            logger.warning("save checker: the save's deadline passed in %s; nothing saved", "session")
            return NotInTime(_late(_SCAN_LATE))
        return answer

    async def check(
        self,
        payload: dict[str, Any],
        *,
        require_setup_contract: bool,
        asked: set[str],
        held: dict[str, str],
        deadline: float,
    ) -> Saved | Refused | NotInTime:
        loop = asyncio.get_running_loop()
        if self._in_thread(loop):
            return await self._thread_check(payload, require_setup_contract, asked, held)
        self.start()
        seen = self._seen()
        parts = await self._until(lambda: self._parts_ready(seen), deadline, session=True)
        if isinstance(parts, _Thread):
            self._note_closed()
            return await self._thread_check(payload, require_setup_contract, asked, held)
        if parts is None:
            logger.warning("save checker: the save's deadline passed in %s; nothing saved", "session")
            return NotInTime(_late(_SAVE_LATE))
        lock = self._lock
        assert lock is not None
        try:
            await _by(lock.acquire(), deadline)
        except asyncio.TimeoutError:
            logger.warning(
                "save checker: the save's deadline passed in %s; nothing saved", ", ".join(STAGES)
            )
            return NotInTime(_late(_SAVE_LATE))
        if loop.time() >= deadline:
            # The lock came with no time left: sending would only get a healthy child killed.
            lock.release()
            logger.warning(
                "save checker: the save's deadline passed in %s; nothing saved", ", ".join(STAGES)
            )
            return NotInTime(_late(_SAVE_LATE))
        try:
            result = await self._save(payload, require_setup_contract, asked, held, deadline)
        finally:
            lock.release()
        if isinstance(result, _Thread):
            self._note_closed()
            return await self._thread_check(payload, require_setup_contract, asked, held)
        return result

    def close(self) -> None:
        """Kills the child and stops its threads. Never raises."""
        child = thread = None
        try:
            with self._cache_lock:
                self._closed = True
                child, thread = self._child, self._start_thread
                session, self._session = self._session, None
            if session is not None and session.timer is not None:
                session.timer.cancel()
        except Exception:
            logger.warning("save checker: closing could not read its state", exc_info=True)
        try:
            if child is not None:
                self._kill(child, "close", restart=False)
        except Exception:
            logger.warning("save checker: the child could not be killed", exc_info=True)
        try:
            if thread is not None and thread is not threading.current_thread():
                # A start still running sees the close and kills what it started.
                thread.join(JOIN_SECONDS)
        except Exception:
            logger.warning("save checker: the start thread could not be joined", exc_info=True)
        try:
            with self._cache_lock:
                last = self._child
            for one in {c for c in (child, last) if c is not None}:
                self._reap(one)
        except Exception:
            logger.warning("save checker: the child could not be reaped", exc_info=True)
        try:
            self._notify()
        except Exception:
            logger.warning("save checker: waiting calls could not be woken", exc_info=True)

    # -- which path a call takes -----------------------------------------------------------

    def _wants_child(self) -> bool:
        return self._mode == "child" and (
            self._scan_root is not None or self._source_root is not None
        )

    def _in_thread(self, loop: asyncio.AbstractEventLoop) -> bool:
        if self._closed:
            self._note_closed()
            return True
        if not self._wants_child() or self._thread_reason is not None:
            return True
        self._raise_stand_in()
        if self._loop is None:
            self._loop, self._lock = loop, asyncio.Lock()
        elif self._loop is not loop:
            # The child serves one loop; another loop's call keeps today's in-process path.
            self._warn_once(
                "other loop", "save checker: a call from a second event loop runs in this process"
            )
            return True
        return False

    def _note_closed(self) -> None:
        if self._closed:
            self._warn_once("closed", "save checker: closed; running the checks in this process")

    def _warn_once(self, key: str, text: str) -> None:
        with self._cache_lock:
            if key in self._warned:
                return
            self._warned.add(key)
        logger.warning(text)

    def _raise_stand_in(self) -> None:
        error = self._stand_in_error
        if error is not None:
            raise StandInFailed(*error)

    # -- thread mode -----------------------------------------------------------------------

    async def _thread_scan(self) -> ScanAnswer:
        with self._cache_lock:
            cached = self._scan
        if cached is not None:
            return cached
        from . import tools  # tools imports this module

        loop = asyncio.get_running_loop()
        scan = self._thread_task
        # Run again only a scan that can't give its answer here: one that raised or was
        # cancelled, or one on another loop, which this one can't wait on.
        if (
            scan is None
            or scan.get_loop() is not loop
            or (scan.done() and (scan.cancelled() or scan.exception() is not None))
        ):
            scan = asyncio.ensure_future(
                asyncio.to_thread(
                    tools._tools_in_agent_folder, self._scan_root, self._subfolder, self._reach
                )
            )
            self._thread_task = scan
        # Shielded: one call cancelled mustn't cancel the scan the others wait on.
        found, cut = await asyncio.shield(scan)
        self._scanned_here = True
        return ScanAnswer(found, cut)

    async def _thread_check(
        self,
        payload: dict[str, Any],
        require_setup_contract: bool,
        asked: set[str],
        held: dict[str, str],
    ) -> Saved | Refused:
        from . import tools  # tools imports this module

        with self._cache_lock:
            instrumentation = self._instrumentation
        extra: dict[str, Any] = {}
        if instrumentation is not None:
            extra["instrumentation"] = instrumentation
        checked = await asyncio.to_thread(
            tools._checked_contract,
            payload,
            source_root=self._source_root,
            require_setup_contract=require_setup_contract,
            subfolder=self._subfolder,
            # Only a reach this process walked is whole; a cached scan's reach stayed in the child.
            reached=self._reach
            if self._scan_root == self._source_root and self._scanned_here
            else None,
            asked=asked,
            held=held,
            **extra,
        )
        if isinstance(checked, dict):
            return Refused(checked)
        return Saved(checked)

    # -- waiting on the loop ---------------------------------------------------------------

    def _seen(self) -> dict[str, int]:
        with self._cache_lock:
            return dict(self._failures)

    def _has(self, part: str) -> bool:
        return (self._scan if part == "scan" else self._instrumentation) is not None

    def _gave_way(self) -> bool:
        return self._closed or self._thread_reason is not None

    def _raise_failed(self, part: str, seen: dict[str, int]) -> None:
        if not self._has(part) and self._failures[part] > seen[part]:
            raise SessionChecksFailed(part, *self._failure[part])

    def _scan_ready(self, seen: dict[str, int]) -> ScanAnswer | _Thread | None:
        if self._gave_way():
            return _THREAD
        self._raise_stand_in()
        if self._scan is not None:
            return self._scan
        self._raise_failed("scan", seen)
        return None

    def _parts_ready(self, seen: dict[str, int]) -> bool | _Thread | None:
        if self._gave_way():
            return _THREAD
        self._raise_stand_in()
        if self._scan is not None and self._instrumentation is not None:
            return True
        for part in _PARTS:
            self._raise_failed(part, seen)
        return None

    def _child_ready(self) -> _Child | _Thread | None:
        if self._gave_way():
            return _THREAD
        self._raise_stand_in()
        child = self._child
        if (
            child is not None
            and child.ready
            and not child.gone
            and child.killed is None
            and child.request is None
        ):
            return child
        return None

    async def _until(self, condition: Callable[[], Any], deadline: float, *, session: bool = False) -> Any:
        """``condition()``'s first answer that isn't None, or None once ``deadline`` passes."""
        loop = asyncio.get_running_loop()
        while True:
            if session:
                self._ask_session()
            future: asyncio.Future[None] = loop.create_future()
            entry = (loop, future)
            with self._cache_lock:
                # Registered and read under one lock, so an answer can't land between the two.
                answer = condition()
                if answer is None:
                    self._watchers.append(entry)
            if answer is not None:
                return answer
            try:
                await _by(future, deadline)
            except asyncio.TimeoutError:
                with self._cache_lock:
                    return condition()
            finally:
                with self._cache_lock:
                    if entry in self._watchers:
                        self._watchers.remove(entry)

    def _notify(self) -> None:
        with self._cache_lock:
            watchers, self._watchers = self._watchers, []
        for loop, future in watchers:
            try:
                loop.call_soon_threadsafe(_settle, future)
            except RuntimeError:
                # Its loop has closed; the cache is already written for a later call.
                pass

    # -- a save in the child ---------------------------------------------------------------

    async def _save(
        self,
        payload: dict[str, Any],
        require_setup_contract: bool,
        asked: set[str],
        held: dict[str, str],
        deadline: float,
    ) -> Saved | Refused | NotInTime | _Thread:
        loop = asyncio.get_running_loop()
        inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def reply(message: dict[str, Any]) -> None:
            try:
                loop.call_soon_threadsafe(inbox.put_nowait, message)
            except RuntimeError:
                pass

        while True:
            child = await self._until(self._child_ready, deadline)
            if isinstance(child, _Thread):
                return child
            if child is None or loop.time() >= deadline:
                # A child that came up in the deadline's tick isn't sent the save: it would only
                # be cut, and a healthy child killed for it.
                logger.warning(
                    "save checker: the save's deadline passed in %s; nothing saved",
                    ", ".join(STAGES),
                )
                return NotInTime(_late(_SAVE_LATE))
            rid = next(self._ids)
            with self._cache_lock:
                if child.gone or child.killed is not None:
                    continue
                child.replies[rid] = reply
                child.request = rid
                instrumentation = self._instrumentation
            break
        request = {
            "id": rid,
            "kind": "save",
            "payload": payload,
            "source_root": _text(self._source_root),
            "subfolder": self._subfolder,
            "require_setup_contract": require_setup_contract,
            "instrumentation": instrumentation,
            "asked": sorted(asked),
            "held": dict(held),
            # A restarted child gets no `session`, so each save carries the level too.
            "log_level": logging.getLogger().level,
        }
        if self._send(child, request):
            self.ran_in_child += 1
        problems: list[str] = []
        answered: list[str] = []
        last: dict[str, Any] | None = None
        outcome: tuple[str, dict[str, Any] | None] | None = None

        def read(message: dict[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
            nonlocal last
            kind = message.get("kind")
            if kind == "gone":
                return "gone", None
            if message.get("id") != rid:
                return None
            if kind == "stage":
                answered.append(str(message.get("stage")))
                problems.extend(message.get("problems") or [])
                last = message
                return None
            if kind in ("done", "failed"):
                return str(kind), message
            return None

        try:
            while outcome is None:
                try:
                    message = await _by(inbox.get(), deadline)
                except asyncio.TimeoutError:
                    # Answers the reader handed over in this tick still count: `done` wins a tie.
                    await asyncio.sleep(0)
                    while outcome is None and not inbox.empty():
                        outcome = read(inbox.get_nowait())
                    if outcome is None:
                        outcome = ("cut", None)
                    break
                outcome = read(message)
        except asyncio.CancelledError:
            if outcome is None:
                # Only this call's own request is killed; it isn't a death.
                self._kill(child, "cancel", restart=True)
            raise
        finally:
            with self._cache_lock:
                child.replies.pop(rid, None)
                if child.request == rid:
                    child.request = None
        kind, message = outcome
        if kind == "done":
            assert message is not None
            with self._cache_lock:
                self._deaths = 0
            asked.update(message.get("asked") or [])
            held.update(message.get("held") or {})
            if message.get("contract") is not None:
                return Saved(AgentContract.model_validate(message["contract"]))
            return Refused(message["response"])
        if kind == "failed":
            assert message is not None
            with self._cache_lock:
                self._deaths = 0
            _log_failed("save", message)
            raise SaveChecksFailed(str(message.get("error")), str(message.get("message")))
        if kind == "cut":
            logger.warning(
                "save checker: the save's deadline passed in %s; nothing saved",
                ", ".join(stage for stage in STAGES if stage not in answered),
            )
            self._kill(child, "deadline", restart=True)
        # Cut, or the child died: the model sees only the problems that arrived.
        if problems and last is not None:
            from .tools import _problems  # tools imports this module

            asked.update(last.get("asked") or [])
            held.update(last.get("held") or {})
            arrived = sorted(payload) if isinstance(payload, dict) else [type(payload).__name__]
            return Refused(_problems(problems, arrived))
        return NotInTime(_late(_SAVE_LATE))

    # -- the session job -------------------------------------------------------------------

    def _ask_session(self) -> None:
        """Sends `session` when a part is missing, the child is ready and none is in flight."""
        with self._cache_lock:
            child = self._child
            if (
                self._session is not None
                or (self._scan is not None and self._instrumentation is not None)
                or child is None
                or not child.ready
                or child.gone
                or child.killed is not None
                or child.request is not None
                or self._gave_way()
                or self._stand_in_error is not None
            ):
                return
            session = _Session(next(self._ids), child, set(_PARTS))
            session.timer = threading.Timer(SESSION_BOUND, self._bound, args=(session,))
            session.timer.daemon = True
            self._session = session
            child.request = session.id
        session.timer.start()
        self._send(
            child,
            {
                "id": session.id,
                "kind": "session",
                "scan_root": _text(self._scan_root),
                "source_root": _text(self._source_root),
                "subfolder": self._subfolder,
                "log_level": logging.getLogger().level,
            },
        )

    def _cut(self, part: str) -> None:
        """Caches ``part`` as cut, unless it answered: the first write wins. Under the lock."""
        if part == "scan" and self._scan is None:
            self._scan = ScanAnswer([], frozenset({"deadline"}))
        elif part == "instrumentation" and self._instrumentation is None:
            self._instrumentation = "none"

    def _end_session(self, session: _Session) -> list[str]:
        """Takes ``session`` out of flight and caches its missing parts as cut. Under the lock."""
        self._session = None
        if session.timer is not None:
            session.timer.cancel()
        if session.child.request == session.id:
            session.child.request = None
        missing = [part for part in _PARTS if not self._has(part)]
        for part in missing:
            self._cut(part)
        return missing

    def _bound(self, session: _Session) -> None:
        child = session.child
        with self._cache_lock:
            if self._session is not session:
                return
            missing = self._end_session(session)
            # Marked killed in the same hold that ends the session, so no call, waiting or new,
            # sends a save to a child about to go.
            ending = child.killed is None and not child.gone
            if ending:
                child.killed = "bound"
        if missing:
            logger.warning(
                "save checker: the session's checks ran past %d s; %s counts as cut",
                SESSION_BOUND,
                _which(missing),
            )
        if ending:
            try:
                child.process.kill()
            except Exception:
                logger.warning("save checker: the child could not be killed", exc_info=True)
            self._spawn(self._replace, child)
        self._notify()

    def _part(self, child: _Child, message: dict[str, Any]) -> None:
        kind = message.get("kind")
        part = str(message.get("part")) if kind == "failed" else str(kind)
        cut_now = False
        with self._cache_lock:
            session = self._session
            if session is None or session.child is not child or message.get("id") != session.id:
                return
            session.pending.discard(part)
            if kind == "failed":
                if not self._has(part):
                    self._failures[part] += 1
                    if self._failures[part] >= 2:
                        self._cut(part)
                        cut_now = True
                    else:
                        self._failure[part] = (
                            str(message.get("error")),
                            str(message.get("message")),
                        )
            elif part == "scan" and self._scan is None:
                self._scan = ScanAnswer(
                    list(message.get("found") or []), frozenset(message.get("cut") or [])
                )
            elif part == "instrumentation" and self._instrumentation is None:
                self._instrumentation = str(message.get("value"))
            if not session.pending:
                self._session = None
                if session.timer is not None:
                    session.timer.cancel()
                if child.request == session.id:
                    child.request = None
        if kind == "failed":
            _log_failed(part, message)
        if cut_now:
            logger.warning("save checker: the session's %s failed twice; it counts as cut", part)
        self._notify()

    # -- the child's life ------------------------------------------------------------------

    def _spawn(self, work: Callable[..., None], *args: Any) -> None:
        thread = threading.Thread(
            target=self._guarded, args=(work, *args), name="save-checker-start", daemon=True
        )
        with self._cache_lock:
            self._start_thread = thread
        thread.start()

    def _guarded(self, work: Callable[..., None], *args: Any) -> None:
        try:
            work(*args)
        except Exception:
            logger.error("save checker: the start thread failed", exc_info=True)

    def _launch(self) -> None:
        try:
            child = self._new_child()
        except Exception as error:
            logger.debug("save checker: the child could not start", exc_info=True)
            self._fallback(f"the child could not start ({type(error).__name__})")
            return
        if child is not None:
            self._bring_up(child)

    def _new_child(self) -> _Child | None:
        target = self._target
        if target is None:
            from . import save_child  # it imports tools, which imports this module

            target = save_child.serve
        context = multiprocessing.get_context("forkserver")
        # The fork server is one per process and the first caller fixes its preload, so both
        # checkers that use it name the same modules.
        context.set_forkserver_preload(PRELOAD)
        parent, child_end = context.Pipe()
        try:
            process = context.Process(target=target, args=(child_end,), daemon=True)
            process.start()
        except BaseException:
            parent.close()
            raise
        finally:
            # The child has its own copy. Closing ours lets a dead child read as end of file.
            child_end.close()
        assert process.pid is not None
        child = _Child(process, parent, process.pid)
        with self._cache_lock:
            self.pids.append(process.pid)
            closed = self._closed
            if not closed:
                self._child = child
        if closed:
            # A close that landed while this started: it found nothing to kill, so this does.
            child.killed = "close"
            self._reap(child)
            return None
        child.reader = threading.Thread(
            target=self._read, args=(child,), name="save-checker-reader", daemon=True
        )
        child.reader.start()
        threading.Thread(
            target=self._watch, args=(child,), name="save-checker-watchdog", daemon=True
        ).start()
        return child

    def _bring_up(self, child: _Child) -> None:
        up = self._next(child, time.monotonic() + START_SECONDS)
        if up.get("kind") != "up":
            # One that is still alive is ended first. After a passed trial it counts as a death,
            # as the watchdog's kills do, so a child that never comes up can't hold the session.
            self._kill(child, "start" if self._trial_passed else "trial", restart=False)
            self._lost(child, f"the child wasn't up within {START_SECONDS:g} s, or ended first")
            return
        since = time.monotonic()
        if not self._trial_passed:
            answer = self._ask(child, {"kind": "trial"}, since + TRIAL_SECONDS)
            if answer is None:
                self._lost(child, f"the trial did not answer within {TRIAL_SECONDS:g} s")
                return
            if answer.get("kind") != "trial":
                self._lost(child, "the child ended before the trial answered")
                return
            if not answer.get("ok"):
                self._lost(child, f"the trial failed: {answer.get('detail')}")
                return
            self._trial_passed = True
        if self._stand_in:
            answer = self._ask(
                child,
                {"kind": "stand_in", "module": self._stand_in},
                time.monotonic() + TRIAL_SECONDS,
            )
            if answer is not None and answer.get("kind") == "gone":
                self._lost(child, "the child ended before the stand-in answered")
                return
            if answer is None or not answer.get("ok"):
                detail = "no answer in time" if answer is None else str(answer.get("detail"))
                with self._cache_lock:
                    self._stand_in_error = (self._stand_in, detail)
                self._kill(child, "stand_in", restart=False)
                self._reap(child)
                self._notify()
                return
        with self._cache_lock:
            gone = child.gone
            if not gone:
                child.ready = True
        if gone:
            self._ended(child)
            return
        self._notify()
        self._ask_session()

    def _lost(self, child: _Child, reason: str) -> None:
        """The child stopped before it was ready: a failed trial, or a death after one passed."""
        if child.killed == "close":
            return
        if self._trial_passed:
            self._ended(child)
            return
        self._fallback(reason)
        self._kill(child, "trial", restart=False)
        self._reap(child)

    def _next(self, child: _Child, until: float | None = None) -> dict[str, Any]:
        """The child's next message to the start thread; `gone` when it ends or is closed, or
        when ``until`` passes first."""
        while True:
            try:
                return child.inbox.get(timeout=0.1)
            except queue.Empty:
                if self._closed or (until is not None and time.monotonic() > until):
                    return _GONE

    def _ask(self, child: _Child, request: dict[str, Any], until: float) -> dict[str, Any] | None:
        rid = next(self._ids)
        with self._cache_lock:
            if child.gone or child.killed is not None:
                return _GONE
            child.replies[rid] = child.inbox.put
            child.request = rid
        answer: dict[str, Any] | None = None
        try:
            self._send(child, {"id": rid, **request})
            while True:
                try:
                    message = child.inbox.get(timeout=max(until - time.monotonic(), 0.0))
                except queue.Empty:
                    break
                if message.get("kind") == "gone" or message.get("id") == rid:
                    answer = message
                    break
        finally:
            with self._cache_lock:
                child.replies.pop(rid, None)
                if child.request == rid:
                    child.request = None
        return answer

    def _send(self, child: _Child, message: dict[str, Any]) -> bool:
        try:
            with self._send_lock:
                child.conn.send(message)
            return True
        except (OSError, ValueError):
            # A child that ended; its reader reports that.
            return False

    def _read(self, child: _Child) -> None:
        while True:
            try:
                message = child.conn.recv()
            except Exception:
                # End of file, a closed pipe, or a message that won't unpickle: the child is gone.
                break
            try:
                self._dispatch(child, message)
            except Exception:
                logger.error("save checker: a message from the child could not be handled", exc_info=True)
        self._gone(child)

    def _dispatch(self, child: _Child, message: Any) -> None:
        if not isinstance(message, dict):
            return
        kind = message.get("kind")
        if kind == "log":
            self._relog(message)
            return
        if kind == "up":
            child.inbox.put(message)
            return
        if kind in _PARTS or (kind == "failed" and message.get("part") in _PARTS):
            self._part(child, message)
            return
        with self._cache_lock:
            reply = child.replies.get(message.get("id"))
        if reply is not None:
            reply(message)

    def _relog(self, message: dict[str, Any]) -> None:
        name = str(message.get("name") or __name__)
        level = message.get("level")
        level = level if isinstance(level, int) else logging.WARNING
        target = logging.getLogger(name)
        if not target.isEnabledFor(level):
            return
        text = str(message.get("message", ""))
        if message.get("trace"):
            text += "\n" + str(message["trace"])
        record = target.makeRecord(name, level, "(save checker child)", 0, text, None, None)
        # Masked here too: the child's logger may be one this process never set a filter on.
        MaskSecrets().filter(record)
        target.handle(record)

    def _gone(self, child: _Child) -> None:
        with self._cache_lock:
            child.gone = True
            ready = child.ready
            replies = list(child.replies.values())
            child.replies.clear()
        child.stop.set()
        child.inbox.put(_GONE)
        for reply in replies:
            reply(_GONE)
        # Before it was ready, the start thread decides what its end means.
        if ready:
            self._ended(child)

    def _ended(self, child: _Child) -> None:
        """A ready child ended. Unless the parent killed it, that is a death."""
        with self._cache_lock:
            if child.handled:
                return
            child.handled = True
            reason = child.killed
            if reason not in (None, "watchdog", "start"):
                return
            self._deaths += 1
            deaths = self._deaths
            missing: list[str] = []
            session = self._session
            if session is not None and session.child is child:
                missing = self._end_session(session)
        code = reason if reason in ("watchdog", "start") else self._exit_code(child)
        logger.error("save checker: the child ended (%s)", code)
        if missing:
            logger.warning(
                "save checker: the child ended during the session's checks; %s counts as cut",
                _which(missing),
            )
        self._notify()
        if deaths >= DEATHS:
            self._fallback(f"the child ended {deaths} times in a row")
            self._spawn(self._reap, child)
        else:
            self._spawn(self._replace, child)

    def _exit_code(self, child: _Child) -> str:
        try:
            child.process.join(0.5)
            code = child.process.exitcode
        except (OSError, ValueError, AssertionError):
            return "exit code unknown"
        return "still running" if code is None else f"exit code {code}"

    def _kill(self, child: _Child, reason: str, *, restart: bool) -> bool:
        """Kills ``child`` for ``reason``; False when it had already ended or been killed."""
        with self._cache_lock:
            if child.killed is not None or child.gone:
                return False
            child.killed = reason
        try:
            child.process.kill()
        except Exception:
            logger.warning("save checker: the child could not be killed", exc_info=True)
        if restart:
            self._spawn(self._replace, child)
        return True

    def _replace(self, old: _Child) -> None:
        # The old reader ends before the new one starts, so one reader reads at a time.
        self._reap(old)
        with self._cache_lock:
            stop = self._gave_way() or self._stand_in_error is not None
        if not stop:
            self._launch()

    def _reap(self, child: _Child) -> None:
        """Kills, joins and closes ``child``, its pipe and its reader. Never raises."""
        with self._cache_lock:
            if child.reaped:
                return
            child.reaped = True
            if child.killed is None and not child.gone:
                child.killed = "reap"
        child.stop.set()
        process = child.process
        try:
            process.kill()
            process.join(JOIN_SECONDS)
        except Exception:
            logger.warning("save checker: the child could not be ended", exc_info=True)
        reader = child.reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(JOIN_SECONDS)
        try:
            child.conn.close()
        except OSError:
            logger.debug("save checker: the pipe was already closed", exc_info=True)
        try:
            # Raises when the join above timed out; the child is then left to the exit handler.
            process.close()
        except Exception:
            logger.warning("save checker: the child could not be closed", exc_info=True)
        with self._cache_lock:
            if self._child is child:
                self._child = None

    def _fallback(self, reason: str) -> None:
        with self._cache_lock:
            if self._thread_reason is not None:
                return
            self._thread_reason = reason
        logger.warning("save checker: running the checks in this process: %s", reason)
        self._notify()

    def _watch(self, child: _Child) -> None:
        while not child.stop.wait(WATCH_INTERVAL):
            with self._cache_lock:
                busy = child.request is not None and child.killed is None and not child.gone
            if not busy:
                continue
            rss = _rss(child.pid)
            if rss is not None and rss > WATCH_CAP:
                self._kill(child, "watchdog", restart=False)
                return
