import asyncio
from types import SimpleNamespace

import pytest

from test_chat_call_runner import _Adapter, _ToolWorld, _context, _single_exchange

from fi.alk.harness import retell_chat_call_runner as retell
from fi.alk.harness.chat_call_runner import _HostedChatTarget
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState
from fi.alk.harness.retell_chat_call_runner import (
    RetellChatEnded,
    RetellChatWrapper,
    _completion_messages,
)
from fi.alk.harness.run.conversation import TargetConversationEnded
from fi.simulate.agent.wrapper import AgentInput, AgentResponse


def test_completion_messages_preserve_agent_and_completed_tool_evidence() -> None:
    answer, calls, responses = _completion_messages(
        [
            {
                "role": "tool_call_invocation",
                "tool_call_id": "tool-1",
                "name": "lookup_balance",
                "arguments": '{"account_id":"a-1"}',
            },
            {
                "role": "tool_call_result",
                "tool_call_id": "tool-1",
                "content": '{"balance":124}',
            },
            {"role": "agent", "content": "Your balance is $124."},
        ]
    )

    assert answer == "Your balance is $124."
    assert calls == [
        {"id": "tool-1", "name": "lookup_balance", "arguments": {"account_id": "a-1"}}
    ]
    assert responses[0]["tool_call_id"] == "tool-1"
    assert responses[0]["success"] is True


def test_retell_chat_wrapper_creates_one_chat_and_completes_multiple_turns(
    monkeypatch,
) -> None:
    requests: list[tuple[str, str, object]] = []
    wrapper = RetellChatWrapper(api_key="secret", agent_id="agent-1")

    def request(method, path, payload=None):
        requests.append((method, path, payload))
        if path == "/create-chat":
            return {"chat_id": "chat-1"}
        if path == "/create-chat-completion":
            return {"messages": [{"role": "agent", "content": "Hello."}]}
        return {}

    monkeypatch.setattr(wrapper, "_request", request)
    base = {
        "thread_id": "thread-1",
        "messages": [{"role": "user", "content": "Hi"}],
        "new_message": {"role": "user", "content": "Hi"},
    }
    first = asyncio.run(wrapper.call(AgentInput(**base)))
    second = asyncio.run(wrapper.call(AgentInput(**base)))
    asyncio.run(wrapper.aclose())

    assert first.content == second.content == "Hello."
    assert [path for _, path, _ in requests].count("/create-chat") == 1
    assert [path for _, path, _ in requests].count("/create-chat-completion") == 2
    assert requests[-1][:2] == ("PATCH", "/end-chat/chat-1")


def test_retell_chat_wrapper_treats_ended_chat_without_agent_text_as_normal(
    monkeypatch,
) -> None:
    wrapper = RetellChatWrapper(api_key="secret", agent_id="agent-1")

    def request(method, path, payload=None):
        del method, payload
        if path == "/create-chat":
            return {"chat_id": "chat-1"}
        if path == "/create-chat-completion":
            return {"messages": []}
        if path == "/get-chat/chat-1":
            return {"chat_status": "ended"}
        return {}

    monkeypatch.setattr(wrapper, "_request", request)
    response = asyncio.run(
        wrapper.call(
            AgentInput(
                thread_id="thread-1",
                messages=[{"role": "user", "content": "Goodbye"}],
                new_message={"role": "user", "content": "Goodbye"},
            )
        )
    )

    assert response.content == ""
    assert response.metadata["conversation_ended"] is True
    assert response.metadata["external_agent"]["success"] is True


def test_retell_chat_wrapper_treats_already_ended_error_as_normal(monkeypatch) -> None:
    wrapper = RetellChatWrapper(api_key="secret", agent_id="agent-1")

    def request(method, path, payload=None):
        del method, payload
        if path == "/create-chat":
            return {"chat_id": "chat-1"}
        if path == "/create-chat-completion":
            raise RetellChatEnded
        return {}

    monkeypatch.setattr(wrapper, "_request", request)
    response = asyncio.run(
        wrapper.call(
            AgentInput(
                thread_id="thread-1",
                messages=[{"role": "user", "content": "Goodbye"}],
                new_message={"role": "user", "content": "Goodbye"},
            )
        )
    )

    assert response.content == ""
    assert response.metadata["conversation_ended"] is True


def test_retell_chat_wrapper_close_is_idempotent_after_provider_ended_chat(
    monkeypatch,
) -> None:
    wrapper = RetellChatWrapper(api_key="secret", agent_id="agent-1")
    wrapper._chat_id = "chat-1"

    def request(method, path, payload=None):
        del method, path, payload
        raise RetellChatEnded

    monkeypatch.setattr(wrapper, "_request", request)

    asyncio.run(wrapper.aclose())

    assert wrapper._chat_id is None


def test_retell_chat_uses_authored_scenario_document_for_selected_trial(
    tmp_path, monkeypatch
) -> None:
    context = _context(tmp_path)
    looked_up = []

    class Wrapper:
        def __init__(self, **_kwargs):
            pass

        async def call(self, _input):
            return AgentResponse(content="Your account is active.")

        async def aclose(self):
            return

    source_document = retell._scenario_document

    def load_document(bundle_dir, scenario_key):
        looked_up.append(scenario_key)
        return source_document(bundle_dir, scenario_key)

    monkeypatch.setattr(retell, "RetellChatWrapper", Wrapper)
    monkeypatch.setattr(retell, "_scenario_document", load_document)
    monkeypatch.setattr(retell, "_tool_world", lambda *_args: _ToolWorld())
    monkeypatch.setattr(retell, "_drive_conversation", _single_exchange)
    adapter = _Adapter()
    runner = retell.RetellChatCallRunner(adapter, context)
    runtime = EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
    )

    outcome = asyncio.run(
        runner.run(
            SimpleNamespace(
                scenario_key="trial-key",
                source_scenario_key="one",
                scenario_id="trial-id",
            ),
            runtime,
        )
    )

    assert looked_up == ["one"]
    assert outcome.messages[-1]["content"] == "Your account is active."


def test_hosted_chat_target_stops_normally_on_provider_terminal_response() -> None:
    class Wrapper:
        async def call(self, input):
            del input
            return AgentResponse(
                content="",
                metadata={
                    "external_agent": {"success": True},
                    "conversation_ended": True,
                },
            )

    target = _HostedChatTarget(
        wrapper=Wrapper(),
        contract=SimpleNamespace(runtime=None),
        world=SimpleNamespace(calls=[]),
        scenario_key="terminal-chat",
        scenario_id="scenario-1",
    )

    with pytest.raises(TargetConversationEnded):
        asyncio.run(target.say("Goodbye"))
