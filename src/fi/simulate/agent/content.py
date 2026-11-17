"""Reduce a model reply's ``content`` to the text a transcript should show.

The hosted adapters under ``fi.alk.harness`` carry a verbatim copy of the marked region below
(they run in the agent's own venv, which may hold a ``fi.simulate`` without this module);
``tests/simulate/test_content_text.py`` fails if the copies drift.
"""

from __future__ import annotations

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

text_of = _text_of
