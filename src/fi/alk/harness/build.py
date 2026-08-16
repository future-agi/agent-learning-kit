"""Stage two: build the world the agent's tools run against.

Reads the contract stage one produced and builds a database behind the agent's action space,
then freezes it. The frozen snapshot is the base state every scenario restores from; a scenario
adds only the rows it additionally needs.

The stage stays open, because a world is usually right on the second look. Correcting a handler
is the next thing said, and the tool is re-run on the spot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from claude_agent_sdk import ClaudeAgentOptions

from .config import artifact_dir, load_skill, provider_env
from .contract import AgentContract
from .session import Stage
from .tools import qualified
from .world.tools import TOOL_NAMES, WORLD_SERVER, world_tools

SKILL = "build-environment"


def open_stage(
    contract: AgentContract,
    *,
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 60,
) -> tuple[Stage, Path]:
    """A live build-the-world stage, and where it will write."""
    destination = out or artifact_dir(contract.agent)
    server, _world = world_tools(contract, destination)
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(SKILL)}\n\n## This agent\n\n{contract.brief(with_data=True)}"
        ),
        # No file tools and no shell. Everything this stage can do goes through a tool that
        # executes it and reports back, which is what makes the guardrails meaningful.
        allowed_tools=[
            "AskUserQuestion",
            *(qualified(WORLD_SERVER, name) for name in TOOL_NAMES),
        ],
        mcp_servers={WORLD_SERVER: server},
        permission_mode="acceptEdits",
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        setting_sources=[],
        max_turns=max_turns,
        env=provider_env(),
    )
    if ask is not None:
        options.can_use_tool = ask
    return Stage(options, name=SKILL), destination


def opening(contract: AgentContract) -> str:
    return (
        f"Build the world for {contract.agent!r}.\n\n"
        "Design the schema, seed it from the contract's real data, and write one handler per "
        "tool. Verify the refusals yourself with run_tool: a call naming something that does "
        "not exist must be refused, not succeed. Declare at least one sequence where state has "
        "to carry across calls, then check_world and save_world."
    )


async def build(
    contract: AgentContract,
    *,
    out: Path | None = None,
    follow_ups: list[str] | None = None,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 60,
) -> Path | None:
    """Run the stage start to finish. Returns where the world was written, or None."""
    stage, destination = open_stage(contract, out=out, ask=ask, max_turns=max_turns)
    async with stage:
        await stage.say(opening(contract), on_event=on_event)
        for follow_up in follow_ups or []:
            await stage.say(follow_up, on_event=on_event)
    return destination if (destination / "world.sqlite").exists() else None
