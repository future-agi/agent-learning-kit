"""Runs the contract tool's checks in a child process the save checker can stop.

The checks read the customer's files, and Python's parser holds the GIL while it reads one, so
in a thread of the conversation's process a crafted repository can hold the conversation for
seconds. In a child, the parent stops waiting at the save's deadline and kills it. The child runs
the same ``_checked_contract`` the thread fallback runs, so both paths share one code path.

Every check is looked up through its module when it is called, so a test's stand-in, which
replaces a module's attribute in the child, reaches it.
"""

from __future__ import annotations

import copy
import importlib
import logging
import os
import shutil
import tempfile
import threading
import traceback
from collections.abc import Callable
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

from . import agent_type_check, instrumentation_evidence, source_folder, tools
from .contract import AgentContract
from .reading_entry import MaskSecrets

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

# The trial's repository: the folder imports a browser tool, and the traced import sits in code
# outside the folder, so the trial passes only when the scan finds something and instrumentation
# reads what the scan reached. A tree with neither passes even when the child's scan is broken.
TRIAL_TREE = {
    "agent/main.py": "from lib import trace\nimport playwright\n\n\ndef run(x):\n    return x\n",
    "agent/compose.yaml": "services:\n  agent:\n    build: .\n",
    "lib/__init__.py": "",
    "lib/trace.py": "import traceai_openai\n",
}
TRIAL_SUBFOLDER = "agent"
TRIAL_PAYLOAD: dict[str, Any] = {
    "agent": "trial",
    "modality": "chat",
    "real_use_cases": ["o"],
    "hard_constraints": ["be polite"],
    "tools": [{"name": "t0", "args": ["x"]}],
    "tool_entrypoints": [{"tool": "t0", "mode": "import", "module": "main", "callable": "run"}],
    "system_prompt_from": [{"path": "agent/main.py", "start_line": 1, "end_line": 3}],
    "data_store": {"kind": "none"},
    "runtime": {"language": "python", "compose_file": "agent/compose.yaml"},
}
# The order _checked_contract reports its stages in; the trial holds the child to it.
_STAGE_ORDER = ["contract", "tool_evidence", "prompt_copy", "model", "runtime"]
# A stand-in may only replace the kit's own checks.
_PACKAGE = "fi.alk.harness"

Send = Callable[[dict[str, Any]], None]


class _PipeGone(BaseException):
    """The parent's end of the pipe is closed. A BaseException, so that no check's own
    ``except Exception`` mistakes it for a fault in the customer's files: the serve loop ends."""


class _Pipe:
    """The child's end of the pipe, shared by the serve loop and the log handler. One lock
    around each send, so a line logged from another thread never lands inside a reply."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self._lock = threading.RLock()

    def send(self, message: dict[str, Any]) -> None:
        try:
            with self._lock:
                self.conn.send(message)
        except OSError as gone:
            raise _PipeGone from gone


class PipeLogHandler(logging.Handler):
    """Sends each record to the parent as text, which logs it on its own logger of that name.
    A record, an exception or a path can't cross the pipe, so only strings and numbers do."""

    def __init__(self, pipe: _Pipe) -> None:
        super().__init__()
        # No MaskSecrets here: reading the secret values can log, and on the root that line would
        # come back through this handler's mask again. The kit's loggers mask their own lines.
        self._pipe = pipe

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.exc_text:
                # A masked logger keeps a trace only as this text, having cleared exc_info.
                trace: str | None = record.exc_text
            elif record.exc_info:
                trace = logging.Formatter().formatException(record.exc_info)
            else:
                trace = None
            message = {
                "id": None,
                "kind": "log",
                "name": record.name,
                "level": record.levelno,
                "message": record.getMessage(),
                "trace": trace,
            }
        except Exception:
            self.handleError(record)
            return
        try:
            self._pipe.send(message)
        except _PipeGone:
            # The parent closed its end, so this child is going away; its last lines go with it.
            pass


class _Held(dict[str, str]):
    """The reading's held quotes, recording every key the save writes. Only those go back, so
    a quote another call wrote meanwhile isn't overwritten with the one this call was given."""

    def __init__(self, given: dict[str, str]) -> None:
        super().__init__(given)
        self.written: set[str] = set()

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self.written.add(key)

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def setdefault(self, key: str, default: Any = None) -> Any:  # type: ignore[override]
        if key not in self:
            self[key] = default
        return self[key]

    def delta(self) -> dict[str, str]:
        return {key: self[key] for key in sorted(self.written) if key in self}


def _path(value: str | None) -> Path | None:
    return Path(value) if value is not None else None


def _failed(rid: int, part: str, error: BaseException) -> dict[str, Any]:
    return {
        "id": rid,
        "kind": "failed",
        "part": part,
        "error": type(error).__name__,
        "message": str(error),
        "trace": traceback.format_exc(),
    }


def _session_job(
    send: Send,
    rid: int,
    scan_root: Path | None,
    source_root: Path | None,
    subfolder: str | None,
) -> None:
    """The browser scan and the instrumentation check, each sent as soon as it answers."""
    reach: list[Path] = []
    scanned = False

    def scan() -> None:
        nonlocal scanned
        found, cut = tools._tools_in_agent_folder(scan_root, subfolder, reach)
        scanned = True
        send({"id": rid, "kind": "scan", "found": list(found), "cut": sorted(cut)})

    def instrumentation() -> None:
        send({"id": rid, "kind": "instrumentation", "value": traced()})

    def traced() -> str:
        if source_root is None:
            return "none"
        if not subfolder:
            return instrumentation_evidence.detect_instrumentation(source_root)
        try:
            folder = source_folder.agent_folder(source_root, subfolder)
            # A scan that raised may have filled only part of its reach, and one of another
            # root reached other files: either way the agent's reach is walked afresh.
            reached = (
                reach
                if scanned and scan_root is not None and scan_root == source_root
                else agent_type_check.code_the_agent_reaches(source_root, subfolder)
            )
        except source_folder.SubfolderNotFound:
            # The save refuses the folder itself; instrumentation has nothing to read.
            return "none"
        return instrumentation_evidence.detect_instrumentation(folder, reached)

    # With a folder, instrumentation reads what the scan reached, so the scan goes first.
    parts = (
        [("scan", scan), ("instrumentation", instrumentation)]
        if subfolder
        else [("instrumentation", instrumentation), ("scan", scan)]
    )
    for part, run in parts:
        try:
            run()
        except Exception as error:
            send(_failed(rid, part, error))


def _save_job(send: Send, request: dict[str, Any]) -> None:
    """``_checked_contract`` on one payload: a ``stage`` per stage as it ends, then one
    ``done``, or ``failed`` when it raised."""
    rid = request["id"]
    try:
        asked = set(request["asked"])
        held = _Held(request["held"])

        def report(stage: str, problems: list[str]) -> None:
            send(
                {
                    "id": rid,
                    "kind": "stage",
                    "stage": stage,
                    "problems": [str(problem) for problem in problems],
                    "asked": sorted(asked),
                    "held": held.delta(),
                }
            )

        result = tools._checked_contract(
            request["payload"],
            source_root=_path(request["source_root"]),
            require_setup_contract=bool(request["require_setup_contract"]),
            subfolder=request["subfolder"],
            instrumentation=request["instrumentation"],
            asked=asked,
            held=held,
            progress=report,
        )
        if isinstance(result, AgentContract):
            contract, response = result.model_dump(mode="json"), None
        elif isinstance(result, dict):
            contract, response = None, result
        else:
            raise TypeError(
                f"the save checks answered with a {type(result).__name__}, "
                "not a contract or a response"
            )
    except Exception as error:
        send(_failed(rid, "save", error))
        return
    send(
        {
            "id": rid,
            "kind": "done",
            "contract": contract,
            "response": response,
            "asked": sorted(asked),
            "held": held.delta(),
        }
    )


def _trial_mismatch() -> str:
    """Runs both jobs on the trial's tree, keeping their messages here, and names the first
    thing that isn't as the real checks answer it; empty when everything is."""
    heard: list[dict[str, Any]] = []
    root = Path(tempfile.mkdtemp(prefix="alk-trial-"))
    try:
        for name, text in TRIAL_TREE.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        _session_job(heard.append, 0, root, root, TRIAL_SUBFOLDER)
        value = next((m["value"] for m in heard if m["kind"] == "instrumentation"), "none")
        _save_job(
            heard.append,
            {
                "id": 0,
                "payload": copy.deepcopy(TRIAL_PAYLOAD),
                "source_root": str(root),
                "subfolder": TRIAL_SUBFOLDER,
                # The trial's payload declares no setup, which the stricter check refuses.
                "require_setup_contract": False,
                "instrumentation": value,
                "asked": [],
                "held": {},
            },
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return _first_mismatch(heard)


def _answer_of(heard: list[dict[str, Any]], kind: str, part: str) -> tuple[dict[str, Any], str]:
    for message in heard:
        if message["kind"] == kind:
            return message, ""
        if message["kind"] == "failed" and message["part"] == part:
            return message, f"the {part} raised {message['error']}: {message['message']}"
    return {}, f"no {kind} answer"


def _first_mismatch(heard: list[dict[str, Any]]) -> str:
    scan, why = _answer_of(heard, "scan", "scan")
    if why:
        return why
    if scan["found"] != ["playwright"]:
        return f"the scan found {scan['found']}, not ['playwright']"
    traced, why = _answer_of(heard, "instrumentation", "instrumentation")
    if why:
        return why
    if traced["value"] != "traceai":
        return f"instrumentation read {traced['value']!r}, not 'traceai'"
    done, why = _answer_of(heard, "done", "save")
    if why:
        return why
    stages = [m["stage"] for m in heard if m["kind"] == "stage"]
    if stages != _STAGE_ORDER:
        return f"the save reported the stages {stages}, not {_STAGE_ORDER}"
    if heard[-1] is not done:
        return "the save answered before its last stage"
    if done["contract"] is None or done["response"] is not None:
        return "the save refused the trial's contract"
    return ""


def _trial(send: Send, request: dict[str, Any]) -> None:
    try:
        detail = _trial_mismatch()
    except Exception as error:
        # The trial's own fault fails the trial; it isn't a part of a job to report as failed.
        detail = f"{type(error).__name__}: {error}"
    send({"id": request["id"], "kind": "trial", "ok": not detail, "detail": detail})


def _install_stand_in(name: str) -> None:
    """Replaces each ``(module, attribute)`` in the stand-in's ``STAGES`` with its value, all or
    none, so a stand-in naming a check that isn't there changes nothing."""
    stages = importlib.import_module(name).STAGES
    found = []
    for (target, attribute), value in stages.items():
        full = target if target.startswith(f"{_PACKAGE}.") else f"{_PACKAGE}.{target}"
        module = importlib.import_module(full)
        if not hasattr(module, attribute):
            raise AttributeError(f"{full} has no {attribute} to stand in for")
        found.append((module, attribute, value))
    for module, attribute, value in found:
        setattr(module, attribute, value)


def _stand_in(send: Send, request: dict[str, Any]) -> None:
    try:
        _install_stand_in(request["module"])
    except Exception as error:
        detail = f"{type(error).__name__}: {error}"
        send({"id": request["id"], "kind": "stand_in", "ok": False, "detail": detail})
        return
    send({"id": request["id"], "kind": "stand_in", "ok": True, "detail": ""})


def _session(send: Send, request: dict[str, Any]) -> None:
    _session_job(
        send,
        request["id"],
        _path(request["scan_root"]),
        _path(request["source_root"]),
        request["subfolder"],
    )


_JOBS: dict[str, Callable[[Send, dict[str, Any]], None]] = {
    "trial": _trial,
    "stand_in": _stand_in,
    "session": _session,
    "save": _save_job,
}


def serve(conn: Connection, *, install_logging: bool = True) -> None:
    """The child's loop: ``up``, then one request at a time, in order, until the parent's end
    closes. With ``install_logging`` the child's log lines go to the parent instead of its own
    handlers, which are put back, with the level, when the loop ends."""
    pipe = _Pipe(conn)
    root = logging.getLogger()
    kept_handlers, kept_level = list(root.handlers), root.level
    handler = PipeLogHandler(pipe) if install_logging else None
    if handler is not None:
        for old in kept_handlers:
            root.removeHandler(old)
        root.addHandler(handler)
    try:
        pipe.send({"id": None, "kind": "up", "pid": os.getpid()})
        while True:
            try:
                request = conn.recv()
            except (EOFError, OSError):
                return
            job = _JOBS.get(request.get("kind")) if isinstance(request, dict) else None
            if job is None:
                # The parent sends only the kinds above and waits on no other, so a reply would
                # have no reader.
                logger.warning("save child: a request of a kind it doesn't know was dropped")
                continue
            level = request.get("log_level")
            if handler is not None and isinstance(level, int):
                # The parent's level, so the child sends only what the parent would log.
                root.setLevel(level)
            try:
                job(pipe.send, request)
            except Exception:
                # A malformed request; the loop goes on for the next one.
                logger.exception("save child: a %s request could not be answered", request["kind"])
    except _PipeGone:
        return
    finally:
        if handler is not None:
            root.removeHandler(handler)
            for old in kept_handlers:
                root.addHandler(old)
            root.setLevel(kept_level)
