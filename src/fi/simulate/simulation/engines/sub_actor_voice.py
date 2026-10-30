"""Sub-actors on a voice call: a voice near the person, someone who takes the phone from them, or the
person's own phone menu, which answers a call the agent places."""

from __future__ import annotations

import array
import asyncio
import logging
import math
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable

from fi.simulate.simulation.engines.caller_barge_in import complete_text
from fi.simulate.simulation.sub_actors import (
    HANDOFF_LINE_SECONDS,
    TOOLS_READ_SECONDS,
    Cast,
    Moment,
    limited,
    line_brief,
    line_gain,
)

logger = logging.getLogger(__name__)

# Ends a line whose speaker hands the phone back (a phone-taker) or puts the call through (a menu).
_DONE = "[DONE]"
_ANSWERED = "(the call has just been answered)"
_HOW = {
    "audio_mix": "Say one short thing you would say out loud near the call now, under 15 words. Output only the line.",
    "audio_handoff": "Say your next line into the phone, under 30 words. Output only the line. Stay on to hear "
    "the agent answer what you said and reply to it; end a line with "
    f"{_DONE} only once the agent has dealt with your part and you give the phone back; once your part is done, "
    f"output only {_DONE}.",
    "ivr": "Say the menu's next prompt, under 30 words. Output only the prompt. When the agent's reply is what "
    f"you are waiting for, output only {_DONE} and the call is put through.",
}
# Someone who has just taken the phone always says something of their own before handing it back.
_FIRST_HANDOFF = (
    "You have just taken the phone: say your line now, never only " + _DONE + "."
)


async def voiced(tts: Any, text: str) -> AsyncIterator[Any] | None:
    """The text's audio as the voice makes it, or None when it makes none. Waits only for the first
    frame, so a line starts as soon as it can be heard rather than once all of it is voiced."""
    if getattr(getattr(tts, "capabilities", None), "streaming", False):
        stream = tts.stream()
        stream.push_text(text)
        stream.end_input()
        frames = (item.frame async for item in stream)
        close = stream.aclose
    else:
        spoken = tts.synthesize(text)
        frames = (item.frame async for item in spoken)
        close = spoken.aclose

    try:
        first = await anext(frames, None)
    except BaseException:
        await close()
        raise
    if first is None:
        await close()
        return None

    async def rest() -> AsyncIterator[Any]:
        try:
            yield first
            async for frame in frames:
                yield frame
        finally:
            await close()

    return rest()


# Below this a frame is a pause, not speech (about 1% of full scale).
SILENCE_RMS = 328.0
# How much of a background line is measured before it starts, to set its level.
MEASURED_SECONDS = 0.5


def _samples(frame: Any) -> array.array:
    return array.array("h", bytes(frame.data))


def _rms(samples: array.array) -> float:
    return math.sqrt(sum(s * s for s in samples) / len(samples)) if samples else 0.0


async def at_level(
    frames: AsyncIterator[Any], ratio: float, person_rms: float | None
) -> AsyncIterator[Any]:
    """A background line at `ratio` of the person's level, measured on its own opening speech, with
    each frame held under the peak limit so the mix never clips."""
    from livekit import rtc

    opening, measured, rate = [], array.array("h"), 0
    async for frame in frames:
        opening.append(frame)
        rate = frame.sample_rate * frame.num_channels
        heard = _samples(frame)
        if _rms(heard) > SILENCE_RMS:
            measured.extend(heard)
        if len(measured) >= rate * MEASURED_SECONDS:
            break
    gain = line_gain(ratio, person_rms, _rms(measured))

    def scaled(frame: Any) -> Any:
        samples = _samples(frame)
        peak = max((abs(s) for s in samples), default=0)
        factor = limited(gain, peak)
        data = array.array(
            "h", (max(-32768, min(32767, round(s * factor))) for s in samples)
        )
        return rtc.AudioFrame(
            data=data.tobytes(),
            sample_rate=frame.sample_rate,
            num_channels=frame.num_channels,
            samples_per_channel=frame.samples_per_channel,
        )

    for frame in opening:
        yield scaled(frame)
    async for frame in frames:
        yield scaled(frame)


class SubActorVoice:
    """One sub-actor's lines on a call: written from what the agent just said, then voiced."""

    def __init__(
        self,
        *,
        actor: dict[str, Any],
        model: Any,
        tts: Any,
        situation: str,
        language: str,
    ) -> None:
        self.actor = actor
        self.model = model
        self.tts = tts
        self.situation = situation
        self.language = language
        self.said: list[str] = []

    @property
    def channel(self) -> str:
        return str(self.actor.get("channel") or "")

    async def line(
        self, conversation: list[str]
    ) -> tuple[str, AsyncIterator[Any] | None, bool]:
        """The next line, its audio as it is made, and whether this sub-actor is done after it."""
        from livekit.agents import llm as livekit_llm

        context = livekit_llm.ChatContext.empty()
        brief = line_brief(
            self.actor,
            situation=self.situation,
            said=self.said,
            conversation=conversation,
            language=self.language,
        )
        how = _HOW[self.channel]
        if self.channel == "audio_handoff" and not self.said:
            how += " " + _FIRST_HANDOFF
        context.add_message(role="user", content=brief + "\n\n" + how)
        written = await complete_text(self.model, context)
        # Wherever the model put the marker, it is never spoken.
        done = _DONE in written
        text = re.sub(r"\s*\[DONE\][.!?,]*", "", written).strip().strip('"“”').strip()
        return text, (await voiced(self.tts, text) if text else None), done


def _failed(exc: Exception) -> str:
    """Why a line failed, with the provider's status when it gave one."""
    logger.warning("sub-actor line failed", exc_info=exc)
    status = getattr(exc, "status_code", None)
    return f"line failed: {type(exc).__name__}" + (f" {status}" if status else "")


class SubActorsOnCall:
    """Every sub-actor on one voice call, told about each agent turn once, within the call's budget."""

    def __init__(
        self,
        voices: list[SubActorVoice],
        *,
        seed: str,
        play: Callable[[list[Any], float, str, str], Awaitable[None]] | None,
        say: Callable[[list[Any], str, str], Awaitable[None]] | None,
        tools_called: Callable[[], list[str]] | None = None,
        conversation: Callable[[], list[str]] = list,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.voices = {str(voice.actor["id"]): voice for voice in voices}
        self.cast = Cast([voice.actor for voice in voices], seed, clock=clock)
        self.play = play
        self.say = say
        # Read only for a sub-actor that enters on a tool call: the person's turn waits on it.
        self.tools_called = (
            tools_called
            if any(
                (voice.actor.get("trigger") or {}).get("kind") == "on_tool"
                for voice in voices
            )
            else None
        )
        self.conversation = conversation
        self.clock = clock
        self.agent_turns = 0
        self._pending: dict[str, asyncio.Task[None]] = {}
        # Agent turns can overlap; one line at a time on the phone, decided once the last has ended.
        self._phone = asyncio.Lock()
        # One line written and voiced at a time across the call; the others wait their turn.
        self._writing = asyncio.Lock()
        self._greeting: asyncio.Task[Any] | None = None

    def __bool__(self) -> bool:
        return bool(self.voices)

    def menu(self) -> SubActorVoice | None:
        return next(
            (voice for voice in self.voices.values() if voice.channel == "ivr"), None
        )

    def prepare_menu(self) -> None:
        """Writes and voices a phone menu's greeting while the call connects, so it answers at once."""
        menu = self.menu()
        if menu is not None and self.say is not None and menu.tts is not None:
            self._greeting = asyncio.create_task(menu.line([_ANSWERED]))

    async def answer_first(self) -> None:
        """A phone menu answers the call before the person; the person comes on once it puts the call through."""
        menu = self.menu()
        if menu is None:
            return
        self.cast.entering(Moment(agent_turns=1))
        self.cast.holder = str(menu.actor["id"])
        self.cast.line_wanted(menu.actor)
        await self._speak(menu, [_ANSWERED], written=self._greeting)

    def menu_cannot_answer(self, reason: str) -> None:
        """The agent greeted first, so the person's menu never answers this call."""
        if self._greeting is not None:
            self._greeting.cancel()
        menu = self.menu()
        if menu is not None:
            self.cast.not_entered(menu.actor, reason)

    async def agent_turn(self, text: str) -> bool:
        """One agent turn. True while a sub-actor holds the phone, so the person stays silent."""
        self.agent_turns += 1
        tools = []
        if self.tools_called and self.cast.waits_on_tools():
            try:
                tools = list(
                    await asyncio.wait_for(
                        asyncio.to_thread(self.tools_called), TOOLS_READ_SECONDS
                    )
                )
            except asyncio.TimeoutError:
                logger.warning("sub-actors: the tool trace took too long to read")
        moment = Moment(
            agent_turns=self.agent_turns, last_text=text, tools_called=tools
        )
        self.cast.entering(moment)
        heard = [*self.conversation(), f"The agent: {text}"]
        took_part = False
        holding = False
        for key, voice in self.voices.items():
            if voice.channel == "audio_mix":
                if (
                    key not in self._pending
                    and self.cast.may_speak(voice.actor, self.agent_turns)
                    and self.cast.line_wanted(voice.actor)
                ):
                    self._pending[key] = asyncio.create_task(
                        self._background(voice, heard)
                    )
                    took_part = True
                continue
            async with self._phone:
                if self.cast.holder not in ("", key) or not self.cast.may_speak(
                    voice.actor, self.agent_turns
                ):
                    continue
                if not self.cast.line_wanted(voice.actor):
                    self.cast.finish(voice.actor)
                    continue
                self.cast.holder = key
                took_part = True
                holding = await self._speak(voice, heard) or holding
        if took_part:
            self.cast.took_turn()
        return holding

    async def _speak(
        self,
        voice: SubActorVoice,
        heard: list[str],
        written: Awaitable[tuple[str, Any, bool]] | None = None,
    ) -> bool:
        """One line on the phone, or the one already being written. True while this sub-actor still holds it."""
        if self.say is None or voice.tts is None:
            self.cast.line_settled()
            self.cast.dropped(voice.actor, "no voice of its own on this call")
            self.cast.finish(voice.actor)
            return False
        asked = self.clock()
        limit = self.cast.line_limit(HANDOFF_LINE_SECONDS)
        try:
            text, frames, done = await asyncio.wait_for(
                written or self._written(voice, heard), limit
            )
        except asyncio.TimeoutError:
            self.cast.late(voice.actor, cut_by_call_time=limit < HANDOFF_LINE_SECONDS)
            self.cast.finish(voice.actor)
            return False
        except Exception as exc:  # noqa: BLE001 - a failed line hands the phone back, never fails the call
            self.cast.dropped(voice.actor, _failed(exc))
            self.cast.finish(voice.actor)
            return False
        finally:
            self.cast.line_settled()
        if not frames:
            if text:
                self.cast.dropped(voice.actor, "voice failed")
            elif not voice.said:
                self.cast.dropped(voice.actor, "wrote no line")
            self.cast.finish(voice.actor)
            return False
        started = self.clock()
        await self.say(frames, text, str(voice.actor.get("name") or "someone"))
        voice.said.append(text)
        self.cast.delivered(
            voice.actor, text, self.agent_turns, started, self.clock(), asked
        )
        if done:
            self.cast.finish(voice.actor)
        return not done

    async def _written(
        self, voice: SubActorVoice, heard: list[str]
    ) -> tuple[str, Any, bool]:
        async with self._writing:
            return await voice.line(heard)

    async def _background(self, voice: SubActorVoice, heard: list[str]) -> None:
        key = str(voice.actor["id"])
        if self.play is None or voice.tts is None:
            self.cast.line_settled()
            self.cast.dropped(voice.actor, "no voice of its own on this call")
            self.cast.finish(voice.actor)
            self._pending.pop(key, None)
            return
        asked = self.clock()
        limit = self.cast.line_limit(HANDOFF_LINE_SECONDS)
        try:
            try:
                text, frames, _done = await asyncio.wait_for(
                    self._written(voice, heard), limit
                )
            finally:
                self.cast.line_settled()
            if not frames:
                self.cast.dropped(voice.actor, "voice failed")
                return
            started = self.clock()
            await self.play(
                frames,
                float(voice.actor.get("loudness_ratio") or 1.0),
                text,
                str(voice.actor.get("name") or "someone"),
            )
            voice.said.append(text)
            self.cast.delivered(
                voice.actor, text, self.agent_turns, started, self.clock(), asked
            )
        except asyncio.TimeoutError:
            self.cast.late(voice.actor, cut_by_call_time=limit < HANDOFF_LINE_SECONDS)
        except asyncio.CancelledError:
            self.cast.dropped(voice.actor, "call ended before it was said")
        except Exception as exc:  # noqa: BLE001 - a quiet background voice is a weaker test, never a failed call
            self.cast.dropped(voice.actor, _failed(exc))
        finally:
            self._pending.pop(key, None)

    def events(self) -> list[dict[str, Any]]:
        return list(self.cast.events)

    def summary(self) -> list[dict[str, Any]]:
        return self.cast.summary()

    async def close(self) -> None:
        tasks = list(self._pending.values()) + [
            task for task in (self._greeting,) if task is not None
        ]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.cast.hang_up()


def with_sub_actor_turns(
    messages: list[dict[str, Any]], events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The call's messages with each delivered sub-actor line placed where it was heard."""
    spoken = [
        {
            "role": "sub_actor",
            "sub_actor": event["sub_actor"],
            "name": event.get("name"),
            "content": event["text"],
            "created_at": event["at"],
            "started_speaking_at": event["at"],
            "stopped_speaking_at": event.get("ended_at"),
            "interrupted": False,
            "e2e_latency": None,
        }
        for event in events
        if event.get("kind") == "delivered" and event.get("text")
    ]
    if not spoken:
        return messages

    def when(message: dict[str, Any]) -> float:
        value = message.get("started_speaking_at") or message.get("created_at")
        return float(value) if isinstance(value, (int, float)) else float("inf")

    ordered = list(messages)
    for line in spoken:
        index = next(
            (i for i, one in enumerate(ordered) if when(one) > when(line)), len(ordered)
        )
        ordered.insert(index, line)
    return ordered


def world_access(dsn: str) -> Callable[[], list[str]] | None:
    """The names of the tools the agent has called so far, read from the run's live tool trace."""
    if not dsn:
        return None
    from fi.alk.harness.call_runner import read_tool_trace

    def tools_called() -> list[str]:
        try:
            return [
                str(record.get("name"))
                for record in read_tool_trace(dsn, timeout_ms=2000)
            ]
        except Exception:  # noqa: BLE001 - not knowing the tools so far never holds up the call
            logger.warning("sub-actors could not read the tool trace", exc_info=True)
            return []

    return tools_called
