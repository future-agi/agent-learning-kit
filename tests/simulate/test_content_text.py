"""``text_of`` and its three call sites keep only a turn's text, never its thinking."""

import ast
import asyncio
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace
from types import SimpleNamespace as NS

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


GUARDED = (
    "_TEXT_KINDS",
    "_ENVELOPE_KINDS",
    "_CHAT_ROLES",
    "_REPLY_ITEM_KINDS",
    "_STREAM_ANSWER_TYPES",
    "_STREAM_ANSWER_FIELDS",
    "_MISSING",
    "_MOST_DEPTH",
    "_field",
    "_stream_answer_text",
    "_is_agent_turn",
    "_is_reply_item",
    "_is_history_item",
    "_is_block_or_message",
    "_is_untyped_text",
    "_stream_event_text",
    "_is_text_block",
    "_block_text",
    "_is_message",
    "_text_of",
)


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
        for helper in GUARDED:
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
            "It shipped today.",
        ),
        (
            {
                "output": [
                    {"role": "assistant", "content": "Let me check."},
                    {"role": "assistant", "content": "Done."},
                ]
            },
            "Done.",
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


def test_the_drift_guard_names_every_helper_of_the_region():
    """A name defined in the region but not in the guard can be rebound outside it unseen."""
    tree = ast.parse(_region(COPIES["content"]))
    defined = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            defined.add(node.name)
        if isinstance(node, ast.Assign):
            defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    assert defined == set(GUARDED)


def _generic_stream(chunks, *, is_async=False):
    """The AgentResponse a streaming agent becomes after ``wrap_agent(...).call(...)``."""

    class SyncAgent:
        def invoke(self, inputs):
            yield from chunks

    class AsyncAgent:
        async def invoke(self, inputs):
            for chunk in chunks:
                yield chunk

    agent = AsyncAgent() if is_async else SyncAgent()
    agent_input = AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )
    return asyncio.run(wrap_agent(agent).call(agent_input))


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "chunks",
    [
        [
            {"type": "token", "content": "Your order "},
            {"type": "token", "content": "shipped today."},
        ],
        [
            {"type": "delta", "delta": "Your order "},
            {"type": "delta", "delta": "shipped today."},
        ],
        [
            {"type": "chunk", "text": "Your order "},
            {"type": "chunk", "text": "shipped today."},
        ],
        [
            {"type": "TOKEN", "content": "Your order "},
            {"type": "text-delta", "delta": "shipped "},
            {"type": "Text_Message_Content", "delta": "today."},
        ],
        [
            SimpleNamespace(type="token", content="Your order "),
            SimpleNamespace(type="answer", text="shipped today."),
        ],
        [
            {"type": "token", "content": "Your order "},
            {"type": "token", "content": "shipped today."},
            {"type": "done"},
        ],
        [
            {"type": "thought", "content": "THOUGHT-1"},
            {"type": "token", "content": "Your order "},
            {"type": "status", "content": "STATUS-1", "text": "STATUS-2"},
            {"type": "token", "content": "shipped today."},
            {"type": "tool_result", "content": "TOOL-1", "delta": "TOOL-2"},
        ],
        [
            {"type": "token", "content": "Your order "},
            {"type": "vendor.token", "content": "VENDOR-1"},
            {"type": "token", "content": ["LIST-1"]},
            {"type": "token", "content": {"a": "DICT-1"}},
            {"type": "token", "content": "shipped today."},
        ],
    ],
    ids=[
        "token-content",
        "delta-delta",
        "chunk-text",
        "spellings-of-the-listed-types",
        "objects",
        "ends-in-done",
        "thought-status-and-tool-chunks-give-nothing",
        "dotted-and-non-string-chunks-give-nothing",
    ],
)
def test_a_stream_chunk_of_the_customers_own_type_is_read_when_the_type_is_listed(
    chunks, is_async
):
    result = _generic_stream(chunks, is_async=is_async)
    assert result.content == "Your order shipped today."
    assert "reply_unreadable" not in (result.metadata or {})


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
def test_a_stream_of_only_thought_chunks_is_unreadable_and_names_the_types(is_async):
    chunks = [
        {"type": "thought", "content": "THOUGHT-1"},
        {"type": "thought", "content": "THOUGHT-2"},
    ]
    result = _generic_stream(chunks, is_async=is_async)
    assert result.content == ""
    assert result.metadata["reply_unreadable"] == {
        "type": "stream",
        "keys": ["content", "type"],
        "chunk_types": ["thought"],
    }
    assert "THOUGHT" not in json.dumps(result.metadata["reply_unreadable"])


def test_a_stream_with_no_chunks_is_not_recorded_as_unreadable():
    result = _generic_stream([])
    assert result.content == ""
    assert "reply_unreadable" not in (result.metadata or {})


BEDROCK_BLOCKS = [{"text": "Your order shipped."}, {"text": "Anything else?"}]


@pytest.mark.parametrize(
    "reply",
    [
        {"role": "assistant", "content": BEDROCK_BLOCKS},
        {"content": BEDROCK_BLOCKS},
        {"output": BEDROCK_BLOCKS},
        BEDROCK_BLOCKS,
    ],
    ids=["message-content", "content-key", "output-key", "bare-list"],
)
def test_untyped_text_blocks_join_with_a_space_wherever_they_sit(reply):
    assert _generic_reply(reply) == "Your order shipped. Anything else?"


def test_stream_chunks_join_with_nothing_whatever_the_untyped_rule_is():
    chunks = [{"text": "Your order "}, {"text": "shipped."}]
    assert _generic_stream(chunks).content == "Your order shipped."


def _generic_response(reply):
    class Agent:
        def invoke(self, inputs):
            return reply

    agent_input = AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )
    return asyncio.run(wrap_agent(Agent()).call(agent_input))


@pytest.mark.parametrize(
    "reply",
    [
        {"result": [{"role": "user", "content": "where is my order"}]},
        {"output": [{"role": "user", "content": "where is my order"}]},
        [{"role": "user", "content": "where is my order"}],
        [
            {"role": "user", "content": "where is my order"},
            {"role": "tool", "content": "TOOL-1"},
        ],
        {"result": [SimpleNamespace(type="human", content="where is my order")]},
    ],
    ids=[
        "result-key",
        "output-key",
        "bare-list",
        "user-then-tool",
        "human-object",
    ],
)
def test_a_history_with_no_assistant_message_gives_no_user_words(reply):
    result = _generic_response(reply)
    assert result.content == ""
    assert "reply_unreadable" in result.metadata


# --- Untyped text in a reply keeps its text. ---


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


# --- Untyped provider thinking blocks (Gemini, Bedrock, langchain-aws) never reach the reply. ---

BEDROCK_REASONING = {
    "reasoningContent": {
        "reasoningText": {"text": "plan the reply", "signature": "sig-abc"}
    }
}


AWS_REASONING = {
    "type": "reasoning_content",
    "reasoning_content": {"text": "plan the reply", "signature": "sig-abc"},
}


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"content": [{"text": "plan the reply", "thought": True}]}, ""),
        (
            {
                "role": "assistant",
                "content": [
                    BEDROCK_REASONING,
                    {"toolUse": {"toolUseId": "t", "name": "f", "input": {}}},
                ],
            },
            "",
        ),
        ({"output": [{"text": ANSWER}, BEDROCK_REASONING]}, ANSWER),
        ({"content": [AWS_REASONING]}, ""),
        (
            {
                "message": {
                    "role": "model",
                    "parts": [
                        {"text": "plan the reply", "thought": True},
                        {"text": ANSWER},
                    ],
                }
            },
            ANSWER,
        ),
    ],
    ids=[
        "gemini-thought-only",
        "bedrock-reasoning-tool-turn",
        "bedrock-text-and-reasoning-under-output",
        "langchain-aws-reasoning-only",
        "gemini-content-parts-in-message",
    ],
)
def test_an_untyped_thinking_block_never_shows(reply, expected):
    text = _generic_reply(reply)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text


# --- A single message (not a list) under a reply key keeps only its text. ---


class _Message:  # an AIMessage-like object with model_dump
    def __init__(self, content):
        self.type, self.content = "ai", content

    def model_dump(self):
        return {"type": self.type, "content": self.content, "additional_kwargs": {}}


@pytest.mark.parametrize(
    "reply, expected",
    [
        ({"output": _Message(_blocks())}, ANSWER),
        ({"response": {"role": "assistant", "content": _blocks()}}, ANSWER),
        (
            {"result": {"type": "message", "role": "assistant", "content": _blocks()}},
            ANSWER,
        ),
        (NS(output=_Message(_blocks())), ANSWER),
        (
            {"result": [{"order_id": "A1", "status": "shipped"}]},
            '[{"order_id": "A1", "status": "shipped"}]',
        ),
        (
            {"data": {"name": "Ann", "role": "admin"}},
            '{"name": "Ann", "role": "admin"}',
        ),
    ],
    ids=[
        "message-object-under-output",
        "message-dict-under-response",
        "responses-message-under-result",
        "message-object-as-output-attribute",
        "control-records-stay-json",
        "control-record-dict-stays-json",
    ],
)
def test_a_message_under_a_reply_key_never_shows_thinking(reply, expected):
    text = _generic_reply(reply)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text


# --- A bare block list, a history or a stream returned by the agent keeps only the text. ---


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
        ([{"text": "a"}, {"text": "b"}], "a b"),
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


def _agent_input():
    return AgentInput(
        thread_id="t",
        execution_id="t",
        messages=[{"role": "user", "content": "hi"}],
        new_message={"role": "user", "content": "hi"},
    )


def _stream_reply(chunks):
    class Agent:
        def invoke(self, inputs):
            yield from chunks

    result = asyncio.run(wrap_agent(Agent()).call(_agent_input()))
    return result.content if isinstance(result, AgentResponse) else result


@pytest.mark.parametrize(
    "chunks, expected",
    [
        (
            [
                NS(content=[THINKING, _text("The status ")]),
                NS(content=[_text("is complete.")]),
            ],
            ANSWER,
        ),
        (
            [{"content": [THINKING]}, {"delta": [_text("The status is complete.")]}],
            ANSWER,
        ),
        ([{"content": "The status "}, {"content": "is complete."}], ANSWER),
        (
            [
                {"message": {"role": "assistant", "content": [THINKING, _text("Hi")]}},
                NS(output=[THINKING]),
            ],
            "Hi",
        ),
        ([{"content": {"city": "Pune"}}], '{"city": "Pune"}'),
    ],
    ids=[
        "chunk-objects-with-blocks",
        "chunk-dicts-with-blocks",
        "string-deltas",
        "message-chunk-and-thinking-only-chunk",
        "record-chunk-stays-json",
    ],
)
def test_a_streamed_reply_keeps_only_the_text(chunks, expected):
    text = _stream_reply(chunks)
    assert text == expected
    assert "sig-abc" not in text and "plan the reply" not in text


def _anthropic(delta):
    return {"type": "content_block_delta", "index": 0, "delta": delta}


_ANTHROPIC_THINKING_STREAM = [
    {
        "type": "message_start",
        "message": {"id": "m", "role": "assistant", "content": []},
    },
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
    },
    _anthropic({"type": "thinking_delta", "thinking": "plan the reply"}),
    _anthropic({"type": "signature_delta", "signature": "sig-abc"}),
    {"type": "content_block_stop", "index": 0},
    {
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "text", "text": ""},
    },
    _anthropic({"type": "text_delta", "text": "Hi"}),
    _anthropic({"type": "text_delta", "text": " there"}),
    {"type": "message_stop"},
]


_OPENAI_REASONING_STREAM = [
    {"type": "response.created", "response": {"id": "r"}},
    {"type": "response.reasoning_summary_text.delta", "delta": "plan the reply"},
    {"type": "response.reasoning_text.delta", "delta": "plan the reply"},
    {"type": "response.output_text.delta", "delta": "Hi"},
    {"type": "response.output_text.delta", "delta": " there"},
    {"type": "response.completed", "response": {"id": "r", "output": []}},
]


def _as_objects(chunks):
    def convert(value):
        if isinstance(value, dict):
            return NS(**{key: convert(item) for key, item in value.items()})
        return value

    return [convert(chunk) for chunk in chunks]


@pytest.mark.parametrize(
    "chunks",
    [
        _ANTHROPIC_THINKING_STREAM,
        _OPENAI_REASONING_STREAM,
        _as_objects(_ANTHROPIC_THINKING_STREAM),
        _as_objects(_OPENAI_REASONING_STREAM),
    ],
    ids=[
        "anthropic-events",
        "openai-responses-events",
        "anthropic-objects",
        "openai-objects",
    ],
)
def test_a_stream_of_provider_events_keeps_only_the_answer_text(chunks):
    assert _stream_reply(chunks) == "Hi there"


@pytest.mark.parametrize(
    "chunk",
    [
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "t", "name": "f", "input": {}},
        },
        {"type": "response.completed", "response": {"id": "r"}},
        {"type": "message_start", "message": {"id": "m", "content": []}},
        {"type": "thinking", "thinking": "plan the reply", "signature": "sig-abc"},
        {"type": "tool_use", "id": "t", "name": "f", "input": {}},
        {"type": "input_json_delta", "partial_json": '{"a"'},
    ],
    ids=[
        "tool-use-block-start",
        "response-completed",
        "message-start",
        "thinking-block",
        "tool-use-block",
        "input-json-delta",
    ],
)
def test_a_stream_event_with_a_non_text_type_gives_nothing(chunk):
    assert _stream_reply([chunk]) == ""


@pytest.mark.parametrize(
    "chunks",
    [
        [{"type": "vendor.custom_event", "payload": {"secret": "SIG"}}, "Hi"],
        [{"type": "vendor.custom_event", "delta": {"secret": "SIG"}}, "Hi"],
        [NS(type="vendor.custom_event", content="SIG", payload={"s": "SIG"}), "Hi"],
        [NS(type="AIMessageChunk", content=[_text("Hi")])],
        [{"type": "message", "role": "assistant", "content": "Hi"}],
        [{"content": "H"}, NS(text="i")],
    ],
    ids=[
        "unknown-dict-event",
        "unknown-dict-event-with-delta",
        "unknown-object-event",
        "langchain-chunk-type-kept",
        "message-type-kept",
        "untyped-chunks-kept",
    ],
)
def test_a_stream_event_of_an_unknown_type_gives_nothing(chunks):
    text = _stream_reply(chunks)
    assert text == "Hi"
    assert "SIG" not in text


# --- Replies the first scoped check found leaking or lost under a reply key. ---

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


SCOPED_BEDROCK_REASONING = {
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
                        "content": [SCOPED_BEDROCK_REASONING, {"text": ANSWER}],
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
                    "message": {
                        "role": "assistant",
                        "content": [SCOPED_BEDROCK_REASONING],
                    }
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
        ({"data": {"type": "user", "id": 42}}, ""),
        ({"result": {"type": "message", "text": "Your order shipped"}}, ""),
        ({"result": {"type": "tool"}}, ""),
        ({"result": {"type": "system"}}, ""),
        ({"output": {"type": "text", "value": "v"}}, ""),
        ({"data": {"role": "user", "name": "n"}}, ""),
        ({"response": {"role": "assistant", "text": "Hi there"}}, "Hi there"),
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
def test_a_message_like_record_under_a_reply_key_gives_only_its_text(reply, expected):
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
        (
            {
                "result": {
                    "status": "error",
                    "code": 429,
                    "message": {"type": "text", "text": "Rate limited"},
                }
            },
            "Rate limited",
        ),
    ],
    ids=["record-with-bare-string-parts", "error-record-gives-its-text-block"],
)
def test_a_record_with_bare_string_parts_stays_json_and_an_error_record_gives_its_text(
    reply, expected
):
    _check(reply, expected)


# --- Nested replies are read only to a fixed depth. ---

BOUND = 32


TOO_DEEP_FOR_THE_STACK = 3000


def _namespace(name):
    """Run one file's text_of region on its own, as a hosted adapter does (stdlib only)."""
    path = COPIES[name]
    match = REGION.search(path.read_text(encoding="utf-8"))
    assert match, f"{path} has no text_of region"
    namespace = {"Any": object}
    exec(compile(match.group(0), str(path), "exec"), namespace)
    return namespace


def _message_chain(depth, leaf="x"):
    value = leaf
    for _ in range(depth):
        value = {"role": "assistant", "content": value}
    return value


def _list_chain(depth, leaf="x"):
    value = leaf
    for _ in range(depth):
        value = [{"role": "assistant", "content": value}]
    return value


def _state_chain(depth, leaf="x"):
    value = {"messages": [{"role": "assistant", "content": leaf}]}
    for _ in range(depth - 2):
        value = {"messages": [value]}
    return value


def _object_chain(depth, leaf="x"):
    value = leaf
    for _ in range(depth):
        value = SimpleNamespace(type="ai", content=value)
    return value


CHAINS = {
    "message-dicts": _message_chain,
    "message-lists": _list_chain,
    "graph-states": _state_chain,
    "message-objects": _object_chain,
}


COPY_NAMES = list(COPIES)


@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_every_copy_names_the_same_depth_bound(copy_name):
    assert _namespace(copy_name)["_MOST_DEPTH"] == BOUND


@pytest.mark.parametrize("chain", CHAINS, ids=list(CHAINS))
@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_nested_reply_at_the_bound_keeps_its_text(copy_name, chain):
    assert _namespace(copy_name)["_text_of"](CHAINS[chain](BOUND)) == "x"


@pytest.mark.parametrize("chain", CHAINS, ids=list(CHAINS))
@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_nested_reply_one_past_the_bound_gives_nothing(copy_name, chain):
    assert _namespace(copy_name)["_text_of"](CHAINS[chain](BOUND + 1)) == ""


@pytest.mark.parametrize("chain", CHAINS, ids=list(CHAINS))
@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_reply_nested_past_the_stack_limit_gives_nothing_and_never_raises(
    copy_name, chain
):
    assert (
        _namespace(copy_name)["_text_of"](CHAINS[chain](TOO_DEEP_FOR_THE_STACK)) == ""
    )


@pytest.mark.parametrize("copy_name", COPY_NAMES)
def test_a_deep_reply_without_an_answer_never_shows_its_repr(copy_name):
    reply = _message_chain(BOUND + 5, leaf={"type": "image", "source": "SECRET"})
    assert "SECRET" not in _namespace(copy_name)["_text_of"](reply)


def test_a_reply_one_past_the_bound_through_a_generic_agent_gives_nothing():
    assert _generic_reply({"output": _list_chain(BOUND + 1)}) == ""


# --- The frameworks probe traces only the text of a non-string AgentResponse content. ---


def test_a_non_string_agent_response_is_normalized_and_keeps_its_fields():
    from fi.simulate.agent.frameworks import _run_probe_case

    class Agent(AgentWrapper):
        async def call(self, input):
            return AgentResponse.model_construct(
                content=_blocks(),
                tool_calls=[{"name": "lookup", "arguments": {}}],
                artifacts=[],
                events=[],
            )

    result = asyncio.run(
        _run_probe_case(
            Agent(),
            "generic",
            {"id": "blocks", "input": "hi"},
            index=1,
            trace_runtime=False,
            contract={},
        )
    )
    assert result["response"]["content"] == ANSWER
    assert result["response"]["tool_names"] == ["lookup"]
    payload = json.dumps(result, default=str)
    assert "sig-abc" not in payload and "plan the reply" not in payload


# --- A reply the kit cannot read gives an empty turn, never a printed object. ---

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


def _closed_text(reply, **options):
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
    assert MARK not in _closed_text(reply)


def test_a_message_type_without_content_gives_nothing_and_an_error_record_gives_its_text():
    assert _closed_text({"type": "user", "id": 1}) == ""
    assert (
        _closed_text(
            {"status": "error", "message": {"type": "text", "text": "Rate limited"}}
        )
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
    assert MARK not in _closed_text(reply)


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
    assert _closed_text(reply) == expected


def test_output_key_goes_through_the_same_rule():
    options = {"output_key": "out"}
    assert (
        _closed_text({"out": {"type": "thinking", "thinking": MARK}}, **options) == ""
    )
    assert _closed_text({"out": NS(extra=MARK)}, **options) == ""
    assert _closed_text({"out": {"type": "text", "text": "Hi"}}, **options) == "Hi"


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
    assert _closed_text(reply) == expected


def test_output_key_value_is_scrubbed_too():
    assert (
        _closed_text({"out": {"order": "A1", "signature": "s"}}, output_key="out")
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


# --- A history read from state or under a reply key gives only the agent's own turn. ---

_USER_ONLY = [{"role": "user", "content": "where is my order"}]


@pytest.mark.parametrize(
    "reply",
    [
        {"messages": _USER_ONLY},
        SimpleNamespace(messages=_USER_ONLY),
        {"output": {"messages": _USER_ONLY}},
        {"response": _USER_ONLY},
        {"data": _USER_ONLY},
        {"result": {"messages": _USER_ONLY}},
        {
            "messages": [
                {"role": "user", "content": "q"},
                {"type": "tool", "content": "T"},
            ]
        },
        {"messages": [SimpleNamespace(type="human", content="where is my order")]},
    ],
    ids=[
        "state-messages",
        "object-messages",
        "output-state",
        "response-list",
        "data-list",
        "result-state",
        "user-then-tool-state",
        "human-object-state",
    ],
)
def test_a_history_with_no_assistant_message_gives_no_user_words_on_every_route(reply):
    result = _generic_response(reply)
    assert result.content == ""
    assert "reply_unreadable" in result.metadata


@pytest.mark.parametrize(
    "reply",
    [
        {
            "messages": [
                {"role": "assistant", "content": "Shipped."},
                {"role": "user", "content": "thanks"},
            ]
        },
        {
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "Shipped."},
                {"role": "tool", "content": "T"},
            ]
        },
        {
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "model", "parts": [{"text": "Shipped."}]},
            ]
        },
    ],
    ids=["assistant-then-user", "assistant-then-tool", "gemini-model-turn"],
)
def test_a_state_history_gives_its_last_agent_turn(reply):
    assert _generic_response(reply).content == "Shipped."


# --- A stream chunk of a message kind that is not the agent's gives nothing. ---


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize(
    "other",
    [
        {"type": "tool", "content": "BEHIND-1"},
        {"type": "user", "content": "BEHIND-2"},
        {"type": "human", "content": "BEHIND-3"},
        {"type": "system", "content": "BEHIND-4"},
        {"type": "message", "role": "user", "content": "BEHIND-5"},
        SimpleNamespace(type="human", content="BEHIND-6"),
    ],
    ids=["tool", "user", "human", "system", "message-from-user", "human-object"],
)
def test_a_stream_chunk_of_a_message_kind_that_is_not_the_agents_gives_nothing(
    other, is_async
):
    chunks = [
        {"type": "token", "content": "Your order "},
        other,
        {"type": "token", "content": "shipped today."},
    ]
    result = _generic_stream(chunks, is_async=is_async)
    assert result.content == "Your order shipped today."
