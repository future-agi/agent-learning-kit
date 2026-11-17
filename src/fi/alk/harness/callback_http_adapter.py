"""HTTP bridge embedded into hosted bundles for ``fi.simulate`` callbacks.

The bundle producer serializes this module into the process command.  It deliberately uses only
the standard library plus ``AgentInput`` from the submitted agent's own ``fi.simulate`` dependency,
so a callback-only repository does not need to be modified or taught about the hosted process
runtime. That published ``fi.simulate`` may predate ``fi.simulate.agent.content``, so ``_text_of``
below is a private copy of it, not an import.
"""

from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

# Main thread only: with livekit installed this import registers plugins.
from fi.simulate import AgentInput


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
        kind is None
        and isinstance(_field(block, "text"), str)
        and _field(block, "thought") is not True
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
                if not isinstance(item, str) and _field(item, "type") not in _TEXT_KINDS:
                    after_block = False
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


def _load_callback() -> Any:
    target = os.environ["ALK_CALLBACK_ENTRYPOINT"]
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise RuntimeError(f"invalid ALK_CALLBACK_ENTRYPOINT: {target!r}")
    callback = getattr(importlib.import_module(module_name), attribute)
    if not callable(callback):
        raise RuntimeError(f"callback entrypoint is not callable: {target!r}")
    return callback


CALLBACK = _load_callback()


def _json_fields(value: dict[str, Any]) -> dict[str, Any]:
    """The fields of ``value`` that survive ``json.dumps``, minus the raw ``messages``."""
    fields: dict[str, Any] = {}
    for key, item in value.items():
        if key == "messages" or not isinstance(key, str):
            continue
        try:
            json.dumps(item)
        except (TypeError, ValueError):
            continue
        fields[key] = item
    return fields


def _json_body(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        body = value.model_dump(mode="json", exclude_none=True)
    elif isinstance(value, dict):
        if "content" in value or "messages" not in value:
            body = dict(value)
        else:
            # Graph state: the answer is its last message; the other fields travel with it.
            body = {**_json_fields(value), "content": _text_of(value)}
    else:
        body = {"content": _text_of(value)}
    if not isinstance(body, dict):
        raise TypeError("callback response must serialize to a JSON object")
    if "content" in body and not isinstance(body["content"], str):
        body["content"] = _text_of(body["content"])
    body.setdefault("content", "")
    return body


def _invoke(payload: dict[str, Any]) -> dict[str, Any]:
    result = CALLBACK(AgentInput.model_validate(payload))
    if inspect.isawaitable(result):
        result = asyncio.run(result)
    return _json_body(result)


class Handler(BaseHTTPRequestHandler):
    def _respond(self, status: int, body: dict[str, Any]) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") == "/health":
            self._respond(200, {"status": "ok"})
        else:
            self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if self.path.rstrip("/") != "/invoke":
            self._respond(404, {"error": "not_found"})
            return
        started = time.monotonic()
        status = 500
        error_type = "none"
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(payload, dict):
                raise TypeError("request body must be a JSON object")
            self._respond(200, _invoke(payload))
            status = 200
        except Exception as exc:  # noqa: BLE001 - target errors cross the HTTP seam
            error_type = type(exc).__name__
            self._respond(
                500,
                {"error": f"{type(exc).__name__}: {exc}", "content": ""},
            )
        finally:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            print(
                "callback_http_request "
                f"status={status} elapsed_ms={elapsed_ms} error_type={error_type}",
                file=sys.stderr,
                flush=True,
            )

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
