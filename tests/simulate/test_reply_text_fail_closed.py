"""A reply the kit cannot read gives an empty turn, never a printed object."""

import asyncio
import logging
from types import SimpleNamespace as NS

import pytest

from fi.simulate.agent.generic import wrap_agent
from fi.simulate.agent.wrapper import AgentInput, AgentResponse

MARK = "marker-9f3a"


class _Printable:
    """An object whose printed form carries a marker."""

    def __repr__(self):
        return f"<Printable {MARK}>"

    __str__ = __repr__


def _call(reply, **options):
    class Agent:
        def invoke(self, inputs):
            return reply

    agent_input = AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )
    result = asyncio.run(wrap_agent(Agent(), **options).call(agent_input))
    return (
        result if isinstance(result, AgentResponse) else AgentResponse(content=result)
    )


def _text(reply, **options):
    return _call(reply, **options).content


@pytest.mark.parametrize(
    "reply",
    [
        {"output": {"role": "assistant", "content": {"k": MARK}}},
        {"role": "assistant", "content": {"k": MARK}},
        {"output": {"type": "thinking", "content": MARK}},
        {"type": "thinking", "thinking": MARK},
        {"text": MARK, "thought": True},
        {"output": NS(text=MARK, thought=True)},
        {"output": NS(type="thinking", content=MARK)},
    ],
    ids=[
        "message-with-a-record-as-content",
        "message-returned-whole-with-a-record-as-content",
        "thinking-block-with-content-string",
        "thinking-block-returned-whole",
        "thought-part-returned-whole",
        "thought-object-under-output",
        "typed-object-with-content",
    ],
)
def test_a_block_or_message_that_is_not_answer_text_gives_nothing(reply):
    assert MARK not in _text(reply)


def test_a_message_type_without_content_gives_nothing_and_an_error_record_gives_its_text():
    assert _text({"type": "user", "id": 1}) == ""
    assert (
        _text({"status": "error", "message": {"type": "text", "text": "Rate limited"}})
        == "Rate limited"
    )


@pytest.mark.parametrize(
    "reply",
    [
        NS(extra=MARK),
        _Printable(),
        {"message": _Printable()},
        {"choices": [_Printable()]},
        {"messages": [_Printable()]},
        {"result": {"k": NS(x=MARK)}},
        {"result": [NS(x=MARK)]},
        {"data": {"k": _Printable()}},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": MARK}]},
    ],
    ids=[
        "unknown-object",
        "object-with-a-printed-form",
        "object-under-message",
        "object-under-choices",
        "object-under-messages",
        "object-nested-in-data",
        "object-in-a-list-in-data",
        "object-nested-under-data",
        "choice-without-a-message",
    ],
)
def test_a_value_nothing_can_read_gives_no_text(reply):
    assert MARK not in _text(reply)


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"role": "user", "name": "n"}, ""),
        ({"order": "A1", "total": 3}, '{"order": "A1", "total": 3}'),
        (
            [{"order_id": "A1"}, {"order_id": "B2"}],
            '[{"order_id": "A1"}, {"order_id": "B2"}]',
        ),
        (
            {"result": {"order": "A1", "parts": ["bolt", "nut"]}},
            '{"order": "A1", "parts": ["bolt", "nut"]}',
        ),
        (
            {"result": {"message": "hello", "code": 200}},
            '{"message": "hello", "code": 200}',
        ),
        (
            {
                "id": 7,
                "result": {
                    "status": "error",
                    "note": {"type": "text", "text": "Rate limited"},
                },
            },
            "Rate limited",
        ),
    ],
    ids=[
        "chat-role-without-content",
        "plain-record-stays-json",
        "plain-list-stays-json",
        "bare-string-parts-stay-json",
        "status-message-stays-json",
        "text-block-inside-a-record-is-found",
    ],
)
def test_plain_data_stays_json_and_a_message_like_value_gives_only_its_text(
    reply, expected
):
    assert _text(reply) == expected


def test_output_key_goes_through_the_same_rule():
    options = {"output_key": "out"}
    assert _text({"out": {"type": "thinking", "thinking": MARK}}, **options) == ""
    assert _text({"out": NS(extra=MARK)}, **options) == ""
    assert _text({"out": {"type": "text", "text": "Hi"}}, **options) == "Hi"


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"order": "A1", "signature": "sig-1"}, '{"order": "A1"}'),
        ({"order": "A1", "thought_signature": "sig-1"}, '{"order": "A1"}'),
        ({"order": "A1", "encrypted_content": "enc"}, '{"order": "A1"}'),
        ({"order": "A1", "reasoning_content": "why"}, '{"order": "A1"}'),
        ({"order": "A1", "reasoningContent": {"text": "why"}}, '{"order": "A1"}'),
        (
            {"order": "A1", "lines": [{"sku": "x", "signature": "sig-1"}]},
            '{"order": "A1", "lines": [{"sku": "x"}]}',
        ),
    ],
    ids=[
        "signature",
        "thought-signature",
        "encrypted-content",
        "reasoning-content",
        "reasoning-content-camel",
        "nested-signature",
    ],
)
def test_the_plain_data_dump_drops_the_known_reasoning_keys(reply, expected):
    assert _text(reply) == expected


def test_output_key_value_is_scrubbed_too():
    assert (
        _text({"out": {"order": "A1", "signature": "s"}}, output_key="out")
        == '{"order": "A1"}'
    )


def test_an_unreadable_reply_records_its_type_and_key_names_only():
    response = _call({"type": "user", "name": MARK})
    assert response.content == ""
    record = response.metadata["reply_unreadable"]
    assert record == {"type": "dict", "keys": ["name", "type"]}
    assert MARK not in repr(record) and "user" not in repr(record)


def test_an_unreadable_object_records_its_type_name():
    response = _call(_Printable())
    assert response.content == ""
    assert response.metadata["reply_unreadable"] == {"type": "_Printable", "keys": []}


def test_an_unreadable_reply_logs_names_only(caplog):
    with caplog.at_level(logging.INFO, logger="fi.simulate.agent.generic"):
        _call({"type": "user", "name": MARK})
    lines = [r.getMessage() for r in caplog.records if "unreadable" in r.getMessage()]
    assert len(lines) == 1
    assert "dict" in lines[0] and "name" in lines[0] and "type" in lines[0]
    assert MARK not in lines[0] and "user" not in lines[0]


@pytest.mark.parametrize(
    "reply",
    [
        None,
        {},
        [],
        {"content": "Hello"},
        {"output": {"type": "text", "text": "Hello"}},
        {"order": "A1"},
    ],
    ids=["none", "empty-dict", "empty-list", "text", "text-block", "plain-record"],
)
def test_a_reply_that_is_empty_or_was_read_records_nothing(reply):
    assert "reply_unreadable" not in (_call(reply).metadata or {})
