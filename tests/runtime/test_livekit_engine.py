from __future__ import annotations

import asyncio
import json
import logging
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("livekit")

from fi.simulate.agent.definition import AgentDefinition
from fi.simulate.recording.room_recorder import mix_recordings
from fi.simulate.runtime import TestCaseStatus as CaseStatus
from fi.simulate.simulation.engines import livekit
from fi.simulate.simulation.engines.livekit import LiveKitEngine
from fi.simulate.simulation import livekit_models
from fi.simulate.simulation.models import Persona, Scenario


def _agent(**updates) -> AgentDefinition:
    values = {
        "name": "support-agent",
        "url": "wss://livekit.example.com",
        "room_name": "support-room",
        "system_prompt": "Help the caller.",
    }
    values.update(updates)
    return AgentDefinition(**values)


def _scenario(count: int = 1) -> Scenario:
    return Scenario(
        name="voice",
        dataset=[
            Persona(
                persona={"name": f"Caller {index}"},
                situation="I need help.",
                outcome="The issue is resolved.",
            )
            for index in range(count)
        ],
    )


def _write_wav(path: Path, samples: np.ndarray, sample_rate: int = 8000) -> None:
    with wave.open(str(path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(samples.astype(np.int16).tobytes())


def test_managed_room_names_are_unique_per_run_and_case() -> None:
    agent = _agent(room_mode="managed", agent_name="support-agent")

    first = livekit._resolve_room_name(
        agent,
        run_id="run_a",
        test_case_id="case_aaaaaaaaaaaa",
        index=0,
        invocation_id="invocation-a",
    )
    second = livekit._resolve_room_name(
        agent,
        run_id="run_a",
        test_case_id="case_bbbbbbbbbbbb",
        index=1,
        invocation_id="invocation-a",
    )

    assert first != second
    assert first.startswith("support-room-")


def test_external_multi_case_run_requires_room_template() -> None:
    with pytest.raises(ValueError, match="external_room_template_required"):
        asyncio.run(
            LiveKitEngine().run(
                agent_definition=_agent(),
                scenario=_scenario(2),
            )
        )


def test_missing_credentials_is_typed_failure_not_transcript(monkeypatch) -> None:
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)

    report = asyncio.run(
        LiveKitEngine().run(
            agent_definition=_agent(),
            scenario=_scenario(),
            run_id="run_missing_credentials",
        )
    )

    result = report.results[0]
    assert result.transcript == ""
    assert result.metadata["status"] == CaseStatus.FAILED.value
    assert result.metadata["failure"]["code"] == "livekit_credentials_missing"


def test_default_customer_agent_supports_elevenlabs(monkeypatch) -> None:
    monkeypatch.setenv("SIMULATOR_VOICE_PROVIDER", "elevenlabs")
    monkeypatch.setenv("SIMULATOR_LLM_MODEL", "gpt-5.4-mini")
    monkeypatch.setenv("SIMULATOR_STT_MODEL", "scribe_v2_realtime")
    monkeypatch.setenv("SIMULATOR_TTS_MODEL", "eleven_flash_v2_5")
    monkeypatch.setenv("SIMULATOR_TTS_VOICE_ID", "voice-id")
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.delenv("ELEVEN_API_KEY", raising=False)

    fake_openai = SimpleNamespace(
        LLM=lambda **kw: ("llm", kw),
        STT=lambda **kw: ("stt", kw),
        TTS=lambda **kw: ("tts", kw),
    )
    fake_elevenlabs = SimpleNamespace(
        STT=lambda **kw: ("stt", kw), TTS=lambda **kw: ("tts", kw)
    )

    def _fake_import(name):
        return {"openai": fake_openai, "elevenlabs": fake_elevenlabs}[name]

    monkeypatch.setattr(livekit_models, "_import_plugin", _fake_import)
    monkeypatch.setattr(
        livekit.silero,
        "VAD",
        SimpleNamespace(load=lambda: "vad"),
    )
    monkeypatch.setattr(
        livekit,
        "_TestRunnerAgent",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    agent, models = asyncio.run(
        LiveKitEngine()._create_customer_agent(
            _scenario().dataset[0],
            None,
        )
    )

    assert agent.llm == (
        "llm",
        {"model": "gpt-5.4-mini", "temperature": 0.6},
    )
    assert agent.stt == (
        "stt",
        {
            "api_key": "test-key",
            "http_session": models.http_session,
            "model_id": "scribe_v2_realtime",
            "server_vad": {
                "vad_silence_threshold_secs": 0.8,
                "vad_threshold": 0.4,
                "min_speech_duration_ms": 100,
                "min_silence_duration_ms": 500,
            },
        },
    )
    assert agent.tts == (
        "tts",
        {
            "api_key": "test-key",
            "http_session": models.http_session,
            "model": "eleven_flash_v2_5",
            "voice_id": "voice-id",
        },
    )
    assert models.http_session is not None
    asyncio.run(models.aclose())


def test_two_ten_case_suites_do_not_share_room_names(monkeypatch) -> None:
    monkeypatch.delenv("LIVEKIT_API_KEY", raising=False)
    monkeypatch.delenv("LIVEKIT_API_SECRET", raising=False)
    engine = LiveKitEngine()
    agent = _agent(room_mode="managed", agent_name="support-agent")

    async def run_suites():
        return await asyncio.gather(
            engine.run(
                agent_definition=agent,
                scenario=_scenario(10),
                run_id="run_a",
            ),
            engine.run(
                agent_definition=agent,
                scenario=_scenario(10),
                run_id="run_b",
            ),
        )

    first, second = asyncio.run(run_suites())
    first_rooms = {result.metadata["room_name"] for result in first.results}
    second_rooms = {result.metadata["room_name"] for result in second.results}

    assert len(first_rooms) == 10
    assert len(second_rooms) == 10
    assert first_rooms.isdisjoint(second_rooms)


def test_report_messages_use_target_perspective_roles() -> None:
    session = SimpleNamespace(
        history=SimpleNamespace(
            items=[
                SimpleNamespace(
                    type="message",
                    role="assistant",
                    text_content="Simulator opens the call.",
                ),
                SimpleNamespace(
                    type="message",
                    role="user",
                    text_content="Target agent responds.",
                ),
            ]
        )
    )

    assert livekit._canonical_report_messages(session) == [
        {"role": "user", "content": "Simulator opens the call."},
        {"role": "assistant", "content": "Target agent responds."},
    ]


def test_target_audio_selection_uses_explicit_identity() -> None:
    audio_kind = livekit.rtc.TrackKind.KIND_AUDIO
    room = SimpleNamespace(
        remote_participants={
            "other": SimpleNamespace(
                identity="other-agent",
                sid="participant-other",
                track_publications={
                    "track-other": SimpleNamespace(
                        sid="track-other",
                        kind=audio_kind,
                    )
                },
            ),
            "target": SimpleNamespace(
                identity="target-agent",
                sid="participant-target",
                track_publications={
                    "track-target": SimpleNamespace(
                        sid="track-target",
                        kind=audio_kind,
                    )
                },
            ),
        }
    )

    selected = livekit._find_target_audio(
        room,
        excluded_identities=set(),
        target_identity="target-agent",
    )

    assert selected is not None
    assert selected.identity == "target-agent"
    assert selected.audio_track_sid == "track-target"


def test_recording_mix_uses_only_explicit_paths(tmp_path: Path) -> None:
    first = tmp_path / "simulator.wav"
    second = tmp_path / "target.wav"
    unrelated = tmp_path / "unrelated.wav"
    _write_wav(first, np.array([1000, 1000], dtype=np.int16))
    _write_wav(second, np.array([2000, 2000], dtype=np.int16))
    _write_wav(unrelated, np.array([30000, 30000], dtype=np.int16))

    destination = tmp_path / "combined.wav"
    mix_recordings([first, second], destination, sample_rate=8000)

    with wave.open(str(destination), "rb") as wav_file:
        samples = np.frombuffer(
            wav_file.readframes(wav_file.getnframes()),
            dtype=np.int16,
        )
    assert samples.tolist() == [3000, 3000]


def test_livekit_api_url_normalizes_websocket_schemes() -> None:
    assert livekit._api_url("wss://lk.example.com") == "https://lk.example.com"
    assert livekit._api_url("ws://localhost:7880") == "http://localhost:7880"


def test_provider_evidence_uses_explicit_target_api_configuration(monkeypatch) -> None:
    monkeypatch.setenv("TARGET_VAPI_KEY", "vapi-secret")
    vapi_target = livekit.VapiTargetConfig(
        assistant_id="assistant_123",
        api_base_url="https://vapi.example",
        api_key_env="TARGET_VAPI_KEY",
    )
    retell_target = livekit.RetellTargetConfig(
        agent_id="agent_123",
        api_url="https://retell.example/v2/create-web-call",
    )

    assert livekit._target_api_key(vapi_target) == "vapi-secret"
    assert livekit._target_evidence_base_url(vapi_target) == "https://vapi.example"
    assert livekit._target_evidence_base_url(retell_target) == "https://retell.example"


def test_managed_case_dispatches_waits_and_cleans_up(monkeypatch) -> None:
    calls = []
    audio_kind = livekit.rtc.TrackKind.KIND_AUDIO

    class FakeRoom:
        def __init__(self):
            self.remote_participants = {
                "target": SimpleNamespace(
                    identity="target-agent",
                    sid="participant-target",
                    track_publications={
                        "track-target": SimpleNamespace(
                            sid="track-target",
                            kind=audio_kind,
                        )
                    },
                )
            }
            self.listeners = {}

        async def connect(self, url, token):
            calls.append(("connect", url, token))

        async def disconnect(self):
            calls.append(("disconnect",))

        def on(self, event, callback=None):
            self.listeners.setdefault(event, []).append(callback)
            return callback

        def off(self, event, callback):
            self.listeners.get(event, []).remove(callback)

    class FakeRoomService:
        async def create_room(self, request):
            calls.append(("create_room", request.name))

        async def delete_room(self, request):
            calls.append(("delete_room", request.room))

    class FakeDispatchService:
        async def create_dispatch(self, request):
            calls.append(
                ("dispatch", request.agent_name, request.room, request.metadata)
            )

    class FakeApiClient:
        def __init__(self):
            self.room = FakeRoomService()
            self.agent_dispatch = FakeDispatchService()

        async def aclose(self):
            calls.append(("api_close",))

    class FakeAccessToken:
        def __init__(self, _key, _secret):
            pass

        def with_identity(self, identity):
            calls.append(("identity", identity))
            return self

        def with_grants(self, _grants):
            return self

        def to_jwt(self):
            return "token"

    class FakeSession:
        def __init__(self):
            self.history = SimpleNamespace(
                items=[
                    SimpleNamespace(
                        type="message",
                        role="user",
                        text_content="Hello",
                    ),
                    SimpleNamespace(
                        type="message",
                        role="assistant",
                        text_content="Resolved",
                    ),
                ]
            )

        def on(self, event, callback):
            if event == "close":
                asyncio.get_running_loop().call_soon(callback, None)

        def shutdown(self, *, drain=True):
            calls.append(("shutdown", drain))

        async def wait_for_inactive(self):
            calls.append(("inactive",))

    class FakeCustomerAgent:
        def __init__(self):
            self.end_requested = asyncio.Event()

        async def start_session(self, _room, **_kwargs):
            return FakeSession()

        def open_conversation(self):
            calls.append(("open",))

    room = FakeRoom()
    api_client = FakeApiClient()
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")
    monkeypatch.setattr(livekit.rtc, "Room", lambda: room)
    monkeypatch.setattr(livekit.api, "LiveKitAPI", lambda *_args: api_client)
    monkeypatch.setattr(livekit, "AccessToken", FakeAccessToken)
    engine = LiveKitEngine()

    async def _fake_create(_persona, _simulator, **_kwargs):
        return FakeCustomerAgent(), None

    monkeypatch.setattr(engine, "_create_customer_agent", _fake_create)

    report = asyncio.run(
        engine.run(
            agent_definition=_agent(
                room_mode="managed",
                agent_name="registered-agent",
                target_participant_identity="target-agent",
            ),
            scenario=_scenario(),
            run_id="run_managed",
            min_turn_messages=2,
        )
    )

    result = report.results[0]
    room_name = result.metadata["room_name"]
    assert result.metadata["status"] == CaseStatus.COMPLETED.value
    assert result.metadata["target_participant_identity"] == "target-agent"
    dispatch = next(call for call in calls if call[0] == "dispatch")
    assert dispatch[1:3] == ("registered-agent", room_name)
    dispatch_metadata = json.loads(dispatch[3])
    assert dispatch_metadata["target_instructions"] == "Help the caller."
    assert dispatch_metadata["simulator_participant_identity"] == (
        "fagi-simulator-" + result.metadata["test_case_id"][-12:]
    )
    assert ("delete_room", room_name) in calls
    assert ("open",) in calls


def test_simulator_subscribes_to_sip_participant_audio(monkeypatch) -> None:
    captured = {}

    class FakeSession:
        def __init__(self, **kwargs):
            captured["session_options"] = kwargs

        async def start(self, _agent, *, room, room_input_options, room_output_options):
            captured["input_options"] = room_input_options
            captured["output_options"] = room_output_options

        def update_options(self, **kwargs):
            captured["updated_options"] = kwargs

    monkeypatch.setattr(livekit, "AgentSession", FakeSession)
    agent = livekit._TestRunnerAgent(
        persona=_scenario().dataset[0],
        instructions="Be a customer.",
    )
    asyncio.run(agent.start_session(SimpleNamespace()))

    assert (
        livekit.rtc.ParticipantKind.PARTICIPANT_KIND_SIP
        in captured["input_options"].participant_kinds
    )


def test_open_conversation_generates_opener_without_reading_situation() -> None:
    calls = []
    agent = livekit._TestRunnerAgent(
        persona=_scenario().dataset[0],
        instructions="Be a customer.",
    )
    agent._session = SimpleNamespace(
        generate_reply=lambda **kwargs: calls.append(kwargs)
    )

    agent.open_conversation()

    assert calls == [{}]


def test_conversation_completes_after_minimum_messages() -> None:
    calls = []

    class FakeSession:
        history = SimpleNamespace(
            items=[
                SimpleNamespace(type="message", role="assistant", text_content="Hello"),
                SimpleNamespace(type="message", role="user", text_content="Resolved"),
            ]
        )

        def on(self, _event, _callback):
            return None

        def shutdown(self, *, drain=True):
            calls.append(("shutdown", drain))

        async def wait_for_inactive(self):
            calls.append(("inactive",))

    class FakeRoom:
        def on(self, _event, _callback):
            return None

        def off(self, _event, _callback):
            return None

    reason = asyncio.run(
        livekit._wait_for_conversation_end(
            FakeRoom(),
            FakeSession(),
            customer_agent=SimpleNamespace(end_requested=asyncio.Event()),
            target_identity="target-agent",
            min_turn_messages=2,
            timeout=1,
            conversation_direction="simulator_first",
            agent_first_silence_timeout_seconds=30,
        )
    )

    assert reason == "minimum_messages_reached"
    assert calls == []


def test_unsupported_provider_lists_supported_options() -> None:
    from fi.simulate.agent.definition import LLMConfig, STTConfig, TTSConfig

    with pytest.raises(
        ValueError, match="Unsupported LiveKit STT provider: 'nope'"
    ) as exc_info:
        asyncio.run(
            livekit_models.build_livekit_models(
                llm_config=LLMConfig(),
                stt_config=STTConfig(provider="nope"),
                tts_config=TTSConfig(),
            )
        )
    assert "Supported:" in str(exc_info.value)


class _FakeRoomAudio:
    def __init__(self, target_identity: str) -> None:
        audio_kind = livekit.rtc.TrackKind.KIND_AUDIO
        self.remote_participants = {
            "target": SimpleNamespace(
                identity=target_identity,
                sid="participant-target",
                track_publications={
                    "track-target": SimpleNamespace(sid="track-target", kind=audio_kind)
                },
            )
        }

    async def connect(self, url, token):
        pass

    async def disconnect(self):
        pass

    def on(self, event, callback=None):
        return callback

    def off(self, event, callback):
        pass


class _FakeSipSession:
    def __init__(self) -> None:
        self.history = SimpleNamespace(
            items=[
                SimpleNamespace(type="message", role="user", text_content="hi"),
                SimpleNamespace(type="message", role="assistant", text_content="ok"),
            ]
        )

    def on(self, event, callback):
        if event == "close":
            asyncio.get_running_loop().call_soon(callback, None)

    def shutdown(self, *, drain=True):
        pass

    async def wait_for_inactive(self):
        pass


class _FakeCustomerAgent:
    def __init__(self) -> None:
        self.end_requested = asyncio.Event()

    async def start_session(self, _room, **_kwargs):
        return _FakeSipSession()

    def open_conversation(self):
        pass


def _fake_access_token():
    class _Tok:
        def __init__(self, *_a):
            pass

        def with_identity(self, _i):
            return self

        def with_grants(self, _g):
            return self

        def to_jwt(self):
            return "t"

    return _Tok


def _install_engine_fakes(monkeypatch, calls, target_identity="target-agent"):
    monkeypatch.setenv("LIVEKIT_API_KEY", "key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "secret")

    class _Sip:
        async def create_sip_participant(self, request):
            calls.append(
                (
                    "sip_dial",
                    request.sip_trunk_id,
                    request.sip_number,
                    request.sip_call_to,
                    request.room_name,
                    request.participant_identity,
                    request.wait_until_answered,
                )
            )

    class _Room:
        async def create_room(self, request):
            calls.append(("create_room", request.name))

        async def delete_room(self, request):
            calls.append(("delete_room", request.room))

    class _Dispatch:
        async def create_dispatch(self, request):
            calls.append(("dispatch", request.agent_name, request.room))

    class _Api:
        def __init__(self):
            self.room = _Room()
            self.agent_dispatch = _Dispatch()
            self.sip = _Sip()

        async def aclose(self):
            pass

    room = _FakeRoomAudio(target_identity)
    monkeypatch.setattr(livekit.rtc, "Room", lambda: room)
    monkeypatch.setattr(livekit.api, "LiveKitAPI", lambda *_a: _Api())
    monkeypatch.setattr(livekit, "AccessToken", _fake_access_token())
    engine = LiveKitEngine()

    async def _fake_create(_p, _s, **_kwargs):
        return _FakeCustomerAgent(), None

    monkeypatch.setattr(engine, "_create_customer_agent", _fake_create)
    return engine


def test_sip_outbound_dials_per_case_room_and_identity(monkeypatch) -> None:
    calls: list = []
    engine = _install_engine_fakes(monkeypatch, calls, target_identity="sip-target")
    agent = _agent(
        room_mode="managed",
        room_name="sdk-suite-{test_case_id}",
        target_participant_identity="sip-target",
        transport={
            "kind": "sip_outbound",
            "sip_trunk_id": "ST_test",
            "sip_number": "+12068956991",
            "sip_call_to": "+14155551234",
        },
    )
    report = asyncio.run(
        engine.run(
            agent_definition=agent,
            scenario=_scenario(2),
            run_id="run_sip",
            min_turn_messages=2,
        )
    )

    dials = [call for call in calls if call[0] == "sip_dial"]
    assert len(dials) == 2
    room_names = [call[4] for call in dials]
    identities = [call[5] for call in dials]
    assert len(set(room_names)) == 2
    assert len(set(identities)) == 2
    assert all(call[1] == "ST_test" for call in dials)
    assert all(call[2] == "+12068956991" for call in dials)
    assert all(call[3] == "+14155551234" for call in dials)
    assert all(call[6] is True for call in dials)
    for result in report.results:
        assert result.metadata["status"] == CaseStatus.COMPLETED.value

    assert not [call for call in calls if call[0] == "dispatch"]


def test_sip_outbound_api_failure_yields_typed_sip_dial_failed(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")

    class _Sip:
        async def create_sip_participant(self, request):
            raise RuntimeError("dial refused")

    class _Room:
        async def create_room(self, request):
            calls.append(("create_room", request.name))

        async def delete_room(self, request):
            calls.append(("delete_room", request.room))

    class _Api:
        def __init__(self):
            self.room = _Room()
            self.agent_dispatch = SimpleNamespace(create_dispatch=lambda _r: None)
            self.sip = _Sip()

        async def aclose(self):
            pass

    monkeypatch.setattr(livekit.rtc, "Room", lambda: _FakeRoomAudio("sip-target"))
    monkeypatch.setattr(livekit.api, "LiveKitAPI", lambda *_a: _Api())
    monkeypatch.setattr(livekit, "AccessToken", _fake_access_token())
    engine = LiveKitEngine()

    async def _fake_create(_p, _s, **_kwargs):
        return _FakeCustomerAgent(), None

    monkeypatch.setattr(engine, "_create_customer_agent", _fake_create)

    agent = _agent(
        room_mode="managed",
        transport={
            "kind": "sip_outbound",
            "sip_trunk_id": "ST_test",
            "sip_number": "+12068956991",
            "sip_call_to": "+14155551234",
        },
    )
    report = asyncio.run(
        engine.run(
            agent_definition=agent,
            scenario=_scenario(),
            run_id="run_sip_fail",
        )
    )
    result = report.results[0]
    assert result.metadata["status"] == CaseStatus.FAILED.value
    failure = result.metadata["failure"]
    assert failure["code"] == "sip_dial_failed"
    assert "dial refused" not in json.dumps(failure)
    assert failure["details"]["exception_type"] == "RuntimeError"


def test_sip_inbound_timeout_yields_typed_no_participant(monkeypatch) -> None:
    calls: list = []
    monkeypatch.setenv("LIVEKIT_API_KEY", "k")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "s")

    class _Room:
        async def create_room(self, request):
            calls.append(("create_room", request.name))
            calls_room_name.append(request.name)

        async def delete_room(self, request):
            calls.append(("delete_room", request.room))

    calls_room_name: list[str] = []

    class _SipStub:
        async def list_sip_dispatch_rule(self, _request):
            expected_room = calls_room_name[-1] if calls_room_name else ""
            item = SimpleNamespace(
                name="inbound-rule",
                sip_dispatch_rule_id="SD_reused",
                trunk_ids=["ST_test_inbound"],
                rule=SimpleNamespace(
                    dispatch_rule_direct=SimpleNamespace(room_name=expected_room)
                ),
            )
            return SimpleNamespace(items=[item])

        async def create_sip_dispatch_rule(self, _request):
            raise AssertionError("dispatch_rule_name reuse must not create a new rule")

        async def delete_sip_dispatch_rule(self, _request):
            raise AssertionError("reused dispatch rule must not be deleted")

        async def create_sip_participant(self, _request):
            return None

    class _Api:
        def __init__(self):
            self.room = _Room()
            self.agent_dispatch = SimpleNamespace(create_dispatch=lambda _r: None)
            self.sip = _SipStub()

        async def aclose(self):
            pass

    class _EmptyRoom:
        remote_participants: dict = {}

        async def connect(self, url, token):
            pass

        async def disconnect(self):
            pass

        def on(self, event, callback=None):
            return callback

        def off(self, event, callback):
            pass

    monkeypatch.setattr(livekit.rtc, "Room", lambda: _EmptyRoom())
    monkeypatch.setattr(livekit.api, "LiveKitAPI", lambda *_a: _Api())
    monkeypatch.setattr(livekit, "AccessToken", _fake_access_token())
    engine = LiveKitEngine()

    async def _fake_create(_p, _s, **_kwargs):
        session = _FakeSipSession()

        class _Agent:
            async def start_session(self, _room, **_kwargs):
                return session

            def open_conversation(self):
                pass

        return _Agent(), None

    monkeypatch.setattr(engine, "_create_customer_agent", _fake_create)

    agent = _agent(
        room_mode="managed",
        transport={
            "kind": "sip_inbound",
            "dispatch_rule_name": "inbound-rule",
            "readiness_timeout_seconds": 0.05,
        },
    )
    report = asyncio.run(
        engine.run(
            agent_definition=agent,
            scenario=_scenario(),
            run_id="run_sip_in",
            readiness_timeout=5.0,
        )
    )
    result = report.results[0]
    assert result.metadata["status"] == CaseStatus.AGENT_UNAVAILABLE.value
    assert result.metadata["failure"]["code"] == "sip_inbound_no_participant"
    assert result.metadata["sip_dispatch_rule_id"] == "SD_reused"
    assert result.metadata["sip_dispatch_rule_created"] is False


def test_cleanup_logging_redacts_exception_details(caplog) -> None:
    secret = "-".join(("provider", "secret", "value"))
    errors = []

    try:
        raise RuntimeError(secret)
    except RuntimeError as exc:
        with caplog.at_level(
            logging.ERROR,
            logger="fi.simulate.simulation.engines.livekit",
        ):
            livekit._record_cleanup_error(
                errors,
                exc,
                "disconnect",
                "run_redaction",
                "case_redaction",
            )

    assert errors == ["disconnect:RuntimeError"]
    assert secret not in caplog.text
    assert "RuntimeError: details redacted" in caplog.text


@pytest.mark.parametrize(
    (
        "transport_kind",
        "connector_name",
        "identity_prefix",
        "target",
        "conversation_direction",
        "first_message_mode",
    ),
    [
        (
            "vapi_websocket",
            "VapiWebSocketConnector",
            "fagi-vapi-bridge-",
            {
                "provider": "vapi",
                "assistant_id": "assistant_123",
                "api_key_env": "TARGET_PROVIDER_KEY",
            },
            "simulator_first",
            "assistant-waits-for-user",
        ),
        (
            "vapi_websocket",
            "VapiWebSocketConnector",
            "fagi-vapi-bridge-",
            {
                "provider": "vapi",
                "assistant_id": "assistant_123",
                "api_key_env": "TARGET_PROVIDER_KEY",
            },
            "agent_first",
            "assistant-speaks-first",
        ),
        (
            "retell_webcall",
            "RetellWebCallConnector",
            "fagi-retell-bridge-",
            {
                "provider": "retell",
                "agent_id": "agent_123",
                "api_key_env": "TARGET_PROVIDER_KEY",
            },
            "simulator_first",
            None,
        ),
    ],
)
def test_web_bridge_joins_as_target_without_sip(
    monkeypatch,
    transport_kind,
    connector_name,
    identity_prefix,
    target,
    conversation_direction,
    first_message_mode,
) -> None:
    calls: list[tuple] = []
    engine = _install_engine_fakes(monkeypatch, calls)

    class _Bridge:
        def __init__(self, **kwargs):
            calls.append(("bridge_init", kwargs["room_name"], kwargs["identity"]))
            self.call_id = "call_web_123"
            self._closed = asyncio.Event()

        async def connect(self):
            calls.append(("bridge_connect",))

        async def run(self):
            await self._closed.wait()

        async def aclose(self):
            calls.append(("bridge_close",))
            self._closed.set()

    async def _wait_for_target(
        _room,
        *,
        excluded_identities,
        target_identity,
        timeout,
    ):
        calls.append(("target_wait", target_identity))
        return livekit._TargetParticipant(
            identity=target_identity,
            sid="bridge-participant",
            audio_track_sid="bridge-track",
        )

    connector_type = getattr(livekit, connector_name)
    received_targets = []
    connector_kwargs = []

    def _from_target(_cls, provider_target, **kwargs):
        received_targets.append(provider_target)
        connector_kwargs.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(
        connector_type,
        "from_target",
        classmethod(_from_target),
    )
    monkeypatch.setattr(livekit, "LiveKitAudioBridge", _Bridge)
    monkeypatch.setattr(livekit, "_wait_for_target_audio", _wait_for_target)

    report = asyncio.run(
        engine.run(
            agent_definition=_agent(
                room_mode="managed",
                room_name="sdk-web-{test_case_id}",
                transport={"kind": transport_kind},
                target=target,
            ),
            scenario=_scenario(),
            run_id="run_web_bridge",
            min_turn_messages=2,
            conversation_direction=conversation_direction,
        )
    )

    result = report.results[0]
    assert result.metadata["status"] == CaseStatus.COMPLETED.value
    assert result.metadata["target_provider"] == target["provider"]
    assert result.metadata["provider_call_id"] == "call_web_123"
    assert result.metadata["target_participant_identity"].startswith(identity_prefix)
    assert received_targets[0].provider == target["provider"]
    assert connector_kwargs == (
        [{"first_message_mode": first_message_mode}]
        if first_message_mode is not None
        else [{}]
    )
    assert ("bridge_connect",) in calls
    assert ("bridge_close",) in calls
    assert not [call for call in calls if call[0] in {"dispatch", "sip_dial"}]


def _dispatch_rule(rule_id: str, name: str, trunk_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        sip_dispatch_rule_id=rule_id, name=name, trunk_ids=[trunk_id], rule=None
    )


class _StubSipService:
    """Minimal SIP surface for exercising dispatch-rule provisioning."""

    def __init__(self, existing):
        self.existing = list(existing)
        self.deleted: list[str] = []
        self.created: list[str] = []

    async def list_sip_dispatch_rule(self, _request):
        return SimpleNamespace(items=list(self.existing))

    async def delete_sip_dispatch_rule(self, request):
        self.deleted.append(request.sip_dispatch_rule_id)
        self.existing = [
            r
            for r in self.existing
            if r.sip_dispatch_rule_id != request.sip_dispatch_rule_id
        ]

    async def create_sip_dispatch_rule(self, request):
        self.created.append(request.name)
        return SimpleNamespace(sip_dispatch_rule_id="SDR_new")


def _dispatch_client(existing):
    sip = _StubSipService(existing)
    return SimpleNamespace(sip=sip), sip


def test_sip_inbound_dispatch_reclaims_own_orphaned_rule(monkeypatch):
    """A rule this SDK left behind must not block later runs.

    Without this, one crashed run poisons the project until a human
    deletes the leftover rule by hand.
    """
    monkeypatch.setenv("LIVEKIT_INBOUND_TRUNK_ID", "ST_trunk")
    orphan = _dispatch_rule("SDR_orphan", "sim-inbound-abandoned-room", "ST_trunk")
    client, sip = _dispatch_client([orphan])

    rule_id, created = asyncio.run(
        livekit._ensure_sip_inbound_dispatch(
            client,
            transport=livekit.TelephonyTransport(kind="sip_inbound"),
            room_name="fresh-room",
        )
    )

    assert sip.deleted == ["SDR_orphan"]
    assert rule_id == "SDR_new"
    assert created is True


def test_sip_inbound_dispatch_refuses_foreign_rule(monkeypatch):
    """Routing we did not create is never silently deleted."""
    monkeypatch.setenv("LIVEKIT_INBOUND_TRUNK_ID", "ST_trunk")
    foreign = _dispatch_rule("SDR_theirs", "production-inbound", "ST_trunk")
    client, sip = _dispatch_client([foreign])

    with pytest.raises(RuntimeError, match="sip_inbound_route_conflict"):
        asyncio.run(
            livekit._ensure_sip_inbound_dispatch(
                client,
                transport=livekit.TelephonyTransport(kind="sip_inbound"),
                room_name="fresh-room",
            )
        )

    assert sip.deleted == []
    assert sip.created == []
