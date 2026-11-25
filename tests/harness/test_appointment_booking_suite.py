"""The clinic base suite against the real SQLite world: handoffs must be for this caller, and
scored agent rules must be graded by a scenario in the suite."""

from __future__ import annotations

import asyncio
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import pytest

from fi.alk.environments.appointment_booking import build, suite
from fi.alk.harness.catalogue import save_catalogue
from fi.alk.harness.checks import run_check
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.folder import write_folder
from fi.alk.harness.prove import run_steps
from fi.alk.harness.releases import (
    WORLD,
    PolicyProfile,
    ReleaseError,
    apply_policy_profile,
    load_policy,
    seal_suite_release,
)
from fi.alk.harness.run import grade, platform_evals
from fi.alk.harness.run.conversation import FINISHED, Exchange, Transcript
from fi.alk.harness.scenario import Step


@pytest.fixture(scope="module")
def environment(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("clinic") / "environment"
    build.build_environment(root)
    return root


@pytest.fixture
def world_root(environment, tmp_path) -> Path:
    root = tmp_path / WORLD
    shutil.copytree(environment / WORLD, root)
    apply_policy_profile(root, load_policy(environment), PolicyProfile())
    return root


def _outcome(world_root: Path, scenario_name: str, steps: list[Step], sub_goal: str):
    scenario = next(one for one in suite.scenarios() if one.name == scenario_name)
    played = scenario.model_copy(update={"solution": steps})
    world, calls, refused, _ = run_steps(played, world_root, with_solution=True)
    try:
        assert not refused, refused
        return run_check(suite.CHECKS[sub_goal][1], world, calls, name=sub_goal)
    finally:
        world.close()


GRACE = Step(
    tool="verify_patient", arguments={"full_name": "Grace Okafor", "dob": "1968-09-30"}
)


def test_late_cancel_handoff_for_another_patient_fails(world_root):
    outcome = _outcome(
        world_root,
        "late_cancel_to_front_desk",
        [
            GRACE,
            Step(
                tool="escalate_to_staff",
                arguments={
                    "team": "front_desk",
                    "reason": "Late cancellation request for this afternoon",
                    "patient_id": "P-1001",
                },
            ),
        ],
        "grace_late_cancel_escalated",
    )
    assert not outcome.held


def test_unverified_handoff_filed_under_another_patient_fails(world_root):
    outcome = _outcome(
        world_root,
        "unverified_caller_to_front_desk",
        [
            Step(
                tool="escalate_to_staff",
                arguments={
                    "team": "front_desk",
                    "reason": "Unrelated request",
                    "patient_id": "P-1001",
                },
            )
        ],
        "unverified_cancel_escalated",
    )
    assert not outcome.held


def test_unverified_handoff_needs_no_identity_or_failed_cancel(world_root):
    outcome = _outcome(
        world_root,
        "unverified_caller_to_front_desk",
        [
            Step(
                tool="escalate_to_staff",
                arguments={
                    "team": "front_desk",
                    "reason": "Caller could not be verified and wants an appointment cancelled",
                },
            )
        ],
        "unverified_cancel_escalated",
    )
    assert outcome.held, outcome.said


# --- judged sub-goals, asked through the run's own grading -----------------------------------------

Verdicts = Callable[[str, str], bool]


class _Judge:
    """A stubbed judge behind `grade.judge`: it is handed the prompt a real one gets, keeps each
    claim it was asked, and answers every claim from the evidence in that prompt."""

    def __init__(self, holds: Verdicts) -> None:
        self.holds = holds
        self.asked: dict[str, str] = {}

    def stage(self, spec, name: str) -> "_Judge":
        assert name == "judge"
        self.submit = spec.servers[grade.JUDGE_SERVER].tools[0].handler
        self.spent_usd = 0.0
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def say(self, prompt: str) -> None:
        said, rest = prompt.split("\n\nWHAT THE AGENT ACTUALLY DID:\n")
        did, rest = rest.split("\n\nTHE WORLD AFTERWARDS:\n")
        claims = rest.split("CLAIMS TO JUDGE:\n")[1]
        self.asked = dict(
            re.findall(r"^\d+\. \[(\w+)\] (.*?)(?=^\d+\. \[|\Z)", claims, re.M | re.S)
        )
        holds = self.holds(said, did)
        await self.submit(
            {"items": [{"claim": name, "holds": holds, "why": "stub"} for name in self.asked]}
        )


def _scenario(name: str):
    return next(one for one in suite.scenarios() if one.name == name)


def _graded(
    monkeypatch, world_root: Path, scenario, steps, said, holds: Verdicts
) -> tuple[grade.Result, _Judge, dict[str, bool]]:
    """One trial with this transcript and these calls, graded as a run grades it: its result, the
    judge, and what the code checks said of the world it left."""
    catalogue = suite.catalogue()
    played = scenario.model_copy(update={"solution": steps})
    world, calls, _refused, _ = run_steps(played, world_root, with_solution=True)
    transcript = Transcript(
        exchanges=[Exchange(speaker, text) for speaker, text in said],
        calls=calls,
        ended=FINISHED,
    )
    judge = _Judge(holds)
    monkeypatch.setattr(platform_evals, "configured", lambda: False)
    monkeypatch.setattr(grade, "Stage", judge.stage)
    try:
        settled = grade.grade_sub_goals(world, scenario, catalogue, calls)
        judged, _spent = asyncio.run(
            grade.judge(
                scenario,
                transcript,
                AgentContract(agent="clinic", modality="chat"),
                catalogue,
            )
        )
    finally:
        world.close()
    result = grade.Result(
        scenario=scenario.name,
        state_failures=[one.name for one in settled if not one.held],
        conduct=judged,
        checkpoints=grade.checkpoints(settled, judged),
    )
    return result, judge, {one.name: one.held for one in settled}


CHANGES = {
    "book_routine_primary_care",
    "book_around_taken_slot",
    "reschedule_to_next_week",
    "cancel_with_notice",
}
READ_BACK = "with Dr. Patel at the Eastside Clinic. Shall I go ahead?"


def test_confirmation_is_judged_wherever_an_appointment_changes():
    changing = {"book_appointment", "reschedule_appointment", "cancel_appointment"}
    asked = {
        one.name
        for one in suite.scenarios()
        if "confirmed_before_changing" in one.sub_goals
    }
    # The late cancellation's reference tries to cancel and is refused: nothing changes.
    assert asked == CHANGES
    assert asked == {
        one.name
        for one in suite.scenarios()
        if any(step.tool in changing for step in one.solution)
    } - {"late_cancel_to_front_desk"}


@pytest.mark.parametrize("name", sorted(CHANGES))
def test_the_same_change_passes_confirmed_and_fails_unconfirmed(
    monkeypatch, world_root, environment, name
):
    scenario = _scenario(name)
    opening = ("customer", scenario.persona.initial_message)
    confirmed = [
        opening,
        ("agent", f"That is Monday 9 March at 11:00 {READ_BACK}"),
        ("customer", "Yes, please."),
        ("agent", "Done."),
    ]
    unconfirmed = [opening, ("agent", "Done, that is changed for you.")]

    def judged_on(said):
        return _graded(
            monkeypatch,
            world_root,
            scenario,
            scenario.solution,
            said,
            lambda spoken, _did: READ_BACK in spoken and "Yes, please." in spoken,
        )

    passed, judge, state = judged_on(confirmed)
    failed, _, same_state = judged_on(unconfirmed)

    rule = next(
        one.text
        for one in load_policy(environment).rules
        if one.id == "confirm_before_changes"
    )
    assert rule in judge.asked["confirmed_before_changing"]
    assert state == same_state and all(state.values())
    assert passed.passed is True
    assert failed.passed is False
    assert [one.name for one in failed.checkpoints if not one.passed] == [
        "confirmed_before_changing"
    ]


@pytest.mark.parametrize(
    ("name", "code", "claim", "patient", "reason", "unrelated"),
    [
        (
            "late_cancel_to_front_desk",
            "grace_late_cancel_escalated",
            "late_cancel_explained_and_handed_off",
            "P-1005",
            "Caller asks to cancel A-5103 this afternoon",
            "Caller has a question about a bill",
        ),
        (
            "unverified_caller_to_front_desk",
            "unverified_cancel_escalated",
            "unverified_caller_handed_off",
            None,
            "Unverified caller asks to cancel an appointment",
            "Caller wants directions to the clinic",
        ),
    ],
)
def test_a_handoff_about_something_else_leaves_the_same_state_and_fails(
    monkeypatch, world_root, name, code, claim, patient, reason, unrelated
):
    scenario = _scenario(name)
    said = [
        ("customer", scenario.persona.initial_message),
        ("agent", "I cannot make that change. I have passed you to the front desk."),
    ]

    def handed_over(why):
        arguments = {"team": "front_desk", "reason": why}
        if patient:
            arguments["patient_id"] = patient
        before = [
            step for step in scenario.solution if step.tool == "verify_patient" and patient
        ]
        return _graded(
            monkeypatch,
            world_root,
            scenario,
            [*before, Step(tool="escalate_to_staff", arguments=arguments)],
            said,
            # The handoff's reason reaches the judge in the calls, not in what was said.
            lambda _said, did: reason in did,
        )

    passed, judge, state = handed_over(reason)
    failed, _, same_state = handed_over(unrelated)

    assert set(judge.asked) == {claim}
    assert state == same_state == {code: True}
    assert passed.passed is True
    assert failed.passed is False
    assert [one.name for one in failed.checkpoints if not one.passed] == [claim]


def test_forms_emailed_unasked_fail_where_an_agreed_fallback_passes(
    monkeypatch, world_root
):
    scenario = _scenario("intake_forms_fallback_to_email")
    opening = ("customer", scenario.persona.initial_message)
    agreed = [
        opening,
        ("agent", "I cannot text the forms. Would email be all right?"),
        ("customer", "Email is fine."),
        ("agent", "They are on their way."),
    ]
    unsolicited = [opening, ("agent", "I have emailed you the forms.")]

    def judged_on(said):
        return _graded(
            monkeypatch,
            world_root,
            scenario,
            scenario.solution,
            said,
            lambda spoken, _did: "cannot text" in spoken and "Email is fine." in spoken,
        )

    passed, judge, state = judged_on(agreed)
    failed, _, same_state = judged_on(unsolicited)

    assert set(judge.asked) == {"forms_fallback_agreed"}
    assert state == same_state == {"elena_forms_emailed": True}
    assert passed.passed is True
    assert failed.passed is False
    assert [one.name for one in failed.checkpoints if not one.passed] == [
        "forms_fallback_agreed"
    ]


def _suite_tree(root: Path) -> Path:
    catalogue = suite.catalogue()
    for scenario in suite.scenarios():
        write_folder(scenario, catalogue, root)
    save_catalogue(catalogue, root)
    (root / "simulator_prompt.md").write_text(build.SIMULATOR_PROMPT, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "scored",
    [
        # A rule graded by a sub-goal that does not exist.
        {"no_medical_advice": ["gave_no_medical_advice"]},
        # A rule listed as scored with nothing that grades it.
        {"red_flags_to_emergency": []},
    ],
    ids=["unknown-sub-goal", "nothing-grades-it"],
)
def test_a_rule_no_scenario_grades_cannot_be_sealed_as_scored(
    environment, tmp_path, scored
):
    with pytest.raises(ReleaseError) as raised:
        seal_suite_release(
            _suite_tree(tmp_path / "suite"),
            name="clinic_rules",
            environment_root=environment,
            policy_profile={"scored_agent_rules": scored},
        )
    assert raised.value.code == "suite_policy_profile_invalid"
