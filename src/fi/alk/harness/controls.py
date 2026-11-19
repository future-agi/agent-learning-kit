"""The negative-control lane of a proof: each wrong behaviour a scenario names, played like its solution, and whether
its checks (or the judge, for a case only a judge grades) fail it. The steps are played by the runner the proof passes
in, so this module never reaches back into the proof."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .catalogue import Catalogue
from .checks import Outcome, run_check
from .judge import judge_sub_goal
from .proof_records import ControlResult
from .scenario import NegativeControl, Scenario
from .world.runtime import Call, GeneratedWorld, settled

# Plays `steps` (else the scenario's solution) on a world set up for the scenario: (world, calls, refused, assumed).
StepRunner = Callable[..., tuple[GeneratedWorld, list[Call], list[str], list[str]]]
# Decides one judged sub-goal of a control: (goal, world, calls, what was said, scenario) -> (held, explanation).
ControlJudge = Callable[[Any, Any, list[Call], list[dict[str, str]], Any], tuple[bool | None, str]]


def play_controls(
    scenario: Scenario,
    world_root: Path,
    checks: list[tuple[str, str]],
    reference: Any,
    *,
    run: StepRunner,
) -> list[ControlResult]:
    """Play each negative control like a solution; a control fails when a check it names fails."""
    played: list[ControlResult] = []
    for control in getattr(scenario, "negative_controls", None) or []:
        if not checks:
            played.append(
                {"name": control.name, "why": control.why, "ran": False, "failed": None, "held": [], "assumed": []}
            )
            continue
        world, calls, _refused, assumed = run(scenario, world_root, with_solution=True, steps=control.steps)
        try:
            outcomes = [run_check(source, world, calls, name=name, reference=reference) for name, source in checks]
        finally:
            world.close()
        played.append(
            played_control(
                control, outcomes, assumed, unmatched_calls(scenario, calls), refused_attempts(scenario, calls)
            )
        )
    return played


def unmatched_calls(scenario: Scenario, calls: Sequence[Call]) -> list[str]:
    """The tools a control called that went through and that the reference never calls: the wrong action itself, when
    a refusal is the case."""
    solved = {str(step.tool) for step in getattr(scenario, "solution", None) or ()}
    return sorted({call.name for call in calls if call.ok and not call.refused and call.name not in solved})


def refused_attempts(scenario: Scenario, calls: Sequence[Call]) -> list[str]:
    """The tools a control tried that the reference never calls and the world refused: the wrong action attempted, with
    nothing committed, so only a check on the attempt can fail it."""
    solved = {str(step.tool) for step in getattr(scenario, "solution", None) or ()}
    went_through = set(unmatched_calls(scenario, calls))
    # Every call outside the reference that did not go through (`unmatched_calls`) was refused or failed.
    return sorted({call.name for call in calls if call.name not in solved and call.name not in went_through})


def played_control(
    control: NegativeControl,
    outcomes: Sequence[Outcome],
    assumed: list[str],
    unmatched: Sequence[str] = (),
    attempted: Sequence[str] = (),
) -> ControlResult:
    """A control as played: it fails when a check it names (every check when it names none) fails. A check that raised
    under it is broken, not failing: no evidence. With no outcomes it was not played, and with a step assumed (recorded
    unrun, no call made) it did not run as written: a check failing then shows nothing about the wrong behaviour, so
    neither reads failed."""
    named = set(control.must_fail) or {one.name for one in outcomes}
    ran = bool(outcomes) and not assumed
    return {
        "name": control.name,
        "why": control.why,
        "ran": ran,
        "failed": any(not one.held and not one.broken for one in outcomes if one.name in named) if ran else None,
        "held": [one.name for one in outcomes if one.held],
        # The checks the control names that its steps made fail, whether or not every step ran: a check it does not
        # name failing only because the control did less (nothing refunded after a wrong lookup) shows nothing.
        "failing": [one.name for one in outcomes if one.name in named and not one.held and not one.broken],
        "broken": [one.name for one in outcomes if one.broken and one.name in named],
        "assumed": assumed,
        # The tools it called that the reference never calls: the wrong action itself, when a refusal is the case.
        "unmatched": list(unmatched),
        "attempted": list(attempted),
    }


JUDGED_ONLY_TEXT = (
    "Kept as written: only a judge grades this case, and nothing has shown the judge fails a wrong behaviour."
)


def _harness_judge(goal: Any, world: Any, calls: list[Call], messages: list[dict[str, str]], scenario: Any) -> Any:
    """The harness judge on one sub-goal, run on its own loop so it works from sync and async callers."""
    return settled(judge_sub_goal(goal, world, calls, messages=messages, scenario=scenario))


def play_judged_controls(
    scenario: Scenario,
    catalogue: Catalogue,
    world_root: Path,
    *,
    run: StepRunner,
    judge: ControlJudge | None = None,
) -> list[ControlResult]:
    """Play each control of a judged-only case through the judge: its steps on a set-up world and its
    transcript as what was said. It fails when the judge fails a sub-goal it names. A control with no
    transcript gives the judge nothing the wrong agent said, so it is not played and reads not run."""
    decide = judge or _harness_judge
    judged = {
        name: goal
        for name in scenario.sub_goals
        if (goal := catalogue.named(name)) is not None and not goal.deterministic()
    }
    played: list[ControlResult] = []
    for control in getattr(scenario, "negative_controls", None) or []:
        entry: ControlResult = {
            "name": control.name,
            "why": control.why,
            "ran": False,
            "failed": None,
            "held": [],
            "assumed": [],
            "judged": True,
            "verdicts": {},
        }
        named = [name for name in (control.must_fail or judged) if name in judged]
        if not control.transcript or not named:
            played.append(entry)
            continue
        world, calls, _refused, _assumed = run(scenario, world_root, with_solution=True, steps=control.steps)
        try:
            for name in named:
                try:
                    held, explanation = decide(judged[name], world, calls, list(control.transcript), scenario)
                except Exception as exc:  # noqa: BLE001 - a judge that could not run decides nothing
                    held, explanation = None, f"the judge could not run: {type(exc).__name__}"
                entry["verdicts"][name] = {
                    "verdict": "pass" if held is True else "fail" if held is False else "inconclusive",
                    "explanation": str(explanation or "")[:500],
                }
        finally:
            world.close()
        verdicts = [one["verdict"] for one in entry["verdicts"].values()]
        # Judged on its transcript: it ran when the judge decided at least one sub-goal.
        entry["ran"] = any(one != "inconclusive" for one in verdicts)
        entry["held"] = [name for name, one in entry["verdicts"].items() if one["verdict"] == "pass"]
        if "fail" in verdicts:
            entry["failed"] = True
        elif verdicts and all(one == "pass" for one in verdicts):
            entry["failed"] = False
        played.append(entry)
    return played


def control_broken(controls: list[ControlResult]) -> list[str]:
    """Each check that raised under a negative control, named with the control."""
    return [
        f"{name} (under negative control {one.get('name')})"
        for one in controls
        for name in one.get("broken") or []
    ]
