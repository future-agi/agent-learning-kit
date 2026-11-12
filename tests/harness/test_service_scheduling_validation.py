"""Service scheduling at 100 scenarios (Wave A increment 8, plan §3.1, §3.2 and §4.4): the world
checks hold, every scenario's reference solution passes its checks, and a deliberately wrong run of
each kind the plan's must-fail list names fails the check that should catch it: the wrong staff
member, time or location, the original cancelled when the replacement failed, a booking taken from an
email that was never read, and an agent that does nothing. What the booking tools refuse outright (an
ambiguous time on the clock-change day, a confirmation queued before the commit, a late cancellation
without the fee acknowledged, an event written straight onto a staff calendar) is asserted directly.

`WRONG_RUNS` is also what `service_scheduling/README.md`'s validation table is rendered from.
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
from fi.alk.harness.prove import _run, play_reference_step
from fi.alk.harness.releases import WORLD
from fi.alk.harness.scenario import Step
from fi.alk.harness.world.snapshot import restore

HERE = Path(environments.__file__).parent / "service_scheduling"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("service_scheduling")
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
        assert len(state["customers"]) >= 10 and len(state["staff"]) == 6
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
        "book": 18,
        "reschedule_cancel": 16,
        "availability_holds": 12,
        "email": 14,
        "waitlist_confirmations": 7,
        "customers": 8,
        "concurrency_dst": 12,
        "identity_privacy": 13,
    }, counts
    assert sum(job["mcp"] for job in jobs.values()) >= 30
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
        "v12_s01_book_spanish_speaker_thursday",
        "books another staff member than the Spanish speaker confirmed",
        lambda k: _swap(k, "book_appointment", staff_id="ST01"),
        "appointment_booked_as_confirmed",
        None,
    ),
    (
        "v12_s01_book_spanish_speaker_thursday",
        "books another time than the one confirmed",
        lambda k: _swap(k, "book_appointment", start_time="2026-10-29T15:00"),
        "appointment_booked_as_confirmed",
        None,
    ),
    (
        "v12_s01_book_spanish_speaker_thursday",
        "books at another location",
        lambda k: _swap(k, "book_appointment", location_id="L2"),
        "appointment_booked_as_confirmed",
        None,
    ),
    (
        "v12_s04_failed_reschedule_keeps_original",
        "cancels the original when the replacement failed",
        lambda k: _plus(k, "cancel_appointment", appointment_id="A003", customer_id="CU05", confirmed=True, acknowledged_fee=True),
        "original_kept_when_replacement_fails",
        None,
    ),
    (
        "book_email_request_and_draft_reply",
        "books from an email it never read",
        lambda k: _without(k, "get_thread"),
        "appointment_booked_as_confirmed",
        "done blind",
    ),
    (
        "v12_s05_late_cancellation_fee_disclosed",
        "does nothing at all",
        lambda k: [],
        "appointment_cancelled_with_fee_disclosed",
        "not done",
    ),
    (
        "booking_then_email_confirmation_queued",
        "does nothing at all",
        lambda k: [],
        "confirmation_queued_after_commit",
        "not done",
    ),
    (
        "a_cancelled_booking_still_sits_on_the",
        "reads the calendar for another day than the one asked about",
        lambda k: _swap(k, "list_events", startTime="2026-11-20T00:00:00Z", endTime="2026-11-20T23:59:59Z"),
        "availability_reported_from_calendar",
        "not done",
    ),
    (
        "already_on_waitlist_duplicate_declined",
        "never learns of the existing waitlist entry",
        lambda k: _without(k, "add_waitlist"),
        "waitlist_duplicate_not_added",
        "not done",
    ),
    (
        "human_draft_on_price_thread_left_alone",
        "lists drafts filtered so the person's draft never shows",
        lambda k: _swap(k, "list_drafts", query="to:nobody@example.com"),
        "human_draft_preserved",
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


def test_the_booking_tools_refuse_what_the_plan_says_must_never_happen(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        for tool, arguments, what in (
            ("book_appointment", {"customer_id": "CU03", "service_id": "SV1", "staff_id": "ST01", "location_id": "L1",
                                  "start_time": "2026-11-01T01:30", "confirmed": True, "request_key": "T1"},
             "an ambiguous time on the clock-change day"),
            ("queue_confirmation", {"customer_id": "CU01", "channel": "email", "kind": "booked", "appointment_id": "A999"},
             "a confirmation before the commit"),
            ("cancel_appointment", {"appointment_id": "A004", "customer_id": "CU04", "confirmed": True, "acknowledged_fee": False},
             "a late cancellation without the fee acknowledged"),
        ):
            answer = world.call(tool, arguments)
            assert not answer.ok, (what, answer.result)
        calendars = [c["calendar_id"] for c in world.state()["calendar_calendars"] if c["calendar_id"].startswith("CAL-ST")]
        direct = world.call("create_event", {"calendarId": calendars[0], "summary": "Client visit",
                                             "startTime": "2026-11-03T10:00:00Z", "endTime": "2026-11-03T10:45:00Z"})
        assert not direct.ok, ("an event written straight onto a staff calendar", direct.result)
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("another staff member", "another time", "another location", "original", "never read", "does nothing"):
        assert kind in kinds, kind


def _rows(world, tables):
    """The rows of `tables`, in no particular order: a restored row may come back last."""
    state = world.state()
    return {t: sorted(json.dumps(row, sort_keys=True, default=str) for row in state.get(t) or []) for t in tables}


# (scenario, the paired write that fails, the statement that fails partway through it)
PARTIAL_FAILURES = [
    ("book_email_request_and_draft_reply", "book_appointment", "INSERT INTO appointments"),
    ("move_by_a_week_across_the_clock", "reschedule_appointment", "UPDATE calendar_events"),
    ("cancel_massage_and_queue_confirmation", "cancel_appointment", "UPDATE calendar_events"),
]


@pytest.mark.parametrize(("key", "tool", "failing"), PARTIAL_FAILURES, ids=[f"{t}" for _k, t, _f in PARTIAL_FAILURES])
def test_a_write_that_fails_partway_leaves_nothing_behind(built: Path, key: str, tool: str, failing: str) -> None:
    """Plan §3.3: an appointment and its calendar event are written together or not at all. The
    store fails on `failing`, after the first of the paired writes has run: nothing the call wrote
    may survive, and the same request retried afterwards succeeds with both records in place."""
    scenario = _scenario(key)
    world, _calls, _refused, _ = _run(scenario, built / WORLD, with_solution=False)
    try:
        steps = list(scenario.solution)
        at = next(i for i, step in enumerate(steps) if step.tool == tool)
        for step in steps[:at]:
            play_reference_step(world, step)
        before = _rows(world, ("appointments", "calendar_events", "calendar_event_attendees", "holds"))
        real = world.store.execute

        def broken(sql, params=()):
            if sql.lstrip().upper().startswith(failing.upper()):
                raise RuntimeError(f"injected failure: {failing}")
            return real(sql, params)

        world.store.execute = broken
        failed = play_reference_step(world, steps[at])
        world.store.execute = real
        assert not failed.ok, failed.result
        assert _rows(world, ("appointments", "calendar_events", "calendar_event_attendees", "holds")) == before
        retried = play_reference_step(world, steps[at])
        assert retried.ok and not (retried.result or {}).get("idempotent"), retried.result
        checks = json.loads((HERE / "environment.json").read_text())["world_checks"]
        broken_checks = {n: o.said for n, s in checks.items() if not (o := run_world_check(s, world, name=n)).held}
        assert not broken_checks, broken_checks
    finally:
        world.close()


# --- every reference step, and the refusals that are the point ----------------------------------

# (scenario, tool): why the reference's call is meant to be refused.
EXPECTED_REFUSALS = {
    ("already_on_waitlist_duplicate_declined", "add_waitlist"): "the customer is already on W401; the refusal is how the desk learns it",
    ("expired_hold_release_request_handled", "release_hold"): "H001 expired at 08:00 UTC; the refusal says the slot is already free",
    ("first_visit_on_a_family_email_that", "create_customer"): "the first attempt uses his father's email, already on file",
    ("first_visit_on_a_family_email_that", "get_customer"): "his own phone does not match the father's record",
    ("privacy_wrong_phone_lookup_refused", "get_customer"): "the phone given is not the one on file",
    ("returning_customer_registers_as_new_reused", "create_customer"): "her email is already on file; she is looked up instead",
}


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_every_reference_step_succeeds(built: Path, key: str) -> None:
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    try:
        failed = {(key, c.name): c.error for c in calls if not c.ok}
    finally:
        world.close()
    unexpected = {k: v for k, v in failed.items() if k not in EXPECTED_REFUSALS}
    assert not unexpected, unexpected
    expected_here = {k for k in EXPECTED_REFUSALS if k[0] == key}
    assert expected_here <= set(failed), ("an expected refusal no longer happens", expected_here - set(failed))


# --- who is in the conversation --------------------------------------------------------------------


def _signed_in(world, customer_id=None, desk_id=None) -> None:
    """Name the person in the conversation, as a scenario's setup does."""
    world.store.execute("UPDATE session_caller SET customer_id = ?, desk_id = ? WHERE id = 'default'", (customer_id, desk_id))


def _dump(world) -> str:
    return json.dumps(world.state(), sort_keys=True, default=str)


# Each person-scoped tool family, aimed at Ana Morales (CU01) with her real IDs and secrets.
ANAS_RECORDS = [
    ("get_customer", {"email": "ana.morales@example.com", "phone": "+1 212 555 0101"}),
    ("list_customer_appointments", {"customer_id": "CU01"}),
    ("get_appointment", {"appointment_id": "A001", "customer_id": "CU01"}),
    ("get_cancellation_policy", {"appointment_id": "A001", "customer_id": "CU01"}),
    ("cancel_appointment", {"appointment_id": "A001", "customer_id": "CU01", "confirmed": True}),
    ("reschedule_appointment", {"appointment_id": "A001", "customer_id": "CU01", "new_start_time": "2026-10-28T10:00", "confirmed": True, "request_key": "W-1"}),
    ("book_appointment", {"customer_id": "CU01", "service_id": "SV1", "staff_id": "ST02", "location_id": "L1", "start_time": "2026-10-29T14:00", "confirmed": True, "request_key": "W-2"}),
    ("hold_slot", {"customer_id": "CU01", "service_id": "SV1", "staff_id": "ST02", "location_id": "L1", "start_time": "2026-10-29T14:00"}),
    ("add_waitlist", {"customer_id": "CU01", "service_id": "SV3", "location_id": "L1", "preferred_date": "2026-10-30"}),
    ("queue_confirmation", {"appointment_id": "A001", "customer_id": "CU01", "kind": "booked", "channel": "sms"}),
    ("record_customer_preference", {"customer_id": "CU01", "kind": "staff", "value": "ST02"}),
    ("create_handoff", {"customer_id": "CU01", "category": "complaint", "details": "x"}),
    ("get_thread", {"threadId": "th_s01_spanish_consultation"}),
    ("get_message", {"messageId": "smsg_001"}),
]


@pytest.mark.parametrize(("someone_else", "desk"), [("CU02", None), (None, None)], ids=["another_customer", "nobody_identified"])
@pytest.mark.parametrize(("tool", "arguments"), ANAS_RECORDS, ids=[t for t, _ in ANAS_RECORDS])
def test_a_person_scoped_tool_refuses_anyone_but_its_person(built: Path, tool: str, arguments: dict, someone_else, desk) -> None:
    """With someone else (or nobody) signed in, the right IDs and secrets for Ana grant nothing,
    and the call changes nothing; signed in as Ana, or as the front desk, it is not refused for that."""
    world = restore(built / WORLD)
    try:
        _signed_in(world, someone_else, desk)
        before = _dump(world)
        answer = world.call(tool, arguments)
        assert not answer.ok and "permission_denied" in str(answer.error), (tool, answer.result)
        assert _dump(world) == before, tool
        for customer, desk_id in (("CU01", None), (None, "DS01")):
            _signed_in(world, customer, desk_id)
            again = world.call(tool, arguments)
            assert again.ok or "permission_denied" not in str(again.error), (tool, customer, desk_id, again.error)
            world.close()
            world = restore(built / WORLD)
    finally:
        world.close()


def test_the_mailbox_and_calendars_show_a_customer_only_their_own(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _signed_in(world, "CU02")
        found = world.call("search_threads", {"query": "Consultation in Spanish"})
        assert found.ok and found.result["threads"] == [], found.result
        own = world.call("search_threads", {"query": "massage"})
        assert {t["id"] for t in own.result["threads"]} == {"th_s02_moved_last_month"}, own.result
        for tool, arguments in (("list_drafts", {}), ("create_draft", {"to": ["ana.morales@example.com"], "body": "x"}),
                                ("label_thread", {"threadId": "th_s02_moved_last_month", "labelIds": ["handled"]})):
            answer = world.call(tool, arguments)
            assert not answer.ok and "permission_denied" in str(answer.error), tool
        events = {e["id"]: e for e in world.call("list_events", {"calendarId": "CAL-ST02"}).result["events"]}
        assert events["EV-A001"]["summary"] == "Booked" and events["EV-A001"]["attendees"] == [], events["EV-A001"]
        assert world.call("list_events", {"calendarId": "CAL-ST02", "fullText": "Morales"}).result["events"] == []
        theirs = {e["id"]: e for e in world.call("list_events", {"calendarId": "CAL-ST01"}).result["events"]}
        assert theirs["EV-A002"]["summary"] == "Massage Therapy - Brian Cole"
        assert world.call("get_event", {"calendarId": "CAL-ST02", "eventId": "EV-A001"}).result["description"] is None
        _signed_in(world, None, "DS01")
        assert world.call("get_event", {"calendarId": "CAL-ST02", "eventId": "EV-A001"}).result["summary"] == "Initial Consultation - Ana Morales"
    finally:
        world.close()


def test_a_first_time_customer_is_signed_in_by_registering(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        made = world.call("create_customer", {"name": "Pat New", "email": "pat.new@example.com", "phone": "+1 212 555 0777"})
        assert made.ok
        assert world.state()["session_caller"][0]["customer_id"] == made.result["customer_id"]
        again = world.call("create_customer", {"name": "Someone Else", "email": "someone.else@example.com", "phone": "+1 212 555 0778"})
        assert not again.ok and "permission_denied" in str(again.error)
    finally:
        world.close()


# --- request keys, the competing booking, read-only calendars, confirmations --------------------


def test_a_request_key_replays_only_the_same_request(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _signed_in(world, "CU01")
        before = _dump(world)
        # Brian's key, with Ana's own booking: refused, and Brian's appointment is not shown.
        other = world.call("book_appointment", {"customer_id": "CU01", "service_id": "SV1", "staff_id": "ST02", "location_id": "L1",
                                                "start_time": "2026-10-29T14:00", "confirmed": True, "request_key": "SEED-A002"})
        assert not other.ok and "conflict" in str(other.error) and "CU02" not in str(other.error) and "20:00" not in str(other.error)
        moved = world.call("reschedule_appointment", {"appointment_id": "A001", "customer_id": "CU01", "new_start_time": "2026-10-28T10:00",
                                                      "confirmed": True, "request_key": "SEED-A016"})
        assert not moved.ok and "conflict" in str(moved.error)
        assert _dump(world) == before
        args = {"customer_id": "CU01", "service_id": "SV1", "staff_id": "ST02", "location_id": "L1", "start_time": "2026-10-29T14:00", "confirmed": True, "request_key": "R-1"}
        first = world.call("book_appointment", args)
        assert first.ok and not first.result["idempotent"]
        replay = world.call("book_appointment", args)
        assert replay.ok and replay.result["idempotent"] and replay.result["appointment"]["appointment_id"] == first.result["appointment"]["appointment_id"]
        changed = world.call("book_appointment", {**args, "start_time": "2026-10-29T15:00"})
        assert not changed.ok and "conflict" in str(changed.error)
    finally:
        world.close()


def test_the_competing_booking_races_only_for_a_bookable_unheld_slot(built: Path) -> None:
    scenario = _scenario("v12_s03_last_slot_taken_during_confirmation")
    world, _calls, _refused, _ = _run(scenario, built / WORLD, with_solution=False)
    try:
        slot = {"customer_id": "CU05", "staff_id": "ST03", "location_id": "L1", "start_time": "2026-10-30T10:00"}
        wrong = world.call("book_appointment", {**slot, "service_id": "SV1", "confirmed": True, "request_key": "C-1"})
        assert not wrong.ok and "does not offer" in str(wrong.error)
        assert not any(a["source"] == "competitor" for a in world.state()["appointments"])
        held = world.call("hold_slot", {**slot, "service_id": "SV3"})
        assert held.ok
        booked = world.call("book_appointment", {**slot, "service_id": "SV3", "confirmed": True, "request_key": "C-2", "hold_id": held.result["hold_id"]})
        assert booked.ok and booked.result["booked"], booked.result
        assert not any(a["source"] == "competitor" for a in world.state()["appointments"])
    finally:
        world.close()


def test_a_read_only_calendar_is_changed_only_through_the_front_desk(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _signed_in(world, "CU06")
        before = _dump(world)
        for tool, arguments in (
            ("cancel_appointment", {"appointment_id": "A014", "customer_id": "CU06", "confirmed": True}),
            ("reschedule_appointment", {"appointment_id": "A014", "customer_id": "CU06", "new_start_time": "2026-11-03T10:00", "new_staff_id": "ST04", "confirmed": True, "request_key": "RO-1"}),
            ("hold_slot", {"customer_id": "CU06", "service_id": "SV1", "staff_id": "ST06", "location_id": "L2", "start_time": "2026-11-03T10:00"}),
        ):
            answer = world.call(tool, arguments)
            assert not answer.ok and "policy_blocked" in str(answer.error), (tool, answer.result)
        assert _dump(world) == before
    finally:
        world.close()


def test_a_resend_queues_a_new_confirmation_and_a_repeat_does_not(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _signed_in(world, "CU01")
        args = {"appointment_id": "A001", "customer_id": "CU01", "kind": "booked", "channel": "sms"}
        first = world.call("queue_confirmation", args)
        assert first.ok and not first.result["idempotent"] and first.result["outbox_id"] != "O001"
        repeat = world.call("queue_confirmation", args)
        assert repeat.ok and repeat.result["idempotent"] and repeat.result["outbox_id"] == first.result["outbox_id"]
    finally:
        world.close()


def test_an_empty_day_says_why(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        for date, word in (("2026-11-30", "horizon"), ("2026-11-01", "closed on Sundays"), ("2026-11-11", "staff training")):
            answer = world.call("list_slots", {"service_id": "SV3", "location_id": "L1", "date": date})
            assert answer.ok and answer.result["slots"] == [] and word in answer.result["unavailable_reason"], (date, answer.result)
        busy = world.call("list_slots", {"service_id": "SV3", "location_id": "L1", "date": "2026-10-30", "staff_id": "ST02"})
        assert busy.result["slots"] == [] and "unavailable_reason" not in busy.result
    finally:
        world.close()


def test_a_language_preference_is_stored_as_its_code(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _signed_in(world, "CU07")
        said = world.call("record_customer_preference", {"customer_id": "CU07", "kind": "language", "value": "Hindi"})
        assert said.ok and said.result["value"] == "hi"
        assert not world.call("record_customer_preference", {"customer_id": "CU07", "kind": "language", "value": "Klingon"}).ok
    finally:
        world.close()
