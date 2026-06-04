
from typing import AsyncIterable, Optional, List
import asyncio
import os
import contextlib
import wave
import numpy as np
try:
    from livekit.agents import stt, tts, llm, vad, Agent, AgentSession, function_tool
    from livekit.agents.voice.room_io import RoomInputOptions, RoomOutputOptions
    from livekit.plugins import openai, silero
    from livekit import rtc
    from livekit.api import AccessToken, VideoGrants
    from livekit.agents.voice import ModelSettings
    from livekit.agents.voice.io import TimedString
except ImportError as e:
    raise ImportError(
        "LiveKit SDK is not installed (or incompatible version). "
        "Install it to use LiveKit/local mode."
    ) from e

from fi.simulate.agent.definition import AgentDefinition, SimulatorAgentDefinition
from fi.simulate.simulation.models import Scenario, Persona, TestReport, TestCaseResult
from fi.simulate.simulation.generator import ScenarioGenerator
from fi.simulate.recording.room_recorder import RoomRecorder
from fi.simulate.simulation.engines.base import BaseEngine

class _TestRunnerAgent(Agent):
    """
    An agent used by the TestRunner to simulate a customer.
    """
    def __init__(self, persona: Persona, **kwargs):
        super().__init__(**kwargs)
        self._persona = persona
        self._session_future = asyncio.Future()

    @function_tool()
    async def end_call(self) -> None:
        # Simulated customer ends the call when satisfied
        self.session.say("Thanks, that's all. Goodbye.")
        await asyncio.sleep(0.2)
        self.session.shutdown()

    async def run(self, room: rtc.Room):
        # Coalesce None simulator values to safe defaults
        _min_ep = getattr(self, "min_endpointing_delay", None)
        _max_ep = getattr(self, "max_endpointing_delay", None)

        session = AgentSession(
            stt=self.stt,
            llm=self.llm,
            tts=self.tts,
            vad=None,
            allow_interruptions=True,
            # Stable endpointing delays
            min_endpointing_delay=(_min_ep if _min_ep is not None else 0.4),
            max_endpointing_delay=(_max_ep if _max_ep is not None else 2.2),
            # Use STT-based turn detection for stability
            turn_detection=getattr(self, "turn_detection", "stt"),
            preemptive_generation=False,
            discard_audio_if_uninterruptible=True,
            min_interruption_duration=0.3,
        )
        self._session_future.set_result(session)
        await session.start(
            self,
            room=room,
            room_input_options=RoomInputOptions(
                delete_room_on_close=False,
                participant_kinds=[
                    rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD,
                    getattr(rtc.ParticipantKind, "PARTICIPANT_KIND_AGENT", rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD),
                ],
                pre_connect_audio=True,
                pre_connect_audio_timeout=3.0,
            ),
            room_output_options=RoomOutputOptions(transcription_enabled=False),
        )
        try:
            # Give I/O a brief moment to publish tracks before first TTS
            import asyncio as _asyncio
            await _asyncio.sleep(0.6)
            name = str(self._persona.persona.get("name", "customer"))
        except Exception:
            name = "customer"
        situation = self._persona.situation or ""
        opener = f"Hi, I'm {name}. {situation}".strip()
        print(f"Opener: {opener}")
        if opener:
            session.say(opener)
        # Reinforce numeric endpointing on the live session
        try:
            session.update_options(
                min_endpointing_delay=(_min_ep if _min_ep is not None else 0.4),
                max_endpointing_delay=(_max_ep if _max_ep is not None else 2.2),
            )
        except Exception:
            pass

    async def get_session(self) -> AgentSession:
        return await self._session_future

    # Use default stt_node; session-level endpointing is configured in AgentSession

    async def transcription_node(
        self,
        text: AsyncIterable[str | TimedString],
        model_settings: ModelSettings,
    ):
        async for chunk in text:
            if isinstance(chunk, TimedString):
                print(f"ASR: '{chunk}' ({getattr(chunk, 'start_time', None)} - {getattr(chunk, 'end_time', None)})")
            else:
                print(f"LLM: {chunk}")
            yield chunk

class LiveKitEngine(BaseEngine):
    """
    Execution engine that uses LiveKit to connect a simulated customer agent
    to a deployed voice agent.
    """

    async def run(
        self,
        agent_definition: Optional[AgentDefinition] = None,
        scenario: Optional[Scenario] = None,
        simulator: Optional[SimulatorAgentDefinition] = None,
        num_scenarios: int = 1,
        topic: str | None = None,
        record_audio: bool = False,
        recorder_sample_rate: int = 8000,
        recorder_join_delay: float = 0.2,
        min_turn_messages: int = 8,
        max_seconds: float = 45.0,
        **kwargs
    ) -> TestReport:
        if agent_definition is None:
            raise ValueError("LiveKitEngine requires 'agent_definition' to be provided.")

        # If no scenario provided, generate personas using generator
        if scenario is None:
            gen = ScenarioGenerator(agent_definition)
            # Build a simple topic from provided context if none given
            if topic is None:
                agent_ctx = agent_definition.system_prompt
                sim_ctx = simulator.instructions if simulator and simulator.instructions else ""
                topic = (sim_ctx or agent_ctx or "customer support scenarios").strip()
            personas = await gen.generate(topic=topic, num_personas=num_scenarios)
            scenario = Scenario(name="Generated Scenario", dataset=personas)

        report = TestReport()
        for persona in scenario.dataset:
            print(f"Running test case for persona: {persona.persona.get('name', 'Unknown')}")
            
            transcript, audio_in, audio_out, audio_combined = await self._run_single_test_case(
                agent_definition,
                persona,
                simulator,
                record_audio=record_audio,
                recorder_sample_rate=recorder_sample_rate,
                recorder_join_delay=recorder_join_delay,
                min_turn_messages=min_turn_messages,
                max_seconds=max_seconds,
            )
            
            report.results.append(
                TestCaseResult(
                    persona=persona,
                    transcript=transcript,
                    audio_input_path=audio_in,
                    audio_output_path=audio_out,
                    audio_combined_path=audio_combined,
                )
            )
            
        return report

    async def _run_single_test_case(
        self,
        agent_definition: AgentDefinition,
        persona: Persona,
        simulator: SimulatorAgentDefinition | None,
        *,
        record_audio: bool = False,
        recorder_sample_rate: int = 8000,
        recorder_join_delay: float = 0.2,
        min_turn_messages: int = 8,
        max_seconds: float = 45.0,
    ) -> tuple[str, str | None, str | None, str | None]:
        livekit_api_key = os.environ.get("LIVEKIT_API_KEY")
        livekit_api_secret = os.environ.get("LIVEKIT_API_SECRET")

        if not all([livekit_api_key, livekit_api_secret]):
            raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set.")

        customer_room = rtc.Room()
        
        try:
            token = (
                AccessToken(livekit_api_key, livekit_api_secret)
                .with_identity(persona.persona.get("name", "customer"))
                .with_grants(VideoGrants(room_join=True, room=agent_definition.room_name))
                .to_jwt()
            )

            # Join the simulator as an Agent participant so it shows as Agent
            # in LiveKit and benefits from agent-specific behavior. Fall back if unsupported.
            try:
                opts = rtc.ConnectOptions()
                # ParticipantKind may not exist on older SDKs
                if hasattr(rtc, "ParticipantKind"):
                    opts.participant_kind = rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                await customer_room.connect(str(agent_definition.url), token, opts)
            except Exception:
                await customer_room.connect(str(agent_definition.url), token)
            print(f"✓ Customer '{persona.persona.get('name')}' connected to room")
            
            customer_agent = self._create_customer_agent(persona, simulator)

            # Optionally start a separate recorder participant to capture all audio
            recorder: RoomRecorder | None = None
            if record_audio:
                if livekit_api_key and livekit_api_secret:
                    recorder = RoomRecorder(
                        url=str(agent_definition.url),
                        api_key=livekit_api_key,
                        api_secret=livekit_api_secret,
                        room_name=agent_definition.room_name,
                        sample_rate=recorder_sample_rate,
                        join_delay_s=recorder_join_delay,
                    )
                    # Join immediately to capture early utterances
                    await recorder.start()

            # Start the agent in a background task
            session_task = asyncio.create_task(
                customer_agent.run(room=customer_room)
            )

            # Wait for the session to be created
            customer_session = await customer_agent.get_session()

            # Stream transcripts and messages in real-time
            def _on_user_input_transcribed(ev):
                try:
                    suffix = "" if getattr(ev, "is_final", False) else "…"
                    print(f"ASR(user): {getattr(ev, 'transcript', '')}{suffix}")
                except Exception:
                    pass

            def _on_conversation_item_added(ev):
                try:
                    item = getattr(ev, "item", None)
                    role = getattr(item, "role", None)
                    text = getattr(item, "text_content", None)
                    if role and text:
                        print(f"MSG({role}): {text}")
                except Exception:
                    pass

            customer_session.on("user_input_transcribed", _on_user_input_transcribed)
            customer_session.on("conversation_item_added", _on_conversation_item_added)

            # Wait for natural session close (tool-triggered or remote hangup), with hard timeout
            closed = asyncio.Event()
            def _on_close(ev):
                closed.set()
            customer_session.on("close", _on_close)

            try:
                await asyncio.wait_for(closed.wait(), timeout=max_seconds)
            except asyncio.TimeoutError:
                with contextlib.suppress(Exception):
                    customer_session.shutdown()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(closed.wait(), timeout=5)
            
            # Get transcript from history (dedupe partial repeats)
            if customer_session:
                lines: list[str] = []
                last_by_role: dict[str, str] = {}
                for item in customer_session.history.items:
                    item_type = getattr(item, "type", None)
                    role = getattr(item, "role", None)
                    text = getattr(item, "text_content", None)
                    if item_type == "message" and text is not None and role is not None:
                        prev = last_by_role.get(role)
                        # Deduplicate streaming partials by collapsing near-duplicates
                        if prev and (text.startswith(prev) or prev.startswith(text)):
                            # Replace last line for this role
                            for i in range(len(lines) - 1, -1, -1):
                                if lines[i].startswith(f"{role}:"):
                                    lines[i] = f"{role}: {text}"
                                    break
                        else:
                            lines.append(f"{role}: {text}")
                        last_by_role[role] = text
                transcript = "\n".join(lines)
            else:
                transcript = "Error: Agent session was not created."
            
        except Exception as e:
            print(f"Error during test case: {e}")
            return (f"Error: {e}", None, None, None)
        finally:
            # Support both property and method across versions
            try:
                if getattr(customer_room, "isconnected", False):
                    if callable(customer_room.isconnected):
                        if customer_room.isconnected():
                            await customer_room.disconnect()
                    elif customer_room.isconnected:
                        await customer_room.disconnect()
                elif getattr(customer_room, "is_connected", False):
                    if customer_room.is_connected:
                        await customer_room.disconnect()
            except Exception:
                pass
                print(f"✓ Customer disconnected")
            # Stop recorder if running
            if recorder is not None:
                with contextlib.suppress(Exception):
                    await recorder.aclose()

        # Resolve per-persona input/output recordings and build combined WAV
        def _find_paths_for_identity(room_name: str, identity: str) -> list[str]:
            try:
                base = os.path.join("recordings", f"{room_name}-{identity}-track-")
                # listdir and filter to avoid glob deps
                files = [os.path.join("recordings", f) for f in os.listdir("recordings") if f.startswith(f"{room_name}-{identity}-track-") and f.endswith(".wav")]
                return sorted(files, key=lambda p: os.path.getmtime(p), reverse=True)
            except Exception:
                return []

        def _pick_best(paths: list[str]) -> str | None:
            if not paths:
                return None
            return max(paths, key=lambda p: (os.path.getsize(p), os.path.getmtime(p)))

        persona_name = str(persona.persona.get("name", "customer"))
        in_candidates = _find_paths_for_identity(agent_definition.room_name, persona_name)

        # Auto-pick a likely agent identity (prefer cloud/local agent-looking ids)
        def _list_identities(room_name: str) -> list[str]:
            try:
                ids: set[str] = set()
                for f in os.listdir("recordings"):
                    if not f.endswith(".wav"):
                        continue
                    if not f.startswith(f"{room_name}-"):
                        continue
                    rest = f[len(room_name)+1:]
                    parts = rest.split("-track-")
                    if len(parts) != 2:
                        continue
                    identity = parts[0]
                    ids.add(identity)
                return sorted(ids)
            except Exception:
                return []

        identities = _list_identities(agent_definition.room_name)
        candidate_agent_ids = [i for i in identities if i not in {persona_name, "recorder"}]

        def _agent_rank(i: str) -> tuple[int, float]:
            score = 0
            if i.startswith("agent-"):
                score += 2
            if i == "support-agent":
                score += 3
            best = _pick_best(_find_paths_for_identity(agent_definition.room_name, i))
            size = os.path.getsize(best) if best and os.path.exists(best) else 0
            return (score, float(size))

        chosen_agent_id: str | None = None
        if candidate_agent_ids:
            chosen_agent_id = max(candidate_agent_ids, key=_agent_rank)
        out_candidates = _find_paths_for_identity(agent_definition.room_name, chosen_agent_id) if chosen_agent_id else []
        audio_in = _pick_best(in_candidates)
        audio_out = _pick_best(out_candidates)

        audio_combined: str | None = None
        try:
            # Overlay all recorder tracks for this room (covers any agent identity)
            def _find_all_room_tracks(room_name: str) -> list[str]:
                try:
                    files = [os.path.join("recordings", f) for f in os.listdir("recordings")
                             if f.startswith(f"{room_name}-") and f.endswith(".wav") and "-combined" not in f]
                    return sorted(files, key=lambda p: os.path.getmtime(p))
                except Exception:
                    return []

            mix_inputs = _find_all_room_tracks(agent_definition.room_name)
            if mix_inputs:
                os.makedirs("recordings", exist_ok=True)
                audio_combined = os.path.join("recordings", f"{agent_definition.room_name}-{persona_name}-combined.wav")
                arrays: list[np.ndarray] = []
                max_len = 0
                for p in mix_inputs:
                    with wave.open(p, "rb") as wf:
                        frames = wf.readframes(wf.getnframes())
                        arr = np.frombuffer(frames, dtype=np.int16)
                        arrays.append(arr)
                        if arr.shape[0] > max_len:
                            max_len = arr.shape[0]
                if arrays and max_len > 0:
                    mix = np.zeros(max_len, dtype=np.int32)
                    for arr in arrays:
                        if arr.shape[0] < max_len:
                            pad = np.zeros(max_len - arr.shape[0], dtype=arr.dtype)
                            arr = np.concatenate([arr, pad])
                        mix += arr.astype(np.int32)
                    mix = np.clip(mix, -32768, 32767).astype(np.int16)
                    with wave.open(audio_combined, "wb") as wf_out:
                        wf_out.setnchannels(1)
                        wf_out.setsampwidth(2)
                        wf_out.setframerate(8000)
                        wf_out.writeframes(mix.tobytes())
                    print(f"✓ Combined conversation saved: {audio_combined}")
        except Exception as e:
            print(f"Combined mix failed: {e}")

        return (transcript, audio_in, audio_out, audio_combined)

    def _create_customer_agent(self, persona: Persona, simulator: SimulatorAgentDefinition | None) -> _TestRunnerAgent:
        customer_prompt = self._create_customer_prompt(persona)

        # Build components from simulator config or use sensible defaults
        if simulator is None:
            stt_model = openai.STT(language="en")
            llm_model = openai.LLM(model="gpt-4o-mini", temperature=0.6)
            tts_model = openai.TTS(model="tts-1", voice="alloy")
            vad_model = silero.VAD.load()
            instructions = customer_prompt
            allow_interruptions = None
            min_ep = None
            max_ep = None
            use_aligned = None
        else:
            stt_model = openai.STT(language=simulator.stt.language)
            llm_model = openai.LLM(model=simulator.llm.model, temperature=simulator.llm.temperature)
            tts_model = openai.TTS(model=simulator.tts.model, voice=simulator.tts.voice)
            vad_model = silero.VAD.load()
            # Merge simulator instructions with persona-derived prompt so both are applied
            if simulator.instructions:
                instructions = f"{simulator.instructions}\n\n{customer_prompt}"
            else:
                instructions = customer_prompt
            allow_interruptions = simulator.allow_interruptions
            min_ep = simulator.min_endpointing_delay
            max_ep = simulator.max_endpointing_delay
            use_aligned = simulator.use_tts_aligned_transcript

        agent = _TestRunnerAgent(
            persona=persona,
            stt=stt_model,
            llm=llm_model,
            tts=tts_model,
            vad=vad_model,
            instructions=instructions,
            allow_interruptions=allow_interruptions,
            min_endpointing_delay=min_ep,
            max_endpointing_delay=max_ep,
            use_tts_aligned_transcript=use_aligned,
        )
        return agent

    def _create_customer_prompt(self, persona: Persona) -> str:
        return (
            "You are a realistic customer in a support call. "
            f"Profile: {persona.persona}. "
            f"Situation: {persona.situation}. "
            f"Goal: {persona.outcome}. "
            "Have a natural back-and-forth conversation, asking clarifying questions. "
            "Keep the conversation going for at least 6 turns unless the problem is fully solved. "
            "When you are satisfied and done, call the `end_call` tool to hang up. "
            "Use short, spoken-style sentences."
        )

