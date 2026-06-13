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
from ._stats import (
    derive_channel_evidence,
    lane_run_payload,
    primary_transcript_events,
    run_repeated,
)

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


def _rung2_loopback_channels(
    turns: Sequence[Mapping[str, Any]],
    *,
    loopback: Optional[Mapping[str, Any]],
    codec_profile: str,
    seed: int,
) -> tuple[dict[str, Any], str]:
    """Phase 9A unit 2 — the rung-2 loopback dispatch (§2.1 / §2.5), byte-parallel
    to the LiveKit lane. The rung-2 ``loopback_transport`` label is byte-identical
    across both lanes (the seam 9A grows). Produces the two PCM streams via the
    deterministic ``_loopback`` round-trip, applies the default-ON codec round-trip
    (9A-A11) unless ``codec_profile == "none"``, feeds the ALREADY-BUILT
    ``derive_channel_evidence`` (REUSED), and returns ``channels`` + the
    ``fidelity_tier`` marker."""

    from agent_learning import live  # sanctioned facade idiom (cli.py)

    cfg = dict(loopback or {})
    tick_ms = float(cfg.get("tick_ms", live._loopback.DEFAULT_TICK_MS))
    sample_rate = int(cfg.get("sample_rate", live._loopback.DEFAULT_SAMPLE_RATE))
    loop_seed = int(cfg.get("seed", seed))
    profile = str(cfg.get("codec_profile", codec_profile))

    loop = live._loopback.run_loopback_roundtrip(
        list(turns),
        user_wav=cfg.get("user_wav"),
        agent_wav=cfg.get("agent_wav"),
        tick_ms=tick_ms,
        sample_rate=sample_rate,
        seed=loop_seed,
    )
    user_pcm, agent_pcm = loop["user_pcm"], loop["agent_pcm"]

    codec_record: dict[str, Any] | None = None
    phone_survival: dict[str, Any] | None = None
    if profile != "none":
        user_pcm, agent_pcm, codec_record = live._codec.apply_codec_profile(
            user_pcm, agent_pcm, profile=profile, seed=loop_seed, sample_rate=sample_rate
        )
        codec, packet_loss = live._codec._PROFILE_BUNDLE[profile]
        phone_survival = live._codec.score_codec_survival(
            loop["user_pcm"],
            loop["agent_pcm"],
            codec=codec,
            packet_loss=packet_loss,
            seed=loop_seed,
            sample_rate=sample_rate,
        )

    derived = derive_channel_evidence(
        user_pcm, agent_pcm, sample_rate=(8000 if profile != "none" else sample_rate)
    )
    channels: dict[str, Any] = {
        "derived": derived,
        "source": "derive_channel_evidence",
        "rung": _RUNG_LABELS[2],
        "fidelity_tier": "deterministic_loopback",
        "seed": loop_seed,
        "loopback_provenance": loop["provenance"],
    }
    if codec_record is not None:
        channels["codec_round_trip"] = codec_record
    if phone_survival is not None:
        channels["phone_survival"] = phone_survival
    return channels, "deterministic_loopback"


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
    # Phase 9A (BBG A2): additive optional loopback config consumed ONLY on the
    # rung==2 branch; rung-1/rung-3 callers are unaffected.
    loopback: Optional[Mapping[str, Any]] = None,
    codec_profile: str = "g711_ulaw_8k_ge",
) -> dict[str, Any]:
    require_lane_enabled("pipecat")
    if rung >= 3:
        require_lane_enabled("credentialed")
    if rung not in _RUNG_LABELS:
        raise ValueError(f"rung must be one of {sorted(_RUNG_LABELS)}, got {rung}")

    required = tuple(required_env) if required_env is not None else ()
    operators = list(perturbations or (["asr_error"] if stressed else []))
    turns = _scenario_turns(scenario)
    applied: list[dict[str, Any]] = []
    if operators:
        turns, applied = apply_text_perturbations(turns, operators, seed=seed)

    # Phase 9A unit 2: the rung wall narrows — rung-2 dispatches into the
    # deterministic loopback (§2.1); rung-3 still raises (the owner live-proof,
    # unit 7). rung-1 is completely untouched (timing-only, NO channels block).
    channels: dict[str, Any] | None = None
    fidelity_tier: str | None = None
    if rung == 2:
        channels, fidelity_tier = _rung2_loopback_channels(
            turns, loopback=loopback, codec_profile=codec_profile, seed=seed
        )
        # §2.5 binding correction: a deterministic in-process loopback is
        # NEVER live_lane.
        evidence_class = "live_stressed"
    elif rung != 1:
        # rung == 3: unchanged keyed path; still requires the credentialed flag
        # + RUNG3_REQUIRED_ENV; rung-3 lands as the owner live-proof (unit 7).
        raise NotImplementedError(
            f"pipecat lane rung {rung} ({_RUNG_LABELS[rung]}) is not "
            "implemented yet; rung 1 (frame_injection) and rung 2 "
            "(loopback_transport) are the supported tiers — rung 3 "
            "(credentialed_providers) is the owner-keyed live-proof lane"
        )
    else:
        evidence_class = "live_stressed" if operators else "live_lane"

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
    if channels is not None:
        # rung-2: attach the dual-channel evidence + the fidelity marker (§2.5 /
        # 9A-A10). fidelity_tier is a MARKER FIELD, not a new evidence class.
        payload["channels"] = channels
        if isinstance(payload.get("live_lane"), dict):
            payload["live_lane"]["fidelity_tier"] = fidelity_tier
        payload["fidelity_tier"] = fidelity_tier
    return payload
