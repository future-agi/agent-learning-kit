"""Untyped text in a reply keeps its text."""

import asyncio
from types import SimpleNamespace

import pytest

from fi.simulate.agent.generic import wrap_agent
from fi.simulate.agent.wrapper import AgentInput, AgentResponse


def _generic_reply(reply):
    class Agent:
        def invoke(self, inputs):
            return reply

    agent_input = AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )
    result = asyncio.run(wrap_agent(Agent()).call(agent_input))
    return result.content if isinstance(result, AgentResponse) else result


@pytest.mark.parametrize(
    "reply, expected",
    [
        (
            {"content": [{"text": "Your order shipped today."}]},
            "Your order shipped today.",
        ),
        (
            {"content": [SimpleNamespace(text="Your order shipped today.")]},
            "Your order shipped today.",
        ),
        (
            {"content": ["Sure.", "Your order shipped today."]},
            "Sure. Your order shipped today.",
        ),
        (
            {"message": {"content": [{"text": "Sure."}, "Your order shipped today."]}},
            "Sure. Your order shipped today.",
        ),
        (
            {
                "messages": [
                    {"role": "assistant", "content": [{"text": "Done."}, "Really."]}
                ]
            },
            "Done. Really.",
        ),
    ],
    ids=[
        "untyped-dict-in-content",
        "untyped-object-in-content",
        "plain-strings-in-content",
        "untyped-dict-and-string-in-message",
        "untyped-dict-and-string-in-state",
    ],
)
def test_untyped_text_in_a_reply_keeps_its_text(reply, expected):
    assert _generic_reply(reply) == expected
