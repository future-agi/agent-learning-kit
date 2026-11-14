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
