"""The proof record says whether the reference solution really ran, and counts that apart."""

from __future__ import annotations

from fi.alk.harness import prove as module
from fi.alk.harness.proofs import load_proofs, proof_counts, record_proof, record_unprovable
from fi.alk.harness.prove import Proof

from proof_world import ONE_REFUND, catalogue, install, scenario

REFUND = [{"tool": "refund_order", "arguments": {"order_id": "O-10"}, "fixed": {"order_id": "the caller"}}]


def test_assumed_steps_are_saved_and_not_proved(monkeypatch, tmp_path):
    install(monkeypatch, runtime_tools={"place_order"})
    case = scenario(
        [{"tool": "place_order", "arguments": {"order_id": "O-10"}, "fixed": {"order_id": "the caller"}}],
        sub_goals=["placed"],
    )
    proof = module.prove(case, catalogue(placed="def check(world, calls):\n    return None if any(c.name == 'place_order' and c.arguments.get('order_id') == 'O-10' for c in calls) else 'not placed'\n"), tmp_path)

    record_proof(tmp_path, "refund_case", proof, solution_steps=1)
    saved = load_proofs(tmp_path)["refund_case"]

    assert proof.holds is True
    assert saved["assumed"] == ["place_order"]
    assert saved["proved"] is False
    assert saved["blocked_reason"] == "solution_assumed"
    assert saved["kept_as_written"] is True
    assert saved["format"] == 2 and saved["solution_steps"] == 1


def test_proof_counts_split_kept_as_written(tmp_path):
    proved = Proof(ready=True, solvable=True, vacuous=False)
    assumed = Proof(ready=True, solvable=True, vacuous=False, assumed=["place_order"])
    deferred = Proof(deferred=True)
    failing = Proof(ready=True, solvable=False, vacuous=False)
    for key in ("a", "b", "c"):
        record_proof(tmp_path, key, proved)
    record_proof(tmp_path, "d", assumed)
    record_proof(tmp_path, "e", assumed)
    record_proof(tmp_path, "f", deferred)
    record_proof(tmp_path, "g", failing)

    counts = proof_counts(load_proofs(tmp_path), ["a", "b", "c", "d", "e", "f", "g"])

    assert counts == {"proved": 3, "kept_as_written": 3, "not_proved": 1}


def test_unprovable_is_format_two(tmp_path):
    record_unprovable(tmp_path, "x", "ValueError")
    saved = load_proofs(tmp_path)["x"]
    assert saved["format"] == 2
    assert saved["blocked_reason"] == "broken_check"
    assert saved["proved"] is False and saved["why"] == "ValueError"


def test_deferred_reads_proof_deferred_not_not_ready(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "native_runtime", lambda world_root: True)
    monkeypatch.setattr(module, "workspace_fixture", lambda *a: False)
    case = scenario(REFUND, sub_goals=["refund_once"])
    proof = module.prove(case, catalogue(refund_once=ONE_REFUND), tmp_path)
    assert proof.deferred and not proof.ready
    assert proof.blocked_reason() == "proof_deferred"
    assert proof.kept_as_written is True


def test_holds_failures_map_to_codes():
    base = dict(ready=True, solvable=True, vacuous=False)
    assert Proof(**base, weak=["no_refund"]).blocked_reason() == "weak_check"
    assert Proof(**base, failed_attempts=["tried"]).blocked_reason() == "credits_failed_attempt"
    assert Proof(**base, too_strict=["refund_once (alternative 1)"]).blocked_reason() == "too_strict"
    assert Proof(**base, unchecked=["refund_order.order_id"]).blocked_reason() == "unchecked"
    for proof in (Proof(**base, weak=["x"]), Proof(**base, unchecked=["y"])):
        assert proof.kept_as_written is False
