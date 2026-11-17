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
        ({"output": _content("model", _thought(), _answer())}, ANSWER),
        (NS(response=_content("model", _thought(), _answer())), ANSWER),
        (
            {
                "result": {
                    "role": "model",
                    "parts": [
                        {"text": PLAN, "thought": True, "thought_signature": "sig-abc"},
                        {"text": ANSWER},
                    ],
                }
            },
            ANSWER,
        ),
        ({"output": _content("model", _thought())}, ""),
        (
            [_content("user", NS(text="q", thought=None)),
             _content("model", _thought(), _answer())],
            ANSWER,
        ),
        ({"data": {"parts": [{"name": "bolt"}, {"name": "nut"}]}},
         '{"parts": [{"name": "bolt"}, {"name": "nut"}]}'),
    ],
    ids=[
        "content-object-under-output",
        "content-object-as-attribute",
        "content-dict-under-result",
        "content-thought-only",
        "history-of-content-objects",
        "control-records-with-parts-stay-json",
    ],
)
def test_a_gemini_content_keeps_only_its_answer(reply, expected):
    _check(reply, expected)


BEDROCK_REASONING = {
    "reasoningContent": {
        "reasoningText": {"text": PLAN, "signature": "sig-abc"}
    }
}


@pytest.mark.parametrize(
    "reply, expected",
    [
        (
            {
                "output": {
                    "message": {
                        "role": "assistant",
                        "content": [BEDROCK_REASONING, {"text": ANSWER}],
                    }
                },
                "stopReason": "end_turn",
                "usage": {"inputTokens": 3, "outputTokens": 4},
            },
            ANSWER,
        ),
        (
            {
                "output": {
                    "message": {"role": "assistant", "content": [BEDROCK_REASONING]}
                }
            },
            "",
        ),
        (
            {"result": {"message": "hello", "code": 200}},
            '{"message": "hello", "code": 200}',
        ),
    ],
    ids=["raw-converse-response", "raw-converse-reasoning-only", "control-status-message"],
)
def test_a_raw_bedrock_converse_response_keeps_only_its_answer(reply, expected):
    _check(reply, expected)


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
