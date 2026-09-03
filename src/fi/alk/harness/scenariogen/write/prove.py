"""Proving a scenario is worth keeping, before anything is ever run against the agent.

Three gates, all pure code. No model is asked whether a scenario is good; the environment
decides. Terminal-bench keeps its tasks honest this way, and it is the cheapest useful thing in
the whole harness: no tokens, no network, a few milliseconds.

**Ready.** Reset the world, run the scenario's own ``setup.py``, then its ``ready.py``. The world
has to hold what the scenario presumes. A scenario about the last five chocolates is only a test
of the agent if there really are five; otherwise the agent fails for something we got wrong and
it reads as the agent's fault. This gate is why a missing precondition can never be mistaken for
a finding.

**Solvable.** Then run the reference solution and the checks. They must pass. If they do not,
either the scenario cannot be passed at all or its checks are wrong, and both have happened
here: one scenario asserted a value the agent was never permitted to send; another demanded
confirmation of an item that could not be ordered. Neither was noticed until a live run failed
and read as a finding about the agent.

**Not vacuous.** Then reset, set up again, run *nothing*, and run the checks. They must fail. A
check that passes with no actions taken grades nothing while reporting a result, which is how a
suite goes quietly green. This one earns its keep: on a third-party benchmark it caught three
sub-goals that passed trivially because the seeded world already contained a cancelled order.

Only a scenario that clears all three is kept. That is the green light.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ..model.catalogue import Catalogue
from ...checks import Outcome, run_check
from ..store.folder import apply_setup, check_ready
from ..model.scenario import Scenario
from ...world.runtime import Call, GeneratedWorld
from ...world.snapshot import restore


@dataclass
class Proof:
    """Whether a scenario holds up, and what happened when it was tried."""

    ready: bool = False
    solvable: bool = False
    vacuous: bool = True
    why_not_ready: str = ""
    # Checks that held with nothing done. The scenario is only vacuous when *every* check does
    # that, but a single one still grades nothing, and since sub-goals are shared it will report
    # itself as held for an agent that did nothing at all. Named rather than refused: on a
    # scenario about a refusal, "no order was placed" holding on an untouched world is correct.
    weak: list[str] = field(default_factory=list)
    with_solution: list[Outcome] = field(default_factory=list)
    with_nothing: list[Outcome] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    # Solution steps recorded as passing without being executed, because the tool they name has
    # nothing bound to call. The proof covers the remaining steps only.
    #
    # Not a failure and not nothing. A step that genuinely has no effect on the world is
    # correctly assumed; a step that should have had one and could not run leaves this scenario
    # proved more weakly than its siblings, and the two are indistinguishable from here. It is
    # carried so that whatever reads a proof can say which it is looking at, and so expansion
    # cannot quietly multiply a partial proof into a dozen of them.
    assumed: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return (
            self.ready
            and self.solvable
            and not self.vacuous
            and not self.weak
            and not self.broken
        )

    def gates(self) -> dict[str, bool]:
        """The three answers, for anything that wants to show them."""
        return {
            "ready": self.ready,
            "solvable": self.solvable,
            "not_vacuous": not self.vacuous,
        }

    def why(self) -> str:
        """What to fix, in the order worth fixing it."""
        if not self.ready:
            return (
                "the world is not ready for this scenario, so running it would test us rather "
                f"than the agent:\n  - {self.why_not_ready}\n\n"
                "Either setup.py does not make the change this scenario needs, or ready.py is "
                "checking for something the setup never creates."
            )
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
        if self.weak:
            return (
                "these individual checks pass without the agent doing anything, so keeping them "
                "would display credit unsupported by evidence:\n  - "
                + "\n  - ".join(self.weak)
                + "\n\nMake each check assert the relevant attempt or observation as well as the "
                "final state. For a refusal, require that the call was attempted and refused; "
                "an untouched world is not evidence that the agent refused correctly."
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


def prepared(
    scenario: Scenario, world_root: Path
) -> tuple[GeneratedWorld, Outcome, Outcome]:
    """A fresh world with this scenario's setup applied, and how that went."""
    world = restore(world_root)
    world.reset()
    applied = apply_setup(scenario, world)
    ready = check_ready(scenario, world) if applied.ok else Outcome(False, applied.said)
    # The setup's own calls are not the agent's. Clearing them keeps a check that counts calls
    # from crediting the agent with work the scenario did on its behalf.
    world.calls = []
    return world, applied, ready


logger = logging.getLogger(__name__)


def play_reference_step(world: GeneratedWorld, step: object) -> Call:
    """Play one correct-agent step without confusing its API with its dependency's API.

    A generated world can execute the agent-facing call directly.  A source-provisioned world
    cannot: its model-facing tool may enforce local ordering and inject session state before it
    reaches a raw HTTP service.  For those worlds, drive the raw endpoint with the explicitly
    declared environment payload, but record only the semantic call the agent was expected to
    make.  Purely local state-machine tools have no dependency effect and are recorded as such.
    """
    name = str(getattr(step, "tool", "") or "")
    arguments = dict(getattr(step, "arguments", {}) or {})
    environment_arguments = _resolve_reference_values(
        dict(getattr(step, "environment_arguments", {}) or {}), world.calls
    )
    runtime_tools = set(getattr(world, "runtime_tools", set()))
    if name not in runtime_tools:
        return world.call(name, arguments)

    endpoint = getattr(world, "endpoint_for", {}).get(name)
    forward = getattr(world, "forward", None)
    if endpoint and callable(forward):
        dependency_arguments = environment_arguments or arguments
        effect = forward(endpoint, dependency_arguments, record=False)
        semantic = Call(
            name=name,
            arguments=arguments,
            result=effect.result,
            ok=effect.ok,
            error=effect.error,
            refused=effect.refused,
            at=effect.at,
        )
    else:
        # Reaching here means the tool is a declared runtime tool with nothing bound to call.
        # Two very different situations arrive at the same place: a local orchestration action
        # that genuinely has no environment effect, and a lane where the runtime is built after
        # authoring so no endpoint exists yet. The second cannot be proved, and recording it as
        # a pass is what lets a scenario be kept on a solution nothing ever executed. Say so.
        logger.warning(
            "reference step assumed rather than executed: tool=%s reason=%s. Its result is "
            "recorded ok with no call made, so this step proves nothing about the world.",
            name,
            "no endpoint bound" if not endpoint else "no forwarder on the world",
        )
        semantic = Call(name=name, arguments=arguments)
    world.calls.append(semantic)
    return semantic


def _resolve_reference_values(value: object, calls: list[Call]) -> object:
    """Resolve a dependency value produced by an earlier reference call.

    ``$call.book_ride.booking_ref`` refers to the named field on the most recent successful
    ``book_ride`` result. This drives real chained effects without pretending the model knew a
    backend-generated id.
    """
    if isinstance(value, dict):
        return {
            key: _resolve_reference_values(item, calls) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_resolve_reference_values(item, calls) for item in value]
    if not isinstance(value, str) or not value.startswith("$call."):
        return value
    parts = value.split(".")
    if len(parts) < 3:
        return value
    call_name = parts[1]
    found = next(
        (call for call in reversed(calls) if call.name == call_name and call.ok), None
    )
    current = found.result if found is not None else None
    for key in parts[2:]:
        if not isinstance(current, dict) or key not in current:
            return value
        current = current[key]
    return current


def _run(
    scenario: Scenario, world_root: Path, *, with_solution: bool
) -> tuple[GeneratedWorld, list[Call], list[str], list[str]]:
    """A world set up for this scenario, optionally with the solution played through it."""
    world, _applied, _ready = prepared(scenario, world_root)
    refused: list[str] = []
    assumed: list[str] = []
    if with_solution:
        for step in scenario.solution:
            call = play_reference_step(world, step)
            if not call.ok:
                refused.append(f"{call.name}({step.arguments}): {call.error}")
        runtime_tools = set(getattr(world, "runtime_tools", set()))
        endpoints = getattr(world, "endpoint_for", {}) or {}
        assumed[:] = [
            str(getattr(step, "tool", ""))
            for step in scenario.solution
            if str(getattr(step, "tool", "")) in runtime_tools
            and not endpoints.get(str(getattr(step, "tool", "")))
        ]
        if assumed:
            logger.warning(
                "scenario %s: %d of %d solution steps were assumed, not executed (%s). The "
                "proof below covers only the remaining steps.",
                scenario.name,
                len(assumed),
                len(scenario.solution),
                ", ".join(sorted(set(assumed))),
            )
        else:
            logger.info(
                "scenario %s: all %d solution steps executed against the world",
                scenario.name,
                len(scenario.solution),
            )
    return world, list(world.calls), refused, sorted(set(assumed))


# Proving is not re-entrant across writers, because there is one world and a proof rewrites it.
# ``restore`` truncates every table and then inserts the snapshot back on an autocommit
# connection, so the truncate lands before the inserts do. Two writers proving at once interleave
# there: both truncate, then both insert, and the second collides with the first on a primary key.
# That is the loud failure, and it killed a writer mid-run. The quiet one is worse: between a
# writer's restore and its own ready check, a sibling can restore underneath it, and the scenario
# is then proved against a world nobody wrote for it. A proof that passes for the wrong reason is
# the one thing this whole stage exists to prevent, so the world is held for the length of a proof.
WORLD_IN_USE = threading.RLock()


def prove(scenario: Scenario, catalogue: Catalogue, world_root: Path) -> Proof:
    """Run all three gates and say whether this scenario is worth keeping."""
    proof = Proof()
    checks = _checks_for(scenario, catalogue)
    if not checks:
        proof.broken = [
            "none of this scenario's sub-goals has a check in code, so nothing here can be "
            "settled without asking a model"
        ]
        return proof

    # Gate 1: is the world ready for this scenario at all?
    world, applied, ready = prepared(scenario, world_root)
    world.close()
    if not applied.ok:
        proof.why_not_ready = applied.said
        if applied.broken:
            proof.broken = [applied.said]
        return proof
    if not ready.ok:
        proof.why_not_ready = ready.said
        if ready.broken:
            proof.broken = [ready.said]
        return proof
    proof.ready = True

    # Gate 2: does the reference solution pass this scenario's own checks?
    world, calls, refused, assumed = _run(scenario, world_root, with_solution=True)
    proof.assumed = assumed
    try:
        proof.with_solution = [
            run_check(source, world, calls, name=name) for name, source in checks
        ]
    finally:
        world.close()
    proof.refused = refused
    proof.broken = [one.name for one in proof.with_solution if one.broken]
    proof.solvable = all(one.held for one in proof.with_solution) and not proof.broken

    # Gate 3: do those same checks fail when nothing is done?
    untouched, nothing, _, _ = _run(scenario, world_root, with_solution=False)
    try:
        proof.with_nothing = [
            run_check(source, untouched, nothing, name=name) for name, source in checks
        ]
    finally:
        untouched.close()
    # Record every check that passes without an action. Even when another checkpoint makes the
    # overall scenario non-vacuous, showing this one as green would award unsupported credit —
    # exactly the misleading partial-pass display the proof gate exists to prevent.
    proof.weak = [one.name for one in proof.with_nothing if one.held]
    # A judged sub-goal reads what the agent said, and an agent that did nothing said nothing, so
    # it cannot be passed by an empty run the way a state check can. It still does not excuse a
    # deterministic checkpoint that independently awards credit with no evidence.
    judged = [
        name
        for name in scenario.sub_goals
        if (found := catalogue.named(name)) is not None and not found.deterministic()
    ]
    proof.vacuous = (
        bool(proof.with_nothing)
        and len(proof.weak) == len(proof.with_nothing)
        and not judged
    )
    return proof
