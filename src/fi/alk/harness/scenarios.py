"""Stage three: write the scenarios the agent will be tested with.

Reads the contract and the world that was built from it, and produces scenarios grounded in both.
The stage can look at the world and run calls against throwaway copies of it, which is what keeps
a scenario about a real record rather than a plausible-sounding one.

Like the other stages it stays open. A suite is usually right on the second look, and "make three
of these harder" is the next thing said rather than a regeneration from nothing.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .backends import (
    DELEGATE_TOOL,
    MOST_WORKERS_AT_ONCE,
    SessionSpec,
    ToolServer,
    WorkerSpec,
)
from .config import (
    artifact_dir,
    chosen_model,
    discovered_skills,
    load_skill,
    writer_model,
)
from .contract import AgentContract
from .scenario import Scenario, voicemail_enabled
from .scenario_tools import (
    SCENARIO_SERVER,
    load_scenarios,
    scenario_tools,
    world_summary,
)
from .session import Stage

logger = logging.getLogger(__name__)

SKILL = "write-scenarios"
PLAN_SKILL = "plan-suite"

# Turns a scenario costs in practice: look at the world, rehearse the calls, submit, and often
# one more to correct what a gate refused.
TURNS_EACH = 3
# A briefed writer reads the world again in its own context, and its turns come out of the same
# budget as the loop that briefed it, so a handed-out suite spends more turns than one written
# in a single session.
TURNS_EACH_HANDED_OUT = 9
# The suite size above which the skill has the loop hand the writing out rather than do it.
HANDS_OUT_ABOVE = 20
# Enough to write a handful without the budget being the thing that stops it.
TURNS_FLOOR = 120
# One writer's own ceiling. A worker is a smaller agent with a smaller goal: it reads the world
# once, writes its slice, and reports. Given the stage's budget instead it can spend the suite's
# turns on its own part, and nothing is left for the rest.
WRITER_TURNS = int(os.environ.get("ALK_HARNESS_WRITER_TURNS", "110") or 110)
# Everything a writer needs to write its slice, and nothing else. Read the world, rehearse the
# calls, name what is checked, submit. Planning the suite, reading it back, saving it and changing
# the contract all belong to the loop that briefed it; offered here they get used, and a tool a
# writer has no business calling is turns and context spent on nothing.
WRITER_TOOLS = ("inspect_world", "try_calls", "add_sub_goal", "submit_scenario")


def turns_for(wanted: int) -> int:
    """A turn budget that grows with the suite being asked for.

    A fixed ceiling is what made asking for a large suite pointless: generation stopped partway
    through, and `save_scenarios` refuses a count that does not match what was asked for, so a run
    that asked for fifty and reached twenty-eight saved nothing at all. The budget has to follow
    the request, or the request cannot be honoured.
    """
    each = TURNS_EACH_HANDED_OUT if wanted > HANDS_OUT_ABOVE else TURNS_EACH
    return max(TURNS_FLOOR, wanted * each + 40)


# Named with underscores because one backend sanitises a worker name into an identifier and the
# other passes it through. The same spelling on both is what lets the skill name the worker.
WRITER = "scenario_writer"


def writer_worker(
    contract: AgentContract, destination: Path, server: ToolServer, budget: int
) -> dict[str, WorkerSpec]:
    """The worker this stage may run to write part of the suite.

    One definition rather than one per slice: the model writes the brief when it delegates, so
    the same worker takes whichever part of the suite it decides to hand out. It is given the
    agent and its world up front because a worker never sees the parent's conversation.

    It gets the stage's own tool server, so a scenario it proves lands in the same list the
    stage later saves from. Two tools are withheld. ``save_scenarios`` rewrites the index and
    deletes folders it does not know about, so two workers saving at once would each remove the
    other's work; the stage saves once, when the fan-out is done. ``suite_progress`` is the
    briefing loop's own instrument: a writer that reads it starts deciding what the suite needs
    instead of writing what it was given.

    It is given ``WRITER_TOOLS`` and nothing else. ``budget`` is the stage's, and a writer is
    capped well below it. Every call a writer makes
    is spent from the same budget as the loop that briefed it, so an uncapped writer can spend
    the suite's turns on one slice. A writer that runs out says so and the loop hands the rest
    of its brief to the next round.
    """
    return {
        WRITER: WorkerSpec(
            description=(
                "Writes and proves part of a scenario suite in its own session. Brief it with "
                "which use cases and situations to cover, how many scenarios, and what makes "
                "them different from what the other writers were given."
            ),
            instructions=(
                f"## This agent\n\n{contract.brief(with_data=True, sample_rows=3)}"
                f"\n\n## Its world\n\n{world_summary(destination)}"
                f"\n\n{load_skill(SKILL)}"
                + discovered_skills(
                    modality=contract.modality,
                    voicemail="on" if voicemail_enabled() else "off",
                    conversational="yes" if contract.conversational else "no",
                )
                + "\n\n## Your part of the suite\n\nYou are one writer among several working "
                "on the same suite at the same time, and you cannot see what the others were "
                "given. Write only what your brief names. Submit each scenario with "
                "submit_scenario as you prove it, rather than holding them to the end. Do not "
                "save the suite; whoever briefed you saves once when every writer is done. "
                "When you finish, name every scenario you wrote and say which part of your "
                "brief you could not cover, if any."
            ),
            servers={
                SCENARIO_SERVER: ToolServer(
                    name=server.name,
                    version=server.version,
                    tools=[spec for spec in server.tools if spec.name in WRITER_TOOLS],
                )
            },
            max_turns=min(WRITER_TURNS, budget),
            # Empty inherits the parent's model. A writer is briefed rather than deciding, so a
            # cheaper model may do this work; whether it does is a measurement, not an assumption,
            # because a weaker writer that fails the gates more often spends the saving on retries.
            model=writer_model(),
        )
    }


REVIEWER = "suite_reviewer"

# What a reviewer may touch. Reading the suite and the world is the whole job; a reviewer that
# could submit would answer its own objection instead of reporting it, and one that could save
# would rewrite the index underneath the writers still running.
REVIEWER_TOOLS = ("inspect_world", "inspect_scenario")


def reviewer_worker(
    contract: AgentContract, destination: Path, server: ToolServer, budget: int
) -> dict[str, WorkerSpec]:
    """A worker that reads a finished suite as a whole and says what is missing.

    Nobody else looks at the suite whole. Each writer sees only what it was briefed with, so a
    use case that came back one short, or an obvious branch every writer assumed somebody else
    had, survives to the end unnoticed. Its report comes back as its final message, which is
    what delegation returns anyway, so it needs no tool of its own to answer through.
    """
    return {
        REVIEWER: WorkerSpec(
            description=(
                "Reads a finished suite as a whole and reports what it does not cover. Run it "
                "once the writers are done and before saving. It reports; it never writes."
            ),
            instructions=(
                "You are reviewing a suite of tests somebody else wrote for an AI agent, in "
                "parallel, each writer blind to the others. Your only job is to say what is "
                "missing.\n\n"
                "Look for: a use case of this agent that nothing covers; a use case covered "
                "only on its ordinary path, where the branch that cannot be completed or the "
                "rule under pressure is the interesting one; two scenarios that are the same "
                "test under different names, leaving the branch one of them claimed "
                "uncovered.\n\n"
                "Judge coverage of the agent, not of anyone's plan. Do not ask for more of what "
                "is already well covered, and do not report a gap you cannot name a scenario "
                "for. A suite of the right size that covers what matters is finished, and "
                "saying so is the useful answer.\n\n"
                "Report each gap as the use case, the scenario that is missing in one line, and "
                "why it matters. Report nothing when there is nothing to report."
                f"\n\n## This agent\n\n{contract.brief()}"
            ),
            servers={
                SCENARIO_SERVER: ToolServer(
                    name=server.name,
                    version=server.version,
                    tools=[
                        spec for spec in server.tools if spec.name in REVIEWER_TOOLS
                    ],
                )
            },
            max_turns=budget,
        )
    }


def open_stage(
    contract: AgentContract,
    *,
    out: Path | None = None,
    wanted: int = 10,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 0,
) -> tuple[Stage, Path]:
    """A live write-the-scenarios stage, and where it will write."""
    destination = out or artifact_dir(contract.agent)
    server, kept = scenario_tools(contract, destination, destination, wanted=wanted)
    budget = max_turns or turns_for(wanted)
    spec = SessionSpec(
        # The agent and its world before the method: grounding evidence read before the
        # instructions that operate on it is followed more closely than the same evidence
        # buried between the instructions and the task.
        system_prompt=(
            f"## This agent\n\n{contract.brief(with_data=True, sample_rows=3)}"
            f"\n\n## Its world\n\n{world_summary(destination)}"
            f"\n\n{load_skill(SKILL)}"
            # The preamble is shared by every skill, so the second one carries only its method.
            f"\n\n{load_skill(PLAN_SKILL, preamble=False)}"
            # Whatever this kind of agent adds on top. A file under skills/kinds/ that
            # declares `applies_to: modality=<kind>` is appended here, so supporting a
            # new kind of agent is adding that file and nothing else.
            # `voicemail` gates the mailbox skill the way `modality` gates this one.
            + discovered_skills(
                modality=contract.modality,
                voicemail="on" if voicemail_enabled() else "off",
                conversational="yes" if contract.conversational else "no",
            )
            + f"\n\nAt most {MOST_WORKERS_AT_ONCE} writers may run at the same time."
            # The loop cannot ration what it cannot see. Without this it has no reason to believe
            # writing the suite alone will not fit, and it runs out mid-suite instead of delegating.
            + (
                f"\n\nYou have {budget} turns for this whole stage, and every tool call spends one. "
                f"Writing one scenario takes several: exploring the world, probing calls, submitting, "
                f"and fixing what the gates refuse. Work out whether {wanted} of them fit in {budget} "
                f"before you start writing, because running out mid-suite loses the turns you spent. "
                f"A writer you brief reads the world in its own context, which is cheaper than "
                f"carrying it in yours, but its turns come out of this same budget."
            )
            + (
                f"\n\nWrite {wanted} scenarios."
                if not kept
                else f"\n\n{len(kept)} scenarios already exist and are loaded: "
                + ", ".join(scenario.name for scenario in kept)
                + ". Submitting one under an existing name replaces it."
            )
        ),
        servers={SCENARIO_SERVER: server},
        # Delegation is offered, never imposed. Whether a suite is worth splitting is a judgement
        # about this suite, so the skill argues it and the stage decides; a threshold in code here
        # decided it for every suite alike and was wrong at both ends.
        builtins=("AskUserQuestion", DELEGATE_TOOL),
        workers={
            **writer_worker(contract, destination, server, budget),
            **reviewer_worker(contract, destination, server, budget),
        },
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        max_turns=budget,
        model=chosen_model(),
        ask=ask,
        thinking=True,
        # A stage that has handed work out has nothing to say while it waits: see the constant.
        idle_timeout_seconds=QUIET_WHILE_DELEGATING_SECONDS,
    )
    return Stage(spec, name=SKILL), destination


def opening(contract: AgentContract, wanted: int = 10, existing: int = 0) -> str:
    if existing:
        return (
            f"There are already {existing} scenarios for {contract.agent!r}, and they are "
            "loaded. Use inspect_scenario before changing each one so every unchanged field is "
            "preserved exactly. Say what you want changed, or add to them. Anything you submit "
            "under an existing name replaces it."
        )
    return (
        f"Write {wanted} scenarios for {contract.agent!r}.\n\n"
        "Look at the world first with inspect_world so every scenario names real records, and "
        "read the sub-goals already defined. Then plan the suite, and decide from that plan "
        "whether to write it yourself or to brief writers to write parts of it at the same "
        "time; your method says how to judge that and you are the one who saves either way.\n\n"
        "However it is written, each scenario is worked out and submitted on its own; never "
        "hold a suite in one long response. Emit a tool call after each scenario so progress is "
        "visible and proved work survives a stop. "
        "Work out each scenario's solution with try_calls before you submit it, because a "
        "scenario is only kept if its solution passes its own "
        "checks and those checks fail without it. In a source-provisioned world, keep each "
        "solution step's arguments exactly model-facing. If the raw dependency needs trusted "
        "fields injected by the worker, put its complete payload in environment_arguments; "
        "never pretend the model supplied an internal identifier, a resolved lookup, a priced "
        "result, or any other value it could not have known. Treat every contract phrase that ties "
        "a value to this conversation literally: the reference "
        "solution must create that state earlier in the same conversation. Never pre-seed "
        "opaque state that the agent has no public tool or session state to retrieve. Cover the "
        "ordinary case, the request that has "
        "to be refused, the rule under pressure, and at least one where state has to carry "
        "across several turns. If a proof says an intended check is vacuous or broken, repair "
        "that named sub-goal with add_sub_goal and resubmit. Never evade a gate by deleting a "
        "check for behavior the scenario still claims to test. Then save_scenarios."
    )


def load(destination: Path) -> list[Scenario]:
    """The scenarios written for this agent, if any have been."""
    return load_scenarios(Path(destination))


# What a suite costs, and what it is allowed to cost.
#
# How long a session may say nothing before the harness treats it as hung, where the default of
# ten minutes is wrong for this stage.
#
# A stage that has handed work out is waiting on its workers, and a delegating call does not
# return until they finish. It is working the entire time and has nothing to emit while it works,
# so the default bound kills a fan-out mid-flight and throws away everything the writers proved.
# A hundred scenarios across several writers is comfortably an hour, and any writer may also be
# waiting out a quota refusal inside that.
#
# Still bounded, because the reason the bound exists is real: a dropped provider stream leaves a
# session alive forever. The outer bound is the run's own authoring deadline.
QUIET_WHILE_DELEGATING_SECONDS = 5400.0


# A session refused by the provider is retried rather than abandoned: its work is still worth doing and
# a slice keeps what it already proved. The quota is measured over a minute, so each wait clears a
# minute; a shorter one asks inside the same window and is refused again for the same reason. What is
# bounded is the total, not the number of tries: five minutes of waiting is worth a slice, and a run
# that waits longer than that is not going to be rescued by waiting more.
RATE_LIMIT_BACKOFF_SECONDS = 60
RATE_LIMIT_JITTER_SECONDS = 30
RATE_LIMIT_TOTAL_WAIT_SECONDS = 300


def _rate_limited(said: str) -> bool:
    """Whether this is the provider refusing for rate or quota rather than for what was asked."""
    lowered = said.lower()
    return any(
        mark in lowered
        for mark in ("429", "resource_exhausted", "resourceexhausted", "rate limit", "quota")
    )


def _refusal_in(broke: BaseException | None, ended: Any) -> str:
    """The refusal, when a turn or an exception is one for rate or quota, and empty otherwise.

    Two shapes because a backend has two ways of reporting a dead model call, and only one of them
    is an exception. The Vertex backend never raises: it catches everything and finishes the turn
    with `outcome` "failed" and the provider's own words in `error`. A retry that watched only for
    exceptions therefore never fired on the backend the hosted run actually uses, which is how a
    quota refusal went on costing a whole slice while the waiting code looked correct.
    """
    if broke is not None:
        said = f"{type(broke).__name__} {broke}"
        return said if _rate_limited(said) else ""
    if str(getattr(ended, "outcome", "") or "") != "failed":
        return ""
    said = str(getattr(ended, "error", "") or "")
    return said if _rate_limited(said) else ""


def _refusal_pause() -> float:
    """How long to wait before asking again, spread out so sessions do not return together.

    Sessions are refused at the same moment because they ask at the same moment, so a fixed wait has
    them all wake together and refuse together. Each wait clears the quota's minute and carries
    jitter on top, which is what breaks the lockstep.
    """
    return round(
        RATE_LIMIT_BACKOFF_SECONDS + random.uniform(0, RATE_LIMIT_JITTER_SECONDS), 1
    )


async def survive_refusal(
    run: Callable[[], Awaitable[Any]],
    *,
    what: str,
    on_event: Callable[..., Any] | None = None,
    enough: Callable[[], bool] | None = None,
) -> Any:
    """Run something that talks to the model, waiting out a refusal for rate or quota.

    Used by every session that drives its own model turn, because any of them can be the one the
    provider refuses, and losing the planning turn costs the whole suite rather than one slice.
    Anything that is not a rate or quota refusal is raised, so a real fault still fails fast.
    """
    waited = 0.0
    while True:
        broke: BaseException | None = None
        ended: Any = None
        try:
            ended = await run()
        except Exception as raised:  # noqa: BLE001 - classified below, re-raised when not a refusal
            broke = raised
        refusal = _refusal_in(broke, ended)
        pause = _refusal_pause()
        spent_out = waited + pause > RATE_LIMIT_TOTAL_WAIT_SECONDS
        if not refusal or spent_out or (enough is not None and enough()):
            if broke is not None:
                raise broke
            return ended
        waited += pause
        logger.warning(
            "%s was refused for rate or quota; waiting %ss (%ss of %ss spent): %s",
            what, pause, round(waited), RATE_LIMIT_TOTAL_WAIT_SECONDS, refusal[:200],
        )
        if on_event:
            on_event({"type": "waiting_on_provider", "what": what, "seconds": pause})
        await asyncio.sleep(pause)


async def write(
    contract: AgentContract,
    *,
    out: Path | None = None,
    wanted: int = 10,
    follow_ups: list[str] | None = None,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
    max_turns: int = 0,
) -> list[Scenario]:
    """Run the stage start to finish. Returns whatever scenarios were saved."""
    stage, destination = open_stage(
        contract, out=out, wanted=wanted, ask=ask, max_turns=max_turns
    )
    async with stage:
        # The planning turn is the expensive one to lose: a refusal here costs the whole suite, not
        # one slice, so it waits the same way a writer does.
        await survive_refusal(
            lambda: stage.say(opening(contract, wanted), on_event=on_event),
            what="the opening turn",
            on_event=on_event,
        )
        for follow_up in follow_ups or []:
            await survive_refusal(
                lambda message=follow_up: stage.say(message, on_event=on_event),
                what="a follow-up turn",
                on_event=on_event,
            )
    return load(destination)
