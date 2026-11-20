"""A sub-goal checks what its name says: an order it names is checked as an order, a write it expects once is checked
once, its label says what runs, a rule change finds the sub-goals that quote the rule, and a held case's advice names
the sub-goal code cannot grade."""

from __future__ import annotations

import asyncio
import json

from fi.alk.harness.amend import _sub_goals_quoting
from fi.alk.harness.catalogue import Catalogue, SubGoal, save_catalogue, settled_by
from fi.alk.harness.checks import came_before, check_namespace, exactly_once
from fi.alk.harness.prove import Proof
from fi.alk.harness.world.runtime import Call

LOOKUP = Call("get_order", {"order_id": "1043"})
REFUND = Call("issue_refund", {"order_id": "1043", "amount": 89})
REFUSED = Call("issue_refund", {"order_id": "1043", "amount": 89}, ok=False, refused=True, refusal_code="world_rule")


def test_an_order_is_checked_against_the_first_attempt_and_holds_when_the_later_step_never_came():
    assert came_before([LOOKUP, REFUND], "get_order", "issue_refund", order_id="1043")
    assert not came_before([REFUND, LOOKUP], "get_order", "issue_refund")
    # The first attempt counts, even one the world refused.
    assert not came_before([REFUSED, LOOKUP, REFUND], "get_order", "issue_refund")
    assert not came_before(
        [Call("get_order", {"order_id": "1044"}), REFUND], "get_order", "issue_refund", order_id="1043"
    )
    assert came_before([], "get_order", "issue_refund")


def test_a_write_expected_once_fails_twice_and_never_counts_a_refused_try():
    assert exactly_once([LOOKUP, REFUSED, REFUND], "issue_refund", order_id="1043")
    assert not exactly_once([REFUND, REFUND], "issue_refund", order_id="1043")
    assert not exactly_once([LOOKUP], "issue_refund")
    assert {"came_before", "exactly_once"} <= set(check_namespace())


def test_a_sub_goal_is_labelled_by_what_runs_never_by_the_label_given(tmp_path, monkeypatch):
    from test_scenario_quality_gates import _server

    check = "def check(world, calls):\n    return None if any(c.arguments for c in calls) else 'not done'\n"
    assert settled_by(SubGoal(name="a", check=check)) == "code"
    assert settled_by(SubGoal(name="a", judged="x")) == "llm"
    assert settled_by(SubGoal(name="a", check=check, judged="x")) == "both"

    handlers, _ = _server(tmp_path, monkeypatch)
    said = asyncio.run(
        handlers["add_sub_goal"](
            {"name": "refuses_twice", "what": "Refuses a second refund", "check": check, "verifier": "llm"}
        )
    )
    assert not said.get("is_error"), said
    saved = json.loads((tmp_path / "sub_goals.json").read_text(encoding="utf-8"))
    assert next(one for one in saved["sub_goals"] if one["name"] == "refuses_twice")["verifier"] == "code"


def test_a_rule_change_lists_the_sub_goals_that_quote_the_rule(tmp_path):
    rule = "Refund only within 30 days of delivery."
    save_catalogue(
        Catalogue(
            sub_goals=[
                SubGoal(name="refuses_late", what="x", judged="x", reference=f'The rule: "{rule}"'),
                SubGoal(name="looks_up", what="Looks the order up", judged="x"),
            ]
        ),
        tmp_path,
    )

    assert _sub_goals_quoting(tmp_path, rule) == ["refuses_late"]
    assert _sub_goals_quoting(tmp_path, "Never sell more than 130 items.") == []


def test_a_control_that_passes_names_the_judge_only_sub_goals_not_the_nearest_check():
    control = {"name": "reads_out_the_card", "why": "reads another customer's card", "ran": True, "failed": False}
    held = Proof(ready=True, solvable=True, vacuous=False, controls=[{**control, "held": ["looks_up"]}])

    named = Proof(**{**held.__dict__, "judged_goals": ["privacy_pii_protected"]})
    assert "privacy_pii_protected" in named.why() and "Tighten the named checks" not in named.why()
    assert "Tighten the named checks" in held.why()


def test_the_proof_records_which_of_the_case_s_sub_goals_only_a_judge_grades(monkeypatch, tmp_path):
    from fi.alk.harness import prove as module

    from proof_world import REFUND_CATALOGUE, catalogue, install, refund_case

    install(monkeypatch)
    goals = catalogue(**REFUND_CATALOGUE)
    goals.sub_goals.append(SubGoal(name="privacy_pii_protected", what="x", judged="x"))
    case = refund_case(sub_goals=["refund_once", "looked_up", "privacy_pii_protected"])

    proof = module.prove(case, goals, tmp_path, contract=None, actors=[])

    assert proof.judged_goals == ["privacy_pii_protected"]
