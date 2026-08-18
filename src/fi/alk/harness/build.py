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

from .config import (
    artifact_dir,
    UNWANTED,
    gate_hooks,
    chosen_model,
    load_skill,
    permission_gate,
    provider_env,
)
from .contract import AgentContract
from .world.snapshot import saved as world_saved
from .session import Stage
from .tools import qualified
from .world.tools import TOOL_NAMES, WORLD_SERVER, world_tools

def _artifacts_root(destination: Path) -> Path:
    return destination.parent if destination.parent.exists() else Path.cwd()


def _reading_root(source_root: str, contract: AgentContract) -> Path:
    """Where to read the agent from: its repository, not the package inside it.

    The same answer the container build uses, so a path that works in one works in the other
    and both agree with contract.runtime.workdir, which is written repository-relative.
    """
    from .world.sandbox import context_for

    try:
        found = context_for(source_root, getattr(contract, "runtime", None))
    except Exception:  # noqa: BLE001 - a root we cannot work out is just the one we were given
        return Path(source_root)
    return found if found.is_dir() else Path(source_root)


SKILL = "build-environment"


def open_stage(
    contract: AgentContract,
    *,
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    source_root: str = "",
    max_turns: int = 60,
) -> tuple[Stage, Path]:
    """A live build-the-world stage, and where it will write."""
    destination = out or artifact_dir(contract.agent)
    server, _world = world_tools(contract, destination, source_root=source_root)
    # Read-only access to the agent, where there is an agent to read. adopt_tool and
    # adopt_store are asked for a path into somebody else's repository, and this stage had no
    # way to look: pointed at an agent whose tools it was told to reuse, it tried Read, was
    # refused, said it would work from the contract alone, and wrote its own versions of two
    # tools -- inventing menu validation the real agent does not have. Read, Glob and Grep and
    # nothing else: no Write, no Edit, no shell, so the agent is still never modified.
    reading = ["Read", "Glob", "Grep"] if source_root else []
    allowed = [
        "AskUserQuestion",
        *reading,
        *(qualified(WORLD_SERVER, name) for name in TOOL_NAMES),
    ]
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(SKILL)}\n\n## This agent\n\n{contract.brief(with_data=True)}"
        ),
        # No writing and no shell. Everything this stage can change goes through a tool that
        # executes it and reports back, which is what makes the guardrails meaningful;
        # reading the agent is not changing it.
        allowed_tools=allowed,
        mcp_servers={WORLD_SERVER: server},
        # Not acceptEdits: that auto-approves Edit and Write before the permission callback is
        # consulted, so a stage can rewrite an artifact by hand and skip the tool whose
        # whole job is to validate that change.
        permission_mode="default",
        # Rooted at the agent when there is one to read, so a relative Glob lands in the
        # repository this stage was told to go looking through rather than in our artifacts.
        # The same root the agent's container is built from. Rooted at the package instead,
        # a repository-relative path resolves under itself -- one stage globbed
        # components/python/src/main.py from inside components/python, found nothing, and
        # ended by asking a person to add a file that was there all along.
        cwd=str(_reading_root(source_root, contract) if reading else _artifacts_root(destination)),
        setting_sources=[],
        max_turns=max_turns,
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
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
    return destination if world_saved(destination) else None
