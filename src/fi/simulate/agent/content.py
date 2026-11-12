"""Reduce a model reply's ``content`` to the text a transcript should show."""

from __future__ import annotations

from typing import Any


def _text_block(block: Any) -> tuple[bool, str]:
    """Return ``(is_text_block, text)`` for a dict or SDK block object."""
    if isinstance(block, dict):
        kind, text = block.get("type"), block.get("text")
    else:
        kind, text = getattr(block, "type", None), getattr(block, "text", None)
    if kind == "text" and isinstance(text, str):
        return True, text
    return False, ""


def text_of(content: Any) -> str:
    """Keep only the answer text of a reply.

    Extended-thinking models return a list of blocks (``thinking``, ``text``, ...).
    Plain strings and ``text`` blocks are kept in order and joined with ``""``,
    because one answer can be split across several text blocks. Every other block
    (thinking, redacted_thinking, tool_use, tool_result, image, unknown) is dropped.
    """
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, (list, tuple)):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            is_text, text = _text_block(item)
            if is_text:
                parts.append(text)
        return "".join(parts)
    is_text, text = _text_block(content)
    if is_text:
        return text
    return str(content)
