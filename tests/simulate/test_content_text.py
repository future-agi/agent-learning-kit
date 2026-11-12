"""``text_of`` and its three call sites keep only a turn's text, never its thinking."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fi.simulate.agent.content import text_of
from fi.simulate.agent.wrapper import AgentInput, AgentResponse, AgentWrapper
from fi.simulate.agent.wrappers.langchain import LangChainAgentWrapper
from fi.simulate.environments.chat import ChatEnvironment
from fi.simulate.simulation.models import Persona, Scenario

THINKING = {"type": "thinking", "thinking": "plan the reply", "signature": "sig-abc"}
ANSWER = "The status is complete."


def _blocks():
    return [THINKING, {"type": "text", "text": ANSWER}]


def test_dict_blocks_thinking_first_text_second():
    assert text_of(_blocks()) == ANSWER


def test_sdk_like_objects():
    blocks = [
        SimpleNamespace(type="thinking", thinking="x", signature="sig"),
        SimpleNamespace(type="text", text=ANSWER),
    ]
    assert text_of(blocks) == ANSWER


def test_other_block_types_are_dropped():
    blocks = [
        {"type": "redacted_thinking", "data": "enc"},
        {"type": "tool_use", "id": "t1", "name": "f", "input": {}},
        {"type": "tool_result", "content": "r"},
        {"type": "image", "source": {}},
        {"type": "mystery", "text": "nope"},
        {"type": "text", "text": "kept"},
    ]
    assert text_of(blocks) == "kept"


def test_consecutive_text_blocks_join_without_separator():
    blocks = [{"type": "text", "text": "The status "}, {"type": "text", "text": "is complete."}]
    assert text_of(blocks) == ANSWER


def test_string_none_empty_and_string_list():
    assert text_of("plain") == "plain"
    assert text_of(None) == ""
    assert text_of([]) == ""
    assert text_of(["a", "b"]) == "ab"


def test_strings_and_text_blocks_keep_order():
    assert text_of(["a", {"type": "text", "text": "b"}, THINKING, "c"]) == "abc"


def test_single_block_dict_or_object():
    assert text_of({"type": "text", "text": "one"}) == "one"
    assert text_of(SimpleNamespace(type="text", text="two")) == "two"


def test_anything_else_falls_back_to_str():
    assert text_of(42) == "42"
    assert text_of({"type": "thinking", "thinking": "x"}) == str(
        {"type": "thinking", "thinking": "x"}
    )


def test_langchain_wrapper_returns_only_the_text():
    class Agent:
        def invoke(self, inputs):
            return SimpleNamespace(content=_blocks())

    wrapper = LangChainAgentWrapper(Agent())
    agent_input = AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )
    result = asyncio.run(wrapper.call(agent_input))
    assert result == ANSWER


class _BlockAgent(AgentWrapper):
    def __init__(self, reply):
        self._reply = reply

    async def call(self, input):
        return self._reply


def _run_chat(reply):
    scenario = Scenario(
        name="blocks",
        dataset=[
            Persona(
                persona={"name": "Morgan"},
                situation="I need a status update.",
                outcome="The status is complete.",
            )
        ],
    )
    report = asyncio.run(
        ChatEnvironment().run(
            scenario=scenario,
            agent_callback=_BlockAgent(reply),
            max_turns=1,
            min_turns=1,
        )
    )
    return report.results[0]


def _assistant_contents(result):
    assert result.metadata.get("failure") is None
    return [m["content"] for m in result.messages if m.get("role") == "assistant"]


def test_chat_environment_stores_only_the_text():
    result = _run_chat(_blocks())
    contents = _assistant_contents(result)
    assert contents == [ANSWER]
    assert "signature" not in result.transcript and "thinking" not in result.transcript


def test_chat_environment_cleans_an_agent_response_with_block_content():
    reply = AgentResponse.model_construct(content=_blocks())
    result = _run_chat(reply)
    assert _assistant_contents(result) == [ANSWER]
