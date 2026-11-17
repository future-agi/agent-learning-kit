"""Native sub-agents: what tools they are actually given."""

import asyncio
from types import SimpleNamespace

from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolResultBlock, ToolUseBlock

from fi.alk.harness.backends.base import (
    Call,
    ConversationSession,
    ModelReply,
    SessionSpec,
    StageDone,
    ToolServer,
    ToolSpec,
    WorkerSpec,
)
from fi.alk.harness.backends.claude import ClaudeBackend, ClaudeSession, _definition


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
