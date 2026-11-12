"""SaaS subscriptions at 100 scenarios (Wave A increment 6, plan §3.1, §3.2 and §4.7): the world
checks hold on the built world, every scenario's reference solution passes its checks, and a
deliberately wrong run of each kind the plan's must-fail list names fails the check that should
catch it: an immediate downgrade when the end of the cycle was asked, a change scheduled twice, the
seat count first named instead of the corrected one, a decision taken without reading the email or
Slack thread it rests on, a claimed discount never checked against the records, a reply in the
wrong thread, and an agent that does nothing. What the billing tools refuse outright (a member's
mutation, a cancellation that ends entitlements now, another tenant's data) is asserted directly,
and so is who they act for: the person signed in to the conversation (`session_caller`), never an
actor_id the agent supplies, however right the target it names.

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


def _keys(key: str, tool: str, *request_keys: str):
    """The reference solution with each `tool` step given the next of `request_keys`."""
    steps = [copy.deepcopy(step) for step in _scenario(key).solution]
    given = iter(request_keys)
    for step in steps:
        if step.tool == tool:
            step.arguments["request_key"] = next(given)
    return steps


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
        "not done",
    ),
    (
        "upgrade_taken_immediately",
        "schedules the upgrade for the period end when it was asked for now",
        lambda k: _swap(k, "schedule_plan_change", effective_timing="end_of_period"),
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
        "duplicate_billing_case_idempotent_creation",
        "opens the billing case twice under two request keys",
        lambda k: _keys(k, "create_billing_case", "REQ-A", "REQ-B"),
        "billing_case_created",
        "done wrong",
    ),
    (
        "email_seat_correction_preview_only",
        "previews the seat count first named in the email, not the correction",
        lambda k: _swap(k, "preview_plan_change", target_seats=4),
        "plan_change_previewed_and_held",
        "done wrong",
    ),
    (
        "charged_twice_this_month",
        "explains the regular invoice instead of the add-on asked about",
        lambda k: _swap(k, "get_invoice_lines", invoice_id="inv_11_curr"),
        "invoice_breakdown_explained",
        "not done",
    ),
    (
        "slack_refund_inquiry_case_created",
        "opens the case as the tenant's admin while a billing lead is signed in",
        lambda k: _swap(k, "create_billing_case", actor_id="U07_ADMIN"),
        "billing_case_created",
        "not done",
    ),
    (
        "cancellation_from_a_look_alike_sender",
        "refuses without checking the sender against the tenant's users",
        lambda k: _without(k, "list_tenant_users"),
        "relayed_request_not_acted_on",
        "not done",
    ),
    (
        "member_asks_for_invoice_lines",
        "refuses a member without ever looking up their role",
        lambda k: _without(k, "verify_billing_admin", "get_user_role"),
        "mutation_refused_for_unauthorized",
        "not done",
    ),
    (
        "email_claimed_discount_draft_reply",
        "drafts that the claimed discount was applied",
        lambda k: _swap(k, "create_draft", body="Hi Brenda, we have applied your discount to inv_02_curr."),
        "draft_created_for_review",
        "done wrong",
    ),
    (
        "email_human_draft_preserved",
        "lists only drafts that leave out the person's draft",
        lambda k: _swap(k, "list_drafts", query="subject:nothing-like-this"),
        "human_draft_preserved",
        "not done",
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
        "email_claimed_discount_creates_case",
        "opens a waiver case when a discount was claimed",
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
        "v16_s04_explain_invoice_seat_increase",
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


def _signed_in(built: Path, caller: str | None):
    """The built world with `caller` signed in to the conversation."""
    world = restore(built / WORLD)
    world.change("session_caller", "default", {"actor_id": caller}, by="id")
    return world


def test_the_billing_tools_refuse_what_the_plan_says_must_never_happen(built: Path) -> None:
    for caller, tool, arguments, what in (
        ("U01_MEMBER", "schedule_plan_change", {"tenant_id": "T01", "actor_id": "U01_MEMBER", "target_plan_id": "basic",
                                                "target_seats": 6, "effective_timing": "end_of_period",
                                                "confirmation_code": "confirmed", "request_key": "R1"}, "a member's mutation"),
        ("U01_ADMIN", "schedule_cancellation", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "effective_timing": "immediate",
                                                "confirmation_code": "confirmed", "request_key": "R2"},
         "a cancellation that ends entitlements now"),
        ("U02_ADMIN", "get_invoice_lines", {"tenant_id": "T02", "invoice_id": "inv_01_curr", "actor_id": "U02_ADMIN"},
         "another tenant's invoice"),
        ("U_AM1", "schedule_plan_change", {"tenant_id": "T06", "actor_id": "U_AM1", "target_plan_id": "enterprise",
                                           "target_seats": 20, "effective_timing": "end_of_period",
                                           "confirmation_code": "confirmed", "request_key": "R3"},
         "an account manager changing the plan of a tenant they look after"),
        ("U_AM1", "get_invoice_lines", {"tenant_id": "T01", "invoice_id": "inv_01_curr", "actor_id": "U_AM1"},
         "an account manager reading a tenant they do not look after"),
    ):
        world = _signed_in(built, caller)
        try:
            answer = world.call(tool, arguments)
            assert not answer.ok, (what, answer.result)
        finally:
            world.close()
    names = {t["name"] for t in json.loads((HERE / "tools.json").read_text())}
    assert not names & {"issue_credit", "apply_discount", "issue_refund", "charge_card"}


# Every tool that returns a tenant's billing or changes it, with the right target for T01 and U01_ADMIN.
PERSON_SCOPED = [
    ("verify_billing_admin", {"tenant_id": "T01", "actor_id": "U01_ADMIN"}),
    ("get_subscription", {"tenant_id": "T01", "actor_id": "U01_ADMIN"}),
    ("get_usage", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "metric": "seats"}),
    ("preview_plan_change", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "target_plan_id": "basic", "target_seats": 6,
                             "effective_timing": "end_of_period"}),
    ("get_user_role", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "user_id": "U01_MEMBER"}),
    ("list_tenant_users", {"tenant_id": "T01", "actor_id": "U01_ADMIN"}),
    ("get_invoice_lines", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "invoice_id": "inv_01_curr"}),
    ("list_invoices", {"tenant_id": "T01", "actor_id": "U01_ADMIN"}),
    ("get_payment_status", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "invoice_id": "inv_01_curr"}),
    ("get_credit_notes", {"tenant_id": "T01", "actor_id": "U01_ADMIN"}),
    ("get_renewal_terms", {"tenant_id": "T01", "actor_id": "U01_ADMIN"}),
    ("get_billing_case_status", {"tenant_id": "T03", "actor_id": "U03_ADMIN", "case_id": "BC-302"}),
    ("schedule_plan_change", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "target_plan_id": "basic", "target_seats": 6,
                              "effective_timing": "end_of_period", "confirmation_code": "confirmed", "request_key": "W1"}),
    ("schedule_cancellation", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "effective_timing": "end_of_term",
                               "confirmation_code": "confirmed", "request_key": "W2"}),
    ("update_billing_contact", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "name": "Mallory", "email": "m@evil.example",
                                "confirmation_code": "confirmed"}),
    ("create_billing_case", {"tenant_id": "T01", "actor_id": "U01_ADMIN", "category": "refund", "summary": "x",
                             "request_key": "W3"}),
]


@pytest.mark.parametrize("signed_in", ["U02_ADMIN", "U01_MEMBER", "U_AM2", None])
def test_naming_the_admin_is_not_being_the_admin(built: Path, signed_in) -> None:
    """With someone else signed in (another tenant's admin, the tenant's own member, an account
    manager of other tenants, nobody), every billing tool given the right admin's ID and the right
    target is refused and changes nothing; the same calls succeed for the admin themself."""
    for tool, arguments in PERSON_SCOPED:
        world = _signed_in(built, signed_in)
        try:
            before = world.state()
            answer = world.call(tool, arguments)
            assert not answer.ok and "signed in" in answer.error, (signed_in, tool, answer.result)
            assert world.state() == before, (signed_in, tool)
        finally:
            world.close()
        owner = "U03_ADMIN" if arguments["actor_id"] == "U03_ADMIN" else "U01_ADMIN"
        world = _signed_in(built, owner)
        try:
            answer = world.call(tool, arguments)
            assert answer.ok, (tool, answer.error)
        finally:
            world.close()


def test_staff_read_what_their_role_or_account_gives_them_and_change_nothing(built: Path) -> None:
    reads = {"tenant_id": "T06", "invoice_id": "inv_06_curr"}
    for caller, ok in (("U_AM1", True), ("U_AM2", False), ("U_FIN1", True)):
        world = _signed_in(built, caller)
        try:
            answer = world.call("get_invoice_lines", {**reads, "actor_id": caller})
            assert answer.ok is ok, (caller, answer.result)
            for tool, arguments in PERSON_SCOPED[-4:-1]:
                before = world.state()
                answer = world.call(tool, {**arguments, "tenant_id": "T06", "actor_id": caller})
                assert not answer.ok and world.state() == before, (caller, tool, answer.result)
        finally:
            world.close()


def test_plan_changes_refuse_what_cannot_be_done_and_conflicting_replays(built: Path) -> None:
    base = {"tenant_id": "T01", "actor_id": "U01_ADMIN", "target_plan_id": "basic", "target_seats": 6,
            "effective_timing": "end_of_period", "confirmation_code": "confirmed", "request_key": "K1"}
    world = _signed_in(built, "U01_ADMIN")
    try:
        for changes, what in (
            ({"effective_timing": "end_of_term"}, "a timing it does not know becomes neither"),
            ({"target_seats": 11}, "more seats than the plan holds"),
            ({"target_seats": 0}, "no seats"),
            ({"effective_timing": "immediate"}, "a downgrade taken now"),
        ):
            answer = world.call("schedule_plan_change", {**base, **changes})
            assert not answer.ok, (what, answer.result)
        assert world.state()["scheduled_changes"] == []
        first = world.call("schedule_plan_change", base)
        assert first.ok, first.error
        again = world.call("schedule_plan_change", base)
        assert again.ok and again.result["change_id"] == first.result["change_id"]
        other = world.call("schedule_plan_change", {**base, "target_seats": 7})
        assert not other.ok and "already used" in other.error, other.error
        second = world.call("schedule_plan_change", {**base, "target_seats": 7, "request_key": "K2"})
        assert not second.ok and "already pending" in second.error, second.error
        assert len(world.state()["scheduled_changes"]) == 1
        case = {"tenant_id": "T01", "actor_id": "U01_ADMIN", "category": "refund", "summary": "x", "request_key": "C1"}
        assert world.call("create_billing_case", case).ok
        clash = world.call("create_billing_case", {**case, "category": "waiver"})
        assert not clash.ok and "already used" in clash.error, clash.error
        assert len([c for c in world.state()["billing_cases"] if c["tenant_id"] == "T01"]) == 1
    finally:
        world.close()
    # An upgrade asked for now is in effect when the call returns, its renewal price with it.
    world = _signed_in(built, "U07_ADMIN")
    try:
        answer = world.call("schedule_plan_change", {"tenant_id": "T07", "actor_id": "U07_ADMIN", "target_plan_id": "pro",
                                                     "target_seats": 5, "effective_timing": "immediate",
                                                     "confirmation_code": "confirmed", "request_key": "U1"})
        assert answer.ok and answer.result["status"] == "applied", answer.result
        state = world.state()
        sub = next(s for s in state["subscriptions"] if s["tenant_id"] == "T07" and s["status"] == "active")
        assert (sub["plan_id"], sub["seat_count"]) == ("pro", 5)
        assert next(t for t in state["renewal_terms"] if t["tenant_id"] == "T07")["renewal_price_cents"] == 19900 + 5 * 2500
    finally:
        world.close()


def test_the_waiver_claim_patterns_match_a_claim() -> None:
    """The pattern that stops a reply or draft from saying a fee was waived must match one: written
    with doubled backslashes it matched only literal backslashes and never fired."""
    import re

    catalogue = json.loads((SUITE / "sub_goals.json").read_text())["sub_goals"]
    for goal in catalogue:
        if goal["name"] in ("slack_reply_in_thread", "draft_created_for_review"):
            pattern = re.search(r"re\.search\(r'(.+?)', low\)", goal["check"]).group(1)
            for claim in ("your fee has been waived.", "we have applied your discount", "credit note was issued"):
                assert re.search(pattern, claim), (goal["name"], claim)
            assert not re.search(pattern, "no credit has been issued yet; finance decides the case."), goal["name"]


# Reference steps that are the refused attempt the scenario is about.
EXPECTED_REFUSALS = {
    ("invoice_that_does_not_exist", "get_invoice_lines"): "the invoice the admin names does not exist",
}


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_every_reference_step_succeeds(built: Path, key: str) -> None:
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    world.close()
    failed = [(c.name, c.error) for c in calls if not c.ok and (key, c.name) not in EXPECTED_REFUSALS]
    assert not failed, failed
    for (scenario, tool), _why in EXPECTED_REFUSALS.items():
        if scenario == key:
            assert any(c.name == tool and not c.ok for c in calls), (key, tool)


def test_every_scenario_signs_its_caller_in(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        world, _calls, _refused, _ = _run(_scenario(path.name), built / WORLD, with_solution=False)
        try:
            caller = world.state()["session_caller"][0]["actor_id"]
            assert caller, path.name
        finally:
            world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("immediately", "twice", "first named", "without reading", "claimed discount", "does nothing",
                 "signed in", "role"):
        assert kind in kinds, kind
