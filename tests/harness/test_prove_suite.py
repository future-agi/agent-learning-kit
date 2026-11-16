"""Every certification proves every case again, writing each proof as it goes."""

from __future__ import annotations

import json
import time

from fi.alk.harness import catalogue as catalogue_module
from fi.alk.harness import prove as module
from fi.alk.harness import scenario_tools
from fi.alk.harness.prove_suite import main, prove_suite

from proof_world import REFUND_CATALOGUE, catalogue, install, refund_case


def _suite(monkeypatch, cases):
    monkeypatch.setattr(scenario_tools, "load_scenarios", lambda path: cases)
    monkeypatch.setattr(catalogue_module, "load_catalogue", lambda path: catalogue(**REFUND_CATALOGUE))
    monkeypatch.setattr(module, "_load_contract", lambda root: None)
    monkeypatch.setattr(module, "_load_actors", lambda root: [])


def test_writes_every_key_and_survives_one_crash(monkeypatch, tmp_path):
    install(monkeypatch)
    cases = [refund_case(name="one", scenario_key="one"), refund_case(name="two", scenario_key="two")]
    _suite(monkeypatch, cases)
    real = module.prove

    def flaky(scenario, *args, **kwargs):
        if scenario.name == "two":
            raise ValueError("the world exploded\nsecond line")
        return real(scenario, *args, **kwargs)

    monkeypatch.setattr(module, "prove", flaky)
    output = tmp_path / "out" / "scenario-proofs.json"
    assert main(["--authoring", str(tmp_path), "--output", str(output)]) == 0
    proofs = json.loads(output.read_text(encoding="utf-8"))
    assert proofs["one"]["proved"] is True
    assert proofs["two"]["blocked_reason"] == "broken_check"
    assert proofs["two"]["why"] == "ValueError: the world exploded"


def test_missing_record_reads_not_ready(monkeypatch, tmp_path):
    install(monkeypatch, ready=False, ready_said="order O-999 is not in the world")
    _suite(monkeypatch, [refund_case(name="o999", scenario_key="o999")])
    proofs = prove_suite(tmp_path, tmp_path / "proofs.json")
    assert proofs["o999"]["blocked_reason"] == "not_ready"
    assert "O-999" in proofs["o999"]["why"]


def test_budget_writes_remaining_as_incomplete(monkeypatch, tmp_path):
    install(monkeypatch)
    _suite(monkeypatch, [refund_case(name="a", scenario_key="a"), refund_case(name="b", scenario_key="b")])
    output = tmp_path / "proofs.json"
    assert main(["--authoring", str(tmp_path), "--output", str(output), "--budget-seconds", "0"]) == 0
    proofs = json.loads(output.read_text(encoding="utf-8"))
    assert set(proofs) == {"a", "b"}
    assert all(one["blocked_reason"] == "validation_incomplete" for one in proofs.values())
    assert all("budget" in one["why"] for one in proofs.values())
    past = str(time.time() - 10)
    assert main(["--authoring", str(tmp_path), "--output", str(output), "--deadline-at", past]) == 0
    assert all(one["blocked_reason"] == "validation_incomplete" for one in json.loads(output.read_text()).values())


def test_keys_prove_only_the_named_cases(monkeypatch, tmp_path):
    install(monkeypatch)
    _suite(monkeypatch, [refund_case(name=key, scenario_key=key) for key in ("a", "b", "c")])
    proved: list[str] = []
    real = module.prove

    def counting(scenario, *args, **kwargs):
        proved.append(scenario.scenario_key)
        return real(scenario, *args, **kwargs)

    monkeypatch.setattr(module, "prove", counting)
    output = tmp_path / "proofs.json"
    assert main(["--authoring", str(tmp_path), "--output", str(output), "--keys", "b, c"]) == 0
    assert proved == ["b", "c"]
    assert set(json.loads(output.read_text(encoding="utf-8"))) == {"b", "c"}


def test_a_proof_says_whether_the_live_runtime_made_it(monkeypatch, tmp_path):
    from fi.alk.harness import live_proof

    install(monkeypatch)
    _suite(monkeypatch, [refund_case(name=key, scenario_key=key) for key in ("live", "offline")])
    # On the live runtime the reference did not hold.
    live_proof.write_live(
        tmp_path,
        {"live": {"assumed": [], "with_solution": [{"name": "refund_once", "held": False, "said": "no refund"}],
                  "with_nothing": [], "outcome": "change", "controls": []}},
    )
    proofs = prove_suite(tmp_path, tmp_path / "proofs.json")
    assert proofs["live"]["live"] is True and proofs["live"]["blocked_reason"] == "not_solvable"
    assert proofs["offline"]["live"] is False and proofs["offline"]["proved"] is True
