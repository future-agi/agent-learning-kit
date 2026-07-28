from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterable

try:
    from livekit import api, rtc
    from livekit.agents import Agent, AgentSession, function_tool
    from livekit.agents.voice import ModelSettings
    from livekit.agents.voice.io import TimedString
    from livekit.agents.voice.room_io import RoomInputOptions, RoomOutputOptions
    from livekit.plugins import silero
    from livekit.api import AccessToken, VideoGrants
except ImportError as exc:
    raise ImportError(
        "LiveKit mode requires the 'livekit' optional dependency"
    ) from exc

from fi.simulate._logging import redacted_exc_info
from fi.simulate.agent.definition import (
    AgentDefinition,
    LLMConfig,
    SimulatorAgentDefinition,
    STTConfig,
    TelephonyTransport,
    TTSConfig,
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


class _TestRunnerAgent(Agent):
    def __init__(self, persona: Persona, **kwargs):
        super().__init__(**kwargs)
        self._persona = persona
        self._session: AgentSession | None = None

    @function_tool()
    async def end_call(self) -> None:
        await asyncio.sleep(0.2)
        self.session.shutdown()

    @property
    def started_session(self) -> AgentSession | None:
        return self._session

    async def start_session(self, room: rtc.Room) -> AgentSession:
        configured_min = getattr(self, "min_endpointing_delay", None)
        configured_max = getattr(self, "max_endpointing_delay", None)
        min_endpointing_delay = (
            configured_min if isinstance(configured_min, (int, float)) else 0.4
        )
        max_endpointing_delay = (
            configured_max if isinstance(configured_max, (int, float)) else 2.2
        )
        session = AgentSession(
            stt=self.stt,
            llm=self.llm,
            tts=self.tts,
            vad=None,
            allow_interruptions=True,
            min_endpointing_delay=min_endpointing_delay,
            max_endpointing_delay=max_endpointing_delay,
            turn_detection=getattr(self, "turn_detection", "stt"),
            preemptive_generation=False,
            discard_audio_if_uninterruptible=True,
            min_interruption_duration=0.3,
        )
        self._session = session
        await session.start(
            self,
            room=room,
            room_input_options=RoomInputOptions(
                delete_room_on_close=False,
                participant_kinds=[
                    rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
                    getattr(
                        rtc.ParticipantKind,
                        "PARTICIPANT_KIND_AGENT",
                        rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
                    ),
                    rtc.ParticipantKind.PARTICIPANT_KIND_SIP,
                ],
                pre_connect_audio=True,
                pre_connect_audio_timeout=3.0,
            ),
            room_output_options=RoomOutputOptions(transcription_enabled=False),
        )
        session.update_options(
            min_endpointing_delay=min_endpointing_delay,
            max_endpointing_delay=max_endpointing_delay,
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
        recording_root: str | Path = "recordings",
        run_id: str | None = None,
        **kwargs,
    ) -> TestReport:
        if agent_definition is None:
            raise ValueError("LiveKitEngine requires 'agent_definition'.")
        if conversation_direction not in {"simulator_first", "agent_first"}:
            raise ValueError("conversation_direction must be simulator_first or agent_first")
        if scenario is None:
            generator = ScenarioGenerator(agent_definition)
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
        if (
            agent_definition.room_mode == "external"
            and len(scenario.dataset) > 1
            and not _has_room_template(agent_definition.room_name)
        ):
            raise ValueError(
                "external_room_template_required: concurrent-safe multi-case runs "
                "need {run_id}, {test_case_id}, or {index} in room_name"
            )
        current_run_id = run_id or new_run_id()
        report = TestReport()
        for index, persona in enumerate(scenario.dataset):
            persona_ref = persona.version or persona.content_hash()
            test_case_id = derive_test_case_id(
                current_run_id,
                persona_ref,
                index,
            )
            room_name = _resolve_room_name(
                agent_definition,
                run_id=current_run_id,
                test_case_id=test_case_id,
                index=index,
            )
            case_directory = Path(recording_root) / current_run_id / test_case_id
            outcome = await self._run_single_test_case(
                agent_definition,
                persona,
                simulator,
                run_id=current_run_id,
                test_case_id=test_case_id,
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
            )
            metadata = {
                "engine": "livekit",
                "run_id": current_run_id,
                "test_case_id": test_case_id,
                "status": outcome.status.value,
                "room_name": room_name,
                "room_mode": agent_definition.room_mode,
                **outcome.metadata,
            }
            if outcome.failure is not None:
                metadata["failure"] = outcome.failure.model_dump(
                    mode="json",
                    exclude_none=True,
                )
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
        persona: Persona,
        simulator: SimulatorAgentDefinition | None,
        *,
        run_id: str,
        test_case_id: str,
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
    ) -> _CaseOutcome:
        api_key = os.environ.get("LIVEKIT_API_KEY")
        api_secret = os.environ.get("LIVEKIT_API_SECRET")
        if not api_key or not api_secret:
            return _failure_outcome(
                TestCaseStatus.FAILED,
                FailureStage.PREPARING,
                "livekit_credentials_missing",
                "LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required",
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
        managed_room_created = False
        room_connected = False
        cleanup_errors: list[str] = []
        outcome: _CaseOutcome | None = None
        transport = agent_definition.transport or TelephonyTransport()
        effective_readiness_timeout = (
            transport.readiness_timeout_seconds
            if transport.kind == "sip_inbound"
            and transport.readiness_timeout_seconds is not None
            else readiness_timeout
        )
        try:
            if agent_definition.room_mode == "managed":
                api_client = api.LiveKitAPI(
                    _api_url(str(agent_definition.url)),
                    api_key,
                    api_secret,
                )
                await asyncio.wait_for(
                    api_client.room.create_room(api.CreateRoomRequest(name=room_name)),
                    timeout=connect_timeout,
                )
                managed_room_created = True
                if transport.kind == "webrtc":
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
                                        "target_instructions": agent_definition.system_prompt,
                                    },
                                    sort_keys=True,
                                ),
                            )
                        ),
                        timeout=connect_timeout,
                    )
                elif transport.kind == "sip_outbound":
                    identity_template = (
                        transport.participant_identity
                        or "sip-caller-{test_case_id}"
                    )
                    participant_identity = identity_template.format(
                        test_case_id=test_case_id, run_id=run_id
                    )
                    try:
                        await asyncio.wait_for(
                            api_client.sip.create_sip_participant(
                                api.CreateSIPParticipantRequest(
                                    sip_trunk_id=transport.sip_trunk_id,
                                    sip_number=transport.sip_number,
                                    sip_call_to=transport.sip_call_to,
                                    room_name=room_name,
                                    participant_identity=participant_identity,
                                    wait_until_answered=True,
                                    play_ringtone=True,
                                )
                            ),
                            timeout=connect_timeout,
                        )
                    except asyncio.TimeoutError:
                        raise
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
                            details={"exception_type": type(exc).__name__},
                        )
                        return outcome
            token = (
                AccessToken(api_key, api_secret)
                .with_identity(simulator_identity)
                .with_grants(VideoGrants(room_join=True, room=room_name))
                .to_jwt()
            )
            await asyncio.wait_for(
                room.connect(str(agent_definition.url), token),
                timeout=connect_timeout,
            )
            room_connected = True
            if record_audio:
                recorder = RoomRecorder(
                    url=str(agent_definition.url),
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
            )
            session = await asyncio.wait_for(
                customer_agent.start_session(room),
                timeout=connect_timeout,
            )
            target = await _wait_for_target_audio(
                room,
                excluded_identities={simulator_identity, recorder_identity},
                target_identity=agent_definition.target_participant_identity,
                timeout=effective_readiness_timeout,
            )
            if conversation_direction == "simulator_first":
                customer_agent.open_conversation()
            stop_reason = await _wait_for_conversation_end(
                room,
                session,
                target_identity=target.identity,
                min_turn_messages=min_turn_messages,
                timeout=max_seconds,
            )
            messages = _session_messages(session)
            transcript = "\n".join(
                f"{message['role']}: {message['content']}" for message in messages
            )
            if stop_reason == "timeout":
                outcome = _failure_outcome(
                    TestCaseStatus.TIMED_OUT,
                    FailureStage.RUNNING,
                    "conversation_timeout",
                    "Conversation exceeded its deadline",
                    transcript=transcript,
                    messages=messages,
                    retryable=True,
                )
            elif (
                stop_reason == "target_disconnected"
                and len(messages) < min_turn_messages
            ):
                outcome = _failure_outcome(
                    TestCaseStatus.FAILED,
                    FailureStage.RUNNING,
                    "target_disconnected",
                    "Target agent disconnected before the conversation completed",
                    transcript=transcript,
                    messages=messages,
                    retryable=True,
                )
            else:
                outcome = _CaseOutcome(
                    status=TestCaseStatus.COMPLETED,
                    transcript=transcript,
                    messages=messages,
                    metadata={"stop_reason": stop_reason},
                )
        except asyncio.TimeoutError:
            stage = (
                FailureStage.READINESS
                if session is not None and target is None
                else FailureStage.PREPARING
            )
            if (
                stage == FailureStage.READINESS
                and transport.kind == "sip_inbound"
            ):
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
                    close_session = getattr(session_to_close, "aclose", None)
                    if close_session is not None:
                        await asyncio.wait_for(
                            close_session(),
                            timeout=cleanup_timeout,
                        )
                    else:
                        session_to_close.shutdown(drain=False)
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
            if api_client is not None and managed_room_created:
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
                "cleanup_status": "failed" if cleanup_errors else "completed",
                "cleanup_errors": cleanup_errors,
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
    ) -> tuple[_TestRunnerAgent, LiveKitModels]:
        customer_prompt = build_voice_simulator_prompt(
            persona,
            call_type=call_type,
            agent_name=agent_name,
        )
        if simulator is None:
            voice_provider = os.environ.get(
                "SIMULATOR_VOICE_PROVIDER", "openai"
            ).lower()
            llm_config = LLMConfig(
                model=os.environ.get("SIMULATOR_LLM_MODEL", "gpt-4o-mini"),
                temperature=0.6,
            )
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
            instructions = simulator.instructions or customer_prompt
            allow_interruptions = simulator.allow_interruptions
            min_endpointing_delay = simulator.min_endpointing_delay
            max_endpointing_delay = simulator.max_endpointing_delay
            use_aligned_transcript = simulator.use_tts_aligned_transcript
        models = await build_livekit_models(
            llm_config=llm_config,
            stt_config=stt_config,
            tts_config=tts_config,
        )
        agent = _TestRunnerAgent(
            persona=persona,
            stt=models.stt,
            llm=models.llm,
            tts=models.tts,
            vad=silero.VAD.load(),
            instructions=instructions,
            allow_interruptions=allow_interruptions,
            min_endpointing_delay=min_endpointing_delay,
            max_endpointing_delay=max_endpointing_delay,
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
            candidates.append(
                (
                    priority,
                    _TargetParticipant(
                        identity=identity,
                        sid=str(participant.sid),
                        audio_track_sid=str(publication.sid),
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
    target_identity: str,
    min_turn_messages: int,
    timeout: float,
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
    close_task = asyncio.create_task(closed.wait())
    disconnect_task = asyncio.create_task(target_disconnected.wait())
    minimum_task = asyncio.create_task(
        _wait_for_minimum_messages(session, min_turn_messages)
    )
    try:
        done, pending = await asyncio.wait(
            {close_task, disconnect_task, minimum_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if not done:
            session.shutdown(drain=False)
            return "timeout"
        if disconnect_task in done:
            session.shutdown(drain=False)
            return "target_disconnected"
        if minimum_task in done:
            return "minimum_messages_reached"
        return "session_closed"
    finally:
        _remove_room_listener(
            room,
            "participant_disconnected",
            on_participant_disconnected,
        )


async def _wait_for_minimum_messages(
    session: AgentSession,
    min_turn_messages: int,
) -> None:
    while len(_session_messages(session)) < min_turn_messages:
        await asyncio.sleep(0.1)


def _session_messages(session: AgentSession) -> list[dict[str, str]]:
    messages = []
    for item in session.history.items:
        if getattr(item, "type", None) != "message":
            continue
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if role is None or text is None:
            continue
        current = {"role": str(role), "content": str(text)}
        if messages and messages[-1]["role"] == current["role"]:
            previous = messages[-1]["content"]
            if current["content"].startswith(previous) or previous.startswith(
                current["content"]
            ):
                messages[-1] = current
                continue
        messages.append(current)
    return messages


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


def _resolve_room_name(
    agent_definition: AgentDefinition,
    *,
    run_id: str,
    test_case_id: str,
    index: int,
) -> str:
    if agent_definition.room_mode == "external":
        return agent_definition.room_name.format(
            run_id=run_id,
            test_case_id=test_case_id,
            index=index,
        )
    prefix = _SAFE_ROOM.sub("-", agent_definition.room_name).strip("-._")
    return f"{prefix[:48]}-{test_case_id[-12:]}"


def _has_room_template(room_name: str) -> bool:
    return any(
        marker in room_name
        for marker in ("{run_id}", "{test_case_id}", "{index}")
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
