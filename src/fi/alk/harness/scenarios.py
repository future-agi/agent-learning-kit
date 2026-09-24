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
    TOOL_NAMES,
    load_scenarios,
    scenario_tools,
    world_summary,
)
from .session import Stage

logger = logging.getLogger(__name__)

SKILL = "write-scenarios"
PLAN_SKILL = "plan-suite"

# Turns a scenario costs in practice: look at the world, rehearse the calls, submit, and often one
# more to correct what a gate refused. A briefed writer reads the world again in its own context and
# its turns come out of this same budget, so the rate is the handed-out one: the loop decides whether
# to hand out, and a budget that assumed it would not is a budget that cannot afford the decision.
TURNS_EACH = 9
# Enough to write a handful without the budget being the thing that stops it.
TURNS_FLOOR = 120
# One writer's own ceiling. A worker is a smaller agent with a smaller goal: it reads the world
# once, writes its slice, and reports. Given the stage's budget instead it can spend the suite's
# turns on its own part, and nothing is left for the rest.
WRITER_TURNS = int(os.environ.get("ALK_HARNESS_WRITER_TURNS", "300") or 300)
# Everything a writer needs to write its slice, and nothing else. Read the world, rehearse the
# calls, name what is checked, submit. Planning the suite, reading it back, saving it and changing
# the contract all belong to the loop that briefed it; offered here they get used, and a tool a
# writer has no business calling is turns and context spent on nothing.
WRITER_TOOLS = ("inspect_world", "try_calls", "add_sub_goal", "submit_scenario")
# The two tools that write a scenario. A loop that hands the suite out is not offered them: an
# orchestrator holding the writing tools writes, which is what it did, and then pays for the
# suite in its own context instead of in its writers'.
WRITES_A_SCENARIO = ("try_calls", "submit_scenario")
# Above this, the loop hands the suite out instead of writing it. Leaving the choice to the model
# read well and did not survive contact: offered the writing tools, it wrote twenty scenarios itself
# in one lane, thirty-six minutes, zero sub-agents dispatched. A small suite is genuinely faster
# written in place, so the rule is a size and not a ban.
# Zero: the loop always hands the suite out. Every judgement call about when to delegate was wrong.
HANDS_OUT_ABOVE = int(os.environ.get("ALK_HARNESS_HANDS_OUT_ABOVE", "0") or 0)


# How many scenarios one writer should be given. Affordability alone put 33 on a single writer,
# so a suite of twenty ran entirely serially: one writer, twenty scenarios, every refusal a rewrite
# in the same context, forty-four minutes for what five hundred did in ten. A slice is small enough
# that writers finish together and a rewrite costs one slice, not the suite.
SLICE = int(os.environ.get("ALK_HARNESS_SLICE", "6") or 6)


def writers_for(wanted: int) -> int:
    """How many writers a suite of this size needs.

    Bounded twice: a writer may not be given more than it can afford to write, and it is not given
    more than a slice, so wall clock falls with the number of writers rather than staying flat.
    """
    most_a_writer_can_write = max(WRITER_TURNS // TURNS_EACH, 1)
    each = max(1, min(SLICE, most_a_writer_can_write))
    return max(min(-(-wanted // each), MOST_WORKERS_AT_ONCE), 1)


def turns_for(wanted: int) -> int:
    """A turn budget that affords one writer per slice, plus the loop itself.

    Derived rather than guessed, because the budget is what decides how many writers the loop may
    brief at once, and briefing fewer than the suite needs is what turns one round into several.
    A fixed ceiling is what made asking for a large suite pointless: generation stopped partway
    through, and `save_scenarios` refuses a count that does not match what was asked for, so a run
    that asked for fifty and reached twenty-eight saved nothing at all.
    """
    writers = writers_for(wanted)
    # The loop spends a turn per brief it sends, plus the planning and progress calls around them.
    own = 40 + writers
    # Headroom, deliberately generous: a refused submission costs turns, there are several ways to
    # be refused, and a stage that runs out mid-suite loses everything the spent turns bought. The
    # ceiling exists to stop a runaway, not to ration the work.
    return max(TURNS_FLOOR, (writers * WRITER_TURNS + own) * 3)


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
                "them different from what the other writers were given.\n\n"
                "**Brief it fifteen to twenty scenarios, never two or three.** A writer reads the "
                "world once and then writes its whole slice, so that reading is paid once per "
                "writer whatever the slice is worth. Measured on a hundred-scenario run: 45 "
                "writers were briefed instead of the seven the budget allows, the world was read "
                "215 times, and the stage cost $12.86 where fifty scenarios in slices of sixteen "
                "cost $3.85. Group the empty cells until a brief is worth a session."
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
                + (
                    "\n\n## Not yours to do\n\nThe method above names tools this session does "
                    "not have: "
                    + ", ".join(
                        f"`{name}`"
                        for name in TOOL_NAMES
                        if name not in WRITER_TOOLS
                    )
                    + ". Planning the suite, saving it, reading it back and changing the contract "
                    "belong to whoever briefed you. Where the method tells you to reach for one, "
                    "say so in your report instead and it will be done for you."
                )
                + "\n\n## Your part of the suite\n\nYou are one writer among several working "
                "on the same suite at the same time. Your brief names what the others were "
                "given; that list is there so you can stay out of theirs, not so you can "
                "cover it. Write only what your own brief names: a scenario that strays is "
                "either a duplicate of somebody else's or a gap in yours. The names already "
                "taken come back with every submission.\n\n"
                "Each scenario carries its use case verbatim and its own one-line `branch` "
                "saying what makes it different from the others you write here. **Branches are "
                "where the variety lives**: the ordinary path, the branch that cannot be "
                "completed, the rule under pressure, state that has to carry across turns, the "
                "same request against a differently seeded world.\n\n"
                "What each one has to be, before you submit it:\n"
                "  - every value real, read out of the world with inspect_world, never invented\n"
                "  - an instruction that is a circumstance the person is living through, not a "
                "script of lines to say\n"
                "  - a setup that makes true whatever the instruction presumes, and a ready "
                "check that proves it\n"
                "  - a solution worked out with try_calls first, so the gates are not where you "
                "find out it cannot be passed\n"
                "  - sub-goals named from the shared catalogue, and checks that assert the right "
                "call with the right arguments or the right end state, never that something "
                "merely happened\n"
                "  - a scenario a competent agent could plausibly fail. If any correct "
                "implementation passes it for free, it teaches nothing and is not worth the "
                "run\n\n"
                "**The number in your brief is yours, not the suite's.** Every submission "
                "reports how many the whole suite holds, across every writer; that count is not "
                "your target and reaching it is not your job. Write what you were asked for and "
                "stop.\n\n"
                "Look at the world first, and read the sub-goals already defined. Submit each "
                "scenario with submit_scenario as you prove it rather than holding them to the "
                "end, then stop: do not save, and do not ask what to do next.\n\n"
                "**Finish with a report, because it is the only thing the loop sees of your "
                "session.** Name every scenario you wrote and the cell each one covers; say "
                "which part of your brief you could not cover and why, whether a gate refused "
                "you and what it said, and anything the world would not support. A round is "
                "planned from these reports, so a brief that comes back with a bare count "
                "leaves the next round guessing at what is still missing."
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
    # How many writers the turn budget can afford, and how many of those may be in flight together.
    # Briefing fewer than this in one message costs a whole extra wave of writer-lifetimes, which is
    # the difference between a large suite taking one writer's time and taking several.
    # The two hard limits. How the suite is cut is the loop's to decide: it has read the grid.
    affordable = max(budget // WRITER_TURNS, 1)
    at_once = max(min(affordable, MOST_WORKERS_AT_ONCE), 1)
    most_a_writer_can_write = max(WRITER_TURNS // TURNS_EACH, 1)
    hands_out = wanted > HANDS_OUT_ABOVE
    loop_server = ToolServer(
        name=server.name,
        version=server.version,
        # Withheld rather than discouraged: a loop that can write, writes.
        tools=[
            one
            for one in server.tools
            if not hands_out or one.name not in WRITES_A_SCENARIO
        ],
    )
    spec = SessionSpec(
        # The agent and its world before the method: grounding evidence read before the
        # instructions that operate on it is followed more closely than the same evidence
        # buried between the instructions and the task.
        system_prompt=(
            f"## This agent\n\n{contract.brief(with_data=True, sample_rows=3)}"
            f"\n\n## Its world\n\n{world_summary(destination)}"
            # One role per session. A suite this size is handed out, so this session plans and
            # briefs and never writes, and the writing method belongs to the writers rather than
            # here. Carrying it anyway is what made the loop behave like a writer.
            # Both methods, always. Whether to write the suite here or brief writers for it is
            # a judgement about this suite, so the loop needs the plan and the writing method in
            # front of it either way. A threshold in code decided it for every suite alike.
            + f"\n\n{load_skill(SKILL)}"
            + f"\n\n{load_skill(PLAN_SKILL, preamble=False)}"
            # Whatever this kind of agent adds on top. A file under skills/kinds/ that
            # declares `applies_to: modality=<kind>` is appended here, so supporting a
            # new kind of agent is adding that file and nothing else.
            # `voicemail` gates the mailbox skill the way `modality` gates this one.
            + discovered_skills(
                modality=contract.modality,
                voicemail="on" if voicemail_enabled() else "off",
                conversational="yes" if contract.conversational else "no",
            )
            + f"\n\nPlan the grid first, then decide how to cut it. You choose how many "
            f"scenarios each writer gets and how many writers the suite needs; you have read the "
            f"grid and know which cells are rich and which are thin, and an even split sizes a "
            f"use case with one real branch the same as one with six.\n\n"
            f"Two limits are not yours to choose. A writer may spend {WRITER_TURNS} turns and a "
            f"scenario costs about {TURNS_EACH}, so one writer is worth roughly "
            f"{most_a_writer_can_write} scenarios: brief it more and it runs out mid-slice and "
            f"the rest of its cells come back empty. And at most {at_once} writers run at once; "
            f"a brief beyond that is refused and has written nothing.\n\n"
            f"Brief a whole round in ONE message: sub-agents briefed in the same message run at "
            f"the same time, and briefing one, waiting for it, then briefing the next runs them "
            f"one at a time for no reason. That single choice is the difference between a large "
            f"suite taking one writer's time and taking {at_once} times as long. Each brief names "
            f"different cells, so no two writers are given the same work.\n\n"
            f"A writer runs only when you call it. Writing that writers have been dispatched, or "
            f"that you are standing by for them, calls nothing: the stage ends there with whatever "
            f"was already submitted. One hosted run announced three writers and saved 11 of 50.\n\n"
            + (
                "\n\nYou do not hold the scenario-writing tools on this suite. It is too large to "
                "write in one context and writing it there is what makes a twenty take forty "
                "minutes, so this stage is yours to plan, brief and check, and the scenarios are "
                "your sub-agents' to write. Brief a whole round in ONE message.\n\n"
                if wanted > HANDS_OUT_ABOVE
                else ""
            )
            + (
            f"Every writer comes back with a report: what it wrote, and what of its brief it "
            f"could not cover. Read those, then call suite_progress, which names the cells still "
            f"empty without returning a scenario body. The next round goes out the same way, in "
            f"one message, for what is still missing. Repeat until suite_progress reports the "
            f"count. Never say work is running on the strength of a brief you did not see "
            f"accepted, and check suite_progress before you believe your own count.")
            # The loop cannot ration what it cannot see. Without this it has no reason to believe
            # writing the suite alone will not fit, and it runs out mid-suite instead of delegating.
            + (
                f"\n\nYou have {budget} turns for this whole stage and every tool call spends one, "
                f"including the calls your writers make: a writer reads the world in its own "
                f"context, which is far cheaper than carrying it in yours, but its turns come out "
                f"of this same budget. A writer may spend up to {WRITER_TURNS}, so across all "
                f"rounds together brief at most {max(budget // WRITER_TURNS, 1)} writers and keep "
                f"turns back for yourself. Running out mid-suite loses everything the spent turns "
                f"bought."
            )
            + (
                f"\n\nWrite {wanted} scenarios."
                if not kept
                else f"\n\n{len(kept)} scenarios already exist and are loaded: "
                + ", ".join(scenario.name for scenario in kept)
                + ". Submitting one under an existing name replaces it."
            )
        ),
        servers={SCENARIO_SERVER: loop_server},
        # Delegation is imposed above HANDS_OUT_ABOVE by withholding the writing tools, and
        # offered below it. How the suite is cut stays the loop's judgement; whether it is cut
        # at all cannot be, because the answer was always no.
        builtins=("AskUserQuestion", DELEGATE_TOOL),
        workers=writer_worker(contract, destination, server, budget),
        cwd=str(destination.parent if destination.parent.exists() else Path.cwd()),
        max_turns=budget,
        model=chosen_model(),
        ask=ask,
        thinking=True,
        # A stage that has handed work out has nothing to say while it waits: see the constant.
        idle_timeout_seconds=QUIET_WHILE_DELEGATING_SECONDS,
    )
    return Stage(spec, name=SKILL), destination


def opening(
    contract: AgentContract,
    wanted: int = 10,
    existing: int = 0,
) -> str:
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
        for mark in (
            "429",
            "resource_exhausted",
            "resourceexhausted",
            "rate limit",
            "quota",
        )
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
            what,
            pause,
            round(waited),
            RATE_LIMIT_TOTAL_WAIT_SECONDS,
            refusal[:200],
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
            lambda: stage.say(
                opening(contract, wanted),
                on_event=on_event,
            ),
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
