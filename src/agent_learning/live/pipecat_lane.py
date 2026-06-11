"""Pipecat live lane (3C) — real Pipecat ``Pipeline`` with frame injection.

Framework imports: NONE at module top (P3-D1); execution happens in
``_workers/pipecat_worker.py``. Rung 1 (default, implemented) injects
``TranscriptionFrame``s — bypassing STT/TTS, Pipecat's own documented eval
technique — and collects output frames + TTFB/processing timing. Same
dual-channel/perturbation/variance contract as 3B (PRD §4.3); rung-1
honesty: timing-only voice metrics, no ``channels`` block.
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from ._contract import lane_budget_s, require_lane_enabled
from ._perturb import apply_text_perturbations, perturbations_stanza
from ._runner import run_worker_once
from ._stats import lane_run_payload, primary_transcript_events, run_repeated

_WORKERS = Path(__file__).resolve().parent / "_workers"
_RUNG_LABELS = {1: "frame_injection", 2: "loopback_transport", 3: "credentialed_providers"}

_DEFAULT_TURNS = (
    {"user": "Hello there."},
    {"user": "What can you help me with today?"},
)


def _scenario_turns(scenario: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = scenario.get("turns") or scenario.get("user_messages")
    if not raw:
        return [dict(turn) for turn in _DEFAULT_TURNS]
    turns: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            turns.append({"user": item})
        elif isinstance(item, Mapping):
            turns.append(dict(item))
    return turns or [dict(turn) for turn in _DEFAULT_TURNS]


def _frame_timing(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """TTFB/processing timing evidence reported by the worker (PRD §4.3)."""

    ttfb: list[float] = []
    processing: list[float] = []
    for event in events:
        if event.get("channel") == "lane" and event.get("type") == "timing":
            payload = event.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            if isinstance(payload.get("ttfb_ms"), (int, float)):
                ttfb.append(float(payload["ttfb_ms"]))
            if isinstance(payload.get("processing_ms"), (int, float)):
                processing.append(float(payload["processing_ms"]))
    return {
        "ttfb_ms": ttfb,
        "mean_ttfb_ms": round(sum(ttfb) / len(ttfb), 3) if ttfb else None,
        "processing_ms": processing,
    }


def _realtime_state(
    events: Sequence[Mapping[str, Any]], *, rung_label: str
) -> dict[str, Any]:
    items = []
    for index, event in enumerate(events, start=1):
        if event.get("channel") in ("user", "agent", "tool"):
            payload = event.get("payload")
            payload = payload if isinstance(payload, Mapping) else {}
            items.append(
                {
                    "index": index,
                    "channel": event.get("channel"),
                    "item_type": event.get("type"),
                    "text": payload.get("text"),
                }
            )
    return {
        "engine": "live_lane_pipecat",
        "rung": rung_label,
        "item_count": len(items),
        "items": items[:200],
    }


def run_pipecat_lane(
    pipeline_factory_path: str | None,
    scenario: Mapping[str, Any],
    *,
    rung: int = 1,
    repeats: int = 8,
    stressed: bool = False,
    perturbations: Optional[Sequence[str]] = None,
    seed: int = 0,
    required_env: Optional[Sequence[str]] = None,
    version_requirement: str | None = None,
    budget_s: float | None = None,
    artifacts_dir: str | Path | None = None,
) -> dict[str, Any]:
    require_lane_enabled("pipecat")
    if rung >= 3:
        require_lane_enabled("credentialed")
    if rung not in _RUNG_LABELS:
        raise ValueError(f"rung must be one of {sorted(_RUNG_LABELS)}, got {rung}")
    if rung != 1:
        raise NotImplementedError(
            f"pipecat lane rung {rung} ({_RUNG_LABELS[rung]}) is not "
            "implemented yet; rung 1 (frame_injection) is the supported tier"
        )

    required = tuple(required_env) if required_env is not None else ()
    operators = list(perturbations or (["asr_error"] if stressed else []))
    evidence_class = "live_stressed" if operators else "live_lane"
    turns = _scenario_turns(scenario)
    applied: list[dict[str, Any]] = []
    if operators:
        turns, applied = apply_text_perturbations(turns, operators, seed=seed)

    base_dir = (
        Path(artifacts_dir)
        if artifacts_dir is not None
        else Path(tempfile.mkdtemp(prefix="agent-learning-live-pipecat-"))
    )
    run_id = uuid.uuid4().hex
    resolved_budget = float(budget_s) if budget_s is not None else lane_budget_s("pipecat")
    boot = {
        "type": "boot",
        "lane": "pipecat",
        "rung": rung,
        "scenario": {"name": str(scenario.get("name") or "pipecat-smoke")},
        "turns": turns,
        "config": {
            "pipeline_factory": pipeline_factory_path,
            "responses": scenario.get("responses"),
            "expect": scenario.get("expect"),
        },
    }
    worker = _WORKERS / "pipecat_worker.py"

    def _run_once(index: int, transcript: Any) -> dict[str, Any]:
        return run_worker_once(
            worker,
            boot,
            lane="pipecat",
            required_env=required,
            cwd=base_dir,
            timeout_s=resolved_budget,
            transcript=transcript,
            version_requirement=version_requirement,
        )

    result = run_repeated(
        _run_once,
        lane="pipecat",
        evidence_class=evidence_class,
        repeats=repeats,
        budget_s=budget_s,
        required_env=required,
        artifacts_dir=base_dir,
        run_id=run_id,
        rung=_RUNG_LABELS[rung],
        framework="pipecat-ai",
        version_requirement=version_requirement,
    )

    events = primary_transcript_events(result)
    from .. import simulate as _simulate

    manifest = _simulate.build_realtime_run_manifest(
        name=f"live-pipecat-{run_id[:8]}",
        framework="pipecat",
        required_env=required,
        min_turns=1,
        max_turns=max(len(turns), 1),
        metadata={
            "simulation_engine": "live_lane_pipecat",
            "live_lane": {"lane": "pipecat", "rung": _RUNG_LABELS[rung]},
        },
    )

    payload = lane_run_payload(
        result,
        name=f"live-pipecat-{run_id[:8]}",
        scenario=scenario,
        manifest=manifest,
        states={"realtime_trace": _realtime_state(events, rung_label=_RUNG_LABELS[rung])},
        metadata={
            "execution_model": "subprocess",
            "rung": _RUNG_LABELS[rung],
            # rung-1 honesty: timing-only voice metrics, NO channels block
            "voice_timing": _frame_timing(events),
        },
    )
    if applied:
        payload["live_lane"]["perturbations"] = perturbations_stanza(
            applied, seed=seed, paired_clean_run=None
        )
    return payload
