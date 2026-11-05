"""Plays a scenario's actors in the world, through the world's door.

A mocked actor takes the actions its scenario schedules, each when its trigger is met, and decides nothing. A live
actor decides from its brief on every new event it can see (anything addressed to it, and any action taken where it
can act itself, by the agent or another actor) and when its activation is first met, for as long as the trial lasts;
`both` does the two. Whether an actor is due is the one `due()` every runtime uses. Every action is sent by an `Acts`
surface (HTTP to the bundle's tool proxy, or the in-process door) under the actor's id, so the door checks the actor's
permissions and logs the attempt with what the world made of it. A live decision is kept with its reason, never
trusted for permission.

Before the run, the scenario's pre-run sessions are replayed through the same surface, each message under its actor's
id with its own `h<n>` key, so a retry never writes it twice and a pre-run row is told apart from the run's.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol
from urllib import error, request

from fi.simulate.simulation.call_actors import Moment, decision_brief, due, read_decision, seeded

from .actors import AGENT, Actor, PreRunSession, ScenarioActor, ScheduledAction, is_pre_run

logger = logging.getLogger(__name__)

POLL_SECONDS = 0.25

Decide = Callable[[str, Actor], Any]


class Acts(Protocol):
    """Sends one actor action through the world's door and says what came of it: `{status, outcome, result}`."""

    def __call__(
        self, actor: str, action: str, arguments: dict[str, Any], *, key: str, parent: str | None, form: str = "tool"
    ) -> Awaitable[dict[str, Any]]: ...


class HttpActs:
    """The bundle's tool proxy: the actor's id and this trial's token in headers, the action as the route."""

    def __init__(
        self,
        proxy_url: str,
        token: str,
        timeout: float,
        send: Callable[[str, dict[str, str], dict[str, Any]], Any] | None = None,
    ) -> None:
        self.proxy_url = proxy_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.send = send or self._send

    def _send(self, url: str, headers: dict[str, str], arguments: dict[str, Any]) -> dict[str, Any]:
        outgoing = request.Request(
            url,
            data=json.dumps(arguments).encode(),
            method="POST",
            headers={"content-type": "application/json", **headers},
        )
        try:
            with request.urlopen(outgoing, timeout=self.timeout) as response:
                status, content = int(response.status), response.read()
        except error.HTTPError as exc:
            status, content = exc.code, exc.read()
        try:
            result = json.loads(content) if content else None
        except ValueError:
            result = None
        return {"status": status, "result": result}

    async def __call__(
        self, actor: str, action: str, arguments: dict[str, Any], *, key: str, parent: str | None, form: str = "tool"
    ) -> dict[str, Any]:
        headers = {"X-ALK-Actor": actor, "X-ALK-Actor-Token": self.token, "Idempotency-Key": key}
        if parent:
            headers["X-ALK-Parent-Event"] = parent
        if form == "record":
            headers["X-ALK-Record"] = "1"
        sent = await asyncio.to_thread(self.send, f"{self.proxy_url}/{action.replace('.', '/')}", headers, arguments)
        if isinstance(sent, int):
            sent = {"status": sent, "result": None}
        status = int(sent.get("status") or 0)
        return {**sent, "outcome": "committed" if 0 < status < 400 else "refused" if status < 500 else "faulted"}


class DoorActs:
    """The world's door in this process: the harness itself is the caller, so no token is needed."""

    def __init__(self, door: Any) -> None:
        self.door = door

    async def __call__(
        self, actor: str, action: str, arguments: dict[str, Any], *, key: str, parent: str | None, form: str = "tool"
    ) -> dict[str, Any]:
        from .world_door import ToolCall

        call = ToolCall(
            action=action,
            name=action.rsplit(".", 1)[-1],
            arguments=arguments,
            actor=actor,
            key=key,
            parent=parent,
            form=form,
        )
        outcome = await asyncio.to_thread(self.door.enter, call)
        return {"status": outcome.status, "outcome": outcome.outcome, "result": outcome.result}


def contacts(arguments: Any, actor: Actor) -> bool:
    """Whether an action is addressed to one of this actor's identities: an argument value equal to one, compared
    in the same encoding so any character matches."""
    said = json.dumps(arguments or {}, default=str)
    return any(identity and json.dumps(identity) in said for identity in actor.identities.values())


def form_of(action: str, actor: Actor) -> str:
    """`record` for a record change the actor is permitted (`approvals.change`), else `tool`."""
    from .world_door import record_action

    return "record" if record_action(action) is not None and action in actor.permitted_actions else "tool"


class ActorDispatcher:
    def __init__(
        self,
        actors: dict[str, Actor],
        cast: list[ScenarioActor],
        *,
        act: Acts,
        trial_id: str,
        read_log: Callable[[], list[dict[str, Any]]],
        seed: str,
        clock: Callable[[], float] = time.monotonic,
        decide: Decide | None = None,
        briefs: dict[str, str] | None = None,
        world_actions: dict[str, str] | None = None,
        pre_run: list[PreRunSession] | None = None,
        records: Callable[[str], list[dict[str, Any]]] | None = None,
        names_actor: set[str] | None = None,
        reported: Callable[[], list[dict[str, Any]]] | None = None,
    ) -> None:
        self.act = act
        # Actions speaking actors decided on the call, handed over by the voice engine, sent from here.
        self.reported = reported
        self.reported_sent = 0
        # World actions whose tool takes an `actor_id` argument.
        self.names_actor = set(names_actor or ())
        self.trial_id = trial_id
        self.read_log = read_log
        self.clock = clock
        self.records = records
        self.actors = actors
        self.seed = seed
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
            and (action.trigger.kind != "probability" or seeded(seed, actor.id, index).random() < action.trigger.p)
        ]
        self.started: float | None = None
        self.fired: list[dict[str, Any]] = []
        self.pre_run = list(pre_run or [])
        self.decide = decide
        self.briefs = briefs or {}
        self.world_actions = world_actions or {}
        # Only an actor with no channel is decided here; one that speaks is decided by its channel, once per turn,
        # its line and its action together, so no actor is ever asked twice about the same moment.
        self.live = [
            actors[entry.id]
            for entry in cast
            if entry.id in actors and actors[entry.id].play in ("live", "both") and not actors[entry.id].channel
        ]
        self.live_turns: dict[str, int] = {one.id: 0 for one in self.live}
        self.answered: dict[str, set[str]] = {one.id: set() for one in self.live}
        self.woken: set[str] = set()
        self.problems: list[dict[str, Any]] = []
        # When each actor comes in here: the scenario's own trigger, else the actor's own activation.
        self.triggers = {
            entry.id: entry.trigger or actors[entry.id].activation.trigger
            for entry in cast
            if entry.id in actors and (entry.trigger is not None or actors[entry.id].activation is not None)
        }
        # Actors the world waits on for at most `timeout_seconds` once due, then applies their fallback.
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
        self.sent = 0

    # -- when --------------------------------------------------------------------------------------------------

    def _moment(self, actor: Actor, elapsed: float, log: list[dict[str, Any]]) -> Moment:
        agent_rows = [row for row in log if (row.get("actor_id") or AGENT) == AGENT and not is_pre_run(row.get("idempotency_key"))]
        return Moment(
            agent_turns=None,
            tools_called=[str(row.get("name") or "") for row in agent_rows],
            elapsed=elapsed,
            contacted=any(contacts(row.get("arguments"), actor) for row in agent_rows),
            records=self.records,
        )

    def _cause(self, actor: Actor, trigger: Any, elapsed: float, log: list[dict[str, Any]], index: int = -1) -> str | None:
        """Whether this trigger is met now (the one `due()`): None when not, else the id of the agent action that met
        it ("" when no single action did)."""
        if trigger.kind == "probability" and index >= 0:
            # A scheduled action's chance was drawn once, when the trial began; one still pending was drawn in.
            return ""
        moment = self._moment(actor, elapsed, log)
        if not due(trigger.model_dump(), moment, seed=self.seed, actor_id=actor.id):
            return None
        agent_rows = [row for row in log if (row.get("actor_id") or AGENT) == AGENT and not is_pre_run(row.get("idempotency_key"))]
        if trigger.kind == "on_tool":
            calls = [row for row in agent_rows if row.get("name") == trigger.tool]
            return str(calls[trigger.on_call - 1].get("action_id") or "") if len(calls) >= trigger.on_call else ""
        if trigger.kind == "contacted":
            reached = next((row for row in agent_rows if contacts(row.get("arguments"), actor)), None)
            return str((reached or {}).get("action_id") or "")
        return ""

    def _sees(self, actor: Actor, row: dict[str, Any]) -> bool:
        """Whether an action in the log is one this actor can see: never its own or a pre-run row; one addressed to it;
        or one taken where it can act itself (the same action, by the agent or another actor)."""
        if (row.get("actor_id") or AGENT) == actor.id or is_pre_run(row.get("idempotency_key")):
            return False
        name = str(row.get("name") or "")
        return contacts(row.get("arguments"), actor) or any(
            name in (one, one.rsplit(".", 1)[-1], one.replace(".", "/")) for one in actor.permitted_actions
        )

    # -- acting ------------------------------------------------------------------------------------------------

    async def _send(self, actor: Actor, action: str, arguments: dict[str, Any], key: str, cause: str) -> dict[str, Any]:
        service = action.split(".", 1)[0]
        if action in self.names_actor and "actor_id" not in arguments and actor.identities.get(service):
            # A world that checks who acts takes the actor's id in the call: it is the actor's identity there.
            arguments = {**arguments, "actor_id": actor.identities[service]}
        try:
            return await self.act(actor.id, action, arguments, key=key, parent=cause or None, form=form_of(action, actor))
        except Exception as exc:  # noqa: BLE001 - one action that cannot be sent never stops the rest
            logger.warning("actor action %s by %s could not be sent: %s", action, actor.id, type(exc).__name__)
            return {"status": None, "outcome": "faulted", "error": type(exc).__name__}

    async def _fire(self, actor: Actor, index: int, action: ScheduledAction, cause: str) -> None:
        sent = await self._send(actor, action.action, action.arguments, f"{self.trial_id}:{actor.id}:{index}", cause)
        self._answered(actor)
        self.fired.append(
            {"actor": actor.id, "play": "mocked", "action": action.action, "arguments": action.arguments,
             "status": sent.get("status"), "outcome": sent.get("outcome"), "parent": cause or None}
        )

    def _live_due(self, actor: Actor, elapsed: float, log: list[dict[str, Any]]) -> str | None:
        """The cause of a live actor's next turn: a new event it can see, else its activation the first time it is
        met; None when nothing calls for it."""
        if actor.id in self.fell_back:
            return None
        if actor.max_turns is not None and self.live_turns[actor.id] >= actor.max_turns:
            return None
        for row in log:
            key = str(row.get("action_id") or "")
            if key and key not in self.answered[actor.id] and self._sees(actor, row):
                self.answered[actor.id].add(key)
                return key
        if actor.id in self.woken:
            return None
        trigger = self.triggers.get(actor.id)
        if trigger is None or trigger.kind == "contacted":
            return None
        cause = self._cause(actor, trigger, elapsed, log)
        if cause is not None:
            self.woken.add(actor.id)
        return cause

    def _situation(self, actor: Actor, log: list[dict[str, Any]]) -> str:
        """What a live actor decides from: its brief, what it may do, and what it can see happened."""
        seen = [
            json.dumps({"by": r.get("actor_id") or AGENT, "action": r.get("name"), "arguments": r.get("arguments"),
                        "outcome": r.get("outcome")}, default=str)
            for r in log
            if self._sees(actor, r) or r.get("actor_id") == actor.id
        ]
        return decision_brief(
            {"name": actor.name, "brief": self.briefs.get(actor.id, f"You are {actor.name}.")},
            situation="",
            said=[],
            conversation=[],
            language="",
            seen=seen,
            actions={name: self.world_actions.get(name, "") for name in actor.permitted_actions},
            speaks=False,
        )

    async def _live_turn(self, actor: Actor, cause: str, log: list[dict[str, Any]]) -> None:
        self.live_turns[actor.id] += 1
        turn = self.live_turns[actor.id]
        try:
            raw = self.decide(self._situation(actor, log), actor) if self.decide else None
            if inspect.isawaitable(raw):
                raw = await raw
            decision = read_decision(raw)
        except Exception as exc:  # noqa: BLE001 - a failed decision is recorded, never a failed trial
            self.fired.append({"actor": actor.id, "play": "live", "turn": turn, "decided": None, "why": None,
                               "error": type(exc).__name__, "parent": cause or None})
            return
        if not decision.action:
            self.fired.append({"actor": actor.id, "play": "live", "turn": turn, "decided": None,
                               "why": decision.why or None, "parent": cause or None})
            return
        # Sent whatever it chose: the world, not the decision, decides whether it may.
        sent = await self._send(actor, decision.action, decision.arguments, f"{self.trial_id}:{actor.id}:l{turn}", cause)
        self._answered(actor)
        self.fired.append({"actor": actor.id, "play": "live", "turn": turn, "action": decision.action,
                           "arguments": decision.arguments, "status": sent.get("status"),
                           "outcome": sent.get("outcome"), "why": decision.why or None, "parent": cause or None})

    # -- waits and fallbacks -------------------------------------------------------------------------------------

    def _answered(self, actor: Actor) -> None:
        """An action sent by an actor the world is waiting on is its answer: no fallback for it."""
        if actor.id in self.waiting:
            self.settled.add(actor.id)

    def _wait_on(self, elapsed: float, log: list[dict[str, Any]]) -> list[Actor]:
        """Starts the wait for each actor whose activation is met now, recorded with its timeout and fallback; returns
        those whose wait has run out without an answer."""
        due_now = []
        for actor in self.timed:
            if actor.id in self.settled:
                continue
            activation = actor.activation
            if actor.id not in self.waiting:
                cause = self._cause(actor, self.triggers[actor.id], elapsed, log)
                if cause is not None:
                    self.waiting[actor.id] = (self.clock(), cause)
                    self.fired.append({"actor": actor.id, "play": "waiting", "timeout_seconds": activation.timeout_seconds,
                                       "fallback": activation.fallback, "parent": cause or None})
            elif self.clock() - self.waiting[actor.id][0] >= activation.timeout_seconds:
                due_now.append(actor)
        return due_now

    async def _fall_back(self, actor: Actor, log: list[dict[str, Any]]) -> None:
        """No answer within its timeout: its fallback is the outcome, recorded as the timeout's, and it takes no more
        part. `silent` is a time-out, `decline` the request declined, one of its own permitted actions taken for it and
        labelled as the fallback, another actor's id that actor answering instead."""
        self.settled.add(actor.id)
        self.fell_back.add(actor.id)
        self.pending = [one for one in self.pending if one[0].id != actor.id]
        fallback = actor.activation.fallback
        cause = self.waiting[actor.id][1]
        if fallback in ("silent", "decline"):
            outcome = {"silent": "timed_out", "decline": "declined"}[fallback]
        elif fallback in actor.permitted_actions:
            outcome = "fallback_action"
        else:
            outcome = "answered_by"
        record = {"actor": actor.id, "play": "timeout", "fallback": fallback, "outcome": outcome,
                  "timeout_seconds": actor.activation.timeout_seconds, "parent": cause or None}
        if outcome == "fallback_action":
            sent = await self._send(actor, fallback, {}, f"{self.trial_id}:{actor.id}:fallback", cause)
            record["status"] = sent.get("status")
            record["action_outcome"] = sent.get("outcome")
        self.fired.append(record)
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

    def unresolved(self) -> list[dict[str, Any]]:
        """Waits still open when the trial ended, recorded as such."""
        return [
            {"actor": actor_id, "play": "timeout", "outcome": "unresolved_at_end", "parent": cause or None}
            for actor_id, (_, cause) in self.waiting.items()
            if actor_id not in self.settled
        ]

    def not_activated(self) -> list[dict[str, Any]]:
        """Each actor whose activation never came this trial, as a problem of the scenario, never the agent's."""
        acted = {one["actor"] for one in self.fired if one.get("play") in ("mocked", "live")}
        return [
            {"actor": actor_id, "play": "problem", "problem": "not_activated",
             "why": f"its trigger ({trigger.kind}) never came in this trial"}
            for actor_id, trigger in self.triggers.items()
            if actor_id not in self.waiting and actor_id not in self.woken and actor_id not in acted
            and trigger.kind not in ("always", "probability")
        ]

    # -- before the run ------------------------------------------------------------------------------------------

    async def replay(self) -> None:
        """Writes every pre-run session's thread through the world's door, in order, before the run starts. Each
        message has its own key (`h<n>`), and an argument that refers to an earlier message of its session is filled
        from what the world answered for it (the id a reply needs only exists once that message is in)."""
        number = 0
        for session in self.pre_run:
            answered: dict[int, Any] = {}
            for position, message in enumerate(session.thread, start=1):
                number += 1
                actor = self.actors.get(message.actor)
                if actor is None:
                    self.fired.append({"actor": message.actor, "play": "history", "action": message.action,
                                       "status": None, "error": "not_listed"})
                    continue
                arguments = dict(message.arguments)
                missing = []
                for name, ref in message.refers.items():
                    value = _field(answered.get(ref.message), ref.field)
                    if value is None:
                        missing.append(name)
                    else:
                        arguments[name] = value
                sent = await self._send(actor, message.action, arguments, f"{self.trial_id}:{actor.id}:h{number}", "")
                answered[position] = sent.get("result")
                self.fired.append({"actor": actor.id, "play": "history", "action": message.action,
                                   "arguments": arguments, "status": sent.get("status"), "outcome": sent.get("outcome"),
                                   **({"unresolved_refers": missing} if missing else {})})

    # -- the loop ------------------------------------------------------------------------------------------------

    async def tick(self) -> None:
        """Fires every pending action whose trigger is met now, and gives each live actor a turn when something calls
        for one; the log is read only when one depends on it."""
        if self.started is None:
            self.started = self.clock()
        log: list[dict[str, Any]] = []
        if self.live or self.timed or any(
            action.trigger.kind in ("on_tool", "contacted") for _, _, action in self.pending
        ):
            try:
                log = await asyncio.to_thread(self.read_log)
            except Exception:  # noqa: BLE001 - an unreadable log this tick is retried next tick
                log = []
        elapsed = self.clock() - self.started
        # Waits start before this tick's actions are sent, so an answer sent in the same tick counts.
        timed_out = self._wait_on(elapsed, log)
        for actor, index, action in list(self.pending):
            cause = self._cause(actor, action.trigger, elapsed, log, index)
            if cause is None:
                continue
            self.pending.remove((actor, index, action))
            await self._fire(actor, index, action, cause)
        for actor in list(self.live):
            cause = self._live_due(actor, elapsed, log)
            if cause is not None:
                await self._live_turn(actor, cause, log)
        for actor in timed_out:
            if actor.id not in self.settled:
                await self._fall_back(actor, log)
        await self.send_reported()

    async def send_reported(self) -> None:
        """Sends each action a speaking actor decided on the call, under its id, through the world's door: the same
        door every runtime uses; the voice engine never holds the trial's token."""
        if self.reported is None:
            return
        try:
            reported = await asyncio.to_thread(self.reported)
        except Exception:  # noqa: BLE001 - read again next tick
            return
        for one in reported[self.reported_sent :]:
            self.reported_sent += 1
            actor = self.actors.get(str(one.get("actor") or ""))
            if actor is None or not one.get("action"):
                continue
            arguments = one.get("arguments") if isinstance(one.get("arguments"), dict) else {}
            sent = await self._send(
                actor, str(one["action"]), arguments, f"{self.trial_id}:{actor.id}:v{self.reported_sent}", ""
            )
            self._answered(actor)
            self.fired.append({"actor": actor.id, "play": "live", "channel": "voice", "action": one["action"],
                               "arguments": arguments, "status": sent.get("status"), "outcome": sent.get("outcome"),
                               "why": one.get("why"), "parent": None})

    async def run(self) -> None:
        """Plays every actor until the trial is over and it is cancelled."""
        while (
            self.pending
            or self.reported is not None
            or any(one.id not in self.fell_back for one in self.live)
            or any(one.id not in self.settled for one in self.timed)
        ):
            await self.tick()
            await asyncio.sleep(POLL_SECONDS)

    def record(self) -> list[dict[str, Any]]:
        """Everything this dispatcher did and found, in order, with the waits still open and the actors that never
        came in: the decisions half of the trial's actor record."""
        return [*self.fired, *self.unresolved(), *self.not_activated()]


def build_dispatcher(
    doc: dict[str, Any],
    bundle_dir: Any,
    *,
    act: Acts,
    read_log: Callable[[], list[dict[str, Any]]],
    records: Callable[[str], list[dict[str, Any]]] | None,
    trial_id: str,
    meter: Any = None,
    reported: Callable[[], list[dict[str, Any]]] | None = None,
) -> ActorDispatcher | None:
    """The scenario's actors for this trial, the same way on every runtime; None when it has no actor or pre-run
    session. The surface (`act`) is the only thing a runtime chooses."""
    from pathlib import Path

    from .actors import actor_brief, brief_text, load_actors, with_knowledge_read
    from .call_actor import logged_tool_name
    from .scenario import pre_run_of

    doc = pre_run_of(dict(doc))
    if not (doc.get("actors") or doc.get("pre_run")):
        return None
    bundle_dir = Path(bundle_dir)
    cast = [ScenarioActor.model_validate(entry) for entry in doc.get("actors") or []]
    contract_path = bundle_dir / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8")) if contract_path.is_file() else {}
    for action in (one for entry in cast for one in entry.actions):
        if action.trigger.kind == "on_tool":
            # The log records the route the agent's tool calls, which can differ from the tool's name.
            action.trigger.tool = logged_tool_name(contract, action.trigger.tool)
    for entry in cast:
        if entry.trigger is not None and entry.trigger.kind == "on_tool":
            entry.trigger.tool = logged_tool_name(contract, entry.trigger.tool)
    listing = load_actors(bundle_dir)
    listed = listing.by_id()
    briefs = {
        entry.id: brief_text(
            actor_brief(
                with_knowledge_read(listed[entry.id].model_dump(exclude_none=True), bundle_dir, records) or {},
                entry=with_knowledge_read(entry.model_dump(), bundle_dir, records),
                scenario=dict(doc),
            )
        )
        for entry in cast
        if entry.id in listed and listed[entry.id].play != "mocked" and not listed[entry.id].channel
    }
    about = {
        str(one.get("name")): str(one.get("description") or "")
        for one in contract.get("tools") or []
        if isinstance(one, dict)
    }
    world_actions = {
        route.replace("/", "."): about.get(str(entry.get("tool") or ""), "")
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, dict) and (route := str(entry.get("endpoint") or "").strip("/"))
    }
    takes_actor = {
        str(one.get("name")) for one in contract.get("tools") or [] if isinstance(one, dict) and "actor_id" in (one.get("args") or [])
    }
    names_actor = {
        route.replace("/", ".")
        for entry in contract.get("tool_entrypoints") or []
        if isinstance(entry, dict)
        and str(entry.get("tool") or "") in takes_actor
        and (route := str(entry.get("endpoint") or "").strip("/"))
    }
    return ActorDispatcher(
        listed,
        cast,
        act=act,
        decide=live_decider(meter, listing.limits.model) if briefs else None,
        briefs=briefs,
        pre_run=[PreRunSession.model_validate(one) for one in doc.get("pre_run") or []],
        world_actions=world_actions,
        trial_id=trial_id,
        read_log=read_log,
        seed=trial_id,
        records=records,
        names_actor=names_actor,
        reported=reported,
    )


def world_summary(decisions: list[dict[str, Any]], listed: dict[str, Actor], speaking: set[str]) -> list[dict[str, Any]]:
    """The call's actor summary rows for actors that act through the world (one per actor, in the words the
    speaking actors' rows use): triggered when it took any part or a wait began, delivered when it acted, and the
    problem when it could not. Actors already in the speaking summary are left to it."""
    rows: dict[str, dict[str, Any]] = {}
    for one in decisions:
        actor_id = str(one.get("actor") or "")
        if not actor_id or actor_id in speaking:
            continue
        actor = listed.get(actor_id)
        row = rows.setdefault(
            actor_id,
            {"actor": actor_id, "name": actor.name if actor else actor_id, "triggered": False, "delivered": 0,
             "dropped": [], "acts_in_world": True},
        )
        play = one.get("play")
        if play in ("mocked", "live", "history", "waiting", "timeout"):
            row["triggered"] = True
        if play in ("mocked", "live") and one.get("action") and one.get("outcome") in ("committed", "duplicate"):
            row["delivered"] += 1
        if play == "problem":
            row["dropped"].append(one.get("why") or one.get("problem"))
            row["problem"] = one.get("problem")
    return list(rows.values())


def _field(result: Any, path: str) -> Any:
    """A value in a result by a dotted path (`message.ts`), or None."""
    at = result
    for part in path.split("."):
        if isinstance(at, dict) and part in at:
            at = at[part]
        else:
            return None
    return at


def live_decider(meter: Any = None, default_model: str | None = None) -> Decide:
    """One live actor turn on the model the actor's record or the environment names: the model reads the actor's
    situation and answers with its decision; the call is metered when a meter is given."""

    async def decide(situation: str, actor: Actor) -> str:
        from .backends import SessionSpec
        from .config import chosen_model
        from .session import Stage

        model = chosen_model(actor.model or default_model)
        stage = Stage(SessionSpec(system_prompt=situation, max_turns=1, model=model, gated=False), name="actor")
        await stage.__aenter__()
        try:
            turn = await stage.say("Decide now.")
        finally:
            await stage.__aexit__(None, None, None)
        if meter is not None:
            meter.add(actor.id, model, turn)
        return turn.text

    return decide


async def live_decision(situation: str) -> str:
    """Kept for callers that decide without an actor record: the harness's own model."""
    return await live_decider()(situation, Actor(id="someone", name="someone"))
