"""Text leaves a reply only through ``text_of``: a block or message that is not answer text gives nothing."""

import asyncio
from types import SimpleNamespace as NS

import pytest

from fi.simulate.agent.generic import wrap_agent
from fi.simulate.agent.wrapper import AgentInput, AgentResponse

MARK = "marker-9f3a"


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
