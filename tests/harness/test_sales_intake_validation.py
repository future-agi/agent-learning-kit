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
        world.call("update_contact_consent", {"contact_id": "C05", "status": "opted_out", "source": "chat", "confirmed": True})
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
