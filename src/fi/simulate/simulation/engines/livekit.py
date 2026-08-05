from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from pathlib import Path
from typing import AsyncIterable
from uuid import uuid4

try:
    from livekit import api, rtc
    from livekit.agents import Agent, AgentSession, function_tool, metrics
    from livekit.agents.voice import ModelSettings
    from livekit.agents.voice.io import TimedString
    from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions
    from livekit.api import AccessToken, VideoGrants
    from livekit.plugins import silero
    from livekit.protocol.sip import (
        CreateSIPDispatchRuleRequest,
        DeleteSIPDispatchRuleRequest,
        ListSIPDispatchRuleRequest,
        SIPDispatchRule,
        SIPDispatchRuleDirect,
    )
except ImportError as exc:
    raise ImportError(
        "LiveKit mode requires the 'livekit' optional dependency"
    ) from exc

from datetime import datetime, timezone

from fi.simulate._logging import redacted_exc_info
from fi.simulate.agent.definition import (
    AgentDefinition,
    LiveKitSimulatorRuntime,
    LLMConfig,
    ProviderEvidenceConfig,
    RetellTargetConfig,
    SimulatorAgentDefinition,
    STTConfig,
    TelephonyTransport,
    TTSConfig,
    VapiTargetConfig,
    VoiceProviderTarget,
)
from fi.simulate.artifacts.manifest import ArtifactManifestEntry
from fi.simulate.evidence.base import EvidenceSourceSummary
from fi.simulate.evidence.providers import (
    EvidenceContext,
    ProviderConfigError,
    ProviderFetchResult,
    RetellEvidenceSource,
    VapiEvidenceSource,
)
from fi.simulate.endpoints.vapi import VapiCallOriginator
from fi.simulate.simulation.bridge import (
    LiveKitAudioBridge,
    RetellWebCallConnector,
    VapiWebSocketConnector,
)
from fi.simulate.simulation.livekit_models import LiveKitModels, build_livekit_models
from fi.simulate.recording.room_recorder import RoomRecorder, mix_recordings
from fi.simulate.runtime import (
    FailureStage,
    SimulationFailure,
    TestCaseStatus,
    derive_test_case_id,
    new_run_id,
)
from fi.simulate.simulation.engines.base import BaseEngine
from fi.simulate.simulation.generator import ScenarioGenerator
from fi.simulate.simulation.models import Persona, Scenario, TestCaseResult, TestReport
from fi.simulate.simulation.voice_prompt import CallType, build_voice_simulator_prompt

logger = logging.getLogger(__name__)
_SAFE_ROOM = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class _TargetParticipant:
    identity: str
    sid: str
    audio_track_sid: str
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class _CaseOutcome:
    status: TestCaseStatus
    transcript: str = ""
    messages: list[dict[str, str]] = field(default_factory=list)
    failure: SimulationFailure | None = None
    audio_input_path: str | None = None
    audio_output_path: str | None = None
    audio_combined_path: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
    evidence: list[EvidenceSourceSummary] = field(default_factory=list)
    provider_artifacts: list[ArtifactManifestEntry] = field(default_factory=list)


def _simulator_turn_handling(
    *,
    vad: object | None,
    allow_interruptions: bool | None = None,
    min_endpointing_delay: float | None = None,
    max_endpointing_delay: float | None = None,
) -> dict[str, object]:
    return {
        "turn_detection": "vad" if vad is not None else "stt",
        "endpointing": {
            "mode": "fixed",
            "min_delay": min_endpointing_delay or 0.4,
            "max_delay": max_endpointing_delay or 2.2,
        },
        "interruption": {
            "enabled": (True if allow_interruptions is None else allow_interruptions),
            "discard_audio_if_uninterruptible": True,
            "min_duration": 0.3,
        },
        "preemptive_generation": {"enabled": False},
    }


class _TestRunnerAgent(Agent):
    def __init__(
        self,
        persona: Persona,
        *,
        min_turn_messages: int = 0,
        **kwargs,
    ):
        turn_handling = kwargs.setdefault(
            "turn_handling",
            _simulator_turn_handling(vad=kwargs.get("vad")),
        )
        super().__init__(**kwargs)
        self._persona = persona
        self._min_turn_messages = min_turn_messages
        self._session_turn_handling = turn_handling
        self._session: AgentSession | None = None
        self._end_requested = asyncio.Event()
        self._usage_collector = metrics.ModelUsageCollector()

    @function_tool(
        name="endCall",
        description=(
            "End the conversation after you have said one natural closing sentence. "
            "Use this immediately when the caller says goodbye or the objective is done."
        ),
    )
    async def end_call(self) -> str:
        if self._session is None:
            return "Continue the conversation before ending the call."
        messages = _session_messages(self._session)
        if len(messages) < self._min_turn_messages or not _has_role_alternation(
            messages
        ):
            return (
                "Continue the conversation until both speakers have participated "
                f"and at least {self._min_turn_messages} messages are complete."
            )
        self._end_requested.set()
        return "Conversation ended."

    @property
    def started_session(self) -> AgentSession | None:
        return self._session

    @property
    def end_requested(self) -> asyncio.Event:
        return self._end_requested

    @property
    def model_usage(self) -> list[dict[str, object]]:
        return [
            usage.model_dump(mode="json")
            for usage in sorted(
                self._usage_collector.flatten(),
                key=lambda usage: (usage.type, usage.provider, usage.model),
            )
        ]

    async def start_session(
        self,
        room: rtc.Room,
        *,
        participant_kinds: list | None = None,
        participant_identity: str | None = None,
    ) -> AgentSession:
        session = AgentSession(
            stt=self.stt,
            llm=self.llm,
            tts=self.tts,
            vad=self.vad,
            turn_handling=self._session_turn_handling,
        )
        self._session = session
        session.on(
            "metrics_collected",
            lambda event: self._usage_collector.collect(event.metrics),
        )
        default_kinds = [
            rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
            getattr(
                rtc.ParticipantKind,
                "PARTICIPANT_KIND_AGENT",
                rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
            ),
            rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
        ]
        room_kwargs: dict = {
            "audio_input": AudioInputOptions(
                pre_connect_audio=False,
                pre_connect_audio_timeout=3.0,
            ),
            "text_output": False,
            "close_on_disconnect": False,
            "delete_room_on_close": False,
            "participant_kinds": participant_kinds or default_kinds,
        }
        if participant_identity:
            room_kwargs["participant_identity"] = participant_identity
        await session.start(
            self,
            room=room,
            room_options=RoomOptions(**room_kwargs),
        )
        return session

    def open_conversation(self) -> None:
        if self._session is None:
            raise RuntimeError("simulator_session_not_started")
        initial_message = self._persona.persona.get("initial_message")
        if isinstance(initial_message, str) and initial_message.strip():
            self._session.say(initial_message.strip())
            return
        self._session.generate_reply()

    async def transcription_node(
        self,
        text: AsyncIterable[str | TimedString],
        model_settings: ModelSettings,
    ):
        async for chunk in text:
            logger.debug(
                "Simulator transcription chunk",
                extra={"timed": isinstance(chunk, TimedString)},
            )
            yield chunk


class LiveKitEngine(BaseEngine):
    async def run(
        self,
        agent_definition: AgentDefinition | None = None,
        livekit_runtime: LiveKitSimulatorRuntime | None = None,
        scenario: Scenario | None = None,
        simulator: SimulatorAgentDefinition | None = None,
        num_scenarios: int = 1,
        topic: str | None = None,
        record_audio: bool = False,
        recorder_sample_rate: int = 8000,
        recorder_join_delay: float = 0.2,
        min_turn_messages: int = 8,
        max_seconds: float = 45.0,
        connect_timeout: float = 15.0,
        readiness_timeout: float = 30.0,
        cleanup_timeout: float = 30.0,
        conversation_direction: str = "simulator_first",
        agent_first_silence_timeout_seconds: float = 30.0,
        recording_root: str | Path = "recordings",
        recording_case_directory: str | Path | None = None,
        run_id: str | None = None,
        **kwargs,
    ) -> TestReport:
        if agent_definition is None:
            raise ValueError("LiveKitEngine requires 'agent_definition'.")
        runtime = _resolve_livekit_runtime(agent_definition, livekit_runtime)
        if conversation_direction not in {"simulator_first", "agent_first"}:
            raise ValueError(
                "conversation_direction must be simulator_first or agent_first"
            )
        if agent_first_silence_timeout_seconds <= 0:
            raise ValueError("agent_first_silence_timeout_seconds must be positive")
        if scenario is None:
            generator = ScenarioGenerator(
                agent_definition,
                llm_config=(
                    simulator.llm
                    if simulator is not None
                    else _default_simulator_llm_config()
                ),
            )
            if topic is None:
                simulator_context = (
                    simulator.instructions
                    if simulator and simulator.instructions
                    else ""
                )
                topic = (
                    simulator_context
                    or agent_definition.system_prompt
                    or "customer support scenarios"
                ).strip()
            personas = await generator.generate(
                topic=topic,
                num_personas=num_scenarios,
            )
            scenario = Scenario(name="Generated Scenario", dataset=personas)
        if runtime.room_name_verbatim and len(scenario.dataset) != 1:
            raise ValueError("room_name_verbatim requires a single-persona scenario")
        if (
            runtime.room_mode == "external"
            and len(scenario.dataset) > 1
            and not _has_room_template(runtime.room_name)
        ):
            raise ValueError(
                "external_room_template_required: concurrent-safe multi-case runs "
                "need {run_id}, {test_case_id}, or {index} in room_name"
            )
        transport = agent_definition.transport or TelephonyTransport()
        if transport.kind != "webrtc" and runtime.room_mode != "managed":
            raise ValueError("managed_transport_requires_managed_room")
        if (
            transport.kind == "sip_inbound"
            and len(scenario.dataset) > 1
            and not _has_room_template(runtime.room_name)
        ):
            raise ValueError(
                "sip_inbound_room_template_required: multi-case inbound runs "
                "need {run_id} or {test_case_id} in room_name"
            )
        current_run_id = run_id or new_run_id()
        if recording_case_directory is not None and len(scenario.dataset) != 1:
            raise ValueError(
                "recording_case_directory requires a single-persona scenario"
            )
        invocation_id = uuid4().hex[:12]
        report = TestReport()
        for index, persona in enumerate(scenario.dataset):
            persona_ref = persona.version or persona.content_hash()
            test_case_id = derive_test_case_id(
                current_run_id,
                persona_ref,
                index,
            )
            room_name = _resolve_room_name(
                runtime,
                run_id=current_run_id,
                test_case_id=test_case_id,
                index=index,
                invocation_id=invocation_id,
            )
            case_directory = (
                Path(recording_case_directory)
                if recording_case_directory is not None
                else Path(recording_root) / current_run_id / test_case_id
            )
            outcome = await self._run_single_test_case(
                agent_definition,
                runtime,
                persona,
                simulator,
                run_id=current_run_id,
                test_case_id=test_case_id,
                invocation_id=invocation_id,
                room_name=room_name,
                case_directory=case_directory,
                record_audio=record_audio,
                recorder_sample_rate=recorder_sample_rate,
                recorder_join_delay=recorder_join_delay,
                min_turn_messages=min_turn_messages,
                max_seconds=max_seconds,
                connect_timeout=connect_timeout,
                readiness_timeout=readiness_timeout,
                cleanup_timeout=cleanup_timeout,
                conversation_direction=conversation_direction,
                agent_first_silence_timeout_seconds=agent_first_silence_timeout_seconds,
            )
            metadata = {
                "engine": "livekit",
                "run_id": current_run_id,
                "test_case_id": test_case_id,
                "invocation_id": invocation_id,
                "status": outcome.status.value,
                "room_name": room_name,
                "room_mode": runtime.room_mode,
                **outcome.metadata,
            }
            if outcome.failure is not None:
                metadata["failure"] = outcome.failure.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            if outcome.evidence:
                metadata["evidence"] = [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in outcome.evidence
                ]
            if outcome.provider_artifacts:
                metadata["provider_artifacts"] = [
                    entry.model_dump(mode="json", exclude_none=True)
                    for entry in outcome.provider_artifacts
                ]
            report.results.append(
                TestCaseResult(
                    persona=persona,
                    transcript=outcome.transcript,
                    messages=outcome.messages,
                    metadata=metadata,
                    audio_input_path=outcome.audio_input_path,
                    audio_output_path=outcome.audio_output_path,
                    audio_combined_path=outcome.audio_combined_path,
                )
            )
        return report

    async def _run_single_test_case(
        self,
        agent_definition: AgentDefinition,
        runtime: LiveKitSimulatorRuntime,
        persona: Persona,
        simulator: SimulatorAgentDefinition | None,
        *,
        run_id: str,
        test_case_id: str,
        invocation_id: str,
        room_name: str,
        case_directory: Path,
        record_audio: bool,
        recorder_sample_rate: int,
        recorder_join_delay: float,
        min_turn_messages: int,
        max_seconds: float,
        connect_timeout: float,
        readiness_timeout: float,
        cleanup_timeout: float,
        conversation_direction: str,
        agent_first_silence_timeout_seconds: float,
    ) -> _CaseOutcome:
        api_key = os.environ.get(runtime.api_key_env)
        api_secret = os.environ.get(runtime.api_secret_env)
        if not api_key or not api_secret:
            return _failure_outcome(
                TestCaseStatus.FAILED,
                FailureStage.PREPARING,
                "livekit_credentials_missing",
                f"{runtime.api_key_env} and {runtime.api_secret_env} are required",
            )
        simulator_identity = f"fagi-simulator-{test_case_id[-12:]}"
        recorder_identity = f"fagi-recorder-{test_case_id[-12:]}"
        room = rtc.Room()
        models: LiveKitModels | None = None
        recorder: RoomRecorder | None = None
        customer_agent: _TestRunnerAgent | None = None
        session: AgentSession | None = None
        api_client: api.LiveKitAPI | None = None
        target: _TargetParticipant | None = None
        managed_room_owned = runtime.room_mode == "managed"
        room_connected = False
        cleanup_errors: list[str] = []
        outcome: _CaseOutcome | None = None
        sip_dispatch_rule_id: str | None = None
        sip_dispatch_rule_created = False
        vapi_originator: VapiCallOriginator | None = None
        provider_call_id: str | None = None
        provider_termination_source: str | None = None
        audio_bridge: LiveKitAudioBridge | None = None
        bridge_task: asyncio.Task[None] | None = None
        case_started_at = datetime.now(timezone.utc)
        transport = agent_definition.transport or TelephonyTransport()
        provider_target = agent_definition.target
        effective_target_identity = agent_definition.target_participant_identity
        effective_readiness_timeout = (
            transport.readiness_timeout_seconds
            if transport.kind == "sip_inbound"
            and transport.readiness_timeout_seconds is not None
            else readiness_timeout
        )
        sip_answer_timeout = transport.answer_timeout_seconds or max(
            connect_timeout, 60.0
        )
        try:
            if managed_room_owned:
                api_client = api.LiveKitAPI(
                    _api_url(str(runtime.url)),
                    api_key,
                    api_secret,
                )
                if transport.kind != "sip_outbound":
                    try:
                        await asyncio.wait_for(
                            api_client.room.create_room(
                                api.CreateRoomRequest(name=room_name)
                            ),
                            timeout=connect_timeout,
                        )
                    except asyncio.TimeoutError:
                        outcome = _failure_outcome(
                            TestCaseStatus.TIMED_OUT,
                            FailureStage.PREPARING,
                            "livekit_room_create_timeout",
                            "LiveKit room creation exceeded its deadline",
                            retryable=True,
                        )
                    except Exception as exc:
                        logger.warning(
                            "LiveKit room creation failed",
                            exc_info=redacted_exc_info(exc),
                            extra={
                                "run_id": run_id,
                                "test_case_id": test_case_id,
                                "room_name": room_name,
                            },
                        )
                        outcome = _failure_outcome(
                            TestCaseStatus.FAILED,
                            FailureStage.PREPARING,
                            "livekit_room_create_failed",
                            "Failed to create the LiveKit room",
                            details=_safe_provider_error_details(
                                exc, operation="room_create"
                            ),
                        )
                if outcome is None and transport.kind == "webrtc":
                    await asyncio.wait_for(
                        api_client.agent_dispatch.create_dispatch(
                            api.CreateAgentDispatchRequest(
                                agent_name=agent_definition.agent_name
                                or agent_definition.name,
                                room=room_name,
                                metadata=json.dumps(
                                    {
                                        "simulation_run_id": run_id,
                                        "test_case_id": test_case_id,
                                        "simulator_participant_identity": simulator_identity,
                                        "target_instructions": agent_definition.system_prompt,
                                    },
                                    sort_keys=True,
                                ),
                            )
                        ),
                        timeout=connect_timeout,
                    )
                elif outcome is None and transport.kind == "sip_inbound":
                    try:
                        (
                            sip_dispatch_rule_id,
                            sip_dispatch_rule_created,
                        ) = await asyncio.wait_for(
                            _ensure_sip_inbound_dispatch(
                                api_client,
                                transport=transport,
                                room_name=room_name,
                            ),
                            timeout=connect_timeout,
                        )
                    except asyncio.TimeoutError:
                        outcome = _failure_outcome(
                            TestCaseStatus.TIMED_OUT,
                            FailureStage.PREPARING,
                            "sip_inbound_dispatch_timeout",
                            "SIP inbound dispatch provisioning exceeded its deadline",
                            retryable=True,
                        )
                    except Exception as exc:
                        logger.warning(
                            "SIP inbound dispatch provisioning failed",
                            exc_info=redacted_exc_info(exc),
                            extra={
                                "run_id": run_id,
                                "test_case_id": test_case_id,
                                "room_name": room_name,
                            },
                        )
                        outcome = _failure_outcome(
                            TestCaseStatus.FAILED,
                            FailureStage.PREPARING,
                            "sip_inbound_dispatch_failed",
                            "Failed to provision SIP inbound dispatch",
                            details=_safe_provider_error_details(
                                exc, operation="sip_dispatch"
                            ),
                        )
            if outcome is not None:
                return outcome
            token = (
                AccessToken(api_key, api_secret)
                .with_identity(simulator_identity)
                .with_grants(VideoGrants(room_join=True, room=room_name))
                .to_jwt()
            )
            await asyncio.wait_for(
                room.connect(str(runtime.url), token),
                timeout=connect_timeout,
            )
            room_connected = True
            if record_audio:
                recorder = RoomRecorder(
                    url=str(runtime.url),
                    api_key=api_key,
                    api_secret=api_secret,
                    room_name=room_name,
                    identity=recorder_identity,
                    sample_rate=recorder_sample_rate,
                    output_dir=case_directory / "audio",
                    join_delay_s=recorder_join_delay,
                )
                await asyncio.wait_for(
                    recorder.start(),
                    timeout=connect_timeout,
                )
            customer_agent, models = await self._create_customer_agent(
                persona,
                simulator,
                call_type=(
                    "inbound"
                    if conversation_direction == "simulator_first"
                    else "outbound"
                ),
                agent_name=agent_definition.name,
                min_turn_messages=min_turn_messages,
            )
            sip_participant_identity: str | None = None
            bridge_identity: str | None = None
            if transport.kind == "sip_outbound":
                identity_template = (
                    transport.participant_identity
                    or "sip-caller-{invocation_id}-{test_case_id}"
                )
                sip_participant_identity = identity_template.format(
                    test_case_id=test_case_id,
                    run_id=run_id,
                    invocation_id=invocation_id,
                )
                if effective_target_identity is None:
                    effective_target_identity = sip_participant_identity
            elif transport.kind in {"vapi_websocket", "retell_webcall"}:
                provider_name = transport.kind.split("_", maxsplit=1)[0]
                bridge_identity = f"fagi-{provider_name}-bridge-{test_case_id[-12:]}"
                effective_target_identity = bridge_identity
            session_participant_kinds = None
            session_participant_identity: str | None = None
            if transport.kind in (
                "sip_outbound",
                "sip_inbound",
                "vapi_websocket",
                "retell_webcall",
            ):
                session_participant_kinds = [rtc.ParticipantKind.PARTICIPANT_KIND_SIP]
                session_participant_identity = (
                    effective_target_identity or sip_participant_identity
                )
            session = await asyncio.wait_for(
                customer_agent.start_session(
                    room,
                    participant_kinds=session_participant_kinds,
                    participant_identity=session_participant_identity,
                ),
                timeout=connect_timeout,
            )
            if transport.kind in {"vapi_websocket", "retell_webcall"}:
                try:
                    if transport.kind == "vapi_websocket" and isinstance(
                        provider_target, VapiTargetConfig
                    ):
                        connector = VapiWebSocketConnector.from_target(
                            provider_target,
                            first_message_mode=(
                                "assistant-waits-for-user"
                                if conversation_direction == "simulator_first"
                                else "assistant-speaks-first"
                            ),
                        )
                    elif transport.kind == "retell_webcall" and isinstance(
                        provider_target, RetellTargetConfig
                    ):
                        connector = RetellWebCallConnector.from_target(provider_target)
                    else:
                        connector = (
                            VapiWebSocketConnector.from_env()
                            if transport.kind == "vapi_websocket"
                            else RetellWebCallConnector.from_env()
                        )
                    audio_bridge = LiveKitAudioBridge(
                        url=str(runtime.url),
                        api_key=api_key,
                        api_secret=api_secret,
                        room_name=room_name,
                        identity=bridge_identity or "fagi-provider-bridge",
                        connector=connector,
                    )
                    await asyncio.wait_for(
                        audio_bridge.connect(), timeout=connect_timeout
                    )
                    provider_call_id = audio_bridge.call_id
                    bridge_task = asyncio.create_task(audio_bridge.run())
                except asyncio.TimeoutError:
                    outcome = _failure_outcome(
                        TestCaseStatus.TIMED_OUT,
                        FailureStage.PREPARING,
                        "web_bridge_start_timeout",
                        "Provider web call creation exceeded its deadline",
                        retryable=True,
                    )
                    return outcome
                except Exception as exc:
                    logger.warning(
                        "Provider web bridge creation failed",
                        exc_info=redacted_exc_info(exc),
                        extra={
                            "run_id": run_id,
                            "test_case_id": test_case_id,
                            "transport": transport.kind,
                        },
                    )
                    outcome = _failure_outcome(
                        TestCaseStatus.FAILED,
                        FailureStage.PREPARING,
                        "web_bridge_start_failed",
                        "Failed to start the provider web bridge",
                        details=_safe_provider_error_details(
                            exc, operation="web_bridge_start"
                        ),
                    )
                    return outcome
            if transport.kind == "sip_outbound" and api_client is not None:
                try:
                    logger.info(
                        "sip_outbound_dialing",
                        extra={
                            "run_id": run_id,
                            "test_case_id": test_case_id,
                            "room_name": room_name,
                        },
                    )
                    await asyncio.wait_for(
                        api_client.sip.create_sip_participant(
                            api.CreateSIPParticipantRequest(
                                sip_trunk_id=transport.sip_trunk_id,
                                sip_number=transport.sip_number,
                                sip_call_to=transport.sip_call_to,
                                room_name=room_name,
                                participant_identity=sip_participant_identity,
                                wait_until_answered=True,
                                play_ringtone=True,
                            )
                        ),
                        timeout=sip_answer_timeout,
                    )
                except asyncio.TimeoutError:
                    outcome = _failure_outcome(
                        TestCaseStatus.TIMED_OUT,
                        FailureStage.PREPARING,
                        "sip_answer_timeout",
                        "Outbound SIP call was not answered before the deadline",
                        retryable=True,
                    )
                    return outcome
                except Exception as exc:
                    logger.warning(
                        "SIP dial failed",
                        exc_info=redacted_exc_info(exc),
                        extra={
                            "run_id": run_id,
                            "test_case_id": test_case_id,
                            "room_name": room_name,
                        },
                    )
                    outcome = _failure_outcome(
                        TestCaseStatus.FAILED,
                        FailureStage.PREPARING,
                        "sip_dial_failed",
                        "Failed to dial the SIP participant",
                        details=_safe_provider_error_details(exc, operation="sip_dial"),
                    )
                    return outcome
            if transport.kind == "sip_inbound":
                logger.info(
                    "sip_inbound_ready",
                    extra={
                        "run_id": run_id,
                        "test_case_id": test_case_id,
                        "room_name": room_name,
                        "sip_dispatch_rule_id": sip_dispatch_rule_id,
                        "sip_dispatch_rule_created": sip_dispatch_rule_created,
                    },
                )
            if transport.inbound_call_originator == "vapi":
                try:
                    vapi_originator = VapiCallOriginator.from_env()
                    vapi_call = await asyncio.wait_for(
                        vapi_originator.start(), timeout=connect_timeout
                    )
                    provider_call_id = vapi_call.call_id
                except asyncio.TimeoutError:
                    outcome = _failure_outcome(
                        TestCaseStatus.TIMED_OUT,
                        FailureStage.PREPARING,
                        "vapi_call_start_timeout",
                        "Vapi call creation exceeded its deadline",
                        retryable=True,
                    )
                    return outcome
                except Exception as exc:
                    logger.warning(
                        "Vapi call creation failed",
                        exc_info=redacted_exc_info(exc),
                        extra={
                            "run_id": run_id,
                            "test_case_id": test_case_id,
                        },
                    )
                    outcome = _failure_outcome(
                        TestCaseStatus.FAILED,
                        FailureStage.PREPARING,
                        "vapi_call_start_failed",
                        "Failed to start the Vapi call",
                        details=_safe_provider_error_details(
                            exc, operation="vapi_call_start"
                        ),
                    )
                    return outcome
            target = await _wait_for_target_audio(
                room,
                excluded_identities={simulator_identity, recorder_identity},
                target_identity=effective_target_identity,
                timeout=effective_readiness_timeout,
            )
            if conversation_direction == "simulator_first":
                customer_agent.open_conversation()
            stop_reason = await _wait_for_conversation_end(
                room,
                session,
                customer_agent=customer_agent,
                target_identity=target.identity,
                min_turn_messages=min_turn_messages,
                timeout=max_seconds,
                conversation_direction=conversation_direction,
                agent_first_silence_timeout_seconds=agent_first_silence_timeout_seconds,
                provider_task=bridge_task,
            )
            messages = _canonical_report_messages(session)
            outcome = _conversation_outcome(
                stop_reason,
                messages,
                min_turn_messages=min_turn_messages,
            )
        except asyncio.TimeoutError:
            stage = (
                FailureStage.READINESS
                if session is not None and target is None
                else FailureStage.PREPARING
            )
            if stage == FailureStage.READINESS and transport.kind == "sip_inbound":
                code = "sip_inbound_no_participant"
                message = "No inbound SIP participant joined before deadline"
            elif stage == FailureStage.READINESS:
                code = "agent_unavailable"
                message = "Target agent did not become ready"
            else:
                code = "livekit_connect_timeout"
                message = "LiveKit setup exceeded its deadline"
            status = (
                TestCaseStatus.AGENT_UNAVAILABLE
                if stage == FailureStage.READINESS
                else TestCaseStatus.TIMED_OUT
            )
            outcome = _failure_outcome(
                status,
                stage,
                code,
                message,
                retryable=True,
            )
        except Exception as exc:
            logger.error(
                "LiveKit test case failed",
                exc_info=redacted_exc_info(exc),
                extra={
                    "run_id": run_id,
                    "test_case_id": test_case_id,
                    "exception_type": type(exc).__name__,
                },
            )
            outcome = _failure_outcome(
                TestCaseStatus.FAILED,
                FailureStage.RUNNING if session is not None else FailureStage.PREPARING,
                "livekit_case_failed",
                "LiveKit test case failed",
                details={"exception_type": type(exc).__name__},
            )
        finally:
            session_to_close = session or (
                getattr(customer_agent, "started_session", None)
                if customer_agent is not None
                else None
            )
            if session_to_close is not None:
                try:
                    await _close_agent_session(
                        session_to_close,
                        timeout=cleanup_timeout,
                    )
                except Exception as exc:
                    _record_cleanup_error(
                        cleanup_errors,
                        exc,
                        "session_close",
                        run_id,
                        test_case_id,
                    )
            if models is not None:
                try:
                    await asyncio.wait_for(
                        models.aclose(),
                        timeout=cleanup_timeout,
                    )
                except Exception as exc:
                    _record_cleanup_error(
                        cleanup_errors,
                        exc,
                        "models_close",
                        run_id,
                        test_case_id,
                    )
            if recorder is not None:
                try:
                    await asyncio.wait_for(
                        recorder.aclose(),
                        timeout=cleanup_timeout,
                    )
                except Exception as exc:
                    _record_cleanup_error(
                        cleanup_errors,
                        exc,
                        "recorder_close",
                        run_id,
                        test_case_id,
                    )
            if audio_bridge is not None:
                try:
                    await asyncio.wait_for(
                        audio_bridge.aclose(), timeout=cleanup_timeout
                    )
                    if bridge_task is not None:
                        await asyncio.wait_for(bridge_task, timeout=cleanup_timeout)
                except Exception as exc:
                    _record_cleanup_error(
                        cleanup_errors,
                        exc,
                        "bridge_close",
                        run_id,
                        test_case_id,
                    )
            if room_connected:
                try:
                    await asyncio.wait_for(room.disconnect(), timeout=cleanup_timeout)
                except Exception as exc:
                    _record_cleanup_error(
                        cleanup_errors,
                        exc,
                        "room_disconnect",
                        run_id,
                        test_case_id,
                    )
            if vapi_originator is not None:
                try:
                    if provider_call_id is not None:
                        await asyncio.wait_for(
                            vapi_originator.stop(provider_call_id),
                            timeout=cleanup_timeout,
                        )
                        provider_termination_source = "sdk_originator_cleanup"
                    await vapi_originator.close()
                except Exception as exc:
                    _record_cleanup_error(
                        cleanup_errors,
                        exc,
                        "vapi_call_stop",
                        run_id,
                        test_case_id,
                    )
            if (
                api_client is not None
                and sip_dispatch_rule_id
                and sip_dispatch_rule_created
            ):
                try:
                    await asyncio.wait_for(
                        _delete_sip_dispatch_rule(api_client, sip_dispatch_rule_id),
                        timeout=cleanup_timeout,
                    )
                except Exception as exc:
                    if not _is_not_found(exc):
                        _record_cleanup_error(
                            cleanup_errors,
                            exc,
                            "sip_dispatch_delete",
                            run_id,
                            test_case_id,
                        )
            if api_client is not None and managed_room_owned:
                try:
                    await asyncio.wait_for(
                        api_client.room.delete_room(
                            api.DeleteRoomRequest(room=room_name)
                        ),
                        timeout=cleanup_timeout,
                    )
                except Exception as exc:
                    if not _is_not_found(exc):
                        _record_cleanup_error(
                            cleanup_errors,
                            exc,
                            "room_delete",
                            run_id,
                            test_case_id,
                        )
            if api_client is not None:
                try:
                    await api_client.aclose()
                except Exception as exc:
                    _record_cleanup_error(
                        cleanup_errors,
                        exc,
                        "api_close",
                        run_id,
                        test_case_id,
                    )
        if outcome is None:
            outcome = _failure_outcome(
                TestCaseStatus.FAILED,
                FailureStage.FINALIZING,
                "livekit_outcome_missing",
                "LiveKit test case ended without an outcome",
            )
        if recorder is not None:
            _attach_recordings(
                outcome,
                recorder,
                simulator_identity=simulator_identity,
                target_identity=target.identity if target is not None else None,
                case_directory=case_directory,
                sample_rate=recorder_sample_rate,
            )
            if recorder.errors:
                cleanup_errors.extend(
                    f"recording:{type(error).__name__}" for error in recorder.errors
                )
        if agent_definition.provider_evidence is not None:
            provider_summary, provider_artifacts = await _collect_provider_evidence(
                config=agent_definition.provider_evidence,
                transport=transport,
                run_id=run_id,
                test_case_id=test_case_id,
                case_directory=case_directory,
                started_at=case_started_at,
                target=target,
                provider_call_id_hint=provider_call_id,
                provider_api_key=_target_api_key(provider_target),
                provider_api_base_url=_target_evidence_base_url(provider_target),
                termination_source=provider_termination_source,
            )
            if provider_summary is not None:
                outcome.evidence.append(provider_summary)
                if provider_call_id is None:
                    resolved_call_id = provider_summary.metadata.get("call_id")
                    if resolved_call_id:
                        provider_call_id = str(resolved_call_id)
            outcome.provider_artifacts.extend(provider_artifacts)
        outcome.metadata.update(
            {
                "simulator_participant_identity": simulator_identity,
                "target_participant_identity": (
                    target.identity if target is not None else None
                ),
                "target_participant_sid": target.sid if target is not None else None,
                "target_audio_track_sid": (
                    target.audio_track_sid if target is not None else None
                ),
                "target_participant_attributes": (
                    dict(target.attributes) if target is not None else {}
                ),
                "cleanup_status": "failed" if cleanup_errors else "completed",
                "cleanup_errors": cleanup_errors,
                "sip_dispatch_rule_id": sip_dispatch_rule_id,
                "sip_dispatch_rule_created": sip_dispatch_rule_created,
                "target_provider": (
                    provider_target.provider if provider_target is not None else None
                ),
                "provider_call_id": provider_call_id,
                "vapi_call_id": (
                    provider_call_id
                    if transport.kind == "vapi_websocket"
                    or transport.inbound_call_originator == "vapi"
                    else None
                ),
                "retell_call_id": (
                    provider_call_id if transport.kind == "retell_webcall" else None
                ),
                "simulator_model_usage": (
                    customer_agent.model_usage
                    if customer_agent is not None
                    and hasattr(customer_agent, "model_usage")
                    else []
                ),
            }
        )
        return outcome

    async def _create_customer_agent(
        self,
        persona: Persona,
        simulator: SimulatorAgentDefinition | None,
        *,
        call_type: CallType = "inbound",
        agent_name: str | None = None,
        min_turn_messages: int = 0,
    ) -> tuple[_TestRunnerAgent, LiveKitModels]:
        customer_prompt = build_voice_simulator_prompt(
            persona,
            call_type=call_type,
            agent_name=agent_name,
            additional_instructions=(
                simulator.instructions if simulator is not None else None
            ),
            default_language=(
                simulator.stt.language if simulator is not None else None
            ),
        )
        if simulator is None:
            voice_provider = os.environ.get(
                "SIMULATOR_VOICE_PROVIDER", "openai"
            ).lower()
            llm_config = _default_simulator_llm_config()
            stt_config = STTConfig(
                provider=voice_provider,
                model=os.environ.get("SIMULATOR_STT_MODEL", "gpt-4o-mini-transcribe"),
            )
            tts_config = TTSConfig(
                provider=voice_provider,
                model=os.environ.get("SIMULATOR_TTS_MODEL", "gpt-4o-mini-tts"),
                voice=os.environ.get("SIMULATOR_TTS_VOICE_ID", "alloy"),
            )
            instructions = customer_prompt
            allow_interruptions = None
            min_endpointing_delay = None
            max_endpointing_delay = None
            use_aligned_transcript = None
        else:
            llm_config = simulator.llm
            stt_config = simulator.stt
            tts_config = simulator.tts
            instructions = customer_prompt
            allow_interruptions = simulator.allow_interruptions
            min_endpointing_delay = simulator.min_endpointing_delay
            max_endpointing_delay = simulator.max_endpointing_delay
            use_aligned_transcript = simulator.use_tts_aligned_transcript
        models = await build_livekit_models(
            llm_config=llm_config,
            stt_config=stt_config,
            tts_config=tts_config,
        )
        vad = silero.VAD.load()
        agent = _TestRunnerAgent(
            persona=persona,
            min_turn_messages=min_turn_messages,
            stt=models.stt,
            llm=models.llm,
            tts=models.tts,
            vad=vad,
            instructions=instructions,
            turn_handling=_simulator_turn_handling(
                vad=vad,
                allow_interruptions=allow_interruptions,
                min_endpointing_delay=min_endpointing_delay,
                max_endpointing_delay=max_endpointing_delay,
            ),
            use_tts_aligned_transcript=use_aligned_transcript,
        )
        return agent, models


async def _wait_for_target_audio(
    room: rtc.Room,
    *,
    excluded_identities: set[str],
    target_identity: str | None,
    timeout: float,
) -> _TargetParticipant:
    ready = asyncio.Event()
    selected: _TargetParticipant | None = None

    def inspect_room(*_args) -> None:
        nonlocal selected
        selected = _find_target_audio(
            room,
            excluded_identities=excluded_identities,
            target_identity=target_identity,
        )
        if selected is not None:
            ready.set()

    room.on("participant_connected", inspect_room)
    room.on("track_published", inspect_room)
    room.on("track_subscribed", inspect_room)
    inspect_room()
    try:
        await asyncio.wait_for(ready.wait(), timeout=timeout)
    finally:
        _remove_room_listener(room, "participant_connected", inspect_room)
        _remove_room_listener(room, "track_published", inspect_room)
        _remove_room_listener(room, "track_subscribed", inspect_room)
    if selected is None:
        raise asyncio.TimeoutError
    return selected


def _find_target_audio(
    room: rtc.Room,
    *,
    excluded_identities: set[str],
    target_identity: str | None,
) -> _TargetParticipant | None:
    candidates: list[tuple[int, _TargetParticipant]] = []
    agent_kind = getattr(
        rtc.ParticipantKind,
        "PARTICIPANT_KIND_AGENT",
        None,
    )
    for participant in room.remote_participants.values():
        identity = str(participant.identity)
        if identity in excluded_identities:
            continue
        if target_identity is not None and identity != target_identity:
            continue
        priority = 0 if getattr(participant, "kind", None) == agent_kind else 1
        for publication in participant.track_publications.values():
            if getattr(publication, "kind", None) != rtc.TrackKind.KIND_AUDIO:
                continue
            attrs = dict(getattr(participant, "attributes", {}) or {})
            candidates.append(
                (
                    priority,
                    _TargetParticipant(
                        identity=identity,
                        sid=str(participant.sid),
                        audio_track_sid=str(publication.sid),
                        attributes={
                            str(key): str(value) for key, value in attrs.items()
                        },
                    ),
                )
            )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (item[0], item[1].identity))[0][1]


async def _wait_for_conversation_end(
    room: rtc.Room,
    session: AgentSession,
    *,
    customer_agent: _TestRunnerAgent,
    target_identity: str,
    min_turn_messages: int,
    timeout: float,
    conversation_direction: str,
    agent_first_silence_timeout_seconds: float,
    provider_task: asyncio.Task[None] | None = None,
) -> str:
    closed = asyncio.Event()
    target_disconnected = asyncio.Event()

    def on_close(_event) -> None:
        closed.set()

    def on_participant_disconnected(participant) -> None:
        if str(participant.identity) == target_identity:
            target_disconnected.set()

    session.on("close", on_close)
    room.on("participant_disconnected", on_participant_disconnected)
    tasks = {
        "closed": asyncio.create_task(closed.wait()),
        "target_disconnected": asyncio.create_task(target_disconnected.wait()),
        "simulator_end_call": asyncio.create_task(customer_agent.end_requested.wait()),
        "minimum_messages_reached": asyncio.create_task(
            _wait_for_stable_minimum_messages(session, min_turn_messages)
        ),
    }
    if conversation_direction == "agent_first":
        tasks["conversation_silence_timeout"] = asyncio.create_task(
            _wait_for_agent_first_silence(
                session,
                timeout_seconds=agent_first_silence_timeout_seconds,
            )
        )
    if provider_task is not None:
        tasks["provider_disconnected"] = provider_task
    try:
        done, pending = await asyncio.wait(
            set(tasks.values()),
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        owned_pending = {
            task
            for name, task in tasks.items()
            if task in pending and name != "provider_disconnected"
        }
        for task in owned_pending:
            task.cancel()
        if owned_pending:
            await asyncio.gather(*owned_pending, return_exceptions=True)
        if not done:
            return "timeout"
        for reason in (
            "simulator_end_call",
            "target_disconnected",
            "conversation_silence_timeout",
            "minimum_messages_reached",
            "provider_disconnected",
            "closed",
        ):
            task = tasks.get(reason)
            if task is not None and task in done:
                return "session_closed" if reason == "closed" else reason
        return "session_closed"
    finally:
        _remove_room_listener(
            room,
            "participant_disconnected",
            on_participant_disconnected,
        )


async def _wait_for_stable_minimum_messages(
    session: AgentSession,
    min_turn_messages: int,
    *,
    quiet_seconds: float = 5.0,
) -> None:
    """Finish after the message floor and a short period without a new turn."""
    if min_turn_messages <= 0:
        return
    last_signature: tuple[tuple[str, str], ...] | None = None
    stable_since: float | None = None
    loop = asyncio.get_running_loop()
    while True:
        messages = _session_messages(session)
        signature = tuple((message["role"], message["content"]) for message in messages)
        eligible = len(messages) >= min_turn_messages and _has_role_alternation(
            messages
        )
        participant_speaking = (
            getattr(session, "agent_state", None) == "speaking"
            or getattr(session, "user_state", None) == "speaking"
        )
        if not eligible or participant_speaking:
            stable_since = None
        elif stable_since is None or signature != last_signature:
            stable_since = loop.time()
        elif stable_since is not None and loop.time() - stable_since >= quiet_seconds:
            return
        last_signature = signature
        await asyncio.sleep(0.1)


async def _wait_for_agent_first_silence(
    session: AgentSession,
    *,
    timeout_seconds: float,
) -> None:
    last_signature: tuple[tuple[str, str], ...] = ()
    last_change = asyncio.get_running_loop().time()
    while True:
        messages = _session_messages(session)
        signature = tuple((message["role"], message["content"]) for message in messages)
        if signature != last_signature:
            last_signature = signature
            last_change = asyncio.get_running_loop().time()
        roles = {message["role"] for message in messages if message["content"]}
        if {"user", "assistant"}.issubset(
            roles
        ) and asyncio.get_running_loop().time() - last_change >= timeout_seconds:
            return
        await asyncio.sleep(0.1)


def _session_messages(session: AgentSession) -> list[dict[str, Any]]:
    """Return normalized transcript messages with real per-item speech timing.

    Each dict carries:
      role, content: str
      started_speaking_at, stopped_speaking_at: float | None
          Real audio timing from ``ChatMessage.metrics`` (seconds since epoch).
          See livekit.agents.llm.chat_context.MetricsReport.
      created_at: float
          Fallback wall-clock stamp from ``ChatMessage.created_at`` (used when
          the metrics timestamps are missing, e.g. text-only turns).
      interrupted: bool
      e2e_latency: float | None
          Agent-side turn latency, when reported by LiveKit.

    Downstream code turns these into millisecond offsets so the platform can
    recompute WPM, talk-ratio and interruption counts with real overlap data.
    """
    messages: list[dict[str, Any]] = []
    for item in session.history.items:
        if getattr(item, "type", None) != "message":
            continue
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if role is None or text is None:
            continue
        interrupted = bool(getattr(item, "interrupted", False))
        created_at = float(getattr(item, "created_at", 0.0) or 0.0)
        metrics = getattr(item, "metrics", None) or {}
        started_speaking_at = _maybe_float(metrics.get("started_speaking_at"))
        stopped_speaking_at = _maybe_float(metrics.get("stopped_speaking_at"))
        e2e_latency = _maybe_float(metrics.get("e2e_latency"))
        current: dict[str, Any] = {
            "role": str(role),
            "content": str(text),
            "created_at": created_at,
            "started_speaking_at": started_speaking_at,
            "stopped_speaking_at": stopped_speaking_at,
            "interrupted": interrupted,
            "e2e_latency": e2e_latency,
        }
        if messages and messages[-1]["role"] == current["role"]:
            previous = messages[-1]
            previous_text = previous["content"]
            if current["content"].startswith(previous_text):
                # Newer emission extends the previous partial — keep the
                # earliest start we saw, adopt the latest stop.
                current["started_speaking_at"] = (
                    previous.get("started_speaking_at")
                    or current["started_speaking_at"]
                )
                current["created_at"] = previous["created_at"] or created_at
                messages[-1] = current
            elif previous_text.startswith(current["content"]):
                previous["interrupted"] = previous.get("interrupted") or interrupted
                previous["stopped_speaking_at"] = (
                    previous.get("stopped_speaking_at")
                    or current["stopped_speaking_at"]
                )
            elif previous.get("interrupted") or interrupted:
                previous["content"] = (
                    f"{previous_text} {current['content']}".strip()
                )
                previous["interrupted"] = interrupted
                previous["stopped_speaking_at"] = (
                    current["stopped_speaking_at"]
                    or previous.get("stopped_speaking_at")
                )
            else:
                messages.append(current)
            continue
        messages.append(current)
    return messages


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _canonical_report_messages(session: AgentSession) -> list[dict[str, Any]]:
    """Emit report messages with roles remapped to the test-agent perspective.

    LiveKit reports our simulator as ``assistant`` and the target agent as
    ``user`` (the SDK connects with role ``agent``); we swap those so the
    downstream platform sees:
      role="user"      → simulator / customer
      role="assistant" → agent-under-test
    which matches the CallTranscript convention.

    Timing anchors (``started_speaking_at`` / ``stopped_speaking_at``) travel
    through unchanged so the platform can derive ms offsets.
    """
    role_map = {"assistant": "user", "user": "assistant"}
    messages: list[dict[str, Any]] = []
    for source in _session_messages(session):
        messages.append(
            {
                "role": role_map.get(source["role"], source["role"]),
                "content": source["content"],
                "created_at": source.get("created_at"),
                "started_speaking_at": source.get("started_speaking_at"),
                "stopped_speaking_at": source.get("stopped_speaking_at"),
                "interrupted": source.get("interrupted", False),
                "e2e_latency": source.get("e2e_latency"),
            }
        )
    return messages


def _has_role_alternation(messages: list[dict[str, Any]]) -> bool:
    roles = {msg.get("role") for msg in messages if msg.get("content")}
    return "user" in roles and "assistant" in roles


def _conversation_outcome(
    stop_reason: str,
    messages: list[dict[str, str]],
    *,
    min_turn_messages: int,
) -> _CaseOutcome:
    transcript = "\n".join(
        f"{message['role']}: {message['content']}" for message in messages
    )
    if stop_reason == "timeout":
        return _failure_outcome(
            TestCaseStatus.TIMED_OUT,
            FailureStage.RUNNING,
            "conversation_timeout",
            "Conversation exceeded its deadline",
            transcript=transcript,
            messages=messages,
            retryable=True,
        )
    if stop_reason in {"conversation_silence_timeout", "session_closed"}:
        code = stop_reason
        message = (
            "Agent-first conversation stalled after it began"
            if stop_reason == "conversation_silence_timeout"
            else "Conversation session closed before a natural end condition"
        )
        return _failure_outcome(
            TestCaseStatus.FAILED,
            FailureStage.RUNNING,
            code,
            message,
            transcript=transcript,
            messages=messages,
            retryable=True,
        )
    if len(messages) < min_turn_messages or not _has_role_alternation(messages):
        code = (
            "target_disconnected"
            if stop_reason == "target_disconnected"
            else "insufficient_conversation"
        )
        return _failure_outcome(
            TestCaseStatus.FAILED,
            FailureStage.RUNNING,
            code,
            "Conversation ended before the required alternating turns completed",
            transcript=transcript,
            messages=messages,
            retryable=stop_reason in {"target_disconnected", "session_closed"},
            details={
                "stop_reason": stop_reason,
                "turn_count": str(len(messages)),
                "minimum_turn_count": str(min_turn_messages),
            },
        )
    return _CaseOutcome(
        status=TestCaseStatus.COMPLETED,
        transcript=transcript,
        messages=messages,
        metadata={"stop_reason": stop_reason},
    )


def _failure_outcome(
    status: TestCaseStatus,
    stage: FailureStage,
    code: str,
    message: str,
    *,
    transcript: str = "",
    messages: list[dict[str, str]] | None = None,
    retryable: bool = False,
    details: dict[str, str] | None = None,
) -> _CaseOutcome:
    return _CaseOutcome(
        status=status,
        transcript=transcript,
        messages=messages or [],
        failure=SimulationFailure(
            stage=stage,
            code=code,
            message=message,
            retryable=retryable,
            provider="livekit",
            details=details or {},
        ),
    )


def _attach_recordings(
    outcome: _CaseOutcome,
    recorder: RoomRecorder,
    *,
    simulator_identity: str,
    target_identity: str | None,
    case_directory: Path,
    sample_rate: int,
) -> None:
    simulator_paths = recorder.paths_for_participant(simulator_identity)
    target_paths = (
        recorder.paths_for_participant(target_identity)
        if target_identity is not None
        else []
    )
    audio_directory = case_directory / "audio"
    input_path = _collapse_recordings(
        simulator_paths,
        audio_directory / "simulator.wav",
        sample_rate=sample_rate,
    )
    output_path = _collapse_recordings(
        target_paths,
        audio_directory / "target.wav",
        sample_rate=sample_rate,
    )
    combined_path = mix_recordings(
        [path for path in (input_path, output_path) if path is not None],
        audio_directory / "combined.wav",
        sample_rate=sample_rate,
    )
    outcome.audio_input_path = str(input_path) if input_path is not None else None
    outcome.audio_output_path = str(output_path) if output_path is not None else None
    outcome.audio_combined_path = (
        str(combined_path) if combined_path is not None else None
    )
    outcome.metadata["recording_tracks"] = [
        {
            "participant_identity": record.participant_identity,
            "participant_sid": record.participant_sid,
            "track_sid": record.track_sid,
            "path": str(record.path),
        }
        for record in recorder.records
    ]


def _collapse_recordings(
    paths: list[Path],
    destination: Path,
    *,
    sample_rate: int,
) -> Path | None:
    if not paths:
        return None
    if len(paths) == 1:
        return paths[0]
    return mix_recordings(paths, destination, sample_rate=sample_rate)


def _target_api_key(target: VoiceProviderTarget | None) -> str | None:
    if target is None:
        return None
    return os.environ.get(target.api_key_env) or None


def _target_evidence_base_url(target: VoiceProviderTarget | None) -> str | None:
    if isinstance(target, VapiTargetConfig):
        return str(target.api_base_url).rstrip("/")
    if isinstance(target, RetellTargetConfig):
        parsed = urlsplit(str(target.api_url))
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def _default_simulator_llm_config() -> LLMConfig:
    return LLMConfig(
        provider=os.environ.get("SIMULATOR_LLM_PROVIDER", "openai"),
        model=os.environ.get("SIMULATOR_LLM_MODEL", "gpt-4o-mini"),
        temperature=0.6,
    )


def _resolve_livekit_runtime(
    agent_definition: AgentDefinition,
    runtime: LiveKitSimulatorRuntime | None,
) -> LiveKitSimulatorRuntime:
    if runtime is not None:
        return runtime
    if agent_definition.url is None or not agent_definition.room_name:
        raise ValueError(
            "livekit_runtime_required: provide LiveKitSimulatorRuntime or legacy "
            "AgentDefinition url and room_name"
        )
    return LiveKitSimulatorRuntime(
        url=agent_definition.url,
        room_name=agent_definition.room_name,
        room_mode=agent_definition.room_mode,
    )


def _resolve_room_name(
    runtime: LiveKitSimulatorRuntime,
    *,
    run_id: str,
    test_case_id: str,
    index: int,
    invocation_id: str,
) -> str:
    rendered = runtime.room_name.format(
        run_id=run_id,
        test_case_id=test_case_id,
        index=index,
        invocation_id=invocation_id,
    )
    if runtime.room_mode == "external" or getattr(runtime, "room_name_verbatim", False):
        return rendered
    prefix = _SAFE_ROOM.sub("-", rendered).strip("-._") or "simulation"
    suffix_parts = []
    if invocation_id not in prefix:
        suffix_parts.append(invocation_id)
    if test_case_id not in prefix:
        suffix_parts.append(test_case_id[-12:])
    suffix = "-" + "-".join(suffix_parts) if suffix_parts else ""
    return f"{prefix[: 255 - len(suffix)]}{suffix}"


def _has_room_template(room_name: str) -> bool:
    return any(
        marker in room_name for marker in ("{run_id}", "{test_case_id}", "{index}")
    )


def _api_url(url: str) -> str:
    if url.startswith("wss://"):
        return "https://" + url.removeprefix("wss://")
    if url.startswith("ws://"):
        return "http://" + url.removeprefix("ws://")
    return url


def _remove_room_listener(room: rtc.Room, event: str, listener) -> None:
    try:
        room.off(event, listener)
    except (AttributeError, ValueError):
        logger.debug("LiveKit listener was already removed", extra={"event": event})


async def _close_agent_session(session: AgentSession, *, timeout: float) -> None:
    """Close without cancelling LiveKit's recursive activity teardown on timeout."""
    close_session = getattr(session, "aclose", None)
    if close_session is None:
        session.shutdown(drain=False)
        return
    close_task = asyncio.create_task(close_session())
    try:
        await asyncio.wait_for(asyncio.shield(close_task), timeout=timeout)
    except asyncio.TimeoutError:
        close_task.add_done_callback(_consume_background_task_result)
        raise


def _consume_background_task_result(task: asyncio.Task) -> None:
    try:
        task.result()
    except (Exception, asyncio.CancelledError):
        pass


def _is_not_found(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    return str(getattr(code, "value", code)).lower() in {
        "not_found",
        "404",
    }


def _record_cleanup_error(
    errors: list[str],
    exc: Exception,
    operation: str,
    run_id: str,
    test_case_id: str,
) -> None:
    errors.append(f"{operation}:{type(exc).__name__}")
    logger.error(
        "LiveKit cleanup operation failed",
        exc_info=redacted_exc_info(exc),
        extra={
            "run_id": run_id,
            "test_case_id": test_case_id,
            "operation": operation,
            "exception_type": type(exc).__name__,
        },
    )


_LIVEKIT_INBOUND_TRUNK_ENV = "LIVEKIT_INBOUND_TRUNK_ID"


def _safe_provider_error_details(
    exc: Exception, *, operation: str
) -> dict[str, object]:
    """Extract sanitized error attributes for report failures.

    Never returns the exception message; only structural fields that are
    known to be safe from LiveKit/Twirp exception classes.
    """

    code = getattr(exc, "code", None)
    if code is not None:
        code_value = getattr(code, "value", None)
        if code_value is None and not isinstance(code, (str, int)):
            code_value = str(code)
        else:
            code_value = code_value if code_value is not None else code
    else:
        code_value = None
    status = getattr(exc, "status", None)
    details: dict[str, object] = {
        "operation": operation,
        "exception_type": type(exc).__name__,
    }
    if code_value is not None:
        details["provider_code"] = code_value
    if status is not None:
        try:
            details["http_status"] = int(status)
        except (TypeError, ValueError):
            details["http_status"] = str(status)
    metadata = getattr(exc, "metadata", None)
    if isinstance(metadata, dict):
        for key in ("sip_status_code", "sip_status", "sip-code"):
            value = metadata.get(key)
            if value is not None:
                details["sip_status_code"] = str(value)
                break
    return details


async def _ensure_sip_inbound_dispatch(
    api_client: api.LiveKitAPI,
    *,
    transport: TelephonyTransport,
    room_name: str,
) -> tuple[str, bool]:
    """Return ``(sip_dispatch_rule_id, created_by_sdk)``.

    When ``transport.dispatch_rule_name`` is supplied the SDK verifies
    the rule exists and reuses it. Otherwise the SDK provisions a
    per-run direct rule bound to ``LIVEKIT_INBOUND_TRUNK_ID`` that routes
    incoming calls into ``room_name`` — the same room the local
    simulator has already joined — and returns its id so the caller can
    tear it down.
    """

    existing = await api_client.sip.list_sip_dispatch_rule(ListSIPDispatchRuleRequest())
    if transport.dispatch_rule_name:
        for rule in existing.items:
            if rule.name != transport.dispatch_rule_name:
                continue
            direct = (
                getattr(rule.rule, "dispatch_rule_direct", None) if rule.rule else None
            )
            direct_room = getattr(direct, "room_name", "") if direct is not None else ""
            if not direct_room:
                raise RuntimeError(
                    "sip_inbound_rule_mismatch: "
                    f"{transport.dispatch_rule_name} is not a direct rule"
                )
            if direct_room != room_name:
                raise RuntimeError(
                    "sip_inbound_rule_mismatch: "
                    f"{transport.dispatch_rule_name} targets a different room"
                )
            return rule.sip_dispatch_rule_id, False
        raise RuntimeError(f"sip_inbound_rule_missing: {transport.dispatch_rule_name}")
    trunk_id = os.environ.get(_LIVEKIT_INBOUND_TRUNK_ENV)
    if not trunk_id:
        raise RuntimeError(
            f"sip_inbound_trunk_missing: set {_LIVEKIT_INBOUND_TRUNK_ENV}"
        )
    for rule in existing.items:
        if trunk_id and trunk_id in rule.trunk_ids:
            raise RuntimeError(
                "sip_inbound_route_conflict: existing dispatch rule "
                f"{rule.sip_dispatch_rule_id} already covers this trunk"
            )
    rule_name = f"sim-inbound-{room_name[-24:]}"
    resp = await api_client.sip.create_sip_dispatch_rule(
        CreateSIPDispatchRuleRequest(
            rule=SIPDispatchRule(
                dispatch_rule_direct=SIPDispatchRuleDirect(
                    room_name=room_name,
                ),
            ),
            trunk_ids=[trunk_id],
            hide_phone_number=False,
            name=rule_name,
        )
    )
    return resp.sip_dispatch_rule_id, True


async def _delete_sip_dispatch_rule(api_client: api.LiveKitAPI, rule_id: str) -> None:
    await api_client.sip.delete_sip_dispatch_rule(
        DeleteSIPDispatchRuleRequest(sip_dispatch_rule_id=rule_id)
    )


async def _collect_provider_evidence(
    *,
    config: ProviderEvidenceConfig,
    transport: TelephonyTransport,
    run_id: str,
    test_case_id: str,
    case_directory: Path,
    started_at: datetime,
    target: _TargetParticipant | None,
    provider_call_id_hint: str | None = None,
    provider_api_key: str | None = None,
    provider_api_base_url: str | None = None,
    termination_source: str | None = None,
) -> tuple[EvidenceSourceSummary | None, list[ArtifactManifestEntry]]:
    call_id_hint = provider_call_id_hint
    caller_phone = transport.sip_number if transport.kind == "sip_outbound" else None
    callee_phone = transport.sip_call_to if transport.kind == "sip_outbound" else None
    if target is not None:
        if call_id_hint is None and config.participant_attribute:
            call_id_hint = target.attributes.get(config.participant_attribute)
        caller_phone = caller_phone or (
            target.attributes.get("sip.from")
            or target.attributes.get("sip.fromUser")
            or target.attributes.get("sip.callerNumber")
        )
        callee_phone = callee_phone or (
            target.attributes.get("sip.to")
            or target.attributes.get("sip.toUser")
            or target.attributes.get("sip.calledNumber")
        )
    context = EvidenceContext(
        run_id=run_id,
        test_case_id=test_case_id,
        case_directory=case_directory,
        started_at=started_at,
        call_id_hint=call_id_hint,
        caller_phone=caller_phone,
        callee_phone=callee_phone,
        termination_source=termination_source,
    )
    try:
        if config.provider == "vapi":
            adapter = VapiEvidenceSource(
                config,
                api_key=provider_api_key,
                api_base_url=provider_api_base_url,
            )
        elif config.provider == "retell":
            adapter = RetellEvidenceSource(
                config,
                api_key=provider_api_key,
                api_base_url=provider_api_base_url,
            )
        else:
            raise ProviderConfigError(
                f"unsupported_provider_evidence: {config.provider}"
            )
    except ProviderConfigError as exc:
        summary = EvidenceSourceSummary(
            source_id=f"{config.provider}:unconfigured",
            adapter=config.provider,
            evidence_class=_EVIDENCE_PROVIDER_REPORTED,
            available=False,
            redactions=["auth", "phone_e164"],
            metadata={"provider": config.provider, "reason": str(exc)},
        )
        return summary, []
    try:
        await adapter.connect(context)
        result: ProviderFetchResult = await adapter.fetch_final()
    except Exception as exc:  # noqa: BLE001 — provider failures are first-class evidence
        logger.warning(
            "Provider evidence adapter failed",
            exc_info=redacted_exc_info(exc),
            extra={
                "provider": config.provider,
                "run_id": run_id,
                "test_case_id": test_case_id,
            },
        )
        summary = EvidenceSourceSummary(
            source_id=f"{config.provider}:error",
            adapter=config.provider,
            evidence_class=_EVIDENCE_PROVIDER_REPORTED,
            available=False,
            redactions=["auth", "phone_e164"],
            metadata={
                "provider": config.provider,
                "reason": "adapter_exception",
                "exception_type": type(exc).__name__,
            },
        )
        return summary, []
    finally:
        try:
            await adapter.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "Provider evidence adapter close failed",
                extra={
                    "provider": config.provider,
                    "exception_type": type(exc).__name__,
                },
            )
    return result.summary, result.artifacts


# Import lazily to avoid a module-import cycle with ProviderConfigError above.
from fi.simulate.evidence.base import EvidenceClass as _EvidenceClass  # noqa: E402

_EVIDENCE_PROVIDER_REPORTED = _EvidenceClass.PROVIDER_REPORTED
