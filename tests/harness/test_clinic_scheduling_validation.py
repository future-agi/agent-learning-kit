"""Clinic scheduling at 100 scenarios (Wave A increment 7, plan §3.1, §3.2 and §4.2): the world
checks hold, every scenario's reference solution passes its checks, and a deliberately wrong run of
each kind the plan's must-fail list names fails the check that should catch it: the wrong slot or
provider, the email's first request instead of its correction, a decision taken without reading the
email it rests on, an urgent symptom routed to a routine team, and an agent
that does nothing. What the clinic tools refuse outright (a referral-only specialty without a referral,
an event written straight onto a provider's calendar, another patient's records) is asserted directly.

`WRONG_RUNS` is also what `clinic_scheduling/README.md`'s validation table is rendered from.
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

HERE = Path(environments.__file__).parent / "clinic_scheduling"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("clinic_scheduling")
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
        assert len(state["patients"]) >= 20 and len(state["providers"]) == 8
        events = {a["calendar_event_id"] for a in state["appointments"] if a.get("calendar_event_id")}
        assert events and events <= {e["event_id"] for e in state["calendar_events"]}
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
        "referral": 11,
        "email": 14,
        "availability": 10,
        "intake_insurance": 11,
        "urgent_privacy": 13,
        "waitlist_concurrency": 7,
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
        "email_tuesday_corrected_to_thursday_booking",
        "books the Tuesday first named in the email, not the corrected Thursday",
        lambda k: _swap(k, "book_appointment", slot_id="SL-D03-20261027-1300"),
        "appointment_booked_as_confirmed",
        None,
    ),
    (
        "email_tuesday_corrected_to_thursday_booking",
        "books without reading the patient's email",
        lambda k: _without(k, "get_thread"),
        "appointment_booked_as_confirmed",
        "done blind",
    ),
    (
        "book_primary_care_named_provider_west",
        "books another time from the one the patient confirmed",
        lambda k: _swap(k, "book_appointment", slot_id="SL-D02-20261029-1000"),
        "appointment_booked_as_confirmed",
        None,
    ),
    (
        "book_primary_care_named_provider_west",
        "books with another provider",
        lambda k: _swap(k, "book_appointment", slot_id="SL-D01-20261029-0900"),
        "appointment_booked_as_confirmed",
        None,
    ),
    (
        "v02_s01_reschedule_within_constraints",
        "moves the appointment to another slot than the one confirmed",
        lambda k: _swap(k, "reschedule_appointment", new_slot_id="SL-D02-20261103-1430"),
        "appointment_rescheduled_atomically",
        None,
    ),
    (
        "v02_s04_urgent_symptom_during_scheduling",
        "routes an urgent symptom to a routine team",
        lambda k: _swap(k, "escalate_to_staff", team="front_desk", urgency="routine"),
        "urgent_symptom_escalated",
        None,
    ),
    (
        "email_cancel_physiotherapy",
        "cancels without reading the patient's email",
        lambda k: _without(k, "get_thread"),
        "appointment_cancelled_as_requested",
        "done blind",
    ),
    (
        "v02_s02_referral_required",
        "does nothing at all",
        lambda k: [],
        "referral_request_created",
        "not done",
    ),
    (
        "email_insurance_typo_correction",
        "does nothing at all",
        lambda k: [],
        "insurance_updated",
        "not done",
    ),
    (
        "location_accessibility_riverbank_west",
        "reads another location's information than the one asked about",
        lambda k: _swap(k, "get_location_info", location_id="LOC-CEN"),
        "clinic_information_reported",
        "not done",
    ),
    (
        "calendar_earliest_primary_care_this_week",
        "reports availability from another week than the one asked about",
        lambda k: _swap(k, "find_available_slots", date_from="2026-11-02", date_to="2026-11-06"),
        "availability_reported_from_calendar",
        "done blind",
    ),
    (
        "email_preparation_human_draft_preserved",
        "answers without reading the drafts, so the colleague's draft is never found",
        lambda k: _without(k, "list_drafts"),
        "human_draft_preserved",
        "done blind",
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


def test_the_clinic_tools_refuse_what_the_plan_says_must_never_happen(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        world.change("session_caller", "default", {"caller_id": "P02"}, by="id")
        assert world.call("verify_patient", {"full_name": "Priya Raman", "dob": "1990-11-03"}).ok
        slots = world.call("find_available_slots", {"provider_id": "D04", "date_from": "2026-10-27", "date_to": "2026-11-06", "patient_id": "P02"})
        assert slots.ok and slots.result["slots"], slots.result
        free = [s["slot_id"] for s in (slots.result or {}).get("slots", [])] if slots.ok else []
        if free:
            booked = world.call("book_appointment", {"patient_id": "P02", "slot_id": free[0], "reason": "Chest", "confirmed": True, "request_key": "T1"})
            assert not booked.ok, ("a referral-only specialty booked without a referral", booked.result)
        other = world.call("list_appointments", {"patient_id": "P04"})
        assert not other.ok, ("another patient's appointments", other.result)
        direct = world.call("create_event", {"calendarId": "CAL-D02", "summary": "Patient visit", "startTime": "2026-11-03T10:00:00Z", "endTime": "2026-11-03T10:30:00Z"})
        assert not direct.ok, ("an event written straight onto a provider's calendar", direct.result)
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("first named", "another provider", "another time", "urgent symptom", "without reading", "does nothing"):
        assert kind in kinds, kind


def _rows(world, tables):
    """The rows of `tables`, in no particular order: a restored row may come back last."""
    state = world.state()
    return {t: sorted(json.dumps(row, sort_keys=True, default=str) for row in state.get(t) or []) for t in tables}


# (scenario, the paired write that fails, the statement that fails partway through it)
PARTIAL_FAILURES = [
    ("book_cardiology_active_referral_follow_up", "book_appointment", "INSERT INTO calendar_events"),
    ("email_search_sender_reschedule", "reschedule_appointment", "UPDATE calendar_events"),
    ("cancel_appointment_confirmed", "cancel_appointment", "UPDATE calendar_events"),
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
        before = _rows(world, ("appointments", "calendar_events", "slot_holds"))
        real = world.store.execute

        def broken(sql, params=()):
            if sql.lstrip().upper().startswith(failing.upper()):
                raise RuntimeError(f"injected failure: {failing}")
            return real(sql, params)

        world.store.execute = broken
        failed = play_reference_step(world, steps[at])
        world.store.execute = real
        assert not failed.ok, failed.result
        assert _rows(world, ("appointments", "calendar_events", "slot_holds")) == before
        retried = play_reference_step(world, steps[at])
        assert retried.ok and not (retried.result or {}).get("idempotent"), retried.result
        checks = json.loads((HERE / "environment.json").read_text())["world_checks"]
        broken_checks = {n: o.said for n, s in checks.items() if not (o := run_world_check(s, world, name=n)).held}
        assert not broken_checks, broken_checks
    finally:
        world.close()


# --- who is talking (the session's caller), and what failed calls leave -------------------------


def _everything(world):
    """Every table's rows, in no particular order."""
    return {t: sorted(json.dumps(row, sort_keys=True, default=str) for row in rows) for t, rows in world.state().items()}


# Reference steps that are refused on purpose: the refusal is what the scenario is about.
EXPECTED_REFUSALS = {
    ("failed_verification_withholds_records", "verify_patient"): "the caller gives a date of birth that matches no patient",
    ("intake_forms_fallback_to_sms", "send_intake_forms"): "no email is on file, so the first (email) send is refused before SMS",
    ("reschedule_slot_conflict_recovery", "reschedule_appointment"): "the first slot is taken by another caller before commit",
    ("v02_s03_slot_taken_during_confirmation", "book_appointment"): "the first slot is taken by another caller before commit",
    ("waitlist_duplicate_entry_reported", "add_to_waitlist"): "the patient is already on that waitlist; the refusal names the entry",
}


@pytest.mark.parametrize(
    "key", sorted(path.name for path in (SUITE / "scenarios").iterdir() if path.is_dir())
)
def test_every_reference_step_succeeds(built: Path, key: str) -> None:
    """Every step of a reference solution is a call a correct agent can make: it succeeds, unless
    it is the deliberate refused attempt the scenario is about (EXPECTED_REFUSALS), which must then
    really be refused."""
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True)
    world.close()
    refused = {(key, c.name) for c in calls if not c.ok}
    assert refused <= set(EXPECTED_REFUSALS), [(c.name, c.arguments, c.error) for c in calls if not c.ok]
    assert {k for k in EXPECTED_REFUSALS if k[0] == key} <= refused, key


# (tool, arguments) acting for a patient the signed-in caller is not, nor recorded as able to act
# for: P02 is signed in and verified, and the target patient is even marked verified, so only the
# caller check can refuse it.
WRONG_CALLER = [
    ("verify_patient", {"full_name": "Marcus Vance", "dob": "1982-04-12"}),
    ("list_appointments", {"patient_id": "P01"}),
    ("get_appointment", {"appointment_id": "A01"}),
    ("reschedule_appointment", {"appointment_id": "A01", "new_slot_id": "SL-D02-20261103-1400", "confirmed": True, "request_key": "W1"}),
    ("cancel_appointment", {"appointment_id": "A01", "confirmed": True}),
    ("book_appointment", {"patient_id": "P01", "slot_id": "SL-D02-20261103-1400", "reason": "Check", "confirmed": True, "request_key": "W2"}),
    ("update_insurance", {"patient_id": "P01", "payer": "Aviva", "member_id": "AV-1", "confirmed": True}),
    ("send_intake_forms", {"patient_id": "P01", "channel": "email"}),
    ("get_referral_status", {"patient_id": "P11"}),
    ("create_referral_request", {"patient_id": "P01", "specialty": "cardiology", "request_key": "W3"}),
    ("hold_slot", {"patient_id": "P01", "slot_id": "SL-D02-20261103-1400", "request_key": "W4"}),
    ("release_hold", {"hold_id": "H01"}),
    ("add_to_waitlist", {"patient_id": "P01", "specialty": "primary_care", "earliest_date": "2026-10-27", "latest_date": "2026-11-06", "request_key": "W5"}),
    ("find_available_slots", {"provider_id": "D02", "date_from": "2026-11-03", "date_to": "2026-11-03", "patient_id": "P17"}),
    ("escalate_to_staff", {"team": "front_desk", "reason": "x", "patient_id": "P01"}),
    ("get_thread", {"threadId": "th_c04_reschedule_p14"}),
    ("get_message", {"messageId": "cmsg_005"}),
    ("create_draft", {"to": ["james.walsh@example.com"], "body": "x", "replyToMessageId": "cmsg_005"}),
]


@pytest.mark.parametrize(("tool", "arguments"), WRONG_CALLER, ids=[t for t, _a in WRONG_CALLER])
def test_a_tool_acts_only_for_the_caller(built: Path, tool: str, arguments: dict) -> None:
    world = restore(built / WORLD)
    try:
        world.change("session_caller", "default", {"caller_id": "P02"}, by="id")
        assert world.call("verify_patient", {"full_name": "Priya Raman", "dob": "1990-11-03"}).ok
        for pid in ("P01", "P11", "P14", "P17"):
            world.put("verified_patients", {"patient_id": pid, "verified_at": "2026-10-26T09:00:00Z"})
        before = _everything(world)
        call = world.call(tool, arguments)
        assert not call.ok, (tool, call.result)
        assert call.error.startswith("permission_denied"), (tool, call.error)
        assert _everything(world) == before, tool
    finally:
        world.close()


def test_listings_hide_mail_about_patients_the_caller_may_not_see(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        world.change("session_caller", "default", {"caller_id": "P02"}, by="id")
        found = world.call("search_threads", {"query": "from:james.walsh@example.com"})
        assert found.ok and found.result["threads"] == [], found.result
        drafts = world.call("list_drafts", {})
        assert drafts.ok and drafts.result["drafts"] == [], drafts.result
        world.change("session_caller", "default", {"caller_id": "P14"}, by="id")
        assert world.call("verify_patient", {"full_name": "James Walsh", "dob": "1966-04-30"}).ok
        found = world.call("search_threads", {"query": "from:james.walsh@example.com"})
        assert [t["id"] for t in found.result["threads"]] == ["th_c04_reschedule_p14"]
        assert world.call("get_thread", {"threadId": "th_c04_reschedule_p14"}).ok
    finally:
        world.close()


def test_recorded_authority_lets_a_guardian_act_and_nobody_else(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        # Sarah Bennett (C01) is recorded as Noah's guardian: she may verify him and see his visits.
        world.change("session_caller", "default", {"caller_id": "C01"}, by="id")
        assert world.call("verify_patient", {"full_name": "Noah Bennett", "dob": "2018-05-04"}).ok
        assert world.call("list_appointments", {"patient_id": "P06"}).ok
        # A partner who knows Daniel Kim's date of birth is still not Daniel Kim.
        world.put("callers", {"caller_id": "C900", "full_name": "Jessica Taylor", "organisation": None})
        world.change("session_caller", "default", {"caller_id": "C900"}, by="id")
        refused = world.call("verify_patient", {"full_name": "Daniel Kim", "dob": "2001-02-14"})
        assert not refused.ok and "No patient this caller may act for" in refused.error
        unknown = world.call("verify_patient", {"full_name": "Nobody Here", "dob": "2001-02-14"})
        assert unknown.error == refused.error  # no way to test whether someone is a patient
    finally:
        world.close()


def _as(world, caller: str, name: str, dob: str) -> None:
    world.change("session_caller", "default", {"caller_id": caller}, by="id")
    assert world.call("verify_patient", {"full_name": name, "dob": dob}).ok


def test_a_reused_request_key_for_another_request_is_a_conflict(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _as(world, "P01", "Marcus Vance", "1982-04-12")
        before = _everything(world)
        for tool, arguments in [
            ("hold_slot", {"patient_id": "P01", "slot_id": "SL-D02-20261103-1500", "request_key": "seed-H01"}),
            ("add_to_waitlist", {"patient_id": "P01", "specialty": "dermatology", "earliest_date": "2026-10-27", "latest_date": "2026-11-20", "request_key": "seed-W01"}),
            ("book_appointment", {"patient_id": "P01", "slot_id": "SL-D02-20261103-1400", "reason": "x", "confirmed": True, "request_key": "seed-A01"}),
            ("reschedule_appointment", {"appointment_id": "A01", "new_slot_id": "SL-D02-20261103-1400", "confirmed": True, "request_key": "seed-A01"}),
            ("reschedule_appointment", {"appointment_id": "A01", "new_slot_id": "SL-D02-20261103-1400", "confirmed": True, "request_key": "seed-A02"}),
        ]:
            call = world.call(tool, arguments)
            assert not call.ok and "conflict" in call.error, (tool, call.result, call.error)
        assert _everything(world) == before
        # The same request replayed is still the same request.
        booked = world.call("book_appointment", {"patient_id": "P01", "slot_id": "SL-D02-20261103-1400", "reason": "x", "confirmed": True, "request_key": "K1"})
        again = world.call("book_appointment", {"patient_id": "P01", "slot_id": "SL-D02-20261103-1400", "reason": "x", "confirmed": True, "request_key": "K1"})
        assert booked.ok and again.ok and again.result["idempotent"] and again.result["appointment_id"] == booked.result["appointment_id"]
        other = world.call("book_appointment", {"patient_id": "P01", "slot_id": "SL-D02-20261103-1430", "reason": "x", "confirmed": True, "request_key": "K1"})
        assert not other.ok and "conflict" in other.error
    finally:
        world.close()


def test_the_waitlist_answers_a_different_request_with_the_entry_already_there(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _as(world, "P02", "Priya Raman", "1990-11-03")
        other = world.call("add_to_waitlist", {"patient_id": "P02", "specialty": "primary_care", "earliest_date": "2026-10-27", "latest_date": "2026-11-20", "request_key": "N1"})
        assert not other.ok and "W02" in other.error, other.error
        same = world.call("add_to_waitlist", {"patient_id": "P02", "specialty": "primary_care", "provider_id": "D02", "earliest_date": "2026-10-26", "latest_date": "2026-11-06", "request_key": "N2"})
        assert same.ok and same.result["waitlist_id"] == "W02" and same.result["idempotent"]
        wrong = world.call("add_to_waitlist", {"patient_id": "P02", "specialty": "dermatology", "provider_id": "D02", "earliest_date": "2026-10-27", "latest_date": "2026-11-20", "request_key": "N3"})
        assert not wrong.ok and "not dermatology" in wrong.error
    finally:
        world.close()


def test_intake_forms_for_another_appointment_are_another_send(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _as(world, "P01", "Marcus Vance", "1982-04-12")
        first = world.call("send_intake_forms", {"patient_id": "P01", "channel": "email", "appointment_id": "A01"})
        replay = world.call("send_intake_forms", {"patient_id": "P01", "channel": "email", "appointment_id": "A01"})
        other = world.call("send_intake_forms", {"patient_id": "P01", "channel": "email"})
        assert first.ok and replay.ok and other.ok
        assert replay.result["idempotent"] and replay.result["send_id"] == first.result["send_id"]
        assert not other.result["idempotent"] and other.result["send_id"] != first.result["send_id"]
    finally:
        world.close()


def test_a_slot_lost_during_a_reschedule_is_really_booked_by_the_rival(built: Path) -> None:
    key = "reschedule_slot_conflict_recovery"
    world, calls, _refused, _ = _run(_scenario(key), built / WORLD, with_solution=True, steps=_scenario(key).solution[:2])
    try:
        assert not calls[-1].ok and "another booking took it" in calls[-1].error
        slots = world.call("find_available_slots", {"provider_id": "D01", "date_from": "2026-11-04", "date_to": "2026-11-04"})
        assert "SL-D01-20261104-1000" not in [s["slot_id"] for s in slots.result["slots"]]
        rival = [a for a in world.state()["appointments"] if a["start_time"] == "2026-11-04T10:00:00Z" and a["provider_id"] == "D01"]
        assert [a["patient_id"] for a in rival] == ["P18"]
        assert world.state()["appointments"] and next(a for a in world.state()["appointments"] if a["appointment_id"] == "A415")["start_time"] == "2026-10-29T16:00:00Z"
    finally:
        world.close()


@pytest.mark.parametrize("fault", ["timeout", "permission_denied", "timeout_after_commit"])
def test_a_calendar_fault_keeps_only_what_its_kind_commits(built: Path, fault: str) -> None:
    """A timeout or a refusal before the commit changes nothing but uses the fault up, in
    create_event and update_event alike; only a timeout after the commit lands the change."""
    world = restore(built / WORLD)
    try:
        world.put("calendar_approvals", {"approval_id": "AP1", "actor_id": "u_clinic_desk", "operation": "any", "target_id": None, "payload_hash": "x",
                                         "status": "approved", "created_at": "2026-10-26T08:00:00Z", "expires_at": "2026-10-27T08:00:00Z"})
        made = world.call("create_event", {"calendarId": "CAL-DESK", "summary": "Desk task", "startTime": "2026-10-27T09:00:00Z", "endTime": "2026-10-27T09:30:00Z", "approval_id": "AP1"})
        assert made.ok, made.error
        for operation, arguments in (
            ("update_event", {"calendarId": "CAL-DESK", "eventId": made.result["id"], "summary": "Moved", "approval_id": "AP1"}),
            ("create_event", {"calendarId": "CAL-DESK", "summary": "Second", "startTime": "2026-10-27T11:00:00Z", "endTime": "2026-10-27T11:30:00Z", "approval_id": "AP1"}),
        ):
            world.put("calendar_fault_injections", {"fault_id": f"F-{operation}", "operation": operation, "target_id": None, "fault_type": fault, "trigger_once": True, "is_active": True})
            events = _rows(world, ("calendar_events",))
            call = world.call(operation, arguments)
            assert not call.ok, (operation, call.result)
            assert not next(f for f in world.state()["calendar_fault_injections"] if f["fault_id"] == f"F-{operation}")["is_active"]
            assert (_rows(world, ("calendar_events",)) != events) == (fault == "timeout_after_commit"), (operation, fault)
    finally:
        world.close()


def test_the_world_check_catches_a_double_booked_slot(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        world.put("appointments", {"appointment_id": "A900", "patient_id": "P02", "provider_id": "D01", "location_id": "LOC-CEN", "specialty": "primary_care",
                                   "start_time": "2026-11-02T13:30:00Z", "end_time": "2026-11-02T14:00:00Z", "reason": "x", "status": "scheduled", "calendar_event_id": "EVT-A900",
                                   "self_pay": 0, "request_key": None, "reschedule_count": 0, "cancel_reason": None, "cancelled_at": None,
                                   "created_at": "2026-10-12T10:00:00Z", "updated_at": "2026-10-12T10:00:00Z"})
        world.put("calendar_events", {"event_id": "EVT-A900", "calendar_id": "CAL-D01", "summary": "Clinic appointment A900", "description": "appointment_id: A900",
                                      "location": "LOC-CEN", "start_time": "2026-11-02T13:30:00Z", "end_time": "2026-11-02T14:00:00Z", "time_zone": "Europe/London",
                                      "status": "confirmed", "recurring_event_id": None, "recurrence_rule": None, "original_start_time": None, "version": 1,
                                      "created_at": "2026-10-12T10:00:00Z", "updated_at": "2026-10-12T10:00:00Z"})
        source = json.loads((HERE / "environment.json").read_text())["world_checks"]["check_no_double_bookings"]
        outcome = run_world_check(source, world, name="check_no_double_bookings")
        assert not outcome.held and "double-books" in outcome.said
    finally:
        world.close()


def test_the_appointment_shown_by_a_listing_counts_as_looked_up(built: Path) -> None:
    """The details the answer rests on may come from the patient's list as well as from the single
    appointment: what was returned counts, not which lookup returned it."""
    key = "cancel_completed_appointment_declined"
    steps = [copy.deepcopy(step) for step in _scenario(key).solution if step.tool != "get_appointment"]
    steps.append(Step(tool="list_appointments", arguments={"patient_id": "P419"}, environment_arguments={}, fixed={}))
    assert _grade(built, key, steps) == {"appointment_details_reported": None}


# One private record per route: (owner, name, dob, the business call, the attached-capability calls).
# A caller who may not act for the owner is refused on every route; the owner, verified, is served
# on every route. Search and listing routes must not show the record either.
CROSS_ROUTES = [
    ("P14", "James Walsh", "1966-04-30", ("get_appointment", {"appointment_id": "A07"}), [
        ("get_thread", {"threadId": "th_c04_reschedule_p14"}),
        ("get_message", {"messageId": "cmsg_005"}),
        ("label_thread", {"threadId": "th_c04_reschedule_p14", "labelIds": ["handled"]}),
        ("create_draft", {"to": ["james.walsh@example.com"], "body": "About A07", "replyToMessageId": "cmsg_005"}),
        ("create_draft", {"to": ["james.walsh@example.com"], "subject": "Your visit", "body": "About A07"}),
        ("get_event", {"calendarId": "CAL-D02", "eventId": "EVT-A07"}),
    ]),
    ("P12", "Mei Tanaka", "1971-02-27", ("list_appointments", {"patient_id": "P12"}), [
        ("get_thread", {"threadId": "th_c11_prep_p12"}),
        ("get_event", {"calendarId": "CAL-D05", "eventId": "EVT-A05"}),
    ]),
]


def _seen(world, needle: str) -> bool:
    """Whether any listing or search route shows `needle` (an id, an address, a body fragment)."""
    shown = [
        world.call("search_threads", {"query": "in:inbox"}),
        world.call("list_drafts", {}),
        world.call("list_events", {"calendarId": "CAL-D02", "startTime": "2026-10-26T00:00:00Z", "endTime": "2026-11-26T00:00:00Z"}),
        world.call("list_events", {"calendarId": "CAL-D05", "startTime": "2026-10-26T00:00:00Z", "endTime": "2026-11-26T00:00:00Z"}),
        world.call("list_events", {"calendarId": "CAL-D02", "fullText": needle}),
    ]
    return any(needle in json.dumps(call.result) for call in shown if call.ok)


@pytest.mark.parametrize(("owner", "name", "dob", "business", "routes"), CROSS_ROUTES, ids=[r[0] for r in CROSS_ROUTES])
def test_every_route_to_a_private_record_follows_the_caller(built: Path, owner, name, dob, business, routes) -> None:
    appointment = {"P14": "A07", "P12": "A05"}[owner]
    # Someone else signed in and verified as themself: every route is refused or shows nothing.
    world = restore(built / WORLD)
    try:
        _as(world, "P02", "Priya Raman", "1990-11-03")
        before = _everything(world)
        for tool, arguments in [business, *routes]:
            call = world.call(tool, arguments)
            assert not call.ok and call.error.startswith("permission_denied"), (tool, call.result)
        assert _everything(world) == before
        for needle in (appointment, f"EVT-{appointment}", next(p["email"] for p in world.state()["patients"] if p["patient_id"] == owner)):
            assert not _seen(world, needle), needle
    finally:
        world.close()
    # The owner, verified: every route is served.
    world = restore(built / WORLD)
    try:
        _as(world, owner, name, dob)
        for tool, arguments in [business, *routes]:
            call = world.call(tool, arguments)
            assert call.ok, (tool, call.error)
        assert _seen(world, f"EVT-{appointment}")
    finally:
        world.close()


def test_a_listed_booking_of_someone_else_is_only_busy_time(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        _as(world, "P02", "Priya Raman", "1990-11-03")
        events = world.call("list_events", {"calendarId": "CAL-D02", "startTime": "2026-10-29T00:00:00Z", "endTime": "2026-10-30T00:00:00Z"}).result["events"]
        booked = [e for e in events if e["start"]["dateTime"] == "2026-10-29T10:00:00Z"]
        assert booked and booked[0]["summary"] == "Busy (booked)" and booked[0]["id"] == "busy" and not booked[0]["description"]
        # Her own booking she sees in full.
        own = world.call("list_events", {"calendarId": "CAL-D01", "startTime": "2026-10-28T00:00:00Z", "endTime": "2026-10-29T00:00:00Z"}).result["events"]
        assert any(e["id"] == "EVT-A08" and e["description"] == "appointment_id: A08" for e in own)
    finally:
        world.close()


@pytest.mark.parametrize(
    "to",
    [
        ["James Walsh <james.walsh@example.com>"],
        ["front.desk@example.com, james.walsh@example.com"],
        ["JAMES.WALSH@EXAMPLE.COM"],
    ],
)
def test_a_patient_s_address_is_recognised_however_it_is_written(built: Path, to: list[str]) -> None:
    """A draft to another patient is refused whatever form the address takes: behind a display
    name or inside a comma-separated list (refused as not a plain address), or in capitals."""
    world = restore(built / WORLD)
    try:
        _as(world, "P02", "Priya Raman", "1990-11-03")
        before = len(world.state()["gmail_drafts"])
        drafted = world.call("create_draft", {"to": to, "subject": "Your visit", "body": "About A07"})
        assert not drafted.ok, drafted.result
        assert len(world.state()["gmail_drafts"]) == before
    finally:
        world.close()
