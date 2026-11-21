"""Small HTTP boundary for a graph explicitly declared in ``langgraph.json``.

The graph remains submitted source code. This adapter only translates the harness chat
request into LangGraph's standard messages state; it never edits or substitutes the graph.

Every request runs on one event loop, on a daemon thread that lives as long as the process,
so a model client built when the graph's module is imported keeps a live loop across turns.
The kit runs one agent process per world and sends its turns one at a time, so sharing the
loop costs little, but:

- a node that blocks the loop (synchronous I/O inside an ``async`` node) stalls any other
  request in flight until it returns;
- synchronous nodes share the loop's default thread pool, and ``threading.local`` state in
  async node code is shared between requests (context variables stay per request);
- a graph still running at ``ALK_LANGGRAPH_TIMEOUT_SECONDS`` is cancelled and answers 500
  ``RequestTimeout``, as the subprocess adapter does: a slow graph is the agent's failure,
  not one to retry. A node that blocks the loop, or catches the cancel, can run past the
  deadline; if it is still running ``_WAIT_MARGIN_SECONDS`` later, the request answers 500
  ``RequestTimeout`` then. A request answered with a timeout before its graph started never
  starts it;
- tasks a turn starts and leaves running are not cancelled when the turn ends;
- if the graph's code stops the loop itself, every later turn is refused and ``/health``
  answers 503 instead of 200;
- ``close()`` is for tests: it ends the loop for good, because clients built at import may
  be bound to it.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


# --- begin text_of (kept identical in agent/content.py and the hosted adapters) ---
_TEXT_KINDS = ("text", "output_text")
_ENVELOPE_KINDS = (
    "ai",
    "AIMessageChunk",
    "human",
    "tool",
    "system",
    "user",
    "assistant",
    "message",
)
# A streamed chunk of a type the customer chose is answer text only when its type is one of these,
# compared without regard to case, "-" or "_". Any other type, dotted or not, gives nothing.
_STREAM_ANSWER_TYPES = frozenset(
    (
        "token",
        "text",
        "delta",
        "chunk",
        "content",
        "answer",
        "output",
        "outputtext",
        "textdelta",
        "textmessagecontent",
    )
)
_STREAM_ANSWER_FIELDS = ("content", "text", "delta")
_CHAT_ROLES = frozenset(("assistant", "user", "system", "tool", "developer", "model"))
_REPLY_ITEM_KINDS = frozenset(
    _TEXT_KINDS
    + _ENVELOPE_KINDS
    + (
        "refusal",
        "thinking",
        "redacted_thinking",
        "reasoning",
        "reasoning_content",
        "tool_use",
        "server_tool_use",
        "tool_result",
        "image",
    )
)
_MISSING = object()
_MOST_DEPTH = 32


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _stream_answer_text(chunk: Any) -> str:
    """Text of a streamed chunk whose undotted type is on the answer list, else ``""``.

    The text is the first of ``content``, ``text`` and ``delta`` that is a string. A thought, a
    status, a tool result, a provider event (its type has a dot) or any unlisted type gives
    nothing, so only what a reader positively knows is answer text reaches the transcript.
    """
    kind = _field(chunk, "type")
    if not isinstance(kind, str) or "." in kind:
        return ""
    if kind.replace("-", "").replace("_", "").lower() not in _STREAM_ANSWER_TYPES:
        return ""
    for name in _STREAM_ANSWER_FIELDS:
        value = _field(chunk, name)
        if isinstance(value, str):
            return value
    return ""


def _is_text_block(block: Any) -> bool:
    kind = _field(block, "type")
    return kind in _TEXT_KINDS or (
        kind is None
        and isinstance(_field(block, "text"), str)
        and _field(block, "thought") is not True
    )


def _block_text(block: Any, depth: int = 0) -> str:
    """Answer text of one content block; "" for any block that is not answer text."""
    kind = _field(block, "type")
    if _is_text_block(block):
        text = _field(block, "text")
        if isinstance(text, dict):
            text = text.get("value")
        return text if isinstance(text, str) else ""
    if kind == "refusal":
        refusal = _field(block, "refusal")
        return refusal if isinstance(refusal, str) else ""
    if _is_message(block):
        return _text_of(_field(block, "content"), depth + 1)
    return ""


def _is_message(item: Any) -> bool:
    """An assistant message: an ``ai`` message, or a ``role: assistant`` one that is no typed block."""
    kind = _field(item, "type")
    if kind in ("ai", "AIMessageChunk"):
        return True
    return _field(item, "role") == "assistant" and kind in (None, "message")


def _is_agent_turn(item: Any) -> bool:
    """A message the agent said: an assistant or model role, or an ``ai`` or assistant type."""
    role, kind = _field(item, "role"), _field(item, "type")
    return role in ("assistant", "model") or kind in (
        "ai",
        "AIMessageChunk",
        "assistant",
    )


def _is_reply_item(value: Any) -> bool:
    """A message or a block by its own ``role``, ``type`` or ``thought`` flag."""
    role = _field(value, "role")
    kind = _field(value, "type")
    return (
        (isinstance(role, str) and role in _CHAT_ROLES)
        or (isinstance(kind, str) and kind in _REPLY_ITEM_KINDS)
        or _field(value, "thought") is True
    )


def _is_history_item(item: Any) -> bool:
    """A chat message by its ``type`` or ``role``, whoever said it."""
    role, kind = _field(item, "role"), _field(item, "type")
    return (isinstance(kind, str) and kind in _ENVELOPE_KINDS) or (
        isinstance(role, str) and role in _CHAT_ROLES
    )


def _is_block_or_message(item: Any) -> bool:
    role, kind = _field(item, "role"), _field(item, "type")
    if (isinstance(kind, str) and kind in _REPLY_ITEM_KINDS) or (
        isinstance(role, str) and role in _CHAT_ROLES
    ):
        return True
    # Untyped provider blocks that carry thinking: a Gemini thought part, a Bedrock reasoning block.
    return kind is None and (
        _field(item, "thought") is True or _field(item, "reasoningContent") is not None
    )


def _is_untyped_text(item: Any) -> bool:
    return _field(item, "type") is None and isinstance(_field(item, "text"), str)


def _stream_event_text(chunk: Any) -> Any:
    """Answer text of a stream event; None when ``chunk`` is a message or has no ``type``.

    Anthropic raw events and OpenAI Responses events carry thinking, signatures, tool input
    and bookkeeping next to the answer. Only ``response.output_text.delta`` and a
    ``content_block_delta`` holding a listed delta give text. An agent message kind (and a chunk with
    no string ``type``) is left to the caller's key lookup; a tool, user, human or system chunk
    gives ``""``. Any other typed event gives the
    string of a listed answer type (``_stream_answer_text``), else ``""``, never its JSON.
    """
    kind = _field(chunk, "type")
    if not isinstance(kind, str):
        return None
    if kind == "content_block_delta":
        return _stream_event_text(_field(chunk, "delta")) or ""
    if kind == "response.output_text.delta":
        value = _field(chunk, "delta")
        return value if isinstance(value, str) else ""
    if kind in ("tool", "user", "human", "system"):
        return ""  # a message of the caller or of a tool, never the agent's answer
    if (
        kind == "refusal"
        or kind in _ENVELOPE_KINDS
        or kind.endswith(("Message", "MessageChunk"))
    ):
        role = _field(chunk, "role")
        if (
            isinstance(role, str)
            and role in _CHAT_ROLES
            and role not in ("assistant", "model")
        ):
            return ""
        return None
    return _stream_answer_text(chunk)


def _text_of(content: Any, depth: int = 0) -> str:
    """Keep only the answer text of a reply.

    Extended-thinking models return a list of blocks (``thinking``, ``text``, ...). Answer
    blocks are kept in order. Typed text blocks (``text``, ``output_text``) that sit next to
    each other are joined with ``""``, because one answer can be split across several of them.
    Every other pair of pieces, plain strings and blocks with no ``type`` (a Bedrock Converse
    ``{"text": ...}``, a Gemini part), is joined with ``" "``. A list holding assistant messages is a history: only its last assistant message
    counts (``AIMessage``, ``{"role": "assistant", "content"}``), and its content is joined as
    above. Any other block (thinking, redacted_thinking, tool_use, tool_result, image,
    unknown) gives ``""``, never its repr, and a typed block is never unwrapped as a message.
    A message inside a message is read down to ``_MOST_DEPTH`` levels; anything nested deeper
    gives ``""``.
    """
    if depth > _MOST_DEPTH:
        return ""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, (list, tuple)):
        for item in reversed(content):
            if _is_message(item):
                return _block_text(item, depth)
        text = ""
        after_typed = False
        for item in content:
            typed = not isinstance(item, str) and _field(item, "type") is not None
            piece = item if isinstance(item, str) else _block_text(item, depth)
            if not piece:
                if not isinstance(item, str) and not _is_text_block(item):
                    after_typed = False
                continue
            if text and not (typed and after_typed):
                text += " "
            text += piece
            after_typed = typed
        return text
    kind = _field(content, "type")
    if kind in _TEXT_KINDS or kind == "refusal":
        return _block_text(content, depth)
    if isinstance(kind, str) and kind not in _ENVELOPE_KINDS:
        return ""
    if (
        isinstance(content, dict)
        and "content" not in content
        and isinstance(content.get("messages"), list)
        and content["messages"]
    ):
        return _text_of(content["messages"][-1], depth + 1)
    if isinstance(content, dict):
        inner = content.get("content", _MISSING)
    else:
        inner = getattr(content, "content", _MISSING)
    if inner is not _MISSING:
        return _text_of(inner, depth + 1)
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


_WAIT_MARGIN_SECONDS = 5.0
_CLOSE_WAIT_SECONDS = 5.0
_LOOP_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
_LOOP_THREAD: threading.Thread | None = None
_CLOSED = False


class RequestTimeout(Exception):
    """The graph did not answer within the configured time."""


def _start_loop() -> None:
    # Called with the lock held.
    global _LOOP, _LOOP_THREAD
    if _CLOSED:
        raise RuntimeError("the LangGraph adapter is closed")
    if _LOOP_THREAD is not None and not _LOOP_THREAD.is_alive():
        # A node stopped the loop; clients built at import may be bound to it, so a new loop
        # would not help them.
        raise RuntimeError("the LangGraph adapter's event loop stopped; restart the agent")
    if _LOOP is None:
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, name="alk-langgraph-loop", daemon=True)
        thread.start()
        _LOOP, _LOOP_THREAD = loop, thread


async def _run_graph(messages: list[dict[str, str]], timeout: float, abandoned: threading.Event) -> Any:
    if abandoned.is_set():
        # Answered already, while the loop was busy: never start the graph.
        raise RequestTimeout(f"graph did not answer within {timeout:g}s")
    task = asyncio.current_task()
    expired = False

    def expire() -> None:
        nonlocal expired
        expired = True
        task.cancel()

    # The graph runs in this task, not a child one, so the deadline is told apart from the
    # graph's own TimeoutError on every Python version, and SystemExit can't stop the loop.
    deadline = asyncio.get_running_loop().call_later(timeout, expire)
    try:
        return await GRAPH.ainvoke({"messages": messages})
    except asyncio.CancelledError:
        # Once the request is abandoned nobody reads this task's result, so it ends cancelled.
        if expired and not abandoned.is_set():
            raise RequestTimeout(f"graph did not answer within {timeout:g}s") from None
        raise
    except (SystemExit, KeyboardInterrupt) as exc:
        raise RuntimeError(f"the graph raised {type(exc).__name__}") from exc
    finally:
        deadline.cancel()


def _submit(messages: list[dict[str, str]], timeout: float, abandoned: threading.Event):
    with _LOOP_LOCK:
        _start_loop()
        return asyncio.run_coroutine_threadsafe(_run_graph(messages, timeout, abandoned), _LOOP)


async def _shutdown() -> None:
    current = asyncio.current_task()
    tasks = {task for task in asyncio.all_tasks() if task is not current}
    for task in tasks:
        task.cancel()
    pending: set[asyncio.Task[Any]] = set()
    if tasks:
        _, pending = await asyncio.wait(tasks, timeout=_CLOSE_WAIT_SECONDS)
    loop = asyncio.get_running_loop()
    await loop.shutdown_asyncgens()
    if not pending:
        # A node that ignores the cancel may hold an executor thread; don't wait for it.
        await loop.shutdown_default_executor()


def close() -> None:
    """End the loop for good, cancelling any request in flight. For tests."""
    global _LOOP, _LOOP_THREAD, _CLOSED
    with _LOOP_LOCK:
        loop, thread = _LOOP, _LOOP_THREAD
        if thread is not None and thread is threading.current_thread():
            raise RuntimeError("close() can't run on the adapter's own event loop")
        _CLOSED = True
        _LOOP = _LOOP_THREAD = None
        if loop is None:
            return
        if thread is not None and thread.is_alive():
            try:
                asyncio.run_coroutine_threadsafe(_shutdown(), loop).result(
                    _CLOSE_WAIT_SECONDS + _WAIT_MARGIN_SECONDS
                )
            except Exception:  # noqa: BLE001 - a node that won't stop must not hold up close()
                pass
            loop.call_soon_threadsafe(loop.stop)
            thread.join(_CLOSE_WAIT_SECONDS)
        if not loop.is_running():
            loop.close()


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
    abandoned = threading.Event()
    future = _submit(messages, timeout, abandoned)
    try:
        # The margin lets the deadline fire first; this wait covers a node that ignores cancellation.
        state = future.result(timeout + _WAIT_MARGIN_SECONDS)
    except concurrent.futures.CancelledError as exc:
        if _CLOSED:
            raise RuntimeError("the LangGraph adapter closed while the graph ran") from exc
        raise RuntimeError("the graph was cancelled") from exc
    except concurrent.futures.TimeoutError:
        if not future.done():
            abandoned.set()
            future.cancel()
            raise RequestTimeout(f"graph did not answer within {timeout:g}s") from None
        # The graph finished, or raised a TimeoutError of its own, just as the wait ended.
        state = future.result()
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
        if self.path != "/health":
            self._respond(404, {"error": "not_found"})
        elif _LOOP_THREAD is not None and not _LOOP_THREAD.is_alive():
            self._respond(503, {"status": "event_loop_stopped"})
        else:
            self._respond(200, {"status": "ok"})

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
