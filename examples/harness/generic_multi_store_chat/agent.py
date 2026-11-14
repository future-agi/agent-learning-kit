"""Small OpenAI-compatible chat target backed by PostgreSQL and Redis."""

from __future__ import annotations

import json
import os
import re
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import psycopg


def _account_status(account_id: str) -> str | None:
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        row = connection.execute(
            "SELECT support_status FROM accounts WHERE account_id = %s",
            (account_id,),
        ).fetchone()
    return str(row[0]) if row else None


def _redis_command(*parts: str) -> str:
    endpoint = urlsplit(os.environ["CACHE_URL"])
    database = int((endpoint.path or "/0").strip("/") or "0")
    commands = []
    if database:
        commands.append(("SELECT", str(database)))
    commands.append(parts)
    response = ""
    with socket.create_connection(
        (endpoint.hostname or "localhost", endpoint.port or 6379), timeout=5
    ) as client:
        for command in commands:
            encoded = [item.encode("utf-8") for item in command]
            payload = f"*{len(encoded)}\r\n".encode("ascii")
            payload += b"".join(
                f"${len(item)}\r\n".encode("ascii") + item + b"\r\n" for item in encoded
            )
            client.sendall(payload)
            response = client.recv(4096).decode("utf-8", errors="replace").strip()
            if response.startswith("-"):
                raise RuntimeError(response[1:])
    return response.lstrip(":+")


def _answer(text: str) -> str:
    match = re.search(r"\bACC-[A-Za-z0-9-]+\b", text, flags=re.IGNORECASE)
    account_id = match.group(0).upper() if match else "ACC-2048"
    status = _account_status(account_id)
    accesses = _redis_command("INCR", f"account-access:{account_id}")
    if status is None:
        return f"I could not find account {account_id}. Access attempt {accesses}."
    return (
        f"Account {account_id} has support status {status}. Access attempt {accesses}."
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def _send(self, status: int, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send(404, {"ok": False})
            return
        try:
            with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
                connection.execute("SELECT 1").fetchone()
            pong = _redis_command("PING")
        except Exception as exc:
            self._send(503, {"ok": False, "error": type(exc).__name__})
            return
        self._send(200, {"ok": pong == "PONG"})

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(size) or b"{}")
            messages = body.get("messages") or []
            text = str(messages[-1].get("content") or "") if messages else ""
            content = _answer(text)
        except Exception as exc:
            self._send(500, {"error": {"type": type(exc).__name__}})
            return
        self._send(
            200,
            {
                "choices": [
                    {
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ]
            },
        )


if __name__ == "__main__":
    ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler
    ).serve_forever()
