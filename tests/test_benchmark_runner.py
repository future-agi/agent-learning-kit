"""Beat-HUD B2 — benchmark runner tests.

Deterministic unit tests via the injectable ``runner`` seam (no engine), plus ONE
real end-to-end run through the existing engine on the credential-free scripted
agent (the execution spike's fixture lane). Pins: aggregation, deterministic
ordering, per-task + rollup scoring, honesty (fixture vs live), and the overclaim
flag (a non-live execution_class carrying a live evidence_class).
"""

from __future__ import annotations

import pytest

from agent_learning import tasks


# --- fixtures ---------------------------------------------------------------
def _objective() -> dict:
    return {
        "source": "declared",
        "evals": [
            {"eval": "task_success", "weight": 1.0, "anchor": True},
            {"eval": "instruction_adherence", "weight": 0.4},
        ],
        "guards": {
            "sentinel_rows": [{"id": "answerable_without_tool"}],
            "min_guard_count": 1,
        },
    }


def _task(task_id: str, world_kind: str = "conversation", difficulty: str = "easy") -> dict:
    return {
        "id": task_id,
        "title": f"task {task_id}",
        "world": {"kind": world_kind},
        "difficulty": difficulty,
        "objective": _objective(),
        "scenario": {
            "name": task_id,
            "kind": "task",
            "dataset": [{"persona": {"name": "P"}, "situation": "s", "outcome": "o"}],
        },
        "verification": {"checks": [{"type": "contains", "value": "x"}], "threshold": 0.5},
    }


def _dataset() -> dict:
    return tasks.compile_task_dataset(
        {
            "name": "runner-mini",
            "tasks": [
                _task("a-conv", "conversation", "easy"),
                _task("b-tool", "tool_api", "medium"),
                _task("c-browser", "browser", "hard"),
            ],
            "splits": {"test": ["a-conv", "b-tool"]},
        }
    )


def _fake_runner(scores: dict):
    """A deterministic runner seam: returns a run-result with the given score per
    task id (shape mirrors the real engine result the spike observed)."""

    def _run(task, agent):  # noqa: ANN001
        s = scores[task["id"]]
        return {
            "status": "passed" if s >= 0.5 else "failed",
            "summary": {
                "evaluation_score": s,
                "evaluation_passed": s >= 0.5,
                "metric_averages": {"task_completion": s},
            },
        }

    return _run


# --- aggregation + scoring --------------------------------------------------
def test_run_benchmark_aggregates_scores() -> None:
    ds = _dataset()
    runner = _fake_runner({"a-conv": 0.9, "b-tool": 0.4, "c-browser": 0.8})
    res = tasks.run_benchmark(ds, {"type": "scripted"}, runner=runner)
    assert res["kind"] == tasks.AGENT_LEARNING_BENCHMARK_RESULT_KIND
    agg = res["aggregate"]
    assert agg["count"] == 3
    assert agg["passed"] == 2  # 0.9 and 0.8 pass, 0.4 fails
    assert agg["mean_score"] == round((0.9 + 0.4 + 0.8) / 3, 6)
    assert agg["by_world_kind"]["conversation"]["passed"] == 1
    assert agg["by_difficulty"]["hard"]["mean_score"] == 0.8


def test_run_benchmark_deterministic_order() -> None:
    ds = _dataset()
    runner = _fake_runner({"a-conv": 0.9, "b-tool": 0.4, "c-browser": 0.8})
    res = tasks.run_benchmark(ds, {"type": "scripted"}, runner=runner)
    ids = [r["task_id"] for r in res["per_task"]]
    assert ids == sorted(ids)  # ordered by id


def test_run_benchmark_split_selects_subset() -> None:
    ds = _dataset()
    runner = _fake_runner({"a-conv": 0.9, "b-tool": 0.4, "c-browser": 0.8})
    res = tasks.run_benchmark(ds, {"type": "scripted"}, split="test", runner=runner)
    assert {r["task_id"] for r in res["per_task"]} == {"a-conv", "b-tool"}


def test_run_benchmark_max_tasks() -> None:
    ds = _dataset()
    runner = _fake_runner({"a-conv": 0.9, "b-tool": 0.4, "c-browser": 0.8})
    res = tasks.run_benchmark(ds, {"type": "scripted"}, max_tasks=1, runner=runner)
    assert res["aggregate"]["count"] == 1
    assert res["per_task"][0]["task_id"] == "a-conv"  # first by id


# --- honesty + overclaim ----------------------------------------------------
def test_fixture_lane_is_honest() -> None:
    ds = _dataset()
    runner = _fake_runner({"a-conv": 1.0, "b-tool": 1.0, "c-browser": 1.0})
    res = tasks.run_benchmark(ds, {"type": "scripted"}, evidence_class="captured_fixture", runner=runner)
    h = res["aggregate"]["honesty"]
    assert h["any_live"] is False
    assert h["any_overclaim"] is False
    assert all(r["overclaim"] is False for r in res["per_task"])


def test_overclaim_flagged_for_non_live_task_with_live_evidence() -> None:
    # browser/tool tasks are typed_only/executable; a typed_only task carrying a
    # live evidence_class is an overclaim and MUST be flagged.
    ds = _dataset()
    runner = _fake_runner({"a-conv": 1.0, "b-tool": 1.0, "c-browser": 1.0})
    res = tasks.run_benchmark(ds, {"type": "scripted"}, evidence_class="live_lane", runner=runner)
    browser_row = next(r for r in res["per_task"] if r["task_id"] == "c-browser")
    assert browser_row["execution_class"] == "typed_only"
    assert browser_row["overclaim"] is True
    assert res["aggregate"]["honesty"]["any_overclaim"] is True


def test_executable_task_with_live_evidence_is_not_overclaim() -> None:
    ds = _dataset()
    runner = _fake_runner({"a-conv": 1.0, "b-tool": 1.0, "c-browser": 1.0})
    res = tasks.run_benchmark(ds, {"type": "scripted"}, evidence_class="live_lane", runner=runner)
    conv_row = next(r for r in res["per_task"] if r["task_id"] == "a-conv")
    assert conv_row["execution_class"] == "executable"
    assert conv_row["overclaim"] is False


def test_invalid_evidence_class_rejected() -> None:
    ds = _dataset()
    with pytest.raises(tasks.TaskError):
        tasks.run_benchmark(ds, {"type": "scripted"}, evidence_class="totally_made_up",
                            runner=_fake_runner({"a-conv": 1.0, "b-tool": 1.0, "c-browser": 1.0}))


def test_empty_split_rejected() -> None:
    ds = _dataset()
    with pytest.raises(tasks.TaskDatasetError):
        tasks.run_benchmark(ds, {"type": "scripted"}, split="nonexistent",
                            runner=_fake_runner({}))


def test_failed_task_scores_void_not_crash() -> None:
    ds = _dataset()

    def _boom(task, agent):  # noqa: ANN001
        raise RuntimeError("engine exploded")

    res = tasks.run_benchmark(ds, {"type": "scripted"}, runner=_boom)
    assert all(r["verdict"] == "void" for r in res["per_task"])
    assert all("error" in r for r in res["per_task"])
    assert res["aggregate"]["passed"] == 0


# --- one REAL end-to-end run through the engine (credential-free) -----------
@pytest.mark.integration
def test_run_benchmark_real_engine_scripted() -> None:
    ds = tasks.compile_task_dataset(
        {"name": "real-mini", "tasks": [_task("a-conv", "conversation", "easy")]}
    )
    agent = {"type": "scripted", "content": "x marks the answer."}
    res = tasks.run_benchmark(ds, agent)  # real engine (runner=None)
    row = res["per_task"][0]
    assert row["verdict"] in ("pass", "fail")  # a terminal score came back
    assert isinstance(row["score"], float)
    assert res["aggregate"]["honesty"]["any_overclaim"] is False
