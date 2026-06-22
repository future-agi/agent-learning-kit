"""Tests for the bench artifact_in coding lane (phase 15B).

Covers the code-tests verifier (accepts gold, fails broken / fake-success /
timeout), the coding suite loader/validation, the artifact_in run path, and the
``bench_contract_readiness`` release gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_learning import bench, trinity
from agent_learning.bench import _coding
from agent_learning.bench._codeexec import run_code_tests

ROOT = Path(__file__).parent.parent
SUITE = ROOT / "examples" / "bench_suites" / "coding_starter.json"

_CHECKS = (
    "import solution\n\n"
    "def check_a():\n    assert solution.f(2) == 4\n\n"
    "def check_b():\n    assert solution.f(3) == 9\n"
)


def test_verifier_accepts_correct() -> None:
    r = run_code_tests("def f(x):\n    return x * x\n", _CHECKS)
    assert r["result"]["scalar"] == 1.0
    assert all(r["result"]["pass_fail"].values())


def test_verifier_fails_wrong() -> None:
    r = run_code_tests("def f(x):\n    return 0\n", _CHECKS)
    assert r["result"]["scalar"] == 0.0
    assert not any(r["result"]["pass_fail"].values())


def test_verifier_fails_fake_success_noop() -> None:
    # No entrypoint defined; just prints success -> the held-out oracle fails it.
    r = run_code_tests("print('done!')\n", _CHECKS)
    assert r["result"]["scalar"] == 0.0


def test_verifier_enforces_timeout() -> None:
    r = run_code_tests("import time\ndef f(x):\n    time.sleep(30)\n    return x*x\n", _CHECKS, timeout_s=2.0)
    assert r["result"]["scalar"] == 0.0
    assert r["raw"]["timed_out"] is True


def test_verifier_rejects_unknown_sandbox_and_language() -> None:
    assert "unknown sandbox" in run_code_tests("x=1", _CHECKS, sandbox="vm")["result"]["explanation"]
    assert "unsupported language" in run_code_tests("x=1", _CHECKS, language="rust")["result"]["explanation"]


def test_suite_loads_and_validates() -> None:
    suite = _coding.load_coding_suite(SUITE)
    assert suite["kind"] == _coding.BENCH_SUITE_KIND
    assert len(suite["tasks"]) >= 3


def test_suite_validation_rejects_malformed() -> None:
    base = {"kind": _coding.BENCH_SUITE_KIND, "name": "x", "tasks": []}
    with pytest.raises(_coding.CodingSuiteError):
        _coding.load_coding_suite(base)  # no tasks
    missing_field = {
        "kind": _coding.BENCH_SUITE_KIND,
        "tasks": [{"id": "t", "instruction": "i", "checks": "c"}],  # no reference_solution
    }
    with pytest.raises(_coding.CodingSuiteError):
        _coding.load_coding_suite(missing_field)
    no_guards = {
        "kind": _coding.BENCH_SUITE_KIND,
        "tasks": [
            {"id": "t", "instruction": "i", "checks": "c", "reference_solution": "s"}
        ],
    }
    with pytest.raises(_coding.CodingSuiteError):
        _coding.load_coding_suite(no_guards)  # missing guards.min_guard_count


def test_artifact_in_reference_all_pass() -> None:
    suite = _coding.load_coding_suite(SUITE)
    ref = _coding.reference_submission(suite)
    res = bench.run_bench(
        SUITE, control_mode="artifact_in", submission=ref,
        evidence_class="local_gate", emit_telemetry=False,
    )
    assert res["control_mode"] == "artifact_in"
    assert res["modalities"] == ["coding"]
    assert res["aggregate"]["pass_rate"] == 1.0
    for row in res["per_task"]:
        assert row["verdict"] == "pass"
        assert row["execution_class"] == "executable"
        assert row["overclaim"] is False


def test_artifact_in_broken_and_missing() -> None:
    suite = _coding.load_coding_suite(SUITE)
    ref = _coding.reference_submission(suite)
    broken = dict(ref)
    first = suite["tasks"][0]["id"]
    broken[first] = "def nope():\n    return 1\n"
    del broken[suite["tasks"][1]["id"]]  # missing submission -> void
    res = bench.run_bench(
        SUITE, control_mode="artifact_in", submission=broken, emit_telemetry=False,
    )
    by = {r["task_id"]: r for r in res["per_task"]}
    assert by[first]["verdict"] == "fail"
    assert by[suite["tasks"][1]["id"]]["verdict"] == "void"


def test_artifact_in_requires_submission() -> None:
    with pytest.raises(bench.BenchError):
        bench.run_bench(SUITE, control_mode="artifact_in", emit_telemetry=False)


def test_coding_suite_rejects_push_mode() -> None:
    with pytest.raises(bench.BenchError):
        bench.run_bench(SUITE, {"type": "scripted"}, control_mode="push", emit_telemetry=False)


def test_artifact_in_is_deterministic() -> None:
    suite = _coding.load_coding_suite(SUITE)
    ref = _coding.reference_submission(suite)
    a = bench.run_bench(SUITE, control_mode="artifact_in", submission=ref, emit_telemetry=False)
    b = bench.run_bench(SUITE, control_mode="artifact_in", submission=ref, emit_telemetry=False)
    sa = {r["task_id"]: r["result"]["scalar"] for r in a["per_task"]}
    sb = {r["task_id"]: r["result"]["scalar"] for r in b["per_task"]}
    assert sa == sb


def test_bench_contract_gate_clean() -> None:
    st = trinity._release_bench_contract_status(ROOT)
    assert st["kind"] == "agent-learning.bench-contract-readiness.v1"
    for bucket in (
        "missing_files",
        "suite_errors",
        "reference_pass_errors",
        "discrimination_errors",
        "determinism_errors",
        "oracle_held_out_errors",
        "guard_errors",
    ):
        assert st[bucket] == [], f"{bucket}: {st[bucket]}"


def _good_artifact() -> dict:
    return {
        "kind": "agent-learning.coding-benchmark-example.v1",
        "gate_evidence": {
            "reference_pass": {"all_reference_solutions_pass": True},
            "discrimination": {"broken_candidate_fails": True, "fake_success_noop_fails": True},
            "determinism": {"scores_identical_across_runs": True},
            "oracle_held_out": {"checks_not_in_reference": True},
            "guard_presence": {"all_tasks_have_guards": True},
            "honesty": {"no_executable_overclaim": True},
        },
    }


# Each mutation -> the bucket that MUST fire. A gate that cannot fail is worthless;
# the gate audits the example's self-reported evidence, so prove every bucket bites.
_BUCKET_FIRES = [
    ("kind", lambda a: a.update(kind="wrong"), "suite_errors"),
    (
        "reference_pass",
        lambda a: a["gate_evidence"]["reference_pass"].update(all_reference_solutions_pass=False),
        "reference_pass_errors",
    ),
    (
        "broken_candidate",
        lambda a: a["gate_evidence"]["discrimination"].update(broken_candidate_fails=False),
        "discrimination_errors",
    ),
    (
        "noop",
        lambda a: a["gate_evidence"]["discrimination"].update(fake_success_noop_fails=False),
        "discrimination_errors",
    ),
    (
        "determinism",
        lambda a: a["gate_evidence"]["determinism"].update(scores_identical_across_runs=False),
        "determinism_errors",
    ),
    (
        "oracle",
        lambda a: a["gate_evidence"]["oracle_held_out"].update(checks_not_in_reference=False),
        "oracle_held_out_errors",
    ),
    (
        "guards",
        lambda a: a["gate_evidence"]["guard_presence"].update(all_tasks_have_guards=False),
        "guard_errors",
    ),
    (
        "overclaim",
        lambda a: a["gate_evidence"]["honesty"].update(no_executable_overclaim=False),
        "guard_errors",
    ),
]


@pytest.mark.parametrize("label,mutate,bucket", _BUCKET_FIRES, ids=[c[0] for c in _BUCKET_FIRES])
def test_bench_contract_gate_buckets_fire(monkeypatch, label, mutate, bucket) -> None:
    artifact = _good_artifact()
    mutate(artifact)
    monkeypatch.setattr(trinity, "_exec_example_run", lambda *a, **k: (artifact, None))
    st = trinity._release_bench_contract_status(ROOT)
    assert st[bucket], f"expected {bucket} to fire for mutation {label!r}"


def test_bench_contract_gate_fires_on_run_error(monkeypatch) -> None:
    monkeypatch.setattr(trinity, "_exec_example_run", lambda *a, **k: ({}, "boom"))
    st = trinity._release_bench_contract_status(ROOT)
    assert st["suite_errors"]
