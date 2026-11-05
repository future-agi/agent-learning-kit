"""Bundle-owned observable HTTP tool proxy used by hosted process environments."""

from __future__ import annotations

import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import psycopg

UPSTREAM = os.environ.get("UPSTREAM_URL", "").rstrip("/")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
# This trial's actor token; a request naming an actor without it is the agent's.
ACTOR_PROOF_FILE = os.environ.get("ACTOR_PROOF_FILE", "")


# One request per idempotency key at a time, so a retry racing the first never reaches the world twice.
_KEYS_LOCK = threading.Lock()
_KEY_LOCKS: dict[str, threading.Lock] = {}


def permitted(permitted_actions: object, action: str) -> bool:
    """Whether an actor with these permitted actions may take this world action."""
    return isinstance(permitted_actions, list) and action in permitted_actions


def _actor(actor_id: str) -> dict | None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        row = connection.execute(
            "SELECT permitted_actions, on_behalf_of FROM _alk_actors WHERE id = %s",
            (actor_id,),
        ).fetchone()
    return {"permitted_actions": row[0], "on_behalf_of": row[1]} if row else None


# Whether the action log has the actor columns, once the table has been seen; a world's log keeps its shape.
_LOG_SHAPE: dict[str, bool] = {}


def _logs_actors() -> bool:
    """Whether the world's action log can record whose an action was."""
    if "actors" not in _LOG_SHAPE:
        try:
            with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
                connection.execute("SELECT actor_id FROM _alk_tool_trace LIMIT 0")
        except psycopg.errors.UndefinedColumn:
            _LOG_SHAPE["actors"] = False
        except Exception:
            return False
        else:
            _LOG_SHAPE["actors"] = True
    return _LOG_SHAPE["actors"]


def _trial_token() -> str:
    try:
        with open(ACTOR_PROOF_FILE, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _trial_faults() -> list[dict]:
    """This trial's faults on the agent's calls, written by the runner beside the token; never from a request."""
    if not ACTOR_PROOF_FILE:
        return []
    try:
        with open(os.path.join(os.path.dirname(ACTOR_PROOF_FILE), "trial.json"), encoding="utf-8") as handle:
            faults = json.load(handle).get("faults") or []
    except (OSError, ValueError, AttributeError):
        return []
    return [one for one in faults if isinstance(one, dict)]


def _agent_calls(name: str) -> int:
    """How many times the agent has already sent this action in this trial."""
    try:
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                "SELECT count(*) FROM _alk_tool_trace WHERE name = %s AND actor_id = 'agent'", (name,)
            ).fetchone()
    except Exception:
        return 0
    return int(row[0]) if row else 0


def _record(
    name: str,
    arguments: object,
    result: object,
    outcome: str,
    failure: str = "",
    *,
    refusal_code: str | None = None,
    idempotency_key: str | None = None,
    actor_id: str = "agent",
    parent_event_id: str | None = None,
    on_behalf_of: str | None = None,
) -> None:
    """One row of the world's action log. `ok` stays, true only for a committed action."""
    row = (
        name,
        json.dumps(arguments),
        json.dumps(result),
        outcome == "committed",
        failure,
        time.time(),
    )
    try:
        extended = _logs_actors()
        if not extended and actor_id != "agent":
            return
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            if extended:
                connection.execute(
                    "INSERT INTO _alk_tool_trace(name, arguments, result, ok, error, at, action_id, "
                    "outcome, refusal, refusal_code, idempotency_key, actor_id, parent_event_id, on_behalf_of) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        *row,
                        uuid.uuid4().hex,
                        outcome,
                        failure if outcome == "refused" else None,
                        refusal_code,
                        idempotency_key,
                        actor_id,
                        parent_event_id,
                        on_behalf_of,
                    ),
                )
            else:
                # A world seeded without actors keeps the log's original columns.
                connection.execute(
                    "INSERT INTO _alk_tool_trace(name, arguments, result, ok, error, at) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    row,
                )
    except Exception:
        # Evidence persistence must never alter the target tool response.
        pass


def _committed(key: str) -> tuple[object, object] | None:
    """The arguments and result of this trial's committed action with this key, if there is one."""
    try:
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                "SELECT arguments, result FROM _alk_tool_trace "
                "WHERE idempotency_key = %s AND outcome = 'committed' ORDER BY at LIMIT 1",
                (key,),
            ).fetchone()
    except Exception:
        return None
    return (row[0], row[1]) if row else None


def _key_lock(key: str) -> threading.Lock:
    with _KEYS_LOCK:
        return _KEY_LOCKS.setdefault(key, threading.Lock())


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            return
        self._forward()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self._forward()

    def _reply(self, status: int, payload: object) -> None:
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _log(self, *args: object, **kwargs: object) -> None:
        _record(*args, **kwargs, **self._as)  # type: ignore[arg-type]

    def _action(self) -> str:
        """The world action this request is: its path, `<service>.<action>`."""
        return self.path.split("?", 1)[0].strip("/").replace("/", ".") or "unknown"

    def _refuse(self, arguments: object, code: str, reason: str) -> None:
        # Says why, never whether the record asked about exists.
        self._log(self._name(), arguments, None, "refused", reason, refusal_code=code)
        self._reply(403, {"detail": reason})

    def _forward(self) -> None:
        size = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(size) if size else b""
        try:
            arguments = json.loads(body) if body else {}
        except ValueError:
            arguments = {"_raw": body.decode("utf-8", errors="replace")}
        self._as: dict[str, str | None] = {
            "actor_id": "agent",
            "parent_event_id": (self.headers.get("x-alk-parent-event") or "").strip()
            or None,
            "on_behalf_of": None,
        }
        actor = (self.headers.get("x-alk-actor") or "").strip()
        if actor:
            token = _trial_token()
            sent = (self.headers.get("x-alk-actor-token") or "").strip()
            if not token or not hmac.compare_digest(sent, token):
                self._refuse(
                    arguments,
                    "identity_conflict",
                    "this request names an actor it cannot prove it is",
                )
                return
            if not _logs_actors():
                # Logged without its actor, the action would read as the agent's, so it never reaches the world.
                self._reply(
                    503,
                    {
                        "detail": "this world's action log cannot record an actor's action"
                    },
                )
                return
            self._as["actor_id"] = actor
            try:
                listed = _actor(actor)
            except Exception:
                # The world could not say who the actor is: its fault, never the actor's.
                self._log(
                    self._name(),
                    arguments,
                    None,
                    "faulted",
                    "the world's actor list could not be read",
                )
                self._reply(503, {"detail": "the world could not check this actor"})
                return
            if listed is None:
                self._refuse(
                    arguments, "unknown_actor", f"{actor} is not an actor in this world"
                )
                return
            self._as["on_behalf_of"] = listed["on_behalf_of"]
            if not permitted(listed["permitted_actions"], self._action()):
                self._refuse(
                    arguments,
                    "not_permitted",
                    f"{self._action()} is not permitted for {actor}",
                )
                return
        if not actor and (fault := self._fault()) is not None:
            self._faulted(body, arguments, fault)
            return
        # Only an actor's retries are answered from the first result; the agent's reach the world as they always have.
        key = (self.headers.get("idempotency-key") or "").strip() if actor else ""
        if not key:
            self._send(body, arguments, None)
            return
        with _key_lock(key):
            earlier = _committed(key)
            if earlier is None:
                self._send(body, arguments, key)
                return
            name = self._name()
            if earlier[0] != arguments:
                failure = "this idempotency key was already used with other arguments"
                self._log(
                    name,
                    arguments,
                    None,
                    "refused",
                    failure,
                    refusal_code="idempotency_conflict",
                    idempotency_key=key,
                )
                self._reply(409, {"detail": failure})
                return
            # Answered from the first result; the world never sees the same action twice.
            self._log(name, arguments, earlier[1], "duplicate", idempotency_key=key)
            self._reply(200, earlier[1])

    def _fault(self) -> dict | None:
        """The fault this trial sets on this call of the agent's, if any: by action and which call of it."""
        action = self._action()
        faults = [one for one in _trial_faults() if one.get("action") == action]
        if not faults:
            return None
        call = _agent_calls(self._name()) + 1
        return next((one for one in faults if int(one.get("on_call") or 1) == call), None)

    def _faulted(self, body: bytes, arguments: object, fault: dict) -> None:
        """A timeout the scenario sets: before commit the world never sees the call; after commit it does and the
        agent never hears back. Either way the agent waits, then gets 504, and may retry; a retry is not deduplicated,
        so a second commit is what a check can catch."""
        delay = min(max(float(fault.get("delay_seconds", 30)), 0.0), 30.0)
        name = self._name()
        if fault.get("mode") == "timeout_after_commit":
            outgoing = request.Request(
                UPSTREAM + self.path,
                data=body if self.command != "GET" else None,
                method=self.command,
                headers={"content-type": self.headers.get("content-type", "application/json")},
            )
            try:
                with request.urlopen(outgoing, timeout=30) as response:
                    content = response.read()
                    status = int(response.status)
                result = json.loads(content) if content else None
                self._log(
                    name, arguments, result, "committed" if status < 400 else "refused", "response_lost"
                )
            except Exception as exc:  # noqa: BLE001 - the world failing is a fault of its own
                self._log(name, arguments, None, "faulted", f"{type(exc).__name__}: unavailable")
        else:
            self._log(name, arguments, None, "faulted", "timeout_before_commit")
        time.sleep(delay)
        self._reply(504, {"detail": "tool_upstream_timeout"})

    def _name(self) -> str:
        return self.path.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "unknown"

    def _send(self, body: bytes, arguments: object, key: str | None) -> None:
        outgoing = request.Request(
            UPSTREAM + self.path,
            data=body if self.command != "GET" else None,
            method=self.command,
            headers={
                "content-type": self.headers.get("content-type", "application/json")
            },
        )
        name = self._name()
        try:
            with request.urlopen(outgoing, timeout=30) as response:
                content = response.read()
                status = int(response.status)
                response_type = response.headers.get("content-type", "application/json")
            try:
                result = json.loads(content) if content else None
            except ValueError:
                result = content.decode("utf-8", errors="replace")
            self._log(
                name,
                arguments,
                result,
                "committed" if status < 400 else "refused",
                idempotency_key=key,
            )
            self.send_response(status)
            self.send_header("content-type", response_type)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except error.HTTPError as exc:
            content = exc.read()
            failure = content.decode("utf-8", errors="replace")[:2000]
            # The request's own fault is the world refusing it; the server's is a world fault.
            refused = exc.code < 500
            self._log(
                name,
                arguments,
                None,
                "refused" if refused else "faulted",
                failure,
                refusal_code="world_rule" if refused else None,
                idempotency_key=key,
            )
            self.send_response(exc.code)
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        except Exception as exc:
            self._log(
                name,
                arguments,
                None,
                "faulted",
                f"{type(exc).__name__}: unavailable",
                idempotency_key=key,
            )
            self._reply(502, {"detail": "tool_upstream_unavailable"})


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
