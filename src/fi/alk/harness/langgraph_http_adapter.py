"""Small HTTP boundary for a graph explicitly declared in ``langgraph.json``.

The graph remains submitted source code. This adapter only translates the harness chat
request into LangGraph's standard messages state; it never edits or substitutes the graph.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


def _load_graph() -> Any:
    declaration = os.environ["ALK_LANGGRAPH_ENTRYPOINT"]
    relative, attribute = declaration.rsplit(":", 1)
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
        raise ValueError("invalid LangGraph entrypoint path")
    parts = path.with_suffix("").parts
    if "src" in parts:
        index = parts.index("src")
        sys.path.insert(0, str(Path.cwd().joinpath(*parts[: index + 1])))
        parts = parts[index + 1 :]
    else:
        sys.path.insert(0, str(Path.cwd()))
    graph = getattr(importlib.import_module(".".join(parts)), attribute)
    if not callable(getattr(graph, "ainvoke", None)):
        raise TypeError("declared LangGraph object has no ainvoke method")
    return graph


GRAPH = _load_graph()


def _response_text(state: Any) -> str:
    if isinstance(state, dict):
        report = state.get("final_report")
        if isinstance(report, str) and report.strip():
            return report
        messages = state.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            content = last.get("content") if isinstance(last, dict) else getattr(last, "content", None)
            if isinstance(content, str):
                return content
    raise ValueError("declared LangGraph graph returned no assistant content")


def _invoke(payload: dict[str, Any]) -> dict[str, str]:
    raw_messages = payload.get("messages")
    raw_messages = raw_messages if isinstance(raw_messages, list) else []
    messages = [
        {"role": str(message.get("role") or "user"), "content": str(message.get("content") or "")}
        for message in raw_messages
        if isinstance(message, dict)
    ]
    new_message = payload.get("new_message")
    if isinstance(new_message, dict):
        current = {"role": "user", "content": str(new_message.get("content") or "")}
        if not messages or messages[-1] != current:
            messages.append(current)
    if not messages:
        raise ValueError("chat request contains no messages")
    timeout = float(os.environ.get("ALK_LANGGRAPH_TIMEOUT_SECONDS", "110"))
    state = asyncio.run(asyncio.wait_for(GRAPH.ainvoke({"messages": messages}), timeout))
    return {"content": _response_text(state)}


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: dict[str, str]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802
        self._respond(200, {"status": "ok"}) if self.path == "/health" else self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/invoke":
            self._respond(404, {"error": "not_found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(payload, dict):
                raise TypeError("chat request must be a JSON object")
            self._respond(200, _invoke(payload))
        except Exception as exc:  # noqa: BLE001 - source errors cross the transport boundary
            self._respond(500, {"error": f"{type(exc).__name__}: {exc}", "content": ""})

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
