"""Banking at 100 scenarios (Wave A increment 2, plan §3.1 and §3.2): the world checks hold on the
built world, every scenario's reference solution passes its checks on SQLite, and a deliberately
wrong run of each kind the plan names fails the check that should catch it: a wrong target, a
duplicate, a draft on another thread or with a superseded fee or a false status, a decision taken
without reading the evidence it rests on, a stale policy version, a contact change without the
step-up, an agent that claims completion without acting, and an agent that does nothing.

`WRONG_RUNS` is also what `banking/README.md`'s validation table is rendered from, so the README
and this test cannot disagree.
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
from fi.alk.harness.world.snapshot import restore

HERE = Path(environments.__file__).parent / "banking"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("banking")
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
        copy.deepcopy(step)
        for step in _scenario(key).solution
        if step.tool not in tools
    ]


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
        assert len(state["gmail_threads"]) >= 20 and len(state["gmail_messages"]) >= 30
        assert len(state["drive_files"]) >= 15
        assert {t["name"] for t in json.loads((HERE / "tools.json").read_text())} >= {
            "get_customer_profile",
            "list_accounts",
            "get_transaction",
            "lookup_merchant_descriptor",
            "request_replacement_card",
            "update_card_controls",
            "list_disputes",
            "withdraw_dispute",
            "update_contact_details",
            "read_file_content",
            "search_files",
            "get_thread",
            "create_draft",
        }
    finally:
        world.close()


def test_policy_reader_and_drive_resolve_to_the_same_effective_document(
    built: Path,
) -> None:
    """Plan §3.3: the business tool and the Drive document agree on the version in effect."""
    world = restore(built / WORLD)
    try:
        for policy_type, file_id, figure in (
            ("transaction_dispute", "pol_dispute_v2", "60 days"),
            ("card_policy", "pol_card_v2", "$5,000.00"),
            ("fee_schedule", "fee_sched_v4", "$25.00"),
            ("provisional_credit", "pol_provisional_credit", "10 business days"),
            ("contact_changes", "pol_contact_changes", "one-time passcode"),
        ):
            answer = world.call("get_dispute_policy", {"policy_type": policy_type})
            assert answer.ok and answer.result["source"]["file_id"] == file_id, (
                policy_type,
                answer.error,
            )
            read = world.call("read_file_content", {"fileId": file_id})
            assert read.ok and figure in read.result["fileContent"], (file_id, figure)
            assert answer.result["source"]["version"] in read.result["fileContent"]
        # Superseded and draft versions exist, are readable, and are never the source.
        for stale in (
            "pol_dispute_v1",
            "pol_dispute_v3_draft",
            "fee_sched_v3",
            "fee_sched_v5_draft",
        ):
            assert world.call("read_file_content", {"fileId": stale}).ok
        assert not world.call("read_file_content", {"fileId": "fraud_playbook"}).ok
    finally:
        world.close()


def test_suite_has_one_hundred_distinct_scenarios_across_the_plans_jobs() -> None:
    folders = sorted(
        path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir()
    )
    assert len(folders) == 100
    jobs = json.loads((HERE / "scenario_jobs.json").read_text(encoding="utf-8"))
    assert set(jobs) == set(folders)
    counts: dict[str, int] = {}
    mcp = 0
    for name in folders:
        counts[jobs[name]["job"]] = counts.get(jobs[name]["job"], 0) + 1
        mcp += jobs[name]["mcp"]
    assert counts == {
        "servicing": 16,
        "dispute_intake": 18,
        "follow_up": 11,
        "email_drafts": 16,
        "policy_questions": 13,
        "contact_changes": 10,
        "verification": 12,
        "out_of_scope": 4,
    }, counts
    assert mcp >= 40
    # Distinct: no two scenarios share a branch, and every scenario names its own target.
    branches = [
        json.loads((SUITE / "scenarios" / name / "scenario.json").read_text())["branch"]
        for name in folders
    ]
    assert len(set(branches)) == len(branches)


# --- positive runs: every reference solution holds -----------------------------------------------


@pytest.mark.parametrize(
    "key",
    sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir()),
)
def test_reference_solution_holds(built: Path, key: str) -> None:
    said = _grade(built, key)
    assert all(value is None for value in said.values()), said


# --- negative runs: the wrong outcome each check must reject --------------------------------------

# (scenario, what the wrong run does, the steps, the check that must fail, a word its verdict carries)
WRONG_RUNS = [
    # wrong targets
    (
        "lock_only_the_card_named_when_two_are_held",
        "locks the card the customer kept",
        lambda k: _swap(k, "lock_card", card_id="CARD312"),
        "card_locked_upon_confirmation",
        "done wrong",
    ),
    (
        "replacement_wrong_card_first_named_then_corrected",
        "replaces the card first named, not the corrected one",
        lambda k: _swap(k, "request_replacement_card", card_id="CARD305"),
        "replacement_requested_as_confirmed",
        "done wrong",
    ),
    (
        "stolen_card_lock_then_expedited_replacement_fee_disclosed",
        "sends the replacement by standard post after the customer chose expedited",
        lambda k: _swap(k, "request_replacement_card", delivery_speed="standard"),
        "replacement_requested_as_confirmed",
        "done wrong",
    ),
    (
        "enable_international_use_for_travel",
        "disables online payments instead of enabling international use",
        lambda k: _swap(
            k, "update_card_controls", international_enabled=False, online_enabled=False
        ),
        "card_controls_updated_as_requested",
        "done wrong",
    ),
    (
        "raise_daily_limit_within_policy_maximum",
        "sets a different limit from the one asked for",
        lambda k: _swap(k, "update_card_controls", daily_limit=300000),
        "card_controls_updated_as_requested",
        "done wrong",
    ),
    (
        "dispute_duplicate_charge_second_of_two_identical",
        "disputes the legitimate first charge",
        lambda k: _swap(
            k, "create_dispute", transaction_id="TX320", request_key="REQ-C320-TX320"
        ),
        "dispute_filed_and_reported",
        "done wrong",
    ),
    (
        "two_fraudulent_charges_two_disputes_one_each",
        "disputes one taxi charge and the customer's own deli charge",
        lambda k: (
            [s for s in _swap(k, "create_dispute") if s.tool != "create_dispute"]
            + [
                _swap(k, "create_dispute", transaction_id="TX327", request_key="a")[-1],
                _swap(k, "create_dispute", transaction_id="TX329", request_key="b")[-1],
            ]
        ),
        "dispute_filed_and_reported",
        "done wrong",
    ),
    (
        "email_pickup_double_correction_latest_message_governs",
        "disputes the charge named in an earlier email (Petco TX14)",
        lambda k: _swap(
            k, "create_dispute", transaction_id="TX14", request_key="REQ-C07-TX14"
        ),
        "dispute_filed_and_reported",
        "done wrong",
    ),
    (
        "chat_correction_overrides_email_transaction",
        "disputes the email's charge, ignoring the correction in chat",
        lambda k: _swap(
            k, "create_dispute", transaction_id="TX17", request_key="REQ-C01-TX17"
        ),
        "dispute_filed_and_reported",
        "done wrong",
    ),
    (
        "withdraw_one_of_two_disputes_first_id_corrected",
        "withdraws the hotel dispute the customer first named",
        lambda k: _swap(k, "withdraw_dispute", dispute_id="D342"),
        "dispute_withdrawn_as_requested",
        "done wrong",
    ),
    (
        "email_pickup_withdraw_dispute_customer_recognised_charge",
        "withdraws another customer's dispute",
        lambda k: _swap(k, "withdraw_dispute", dispute_id="D01"),
        "dispute_withdrawn_as_requested",
        "not done",
    ),
    (
        "dispute_status_by_transaction_reference",
        "looks up a transaction with no dispute (the lookup is refused)",
        lambda k: _swap(k, "get_dispute_status", transaction_id="TX64"),
        "dispute_status_retrieved",
        "not done",
    ),
    (
        "list_all_disputes_overview",
        "lists another verified customer's disputes instead of Kevin's",
        lambda k: _swap(k, "list_disputes", customer_id="C08", session_id="SESS08"),
        "dispute_status_retrieved",
        "done wrong",
    ),
    (
        "profile_and_accounts_summary_read_only",
        "reads another customer's profile in a session that verified only this one",
        lambda k: _swap(
            k, "get_customer_profile", customer_id="C01", session_id="SESS01"
        ),
        "authenticated_before_disclosure",
        "done wrong",
    ),
    (
        "change_postal_address_with_otp_step_up",
        "writes an address with the apartment missing",
        lambda k: _swap(
            k,
            "update_contact_details",
            new_value="77 Lakeview Drive, Madison, WI 53703",
        ),
        "contact_details_updated_after_step_up",
        "done wrong",
    ),
    (
        "contact_change_value_corrected_before_commit",
        "applies the mistyped number instead of the corrected one",
        lambda k: _swap(k, "update_contact_details", new_value="+15551230386"),
        "contact_details_updated_after_step_up",
        "done wrong",
    ),
    (
        "change_email_address_with_otp_step_up",
        "changes the phone instead of the email",
        lambda k: _swap(
            k, "update_contact_details", field="phone", new_value="+15551239000"
        ),
        "contact_details_updated_after_step_up",
        "done wrong",
    ),
    # duplicates and retries
    (
        "replacement_request_retried_once_idempotent",
        "verifies, then tells the customer the request is placed without placing it",
        lambda k: _scenario(k).solution[:1],
        "replacement_requested_as_confirmed",
        "not done",
    ),
    (
        "email_pickup_repeat_dispute_request_returns_existing",
        "files a dispute for a different Nordstrom-era charge (TX47) instead of returning D01",
        lambda k: _swap(
            k, "create_dispute", transaction_id="TX47", request_key="other"
        ),
        "idempotent_dispute_reused",
        "done wrong",
    ),
    # drafts: wrong thread, superseded fee, false status, promised credit
    (
        "draft_fee_reply_from_effective_schedule",
        "quotes the superseded $15.00 fee",
        lambda k: _swap(
            k,
            "create_draft",
            body="Dear Ian Taylor, an expedited replacement costs $15.00 and arrives in 2 business days.",
        ),
        "draft_created_for_review",
        "superseded",
    ),
    (
        "draft_fee_reply_from_effective_schedule",
        "quotes the $30.00 of the 2027 draft schedule",
        lambda k: _swap(
            k,
            "create_draft",
            body="Dear Ian Taylor, from next year the expedited replacement fee is $30.00.",
        ),
        "draft_created_for_review",
        "superseded",
    ),
    (
        "draft_dispute_status_reply_matches_record",
        "tells Julia her dispute is resolved when it is under review",
        lambda k: _swap(
            k,
            "create_draft",
            body="Dear Julia Anderson, good news: dispute D02 has been resolved in your favour.",
        ),
        "draft_created_for_review",
        "under_review",
    ),
    (
        "draft_dispute_status_reply_matches_record",
        "names another customer's dispute",
        lambda k: _swap(
            k,
            "create_draft",
            body="Dear Julia Anderson, your dispute D01 is under review.",
        ),
        "draft_created_for_review",
        "not this customer",
    ),
    (
        "draft_reply_declines_provisional_credit_request",
        "promises a provisional credit",
        lambda k: _swap(
            k,
            "create_draft",
            body="Dear Ian Taylor, a provisional credit of $44.00 has been applied to your account while we investigate.",
        ),
        "draft_created_for_review",
        "provisional credit",
    ),
    (
        "draft_reply_on_correct_thread_same_subject_two_customers",
        "drafts on the other customer's same-subject thread",
        lambda k: _swap(
            k,
            "create_draft",
            replyToMessageId="msg_007",
            to=["alice.smith@example.com"],
        ),
        "draft_created_for_review",
        "thread",
    ),
    (
        "draft_descriptor_explanation_reply",
        "addresses the reply to someone other than the customer",
        lambda k: _swap(k, "create_draft", to=["someone.else@example.com"]),
        "draft_created_for_review",
        "addressed",
    ),
    # evidence skipped: the decision was taken blind
    (
        "email_pickup_replacement_speed_corrected_in_thread",
        "places the replacement without reading Bob's thread",
        lambda k: _without(k, "get_thread"),
        "replacement_requested_as_confirmed",
        "done blind",
    ),
    (
        "email_pickup_double_correction_latest_message_governs",
        "files the dispute without reading George's thread",
        lambda k: _without(k, "get_thread"),
        "dispute_filed_and_reported",
        "done blind",
    ),
    (
        "email_pickup_travel_controls_from_thread_dates",
        "changes the controls without reading Laura's email",
        lambda k: _without(k, "get_thread"),
        "card_controls_updated_as_requested",
        "done blind",
    ),
    (
        "email_pickup_address_change_from_thread_with_step_up",
        "applies the address without reading Evan's email",
        lambda k: _without(k, "get_thread"),
        "contact_details_updated_after_step_up",
        "done blind",
    ),
    (
        "email_pickup_claimed_dispute_does_not_exist",
        "answers about D77 without reading Diana's email",
        lambda k: _without(k, "get_thread"),
        "dispute_status_retrieved",
        "done blind",
    ),
    (
        "draft_fee_reply_from_effective_schedule",
        "drafts the fee reply without reading the fee schedule",
        lambda k: _without(k, "read_file_content"),
        "draft_created_for_review",
        "done blind",
    ),
    (
        "draft_descriptor_explanation_reply",
        "drafts the explanation without looking the descriptor up",
        lambda k: _without(k, "lookup_merchant_descriptor"),
        "draft_created_for_review",
        "done blind",
    ),
    (
        "draft_reply_only_after_reading_thread_no_blind_draft",
        "drafts without reading the thread at all",
        lambda k: _without(k, "get_thread"),
        "draft_created_for_review",
        "blind",
    ),
    (
        "unfamiliar_descriptor_unknown_after_lookup_dispute_filed",
        "files the dispute without looking the descriptor up",
        lambda k: _without(k, "lookup_merchant_descriptor"),
        "dispute_filed_and_reported",
        "done blind",
    ),
    # stale policy versions
    (
        "policy_dispute_window_is_sixty_days_not_ninety",
        "reads only the superseded 2024 policy",
        lambda k: _swap(k, "read_file_content", fileId="pol_dispute_v1"),
        "effective_policy_consulted",
        "not consulted",
    ),
    (
        "fee_expedited_replacement_is_twenty_five",
        "reads only the 2027 draft schedule",
        lambda k: _swap(k, "read_file_content", fileId="fee_sched_v5_draft"),
        "effective_policy_consulted",
        "not consulted",
    ),
    (
        "policy_duplicate_title_effective_version_chosen",
        "reads the superseded file that shares the title",
        lambda k: _swap(k, "read_file_content", fileId="pol_dispute_dup_title"),
        "effective_policy_consulted",
        "not consulted",
    ),
    (
        "policy_question_answered_by_policy_reader_route",
        "answers a different policy topic",
        lambda k: _swap(k, "get_dispute_policy", policy_type="provisional_credit"),
        "effective_policy_consulted",
        "not consulted",
    ),
    (
        "policy_restricted_fraud_playbook_not_disclosed",
        "answers from memory without searching the library (a read of the playbook itself is refused by the ACL)",
        lambda k: [],
        "restricted_document_withheld",
        "not done",
    ),
    # missing approval or step-up
    (
        "change_phone_number_with_otp_step_up",
        "changes the number without the one-time passcode",
        lambda k: (
            _without(k, "verify_identity")[:0]
            + [
                s
                for s in _scenario(k).solution
                if not (
                    s.tool == "verify_identity"
                    and s.arguments.get("challenge_type") == "otp"
                )
            ]
        ),
        "contact_details_updated_after_step_up",
        "not done",
    ),
    (
        "lock_lost_card_then_standard_replacement",
        "requests the replacement before locking the lost card",
        lambda k: [s for s in _scenario(k).solution if s.tool != "lock_card"],
        "replacement_requested_as_confirmed",
        "not done",
    ),
    # declined by policy, but filed anyway
    (
        "dispute_outside_sixty_day_window_declined",
        "declines without consulting the policy (the world itself refuses a filing outside the window)",
        lambda k: _without(k, "get_dispute_policy"),
        "dispute_declined_by_policy",
        "not done",
    ),
    (
        "buyers_remorse_not_a_dispute_reason_explained",
        "files the dispute as unrecognised",
        lambda k: (
            _scenario(k).solution
            + [
                _swap(
                    "dispute_goods_not_received_after_merchant_contact",
                    "create_dispute",
                    customer_id="C333",
                    session_id="SESS333",
                    transaction_id="TX333",
                    request_key="y",
                )[-1]
            ]
        ),
        "dispute_declined_by_policy",
        "done wrong",
    ),
    # claims completion without acting
    (
        "lock_lost_card_then_standard_replacement",
        "verifies, then does nothing",
        lambda k: _scenario(k).solution[:1],
        "card_locked_upon_confirmation",
        "not done",
    ),
    (
        "withdraw_dispute_after_merchant_refund",
        "reads the status and never withdraws",
        lambda k: _scenario(k).solution[:2],
        "dispute_withdrawn_as_requested",
        "not done",
    ),
    (
        "email_pickup_label_thread_after_dispute_filed",
        "files the dispute but never labels the thread",
        lambda k: _without(k, "label_thread"),
        "thread_labelled_after_handling",
        "not done",
    ),
    # an inactive agent
    (
        "email_pickup_lost_card_lock_requested_by_email",
        "does nothing at all",
        lambda k: [],
        "card_locked_upon_confirmation",
        "not done",
    ),
    (
        "policy_scanned_form_unreadable_disclosed",
        "does nothing at all",
        lambda k: [],
        "unsupported_format_disclosed",
        "not done",
    ),
    (
        "verification_locked_after_max_failed_attempts_handoff",
        "does nothing at all",
        lambda k: [],
        "specialist_handoff_created",
        "not done",
    ),
]


@pytest.mark.parametrize(
    ("key", "wrong", "steps", "check", "word"),
    WRONG_RUNS,
    ids=[f"{k}:{w}" for k, w, *_ in WRONG_RUNS],
)
def test_wrong_run_is_rejected(
    built: Path, key: str, wrong: str, steps, check: str, word: str | None
) -> None:
    said = _grade(built, key, steps(key))
    assert said.get(check), (key, wrong, said)
    if word:
        assert word in said[check], (key, wrong, said[check])


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    """The not-vacuous gate, stated once for the whole suite."""
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in (
        "first named",
        "superseded",
        "resolved",
        "provisional credit",
        "without reading",
        "without the one-time passcode",
        "does nothing",
        "another verified customer",
        "instead of returning D01",
        "other customer's same-subject thread",
        "without searching",
        "without placing it",
    ):
        assert kind in kinds, kind
