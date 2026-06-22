"""Unified benchmark harness — the benchmarking front door to the simulation layer.

This module is a *composition facade*, not a new engine. A modern agent benchmark
harness decomposes into five layers — Task / Environment / Agent-adapter /
Verifier / Runner — and the kit already ships them (``tasks`` + worlds + the
framework adapters + ``evals``/``rewardhack`` + the live-lane runner + telemetry).
``bench`` adds the *contract glue* on top:

* **Fixed Task<->Verifier coupling** — a suite carries (or references) its own
  oracle. This is the one part that never varies by modality.
* **Pluggable Environment + Agent-adapter** — the modality (text / tool / coding
  / voice / ...) is a dimension, not a fork.
* **Three control modes** —
    - ``push``         : the harness drives the agent (today's ``run_benchmark``);
    - ``artifact_in``  : score a submitted artifact, no live agent (staged 15B);
    - ``pull``         : the agent drives a live environment via reset/step
                         (staged 15D).
* **A unified ``Result``** ``{scalar, components, pass_fail, explanation}`` that
  every modality's verdict projects into.

Honesty primitives are preserved verbatim: every per-task row keeps its
``execution_class`` / ``evidence_class`` and the overclaim tripwire, and the
reward-hack detector still fails a gamed run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import tasks

BENCH_RESULT_KIND = "agent-learning.bench-result.v1"

#: The control modes a bench run can take. ``push`` is live today; the others are
#: staged increments and raise a clear ``NotImplementedError`` until they land.
CONTROL_MODES = ("push", "artifact_in", "pull")

#: World-kind -> coarse modality label. Kept deliberately small; new worlds map
#: here as they become executable (e.g. ``code_exec`` -> ``coding`` in 15E).
_WORLD_KIND_MODALITY = {
    "conversation": "text",
    "tool_api": "tool",
    "code_exec": "coding",
    "browser": "computer_use",
    "computer_use": "computer_use",
    "voice_telephony": "voice",
}


class BenchError(ValueError):
    """Raised for malformed bench suites or invalid harness arguments."""


def modality_for_world_kind(world_kind: str) -> str:
    """Map a world kind to its coarse modality label (``unknown`` if unmapped)."""

    return _WORLD_KIND_MODALITY.get(str(world_kind), "unknown")


def load_bench_suite(suite: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    """Resolve a bench suite to a compiled TaskDataset.

    Accepts a path (loaded + compiled via :func:`tasks.load_task_dataset`) or an
    already-compiled dataset mapping (returned as a shallow copy). A raw,
    uncompiled mapping is compiled via :func:`tasks.compile_task_dataset` so the
    Goodhart guards are enforced before any run.
    """

    if isinstance(suite, (str, Path)):
        return tasks.load_task_dataset(suite)
    if isinstance(suite, Mapping):
        # A compiled dataset is idempotent under compile; compiling here keeps the
        # guard checks on the Task<->Verifier coupling no matter how it arrived.
        return tasks.compile_task_dataset(suite)
    raise BenchError(
        f"suite must be a path or a dataset mapping, got {type(suite).__name__!r}"
    )


def _project_result(row: Mapping[str, Any]) -> dict[str, Any]:
    """Project a ``tasks.run_benchmark`` per-task row into the unified Result.

    The superset shape is synthesised from the strongest external references
    (a metric->number map plus a value+rationale verdict): scalar score,
    per-metric components, pass/fail booleans, and a short explanation.
    """

    metric_averages = row.get("metric_averages") or {}
    components = {
        str(k): float(v)
        for k, v in metric_averages.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    scoring = row.get("scoring") or {}
    return {
        "scalar": row.get("score"),
        "components": components,
        "pass_fail": {"verdict": row.get("verdict") == "pass"},
        "explanation": scoring.get("basis"),
    }


def _bench_row(row: Mapping[str, Any], *, control_mode: str) -> dict[str, Any]:
    world_kind = str(row.get("world_kind") or "")
    out: dict[str, Any] = {
        "task_id": row.get("task_id"),
        "modality": modality_for_world_kind(world_kind),
        "world_kind": world_kind,
        "control_mode": control_mode,
        "result": _project_result(row),
        "verdict": row.get("verdict"),
        "execution_class": row.get("execution_class"),
        "evidence_class": row.get("evidence_class"),
        "overclaim": bool(row.get("overclaim", False)),
    }
    if "rewardhack" in row:
        out["rewardhack"] = row["rewardhack"]
    if "error" in row:
        out["error"] = row["error"]
    return out


def _to_bench_result(result: Mapping[str, Any], *, control_mode: str) -> dict[str, Any]:
    """Re-badge an engine benchmark result under the unified bench contract."""

    per_task = [
        _bench_row(r, control_mode=control_mode)
        for r in result.get("per_task", [])
    ]
    out: dict[str, Any] = {
        "kind": BENCH_RESULT_KIND,
        "control_mode": control_mode,
        "dataset_name": result.get("dataset_name"),
        "dataset_version": result.get("dataset_version"),
        "modalities": sorted({r["modality"] for r in per_task}),
        "per_task": per_task,
        "aggregate": result.get("aggregate"),
    }
    if "telemetry" in result:
        out["telemetry"] = result["telemetry"]
    return out


def run_bench(
    suite: Mapping[str, Any] | str | Path,
    agent: Mapping[str, Any],
    *,
    control_mode: str = "push",
    split: str | None = None,
    max_tasks: int | None = None,
    seed: int = 42,
    evidence_class: str = "captured_fixture",
    detect_reward_hacks: bool = True,
    runner: Any = None,
    emit_telemetry: bool = True,
    project_name: str | None = None,
) -> dict[str, Any]:
    """Run ``agent`` across a bench ``suite`` and return a unified bench result.

    ``control_mode``:
      * ``push`` (default) delegates to :func:`tasks.run_benchmark` — the harness
        drives the agent through a world. Live today for text / tool worlds.
      * ``artifact_in`` scores a submitted artifact with no live agent (15B).
      * ``pull`` runs an agent-driven reset/step loop over a live env (15D).

    The returned payload is ``agent-learning.bench-result.v1``: per-task rows each
    carrying the unified ``result`` plus the preserved honesty fields, an
    ``aggregate`` block, and the set of ``modalities`` exercised.
    """

    if control_mode not in CONTROL_MODES:
        raise BenchError(
            f"unknown control_mode {control_mode!r}; expected one of {CONTROL_MODES}"
        )
    compiled = load_bench_suite(suite)

    if control_mode == "push":
        result = tasks.run_benchmark(
            compiled,
            agent,
            split=split,
            max_tasks=max_tasks,
            seed=seed,
            evidence_class=evidence_class,
            detect_reward_hacks=detect_reward_hacks,
            runner=runner,
            emit_telemetry=emit_telemetry,
            project_name=project_name,
        )
        return _to_bench_result(result, control_mode="push")

    if control_mode == "artifact_in":
        raise NotImplementedError(
            "control_mode='artifact_in' (submit-and-score, no live agent) lands in "
            "bench step 15B"
        )
    # control_mode == "pull"
    raise NotImplementedError(
        "control_mode='pull' (agent-driven reset/step over a live environment) "
        "lands in bench step 15D"
    )


def run_bench_file(
    suite_path: str | Path,
    agent: Mapping[str, Any],
    *,
    output_path: str | Path | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Convenience: :func:`run_bench` from a suite file, optionally writing JSON."""

    payload = run_bench(Path(suite_path), agent, **kwargs)
    if output_path is not None:
        out = Path(output_path).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
    return payload
