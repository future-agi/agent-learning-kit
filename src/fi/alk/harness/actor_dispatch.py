"""Plays a scenario's actors in the world, through the world's door.

A mocked actor takes the actions its scenario schedules, each at its trigger, and decides nothing. A live actor
decides its own next action from its brief on every new event it can see (anything addressed to it, and any action
taken where it can act itself, by the agent or another actor), and when its activation trigger fires, up to
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

from .actors import AGENT, Actor, HistoryEntry, ScenarioActor, ScheduledAction

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.25
# How many actions a live actor may decide in one trial.
LIVE_TURNS = 3
# Actions by actors in a row, with none by the agent between them, after which the actors in that run are stopped and
# the run is recorded as the scenario's problem: actors answering each other this often with the agent idle are in a
# loop, not taking part around the agent.
ACTOR_ONLY_RUN = 6
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
        history: list[HistoryEntry] | None = None,
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
        self.actors = actors
        self.history = list(history or [])
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
        # Live actors stopped by the loop guard, and the problems the guard recorded.
        self.stopped: set[str] = set()
        self.problems: list[dict[str, Any]] = []
        # Actors the world waits on for at most `timeout_seconds` once their activation trigger is met, then applies
        # their fallback. `waiting` holds when each began and what began it; `settled` those that answered or fell back.
        self.timed = [
            actors[entry.id]
            for entry in cast
            if entry.id in actors
            and actors[entry.id].activation is not None
            and actors[entry.id].activation.timeout_seconds > 0
        ]
        self.waiting: dict[str, tuple[float, str]] = {}
        self.settled: set[str] = set()
        self.fell_back: set[str] = set()

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
        self._answered(actor)
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
        if self.live_turns[actor.id] >= LIVE_TURNS or actor.id in self.fell_back or actor.id in self.stopped:
            return None
        for row in log:
            key = str(row.get("action_id") or "")
            if key and key not in self.answered[actor.id] and self._sees(actor, row):
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

    def _sees(self, actor: Actor, row: dict[str, Any]) -> bool:
        """Whether an action in the log is one this actor can see: never its own or the history's; one addressed to
        it; or one taken where it can act itself (the same action, by the agent or another actor)."""
        if (row.get("actor_id") or AGENT) == actor.id or ":h" in str(row.get("idempotency_key") or ""):
            return False
        name = str(row.get("name") or "")
        return contacts(row.get("arguments"), actor) or any(
            name in (one, one.rsplit(".", 1)[-1], one.replace(".", "/")) for one in actor.permitted_actions
        )

    def _loop_guard(self, log: list[dict[str, Any]]) -> None:
        """Stops the live actors in a run of `ACTOR_ONLY_RUN` actor actions with none by the agent, once per run, and
        records it as a problem of the scenario, never of the agent."""
        run: list[str] = []
        for row in log:
            who = str(row.get("actor_id") or AGENT)
            if ":h" in str(row.get("idempotency_key") or ""):
                continue
            run = [] if who == AGENT else [*run, who]
        involved = sorted({who for who in run} & {one.id for one in self.live} - self.stopped)
        if len(run) < ACTOR_ONLY_RUN or not involved:
            return
        self.stopped.update(involved)
        problem = {"actor": involved[0], "play": "problem", "problem": "actor_loop", "actors": involved,
                   "why": f"{len(run)} actions by actors in a row with none by the agent"}
        self.problems.append(problem)
        self.fired.append(problem)

    def _situation(self, actor: Actor, log: list[dict[str, Any]]) -> str:
        """What a live actor is given to decide from: its brief, what it may do, and what has happened in the world
        that concerns it."""
        own = [row for row in log if row.get("actor_id") == actor.id]
        asked = [row for row in log if self._sees(actor, row)]
        allowed = "\n".join(
            f"- {name}: {self.world_actions.get(name, '')}".rstrip(": ") for name in actor.permitted_actions
        ) or "- none: you may not act in the world"
        return "\n\n".join(
            [
                self.briefs.get(actor.id, f"You are {actor.name}."),
                "Your actions in the world:\n" + allowed,
                "What you can see happened, oldest first:\n"
                + ("\n".join(json.dumps({"by": r.get("actor_id") or AGENT, "action": r.get("name"),
                                          "arguments": r.get("arguments")}, default=str) for r in asked)
                   or "nothing yet"),
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
        self._answered(actor)
        self.fired.append({"actor": actor.id, "play": "live", "turn": turn, "action": action, "status": status,
                           "why": decision.get("why"), "parent": cause or None})

    def _answered(self, actor: Actor) -> None:
        """An action sent by an actor the world is waiting on is its answer: no fallback for it."""
        if actor.id in self.waiting:
            self.settled.add(actor.id)

    def _wait_on(self, elapsed: float, log: list[dict[str, Any]]) -> list[Actor]:
        """Starts the wait for each actor whose activation trigger is met now, recorded with its timeout and
        fallback; returns those whose wait has run out without an answer."""
        due = []
        for actor in self.timed:
            if actor.id in self.settled:
                continue
            activation = actor.activation
            if actor.id not in self.waiting:
                cause = self._cause(actor, ScheduledAction(action="activation", trigger=activation.trigger), elapsed, log)
                if cause is not None:
                    self.waiting[actor.id] = (self.clock(), cause)
                    self.fired.append({"actor": actor.id, "play": "waiting", "timeout_seconds": activation.timeout_seconds,
                                       "fallback": activation.fallback, "parent": cause or None})
            elif self.clock() - self.waiting[actor.id][0] >= activation.timeout_seconds:
                due.append(actor)
        return due

    async def _fall_back(self, actor: Actor, log: list[dict[str, Any]]) -> None:
        """No answer within its timeout: its fallback is the outcome, recorded as the timeout's, and it takes no more
        part. `silent` is a time-out, `decline` the request declined, another actor's id that actor answering
        instead. Nothing is said or done in the actor's own name."""
        self.settled.add(actor.id)
        self.fell_back.add(actor.id)
        self.pending = [one for one in self.pending if one[0].id != actor.id]
        fallback = actor.activation.fallback
        cause = self.waiting[actor.id][1]
        outcome = {"silent": "timed_out", "decline": "declined"}.get(fallback, "answered_by")
        self.fired.append({"actor": actor.id, "play": "timeout", "fallback": fallback, "outcome": outcome,
                           "timeout_seconds": actor.activation.timeout_seconds, "parent": cause or None})
        instead = self.actors.get(fallback) if outcome == "answered_by" else None
        if instead is None or instead.id in self.fell_back:
            return
        if instead.play in ("live", "both"):
            if instead not in self.live:
                self.live.append(instead)
                self.live_turns[instead.id] = 0
                self.answered[instead.id] = set()
            await self._live_turn(instead, cause, log)
            return
        for one, index, action in [one for one in self.pending if one[0].id == instead.id]:
            self.pending.remove((one, index, action))
            await self._fire(one, index, action, cause)

    async def write_history(self) -> None:
        """Writes the scenario's history through the world's door, in order, before the run starts. Each entry has
        its own key (`h<n>`), so a retry never writes it twice and a history row is told apart from the run's."""
        for number, entry in enumerate(self.history, start=1):
            actor = self.actors.get(entry.actor)
            if actor is None:
                self.fired.append({"actor": entry.actor, "play": "history", "action": entry.action, "status": None,
                                   "error": "not_listed"})
                continue
            headers = {
                "X-ALK-Actor": actor.id,
                "X-ALK-Actor-Token": self.token,
                "Idempotency-Key": f"{self.trial_id}:{actor.id}:h{number}",
            }
            url = f"{self.proxy_url}/{entry.action.replace('.', '/')}"
            try:
                status = await asyncio.to_thread(self.send, url, headers, entry.arguments)
            except Exception as exc:  # noqa: BLE001 - one entry that cannot be sent never stops the rest
                self.fired.append({"actor": actor.id, "play": "history", "action": entry.action, "status": None,
                                   "error": type(exc).__name__})
                continue
            self.fired.append({"actor": actor.id, "play": "history", "action": entry.action, "status": status})

    async def tick(self) -> None:
        """Fires every pending action whose trigger is met now, and gives each live actor a turn when something calls
        for one; the log is read only when one depends on it."""
        if self.started is None:
            self.started = self.clock()
        log: list[dict[str, Any]] = []
        if self.live or self.timed or any(
            action.trigger.kind in ("on_tool", "contacted")
            for _, _, action in self.pending
        ):
            try:
                log = await asyncio.to_thread(self.read_log)
            except Exception:  # noqa: BLE001 - an unreadable log this tick is retried next tick
                log = []
        elapsed = self.clock() - self.started
        # Waits start before this tick's actions are sent, so an answer sent in the same tick counts.
        timed_out = self._wait_on(elapsed, log)
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
        self._loop_guard(log)
        for actor in list(self.live):
            cause = self._live_due(actor, elapsed, log)
            if cause is not None:
                await self._live_turn(actor, cause, log)
        for actor in timed_out:
            if actor.id not in self.settled:
                await self._fall_back(actor, log)

    async def run(self) -> None:
        """Fires every scheduled action once, when its trigger is met, and plays live actors; runs until the trial is
        over and it is cancelled."""
        while (
            self.pending
            or any(self.live_turns[one.id] < LIVE_TURNS for one in self.live)
            or any(one.id not in self.settled for one in self.timed)
        ):
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
