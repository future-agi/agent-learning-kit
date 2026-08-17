"""Stage four: run the scenarios against the world and say what happened.

Every scenario gets its own world. It is restored from the frozen snapshot, the scenario's own
setup is run against it, and it is thrown away afterwards. Nothing a scenario does can reach the
next one, which is what makes a result mean something on its own and makes the whole suite
repeatable a week later.

The shape is the same regardless of what is being tested: restore, converse, grade against the
state that is left behind. Where the agent actually runs is a target, so the same scenarios grade
a hosted agent without any of this changing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Sequence

from ..contract import AgentContract
from ..environment import load_catalogue, load_simulator_prompt
from ..scenario import Scenario
from ..folder import apply_setup, check_ready
from ..world.snapshot import restore
from .conversation import FINISHED, Exchange, Transcript, converse
from .grade import (
    Checkpoint,
    Result,
    as_json,
    checkpoints,
    grade_sub_goals,
    judge,
    summarise,
)
from .targets import LocalAgent, Target, register_target, resolve, supported


def from_alk(report: Any, world, spent: float) -> Transcript:
    """What ALK's report says happened, in the shape the grading already reads."""
    exchanges: list[Exchange] = []
    for case in getattr(report, "results", None) or []:
        for message in getattr(case, "transcript", None) or []:
            role = (message.get("role") or "") if isinstance(message, dict) else ""
            text = (message.get("content") or "") if isinstance(message, dict) else ""
            if text:
                exchanges.append(
                    Exchange("agent" if role == "assistant" else "customer", str(text))
                )
    return Transcript(
        exchanges=exchanges,
        calls=list(world.calls),
        ended=FINISHED,
        spent_usd=spent,
    )


RUNS = "runs.json"
REPORT = "report.txt"

__all__ = [
    "Checkpoint",
    "Exchange",
    "LocalAgent",
    "Result",
    "Target",
    "Transcript",
    "converse",
    "register_target",
    "run_scenario",
    "run_suite",
    "supported",
    "summarise",
]


async def run_scenario(
    scenario: Scenario,
    contract: AgentContract,
    world_root: Path,
    *,
    target: str = "local",
    model: str | None = None,
    through_alk: bool = False,
    on_exchange: Callable[[Exchange], Any] | None = None,
) -> Result:
    """Run one scenario in its own copy of the world and grade what it left behind."""
    catalogue = load_catalogue(world_root)
    world = restore(world_root)
    try:
        # reset() is how an environment is started in ALK: it clears the call log and
        # publishes the tools and the starting state. Going through it keeps a generated world
        # drivable by anything that already drives an environment.
        world.reset()
        applied = apply_setup(scenario, world)
        if not applied.ok:
            raise RuntimeError(f"the scenario's setup did not run: {applied.said}")
        ready = check_ready(scenario, world)
        if not ready.ok:
            raise RuntimeError(
                f"the world is not ready for this scenario: {ready.said}. Running it would "
                "test us rather than the agent."
            )
        # The setup's calls are not the agent's.
        world.calls = []
        if through_alk:
            # ALK owns the simulation and drives the world through EnvironmentAdapter; the
            # harness only grades what it is left with. Nothing here is modality-specific,
            # which is the point: the browser and voice runners take the same adapter.
            from .alk import simulate

            report, spent = await simulate(
                scenario,
                contract,
                world,
                model=model,
                simulator_prompt=load_simulator_prompt(world_root),
            )
            transcript = from_alk(report, world, spent)
            for exchange in transcript.exchanges:
                if on_exchange:
                    on_exchange(exchange)
        else:
            agent = resolve(target)(contract, world, model=model)
            transcript = await converse(
                agent,
                scenario,
                contract,
                world_root=world_root,
                model=model,
                on_exchange=on_exchange,
            )
        # Settled by code first. The judge is only handed the sub-goals whose catalogue entry
        # says nothing observable decides them.
        settled = grade_sub_goals(world, scenario, catalogue, transcript.calls)
        ending = ", ".join(
            f"{name}: {len(rows)} rows"
            for name, rows in sorted(world.observe().state.items())
        )
        judgements, judged_cost = await judge(
            scenario, transcript, contract, catalogue, model=model, ending=ending
        )
        return Result(
            scenario=scenario.name,
            tests=scenario.tests,
            state_failures=[
                f"{one.name}: {one.said}" for one in settled if not one.held
            ],
            conduct=judgements,
            checkpoints=checkpoints(settled, judgements),
            crashes=[f"{call.name}: {call.error}" for call in transcript.crashed()],
            ended=transcript.ended,
            turns=len(transcript.exchanges),
            calls=len(transcript.calls),
            spent_usd=transcript.spent_usd + judged_cost,
            transcript=transcript.spoken(),
            actions=transcript.actions(),
        )
    finally:
        world.close()


async def run_suite(
    scenarios: Sequence[Scenario],
    contract: AgentContract,
    world_root: Path,
    *,
    target: str = "local",
    model: str | None = None,
    through_alk: bool = False,
    out: Path | None = None,
    on_result: Callable[[Result], Any] | None = None,
    on_exchange: Callable[[Exchange], Any] | None = None,
) -> list[Result]:
    """Run every scenario and write the results out. One failing scenario never stops the rest."""
    destination = Path(out or world_root)
    results: list[Result] = []
    for scenario in scenarios:
        try:
            result = await run_scenario(
                scenario,
                contract,
                world_root,
                target=target,
                model=model,
                through_alk=through_alk,
                on_exchange=on_exchange,
            )
        except Exception as failed:
            # A scenario that could not be run is recorded as unrunnable rather than as a
            # failure of the agent, and the rest of the suite still runs.
            result = Result(
                scenario=scenario.name,
                tests=scenario.tests,
                crashes=[f"could not run: {type(failed).__name__}: {failed}"],
                ended="not-run",
            )
        results.append(result)
        if on_result:
            on_result(result)

    destination.mkdir(parents=True, exist_ok=True)
    # Records for scenarios this suite did not run are kept, not clobbered. A live call and a
    # local run write to the same file, and re-running two scenarios must not erase the third.
    ran = {result.scenario for result in results}
    kept = [
        record
        for record in load_results(destination)
        if isinstance(record, dict) and record.get("scenario") not in ran
    ]
    merged = kept + json.loads(as_json(results))
    (destination / RUNS).write_text(
        json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (destination / REPORT).write_text(summarise(results), encoding="utf-8")
    return results


def load_results(destination: Path) -> list[dict[str, Any]]:
    path = Path(destination) / RUNS
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
