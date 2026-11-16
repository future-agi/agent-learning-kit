"""The one way into a world: every surface builds a `ToolCall` and calls `WorldDoor.enter`.

Never raises into its caller. Copied into each bundle: standard library only, plus `psycopg` for Postgres."""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib import error, request

# The agent under test in the action log; no actor takes this id.
AGENT = "agent"
OUTCOMES = ("committed", "refused", "faulted", "duplicate")
# Longest a scenario's fault holds a call, and how long a log write may hold the answer before the row is given up.
MAX_FAULT_SECONDS = 30.0
# Every fault a scenario can set on one of the agent's calls, and the two that hold the call before answering.
FAULT_MODES = ("timeout_before_commit", "timeout_after_commit", "unavailable", "error", "approval_required")
TIMEOUT_MODES = ("timeout_before_commit", "timeout_after_commit")
# The fault that holds every write of an action until an actor has recorded an approval, and its refusal's code.
APPROVAL_REQUIRED = "approval_required"
APPROVAL_MISSING = "approval_missing"
LOG_WRITE_SECONDS = 2.0
# A record change an actor may be permitted beside the world's tools: `<collection>.change` or `<collection>.put`.
logger = logging.getLogger(__name__)
RECORD_VERBS = ("change", "put")
# How a tools service learns which person an actor's call is from: the actor's identity in that service.
IDENTITY_HEADER = "X-ALK-Actor-Identity"
# HTTP methods that only read: a call made with one changes nothing in the world.
READ_METHODS = ("GET", "HEAD", "OPTIONS")
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
# The code of a row that marks a call of the agent's which ran without the door deciding or recording it: the trial's
# evidence is incomplete. Never a call or a change itself.
EVIDENCE_INCOMPLETE = "evidence_incomplete"
# Why such a mark was written for calls made while the in-process door could not be reached; it carries their `count`.
DOOR_OUT_OF_REACH = "the world's door was out of reach"
# The code of a row the egress proxy writes for a request to a host outside the test world (its `REFUSAL_CODE`).
EGRESS_BLOCKED = "egress_blocked"


def is_attempt(row: Mapping[str, Any]) -> bool:
    """Whether a log row is an action someone attempted in the world: never a stopped request to a host outside it,
    nor a mark that a call ran unrecorded."""
    return row.get("refusal_code") not in (EGRESS_BLOCKED, EVIDENCE_INCOMPLETE)


class EvidenceUnavailable(Exception):
    """The trial's action log could not be read: a check that needs it is unmeasured, never passed on an empty log."""


@dataclass(frozen=True)
class ToolCall:
    """One attempt to act in the world, as every surface describes it."""

    # The world action, `<service>.<action>`: the route the call takes with `/` read as `.`.
    action: str
    # What the action log records it as: the route's last part, or the tool's own name.
    name: str
    arguments: Any = field(default_factory=dict)
    actor: str = AGENT
    # A retry with the same key is answered from the first result, the agent's (its `Idempotency-Key`) and an actor's.
    key: str | None = None
    # The action that caused this one (an actor answering the agent).
    parent: str | None = None
    # Whether the surface proved the actor is who it says (a token on HTTP; the harness itself in process).
    proven: bool = True
    # `tool`, or `record` for a record change on one of the world's collections (`approvals.change`).
    form: str = "tool"
    # What an HTTP executor forwards as is; empty for a call made in process.
    method: str = "POST"
    path: str = ""
    body: bytes | None = None
    content_type: str = "application/json"
    # Who the actor is in the service it acts in (its identity there), sent to the service beside the call.
    identity: str = ""
    # The agent's worker that made the call, from the channel it came in on: None when it came in on no worker's
    # channel, "" when the channel belongs to no worker of this trial.
    worker: str | None = None
    # Why a call is the coordinator's though this trial gave its workers channels: it came in on the shared tools
    # address (`SHARED_ADDRESS`). Attributed, not isolated: any worker can call that address, and the log says so.
    attribution: str | None = None
    # The agent's own name for the tool this action is, when the surface knows it (a route is often named otherwise,
    # `<tool>` -> `<resource>.get`); kept beside `name` so grading reads the tool the agent called.
    tool: str = ""
    # `read` or `write` as the tool's contract declares it; empty reads the HTTP method (see `call_effect`).
    effect: str = ""


@dataclass
class Executed:
    """What running a call gave back: its status as HTTP reads it, the result, and the raw answer when there is one."""

    status: int
    result: Any = None
    body: bytes | None = None
    content_type: str | None = "application/json"
    error: str = ""


@dataclass
class Outcome:
    """What the door made of one call. `fault` is the scenario's timeout when one applied: the surface holds the
    answer for its `delay_seconds` and then says the call timed out."""

    outcome: str
    status: int
    result: Any = None
    body: bytes | None = None
    content_type: str | None = "application/json"
    reason: str = ""
    refusal_code: str | None = None
    fault: dict[str, Any] | None = None
    action_id: str = ""

    @property
    def delay_seconds(self) -> float:
        if not self.fault or not is_timeout(self.fault):
            return 0.0
        try:
            return min(
                max(float(self.fault.get("delay_seconds", MAX_FAULT_SECONDS)), 0.0),
                MAX_FAULT_SECONDS,
            )
        except (TypeError, ValueError):
            return MAX_FAULT_SECONDS

    def answer(self) -> bytes:
        """The answer's bytes for a surface that speaks HTTP: the executor's own, else the result or the reason."""
        if self.body is not None:
            return self.body
        if self.outcome in ("committed", "duplicate") and self.result is not None:
            return json.dumps(self.result).encode()
        return json.dumps({"detail": self.reason or self.outcome}).encode()


class Executor(Protocol):
    def run(self, call: ToolCall) -> Executed: ...


class Roster(Protocol):
    def actor(self, actor_id: str) -> Mapping[str, Any] | None: ...


class ActionLog(Protocol):
    def records_actors(self) -> bool: ...

    def write(self, row: dict[str, Any]) -> None: ...

    def rows(self) -> list[dict[str, Any]]: ...

    def committed(self, key: str, actor: str) -> tuple[str | None, Any, Any] | None:
        """The action, arguments and result an actor's key first committed; the action is None on a row written
        before actions were recorded."""
        ...

    def claim(self, key: str, actor: str) -> str | None:
        """Claim an actor's key before its action runs: None when this call claimed it, else the state a door before
        this one left it in (`pending`, `committed`, `unknown`). Raises when the claim cannot be kept."""
        ...

    def settle(self, key: str, actor: str, state: str | None) -> None:
        """A claimed key's state once its action ran; None releases it (refused: nothing took effect)."""
        ...

    def agent_calls(self, action: str, name: str) -> int: ...


def logged_as(row: Mapping[str, Any]) -> str:
    """The world action a log row is a call of, in full; a row written before actions were recorded has only its
    name."""
    return str(row.get("action") or row.get("name") or "")


def action_matches(logged: str, action: str) -> bool:
    """Whether a logged world action is this one. A `{part}` in `action` (a route's template, `orders.{id}.cancel`)
    stands for any one part."""
    if not action or logged == action:
        return bool(action)
    if "{" not in action:
        return False
    pattern = ".".join(
        "[^.]+" if part.startswith("{") and part.endswith("}") else re.escape(part) for part in action.split(".")
    )
    return re.fullmatch(pattern, logged) is not None


def calls_tool(logged: str, tool: str) -> bool:
    """Whether a logged world action is a call an `on_tool` trigger waits for. A trigger names the world action in
    full; one that names only a tool (no route to read it from) is matched by the logged action's last part, as before
    actions were logged in full."""
    return action_matches(logged, tool) or ("." not in tool and bool(tool) and logged.rsplit(".", 1)[-1] == tool)


def same_action(row: Mapping[str, Any], action: str) -> bool:
    """Whether a log row is a call of this world action. A row names its action in full (`service_a.update`), so
    `ticket.update` is never taken for it; one written before actions were recorded has only its name, the action
    itself or its last part (`update`)."""
    if row.get("action"):
        return action_matches(str(row["action"]), action)
    return bool(action) and str(row.get("name") or "") in (action, action.rsplit(".", 1)[-1])


def row_arguments(value: Any) -> Any:
    """A row's arguments as data: a log read back from the database can hold them as JSON text."""
    if isinstance(value, (str, bytes)):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def is_committed(row: Mapping[str, Any]) -> bool:
    """Whether a log row is a committed action; a row from before outcomes were logged says so by `ok`."""
    return (row.get("outcome") or ("committed" if row.get("ok", True) else "")) == "committed"


def named_alike(action: str, name: str) -> bool:
    """A fault names the world action (`service_a.update`); a call may carry the tool's own name (`update`)."""
    return bool(action) and bool(name) and (action == name or action.rsplit(".", 1)[-1] == name.rsplit(".", 1)[-1])


def fixed_fields(solution: Sequence[Any], action: str) -> set[str]:
    """The arguments the reference solution fixes for this action, over every step that takes it."""
    fields: set[str] = set()
    for step in solution or ():
        step = step.model_dump() if hasattr(step, "model_dump") else step
        if isinstance(step, Mapping) and named_alike(action, str(step.get("tool") or "")):
            fields |= set(step.get("fixed") or {})
    return fields


def same_write(first: Mapping[str, Any], later: Mapping[str, Any], fixed: set[str]) -> bool:
    """Whether two writes of one action are the same write: equal arguments, or equal on every fixed argument either
    carries and on every other argument that is not text. Text the solution does not fix is the agent's own words, so
    a retry that rewords it is still the same write; a different record, named by a value that is not text, is not."""
    one, two = row_arguments(first.get("arguments")), row_arguments(later.get("arguments"))
    if one == two:
        return True
    if not isinstance(one, dict) or not isinstance(two, dict):
        return False
    carried = [field for field in fixed if field in one or field in two]
    if not carried:
        return False
    others = (set(one) | set(two)) - set(fixed)
    return all(one.get(field) == two.get(field) for field in carried) and all(
        one.get(field) == two.get(field)
        for field in others
        if not isinstance(one.get(field), str) or not isinstance(two.get(field), str)
    )


def call_effect(call: ToolCall) -> str:
    """Whether a call reads or writes the world: a record change writes; else the tool's declared effect; else a
    read-only HTTP method reads. Anything undeclared and not read-only counts as a write, as every call did before."""
    if call.form == "record":
        return "write"
    if call.effect in ("read", "write"):
        return call.effect
    return "read" if (call.method or "").upper() in READ_METHODS else "write"


def is_read(row: Mapping[str, Any]) -> bool:
    """Whether a log row is a read: committed or not, it changed nothing. A row from before effects were logged is
    a write."""
    return row.get("effect") == "read"


def world_action(route: str) -> str:
    """A route or endpoint as the world names the action it takes: the path without its query, `/` read as `.`.
    Every surface (the proxy, the sealed allow-lists, the service routes) names actions through this one rule."""
    return str(route or "").split("?", 1)[0].strip("/").replace("/", ".")


# What this door enforces, sealed beside it in a bundle: a runner reads it rather than assume a newer door than shipped.
DOOR_FILE = "door.json"
DOOR_CAPABILITIES = {"worker_deadlines": "per_worker"}
# A worker's call refused because the world could not read its limits: an infrastructure failure, not the worker's.
LIMITS_UNREADABLE = "limits_unreadable"
# The attribution of a call that reached the door on the shared tools address while workers had channels.
SHARED_ADDRESS = "shared_address"

# Kept in the world's database, never in a file the agent's process could read or rewrite.
# Known limit: a connection with the world's role can write it until per-user database isolation is on.
TRIAL_FAULTS_DDL = "CREATE TABLE IF NOT EXISTS _alk_trial_faults (id INT PRIMARY KEY, faults JSONB NOT NULL)"
# When this trial's workers stop being allowed to act, beside its faults; null when the scenario sets no budget.
TRIAL_DEADLINE_DDL = "ALTER TABLE _alk_trial_faults ADD COLUMN IF NOT EXISTS worker_deadline_at DOUBLE PRECISION"
# Each named worker's own deadline, {worker id: epoch seconds}, when the scenario gives a budget per worker.
TRIAL_WORKER_DEADLINES_DDL = "ALTER TABLE _alk_trial_faults ADD COLUMN IF NOT EXISTS worker_deadlines JSONB"
# When this trial's call read its stopped workers to seal them; a worker's process is never ended after it, so a stop
# is sealed or never made. Null until then, and again at each trial's start.
TRIAL_SEALED_DDL = "ALTER TABLE _alk_trial_faults ADD COLUMN IF NOT EXISTS sealed_at DOUBLE PRECISION"


# The workers the harness stopped in this trial, written by whoever ended a worker past its deadline (the scheduler ends
# its process, the in-process door its task); cleared at each trial's start.
WORKER_STOPS_DDL = (
    "CREATE TABLE IF NOT EXISTS _alk_worker_stops "
    "(worker TEXT PRIMARY KEY, process TEXT NOT NULL, at DOUBLE PRECISION NOT NULL, reason TEXT NOT NULL)"
)

# The workers the in-process door saw run as a task of their own this trial, so ones it could stop at their deadline;
# only the run shows how the framework runs a worker. Cleared at each trial's start.
WORKER_RUNS_DDL = "CREATE TABLE IF NOT EXISTS _alk_worker_runs (worker TEXT PRIMARY KEY, at DOUBLE PRECISION NOT NULL)"


# The world databases whose trial tables this process has made, so the schema is changed once per world, never at
# each trial: an ALTER takes the table's lock, which a stop under way holds.
_TRIAL_SCHEMA_MADE: set[str] = set()
_TRIAL_SCHEMA_LOCK = threading.Lock()


def _trial_schema(address: str) -> None:
    """Makes this world's trial tables and columns, once per world database in this process."""
    with _TRIAL_SCHEMA_LOCK:
        if address in _TRIAL_SCHEMA_MADE:
            return
        import psycopg

        with psycopg.connect(address, autocommit=True, connect_timeout=5) as connection:
            for statement in (
                TRIAL_FAULTS_DDL, TRIAL_DEADLINE_DDL, TRIAL_WORKER_DEADLINES_DDL, TRIAL_SEALED_DDL, WORKER_STOPS_DDL,
                WORKER_RUNS_DDL,
            ):
                connection.execute(statement)
        _TRIAL_SCHEMA_MADE.add(address)


def _on_trial_tables(address: str, work: Callable[[], Any]) -> Any:
    """Runs `work` on this world's trial tables, making them first; once more after making them again when the world's
    database was made anew since (a reset from its seed drops them)."""
    import psycopg

    _trial_schema(address)
    try:
        return work()
    except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn):
        with _TRIAL_SCHEMA_LOCK:
            _TRIAL_SCHEMA_MADE.discard(address)
        _trial_schema(address)
        return work()


def claim_worker_stop(address: str, worker: str, process: str, stop: Callable[[], bool]) -> bool:
    """End this worker's run with `stop` and keep that it ended, unless the call has already sealed its stops: the
    trial's row stays locked from the check to the record, so the seal (`read_worker_stops`) waits for a stop under
    way and a stop never lands after it. True when the run was ended and kept."""
    import psycopg

    def claim() -> bool:
        with psycopg.connect(address, connect_timeout=5) as connection:
            with connection.transaction():
                row = connection.execute(
                    "SELECT sealed_at, worker_deadline_at, worker_deadlines FROM _alk_trial_faults WHERE id = 1 "
                    "FOR UPDATE"
                ).fetchone()
                if row is None or row[0] is not None:
                    return False
                # Read again under the lock: the deadline the watcher saw may since have been cleared with the call.
                named = row[2] if isinstance(row[2], dict) else {}
                due = named.get(worker, row[1])
                if not isinstance(due, (int, float)) or time.time() <= due:
                    return False
                if not stop():
                    return False
                connection.execute(
                    "INSERT INTO _alk_worker_stops (worker, process, at, reason) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (worker) DO NOTHING",
                    (worker, process, time.time(), "deadline_passed"),
                )
        return True

    return bool(_on_trial_tables(address, claim))


def mark_worker_run(address: str, worker: str) -> None:
    """Keep that this worker ran as a task of its own in this trial, which the in-process door can stop."""
    import psycopg

    def mark() -> None:
        with psycopg.connect(address, autocommit=True, connect_timeout=5) as connection:
            connection.execute(
                "INSERT INTO _alk_worker_runs (worker, at) VALUES (%s, %s) ON CONFLICT (worker) DO NOTHING",
                (worker, time.time()),
            )

    _on_trial_tables(address, mark)


_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def method_action(method: str, action: str) -> str:
    """A world action with the HTTP method that takes it (`GET <resource>.{id}`): two tools on one path are two
    actions. Sealed routes and worker allow-lists are named this way."""
    return f"{(method or 'POST').upper()} {action}"


def _split(value: str) -> tuple[str, str]:
    """The method and path of `GET <resource>.get`, or no method for a bare action (`<resource>.get`), which any method
    takes."""
    method, _, path = str(value).partition(" ")
    return (method, path) if path and method in _METHODS else ("", str(value))


def _matches(allowed: str, action: str) -> bool:
    """An allowed action matches the same action, and a templated route (`<resource>.{id}`) matches any value in
    each of its `{...}` segments. When both name a method, the methods must agree."""
    allowed_method, want = _split(allowed)
    action_method, have = _split(action)
    if allowed_method and action_method and allowed_method != action_method:
        return False
    if want == have:
        return True
    if "{" not in want:
        return False
    wants, haves = want.split("."), have.split(".")
    return len(wants) == len(haves) and all(
        (w.startswith("{") and w.endswith("}") and h) or w == h for w, h in zip(wants, haves)
    )


def plain_route(action: str) -> bool:
    """Whether a world action names one route as written: no encoded separator, backslash or `..` segment that the
    service behind the proxy would decode into a different route than the one checked."""
    lowered = str(action or "").lower()
    if any(mark in lowered for mark in ("%2f", "%5c", "\\", "%2e")):
        return False
    return ".." not in lowered.split("?", 1)[0]


def permitted(permitted_actions: object, action: str, routes: object = ()) -> bool:
    """Whether an actor or worker with these permitted actions may take this world action. A path that is one of the
    world's own routes (`routes`, the sealed route map) is that route's action, never a value of a template."""
    if not isinstance(permitted_actions, list):
        return False
    literal = {_split(str(route))[1] for route in routes or () if "{" not in str(route)}
    path = _split(action)[1]
    return any(
        _matches(str(allowed), action) and ("{" not in str(allowed) or path not in literal or _split(str(allowed))[1] == path)
        for allowed in permitted_actions
    )


def declared_read(read_actions: object, action: str, routes: object = ()) -> bool:
    """Whether the contract declares this world action read-only, matched as an allow-list is: a templated read
    (`GET <resource>.{id}`) covers each record it is called for, never another tool's own route or method."""
    return permitted(list(read_actions) if isinstance(read_actions, (list, tuple, frozenset, set)) else None, action, routes)


def action_tool(actions: object, action: str) -> str:
    """The agent's own tool a world action is, from the sealed map of routes to tools: the action itself first, then a
    templated route (`<resource>.{id}.<verb>`) whose `{...}` segments its values fill. Empty when no route names it."""
    if not isinstance(actions, dict):
        return ""
    tool = actions.get(action)
    if tool is None:
        tool = next((name for route, name in actions.items() if _templated_match(actions, str(route), action)), "")
    return tool if isinstance(tool, str) else ""


def _templated_match(actions: Mapping[str, Any], route: str, action: str) -> bool:
    """A templated route matches an action whose path is not one of the map's own literal routes."""
    literal = {_split(str(one))[1] for one in actions if "{" not in str(one)}
    return "{" in route and _split(action)[1] not in literal and _matches(route, action)


def route_values(actions: object, action: str) -> dict[str, str]:
    """The values a world action's path carries for the templated route it matches (`<resource>.{id}.<verb>` and
    `<resource>.7.<verb>` give `{"id": "7"}`), so a check reads them as it reads the body. Empty otherwise."""
    if not isinstance(actions, dict) or action in actions:
        return {}
    for route in actions:
        if _templated_match(actions, str(route), action):
            return {
                want[1:-1]: have
                for want, have in zip(_split(str(route))[1].split("."), _split(action)[1].split("."))
                if want.startswith("{") and want.endswith("}")
            }
    return {}


def is_timeout(fault: Mapping[str, Any]) -> bool:
    """Whether a fault holds the call for its delay and then says it timed out (the two timeouts); an unavailable or
    error fault answers at once."""
    return fault.get("mode") in TIMEOUT_MODES


def fault_status(fault: Mapping[str, Any]) -> int:
    """The status an `error` fault answers with: the scenario's own, 500 when it set none or one outside 400-599."""
    status = _int(fault.get("status"), 500)
    return status if 400 <= status <= 599 else 500


def record_action(action: str) -> tuple[str, str] | None:
    """The collection and verb of a record change (`approvals.change`), or None for any other action."""
    collection, _, verb = action.rpartition(".")
    if verb in RECORD_VERBS and _IDENTIFIER.match(collection or ""):
        return collection, verb
    return None


class WorldDoor:
    """Permission, idempotency, faults and the log, once, for every surface."""

    def __init__(
        self,
        executor: Executor,
        log: ActionLog,
        roster: Roster,
        *,
        faults: Callable[[], list[dict[str, Any]]] = list,
        write_seconds: float = LOG_WRITE_SECONDS,
        faults_by_tool: bool = False,
        workers: Callable[[], Mapping[str, Any]] = dict,
        trial: Callable[[], Any] | None = None,
    ) -> None:
        self.executor = executor
        self.log = log
        self.roster = roster
        self.faults = faults
        # This trial's limits on the agent's workers: `permissions`, {worker id: [world actions]} as sealed (absent:
        # workers are not limited), and `deadline_at`, epoch seconds past which no worker's call reaches the world.
        self.workers = workers
        self.write_seconds = write_seconds
        # A surface whose calls are named by the tool also matches a fault by the tool's own name.
        self.faults_by_tool = faults_by_tool
        self._keys_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}
        # Kept here as well as in the log, so a log that cannot be written or read never lets a retry run it again.
        self._keyed: dict[str, tuple[str, Any, Any]] = {}
        # Counted here, so a call whose row the log failed to keep still counts toward a one-shot fault.
        self._numbered: dict[str, int] = {}
        # Which trial the numbers belong to, for a door that outlives a trial (the tool proxy); None for one door a trial.
        self.trial = trial
        self._numbered_trial: Any = None
        self._keyed_trial: Any = None

    def enter(self, call: ToolCall) -> Outcome:
        """The outcome of one call. Never raises: whatever goes wrong comes back as an outcome."""
        try:
            return self._enter(call)
        except Exception as exc:  # noqa: BLE001 - the door adds no error of its own to any caller
            outcome = Outcome(
                "faulted", 502, reason=f"{type(exc).__name__}: the world's door failed"
            )
            self._write(call, outcome)
            return outcome

    def events(
        self, actor: str | None = None, outcome: str | None = None
    ) -> list[dict[str, Any]]:
        """Every attempt in this trial's log, oldest first, optionally one participant's or one outcome's. Raises
        `EvidenceUnavailable` when the log cannot be read: an empty answer would pass a check like "nothing was
        committed" on missing evidence."""
        try:
            rows = self.log.rows()
        except Exception as exc:  # noqa: BLE001 - whatever broke, the evidence is missing
            raise EvidenceUnavailable("the world's action log could not be read") from exc
        return [
            row
            for row in rows
            if is_attempt(row)
            and (actor is None or str(row.get("actor_id") or AGENT) == actor)
            and (outcome is None or row.get("outcome") == outcome)
        ]

    def _enter(self, call: ToolCall) -> Outcome:
        acting = call.actor != AGENT
        on_behalf_of = None
        if acting:
            if not call.proven:
                # Unproven, it is the agent's own attempt to act as an actor, and logged as the agent's.
                return self._refused(
                    replace(call, actor=AGENT),
                    "identity_conflict",
                    "this request names an actor it cannot prove it is",
                )
            if not self.log.records_actors():
                # Logged without its actor, the action would read as the agent's, so it never reaches the world.
                return Outcome(
                    "refused",
                    503,
                    reason="this world's action log cannot record an actor's action",
                )
            try:
                listed = self.roster.actor(call.actor)
            except Exception:  # noqa: BLE001 - the world could not say who the actor is: its fault, never the actor's
                self._write(
                    call,
                    Outcome(
                        "faulted",
                        503,
                        reason="the world's actor list could not be read",
                    ),
                )
                return Outcome(
                    "faulted", 503, reason="the world could not check this actor"
                )
            if listed is None:
                return self._refused(
                    call, "unknown_actor", f"{call.actor} is not an actor in this world"
                )
            on_behalf_of = listed.get("on_behalf_of")
            if not permitted(listed.get("permitted_actions"), method_action(call.method, call.action), self._routes()):
                return self._refused(
                    call,
                    "not_permitted",
                    f"{call.action} is not permitted for {call.actor}",
                    on_behalf_of,
                )
        elif call.worker is not None:
            refused = self._worker_refusal(call)
            if refused is not None:
                return self._refused(call, *refused)
        key = (call.key or "").strip()
        if not key:
            return self._attempt(call, None, on_behalf_of)
        # A key is one participant's own: another's identical key is a different action.
        slot = f"{call.actor}\x00{key}"
        # A log written before actors were recorded cannot find a key by who used it; this door's own memory still can.
        logged = acting or self.log.records_actors()
        self._forget_past_trials()
        with self._key_lock(slot):
            known = self._keyed.get(slot)
            if known is None and logged:
                try:
                    earlier = self.log.committed(key, call.actor)
                    # Claimed before the action runs, so a door that never recorded its answer (a log write that
                    # failed after the world applied it) leaves the key claimed for any door after it.
                    claimed = self.log.claim(key, call.actor) if earlier is None else None
                except Exception:  # noqa: BLE001 - the log cannot say: running again could apply the action twice
                    return self._key_unknown(call, key, on_behalf_of)
                if earlier is not None:
                    known = ("committed", *earlier)
                elif claimed is not None:
                    return self._key_unknown(call, key, on_behalf_of)
            if known is None:
                self._keyed[slot] = ("pending", call.action, call.arguments, None)
                outcome = self._attempt(call, key, on_behalf_of)
                if outcome.outcome == "committed":
                    # Committed, even when a fault then lost the answer: a retry with this key is answered from it.
                    self._keyed[slot] = ("committed", call.action, call.arguments, outcome.result)
                    self._settle(key, call.actor, "committed", logged)
                elif outcome.outcome == "faulted" and outcome.fault is None:
                    # The world failed mid-action: whether it applied it is unknown, so the key is never run again.
                    self._keyed[slot] = ("unknown", call.action, call.arguments, None)
                    self._settle(key, call.actor, "unknown", logged)
                else:
                    # Refused, or held by the scenario's fault before the world saw it: nothing took effect, and a
                    # retry is a new attempt.
                    del self._keyed[slot]
                    self._settle(key, call.actor, None, logged)
                return outcome
            state, action, arguments, result = known
            if state != "committed":
                return self._key_unknown(call, key, on_behalf_of)
            if (action is not None and action != call.action) or arguments != call.arguments:
                outcome = Outcome(
                    "refused",
                    409,
                    reason="this idempotency key was already used for another action or with other arguments",
                    refusal_code="idempotency_conflict",
                )
                self._write(call, outcome, key=key, on_behalf_of=on_behalf_of)
                return outcome
            # Answered from the first result; the world never sees the same action twice.
            outcome = Outcome("duplicate", 200, result=result)
            self._write(call, outcome, key=key, on_behalf_of=on_behalf_of)
            return outcome

    def _worker_refusal(self, call: ToolCall) -> tuple[str, str] | None:
        """Why a worker's call never reaches the world, or None: a channel no worker of this trial owns, the trial's
        worker deadline passed, or an action the worker's sealed allow-list does not hold. Limits that cannot be read
        refuse as the world's failure (`LIMITS_UNREADABLE`), never the worker's: a broken seal never widens them."""
        if not call.worker:
            return "identity_conflict", "this channel does not belong to a worker of this trial"
        try:
            limits = self.workers() or {}
        except Exception:  # noqa: BLE001 - unreadable limits are no permission, and no violation of the worker's
            return LIMITS_UNREADABLE, f"the world could not read the limits of {call.worker}"
        own = (limits.get("deadlines") or {}).get(call.worker)
        for deadline in (own, limits.get("deadline_at")):
            if isinstance(deadline, (int, float)) and not isinstance(deadline, bool) and time.time() > deadline:
                return "deadline_passed", f"the deadline for {call.worker} has passed"
        if "permissions" in limits:
            allowed = (limits.get("permissions") or {}).get(call.worker)
            if not plain_route(call.action):
                # An encoded separator or `..` would be read as another route by the service behind the proxy.
                return "not_permitted", f"{call.action} is not a plain route, so it is not permitted for {call.worker}"
            if not permitted(allowed, method_action(call.method, call.action), limits.get("routes")):
                return "not_permitted", f"{call.action} is not permitted for {call.worker}"
        return None

    def _routes(self) -> list[str]:
        """The world's own sealed routes, so a templated permission never reaches another tool's route."""
        try:
            return list((self.workers() or {}).get("routes") or [])
        except Exception:  # noqa: BLE001 - unreadable routes leave only the template check
            return []

    def _forget_past_trials(self) -> None:
        """A door that outlives a trial (the tool proxy) forgets the keys an earlier trial used: the world was reset
        under them, so a key reused in the next trial is a new action."""
        if self.trial is None:
            return
        try:
            self.faults()  # reading the trial's faults is what tells the proxy which trial this is
        except Exception:  # noqa: BLE001 - unknown this time; the trial marker stays as it was
            pass
        current = self.trial()
        with self._keys_lock:
            if current != self._keyed_trial:
                self._keyed.clear()
                self._keyed_trial = current

    def _attempt(self, call: ToolCall, key: str | None, on_behalf_of: str | None) -> Outcome:
        """Run a call the rules let through: the agent's call takes the scenario's fault when one is set on it."""
        if call.actor == AGENT:
            named = self._named_faults(call)
            held = [one for one in named if one.get("mode") == APPROVAL_REQUIRED]
            if held and call_effect(call) == "write":
                try:
                    approved = all(self._approved(one) for one in held)
                except Exception:  # noqa: BLE001 - unknown whether an approval exists: the world's fault, never the agent's
                    outcome = Outcome("faulted", 503, reason="whether this action was approved could not be read")
                    self._write(call, outcome, key=key)
                    return outcome
                if not approved:
                    return self._refused(call, APPROVAL_MISSING, f"{call.action} needs an approval that has not been given")
            named = [one for one in named if one.get("mode") != APPROVAL_REQUIRED]
            if named:
                # Held across the world call, so two calls never both take a one-shot fault on the same number.
                with self._key_lock(f"\x01calls\x00{call.action}"):
                    fault = self._fault(call, named)
                    if fault is not None:
                        return self._faulted(call, fault, key)
                    return self._run(call, key, None, wait=True)
        return self._run(call, key, on_behalf_of)

    def _settle(self, key: str, actor: str, state: str | None, logged: bool = True) -> None:
        if not logged:
            return
        try:
            self.log.settle(key, actor, state)
        except Exception:  # noqa: BLE001 - a key left pending is never run again: the safe side
            pass

    def _key_unknown(self, call: ToolCall, key: str, on_behalf_of: str | None) -> Outcome:
        """A keyed action whose first attempt may have taken effect without a recorded answer: never run again."""
        outcome = Outcome(
            "faulted",
            503,
            reason="whether this idempotency key's first action took effect is unknown; it is not run again",
        )
        self._write(call, outcome, key=key, on_behalf_of=on_behalf_of)
        return outcome

    def refuse(self, call: ToolCall, code: str, reason: str) -> Outcome:
        """Refuse a call a surface stopped before the world, logged like every other refused attempt."""
        return self._refused(call, code, reason)

    def _refused(
        self, call: ToolCall, code: str, reason: str, on_behalf_of: str | None = None
    ) -> Outcome:
        # Says why, never whether the record asked about exists.
        outcome = Outcome("refused", 403, reason=reason, refusal_code=code)
        self._write(call, outcome, on_behalf_of=on_behalf_of)
        return outcome

    def _named_faults(self, call: ToolCall) -> list[dict[str, Any]]:
        """The faults this trial sets on this action of the agent's, whichever call of it they name."""
        try:
            faults = [one for one in self.faults() or [] if isinstance(one, dict)]
        except Exception as exc:  # noqa: BLE001 - unreadable faults cannot be applied; the runner says so in notices
            logger.warning("trial faults unreadable, none applied to %s: %s", call.action, type(exc).__name__)
            return []
        names = {call.action, call.name} if self.faults_by_tool else {call.action}
        if call.tool:
            # A route's sealed tool is the agent's own name for it: a fault naming the tool holds its call on every
            # surface, a worker's channel included.
            names.add(call.tool)
        return [one for one in faults if one.get("action") in names]

    def _approved(self, fault: Mapping[str, Any]) -> bool:
        """Whether an actor has committed the fault's `approval` action in this trial, with arguments that include its
        `approved_when`. The door commits an actor's action only when that actor is permitted it."""
        wanted = fault.get("approved_when") if isinstance(fault.get("approved_when"), dict) else {}
        for row in self.log.rows():
            arguments = row.get("arguments")
            if isinstance(arguments, (str, bytes)):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    arguments = {}
            arguments = arguments if isinstance(arguments, dict) else {}
            if (
                row.get("outcome") == "committed"
                and str(row.get("actor_id") or AGENT) != AGENT
                and action_matches(logged_as(row), str(fault.get("approval") or ""))
                and all(arguments.get(name) == value for name, value in wanted.items())
            ):
                return True
        return False

    def _fault(self, call: ToolCall, named: list[dict[str, Any]]) -> dict[str, Any] | None:
        """The fault set on this call of the action, by which call of it this is."""
        try:
            made = self.log.agent_calls(call.action, call.name)
        except Exception:  # noqa: BLE001 - which call this is is unknown: a guess would fault every call as the first
            return None
        if self.trial is not None:
            current = self.trial()
            if current != self._numbered_trial:
                self._numbered.clear()
                self._numbered_trial = current
        made = max(made, self._numbered.get(call.action, 0))
        self._numbered[call.action] = made + 1
        return next(
            (one for one in named if _int(one.get("on_call"), 1) == made + 1), None
        )

    def _faulted(self, call: ToolCall, fault: dict[str, Any], key: str | None = None) -> Outcome:
        """The fault the scenario sets on this call; only after a commit does the world see it. A retry with the first
        call's key is answered from the first result; one without is not deduplicated."""
        mode = fault.get("mode")
        if mode == "timeout_after_commit":
            ran = self._execute(call)
            logged = replace(
                ran,
                reason="response_lost",
                fault=fault,
                status=504,
                body=None,
                refusal_code=None,
            )
            self._write(call, logged, key=key, wait=True)
            return logged
        if mode == "unavailable":
            outcome = Outcome("faulted", 503, reason="unavailable", fault=fault)
        elif mode == "error":
            status = fault_status(fault)
            message = str(fault.get("message") or "").strip() or "error"
            outcome = Outcome(
                "faulted",
                status,
                body=json.dumps({"detail": message}).encode(),
                reason=message[:2000],
                fault=fault,
            )
        else:
            outcome = Outcome("faulted", 504, reason="timeout_before_commit", fault=fault)
        self._write(call, outcome, key=key, wait=True)
        return outcome

    def _execute(self, call: ToolCall) -> Outcome:
        try:
            ran = self.executor.run(call)
        except Exception as exc:  # noqa: BLE001 - the world failing is a fault of its own
            return Outcome("faulted", 502, reason=f"{type(exc).__name__}: unavailable")
        if ran.status < 400:
            return Outcome(
                "committed", ran.status, ran.result, ran.body, ran.content_type
            )
        # The request's own fault is the world refusing it; the server's is a world fault.
        refused = ran.status < 500
        return Outcome(
            "refused" if refused else "faulted",
            ran.status,
            ran.result,
            ran.body,
            ran.content_type,
            reason=ran.error[:2000],
            refusal_code="world_rule" if refused else None,
        )

    def _run(
        self, call: ToolCall, key: str | None, on_behalf_of: str | None, *, wait: bool = False
    ) -> Outcome:
        outcome = self._execute(call)
        # A keyed action's row is written before the answer, so a retry always finds the first result; so is the row
        # of an action a fault is set on, so the next call of it is counted after this one.
        self._write(
            call,
            outcome,
            key=key,
            on_behalf_of=on_behalf_of,
            wait=wait or key is not None,
        )
        return outcome

    def _key_lock(self, key: str) -> threading.Lock:
        with self._keys_lock:
            return self._key_locks.setdefault(key, threading.Lock())

    def record(self, call: ToolCall, outcome: Outcome) -> None:
        """Writes one row the door did not decide itself (a mark that a call ran unrecorded), and waits for it."""
        self._write(call, outcome, wait=True)

    def _write(
        self,
        call: ToolCall,
        outcome: Outcome,
        *,
        key: str | None = None,
        on_behalf_of: str | None = None,
        wait: bool = False,
    ) -> None:
        """One row of the action log, given at most `write_seconds` (or, with `wait`, until it is written); a failed
        or slow write loses the row only."""
        action_id = uuid.uuid4().hex
        outcome.action_id = action_id
        row = {
            "name": call.name,
            # The world action in full: `name` alone cannot tell `service_a.update` from `service_b.update`.
            "action": call.action,
            "arguments": call.arguments,
            "result": outcome.result
            if outcome.outcome in ("committed", "duplicate")
            else None,
            # `ok` stays, true only for a committed action.
            "ok": outcome.outcome == "committed",
            "error": outcome.reason,
            "at": time.time(),
            "action_id": action_id,
            "outcome": outcome.outcome,
            "refusal": outcome.reason if outcome.outcome == "refused" else None,
            "refusal_code": outcome.refusal_code,
            "idempotency_key": key,
            "actor_id": call.actor,
            "worker_id": call.worker or None,
            "attribution": call.attribution,
            "agent_tool": call.tool or None,
            "parent_event_id": call.parent,
            "on_behalf_of": on_behalf_of,
            "effect": call_effect(call),
        }
        if wait:
            self._write_row(row)
            return
        writer = threading.Thread(target=self._write_row, args=(row,), daemon=True)
        writer.start()
        writer.join(self.write_seconds)

    def _write_row(self, row: dict[str, Any]) -> None:
        try:
            self.log.write(row)
        except Exception:  # noqa: BLE001 - evidence persistence must never alter the tool's answer
            pass


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# --- executors ---------------------------------------------------------------------------------------------------


class HttpExecutor:
    """Forwards a call to an upstream HTTP service as it came: the agent's own tools service behind the proxy."""

    def __init__(self, upstream: str, timeout: float = 30.0) -> None:
        self.upstream = upstream.rstrip("/")
        self.timeout = timeout

    def run(self, call: ToolCall) -> Executed:
        path = call.path or "/" + call.action.replace(".", "/")
        body = (
            call.body if call.body is not None else json.dumps(call.arguments).encode()
        )
        outgoing = request.Request(
            self.upstream + path,
            data=body if call.method != "GET" else None,
            method=call.method,
            headers={
                "content-type": call.content_type or "application/json",
                **({IDENTITY_HEADER: call.identity} if call.identity and call.actor != AGENT else {}),
            },
        )
        try:
            with request.urlopen(outgoing, timeout=self.timeout) as response:
                content = response.read()
                status = int(response.status)
                kind = response.headers.get("content-type", "application/json")
        except error.HTTPError as exc:
            content = exc.read()
            return Executed(
                exc.code,
                None,
                content,
                None,
                content.decode("utf-8", errors="replace")[:2000],
            )
        try:
            result = json.loads(content) if content else None
        except ValueError:
            result = content.decode("utf-8", errors="replace")
        return Executed(status, result, content, kind)


class RecordExecutor:
    """A record change on one of the world's collections: `<collection>.change` with `{where, set}` or
    `<collection>.put` with the record's fields (or `{record}`). Identifiers are checked; values are always
    parameters."""

    def __init__(
        self, execute: Callable[[str, list[Any]], Any], placeholder: str = "%s"
    ) -> None:
        self.execute = execute
        self.placeholder = placeholder

    def run(self, call: ToolCall) -> Executed:
        named = record_action(call.action)
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        if named is None:
            return Executed(400, None, error=f"{call.action} is not a record change")
        collection, verb = named
        if collection.lower().startswith("_alk_"):
            # The harness's own tables (who may act, the action log, the trial's faults) are never a world record.
            return Executed(403, None, error=f"{collection} is the harness's own table, not a world record")
        if verb == "put":
            # A put's arguments are the record, or carry it under `record`.
            values = arguments.get("record") if "record" in arguments else arguments
        else:
            values = arguments.get("set")
        where = arguments.get("where") if verb == "change" else {}
        if (
            not isinstance(values, dict)
            or not values
            or not isinstance(where, dict)
            or (verb == "change" and not where)
        ):
            return Executed(
                400,
                None,
                error="a change needs `where` (which records) and `set` (the new values); a put needs the record's fields, "
                "each a non-empty object",
            )
        names = [*values, *where]
        bad = [one for one in names if not _IDENTIFIER.match(str(one))]
        if bad:
            return Executed(
                400, None, error="not a field name: " + ", ".join(map(str, bad))
            )
        mark = self.placeholder
        quoted = {one: '"' + str(one) + '"' for one in names}
        if verb == "put":
            sql = (
                f'INSERT INTO "{collection}" ({", ".join(quoted[one] for one in values)}) '
                f"VALUES ({', '.join(mark for _ in values)})"
            )
            params = list(values.values())
        else:
            assignments = ", ".join(f"{quoted[one]} = {mark}" for one in values)
            matches = " AND ".join(f"{quoted[one]} = {mark}" for one in where)
            sql = f'UPDATE "{collection}" SET {assignments} WHERE {matches}'
            params = [*values.values(), *where.values()]
        try:
            changed = self.execute(sql, params)
        except Exception as exc:
            if not _breaks_a_rule(exc):
                raise
            # The world's own rule (a unique key, a check, a missing parent) refuses it, as the agent's call would be.
            return Executed(409, None, error=f"the world refuses this record: {str(exc).strip()[:300]}")
        if verb == "change" and isinstance(changed, int) and changed == 0:
            return Executed(404, None, error=f"no {collection} record matches")
        return Executed(200, {"changed": changed if isinstance(changed, int) else 1})


def _breaks_a_rule(exc: Exception) -> bool:
    """Whether a database error is the data breaking one of the world's constraints (SQL state class 23), not the
    database being out of reach."""
    import sqlite3

    return isinstance(exc, sqlite3.IntegrityError) or str(getattr(exc, "sqlstate", "") or "").startswith("23")


class HandlerExecutor:
    """Runs a call through a world's own handler in process: the chat runner's generated world. `handle` takes the
    call and returns `(ok, result, content)`, `content` being what the agent is shown. One call at a time: the
    agent's calls and the actors' run on different threads against one world."""

    def __init__(
        self, handle: Callable[[ToolCall], tuple[bool, Any, str] | None]
    ) -> None:
        self.handle = handle
        self._lock = threading.Lock()

    def run(self, call: ToolCall) -> Executed:
        with self._lock:
            answered = self.handle(call)
        if answered is None:
            missing = f"no such tool {call.name}"
            return Executed(404, None, missing.encode(), "text/plain", error=missing)
        ok, result, content = answered
        body = content.encode() if isinstance(content, str) else None
        if ok:
            return Executed(200, result, body, "text/plain")
        return Executed(
            400,
            result,
            body,
            "text/plain",
            error=content if isinstance(content, str) else "refused",
        )


class Routed:
    """Record changes to the record executor, everything else to the world's own executor. Only an actor's call can
    be a record change; the agent's calls always take the route they came by."""

    def __init__(self, tools: Executor, records: Executor | None = None) -> None:
        self.tools = tools
        self.records = records

    def run(self, call: ToolCall) -> Executed:
        if call.form == "record" and call.actor != AGENT:
            if self.records is None:
                return Executed(400, None, error="this world takes no record changes")
            return self.records.run(call)
        return self.tools.run(call)


# --- logs and rosters --------------------------------------------------------------------------------------------

_EXTENDED = (
    "name, arguments, result, ok, error, at, action_id, outcome, refusal, refusal_code, idempotency_key, actor_id, "
    "worker_id, agent_tool, parent_event_id, on_behalf_of"
)
# Written beside the extended columns when the table has it.
_ACTION = "action"
# Written beside either set of columns when the table has it: a log made before effects were recorded has no column.
_EFFECT = "effect"
# Likewise: why a call with no worker is the coordinator's while workers had channels.
_ATTRIBUTION = "attribution"
_BASIC = "name, arguments, result, ok, error, at"
# Each actor key a door claimed before its action ran, so no later door runs it again; beside the log, which a
# failed write can leave without the row.
_KEYS_DDL = (
    "CREATE TABLE IF NOT EXISTS _alk_action_keys "
    "(actor_id TEXT NOT NULL, idempotency_key TEXT NOT NULL, state TEXT NOT NULL, PRIMARY KEY (actor_id, idempotency_key))"
)


class PostgresLog:
    """The world's `_alk_tool_trace` table. A world seeded without actors keeps the log's original columns, and its
    rows are written in that shape."""

    def __init__(self, dsn: str, connect_seconds: int = 5) -> None:
        self.dsn = dsn
        self.connect_seconds = connect_seconds
        self._columns: frozenset[str] | None = None
        self._keys_made = False

    def _connect(self, **options: Any) -> Any:
        import psycopg

        return psycopg.connect(
            self.dsn, autocommit=True, connect_timeout=self.connect_seconds, **options
        )

    def _table_columns(self) -> frozenset[str]:
        """The log table's columns, read once; nothing (and asked again next time) when it cannot be read."""
        if self._columns is None:
            try:
                with self._connect() as connection:
                    cursor = connection.execute("SELECT * FROM _alk_tool_trace LIMIT 0")
                    self._columns = frozenset(one[0] for one in cursor.description or [])
            except Exception:  # noqa: BLE001 - unknown this time, asked again next time
                return frozenset()
        return self._columns

    def records_actors(self) -> bool:
        return "actor_id" in self._table_columns()

    def _records_action(self) -> bool:
        return _ACTION in self._table_columns()

    def write(self, row: dict[str, Any]) -> None:
        extended = self.records_actors()
        if not extended and row.get("actor_id") != AGENT:
            return
        columns = _EXTENDED if extended else _BASIC
        if extended and self._records_action():
            columns += ", " + _ACTION
        for optional in (_EFFECT, _ATTRIBUTION):
            if optional in self._table_columns():
                columns += ", " + optional
        keys = [one.strip() for one in columns.split(",")]
        values = [
            json.dumps(row.get(key), default=str)
            if key in ("arguments", "result")
            else row.get(key)
            for key in keys
        ]
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO _alk_tool_trace({columns}) VALUES ({', '.join('%s' for _ in keys)})",  # noqa: S608 - fixed columns
                values,
            )

    def rows(self) -> list[dict[str, Any]]:
        with self._connect(options="-c default_transaction_read_only=on") as connection:
            cursor = connection.execute("SELECT * FROM _alk_tool_trace ORDER BY at ASC")
            columns = [one[0] for one in cursor.description or []]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def committed(self, key: str, actor: str) -> tuple[str | None, Any, Any] | None:
        action = _ACTION if self._records_action() else "NULL"
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {action}, arguments, result FROM _alk_tool_trace "  # noqa: S608 - a fixed column name
                "WHERE idempotency_key = %s AND actor_id = %s AND outcome = 'committed' ORDER BY at LIMIT 1",
                (key, actor),
            ).fetchone()
        return (row[0], row[1], row[2]) if row else None

    def _keys_table(self, connection: Any) -> None:
        """The keys table, made once per log; two first claims at once can race on creating it, and the loser finds it
        made."""
        if self._keys_made:
            return
        try:
            connection.execute(_KEYS_DDL)
        except Exception:  # noqa: BLE001 - another claim created it at the same moment; a real failure raises below
            connection.execute(_KEYS_DDL)
        self._keys_made = True

    def claim(self, key: str, actor: str) -> str | None:
        with self._connect() as connection:
            self._keys_table(connection)
            row = connection.execute(
                "INSERT INTO _alk_action_keys (actor_id, idempotency_key, state) VALUES (%s, %s, 'pending') "
                "ON CONFLICT DO NOTHING RETURNING state",
                (actor, key),
            ).fetchone()
            if row is not None:
                return None
            found = connection.execute(
                "SELECT state FROM _alk_action_keys WHERE actor_id = %s AND idempotency_key = %s", (actor, key)
            ).fetchone()
        return str(found[0]) if found else "pending"

    def settle(self, key: str, actor: str, state: str | None) -> None:
        with self._connect() as connection:
            if state is None:
                connection.execute(
                    "DELETE FROM _alk_action_keys WHERE actor_id = %s AND idempotency_key = %s", (actor, key)
                )
            else:
                connection.execute(
                    "UPDATE _alk_action_keys SET state = %s WHERE actor_id = %s AND idempotency_key = %s",
                    (state, actor, key),
                )

    def agent_calls(self, action: str, name: str) -> int:
        clause = " AND actor_id = 'agent'" if self.records_actors() else ""
        if self._records_action():
            # A row from before actions were recorded counts by its name, as `same_action` reads it.
            match, params = "(action = %s OR (action IS NULL AND name IN (%s, %s)))", (action, action, name)
        else:
            match, params = "name = %s", (name,)
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT count(*) FROM _alk_tool_trace WHERE {match}{clause}",  # noqa: S608 - fixed clauses
                params,
            ).fetchone()
        return int(row[0]) if row else 0


class PostgresRoster:
    """Who may do what, from the world's own `_alk_actors` table."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def actor(self, actor_id: str) -> Mapping[str, Any] | None:
        import psycopg

        with psycopg.connect(
            self.dsn, autocommit=True, connect_timeout=5
        ) as connection:
            row = connection.execute(
                "SELECT permitted_actions, on_behalf_of FROM _alk_actors WHERE id = %s",
                (actor_id,),
            ).fetchone()
        return {"permitted_actions": row[0], "on_behalf_of": row[1]} if row else None


class PostgresRecords:
    """Record changes run on the world's own Postgres database."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def __call__(self, sql: str, params: list[Any]) -> int:
        import psycopg

        with psycopg.connect(
            self.dsn, autocommit=True, connect_timeout=5
        ) as connection:
            return int(connection.execute(sql, params).rowcount)


class MemoryLog:
    """An action log kept in memory, for a world with nowhere else to keep one; it always records actors."""

    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []
        self._keys: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def records_actors(self) -> bool:
        return True

    def claim(self, key: str, actor: str) -> str | None:
        with self._lock:
            found = self._keys.get((actor, key))
            if found is None:
                self._keys[(actor, key)] = "pending"
            return found

    def settle(self, key: str, actor: str, state: str | None) -> None:
        with self._lock:
            if state is None:
                self._keys.pop((actor, key), None)
            else:
                self._keys[(actor, key)] = state

    def write(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._rows.append(dict(row))

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(one) for one in self._rows]

    def committed(self, key: str, actor: str) -> tuple[str | None, Any, Any] | None:
        found = next(
            (
                one
                for one in self.rows()
                if one.get("idempotency_key") == key
                and one.get("actor_id") == actor
                and one.get("outcome") == "committed"
            ),
            None,
        )
        return (found.get("action"), found["arguments"], found["result"]) if found else None

    def agent_calls(self, action: str, name: str) -> int:
        return sum(
            1
            for one in self.rows()
            if one.get("actor_id") == AGENT
            and (one.get("action") == action if one.get("action") else one.get("name") in (action, name))
        )


class TeeLog:
    """One log read from, with every row also written to a second one: in process the trial's rows live in memory
    and are mirrored to the world's table when it has one, so a missing table never changes what the door decides."""

    def __init__(self, primary: ActionLog, mirror: ActionLog | None = None) -> None:
        self.primary = primary
        self.mirror = mirror

    def records_actors(self) -> bool:
        return self.primary.records_actors()

    def write(self, row: dict[str, Any]) -> None:
        self.primary.write(row)
        if self.mirror is not None:
            try:
                self.mirror.write(row)
            except Exception:  # noqa: BLE001 - the mirror is evidence only
                pass

    def rows(self) -> list[dict[str, Any]]:
        return self.primary.rows()

    def committed(self, key: str, actor: str) -> tuple[str | None, Any, Any] | None:
        return self.primary.committed(key, actor)

    def claim(self, key: str, actor: str) -> str | None:
        return self.primary.claim(key, actor)

    def settle(self, key: str, actor: str, state: str | None) -> None:
        self.primary.settle(key, actor, state)

    def agent_calls(self, action: str, name: str) -> int:
        return self.primary.agent_calls(action, name)


class DictRoster:
    """Who may do what, from records the caller already holds (the harness's actor list)."""

    def __init__(self, actors: Mapping[str, Mapping[str, Any]]) -> None:
        self.actors = dict(actors)

    def actor(self, actor_id: str) -> Mapping[str, Any] | None:
        return self.actors.get(actor_id)
