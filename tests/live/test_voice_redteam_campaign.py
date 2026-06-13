"""Voice escalation campaign runner tests (Phase 12, units 4/4b/4c).

Machinery tier (no extras, no flags) runs in the DEFAULT suite: arc-turn
compilation determinism + dial conditioning, timing-fidelity math, the rung
wall + flag refusal, authorization ordering, simulator hardening. Lane tier
(env-gated, auto-skip bare) runs one full clean+stressed campaign over the
livekit rung-1 lane.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = PROJECT_ROOT / "examples" / "voice_redteam"


def _scenario() -> dict:
    s = json.loads((FIXTURES / "scenarios/adversarial.json").read_text())
    return s


# --- machinery tier (no flags) ----------------------------------------------


def test_compile_arc_turns_determinism_and_dial_conditioning():
    from agent_learning.live import voice_redteam

    scenario = _scenario()
    turns_a = voice_redteam.compile_arc_turns(scenario)
    turns_b = voice_redteam.compile_arc_turns(scenario)
    assert turns_a == turns_b  # deterministic
    assert len(turns_a) == len(scenario["escalation"]["steps"])
    # rajas >= 0.7 (the attacker fixture) -> interruption-marked turns
    assert all(t["user"].startswith("--") for t in turns_a)
    # escalation_schedule overrides step pressure when present
    schedule = scenario["dataset"][0]["behavior_policy"]["escalation_schedule"]
    assert turns_a[0]["pressure"] == pytest.approx(schedule[0])


def test_timing_fidelity_is_a_labeled_rung1_proxy():
    from agent_learning.live import voice_redteam

    scenario = _scenario()
    turns = voice_redteam.compile_arc_turns(scenario)
    persona = scenario["dataset"][0]
    tf = voice_redteam.timing_fidelity(turns, persona, turns)
    assert tf["proxy"] == "timing_only"
    assert tf["rung"] == 1
    assert isinstance(tf["in_character_timing"], bool)


def test_simulator_hardening_voids_on_persona_jailbreak():
    from agent_learning.live import voice_redteam

    held = voice_redteam.simulator_hardening(
        [{"counter_pressure": True}, {"text": "ok"}]
    )
    assert held["simulator_held"] is True
    assert held["counter_pressure_probes"] == 1
    broken = voice_redteam.simulator_hardening(
        [{"counter_pressure": True, "persona_jailbroken": True}]
    )
    assert broken["simulator_held"] is False


def test_campaign_refuses_without_lane_flag(monkeypatch):
    from agent_learning.live import _contract, voice_redteam

    monkeypatch.delenv("AGENT_LEARNING_LIVE_LIVEKIT", raising=False)
    scenario = _scenario()
    with pytest.raises(_contract.LaneDisabledError):
        voice_redteam.run_voice_escalation_campaign(scenario, lane="livekit")


def test_campaign_rung_wall_and_authorization_ordering(monkeypatch):
    from agent_learning.live import voice_redteam

    scenario = _scenario()
    # acoustic operator raises at text rung (before any lane dispatch)
    with pytest.raises(ValueError):
        voice_redteam.run_voice_escalation_campaign(
            scenario, lane="livekit", operators=["noise"]
        )
    # a non-local target without the stanza refuses with the finding FIRST,
    # before LaneDisabledError could fire
    monkeypatch.delenv("AGENT_LEARNING_LIVE_LIVEKIT", raising=False)
    with pytest.raises(voice_redteam.VoiceAuthorizationError) as exc:
        voice_redteam.run_voice_escalation_campaign(
            scenario,
            lane="livekit",
            target={"kind": "live_lane", "lane": "livekit"},
            provider="custom",
        )
    assert exc.value.finding["type"] == "voice_target_authorization_missing"


# --- lane tier (env-gated, auto-skip bare) ----------------------------------

pytestmark = []


@pytest.mark.live_lane
@pytest.mark.live_livekit
def test_full_clean_stressed_campaign_over_livekit_rung1(tmp_path):
    from agent_learning.live import voice_redteam

    scenario = _scenario()
    scenario["responses"] = ["sure", "confirmed", "done", "ok"]
    payload = voice_redteam.run_voice_escalation_campaign(
        scenario,
        lane="livekit",
        operators=["homophone", "near_dup"],
        seed=7,
        repeats=4,
        artifacts_dir=tmp_path,
    )
    vr = payload["voice_redteam"]
    assert payload["attack_rung"] == "transcript_level"
    assert vr["attack_rung"] == "transcript_level"
    assert vr["phone_survival"] == {"status": "untested", "tier": "research_pinned"}
    assert payload["evidence_class"] == "live_stressed"
    # the stressed run's paired_clean_run is filled with the clean run id
    perturbations = payload["live_lane"]["perturbations"]
    assert perturbations["paired_clean_run"] == vr["paired"]["clean_run"]
    assert perturbations["paired_clean_run"] is not None
