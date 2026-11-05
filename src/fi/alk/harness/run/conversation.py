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
    """The actors who write in one chat conversation: when each comes in (its trigger, as on a call), what it writes
    (from its brief), and what happened to each, recorded as a call records them. As on a call, actors write on at most
`TAKEOVER_AFTER_TURNS` of the agent's turns in all, so the conversation stays the person's."""

    def __init__(self, actors: list[dict[str, Any]], *, seed: str, model: str | None) -> None:
        self.actors = [one for one in actors if one.get("entered", True) is not False]
        self.skipped = [one for one in actors if one.get("entered", True) is False]
        self.seed = seed
        self.model = model
        self.turns_taken = 0
        self.written: dict[str, list[str]] = {str(one["id"]): [] for one in self.actors}
        self.triggered: set[str] = set()
        self.events: list[dict[str, Any]] = []

    async def due_lines(
        self, *, agent_turns: int, last_text: str, tools_called: list[str], conversation: list[str]
    ) -> list[tuple[dict[str, Any], str]]:
        """The lines actors write now, after the agent's latest turn, in the scenario's order."""
        from fi.simulate.simulation.call_actors import TAKEOVER_AFTER_TURNS, Moment, due, line_brief

        if self.turns_taken >= TAKEOVER_AFTER_TURNS:
            return []
        moment = Moment(agent_turns=agent_turns, last_text=last_text, tools_called=tools_called)
        out = []
        for actor in self.actors:
            key = str(actor["id"])
            fired = key in self.triggered or due(
                dict(actor.get("trigger") or {}), moment, seed=self.seed, actor_id=key
            )
            if not fired:
                continue
            first = key not in self.triggered
            self.triggered.add(key)
            if not first and not self.written[key]:
                continue
            if not first:
                # After its first message an actor writes again only when the agent speaks to it by name.
                name = str(actor.get("name") or "")
                if not name or name.casefold() not in last_text.casefold():
                    continue
            brief = line_brief(
                actor,
                situation=str(actor.get("situation") or ""),
                said=self.written[key],
                conversation=conversation,
                language=str(actor.get("language") or ""),
            )
            line = await self._write(brief)
            if not line:
                self.events.append({"actor": key, "kind": "dropped", "reason": "wrote nothing"})
                continue
            self.written[key].append(line)
            self.events.append({"actor": key, "kind": "delivered", "name": actor.get("name"), "text": line})
            out.append((actor, line))
        if out:
            self.turns_taken += 1
        return out

    async def _write(self, brief: str) -> str:
        stage = Stage(
            SessionSpec(
                system_prompt=brief + "\n\nWrite only your next message, as plain text.",
                max_turns=1,
                model=chosen_model(self.model),
                gated=False,
            ),
            name="actor",
        )
        await stage.__aenter__()
        try:
            return (await stage.say("Write your message now.")).text.strip()
        finally:
            await stage.__aexit__(None, None, None)

    def summary(self) -> list[dict[str, Any]]:
        """Each actor the conversation included: triggered, delivered, dropped, as a call records them."""
        out = [
            {
                "actor": str(one["id"]),
                "name": one.get("name"),
                "triggered": str(one["id"]) in self.triggered,
                "delivered": len(self.written[str(one["id"])]),
                "dropped": [e.get("reason") for e in self.events if e["actor"] == one["id"] and e["kind"] == "dropped"],
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
    target: Target, starts_with: Any, on_exchange: Callable[[Exchange], Any] | None
) -> Transcript:
    """One invocation of the agent by an actor's message, and whatever it answers; no one else speaks. What it did
    is in the world, which is what the run is graded on."""
    transcript = Transcript()

    def record(exchange: Exchange) -> None:
        transcript.exchanges.append(exchange)
        if on_exchange:
            on_exchange(exchange)

    await target.open()
    try:
        record(Exchange("actor", starts_with.text, time.time(), {"id": starts_with.actor, "name": starts_with.actor}))
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
    scenario (`starts_with`) has no person: the agent is invoked once by that actor's message."""
    starts_with = getattr(scenario, "starts_with", None)
    if starts_with is not None:
        return await _started_by_event(target, starts_with, on_exchange)
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
                written = await actors.due_lines(
                    agent_turns=_turn + 1,
                    last_text=reply or "",
                    tools_called=[str(one.name) for one in getattr(getattr(target, "world", None), "calls", [])],
                    conversation=[f"{turn.speaker}: {turn.text}" for turn in transcript.exchanges],
                )
                for actor, line in written:
                    record("actor", line, {"id": actor["id"], "name": actor.get("name")})
                    try:
                        answer = await target.say(f"{actor.get('name') or 'Someone else'}: {line}")
                    except TargetConversationEnded as ended:
                        if ended.final_message:
                            record("agent", ended.final_message)
                        transcript.ended = FINISHED
                        break
                    record("agent", answer or "(said nothing)")
                    heard = (
                        f"{actor.get('name') or 'Someone else'} wrote in the conversation: {line}\n"
                        f"The agent replied: {answer or '(no response)'}"
                    )
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
    return transcript
