"""Proving a scenario is worth keeping, before anything is ever run against the agent.

Two gates, both pure code. No model is asked whether a scenario is good; the environment decides.

**Solvable.** Reset the world, apply the scenario's own setup, run its reference solution, run
its checks. They must pass. If they do not, either the scenario cannot be passed at all or its
checks are wrong, and both have happened here: one scenario asserted a value the agent was never
permitted to send; another demanded confirmation of an item that could not be ordered. Neither
was noticed until a live run failed and read as a finding about the agent.

**Not vacuous.** Reset, apply the setup, run *nothing*, run the checks. They must fail. A check
that passes with no actions taken grades nothing while reporting a result, which is how a suite
goes quietly green.

Terminal-bench keeps its tasks honest this way, and it is the cheapest useful thing in the whole
harness: no tokens, no network, a few milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .checks import Outcome, run_check
from .environment import Catalogue
from .scenario import Scenario
from .world.runtime import Call, GeneratedWorld
from .world.snapshot import apply_overlay, restore


@dataclass
class Proof:
    """Whether a scenario holds up, and what happened when it was tried."""

    solvable: bool = False
    vacuous: bool = True
    with_solution: list[Outcome] = field(default_factory=list)
    with_nothing: list[Outcome] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return self.solvable and not self.vacuous and not self.broken

    def why(self) -> str:
        """What to fix, in the order worth fixing it."""
        if self.broken:
            return "these checks are broken, not failing:\n  - " + "\n  - ".join(
                self.broken
            )
        if not self.solvable:
            failed = [one for one in self.with_solution if not one.held]
            said = "\n  - ".join(f"{one.name}: {one.said}" for one in failed)
            refusals = (
                "\n\nThe solution's own calls were refused by the world:\n  - "
                + "\n  - ".join(self.refused)
                if self.refused
                else ""
            )
            return (
                "the reference solution does not pass this scenario's own checks, so either the "
                "scenario cannot be passed or the checks are wrong:\n  - "
                + said
                + refusals
            )
        if self.vacuous:
            passed = [one.name for one in self.with_nothing if one.held]
            return (
                "these checks pass without the agent doing anything, so they grade nothing:\n  - "
                + "\n  - ".join(passed)
                + "\n\nIf the point of this scenario is that nothing should happen, checking "
                "the world alone cannot show it — an untouched world looks identical to one "
                "where the agent did nothing at all. Check the calls instead: that the agent "
                "tried, and that the attempt was refused rather than succeeding.\n"
                "    def check(world, calls):\n"
                "        tried = [c for c in calls if c.name == 'add']\n"
                "        if not tried: return 'never attempted it'\n"
                "        if any(c.ok for c in tried): return 'it succeeded'\n"
                "        return None"
            )
        return "holds"


def _checks_for(scenario: Scenario, catalogue: Catalogue) -> list[tuple[str, str]]:
    """The deterministic checks this scenario is graded by, in catalogue order."""
    chosen: list[tuple[str, str]] = []
    for name in scenario.sub_goals:
        sub_goal = catalogue.named(name)
        if sub_goal is not None and sub_goal.deterministic():
            chosen.append((name, sub_goal.check))
    return chosen


def _run(
    scenario: Scenario, world_root: Path, *, with_solution: bool
) -> tuple[GeneratedWorld, list[Call], list[str]]:
    """A fresh world with the scenario's setup, optionally with the solution played through it."""
    world = restore(world_root)
    apply_overlay(world, scenario.setup)
    world.reset()
    refused: list[str] = []
    if with_solution:
        for step in scenario.solution:
            call = world.call(step.tool, step.arguments)
            if not call.ok:
                refused.append(f"{call.name}({step.arguments}): {call.error}")
    return world, list(world.calls), refused


def prove(scenario: Scenario, catalogue: Catalogue, world_root: Path) -> Proof:
    """Run both gates and say whether this scenario is worth keeping."""
    proof = Proof()
    checks = _checks_for(scenario, catalogue)
    if not checks:
        proof.broken = [
            "none of this scenario's sub-goals has a check in code, so nothing here can be "
            "settled without asking a model"
        ]
        return proof

    world, calls, refused = _run(scenario, world_root, with_solution=True)
    try:
        proof.with_solution = [
            run_check(source, world, calls, name=name) for name, source in checks
        ]
    finally:
        world.close()
    proof.refused = refused
    proof.broken = [one.name for one in proof.with_solution if one.broken]
    proof.solvable = all(one.held for one in proof.with_solution) and not proof.broken

    untouched, nothing, _ = _run(scenario, world_root, with_solution=False)
    try:
        proof.with_nothing = [
            run_check(source, untouched, nothing, name=name) for name, source in checks
        ]
    finally:
        untouched.close()
    # Vacuous only if *every* check still passes with nothing done. One check that survives an
    # empty run is often legitimate — "no order was placed" is a real thing to assert about a
    # refusal scenario — but a whole set of them means nothing is being graded.
    proof.vacuous = bool(proof.with_nothing) and all(
        one.held for one in proof.with_nothing
    )
    return proof
