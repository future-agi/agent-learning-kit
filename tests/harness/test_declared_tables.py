"""A tool's declared `tables_changed` checked against what its scenarios' proof runs changed: a proof records its
solution trial, and certification flags a changed table the one writing tool does not declare."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from fi.alk.harness import certification
from fi.alk.harness import prove as module
from fi.alk.harness.authoring_runtime_validation import _write_runtime_evidence
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.certification import CheckStatus
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.proofs import load_proofs, record_proof
from fi.alk.harness.prove import Proof
from fi.alk.harness.tool_certification import certify_tool_inventory
from fi.alk.harness.world.runtime import Call

CONTRACT = {
    "tools": [
        {"name": "lookup", "tables_read": ["ledger"]},
        {"name": "post_entry", "tables_changed": ["ledger"]},
        {"name": "close_period", "tables_changed": ["periods"]},
    ],
    "tool_entrypoints": [
        {"tool": "lookup", "mode": "service", "endpoint": "/lookup", "method": "GET"},
        {"tool": "post_entry", "mode": "service", "endpoint": "/post", "method": "POST"},
        {"tool": "close_period", "mode": "service", "endpoint": "/close", "method": "POST"},
    ],
}


def _trial(calls, changed, unattributable=""):
    return {"trial": {"calls": calls, "changed": changed, "unattributable": unattributable}}


def _certify(tmp_path, monkeypatch, proofs):
    """Certification evidence for `CONTRACT` with these proof runs saved: (each tool's reason, limitations)."""
    captured = {}

    class Store:
        def __init__(self, _root):
            pass

        def write_runtime_evidence(self, evidence):
            captured["limitations"] = evidence.limitations

        def write_tool_certification(self, report):
            captured["reasons"] = {row.tool: row.reason for row in report.tools}

        def write_action_certification(self, _report):
            pass

    monkeypatch.setattr(certification, "GenericHarnessArtifactStore", Store)
    contract = AgentContract.model_validate(CONTRACT)
    (tmp_path / "contract.json").write_text(contract.model_dump_json(), encoding="utf-8")
    for key, proof in proofs.items():
        record_proof(tmp_path, key, Proof(ready=True, solvable=True, vacuous=False, **proof))
    _write_runtime_evidence(
        job=SimpleNamespace(),
        authoring=tmp_path,
        manifest=SimpleNamespace(provenance=SimpleNamespace(source_digest="a" * 64), digest="sha256:" + "d" * 64),
        count=1,
        external_provider=True,
        tool_report=certify_tool_inventory(contract, tmp_path, external_provider=True),
        action_report=SimpleNamespace(certified=0, executable=0, total=0),
        reset_equivalence=CheckStatus.NOT_APPLICABLE,
        world_isolation=CheckStatus.NOT_APPLICABLE,
        chat_ingress=CheckStatus.NOT_APPLICABLE,
    )
    return captured["reasons"], "\n".join(captured["limitations"])


@pytest.mark.parametrize(
    ("changed", "flagged"),
    [(["ledger"], False), (["audit_log", "ledger"], True)],
)
def test_a_changed_table_the_one_writing_tool_does_not_declare_is_flagged(tmp_path, monkeypatch, changed, flagged):
    reasons, _ = _certify(tmp_path, monkeypatch, {"s1": _trial(["lookup", "post_entry", "post_entry"], changed)})
    assert ("audit_log" in reasons["post_entry"]) is flagged
    assert "audit_log" not in reasons["lookup"] + reasons["close_period"]


@pytest.mark.parametrize(
    "trial",
    [
        _trial(["post_entry", "close_period"], ["audit_log", "periods"]),
        _trial(["post_entry"], ["audit_log"], "a world actor acted during it"),
        _trial(["lookup"], ["audit_log"]),
    ],
)
def test_a_proof_run_without_one_writing_tool_is_skipped_and_named(tmp_path, monkeypatch, trial):
    reasons, limitations = _certify(tmp_path, monkeypatch, {"s1": trial})
    assert not any("audit_log" in reason for reason in reasons.values())
    assert "s1" in limitations


def _proved(monkeypatch, tmp_path, *, actor_calls=0, assumed=()):
    """`prove` over fake worlds: one entry posted to an empty ledger, a refused retry, and a lookup."""
    solution = [Call("lookup", {}), Call("post_entry", {}), Call("post_entry", {}, ok=False, refused=True)]
    outcome = SimpleNamespace(ok=True, said="", broken=False)
    ready = SimpleNamespace(close=lambda: None)
    end = {"ledger": [{"id": 7}], "periods": [{"id": 1}]}
    acted = [Call("post", {})] * actor_calls
    solved = SimpleNamespace(close=lambda: None, state=lambda: end, calls=[*solution, *acted])
    start = {"ledger": [], "periods": [{"id": 1}]}
    untouched = SimpleNamespace(close=lambda: None, state=lambda: start, calls=[])
    monkeypatch.setattr(module, "prepared", lambda *a: (ready, outcome, outcome))
    monkeypatch.setattr(
        module,
        "run_steps",
        lambda *a, with_solution, steps=None, **_: (
            (solved, list(solution), [], list(assumed)) if with_solution else (untouched, [], [], [])
        ),
    )
    check = "def check(world, calls):\n    return None if any(c.ok for c in calls) else 'no entry'\n"
    catalogue = Catalogue(sub_goals=[SubGoal(name="posted", what="An entry is posted", check=check)])
    scenario = SimpleNamespace(sub_goals=["posted"], solution=(), setup_code="", ready_code="")
    proof = module.prove(scenario, catalogue, tmp_path)
    record_proof(tmp_path, "s1", proof)
    return load_proofs(tmp_path)["s1"]["trial"]


def test_a_saved_proof_carries_the_calls_the_world_took_and_the_tables_that_changed(monkeypatch, tmp_path) -> None:
    trial = _proved(monkeypatch, tmp_path)
    assert (trial["calls"], trial["changed"], trial["unattributable"]) == (["lookup", "post_entry"], ["ledger"], "")


@pytest.mark.parametrize(("actor_calls", "assumed"), [(1, ()), (0, ("post_entry",))])
def test_a_proof_with_an_actor_acting_or_an_assumed_step_cannot_attribute_its_tables(
    monkeypatch, tmp_path, actor_calls, assumed
) -> None:
    assert _proved(monkeypatch, tmp_path, actor_calls=actor_calls, assumed=assumed)["unattributable"]
