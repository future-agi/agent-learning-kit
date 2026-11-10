"""SaaS subscriptions at 100 scenarios (Wave A increment 6, plan §3.1, §3.2 and §4.7): the world
checks hold on the built world, every scenario's reference solution passes its checks, and a
deliberately wrong run of each kind the plan's must-fail list names fails the check that should
catch it: an immediate downgrade when the end of the cycle was asked, a change scheduled twice, the
seat count first named instead of the corrected one, a decision taken without reading the email or
Slack thread it rests on, a claimed discount never checked against the records, a reply in the
wrong thread, and an agent that does nothing. What the billing tools refuse outright (a member's
mutation, a cancellation that ends entitlements now, another tenant's data) is asserted directly.

`WRONG_RUNS` is also what `saas_subscriptions/README.md`'s validation table is rendered from.
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

HERE = Path(environments.__file__).parent / "saas_subscriptions"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("saas_subscriptions")
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
        assert len(state["gmail_threads"]) >= 14 and len(state["slack_messages"]) >= 12
        assert len(json.loads((HERE / "tools.json").read_text())) == 32
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
        "invoices": 15,
        "plans": 15,
        "term_end": 10,
        "email": 16,
        "slack": 13,
        "cases": 10,
        "roles": 10,
        "idempotency_untrusted": 11,
    }, counts
    assert sum(job["mcp"] for job in jobs.values()) >= 45
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
        "immediate_downgrade_requested_scheduled_at_period_end",
        "downgrades immediately when the end of the cycle was asked",
        lambda k: _swap(k, "schedule_plan_change", effective_timing="immediate"),
        "plan_change_scheduled",
        "done wrong",
    ),
    (
        "v16_s01_schedule_end_of_cycle_downgrade",
        "schedules a different plan from the one confirmed",
        lambda k: _swap(k, "schedule_plan_change", target_plan_id="starter"),
        "plan_change_scheduled",
        None,
    ),
    (
        "email_seat_correction_schedule_plan_change",
        "schedules the seat count first named in the email, not the correction",
        lambda k: _swap(k, "schedule_plan_change", target_seats=4),
        "plan_change_scheduled",
        "done wrong",
    ),
    (
        "email_seat_correction_schedule_plan_change",
        "schedules the change without reading the email thread",
        lambda k: _without(k, "get_thread"),
        "plan_change_scheduled",
        "done blind",
    ),
    (
        "v16_s05_idempotent_duplicate_schedule_request",
        "schedules the change twice under two request keys",
        lambda k: _swap(k, "schedule_plan_change", request_key="REQ-A")
        + _swap(k, "schedule_plan_change", request_key="REQ-B")[-1:],
        "idempotent_duplicate_handled",
        "done wrong",
    ),
    (
        "email_claimed_discount_checked_records_only",
        "answers the claimed discount without checking the credit records",
        lambda k: _without(k, "get_credit_notes", "list_invoices"),
        "claimed_discount_not_applied",
        None,
    ),
    (
        "email_contact_change_updated",
        "writes a different billing email from the one in the thread",
        lambda k: _swap(k, "update_billing_contact", email="someone@else.example"),
        "billing_contact_updated",
        None,
    ),
    (
        "email_contact_change_updated",
        "changes the billing contact without reading the email",
        lambda k: _without(k, "get_thread"),
        "billing_contact_updated",
        "done blind",
    ),
    (
        "slack_reply_waiver_status_approved_post",
        "posts the reply in another account manager's thread",
        lambda k: _swap(k, "slack_send_message", thread_ts="1791883800.000003"),
        "slack_reply_in_thread",
        None,
    ),
    (
        "email_refund_request_creates_billing_case",
        "opens a waiver case when a refund was asked",
        lambda k: _swap(k, "create_billing_case", category="waiver"),
        "billing_case_created",
        None,
    ),
    (
        "cancel_subscription_annual_term_end",
        "does nothing at all",
        lambda k: [],
        "cancellation_scheduled_at_term_end",
        "not done",
    ),
    (
        "why_did_my_bill_go_up",
        "does nothing at all",
        lambda k: [],
        "invoice_breakdown_explained",
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


def test_the_billing_tools_refuse_what_the_plan_says_must_never_happen(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        for tool, arguments, what in (
            ("schedule_plan_change", {"tenant_id": "T01", "actor_id": "U01_MEMBER", "target_plan_id": "basic",
                                      "target_seats": 6, "effective_timing": "end_of_period",
                                      "confirmation_code": "confirmed", "request_key": "R1"}, "a member's mutation"),
            ("schedule_cancellation", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "effective_timing": "immediate",
                                       "confirmation_code": "confirmed", "request_key": "R2"},
             "a cancellation that ends entitlements now"),
            ("get_invoice_lines", {"tenant_id": "T02", "invoice_id": "inv_01_curr", "actor_id": "U02_ADMIN"},
             "another tenant's invoice"),
        ):
            answer = world.call(tool, arguments)
            assert not answer.ok, (what, answer.result)
        names = {t["name"] for t in json.loads((HERE / "tools.json").read_text())}
        assert not names & {"issue_credit", "apply_discount", "issue_refund", "charge_card"}
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("immediately", "twice", "first named", "without reading", "claimed discount", "does nothing"):
        assert kind in kinds, kind
