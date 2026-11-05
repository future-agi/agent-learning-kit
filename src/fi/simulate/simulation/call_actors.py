"""How an actor can reach the agent under test, and when one enters.

Shared by the build, which refuses an actor no run could deliver, and by every runner. Each
channel names the agent types it reaches and whether a runner can deliver it yet, so a new agent
type adds a channel here instead of a branch in the code that reads it.
"""

from __future__ import annotations

import hashlib
import math
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

# Where a caller's own details start in its instruction; anyone with the caller is given only what comes before.
DETAILS_MARK = "Your details:"

CHANNELS: dict[str, dict[str, Any]] = {
    "audio_mix": {
        "modalities": {"voice"},
        "about": "a voice near the person that speaks up whenever it has something to say, which the agent may hear",
        "tells_person": "With you, and may speak during the call: {who}.",
        "setting": "You are near someone who is on a phone call with an automated agent. You are not on "
        "the call yourself; the agent may still hear you.",
    },
    "audio_handoff": {
        "modalities": {"voice"},
        "about": "someone who takes the phone from the person for a few turns to say something of their own, then "
        "hands it back; the person's request stays the person's, so they never carry it on, confirm it or finish it",
        "tells_person": "{who} may take the phone from you for a few turns. While they have it, say nothing; carry on once they hand it back.",
        "setting": "You have just taken the phone from the person, partway through their call with an automated "
        "agent, to say something of your own. Keep it while you still have something of your own to say or to "
        "answer, then hand it back. The call is the person's: never ask for, agree to, confirm or finish what they "
        "called about; they do that once they have the phone back.",
    },
    "ivr": {
        "modalities": {"voice"},
        "about": "a phone menu on the person's own line that answers a call the agent places, before the person, "
        "and puts the call through to them",
        "tells_person": "{who} may answer your line before you do. If it does, say nothing until it has put the call "
        "through to you; if the agent speaks to you first, answer as yourself.",
        "setting": "You are the automated phone menu that answers this line before a person does.",
    },
    "chat_message": {
        "modalities": {"chat"},
        "about": "someone else in the person's conversation with the agent who writes messages of their own, "
        "labelled with their name, which the agent reads",
        "tells_person": "Also in this conversation, and may write in it: {who}.",
        "setting": "You are in a written conversation between someone and an automated agent, and you write in it "
        "yourself; the agent reads what you write, labelled with your name.",
    },
}

TRIGGER_ABOUT: dict[str, str] = {
    "always": "present from the start: at the first moment its channel allows (after the agent's first turn for "
    "someone who speaks; at once for someone acting through the world; a phone menu answers the call itself)",
    "after_seconds": "`seconds` after the trial starts (someone acting through the world)",
    "contacted": "when the agent reaches one of its identities (someone acting through the world)",
    "conditional": "once a record of `collection` matching every field of `where` exists in the world",
    "mid_journey": "after the agent's turn `turn`",
    "on_tool": "on call `on_call` of the agent's tool `tool`, a tool that runs in the simulated world "
    "(one that runs only inside the agent cannot be seen); never if that call never comes",
    "on_keyword": "when one of `words` is in the agent's latest turn; never if none is said",
    "probability": "with chance `p`, drawn once per call from the run's seed",
}
TRIGGERS = tuple(TRIGGER_ABOUT)


def someone_else(name: str) -> str:
    """How an actor's turn is labelled wherever grading reads a conversation."""
    return f"{name or 'Someone else'} (not the person being served)"


def known_to_others(instruction: str) -> str:
    """The person's situation as someone with them knows it: without the person's own details."""
    return instruction.split(DETAILS_MARK, 1)[0].rstrip()


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
        if not modality or modality in entry["modalities"]
    ]


def described(entries: dict[str, str]) -> str:
    return "; ".join(f"`{name}`: {about}" for name, about in entries.items())


# Call-wide limits, the same for every scenario, so no group of actors can take over a call from the person whose
# task it is: how many take part at once, the agent turns they may take, and their time. No actor has a limit of its own.
HANDOFF_LINE_SECONDS = 15.0
# The speaking actors one call carries, as JSON. Set per call and cleared otherwise.
ACTOR_VOICES_ALIAS = "HARNESS_ACTOR_VOICES"
# Where the engine reads the live tool trace. Set per call, never logged.
ACTOR_WORLD_ALIAS = "HARNESS_ACTOR_WORLD"
TOOLS_READ_SECONDS = 1.0
# How much of the conversation an actor hears before writing a line.
CONVERSATION_LINES = 12
# The defaults for every limit nobody set, per run (K-26): two voices on one phone line, the caller back after four
# agent turns, a minute of actors per call, a long ceiling on a pre-run conversation. Each is a setting on the
# environment's actor list, overridable per scenario; `None` there is no limit.
LIMIT_DEFAULTS: dict[str, Any] = {
    "max_active": 2,
    "caller_takes_back_after_turns": 4,
    "actor_seconds_per_call": 60.0,
    "max_messages": 500,
}
# Someone can only take the phone from a person who already has it: not before the agent's second turn, by
# which point the person has spoken at least once. A trigger that fires earlier waits for that turn.
TAKES_PHONE_FROM_TURN = 2
# Time all actors together have in one call (`actor_seconds_per_call`): wall clock from the first actor's trigger, the
# agent's and the person's turns included. Once it runs out every actor stops and the person has the call.
# Lines asked for and not yet ready; one more is dropped rather than queued behind them.
MAX_WAITING_LINES = 2
TIME_UP = "actor time used up"
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


# The share of a suite's scenarios that include an actor when the person has not asked for one:
# drawn once per environment within this range, so suites differ and none goes without.
SHARE_RANGE = (0.15, 0.25)


def default_share(seed: str) -> float:
    """The share of an environment's scenarios that include an actor, unless the person asked for one."""
    return seeded(seed, "share").uniform(*SHARE_RANGE)


def at_least(share: float, of: int) -> int:
    """How many of `of` scenarios a share asks for, rounded up so a small suite still gets one."""
    return math.ceil(share * of) if share > 0 else 0


def loudness_ratio(seed: str, actor_id: str, fixed: float | None) -> float:
    """How loud a background voice is against the person on this call: the list's own ratio when it
    sets one, else a draw from the run seed."""
    if fixed is not None:
        return fixed
    return seeded(seed, actor_id, "loudness").uniform(*LOUDNESS_RANGE)


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
    """What a run knows now, the same shape for every runtime: enough to decide whether an actor is due.

    `agent_turns` is None where turns are not how the run moves (an actor acting through the world beside the call):
    there "present from the start" means at once. `contacted` is whether the agent has reached one of the actor's
    identities; `records` reads the world's records of one collection, where the runtime can read them.
    """

    agent_turns: int | None
    last_text: str = ""
    tools_called: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    contacted: bool = False
    records: Callable[[str], list[dict[str, Any]]] | None = None


# What each runtime can evaluate. A scenario giving an actor a kind its runtime cannot is refused when written.
EVALUATED_BY: dict[str, frozenset[str]] = {
    "speaking": frozenset({"always", "mid_journey", "on_tool", "on_keyword", "probability", "conditional"}),
    "world": frozenset({"always", "after_seconds", "on_tool", "contacted", "probability", "conditional"}),
}


def _whole(word: str) -> str:
    """A keyword as a pattern that never matches inside a longer word; scripts written without
    spaces between words match anywhere, as they have no word edge to stop at."""
    start = r"\b" if word[0].isascii() and word[0].isalnum() else ""
    end = r"\b" if word[-1].isascii() and word[-1].isalnum() else ""
    return f"{start}{re.escape(word)}{end}"


def named_in(text: str, name: str) -> bool:
    """Whether ``text`` names ``name`` as a whole word, written the way a name is (never in lower case
    unless the name itself is), so a name that is also an everyday word is not found in that word."""
    name = " ".join(name.split())
    if not name:
        return False
    pattern = _whole(name).replace(r"\ ", r"\s+")
    return any(
        " ".join(found.group().split()) == name or not found.group()[0].islower()
        for found in re.finditer(pattern, text, re.IGNORECASE)
    )


def drawn(trigger: dict[str, Any], *, seed: str, actor_id: str) -> bool:
    """Whether this call is one a `probability` actor comes into; every other kind always does."""
    if str(trigger.get("kind")) != "probability":
        return True
    return seeded(seed, actor_id, "enters").random() < float(trigger.get("p") or 0)


def matches(record: dict[str, Any], where: dict[str, Any]) -> bool:
    """Whether a record holds every field of `where` with the same value."""
    return all(record.get(key) == value for key, value in (where or {}).items())


def due(
    trigger: dict[str, Any], moment: Moment, *, seed: str, actor_id: str
) -> bool:
    """Whether an actor with this trigger is due now. One definition for every runtime."""
    kind = str(trigger.get("kind") or "always")
    turns = moment.agent_turns
    if kind == "always":
        return turns is None or turns >= 1
    if kind == "after_seconds":
        return moment.elapsed >= float(trigger.get("seconds") or 0)
    if kind == "contacted":
        return moment.contacted
    if kind == "conditional":
        collection = str(trigger.get("collection") or "")
        if not collection or moment.records is None:
            return False
        try:
            rows = moment.records(collection)
        except Exception:  # noqa: BLE001 - unreadable records are not there yet
            return False
        return any(matches(row, trigger.get("where") or {}) for row in rows or [] if isinstance(row, dict))
    if kind == "mid_journey":
        return turns is not None and turns >= int(trigger.get("turn") or 0) >= 1
    if kind == "on_tool":
        tool = str(trigger.get("tool") or "")
        return bool(tool) and moment.tools_called.count(tool) >= int(
            trigger.get("on_call") or 1
        )
    if kind == "on_keyword":
        words = [
            str(one).strip().casefold()
            for one in trigger.get("words") or []
            if str(one).strip()
        ]
        said = moment.last_text.casefold()
        return any(re.search(_whole(word), said) for word in words)
    if kind == "probability":
        return (turns is None or turns >= 1) and drawn(
            trigger, seed=seed, actor_id=actor_id
        )
    return False


def line_brief(
    actor: dict[str, Any],
    *,
    situation: str,
    said: list[str],
    conversation: list[str],
    language: str,
) -> str:
    """What an actor is given each time it writes a line, on any channel."""
    role = f", {actor['role']}" if actor.get("role") else ""
    setting = (CHANNELS.get(str(actor.get("channel") or "")) or {}).get("setting") or ""
    # The actor's brief, when the run resolved one, is the same view the platform shows as what it sees.
    own = (
        [setting, str(actor["brief"])]
        if actor.get("brief")
        else [
            f"You are {actor.get('name') or 'someone'}{role}. {setting}",
            f"What is going on (this is the other person's situation, not yours):\n{situation}",
            f"What you do:\n{actor.get('instruction') or ''}",
        ]
    )
    parts = [
        *own,
        "You are not the other person. Never give any detail the agent needs from them, never "
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


# What an actor is asked each time something it can see happens, on every runtime: one decision, a line to say, an
# action to take in the world, or both, or to wait.
DECISION_ASK = (
    "Decide what you do now. Reply with JSON only: "
    '{"line": "<what you say now, or null>", "action": "<one of your actions, or null>", "arguments": {...}, '
    '"why": "<one sentence>"}. Use null for both to wait.'
)


@dataclass
class Decision:
    """One actor decision: what it says (delivered by its channel) and what it does (sent through the world's door)."""

    line: str = ""
    action: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    why: str = ""

    @property
    def waits(self) -> bool:
        return not self.line and not self.action


def decision_brief(
    actor: dict[str, Any],
    *,
    situation: str,
    said: list[str],
    conversation: list[str],
    language: str,
    seen: list[str] | None = None,
    actions: dict[str, str] | None = None,
    speaks: bool = True,
) -> str:
    """What an actor is given to decide from, on any runtime: its brief and the conversation (when it speaks), what it
    can see happened in the world, and the actions it may take."""
    parts = [line_brief(actor, situation=situation, said=said, conversation=conversation, language=language)] if speaks else [
        str(actor.get("brief") or f"You are {actor.get('name') or 'someone'}.")
    ]
    allowed = actions or {}
    parts.append(
        "Your actions in the world:\n"
        + ("\n".join(f"- {name}: {about}".rstrip(": ") for name, about in allowed.items()) or "- none")
    )
    parts.append("What you can see happened in the world, oldest first:\n" + ("\n".join(seen or []) or "nothing yet"))
    if not speaks:
        parts.append("You do not speak on this conversation; leave `line` null.")
    parts.append(DECISION_ASK)
    return "\n\n".join(part for part in parts if part)


def read_decision(raw: Any) -> Decision:
    """A model's reply as a decision; anything unreadable is a wait, and plain text is a line."""
    import json

    if isinstance(raw, dict):
        parsed: Any = raw
    else:
        text = str(raw or "").strip()
        start, end = text.find("{"), text.rfind("}")
        parsed = None
        if 0 <= start < end:
            try:
                parsed = json.loads(text[start : end + 1])
            except ValueError:
                parsed = None
        if parsed is None:
            return Decision(line=text)
    if not isinstance(parsed, dict):
        return Decision()
    arguments = parsed.get("arguments") if isinstance(parsed.get("arguments"), dict) else {}
    return Decision(
        line=str(parsed.get("line") or "").strip(),
        action=str(parsed.get("action") or "").strip(),
        arguments=arguments,
        why=str(parsed.get("why") or "").strip(),
    )


class Cast:
    """Every actor in one call: who enters when, the call-wide budget, and what happened to each.

    Each actor is a resolved entry: its list fields plus the scenario's `instruction` and `trigger`.
    """

    def __init__(
        self,
        actors: list[dict[str, Any]],
        seed: str,
        clock: Callable[[], float] = time.time,
        limits: dict[str, Any] | None = None,
    ) -> None:
        self.actors = actors
        self.seed = seed
        self.clock = clock
        # The limits in force for this call; a key set to None is no limit.
        given = dict(LIMIT_DEFAULTS if limits is None else limits)
        self.max_active: int | None = given.get("max_active")
        self.takes_back_after: int | None = given.get("caller_takes_back_after_turns")
        self.seconds: float | None = given.get("actor_seconds_per_call")
        # When the first actor was triggered; the call's actor time runs from then.
        self._started: float | None = None
        self._cut = False
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
        """Seconds left of the call's actor time; all of it until the first trigger."""
        if self._cut:
            return 0.0
        if self.seconds is None:
            return float("inf")
        if self._started is None:
            return self.seconds
        return max(0.0, self.seconds - (self.clock() - self._started))

    def out_of_time(self) -> bool:
        """Whether the call's actor time is used up; the first time it is, every actor stops."""
        if not self._time_up and not self.remaining():
            self._time_up = True
            for actor in self.actors:
                if self._state[str(actor["id"])]["entered"]:
                    self.finish(actor, TIME_UP)
            for key in self._waiting:
                self._record(key, "dropped", reason=TIME_UP)
            self._waiting.clear()
        return self._time_up

    def waits_on_tools(self) -> bool:
        """Whether an actor that enters on a tool call has yet to be triggered."""
        return any(
            str((actor.get("trigger") or {}).get("kind")) == "on_tool"
            and not self._state[str(actor["id"])]["entered"]
            and not self._state[str(actor["id"])]["done"]
            and str(actor["id"]) not in self._waiting
            and str(actor["id"]) not in self._held
            for actor in self.actors
        )

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

    def late(self, actor: dict[str, Any], *, cut_by_call_time: bool) -> None:
        """A line not ready within its limit, or cut off by the call's actor time."""
        if cut_by_call_time:
            # It waited out the rest of the call's actor time.
            self._cut = True
        self.dropped(actor, TIME_UP if self.out_of_time() else "not ready in time")

    def entering(self, moment: Moment) -> list[dict[str, Any]]:
        """The actors that enter now: due ones in the scenario's order, while there is room."""
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
                actor_id=key,
            ):
                if (
                    actor.get("channel") == "audio_handoff"
                    and (moment.agent_turns or 0) < TAKES_PHONE_FROM_TURN
                ):
                    self._held.add(key)
                    continue
                self._held.discard(key)
                if self._started is None:
                    self._started = self.clock()
                self._waiting.append(key)
                self._record(key, "triggered", turn=moment.agent_turns)
        entered = []
        while self._waiting and (self.max_active is None or len(self._active()) < self.max_active):
            key = self._waiting.pop(0)
            self._state[key]["entered"] = True
            entered.append(next(one for one in self.actors if str(one["id"]) == key))
        return entered

    def may_speak(self, actor: dict[str, Any], agent_turns: int) -> bool:
        """Whether this actor may take part on this turn, within the call's limits."""
        key = str(actor["id"])
        state = self._state[key]
        if not state["entered"] or state["done"] or self.out_of_time():
            return False
        if self.takes_back_after is not None and self._activity >= self.takes_back_after:
            self.finish(actor, "limit reached")
            return False
        if actor.get("max_lines") is not None and state["lines"] >= int(actor["max_lines"]):
            self.finish(actor, "its own line limit reached")
            return False
        if actor.get("channel") == "audio_mix" and state["lines"]:
            if state["last_turn"] == agent_turns - 1:
                return False
            return seeded(self.seed, key, "again", agent_turns).random() < SPEAKS_AGAIN
        return True

    def took_turn(self) -> None:
        """One agent turn spent with an actor taking part, counted against the call-wide budget."""
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
        self._record(
            str(actor["id"]),
            "delivered",
            text=text,
            at=at,
            ended_at=ended_at,
            asked_at=asked_at,
        )

    def not_entered(self, actor: dict[str, Any], reason: str) -> None:
        """An actor that cannot enter this call at all, with the reason."""
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
        actor = next(one for one in self.actors if str(one["id"]) == key)
        self.events.append(
            {
                "actor": key,
                "name": actor.get("name"),
                "channel": actor.get("channel"),
                "kind": kind,
                **details,
            }
        )

    def summary(self) -> list[dict[str, Any]]:
        """Per actor: whether it was triggered, how many lines reached the agent, and why any were dropped."""
        return [
            {
                "actor": str(actor["id"]),
                "name": actor.get("name"),
                "triggered": any(
                    e["kind"] == "triggered" and e["actor"] == str(actor["id"])
                    for e in self.events
                ),
                "delivered": self._state[str(actor["id"])]["lines"],
                # False only for a `probability` actor this call's draw left out: absent by design.
                "drawn": drawn(
                    dict(actor.get("trigger") or {}),
                    seed=self.seed,
                    actor_id=str(actor["id"]),
                ),
                "dropped": [
                    e["reason"]
                    for e in self.events
                    if e["kind"] == "dropped" and e["actor"] == str(actor["id"])
                ],
            }
            for actor in self.actors
        ]
