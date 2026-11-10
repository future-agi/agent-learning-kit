"""Two parties talking: a simulated customer with a goal, and the agent under test.

The customer is a separate session that can only talk. It has no tools and no view of the world,
which is the point: it knows what it wants and how it behaves, and everything it learns about
what is possible it learns from what the agent tells it. An agent that lies to it gets away with
it here exactly as it would with a person, and that is what makes the transcript worth grading.

The conversation ends when the customer is done, when it gives up, or when it runs out of turns.
All three are recorded, because how a conversation ended is often the finding.
"""

from __future__ import annotations

import asyncio
import json
import time
import unicodedata
from dataclasses import dataclass, field
from itertools import accumulate
from typing import Any, Callable

from fi.simulate.simulation.call_actors import declined_line

from ..actor_model import ActorModelFailed
from ..backends import SessionSpec

from ..config import chosen_model
from ..contract import AgentContract
from ..scenario import Scenario
from ..session import Stage
from ..world.runtime import Call
from .targets import Target

DONE = "[DONE]"
STUCK = "[STUCK]"

FINISHED = "finished"
GAVE_UP = "gave-up"
RAN_OUT = "ran-out-of-turns"


class TargetConversationEnded(Exception):
    """The target ended normally before producing another conversational turn."""

    def __init__(self, final_message: str = "") -> None:
        super().__init__(final_message)
        self.final_message = final_message.strip()


@dataclass
class Exchange:
    speaker: str
    text: str
    # Epoch seconds when the turn was delivered, on the same clock as `Call.at`, so a tool call
    # the agent made before replying sorts between the customer's turn and the reply.
    at: float = 0.0
    # An actor's turn: who wrote it (`id`, `name`). Empty for the person and the agent.
    actor: dict[str, Any] | None = None


@dataclass
class Transcript:
    """What happened, in the two forms grading needs: what was said and what was done."""

    exchanges: list[Exchange] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    ended: str = ""
    spent_usd: float = 0.0
    simulator_input_tokens: int = 0
    simulator_output_tokens: int = 0
    # Who else was in the conversation and what happened to each, as a call's record keeps it.
    call_actors: dict[str, Any] | None = None
    # What actors did in the world and what it changed, as a call's record keeps it.
    world_actors: dict[str, Any] | None = None
    # Things the person should know about how the trial ran, e.g. a fault the scenario set that did not take effect.
    notices: list[dict[str, Any]] = field(default_factory=list)

    def spoken(self) -> str:
        return "\n".join(f"{turn.speaker}: {turn.text}" for turn in self.exchanges)

    def canonical_messages(self) -> list[dict[str, str]]:
        """The turns in the role convention the judge reads: `user` said it, `assistant` answered, `actor` is someone
        else in the conversation."""
        return [_message(turn) for turn in self.exchanges]

    def artifact(self) -> bytes:
        """Return the lossless transcript wire format used by hosted ingestion."""
        return (
            json.dumps(
                {
                    "transcript": self.spoken(),
                    "messages": [
                        {**_message(turn), **({"at": turn.at} if turn.at else {})}
                        for turn in self.exchanges
                    ],
                    "ended": self.ended,
                    **({"call_actors": self.call_actors} if self.call_actors else {}),
                    **({"world_actors": self.world_actors} if self.world_actors else {}),
                    **({"notices": self.notices} if self.notices else {}),
                },
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")

    def actions(self) -> str:
        if not self.calls:
            return "(the agent called no tools at all)"
        lines = []
        for call in self.calls:
            outcome = (
                "refused" if call.refused else ("crashed" if not call.ok else "ok")
            )
            actor = "/".join(
                part
                for part in (
                    call.worker_label or call.worker_id,
                    call.task_label or call.task_id,
                )
                if part
            )
            lines.append(
                f"{f'[{actor}] ' if actor else ''}{call.name}({call.arguments})"
                f" -> {outcome}: {call.error or call.result}"
            )
        return "\n".join(lines)

    def crashed(self) -> list[Call]:
        """Calls that failed for our reasons rather than the world's.

        Reported separately and never counted against the agent. A run over a world that fell
        over says nothing about the agent, and scoring it as a failure is how a harness invents
        findings.
        """
        return [call for call in self.calls if not call.ok and not call.refused]


# What the simulated person is asked for on the turn that has no conversation behind it. It says
# which part they are playing, because that is exactly what is ambiguous here: a model handed a
# system prompt about an agent, and asked to speak with nothing preceding it, will sometimes reply
# as the agent instead of to it.
OPENING = (
    "The conversation is starting and you speak first. You are the person making contact, not "
    "the agent being contacted. What you write now are your first words to the agent, in the "
    "language you speak. Say what you came to say, in your own words, and nothing else. Do not "
    "offer to look anything up and do not answer on the agent's behalf."
)

# When an opening line is not sent, the person is told so, because their own session keeps every
# line they wrote: the next one has to follow from what the agent actually heard, which is nothing.
_UNSENT = (
    "That line was not sent: it was empty, or it repeated the description of your situation "
    "instead of saying something to the agent, so the agent has heard nothing yet. "
)
# The second try asks again for what they came to say; the last asks only for the greeting a person
# opens with, in the language they speak, so the request comes after the agent's reply.
_RETRY = (
    _UNSENT
    + "You are the person making contact. Say what you came to say to the agent, in your own words."
)
_GREET = (
    _UNSENT
    + "Open as a person does: say only a short greeting, in the language you speak."
)


def _opening_line(said: str, instruction: str) -> str:
    """The line the agent hears, or "" when it is not one: empty, only an end marker, or the
    scenario's own instruction handed back instead of the person's words."""
    line = said.replace(DONE, "").replace(STUCK, "").strip()
    written, heard = (
        " ".join(unicodedata.normalize("NFKC", text).casefold().split())
        for text in (instruction, line)
    )
    # The instruction must sit on whole words of the line, so a line that only shares a phrase
    # with it is still sent. Written without spaces, the spaces the line added are ignored.
    if " " in written:
        echoed = f" {written} " in f" {heard} "
    else:
        words = heard.split()
        edges = {0, *accumulate(map(len, words))}
        joined = "".join(words)
        echoed = bool(written) and any(
            joined.startswith(written, at) and at + len(written) in edges
            for at in edges
        )
    return "" if echoed else line


def customer_prompt(
    scenario: Scenario, contract: AgentContract, written: str = ""
) -> str:
    """The simulated person, from the prompt the harness wrote for this agent.

    The prompt belongs to the environment, not to this loop: it is written once for the agent and
    each scenario fills its slots. Only the ending convention is added here, because it is how
    this particular loop knows a conversation is over.
    """
    from ..simulator import fill

    if written:
        filled, missing = fill(written, scenario.slots())
        if missing:
            raise RuntimeError(
                f"the simulator prompt asks for {', '.join(missing)}, which {scenario.name} does "
                "not supply. An unfilled slot reaches the caller verbatim."
            )
    else:
        # No simulator prompt was written, which the environment gate refuses for a
        # conversational agent. Kept minimal rather than inventing a character.
        filled = (
            f"You are contacting {contract.agent}, which is: {contract.one_liner}\n\n"
            f"WHAT YOU ARE HERE TO DO:\n{scenario.instruction}"
        )
    return (
        filled
        + "\n\nWhen you have got what you came for, or accepted that you cannot, say the one "
        f"line you would actually say to end it, then {DONE} on a line of its own. If the agent "
        f"is going in circles and you would give up, do the same with {STUCK}.\n"
        "Do not end while the agent is waiting on you. A refusal that offers you two "
        "alternatives, or asks you a question, is not the end of the conversation: answer it, "
        "and end after that."
    )


def _message(turn: Exchange) -> dict[str, Any]:
    if turn.actor:
        return {
            "role": "actor",
            "actor": turn.actor.get("id"),
            "name": turn.actor.get("name"),
            "content": turn.text,
        }
    return {"role": "assistant" if turn.speaker == "agent" else "user", "content": turn.text}


class ChatActors:
    """The actors who write in one chat conversation. Each comes in when its trigger is met (one definition with every
    other runtime, `due`), and from then on is asked after every agent turn what it does: write a message, act in the
    world through the door, both, or wait. Nothing caps an actor but the limits the environment sets (`limits`, and
    an actor's own `max_lines`); what happened to each is recorded as a call records it."""

    def __init__(
        self,
        actors: list[dict[str, Any]],
        *,
        seed: str,
        model: str | None,
        limits: dict[str, Any] | None = None,
        act: Callable[..., Any] | None = None,
        records: Callable[[str], list[dict[str, Any]]] | None = None,
        meter: Any = None,
        clock: Callable[[], float] = time.time,
        about: dict[str, str] | None = None,
        history: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        from fi.simulate.simulation.call_actors import Waits

        self.actors = [one for one in actors if one.get("entered", True) is not False]
        self.skipped = [one for one in actors if one.get("entered", True) is False]
        self.seed = seed
        self.model = model
        self.limits = dict(limits or {})
        # Sends an actor's action through the world's door: `act(actor_id, action, arguments, key=, parent=)`.
        self.act = act
        # What each world action is and the fields it takes, by its route, so an actor sends what the world reads.
        self.about = dict(about or {})
        self.records = records
        # This trial's world rows an actor wrote itself (its scheduled actions included), read from the door's log; an
        # actor decides knowing what it already did, so it never repeats or silently reverses itself.
        self.history = history
        # What each actor sent from this conversation and how the world answered, when no log can be read.
        self.acted: dict[str, list[dict[str, Any]]] = {str(one["id"]): [] for one in self.actors}
        self.meter = meter
        self.turns_taken = 0
        self.written: dict[str, list[str]] = {str(one["id"]): [] for one in self.actors}
        self.triggered: set[str] = set()
        self.done: set[str] = set()
        self.decisions = 0
        # Turns each actor took answering the agent again within one of the person's turns, held to the live limits.
        self.follow_ups: dict[str, int] = {}
        self.events: list[dict[str, Any]] = []
        # How long the conversation waits for an actor once due, and its fallback when it does not answer.
        self.waits = Waits(clock)

    def _own_rows(self, key: str) -> list[dict[str, Any]]:
        if self.history is not None:
            try:
                return [
                    {"action": row.get("name"), "arguments": row.get("arguments"), "outcome": row.get("outcome")}
                    for row in self.history(key)
                ]
            except Exception:  # noqa: BLE001 - an unreadable log falls back to what this conversation sent
                pass
        return list(self.acted.get(key, []))

    def _own_actions(self, key: str) -> list[str]:
        """What this actor already did in the world this trial, oldest first, marked as its own."""
        import json

        return [json.dumps({"by": "you", **row}, default=str) for row in self._own_rows(key)]

    def _already_committed(self, key: str, action: str, arguments: dict[str, Any]) -> bool:
        import json

        wanted = json.dumps(arguments or {}, sort_keys=True, default=str)
        last = action.rsplit(".", 1)[-1]
        return any(
            row.get("outcome") == "committed"
            and str(row.get("action") or "").rsplit(".", 1)[-1] == last
            and json.dumps(row.get("arguments") or {}, sort_keys=True, default=str) == wanted
            for row in self._own_rows(key)
        )

    def _room(self) -> bool:
        cap = self.limits.get("caller_takes_back_after_turns")
        return cap is None or self.turns_taken < int(cap)

    def follow_up_room(self) -> bool:
        """Whether an actor may answer the agent again before the person's next turn: within the live-turn limits,
        and while some actor in the conversation still has turns of its own."""
        per_trial = self.limits.get("live_turns_per_trial")
        if per_trial is not None and sum(self.follow_ups.values()) >= int(per_trial):
            return False
        return self._room() and any(self._may_follow_up(key) for key in self.triggered - self.done)

    def _may_follow_up(self, key: str) -> bool:
        per_actor = self.limits.get("live_turns_per_actor")
        return per_actor is None or self.follow_ups.get(key, 0) < int(per_actor)

    async def due_lines(
        self,
        *,
        agent_turns: int,
        last_text: str,
        tools_called: list[str],
        conversation: list[str],
        follow_up: bool = False,
    ) -> list[tuple[dict[str, Any], str]]:
        """The lines actors write now, after the agent's latest turn, in the scenario's order; their actions in the
        world are sent as they decide them. A `follow_up` asks again, after the agent answered an actor, only the
        actors with live turns left; each that writes or acts spends one."""
        from fi.simulate.simulation.call_actors import Moment, decision_brief, due, read_decision

        if not self._room():
            return []
        moment = Moment(
            agent_turns=agent_turns, last_text=last_text, tools_called=tools_called, records=self.records
        )
        cap = self.limits.get("max_active")
        out = []
        for actor in self.actors:
            key = str(actor["id"])
            if key in self.done:
                continue
            if follow_up and (key not in self.triggered or not self._may_follow_up(key) or not self.follow_up_room()):
                continue
            if key not in self.triggered:
                if not due(dict(actor.get("trigger") or {}), moment, seed=self.seed, actor_id=key):
                    continue
                if cap is not None and len(self.triggered - self.done) >= int(cap):
                    self.events.append({"actor": key, "kind": "dropped", "reason": "no room: max_active"})
                    continue
                self.triggered.add(key)
                self.events.append({"actor": key, "kind": "triggered", "turn": agent_turns})
                self.waits.due(actor)
            if actor.get("max_lines") is not None and len(self.written[key]) >= int(actor["max_lines"]):
                self.done.add(key)
                self.events.append({"actor": key, "kind": "finished", "reason": "its own line limit reached"})
                continue
            brief = decision_brief(
                actor,
                situation=str(actor.get("situation") or ""),
                said=self.written[key],
                conversation=conversation,
                language=str(actor.get("language") or ""),
                seen=self._own_actions(key) if self.act else None,
                actions={one: self.about.get(one, "") for one in actor.get("permitted_actions") or []} if self.act else {},
            )
            try:
                decision = read_decision(await self._write(actor, brief))
            except ActorModelFailed as failed:
                # A model that did not answer is never the actor's line: the actor says nothing more this trial.
                self.done.add(key)
                self.events.append({"actor": key, "kind": "dropped", "reason": "its model did not answer",
                                    "detail": str(failed)[:300]})
                continue
            if follow_up and (decision.line or decision.action):
                self.follow_ups[key] = self.follow_ups.get(key, 0) + 1
            if decision.action and self._already_committed(key, decision.action, decision.arguments):
                # The same action it already committed: done once is done; it is not sent again.
                self.events.append({"actor": key, "kind": "repeated", "action": decision.action,
                                    "why": "already committed this action in this trial"})
                decision = type(decision)(line=decision.line, action="", arguments={}, why=decision.why)
            if decision.action and self.act is not None:
                self.decisions += 1
                from ..world_door import record_action

                # Without an identity in that service the actor still acts as itself, never as the agent.
                identity = (actor.get("identities") or {}).get(decision.action.split(".", 1)[0], "") or str(
                    actor.get("id") or ""
                )
                # A record change the actor may make goes to the world's records, as a scheduled one does; anything
                # else is a tool call in its service.
                record = record_action(decision.action) is not None and decision.action in (
                    actor.get("permitted_actions") or []
                )
                outcome = await self.act(
                    key,
                    decision.action,
                    decision.arguments,
                    key=f"{self.seed}:{key}:c{self.decisions}",
                    parent=None,
                    **({"form": "record"} if record else {}),
                    **({"identity": identity} if identity and record_action(decision.action) is None else {}),
                )
                self.events.append(
                    {"actor": key, "kind": "acted", "action": decision.action, "outcome": outcome, "why": decision.why}
                )
                self.acted.setdefault(key, []).append({
                    "action": decision.action, "arguments": decision.arguments,
                    "outcome": outcome.get("outcome") if isinstance(outcome, dict) else outcome,
                })
                self.waits.answered(actor)
            if not decision.line:
                if decision.waits:
                    self.events.append({"actor": key, "kind": "waited", "why": decision.why})
                continue
            self.written[key].append(decision.line)
            self.events.append({"actor": key, "kind": "delivered", "name": actor.get("name"), "text": decision.line})
            self.waits.answered(actor)
            out.append((actor, decision.line))
        out += await self._fall_back()
        if out:
            self.turns_taken += 1
        return out

    async def _fall_back(self) -> list[tuple[dict[str, Any], str]]:
        """Each actor whose wait ran out: its fallback applied and recorded. A declined request is a labelled line,
        never written in the actor's own words."""
        out = []
        for actor, fallback, outcome in self.waits.expired(self.actors):
            key = str(actor["id"])
            self.done.add(key)
            event = {"actor": key, "kind": "timeout", "fallback": fallback, "outcome": outcome,
                     "reason": f"no answer in time: {outcome}"}
            self.events.append(event)
            if outcome == "declined":
                out.append((actor, declined_line(actor)))
            elif outcome == "fallback_action" and self.act is not None:
                from ..world_door import record_action

                self.decisions += 1
                # Taken with its own arguments, and as a record change when it is one this actor may make, exactly as
                # the world's dispatcher takes a fallback, so a decline writes its record on chat too.
                record = record_action(fallback) is not None and fallback in (actor.get("permitted_actions") or [])
                sent = await self.act(
                    key,
                    fallback,
                    dict(actor.get("fallback_arguments") or {}),
                    key=f"{self.seed}:{key}:fallback",
                    parent=None,
                    **({"form": "record"} if record else {}),
                )
                if isinstance(sent, dict):
                    event["status"] = sent.get("status")
                    event["action_outcome"] = sent.get("outcome")
            elif outcome == "answered_by":
                instead = next((one for one in self.actors if str(one["id"]) == fallback), None)
                if instead is not None and fallback not in self.triggered:
                    self.triggered.add(fallback)
                    self.events.append({"actor": fallback, "kind": "triggered", "instead_of": key})
        return out

    def end(self) -> None:
        """The conversation is over: a wait still open is recorded as such, as the world records its own."""
        for key in self.waits.open():
            self.events.append({"actor": key, "kind": "timeout", "outcome": "unresolved_at_end",
                                "reason": "no answer in time: unresolved_at_end"})
            self.waits.settled.add(key)

    async def _write(self, actor: dict[str, Any], brief: str) -> str:
        from ..actor_model import decider

        decide = decider(self.meter, self.limits.get("model") or self.model)
        return (await decide(brief, str(actor["id"]), actor.get("model"))).strip()

    def summary(self) -> list[dict[str, Any]]:
        """Each actor the conversation included: triggered, delivered, dropped, as a call records them."""
        out = [
            {
                "actor": str(one["id"]),
                "name": one.get("name"),
                "triggered": str(one["id"]) in self.triggered,
                "delivered": len(self.written[str(one["id"])]),
                "dropped": [
                    e.get("reason")
                    for e in self.events
                    if e["actor"] == one["id"] and e["kind"] in ("dropped", "timeout")
                ],
            }
            for one in self.actors
        ]
        out += [
            {"actor": str(one["id"]), "name": one.get("name"), "triggered": False, "delivered": 0,
             "dropped": [], "entered": False, "reason": one.get("reason")}
            for one in self.skipped
        ]
        return out


async def _started_by_event(
    target: Target,
    starts_with: Any,
    on_exchange: Callable[[Exchange], Any] | None,
    actors: ChatActors | None = None,
) -> Transcript:
    """One invocation of the agent by the scenario's task or an actor's message, and whatever it answers; no one
    else speaks. What it did is in the world, which is what the run is graded on."""
    transcript = Transcript()

    def record(exchange: Exchange) -> None:
        transcript.exchanges.append(exchange)
        if on_exchange:
            on_exchange(exchange)

    await target.open()
    try:
        sender = starts_with.actor
        listed = next((one for one in (actors.actors if actors else []) if str(one["id"]) == sender), None)
        actor = {"id": sender, "name": (listed or {}).get("name") or sender} if sender is not None else None
        if listed is not None:
            # The message that starts the run is the sender's own line, as the call records it.
            actors.triggered.add(sender)
            actors.written[sender].append(starts_with.text)
        record(Exchange("actor" if sender is not None else "task", starts_with.text, time.time(), actor))
        try:
            reply = await target.say(starts_with.text)
        except TargetConversationEnded as ended:
            reply = ended.final_message
        if reply:
            record(Exchange("agent", reply, time.time()))
        transcript.ended = FINISHED
    finally:
        await target.close()
    transcript.calls = list(target.world.calls) if hasattr(target, "world") else []
    transcript.spent_usd = target.spent_usd
    return transcript


async def converse(
    target: Target,
    scenario: Scenario,
    contract: AgentContract,
    *,
    world_root: Any = None,
    model: str | None = None,
    on_exchange: Callable[[Exchange], Any] | None = None,
    actors: ChatActors | None = None,
) -> Transcript:
    """Run one scenario as a conversation and return what happened. `actors` write in it too, each when its trigger
    fires after an agent turn; the agent reads their message as theirs, and the person is told it. An event-started
    scenario (`starts_with`) has no person: the agent is invoked once by the scenario's task or that actor's message."""
    starts_with = getattr(scenario, "starts_with", None)
    if starts_with is not None:
        return await _started_by_event(target, starts_with, on_exchange, actors)
    transcript = Transcript()
    from ..simulator import load_simulator_prompt

    customer = Stage(
        SessionSpec(
            system_prompt=customer_prompt(
                scenario,
                contract,
                load_simulator_prompt(world_root) if world_root else "",
            ),
            max_turns=1,
            model=chosen_model(model),
            # The customer has no tools at all, so the permission machinery has nothing to
            # gate; leaving it off keeps this session exactly what it was before the seam.
            gated=False,
        ),
        name="customer",
    )

    def record(speaker: str, text: str, actor: dict[str, Any] | None = None) -> None:
        exchange = Exchange(speaker, text, time.time(), actor)
        transcript.exchanges.append(exchange)
        if on_exchange:
            on_exchange(exchange)

    await target.open()
    await customer.__aenter__()
    try:
        # The customer opens, in its own words. The scenario's instruction is written *about*
        # the caller ("orders two burgers and asks for..."), so speaking it verbatim would hand
        # the agent a stage direction instead of a person.
        said = _opening_line((await customer.say(OPENING)).text, scenario.instruction)
        for again in (_RETRY, _GREET):
            if said:
                break
            said = _opening_line((await customer.say(again)).text, scenario.instruction)
        # Only when every ask came back unusable does the run open with a line the person did not
        # write; they are told it with the agent's reply, so their session matches what was said.
        opened_for_them = not said
        if opened_for_them:
            said = "Hello."
        record("customer", said)
        for _turn in range(max(1, scenario.max_turns)):
            try:
                reply = await target.say(said)
            except TargetConversationEnded as ended:
                if ended.final_message:
                    record("agent", ended.final_message)
                transcript.ended = FINISHED
                break
            record("agent", reply or "(said nothing)")

            heard = reply or "(no response)"
            if actors is not None:
                exchanged: list[str] = []
                last, follow_up = reply or "", False
                # An actor the agent answered is asked again, so the two can go back and forth; the actors' own
                # limits end it, and the person always takes the next turn.
                while not transcript.ended and (not follow_up or actors.follow_up_room()):
                    written = await actors.due_lines(
                        agent_turns=_turn + 1,
                        last_text=last,
                        tools_called=[str(one.name) for one in getattr(getattr(target, "world", None), "calls", [])],
                        conversation=[f"{turn.speaker}: {turn.text}" for turn in transcript.exchanges],
                        follow_up=follow_up,
                    )
                    if not written:
                        break
                    for actor, line in written:
                        record("actor", line, {"id": actor["id"], "name": actor.get("name")})
                        try:
                            # A declined request reads as the labelled line it is, never as the actor writing it.
                            labelled = line == declined_line(actor)
                            answer = await target.say(
                                line if labelled else f"{actor.get('name') or 'Someone else'}: {line}"
                            )
                        except TargetConversationEnded as ended:
                            if ended.final_message:
                                record("agent", ended.final_message)
                            transcript.ended = FINISHED
                            break
                        record("agent", answer or "(said nothing)")
                        last = answer or ""
                        exchanged.append(
                            f"{actor.get('name') or 'Someone else'} wrote in the conversation: {line}\n"
                            f"The agent replied: {answer or '(no response)'}"
                        )
                    follow_up = True
                if exchanged:
                    heard = "\n".join(exchanged)
                if transcript.ended:
                    break
            if opened_for_them:
                heard = f"You opened with: {said}\nThe agent replied: {heard}"
                opened_for_them = False
            turn = await customer.say(heard)
            said = turn.text.strip()
            if DONE in said or STUCK in said:
                transcript.ended = GAVE_UP if STUCK in said else FINISHED
                # The closing line comes with the sentinel, and is kept. Breaking on the marker
                # alone threw it away, so every conversation ended on the agent's turn with
                # nothing after it: a transcript that reads as cut off rather than finished,
                # and no way to tell a person who left satisfied from one who was still waiting.
                closing = said.replace(DONE, "").replace(STUCK, "").strip()
                if closing:
                    record("customer", closing)
                break
            record("customer", said)
        else:
            transcript.ended = RAN_OUT
    except (Exception, asyncio.CancelledError) as exc:
        # Preserve simulator usage already consumed before a target/provider failure.  Call
        # runners attach this partial transcript to the failed usage record instead of silently
        # dropping a paid attempt that never reached a normal return.
        transcript.calls = list(target.world.calls) if hasattr(target, "world") else []
        transcript.spent_usd = target.spent_usd + customer.spent_usd
        (
            transcript.simulator_input_tokens,
            transcript.simulator_output_tokens,
        ) = getattr(customer, "simulator_tokens", (0, 0))
        _close_actors(transcript, actors)
        setattr(exc, "partial_transcript", transcript)
        trace_path = getattr(target, "trace_path", None)
        trace = getattr(exc, "trace", None)
        if trace_path is not None and trace is not None:
            from pathlib import Path

            path = Path(trace_path).with_name("http-failure.json")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(trace, sort_keys=True) + "\n", encoding="utf-8")
        raise
    finally:
        await customer.__aexit__(None, None, None)
        await target.close()

    transcript.calls = list(target.world.calls) if hasattr(target, "world") else []
    transcript.spent_usd = target.spent_usd + customer.spent_usd
    (
        transcript.simulator_input_tokens,
        transcript.simulator_output_tokens,
    ) = getattr(customer, "simulator_tokens", (0, 0))
    _close_actors(transcript, actors)
    return transcript


def _close_actors(transcript: Transcript, actors: ChatActors | None) -> None:
    """The conversation is over: open waits are recorded, and actors' model calls are counted as simulator usage like
    the caller's, into the call's tokens and cost."""
    if actors is not None:
        actors.end()
    meter = getattr(actors, "meter", None)
    if meter is None:
        return
    tokens_in, tokens_out, cost = meter.totals()
    transcript.simulator_input_tokens += tokens_in
    transcript.simulator_output_tokens += tokens_out
    transcript.spent_usd += cost
