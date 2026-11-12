from __future__ import annotations

import asyncio
import fractions
import json
import logging
import os
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlsplit

import aiohttp
import av
from aiortc import (
    MediaStreamTrack,
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError

from fi.simulate.agent.definition import RetellTargetConfig
from fi.simulate.simulation.bridge.audio import PCMResampler
from fi.simulate.simulation.bridge.connector import ConnectorConfig, ProviderConnector

logger = logging.getLogger(__name__)
RETELL_SAMPLE_RATE = 48000
RETELL_API_URL = "https://api.retellai.com/v3/create-web-call"
FRAME_SAMPLES = 960
FRAME_BYTES = FRAME_SAMPLES * 2
MAX_PENDING_BYTES = RETELL_SAMPLE_RATE * 2 * 10
AGENT_TRACK_TIMEOUT_SECONDS = 30.0
RECV_FRAME_TIMEOUT_SECONDS = 30.0
REQUEST_TIMEOUT_SECONDS = 15.0
CLOSING_STATES = {"ended", "replaced"}
_TIMEOUT_ERRORS = (asyncio.TimeoutError, TimeoutError)


class RetellWebCallError(RuntimeError):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code if status is None else f"{code}:{status}")
        self.code = code
        self.status = status


class _CallerAudioTrack(MediaStreamTrack):
    kind = "audio"

    def __init__(self) -> None:
        super().__init__()
        self._pending = bytearray()
        self._pts = 0
        self._started_at: float | None = None

    def push(self, data: bytes) -> None:
        self._pending.extend(data)
        overflow = len(self._pending) - MAX_PENDING_BYTES
        if overflow > 0:
            del self._pending[:overflow]

    async def recv(self) -> av.AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError
        if self._started_at is None:
            # Audio queued while the connection was coming up would otherwise
            # become a permanent delay on everything the caller says.
            self._pending.clear()
            self._started_at = time.monotonic()
        delay = self._started_at + self._pts / RETELL_SAMPLE_RATE - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)
        chunk = bytes(self._pending[:FRAME_BYTES])
        del self._pending[:FRAME_BYTES]
        frame = av.AudioFrame(format="s16", layout="mono", samples=FRAME_SAMPLES)
        frame.planes[0].update(chunk.ljust(frame.planes[0].buffer_size, b"\x00"))
        frame.sample_rate = RETELL_SAMPLE_RATE
        frame.pts = self._pts
        frame.time_base = fractions.Fraction(1, RETELL_SAMPLE_RATE)
        self._pts += FRAME_SAMPLES
        return frame


def _is_tls_relay(url: str) -> bool:
    return url.startswith("turns:")


def _ice_servers(servers: Any) -> list[RTCIceServer]:
    result = []
    for server in servers if isinstance(servers, list) else []:
        if not isinstance(server, dict):
            continue
        urls = server.get("urls") or server.get("url")
        if isinstance(urls, str):
            urls = [urls]
        if not isinstance(urls, list) or not urls:
            continue
        result.append(
            RTCIceServer(
                urls=sorted(
                    (str(url) for url in urls),
                    key=lambda url: not _is_tls_relay(url),
                ),
                username=server.get("username"),
                credential=server.get("credential"),
            )
        )
    return sorted(
        result, key=lambda server: not any(_is_tls_relay(u) for u in server.urls)
    )


def _mono_pcm(frame: av.AudioFrame, resampler: av.AudioResampler) -> bytes:
    pcm = bytearray()
    for converted in resampler.resample(frame):
        pcm.extend(bytes(converted.planes[0])[: converted.samples * 2])
    return bytes(pcm)


def _sessions_url(api_url: str, call_id: str) -> str:
    parts = urlsplit(api_url)
    return f"{parts.scheme}://{parts.netloc}/webrtc-proxy/{call_id}/v1/webrtc/sessions"


class RetellWebCallConnector(ProviderConnector):
    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._http: aiohttp.ClientSession | None = None
        self._pc: RTCPeerConnection | None = None
        self._caller_track: _CallerAudioTrack | None = None
        self._track_future: asyncio.Future[MediaStreamTrack] | None = None
        self._agent_disconnected = asyncio.Event()
        self._agent_ready = asyncio.Event()
        self._pc_connected = asyncio.Event()
        self._call_id: str | None = None
        self._access_token: str | None = None
        self._session_url: str | None = None
        self._ice: list[RTCIceServer] = []
        self._connected = False
        self._resamplers: dict[int, PCMResampler] = {}

    @classmethod
    def from_target(cls, target: RetellTargetConfig) -> "RetellWebCallConnector":
        api_key = os.environ.get(target.api_key_env, "").strip()
        if not api_key:
            raise ValueError("retell_webcall_config_missing: " + target.api_key_env)
        return cls(
            ConnectorConfig(
                api_key=api_key,
                assistant_id=target.agent_id,
                api_url=str(target.api_url),
            )
        )

    @classmethod
    def from_env(cls) -> "RetellWebCallConnector":
        api_key = os.environ.get("RETELL_API_KEY", "").strip()
        agent_id = os.environ.get("RETELL_AGENT_ID", "").strip()
        missing = [
            name
            for name, value in (
                ("RETELL_API_KEY", api_key),
                ("RETELL_AGENT_ID", agent_id),
            )
            if not value
        ]
        if missing:
            raise ValueError("retell_webcall_config_missing: " + ", ".join(missing))
        return cls.from_target(
            RetellTargetConfig(
                agent_id=agent_id,
                api_url=os.environ.get("RETELL_API_URL", RETELL_API_URL),
            )
        )

    async def connect(self) -> None:
        self._http = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
        )
        try:
            await self._create_call()
            await self._open_session()
            if self._track_future is None:
                raise RetellWebCallError("retell_webcall_track_future_missing")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + AGENT_TRACK_TIMEOUT_SECONDS
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._track_future),
                    timeout=AGENT_TRACK_TIMEOUT_SECONDS,
                )
            except _TIMEOUT_ERRORS as exc:
                raise RetellWebCallError("retell_webcall_agent_track_timeout") from exc
            await self._wait_until_connected(max(0.0, deadline - loop.time()))
            logger.info("retell_webcall_connected", extra={"call_id": self._call_id})
        except Exception:
            await self.disconnect()
            raise

    async def _wait_until_connected(self, timeout: float) -> None:
        pc = self._pc
        if pc is None:
            raise RetellWebCallError("retell_webcall_connect_failed")
        if pc.connectionState == "connected":
            return
        connected = asyncio.ensure_future(self._pc_connected.wait())
        dead = asyncio.ensure_future(self._agent_disconnected.wait())
        try:
            done, _ = await asyncio.wait(
                {connected, dead},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for task in (connected, dead):
                task.cancel()
            await asyncio.gather(connected, dead, return_exceptions=True)
        if connected in done and pc.connectionState == "connected":
            return
        if not done:
            raise RetellWebCallError("retell_webcall_connect_timeout")
        raise RetellWebCallError("retell_webcall_connect_failed")

    async def _create_call(self) -> None:
        assert self._http is not None
        try:
            async with self._http.post(
                self._config.api_url,
                headers={"Authorization": f"Bearer {self._config.api_key}"},
                json={"agent_id": self._config.assistant_id},
            ) as response:
                if response.status != 201:
                    raise RetellWebCallError(
                        "retell_webcall_create_failed", response.status
                    )
                payload = await response.json()
        except _TIMEOUT_ERRORS as exc:
            raise RetellWebCallError("retell_webcall_create_timeout") from exc
        if not isinstance(payload, dict):
            raise RetellWebCallError("retell_webcall_response_invalid")
        for field in ("call_id", "access_token"):
            value = payload.get(field)
            if not isinstance(value, str) or not value.strip():
                raise RetellWebCallError(f"retell_webcall_response_missing_{field}")
        if payload.get("transport", "gateway") != "gateway":
            raise RetellWebCallError("retell_webcall_transport_unsupported")
        self._call_id = payload["call_id"]
        self._access_token = payload["access_token"]
        self._ice = _ice_servers(payload.get("ice_servers"))

    async def _open_session(self) -> None:
        assert self._http is not None and self._call_id is not None
        self._track_future = asyncio.get_running_loop().create_future()
        pc = RTCPeerConnection(RTCConfiguration(iceServers=self._ice))
        self._pc = pc
        self._caller_track = _CallerAudioTrack()
        pc.addTrack(self._caller_track)
        control = pc.createDataChannel("control")

        @control.on("message")
        def _on_control(message: str | bytes) -> None:
            self._handle_control(message)

        @pc.on("track")
        def _on_track(track: MediaStreamTrack) -> None:
            if track.kind != "audio":
                return
            if self._track_future is None or self._track_future.done():
                return
            self._track_future.set_result(track)
            self._agent_ready.set()

        @pc.on("connectionstatechange")
        def _on_state() -> None:
            if pc.connectionState == "connected":
                self._pc_connected.set()
            if pc.connectionState in {"failed", "closed"}:
                self._agent_disconnected.set()

        await pc.setLocalDescription(await pc.createOffer())
        sessions_url = _sessions_url(self._config.api_url, self._call_id)
        try:
            async with self._http.post(
                sessions_url,
                headers=self._session_headers(),
                json={"identity": "client", "sdp": pc.localDescription.sdp},
            ) as response:
                if response.status not in (200, 201):
                    raise RetellWebCallError(
                        "retell_webcall_session_failed", response.status
                    )
                answer = await response.json()
        except _TIMEOUT_ERRORS as exc:
            raise RetellWebCallError("retell_webcall_session_timeout") from exc
        session_id = answer.get("session_id") if isinstance(answer, dict) else None
        sdp = answer.get("sdp") if isinstance(answer, dict) else None
        if not isinstance(session_id, str) or not isinstance(sdp, str):
            raise RetellWebCallError("retell_webcall_session_response_invalid")
        self._session_url = f"{sessions_url}/{session_id}"
        await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
        self._connected = True

    def _handle_control(self, message: str | bytes) -> None:
        try:
            event = json.loads(message)
        except (TypeError, ValueError):
            return
        if not isinstance(event, dict) or event.get("type") != "status":
            return
        if event.get("state") in CLOSING_STATES:
            logger.info(
                "retell_webcall_status",
                extra={"call_id": self._call_id, "state": event.get("state")},
            )
            self._agent_disconnected.set()

    def _session_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    async def send_audio(self, data: bytes, sample_rate: int) -> None:
        if self._caller_track is None or not self._connected:
            return
        if sample_rate != RETELL_SAMPLE_RATE:
            resampler = self._resamplers.setdefault(
                sample_rate,
                PCMResampler(from_rate=sample_rate, to_rate=RETELL_SAMPLE_RATE),
            )
            data = resampler.convert(data)
        self._caller_track.push(data)

    async def recv_audio(self) -> AsyncIterator[tuple[bytes, int]]:
        if self._track_future is None:
            return
        try:
            track = await asyncio.wait_for(
                self._track_future, timeout=AGENT_TRACK_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError as exc:
            self._connected = False
            raise RetellWebCallError("retell_webcall_agent_track_timeout") from exc
        resampler = av.AudioResampler(format="s16", layout="mono")
        ended = asyncio.create_task(self._agent_disconnected.wait())
        frame_task: asyncio.Future[av.AudioFrame] | None = None
        try:
            while not self._agent_disconnected.is_set():
                frame_task = asyncio.ensure_future(track.recv())
                done, _ = await asyncio.wait(
                    {frame_task, ended},
                    timeout=RECV_FRAME_TIMEOUT_SECONDS,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if frame_task not in done:
                    if ended in done:
                        break
                    raise RetellWebCallError("retell_webcall_agent_audio_timeout")
                try:
                    frame = frame_task.result()
                except MediaStreamError:
                    break
                frame_task = None
                pcm = _mono_pcm(frame, resampler)
                if pcm:
                    yield pcm, frame.sample_rate
        finally:
            self._connected = False
            pending = [task for task in (frame_task, ended) if task is not None]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            logger.info("retell_audio_stream_closed call_id=%s", self._call_id)

    async def disconnect(self) -> None:
        self._connected = False
        self._agent_disconnected.set()
        try:
            if self._caller_track is not None:
                self._caller_track.stop()
            if self._pc is not None:
                await self._pc.close()
        finally:
            self._pc = None
            await self._end_session()

    async def _end_session(self) -> None:
        http, self._http = self._http, None
        session_url, self._session_url = self._session_url, None
        if http is None:
            return
        try:
            if session_url and self._access_token:
                async with http.delete(session_url, headers=self._session_headers()):
                    pass
        except (aiohttp.ClientError, asyncio.TimeoutError):
            logger.warning(
                "retell_webcall_session_end_failed", extra={"call_id": self._call_id}
            )
        finally:
            await http.close()

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def is_agent_ready(self) -> bool:
        return self._agent_ready.is_set()

    @property
    def call_id(self) -> str | None:
        return self._call_id
