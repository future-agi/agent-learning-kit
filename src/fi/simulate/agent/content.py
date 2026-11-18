"""Reduce a model reply's ``content`` to the text a transcript should show.

The hosted adapters under ``fi.alk.harness`` carry a verbatim copy of the marked region below
(they run in the agent's own venv, which may hold a ``fi.simulate`` without this module);
``tests/simulate/test_content_text.py`` fails if the copies drift.
"""

from __future__ import annotations

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

# What ``fi.simulate.agent.generic`` reads from this module: one rule, three copies of it.
text_of = _text_of
field = _field
stream_event_text = _stream_event_text
is_agent_message = _is_message
is_agent_turn = _is_agent_turn
is_reply_item = _is_reply_item
is_history_item = _is_history_item
is_block_or_message = _is_block_or_message
is_untyped_text = _is_untyped_text
CHAT_ROLES = _CHAT_ROLES
ENVELOPE_KINDS = _ENVELOPE_KINDS
REPLY_ITEM_KINDS = _REPLY_ITEM_KINDS
MOST_DEPTH = _MOST_DEPTH
