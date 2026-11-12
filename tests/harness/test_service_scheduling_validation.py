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
