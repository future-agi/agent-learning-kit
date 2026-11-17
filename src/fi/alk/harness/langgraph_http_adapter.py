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


# --- begin text_of (kept identical in agent/content.py and the hosted adapters) ---
_TEXT_KINDS = ("text", "output_text")
_MISSING = object()


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _block_text(block: Any) -> str:
    """Answer text of one content block; "" for any block that is not answer text."""
    kind = _field(block, "type")
    if kind in _TEXT_KINDS or (
        kind is None and isinstance(_field(block, "text"), str)
    ):
        text = _field(block, "text")
        if isinstance(text, dict):
            text = text.get("value")
        return text if isinstance(text, str) else ""
    if kind == "refusal":
        refusal = _field(block, "refusal")
        return refusal if isinstance(refusal, str) else ""
    if kind == "ai" or _field(block, "role") == "assistant":
        return _text_of(_field(block, "content"))
    return ""


def _is_message(item: Any) -> bool:
    kind = _field(item, "type")
    if kind in _TEXT_KINDS or kind == "refusal":
        return False
    return kind == "ai" or _field(item, "role") == "assistant"


def _text_of(content: Any) -> str:
    """Keep only the answer text of a reply.

    Extended-thinking models return a list of blocks (``thinking``, ``text``, ...). Answer
    blocks are kept in order and joined with ``""`` only when they sit next to each other,
    because one answer can be split across several text blocks. Plain strings and whole
    messages (``AIMessage``, ``{"role", "content"}``, which give the text of their content)
    are joined with ``" "``. Any other block (thinking, redacted_thinking, tool_use,
    tool_result, image, unknown) gives ``""``, never its repr.
    """
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, (list, tuple)):
        text = ""
        after_block = False
        for item in content:
            is_block = not isinstance(item, str) and not _is_message(item)
            piece = item if isinstance(item, str) else _block_text(item)
            if not piece:
                continue
            if text and not (is_block and after_block):
                text += " "
            text += piece
            after_block = is_block
        return text
    if (
        isinstance(content, dict)
        and "content" not in content
        and isinstance(content.get("messages"), list)
        and content["messages"]
    ):
        return _text_of(content["messages"][-1])
    kind = _field(content, "type")
    if kind in _TEXT_KINDS or kind == "refusal":
        return _block_text(content)
    if kind != "tool_result":
        if isinstance(content, dict):
            inner = content.get("content", _MISSING)
        else:
            inner = getattr(content, "content", _MISSING)
        if inner is not _MISSING:
            return _text_of(inner)
    if isinstance(kind, str):
        return ""
    return str(content)


# --- end text_of ---


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
            if isinstance(content, (list, tuple)):
                return _text_of(content)
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
