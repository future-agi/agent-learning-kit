"""Proving cases on the live runtime for tools the agent serves over HTTP.

Offline, every hosted tool is runtime-owned, so a case's reference solution and its negative
controls are recorded without running and the case reads "kept as written". While certification
has the agent's own stack up, a tool whose contract entry is `service` can be played for real:
the bundle's tool proxy forwards a JSON POST to the agent's tools service, records the call, and
the change lands in the real datastore. So, per case and with a reset between each: set up, play
the reference and run the checks; set up, play nothing and run the checks; set up, play each
control and run the checks. A tool the bundle's MCP Toolbox serves is played the same way, as a `tools/call` to that
server, which runs its SQL on the world's database. A tool the agent runs inside its own process has no endpoint and
stays assumed. The result per case is written beside the authoring tree and laid over the offline proof
by `prove_suite`, so leaks, sources and rule checks still come from the offline proof. Proving a certified pair
again starts its sealed bundle and plays the cases the same way, without certifying it again.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import authoring_runtime_validation
from .catalogue import Catalogue, load_catalogue
from .checks import Outcome, reference_of, run_check
from .contract import AgentContract
from .folder import apply_setup, check_ready
from .json_file import write_json_atomic
from .outbound import redact_outbound_text
from .proof_records import CheckRecord, ControlResult, LiveResult
from .proofs import proof_key
from .prove import (
    Proof,
    checks_for,
    control_broken,
    executes,
    play_reference_step,
    played_control,
)
from .scenario import Scenario, Step
from .scenario_tools import load_scenarios
from .world.handle import MCP_ROUTE, mcp_request
from .world.runtime import Call

if TYPE_CHECKING:
    from .hosted_entrypoint import ProcessWorldFactory
    from .process_runtime import (
        EnvironmentRuntime,
        ProcessRuntimeProvider,
        RuntimeEndpoint,
    )

logger = logging.getLogger(__name__)

LIVE_PROOFS = ".futureagi-live-proofs.json"
TOOL_PROXY = "tool_proxy"
MCP_TOOLBOX = "mcp_toolbox"
# Entry modes whose tool runs inside the agent's own process: a server serving a tool of the same name is not it.
IN_PROCESS_MODES = ("import", "construct")


def service_endpoints(
    contract: AgentContract,
    endpoints: Mapping[str, RuntimeEndpoint],
    toolbox_tools: set[str] | frozenset[str] = frozenset(),
) -> dict[str, str]:
    """Contract tool -> route a live proof plays it on: the path behind the tool proxy for an entry whose mode is
    `service`, else `MCP_ROUTE` for a tool in `toolbox_tools` (what the MCP Toolbox serves) that the contract does not
    place in the agent's own process.

    Empty when neither server is up: nothing could be played."""
    endpoints = endpoints or {}
    bound: dict[str, str] = {}
    for name in contract.tool_names():
        entry = contract.entry_for(name)
        mode = entry.mode if entry is not None else ""
        if TOOL_PROXY in endpoints and mode == "service":
            bound[name] = str(entry.endpoint or name).strip("/")
        elif MCP_TOOLBOX in endpoints and name in toolbox_tools and mode not in IN_PROCESS_MODES:
            bound[name] = MCP_ROUTE + name
    return bound


def served_by_toolbox(endpoints: Mapping[str, RuntimeEndpoint]) -> set[str]:
    """The tool names the runtime's MCP Toolbox lists; empty when it has none or cannot be listed."""
    if MCP_TOOLBOX not in (endpoints or {}):
        return set()
    try:
        body = mcp_request(_address(endpoints[MCP_TOOLBOX]), "tools/list", {})
    except Exception as exc:  # noqa: BLE001 - an unlisted toolbox plays nothing, the cases keep their offline proof
        logger.warning("live proof: MCP Toolbox tools not listed: %s: %s", type(exc).__name__, exc)
        return set()
    tools = (body.get("result") or {}).get("tools") if isinstance(body.get("result"), dict) else None
    return {str(tool["name"]) for tool in tools or [] if isinstance(tool, dict) and tool.get("name")}


def _address(endpoint: RuntimeEndpoint | str) -> str:
    return str(getattr(endpoint, "address", endpoint) or "")


def _outcomes(scenario: Scenario, catalogue: Catalogue, world: Any, calls: list[Call]) -> list[Outcome]:
    reference = reference_of(scenario.solution)
    return [run_check(source, world, calls, name=name, reference=reference) for name, source in checks_for(scenario, catalogue)]


def _play(scenario: Scenario, world: Any, steps: list[Step]) -> tuple[list[Call], list[str], list[str]]:
    """Set up, play `steps`, and say which ran unbound (assumed) and which were refused."""
    applied = apply_setup(scenario, world)
    if not applied.ok:
        raise RuntimeError(f"setup: {applied.said}")
    ready = check_ready(scenario, world)
    if not ready.ok:
        raise RuntimeError(f"ready: {ready.said}")
    world.calls = []
    refused = []
    for step in steps:
        call = play_reference_step(world, step)
        if not call.ok:
            refused.append(f"{call.name}({step.arguments}): {call.error}")
    assumed = sorted({str(step.tool) for step in steps if not executes(world, str(step.tool))})
    return list(world.calls), assumed, refused


def _digest(world: Any) -> str | None:
    try:
        return authoring_runtime_validation.world_state_digest(world)
    except Exception as exc:  # noqa: BLE001 - an unreadable state compares as different
        logger.info("live proof: world state not read: %s", type(exc).__name__)
        return None


async def prove_live(
    *,
    authoring: Path,
    contract: AgentContract,
    runtime: EnvironmentRuntime,
    provider: ProcessRuntimeProvider,
    factory: ProcessWorldFactory,
    work: Path,
    seed: int = 0,
    keys: set[str] | None = None,
    until: float | None = None,
) -> dict[str, LiveResult]:
    """Play each case's reference, an empty run and its controls on the live runtime; write and
    return `{scenario_key: live result}`. Returns {} when no tool is bound to a running server.

    `keys` plays only those cases. No case starts once `until` (epoch seconds) has passed: a case not
    reached keeps its offline proof."""
    bound = service_endpoints(contract, runtime.endpoints, served_by_toolbox(runtime.endpoints))
    if not bound:
        return {}
    proxy = _address(runtime.endpoints.get(TOOL_PROXY, ""))
    toolbox = _address(runtime.endpoints.get(MCP_TOOLBOX, ""))
    scenarios = load_scenarios(authoring)
    catalogue = load_catalogue(authoring)
    results: dict[str, LiveResult] = {}

    async def fresh() -> Any:
        await provider.reset(runtime, work_directory=work)
        world = await factory.create(runtime, rng=random.Random(seed))
        world.bind_tools(proxy, bound, contract.tool_names(), mcp_toolbox=toolbox)
        return world

    for scenario in scenarios:
        key = proof_key(scenario)
        if keys is not None and key not in keys:
            continue
        if until is not None and time.time() >= until:
            logger.warning("live proof: time budget spent before %s; it keeps its offline proof", key)
            continue
        if not any(str(step.tool) in bound for step in scenario.solution) and not any(
            str(step.tool) in bound for control in scenario.negative_controls for step in control.steps
        ):
            continue
        try:
            world = await fresh()
            calls, assumed, refused = await asyncio.to_thread(_play, scenario, world, list(scenario.solution))
            with_solution = await asyncio.to_thread(_outcomes, scenario, catalogue, world, calls)
            right = await asyncio.to_thread(_digest, world)
            world = await fresh()
            nothing_calls, _, _ = await asyncio.to_thread(_play, scenario, world, [])
            with_nothing = await asyncio.to_thread(_outcomes, scenario, catalogue, world, nothing_calls)
            untouched = await asyncio.to_thread(_digest, world)
            controls: list[ControlResult] = []
            for control in scenario.negative_controls:
                try:
                    world = await fresh()
                    control_calls, control_assumed, _ = await asyncio.to_thread(_play, scenario, world, list(control.steps))
                    outcomes = await asyncio.to_thread(_outcomes, scenario, catalogue, world, control_calls)
                except Exception as exc:  # noqa: BLE001 - a control that cannot be played is no evidence, the case still is
                    controls.append(
                        {"name": control.name, "why": control.why, "failed": None, "held": [], "assumed": [],
                         "error": redact_outbound_text(f"{type(exc).__name__}: {exc}")[:300]}
                    )
                    continue
                controls.append(played_control(control, outcomes, control_assumed))
        except Exception as exc:  # noqa: BLE001 - a case the live lane cannot play keeps its offline proof
            logger.warning("live proof skipped for %s: %s", key, redact_outbound_text(f"{type(exc).__name__}: {exc}"))
            continue
        results[key] = {
            "assumed": assumed,
            "refused": refused,
            "with_solution": [{"name": one.name, "held": one.held, "said": one.said, "broken": one.broken} for one in with_solution],
            "with_nothing": [{"name": one.name, "held": one.held, "said": one.said, "broken": one.broken} for one in with_nothing],
            "outcome": "no_change" if not assumed and right is not None and right == untouched else "change",
            "controls": controls,
        }
    await provider.reset(runtime, work_directory=work)
    write_live(authoring, results)
    return results


def write_live(authoring: Path, results: Mapping[str, LiveResult]) -> None:
    """Merge `results` into the live proofs file by key, so other keys keep theirs."""
    write_json_atomic(Path(authoring) / LIVE_PROOFS, {**load_live(authoring), **results})


def forget_live(authoring: Path, keys: set[str] | None) -> None:
    """Drop the live results of `keys` (every key when None), so a case is never given a result an
    earlier job played on what may since have been edited."""
    if not (Path(authoring) / LIVE_PROOFS).is_file():
        return
    kept = {} if keys is None else {key: value for key, value in load_live(authoring).items() if key not in keys}
    (Path(authoring) / LIVE_PROOFS).unlink()
    write_live(authoring, kept)


def load_live(authoring: Path) -> dict[str, LiveResult]:
    """The live results by key, as `write_live` wrote them; none when the file is absent or unreadable."""
    try:
        value = json.loads((Path(authoring) / LIVE_PROOFS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def apply_live(proof: Proof, live: LiveResult) -> Proof:
    """The offline proof with its gates replaced by what the live runtime showed.

    Leaks, sources and rule checks stay the offline proof's; readiness, the reference, the empty run
    and the controls come from the live run, which is the stronger evidence."""
    def outcomes(rows: list[CheckRecord]) -> list[Outcome]:
        return [Outcome(row["name"], bool(row["held"]), str(row.get("said") or ""), broken=bool(row.get("broken"))) for row in rows]

    proof.deferred = False
    proof.ready = True
    proof.why_not_ready = ""
    proof.with_solution = outcomes(live.get("with_solution") or [])
    proof.with_nothing = outcomes(live.get("with_nothing") or [])
    proof.assumed = list(live.get("assumed") or [])
    proof.refused = list(live.get("refused") or [])
    proof.broken = [one.name for one in proof.with_solution if one.broken]
    proof.solvable = all(one.held for one in proof.with_solution) and not proof.broken
    proof.controls = [dict(one) for one in live.get("controls") or []]
    proof.broken += control_broken(proof.controls)
    proof.weak = [one.name for one in proof.with_nothing if one.held]
    proof.outcome = str(live.get("outcome") or "change")
    proof.vacuous = bool(proof.with_nothing) and len(proof.weak) == len(proof.with_nothing)
    if proof.outcome == "no_change" and any(one.get("failed") for one in proof.ran_controls):
        proof.vacuous = False
    return proof
