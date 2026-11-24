import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from test_chat_call_runner import (
    _Adapter,
    _ToolWorld,
    _a_full_exchange,
    _blocked_on_the_second_turn,
    _context,
    _single_exchange,
    _stop_runtime,
)
from test_world_call_transactions import pg_world  # noqa: F401 - a fixture

from fi.alk.harness import retell_chat_call_runner as retell
from fi.alk.harness import chat_call_runner as chat
from fi.alk.harness.chat_call_runner import _HostedChatTarget
from fi.alk.harness.hosted_scheduler import CallCancelled
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


def _bound_run(tmp_path, monkeypatch, read_trace):
    """One Retell chat trial on a request-bound world whose agent calls its own `find_orders`."""
    from fi.alk.harness.call_runner import _RequestBinding
    from fi.alk.harness.world.runtime import Call

    # As in the chat worker, which builds its context without the bundle's evidence seam.
    context = _context(tmp_path)
    assert context.evidence_seam is None

    class Wrapper:
        def __init__(self, **_kwargs):
            pass

        async def call(self, _input):
            return AgentResponse(content="Order O423 is in transit.")

        async def aclose(self):
            return

    world = _ToolWorld()
    # What Retell reports: the agent's own name for the tool.
    world.calls.append(
        Call(name="find_orders", arguments={"zip": "30301"}, result={"count": 1})
    )
    cleared = []
    monkeypatch.setattr(retell, "RetellChatWrapper", Wrapper)
    monkeypatch.setattr(retell, "_tool_world", lambda *_args: world)
    monkeypatch.setattr(retell, "_drive_conversation", _single_exchange)
    monkeypatch.setattr(
        retell, "_bind_call", lambda *_args: _RequestBinding(dsn="dsn", token="t")
    )
    monkeypatch.setattr(
        retell, "_find_postgres_endpoint", lambda _r: SimpleNamespace(address="dsn")
    )
    monkeypatch.setattr(retell, "_clear_tool_trace_calls", cleared.append)
    monkeypatch.setattr(retell, "_collect_tool_trace_calls", read_trace)

    async def upload_trace(*_args, **_kwargs):
        return None

    monkeypatch.setattr(retell, "_upload_tool_trace", upload_trace)
    runtime = EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
    )
    outcome = asyncio.run(
        retell.RetellChatCallRunner(_Adapter(), context).run(
            SimpleNamespace(
                scenario_key="one", source_scenario_key="one", scenario_id="scenario-1"
            ),
            runtime,
        )
    )
    return outcome, cleared


def test_a_bound_retell_chat_is_graded_on_the_worlds_trace_not_retells_names(
    tmp_path, monkeypatch
) -> None:
    from fi.alk.harness.world.runtime import Call

    # The world's tool server ran the routed call and traced it as the environment's tool.
    traced = (
        Call(
            name="search_orders",
            arguments={"postal_code": "30301"},
            result={"count": 1},
        ),
    )
    outcome, cleared = _bound_run(tmp_path, monkeypatch, lambda _r, **_k: traced)

    assert [call.name for call in outcome.calls] == ["search_orders"]
    assert outcome.evidence_collected is True
    assert outcome.tool_capture_failed is False
    # The trace is emptied before the chat, so only this trial's calls are read.
    assert cleared == ["dsn"]


def test_a_bound_retell_chat_whose_trace_cannot_be_read_is_unmeasured(
    tmp_path, monkeypatch
) -> None:
    from fi.alk.harness.call_runner import ToolCaptureFailed

    def unreadable(_runtime, **_kwargs):
        raise ToolCaptureFailed("tool_trace read failed: OperationalError")

    outcome, _cleared = _bound_run(tmp_path, monkeypatch, unreadable)

    # Never graded on the names Retell reported.
    assert outcome.calls == ()
    assert outcome.tool_capture_failed is True
    assert outcome.evidence_collected is False


def test_a_bound_retell_chat_with_no_world_call_keeps_the_platforms_own_actions(
    tmp_path, monkeypatch
) -> None:
    outcome, _cleared = _bound_run(tmp_path, monkeypatch, lambda _r, **_k: ())

    assert [call.name for call in outcome.calls] == ["find_orders"]
    assert outcome.evidence_collected is True


def test_postgres_a_bound_retell_chat_reads_the_call_the_world_traced(
    pg_world,  # noqa: F811 - the imported fixture
    tmp_path,
    monkeypatch,
) -> None:
    """The world's own trace, written by its tool server's call path, is what the trial holds."""
    from fi.alk.harness.call_runner import _RequestBinding
    from fi.alk.harness.process_runtime import RuntimeEndpoint
    from fi.alk.harness.run.conversation import FINISHED, Exchange, Transcript
    from fi.alk.harness.world.runtime import Call

    tools, url = pg_world

    class Wrapper:
        def __init__(self, **_kwargs):
            pass

        async def aclose(self):
            return

    async def conversation(target, scenario, _contract, _bundle_dir):
        # Retell's webhook reaches the tool server, which runs the call on the world.
        assert tools.call("book", {"slot": "S1", "request_key": "K1"}).status == "ok"
        return Transcript(
            exchanges=[
                Exchange("customer", scenario.instruction),
                Exchange("agent", "Booked."),
            ],
            # What Retell reports: the agent's own name for the tool.
            calls=[Call(name="reserve_slot", arguments={"when": "S1"})],
            ended=FINISHED,
        )

    monkeypatch.setattr(retell, "RetellChatWrapper", Wrapper)
    monkeypatch.setattr(retell, "_tool_world", lambda *_args: _ToolWorld())
    monkeypatch.setattr(retell, "_drive_conversation", conversation)
    monkeypatch.setattr(
        retell, "_bind_call", lambda *_args: _RequestBinding(dsn=url, token="t")
    )
    runtime = EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={
            "world_db": RuntimeEndpoint(
                capability="world_db",
                protocol="postgres",
                address=url,
                configuration_name="WORLD_DATABASE_URL",
            )
        },
    )
    # A row left by setup is not this trial's call: the trace is emptied before the chat.
    assert tools.call("book", {"slot": "S0", "request_key": "K0"}).status == "ok"

    outcome = asyncio.run(
        retell.RetellChatCallRunner(_Adapter(), _context(tmp_path)).run(
            SimpleNamespace(
                scenario_key="one", source_scenario_key="one", scenario_id="scenario-1"
            ),
            runtime,
        )
    )

    # One call, under the environment's name: setup's row is gone and Retell's name is not used.
    assert [call.name for call in outcome.calls] == ["book"]
    assert outcome.evidence_collected is True


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


class _ClosingWrapper:
    """A Retell chat that ends on close; `close` is awaited when the call closes it."""

    def __init__(self, close=None) -> None:
        self._close = close

    async def call(self, _input):
        return AgentResponse(content="unused")

    async def aclose(self) -> None:
        if self._close is not None:
            await self._close()


def _stoppable_runner(tmp_path, monkeypatch, *, drive, close=None):
    stopped = {"value": False}
    monkeypatch.setattr(retell, "RetellChatWrapper", lambda **_kwargs: _ClosingWrapper(close))
    monkeypatch.setattr(retell, "_tool_world", lambda *_args: _ToolWorld())
    monkeypatch.setattr(retell, "_drive_conversation", drive)
    adapter = _Adapter()
    runner = retell.RetellChatCallRunner(adapter, replace(_context(tmp_path), stop_requested=lambda: stopped["value"]))
    return adapter, runner, stopped


def _cancelled_once(runner, stopped, reached: asyncio.Event, *, stop: bool):
    async def scenario():
        task = asyncio.create_task(
            runner.run(SimpleNamespace(scenario_key="one", scenario_id="scenario-1"), _stop_runtime())
        )
        await reached.wait()
        stopped["value"] = stop
        task.cancel()
        return await task

    return scenario


def _messages(adapter) -> list[str]:
    (transcript,) = [json.loads(data) for data in adapter.uploads]
    return [one["content"] for one in transcript["messages"]]


def test_a_stop_during_a_retell_turn_seals_the_partial_transcript(tmp_path, monkeypatch) -> None:
    seen: dict[str, Any] = {"blocked": None}

    async def scenario():
        seen["blocked"] = asyncio.Event()
        return await _cancelled_once(runner, stopped, seen["blocked"], stop=True)()

    adapter, runner, stopped = _stoppable_runner(tmp_path, monkeypatch, drive=_blocked_on_the_second_turn(seen))
    with pytest.raises(CallCancelled) as caught:
        asyncio.run(scenario())
    partial = caught.value.partial
    assert partial is not None and partial.stop_reason == "user_canceled"
    assert partial.transcript_artifact == "transcript-1" and partial.turns == 3
    assert _messages(adapter) == ["Refund order 1043", "Looking it up", "Is it done?"]


def _closing_after_the_exchange(tmp_path, monkeypatch, *, stop: bool):
    """The Retell chat ended on its own; the cancel reaches the call while it closes the remote chat."""
    closing: dict[str, Any] = {}

    async def drive(*_args, **_kwargs):
        return _a_full_exchange()

    async def close() -> None:
        closing["event"].set()
        await asyncio.sleep(0.2)

    adapter, runner, stopped = _stoppable_runner(tmp_path, monkeypatch, drive=drive, close=close)

    async def scenario():
        closing["event"] = asyncio.Event()
        return await _cancelled_once(runner, stopped, closing["event"], stop=stop)()

    return adapter, scenario


def test_a_stop_after_the_retell_exchange_still_seals_its_transcript(tmp_path, monkeypatch) -> None:
    adapter, scenario = _closing_after_the_exchange(tmp_path, monkeypatch, stop=True)
    with pytest.raises(CallCancelled) as caught:
        asyncio.run(scenario())
    partial = caught.value.partial
    assert partial is not None and partial.stop_reason == "user_canceled" and partial.turns == 4
    assert _messages(adapter) == ["Refund order 1043", "Done, the refund is on its way", "Thanks", "Anything else?"]


def test_a_retell_cancel_that_is_not_a_stop_still_cuts_the_call(tmp_path, monkeypatch) -> None:
    adapter, scenario = _closing_after_the_exchange(tmp_path, monkeypatch, stop=False)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(scenario())
    assert adapter.uploads == []


def test_a_stopped_retell_call_s_close_is_cut_short_so_it_seals_inside_the_stop_bound(tmp_path, monkeypatch) -> None:
    closing: dict[str, Any] = {}

    async def drive(*_args, **_kwargs):
        return _a_full_exchange()

    async def close() -> None:
        closing["event"].set()
        await asyncio.sleep(60)

    monkeypatch.setattr(chat, "_STOPPED_WIND_DOWN_SECONDS", 0.3)
    adapter, runner, stopped = _stoppable_runner(tmp_path, monkeypatch, drive=drive, close=close)

    async def scenario() -> float:
        closing["event"] = asyncio.Event()
        started = time.monotonic()
        with pytest.raises(CallCancelled) as caught:
            await _cancelled_once(runner, stopped, closing["event"], stop=True)()
        assert caught.value.partial is not None and caught.value.partial.turns == 4
        return time.monotonic() - started

    assert asyncio.run(scenario()) < 3
    assert len(adapter.uploads) == 1
