"""The world's door for tools an agent runs in its own process: `POST /begin` decides, `POST /end` logs the outcome.

Anything this server cannot decide comes back as `run`, so a tool call is never failed by it."""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

try:  # Beside the server in a bundle; the harness package when run from source.
    import tool_trace_proxy as _proxy
    from world_door import (
        DOOR_OUT_OF_REACH,
        EVIDENCE_INCOMPLETE,
        DictRoster,
        Executed,
        Outcome,
        PostgresLog,
        PostgresRecords,
        PostgresRoster,
        RecordExecutor,
        Routed,
        ToolCall,
        WorldDoor,
        claim_worker_stop,
        is_timeout,
        mark_worker_run,
        worker_due,
    )
except ImportError:  # pragma: no cover - imported as part of the package
    from fi.alk.harness import tool_trace_proxy as _proxy  # type: ignore[no-redef]
    from fi.alk.harness.world_door import (  # type: ignore[no-redef]
        DOOR_OUT_OF_REACH,
        EVIDENCE_INCOMPLETE,
        DictRoster,
        Executed,
        Outcome,
        PostgresLog,
        PostgresRecords,
        PostgresRoster,
        RecordExecutor,
        Routed,
        ToolCall,
        WorldDoor,
        claim_worker_stop,
        is_timeout,
        mark_worker_run,
        worker_due,
    )

DATABASE_URL = os.environ.get("DATABASE_URL", "")
# The tools the contract declares read-only, by name: logged as reads.
READ_TOOLS = frozenset(one for one in os.environ.get("READ_ACTIONS", "").split(",") if one)
# Longest the door waits for the agent to report a call it let run; the call itself is the agent's.
RUN_SECONDS = float(os.environ.get("ALK_WORLD_DOOR_RUN_SECONDS", "120"))
# Longest a begin waits for the door to decide.
DECIDE_SECONDS = 30.0

__all__ = ["InProcessDoor", "Handler"]


@dataclass
class _Pending:
    """One call between its begin and its end."""

    decided: threading.Event = field(default_factory=threading.Event)
    ran: bool = False
    ended: threading.Event = field(default_factory=threading.Event)
    executed: Executed | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    outcome: Outcome | None = None
    read: bool = False


def _answered_error(result: Any) -> str | None:
    """The error a tool answered with instead of doing its work (`{"error": ...}`, as text or as an object): the
    tool's own refusal, which a tools service says with a 4xx and an in-process tool says in its return value."""
    value = result
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    if isinstance(value, dict) and value.get("error"):
        return str(value["error"])[:2000]
    return None


class _AgentRuns:
    """The door's executor for a tool the agent runs itself: it lets the call run in the agent's process and waits
    for the agent to say what came of it."""

    def __init__(self, pending: dict[str, _Pending]) -> None:
        self.pending = pending

    def run(self, call: ToolCall) -> Executed:
        waiting = self.pending.get(call.path)
        if waiting is None:
            return Executed(502, None, error="the call is not known to the door")
        waiting.ran = True
        waiting.decided.set()
        if not waiting.ended.wait(RUN_SECONDS):
            return Executed(504, None, error="the agent's tool never reported back")
        return waiting.executed or Executed(502, None, error="the agent's tool reported nothing")


class _NotHere:
    """An actor's action on one of the agent's own tools: the tool lives in the agent's process, where no actor runs."""

    def run(self, call: ToolCall) -> Executed:
        return Executed(
            403, None, error=f"{call.action} runs inside the agent; an actor can act here only through record changes"
        )


class InProcessDoor:
    """The door and its calls in flight."""

    def __init__(self, door: WorldDoor | None = None) -> None:
        self.pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()
        self.door = door if door is not None else WorldDoor(
            Routed(_AgentRuns(self.pending), RecordExecutor(PostgresRecords(DATABASE_URL)) if DATABASE_URL else None),
            PostgresLog(DATABASE_URL),
            PostgresRoster(DATABASE_URL) if DATABASE_URL else DictRoster({}),
            faults=_proxy._trial_faults,
            trial=lambda: _proxy._FAULTS_TRIAL,
            faults_by_tool=True,
        )
        if door is not None and isinstance(getattr(door, "executor", None), Routed):
            door.executor.tools = _AgentRuns(self.pending)

    def begin(self, name: str, arguments: Any, action: str = "", unrecorded: int = 0) -> dict[str, Any]:
        """Whether the agent's tool may run: `{"run": true, "id"}`, or what the scenario's fault answers instead. A
        call the door could not decide or record still runs, and is marked in the log as evidence missing, so the
        trial reads unmeasured; `unrecorded` is how many of this tool's calls ran while the door was out of reach."""
        if unrecorded > 0:
            # One mark for them all, carrying how many: a single log write, however long the door was away.
            missed = ToolCall(action=action or name, name=name, arguments={"count": unrecorded})
            self._unrecorded(missed, DOOR_OUT_OF_REACH)
        call_id = uuid.uuid4().hex
        waiting = _Pending()
        with self._lock:
            self.pending[call_id] = waiting
        call = ToolCall(
            action=action or name,
            name=name,
            arguments=arguments if arguments is not None else {},
            path=call_id,
            method="POST",
            effect="read" if name in READ_TOOLS or action in READ_TOOLS else "",
        )
        waiting.read = call.effect == "read"

        def enter() -> None:
            try:
                waiting.outcome = self.door.enter(call)
            finally:
                waiting.finished.set()
                waiting.decided.set()

        threading.Thread(target=enter, daemon=True).start()
        if not waiting.decided.wait(DECIDE_SECONDS) or (waiting.ran is False and waiting.outcome is None):
            # Undecided in time: the tool runs as it would without a door, never failed by it, and the trial's
            # evidence says a call went unrecorded.
            self._forget(call_id)
            self._unrecorded(call, "the world's door did not decide in time")
            return {"run": True, "id": "", "door": "undecided"}
        if waiting.ran:
            return {"run": True, "id": call_id}
        self._forget(call_id)
        if not _decided(waiting.outcome):
            # The door itself failed, deciding nothing: the tool runs as it would without one, marked.
            self._unrecorded(call, "the world's door failed")
            return {"run": True, "id": "", "door": "failed"}
        # Held on purpose: a scenario's fault, a refusal (an approval not given) or an approval that could not be read.
        return {"run": False, **_held(waiting.outcome)}

    def _unrecorded(self, call: ToolCall, why: str) -> None:
        """Marks a call of the agent's that ran without the door recording what it did."""
        self.door.record(call, Outcome("unrecorded", 0, reason=why, refusal_code=EVIDENCE_INCOMPLETE))

    def end(self, call_id: str, ok: bool, result: Any, error: str = "") -> dict[str, Any]:
        """What the door made of a call the agent ran: `lost` with the delay when the scenario's fault loses its
        answer."""
        with self._lock:
            waiting = self.pending.get(call_id)
        if waiting is None:
            return {"lost": False}
        # A read that answers "not found" still read; only a write that answered an error did not do its work.
        answered = _answered_error(result) if ok and not waiting.read else None
        waiting.executed = (
            Executed(400, result, error=answered)
            if answered
            else Executed(200, result)
            if ok
            else Executed(400, result, error=str(error or "the tool raised")[:2000])
        )
        waiting.ended.set()
        waiting.finished.wait(DECIDE_SECONDS)
        self._forget(call_id)
        outcome = waiting.outcome
        if outcome is not None and outcome.fault is not None and is_timeout(outcome.fault):
            return {"lost": True, "delay_seconds": outcome.delay_seconds, "outcome": outcome.outcome}
        return {"lost": False, "outcome": outcome.outcome if outcome is not None else ""}

    def _forget(self, call_id: str) -> None:
        with self._lock:
            self.pending.pop(call_id, None)


def _int_or_zero(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _decided(outcome: Outcome | None) -> bool:
    """Whether the door held the call on purpose, as opposed to failing to decide anything."""
    return outcome is not None and (
        outcome.fault is not None or outcome.outcome == "refused" or outcome.refusal_code is not None
    )


def _held(outcome: Outcome) -> dict[str, Any]:
    """What the agent's tool says instead of running, from the scenario's fault the door applied."""
    fault = outcome.fault or {}
    detail = outcome.reason or outcome.outcome
    if outcome.body is not None:
        try:
            detail = str(json.loads(outcome.body).get("detail") or detail)
        except (ValueError, AttributeError):
            pass
    return {
        "mode": str(fault.get("mode") or ""),
        "status": outcome.status,
        "detail": detail,
        "delay_seconds": outcome.delay_seconds,
        "outcome": outcome.outcome,
    }


def worker_remaining(worker: str) -> dict[str, Any]:
    """How long a worker has left this trial, `{"remaining": seconds}`; nothing when no deadline names it or none can
    be read, and the worker then runs on. Asked only from a run the door can stop, which is kept: a worker with a
    deadline that never ran so is recorded at the seal as not enforced."""
    row = _proxy._trial_row("worker_deadline_at, worker_deadlines")
    if not row:
        return {}
    named = row[1] if isinstance(row[1], dict) else {}
    due = worker_due(named.get(worker), row[0])
    if due is None:
        return {}
    try:
        mark_worker_run(DATABASE_URL, worker)
    except Exception:  # noqa: BLE001 - an unkept run reads as not enforced, never as a failed call
        pass
    return {"remaining": max(float(due) - time.time(), 0.0)}


@dataclass
class _Stop:
    """One worker's stop between its claim and the agent's word on whether the worker's run ended."""

    claimed: threading.Event = field(default_factory=threading.Event)
    inside: bool = False
    ended: threading.Event = field(default_factory=threading.Event)
    stopped: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    kept: bool = False


_STOPS: dict[str, _Stop] = {}
_STOPS_LOCK = threading.Lock()


def worker_stop(worker: str) -> dict[str, Any]:
    """Claim a worker's stop before the agent's process cancels its run: `{"stop": true, "id"}` while the trial's row
    is held for it, until `worker_stop_end` says whether the run ended; `{"stop": false}` when the call has sealed its
    stops or the deadline no longer holds, and the worker runs on."""
    if not DATABASE_URL or not worker:
        return {"stop": False}
    stop_id, one = uuid.uuid4().hex, _Stop()
    with _STOPS_LOCK:
        _STOPS[stop_id] = one

    def stop() -> bool:
        one.inside = True
        one.claimed.set()
        # Unreported in time: the run is taken as not ended, and kept as not stopped (not held to its deadline).
        one.ended.wait(DECIDE_SECONDS)
        return one.stopped

    def claim() -> None:
        try:
            one.kept = claim_worker_stop(DATABASE_URL, worker, "", stop)
        except Exception:  # noqa: BLE001 - a world out of reach keeps no stop, and the worker runs on
            one.kept = False
        finally:
            one.claimed.set()
            one.done.set()

    threading.Thread(target=claim, daemon=True).start()
    if one.claimed.wait(DECIDE_SECONDS) and one.inside:
        return {"stop": True, "id": stop_id}
    with _STOPS_LOCK:
        _STOPS.pop(stop_id, None)
    return {"stop": False}


def worker_stop_end(stop_id: str, stopped: bool) -> dict[str, Any]:
    """Whether the worker's run ended at its cancel, from the agent's process: kept as stopped only when it did."""
    with _STOPS_LOCK:
        one = _STOPS.pop(stop_id, None)
    if one is None:
        return {"kept": False}
    one.stopped = stopped
    one.ended.set()
    one.done.wait(DECIDE_SECONDS)
    return {"kept": one.kept}


_DOOR: InProcessDoor | None = None
_DOOR_LOCK = threading.Lock()


def _door() -> InProcessDoor:
    global _DOOR
    with _DOOR_LOCK:
        if _DOOR is None:
            _DOOR = InProcessDoor()
        return _DOOR


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._reply(200 if self.path == "/health" else 404, {})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        size = int(self.headers.get("content-length") or 0)
        try:
            body = json.loads(self.rfile.read(size) or b"{}") if size else {}
        except ValueError:
            body = {}
        body = body if isinstance(body, dict) else {}
        if self.path == "/begin":
            self._reply(200, _door().begin(str(body.get("name") or "unknown"), body.get("arguments"),
                                           str(body.get("action") or ""), _int_or_zero(body.get("unrecorded"))))
        elif self.path == "/end":
            self._reply(200, _door().end(str(body.get("id") or ""), bool(body.get("ok")), body.get("result"),
                                         str(body.get("error") or "")))
        elif self.path == "/worker_deadline":
            self._reply(200, worker_remaining(str(body.get("worker") or "")))
        elif self.path == "/worker_stop":
            self._reply(200, worker_stop(str(body.get("worker") or "")))
        elif self.path == "/worker_stop_end":
            self._reply(200, worker_stop_end(str(body.get("id") or ""), body.get("stopped") is True))
        else:
            self._reply(404, {})

    def _reply(self, status: int, payload: object) -> None:
        content = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
