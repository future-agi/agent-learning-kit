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

from .catalogue import Catalogue, load_catalogue
from .checks import Outcome, reference_of, run_check
from .contract import AgentContract
from .controls import control_broken, played_control, refused_attempts, unmatched_calls, writes_beyond
from .folder import apply_setup, check_ready
from .json_file import write_json_atomic
from .outbound import redact_outbound_text
from .proof_records import CheckRecord, ControlResult, LiveResult
from .proofs import proof_key
from .prove import (
    Proof,
    actors_in,
    assumed_tools,
    checks_for,
    excused_by_assumed_steps,
    executes,
    inconclusive_on_assumed,
    play_reference_step,
    recordable_tools,
    record_unrun_step,
)
from .scenario import Scenario, Step
from .scenario_tools import load_scenarios
from .world.handle import MCP_ROUTE, mcp_request
from .world.runtime import Call, as_gone_through
from .world.snapshot import world_state_digest

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


# The most calls a failing reference's evidence lists.
MOST_CALLS_SHOWN = 40


def _as_checked(call: Call) -> dict[str, Any]:
    """One call as a check reads it: its tool, arguments and outcome flags."""
    return {
        "name": call.name,
        "arguments": redact_outbound_text(json.dumps(call.arguments, default=str))[:300],
        "ok": call.ok,
        "refused": call.refused,
        "refusal_code": getattr(call, "refusal_code", ""),
    }


def _outcomes(scenario: Scenario, catalogue: Catalogue, world: Any, calls: list[Call]) -> list[Outcome]:
    reference = reference_of(scenario.solution)
    return [run_check(source, world, calls, name=name, reference=reference) for name, source in checks_for(scenario, catalogue)]


def _play(
    scenario: Scenario,
    world: Any,
    steps: list[Step],
    recordable: frozenset[str] = frozenset(),
    authoring: Path | None = None,
) -> tuple[list[Call], list[str], list[str]]:
    """Set up, play `steps`, and say which ran unbound (assumed) and which were refused. A step of a `recordable` tool
    nothing here runs is recorded, not run (`record_unrun_step`), as the offline proof records it. With the
    `authoring` tree, the scenario's actors act around the steps as in the offline proof (`actors_in`), so an
    approval an actor gives reaches the world before the step that needs it; their calls are never the agent's."""
    applied = apply_setup(scenario, world)
    if not applied.ok:
        raise RuntimeError(f"setup: {applied.said}")
    ready = check_ready(scenario, world)
    if not ready.ok:
        raise RuntimeError(f"ready: {ready.said}")
    world.calls = []
    world.recorded = []
    acting, by_actors = actors_in(scenario, authoring, world) if authoring is not None else (None, {})
    if acting is not None and steps:
        acting.act_on(None)
    refused = []
    for step in steps:
        tool = str(step.tool)
        unrun = tool in recordable and not executes(world, tool)
        call = record_unrun_step(world, step) if unrun else play_reference_step(world, step)
        if call.ok is False and not unrun:
            refused.append(f"{call.name}({step.arguments}): {call.error}")
        if acting is not None:
            acting.act_on(step)
    assumed = [tool for tool in assumed_tools(world, steps) if tool not in world.recorded]
    return [call for index, call in enumerate(world.calls) if index not in by_actors], assumed, refused


def _digest(world: Any) -> str | None:
    try:
        return world_state_digest(world, source_only=True)
    except Exception as exc:  # noqa: BLE001 - an unreadable state compares as different
        logger.info("live proof: world state not read: %s", type(exc).__name__)
        return None


# The most runtime instances in one sandbox (`ALK_VALIDATION_INSTANCES`, the one setting for a build's validation and a
# Validate's proof): a ceiling, never a target. How many the sandbox can hold is decided where they are made
# (`admit_parallelism`, from its own cgroup limits, and one when the agent holds a fixed port).
DEFAULT_PROVE_CONCURRENCY = 4
# What the live pass decided and how long each case took, kept with the job's files: the sandbox is gone afterwards.
LANES_FILE = "generic-harness/live-proof-lanes.json"
# Memory a lane keeps free of the sandbox's limit: under it, lanes stop taking cases until one is left.
MEMORY_FLOOR = 0.15
# The share of the memory limit the lanes may fill, from the first case's measured growth.
MEMORY_SHARE = 0.85
# How often the sandbox's memory is read while the first case is measured.
MEMORY_SAMPLE_SECONDS = 0.25
# How often the results so far are saved while cases are proved.
SAVE_EVERY_SECONDS = 5.0


def prove_concurrency() -> int:
    """`ALK_VALIDATION_INSTANCES` when it is a whole number of at least 1, else the default ceiling."""
    import os

    try:
        value = int(str(os.environ.get("ALK_VALIDATION_INSTANCES") or "").strip())
    except ValueError:
        return DEFAULT_PROVE_CONCURRENCY
    return value if value >= 1 else DEFAULT_PROVE_CONCURRENCY


def _memory() -> tuple[float, float] | None:
    """`(used, limit)` GiB of this sandbox, read the way its runtime instances were sized."""
    from .process_runtime import memory_headroom

    try:
        return memory_headroom()
    except Exception:  # noqa: BLE001 - unreadable memory leaves the lanes as provisioned
        return None


def _short_of_memory() -> bool:
    reading = _memory()
    return reading is not None and reading[1] > 0 and (reading[1] - reading[0]) / reading[1] < MEMORY_FLOOR


def _lanes_that_fit(lanes: int, before: float | None, peak: float | None) -> int:
    """How many lanes the first case's measured growth leaves room for, at least 1."""
    reading = _memory()
    if lanes <= 1 or reading is None or before is None or peak is None:
        return lanes
    used, limit = reading
    growth = max(peak - before, 0.0)
    if growth <= 0:
        return lanes
    room = limit * MEMORY_SHARE - used
    return max(1, min(lanes, 1 + int(room // growth)))


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
    runtimes: list[EnvironmentRuntime] | None = None,
) -> dict[str, LiveResult]:
    """Play each case's reference, an empty run and its controls on the live runtime; write and
    return `{scenario_key: live result}`. Returns {} when no tool is bound to a running server.

    `keys` plays only those cases. No case starts once `until` (epoch seconds) has passed: a case not
    reached keeps its offline proof. With `runtimes` (instances of the same sealed runtime in this sandbox, `runtime`
    among them), up to `prove_concurrency()` cases are played at once, one per instance; one instance plays them one
    after another, as before."""
    lanes = [runtime, *(one for one in runtimes or () if one is not runtime)][: prove_concurrency()]
    started = time.monotonic()
    record: dict[str, Any] = {
        "requested_instances": prove_concurrency(),
        "provisioned_instances": len(lanes),
        # Why provisioning made fewer instances than asked (`resource_limited`, `fixed_port`, ...), as it recorded.
        "admission": [dict(one) for one in getattr(provider, "degrade_events", ()) or ()],
        "lanes": 1,
        "lane_changes": [],
        "cases": [],
    }
    bound = service_endpoints(contract, runtime.endpoints, served_by_toolbox(runtime.endpoints))
    if not bound:
        return {}
    scenarios = load_scenarios(authoring)
    catalogue = load_catalogue(authoring)
    order = [
        scenario
        for scenario in scenarios
        if (keys is None or proof_key(scenario) in keys)
        and (
            any(str(step.tool) in bound for step in scenario.solution)
            or any(str(step.tool) in bound for control in scenario.negative_controls for step in control.steps)
        )
    ]
    results: dict[str, LiveResult] = {}
    retry: list[Scenario] = []
    flushed = {"at": time.monotonic()}

    def due() -> bool:
        return until is None or time.time() < until

    def keep(scenario: Scenario, result: LiveResult) -> None:
        """Record a case's result, and every few seconds save those so far: a job stopped at its deadline keeps them."""
        results[proof_key(scenario)] = result
        if time.monotonic() - flushed["at"] >= SAVE_EVERY_SECONDS:
            write_live(authoring, results)
            flushed["at"] = time.monotonic()

    async def prove_on(lane: Any, scenario: Scenario) -> LiveResult:
        began = time.monotonic()
        try:
            return await _prove_on(lane, scenario)
        finally:
            record["cases"].append(
                {"key": proof_key(scenario), "lane": lanes.index(lane), "seconds": round(time.monotonic() - began, 2)}
            )

    async def _prove_on(lane: Any, scenario: Scenario) -> LiveResult:
        endpoints = service_endpoints(contract, lane.endpoints, served_by_toolbox(lane.endpoints))
        proxy = _address(lane.endpoints.get(TOOL_PROXY, ""))
        toolbox = _address(lane.endpoints.get(MCP_TOOLBOX, ""))

        async def fresh() -> Any:
            await provider.reset(lane, work_directory=work)
            world = await factory.create(lane, rng=random.Random(seed))
            world.bind_tools(proxy, endpoints, contract.tool_names(), mcp_toolbox=toolbox)
            return world

        return await _prove_case(
            scenario,
            catalogue,
            fresh,
            recordable_tools(scenario, contract, checks_for(scenario, catalogue)),
            Path(authoring),
        )

    if len(lanes) == 1 or len(order) <= 1:
        # One instance: today's loop, case after case.
        for scenario in order:
            if not due():
                logger.warning("live proof: time budget spent before %s; it keeps its offline proof", proof_key(scenario))
                continue
            try:
                keep(scenario, await prove_on(lanes[0], scenario))
            except Exception as exc:  # noqa: BLE001 - a case the live lane cannot play keeps its offline proof
                _skipped(scenario, exc)
    else:
        pending = list(order)
        # The first case alone, its memory growth measured, so the lanes used fit what this agent takes.
        before = peak = None
        if due():
            first = pending.pop(0)
            before, peak = await _measured(prove_on(lanes[0], first), keep, retry, first)
        active = _lanes_that_fit(len(lanes), before, peak)
        reading = _memory()
        record["memory_gib"] = {
            "before_first_case": before,
            "peak_first_case": peak,
            "used": reading[0] if reading else None,
            "limit": reading[1] if reading else None,
        }
        record["lanes"] = active
        if active < len(lanes):
            record["lane_changes"].append({"to": active, "reason": "first_case_memory_growth"})
        line = f"live proof: {len(order)} cases on {active} of {len(lanes)} runtime instances"
        logger.info(line)
        print(line, flush=True)
        state = {"active": active, "failed": False}
        queue: asyncio.Queue[Scenario] = asyncio.Queue()
        for scenario in pending:
            queue.put_nowait(scenario)

        async def worker(lane: Any, index: int) -> None:
            while True:
                if index >= state["active"]:
                    return
                if not due():
                    return
                if state["active"] > 1 and _short_of_memory():
                    # Fewer lanes, down to one; this lane's cases go to the others.
                    state["active"] = max(1, state["active"] - 1)
                    record["lane_changes"].append({"to": state["active"], "reason": "memory_short"})
                    logger.warning("live proof: memory is short; %d lanes now", state["active"])
                    continue
                try:
                    scenario = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    keep(scenario, await prove_on(lane, scenario))
                except Exception as exc:  # noqa: BLE001 - played again alone, never dropped
                    logger.warning(
                        "live proof: %s failed beside other cases (%s); it is played again alone",
                        proof_key(scenario),
                        redact_outbound_text(f"{type(exc).__name__}: {exc}")[:200],
                    )
                    retry.append(scenario)
                    # A failure among several lanes may be the sandbox's limits: no more cases at once.
                    if state["active"] > 1:
                        record["lane_changes"].append(
                            {"to": 1, "reason": "case_failed_beside_others", "key": proof_key(scenario)}
                        )
                    state["active"] = 1

        await asyncio.gather(*(worker(lane, index) for index, lane in enumerate(lanes[:active])))
        # What the lanes did not reach (time, or shrunk to one) and what failed beside others: one at a time.
        leftover = [*retry, *_drained(queue)]
        for scenario in sorted(leftover, key=order.index):
            if not due():
                logger.warning("live proof: time budget spent before %s; it keeps its offline proof", proof_key(scenario))
                continue
            try:
                keep(scenario, await prove_on(lanes[0], scenario))
            except Exception as exc:  # noqa: BLE001 - a case the live lane cannot play keeps its offline proof
                _skipped(scenario, exc)
    for lane in lanes:
        await provider.reset(lane, work_directory=work)
    ordered = {proof_key(one): results[proof_key(one)] for one in order if proof_key(one) in results}
    write_live(authoring, ordered)
    record["cases_total"] = len(order)
    record["seconds"] = round(time.monotonic() - started, 2)
    _write_lanes(authoring, record)
    return ordered


def _write_lanes(authoring: Path, record: Mapping[str, Any]) -> None:
    """Keep the live pass's lane decisions and timings with the job's files; never fails the pass."""
    try:
        path = Path(authoring) / LANES_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        write_json_atomic(path, dict(record))
    except Exception as exc:  # noqa: BLE001 - diagnostics only
        logger.warning("live proof: lanes record not written (%s)", type(exc).__name__)


def _drained(queue: asyncio.Queue) -> list[Scenario]:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


def _skipped(scenario: Scenario, exc: Exception) -> None:
    logger.warning(
        "live proof skipped for %s: %s", proof_key(scenario), redact_outbound_text(f"{type(exc).__name__}: {exc}")
    )


async def _measured(
    play: Any, keep: Any, retry: list[Scenario], scenario: Scenario
) -> tuple[float | None, float | None]:
    """Run one case alone while sampling the sandbox's memory: `(before, peak)` GiB, None when unreadable."""
    reading = _memory()
    before = reading[0] if reading is not None else None
    peak = before
    task = asyncio.ensure_future(play)
    while not task.done():
        await asyncio.sleep(MEMORY_SAMPLE_SECONDS)
        now = _memory()
        if now is not None and (peak is None or now[0] > peak):
            peak = now[0]
    try:
        keep(scenario, task.result())
    except Exception as exc:  # noqa: BLE001 - played again alone at the end
        logger.warning("live proof: %s failed (%s); it is played again", proof_key(scenario), type(exc).__name__)
        retry.append(scenario)
    return before, peak


async def _prove_case(
    scenario: Scenario,
    catalogue: Catalogue,
    fresh: Any,
    recordable: frozenset[str] = frozenset(),
    authoring: Path | None = None,
) -> LiveResult:
    """One case on one runtime instance: its reference, an empty run and each control, each on a fresh world."""
    world = await fresh()
    calls, assumed, refused = await asyncio.to_thread(
        _play, scenario, world, list(scenario.solution), recordable, authoring
    )
    recorded = list(getattr(world, "recorded", None) or [])
    with_solution = await asyncio.to_thread(_outcomes, scenario, catalogue, world, calls)
    inconclusive = await asyncio.to_thread(
        inconclusive_on_assumed,
        checks_for(scenario, catalogue),
        world,
        calls,
        reference_of(scenario.solution),
        assumed,
        {one.name for one in with_solution if not one.held and not one.broken},
    )
    right = await asyncio.to_thread(_digest, world)
    world = await fresh()
    nothing_calls, _, _ = await asyncio.to_thread(_play, scenario, world, [], frozenset(), authoring)
    with_nothing = await asyncio.to_thread(_outcomes, scenario, catalogue, world, nothing_calls)
    untouched = await asyncio.to_thread(_digest, world)
    controls: list[ControlResult] = []
    for control in scenario.negative_controls:
        try:
            world = await fresh()
            control_calls, control_assumed, _ = await asyncio.to_thread(
                _play, scenario, world, list(control.steps), recordable, authoring
            )
            # A control's assumed step is read as gone through: an unknown result is no evidence that it fails.
            control_calls = as_gone_through(control_calls, control_assumed)
            outcomes = await asyncio.to_thread(_outcomes, scenario, catalogue, world, control_calls)
            changed = await asyncio.to_thread(writes_beyond, world, reference_of(scenario.solution))
        except Exception as exc:  # noqa: BLE001 - a control that cannot be played is no evidence, the case still is
            controls.append(
                {"name": control.name, "why": control.why, "ran": False, "failed": None, "held": [], "assumed": [],
                 "error": redact_outbound_text(f"{type(exc).__name__}: {exc}")[:300]}
            )
            continue
        played = played_control(
            control,
            outcomes,
            control_assumed,
            unmatched_calls(scenario, control_calls),
            refused_attempts(scenario, control_calls),
        )
        if changed:
            played["changed"] = changed
        controls.append(played)
    return {
        "assumed": assumed,
        "refused": refused,
        **({"inconclusive": inconclusive} if inconclusive else {}),
        # Where the reference fails its own checks, the calls those checks were handed, as they read them: the
        # evidence for telling a check that is wrong from a call recorded wrong.
        **(
            {"calls_checked": [_as_checked(call) for call in calls[:MOST_CALLS_SHOWN]]}
            if any(not one.held and not one.broken for one in with_solution)
            else {}
        ),
        "with_solution": [{"name": one.name, "held": one.held, "said": one.said, "broken": one.broken} for one in with_solution],
        "with_nothing": [{"name": one.name, "held": one.held, "said": one.said, "broken": one.broken} for one in with_nothing],
        "outcome": "no_change" if not assumed and right is not None and right == untouched else "change",
        "controls": controls,
        **({"recorded": recorded} if recorded else {}),
    }


def live_summary(results: Mapping[str, LiveResult]) -> str:
    """How many cases the live runtime played, and in how many the reference ran every step and passed every check. A
    case played is not a case proved: its proof is decided when these results are applied to it."""
    held = sum(
        1
        for one in results.values()
        if not one.get("assumed") and not one.get("refused") and all(row["held"] for row in one.get("with_solution") or [])
    )
    return f"played {len(results)} cases on the live runtime; the reference passed in {held}"


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
    proof.recorded = list(live.get("recorded") or [])
    proof.refused = list(live.get("refused") or [])
    # What the offline pass said about a reference it could not run speaks for nothing the live run did.
    proof.excused = sorted({*excused_by_assumed_steps(proof), *(live.get("inconclusive") or [])})
    proof.trial = {}
    proof.broken = [one.name for one in proof.with_solution if one.broken]
    proof.solvable = all(one.held or one.name in proof.excused for one in proof.with_solution) and not proof.broken
    proof.controls = [dict(one) for one in live.get("controls") or []]
    proof.broken += control_broken(proof.controls)
    proof.weak = [one.name for one in proof.with_nothing if one.held]
    proof.outcome = str(live.get("outcome") or "change")
    proof.vacuous = bool(proof.with_nothing) and len(proof.weak) == len(proof.with_nothing)
    if proof.outcome == "no_change" and any(one.get("failed") for one in proof.ran_controls):
        proof.vacuous = False
    return proof
