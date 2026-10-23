"""Two parties talking: a simulated customer with a goal, and the agent under test.

The customer is a separate session that can only talk. It has no tools and no view of the world,
which is the point: it knows what it wants and how it behaves, and everything it learns about
what is possible it learns from what the agent tells it. An agent that lies to it gets away with
it here exactly as it would with a person, and that is what makes the transcript worth grading.

The conversation ends when the customer is done, when it gives up, or when it runs out of turns.
All three are recorded, because how a conversation ended is often the finding.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fi.simulate.simulation.sub_actors import (
    Cast,
    Moment,
    line_brief,
    someone_else,
    with_others,
)

from ..backends import SessionSpec

from ..config import chosen_model
from ..contract import AgentContract
from ..scenario import Scenario
from ..session import Stage
from ..world.runtime import Call
from .targets import Target

logger = logging.getLogger(__name__)

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
    # A sub-actor's turn: who it was, so it is never read as the person's or the agent's.
    name: str = ""
    sub_actor: str = ""

    def message(self, role: str, *, timed: bool = False) -> dict[str, Any]:
        when = {"at": self.at} if timed and self.at else {}
        if self.speaker != "sub_actor":
            return {"role": role, "content": self.text, **when}
        return {
            "role": "sub_actor",
            "name": self.name,
            "sub_actor": self.sub_actor,
            "content": self.text,
            **when,
        }


@dataclass
class Transcript:
    """What happened, in the two forms grading needs: what was said and what was done."""

    exchanges: list[Exchange] = field(default_factory=list)
    calls: list[Call] = field(default_factory=list)
    ended: str = ""
    spent_usd: float = 0.0
    simulator_input_tokens: int = 0
    simulator_output_tokens: int = 0
    # Per sub-actor: whether it was triggered, how many messages reached the agent, why any were dropped.
    sub_actors: list[dict[str, Any]] = field(default_factory=list)

    def spoken(self) -> str:
        return "\n".join(
            f"{someone_else(turn.name)}: {turn.text}"
            if turn.speaker == "sub_actor"
            else f"{turn.speaker}: {turn.text}"
            for turn in self.exchanges
        )

    def canonical_messages(self) -> list[dict[str, str]]:
        """The turns in the role convention the judge reads: `user` said it, `assistant` answered."""
        return [
            turn.message("assistant" if turn.speaker == "agent" else "user")
            for turn in self.exchanges
        ]

    def artifact(self) -> bytes:
        """Return the lossless transcript wire format used by hosted ingestion."""
        return (
            json.dumps(
                {
                    "transcript": self.spoken(),
                    "messages": [
                        turn.message(
                            "user" if turn.speaker == "customer" else "assistant",
                            timed=True,
                        )
                        for turn in self.exchanges
                    ],
                    "ended": self.ended,
                    **({"sub_actors": self.sub_actors} if self.sub_actors else {}),
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
            lines.append(
                f"{call.name}({call.arguments}) -> {outcome}: {call.error or call.result}"
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
    "the agent being contacted. Say what you came to say, in your own words, and nothing else. "
    "Do not offer to look anything up, do not answer on their behalf, and do not greet them and "
    "wait: say the thing you actually want."
)

# How an opening turn reads when the part has been swapped. Offers of help, not requests for it.
_AS_THE_AGENT = (
    "let me ",
    "i'll look",
    "i will look",
    "i'd be happy to look",
    "i can help you with that",
    "how can i help",
    "how may i help",
    "what would you like to know",
    "i'll check",
    "i will check",
    "let me check",
    "sure! let me",
)


def _answered_as_the_agent(said: str) -> bool:
    """Whether the opening line is the agent's part rather than the person's."""
    opening = said.strip().lower()
    return any(mark in opening for mark in _AS_THE_AGENT)


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


class _ChatSubActors:
    """The scenario's chat participants besides the person, within the call's limits."""

    def __init__(
        self, scenario: Scenario, world_root: Any, model: str | None, seed: str
    ) -> None:
        from ..sub_actor import load_sub_actors, sub_actors_enabled

        listing = (
            load_sub_actors(world_root)
            if world_root and scenario.sub_actors and sub_actors_enabled()
            else None
        )
        self.actors = [
            {
                **actor.model_dump(exclude_none=True),
                "instruction": one.instruction,
                "trigger": one.trigger.model_dump(),
            }
            for one in scenario.sub_actors
            if listing
            and (actor := listing.get(one.id)) is not None
            and actor.channel == "thread_message"
        ]
        self.cast = Cast(self.actors, seed)
        self.situation = scenario.instruction
        self.language = (
            ", ".join(scenario.persona.languages) if scenario.persona else ""
        )
        self.model = model
        self.said: dict[str, list[str]] = {}

    async def after_agent(
        self, turns: int, reply: str, tools_called: list[str], conversation: list[str]
    ) -> list[Exchange]:
        """The messages the sub-actors write after this agent turn."""
        self.cast.entering(
            Moment(agent_turns=turns, last_text=reply, tools_called=tools_called)
        )
        written = []
        for actor in self.actors:
            if not self.cast.may_speak(actor, turns):
                continue
            try:
                text = await self._write(actor, conversation)
            except Exception as exc:  # noqa: BLE001 - a quiet participant is a weaker test, never a failed call
                self.cast.dropped(actor, f"message failed: {type(exc).__name__}")
                self.cast.finish(actor)
                continue
            if not text:
                self.cast.finish(actor, "nothing more to say")
                continue
            now = time.time()
            self.said.setdefault(str(actor["id"]), []).append(text)
            self.cast.delivered(actor, text, turns, now, now)
            written.append(
                Exchange(
                    "sub_actor",
                    text,
                    now,
                    name=str(actor.get("name") or ""),
                    sub_actor=str(actor["id"]),
                )
            )
        if written:
            self.cast.took_turn()
        return written

    async def _write(self, actor: dict[str, Any], conversation: list[str]) -> str:
        brief = line_brief(
            actor,
            situation=self.situation,
            said=self.said.get(str(actor["id"]), []),
            conversation=conversation,
            language=self.language,
        )
        async with Stage(
            SessionSpec(
                system_prompt=brief
                + "\n\nWrite your next message, short, as yourself. Output only the message, "
                "or nothing at all when you have nothing more to say.",
                max_turns=1,
                model=chosen_model(self.model),
                gated=False,
            ),
            name="sub-actor",
        ) as stage:
            return (await stage.say("Write it now.")).text.strip()


async def converse(
    target: Target,
    scenario: Scenario,
    contract: AgentContract,
    *,
    world_root: Any = None,
    model: str | None = None,
    on_exchange: Callable[[Exchange], Any] | None = None,
    seed: str = "",
) -> Transcript:
    """Run one scenario as a conversation and return what happened."""
    transcript = Transcript()
    from ..simulator import load_simulator_prompt

    others = _ChatSubActors(scenario, world_root, model, seed or secrets.token_hex(8))
    # What the person may fall back to saying: their own situation, never the note about the others.
    situation = scenario.instruction
    if others.actors:
        scenario = scenario.model_copy(
            update={"instruction": with_others(scenario.instruction, others.actors)}
        )
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

    def keep(exchange: Exchange) -> None:
        transcript.exchanges.append(exchange)
        if on_exchange:
            on_exchange(exchange)

    def record(speaker: str, text: str) -> None:
        keep(Exchange(speaker, text, time.time()))

    await target.open()
    await customer.__aenter__()
    try:
        # The customer opens, in its own words. The scenario's instruction is written *about*
        # the caller ("orders two burgers and asks for..."), so speaking it verbatim would hand
        # the agent a stage direction instead of a person.
        opening = await customer.say(OPENING)
        said = opening.text.strip() or situation
        if _answered_as_the_agent(said):
            # The opening turn is the one with no conversation behind it, and a model asked to
            # speak into that gap will sometimes take the other part: it offers to look something
            # up, the agent replies that no question was asked, and the run fails for a reason
            # that has nothing to do with the agent. The instruction is the fallback, because a
            # blunt version of the right question tests more than a fluent version of the wrong
            # one.
            said = situation
        record("customer", said)
        # Messages from a sub-actor that the target cannot take with a sender name: they reach it at
        # the start of the person's next message.
        waiting = ""
        for _turn in range(max(1, scenario.max_turns)):
            try:
                reply = await target.say(
                    "\n\n".join(part for part in (waiting, said) if part)
                )
            except TargetConversationEnded as ended:
                if ended.final_message:
                    record("agent", ended.final_message)
                transcript.ended = FINISHED
                break
            waiting = ""
            record("agent", reply or "(said nothing)")

            heard = []
            tools = [
                call.name
                for call in getattr(getattr(target, "world", None), "calls", [])
            ]
            speakers = {"customer": "The person", "agent": "The agent"}
            conversation = [
                f"{speakers.get(one.speaker) or one.name}: {one.text}"
                for one in transcript.exchanges
            ]
            for entered in await others.after_agent(
                _turn + 1, reply or "", tools, conversation
            ):
                keep(entered)
                heard.append(f"[{entered.name}]: {entered.text}")
                interject = getattr(target, "interject", None)
                if callable(interject):
                    interject(entered.name, entered.text)
            if heard and not callable(getattr(target, "interject", None)):
                waiting = "\n\n".join(heard)
            # The chat is shared: the person sees the other participant's messages too.
            turn = await customer.say("\n\n".join([reply or "(no response)", *heard]))
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
    except Exception as exc:
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
        raise
    finally:
        others.cast.hang_up()
        transcript.sub_actors = others.cast.summary() if others.actors else []
        await customer.__aexit__(None, None, None)
        await target.close()

    transcript.calls = list(target.world.calls) if hasattr(target, "world") else []
    transcript.spent_usd = target.spent_usd + customer.spent_usd
    (
        transcript.simulator_input_tokens,
        transcript.simulator_output_tokens,
    ) = getattr(customer, "simulator_tokens", (0, 0))
    return transcript
