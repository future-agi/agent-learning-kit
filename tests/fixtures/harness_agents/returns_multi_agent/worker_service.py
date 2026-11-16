"""The Refund worker as a service of its own: agent.py's REFUND agent, in its own process.

With `REFUND_WORKER_URL` pointing here, agent.py's coordinator reaches the Refund worker over HTTP instead of running
it in its own process, so the worker can be stopped alone. Nothing else changes: the same agent, tools, ledger address
(`REFUND_API_URL`) and records' defects.

How it is reached:
    GET  /health  -> 200 {"status": "ok"}
    POST /run     -> {"input": "<what the coordinator asks>"} answered with {"output": "<what the worker said>"}.
It listens on $PORT (default 8081) and reads everything agent.py reads.
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import agent as returns_desk


def _traced(headers: Any) -> Any:
    """The coordinator's trace context from the request, so the worker's spans sit under its tool call."""
    try:
        from opentelemetry import context
        from opentelemetry.propagate import extract
    except ImportError:
        return None
    return context.attach(extract(dict(headers.items())))


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        if self.path.rstrip("/") == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802 - http.server's name
        if self.path.rstrip("/") != "/run":
            self._send(404, {"error": "not found"})
            return
        token = _traced(self.headers)
        try:
            request = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            output = returns_desk.run_worker(returns_desk.REFUND, str(request.get("input") or ""))
        except Exception as error:  # noqa: BLE001 - every failure becomes a 500 the coordinator can read
            self._send(500, {"error": f"{type(error).__name__}: {error}"})
            return
        finally:
            if token is not None:
                from opentelemetry import context

                context.detach(token)
        self._send(200, {"output": output})

    def log_message(self, *_: Any) -> None:
        return


if __name__ == "__main__":
    returns_desk._tracing()
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT") or 8081)), Handler).serve_forever()
