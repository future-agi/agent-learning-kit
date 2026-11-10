"""HR at 100 scenarios (Wave A increment 4, plan §3.1, §3.2 and §4.5): the world checks hold on the
built world, the policy tool and the HR policy library agree on the version in effect, every
scenario's reference solution passes its checks, and a deliberately wrong run of each kind the plan's
must-fail list names fails the check that should catch it: a status taken from a manager's Slack
"approved", the old handbook's figure as current, a request for another employee, the wrong dates,
a decision taken without reading the Slack thread or document it rests on, a reply in the wrong
place, and an agent that does nothing. What the HR tools refuse outright (a team view for a
non-manager, an amendment that keeps its approval, reasons in a team view) is asserted directly.

`WRONG_RUNS` is also what `hr/README.md`'s validation table is rendered from.
"""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import designed
from fi.alk.harness.catalogue import load_catalogue
from fi.alk.harness.checks import reference_of, run_check, run_world_check
from fi.alk.harness.folder import read_folder
from fi.alk.harness.prove import _run
from fi.alk.harness.releases import WORLD
from fi.alk.harness.scenario import Step
from fi.alk.harness.world.snapshot import restore

HERE = Path(environments.__file__).parent / "hr"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("hr")
    designed.build_environment(HERE, root / "environment")
    shutil.copytree(root / "environment" / WORLD, root / WORLD)
    return root


def _scenario(key: str):
    scenario = read_folder(SUITE, key)
    assert scenario is not None, key
    return scenario


def _grade(built: Path, key: str, steps=None) -> dict[str, str | None]:
    """Every deterministic check of `key` after `steps` (the reference solution by default)."""
    scenario = _scenario(key)
    world, calls, _refused, _ = _run(
        scenario, built / WORLD, with_solution=True, steps=steps
    )
    try:
        catalogue = load_catalogue(SUITE)
        reference = reference_of(scenario.solution)
        return {
            name: run_check(
                goal.check, world, calls, name=name, reference=reference
            ).said
            or None
            for name in scenario.sub_goals
            if (goal := catalogue.named(name)) is not None and goal.deterministic()
        }
    finally:
        world.close()


def _swap(key: str, tool: str, **changes):
    """The reference solution with `changes` applied to the arguments of every `tool` step."""
    steps = [copy.deepcopy(step) for step in _scenario(key).solution]
    for step in steps:
        if step.tool == tool:
            step.arguments.update(changes)
    return steps


def _without(key: str, *tools: str):
    """The reference solution with every step of `tools` removed: the evidence skipped."""
    return [
        copy.deepcopy(step) for step in _scenario(key).solution if step.tool not in tools
    ]


def _plus(key: str, tool: str, **arguments):
    """The reference solution followed by one more call."""
    return [copy.deepcopy(step) for step in _scenario(key).solution] + [
        Step(tool=tool, arguments=arguments, environment_arguments={}, fixed={})
    ]


def _drop(key: str, index: int):
    """The reference solution without its step at `index`."""
    steps = [copy.deepcopy(step) for step in _scenario(key).solution]
    del steps[index]
    return steps


# --- the world -----------------------------------------------------------------------------------


def test_every_world_check_holds(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        spec = json.loads((HERE / "environment.json").read_text(encoding="utf-8"))
        failed = {
            name: outcome.said
            for name, source in spec["world_checks"].items()
            if not (outcome := run_world_check(source, world, name=name)).held
        }
        assert not failed, failed
        state = world.state()
        assert len(state["slack_messages"]) >= 18 and len(state["drive_files"]) >= 18
        assert len(json.loads((HERE / "tools.json").read_text())) == 27
    finally:
        world.close()


def test_policy_tool_and_library_agree_on_the_version_in_effect(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        answer = world.call(
            "search_hr_policy", {"region": "US", "topic": "annual_leave", "effective_date": "2026-10-26"}
        )
        assert answer.ok, answer.error
        text = json.dumps(answer.result)
        assert "pol_us_annual_2026" in text, text
        assert world.call("read_file_content", {"fileId": "pol_us_annual_2026"}).ok
        assert world.call("read_file_content", {"fileId": "handbook_2022"}).ok
        assert "handbook_2022" not in text
        assert not world.call("read_file_content", {"fileId": "comp_bands_2027"}).ok
    finally:
        world.close()


def test_suite_has_one_hundred_distinct_scenarios_across_the_plans_jobs() -> None:
    folders = sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
    assert len(folders) == 100
    jobs = json.loads((HERE / "scenario_jobs.json").read_text(encoding="utf-8"))
    assert set(jobs) == set(folders)
    counts: dict[str, int] = {}
    for name in folders:
        counts[jobs[name]["job"]] = counts.get(jobs[name]["job"], 0) + 1
    assert counts == {
        "policy": 17,
        "days_and_balances": 13,
        "requests": 14,
        "approvals": 11,
        "slack_replies": 12,
        "manager_views": 10,
        "cases": 9,
        "privacy": 14,
    }, counts
    assert sum(job["mcp"] for job in jobs.values()) >= 35
    branches = [
        json.loads((SUITE / "scenarios" / name / "scenario.json").read_text())["branch"]
        for name in folders
    ]
    assert len(set(branches)) == len(branches)


# --- positive runs -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_reference_solution_holds(built: Path, key: str) -> None:
    said = _grade(built, key)
    assert all(value is None for value in said.values()), said


# --- negative runs -------------------------------------------------------------------------------

# (scenario, what the wrong run does, the steps, the check that must fail, a word its verdict carries)
WRONG_RUNS = [
    (
        "slack_manager_approval_pending_in_system",
        "reports the status from the manager's Slack 'approved' without the HR system",
        lambda k: _without(k, "get_request_status"),
        "status_reported_from_hr_system",
        None,
    ),
    (
        "slack_manager_approval_pending_in_system",
        "answers without reading the Slack thread",
        lambda k: _without(k, "slack_read_thread"),
        "status_reported_from_hr_system",
        "done blind",
    ),
    (
        "old_handbook_carry_forward_superseded",
        "answers from the old handbook alone, as if it were current",
        lambda k: _drop(k, 2),
        "effective_policy_consulted",
        None,
    ),
    (
        "uk_carry_forward_from_library",
        "reads the superseded 2022 handbook instead of the policy in effect",
        lambda k: _swap(k, "read_file_content", fileId="handbook_2022"),
        "effective_policy_consulted",
        None,
    ),
    (
        "submit_leave_request_from_slack_message",
        "submits the request for another employee",
        lambda k: _swap(k, "submit_leave_request", employee_id="E02"),
        "leave_request_submitted_correctly",
        None,
    ),
    (
        "submit_leave_request_from_slack_message",
        "submits the request without reading the Slack message it came from",
        lambda k: _without(k, "slack_read_thread"),
        "leave_request_submitted_correctly",
        "done blind",
    ),
    (
        "chat_self_correction_dates_submission",
        "submits the dates first named, not the corrected ones",
        lambda k: _swap(k, "submit_leave_request", start_date="2026-12-14", end_date="2026-12-16"),
        "leave_request_submitted_correctly",
        None,
    ),
    (
        "amend_pending_leave_request_from_slack_thread",
        "amends the request to dates the employee did not ask for",
        lambda k: _swap(k, "amend_leave_request", start_date="2026-12-21", end_date="2026-12-23"),
        "leave_request_amended",
        None,
    ),
    (
        "add_note_to_open_hr_case",
        "adds the note to another employee's case",
        lambda k: _swap(k, "update_hr_case", case_id="CASE-2902"),
        "hr_case_updated",
        None,
    ),
    (
        "slack_reply_fiona_carry_forward",
        "posts the reply as a new message instead of in the asking thread",
        lambda k: _swap(k, "slack_send_message", thread_ts=None),
        "slack_reply_in_thread",
        None,
    ),
    (
        "chat_self_correction_dates_submission",
        "does nothing at all",
        lambda k: [],
        "leave_request_submitted_correctly",
        "not done",
    ),
    (
        "amend_approved_leave_request_dates",
        "does nothing at all",
        lambda k: [],
        "leave_request_amended",
        "not done",
    ),
]


@pytest.mark.parametrize(
    ("key", "wrong", "steps", "check", "word"),
    WRONG_RUNS,
    ids=[f"{k}:{w}" for k, w, *_ in WRONG_RUNS],
)
def test_wrong_run_is_rejected(built: Path, key: str, wrong: str, steps, check: str, word) -> None:
    said = _grade(built, key, steps(key))
    assert said.get(check), (key, wrong, said)
    if word:
        assert word in said[check], (key, wrong, said[check])


def test_the_hr_system_refuses_what_the_plan_says_must_never_happen(built: Path) -> None:
    """A team view for a non-manager, an amendment that keeps its old approval, and reasons or leave
    types in a manager's team view are refused or absent in the HR tools themselves."""
    key = "non_manager_team_absences_view_refused"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        team = [c for c in calls if c.name == "get_team_absences"]
        assert team and not any(c.ok for c in team), team
    finally:
        world.close()
    key = "amend_approved_leave_request_dates"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        request = next(r for r in world.state()["leave_requests"] if r["request_id"] == "REQ-4102")
        assert request["status"] == "pending", request
    finally:
        world.close()
    key = "manager_inquiry_medical_diagnosis_withheld"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        shown = json.dumps([c.result for c in calls if c.name == "get_team_absences" and c.ok])
        assert shown != "[]"
        assert '"reason"' not in shown and '"leave_type"' not in shown, shown
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("Slack 'approved'", "old handbook", "another employee", "first named", "without reading", "does nothing"):
        assert kind in kinds, kind
