"""Native sub-agents: what tools they are actually given.

A real hosted run wrote zero scenarios because every sub-agent was handed read-only tools. The
parent, the writer and the reviewer all publish under one server name with different subsets, and
merging them with `update` meant the last one merged won. What each agent may call is restricted by
its own allowlist, so the registered server has to be the union or the allowlist has nothing to
point at.
"""

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
            # Merged last, and read-only. Before the fix this overwrote the writer's server and
            # the writers could not submit anything.
            "suite_reviewer": WorkerSpec(
                description="reviews",
                instructions="review",
                servers={server: ToolServer(name=server, tools=[_tool("inspect_scenario")])},
            ),
        },
    )

    options = ClaudeBackend()._options(spec)
    # The SDK wraps the server in an instance, so assert on what the backend was asked to allow:
    # every worker's tools reach the allowlist, qualified the way the model calls them.
    allowed = set(options.allowed_tools or [])
    assert {
        f"mcp__{server}__aim_for",
        f"mcp__{server}__submit_scenario",
        f"mcp__{server}__inspect_scenario",
    } <= allowed, sorted(allowed)


def test_a_worker_does_not_inherit_the_stages_delegation_tools() -> None:
    """A worker that could hand out again would spend the stage's budget on a tree of its own.

    Workers declare no builtins, so falling back to the parent's handed every writer `Agent` and
    `Task` by accident.
    """
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
