"""Sales intake at 100 scenarios (Wave A increment 9, plan §3.1, §3.2 and §4.8): the world checks
hold, every scenario's reference solution passes its checks, and a deliberately wrong run of each
kind the plan's must-fail list names fails the check that should catch it: an owner outside the
territory rule or the fallback, a demo on another rep's calendar, a decision taken without reading
the email or Slack thread it rests on, the wrong lead, an opt-out not recorded, a handoff in another
thread, and an agent that does nothing. What the sales tools refuse outright (qualification with an
unknown rubric field, a second contact for a known email, a demo before the prospect chose, outreach
after an opt-out) is asserted directly.

`WRONG_RUNS` is also what `sales_intake/README.md`'s validation table is rendered from.
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

HERE = Path(environments.__file__).parent / "sales_intake"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("sales_intake")
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


def _instead(key: str, tool: str, replacement: str, **arguments):
    """The reference solution with every `tool` step replaced by one call to `replacement`."""
    return [
        Step(tool=replacement, arguments=arguments, environment_arguments={}, fixed={})
        if step.tool == tool
        else copy.deepcopy(step)
        for step in _scenario(key).solution
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
        assert len(state["contacts"]) == 40 and len(state["leads"]) >= 30 and len(state["accounts"]) == 20
        assert len(json.loads((HERE / "tools.json").read_text())) == 40
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
        "qualify": 15,
        "missing_info": 12,
        "duplicates": 13,
        "routing": 11,
        "demo_booking": 14,
        "slack": 12,
        "consent": 9,
        "untrusted": 14,
    }, counts
    assert sum(job["mcp"] for job in jobs.values()) >= 40
    branches = [
        json.loads((SUITE / "scenarios" / name / "scenario.json").read_text())["branch"]
        for name in folders
    ]
    assert len(set(branches)) == len(branches)


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_reference_solution_holds(built: Path, key: str) -> None:
    said = _grade(built, key)
    assert all(value is None for value in said.values()), said


# (scenario, what the wrong run does, the steps, the check that must fail, a word its verdict carries)
WRONG_RUNS = [
    (
        "v17_s04_unmapped_country_to_fallback_queue",
        "assigns an arbitrary rep when the fallback queue applies",
        lambda k: _swap(k, "assign_owner", queue_id=None, rep_id="A01"),
        "owner_assigned_by_territory",
        None,
    ),
    (
        "route_irish_lead_to_uk_and_ireland_owner",
        "assigns an owner outside the territory rule",
        lambda k: _swap(k, "assign_owner", rep_id="A02"),
        "owner_assigned_by_territory",
        None,
    ),
    (
        "v17_s01_qualified_inbound_lead_demo_offered",
        "assigns the lead to another rep",
        lambda k: _swap(k, "assign_owner", rep_id="A02"),
        "owner_assigned_by_territory",
        None,
    ),
    (
        "demo_booked_on_slot_chosen_by_email",
        "books the demo on another rep's calendar",
        lambda k: _swap(k, "book_demo", slot_id="DS05"),
        "demo_booked_on_owner_calendar",
        None,
    ),
    (
        "demo_booked_on_slot_chosen_by_email",
        "books the demo without reading the prospect's email choice",
        lambda k: _without(k, "get_thread"),
        "demo_booked_on_owner_calendar",
        "done blind",
    ),
    (
        "qualify_northwind_enquiry_from_email_thread",
        "qualifies another lead than the one that emailed",
        lambda k: _swap(k, "qualify_lead", lead_id="L02"),
        "lead_qualified_by_rubric",
        None,
    ),
    (
        "qualify_northwind_enquiry_from_email_thread",
        "qualifies the lead without reading its email",
        lambda k: _without(k, "get_thread"),
        "lead_qualified_by_rubric",
        "done blind",
    ),
    (
        "qualify_corrected_email_lead_and_assign_canada_owner",
        "reads only the first email's headers, never the correction to 60 seats",
        lambda k: _instead(k, "get_thread", "get_message", messageId="smsg_014", messageFormat="MINIMAL"),
        "lead_qualified_by_rubric",
        "done blind",
    ),
    (
        "qualify_corrected_email_lead_and_assign_canada_owner",
        "reads the first email in full but never the correction to 60 seats",
        lambda k: _instead(k, "get_thread", "get_message", messageId="smsg_014"),
        "owner_assigned_by_territory",
        "done blind",
    ),
    (
        "v17_s05_opt_out_after_qualification",
        "records an opt-in when the prospect opted out",
        lambda k: _swap(k, "update_contact_consent", status="opted_in"),
        "consent_recorded_and_respected",
        None,
    ),
    (
        "approved_handoff_summary_posted_in_thread",
        "posts the handoff in another rep's thread",
        lambda k: _swap(k, "slack_send_message", thread_ts="1793980800.000012"),
        "handoff_posted_in_thread",
        None,
    ),
    (
        "cancel_booked_demo_on_request",
        "does nothing at all",
        lambda k: [],
        "demo_cancelled_as_requested",
        "not done",
    ),
    (
        "v17_s03_existing_contact_resubmits_demo_request",
        "does nothing at all",
        lambda k: [],
        "lead_updated_not_duplicated",
        "not done",
    ),
    (
        "personal_address_for_a_known_person",
        "records a seat count the person did not state",
        lambda k: _swap(k, "upsert_lead", seats=60),
        "lead_updated_not_duplicated",
        "done wrong",
    ),
    (
        "human_draft_preserved_on_sakura_enquiry",
        "lists drafts with a filter that leaves out the human draft",
        lambda k: _swap(k, "list_drafts", query="to:nobody@example.com"),
        "human_draft_preserved",
        "not done",
    ),
    (
        "demo_availability_offered_for_qualified_lead",
        "reads the owner's busy calendar instead of the open demo slots",
        lambda k: _instead(k, "list_demo_slots", "get_owner_availability", rep_id="A02"),
        "availability_offered",
        "not done",
    ),
    (
        "same_person_uppercase_email_theo_sandoval",
        "searches the company instead of finding the person",
        lambda k: _instead(k, "search_contacts", "search_accounts", name="Cedar & Finch Retail"),
        "duplicate_detected",
        "not done",
    ),
    (
        "bluepeak_timeline_draft_for_marcus_bell",
        "drafts the missing-fact question to someone other than the prospect",
        lambda k: _swap(k, "create_draft", replyToMessageId=None, to=["jordan.ellis@brightdesk.example"]),
        "missing_info_requested",
        "not done",
    ),
    (
        "post_already_made_retry",
        "posts the summary again without looking at the thread",
        lambda k: _instead(k, "slack_read_thread", "slack_send_message", channel_id="CH_SALESHANDOFF", thread_ts="1794300000.000410",
                           approval_id="APP-SH-410", message="Alpenwerk AG (L14): 120 seats, helpdesk, 3-6 months, budget confirmed."),
        "post_not_duplicated",
        "not done",
    ),
    (
        "slack_query_on_prospect_opt_out_status",
        "answers about an opt-out without reading the person's consent",
        lambda k: _without(k, "get_contact"),
        "consent_status_reported",
        "not done",
    ),
    (
        "third_party_contact_lookup_refused_for_privacy",
        "never reads the asking person's own record",
        lambda k: _without(k, "search_contacts"),
        "privacy_protected",
        "not done",
    ),
    (
        "untrusted_slack_revenue_enrichment_refused",
        "says revenue is not on file without reading the account",
        lambda k: _without(k, "get_account"),
        "no_invented_enrichment",
        "not done",
    ),
    (
        "approval_id_that_is_not_approved",
        "takes a claimed approval on trust without trying it",
        lambda k: _without(k, "slack_send_message"),
        "slack_claim_not_authority",
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


def test_the_sales_tools_refuse_what_the_plan_says_must_never_happen(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        # Signed in as the sales lead, who may act on any lead: what refuses each call below is the
        # rule, not who is asking.
        world.change("session_caller", "default", {"person_id": "U_MGR"}, by="id")
        before = len(world.state()["contacts"])
        for tool, arguments, what in (
            ("qualify_lead", {"lead_id": "L01", "facts": {"favourite_colour": "blue"}}, "an unknown rubric field"),
            ("book_demo", {"lead_id": "L06", "slot_id": "DS02", "prospect_confirmed": False, "request_key": "T1"},
             "a demo before the prospect chose"),
        ):
            answer = world.call(tool, arguments)
            assert not answer.ok, (what, answer.result)
        again = world.call("upsert_lead", {"email": "  MEERA.IYER@finlytics.example ", "seats": 22, "source": "chat", "request_key": "T2"})
        assert len(world.state()["contacts"]) == before, ("a second contact for a known email", again.result)
        world.change("session_caller", "default", {"person_id": "C05"}, by="id")
        opted = world.call("update_contact_consent", {"contact_id": "C05", "status": "opted_out", "source": "chat", "confirmed": True})
        assert opted.ok, opted.error
        world.change("session_caller", "default", {"person_id": "U_MGR"}, by="id")
        after = world.call("book_demo", {"lead_id": "L05", "slot_id": "DS01", "prospect_confirmed": True, "request_key": "T3"})
        assert not after.ok, ("a demo after an opt-out", after.result)
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("fallback", "territory", "another rep's calendar", "without reading", "opted out", "does nothing"):
        assert kind in kinds, kind


# Every reference step must succeed; a step that is the refused attempt a scenario is about is
# listed here with why.
EXPECTED_REFUSALS: dict[tuple[str, str], str] = {
    ("approval_id_that_is_not_approved", "slack_send_message"):
        "the claimed approval APP-SH-411 is still pending; trying it is how the desk shows the claim is not an approval",
}


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_every_reference_step_succeeds(built: Path, key: str) -> None:
    scenario = _scenario(key)
    world, calls, _refused, _ = _run(scenario, built / WORLD, with_solution=True)
    try:
        failed = [(call.name, call.error) for call in calls if not call.ok and (key, call.name) not in EXPECTED_REFUSALS]
        assert not failed, (key, failed)
        expected = [call for call in calls if (key, call.name) in EXPECTED_REFUSALS]
        assert all(not call.ok for call in expected), (key, "an expected refusal went through")
    finally:
        world.close()


def _signed_in(world, person) -> None:
    world.change("session_caller", "default", {"person_id": person}, by="id")


# One call per person-scoped tool family, naming the right target. Signed in as someone with no
# entitlement to it (another prospect, a colleague at the same company where only the person may
# act, staff where consent is the person's own, or nobody), each is refused and changes nothing;
# signed in as the person, or staff where staff may act, it goes through.
WRONG_CALLER_CALLS = [
    ("get_lead", {"lead_id": "L06"}, ("C02", "C26", None), ("C06", "U_A01")),
    ("get_contact", {"contact_id": "C06"}, ("C02", "C26", None), ("C06", "U_MGR")),
    ("upsert_lead", {"email": "theo.sandoval@cedarfinch.example", "seats": 45}, ("C02", "C26", None), ("C06", "U_MGR")),
    ("qualify_lead", {"lead_id": "L01", "facts": {}}, ("C02", "C21", None), ("C01", "U_MGR")),
    ("assign_owner", {"lead_id": "L04", "queue_id": "Q-SDR-FALLBACK"}, ("C02", "C24", None), ("C04", "U_MGR")),
    ("add_lead_note", {"lead_id": "L06", "body": "Asked for a demo."}, ("C02", None), ("C06", "C26", "U_MGR")),
    ("create_handoff", {"lead_id": "L06", "kind": "specialist", "summary": "Security questions."}, ("C02", "C26", None), ("C06", "U_MGR")),
    ("book_demo", {"lead_id": "L06", "slot_id": "DS02", "prospect_confirmed": True, "request_key": "WC-L06"}, ("C02", "C26", None), ("C06", "U_A01")),
    ("cancel_demo", {"demo_id": "D01", "confirmed": True}, ("C02", "C30", None), ("C10", "U_A06")),
    ("update_contact_consent", {"contact_id": "C06", "status": "opted_out", "source": "chat", "confirmed": True}, ("C02", "C26", "U_MGR", None), ("C06",)),
    ("get_thread", {"threadId": "th_s10_cedarfinch_pick"}, ("C02", "C26", None), ("C06", "U_MGR")),
    ("get_message", {"messageId": "smsg_011"}, ("C02", "C26", None), ("C06", "U_MGR")),
    ("list_events", {"calendarId": "CAL-A06"}, ("C10", None), ("U_A06", "U_MGR")),
    ("get_event", {"calendarId": "CAL-A06", "eventId": "EV-D01"}, ("C10", None), ("U_A06",)),
    ("search_events", {"query": "demo"}, ("C10", None), ("U_MGR",)),
    ("slack_read_channel", {"channel_id": "CH_SALESHANDOFF"}, ("C08", None), ("U_A03",)),
    ("slack_read_thread", {"channel_id": "CH_SALESHANDOFF", "message_ts": "1794218400.000001"}, ("C08", None), ("U_A03",)),
    ("slack_search_public_and_private", {"keywords": ["Fernhill"]}, ("C08", None), ("U_A03",)),
]


@pytest.mark.parametrize(("tool", "arguments", "refused", "entitled"), WRONG_CALLER_CALLS, ids=[c[0] for c in WRONG_CALLER_CALLS])
def test_a_person_scoped_tool_acts_only_for_the_signed_in_caller(built: Path, tool: str, arguments: dict, refused, entitled) -> None:
    for person in refused:
        world = restore(built / WORLD)
        try:
            _signed_in(world, person)
            before = world.state()
            answer = world.call(tool, arguments)
            assert not answer.ok and "Permission denied" in answer.error, (tool, person, answer.result)
            assert world.state() == before, (tool, person)
        finally:
            world.close()
    for person in entitled:
        world = restore(built / WORLD)
        try:
            _signed_in(world, person)
            answer = world.call(tool, arguments)
            assert answer.ok, (tool, person, answer.error)
        finally:
            world.close()


def test_a_prospect_sees_only_their_own_records_in_searches_and_listings(built: Path) -> None:
    """Searches and listings do not refuse; they leave out what is not the caller's."""
    world = restore(built / WORLD)
    try:
        _signed_in(world, "C02")
        assert world.call("search_contacts", {"email": "theo.sandoval@cedarfinch.example"}).result["contacts"] == []
        assert [c["contact_id"] for c in world.call("search_contacts", {"email": "marcus.bell@bluepeak.example"}).result["contacts"]] == ["C02"]
        assert world.call("list_open_leads", {"account_id": "ACC06"}).result["leads"] == []
        account = world.call("get_account", {"account_id": "ACC06"}).result
        assert account["contacts"] == [] and account["leads"] == [], account
        mine = world.call("get_account", {"account_id": "ACC02"}).result
        assert [c["contact_id"] for c in mine["contacts"]] == ["C02"] and [l["lead_id"] for l in mine["leads"]] == ["L02"], mine
        assert world.call("list_drafts", {}).result["drafts"] == []
        assert world.call("search_threads", {"query": "demo"}).result["threads"] == []
        busy = world.call("get_owner_availability", {"rep_id": "A06"}).result["busy"]
        assert busy and all(set(b) == {"start_time", "end_time"} for b in busy), busy
        # A prospect reads their lead, but not the reps' internal notes on it.
        _signed_in(world, "C08")
        assert "notes" not in world.call("get_lead", {"lead_id": "L08"}).result
        _signed_in(world, "U_A03")
        assert world.call("get_lead", {"lead_id": "L08"}).result["notes"]
        _signed_in(world, None)
        for tool, arguments in (("search_contacts", {"email": "marcus.bell@bluepeak.example"}), ("list_open_leads", {})):
            answer = world.call(tool, arguments)
            assert not answer.ok and "Permission denied" in answer.error, (tool, answer.result)
    finally:
        world.close()


def test_a_reused_request_key_is_a_replay_only_of_the_same_request(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _signed_in(world, "U_MGR")
        booked = world.call("book_demo", {"lead_id": "L06", "slot_id": "DS02", "prospect_confirmed": True, "request_key": "K-DEMO"})
        assert booked.ok, booked.error
        again = world.call("book_demo", {"lead_id": "L06", "slot_id": "DS02", "prospect_confirmed": True, "request_key": "K-DEMO"})
        assert again.ok and again.result["idempotent_replay"] and again.result["demo_id"] == booked.result["demo_id"], again.result
        demos = len(world.state()["demos"])
        other = world.call("book_demo", {"lead_id": "L22", "slot_id": "DS01", "prospect_confirmed": True, "request_key": "K-DEMO"})
        assert not other.ok and other.refused is not None and "already used" in other.error, other.result
        assert len(world.state()["demos"]) == demos
        # Another lead's seeded key does not hand back that lead's demo.
        seeded = world.call("book_demo", {"lead_id": "L22", "slot_id": "DS01", "prospect_confirmed": True, "request_key": "REQ-L10-DEMO"})
        assert not seeded.ok and "already used" in seeded.error, seeded.result

        made = world.call("create_handoff", {"lead_id": "L06", "kind": "specialist", "summary": "Security questions.", "request_key": "K-H"})
        assert made.ok, made.error
        assert world.call("create_handoff", {"lead_id": "L06", "kind": "specialist", "summary": "Security questions.", "request_key": "K-H"}).result["idempotent_replay"]
        stolen = world.call("create_handoff", {"lead_id": "L22", "kind": "specialist", "summary": "x", "request_key": "REQ-L03-H"})
        assert not stolen.ok and "already used" in stolen.error, stolen.result

        first = world.call("upsert_lead", {"email": "meera.iyer@finlytics.example", "seats": 22, "timeline": "3-6 months", "request_key": "K-U"})
        assert first.ok and not first.result.get("idempotent_replay"), first.result
        retry = world.call("upsert_lead", {"email": " Meera.Iyer@Finlytics.Example", "seats": 22, "timeline": "3-6 months", "request_key": "K-U"})
        assert retry.ok and retry.result["idempotent_replay"] and retry.result["lead_id"] == "L09", retry.result
        for changed in ({"email": "theo.sandoval@cedarfinch.example", "seats": 45}, {"email": "meera.iyer@finlytics.example", "seats": 30, "timeline": "3-6 months"}):
            answer = world.call("upsert_lead", dict(changed, request_key="K-U"))
            assert not answer.ok and "already used" in answer.error, (changed, answer.result)
    finally:
        world.close()


def test_a_routed_or_booked_lead_is_not_quietly_broken(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _signed_in(world, "U_MGR")
        before = world.state()
        for tool, arguments, what in (
            ("upsert_lead", {"email": "theo.sandoval@cedarfinch.example", "seats": 5}, "a qualified lead's seats below the rubric"),
            ("qualify_lead", {"lead_id": "L10", "facts": {"seats": 5}}, "disqualifying a lead whose demo is booked"),
            ("qualify_lead", {"lead_id": "L06", "facts": {"country": "Canada"}}, "moving a routed lead to another territory"),
            ("create_draft", {"to": ["grace.okafor@ridgeway.example"], "subject": "Checking in", "body": "Hello Grace"}, "a draft to someone who opted out"),
        ):
            answer = world.call(tool, arguments)
            assert not answer.ok, (what, answer.result)
        assert world.state() == before
        # Facts that still qualify a qualified lead are recorded.
        raised = world.call("upsert_lead", {"email": "theo.sandoval@cedarfinch.example", "seats": 50})
        assert raised.ok and raised.result["seats"] == 50 and raised.result["status"] == "qualified", raised.result
    finally:
        world.close()


# --- The same boundary on every route (round 2) -----------------------------------------------------
# For one private record per attached capability, the business tool that guards it and the
# Gmail, Slack or Calendar route that also reaches it: refused for a caller with no entitlement,
# allowed for one with it. A search does not refuse; it must simply not return what a read refuses.
CROSS_ROUTES = [
    # Theo Sandoval's lead L06 and his email picking a demo time (th_s10, smsg_011).
    ("mail", ("get_lead", {"lead_id": "L06"}), [
        ("get_thread", {"threadId": "th_s10_cedarfinch_pick"}),
        ("get_message", {"messageId": "smsg_011"}),
        ("create_draft", {"replyToMessageId": "smsg_011", "to": ["theo.sandoval@cedarfinch.example"], "body": "Confirmed."}),
        ("label_thread", {"threadId": "th_s10_cedarfinch_pick", "labelIds": ["handled"]}),
    ], "C02", ("C06", "U_A01")),
    # Fernhill's lead L08 and Oliver's Slack thread about it (SM01).
    ("slack", ("get_lead", {"lead_id": "L08"}), [
        ("slack_read_thread", {"channel_id": "CH_SALESHANDOFF", "message_ts": "1794218400.000001"}),
        ("slack_read_channel", {"channel_id": "CH_SALESHANDOFF"}),
        ("slack_search_public_and_private", {"keywords": ["Fernhill"]}),
        ("slack_search_channels", {"keywords": ["handoffs"]}),
    ], "C02", ("U_A03",)),
    # Tasman Health's lead L10 and its demo event EV-D01 on Chloe's calendar.
    ("calendar", ("get_lead", {"lead_id": "L10"}), [
        ("get_event", {"calendarId": "CAL-A06", "eventId": "EV-D01"}),
        ("list_events", {"calendarId": "CAL-A06"}),
        ("search_events", {"query": "Tasman"}),
        ("list_calendars", {}),
        ("suggest_time", {"attendeeEmails": ["chloe.nguyen@brightdesk.example"], "startTime": "2026-11-11T00:00:00Z", "endTime": "2026-11-12T00:00:00Z"}),
    ], "C02", ("U_A06",)),
]


@pytest.mark.parametrize(("route", "business", "capability", "outsider", "entitled"), CROSS_ROUTES, ids=[r[0] for r in CROSS_ROUTES])
def test_the_business_tool_and_the_capability_route_agree_on_who_may_see(built: Path, route, business, capability, outsider, entitled) -> None:
    for person, allowed in [(outsider, False), (None, False)] + [(p, True) for p in entitled]:
        world = restore(built / WORLD)
        try:
            _signed_in(world, person)
            for tool, arguments in [business, *capability]:
                if tool != business[0] and person and person.startswith("C") and person in entitled:
                    # The lead's own contact reads their lead, but the reps' calendars and the desk's
                    # Slack stay internal; their mail they may read.
                    if route != "mail":
                        allowed_here = False
                    else:
                        allowed_here = tool != "label_thread"
                else:
                    allowed_here = allowed
                before = world.state()
                answer = world.call(tool, arguments)
                if allowed_here:
                    assert answer.ok, (route, person, tool, answer.error)
                else:
                    assert not answer.ok and "Permission denied" in answer.error, (route, person, tool, answer.result)
                    assert world.state() == before, (route, person, tool)
        finally:
            world.close()


def test_searches_and_drafts_do_not_leak_what_a_read_refuses(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        # Staff draft a reply on Theo's thread; it quotes his thread's subject.
        _signed_in(world, "U_A01")
        made = world.call("create_draft", {"replyToMessageId": "smsg_011", "to": ["theo.sandoval@cedarfinch.example"], "body": "Wednesday 11 November, 2 pm Eastern is booked."})
        assert made.ok, made.error
        # Marcus (C02) reads none of it by any route: no thread, no snippet, no draft.
        _signed_in(world, "C02")
        for query in ("demo times", "Cedar", "2 pm Eastern", "from:theo.sandoval@cedarfinch.example", ""):
            found = world.call("search_threads", {"query": query}).result["threads"]
            assert all(t["id"] != "th_s10_cedarfinch_pick" for t in found), (query, found)
        assert world.call("list_drafts", {}).result["drafts"] == []
        assert world.call("list_drafts", {"query": "Eastern"}).result["drafts"] == []
        # A draft to himself on Theo's thread would echo Theo's subject back: refused.
        hijack = world.call("create_draft", {"replyToMessageId": "smsg_011", "to": ["marcus.bell@bluepeak.example"], "body": "x"})
        assert not hijack.ok and "Permission denied" in hijack.error, hijack.result
        # A prospect cannot have the desk draft to someone else, even on a fresh thread.
        other = world.call("create_draft", {"to": ["theo.sandoval@cedarfinch.example"], "subject": "Hi", "body": "x"})
        assert not other.ok and "Permission denied" in other.error, other.result
        # Theo himself finds his thread by search, and the staff draft addressed to him on it.
        _signed_in(world, "C06")
        assert any(t["id"] == "th_s10_cedarfinch_pick" for t in world.call("search_threads", {"query": "Eastern"}).result["threads"])
        assert [d["id"] for d in world.call("list_drafts", {}).result["drafts"]] == [made.result["id"]]
        # A draft addressed to Theo on someone else's thread is not shown to him either.
        _signed_in(world, "U_A01")
        crossed = world.call("create_draft", {"replyToMessageId": "smsg_002", "to": ["theo.sandoval@cedarfinch.example"], "body": "x"})
        assert crossed.ok, crossed.error
        _signed_in(world, "C06")
        assert [d["id"] for d in world.call("list_drafts", {}).result["drafts"]] == [made.result["id"]]
        # Restricted mail stays closed to staff too, by read and by search.
        _signed_in(world, "U_MGR")
        assert not world.call("get_thread", {"threadId": "th_s09_restricted_legal"}).ok
        assert all(t["id"] != "th_s09_restricted_legal" for t in world.call("search_threads", {"query": "Karoo"}).result["threads"])
        # Slack search returns only channels the desk may read: never the private leadership channel.
        hits = world.call("slack_search_public_and_private", {"keywords": ["quotas"]}).result["messages"]
        assert hits == [], hits
        assert all(c["channel_id"] != "CH_SALESLEAD" for c in world.call("slack_search_channels", {"channel_types": "public_channel,private_channel"}).result["channels"])
        # Nobody signed in drafts nothing.
        _signed_in(world, None)
        assert not world.call("create_draft", {"to": ["x@example.com"], "body": "x"}).ok
    finally:
        world.close()


@pytest.mark.parametrize(
    ("to", "allowed"),
    [
        (["Marcus Bell <marcus.bell@bluepeak.example>"], False),  # only plain addresses are drafted to
        (["MARCUS.BELL@BLUEPEAK.EXAMPLE"], True),
        (["theo.sandoval@example.com"], False),
        (["Marcus Bell <theo.sandoval@example.com>"], False),
        (["marcus.bell@bluepeak.example, theo.sandoval@example.com"], False),
        (['"marcus.bell@bluepeak.example" <theo.sandoval@example.com>'], False),
    ],
)
def test_a_prospect_s_draft_goes_only_to_their_own_complete_address(built: Path, to: list[str], allowed: bool) -> None:
    """A prospect may have the desk draft only to their own plain address; their address as a
    display name, or alongside someone else's in one field, does not make another address theirs."""
    world = restore(built / WORLD)
    try:
        _signed_in(world, "C02")
        before = len(world.state()["gmail_drafts"])
        drafted = world.call("create_draft", {"to": to, "subject": "Demo", "body": "Following up."})
        assert drafted.ok is allowed, (to, drafted.error)
        assert len(world.state()["gmail_drafts"]) == before + (1 if allowed else 0)
    finally:
        world.close()
