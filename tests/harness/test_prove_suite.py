"""Every certification proves every case again, writing each proof as it goes."""

from __future__ import annotations

import json
import time

from fi.alk.harness import prove as module
from fi.alk.harness import prove_suite as suite_module
from fi.alk.harness.prove_suite import main, prove_suite

from proof_world import REFUND_CATALOGUE, catalogue, install, refund_case


def _suite(monkeypatch, cases):
    monkeypatch.setattr(suite_module, "load_scenarios", lambda path: cases)
    monkeypatch.setattr(suite_module, "load_catalogue", lambda path: catalogue(**REFUND_CATALOGUE))
    monkeypatch.setattr(suite_module, "load_proof_contract", lambda root: None)
    monkeypatch.setattr(suite_module, "load_world_actors", lambda root: [])


def test_writes_every_key_and_survives_one_crash(monkeypatch, tmp_path):
    install(monkeypatch)
    cases = [refund_case(name="one", scenario_key="one"), refund_case(name="two", scenario_key="two")]
    _suite(monkeypatch, cases)
    real = module.prove

    def flaky(scenario, *args, **kwargs):
        if scenario.name == "two":
            raise ValueError("the world exploded\nsecond line")
        return real(scenario, *args, **kwargs)

    monkeypatch.setattr(suite_module, "prove", flaky)
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

    monkeypatch.setattr(suite_module, "prove", counting)
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


def test_a_crash_s_reason_is_written_without_the_secrets_in_it(monkeypatch, tmp_path):
    install(monkeypatch)
    _suite(monkeypatch, [refund_case(name="one", scenario_key="one")])

    def crashes(*args, **kwargs):
        raise RuntimeError("could not connect to postgresql://admin:hunter2@db:5432/world")

    monkeypatch.setattr(suite_module, "prove", crashes)
    proofs = prove_suite(tmp_path, tmp_path / "proofs.json")

    assert "hunter2" not in proofs["one"]["why"] and "db:5432/world" in proofs["one"]["why"]


def test_a_large_suite_writes_its_proofs_in_batches_and_ends_with_every_case(monkeypatch, tmp_path):
    install(monkeypatch)
    _suite(monkeypatch, [refund_case(name=f"c{n}", scenario_key=f"c{n}") for n in range(40)])
    writes = []
    real = suite_module._write

    def counted(output, proofs):
        writes.append(len(proofs))
        real(output, proofs)

    monkeypatch.setattr(suite_module, "_write", counted)
    output = tmp_path / "proofs.json"
    proofs = prove_suite(tmp_path, output)
    # Not the whole file after every case: a thousand cases would rewrite it a thousand times.
    assert len(writes) < 40
    assert set(json.loads(output.read_text(encoding="utf-8"))) == set(proofs) == {f"c{n}" for n in range(40)}


def test_each_case_s_own_goals_read_the_suite_s_shared_goals_once(monkeypatch, tmp_path):
    install(monkeypatch)
    _suite(monkeypatch, [refund_case(name=f"c{n}", scenario_key=f"c{n}") for n in range(5)])
    reads = []
    real = suite_module.shared_goals

    def counted(suite):
        reads.append(len(suite))
        return real(suite)

    monkeypatch.setattr(suite_module, "shared_goals", counted)
    monkeypatch.setattr(module, "shared_goals", counted)
    prove_suite(tmp_path, tmp_path / "proofs.json", judge_case_goals=True)
    assert reads == [5]


def test_a_case_the_live_pass_did_not_play_is_not_kept_as_written_on_steps_that_never_ran(monkeypatch, tmp_path):
    from fi.alk.harness import live_proof

    # Offline nothing runs `refund_order`: its steps are assumed and, alone, the case would be kept as written.
    install(monkeypatch, runtime_tools={"refund_order"})
    _suite(monkeypatch, [refund_case(name=key, scenario_key=key) for key in ("unplayed", "not_asked")])
    alone = prove_suite(tmp_path, tmp_path / "alone.json")
    assert alone["unplayed"]["kept_as_written"] is True and alone["unplayed"]["ready"] is True
    live_proof.write_unplayed(tmp_path, {"unplayed": "ProcessRuntimeError: seed_failed"})

    proofs = prove_suite(tmp_path, tmp_path / "proofs.json")

    assert proofs["unplayed"]["blocked_reason"] == "validation_incomplete"
    assert proofs["unplayed"]["ready"] is False and proofs["unplayed"]["kept_as_written"] is False
    assert "seed_failed" in proofs["unplayed"]["why"]
    # A case the live pass was never meant to play keeps what its offline proof says.
    assert proofs["not_asked"] == {**alone["not_asked"], "proved_at": proofs["not_asked"]["proved_at"]}


def test_an_unplayed_case_whose_offline_proof_ran_every_step_keeps_that_proof(monkeypatch, tmp_path):
    from fi.alk.harness import live_proof

    install(monkeypatch)
    _suite(monkeypatch, [refund_case(name="ran", scenario_key="ran")])
    live_proof.write_unplayed(tmp_path, {"ran": "the time budget ran out before it"})
    assert prove_suite(tmp_path, tmp_path / "proofs.json")["ran"]["proved"] is True
