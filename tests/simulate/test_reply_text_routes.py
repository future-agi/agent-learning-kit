"""A bare block list or a message history returned by the agent keeps only the text."""

from types import SimpleNamespace as NS

import pytest
from test_text_of_untyped import _generic_reply
from text_of_cases import ANSWER, THINKING, _blocks


def _text(text):
    return {"type": "text", "text": text}


@pytest.mark.parametrize(
    "reply, expected",
    [
        (_blocks(), ANSWER),
        ([_text(ANSWER), THINKING], ANSWER),
        ([THINKING], ""),
        (
            [_text("The status"), THINKING, _text("is complete.")],
            "The status is complete.",
        ),
        ([{"text": "a"}, {"text": "b"}], "ab"),
        (tuple(_blocks()), ANSWER),
        (
            [
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "A1"},
                {"role": "user", "content": "q2"},
                {"role": "assistant", "content": [THINKING, _text("A2")]},
            ],
            "A2",
        ),
        (
            [
                NS(type="human", content="q"),
                NS(type="ai", content="Let me check."),
                NS(type="ai", content=_blocks()),
            ],
            ANSWER,
        ),
    ],
    ids=[
        "thinking-then-text",
        "text-then-thinking",
        "only-thinking",
        "text-around-thinking",
        "untyped-parts",
        "tuple",
        "dict-history-gives-the-last-reply-only",
        "object-history-gives-the-last-reply-only",
    ],
)
def test_a_bare_block_list_or_a_history_returned_by_the_agent_is_reduced_to_text(
    reply, expected
):
    text = _generic_reply(reply)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text
