"""Plays a scenario's actors in the world, through the world's door.

A mocked actor takes the actions its scenario schedules, each at its trigger, and decides nothing. A live actor
decides its own next action from its brief each time the agent reaches it (or its activation trigger fires), up to
`LIVE_TURNS` times a trial; `both` does the two. Every action goes to the tool proxy under the actor's id and this
trial's token, so the proxy checks the actor's permissions and logs the attempt with what the world made of it,
whether or not the world was called. A live decision is kept with its reason, never trusted for permission.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Callable
from typing import Any
from urllib import error, request

from fi.simulate.simulation.call_actors import seeded

from .actors import AGENT, Actor, ScenarioActor, ScheduledAction

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.25
# How many actions a live actor may decide in one trial.
LIVE_TURNS = 3
# What a live actor is asked each time it acts: one action of its own, or none.
LIVE_ASK = (
    "Decide what you do in the world now. Reply with JSON only: "
    '{"action": "<one of your actions>", "arguments": {...}, "why": "<one sentence>"}, or '
    '{"action": null, "why": "<one sentence>"} to do nothing now.'
)

Decide = Callable[[str], Any]


def _send(
    url: str, headers: dict[str, str], arguments: dict[str, Any], timeout: float
) -> int:
    outgoing = request.Request(
        url,
        data=json.dumps(arguments).encode(),
        method="POST",
        headers={"content-type": "application/json", **headers},
    )
    try:
        with request.urlopen(outgoing, timeout=timeout) as response:
            return int(response.status)
    except error.HTTPError as exc:
        return exc.code


def contacts(arguments: Any, actor: Actor) -> bool:
    """Whether one of the agent's actions is addressed to one of this actor's identities: an argument value equal
    to one, compared in the same encoding so any character matches."""
    said = json.dumps(arguments or {}, default=str)
    return any(
        identity and json.dumps(identity) in said
        for identity in actor.identities.values()
    )


class ActorDispatcher:
    def __init__(
        self,
        actors: dict[str, Actor],
        cast: list[ScenarioActor],
        *,
        trial_id: str,
        proxy_url: str,
        token: str,
        read_log: Callable[[], list[dict[str, Any]]],
        seed: str,
        timeout: float,
        clock: Callable[[], float] = time.monotonic,
        send: Callable[[str, dict[str, str], dict[str, Any]], int] | None = None,
        decide: Decide | None = None,
        briefs: dict[str, str] | None = None,
        world_actions: dict[str, str] | None = None,
    ) -> None:
        self.proxy_url = proxy_url.rstrip("/")
        self.token = token
        self.trial_id = trial_id
        self.read_log = read_log
        self.clock = clock
        self.send = send or (
            lambda url, headers, arguments: _send(url, headers, arguments, timeout)
        )
        listed = [
            (actors[entry.id], index, action)
            for entry in cast
            if entry.id in actors
            for index, action in enumerate(entry.actions)
        ]
        # A chance trigger is drawn once per trial, the same way on a rerun of it; one drawn out never fires.
        self.pending: list[tuple[Actor, int, ScheduledAction]] = [
            (actor, index, action)
            for actor, index, action in listed
            # A live-only actor takes no scheduled actions.
            if actor.play != "live"
            and (action.trigger.kind != "probability"
            or seeded(seed, actor.id, index).random() < action.trigger.p)
        ]
        self.started: float | None = None
        self.fired: list[dict[str, Any]] = []
        # Live actors: those the scenario includes whose record plays them live, each with its brief.
        self.decide = decide
        self.briefs = briefs or {}
        self.world_actions = world_actions or {}
        self.live = [
            actors[entry.id]
            for entry in cast
            if entry.id in actors and actors[entry.id].play in ("live", "both")
        ]
        self.live_turns: dict[str, int] = {one.id: 0 for one in self.live}
        self.answered: dict[str, set[str]] = {one.id: set() for one in self.live}
        self.woken: set[str] = set()

    def _cause(
        self,
        actor: Actor,
        action: ScheduledAction,
        elapsed: float,
        log: list[dict[str, Any]],
    ) -> str | None:
        """Whether this action is due now: "" when due with no cause in the log, the causing action's id
        when an agent action caused it, None when not due yet."""
        trigger = action.trigger
        agent_rows = [row for row in log if (row.get("actor_id") or AGENT) == AGENT]
        if trigger.kind == "always":
            return ""
        if trigger.kind == "after_seconds":
            return "" if elapsed >= trigger.seconds else None
        if trigger.kind == "probability":
            return ""
        if trigger.kind == "on_tool":
            # `tool` is the name the log records, resolved from the agent's tool when the trial starts.
            calls = [row for row in agent_rows if row.get("name") == trigger.tool]
            return (
                str(calls[trigger.on_call - 1].get("action_id") or "")
                if len(calls) >= trigger.on_call
                else None
            )
        if trigger.kind == "contacted":
            reached = next(
                (row for row in agent_rows if contacts(row.get("arguments"), actor)),
                None,
            )
            return None if reached is None else str(reached.get("action_id") or "")
        return None

    async def _fire(
        self, actor: Actor, index: int, action: ScheduledAction, cause: str
    ) -> None:
        headers = {
            "X-ALK-Actor": actor.id,
            "X-ALK-Actor-Token": self.token,
            "Idempotency-Key": f"{self.trial_id}:{actor.id}:{index}",
        }
        if cause:
            headers["X-ALK-Parent-Event"] = cause
        url = f"{self.proxy_url}/{action.action.replace('.', '/')}"
        status = await asyncio.to_thread(self.send, url, headers, action.arguments)
        self.fired.append(
            {
                "actor": actor.id,
                "play": "mocked",
                "action": action.action,
                "status": status,
                "parent": cause or None,
            }
        )

    def _live_due(self, actor: Actor, elapsed: float, log: list[dict[str, Any]]) -> str | None:
        """The cause of a live actor's next turn: an agent action addressed to it not yet answered, else its
        activation trigger the first time it is met; None when nothing calls for it."""
        if self.live_turns[actor.id] >= LIVE_TURNS:
            return None
        agent_rows = [row for row in log if (row.get("actor_id") or AGENT) == AGENT]
        for row in agent_rows:
            key = str(row.get("action_id") or "")
            if key and key not in self.answered[actor.id] and contacts(row.get("arguments"), actor):
                self.answered[actor.id].add(key)
                return key
        if actor.id in self.woken:
            return None
        trigger = (actor.activation.trigger if actor.activation else None)
        if trigger is None or trigger.kind == "contacted":
            return None
        cause = self._cause(actor, ScheduledAction(action="activation", trigger=trigger), elapsed, log)
        if cause is not None:
            self.woken.add(actor.id)
        return cause

    def _situation(self, actor: Actor, log: list[dict[str, Any]]) -> str:
        """What a live actor is given to decide from: its brief, what it may do, and what has happened in the world
        that concerns it."""
        own = [row for row in log if row.get("actor_id") == actor.id]
        asked = [
            row
            for row in log
            if (row.get("actor_id") or AGENT) == AGENT and contacts(row.get("arguments"), actor)
        ]
        allowed = "\n".join(
            f"- {name}: {self.world_actions.get(name, '')}".rstrip(": ") for name in actor.permitted_actions
        ) or "- none: you may not act in the world"
        return "\n\n".join(
            [
                self.briefs.get(actor.id, f"You are {actor.name}."),
                "Your actions in the world:\n" + allowed,
                "What the agent sent that concerns you, oldest first:\n"
                + ("\n".join(json.dumps({"action": r.get("name"), "arguments": r.get("arguments")}, default=str) for r in asked) or "nothing yet"),
                "What you have already done:\n"
                + ("\n".join(json.dumps({"action": r.get("name"), "outcome": r.get("outcome")}, default=str) for r in own) or "nothing yet"),
                LIVE_ASK,
            ]
        )

    async def _live_turn(self, actor: Actor, cause: str, log: list[dict[str, Any]]) -> None:
        self.live_turns[actor.id] += 1
        turn = self.live_turns[actor.id]
        try:
            raw = self.decide(self._situation(actor, log)) if self.decide else None
            if inspect.isawaitable(raw):
                raw = await raw
            decision = _decision(raw)
        except Exception as exc:  # noqa: BLE001 - a failed decision is recorded, never a failed trial
            self.fired.append({"actor": actor.id, "play": "live", "turn": turn, "decided": None, "why": None,
                               "error": type(exc).__name__, "parent": cause or None})
            return
        if decision is None or not decision.get("action"):
            self.fired.append({"actor": actor.id, "play": "live", "turn": turn, "decided": None,
                               "why": (decision or {}).get("why"), "parent": cause or None})
            return
        action = str(decision["action"])
        headers = {
            "X-ALK-Actor": actor.id,
            "X-ALK-Actor-Token": self.token,
            # Its own key space, so a live action never collides with a scheduled one.
            "Idempotency-Key": f"{self.trial_id}:{actor.id}:l{turn}",
        }
        if cause:
            headers["X-ALK-Parent-Event"] = cause
        url = f"{self.proxy_url}/{action.replace('.', '/')}"
        arguments = decision.get("arguments") if isinstance(decision.get("arguments"), dict) else {}
        # Sent whatever it chose: the world, not the decision, decides whether it may.
        status = await asyncio.to_thread(self.send, url, headers, arguments)
        self.fired.append({"actor": actor.id, "play": "live", "turn": turn, "action": action, "status": status,
                           "why": decision.get("why"), "parent": cause or None})

    async def tick(self) -> None:
        """Fires every pending action whose trigger is met now, and gives each live actor a turn when something calls
        for one; the log is read only when one depends on it."""
        if self.started is None:
            self.started = self.clock()
        log: list[dict[str, Any]] = []
        if self.live or any(
            action.trigger.kind in ("on_tool", "contacted")
            for _, _, action in self.pending
        ):
            try:
                log = await asyncio.to_thread(self.read_log)
            except Exception:  # noqa: BLE001 - an unreadable log this tick is retried next tick
                log = []
        elapsed = self.clock() - self.started
        for actor, index, action in list(self.pending):
            cause = self._cause(actor, action, elapsed, log)
            if cause is None:
                continue
            self.pending.remove((actor, index, action))
            try:
                await self._fire(actor, index, action, cause)
            except Exception as exc:  # noqa: BLE001 - one failed action never stops the rest
                logger.warning(
                    "actor action %s by %s could not be sent: %s",
                    action.action,
                    actor.id,
                    type(exc).__name__,
                )
        for actor in self.live:
            cause = self._live_due(actor, elapsed, log)
            if cause is not None:
                await self._live_turn(actor, cause, log)

    async def run(self) -> None:
        """Fires every scheduled action once, when its trigger is met, and plays live actors; runs until the trial is
        over and it is cancelled."""
        while self.pending or any(self.live_turns[one.id] < LIVE_TURNS for one in self.live):
            await self.tick()
            await asyncio.sleep(POLL_SECONDS)


def _decision(raw: Any) -> dict[str, Any] | None:
    """A live actor's reply as `{action, arguments, why}`; anything unreadable is no action."""
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def live_decision(situation: str) -> str:
    """One live actor turn: the model reads the actor's situation and answers with its action, or none."""
    from .backends import SessionSpec
    from .config import chosen_model
    from .session import Stage

    stage = Stage(
        SessionSpec(system_prompt=situation, max_turns=1, model=chosen_model(None), gated=False),
        name="actor",
    )
    await stage.__aenter__()
    try:
        return (await stage.say("Decide now.")).text
    finally:
        await stage.__aexit__(None, None, None)
