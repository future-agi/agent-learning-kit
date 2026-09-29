from __future__ import annotations

import asyncio
import time
import wave
from types import SimpleNamespace

import numpy as np

from fi.simulate.simulation.engines.caller_barge_in import (
    CallerBargeIn,
    _unsafe_candidate,
    caller_language,
    selected_call_indices,
)
from livekit import rtc


class _History:
    def __init__(self):
        self.items = []

    def add_message(self, *, role, content):
        message = SimpleNamespace(
            id=f"message-{len(self.items)}", type="message", role=role,
            text_content=content,
        )
        self.items.append(message)
        return message

    def messages(self):
        return list(self.items)

    def copy(self):
        copied = _History()
        copied.items = list(self.items)
        return copied


class _Session:
    def __init__(self):
        self.history = _History()
        self.agent_state = "listening"
        self.current_speech = None


class _Output:
    def __init__(self):
        self.played = []
        self.cleared = False
        self.closed = False

    async def prepare(self, frame):
        assert frame.duration == 0.5

    async def play(self, frames, on_started):
        await on_started()
        self.played.append(frames)
        await asyncio.sleep(0.04)

    def clear(self):
        self.cleared = True

    async def close(self):
        self.closed = True


class _ModelStream:
    def __init__(self, phrase):
        self.phrase = phrase

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def __aiter__(self):
        async def chunks():
            yield SimpleNamespace(delta=SimpleNamespace(content=self.phrase))

        return chunks()


class _Model:
    def __init__(self):
        self.contexts = []

    def chat(self, *, chat_ctx, tools, tool_choice):
        assert tools == [] and tool_choice == "none"
        self.contexts.append(chat_ctx)
        return _ModelStream(
            "Could we move a little faster?"
            if len(self.contexts) == 1
            else "Yeah, I understand."
        )


class _TTSStream:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def __aiter__(self):
        async def frames():
            yield SimpleNamespace(frame=SimpleNamespace(duration=0.5))

        return frames()


class _TTS:
    def synthesize(self, text):
        assert text in {"Could we move a little faster?", "Yeah, I understand."}
        return _TTSStream()


def _controller(session, **kwargs):
    model = _Model()
    async def update_chat_ctx(chat_ctx):
        agent.chat_ctx = chat_ctx

    agent = SimpleNamespace(
        instructions="You are a hurried guest caller.",
        chat_ctx=_History(),
        update_chat_ctx=update_chat_ctx,
    )
    controller = CallerBargeIn(
        session=session,
        agent=agent,
        model=model,
        tts=_TTS(),
        audio_output=_Output(),
        **kwargs,
    )
    return controller, model


def test_selection_is_stable_and_selects_whole_calls() -> None:
    assert selected_call_indices("run-123", 10, 0.2) == selected_call_indices(
        "run-123", 10, 0.2
    )
    assert len(selected_call_indices("run-123", 10, 0.2)) == 2
    assert len(selected_call_indices("run-123", 20, 0.2)) == 4
    assert selected_call_indices("run-123", 10, 0) == set()
    assert (
        12
        <= sum(
            bool(selected_call_indices(f"hosted-{index}", 1, 0.2))
            for index in range(100)
        )
        <= 28
    )


def test_language_follows_persona_and_unknown_language_is_not_forced_english() -> None:
    assert caller_language(SimpleNamespace(persona={"language": "Spanish"})) == "es"
    assert (
        caller_language(SimpleNamespace(persona={"languages": ["English", "Spanish"]}))
        == "en"
    )
    assert caller_language(SimpleNamespace(persona={"language": "French"})) is None


def test_candidate_safety_rejects_pin_repeats_and_duplicate_interjections() -> None:
    previous = ["Could we move a little faster?"]
    assert _unsafe_candidate("My PIN is seven six eight two.", previous)
    assert _unsafe_candidate("My PIN is 7682.", previous)
    assert _unsafe_candidate("Could we move a little faster!", previous)
    assert _unsafe_candidate("So you are an AI.", ["Wait, are you an AI?"])
    assert _unsafe_candidate("Okay, so you are an AI.", ["Wait, are you an AI?"])
    assert not _unsafe_candidate("I need a ride", previous)
    assert not _unsafe_candidate("Okay, thanks.", ["Wait, are you an AI?"])
    assert _unsafe_candidate(
        "I'm in a bit of a hurry.",
        [],
        ["My PIN is ready. I'm in a bit of a hurry. I need a ride."],
    )
    assert not _unsafe_candidate(
        "Could we move faster?", [], ["I'm in a bit of a hurry."]
    )


def test_repeated_candidate_is_regenerated_with_a_different_reaction() -> None:
    async def exercise():
        controller, model = _controller(
            _Session(), language="en", seed="varied-retry"
        )
        phrases = iter(["So you are an AI.", "Yeah, I understand."])
        model.chat = lambda **_kwargs: _ModelStream(next(phrases))
        controller.events.append(
            {"text": "Wait, are you an AI?", "speech_state_overlap": False}
        )
        controller.dialogue_started()
        controller.prepare_next()
        await controller._preparation
        assert controller._candidate is not None
        assert controller._candidate[0] == "Yeah, I understand."
        assert controller.summary()["diagnostics"]["preparation_rejected_unsafe"] == 1
        await controller.close()

    asyncio.run(exercise())


def test_multiple_barge_ins_are_generated_from_live_context() -> None:
    async def exercise():
        session = _Session()
        controller, model = _controller(
            session, language="en", seed="case-1", delay=0.001, cooldown=0
        )
        controller.start()
        controller.target_started()  # opening is not a candidate
        controller._target_started_at -= 2
        await asyncio.sleep(0.02)
        controller.target_ended()
        assert controller.audio_output.played == []
        controller.dialogue_started()
        controller.prepare_next()
        await controller._preparation
        assert model.contexts[0].messages()[0].role == "system"
        controller.target_started()
        controller._target_started_at -= 2
        await asyncio.sleep(0.02)
        assert len(controller.audio_output.played) == 1
        assert session.history.items[0].text_content == "Could we move a little faster?"
        controller.target_ended()
        await controller.wait_for_interjection()
        # Force the later-turn coin flip to take the next interjection.
        controller._random.random = lambda: 0.0
        controller.prepare_next()
        await controller._preparation
        controller.target_started()
        controller._target_started_at -= 2
        await asyncio.sleep(0.02)
        controller.target_ended()
        await controller.wait_for_interjection()
        assert len(controller.audio_output.played) == 2
        assert all(event["kind"] == "persona_interjection" for event in controller.events)
        assert controller.summary()["speech_state_overlaps"] == 2
        await controller.close()

    asyncio.run(exercise())


def test_short_target_turn_does_not_trigger_post_turn_speech() -> None:
    async def exercise():
        session = _Session()
        controller, _ = _controller(session, language="es", seed="case-2", delay=0.2)
        controller.start()
        controller.dialogue_started()
        controller.prepare_next()
        await controller._preparation
        controller.target_started()
        controller.target_ended()
        await asyncio.sleep(0.25)
        assert controller.audio_output.played == []
        await controller.close()

    asyncio.run(exercise())


def test_transcription_gate_saves_interjection_for_substantive_turn() -> None:
    async def exercise():
        session = _Session()
        controller, _ = _controller(
            session, language="en", seed="short-filler", delay=0.001, cooldown=0
        )
        controller.dialogue_started()
        controller.prepare_next()
        await controller._preparation
        controller.target_started(wait_for_text=True)
        controller.target_text("Let me check your PIN seven six eight two.")
        await asyncio.sleep(0.02)
        assert controller.audio_output.played == []
        controller.target_ended()
        assert controller._candidate is not None

        controller.target_started(wait_for_text=True)
        controller._target_started_at -= 2
        controller.target_text(
            "We have two pickup options at the airport for your ride today."
        )
        await asyncio.sleep(0.02)
        assert len(controller.audio_output.played) == 1
        controller.target_ended()
        await controller.wait_for_interjection()
        await controller.close()

    asyncio.run(exercise())


def test_audio_gate_waits_for_sustained_target_speech() -> None:
    async def exercise():
        controller, _ = _controller(
            _Session(), language="en", seed="audio-gate", delay=0.001,
            cooldown=0,
        )
        controller.dialogue_started()
        controller.prepare_next()
        await controller._preparation
        controller.target_started(wait_for_audio=True)
        controller._target_started_at -= 3
        voiced = rtc.AudioFrame(
            data=np.full(160, 2000, dtype="<i2").tobytes(),
            sample_rate=8000, num_channels=1, samples_per_channel=160,
        )
        for _ in range(180):
            controller.target_audio_frame(voiced)
        await asyncio.sleep(0.02)
        assert controller.audio_output.played == []
        for _ in range(30):
            controller.target_audio_frame(voiced)
        await asyncio.sleep(0.02)
        assert len(controller.audio_output.played) == 1
        controller.target_ended()
        await controller.wait_for_interjection()
        controller._random.random = lambda: 0.0
        controller.prepare_next()
        await controller._preparation
        controller.target_started(wait_for_audio=True)
        controller._target_started_at -= 3
        for _ in range(210):
            controller.target_audio_frame(voiced)
        await asyncio.sleep(0.02)
        assert len(controller.audio_output.played) == 2
        controller.target_ended()
        await controller.wait_for_interjection()
        await controller.close()

    asyncio.run(exercise())


def test_interjection_finishes_after_target_end_before_reply() -> None:
    async def exercise():
        session = _Session()
        controller, _ = _controller(
            session, language="en", seed="case-3", delay=0.001, cooldown=0
        )
        controller.dialogue_started()
        controller.prepare_next()
        await controller._preparation
        controller.target_started()
        controller._target_started_at -= 2
        await asyncio.sleep(0.02)
        assert len(controller.audio_output.played) == 1
        controller.target_ended()
        await controller.wait_for_interjection()
        assert controller._finishing_task is None
        assert not controller.audio_output.cleared
        await controller.close()

    asyncio.run(exercise())


def test_audio_overlap_requires_energy_on_both_recorded_channels(tmp_path) -> None:
    rate = 8000
    stereo = np.zeros((rate * 3, 2), dtype=np.int16)
    stereo[rate : rate * 2, 1] = 4000  # target speaks for one second
    stereo[int(rate * 1.4) : int(rate * 1.7), 0] = 4000  # caller speaks over it
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as recording:
        recording.setnchannels(2)
        recording.setsampwidth(2)
        recording.setframerate(rate)
        recording.writeframes(stereo.tobytes())
    controller, _ = _controller(_Session(), language="en", seed="a")
    started = time.time()
    controller.events = [
        {
            "attempted_at": started + 1.2,
            "clip_duration_s": 0.5,
            "speech_state_overlap": True,
        }
    ]
    controller.measure_audio(str(path), started)
    assert controller.events[0]["audio_overlap_ms"] >= 250
    assert controller.summary()["audio_verified_overlaps"] == 1
