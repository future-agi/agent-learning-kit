"""Proving cases on the live runtime for tools the agent serves over HTTP.

Offline, every hosted tool is runtime-owned, so a case's reference solution and its negative
controls are recorded without running and the case reads "kept as written". While certification
has the agent's own stack up, a tool whose contract entry is `service` can be played for real:
the bundle's tool proxy forwards a JSON POST to the agent's tools service, records the call, and
the change lands in the real datastore. So, per case and with a reset between each: set up, play
the reference and run the checks; set up, play nothing and run the checks; set up, play each
control and run the checks. A tool the agent runs inside its own process has no endpoint and stays
assumed. The result per case is written beside the authoring tree and laid over the offline proof
by `prove_suite`, so leaks, sources and rule checks still come from the offline proof.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LIVE_PROOFS = ".futureagi-live-proofs.json"
TOOL_PROXY = "tool_proxy"


def service_endpoints(contract: Any, endpoints: dict[str, Any]) -> dict[str, str]:
    """Contract tool -> route behind the tool proxy, for tools whose entry mode is `service`.

    Empty when the runtime has no tool proxy: nothing could be played."""
    if TOOL_PROXY not in (endpoints or {}):
        return {}
    bound: dict[str, str] = {}
    for name in contract.tool_names():
        entry = contract.entry_for(name)
        if entry is not None and entry.mode == "service":
            bound[name] = str(entry.endpoint or name).strip("/")
    return bound


def _address(endpoint: Any) -> str:
    return str(getattr(endpoint, "address", endpoint) or "")


def _outcomes(scenario: Any, catalogue: Any, world: Any, calls: list[Any]) -> list[Any]:
    from .checks import reference_of, run_check
    from .prove import _checks_for

    reference = reference_of(scenario.solution)
    return [run_check(source, world, calls, name=name, reference=reference) for name, source in _checks_for(scenario, catalogue)]


def _play(scenario: Any, world: Any, steps: list[Any]) -> tuple[list[Any], list[str], list[str]]:
    """Set up, play `steps`, and say which ran unbound (assumed) and which were refused."""
    from .folder import apply_setup, check_ready
    from .prove import executes, play_reference_step

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
    from .authoring_runtime_validation import _world_state_digest

    try:
        return _world_state_digest(world)
    except Exception:  # noqa: BLE001 - an unreadable state compares as different
        return None


async def prove_live(
    *,
    authoring: Path,
    contract: Any,
    runtime: Any,
    provider: Any,
    factory: Any,
    work: Path,
    seed: int = 0,
) -> dict[str, dict[str, Any]]:
    """Play each case's reference, an empty run and its controls on the live runtime; write and
    return `{scenario_key: live result}`. Returns {} when no tool is service-bound."""
    from .catalogue import load_catalogue
    from .proofs import proof_key
    from .scenario_tools import load_scenarios

    bound = service_endpoints(contract, runtime.endpoints)
    if not bound:
        return {}
    proxy = _address(runtime.endpoints[TOOL_PROXY])
    scenarios = load_scenarios(authoring)
    catalogue = load_catalogue(authoring)
    results: dict[str, dict[str, Any]] = {}

    async def fresh() -> Any:
        await provider.reset(runtime, work_directory=work)
        world = await factory.create(runtime, rng=random.Random(seed))
        world.bind_tools(proxy, bound, contract.tool_names())
        return world

    for scenario in scenarios:
        key = proof_key(scenario)
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
            controls = []
            for control in scenario.negative_controls:
                try:
                    world = await fresh()
                    control_calls, control_assumed, _ = await asyncio.to_thread(_play, scenario, world, list(control.steps))
                    outcomes = await asyncio.to_thread(_outcomes, scenario, catalogue, world, control_calls)
                except Exception as exc:  # noqa: BLE001 - a control that cannot be played is no evidence, the case still is
                    controls.append(
                        {"name": control.name, "why": control.why, "failed": None, "held": [], "assumed": [],
                         "error": f"{type(exc).__name__}: {exc}"[:300]}
                    )
                    continue
                named = set(control.must_fail) or {one.name for one in outcomes}
                controls.append(
                    {
                        "name": control.name,
                        "why": control.why,
                        "failed": any(not one.held and not one.broken for one in outcomes if one.name in named)
                        if outcomes
                        else None,
                        "held": [one.name for one in outcomes if one.held],
                        "broken": [one.name for one in outcomes if one.broken and one.name in named],
                        "assumed": control_assumed,
                    }
                )
        except Exception as exc:  # noqa: BLE001 - a case the live lane cannot play keeps its offline proof
            logger.warning("live proof skipped for %s: %s: %s", key, type(exc).__name__, exc)
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


def write_live(authoring: Path, results: dict[str, dict[str, Any]]) -> None:
    """Merge `results` into the live proofs file by key, so other keys keep theirs."""
    path = Path(authoring) / LIVE_PROOFS
    merged = {**load_live(authoring), **results}
    handle, temporary = tempfile.mkstemp(dir=Path(authoring), prefix=".live-proofs-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(merged, out, indent=2, sort_keys=True, default=str)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def load_live(authoring: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads((Path(authoring) / LIVE_PROOFS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def apply_live(proof: Any, live: dict[str, Any]) -> Any:
    """The offline proof with its gates replaced by what the live runtime showed.

    Leaks, sources and rule checks stay the offline proof's; readiness, the reference, the empty run
    and the controls come from the live run, which is the stronger evidence."""
    from .checks import Outcome
    from .prove import control_broken

    def outcomes(rows: list[dict[str, Any]]) -> list[Any]:
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
