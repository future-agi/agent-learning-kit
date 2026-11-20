"""A case keeps what its coverage promises and what the agent's rules allow: a fault level carries the fault, a level
the plan says changes the agent's job is checked, one record never carries a task, and the rules a case does not cite
are read against it as advice. Each gate is off until its switch is 1."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from fi.alk.harness import scenario_tools as tools_module
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.grounding import ALL_RULES_SWITCH, policy_conflicts
from fi.alk.harness.prove import Proof
from fi.alk.harness.proofs import proof_entry
from fi.alk.harness.scenario import Scenario, Step
from fi.alk.harness.scenario_tools import (
    CHECKED_LEVEL_GATE,
    FAULT_LEVEL_GATE,
    _fault_level_unmet,
    _level_unchecked,
    _record_over_its_share,
    readable_records,
    scenario_tools,
)

from test_writer_deals import _environment, _grid, _kept_as_given

GRID = {"task": ["issue_refund", "lookup_order", "refuse_refund", "start_return"]}
ORDERS = {"1043", "1044", "1046", "1048"}


def _case(name: str, order: str, task: str = "issue_refund", store: str = "S1", **extra) -> Scenario:
    refund = Step(
        tool="issue_refund",
        arguments={"order_id": order, "store_id": store, "amount": 89},
        fixed={"order_id": "the caller"},
    )
    return Scenario(
        name=name, instruction="i", coverage={"task": task, **extra.pop("coverage", {})}, solution=[refund], **extra
    )


def _orders(argument: str, value: str) -> set[str]:
    return set(ORDERS) if argument == "order_id" else {"S1", "S2"}


def test_a_task_crowded_onto_the_record_it_acts_on_is_refused_with_records_of_the_same_kind_named():
    kept = [_case(f"refund_{n}", "1043") for n in range(17)] + [_case("return_1", "1046", task="start_return")]

    said = _record_over_its_share(_case("next", "1043"), kept, 200, GRID, _orders)

    assert "17 of the 17 issue_refund cases" in said and "more than 17" in said
    assert "1044, 1046, 1048" in said
    # The store every case passes is not fixed by the caller, so it is not the record the case acts on.
    assert _record_over_its_share(_case("next", "1046"), kept, 200, GRID, _orders) == ""
    # Another task's cases are another share, and a case edited in place never counts against itself.
    assert _record_over_its_share(_case("next", "1043", task="start_return"), kept, 200, GRID, _orders) == ""
    assert _record_over_its_share(_case("refund_0", "1043"), kept[:17], 200, GRID, _orders) == ""
    # A record with no other of its kind to move to is never refused.
    assert _record_over_its_share(_case("next", "1043"), kept, 200, GRID, lambda *_: {"1043"}) == ""


def test_the_records_offered_come_from_the_table_and_column_that_hold_the_crowded_one(tmp_path, monkeypatch):
    world = {
        "orders": [{"order_id": "1043", "total": 89}, {"order_id": "1044", "total": 64}],
        "returns": [{"return_id": "R1", "order_id": "1043"}, {"return_id": "R2", "order_id": "1047"}],
        "stores": [{"store_id": "S1"}, {"store_id": "S2"}],
    }
    monkeypatch.setattr(tools_module, "native_runtime", lambda _root: False)
    monkeypatch.setattr(tools_module, "restore", lambda _root: SimpleNamespace(state=lambda: world))

    assert readable_records(tmp_path, "order_id", "1043") == {"1043", "1044", "1047"}
    assert readable_records(tmp_path, "order_id", "9999") == set()


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


def test_a_checked_level_needs_a_sub_goal_whose_overlay_is_that_level_not_one_named_like_it():
    checked = {"interaction": ["resumed_after_pause"]}
    goals = Catalogue(
        sub_goals=[
            SubGoal(name="resumed_after_pause_refund", what="x", judged="x"),
            SubGoal(name="keeps_context_on_return", what="x", judged="x", overlay="resumed_after_pause"),
        ]
    )
    narrative = _case(
        "paused", "1043", coverage={"interaction": "resumed_after_pause"}, sub_goals=["resumed_after_pause_refund"]
    )
    checked_case = narrative.model_copy(update={"sub_goals": ["keeps_context_on_return"]})

    assert "resumed_after_pause" in _level_unchecked(narrative, checked, goals)
    assert _level_unchecked(checked_case, checked, goals) == ""
    assert _level_unchecked(narrative, {"interaction": ["topic_switch"]}, goals) == ""


def _server(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ALK_HARNESS_JOB_ID", raising=False)
    monkeypatch.setattr(tools_module, "accept_scenario", _kept_as_given)
    server, kept = scenario_tools(_environment(tmp_path), tmp_path, tmp_path, wanted=4)
    return {one.name: one.handler for one in server.tools}, kept


def _submit(handlers, name: str, coverage: dict[str, str], order: str):
    refund = {"tool": "put", "arguments": {"order_id": order}}
    reply = asyncio.run(
        handlers["submit_scenario"]({"name": name, "instruction": "i", "coverage": coverage, "solution": [refund]})
    )
    return bool(reply.get("is_error")), reply["content"][0]["text"]


def test_the_fault_gate_is_off_by_default_and_never_refuses_an_edit_that_keeps_the_cell(tmp_path, monkeypatch):
    monkeypatch.delenv(FAULT_LEVEL_GATE, raising=False)
    monkeypatch.delenv(CHECKED_LEVEL_GATE, raising=False)
    handlers, kept = _server(tmp_path, monkeypatch)
    grid = {**_grid(), "disposition": ["hurried", "payment_timeout"], "interaction": ["plain", "resumed"]}

    stray_args = {"count": 4, "axes": grid, "fault_levels": {"disposition": {"declined": []}}}
    ignored = asyncio.run(handlers["aim_for"](stray_args))
    assert not ignored.get("is_error") and "so left out: disposition declined" in ignored["content"][0]["text"]
    monkeypatch.setenv(FAULT_LEVEL_GATE, "1")
    stray = asyncio.run(handlers["aim_for"](stray_args))
    assert stray.get("is_error") and "disposition declined" in stray["content"][0]["text"]
    monkeypatch.delenv(FAULT_LEVEL_GATE)
    unchecked = asyncio.run(handlers["aim_for"]({"count": 4, "axes": grid, "checked_levels": {"interaction": ["x"]}}))
    assert not unchecked.get("is_error") and "interaction x" in unchecked["content"][0]["text"]
    monkeypatch.setenv(CHECKED_LEVEL_GATE, "1")
    assert asyncio.run(handlers["aim_for"]({"count": 4, "axes": grid, "checked_levels": {"interaction": ["x"]}}))[
        "is_error"
    ]
    monkeypatch.delenv(CHECKED_LEVEL_GATE)
    said = asyncio.run(
        handlers["aim_for"](
            {"count": 4, "axes": grid, "fault_levels": FAULTS, "checked_levels": {"interaction": ["resumed"]}}
        )
    )
    assert not said.get("is_error"), said
    assert "Fault levels: disposition payment_timeout (1045)" in said["content"][0]["text"]
    assert "Nothing in the catalogue is claimed for resumed" in said["content"][0]["text"]

    plain = {**{axis: levels[0] for axis, levels in grid.items()}, "task": "t1"}
    faulted = {**plain, "disposition": "payment_timeout"}
    assert _submit(handlers, "written_before", faulted, "1043")[0] is False

    monkeypatch.setenv(FAULT_LEVEL_GATE, "1")
    refused, why = _submit(handlers, "plain", faulted, "1043")
    assert refused and "declares a fault" in why
    assert _submit(handlers, "late", faulted, "1045")[0] is False
    # An edit that leaves the case on its cell is kept; one that moves a case onto the fault level is held to it.
    assert _submit(handlers, "written_before", faulted, "1043")[0] is False
    assert _submit(handlers, "hurried", plain, "1043")[0] is False
    assert _submit(handlers, "hurried", faulted, "1043")[0] is True
    assert {one.name for one in kept} == {"written_before", "late", "hurried"}


RULES = ["Refund only when the customer asks.", "Refund each order at most once."]
GOALS = Catalogue(sub_goals=[SubGoal(name="issues_full_refund", what="The agent issues the full refund.", judged="x")])


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


def test_the_uncited_rules_are_read_with_the_situation_as_advice_in_bundles_only_when_switched_on(
    tmp_path, monkeypatch
):
    from fi.alk.harness.scenario import ScenarioSource

    asked: list[tuple[str, str]] = []

    def judge(expected, rule):
        asked.append((expected, rule))
        return True, "a representative is not the customer"

    monkeypatch.delenv(ALL_RULES_SWITCH, raising=False)
    assert policy_conflicts(_advocate_case(), GOALS, RULES, tmp_path / "off", judge=judge) == []

    monkeypatch.setenv(ALL_RULES_SWITCH, "1")
    many = [f"Rule {n}." for n in range(25)]
    cites = [ScenarioSource(kind="rule", ref="rule:0", quote="Rule 0.")]
    found = policy_conflicts(_advocate_case(sources=cites), GOALS, many, tmp_path / "on", judge=judge)

    cited, *bundles = found
    # The cited rule is judged as before: on the expected outcome alone, so its cache key is unchanged.
    assert cited["ref"] == "rule:0" and not cited.get("advisory") and asked[0][0] == "- The agent issues the full refund."
    assert [one["ref"] for one in bundles] == ["rules", "rules"] and all(one["advisory"] for one in bundles)
    assert [len(one["rule"].splitlines()) for one in bundles] == [20, 4]
    assert "representing Ravi Patel" in asked[1][0]
    assert policy_conflicts(_advocate_case(solution=[]), GOALS, RULES, tmp_path / "none", judge=judge) == []


def test_an_advisory_conflict_never_unproves_a_case_and_is_said_at_submit_and_in_its_reason(tmp_path, monkeypatch):
    from fi.alk.harness.scenario_tools import accept_scenario

    advice = {"rule": RULES[0], "ref": "rules", "conflict": True, "why": "not the customer", "advisory": True}
    proved = Proof(ready=True, solvable=True, vacuous=False, policy_checks=[advice])
    assert proved.conflicts == [] and proved.advisories == [advice]
    assert "not the customer" in proof_entry(proved, 1)["why"]

    monkeypatch.setattr(tools_module, "native_runtime", lambda _root: True)
    monkeypatch.setattr(tools_module, "validate_scenario", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(tools_module, "prove", lambda *_args, **_kwargs: Proof(deferred=True, policy_checks=[advice]))
    said = accept_scenario(
        {"name": "advocate", "instruction": "You represent the customer."},
        world_root=tmp_path,
        catalogue=Catalogue(),
        kept=[],
        allow_empty_solution=True,
        persist=False,
    )

    assert not said.get("is_error"), said
    assert "POSSIBLE RULE CONFLICT" in said["content"][0]["text"] and "not the customer" in said["content"][0]["text"]


def test_aim_for_says_a_case_is_refused_only_when_that_gate_is_on(tmp_path, monkeypatch):
    import json

    def described(on: set[str]) -> str:
        for gate in (FAULT_LEVEL_GATE, CHECKED_LEVEL_GATE):
            if gate in on:
                monkeypatch.setenv(gate, "1")
            else:
                monkeypatch.delenv(gate, raising=False)
        server, _ = scenario_tools(_environment(tmp_path), tmp_path, tmp_path, wanted=4)
        aim = next(one for one in server.tools if one.name == "aim_for")
        return json.dumps(aim.input_schema)

    assert "refuses" not in described(set())
    assert "refuses one that does neither" in described({FAULT_LEVEL_GATE})
    assert "refuses one that does not" in described({CHECKED_LEVEL_GATE})
    assert "refuses one that does neither" not in described({CHECKED_LEVEL_GATE})
