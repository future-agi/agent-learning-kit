"""Bundle-owned observable HTTP tool proxy used by hosted process environments.

One surface of the world's door (`world_door.py`, copied beside it): it reads an HTTP request into a `ToolCall`, proves
an actor by this trial's token, and writes the door's outcome back as HTTP. Every rule lives in the door.
"""

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
        ToolCall,
        WorldDoor,
        permitted,
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
        ToolCall,
        WorldDoor,
        permitted,
    )

UPSTREAM = os.environ.get("UPSTREAM_URL", "").rstrip("/")
DATABASE_URL = os.environ.get("DATABASE_URL", "")

__all__ = ["Handler", "door", "permitted"]


# One connection, opened on the first agent call and kept: reading the trial's faults never opens a connection per call.
_FAULTS_CONNECTION = None
_FAULTS_LOCK = threading.Lock()


def _trial_faults() -> list[dict]:
    """This trial's faults on the agent's calls, written by the runner into the world's database; never from a request.
    Known limit: an agent whose own tools service holds the world's database URL can read and write that table too.
    Read over one kept connection; a world whose runner set no faults has no row, and an unreadable table is no
    faults."""
    global _FAULTS_CONNECTION
    if not DATABASE_URL:
        return []
    with _FAULTS_LOCK:
        try:
            if _FAULTS_CONNECTION is None or _FAULTS_CONNECTION.closed:
                import psycopg

                _FAULTS_CONNECTION = psycopg.connect(DATABASE_URL, autocommit=True, connect_timeout=5)
            row = _FAULTS_CONNECTION.execute("SELECT faults FROM _alk_trial_faults WHERE id = 1").fetchone()
        except Exception:  # noqa: BLE001 - no table (no faults were ever set), or no database: no faults this trial
            return []
    faults = row[0] if row else []
    return [one for one in faults if isinstance(one, dict)] if isinstance(faults, list) else []


def door() -> WorldDoor:
    return WorldDoor(
        Routed(HttpExecutor(UPSTREAM), RecordExecutor(PostgresRecords(DATABASE_URL))),
        PostgresLog(DATABASE_URL),
        PostgresRoster(DATABASE_URL),
        faults=_trial_faults,
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
        route = self.path.split("?", 1)[0]
        # Actors act through the door in the runner's own process; a request here that names one is the agent's own
        # attempt to act as someone else, refused and logged as the agent's.
        actor = (self.headers.get("x-alk-actor") or "").strip()
        proven = not actor
        return ToolCall(
            action=route.strip("/").replace("/", ".") or "unknown",
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
        )

    def _enter(self) -> None:
        outcome = _door().enter(self._call())
        if outcome.fault is not None:
            # The agent waits, then hears its call timed out, and may retry.
            time.sleep(outcome.delay_seconds)
            self._reply(504, {"detail": "tool_upstream_timeout"})
            return
        if outcome.outcome == "faulted" and outcome.body is None:
            self._reply(outcome.status, {"detail": "tool_upstream_unavailable" if outcome.status == 502 else outcome.reason})
            return
        content = outcome.answer()
        self.send_response(outcome.status)
        if outcome.content_type:
            self.send_header("content-type", outcome.content_type)
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _reply(self, status: int, payload: object) -> None:
        content = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
