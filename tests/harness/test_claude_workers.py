"""Native sub-agents: what tools they are actually given."""

from fi.alk.harness.backends.base import SessionSpec, ToolServer, ToolSpec, WorkerSpec
from fi.alk.harness.backends.claude import ClaudeBackend, _definition


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
