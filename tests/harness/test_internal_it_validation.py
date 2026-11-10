"""Internal IT at 100 scenarios (Wave A increment 5, plan §3.1, §3.2 and §4.9): the world checks hold
on the built world, the access-policy tool and the IT policy library agree on the version in effect,
every scenario's reference solution passes its checks, and a deliberately wrong run of each kind the
plan's must-fail list names fails the check that should catch it: a role above the one the need
calls for, the wrong person, the wrong number of days, the wrong decision or entitlement, a reply in
the wrong thread, a decision taken without reading the Slack thread or document it rests on, and an
agent that does nothing. What the access system refuses outright (a grant on a Slack "approved"
without a token, a token from another request, a separation-of-duties conflict, a duplicate open
request, temporary access beyond the limit) is asserted directly.

`WRONG_RUNS` is also what `internal_it/README.md`'s validation table is rendered from.
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

HERE = Path(environments.__file__).parent / "internal_it"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("internal_it")
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
        assert len(state["slack_messages"]) >= 13 and len(state["drive_files"]) >= 9
        assert len(json.loads((HERE / "tools.json").read_text())) == 28
    finally:
        world.close()


def test_policy_tool_and_library_agree_on_the_version_in_effect(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        answer = world.call(
            "check_access_policy", {"employee_id": "E16", "app_id": "APP-FIN", "role_name": "viewer"}
        )
        assert answer.ok, answer.error
        text = json.dumps(answer.result)
        assert "acc_pol_v3" in text, text
        read = world.call("read_file_content", {"fileId": "acc_pol_v3"})
        assert read.ok and "7 days" in read.result["fileContent"]
        for stale in ("acc_pol_v2", "acc_pol_v4_draft"):
            assert world.call("read_file_content", {"fileId": stale}).ok
            assert stale not in text
        assert not world.call("read_file_content", {"fileId": "secops_playbook"}).ok
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
        "requests": 16,
        "approvals": 15,
        "slack": 15,
        "revocation_temporary": 10,
        "policy": 13,
        "sod_licences": 9,
        "duplicates": 9,
        "escalation": 13,
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
        "slack_manager_approved_without_token",
        "reports the status from the manager's Slack 'approved' without the access system",
        lambda k: _without(k, "get_approval_status"),
        "status_reported_from_access_system",
        None,
    ),
    (
        "slack_manager_approved_without_token",
        "answers without reading the Slack thread",
        lambda k: _without(k, "slack_read_thread"),
        "status_reported_from_access_system",
        "done blind",
    ),
    (
        "request_crm_least_privilege_viewer",
        "requests the admin role the employee asked for when viewer covers the need",
        lambda k: _swap(k, "create_access_request", role_name="sales_admin"),
        "pending_request_created",
        "done wrong",
    ),
    (
        "v20_s01_submit_readonly_request",
        "requests editor when the employee only reads dashboards",
        lambda k: _swap(k, "create_access_request", role_name="editor"),
        "pending_request_created",
        "done wrong",
    ),
    (
        "request_crm_viewer_from_slack_message",
        "creates the request without reading the Slack message it came from",
        lambda k: _without(k, "slack_read_thread"),
        "pending_request_created",
        "done blind",
    ),
    (
        "request_crm_viewer_from_slack_message",
        "creates the request for another person",
        lambda k: _swap(k, "create_access_request", employee_id="E11"),
        "pending_request_created",
        None,
    ),
    (
        "request_temporary_access_14_days",
        "asks for 30 days when the employee needs 14",
        lambda k: _swap(k, "request_temporary_access", duration_days=30),
        "temporary_access_requested",
        None,
    ),
    (
        "approver_records_rejection_decision",
        "records an approval when the approver rejected",
        lambda k: _swap(k, "record_approval_decision", decision="approve"),
        "approval_decision_recorded",
        None,
    ),
    (
        "revoke_choose_correct_entitlement",
        "revokes the entitlement first named, not the corrected one",
        lambda k: _swap(k, "revoke_synthetic_access", entitlement_id="ENT-418-A"),
        "entitlement_revoked",
        None,
    ),
    (
        "slack_reply_pending_evan_approval",
        "posts the reply in another employee's thread",
        lambda k: _swap(k, "slack_send_message", thread_ts="1792755000.000003"),
        "slack_reply_in_thread",
        None,
    ),
    (
        "cancel_from_slack_message",
        "cancels the request without reading the Slack message",
        lambda k: _without(k, "slack_read_thread"),
        "access_request_cancelled",
        "done blind",
    ),
    (
        "policy_viewer_approval_requirement",
        "answers from the superseded 2025 policy",
        lambda k: _swap(k, "read_file_content", fileId="acc_pol_v2"),
        "effective_policy_consulted",
        None,
    ),
    (
        "grant_synthetic_access_with_valid_token",
        "does nothing at all",
        lambda k: [],
        "approved_access_granted_with_token",
        "not done",
    ),
    (
        "approve_and_grant_in_single_session",
        "does nothing at all",
        lambda k: [],
        "approval_decision_recorded",
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


def test_the_access_system_refuses_what_the_plan_says_must_never_happen(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        for tool, arguments, what in (
            ("grant_synthetic_access", {"request_id": "AR-101", "approval_token": "approved"},
             "a grant on a Slack 'approved' without a token"),
            ("grant_synthetic_access", {"request_id": "AR-105", "approval_token": "TOK-AR102-7Q4"},
             "a token from another request, person and role"),
            ("grant_synthetic_access", {"request_id": "AR-104", "approval_token": "TOK-AR104-K2M"},
             "an expired token"),
            ("create_access_request", {"employee_id": "E03", "app_id": "APP-FIN", "role_name": "finance_approver",
                                       "justification": "x"}, "a separation-of-duties conflict"),
            ("create_access_request", {"employee_id": "E18", "app_id": "APP-MKT", "role_name": "author",
                                       "justification": "x"}, "a duplicate open request"),
            ("request_temporary_access", {"employee_id": "E12", "app_id": "APP-PROD", "role_name": "readonly",
                                          "duration_days": 45, "justification": "x"}, "temporary access beyond 30 days"),
            ("create_access_request", {"employee_id": "E20", "app_id": "APP-HRIS", "role_name": "employee",
                                       "justification": "x"}, "access for an inactive employee"),
        ):
            answer = world.call(tool, arguments)
            assert not answer.ok, (what, answer.result)
        entitlements = world.state()["entitlements"]
        assert len(entitlements) == 8
    finally:
        world.close()
    # Temporary access always carries an expiry: the request records its days, the grant its end.
    key = "request_temporary_access_14_days"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        made = [r for r in world.state()["access_requests"] if r["employee_id"] == "E419"]
        assert made and made[0]["duration_days"] == 14, made
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("Slack 'approved'", "admin role", "another person", "first named", "without reading", "does nothing"):
        assert kind in kinds, kind
