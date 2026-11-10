"""Internal knowledge at 100 scenarios (Wave A increment 10, plan §3.1, §3.2 and §4.3): the world
checks hold, every scenario's reference solution passes its checks, and a deliberately wrong run of
each kind the plan's must-fail list names fails the check that should catch it: a figure from the
superseded version or the draft, a decision taken without reading the passage or Slack thread it
rests on, a request opened without the employee's consent, a reply in another thread, an issue
flagged on the wrong document, and an agent that does nothing. What the knowledge tools refuse
outright (a restricted passage for an employee outside its collection, a post without an approval)
is asserted directly.

`WRONG_RUNS` is also what `internal_knowledge/README.md`'s validation table is rendered from.
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

HERE = Path(environments.__file__).parent / "internal_knowledge"
SUITE = HERE / "suite"


@pytest.fixture(scope="module")
def built(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("internal_knowledge")
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
        assert len(state["drive_files"]) >= 40 and len(state["slack_messages"]) >= 30
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
        "policy_answers": 20,
        "versions": 13,
        "restricted": 12,
        "missing_evidence": 11,
        "conflicts": 12,
        "slack": 15,
        "issues_owners": 8,
        "untrusted": 9,
    }, counts
    assert sum(job["mcp"] for job in jobs.values()) >= 48
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
        "v11_s01_current_hotel_cap_with_citation",
        "answers from the superseded version's passage",
        lambda k: _swap(k, "fetch_passage", passage_id="P-TRVPOLICYV2-02"),
        "answer_cited_from_effective_version",
        None,
    ),
    (
        "hotel_cap_old_figure_in_superseded_doc",
        "reads the superseded version as the one in effect",
        lambda k: _swap(k, "read_file_content", fileId="trv_policy_v2"),
        "answer_cited_from_effective_version",
        None,
    ),
    (
        "leave_entitlement_2027_draft_vs_effective",
        "answers from the 2027 draft",
        lambda k: _swap(k, "read_file_content", fileId="leave_v3_draft"),
        "answer_cited_from_effective_version",
        None,
    ),
    (
        "mileage_slack_proposal_not_a_decision",
        "weighs the Slack proposal without reading its thread",
        lambda k: _without(k, "slack_read_thread"),
        "slack_evidence_weighed",
        None,
    ),
    (
        "v11_s03_relocation_allowance_japan_not_covered",
        "opens the knowledge request without the employee's consent",
        lambda k: _swap(k, "create_knowledge_request", confirmed=False),
        "knowledge_request_created",
        None,
    ),
    (
        "slack_reply_hotel_cap_grace",
        "posts the reply in another employee's thread",
        lambda k: _swap(k, "slack_send_message", thread_ts="1793696400.000003"),
        "slack_reply_in_thread",
        None,
    ),
    (
        "v11_s04_conflicting_client_dinner_caps",
        "picks one cap without reading the other source",
        lambda k: _drop(k, 2),
        "conflict_reported",
        None,
    ),
    (
        "update_open_sabbatical_request",
        "adds the note to another employee's request",
        lambda k: _swap(k, "update_knowledge_request", request_id="KR-102"),
        "knowledge_request_updated",
        None,
    ),
    (
        "flag_missing_section_relocation_japan",
        "does nothing at all",
        lambda k: [],
        "document_issue_flagged",
        "not done",
    ),
    (
        "v11_s01_current_hotel_cap_with_citation",
        "does nothing at all",
        lambda k: [],
        "answer_cited_from_effective_version",
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


def test_the_knowledge_tools_refuse_what_the_plan_says_must_never_happen(built: Path) -> None:
    world = restore(built / WORLD)
    try:
        for tool, arguments, what in (
            ("fetch_passage", {"employee_id": "E01", "passage_id": "P-SALARYBANDS2026-01"}, "a restricted passage"),
            ("read_file_content", {"fileId": "salary_bands_2026"}, "a restricted document through Drive"),
            ("slack_send_message", {"channel_id": "CH_HELPPOL", "message": "The cap is 150."}, "a post without an approval"),
        ):
            answer = world.call(tool, arguments)
            assert not answer.ok, (what, answer.result)
        found = world.call("search_documents", {"employee_id": "E01", "query": "salary bands"})
        assert "salary_bands_2026" not in json.dumps(found.result if found.ok else found.error)
    finally:
        world.close()


def test_an_agent_that_does_nothing_fails_every_scenario(built: Path) -> None:
    for path in sorted(p for p in (SUITE / "scenarios").iterdir() if p.is_dir()):
        said = _grade(built, path.name, [])
        if said:
            assert any(said.values()), path.name


def test_wrong_runs_cover_every_kind_the_plan_names() -> None:
    kinds = " ".join(wrong for _k, wrong, *_ in WRONG_RUNS)
    for kind in ("superseded", "draft", "Slack proposal", "consent", "another employee's thread", "does nothing"):
        assert kind in kinds, kind
