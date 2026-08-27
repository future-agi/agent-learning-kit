"""Stage two: build the world the agent's tools run against.

Reads the contract stage one produced and builds a database behind the agent's action space,
then freezes it. The frozen snapshot is the base state every scenario restores from; a scenario
adds only the rows it additionally needs.

The stage stays open, because a world is usually right on the second look. Correcting a handler
is the next thing said, and the tool is re-run on the spot.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from claude_agent_sdk import ClaudeAgentOptions

from .config import (
    UNWANTED,
    artifact_dir,
    chosen_model,
    gate_hooks,
    load_skill,
    permission_gate,
    provider_env,
    provisioning,
)
from .contract import AgentContract
from .session import Stage
from .tools import qualified
from .world.snapshot import saved as world_saved
from .world.tools import TOOL_NAMES, WORLD_SERVER, world_tools

SKILL = "build-environment"


def blockers(contract: AgentContract, source_root: str = "") -> list[str]:
    """Reasons the real agent implementation cannot be put in a test environment.

    These are terminal for environment creation.  Synthesising a handler would make the suite
    test the harness's interpretation of the agent, so the only honest response is to name the
    missing seam and let the agent owner expose it.
    """
    problems: list[str] = []
    if contract.tools and not source_root:
        problems.append(
            "the agent source path was not preserved; reopen the session with the repository "
            "path (or pass --path) so its shipped implementation can be run"
        )
    # A voice worker is itself the runnable seam. Frameworks commonly create function tools as
    # closures that capture session state; forcing those closures to also be importable outside
    # the worker rejects valid agents or encourages the harness to rewrite their behavior. The
    # provisioning stage still has to prove that the submitted worker can actually be packaged.
    runtime_owned = bool(
        contract.modality == "voice"
        and contract.runtime
        and (
            contract.runtime.command
            or contract.runtime.dockerfile
            or contract.runtime.install
        )
    )
    entries = {entry.tool: entry for entry in contract.tool_entrypoints}
    for tool in contract.tools:
        entry = entries.get(tool.name)
        if entry is None or entry.mode in ("", "generate", "unreachable"):
            if runtime_owned:
                continue
            problems.append(
                f"{tool.name}: no runnable shipped entrypoint was identified; expose the real "
                "implementation as an importable callable or an HTTP service, then point the "
                "harness at that seam"
            )
            continue
        if runtime_owned:
            continue
        if entry.mode in ("import", "construct") and not (
            entry.module and entry.callable
        ):
            problems.append(
                f"{tool.name}: {entry.mode} entrypoint needs both module and callable"
            )
        if entry.mode == "service" and not contract.dependencies:
            problems.append(
                f"{tool.name}: service entrypoint has no service dependency describing what "
                "must be started and which configuration points the agent to it"
            )
    return problems


def require_buildable(contract: AgentContract, source_root: str = "") -> None:
    problems = blockers(contract, source_root)
    if problems:
        raise RuntimeError(
            "Cannot create a truthful test environment without reimplementing agent behavior:\n"
            "  - " + "\n  - ".join(problems)
        )


def turns_for(contract: AgentContract) -> int:
    """A turn budget that grows with the agent being built for.

    A fixed ceiling silently truncates the work. The budget follows the number of real tool
    bindings that must be exercised rather than a number that happened to fit the first agent.
    """
    return max(80, len(contract.tools or []) * 8 + 40)


DEFAULT_HOSTED_ENVIRONMENT_MAX_TURNS = 80


def environment_turns_for(
    contract: AgentContract, *, requested: int = 0, deferred_runtime: bool = False
) -> int:
    """Bound unattended hosted correction without changing local authoring.

    Local interactive/Compose authoring retains the size-aware budget. Hosted authoring has no
    operator present and must not spend hundreds of turns trying variations when the runtime or
    adapter cannot satisfy a validation gate. An explicit lower requested budget still wins;
    an explicit larger one remains capped in the Dockerless hosted lane.
    """
    budget = requested or turns_for(contract)
    if not deferred_runtime:
        return budget
    raw_limit = os.getenv(
        "ALK_HOSTED_ENVIRONMENT_MAX_TURNS",
        str(DEFAULT_HOSTED_ENVIRONMENT_MAX_TURNS),
    )
    try:
        limit = int(raw_limit)
    except ValueError:
        limit = DEFAULT_HOSTED_ENVIRONMENT_MAX_TURNS
    if limit < 1:
        limit = DEFAULT_HOSTED_ENVIRONMENT_MAX_TURNS
    return min(budget, limit)


def open_stage(
    contract: AgentContract,
    *,
    out: Path | None = None,
    ask: Callable[..., Any] | None = None,
    source_root: str = "",
    max_turns: int = 0,
    deferred_runtime: bool = False,
) -> tuple[Stage, Path]:
    """A live build-the-world stage, and where it will write."""
    destination = out or artifact_dir(contract.agent)
    from .provision import ProvisionedEnvironment

    provisioned = ProvisionedEnvironment.load(destination)
    environment_note = ""
    if provisioned is not None and provisioned.running:
        service_tools = [
            entry.tool for entry in contract.tool_entrypoints if entry.mode == "service"
        ]
        runtime_tools = (
            contract.tool_names()
            if provisioned.runtime_services and contract.modality == "voice"
            else [
                entry.tool
                for entry in contract.tool_entrypoints
                if entry.mode in ("import", "construct")
            ]
        )
        environment_note = (
            "\n\n## Already provisioned from the agent's repository\n\n"
            f"Compose project: {provisioned.project}\n"
            f"Services: {', '.join(provisioned.services)}\n"
            "Point the agent at these endpoints by changing only these settings:\n"
            + (
                "\n".join(
                    f"- {name}={value}"
                    for name, value in sorted(provisioned.overrides.items())
                )
                or (
                    "- This is a harness-managed dependency environment; the submitted runtime "
                    "already receives its internal datastore connection. Bind importable tools "
                    "to the attached real store."
                    if provisioned.managed
                    else "- No URL override could be inferred. Stop and report the missing config seam."
                )
            )
            + "\n\nThe source-backed world has already bound these service tools through the "
            "submitted HTTP service: "
            + (", ".join(service_tools) or "none")
            + "\nThese tools execute inside the submitted worker and are intentionally not "
            "environment endpoints: "
            + (", ".join(runtime_tools) or "none")
            + "\nDo not adopt either group, inspect their source again, or recreate any service "
            "or behavior. Do not use run_env_command for source discovery. The contract already "
            "contains that evidence. Inspect the live data once. Preserve useful repository seed "
            "rows. If the submitted schema is empty or lacks the records needed to exercise the "
            "contract's branches, add a small varied realistic baseline through seed only; never "
            "invent or replace schema, migrations, services, or tool behavior. Avoid placeholder "
            "names/addresses and predictable secrets such as 123456. Scenario-specific people, "
            "credentials and edge states belong in scenario setup rather than the shared base. "
            "Your remaining work is the simulator prompt, observable sub-goals/world checks, "
            + (
                "check_world, and save_world. Do not declare a build-time sequence for "
                "runtime-internal tools: their stateful ordering is proven by real calls."
                if provisioned.runtime_services and contract.modality == "voice"
                else "one truthful service-backed sequence, check_world, and save_world."
            )
        )
    elif deferred_runtime:
        environment_note = (
            "\n\n## Runtime deferred to hosted execution\n\n"
            "The submitted repository processes and datastore will be built, started and "
            "validated inside the Daytona execution sandbox from the sealed process bundle. "
            "Do not start containers here. Build the deterministic baseline, simulator prompt, "
            "sub-goals and world checks. Voice runtime tools are owned by the submitted worker: "
            "do not replace, bind or smoke-call them outside a real voice session. Their real "
            "behavior is validated when the generated scenarios run in Daytona."
        )
    server, _world = world_tools(
        contract,
        destination,
        source_root=source_root,
        deferred_runtime=deferred_runtime,
    )
    allowed = [
        "AskUserQuestion",
        *(qualified(WORLD_SERVER, name) for name in TOOL_NAMES),
    ]
    options = ClaudeAgentOptions(
        system_prompt=(
            f"{load_skill(SKILL)}\n\n## This agent\n\n{contract.brief(with_data=True)}"
            + environment_note
        ),
        # No file tools and no shell. Everything this stage can do goes through a tool that
        # executes it and reports back, which is what makes the guardrails meaningful.
        allowed_tools=allowed,
        mcp_servers={WORLD_SERVER: server},
        # Not acceptEdits: that auto-approves Edit and Write before the permission callback is
        # consulted, so a stage can rewrite an artifact by hand and skip the tool whose
        # whole job is to validate that change.
        permission_mode="default",
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        setting_sources=[],
        max_turns=environment_turns_for(
            contract,
            requested=max_turns,
            deferred_runtime=deferred_runtime,
        ),
        model=chosen_model(),
        env=provider_env(),
    )
    options.disallowed_tools = list(UNWANTED)
    options.hooks = gate_hooks(allowed)
    options.can_use_tool = permission_gate(ask, allowed)
    return Stage(options, name=SKILL), destination


def opening(contract: AgentContract, *, provisioned: bool = False) -> str:
    if provisioning():
        return (
            f"Provision the environment for {contract.agent!r}.\n\n"
            "Stand up the engine it already uses, run its OWN migrations into it, and seed "
            "from the contract's real data. Do not write a schema and do not touch the "
            "agent — you are building what it connects to, not a copy of it."
        )
    if provisioned:
        sequence_instruction = (
            "Do not declare or smoke-call runtime-internal tools outside their voice session; "
            "the real scenarios prove their stateful ordering. "
            if contract.modality == "voice" and contract.runtime
            else "Declare one sequence using already-bound service endpoints with real required "
            "arguments. "
        )
        return (
            f"Finish the already-provisioned source-backed world for {contract.agent!r}.\n\n"
            "The submitted Compose services, seed data, HTTP service-tool bindings, and "
            "worker-internal tools are already authoritative and must not be rebuilt or adopted. "
            "Inspect existing state once; keep useful submitted seed rows, and only seed missing "
            "baseline records into existing collections when the world would otherwise be too "
            "empty to exercise the contract. Use varied realistic values, never demo placeholders "
            "or predictable credentials. Write the simulator prompt, add "
            "observable sub-goals and world checks. "
            + sequence_instruction
            + "Then check_world and save_world. "
            "Do not read source, run shell commands, or investigate runtime-internal tools."
        )
    return (
        f"Build the world for {contract.agent!r}.\n\n"
        "Use only the runtime, services, migrations, data loaders and tool implementations the "
        "agent ships. Bind one handler per tool to each real implementation and verify its "
        "refusals with run_tool. If any real "
        "implementation cannot be reached, state the exact missing seam and stop; never write a "
        "replacement. Declare at least one stateful sequence, then check_world and save_world."
    )


async def build(
    contract: AgentContract,
    *,
    out: Path | None = None,
    source_root: str = "",
    follow_ups: list[str] | None = None,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 0,
) -> Path | None:
    """Run the stage start to finish. Returns where the world was written, or None."""
    require_buildable(contract, source_root)
    destination = out or artifact_dir(contract.agent)
    from .provision import provision_if_present

    provisioned = await asyncio.to_thread(
        provision_if_present, source_root, destination, contract
    )
    stage, destination = open_stage(
        contract,
        out=destination,
        ask=ask,
        source_root=source_root,
        max_turns=max_turns,
    )
    async with stage:
        await stage.say(
            opening(contract, provisioned=provisioned is not None), on_event=on_event
        )
        for follow_up in follow_ups or []:
            await stage.say(follow_up, on_event=on_event)
    return destination if world_saved(destination) else None
