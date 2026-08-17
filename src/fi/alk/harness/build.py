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
    provisioning,
)
from .contract import AgentContract
from .session import Stage
from .tools import qualified
from .world.provision import MANIFEST
from .world.provision import PROVISION_SERVER, provision_tools
from .world.provision import TOOL_NAMES as PROVISION_TOOL_NAMES
from .world.tools import TOOL_NAMES, WORLD_SERVER, world_tools

SKILL = "build-environment"
PROVISION_SKILL = "provision-environment"


def open_stage(
    contract: AgentContract,
    *,
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 60,
    source: str | Path | None = None,
) -> tuple[Stage, Path]:
    """A live build-the-world stage, and where it will write.

    ``source`` is where the agent's code lives, and the provisioning path needs it. That stage
    is defined by using the agent's **own** migrations and its own data loader, and it cannot
    do that from the contract alone — the first real run went looking for them with Glob, was
    refused, and invented a seed instead. Reading the agent is not editing the agent: the read
    tools are granted, the write ones never are.
    """
    destination = out or artifact_dir(contract.agent)
    # Two build stages that prove different things, so exactly one is live. The old one saves a
    # world of handlers it wrote; the new one saves an environment the agent's own unmodified
    # code connects to. See config.provisioning.
    if provisioning():
        server, _held = provision_tools(contract, destination)
        skill, name, names = PROVISION_SKILL, PROVISION_SERVER, PROVISION_TOOL_NAMES
    else:
        server, _held = world_tools(contract, destination)
        skill, name, names = SKILL, WORLD_SERVER, TOOL_NAMES
    # Read-only access to the agent's repository, for the path that has to find its migrations
    # and its data loader. Read, Glob and Grep and nothing else: no Write, no Edit, no Bash, so
    # the rule that the agent is never modified is still enforced by there being no verb for it.
    reading = ["Read", "Glob", "Grep"] if (provisioning() and source) else []
    allowed = [
        "AskUserQuestion",
        *reading,
        *(qualified(name, one) for one in names),
    ]
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(skill)}\n\n## This agent\n\n{contract.brief(with_data=True)}"
        ),
        # No file tools and no shell. Everything this stage can do goes through a tool that
        # executes it and reports back, which is what makes the guardrails meaningful.
        allowed_tools=allowed,
        mcp_servers={name: server},
        # Not acceptEdits: that auto-approves Edit and Write before the permission callback is
        # consulted, so a stage can rewrite an artifact by hand and skip the tool whose
        # whole job is to validate that change.
        permission_mode="default",
        # Rooted at the agent when there is one to read, so a relative Glob lands in the
        # repository the stage was told to go looking through rather than in our artifacts.
        cwd=str(
            Path(source)
            if reading and Path(source).exists()
            else (destination.parent if destination.parent.exists() else Path.cwd())
        ),
        setting_sources=[],
        max_turns=max_turns,
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    return Stage(options, name=skill), destination


def opening(contract: AgentContract) -> str:
    if provisioning():
        return (
            f"Provision the environment for {contract.agent!r}.\n\n"
            "Stand up the engine it already uses, run its OWN migrations into it, and seed "
            "from the contract's real data. Do not write a schema and do not touch the "
            "agent — you are building what it connects to, not a copy of it. Then add the "
            "sub-goals with their checks as code, prove_environment, and save_environment."
        )
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
    source: str | Path | None = None,
) -> Path | None:
    """Run the stage start to finish. Returns where the world was written, or None."""
    stage, destination = open_stage(
        contract, out=out, ask=ask, max_turns=max_turns, source=source
    )
    async with stage:
        await stage.say(opening(contract), on_event=on_event)
        for follow_up in follow_ups or []:
            await stage.say(follow_up, on_event=on_event)
    written = MANIFEST if provisioning() else "world.sqlite"
    return destination if (destination / written).exists() else None
