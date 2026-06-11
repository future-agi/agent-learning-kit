"""LiveKit live lane (3B) — real ``livekit-agents`` AgentSession, opt-in.

Framework imports: NONE at module top (P3-D1). Rung-1 execution happens in
the ``_workers/livekit_worker.py`` subprocess (the only sanctioned top-level
framework import home); this module is importable in the no-extras release
env and the live_lane_boundary gate scans it like any release module.

Rungs (P3-D3): 1 virtual-clock text driver (default, implemented) →
2 loopback real-transport audio → 3 LiveKit Cloud/SIP (``live_credentialed``,
TH-5642 names). Rung 1 is honest about its tier: timing-only voice metrics,
no ``channels`` block, no audio claims (guide §3.5).
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
_RUNG_LABELS = {1: "virtual_clock", 2: "loopback_transport", 3: "cloud_sip"}

# Rung-3 credential names: exactly the names the vendored engine reads
# (engines/livekit.py reads LIVEKIT_API_KEY/LIVEKIT_API_SECRET; the server
# URL arrives via LIVEKIT_URL per the TH-5642 credential map, P3-D5).
RUNG3_REQUIRED_ENV = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")

_DEFAULT_TURNS = (
    {"user": "Hello, can you hear me?"},
    {"user": "Great - please confirm my appointment for tomorrow."},
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


def _voice_timing(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Timing-only voice metrics (the rung-1 honesty tier): per-turn agent
    response latency derived from event timestamps — no audio claims."""

    latencies_ms: list[float] = []
    pending_user_t: float | None = None
    for event in events:
        channel = event.get("channel")
        if channel == "user" and event.get("type") == "message":
            t = event.get("t")
            pending_user_t = float(t) if isinstance(t, (int, float)) else None
        elif channel == "agent" and event.get("type") == "message":
            t = event.get("t")
            if pending_user_t is not None and isinstance(t, (int, float)):
                latencies_ms.append(round((float(t) - pending_user_t) * 1000.0, 3))
            pending_user_t = None
    return {
        "turn_latencies_ms": latencies_ms,
        "mean_turn_latency_ms": (
            round(sum(latencies_ms) / len(latencies_ms), 3) if latencies_ms else None
        ),
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
        "engine": "live_lane_livekit",
        "rung": rung_label,
        "item_count": len(items),
        "items": items[:200],
    }


def run_livekit_lane(
    scenario: Mapping[str, Any],
    *,
    rung: int = 1,                  # P3-D3: 1 virtual-clock | 2 loopback transport | 3 cloud/SIP
    repeats: int = 8,
    stressed: bool = False,         # perturbation sub-lane -> evidence_class "live_stressed"
    perturbations: Optional[Sequence[str]] = None,
    seed: int = 0,
    required_env: Optional[Sequence[str]] = None,
    version_requirement: str | None = None,
    budget_s: float | None = None,
    artifacts_dir: str | Path | None = None,
) -> dict[str, Any]:
    require_lane_enabled("livekit")
    if rung >= 3:
        require_lane_enabled("credentialed")
    if rung not in _RUNG_LABELS:
        raise ValueError(f"rung must be one of {sorted(_RUNG_LABELS)}, got {rung}")
    if rung != 1:
        # Rung-1 virtual-clock is the implemented default (P3-D3); the
        # loopback-transport and Cloud/SIP rungs land with the lane test
        # suites (build units 4-6) — flag discipline above applies already.
        raise NotImplementedError(
            f"livekit lane rung {rung} ({_RUNG_LABELS[rung]}) is not "
            "implemented yet; rung 1 (virtual_clock) is the supported tier"
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
        else Path(tempfile.mkdtemp(prefix="agent-learning-live-livekit-"))
    )
    run_id = uuid.uuid4().hex
    resolved_budget = float(budget_s) if budget_s is not None else lane_budget_s("livekit")
    boot = {
        "type": "boot",
        "lane": "livekit",
        "rung": rung,
        "scenario": {"name": str(scenario.get("name") or "livekit-smoke")},
        "turns": turns,
        "config": {
            "instructions": scenario.get("instructions")
            or "You are a concise, helpful voice agent under test.",
            "responses": scenario.get("responses"),
            "expect": scenario.get("expect"),
        },
    }
    worker = _WORKERS / "livekit_worker.py"

    def _run_once(index: int, transcript: Any) -> dict[str, Any]:
        return run_worker_once(
            worker,
            boot,
            lane="livekit",
            required_env=required,
            cwd=base_dir,
            timeout_s=resolved_budget,
            transcript=transcript,
            version_requirement=version_requirement,
        )

    result = run_repeated(
        _run_once,
        lane="livekit",
        evidence_class=evidence_class,
        repeats=repeats,
        budget_s=budget_s,
        required_env=required,
        artifacts_dir=base_dir,
        run_id=run_id,
        rung=_RUNG_LABELS[rung],
        framework="livekit-agents",
        version_requirement=version_requirement,
    )

    events = primary_transcript_events(result)
    # Normalization rides the existing realtime manifest builder — the run
    # lands in the existing `realtime_trace` state family; the live engine
    # is declared in metadata (guide §3.1).
    from .. import simulate as _simulate

    manifest = _simulate.build_realtime_run_manifest(
        name=f"live-livekit-{run_id[:8]}",
        framework="livekit",
        required_env=required,
        min_turns=1,
        max_turns=max(len(turns), 1),
        metadata={
            "simulation_engine": "live_lane_livekit",
            "live_lane": {"lane": "livekit", "rung": _RUNG_LABELS[rung]},
        },
    )

    payload = lane_run_payload(
        result,
        name=f"live-livekit-{run_id[:8]}",
        scenario=scenario,
        manifest=manifest,
        states={"realtime_trace": _realtime_state(events, rung_label=_RUNG_LABELS[rung])},
        metadata={
            "execution_model": "subprocess",
            "rung": _RUNG_LABELS[rung],
            # rung-1 honesty: timing-only voice metrics, NO channels block
            "voice_timing": _voice_timing(events),
        },
    )
    if applied:
        payload["live_lane"]["perturbations"] = perturbations_stanza(
            applied, seed=seed, paired_clean_run=None
        )
    return payload
