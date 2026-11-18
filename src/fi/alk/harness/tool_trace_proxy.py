"""Bundle-owned observable HTTP tool proxy used by hosted process environments.

The HTTP surface of the world's door (`world_door.py`): every rule lives in the door."""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:  # Beside the proxy in a bundle; the harness package when run from source.
    from world_door import (
        AGENT,
        HttpExecutor,
        PostgresLog,
        PostgresRecords,
        PostgresRoster,
        RecordExecutor,
        Routed,
        SHARED_ADDRESS,
        ToolCall,
        WorldDoor,
        action_tool,
        declared_read,
        is_timeout,
        method_action,
        route_values,
        world_action,
    )
except ImportError:  # pragma: no cover - imported as part of the package
    from fi.alk.harness.world_door import (  # type: ignore[no-redef]
        AGENT,
        HttpExecutor,
        PostgresLog,
        PostgresRecords,
        PostgresRoster,
        RecordExecutor,
        Routed,
        SHARED_ADDRESS,
        ToolCall,
        WorldDoor,
        action_tool,
        declared_read,
        is_timeout,
        method_action,
        route_values,
        world_action,
    )

UPSTREAM = os.environ.get("UPSTREAM_URL", "").rstrip("/")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# This trial's worker channels, {token: worker id}: a worker reaches the world under /w/<token>/.
WORKER_CHANNELS_FILE = os.environ.get("WORKER_CHANNELS_FILE", "")
# The sealed per-worker allow-lists beside this file, {"worker:<id>": {"allow": [...]}}; a bundle without it does not
# limit workers.
WORKER_PERMISSIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "permissions.json")
_CHANNEL_PREFIX = "/w/"
# The tool each world action is, {"<resource>.get": "<tool>"}, sealed beside this file from the contract's routes.
ACTIONS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "actions.json")
# The world actions the agent's contract declares read-only, named as sealed routes are (`GET <resource>.{id}`).
READ_ACTIONS = [one for one in os.environ.get("READ_ACTIONS", "").split(",") if one]

__all__ = ["Handler", "door"]


# One connection, opened on the first agent call and kept: reading the trial's faults never opens a connection per call.
_FAULTS_CONNECTION = None
_FAULTS_LOCK = threading.Lock()
# The version of the faults row last read: the runner rewrites it at every trial's start, so a new version is a new trial.
_FAULTS_TRIAL = None


def _trial_row(column: str, *, strict: bool = False) -> tuple | None:
    """One column of this trial's row, read over the one kept connection; None when there is no database, table,
    column or row. With `strict`, a read that failed for any other reason raises instead of reading as nothing."""
    global _FAULTS_CONNECTION
    if not DATABASE_URL:
        return None
    with _FAULTS_LOCK:
        try:
            if _FAULTS_CONNECTION is None or _FAULTS_CONNECTION.closed:
                import psycopg

                _FAULTS_CONNECTION = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=5)
            return _FAULTS_CONNECTION.execute(
                f"SELECT {column} FROM _alk_trial_faults WHERE id = 1"  # noqa: S608 - a fixed column name
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 - no table or column: nothing was ever set this trial
            if strict and type(exc).__name__ not in {"UndefinedTable", "UndefinedColumn"}:
                raise
            return None


def _trial_faults() -> list[dict]:
    """This trial's faults on the agent's calls, as the runner wrote them into the world's database, never from a
    request (an agent holding the world's database URL could reach that table too). No row or table is no faults; a
    database that cannot be read raises, since a hold may be among them."""
    global _FAULTS_TRIAL
    row = _trial_row("faults, xmin::text", strict=True)
    _FAULTS_TRIAL = row[1] if row and len(row) > 1 else None
    faults = row[0] if row else []
    return [one for one in faults if isinstance(one, dict)] if isinstance(faults, list) else []


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _worker_deadline() -> float | None:
    """This trial's worker deadline, written by the runner beside its faults; none when it set none. A read that failed
    raises, so the door refuses workers rather than letting them act with no deadline."""
    row = _trial_row("worker_deadline_at", strict=True)
    value = row[0] if row else None
    return float(value) if isinstance(value, (int, float)) else None


def _worker_deadlines() -> dict:
    """Each named worker's own deadline this trial, {worker id: epoch seconds}; none when the scenario gave no budget
    per worker. A read that failed raises, so the door refuses workers rather than letting them act with no deadline."""
    row = _trial_row("worker_deadlines", strict=True)
    value = row[0] if row else None
    return {str(worker): float(at) for worker, at in value.items() if isinstance(at, (int, float))} if isinstance(value, dict) else {}


def _sealed_routes() -> list:
    """The world's own routes as sealed; none when nothing was sealed. A sealed file that cannot be read raises, so a
    templated permission is refused rather than checked against no routes."""
    if not os.path.exists(ACTIONS_FILE):
        return []
    actions = _read_json(ACTIONS_FILE)
    if actions is None:
        raise ValueError("the sealed routes could not be read")
    return list(actions)


def _worker_limits() -> dict:
    """What this trial's workers may do: the sealed allow-lists, when the bundle has them, and the deadline. A seal
    that is present but unreadable is an empty allow-list, and so is a missing one while this trial gave its workers
    channels (the bundle that made the channels sealed their allow-lists): it never widens what a worker may do."""
    limits: dict = {
        "deadline_at": _worker_deadline(),
        "deadlines": _worker_deadlines(),
        "routes": _sealed_routes(),
    }
    if os.path.exists(WORKER_PERMISSIONS_FILE) or _channels_given():
        sealed = _read_json(WORKER_PERMISSIONS_FILE) or {}
        limits["permissions"] = {
            key.removeprefix("worker:"): (value or {}).get("allow")
            for key, value in sealed.items()
            if isinstance(value, dict)
        }
    return limits


def _channels_given() -> bool:
    """Whether this trial gave its workers channels."""
    return bool(WORKER_CHANNELS_FILE) and bool(_read_json(WORKER_CHANNELS_FILE))


def _channel_worker(token: str) -> str:
    """The worker a channel token belongs to in this trial, or "" for none."""
    if not token or not WORKER_CHANNELS_FILE:
        return ""
    worker = (_read_json(WORKER_CHANNELS_FILE) or {}).get(token)
    return worker if isinstance(worker, str) else ""


def door() -> WorldDoor:
    return WorldDoor(
        Routed(HttpExecutor(UPSTREAM), RecordExecutor(PostgresRecords(DATABASE_URL))),
        PostgresLog(DATABASE_URL),
        PostgresRoster(DATABASE_URL),
        faults=_trial_faults,
        workers=_worker_limits,
        trial=lambda: _FAULTS_TRIAL,
    )


_DOOR: WorldDoor | None = None


def _door() -> WorldDoor:
    global _DOOR
    if _DOOR is None:
        _DOOR = door()
    return _DOOR


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        self._enter()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._enter()

    def _call(self) -> ToolCall:
        size = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(size) if size else b""
        try:
            arguments = json.loads(body) if body else {}
        except ValueError:
            arguments = {"_raw": body.decode("utf-8", errors="replace")}
        worker = None
        attribution = None
        # `/w/<token>/...` is a worker's channel only in a trial that gave its workers channels; an app's own `/w/`
        # routes run as before otherwise.
        if _channels_given():
            if self.path.startswith(_CHANNEL_PREFIX):
                token, _, rest = self.path[len(_CHANNEL_PREFIX) :].partition("/")
                # The token is the channel's credential: stripped here, never logged or forwarded.
                self.path = "/" + rest
                worker = _channel_worker(token)
            else:
                # On the shared address: the coordinator's, with the reason logged, as with no channels at all.
                attribution = SHARED_ADDRESS
        route = self.path.split("?", 1)[0]
        # Actors act through the door in the runner's own process; a request here that names one is the agent's own
        # attempt to act as someone else, refused and logged as the agent's.
        actor = (self.headers.get("x-alk-actor") or "").strip()
        proven = not actor
        action = world_action(route) or "unknown"
        actions = _read_json(ACTIONS_FILE)
        # Sealed routes are named with their method: two tools on one path are two actions.
        sealed = method_action(self.command, action)
        if isinstance(arguments, dict):
            # The values a templated route's path carries are the call's arguments too; the body's own win.
            arguments = {**route_values(actions, sealed), **arguments}
        return ToolCall(
            action=action,
            name=route.rstrip("/").rsplit("/", 1)[-1] or "unknown",
            arguments=arguments,
            actor=actor or AGENT,
            key=(self.headers.get("idempotency-key") or "").strip() or None,
            parent=(self.headers.get("x-alk-parent-event") or "").strip() or None,
            proven=proven,
            form="record" if actor and (self.headers.get("x-alk-record") or "").strip() == "1" else "tool",
            method=self.command,
            path=self.path,
            body=body,
            content_type=self.headers.get("content-type", "application/json"),
            worker=worker,
            attribution=attribution,
            tool=action_tool(actions, sealed),
            # A templated read (`GET <resource>.{id}`) covers each record it is called for, as an allow-list does.
            effect="read" if declared_read(READ_ACTIONS, sealed, (actions or {}).keys()) else "",
        )

    def _enter(self) -> None:
        outcome = _door().enter(self._call())
        if outcome.fault is not None and is_timeout(outcome.fault):
            # The agent waits, then hears its call timed out, and may retry.
            time.sleep(outcome.delay_seconds)
            self._reply(504, {"detail": "tool_upstream_timeout"})
            return
        if outcome.fault is not None:
            # Unavailable or the scenario's error: answered at once, the world never saw the call.
            if outcome.body is not None:
                self._reply_raw(outcome.status, outcome.body)
            else:
                self._reply(outcome.status, {"detail": "tool_upstream_unavailable"})
            return
        if outcome.outcome == "faulted" and outcome.body is None:
            self._reply(outcome.status, {"detail": "tool_upstream_unavailable" if outcome.status == 502 else outcome.detail})
            return
        content = outcome.answer()
        self.send_response(outcome.status)
        if outcome.content_type:
            self.send_header("content-type", outcome.content_type)
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _reply(self, status: int, payload: object) -> None:
        self._reply_raw(status, json.dumps(payload).encode())

    def _reply_raw(self, status: int, content: bytes) -> None:
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
