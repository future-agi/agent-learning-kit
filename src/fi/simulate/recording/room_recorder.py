from __future__ import annotations

import asyncio
import contextlib
import os
import wave
from typing import Optional

try:
    from livekit import rtc
    from livekit.api import AccessToken, VideoGrants
except ImportError:
    # LiveKit is an optional dependency. In cloud-only usage, we silently skip it.
    rtc = None
    AccessToken = None
    VideoGrants = None


class RoomRecorder:
    def __init__(
        self,
        *,
        url: str,
        api_key: str,
        api_secret: str,
        room_name: str,
        identity: str = "recorder",
        sample_rate: int = 8000,
        output_dir: str = "recordings",
        join_delay_s: float = 0.2,
    ) -> None:
        self._url = url
        self._api_key = api_key
        self._api_secret = api_secret
        self._room_name = room_name
        self._identity = identity
        self._sample_rate = sample_rate
        self._output_dir = output_dir
        self._join_delay_s = join_delay_s
        self._room: Optional[rtc.Room] = None
        self._running = False

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        await asyncio.sleep(max(0.0, self._join_delay_s))

        token = (
            AccessToken(self._api_key, self._api_secret)
            .with_identity(self._identity)
            .with_grants(VideoGrants(room_join=True, room=self._room_name))
            .to_jwt()
        )

        room = rtc.Room()
        await room.connect(self._url, token)
        self._room = room

        os.makedirs(self._output_dir, exist_ok=True)

        async def _record_for_track(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant) -> None:
            try:
                if getattr(track, "kind", None) != rtc.TrackKind.KIND_AUDIO:
                    return
                path = os.path.join(self._output_dir, f"{self._room_name}-{participant.identity}-track-{publication.sid}.wav")
                print(f"Recorder: writing {path}")
                try:
                    stream = rtc.AudioStream(track, sample_rate=self._sample_rate, num_channels=1)
                except Exception:
                    return
                try:
                    with wave.open(path, "wb") as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(self._sample_rate)
                        async for ev in stream:
                            wf.writeframes(ev.frame.data)
                finally:
                    with contextlib.suppress(Exception):
                        await stream.aclose()
            except Exception:
                pass

        @room.on("track_subscribed")
        def _on_track_subscribed(track: rtc.Track, publication: rtc.RemoteTrackPublication, participant: rtc.RemoteParticipant):
            try:
                asyncio.create_task(_record_for_track(track, publication, participant))
            except Exception:
                pass

        # Also attach to any already-available tracks (if joining mid-call)
        try:
            for rp in list(room.remote_participants.values()):
                for pub in list(rp.track_publications.values()):
                    tr = getattr(pub, "track", None)
                    if tr is not None:
                        asyncio.create_task(_record_for_track(tr, pub, rp))
        except Exception:
            pass

        # remain running until aclose is called

    async def aclose(self) -> None:
        self._running = False
        if self._room is not None:
            with contextlib.suppress(Exception):
                await self._room.disconnect()
            self._room = None


