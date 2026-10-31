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
    try:
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            row = connection.execute(
                "SELECT permitted_actions, on_behalf_of FROM _alk_actors WHERE id = %s", (actor_id,)
            ).fetchone()
    except Exception:
        return None
    return {"permitted_actions": row[0], "on_behalf_of": row[1]} if row else None


def _trial_token() -> str:
    try:
        with open(ACTOR_PROOF_FILE, encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


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
    row = (name, json.dumps(arguments), json.dumps(result), outcome == "committed", failure, time.time())
    try:
        with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
            try:
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
            except psycopg.errors.UndefinedColumn:
                # A world seeded before the log had these columns.
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
            "parent_event_id": (self.headers.get("x-alk-parent-event") or "").strip() or None,
            "on_behalf_of": None,
        }
        actor = (self.headers.get("x-alk-actor") or "").strip()
        if actor:
            token = _trial_token()
            sent = (self.headers.get("x-alk-actor-token") or "").strip()
            if not token or not hmac.compare_digest(sent, token):
                self._refuse(arguments, "identity_conflict", "this request names an actor it cannot prove it is")
                return
            self._as["actor_id"] = actor
            listed = _actor(actor)
            if listed is None:
                self._refuse(arguments, "unknown_actor", f"{actor} is not an actor in this world")
                return
            self._as["on_behalf_of"] = listed["on_behalf_of"]
            if not permitted(listed["permitted_actions"], self._action()):
                self._refuse(arguments, "not_permitted", f"{self._action()} is not permitted for {actor}")
                return
        key = (self.headers.get("idempotency-key") or "").strip()
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
