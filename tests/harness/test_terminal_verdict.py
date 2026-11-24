"""The terminal decision's rules, pinned.

These characterize the hosted scheduler's grading semantics so the shared decision cannot
drift from it. A change here is a change to what a reward means, so each test names the rule
it protects rather than the branch it covers.
"""

from __future__ import annotations

from fi.alk.harness.judge import UNMEASURED
from fi.alk.harness.terminal_verdict import (
    Decision,
    Status,
    decide,
    reward_for,
    task_results,
)


class _Result:
    """A decided sub-goal, standing in for ``SubGoalResult``."""

    def __init__(
        self,
        name: str = "refunded",
        *,
        held: bool | None = True,
        reason: str | None = None,
        judged: bool = False,
        subject: str = "task",
    ) -> None:
        self.name = name
        self.held = held
        self.reason = reason
        self.judged = judged
        self.subject = subject


def test_all_task_sub_goals_holding_is_a_pass() -> None:
    assert decide([_Result(held=True)]).status is Status.PASSED


def test_one_false_task_sub_goal_fails_the_trial() -> None:
    assert decide([_Result(held=True), _Result("explained", held=False)]).status is Status.FAILED


def test_a_workers_sub_goal_never_enters_the_verdict() -> None:
    """A sub-agent's failure must not fail a task the agent completed correctly."""
    results = [_Result(held=True), _Result("worker check", held=False, subject="worker:w1")]
    assert decide(results).status is Status.PASSED
    assert [result.name for result in task_results(results)] == ["refunded"]


def test_unreadable_evidence_and_an_undecided_judge_are_different_faults() -> None:
    """A trainer must tell an evidence failure from a judge that would not decide."""
    unreadable = decide(
        [_Result(held=None, reason=f"{UNMEASURED}the action log was empty", judged=False)]
    )
    assert unreadable.status is Status.ERRORED
    assert unreadable.failure_code == "evidence_unmeasured"
    assert "refunded" in (unreadable.failure_detail or "")

    undecided = decide([_Result(held=None, reason="the judge abstained", judged=True)])
    assert undecided.status is Status.ERRORED
    assert undecided.failure_code == "judge_undecided"


def test_one_undecided_judge_among_unmeasured_results_is_a_judge_fault() -> None:
    """Evidence is the diagnosis only when every unsettled result says so."""
    decision = decide(
        [
            _Result("a", held=None, reason=f"{UNMEASURED}no log", judged=False),
            _Result("b", held=None, reason="the judge abstained", judged=True),
        ]
    )
    assert decision.failure_code == "judge_undecided"


def test_a_judged_sub_goal_whose_evidence_was_absent_reads_as_unmeasured() -> None:
    """`lacking_evidence` lets a judged check still be diagnosed as an evidence failure."""
    decision = decide(
        [_Result("a", held=None, reason=f"{UNMEASURED}no recording", judged=True)],
        lacking_evidence=frozenset({"a"}),
    )
    assert decision.failure_code == "evidence_unmeasured"


def test_an_unmeasured_trial_is_never_graded() -> None:
    """The scenario's condition never applied, so its sub-goals do not describe this trial."""
    decision = decide([_Result(held=True)], unmeasured=["the caller never reached the agent"])
    assert decision.status is Status.ERRORED
    assert decision.failure_code == "evidence_unmeasured"


def test_an_off_script_caller_is_never_graded() -> None:
    decision = decide([_Result(held=True)], off_script="the caller changed the request")
    assert decision.status is Status.ERRORED
    assert decision.failure_code == "caller_off_script"
    assert decision.failure_detail == "the caller changed the request"


def test_a_duplicate_write_fails_a_trial_whose_sub_goals_passed() -> None:
    """The world applied a write twice: the agent's failure, whatever the checks say."""
    decision = decide([_Result(held=True)], duplicates=["refund applied twice"])
    assert decision.status is Status.FAILED
    assert decision.failure_code is None


def test_a_duplicate_write_does_not_turn_a_fault_into_a_grade() -> None:
    """An unsettled sub-goal stays a fault; a duplicate must not manufacture a verdict."""
    decision = decide(
        [_Result(held=None, reason="the judge abstained", judged=True)],
        duplicates=["refund applied twice"],
    )
    assert decision.status is Status.ERRORED
    assert decision.failure_code == "judge_undecided"


def test_worker_only_sub_goals_pass_as_the_hosted_path_does() -> None:
    """Pinned as-is, and flagged: nothing about the task was measured, yet it passes.

    Both `held is False` and `held is None` are False over an empty task list, so the hosted
    path falls through to a pass. A scenario with *no* sub-goals is refused upstream as
    `check_broken`, but one whose sub-goals are all worker-subject is not. This test records
    today's behaviour so the extraction is faithful; it is not an endorsement.
    """
    assert decide([_Result("worker check", held=True, subject="worker:w1")]).status is Status.PASSED
    assert decide([]).status is Status.PASSED


def test_reward_is_one_for_a_pass_and_zero_for_a_graded_failure() -> None:
    assert reward_for(Decision(status=Status.PASSED)) == 1.0
    assert reward_for(Decision(status=Status.FAILED)) == 0.0


def test_an_ungraded_trial_carries_no_reward_rather_than_zero() -> None:
    """T32: a grading failure reports an error, never a zero. Coercing it trains the wrong thing."""
    for code in ["evidence_unmeasured", "judge_undecided", "caller_off_script"]:
        decision = Decision(status=Status.ERRORED, failure_code=code)
        assert reward_for(decision) is None
        assert not decision.graded


def test_a_graded_decision_says_so() -> None:
    assert Decision(status=Status.PASSED).graded
    assert Decision(status=Status.FAILED).graded
