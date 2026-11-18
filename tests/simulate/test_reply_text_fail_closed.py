"""A reply the kit cannot read gives an empty turn, never a printed object."""

import asyncio
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
    return result if isinstance(result, AgentResponse) else AgentResponse(content=result)


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
    assert _text({"status": "error", "message": {"type": "text", "text": "Rate limited"}}) == "Rate limited"


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
        ([{"order_id": "A1"}, {"order_id": "B2"}], '[{"order_id": "A1"}, {"order_id": "B2"}]'),
        ({"result": {"order": "A1", "parts": ["bolt", "nut"]}}, '{"order": "A1", "parts": ["bolt", "nut"]}'),
        ({"result": {"message": "hello", "code": 200}}, '{"message": "hello", "code": 200}'),
        ({"id": 7, "result": {"status": "error", "note": {"type": "text", "text": "Rate limited"}}}, "Rate limited"),
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
def test_plain_data_stays_json_and_a_message_like_value_gives_only_its_text(reply, expected):
    assert _text(reply) == expected


def test_output_key_goes_through_the_same_rule():
    options = {"output_key": "out"}
    assert _text({"out": {"type": "thinking", "thinking": MARK}}, **options) == ""
    assert _text({"out": NS(extra=MARK)}, **options) == ""
    assert _text({"out": {"type": "text", "text": "Hi"}}, **options) == "Hi"
