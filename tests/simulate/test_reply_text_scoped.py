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
            [
                _content("user", NS(text="q", thought=None)),
                _content("model", _thought(), _answer()),
            ],
            ANSWER,
        ),
        (
            {"data": {"parts": [{"name": "bolt"}, {"name": "nut"}]}},
            '{"parts": [{"name": "bolt"}, {"name": "nut"}]}',
        ),
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
    "reasoningContent": {"reasoningText": {"text": PLAN, "signature": "sig-abc"}}
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
    ids=[
        "raw-converse-response",
        "raw-converse-reasoning-only",
        "control-status-message",
    ],
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


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"data": {"type": "user", "id": 42}}, '{"type": "user", "id": 42}'),
        (
            {"result": {"type": "message", "text": "Your order shipped"}},
            '{"type": "message", "text": "Your order shipped"}',
        ),
        ({"result": {"type": "tool"}}, '{"type": "tool"}'),
        ({"result": {"type": "system"}}, '{"type": "system"}'),
        (
            {"output": {"type": "text", "value": "v"}},
            '{"type": "text", "value": "v"}',
        ),
        (
            {"data": {"role": "user", "name": "n"}},
            '{"role": "user", "name": "n"}',
        ),
        (
            {"response": {"role": "assistant", "text": "Hi there"}},
            '{"role": "assistant", "text": "Hi there"}',
        ),
        ({"output": {"type": "text", "text": ANSWER}}, ANSWER),
        ({"output": {"type": "thinking", "thinking": PLAN}}, ""),
    ],
    ids=[
        "user-typed-record",
        "slack-message-record",
        "tool-typed-record",
        "system-typed-record",
        "text-typed-record-without-text",
        "user-role-record-without-content",
        "assistant-role-record-with-text",
        "typed-text-block-keeps-its-text",
        "typed-thinking-block-gives-nothing",
    ],
)
def test_a_record_under_a_reply_key_stays_json(reply, expected):
    _check(reply, expected)


@pytest.mark.parametrize(
    "reply, expected",
    [
        (
            [{"id": 1, "text": "note A"}, {"id": 2, "text": "note B"}],
            "note A note B",
        ),
        ([{"text": "a"}, {"text": "b"}], "a b"),
        ([{"type": "tool_result", "content": "result text"}], ""),
        ([{"text": "a"}, {"text": PLAN, "thought": True}, {"text": "b"}], "a b"),
    ],
    ids=[
        "search-hits",
        "untyped-parts",
        "tool-result-only-is-not-text",
        "thought-part",
    ],
)
def test_untyped_text_items_of_a_bare_list_join_with_a_space(reply, expected):
    _check(reply, expected)


@pytest.mark.parametrize(
    "reply, expected",
    [
        (
            {
                "output": [
                    NS(type="human", content="q"),
                    NS(type="AIMessageChunk", content=_blocks()),
                ]
            },
            ANSWER,
        ),
        (
            {
                "output": [
                    {"role": "user", "parts": [{"text": "q"}]},
                    {
                        "role": "model",
                        "parts": [{"text": PLAN, "thought": True}, {"text": ANSWER}],
                    },
                ]
            },
            ANSWER,
        ),
        (
            {
                "result": [
                    {"role": "user", "content": "q1"},
                    {"role": "assistant", "content": "A1"},
                    {"role": "user", "content": "q2"},
                    {"role": "assistant", "content": _blocks()},
                ]
            },
            ANSWER,
        ),
    ],
    ids=["langchain-history", "gemini-dict-history", "dict-history"],
)
def test_a_history_under_a_reply_key_gives_the_last_reply(reply, expected):
    _check(reply, expected)


@pytest.mark.parametrize(
    "reply",
    [
        _content("model", _thought(), _answer()),
        {
            "role": "model",
            "parts": [
                {"text": PLAN, "thought": True, "thought_signature": "sig-abc"},
                {"text": ANSWER},
            ],
        },
    ],
    ids=["content-object-returned-raw", "content-dict-returned-raw"],
)
def test_a_gemini_content_returned_as_the_whole_reply_keeps_only_its_answer(reply):
    _check(reply, ANSWER)


def test_a_real_gemini_content_returned_as_the_whole_reply_keeps_only_its_answer():
    types = pytest.importorskip("google.genai.types")
    content = types.Content(
        role="model",
        parts=[
            types.Part(text=PLAN, thought=True, thought_signature=b"sig-abc"),
            types.Part(text=ANSWER),
        ],
    )
    _check(content, ANSWER)


def _dumped(kind, content):
    return {"type": kind, "data": {"content": content}}


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"output": _dumped("ai", _blocks())}, ANSWER),
        ({"output": _dumped("AIMessageChunk", _blocks())}, ANSWER),
        ({"output": _dumped("assistant", _blocks())}, ANSWER),
        (
            {
                "output": [
                    _dumped("human", "q"),
                    _dumped("ai", _blocks()),
                ]
            },
            ANSWER,
        ),
    ],
    ids=["ai", "ai-message-chunk", "assistant", "messages-to-dict-history"],
)
def test_a_langchain_message_to_dict_under_a_reply_key_keeps_only_its_answer(
    reply, expected
):
    _check(reply, expected)


@pytest.mark.parametrize(
    "reply, expected",
    [
        (
            {"result": {"order": "A1", "parts": ["bolt", "nut"]}},
            '{"order": "A1", "parts": ["bolt", "nut"]}',
        ),
    ],
    ids=["record-with-bare-string-parts"],
)
def test_a_record_that_only_resembles_a_reply_stays_json(reply, expected):
    _check(reply, expected)
