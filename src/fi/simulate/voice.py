from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fi.alk.studio import GeneratedScenario

from fi.simulate.agent.definition import AgentDefinition, SimulatorAgentDefinition
from fi.simulate.simulation.models import Scenario, TestReport
from fi.simulate.simulation.runner import TestRunner

_RUN_VERSION = "agent-learning.run.v1"


async def run_voice_simulation(
    *,
    agent_definition: AgentDefinition,
    scenario: Scenario | None = None,
    simulator: SimulatorAgentDefinition | None = None,
    topic: str | None = None,
    num_scenarios: int = 1,
    simulation_run_id: str | None = None,
    record_audio: bool = False,
    recording_root: str | Path = "recordings",
    recorder_sample_rate: int = 8000,
    recorder_join_delay: float = 0.2,
    min_turn_messages: int = 8,
    max_seconds: float = 45.0,
    connect_timeout: float = 15.0,
    readiness_timeout: float = 30.0,
    cleanup_timeout: float = 30.0,
    conversation_direction: str = "simulator_first",
) -> TestReport:
    """Run a LiveKit voice simulation directly from typed SDK objects."""

    if scenario is not None and topic is not None:
        raise ValueError("scenario and topic are mutually exclusive")
    if scenario is None and not topic:
        raise ValueError("provide scenario or topic for scenario generation")
    return await TestRunner().run_test(
        agent_definition=agent_definition,
        scenario=scenario,
        simulator=simulator,
        topic=topic,
        num_scenarios=num_scenarios,
        simulation_run_id=simulation_run_id,
        record_audio=record_audio,
        recording_root=recording_root,
        recorder_sample_rate=recorder_sample_rate,
        recorder_join_delay=recorder_join_delay,
        min_turn_messages=min_turn_messages,
        max_seconds=max_seconds,
        connect_timeout=connect_timeout,
        readiness_timeout=readiness_timeout,
        cleanup_timeout=cleanup_timeout,
        conversation_direction=conversation_direction,
    )


async def generate_platform_voice_scenario(
    *,
    agent_definition: AgentDefinition,
    name: str,
    description: str | None = None,
    custom_instruction: str | None = None,
    no_of_rows: int = 10,
    poll_interval_seconds: float = 2.0,
    timeout_seconds: float = 900.0,
    config: Any | None = None,
) -> "GeneratedScenario":
    """Create a platform Agent Definition and generate its typed Scenario."""

    from fi.alk import studio

    request = studio.PlatformScenarioRequest(
        name=name,
        agent_definition=agent_definition,
        description=description,
        custom_instruction=custom_instruction,
        no_of_rows=no_of_rows,
        poll_interval_seconds=poll_interval_seconds,
        timeout_seconds=timeout_seconds,
    )
    return await asyncio.to_thread(studio.generate_scenario, request, config=config)


def build_voice_run_manifest(
    *,
    agent_definition: AgentDefinition,
    scenario: Scenario,
    simulator: SimulatorAgentDefinition | None = None,
    name: str | None = None,
    required_env: Sequence[str] = (),
    simulation_run_id: str | None = None,
    record_audio: bool = False,
    recording_root: str | Path = "recordings",
    recorder_sample_rate: int = 8000,
    recorder_join_delay: float = 0.2,
    min_turn_messages: int = 8,
    max_seconds: float = 45.0,
    connect_timeout: float = 15.0,
    readiness_timeout: float = 30.0,
    cleanup_timeout: float = 30.0,
    conversation_direction: str = "simulator_first",
    evaluation_enabled: bool = True,
    evaluation_config: Mapping[str, Any] | None = None,
    threshold: float = 0.7,
) -> dict[str, Any]:
    """Build the portable manifest for a typed LiveKit voice simulation."""

    agent = AgentDefinition.model_validate(agent_definition)
    typed_scenario = Scenario.model_validate(scenario)
    typed_simulator = (
        SimulatorAgentDefinition.model_validate(simulator)
        if simulator is not None
        else None
    )
    simulation: dict[str, Any] = {
        "engine": "livekit",
        "modality": "voice",
        "record_audio": record_audio,
        "recording_root": str(recording_root),
        "recorder_sample_rate": recorder_sample_rate,
        "recorder_join_delay": recorder_join_delay,
        "min_turn_messages": min_turn_messages,
        "max_seconds": max_seconds,
        "connect_timeout": connect_timeout,
        "readiness_timeout": readiness_timeout,
        "cleanup_timeout": cleanup_timeout,
        "conversation_direction": conversation_direction,
    }
    if simulation_run_id:
        simulation["run_id"] = simulation_run_id
    manifest: dict[str, Any] = {
        "version": _RUN_VERSION,
        "name": name or f"{agent.name}-voice-simulation",
        "required_env": _voice_required_env(agent, required_env),
        "agent_definition": agent.model_dump(mode="json", exclude_none=True),
        "scenario": typed_scenario.model_dump(mode="json", exclude_none=True),
        "simulation": simulation,
        "evaluation": {
            "enabled": evaluation_enabled,
            "agent_report": {
                "config": dict(evaluation_config or {}),
                "threshold": threshold,
            },
        },
    }
    if typed_simulator is not None:
        manifest["simulator"] = typed_simulator.model_dump(
            mode="json", exclude_none=True
        )
    return manifest


def _voice_required_env(
    agent_definition: AgentDefinition,
    required_env: Sequence[str],
) -> list[str]:
    names = ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", *required_env]
    transport = agent_definition.transport
    if transport is not None:
        if transport.kind == "vapi_websocket":
            names.extend(("VAPI_API_KEY", "VAPI_ASSISTANT_ID"))
        elif transport.kind == "retell_webcall":
            names.extend(("RETELL_API_KEY", "RETELL_AGENT_ID"))
        elif transport.kind == "sip_inbound":
            names.append("LIVEKIT_INBOUND_TRUNK_ID")
            if transport.inbound_call_originator == "vapi":
                names.extend(
                    (
                        "VAPI_API_KEY",
                        "VAPI_ASSISTANT_ID",
                        "VAPI_PHONE_NUMBER_ID",
                        "LIVEKIT_INBOUND_DID",
                    )
                )
    return list(dict.fromkeys(str(name) for name in names if str(name).strip()))


__all__ = [
    "build_voice_run_manifest",
    "generate_platform_voice_scenario",
    "run_voice_simulation",
]
