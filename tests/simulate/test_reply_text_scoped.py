"""Replies the first scoped check found leaking or lost under a reply key."""

from types import SimpleNamespace as NS

import pytest
from test_text_of_untyped import _generic_reply
from text_of_cases import ANSWER, THINKING, _blocks

PLAN = "plan the reply"


def _check(reply, expected):
    text = _generic_reply(reply)
    assert text == expected
    assert "sig-abc" not in text and PLAN not in text


def _thought():
    return NS(text=PLAN, thought=True, thought_signature="sig-abc")


def _answer():
    return NS(text=ANSWER, thought=None, thought_signature=None)


def _content(role, *parts):
    return NS(role=role, parts=list(parts))


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"output": NS(type="AIMessageChunk", content=_blocks())}, ANSWER),
        ({"output": {"type": "AIMessageChunk", "content": _blocks()}}, ANSWER),
        ({"output": NS(type="AIMessageChunk", content=[THINKING])}, ""),
    ],
    ids=["chunk-object", "chunk-dict", "chunk-thinking-only"],
)
def test_a_langchain_message_chunk_keeps_only_its_answer(reply, expected):
    _check(reply, expected)
