from __future__ import annotations

import asyncio

from fi.simulate.hosted.child_entrypoint import _build_voice_spec
from fi.simulate.hosted.job import RunnerMode, StartRunnerJob, VoiceRunConfig
from fi.simulate.registry import environment_registry
from fi.simulate.runtime import RunStatus
from fi.simulate.runtime.runner import SimulationRunner
from fi.simulate.simulation.models import (
    TestCaseResult as _TestCaseResult,
    TestReport as _TestReport,
)


def _voice_job(mode: RunnerMode = RunnerMode.VOICE_WEBRTC) -> StartRunnerJob:
    return StartRunnerJob(
        job_id="job_voice_1",
        mode=mode,
        voice=VoiceRunConfig(
            agent_definition={
                "name": "probe-agent",
                "system_prompt": "You are a support agent.",
                "agent_name": "target-agent",
                "transport": {"kind": "webrtc"},
            },
            scenario={
                "name": "voice-probe",
                "dataset": [
                    {"persona": {"name": "Sam"}, "situation": "s", "outcome": "o"}
                ],
            },
            livekit_runtime={
                "url": "wss://livekit.example.com",
                "room_name": "sim-room",
                "api_key_env": "LIVEKIT_API_KEY",
                "api_secret_env": "LIVEKIT_API_SECRET",
            },
            params={"max_seconds": 120, "record_audio": False},
        ),
    )


def _legacy(persona, *, failed: bool = False) -> _TestReport:
    if failed:
        return _TestReport(
            results=[
                _TestCaseResult(
                    persona=persona,
                    transcript="x",
                    messages=[],
                    metadata={
                        "status": "failed",
                        "failure": {
                            "stage": "running",
                            "code": "boom",
                            "message": "boom",
                            "retryable": False,
                        },
                    },
                )
            ]
        )
    return _TestReport(
        results=[
            _TestCaseResult(
                persona=persona,
                transcript="Sim: hi\nAgent: done",
                messages=[{"role": "assistant", "content": "done"}],
                metadata={"engine": "livekit"},
            )
        ]
    )


def test_voice_environment_registered() -> None:
    assert environment_registry.has("voice")


def test_build_voice_spec_is_secret_free_and_shaped() -> None:
    spec = _build_voice_spec(_voice_job())
    assert spec.environment.adapter == "voice"
    assert spec.target.adapter == "webrtc"
    assert spec.environment.config["agent_definition"]["name"] == "probe-agent"
    # outer runner deadline clears the call budget
    assert spec.execution.timeout.run_seconds >= 180
    # SimulationSpec validation (incl. _reject_resolved_secrets) passed
    assert spec.spec_hash


def test_sip_transport_selects_sip_target_adapter() -> None:
    job = _voice_job(mode=RunnerMode.VOICE_SIP)
    job.voice.agent_definition["transport"] = {
        "kind": "sip_outbound",
        "sip_trunk_id": "trunk_1",
        "sip_call_to": "+15551230000",
        "sip_number": "+15559990000",
    }
    spec = _build_voice_spec(job)
    assert spec.target.adapter == "sip_outbound"


def test_voice_runs_through_simulation_runner(monkeypatch) -> None:
    spec = _build_voice_spec(_voice_job())
    persona = spec.scenario.dataset[0]

    async def fake_run_voice_simulation(**kwargs):
        assert kwargs["agent_definition"].name == "probe-agent"
        assert kwargs["scenario"].name == "voice-probe"
        assert kwargs["simulation_run_id"] == spec.run_id
        assert kwargs["max_seconds"] == 120
        return _legacy(persona)

    monkeypatch.setattr(
        "fi.simulate.voice.run_voice_simulation", fake_run_voice_simulation
    )
    report = asyncio.run(SimulationRunner().run(spec))
    assert report.status == RunStatus.COMPLETED
    assert report.test_cases[0].result.transcript


def _agent_def(**overrides):
    from fi.simulate.agent.definition import AgentDefinition

    base = {"name": "a", "system_prompt": "p"}
    base.update(overrides)
    return AgentDefinition.model_validate(base)


def test_required_env_parity_across_transport_kinds() -> None:
    """Profiles reproduce the old voice._voice_required_env branches exactly
    (ordered, deduped, empty-strings dropped)."""
    from fi.simulate.voice import _voice_required_env

    base = ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]

    webrtc = _agent_def(agent_name="t", transport={"kind": "webrtc"})
    assert _voice_required_env(webrtc, None, []) == base

    vapi_web = _agent_def(transport={"kind": "vapi_websocket"})
    assert _voice_required_env(vapi_web, None, []) == [
        *base,
        "VAPI_API_KEY",
        "VAPI_ASSISTANT_ID",
    ]

    retell_web = _agent_def(transport={"kind": "retell_webcall"})
    assert _voice_required_env(retell_web, None, []) == [
        *base,
        "RETELL_API_KEY",
        "RETELL_AGENT_ID",
    ]

    sip_in = _agent_def(
        agent_name="t",
        transport={"kind": "sip_inbound"},
    )
    assert _voice_required_env(sip_in, None, []) == [*base, "LIVEKIT_INBOUND_TRUNK_ID"]

    sip_in_vapi = _agent_def(
        transport={"kind": "sip_inbound", "inbound_call_originator": "vapi"},
        provider_evidence={
            "provider": "vapi",
            "call_id_source": "originator_response",
        },
    )
    assert _voice_required_env(sip_in_vapi, None, []) == [
        *base,
        "LIVEKIT_INBOUND_TRUNK_ID",
        "VAPI_API_KEY",
        "VAPI_ASSISTANT_ID",
        "VAPI_PHONE_NUMBER_ID",
        "LIVEKIT_INBOUND_DID",
    ]


def test_profile_flags_for_target_adapters() -> None:
    from fi.simulate.endpoints.profiles import get_profile

    assert get_profile("sip_outbound").is_sip
    assert get_profile("sip_outbound").places_outbound_call
    assert get_profile("vapi_websocket").uses_web_audio_bridge
    assert get_profile("vapi_websocket").bridge_provider == "vapi"
    assert get_profile("vapi_websocket").evidence_provider == "vapi"
    assert get_profile("retell_webcall").bridge_provider == "retell"
    assert get_profile("webrtc").is_sip is False
    assert get_profile("webrtc").uses_web_audio_bridge is False


def test_voice_goal_machine_noop_without_declared_goal() -> None:
    from fi.simulate.environments.voice import VoiceEnvironmentPlugin
    from fi.simulate.simulation.models import Persona, Scenario

    scenario = Scenario(
        name="no-goal",
        dataset=[Persona(persona={"name": "S"}, situation="s", outcome="o")],
    )
    report = _legacy(scenario.dataset[0])
    VoiceEnvironmentPlugin._attach_goal_machine(scenario, report)
    assert "goal_machine" not in report.results[0].metadata


def test_voice_goal_machine_attaches_with_declared_goal() -> None:
    from fi.simulate.environments.voice import VoiceEnvironmentPlugin
    from fi.simulate.simulation.models import Persona, Scenario, ScenarioGoal

    scenario = Scenario(
        name="with-goal",
        dataset=[Persona(persona={"name": "S"}, situation="s", outcome="o")],
        goal=ScenarioGoal(states=["greeted", "resolved"], success_state="resolved"),
    )
    report = _legacy(scenario.dataset[0])
    VoiceEnvironmentPlugin._attach_goal_machine(scenario, report)
    gm = report.results[0].metadata.get("goal_machine")
    assert gm is not None
    assert gm["stop_reason"] is None
    assert "states_reached" in gm and "checks" in gm


def test_voice_all_cases_failed_downgrades_to_failed(monkeypatch) -> None:
    spec = _build_voice_spec(_voice_job())
    persona = spec.scenario.dataset[0]

    async def fake(**kwargs):
        return _legacy(persona, failed=True)

    monkeypatch.setattr("fi.simulate.voice.run_voice_simulation", fake)
    report = asyncio.run(SimulationRunner().run(spec))
    assert report.status == RunStatus.FAILED
    assert report.failure is not None
