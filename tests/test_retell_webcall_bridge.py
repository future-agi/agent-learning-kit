from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import av
import pytest

from fi.alk.harness.call_runner import _retell_web_call_url
from fi.simulate.agent.definition import RetellTargetConfig
from fi.simulate.simulation.bridge import livekit as bridge_livekit
from fi.simulate.simulation.bridge import retell
from fi.simulate.simulation.bridge.connector import ConnectorConfig
from fi.simulate.simulation.bridge.retell import (
    FRAME_SAMPLES,
    RetellWebCallConnector,
    RetellWebCallError,
)
from fi.simulate.simulation.engines.livekit import _safe_provider_error_details

API_URL = "https://api.retellai.com/v3/create-web-call"
SESSIONS_URL = (
    "https://api.retellai.com/webrtc-proxy/call_retell_123/v1/webrtc/sessions"
)
CALL = {
    "call_id": "call_retell_123",
    "access_token": "call-token",
    "transport": "gateway",
    "ice_servers": [
        {"urls": ["turn:turn.retell.example:3478"], "username": "u", "credential": "c"}
    ],
}
ANSWER = {"session_id": "session_1", "sdp": "v=0\r\nanswer"}


class _Response:
    def __init__(self, status, body=None):
        self.status = status
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def json(self):
        return self._body


class _Http:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
        self.closed = False

    def post(self, url, *, headers, json):
        self.requests.append(("POST", url, headers, json))
        return self.responses.pop(0)

    def delete(self, url, *, headers):
        self.requests.append(("DELETE", url, headers, None))
        return _Response(204)

    async def close(self):
        self.closed = True


class _Emitter:
    def __init__(self):
        self.handlers = {}

    def on(self, event):
        def decorator(callback):
            self.handlers[event] = callback
            return callback

        return decorator


class _Track:
    kind = "audio"

    def __init__(self, frames=()):
        self.frames = list(frames)

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        await asyncio.Event().wait()


class _PeerConnection(_Emitter):
    def __init__(self, remote_track=None):
        super().__init__()
        self.tracks = []
        self.channel = _Emitter()
        self.channel_label = None
        self.localDescription = None
        self.remote = None
        self.connectionState = "new"
        self.closed = False
        self.remote_track = remote_track or _Track()

    def addTrack(self, track):
        self.tracks.append(track)

    def createDataChannel(self, label):
        self.channel_label = label
        return self.channel

    async def createOffer(self):
        return SimpleNamespace(sdp="v=0\r\noffer", type="offer")

    async def setLocalDescription(self, description):
        self.localDescription = description

    async def setRemoteDescription(self, description):
        self.remote = description
        self.handlers["track"](self.remote_track)

    async def close(self):
        self.closed = True
        self.connectionState = "closed"


def _connector():
    return RetellWebCallConnector(
        ConnectorConfig(api_key="api-key", assistant_id="agent_123", api_url=API_URL)
    )


def _install(monkeypatch, http, pc):
    monkeypatch.setattr(retell.aiohttp, "ClientSession", lambda **_kwargs: http)
    monkeypatch.setattr(retell, "RTCPeerConnection", lambda configuration: pc)


def _ready(monkeypatch, remote_track=None):
    http = _Http([_Response(201, CALL), _Response(201, ANSWER)])
    pc = _PeerConnection(remote_track=remote_track)
    _install(monkeypatch, http, pc)
    return http, pc, _connector()


def _stereo_frame(sample_value: int) -> av.AudioFrame:
    frame = av.AudioFrame(format="s16", layout="stereo", samples=FRAME_SAMPLES)
    data = sample_value.to_bytes(2, "little", signed=True) * (FRAME_SAMPLES * 2)
    frame.planes[0].update(data.ljust(frame.planes[0].buffer_size, b"\x00"))
    frame.sample_rate = 48000
    return frame


async def _drain(connector):
    async for _ in connector.recv_audio():
        pass


def test_connect_creates_a_v3_call_and_opens_a_gateway_session(monkeypatch) -> None:
    http, pc, connector = _ready(monkeypatch)

    async def run():
        await connector.connect()
        assert connector.is_connected and connector.is_agent_ready
        await connector.disconnect()

    asyncio.run(run())

    create, session, delete = http.requests
    assert create == (
        "POST",
        API_URL,
        {"Authorization": "Bearer api-key"},
        {"agent_id": "agent_123"},
    )
    assert session == (
        "POST",
        SESSIONS_URL,
        {"Authorization": "Bearer call-token"},
        {"identity": "client", "sdp": "v=0\r\noffer"},
    )
    assert delete == (
        "DELETE",
        f"{SESSIONS_URL}/session_1",
        {"Authorization": "Bearer call-token"},
        None,
    )
    assert pc.channel_label == "control"
    assert pc.remote.sdp == ANSWER["sdp"] and pc.remote.type == "answer"
    assert pc.closed and http.closed
    assert connector.call_id == "call_retell_123"


def test_ice_servers_from_the_call_reach_the_peer_connection(monkeypatch) -> None:
    http = _Http([_Response(201, CALL), _Response(201, ANSWER)])
    seen = {}

    def make_pc(configuration):
        seen["servers"] = configuration.iceServers
        return _PeerConnection()

    monkeypatch.setattr(retell.aiohttp, "ClientSession", lambda **_kwargs: http)
    monkeypatch.setattr(retell, "RTCPeerConnection", make_pc)
    connector = _connector()

    async def run():
        await connector.connect()
        await connector.disconnect()

    asyncio.run(run())

    (server,) = seen["servers"]
    assert server.urls == ["turn:turn.retell.example:3478"]
    assert (server.username, server.credential) == ("u", "c")


def test_refused_create_reports_its_http_status(monkeypatch) -> None:
    http = _Http([_Response(422, {"message": "agent missing"})])
    _install(monkeypatch, http, _PeerConnection())

    with pytest.raises(RetellWebCallError) as exc:
        asyncio.run(_connector().connect())

    assert (exc.value.code, exc.value.status) == ("retell_webcall_create_failed", 422)
    details = _safe_provider_error_details(exc.value, operation="web_bridge_start")
    assert details["http_status"] == 422
    assert details["provider_code"] == "retell_webcall_create_failed"
    assert http.closed


def test_refused_session_reports_its_http_status(monkeypatch) -> None:
    http = _Http([_Response(201, CALL), _Response(401, {"message": "expired"})])
    pc = _PeerConnection()
    _install(monkeypatch, http, pc)

    with pytest.raises(RetellWebCallError) as exc:
        asyncio.run(_connector().connect())

    assert (exc.value.code, exc.value.status) == ("retell_webcall_session_failed", 401)
    assert pc.closed and http.closed


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({**CALL, "transport": "livekit"}, "retell_webcall_transport_unsupported"),
        ({**CALL, "access_token": ""}, "retell_webcall_response_missing_access_token"),
        (
            {k: v for k, v in CALL.items() if k != "call_id"},
            "retell_webcall_response_missing_call_id",
        ),
        (["not", "an", "object"], "retell_webcall_response_invalid"),
    ],
)
def test_unusable_create_response_is_refused(monkeypatch, body, code) -> None:
    _install(monkeypatch, _Http([_Response(201, body)]), _PeerConnection())

    with pytest.raises(RetellWebCallError) as exc:
        asyncio.run(_connector().connect())

    assert exc.value.code == code


def test_missing_agent_track_times_out_and_ends_the_session(monkeypatch) -> None:
    http, pc, connector = _ready(monkeypatch)

    async def no_track(description):
        pc.remote = description

    pc.setRemoteDescription = no_track
    monkeypatch.setattr(retell, "AGENT_TRACK_TIMEOUT_SECONDS", 0.05)

    with pytest.raises(RetellWebCallError) as exc:
        asyncio.run(connector.connect())

    assert exc.value.code == "retell_webcall_agent_track_timeout"
    assert pc.closed
    assert http.requests[-1][:2] == ("DELETE", f"{SESSIONS_URL}/session_1")


def test_agent_audio_arrives_as_mono_pcm(monkeypatch) -> None:
    track = _Track([_stereo_frame(1000), _stereo_frame(-1000)])
    _, pc, connector = _ready(monkeypatch, remote_track=track)

    async def run():
        await connector.connect()
        chunks = []
        async for pcm, rate in connector.recv_audio():
            chunks.append((pcm, rate))
            if len(chunks) == 2:
                pc.channel.handlers["message"](
                    json.dumps({"type": "status", "state": "ended"})
                )
        await connector.disconnect()
        return chunks

    chunks = asyncio.run(run())

    assert [rate for _, rate in chunks] == [48000, 48000]
    first, second = (pcm for pcm, _ in chunks)
    assert len(first) == FRAME_SAMPLES * 2
    assert first[:2] == (1000).to_bytes(2, "little", signed=True)
    assert second[:2] == (-1000).to_bytes(2, "little", signed=True)


@pytest.mark.parametrize("state", ["ended", "replaced"])
def test_closing_status_on_control_ends_the_audio_stream(monkeypatch, state) -> None:
    _, pc, connector = _ready(monkeypatch)

    async def run():
        await connector.connect()
        receiver = asyncio.create_task(_drain(connector))
        await asyncio.sleep(0)
        pc.channel.handlers["message"](json.dumps({"type": "status", "state": state}))
        await asyncio.wait_for(receiver, timeout=1)
        assert connector.is_connected is False
        await connector.disconnect()

    asyncio.run(run())


def test_connected_status_and_other_messages_keep_the_call_open(monkeypatch) -> None:
    _, pc, connector = _ready(monkeypatch)

    async def run():
        await connector.connect()
        for message in (
            '{"type":"status","state":"connected"}',
            '{"type":"pong","ts":1}',
            "not json",
            b"\x00",
        ):
            pc.channel.handlers["message"](message)
        assert connector._agent_disconnected.is_set() is False
        await connector.disconnect()

    asyncio.run(run())


def test_failed_peer_connection_ends_the_audio_stream(monkeypatch) -> None:
    _, pc, connector = _ready(monkeypatch)

    async def run():
        await connector.connect()
        receiver = asyncio.create_task(_drain(connector))
        await asyncio.sleep(0)
        pc.connectionState = "failed"
        pc.handlers["connectionstatechange"]()
        await asyncio.wait_for(receiver, timeout=1)
        await connector.disconnect()

    asyncio.run(run())


def test_silent_agent_track_ends_the_stream_after_the_frame_timeout(
    monkeypatch,
) -> None:
    _, _, connector = _ready(monkeypatch)
    monkeypatch.setattr(retell, "RECV_FRAME_TIMEOUT_SECONDS", 0.05)

    async def run():
        await connector.connect()
        await asyncio.wait_for(_drain(connector), timeout=1)
        await connector.disconnect()

    asyncio.run(run())


def test_caller_audio_is_sent_in_20ms_frames_then_silence() -> None:
    sample = (7).to_bytes(2, "little", signed=True)

    async def run():
        track = retell._CallerAudioTrack()
        track.push(sample * (FRAME_SAMPLES + 10))
        frames = [await track.recv() for _ in range(3)]
        track.stop()
        return frames

    first, second, third = asyncio.run(run())

    for frame in (first, second, third):
        assert (frame.samples, frame.sample_rate) == (FRAME_SAMPLES, 48000)
    assert [first.pts, second.pts, third.pts] == [0, FRAME_SAMPLES, 2 * FRAME_SAMPLES]
    assert bytes(first.planes[0])[: FRAME_SAMPLES * 2] == sample * FRAME_SAMPLES
    second_pcm = bytes(second.planes[0])[: FRAME_SAMPLES * 2]
    assert second_pcm[:20] == sample * 10
    assert set(second_pcm[20:]) == {0}
    assert set(bytes(third.planes[0])[: FRAME_SAMPLES * 2]) == {0}


def test_caller_audio_buffer_is_bounded() -> None:
    track = retell._CallerAudioTrack()
    track.push(b"\x01\x00" * retell.MAX_PENDING_BYTES)
    assert len(track._pending) == retell.MAX_PENDING_BYTES


def test_send_audio_resamples_to_48k(monkeypatch) -> None:
    _, _, connector = _ready(monkeypatch)

    async def run():
        await connector.connect()
        await connector.send_audio(b"\x00\x00" * 160, 16000)
        pending = len(connector._caller_track._pending)
        await connector.disconnect()
        return pending

    assert asyncio.run(run()) == pytest.approx(480 * 2, abs=8)


def test_disconnect_closes_http_even_when_session_end_fails(monkeypatch) -> None:
    http, pc, connector = _ready(monkeypatch)

    def failing_delete(url, *, headers):
        raise retell.aiohttp.ClientConnectionError("gone")

    http.delete = failing_delete

    async def run():
        await connector.connect()
        await connector.disconnect()

    asyncio.run(run())

    assert pc.closed and http.closed


def test_cancelled_audio_receiver_leaves_no_task_behind(monkeypatch) -> None:
    _, _, connector = _ready(monkeypatch)

    async def run():
        await connector.connect()
        receiver = asyncio.create_task(_drain(connector))
        await asyncio.sleep(0.01)
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        await connector.disconnect()
        await asyncio.sleep(0)
        current = asyncio.current_task()
        return [t for t in asyncio.all_tasks() if t is not current and not t.done()]

    assert asyncio.run(run()) == []


def test_connector_uses_explicit_target(monkeypatch) -> None:
    monkeypatch.setenv("HEALTHCARE_RETELL_KEY", "test-key")

    connector = RetellWebCallConnector.from_target(
        RetellTargetConfig(
            agent_id="agent_healthcare",
            api_url="https://retell.healthcare.example/v3/create-web-call",
            api_key_env="HEALTHCARE_RETELL_KEY",
        )
    )

    assert connector._config.assistant_id == "agent_healthcare"
    assert connector._config.api_key == "test-key"
    assert (
        connector._config.api_url
        == "https://retell.healthcare.example/v3/create-web-call"
    )


def test_target_defaults_to_v3_and_ignores_the_old_livekit_url() -> None:
    target = RetellTargetConfig(
        agent_id="agent_1", livekit_url="wss://retell-ai-4ihahnq7.livekit.cloud"
    )
    assert str(target.api_url) == API_URL
    assert "livekit_url" not in target.model_dump()


def test_sessions_url_follows_the_configured_api_host() -> None:
    assert (
        retell._sessions_url(
            "https://retell.proxy.example/v3/create-web-call", "call_9"
        )
        == "https://retell.proxy.example/webrtc-proxy/call_9/v1/webrtc/sessions"
    )


def test_connector_requires_credentials(monkeypatch) -> None:
    monkeypatch.delenv("RETELL_API_KEY", raising=False)
    monkeypatch.delenv("RETELL_AGENT_ID", raising=False)

    with pytest.raises(ValueError, match="RETELL_API_KEY, RETELL_AGENT_ID"):
        RetellWebCallConnector.from_env()


def test_from_env_defaults_to_v3(monkeypatch) -> None:
    monkeypatch.setenv("RETELL_API_KEY", "key")
    monkeypatch.setenv("RETELL_AGENT_ID", "agent_1")
    monkeypatch.delenv("RETELL_API_URL", raising=False)

    assert RetellWebCallConnector.from_env()._config.api_url == API_URL


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        (None, API_URL),
        ("", API_URL),
        ("https://api.retellai.com/v2/create-web-call", API_URL),
        ("https://api.retellai.com/v2/create-web-call/", API_URL),
        (
            "https://retell.proxy.example/v3/create-web-call",
            "https://retell.proxy.example/v3/create-web-call",
        ),
    ],
)
def test_harness_upgrades_the_retired_v2_url(configured, expected) -> None:
    assert _retell_web_call_url(configured) == expected


def test_cancelling_bridge_run_reaps_all_media_tasks():
    tasks = []
    ready = asyncio.Event()

    async def media():
        tasks.append(asyncio.current_task())
        if len(tasks) == 4:
            ready.set()
        await asyncio.Event().wait()

    async def run():
        bridge = bridge_livekit.LiveKitAudioBridge(
            url="ws://localhost",
            api_key="key",
            api_secret="secret",
            room_name="room",
            identity="bridge",
            connector=_connector(),
        )
        for name in (
            "_room_to_provider",
            "_provider_to_room",
            "_watchdog",
            "_wait_for_room_disconnect",
        ):
            setattr(bridge, name, media)
        task = asyncio.create_task(bridge.run())
        await ready.wait()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert all(t.done() for t in tasks), (
            "Bridge returned while media tasks were alive"
        )

    asyncio.run(run())
