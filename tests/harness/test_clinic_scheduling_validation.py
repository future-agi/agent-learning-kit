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
from fi.alk.harness.prove import _run
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
        slots = world.call("find_available_slots", {"provider_id": "D04", "date_from": "2026-10-27", "date_to": "2026-11-06", "patient_id": "P02"})
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
