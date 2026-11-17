"""``text_of`` and its three call sites keep only a turn's text, never its thinking."""

import ast
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from text_of_cases import ANSWER, CASES, THINKING, _blocks

import fi.simulate.agent.content as content_module
from fi.simulate.agent.generic import wrap_agent
from fi.simulate.agent.wrapper import (
    AgentInput,
    AgentResponse,
    AgentWrapper,
    SimulationEvent,
)
from fi.simulate.agent.wrappers.langchain import LangChainAgentWrapper
from fi.simulate.environments.chat import ChatEnvironment
from fi.simulate.simulation.models import Persona, Scenario

SRC = Path(content_module.__file__).resolve().parents[2]
COPIES = {
    "content": SRC / "simulate/agent/content.py",
    "callback_http_adapter": SRC / "alk/harness/callback_http_adapter.py",
    "langgraph_http_adapter": SRC / "alk/harness/langgraph_http_adapter.py",
}
REGION = re.compile(r"^# --- begin text_of.*?^# --- end text_of ---$", re.S | re.M)


def _region(path):
    match = REGION.search(path.read_text(encoding="utf-8"))
    assert match, f"{path} has no text_of region"
    return match.group(0)


def _load(path):
    """Run one file's text_of region on its own, as a hosted adapter does (stdlib only)."""
    namespace = {"Any": object}
    exec(compile(_region(path), str(path), "exec"), namespace)
    return namespace["_text_of"]


def _copy(name):
    if name == "content":
        return content_module.text_of
    return _load(COPIES[name])


@pytest.mark.parametrize(
    "copy_name",
    ["content", "callback_http_adapter", "langgraph_http_adapter"],
    ids=[
        "content.text_of",
        "callback_http_adapter._text_of",
        "langgraph_http_adapter._text_of",
    ],
)
@pytest.mark.parametrize(
    "content, expected", [(c, e) for _, c, e in CASES], ids=[i for i, _, _ in CASES]
)
def test_every_copy_keeps_the_text_of_every_reply_shape(copy_name, content, expected):
    assert _copy(copy_name)(content) == expected


def test_the_three_copies_are_the_same_code():
    regions = {name: _region(path) for name, path in COPIES.items()}
    assert len(set(regions.values())) == 1, "text_of copies drifted"
    for name, path in COPIES.items():
        body = ast.parse(path.read_text(encoding="utf-8")).body
        names = [
            t.id
            for node in body
            if isinstance(node, ast.Assign)
            for t in node.targets
            if isinstance(t, ast.Name)
        ]
        names += [node.name for node in body if isinstance(node, ast.FunctionDef)]
        for helper in ("_TEXT_KINDS", "_MISSING", "_field", "_block_text", "_text_of"):
            assert names.count(helper) == 1, (
                f"{name} defines {helper} {names.count(helper)} times"
            )


def test_the_hosted_adapters_import_only_agent_input_from_fi():
    """They run in the agent's own venv, whose fi.simulate may predate agent.content."""
    allowed = {
        "callback_http_adapter": {("fi.simulate", "AgentInput")},
        "langgraph_http_adapter": set(),
    }
    for name, expected in allowed.items():
        tree = ast.parse(COPIES[name].read_text(encoding="utf-8"))
        found = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and (node.module or "").split(".")[0] == "fi"
            ):
                found |= {(node.module, alias.name) for alias in node.names}
            if isinstance(node, ast.Import):
                assert not [a for a in node.names if a.name.split(".")[0] == "fi"]
        assert found == expected, name


def _langchain_reply(response):
    class Agent:
        def invoke(self, inputs):
            return response

    agent_input = AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )
    return asyncio.run(LangChainAgentWrapper(Agent()).call(agent_input))


def test_langchain_wrapper_returns_only_the_text():
    assert _langchain_reply(SimpleNamespace(content=_blocks())) == ANSWER


def test_langchain_wrapper_around_a_langgraph_graph_returns_the_last_message_text():
    state = {
        "messages": [
            SimpleNamespace(content="question"),
            SimpleNamespace(content=_blocks()),
        ]
    }
    assert _langchain_reply(state) == ANSWER


def test_langchain_wrapper_reads_a_dict_message_in_graph_state():
    state = {
        "messages": [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": _blocks()},
        ]
    }
    assert _langchain_reply(state) == ANSWER


def test_langchain_wrapper_output_key_goes_through_text_of():
    assert _langchain_reply({"output": _blocks()}) == ANSWER
    assert _langchain_reply({"output": "plain"}) == "plain"


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


def test_chat_environment_cleans_a_message_object_a_custom_wrapper_returns():
    result = _run_chat(SimpleNamespace(type="ai", content=_blocks()))
    assert _assistant_contents(result) == [ANSWER]
    assert "signature" not in result.transcript


def test_cleaning_an_agent_responses_content_keeps_every_other_field():
    tool_calls = [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "lookup", "arguments": "{}"},
        }
    ]
    tool_responses = [{"role": "tool", "tool_call_id": "c1", "content": "found"}]
    reply = AgentResponse.model_construct(
        content=_blocks(),
        tool_calls=tool_calls,
        tool_responses=tool_responses,
        artifacts=[],
        events=[SimulationEvent(type="note", name="kept_event", payload={"k": 1})],
        memory_updates={"mem": "kept"},
        state={"stage": "kept"},
        metadata={"source": "kept"},
    )
    result = _run_chat(reply)
    assistant = [m for m in result.messages if m.get("role") == "assistant"]
    assert [m["content"] for m in assistant] == [ANSWER]
    assert assistant[0]["tool_calls"] == tool_calls
    assert {"role": "tool", "tool_call_id": "c1", "content": "found"} in result.messages
    names = [event.name for event in result.events]
    assert {
        "agent_tool_calls",
        "kept_event",
        "agent_memory_update",
        "agent_state_update",
    } <= set(names)
    updates = {e.name: e.payload for e in result.events}
    assert updates["agent_memory_update"] == {"mem": "kept"}
    assert updates["agent_state_update"] == {"stage": "kept"}
    # The copy changes `content` only; the caller's object and its metadata are untouched.
    assert reply.metadata == {"source": "kept"}
    assert reply.content == _blocks()


def _generic_reply(reply):
    """What a raw agent's reply becomes after ``wrap_agent(...).call(...)``."""

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
        ({"output": _blocks()}, ANSWER),
        (SimpleNamespace(content=[THINKING]), ""),
        (
            SimpleNamespace(content=[{"type": "refusal", "refusal": "I can't."}]),
            "I can't.",
        ),
        (SimpleNamespace(content=[{"type": "text", "text": {"value": "dv"}}]), "dv"),
        (
            SimpleNamespace(
                content=[
                    {"type": "text", "text": "The status "},
                    {"type": "text", "text": "is complete."},
                ]
            ),
            ANSWER,
        ),
        ({"content": _blocks()}, ANSWER),
        ({"messages": [{"role": "assistant", "content": _blocks()}]}, ANSWER),
        ({"messages": [{"role": "assistant", "content": [THINKING]}]}, ""),
        ({"choices": [{"message": {"content": _blocks()}}]}, ANSWER),
        (
            {"role": "assistant", "content": [{"text": "Your order shipped today."}]},
            "Your order shipped today.",
        ),
        (
            {"result": [{"order_id": "A1", "status": "shipped"}]},
            '[{"order_id": "A1", "status": "shipped"}]',
        ),
        (
            {"content": ["Sure.", "Your order shipped today."]},
            "Sure. Your order shipped today.",
        ),
        (
            {
                "output": [
                    SimpleNamespace(type="human", content="Where is my order?"),
                    SimpleNamespace(type="ai", content="Let me check."),
                    SimpleNamespace(type="tool", content="shipped"),
                    SimpleNamespace(type="ai", content="It shipped today."),
                ]
            },
            "Let me check. It shipped today.",
        ),
        (
            {
                "output": [
                    {"role": "assistant", "content": "Let me check."},
                    {"role": "assistant", "content": "Done."},
                ]
            },
            "Let me check. Done.",
        ),
    ],
    ids=[
        "output-key",
        "only-thinking",
        "refusal",
        "dict-valued-text",
        "split-text-no-extra-space",
        "content-key",
        "state-last-message",
        "state-last-message-only-thinking",
        "choices-message",
        "untyped-text-block",
        "list-of-records-stays-json",
        "list-of-sentences",
        "list-of-messages",
        "list-of-dict-messages",
    ],
)
def test_generic_agent_wrapper_keeps_only_the_text(reply, expected):
    out = _generic_reply(reply)
    assert out == expected
    assert "signature" not in out and "thinking" not in out


def test_langchain_wrapper_returns_only_the_text_of_a_bare_block_list():
    assert _langchain_reply(_blocks()) == ANSWER
    assert _langchain_reply([THINKING]) == ""


def test_the_frameworks_probe_reports_only_the_text_of_a_block_reply():
    from fi.simulate.agent.frameworks import _run_probe_case

    result = asyncio.run(
        _run_probe_case(
            _BlockAgent(_blocks()),
            "generic",
            {"id": "blocks", "input": "hi"},
            index=1,
            trace_runtime=False,
            contract={},
        )
    )
    assert result["response"]["content"] == ANSWER
    payload = json.dumps(result, default=str)
    assert "sig-abc" not in payload and "plan the reply" not in payload


@pytest.mark.parametrize(
    "reply, expected",
    [
        (
            {"result": [{"type": "order", "id": "A1"}]},
            '[{"type": "order", "id": "A1"}]',
        ),
        (
            {"data": [{"name": "Ann", "role": "admin"}]},
            '[{"name": "Ann", "role": "admin"}]',
        ),
        ({"output": [THINKING]}, ""),
    ],
    ids=[
        "records-with-type-stay-json",
        "records-with-role-stay-json",
        "thinking-only-stays-empty",
    ],
)
def test_records_with_a_type_or_role_key_stay_json(reply, expected):
    assert _generic_reply(reply) == expected
