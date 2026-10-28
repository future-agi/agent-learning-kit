"""How a sub-actor can reach the agent under test, and when one enters.

Shared by the build, which refuses a sub-actor no run could deliver, and by every runner. Each
channel names the agent types it reaches and whether a runner can deliver it yet, so a new agent
type adds a channel here instead of a branch in the code that reads it.
"""

from __future__ import annotations

import hashlib
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

CHANNELS: dict[str, dict[str, Any]] = {
    "audio_mix": {
        "modalities": {"voice"},
        "available": True,
        "about": "a voice near the person that speaks up whenever it has something to say, which the agent may hear",
        "tells_person": "With you, and may speak during the call: {who}.",
        "setting": "You are near someone who is on a phone call with an automated agent. You are not on "
        "the call yourself; the agent may still hear you.",
    },
    "audio_handoff": {
        "modalities": {"voice"},
        "available": True,
        "about": "someone who takes the phone from the person for a few turns, then hands it back",
        "tells_person": "{who} may take the phone from you for a few turns. While they have it, say nothing; carry on once they hand it back.",
        "setting": "You have just taken the phone from the person, partway through their call with an automated "
        "agent, to say something of your own before you hand it back.",
    },
    "ivr": {
        "modalities": {"voice"},
        "available": True,
        "about": "a phone menu on the person's own line that answers a call the agent places, before the person, "
        "and puts the call through to them",
        "tells_person": "{who} answers your line first. Say nothing until it has put the call through to you.",
        "setting": "You are the automated phone menu that answers this line before a person does.",
    },
    "thread_message": {
        "modalities": {"chat"},
        "available": True,
        "about": "a second participant in the chat, seen by the agent and the person",
        "tells_person": "{who} is in this chat with you and may write to the agent too. Give them a chance to write before you end the conversation.",
        "setting": "You are a second person in a chat between someone and an automated agent.",
    },
    "ui_event": {
        "modalities": {"computer_use"},
        "available": False,
        "about": "something on the screen",
    },
    "page_content": {
        "modalities": {"browser"},
        "available": False,
        "about": "content on a page",
    },
    "repo_change": {
        "modalities": {"coding"},
        "available": False,
        "about": "a change in the repository",
    },
    "peer_agent": {
        "modalities": {"multi_agent"},
        "available": False,
        "about": "a message from another agent",
    },
}

TRIGGER_ABOUT: dict[str, str] = {
    "always": "after the agent's first turn (a phone menu answers the call itself)",
    "mid_journey": "after the agent's turn `turn`",
    "on_tool": "on call `on_call` of the agent's tool `tool`, a tool that runs in the simulated world "
    "(one that runs only inside the agent cannot be seen)",
    "on_keyword": "when one of `words` is in the agent's latest turn",
    "probability": "with chance `p`, drawn once per call from the run's seed",
}
TRIGGERS = tuple(TRIGGER_ABOUT)


def someone_else(name: str) -> str:
    """How a sub-actor's turn is labelled wherever grading reads a conversation."""
    return f"{name or 'Someone else'} (not the customer)"


def with_others(situation: str, actors: list[dict[str, Any]]) -> str:
    """The person's situation, saying who else is there and what the person does around them."""
    told = [
        template.format(
            who=f"{one['name']} ({one['role']})"
            if one.get("role")
            else str(one["name"])
        )
        for one in actors
        if (
            template := (CHANNELS.get(str(one.get("channel") or "")) or {}).get(
                "tells_person"
            )
        )
    ]
    return situation + "\n\n" + "\n".join(told) if told else situation


def available_channels(modality: str = "") -> list[str]:
    """The channels a runner can deliver, to an agent of this kind when one is named."""
    return [
        name
        for name, entry in CHANNELS.items()
        if entry["available"] and (not modality or modality in entry["modalities"])
    ]


def described(entries: dict[str, str]) -> str:
    return "; ".join(f"`{name}`: {about}" for name, about in entries.items())


# Tunable defaults, the same for every scenario, so no sub-actor and no group of them can take over a call.
LIMITS: dict[str, int] = {
    "audio_mix": 3,
    "audio_handoff": 3,
    "ivr": 3,
    "thread_message": 3,
}
HANDOFF_LINE_SECONDS = 30.0
TOOLS_READ_SECONDS = 1.0
# How much of the conversation a sub-actor hears before writing a line.
CONVERSATION_LINES = 12
MAX_ACTIVE = 2
TAKEOVER_AFTER_TURNS = 4
# Someone can only take the phone from a person who already has it: not before the agent's second turn, by
# which point the person has spoken at least once. A trigger that fires earlier waits for that turn.
TAKES_PHONE_FROM_TURN = 2
# Wall-clock for all sub-actors together, from the first trigger: turns stop advancing while a line
# is written or the agent is slow, so time bounds what turns cannot.
SUB_ACTOR_SECONDS = 60.0
# Lines asked for and not yet ready; one more is dropped rather than queued behind them.
MAX_WAITING_LINES = 2
TIME_UP = "sub-actor time used up"
# A background voice's level as an amplitude ratio to the person's voice (its RMS over theirs, on
# each line), drawn per call from the run seed: 1/3 to 1/1.5 of the person, about -9.5 to -3.5 dB.
LOUDNESS_RANGE = (1 / 3, 1 / 1.5)
# The highest a background line may peak once scaled, as a share of full scale, so the mix never clips.
PEAK_LIMIT = 0.9
SPEAKS_AGAIN = 0.5


def seeded(seed: str, *parts: object) -> random.Random:
    """A random source fixed by the run's seed and what it is for, so a repeated run draws the same."""
    key = "|".join([seed, *map(str, parts)])
    return random.Random(
        int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big")
    )


def loudness_ratio(seed: str, sub_actor_id: str, fixed: float | None) -> float:
    """How loud a background voice is against the person on this call: the list's own ratio when it
    sets one, else a draw from the run seed."""
    if fixed is not None:
        return fixed
    return seeded(seed, sub_actor_id, "loudness").uniform(*LOUDNESS_RANGE)


def line_gain(ratio: float, person_rms: float | None, line_rms: float) -> float:
    """The factor that puts a line at `ratio` of the person's measured level; the ratio itself when
    either level is unknown."""
    if not person_rms or not line_rms:
        return ratio
    return ratio * person_rms / line_rms


def limited(gain: float, peak: int) -> float:
    """The gain, lowered for a frame whose peak it would push past the limit."""
    ceiling = PEAK_LIMIT * 32767
    return min(gain, ceiling / peak) if peak * gain > ceiling else gain


@dataclass
class Moment:
    """What a run knows after one step of the agent: enough to decide whether a sub-actor is due."""

    agent_turns: int
    last_text: str = ""
    tools_called: list[str] = field(default_factory=list)


def due(
    trigger: dict[str, Any], moment: Moment, *, seed: str, sub_actor_id: str
) -> bool:
    kind = str(trigger.get("kind") or "always")
    if kind == "always":
        return moment.agent_turns >= 1
    if kind == "mid_journey":
        return moment.agent_turns >= int(trigger.get("turn") or 0) >= 1
    if kind == "on_tool":
        tool = str(trigger.get("tool") or "")
        return bool(tool) and moment.tools_called.count(tool) >= int(
            trigger.get("on_call") or 1
        )
    if kind == "on_keyword":
        words = [
            str(one).strip().lower()
            for one in trigger.get("words") or []
            if str(one).strip()
        ]
        said = moment.last_text.lower()
        return any(re.search(rf"\b{re.escape(word)}\b", said) for word in words)
    if kind == "probability":
        drawn = seeded(seed, sub_actor_id, "enters").random() < float(
            trigger.get("p") or 0
        )
        return moment.agent_turns >= 1 and drawn
    return False


def line_brief(
    actor: dict[str, Any],
    *,
    situation: str,
    said: list[str],
    conversation: list[str],
    language: str,
) -> str:
    """What a sub-actor is given each time it writes a line, on any channel."""
    role = f", {actor['role']}" if actor.get("role") else ""
    setting = (CHANNELS.get(str(actor.get("channel") or "")) or {}).get("setting") or ""
    parts = [
        f"You are {actor.get('name') or 'someone'}{role}. {setting}",
        f"What is going on (this is the other person's situation, not yours):\n{situation}",
        f"What you do:\n{actor.get('instruction') or ''}",
        "You are not the other person. Never give their codes, numbers, addresses or account details, never "
        "make their request or answer a question put to them, and never confirm or choose anything for them: "
        "what they came for is theirs to say. Say only what you yourself want or know.",
        f"Speak {language}." if language else "",
        "What you have said so far: " + " | ".join(said) if said else "",
        (
            "The conversation so far, latest last:\n"
            + "\n".join(conversation[-CONVERSATION_LINES:])
            if conversation
            else "Nothing has been said yet."
        ),
        "Respond to where it is now, and never repeat what you have already said.",
    ]
    return "\n\n".join(part for part in parts if part)


class Cast:
    """Every sub-actor in one call: who enters when, the call-wide budget, and what happened to each.

    Each actor is a resolved entry: its list fields plus the scenario's `instruction` and `trigger`.
    """

    def __init__(
        self,
        actors: list[dict[str, Any]],
        seed: str,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.actors = actors
        self.seed = seed
        self.clock = clock
        self._started_at: float | None = None
        self._time_up = False
        self._waiting_lines = 0
        self._state = {
            str(one["id"]): {
                "entered": False,
                "lines": 0,
                "last_turn": -2,
                "done": False,
            }
            for one in actors
        }
        self._waiting: list[str] = []
        self._held: set[str] = set()
        self._activity = 0
        self.holder = ""
        self.events: list[dict[str, Any]] = []

    def _active(self) -> list[str]:
        return [
            key
            for key, state in self._state.items()
            if state["entered"] and not state["done"]
        ]

    def remaining(self) -> float:
        """Seconds left of the call's sub-actor time; all of it until the first trigger."""
        if self._started_at is None:
            return SUB_ACTOR_SECONDS
        return max(0.0, SUB_ACTOR_SECONDS - (self.clock() - self._started_at))

    def out_of_time(self) -> bool:
        """Whether the call's sub-actor time is used up; the first time it is, every sub-actor stops."""
        if not self._time_up and self._started_at is not None and not self.remaining():
            self._time_up = True
            for actor in self.actors:
                if self._state[str(actor["id"])]["entered"]:
                    self.finish(actor, TIME_UP)
            for key in self._waiting:
                self._record(key, "dropped", reason=TIME_UP)
            self._waiting.clear()
        return self._time_up

    def line_limit(self, seconds: float) -> float:
        """How long the next line may take to be ready: its own limit, never past the call's time."""
        return min(seconds, self.remaining())

    def line_wanted(self, actor: dict[str, Any]) -> bool:
        """Room for one more line to be written; a line due while the queue is full is dropped."""
        if self._waiting_lines >= MAX_WAITING_LINES:
            self.dropped(actor, "queue full")
            return False
        self._waiting_lines += 1
        return True

    def line_settled(self) -> None:
        """A line asked for is ready, or dropped."""
        self._waiting_lines = max(0, self._waiting_lines - 1)

    def late(self, actor: dict[str, Any]) -> None:
        """A line not ready within its limit, or cut off by the call's sub-actor time."""
        self.dropped(actor, TIME_UP if self.out_of_time() else "not ready in time")

    def entering(self, moment: Moment) -> list[dict[str, Any]]:
        """The sub-actors that enter now: due ones in the scenario's order, while there is room."""
        if self.out_of_time():
            return []
        for actor in self.actors:
            key = str(actor["id"])
            state = self._state[key]
            if state["entered"] or state["done"] or key in self._waiting:
                continue
            if key in self._held or due(
                dict(actor.get("trigger") or {}),
                moment,
                seed=self.seed,
                sub_actor_id=key,
            ):
                if (
                    actor.get("channel") == "audio_handoff"
                    and moment.agent_turns < TAKES_PHONE_FROM_TURN
                ):
                    self._held.add(key)
                    continue
                self._held.discard(key)
                self._waiting.append(key)
                self._record(key, "triggered", turn=moment.agent_turns)
        entered = []
        while self._waiting and len(self._active()) < MAX_ACTIVE:
            key = self._waiting.pop(0)
            self._state[key]["entered"] = True
            entered.append(next(one for one in self.actors if str(one["id"]) == key))
        return entered

    def may_speak(self, actor: dict[str, Any], agent_turns: int) -> bool:
        """Whether this sub-actor may take part on this turn, within its own limit and the call's."""
        key = str(actor["id"])
        state = self._state[key]
        if not state["entered"] or state["done"] or self.out_of_time():
            return False
        if (
            state["lines"] >= LIMITS.get(str(actor.get("channel")), 1)
            or self._activity >= TAKEOVER_AFTER_TURNS
        ):
            self.finish(actor, "limit reached")
            return False
        if actor.get("channel") == "audio_mix" and state["lines"]:
            if state["last_turn"] == agent_turns - 1:
                return False
            return seeded(self.seed, key, "again", agent_turns).random() < SPEAKS_AGAIN
        return True

    def took_turn(self) -> None:
        """One agent turn spent with a sub-actor taking part, counted against the call-wide budget."""
        self._activity += 1

    def delivered(
        self,
        actor: dict[str, Any],
        text: str,
        agent_turns: int,
        at: float,
        ended_at: float,
        asked_at: float | None = None,
    ) -> None:
        state = self._state[str(actor["id"])]
        state["lines"] += 1
        state["last_turn"] = agent_turns
        # asked_at: when the line was asked for, so the time it took to be ready is on record.
        self._record(
            str(actor["id"]),
            "delivered",
            text=text,
            at=at,
            ended_at=ended_at,
            asked_at=asked_at,
        )

    def not_entered(self, actor: dict[str, Any], reason: str) -> None:
        """A sub-actor that cannot enter this call at all, with the reason."""
        self._state[str(actor["id"])]["done"] = True
        self._record(str(actor["id"]), "not_entered", reason=reason)

    def dropped(self, actor: dict[str, Any], reason: str) -> None:
        self._record(str(actor["id"]), "dropped", reason=reason)

    def finish(self, actor: dict[str, Any], reason: str = "") -> None:
        state = self._state[str(actor["id"])]
        if not state["done"]:
            state["done"] = True
            self._record(str(actor["id"]), "finished", reason=reason)
        if self.holder == actor["id"]:
            self.holder = ""

    def hang_up(self) -> None:
        for actor in self.actors:
            if self._state[str(actor["id"])]["entered"]:
                self.finish(actor, "call ended")
        for key in self._waiting:
            self._record(key, "dropped", reason="call ended before there was room")
        self._waiting.clear()

    def _record(self, key: str, kind: str, **details: Any) -> None:
        if kind == "triggered" and self._started_at is None:
            self._started_at = self.clock()
        actor = next(one for one in self.actors if str(one["id"]) == key)
        self.events.append(
            {
                "sub_actor": key,
                "name": actor.get("name"),
                "channel": actor.get("channel"),
                "kind": kind,
                **details,
            }
        )

    def summary(self) -> list[dict[str, Any]]:
        """Per sub-actor: whether it was triggered, how many lines reached the agent, and why any were dropped."""
        return [
            {
                "sub_actor": str(actor["id"]),
                "name": actor.get("name"),
                "triggered": any(
                    e["kind"] == "triggered" and e["sub_actor"] == str(actor["id"])
                    for e in self.events
                ),
                "delivered": self._state[str(actor["id"])]["lines"],
                "dropped": [
                    e["reason"]
                    for e in self.events
                    if e["kind"] == "dropped" and e["sub_actor"] == str(actor["id"])
                ],
            }
            for actor in self.actors
        ]
