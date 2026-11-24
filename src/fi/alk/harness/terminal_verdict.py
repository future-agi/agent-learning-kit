"""The one terminal decision: did this trial pass, and what does a trainer earn for it.

Two drivers must reach the same verdict from the same evidence. The hosted scheduler drives
a submitted agent to completion; an external RL trainer drives one action at a time over
HTTP (T28). If each decided pass and fail for itself, a reward would stop meaning what a
simulation verdict means, which is the whole of T32.

So the decision lives here, as a function of evidence alone -- no scheduler, no world, no
session. ``decide`` answers what a trial earned; ``reward_for`` turns that into the number a
trainer reads. Receipt assembly, retries and world health stay with their owners, because
they are not part of the decision.

The rules are the hosted path's, preserved deliberately:

* A **task** sub-goal that is False fails the trial. A worker's sub-goal sits beside the
  verdict and never inside it -- the product plan asks for both, aggregate and per worker.
* An unsettled task sub-goal leaves the trial ungraded, and *why* matters: evidence that
  could not be read is ``evidence_unmeasured``, a judge that would not decide is
  ``judge_undecided``. They are different operational faults and a trainer must be able to
  tell them from a real zero.
* A write the world applied twice fails the trial whatever its sub-goals say.
* Off-script callers and unmeasured trials never produce a grade: the trial was not the one
  the scenario describes.

Reward, per T32: a graded pass is 1, a graded failure is 0, and **anything ungraded carries
no training label at all**. A missing grade is never coerced to zero -- that would teach a
model that correct behaviour was wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, Sequence

from .judge import UNMEASURED

# A trial's outcome carries no training label unless it was actually graded.
REWARD_PASS = 1.0
REWARD_FAIL = 0.0


class Status(str, Enum):
    """What a trial earned. Mirrors ``ResultReceipt.status``."""

    PASSED = "passed"
    FAILED = "failed"
    # Graded neither way: an operational fault, not the agent's failure.
    ERRORED = "errored"


class SubGoalLike(Protocol):
    """The decided sub-goal this module reads. ``SubGoalResult`` satisfies it."""

    name: str
    held: bool | None
    reason: str | None
    judged: bool
    subject: str


@dataclass(frozen=True)
class Decision:
    """What the trial earned, and why, independent of how it was driven."""

    status: Status
    # Set only when `status` is ERRORED: the operational fault's code, as ReceiptFailure uses.
    failure_code: str | None = None
    failure_detail: str | None = None

    @property
    def graded(self) -> bool:
        """Whether this decision is a judgement of the agent rather than a fault."""
        return self.status in (Status.PASSED, Status.FAILED)


def task_results(results: Sequence[SubGoalLike]) -> list[SubGoalLike]:
    """Only the sub-goals whose subject is the task.

    A worker's result is reported next to the verdict and never folded into it, so including
    one here would let a sub-agent's failure fail a task the agent completed correctly.
    """
    return [result for result in results if (getattr(result, "subject", "task") or "task") == "task"]


def _undecided_fault(undecided: Sequence[SubGoalLike], lacking: frozenset[str]) -> Decision:
    """Which fault an unsettled task sub-goal is: unreadable evidence, or an undecided judge.

    Evidence is the diagnosis only when *every* unsettled result says so, since one readable
    sub-goal the judge simply would not decide is a judge problem, not an evidence problem.
    """
    unmeasured = all(
        (not result.judged or result.name in lacking)
        and (result.reason or "").startswith(UNMEASURED)
        for result in undecided
    )
    names = ", ".join(result.name for result in undecided)
    if unmeasured:
        return Decision(
            status=Status.ERRORED,
            failure_code="evidence_unmeasured",
            failure_detail=f"a sub-goal's evidence could not be read: {names}",
        )
    return Decision(
        status=Status.ERRORED,
        failure_code="judge_undecided",
        failure_detail="a judged sub-goal was not decided",
    )


def decide(
    results: Sequence[SubGoalLike],
    *,
    unmeasured: Sequence[str] = (),
    duplicates: Any = None,
    off_script: str | None = None,
    lacking_evidence: frozenset[str] = frozenset(),
) -> Decision:
    """The trial's terminal decision from its evidence.

    ``unmeasured`` are the trial's own unmeasured notes (the scenario's condition never
    applied, or evidence is missing); ``duplicates`` is truthy when the world applied a write
    more than once; ``off_script`` is set when the caller left the scenario;
    ``lacking_evidence`` names sub-goals whose evidence was absent.

    Order matters and is the hosted path's. A trial that was not the one the scenario
    describes is never graded, however its sub-goals read -- so off-script and unmeasured are
    decided before any sub-goal, and a duplicate write overrides a pass.
    """
    if off_script:
        return Decision(
            status=Status.ERRORED, failure_code="caller_off_script", failure_detail=off_script
        )
    if unmeasured:
        return Decision(
            status=Status.ERRORED,
            failure_code="evidence_unmeasured",
            failure_detail="unmeasured: " + "; ".join(unmeasured),
        )

    scoped = task_results(results)
    if any(result.held is False for result in scoped):
        decision = Decision(status=Status.FAILED)
    elif undecided := [result for result in scoped if result.held is None]:
        # An unsettled sub-goal is a fault, so a duplicate write cannot turn it into a grade.
        return _undecided_fault(undecided, lacking_evidence)
    elif not scoped:
        # Nothing about the task was measured, so there is no verdict to report. The hosted path
        # used to fall through to a pass here, because both checks above are False over an empty
        # list. A scenario declaring *no* sub-goals was already refused upstream as
        # `check_broken` ("zero sub-goals must not read as a silent pass"); this is the
        # neighbouring hole, where every sub-goal is worker-subject. Worker checks are additive
        # by contract -- write-scenarios: "The task's sub-goals stay as they are: they grade what
        # the person got" -- so a scenario without one is malformed, not a shape to support.
        # Safe to enforce: no sub-goal with a worker subject exists in any scenario on record.
        return Decision(
            status=Status.ERRORED,
            failure_code="check_broken",
            failure_detail="the scenario declared no task sub-goal, so nothing about the task was measured",
        )
    else:
        decision = Decision(status=Status.PASSED)

    if duplicates:
        # The world applied a write twice after the agent's answer was lost. That is the
        # agent's failure whatever the sub-goals say, so it overrides a pass.
        return Decision(status=Status.FAILED)
    return decision


def reward_for(decision: Decision) -> float | None:
    """The trainer's reward, or ``None`` when the trial carries no training label.

    ``None`` is not zero. A judge outage, unreadable evidence, a cancellation or an
    infrastructure fault must reach the trainer through an error channel so the sample can be
    dropped; scoring it 0 would train against behaviour that was never judged wrong.
    """
    if decision.status is Status.PASSED:
        return REWARD_PASS
    if decision.status is Status.FAILED:
        return REWARD_FAIL
    return None
