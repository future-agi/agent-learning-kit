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
_MISSING = object()
_MOST_DEPTH = 32


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


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


def _text_of(content: Any, depth: int = 0) -> str:
    """Keep only the answer text of a reply.

    Extended-thinking models return a list of blocks (``thinking``, ``text``, ...). Answer
    blocks are kept in order and joined with ``""`` only when they sit next to each other,
    because one answer can be split across several text blocks. Plain strings are joined with
    ``" "``. A list holding assistant messages is a history: only its last assistant message
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
        after_block = False
        for item in content:
            is_block = not isinstance(item, str)
            piece = item if isinstance(item, str) else _block_text(item, depth)
            if not piece:
                if not isinstance(item, str) and not _is_text_block(item):
                    after_block = False
                continue
            if text and not (is_block and after_block):
                text += " "
            text += piece
            after_block = is_block
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

text_of = _text_of
