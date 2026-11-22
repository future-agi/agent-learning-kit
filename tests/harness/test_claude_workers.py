"""Native sub-agents: what tools they are actually given."""

import asyncio
from types import SimpleNamespace

import logging

from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from fi.alk.harness.backends.base import (
    CALL_BUDGET_SPENT,
    MOST_WORKERS_AT_ONCE,
    Call,
    CallBudgetSpent,
    ConversationSession,
    ModelReply,
    SessionSpec,
    StageDone,
    ToolServer,
    ToolSpec,
    WorkerSpec,
)
from fi.alk.harness.backends.claude import ClaudeBackend, ClaudeSession, _CallCount, _definition


def _tool(name: str) -> ToolSpec:
    return ToolSpec(name=name, description=name, input_schema={}, handler=lambda _a: {})


def test_every_workers_tools_survive_the_merge_onto_one_server_name() -> None:
    server = "scenarios"
    spec = SessionSpec(
        system_prompt="parent",
        servers={server: ToolServer(name=server, tools=[_tool("aim_for")])},
        workers={
            "scenario_writer": WorkerSpec(
                description="writes",
                instructions="write",
                servers={server: ToolServer(name=server, tools=[_tool("submit_scenario")])},
            ),
            "suite_reviewer": WorkerSpec(
                description="reviews",
                instructions="review",
                servers={server: ToolServer(name=server, tools=[_tool("inspect_scenario")])},
            ),
        },
    )

    options = ClaudeBackend()._options(spec)
    allowed = set(options.allowed_tools or [])
    assert {
        f"mcp__{server}__aim_for",
        f"mcp__{server}__submit_scenario",
        f"mcp__{server}__inspect_scenario",
    } <= allowed, sorted(allowed)


def test_a_worker_does_not_inherit_the_stages_delegation_tools() -> None:
    server = "scenarios"
    spec = SessionSpec(
        system_prompt="parent",
        builtins=("AskUserQuestion", "Delegate"),
        servers={server: ToolServer(name=server, tools=[_tool("aim_for")])},
        workers={
            "scenario_writer": WorkerSpec(
                description="writes",
                instructions="write",
                servers={server: ToolServer(name=server, tools=[_tool("submit_scenario")])},
            )
        },
    )
    tools = _definition(spec.workers["scenario_writer"], spec).tools
    assert "Agent" not in tools and "Task" not in tools, tools
    assert f"mcp__{server}__submit_scenario" in tools, tools


def test_a_stage_that_delegates_waits_for_its_writers() -> None:
    from pathlib import Path

    source = Path("src/fi/alk/harness/backends/claude.py").read_text(encoding="utf-8")
    assert 'environment["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"' in source
    assert "background=False" in source


def test_a_successful_terminal_save_interrupts_and_ignores_later_tool_calls() -> None:
    terminal = "mcp__runtime_repair__submit_runtime_plan_patch"
    received = [
        AssistantMessage(
            content=[
                ToolUseBlock(id="first", name=terminal, input={}),
                ToolUseBlock(id="ordinary", name="mcp__world__inspect", input={}),
            ],
            model="gemini-3.7-flash",
        ),
        SimpleNamespace(
            content=[
                ToolResultBlock(tool_use_id="first", content="accepted"),
                ToolResultBlock(tool_use_id="ordinary", content="observed"),
            ]
        ),
        AssistantMessage(
            content=[ToolUseBlock(id="second", name=terminal, input={})],
            model="gemini-3.7-flash",
        ),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="session",
        ),
    ]

    class Client:
        interrupted = 0

        async def receive_response(self):
            for item in received:
                yield item

        async def interrupt(self):
            self.interrupted += 1

    session = ClaudeSession(None)
    client = Client()
    session._client = client

    async def collect():
        return [event async for event in session.replies()]

    events = asyncio.run(collect())
    assert client.interrupted == 1
    calls = [
        part
        for event in events
        if isinstance(event, ModelReply)
        for part in event.parts
        if isinstance(part, Call)
    ]
    assert [call.name for call in calls] == [terminal, "mcp__world__inspect"]
    assert isinstance(events[-1], StageDone)


def test_an_interactive_builder_can_reply_after_saving() -> None:
    terminal = "mcp__scenarios__save_scenarios"
    received = [
        AssistantMessage(
            content=[ToolUseBlock(id="save", name=terminal, input={})],
            model="gemini-3.7-flash",
        ),
        SimpleNamespace(content=[ToolResultBlock(tool_use_id="save", content="saved")]),
        AssistantMessage(content=[TextBlock(text="Saved all scenarios.")], model="gemini-3.7-flash"),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=2,
            session_id="session",
        ),
    ]

    class Client:
        interrupted = 0

        async def receive_response(self):
            for item in received:
                yield item

        async def interrupt(self):
            self.interrupted += 1

    session = ClaudeBackend().create(
        SessionSpec(
            system_prompt="Builder",
            conversation=ConversationSession(
                app_name="builder",
                user_id="user",
                session_id="conversation",
                streaming=False,
            ),
        )
    )
    client = Client()
    session._client = client

    async def collect():
        return [event async for event in session.replies()]

    events = asyncio.run(collect())
    assert client.interrupted == 0
    assert any(
        part.text == "Saved all scenarios."
        for event in events
        if isinstance(event, ModelReply)
        for part in event.parts
        if hasattr(part, "text")
    )
    assert isinstance(events[-1], StageDone)


def test_the_cli_offers_only_the_built_ins_granted_and_the_delegation_tools() -> None:
    spec = SessionSpec(
        system_prompt="parent",
        builtins=("Read", "AskUserQuestion"),
        workers={
            "operator": WorkerSpec(description="runs", instructions="run", builtins=("Bash",)),
        },
    )

    options = ClaudeBackend()._options(spec)

    # Left unset the CLI offers every built-in it has; a worker's own built-ins are offered too,
    # and the gate keeps each one to the worker it was granted to.
    assert set(options.tools) == {"Read", "AskUserQuestion", "Bash", "Agent", "Task"}
    assert "Bash" not in (options.disallowed_tools or [])
    # No allow rule for the question tool, so every CLI sends it to the permission callback,
    # which hands it to the stage's ask.
    assert "AskUserQuestion" not in options.allowed_tools


def _hook_decision(options, name: str, **caller: str) -> str | None:
    """What the session's PreToolUse gate decides for one call; None leaves it to the rest."""
    refuse = options.hooks["PreToolUse"][0].hooks[0]
    payload = {"tool_name": name, "tool_input": {"command": "true"}, **caller}
    out = asyncio.run(refuse(payload, None, None))
    return (out.get("hookSpecificOutput") or {}).get("permissionDecision")


def test_a_worker_s_built_in_is_that_worker_s_alone() -> None:
    spec = SessionSpec(
        system_prompt="parent",
        builtins=("Read",),
        workers={"op": WorkerSpec(description="d", instructions="i", builtins=("Bash",))},
    )
    options = ClaudeBackend()._options(spec)
    worker = {"agent_id": "a1", "agent_type": "op"}

    # The stage itself: its own tools and its delegation, not its worker's shell.
    assert _hook_decision(options, "Bash") == "deny"
    assert _hook_decision(options, "Read") is None
    assert _hook_decision(options, "Agent") is None
    # The worker: its own tools only.
    assert _hook_decision(options, "Bash", **worker) == "allow"
    assert _hook_decision(options, "Read", **worker) == "deny"
    # A sub-agent this session didn't define gets nothing.
    assert _hook_decision(options, "Bash", agent_id="a2", agent_type="general-purpose") == "deny"


def test_a_question_from_the_stage_needs_the_stage_s_own_grant() -> None:
    from claude_agent_sdk.types import (
        PermissionResultAllow,
        PermissionResultDeny,
        ToolPermissionContext,
    )

    spec = SessionSpec(
        system_prompt="parent",
        builtins=("Read",),
        workers={
            "op": WorkerSpec(description="d", instructions="i", builtins=("AskUserQuestion",))
        },
    )
    gate = ClaudeBackend()._options(spec).can_use_tool

    def decide(agent_id: str | None):
        context = ToolPermissionContext(agent_id=agent_id)
        return asyncio.run(gate("AskUserQuestion", {"questions": []}, context))

    assert isinstance(decide(None), PermissionResultDeny)
    assert isinstance(decide("a1"), PermissionResultAllow)


def test_a_built_in_the_backend_cannot_supply_is_refused() -> None:
    import pytest

    spec = SessionSpec(system_prompt="parent", builtins=("Read", "WebSearch"))

    with pytest.raises(ValueError, match="WebSearch"):
        ClaudeBackend()._options(spec)


def test_a_session_given_no_tools_is_offered_none_of_the_cli_s_own():
    bare = ClaudeBackend()._options(SessionSpec(system_prompt="Decide.", max_turns=1, gated=False))
    assert bare.tools == []
    reading = ClaudeBackend()._options(SessionSpec(system_prompt="Read.", builtins=("Read",)))
    assert reading.tools != []


# -- the call budget -----------------------------------------------------------------------------


def _said(message_id, *, by=None, text="reading"):
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model="gemini-3.7-flash",
        parent_tool_use_id=by,
        message_id=message_id,
    )


def _finished():
    return ResultMessage(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=2,
        session_id="session",
    )


def _replies(session, received):
    class Client:
        async def receive_response(self):
            for item in received:
                yield item

    session._client = Client()

    async def collect():
        return [event async for event in session.replies()]

    return asyncio.run(collect())


def test_a_call_is_one_message_id_however_many_messages_carry_it() -> None:
    calls = _CallCount(0)

    # The CLI sends one message per content block, all under the call's id.
    assert calls.see(_said("msg_1")) is True
    assert calls.see(_said("msg_1", text="and a tool")) is False
    assert calls.see(_said("msg_2", by="toolu_reader")) is True
    # A message with no id counts on its own: more, never fewer.
    assert calls.see(_said(None)) is True
    assert calls.see(_said(None)) is True

    assert calls.total == 4
    assert calls.spent is False


def test_the_first_call_past_the_budget_is_said_and_every_call_after_it() -> None:
    session = ClaudeSession(None, calls=_CallCount(2))

    events = _replies(
        session,
        [
            _said("msg_1"),
            _said("msg_1", text="split"),
            # The budget-th call is inside the budget: nothing is said for it.
            _said("msg_2", by="toolu_reader"),
            _said("msg_2", by="toolu_reader", text="split"),
            _said("msg_3"),
            # Already on its way when the first call past the budget was seen.
            _said("msg_4", by="toolu_reader"),
            _finished(),
        ],
    )

    past = [event.calls for event in events if isinstance(event, CallBudgetSpent)]
    assert past == [3, 4]
    done = events[-1]
    assert isinstance(done, StageDone)
    assert (done.outcome, done.is_error, done.calls) == (CALL_BUDGET_SPENT, True, 4)


def test_a_turn_that_ends_on_the_budget_th_call_is_inside_the_budget() -> None:
    session = ClaudeSession(None, calls=_CallCount(2))

    events = _replies(session, [_said("msg_1"), _said("msg_2"), _finished()])

    assert not any(isinstance(event, CallBudgetSpent) for event in events)
    assert (events[-1].outcome, events[-1].is_error, events[-1].calls) == ("success", False, 2)


def test_without_a_budget_calls_are_counted_and_nothing_is_said() -> None:
    session = ClaudeSession(None)

    events = _replies(
        session, [_said("msg_1"), _said("msg_2", by="toolu_reader"), _finished()]
    )

    assert not any(isinstance(event, CallBudgetSpent) for event in events)
    assert (events[-1].outcome, events[-1].calls) == ("success", 2)


def test_each_turn_reports_its_own_calls_against_a_budget_held_across_turns() -> None:
    session = ClaudeSession(None, calls=_CallCount(2))

    first = _replies(session, [_said("msg_1"), _said("msg_2"), _finished()])
    second = _replies(session, [_said("msg_3"), _finished()])

    assert (first[-1].outcome, first[-1].calls) == ("success", 2)
    assert (second[-1].outcome, second[-1].calls) == (CALL_BUDGET_SPENT, 1)
    assert [e.calls for e in second if isinstance(e, CallBudgetSpent)] == [3]


def _sent_out(message_id, tool_use_id, name="Agent"):
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name=name, input={"subagent_type": "reader"})],
        model="gemini-3.7-flash",
        parent_tool_use_id=None,
        message_id=message_id,
    )


def _back(*tool_use_ids, details=None, is_error=False):
    return UserMessage(
        content=[
            ToolResultBlock(tool_use_id=tool_use_id, content="x", is_error=is_error)
            for tool_use_id in tool_use_ids
        ],
        tool_use_result=details,
    )


_ANSWERED = {"status": "completed", "content": [{"type": "text", "text": "Traced."}]}
_OUT_OF_TURNS = {"status": "completed", "content": []}


def test_a_readers_closing_reply_is_counted_from_its_result() -> None:
    # The CLI never streams the reply a reader ends with; it hands it back as the result.
    session = ClaudeSession(None, calls=_CallCount(0))

    events = _replies(
        session,
        [_sent_out("msg_1", "toolu_1"), _said("msg_2", by="toolu_1"), _back("toolu_1", details=_ANSWERED),
         _finished()],
    )

    assert events[-1].calls == 3


def test_what_a_readers_result_says_about_its_closing_reply(caplog) -> None:
    caplog.set_level(logging.WARNING, logger="fi.alk.harness.backends.claude")

    def counted(*received):
        session = ClaudeSession(None, calls=_CallCount(0))
        events = _replies(session, [*received, _finished()])
        return events[-1].calls

    sent = _sent_out("msg_1", "toolu_1")
    assert counted(sent, _back("toolu_1", details=_ANSWERED)) == 2
    assert counted(sent, _back("toolu_1", details={"status": "completed", "content": "Traced."})) == 2
    # Out of turns: its last call was a tool request, in the stream.
    assert counted(sent, _back("toolu_1", details=_OUT_OF_TURNS)) == 1
    blank = {"status": "completed", "content": [{"type": "text", "text": " "}]}
    assert counted(sent, _back("toolu_1", details=blank)) == 1
    # Refused by the gate, or failed: no reader call.
    assert counted(sent, _back("toolu_1", details="Error: refused", is_error=True)) == 1
    assert caplog.text == ""
    # Not a delegate tool: nothing.
    assert counted(_sent_out("msg_1", "toolu_1", name="Glob"), _back("toolu_1", details=_ANSWERED)) == 1
    # Shapes the kit does not know: counted, and said.
    assert counted(sent, _back("toolu_1", details=None)) == 2
    assert counted(sent, _back("toolu_1", details={"status": "running"})) == 2
    assert counted(sent, _back("toolu_1", details={"status": "completed", "content": 7})) == 2
    assert caplog.text.count("a shape the kit does not know") == 3


def test_two_results_in_one_message_are_each_counted_when_the_details_could_be_either(caplog) -> None:
    session = ClaudeSession(None, calls=_CallCount(0))

    events = _replies(
        session,
        [
            _sent_out("msg_1", "toolu_1"),
            _sent_out("msg_1", "toolu_2"),
            _back("toolu_1", "toolu_2", details=_OUT_OF_TURNS),
            _finished(),
        ],
    )

    assert events[-1].calls == 3
    assert "a shape the kit does not know" in caplog.text


def test_a_closing_reply_past_the_budget_is_said_like_any_call_past_it() -> None:
    session = ClaudeSession(None, calls=_CallCount(2))

    events = _replies(
        session,
        [_sent_out("msg_1", "toolu_1"), _said("msg_2", by="toolu_1"), _back("toolu_1", details=_ANSWERED),
         _finished()],
    )

    assert [event.calls for event in events if isinstance(event, CallBudgetSpent)] == [3]
    assert (events[-1].outcome, events[-1].calls) == (CALL_BUDGET_SPENT, 3)


def _reader_spec(**fields) -> SessionSpec:
    return SessionSpec(
        system_prompt="parent",
        builtins=("Read",),
        workers={"reader": WorkerSpec(description="d", instructions="i", builtins=("Read",))},
        **fields,
    )


def _asked(message_id, tool_use_id, *, by=None):
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name="mcp__contract__submit_contract", input={})],
        model="gemini-3.7-flash",
        parent_tool_use_id=by,
        message_id=message_id,
    )


def test_tools_are_refused_only_for_calls_past_the_budget() -> None:
    session = ClaudeBackend().create(_reader_spec(call_budget=2))
    guard = session._options.hooks["PreToolUse"][-1].hooks[0]

    def decision(tool_use_id: str | None, name: str = "mcp__contract__submit_contract"):
        payload = {"tool_name": name, "tool_input": {}, "tool_use_id": tool_use_id}
        out = asyncio.run(guard(payload, tool_use_id, None))
        return (out.get("hookSpecificOutput") or {}).get("permissionDecision")

    # Before the budget is reached, a tool runs even before its call is counted.
    assert decision("toolu_early") is None
    session._calls.see(_asked("msg_1", "toolu_main"))
    # The budget-th call says something first, then asks for the save in a second message
    # under the same id, as the CLI splits a reply.
    session._calls.see(_said("msg_2", by="toolu_reader"))
    assert session._calls.spent
    session._calls.see(_asked("msg_2", "toolu_save", by="toolu_reader"))

    # The call that reached the budget keeps its save, and so does a call before it whose
    # tool was still waiting when the budget was reached.
    assert decision("toolu_save") is None
    assert decision("toolu_main") is None
    # A call past the budget, counted or not yet, has its tools refused.
    session._calls.see(_asked("msg_3", "toolu_late"))
    assert decision("toolu_late") == "deny"
    assert decision("toolu_not_counted_yet", "Read") == "deny"
    assert decision(None, "Read") == "deny"
    # The id is also read from the hook's input.
    assert asyncio.run(guard({"tool_name": "Read", "tool_use_id": "toolu_save"}, None, None)) == {}
    # The session's own gate is still first.
    assert len(session._options.hooks["PreToolUse"]) == 2


def test_no_budget_adds_no_guard() -> None:
    session = ClaudeBackend().create(_reader_spec())

    assert len(session._options.hooks["PreToolUse"]) == 1


def test_workers_at_once_is_the_sessions_own_when_set() -> None:
    own = ClaudeBackend()._options(_reader_spec(workers_at_once=4))
    shared = ClaudeBackend()._options(_reader_spec())

    assert own.env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == "4"
    assert shared.env["CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS"] == str(MOST_WORKERS_AT_ONCE)
