"""A case keeps what its coverage promises and what the agent's rules allow: a fault level carries the fault, a level
the plan says changes the agent's job is checked, one record never carries a task, and every rule governs a case."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fi.alk.harness import scenario_tools as tools_module
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.grounding import policy_conflicts
from fi.alk.harness.scenario import Scenario, Step
from fi.alk.harness.scenario_tools import (
    _fault_level_unmet,
    _level_unchecked,
    _record_over_its_share,
    scenario_tools,
)

from test_writer_deals import _environment, _grid, _kept_as_given

GRID = {"task": ["issue_refund", "lookup_order", "refuse_refund", "start_return"]}


def _case(name: str, order: str, task: str = "issue_refund", **extra) -> Scenario:
    return Scenario(
        name=name,
        instruction="i",
        coverage={"task": task, **extra.pop("coverage", {})},
        solution=[Step(tool="issue_refund", arguments={"order_id": order, "amount": 89})],
        **extra,
    )


def test_a_task_crowded_onto_one_record_is_refused_with_the_other_records_named():
    kept = [_case(f"refund_{n}", "1043") for n in range(17)] + [_case("return_1", "1046", task="start_return")]

    said = _record_over_its_share(_case("next", "1043"), kept, 200, GRID, lambda: {"1043", "1044", "1046", "1048"})

    assert "17 of the 17 issue_refund cases" in said and "more than 17" in said
    assert "1044, 1046, 1048" in said
    assert _record_over_its_share(_case("next", "1046"), kept, 200, GRID) == ""
    # An agent serving one record has nothing to spread across, so nothing is refused.
    assert _record_over_its_share(_case("next", "1043"), kept[:17], 200, GRID, lambda: {"1043"}) == ""
    # Another task's cases are another share, and a case edited in place never counts against itself.
    assert _record_over_its_share(_case("next", "1043", task="start_return"), kept, 200, GRID) == ""
    assert _record_over_its_share(_case("refund_0", "1043"), kept[:17], 200, GRID) == ""


FAULTS = {"disposition": {"payment_timeout": ["1045"]}}


def test_a_case_on_a_fault_level_needs_the_fault_or_a_record_that_carries_it():
    plain = _case("plain", "1043", coverage={"disposition": "payment_timeout"})
    on_the_record = _case("timed_out", "1045", coverage={"disposition": "payment_timeout"})
    elsewhere = _case("hurried", "1043", coverage={"disposition": "hurried"})

    said = _fault_level_unmet(plain, FAULTS, faults_on=False)
    assert "declares a fault" in said and "(1045)" in said and "`faults`" not in said
    assert "`faults`" in _fault_level_unmet(plain, FAULTS, faults_on=True)
    assert _fault_level_unmet(on_the_record, FAULTS, faults_on=False) == ""
    assert _fault_level_unmet(elsewhere, FAULTS, faults_on=False) == ""
    injected = plain.model_copy(update={"faults": [{"action": "issue_refund", "mode": "unavailable"}]})
    assert _fault_level_unmet(injected, FAULTS, faults_on=True) == ""
    nothing = _fault_level_unmet(plain, {"disposition": {"payment_timeout": []}}, faults_on=False)
    assert "Nothing here can give the agent this failure" in nothing


def test_a_level_the_plan_says_must_be_checked_needs_a_sub_goal_claimed_for_it():
    checked = {"interaction": ["resumed_after_pause"]}
    goals = Catalogue(
        sub_goals=[
            SubGoal(name="issues_full_refund", what="x", judged="x"),
            SubGoal(name="keeps_context_on_return", what="x", judged="x", overlay="resumed_after_pause"),
        ]
    )
    narrative = _case(
        "paused", "1043", coverage={"interaction": "resumed_after_pause"}, sub_goals=["issues_full_refund"]
    )
    checked_case = narrative.model_copy(update={"sub_goals": ["issues_full_refund", "keeps_context_on_return"]})

    assert "resumed_after_pause" in _level_unchecked(narrative, checked, goals)
    assert _level_unchecked(checked_case, checked, goals) == ""
    assert _level_unchecked(narrative, {"interaction": ["topic_switch"]}, goals) == ""


def _server(tmp_path: Path, monkeypatch, grid):
    monkeypatch.delenv("ALK_HARNESS_JOB_ID", raising=False)
    monkeypatch.setattr(tools_module, "accept_scenario", _kept_as_given)
    server, kept = scenario_tools(_environment(tmp_path), tmp_path, tmp_path, wanted=4)
    handlers = {one.name: one.handler for one in server.tools}
    return handlers, kept


def test_aim_for_takes_fault_and_checked_levels_only_on_the_grid_and_submit_holds_them(tmp_path, monkeypatch):
    handlers, kept = _server(tmp_path, monkeypatch, None)
    grid = {**_grid(), "disposition": ["hurried", "payment_timeout"], "interaction": ["plain", "resumed"]}

    stray = asyncio.run(handlers["aim_for"]({"count": 4, "axes": grid, "fault_levels": {"disposition": {"declined": []}}}))
    assert stray.get("is_error") and "disposition declined" in stray["content"][0]["text"]
    said = asyncio.run(
        handlers["aim_for"](
            {"count": 4, "axes": grid, "fault_levels": FAULTS, "checked_levels": {"interaction": ["resumed"]}}
        )
    )
    assert not said.get("is_error"), said
    assert "Fault levels: disposition payment_timeout (1045)" in said["content"][0]["text"]
    assert "Nothing in the catalogue is claimed for resumed" in said["content"][0]["text"]

    coverage = {**{axis: levels[0] for axis, levels in grid.items()}, "task": "t1", "disposition": "payment_timeout"}
    refund = {"tool": "put", "arguments": {"order_id": "1043"}}
    refused = asyncio.run(
        handlers["submit_scenario"]({"name": "plain", "instruction": "i", "coverage": coverage, "solution": [refund]})
    )
    assert refused.get("is_error") and "declares a fault" in refused["content"][0]["text"]
    assert not kept
    on_record = {**refund, "arguments": {"order_id": "1045"}}
    accepted = asyncio.run(
        handlers["submit_scenario"]({"name": "late", "instruction": "i", "coverage": coverage, "solution": [on_record]})
    )
    assert not accepted.get("is_error"), accepted


RULES = ["Refund only when the customer asks.", "Refund each order at most once."]


def _advocate_case(**extra):
    values = dict(
        scenario_key="advocate",
        sub_goals=["issues_full_refund"],
        instruction="You are Teresa Howard, representing Ravi Patel, the customer on order 1043.",
        sources=[],
        solution=[Step(tool="issue_refund", arguments={"order_id": "1043"})],
    )
    values.update(extra)
    return SimpleNamespace(**values)


def test_every_rule_governs_a_case_that_calls_the_agent_s_tools_and_the_judge_sees_who_is_asking(tmp_path):
    goals = Catalogue(sub_goals=[SubGoal(name="issues_full_refund", what="The agent issues the full refund.", judged="x")])
    asked: list[tuple[str, str]] = []

    def judge(expected, rule):
        asked.append((expected, rule))
        return True, "a representative is not the customer"

    found = policy_conflicts(_advocate_case(), goals, RULES, tmp_path, judge=judge)

    assert [one["ref"] for one in found] == ["rules"] and found[0]["conflict"] is True
    expected, rule = asked[0]
    assert rule == "- Refund only when the customer asks.\n- Refund each order at most once."
    assert "representing Ravi Patel" in expected and "issues the full refund" in expected
    # A case that calls no tool is held only to the rules it cites.
    asked.clear()
    assert policy_conflicts(_advocate_case(solution=[]), goals, RULES, tmp_path / "none", judge=judge) == []
    assert asked == []


def test_a_kept_case_s_possible_rule_conflict_is_said_at_submit(tmp_path, monkeypatch):
    from fi.alk.harness.prove import Proof
    from fi.alk.harness.scenario_tools import accept_scenario

    conflict = {"rule": RULES[0], "ref": "rules", "conflict": True, "why": "a representative is not the customer"}
    monkeypatch.setattr(tools_module, "native_runtime", lambda _root: True)
    monkeypatch.setattr(tools_module, "validate_scenario", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(tools_module, "prove", lambda *_args, **_kwargs: Proof(deferred=True, policy_checks=[conflict]))

    said = accept_scenario(
        {"name": "advocate", "instruction": "You represent the customer."},
        world_root=tmp_path,
        catalogue=Catalogue(),
        kept=[],
        allow_empty_solution=True,
        persist=False,
    )

    assert not said.get("is_error"), said
    assert "POSSIBLE RULE CONFLICT" in said["content"][0]["text"]
    assert "a representative is not the customer" in said["content"][0]["text"]
