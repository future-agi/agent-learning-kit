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

import ast
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field, replace
from functools import partial
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Collection, Mapping, Sequence

from .actors import load_actors
from .catalogue import Catalogue
from .checks import Outcome, reference_of, run_check
from .contract import AgentContract
from .controls import (
    JUDGED_ONLY_TEXT,
    ControlJudge,
    control_broken,
    play_controls,
    play_judged_controls,
)
from .folder import apply_setup, check_ready
from .grounding import agent_source_root, policy_conflicts, resolve_sources, unresolved
from .leaks import leaks
from .proof_records import ControlResult, LeakRecord, PolicyCheck, SourceResult
from .scenario import Scenario
from .understand import load
from .world.runtime import REQUEST_NOT_ACCEPTED, Call, GeneratedWorld, as_gone_through, record_refusal
from .world.snapshot import read_manifest, restore, source_state
from .world_door import fault_numbered, fault_slot, faults_named, route_action, routed_faults


# The lane could not execute something; the case runs labelled "kept as written", never as proved.
# "judged_only": only a judge grades the case and no control has shown the judge fails a wrong behaviour.
class BlockedReason(str, Enum):
    """Why a proof is not proved, as `blocked_reason` says it and the proof record keeps it."""

    BROKEN_CHECK = "broken_check"
    NOT_READY = "not_ready"
    NOT_SOLVABLE = "not_solvable"
    VACUOUS = "vacuous"
    WEAK_CHECK = "weak_check"
    CREDITS_FAILED_ATTEMPT = "credits_failed_attempt"
    TOO_STRICT = "too_strict"
    UNCHECKED = "unchecked"
    NO_NEGATIVE_CONTROL = "no_negative_control"
    NEGATIVE_CONTROL_PASSED = "negative_control_passed"
    CONTROL_MISSES_CASE_GOAL = "control_misses_case_goal"
    TOOL_NOT_ON_AGENT = "tool_not_on_agent"
    POLICY_CONFLICT = "policy_conflict"
    ANSWER_LEAKED = "answer_leaked"
    SOURCE_UNRESOLVED = "source_unresolved"
    PROOF_DEFERRED = "proof_deferred"
    SOLUTION_ASSUMED = "solution_assumed"
    CONTROL_NOT_RUN = "control_not_run"
    JUDGED_ONLY = "judged_only"
    VALIDATION_INCOMPLETE = "validation_incomplete"

    def __str__(self) -> str:
        return self.value


# Not proved only because this lane could not execute something: the case is kept as written.
LANE_CODES = frozenset(
    {
        BlockedReason.PROOF_DEFERRED,
        BlockedReason.SOLUTION_ASSUMED,
        BlockedReason.CONTROL_NOT_RUN,
        BlockedReason.JUDGED_ONLY,
    }
)
# A model's judgment, never proof: a case it alone holds back is kept as written with the judgment as its warning, and
# handed back to its writer in a repair round first.
JUDGED_CODES = frozenset({BlockedReason.POLICY_CONFLICT})
# A case with no negative control at all: refused at submit, kept not proved through a rebuild.
NO_NEGATIVE_CONTROL = BlockedReason.NO_NEGATIVE_CONTROL
# Refused at submit, where a case is written or edited. A case proved again as it was written (a Validate, a rebuild
# that leaves it untouched) was written before these rules existed: it stays runnable, kept as written with the reason
# as its note, so a new rule never stops an older suite from running (`Proof.as_written`).
KEPT_THROUGH_REBUILD = frozenset({BlockedReason.NO_NEGATIVE_CONTROL, BlockedReason.CONTROL_MISSES_CASE_GOAL})
# A writer's fault: the scenario is not kept. The lane codes (an unbound tool) are kept and labelled.
REFUSED_CODES = frozenset(
    {
        BlockedReason.NEGATIVE_CONTROL_PASSED,
        BlockedReason.CONTROL_MISSES_CASE_GOAL,
        BlockedReason.TOOL_NOT_ON_AGENT,
        BlockedReason.NO_NEGATIVE_CONTROL,
        BlockedReason.ANSWER_LEAKED,
        BlockedReason.SOURCE_UNRESOLVED,
    }
)


def refuses_keeping(proof: Proof) -> bool:
    """Whether a scenario with this proof may not be kept: its checks do not hold (unless only the live runtime can
    prove it), or the writer left a fault the lane caught. One rule for a submit and for every case a rebuild keeps."""
    return (not proof.holds and not (proof.deferred and not proof.broken)) or proof.blocked_reason() in REFUSED_CODES


@dataclass
class Proof:
    """Whether a scenario holds up, and what happened when it was tried."""

    deferred: bool = False
    ready: bool = False
    solvable: bool = False
    vacuous: bool = True
    why_not_ready: str = ""
    # Checks that held with nothing done. The scenario is only vacuous when *every* check does
    # that, but a single one still grades nothing, and since sub-goals are shared it will report
    # itself as held for an agent that did nothing at all. Named rather than refused: on a
    # scenario about a refusal, "no order was placed" holding on an untouched world is correct.
    weak: list[str] = field(default_factory=list)
    failed_attempts: list[str] = field(default_factory=list)
    with_solution: list[Outcome] = field(default_factory=list)
    with_nothing: list[Outcome] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    # Reference steps this scenario's own faults failed, as the run fails them (`run_steps`).
    faulted: list[str] = field(default_factory=list)
    # Tools whose reference steps were recorded, not run: they touch no world and nothing here runs them.
    recorded: list[str] = field(default_factory=list)
    broken: list[str] = field(default_factory=list)
    # Solution steps that were recorded without being executed, because the tool they name has no
    # endpoint bound in this lane. The condition was already detected and logged and then went
    # nowhere, so a scenario whose entire solution was assumed saved as "proved". Carried here so
    # whoever keeps the scenario is told what the proof did not cover.
    assumed: list[str] = field(default_factory=list)
    # World actors' actions on a clock or a chance: a proof has neither, so it never plays them.
    unplayed: list[str] = field(default_factory=list)
    # Graded by judgement alone, because the world holds nothing a check could read. True only for
    # a target we cannot see into, never a way around writing a check for a world that has one.
    judged_only: bool = False
    # Checks that fail when the agent makes its own choice for an argument the scenario does not
    # mark fixed: they hold the agent to a value nothing fixed. Each names those arguments.
    too_strict: list[str] = field(default_factory=list)
    # Values the scenario marks fixed that no code check notices when they are wrong.
    unchecked: list[str] = field(default_factory=list)
    # Checks that fail an agent whose attempt the world refused and who then made the call as the reference does. Named
    # rather than refused: the case still proves, and the writer is told to judge the successful calls.
    retried: list[str] = field(default_factory=list)
    # Each negative control played: {name, why, failed, held, assumed}. A control with assumed
    # steps did not run: its tools have no endpoint, so the world never changed.
    controls: list[ControlResult] = field(default_factory=list)
    # "no_change" when the reference leaves the world as doing nothing does: a refusal case.
    outcome: str = "change"
    # Visible text repeating grader-only material: {who, field, grader_field, excerpt}.
    leaks: list[LeakRecord] = field(default_factory=list)
    # Each cited source and whether it resolved: {kind, ref, resolved, problem, ...}.
    sources: list[SourceResult] = field(default_factory=list)
    # Each cited rule checked against the expected outcome: {rule, ref, conflict, why}.
    policy_checks: list[PolicyCheck] = field(default_factory=list)
    # What each deterministic check reads: "tool_calls" when it uses `calls`, "world_state" when it
    # uses `world`. The platform compares it with what the agent gives the harness.
    evidence: dict[str, list[str]] = field(default_factory=dict)

    @property
    def conflicts(self) -> list[PolicyCheck]:
        """Cited rules the judge found the expected outcome contradicts."""
        return [one for one in self.policy_checks if one.get("conflict") is True and not one.get("advisory")]

    @property
    def advisories(self) -> list[PolicyCheck]:
        """Rules the case does not cite that the judge found its expected outcome may contradict: advice only."""
        return [one for one in self.policy_checks if one.get("conflict") is True and one.get("advisory")]

    def advice(self) -> str:
        """The advisory conflicts in words, "" when none."""
        if not self.advisories:
            return ""
        return (
            "a model judged that this case may contradict a rule it does not cite (advice, not a hold): "
            + "; ".join(one["why"] or "contradicted" for one in self.advisories)
            + ". Read the agent's rules against the case: change the case, or cite the rule that allows it."
        )

    @property
    def ran_controls(self) -> list[ControlResult]:
        """Controls played by code to a pass or fail; one that could not be played (no checks, a
        crash) is no evidence either way."""
        return [
            one
            for one in self.controls
            if not one.get("assumed") and not one.get("judged") and one.get("failed") is not None
        ]

    @property
    def judged_controls(self) -> list[ControlResult]:
        """Controls the judge decided: played through the judge, which said fail or pass."""
        return [one for one in self.controls if one.get("judged") and one.get("failed") is not None]

    @property
    def controls_failed(self) -> bool:
        """Every control that ran failed, and at least one ran: by code, or by the judge for a judged-only case."""
        ran = self.judged_controls if self.judged_only else self.ran_controls
        return bool(ran) and all(one.get("failed") for one in ran)

    @property
    def unresolved_sources(self) -> list[SourceResult]:
        return unresolved(self.sources)
    # The reference solution's trial, for comparing tools' declared tables with what changed: the calls the world took
    # in order, the tables that ended different from the start, and why it cannot say which tool changed them.
    trial: dict[str, Any] = field(default_factory=dict)
    # Checks the reference failed only because a step it took was assumed: each reads the world alone, which an
    # assumed step never changes (`excused_by_assumed_steps`).
    excused: list[str] = field(default_factory=list)
    # The sub-goals that make this case its own, not a step every case in its suite shares (`distinctive_goals`). Set
    # only where a case is written or rebuilt, so a saved proof is never re-judged by this rule on read.
    case_goals: list[str] = field(default_factory=list)
    # Proved again as it was written, not written or edited here: a reason in `KEPT_THROUGH_REBUILD` keeps it as
    # written instead of holding it.
    as_written: bool = False
    # Tools the reference solution calls that the agent under test does not have, read from its agent version's
    # tools when they are known: the world's service may still answer them, but no agent could pass the case.
    missing_tools: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return (
            self.ready
            and self.solvable
            and not self.vacuous
            and (self.outcome == "no_change" or not self.weak)
            and not self.failed_attempts
            and not self.broken
            and not self.too_strict
            and not self.unchecked
        )

    @property
    def proved(self) -> bool:
        """Holds, and the reference solution really ran: nothing assumed, nothing deferred."""
        return (
            self.holds
            and not self.assumed
            and not self.deferred
            and self.controls_failed
            and not self.misses_case_goal
            and not self.missing_tools
            and not self.leaks
            and not self.unresolved_sources
            and not self.conflicts
        )

    @property
    def misses_case_goal(self) -> bool:
        """The controls make none of this case's own goals fail, though they were played where that could be seen.

        A goal failing under a control counts when the control ran, or when its check reads only the calls: an
        unrun step is still recorded with its arguments, so the check judged what the control would do. A check of
        the world under an unrun step shows nothing, so a goal no control could show leaves the rule undecided."""
        return misses_case_goals(self.controls, self.evidence, self.case_goals, judged_only=self.judged_only)

    @property
    def kept_as_written(self) -> bool:
        """Not proved only because this lane could not execute something, or, proved again as it was written, only for
        a rule its case predates; nothing that ran failed."""
        blocked = self.blocked_reason()
        return not self.proved and (
            blocked in LANE_CODES or blocked in JUDGED_CODES or (self.as_written and blocked in KEPT_THROUGH_REBUILD)
        )

    def blocked_reason(self) -> BlockedReason | None:
        """The first reason this proof is not proved, faults before the lane's own limits."""
        if self.proved:
            return None
        if self.missing_tools:
            return BlockedReason.TOOL_NOT_ON_AGENT
        if self.broken:
            return BlockedReason.BROKEN_CHECK
        if not self.deferred:
            # A deferred proof returns before any gate runs, so its gates read unset, not failed.
            if not self.ready:
                return BlockedReason.NOT_READY
            if not self.solvable:
                return BlockedReason.NOT_SOLVABLE
            if self.vacuous:
                return BlockedReason.VACUOUS
            if self.weak and self.outcome == "change":
                return BlockedReason.WEAK_CHECK
            if self.failed_attempts:
                return BlockedReason.CREDITS_FAILED_ATTEMPT
            if self.too_strict:
                return BlockedReason.TOO_STRICT
            if self.unchecked:
                return BlockedReason.UNCHECKED
            if not self.judged_only:
                if not self.controls:
                    return NO_NEGATIVE_CONTROL
                if any(one.get("failed") is False for one in self.ran_controls):
                    return BlockedReason.NEGATIVE_CONTROL_PASSED
                if self.misses_case_goal:
                    return BlockedReason.CONTROL_MISSES_CASE_GOAL
            elif any(one.get("failed") is False for one in self.judged_controls):
                return BlockedReason.NEGATIVE_CONTROL_PASSED
        if self.conflicts:
            return BlockedReason.POLICY_CONFLICT
        if self.leaks:
            return BlockedReason.ANSWER_LEAKED
        if self.unresolved_sources:
            return BlockedReason.SOURCE_UNRESOLVED
        if self.deferred:
            return BlockedReason.PROOF_DEFERRED
        if self.assumed:
            return BlockedReason.SOLUTION_ASSUMED
        if self.controls and not self.ran_controls and not self.judged_only:
            return BlockedReason.CONTROL_NOT_RUN
        if self.judged_only and not self.controls_failed:
            return BlockedReason.JUDGED_ONLY
        return None

    def gates(self) -> dict[str, bool]:
        """The three answers, for anything that wants to show them."""
        return {
            "ready": self.ready,
            "solvable": self.solvable,
            "not_vacuous": not self.vacuous and not self.failed_attempts,
        }

    def _source_faults(self) -> str:
        """What the case's own text and sources got wrong, checked before any gate: a contradicted rule, a leaked
        answer, a source that does not resolve; "" when none."""
        if self.conflicts:
            return (
                "a model judged that its expected outcome may contradict a cited rule (a judgment, not a proof):\n  - "
                + "\n  - ".join(
                    f"possible conflict with {one['ref'] or 'rule'} \"{one['rule']}\": {one['why'] or 'contradicted'}"
                    for one in self.conflicts
                )
                + "\n\nRead the rule against the case. If the case does what the rule says (the rule's worker does the "
                "work, or the rule does not apply here), make that plain in the case or cite the rule it follows; if "
                "not, change the case. Left as it is, the case is kept as written with this warning."
            )
        if self.leaks:
            return "what the simulated person or the agent sees gives the answer away:\n  - " + "\n  - ".join(
                f"{one['who']}: {one['field']} repeats \"{one['excerpt']}\" from {one['grader_field']}"
                for one in self.leaks
            )
        if self.unresolved_sources:
            return "a cited source does not resolve:\n  - " + "\n  - ".join(
                f"{one['kind']} {one['ref']}: {one['problem']}" for one in self.unresolved_sources
            )
        return ""

    def why(self) -> str:
        """What to fix, in the order worth fixing it."""
        if self.missing_tools:
            return (
                "the reference solution calls tools this agent version does not have, so no agent could pass it:\n  - "
                + "\n  - ".join(self.missing_tools)
                + "\n\nRewrite the case around what this agent can do, or drop it."
            )
        if self.deferred and not self.broken and not self.leaks and not self.unresolved_sources and not self.conflicts:
            return "DEFERRED: setup, readiness and outcome checks require the live runtime; file access declarations do not verify fixture contents."
        if self.deferred and not self.broken:
            # A deferred proof returns before any gate runs, so its gates read unset, never failed (`blocked_reason`).
            return self._source_faults()
        if self.broken and not self.why_not_ready:
            return "these checks are broken, not failing:\n  - " + "\n  - ".join(self.broken)
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
            faulted = (
                "\n\nThis scenario's own faults fail these steps, as they will in the run:\n  - "
                + "\n  - ".join(self.faulted)
                + "\n\nA case that injects a fault tests the failure path, never the change succeeding: write its "
                "reference and its checks for what a good agent does when the step fails (it reports the failure "
                "honestly, claims nothing it did not do, and may retry where a retry is safe), and check that the "
                "change did not happen."
                if self.faulted
                else ""
            )
            return (
                "the reference solution does not pass this scenario's own checks, so either the "
                "scenario cannot be passed or the checks are wrong:\n  - "
                + said
                + refusals
                + faulted
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
        if self.unchecked:
            return (
                "these values are marked fixed, yet every check still passes when the agent gets "
                "them wrong:\n  - "
                + "\n  - ".join(self.unchecked)
                + "\n\nA value is fixed so that something checks it. Fix the check that should "
                "compare it, or take it out of `fixed` if only what is said settles it or the agent words it "
                "itself (a reason, a note, a message), which no check compares as text."
            )
        if self.too_strict:
            return (
                "these checks fail an agent that does every fixed thing right but makes its own "
                "choice, for an argument this scenario does not mark fixed, a way its "
                "instructions allow, or a fixed value written the way the person said it:\n  - "
                + "\n  - ".join(self.too_strict)
                + "\n\nA check may hold the agent only to what the caller, their record or a "
                "rule of the agent fixes. Check the outcome instead, or mark the argument fixed "
                "if one of those really fixes it."
            )
        if self.failed_attempts:
            return (
                "these checks award credit when every tool attempt crashes without changing "
                "the world or returning a result:\n  - "
                + "\n  - ".join(self.failed_attempts)
                + "\n\nArguments and a tool name are not evidence of success. Require a "
                "successful outcome or the corresponding world effect. For a refusal or a not-found, "
                "require the world's own answer, not an arbitrary execution error: read the call with "
                "`answered(calls, tool, ...)`, which counts a not-found or a refusal and never a crash."
            )
        if self.weak and self.outcome == "change":
            return (
                "these individual checks pass without the agent doing anything, so keeping them "
                "would display credit unsupported by evidence:\n  - "
                + "\n  - ".join(self.weak)
                + "\n\nMake each check assert the relevant attempt or observation as well as the "
                "final state. For a refusal, require that the call was attempted and refused; "
                "an untouched world is not evidence that the agent refused correctly."
            )
        if self.controls:
            passed = [
                one
                for one in (self.judged_controls if self.judged_only else self.ran_controls)
                if one.get("failed") is False
            ]
            if passed:
                unmatched = any(one.get("unmatched") for one in passed)
                attempted = any(one.get("attempted") for one in passed)
                return "the checks pass a wrong behaviour, so they cannot tell it from the right one:\n  - " + "\n  - ".join(
                    f"{one['name']} ({one['why']}): held {', '.join(one.get('held') or []) or 'every check'}"
                    + (
                        f"; it called {', '.join(one['unmatched'])}, which the reference never calls, and no check "
                        "fails on that"
                        if one.get("unmatched")
                        else ""
                    )
                    + (
                        f"; it tried {', '.join(one['attempted'])}, which the reference never calls; the world refused "
                        "it, so nothing changed, and no check fails on the attempt"
                        if one.get("attempted")
                        else ""
                    )
                    + (
                        f"; it committed {', '.join(one['changed'])}, which the reference never does, and no check "
                        "reads that record. A check that fails when that write commits (`committed_writes(world)`, "
                        "or the record's end state in `world`) fails this control"
                        if one.get("changed")
                        else ""
                    )
                    for one in passed
                ) + (
                    "\n\nA check on that call's effect catches it: give a sub-goal of this case a check that fails "
                    "when that call commits for this caller's record (read `committed_writes(world, reference)` or the "
                    "record it changes). A refusal left to the judge alone cannot fail a control."
                    if unmatched
                    else ""
                ) + (
                    "\n\nWhere the world itself refuses the wrong action, nothing commits, so a check on its effect "
                    "holds either way: give a sub-goal of this case a check that fails when the agent attempts that "
                    "call for this caller's record (read `calls`, whether or not it went through)."
                    if attempted
                    else ""
                ) + ("" if unmatched or attempted else "\n\nTighten the named checks so this wrong behaviour fails them.")
        if self.misses_case_goal and not self.deferred:
            return (
                "the negative controls only make checks fail that every scenario in this suite shares, so they show "
                "nothing about what this scenario is for. Write a control that is the most plausible wrong behaviour "
                "for this case, the mistake its own check exists to catch, so that at least one of these fails:\n  - "
                + "\n  - ".join(self.case_goals)
                + "\n\nWhen what this case is about is a value only it fixes (a corrected number, a changed date) and "
                "the check that reads it is one every case shares, add a sub-goal for that value with add_sub_goal, "
                "name it on this case, and let the control fail it."
            )
        if not self.controls and not self.judged_only and not self.deferred:
            return (
                "no negative control: add a wrong behaviour written for this task (for example the "
                "wrong record, the action done twice, or the forbidden change) that its checks must fail."
            )
        if fault := self._source_faults():
            return fault
        # The lane's own limits, in `blocked_reason`'s order.
        if self.assumed:
            return "kept as written: these tools have no endpoint here, so their solution steps never ran: " + ", ".join(
                self.assumed
            )
        if self.controls and not self.ran_controls and not self.judged_only:
            return "no negative control could run here: " + ", ".join(
                f"{one['name']} ({', '.join(one.get('assumed') or [])})" for one in self.controls
            )
        if self.judged_only and not self.controls_failed:
            return JUDGED_ONLY_TEXT
        return "holds"


def misses_case_goals(
    controls: Sequence[Mapping[str, Any]],
    evidence: Mapping[str, Any],
    case_goals: Sequence[str],
    *,
    judged_only: bool = False,
) -> bool:
    """`Proof.misses_case_goal` on a proof's parts, so a saved proof entry is judged by the same rule."""
    if not case_goals or judged_only or not controls:
        return False
    shown: set[str] = set()
    for one in controls:
        if one.get("judged") or "failing" not in one:
            continue
        for goal in case_goals:
            if one.get("ran") or evidence.get(goal) == ["tool_calls"]:
                shown.add(goal)
                if goal in (one.get("failing") or []):
                    return False
    return shown == set(case_goals)


# Below this many scenarios, a sub-goal every scenario names may be what they are all about, not a shared step.
SHARED_GOAL_SUITE_SIZE = 3


def shared_goals(suite: Sequence[Scenario]) -> set[str] | None:
    """The sub-goals every scenario of the suite names; None for a suite under `SHARED_GOAL_SUITE_SIZE`."""
    if len(suite) < SHARED_GOAL_SUITE_SIZE:
        return None
    return set.intersection(*(set(getattr(one, "sub_goals", None) or []) for one in suite))


def distinctive_goals(
    scenario: Scenario, suite: Sequence[Scenario], shared: set[str] | None = None
) -> list[str]:
    """This scenario's sub-goals that are not named by every scenario of its suite (``suite`` includes it). Empty for
    a suite under `SHARED_GOAL_SUITE_SIZE`: there a goal they all name may be the one they are all about. `shared`,
    the suite's `shared_goals` read once, spares reading the whole suite again for each case."""
    shared = shared_goals(suite) if shared is None else shared
    if shared is None:
        return []
    return [goal for goal in scenario.sub_goals if goal not in shared]


def excused_by_assumed_steps(proof: Proof) -> list[str]:
    """The checks the reference failed that are no evidence, because a step was assumed: an assumed step never ran,
    so the world never changed, and a check that reads only the world cannot pass. A check that reads the calls is
    evidence either way, and with nothing assumed every failure counts."""
    if not proof.assumed:
        return []
    return sorted(
        one.name
        for one in proof.with_solution
        if not one.held and not one.broken and proof.evidence.get(one.name) == ["world_state"]
    )


def inconclusive_on_assumed(
    checks: Sequence[tuple[str, str]],
    world: Any,
    calls: Sequence[Call],
    reference: Any,
    assumed: Collection[str],
    failed: Collection[str],
) -> list[str]:
    """Of the `failed` checks, those that failed only because an `assumed` step's outcome is unknown: each holds when
    those steps are read as gone through. No evidence either way, so the case is kept as written and proved live,
    never refused for it. A check that fails even then is the reference's own failure."""
    if not assumed or not failed:
        return []
    held_calls = list(getattr(world, "calls", []))
    world.calls = as_gone_through(held_calls, assumed)
    try:
        through = as_gone_through(calls, assumed)
        return sorted(
            name
            for name, source in checks
            if name in failed and run_check(source, world, through, name=name, reference=reference).held
        )
    finally:
        world.calls = held_calls


def check_evidence(source: str) -> list[str]:
    """What a check's source reads: "tool_calls" if it uses its `calls` argument, "world_state" if it
    uses `world`. An unparsable check reads nothing."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    function = next(
        (node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "check"),
        None,
    )
    if function is None:
        return []
    params = [arg.arg for arg in function.args.args]
    names = {node.id for node in ast.walk(function) if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
    found = []
    if len(params) > 1 and params[1] in names:
        found.append("tool_calls")
    if params and params[0] in names:
        found.append("world_state")
    return found


def checks_for(scenario: Scenario, catalogue: Catalogue) -> list[tuple[str, str]]:
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
    if native_runtime(world_root):
        raise RuntimeError("native_fixture_proof_deferred_to_live_runtime")
    if workspace_fixture(scenario, Catalogue()):
        raise RuntimeError("workspace_fixture_proof_deferred_to_live_runtime")
    world = restore(world_root)
    world.reset()
    applied = apply_setup(scenario, world)
    ready = check_ready(scenario, world) if applied.ok else Outcome(False, applied.said)
    # The setup's own calls are not the agent's. Clearing them keeps a check that counts calls
    # from crediting the agent with work the scenario did on its behalf.
    world.calls = []
    return world, applied, ready


logger = logging.getLogger(__name__)


def executes(world: GeneratedWorld, tool: str) -> bool:
    """Whether a reference step for `tool` really runs here, rather than being recorded unrun."""
    if tool not in set(getattr(world, "runtime_tools", set())):
        return True
    return bool((getattr(world, "endpoint_for", {}) or {}).get(tool))


# What a recorded step is called wherever a proof shows it: never run, never shown as executed.
RECORDED_NOT_RUN = "recorded, not run (no world effect)"


def not_run(name: str, arguments: Mapping[str, Any]) -> Call:
    """A call made without running, so nothing says it went through or was turned down: no check of success can
    pass on it, and none of refusal either."""
    return Call(
        name=name,
        arguments=dict(arguments),
        result=None,
        ok=None,  # type: ignore[arg-type]
        refused=None,  # type: ignore[arg-type]
        refusal_code=None,  # type: ignore[arg-type]
        error=None,  # type: ignore[arg-type]
    )


def record_unrun_step(world: Any, step: object) -> Call:
    """Record a step of a tool that touches no world as the call the agent makes, with its arguments and no result,
    without running it: what a check of the calls reads, and nothing a check of the world could. Listed in
    `world.recorded`."""
    tool = str(getattr(step, "tool", "") or "")
    call = not_run(tool, dict(getattr(step, "arguments", {}) or {}))
    record = getattr(world, "_record", None)
    call = record(call) if callable(record) else (world.calls.append(call) or call)
    world.recorded = sorted({*getattr(world, "recorded", ()), tool})
    return call


# What a call came to, by the names a check reads it with: on a call, on a log row, or through a helper.
_OUTCOME_FIELDS = frozenset({"result", "ok", "refused", "error", "refusal_code", "outcome"})
_OUTCOME_HELPERS = frozenset({"succeeded", "answered", "committed_writes", "retried_timeouts", "repeated_commits"})


def _reads_results(source: str) -> bool:
    """Whether a check's code reads what any call came to: its result, whether it went through or was turned down
    (`c.ok`, `c.refused`, `c.error`, a log row's fields of the same names, `world.events`), or a helper that reads them
    (`succeeded`, `answered`, ...). Unreadable code is taken to read one."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return True
    return any(
        (isinstance(node, ast.Attribute) and (node.attr in _OUTCOME_FIELDS or node.attr == "events"))
        or (isinstance(node, ast.Constant) and node.value in _OUTCOME_FIELDS)
        or (isinstance(node, ast.Name) and node.id in _OUTCOME_HELPERS)
        for node in ast.walk(tree)
    )


def _takes_from(step: object) -> set[str]:
    """The tools whose results this step's values are taken from (`$call.<tool>.<field>`)."""
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value.startswith("$call.") and len(value.split(".")) >= 3:
            found.add(value.split(".")[1])

    walk(dict(getattr(step, "arguments", {}) or {}))
    walk(dict(getattr(step, "environment_arguments", {}) or {}))
    return found


def recordable_tools(scenario: Scenario, contract: Any, checks: Sequence[tuple[str, str]]) -> frozenset[str]:
    """The tools whose steps a proof records, not runs, where nothing here can run them: each one the contract
    declares `world_free` (it touches no table of the world and no service), when no step of the case takes a value
    from its result and no check of the case reads any call's result. Anything else stays assumed. A tool the contract
    also maps to tables it reads or changes touches the world, whatever `world_free` says, so it is never recorded."""
    free = {
        tool.name
        for tool in getattr(contract, "tools", None) or ()
        if getattr(tool, "world_free", False)
        and not getattr(tool, "tables_read", None)
        and not getattr(tool, "tables_changed", None)
    }
    if not free or any(_reads_results(source) for _, source in checks):
        return frozenset()
    steps = [
        *(getattr(scenario, "solution", None) or ()),
        *(step for control in getattr(scenario, "negative_controls", None) or () for step in control.steps),
    ]
    taken = {tool for step in steps for tool in _takes_from(step)}
    return frozenset(free - taken)


def assumed_tools(world: GeneratedWorld, steps: Sequence[Any]) -> list[str]:
    """The tools of `steps` recorded unrun: none bound here, or a request the service would not take as sent
    (`play_reference_step`)."""
    unplayed = set(getattr(world, "not_replayed", ()))
    return sorted(
        {
            str(getattr(step, "tool", ""))
            for step in steps
            if not executes(world, str(getattr(step, "tool", ""))) or str(getattr(step, "tool", "")) in unplayed
        }
    )


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
        if not environment_arguments and _only_fields_the_agent_adds(effect, arguments):
            # The step names no request for the service, so it was sent the agent-facing arguments. The service
            # asked only for fields those arguments do not have: the agent's own tool adds them from the
            # conversation. The step never ran as the agent runs it, so it is recorded unrun, not refused.
            logger.warning("reference step assumed rather than executed: tool=%s reason=request not accepted", name)
            world.not_replayed = {*getattr(world, "not_replayed", ()), name}
            world.calls.append(not_run(name, arguments))
            return world.calls[-1]
        semantic = Call(
            name=name,
            arguments=arguments,
            result=effect.result,
            ok=effect.ok,
            error=effect.error,
            refused=effect.refused,
            at=effect.at,
        )
    elif (refused := record_refusal(world, name, arguments)) is not None:
        # Nothing here runs the tool, but the contract records that its service refuses this record (a fault the
        # record carries in the agent's own data): answered as the service answers it live, never assumed to succeed.
        # The step was decided here, so it is not counted among the assumed ones.
        world.answered_by_record.append(step)
        semantic = refused
    else:
        # Reaching here means the tool is a declared runtime tool with nothing bound to call.
        # Two very different situations arrive at the same place: a local orchestration action
        # that genuinely has no environment effect, and a lane where the runtime is built after
        # authoring so no endpoint exists yet. The second cannot be proved, and recording it as
        # a pass is what lets a scenario be kept on a solution nothing ever executed. Say so.
        logger.warning(
            "reference step assumed rather than executed: tool=%s reason=%s. It is recorded with no "
            "result and neither ok nor refused, so this step proves nothing about the world.",
            name,
            "no endpoint bound" if not endpoint else "no forwarder on the world",
        )
        semantic = not_run(name, arguments)
    world.calls.append(semantic)
    return semantic


# How a validation error names a missing field: `missing` in current FastAPI and pydantic, `value_error.missing`
# in older ones.
_MISSING_FIELD_TYPES = frozenset({"missing", "value_error.missing"})


def _only_fields_the_agent_adds(effect: Call, arguments: Mapping[str, Any]) -> bool:
    """Whether the service refused `effect` only for missing fields: a request-shape refusal
    (`REQUEST_NOT_ACCEPTED`) whose body is a validation error listing at least one error, every one of type
    missing, for a field the step's own arguments do not hold. A wrong value, an unknown choice, a refusal of the
    action itself or a body that cannot be read is the reference's own, and stays refused."""
    if effect.refusal_code != REQUEST_NOT_ACCEPTED:
        return False
    body = effect.result
    if not isinstance(body, dict):
        try:
            body = json.loads(effect.error)
        except (TypeError, ValueError):
            return False
    errors = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(errors, list) or not errors:
        return False
    for one in errors:
        where = one.get("loc") if isinstance(one, dict) else None
        if not isinstance(where, list) or not where or one.get("type") not in _MISSING_FIELD_TYPES:
            return False
        if str(where[-1]) in arguments:
            return False
    return True


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


def run_steps(
    scenario: Scenario,
    world_root: Path,
    *,
    with_solution: bool,
    steps: Sequence[Any] | None = None,
    recordable: Collection[str] = (),
) -> tuple[GeneratedWorld, list[Call], list[str], list[str]]:
    """A world set up for this scenario, optionally with the solution (or `steps`) played through it: the world, the
    agent's calls, the steps the world refused and the tools assumed (not executed here). A step of a `recordable`
    tool that nothing here runs is recorded as made, not run, and listed in `world.recorded`, never as executed. The
    caller closes the world."""
    world, _applied, _ready = prepared(scenario, world_root)
    world.answered_by_record = []
    refused: list[str] = []
    assumed: list[str] = []
    by_actors: dict[int, str] = {}
    acting = _WorldActors(scenario, world_root, world, by_actors)
    # A check reads the trial's action log the way the run gives it: `world.events(actor_id, outcome)`.
    world.events = acting.events
    # The conversations before the run are in the world when the agent starts, in the proof as in the run, and
    # they are the actors' own: a check never credits the agent with a message an actor wrote.
    acting.replay_pre_run()
    if with_solution:
        acting.act_on(None)
        faults = _Faults(scenario, world_root)
        world.faulted = []
        world.recorded = []
        for step in scenario.solution if steps is None else steps:
            fault = faults.on(step)
            tool = str(getattr(step, "tool", "") or "")
            unrun = fault is None and tool in recordable and not executes(world, tool)
            if unrun:
                call = record_unrun_step(world, step)
            elif fault is None:
                call = play_reference_step(world, step)
            else:
                # The scenario's own fault fails this step before any change, as the world door fails it in the run,
                # with the reason the door logs.
                mode = str(fault.get("mode"))
                reason = (str(fault.get("message") or "").strip() or "error") if mode == "error" else mode
                call = world._record(
                    Call(name=str(step.tool), arguments=dict(step.arguments or {}), ok=False, error=reason)
                )
                world.faulted.append(f"{call.name}({step.arguments}): {mode}")
            if call.ok is False and not unrun:
                refused.append(f"{call.name}({step.arguments}): {call.error}")
            acting.act_on(step)
        answered = world.answered_by_record
        played = scenario.solution if steps is None else steps
        assumed = [
            tool
            for tool in assumed_tools(world, [one for one in played if not any(one is it for it in answered)])
            if tool not in world.recorded
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
    calls = [call for index, call in enumerate(world.calls) if index not in by_actors]
    return world, calls, refused, sorted(set(assumed))


# The fault modes that fail a call before any change. `timeout_after_commit` makes the change (its answer is lost) and
# `approval_required` holds the call for an approver; the proof plays both as written.
_FAILING_FAULTS = ("timeout_before_commit", "unavailable", "error")


class _Faults:
    """A scenario's own faults on its reference steps, matched and counted the way the world door matches and counts
    them in the run (`faults_named`, `fault_slot`, `fault_numbered`): each step as the call it makes, its world action
    the tool's route where the contract gives one, and each fault renamed to that route (`routed_faults`)."""

    def __init__(self, scenario: Scenario, world_root: Path) -> None:
        contract = load_proof_contract(world_root)
        faults = [one.model_dump() if hasattr(one, "model_dump") else dict(one) for one in getattr(scenario, "faults", None) or ()]
        self.faults = routed_faults(faults, contract) if faults else []
        self.routes: dict[str, tuple[str, str]] = {}
        for entry in getattr(contract, "tool_entrypoints", None) or ():
            entry = entry if isinstance(entry, Mapping) else entry.model_dump()
            route = route_action(str(entry.get("endpoint") or ""))
            if entry.get("tool") and route:
                self.routes[str(entry["tool"])] = (route, str(entry.get("method") or "POST").upper())
        self.made: Counter[str] = Counter()

    def on(self, step: object) -> Any:
        """The fault the run sets on this step, by which call of its slot it is; None when none fails it."""
        if not self.faults:
            return None
        tool = str(getattr(step, "tool", "") or "")
        routed = tool in self.routes
        action, method = self.routes.get(tool, (tool, ""))
        named = faults_named(
            self.faults, action=action, name=action.rsplit(".", 1)[-1], tool=tool, method=method, by_tool=not routed
        )
        named = [one for one in named if one.get("mode") != "approval_required"]
        if not named:
            return None
        counted, by_method = fault_slot(named, action, method)
        slot = f"{counted}\x00{by_method}" if by_method is not None else counted
        fault = fault_numbered(named, self.made[slot])
        self.made[slot] += 1
        return fault if fault is not None and fault.get("mode") in _FAILING_FAULTS else None


# The triggers a reference solution can meet; the rest are reported in `Proof.unplayed`.
_PLAYED_TRIGGERS = ("always", "on_tool", "contacted")


def _unplayed(scenario: Scenario) -> list[str]:
    return [
        f"{entry.id} {action.action} ({action.trigger.kind})"
        for entry in getattr(scenario, "actors", None) or ()
        for action in entry.actions
        if action.trigger.kind not in _PLAYED_TRIGGERS
    ]


class _WorldActors:
    """A scenario's world actors during a proof: each permitted action on a trigger the reference solution meets,
    played the way the run plays it at the world's door. None of their calls is the agent's."""

    def __init__(
        self,
        scenario: Scenario,
        world_root: Path,
        world: GeneratedWorld,
        by_actors: dict[int, str],
    ) -> None:
        listed = load_actors(world_root).by_id()
        self.scenario = scenario
        self.pending = [
            (listed[entry.id], action)
            for entry in getattr(scenario, "actors", None) or ()
            if entry.id in listed
            for action in entry.actions
        ]
        self.world = world
        self.by_actors = by_actors
        self.counts: dict[str, int] = {}
        # Actions refused before the world, each with how many world calls came before it.
        self.refusals: list[tuple[int, dict[str, Any]]] = []

    def events(
        self, actor_id: str | None = None, outcome: str | None = None
    ) -> list[dict[str, Any]]:
        """Every attempted action so far, whose it was and what came of it, as `HostedWorld.events` gives them."""
        rows: list[dict[str, Any]] = []
        pending = list(self.refusals)
        for index, call in enumerate(self.world.calls):
            while pending and pending[0][0] <= index:
                rows.append(pending.pop(0)[1])
            rows.append(
                {
                    "name": call.name,
                    "arguments": call.arguments,
                    "result": call.result,
                    "ok": call.ok,
                    # As the live log keeps them, so a check's helpers read the same columns here as in a trial.
                    "error": call.error or None,
                    "action_id": f"proof-{index}",
                    "actor_id": self.by_actors.get(index, "agent"),
                    # A step recorded, not run (`ok` None), neither went through nor was turned down.
                    "outcome": "recorded" if call.ok is None else "committed" if call.ok else "refused",
                    "refusal_code": None if call.ok or call.ok is None else "world_rule",
                }
            )
        rows += [row for _, row in pending]
        return [
            row
            for row in rows
            if (actor_id is None or row["actor_id"] == actor_id)
            and (outcome is None or row["outcome"] == outcome)
        ]

    def _due(self, actor: Any, action: Any, step: Any) -> bool:
        trigger = action.trigger
        if step is None:
            return trigger.kind == "always"
        if trigger.kind == "on_tool":
            return (
                trigger.tool == step.tool
                and self.counts.get(step.tool) == trigger.on_call
            )
        if trigger.kind == "contacted":
            from .actor_dispatch import contacts

            return contacts(step.arguments, actor)
        return False

    def replay_pre_run(self) -> None:
        """Play each stored pre-run thread into the world, message by message, as its actor; a reply takes the id
        the world gave the message it answers. A session not yet written has nothing to replay."""
        from .actor_dispatch import result_field

        for session in getattr(self.scenario, "pre_run", None) or ():
            results: list[Any] = []
            for message in session.thread:
                arguments = dict(message.arguments)
                for name, ref in message.refers.items():
                    earlier = results[ref.message - 1] if ref.message <= len(results) else None
                    value = result_field(earlier, ref.field)
                    if value is not None:
                        arguments[name] = value
                before = len(self.world.calls)
                route = message.action.replace(".", "/")
                known = route in getattr(self.world, "endpoints", ())
                self.world.call(route if known else message.action.rsplit(".", 1)[-1], arguments)
                made = self.world.calls[before:]
                results.append(made[-1].result if made else None)
                self.by_actors.update(dict.fromkeys(range(before, len(self.world.calls)), message.actor))

    def act_on(self, step: Any) -> None:
        if step is not None:
            self.counts[step.tool] = self.counts.get(step.tool, 0) + 1
        for actor, action in list(self.pending):
            if not self._due(actor, action, step):
                continue
            self.pending.remove((actor, action))
            if action.action not in actor.permitted_actions:
                refusal = {
                    "name": action.action.rsplit(".", 1)[-1],
                    "arguments": dict(action.arguments),
                    "result": None,
                    "ok": False,
                    "actor_id": actor.id,
                    "outcome": "refused",
                    "refusal_code": "not_permitted",
                }
                self.refusals.append((len(self.world.calls), refusal))
                continue
            before = len(self.world.calls)
            # A source-hosted world knows the action by its route; a generated one by its name.
            route = action.action.replace(".", "/")
            known = route in getattr(self.world, "endpoints", ())
            self.world.call(
                route if known else action.action.rsplit(".", 1)[-1],
                dict(action.arguments),
            )
            self.by_actors.update(
                dict.fromkeys(range(before, len(self.world.calls)), actor.id)
            )


def native_runtime(world_root: Path) -> bool:
    try:
        return bool(read_manifest(world_root).get("native_runtime", False))
    except FileNotFoundError:
        return False


def workspace_bindings(world_root: Path) -> dict[str, str]:
    contract = load(world_root)
    return dict(contract.runtime.workspaces) if contract and contract.runtime else {}


def _file_calls(tree: ast.AST) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "world"
        and node.func.attr in {"write_file", "read_file"}
    ]


def workspace_fixture(scenario: Scenario, catalogue: Catalogue) -> bool:
    sources = [scenario.setup_code, scenario.ready_code]
    sources.extend(source for _, source in checks_for(scenario, catalogue))
    for source in sources:
        try:
            if _file_calls(ast.parse(source)):
                return True
        except SyntaxError:
            continue
    return False


def workspace_fixture_problems(
    scenario: Scenario, catalogue: Catalogue, bindings: dict[str, str]
) -> list[str]:
    """Check fixture structure; only live readiness can verify target file contents."""
    if not bindings:
        return ["file fixtures require a declared runtime.workspaces binding"]
    from .world.errors import WorldUsageError
    from .world.files import relative_parts

    writes = False
    reads = False
    problems = []
    sources = [("setup", scenario.setup_code), ("ready", scenario.ready_code)]
    sources.extend(("check", source) for _, source in checks_for(scenario, catalogue))
    for entry, source in sources:
        if not source.strip():
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            problems.append(f"{entry} syntax error: {exc.msg}")
            continue
        if not any(
            isinstance(node, ast.FunctionDef) and node.name == entry
            for node in tree.body
        ):
            problems.append(f"missing {entry} function")
        for node in _file_calls(tree):
            writes |= entry == "setup" and node.func.attr == "write_file"
            reads |= entry == "ready" and node.func.attr == "read_file"
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                try:
                    relative_parts(node.args[1].value)
                except WorldUsageError as exc:
                    problems.append(str(exc))
            if node.args and isinstance(node.args[0], ast.Constant):
                name = node.args[0].value
                if name not in bindings:
                    problems.append(
                        f"workspace {name!r} is not declared in runtime.workspaces"
                    )
    if writes and not reads:
        problems.append(
            "ready(world) must read back seeded files and verify their contents in the live runtime"
        )
    return problems


def native_source_problem(source: str, entry: str) -> str:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return f"{entry} syntax error: {exc.msg}"
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == entry
        for node in tree.body
    ):
        return f"missing {entry} function"
    forbidden = {
        "state",
        "query",
        "put",
        "change",
        "drop",
        "call",
        "read_file",
        "write_file",
        "checkpoint",
        "restore",
        "handle_tool_call",
    }
    if any(
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "world"
        and node.attr in forbidden
        for node in ast.walk(tree)
    ):
        return "native fixtures/checks must use actual API responses or native call observations, not shadow state"
    return ""


def _declared_options(world_root: Path) -> set[str]:
    """`tool.argument` names whose values the agent's own tool definitions limit to a set."""
    try:
        tools = json.loads((Path(world_root) / "contract.json").read_text(encoding="utf-8")).get("tools") or []
    except (OSError, ValueError):
        return set()
    return {
        f"{tool.get('name')}.{name}"
        for tool in tools
        for name, values in (tool.get("arg_values") or {}).items()
        if values
    }


def _with_value(step: Any, name: str, value: Any) -> Any:
    """The step with one argument changed where it is executed too, or None when what is executed
    does not carry that argument, so changing it could not change what the step does."""
    update: dict[str, Any] = {"arguments": {**step.arguments, name: value}}
    if step.environment_arguments:
        if step.environment_arguments.get(name) != step.arguments.get(name):
            return None
        update["environment_arguments"] = {**step.environment_arguments, name: value}
    return step.model_copy(update=update)


def _end_state(world: GeneratedWorld) -> dict[str, Any] | None:
    """The world's own state, without the harness's tables a read also writes to."""
    try:
        return source_state(world.state())
    except Exception:  # noqa: BLE001 - an unreadable state compares as different, never as equal
        return None


def _other_choices(scenario: Scenario, declared: set[str] = frozenset()) -> tuple[list[Any], set[str]]:
    """The solution with every free text argument the scenario does not mark fixed set otherwise."""
    steps = list(getattr(scenario, "solution", None) or ())
    if any(step.fixed is None for step in steps):
        return steps, set()
    varied, changed = [], set()
    for step in steps:
        for name, value in step.arguments.items():
            free = name not in step.fixed and f"{step.tool}.{name}" not in declared
            if free and isinstance(value, str) and value and not value.startswith("$"):
                other = _with_value(step, name, f"another {name}")
                if other is not None:
                    step = other
                    changed.add(f"{step.tool}.{name}")
        varied.append(step)
    return varied, changed


_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# Languages whose spoken date form this proof writes ("" = not stated, the platform's default language).
_SPOKEN_DATE_LANGUAGES = frozenset({"", "english", "en"})
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")


def _conversation_language(scenario: Scenario) -> str:
    persona = getattr(scenario, "persona", None)
    languages = (
        persona.get("languages") if isinstance(persona, dict) else getattr(persona, "languages", None)
    ) or []
    return str(languages[0]).strip().casefold() if languages else ""


def _said_another_way(scenario: Scenario) -> tuple[list[Any], set[str]]:
    """The solution with every fixed date written the way a person says it in the conversation's language: the same
    value, in the form it reaches an agent that heard it. Only the spoken form of languages this proof knows is
    replayed; for any other language this replay is skipped (no date is said another way), never guessed."""
    if _conversation_language(scenario) not in _SPOKEN_DATE_LANGUAGES:
        return list(getattr(scenario, "solution", None) or ()), set()
    steps = list(getattr(scenario, "solution", None) or ())
    said, changed = [], set()
    for step in steps:
        for name in step.fixed or ():
            value = step.arguments.get(name)
            found = _ISO_DATE.match(value) if isinstance(value, str) else None
            if found and 1 <= int(found.group(2)) <= 12:
                spoken = f"{int(found.group(3))} {_MONTHS[int(found.group(2)) - 1]} {found.group(1)}"
                other = _with_value(step, name, spoken)
                if other is not None:
                    step = other
                    changed.add(f"{step.tool}.{name}")
        said.append(step)
    return said, changed


def _bare_ids(scenario: Scenario) -> list[tuple[str, list[Any]]]:
    """For each fixed id written with a kind prefix (`saved_card:pm_1`), the solution with that id alone (`pm_1`)."""
    steps = list(getattr(scenario, "solution", None) or ())
    out = []
    for at, step in enumerate(steps):
        for name in step.fixed or ():
            value = step.arguments.get(name)
            if isinstance(value, str) and not value.startswith("$") and ":" in value:
                bare = value.rsplit(":", 1)[1].strip()
                changed = _with_value(step, name, bare) if bare else None
                if changed is not None:
                    out.append((f"{step.tool}.{name}", [*steps[:at], changed, *steps[at + 1 :]]))
    return out


_SEPARATED_ID = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9]+(?:[-_. ][A-Za-z0-9]+)+$")


def _unseparated_ids(scenario: Scenario) -> list[tuple[str, list[Any]]]:
    """For each fixed id written with separators (`<PREFIX>-<digits>`), the solution with it typed without them
    (`<PREFIX><digits>`), as an agent often says it."""
    steps = list(getattr(scenario, "solution", None) or ())
    out = []
    for at, step in enumerate(steps):
        for name in step.fixed or ():
            value = step.arguments.get(name)
            if isinstance(value, str) and not value.startswith("$") and _SEPARATED_ID.match(value):
                changed = _with_value(step, name, re.sub(r"[-_. ]", "", value))
                if changed is not None:
                    out.append((f"{step.tool}.{name}", [*steps[:at], changed, *steps[at + 1 :]]))
    return out


def _other_value(value: Any) -> Any:
    """Another value of the same kind, or None when there is none to make."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, (int, float)):
        return value + 1
    if isinstance(value, str) and value and not value.startswith("$"):
        return f"{value}-other"
    return None


def _corrected_attempts(scenario: Scenario) -> list[tuple[str, list[Any]]]:
    """For each fixed value, the solution with that step first tried with only that value wrong and then made as
    written: an agent that tries a value, is refused, and corrects it."""
    steps = list(getattr(scenario, "solution", None) or ())
    out = []
    for at, step in enumerate(steps):
        for name in step.fixed or ():
            other = _other_value(step.arguments.get(name))
            tried = _with_value(step, name, other) if other is not None else None
            if tried is not None:
                out.append((f"{step.tool}.{name}", [*steps[:at], tried, step, *steps[at + 1 :]]))
    return out


def _with_one_wrong(scenario: Scenario) -> list[tuple[str, list[Any], bool]]:
    """For each fixed value, the solution with only that value changed, and whether a later step
    sets that argument again (a draft the solution itself corrects)."""
    steps = list(getattr(scenario, "solution", None) or ())
    wrong = []
    for at, step in enumerate(steps):
        for name in step.fixed or ():
            other = _other_value(step.arguments.get(name))
            if other is None:
                continue
            changed = _with_value(step, name, other)
            if changed is not None:
                corrected = any(name in (later.arguments or {}) for later in steps[at + 1 :])
                wrong.append((f"{step.tool}.{name}", [*steps[:at], changed, *steps[at + 1 :]], corrected))
    return wrong


def _outcome(scenario: Scenario, world_root: Path) -> str:
    """"no_change" when the reference leaves the world as doing nothing does; unreadable reads "change"."""
    done, _, _, assumed = run_steps(scenario, world_root, with_solution=True)
    try:
        after = _end_state(done)
    finally:
        done.close()
    if assumed:
        return "change"
    nothing, _, _, _ = run_steps(scenario, world_root, with_solution=False)
    try:
        untouched = _end_state(nothing)
    finally:
        nothing.close()
    return "no_change" if after is not None and untouched is not None and after == untouched else "change"


def _base_state(world_root: Path) -> Any:
    """The world before any setup, or None when it cannot be restored here."""
    try:
        world = restore(world_root)
    except Exception as exc:  # noqa: BLE001 - a world only the live runtime holds has no base state here
        logger.info("no base state for the proof: %s", type(exc).__name__)
        return None
    try:
        return _end_state(world)
    finally:
        world.close()


def load_proof_contract(world_root: Path) -> AgentContract | None:
    """The world's contract; none (the defaults: conversational, no rules) when it is absent or cannot be read."""
    try:
        return load(world_root)
    except (OSError, ValueError) as exc:
        logger.info("contract not read for the proof: %s", type(exc).__name__)
        return None


def load_world_actors(world_root: Path) -> list[dict[str, Any]]:
    try:
        loaded = load_actors(world_root)
    except (OSError, ValueError) as exc:
        logger.info("actors not read for the proof, so no actor's view is checked: %s", type(exc).__name__)
        return []
    return [one if isinstance(one, dict) else getattr(one, "model_dump", lambda: {})() for one in loaded or []]


def prove(
    scenario: Scenario,
    catalogue: Catalogue,
    world_root: Path,
    *,
    contract: AgentContract | None = None,
    actors: list[dict[str, Any]] | None = None,
    judge: ControlJudge | None = None,
    conflict_judge: Callable[[str, str], tuple[bool | None, str]] | None = None,
    case_goals: Sequence[str] = (),
    agent_tools: Collection[str] | None = None,
    as_written: bool = False,
    source_root: Path | None = None,
) -> Proof:
    """Run all three gates and say whether this scenario is worth keeping.

    `judge(goal, world, calls, messages, scenario) -> (held, explanation)` decides a judged-only
    case's controls; None uses the harness judge. `conflict_judge(expected, rule) -> (conflict, why)`
    decides whether a cited rule contradicts the expected outcome; None uses the harness judge.
    `agent_tools` are the tools the agent version under test has, when known; None skips that check."""
    proof = Proof(case_goals=list(case_goals), as_written=as_written)
    if agent_tools is not None:
        proof.missing_tools = sorted({str(step.tool) for step in scenario.solution} - set(agent_tools))
    if contract is None:
        contract = load_proof_contract(world_root)
    if actors is None:
        actors = load_world_actors(world_root)
    # Before any gate or early return, so a leak or a false citation is reported whatever the gates say.
    world_state = None if native_runtime(world_root) else _base_state(world_root)
    proof.leaks = [
        one.as_dict()
        for one in leaks(scenario, catalogue, contract, world_state, actors, world_root=world_root)
    ]
    # The agent's own source, which a `file:` source may cite: what this process was given, else what the session
    # recorded, the same for the submit, the live and the run's proofs.
    source_root = source_root if source_root is not None else agent_source_root(world_root)
    proof.sources = resolve_sources(scenario, contract, world_root, source_root)
    rules = [str(one) for one in (getattr(contract, "hard_constraints", None) or [])] if contract is not None else []
    proof.policy_checks = (
        policy_conflicts(
            scenario,
            catalogue,
            rules,
            world_root,
            judge=conflict_judge,
            source_root=source_root,
            workers=getattr(contract, "workers", None) or (),
        )
        if rules
        else []
    )
    checks = checks_for(scenario, catalogue)
    # A step whose tool touches no world and that nothing here can run is recorded, not run (`recordable_tools`).
    run = partial(run_steps, recordable=recordable_tools(scenario, contract, checks))
    proof.evidence = {name: check_evidence(source) for name, source in checks}
    if workspace_fixture(scenario, catalogue) and not native_runtime(world_root):
        proof.deferred = True
        proof.broken = workspace_fixture_problems(
            scenario, catalogue, workspace_bindings(world_root)
        )
        return proof
    if native_runtime(world_root):
        proof.deferred = True
        sources = [("setup", scenario.setup_code), ("ready", scenario.ready_code)]
        sources += [("check", source) for _, source in checks]
        for entry, source in sources:
            if not source.strip():
                continue
            problem = native_source_problem(source, entry)
            if problem:
                proof.broken.append(problem)
        if scenario.setup_code.strip() and not scenario.ready_code.strip():
            proof.broken.append(
                "native fixture setup requires ready(world) to verify actual API state"
            )
        return proof
    reference = reference_of(scenario.solution)
    if not checks:
        # Every sub-goal is judged on the call itself, so the gates that still mean something are
        # that setup and ready ran; the writer proves the reference solution with try_calls.
        world, applied, ready = prepared(scenario, world_root)
        world.close()
        proof.why_not_ready = (
            "" if applied.ok and ready.ok else (applied.said or ready.said)
        )
        proof.ready = applied.ok and ready.ok
        proof.solvable = proof.ready
        proof.vacuous = False
        proof.judged_only = True
        if proof.ready:
            proof.outcome = _outcome(scenario, world_root)
            proof.controls = play_judged_controls(scenario, catalogue, world_root, run=run, judge=judge)
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
    world, calls, refused, assumed = run(scenario, world_root, with_solution=True)
    # What the proof below could not cover. Detected here since forever and logged into a void; a
    # scenario whose whole solution was assumed used to save as proved.
    proof.assumed = assumed
    proof.unplayed = _unplayed(scenario)
    try:
        proof.with_solution = [
            run_check(source, world, calls, name=name, reference=reference)
            for name, source in checks
        ]
        passing = {one.name for one in proof.with_solution if one.held}
        held = [(name, source) for name, source in checks if name in passing]
        proof.excused = excused_by_assumed_steps(proof)
        proof.excused += inconclusive_on_assumed(
            checks,
            world,
            calls,
            reference,
            assumed,
            {one.name for one in proof.with_solution if not one.held and not one.broken} - set(proof.excused),
        )
        right_state = _end_state(world)
        played = [call.name for call in calls if call.ok and not call.refused]
        others = len(world.calls) - len(calls) if right_state is not None else 0
    finally:
        world.close()
    varied, changed = _other_choices(scenario, _declared_options(world_root))
    if changed:
        other_world, other_calls, other_refused, _ = run(
            scenario, world_root, with_solution=True, steps=varied
        )
        try:
            proof.too_strict = [
                f"{name} (the agent chose: {', '.join(sorted(changed))})"
                for name, source in held
                if not other_refused
                and not run_check(source, other_world, other_calls, name=name, reference=reference).held
            ]
        finally:
            other_world.close()
    for label, steps in _bare_ids(scenario) if held else ():
        bare_world, bare_calls, bare_refused, _ = run(scenario, world_root, with_solution=True, steps=steps)
        try:
            # The world took the bare id and ended exactly where the reference does: the same choice.
            if not bare_refused and right_state is not None and _end_state(bare_world) == right_state:
                proof.too_strict += [
                    f"{name} (the same record named without its prefix: {label})"
                    for name, source in held
                    if not run_check(source, bare_world, bare_calls, name=name, reference=reference).held
                ]
        finally:
            bare_world.close()
    for label, steps in _unseparated_ids(scenario) if held else ():
        typed_world, typed_calls, typed_refused, _ = run(scenario, world_root, with_solution=True, steps=steps)
        try:
            # The world took the id typed without separators and ended where the reference does: the same record.
            if not typed_refused and right_state is not None and _end_state(typed_world) == right_state:
                proof.too_strict += [
                    f"{name} (the same id typed without separators: {label}; compare ids with same_id)"
                    for name, source in held
                    if not run_check(source, typed_world, typed_calls, name=name, reference=reference).held
                ]
        finally:
            typed_world.close()
    for label, steps in _corrected_attempts(scenario) if held else ():
        tried_world, tried_calls, tried_refused, _ = run(scenario, world_root, with_solution=True, steps=steps)
        try:
            # Only the wrong attempt was refused and the world ended where the reference does: the agent got it right.
            if len(tried_refused) == len(refused) + 1 and right_state is not None:
                if _end_state(tried_world) == right_state:
                    proof.retried += [
                        f"{name} (a value the world refused, then corrected: {label})"
                        for name, source in held
                        if not run_check(source, tried_world, tried_calls, name=name, reference=reference).held
                    ]
        finally:
            tried_world.close()
    said, spoken = _said_another_way(scenario)
    if spoken and held:
        said_world, said_calls, said_refused, _ = run(scenario, world_root, with_solution=True, steps=said)
        try:
            # Only a world that took the spoken form and ended exactly where the reference does treats it as the
            # same date; one that refuses it or stores it as given leaves a check free to want the reference form.
            if not said_refused and right_state is not None and _end_state(said_world) == right_state:
                proof.too_strict += [
                    f"{name} (the same date said another way: {', '.join(sorted(spoken))})"
                    for name, source in held
                    if not run_check(source, said_world, said_calls, name=name, reference=reference).held
                ]
        finally:
            said_world.close()
    for number, other in enumerate(getattr(scenario, "alternatives", None) or (), start=1):
        alt_world, alt_calls, _, _ = run(scenario, world_root, with_solution=True, steps=other)
        try:
            proof.too_strict += [
                f"{name} (another way the instructions allow, alternative {number}: "
                f"{', '.join(step.tool for step in other)})"
                for name, source in held
                if not run_check(source, alt_world, alt_calls, name=name, reference=reference).held
            ]
        finally:
            alt_world.close()
    for label, steps, corrected in _with_one_wrong(scenario) if held else ():
        if proof.excused and label.split(".", 1)[0] in assumed:
            # A wrong value on an assumed step never reaches the world, so the excused checks that would notice it
            # cannot: no evidence that nothing checks it.
            continue
        wrong_world, wrong_calls, _, _ = run(scenario, world_root, with_solution=True, steps=steps)
        try:
            # Only a value a later step sets again, leaving the right end state, is a corrected draft
            # no check must notice; a read-only lookup leaves the same state and is still checked.
            ended = _end_state(wrong_world)
            if not (corrected and ended is not None and ended == right_state) and all(
                run_check(source, wrong_world, wrong_calls, name=name, reference=reference).held
                for name, source in held
            ):
                proof.unchecked.append(label)
        finally:
            wrong_world.close()
    proof.refused = refused
    proof.faulted = list(getattr(world, "faulted", None) or [])
    proof.recorded = list(getattr(world, "recorded", None) or [])
    proof.broken = [one.name for one in proof.with_solution if one.broken]
    proof.solvable = all(one.held or one.name in proof.excused for one in proof.with_solution) and not proof.broken

    # Gate 3: do those same checks fail when nothing is done?
    untouched, nothing, _, _ = run(scenario, world_root, with_solution=False)
    try:
        untouched_state = _end_state(untouched)
        start = untouched_state
        if start is not None and right_state is not None:
            # The untouched world holds only the pre-run threads; any other call not the solution's is an actor's.
            acted = others > len(untouched.calls) - len(nothing)
            proof.trial = {
                "calls": played,
                "changed": sorted(one for one in {*start, *right_state} if start.get(one) != right_state.get(one)),
                "unattributable": (
                    "a world actor acted during it"
                    if acted
                    else "a solution step was assumed, not run"
                    if assumed
                    else ""
                ),
            }
        proof.with_nothing = [
            run_check(source, untouched, nothing, name=name, reference=reference)
            for name, source in checks
        ]
        # A call-presence check can pass the no-op gate yet credit failed operations.
        # Keep the arguments, remove all results/effects, and inject an execution failure.
        # This is not a business refusal: a timeout does not prove correct refusal either.
        crashed = [
            replace(
                call, ok=False, result=None, error="execution failed", refused=False, refusal_code=""
            )
            for call in calls
        ]
        if crashed:
            proof.failed_attempts = [
                name
                for name, source in checks
                if not any(one.name == name and one.held for one in proof.with_nothing)
                and run_check(source, untouched, crashed, name=name, reference=reference).held
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
    # A refusal case: the reference leaves the world as doing nothing does. Its checks should hold on
    # an untouched world; what proves they can fail is a control performing the forbidden change.
    # An assumed step never ran, so an unchanged world says nothing about what the solution does:
    # it reads "change", as an unreadable state does, and its weak checks are not excused.
    proof.outcome = (
        "no_change"
        if not proof.assumed
        and right_state is not None
        and untouched_state is not None
        and right_state == untouched_state
        else "change"
    )
    proof.controls = play_controls(scenario, world_root, checks, reference, run=run)
    proof.broken += control_broken(proof.controls)
    if proof.outcome == "no_change" and any(one.get("failed") for one in proof.ran_controls):
        proof.vacuous = False
    return proof
