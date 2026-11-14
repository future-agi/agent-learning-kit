"""A cited source must say what the scenario claims; no sources at all is not a fault."""

from __future__ import annotations

from types import SimpleNamespace

from fi.alk.harness import prove as module
from fi.alk.harness.grounding import resolve_sources
from fi.alk.harness.scenario import ScenarioSource

from proof_world import REFUND_CATALOGUE, catalogue, install, refund_case

CONTRACT = SimpleNamespace(hard_constraints=["Never refund an order older than 14 days.", "Refund each order at most once."])


def test_rule_quote_must_match(tmp_path):
    wrong = SimpleNamespace(sources=[ScenarioSource(kind="rule", ref="rule:0", quote="older than 30 days")])
    right = SimpleNamespace(sources=[ScenarioSource(kind="rule", ref="rule:0", quote="at most once")])
    assert resolve_sources(wrong, CONTRACT, tmp_path)[0]["resolved"] is False
    resolved = resolve_sources(right, CONTRACT, tmp_path)[0]
    assert resolved["resolved"] is True and resolved["resolved_ref"] == "rule:1"


def test_no_sources_is_not_blocking(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(sources=[]), catalogue(**REFUND_CATALOGUE), tmp_path, contract=CONTRACT, actors=[])
    assert proof.sources == []
    assert proof.proved is True


def _thirty_day_case():
    from fi.alk.harness.catalogue import Catalogue, SubGoal

    goals = Catalogue(
        sub_goals=[SubGoal(name="refunds_late_order", what="The agent refunds an order placed 30 days ago.", judged="x")]
    )
    case = SimpleNamespace(
        scenario_key="late_refund",
        sub_goals=["refunds_late_order"],
        sources=[ScenarioSource(kind="rule", ref="rule:0", quote="older than 14 days")],
    )
    return case, goals


def test_30_days_against_a_14_day_rule_conflicts(monkeypatch, tmp_path):
    from fi.alk.harness.grounding import policy_conflicts

    case, goals = _thirty_day_case()
    asked: list[tuple[str, str]] = []

    def judge(expected, rule):
        asked.append((expected, rule))
        return True, "the rule allows 14 days, the case expects 30"

    found = policy_conflicts(case, goals, list(CONTRACT.hard_constraints), tmp_path, judge=judge)

    assert asked == [("- The agent refunds an order placed 30 days ago.", "Never refund an order older than 14 days.")]
    assert found == [
        {
            "rule": "Never refund an order older than 14 days.",
            "ref": "rule:0",
            "conflict": True,
            "why": "the rule allows 14 days, the case expects 30",
        }
    ]
    # The proof blocks on it, before a leak or an unresolved source would.
    install(monkeypatch)
    proof = module.prove(
        refund_case(sources=list(case.sources)),
        catalogue(**REFUND_CATALOGUE),
        tmp_path / "proof",
        contract=CONTRACT,
        actors=[],
        conflict_judge=judge,
    )
    assert proof.blocked_reason() == "policy_conflict" and proof.proved is False
    assert "contradicts a cited rule" in proof.why()
    no_conflict = module.prove(
        refund_case(sources=list(case.sources)),
        catalogue(**REFUND_CATALOGUE),
        tmp_path / "proof2",
        contract=CONTRACT,
        actors=[],
        conflict_judge=lambda expected, rule: (False, "consistent"),
    )
    assert no_conflict.blocked_reason() is None


def test_cache_hit_makes_no_call(tmp_path):
    from fi.alk.harness.grounding import POLICY_CHECKS, policy_conflicts

    case, goals = _thirty_day_case()
    calls: list[str] = []

    def judge(expected, rule):
        calls.append(rule)
        return True, "contradicts"

    rules = list(CONTRACT.hard_constraints)
    first = policy_conflicts(case, goals, rules, tmp_path, judge=judge)
    second = policy_conflicts(case, goals, rules, tmp_path, judge=judge)

    assert len(calls) == 1
    assert first == second
    assert (tmp_path / POLICY_CHECKS).is_file()


def test_undecided_judge_is_not_a_conflict_and_is_not_cached(tmp_path):
    from fi.alk.harness.grounding import policy_conflicts

    case, goals = _thirty_day_case()
    calls: list[str] = []

    def judge(expected, rule):
        calls.append(rule)
        return None, ""

    rules = list(CONTRACT.hard_constraints)
    assert policy_conflicts(case, goals, rules, tmp_path, judge=judge)[0]["conflict"] is None
    policy_conflicts(case, goals, rules, tmp_path, judge=judge)
    assert len(calls) == 2
