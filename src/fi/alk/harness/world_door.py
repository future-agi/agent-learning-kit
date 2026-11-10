"""The one way into a world.

Every surface that reaches a world's tools (the HTTP tool proxy in front of an agent's own tools service, the chat
runner running a generated handler, the actors' dispatcher, later the release tool server and MCP bindings) builds one
`ToolCall` and calls `WorldDoor.enter`. The door decides, in this order: may this actor take this action, has this
idempotency key been answered, does the scenario set a fault on this call, then runs the call and logs what came of it
(committed, refused, faulted, duplicate). `WorldDoor.events` is the read side: who did what, when, with what outcome.

Three levels sit under the surfaces and none of them knows the others' inside: the door (rules), executors (how a call
is run: forwarded over HTTP, run by a handler, or a record change) and the action log and roster (how the world keeps
who may do what and what happened). The door never raises into its caller, and a log write that fails or is slow loses
the row, never the call.

This file is copied into each bundle beside the tool proxy and runs there as a plain module, so it needs nothing but
the standard library, and `psycopg` only when a Postgres log or roster is used.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol
from urllib import error, request

# The agent under test in the action log; no actor takes this id.
AGENT = "agent"
OUTCOMES = ("committed", "refused", "faulted", "duplicate")
# Longest a scenario's fault holds a call, and how long a log write may hold the answer before the row is given up.
MAX_FAULT_SECONDS = 30.0
LOG_WRITE_SECONDS = 2.0
# A record change an actor may be permitted beside the world's tools: `<collection>.change` or `<collection>.put`.
RECORD_VERBS = ("change", "put")
# How a tools service learns which person an actor's call is from: the actor's identity in that service.
IDENTITY_HEADER = "X-ALK-Actor-Identity"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


@dataclass(frozen=True)
class ToolCall:
    """One attempt to act in the world, as every surface describes it."""

    # The world action, `<service>.<action>`: the route the call takes with `/` read as `.`.
    action: str
    # What the action log records it as: the route's last part, or the tool's own name.
    name: str
    arguments: Any = field(default_factory=dict)
    actor: str = AGENT
    # Only an actor's retries are answered from the first result; the agent's key is ignored.
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
    # The agent's own name for the tool this action is, when the surface knows it (a route is often named otherwise,
    # `get_order` -> `orders.get`); kept beside `name` so grading reads the tool the agent called.
    tool: str = ""


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
        if not self.fault:
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

    def committed(self, key: str, actor: str) -> tuple[Any, Any] | None: ...

    def agent_calls(self, name: str) -> int: ...


def world_action(route: str) -> str:
    """A route or endpoint as the world names the action it takes: the path without its query, `/` read as `.`.
    Every surface (the proxy, the sealed allow-lists, the service routes) names actions through this one rule."""
    return str(route or "").split("?", 1)[0].strip("/").replace("/", ".")


# What this door enforces, sealed beside it in a bundle: a runner reads it rather than assume a newer door than shipped.
DOOR_FILE = "door.json"
DOOR_CAPABILITIES = {"worker_deadlines": "per_worker"}

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


def method_action(method: str, action: str) -> str:
    """A world action with the HTTP method that takes it (`GET orders.{order_id}`): two tools on one path are two
    actions. Sealed routes and worker allow-lists are named this way."""
    return f"{(method or 'POST').upper()} {action}"


def _split(value: str) -> tuple[str, str]:
    """The method and path of `GET orders.get`, or no method for a bare action (`orders.get`), which any method takes."""
    method, _, path = str(value).partition(" ")
    return (method, path) if path and method in _METHODS else ("", str(value))


def _matches(allowed: str, action: str) -> bool:
    """An allowed action matches the same action, and a templated route (`orders.{order_id}`) matches any value in
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


def action_tool(actions: object, action: str) -> str:
    """The agent's own tool a world action is, from the sealed map of routes to tools: the action itself first, then a
    templated route (`refunds.{order_id}.issue`) whose `{...}` segments its values fill. Empty when no route names it."""
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
    """The values a world action's path carries for the templated route it matches (`refunds.{order_id}.issue` and
    `refunds.1043.issue` give `{"order_id": "1043"}`), so a check reads them as it reads the body. Empty otherwise."""
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
    ) -> None:
        self.executor = executor
        self.log = log
        self.roster = roster
        self.faults = faults
        # This trial's limits on the agent's workers: `permissions`, {worker id: [world actions]} as sealed (absent:
        # workers are not limited), and `deadline_at`, epoch seconds past which no worker's call reaches the world.
        self.workers = workers
        self.write_seconds = write_seconds
        # A fault names the world action (`crm.update`); a surface whose calls are named by the tool (the chat runner)
        # also matches the tool's own name, as it always has. Over HTTP the action alone, as the proxy always did.
        self.faults_by_tool = faults_by_tool
        self._keys_lock = threading.Lock()
        self._key_locks: dict[str, threading.Lock] = {}

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
        """Every attempt in this trial's log, oldest first, optionally one participant's or one outcome's; nothing
        when the log cannot be read."""
        try:
            rows = self.log.rows()
        except Exception:  # noqa: BLE001 - an unreadable log reads as empty this time
            return []
        return [
            row
            for row in rows
            if (actor is None or str(row.get("actor_id") or AGENT) == actor)
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
        else:
            if call.worker is not None:
                refused = self._worker_refusal(call)
                if refused is not None:
                    return self._refused(call, *refused)
            fault = self._fault(call)
            if fault is not None:
                return self._faulted(call, fault)
        key = (call.key or "").strip() if acting else ""
        if not key:
            return self._run(call, None, on_behalf_of)
        # A key is one actor's own: another actor's identical key is a different action.
        with self._key_lock(f"{call.actor}\x00{key}"):
            earlier = self._committed(key, call.actor)
            if earlier is None:
                return self._run(call, key, on_behalf_of)
            if earlier[0] != call.arguments:
                outcome = Outcome(
                    "refused",
                    409,
                    reason="this idempotency key was already used with other arguments",
                    refusal_code="idempotency_conflict",
                )
                self._write(call, outcome, key=key, on_behalf_of=on_behalf_of)
                return outcome
            # Answered from the first result; the world never sees the same action twice.
            outcome = Outcome("duplicate", 200, result=earlier[1])
            self._write(call, outcome, key=key, on_behalf_of=on_behalf_of)
            return outcome

    def _worker_refusal(self, call: ToolCall) -> tuple[str, str] | None:
        """Why a worker's call never reaches the world, or None: a channel no worker of this trial owns, the trial's
        worker deadline passed, or an action the worker's sealed allow-list does not hold. Limits that cannot be read
        refuse: a broken seal never widens what a worker may do."""
        if not call.worker:
            return "identity_conflict", "this channel does not belong to a worker of this trial"
        try:
            limits = self.workers() or {}
        except Exception:  # noqa: BLE001 - unreadable limits are no permission
            return "not_permitted", f"{call.action} is not permitted for {call.worker}: its limits could not be read"
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

    def _refused(
        self, call: ToolCall, code: str, reason: str, on_behalf_of: str | None = None
    ) -> Outcome:
        # Says why, never whether the record asked about exists.
        outcome = Outcome("refused", 403, reason=reason, refusal_code=code)
        self._write(call, outcome, on_behalf_of=on_behalf_of)
        return outcome

    def _fault(self, call: ToolCall) -> dict[str, Any] | None:
        """The fault this trial sets on this call of the agent's, if any: by action and which call of it."""
        try:
            faults = [one for one in self.faults() or [] if isinstance(one, dict)]
        except Exception:  # noqa: BLE001 - unreadable faults are no faults
            return None
        names = (call.action, call.name) if self.faults_by_tool else (call.action,)
        named = [one for one in faults if one.get("action") in names]
        if not named:
            return None
        try:
            made = self.log.agent_calls(call.name)
        except Exception:  # noqa: BLE001 - counted from zero when the log cannot say
            made = 0
        return next(
            (one for one in named if _int(one.get("on_call"), 1) == made + 1), None
        )

    def _faulted(self, call: ToolCall, fault: dict[str, Any]) -> Outcome:
        """A timeout the scenario sets: before commit the world never sees the call; after commit it does and the
        caller never hears back. A retry is not deduplicated, so a second commit is what a check can catch."""
        if fault.get("mode") == "timeout_after_commit":
            ran = self._execute(call)
            logged = replace(
                ran,
                reason="response_lost",
                fault=fault,
                status=504,
                body=None,
                refusal_code=None,
            )
            self._write(call, logged)
            return logged
        outcome = Outcome("faulted", 504, reason="timeout_before_commit", fault=fault)
        self._write(call, outcome)
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
        self, call: ToolCall, key: str | None, on_behalf_of: str | None
    ) -> Outcome:
        outcome = self._execute(call)
        # A keyed action's row is written before the answer, so a retry always finds the first result.
        self._write(call, outcome, key=key, on_behalf_of=on_behalf_of, wait=key is not None)
        return outcome

    def _committed(self, key: str, actor: str) -> tuple[Any, Any] | None:
        try:
            return self.log.committed(key, actor)
        except Exception:  # noqa: BLE001 - unknown is treated as not yet answered
            return None

    def _key_lock(self, key: str) -> threading.Lock:
        with self._keys_lock:
            return self._key_locks.setdefault(key, threading.Lock())

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
            "agent_tool": call.tool or None,
            "parent_event_id": call.parent,
            "on_behalf_of": on_behalf_of,
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
_BASIC = "name, arguments, result, ok, error, at"


class PostgresLog:
    """The world's `_alk_tool_trace` table. A world seeded without actors keeps the log's original columns, and its
    rows are written in that shape."""

    def __init__(self, dsn: str, connect_seconds: int = 5) -> None:
        self.dsn = dsn
        self.connect_seconds = connect_seconds
        self._extended: bool | None = None

    def _connect(self, **options: Any) -> Any:
        import psycopg

        return psycopg.connect(
            self.dsn, autocommit=True, connect_timeout=self.connect_seconds, **options
        )

    def records_actors(self) -> bool:
        if self._extended is None:
            import psycopg

            try:
                with self._connect() as connection:
                    connection.execute("SELECT actor_id FROM _alk_tool_trace LIMIT 0")
            except psycopg.errors.UndefinedColumn:
                self._extended = False
            except Exception:  # noqa: BLE001 - unknown this time, asked again next time
                return False
            else:
                self._extended = True
        return self._extended

    def write(self, row: dict[str, Any]) -> None:
        extended = self.records_actors()
        if not extended and row.get("actor_id") != AGENT:
            return
        columns = _EXTENDED if extended else _BASIC
        keys = [one.strip() for one in columns.split(",")]
        values = [
            json.dumps(row.get(key), default=str)
            if key in ("arguments", "result")
            else row.get(key)
            for key in keys
        ]
        with self._connect() as connection:
            connection.execute(
                f"INSERT INTO _alk_tool_trace({columns}) VALUES ({', '.join('%s' for _ in keys)})",  # noqa: S608
                values,
            )

    def rows(self) -> list[dict[str, Any]]:
        with self._connect(options="-c default_transaction_read_only=on") as connection:
            cursor = connection.execute("SELECT * FROM _alk_tool_trace ORDER BY at ASC")
            columns = [one[0] for one in cursor.description or []]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def committed(self, key: str, actor: str) -> tuple[Any, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT arguments, result FROM _alk_tool_trace "
                "WHERE idempotency_key = %s AND actor_id = %s AND outcome = 'committed' ORDER BY at LIMIT 1",
                (key, actor),
            ).fetchone()
        return (row[0], row[1]) if row else None

    def agent_calls(self, name: str) -> int:
        clause = " AND actor_id = 'agent'" if self.records_actors() else ""
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT count(*) FROM _alk_tool_trace WHERE name = %s{clause}",
                (name,),  # noqa: S608
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
        self._lock = threading.Lock()

    def records_actors(self) -> bool:
        return True

    def write(self, row: dict[str, Any]) -> None:
        with self._lock:
            self._rows.append(dict(row))

    def rows(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(one) for one in self._rows]

    def committed(self, key: str, actor: str) -> tuple[Any, Any] | None:
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
        return (found["arguments"], found["result"]) if found else None

    def agent_calls(self, name: str) -> int:
        return sum(
            1
            for one in self.rows()
            if one.get("name") == name and one.get("actor_id") == AGENT
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

    def committed(self, key: str, actor: str) -> tuple[Any, Any] | None:
        return self.primary.committed(key, actor)

    def agent_calls(self, name: str) -> int:
        return self.primary.agent_calls(name)


class DictRoster:
    """Who may do what, from records the caller already holds (the harness's actor list)."""

    def __init__(self, actors: Mapping[str, Mapping[str, Any]]) -> None:
        self.actors = dict(actors)

    def actor(self, actor_id: str) -> Mapping[str, Any] | None:
        return self.actors.get(actor_id)
