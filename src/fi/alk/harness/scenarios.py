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
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .axes import axes_for
from .backends import SessionSpec, ToolServer, WorkerSpec, resolve, tool, tool_server
from .backends.base import MOST_WORKERS_AT_ONCE

from .config import (
    artifact_dir,
    chosen_model,
    compose_skills,
    load_skill,
    scenario_thinking,
    stage_backend,
    stage_model,
    writer_effort,
)
from .blueprint import SLICE_SCENARIOS, WORTH_PLANNING
from .blueprint import load as load_canvas
from .grid_tools import GRID_SERVER, Coverage, grid_tools
from .sample import Pick, coverage, plan as plan_picks
from .scenariogen.model.catalogue import load_catalogue
from .contract import AgentContract
from .scenariogen.model.scenario import Scenario
from .scenario_tools import (
    SCENARIO_SERVER,
    parallel_suites,
    scenario_tools,
    world_summary,
)
from .scenariogen.store.suite import (
    forget_journal,
    journalled,
    load_scenarios,
    write_scenarios,
)
from .session import Stage
from .tools import schema

logger = logging.getLogger(__name__)

SKILL = "scenarios/write"
PLAN_SKILL = "scenarios/plan"
PARENT_SKILL = "scenarios"

# What the stage may reach for beyond its own tools. Everything the host offers, because the
# scenarios worth writing come from reading the agent rather than from reading its contract.
# Write and Edit are deliberately absent. The harness's own artifacts go through tools that
# validate them, and a stage able to edit the agent under test could make its scenarios pass by
# changing the agent rather than by writing a better scenario.
STAGE_TOOLS = (
    "AskUserQuestion",
    "Read",
    "Glob",
    "Grep",
    "Bash",
    "WebSearch",
    "WebFetch",
)

# The worker the stage runs to write one slice of the grid. Underscored because one backend
# rewrites anything else to this form, and the skill has to name the tool the model actually sees.
WRITER = "scenario_writer"

# The review pass runs its own tool server, kept apart from the writers' one so a reviewer can
# only report gaps and never submit or save a scenario itself.
REVIEW_SERVER = "suite-review"


# Turns a scenario costs in practice: look at the world, rehearse the calls, submit, and often
# one more to correct what a gate refused.
TURNS_EACH = 3
# Enough to write a handful without the budget being the thing that stops it.
TURNS_FLOOR = 120

# One worker's turn budget: enough to inspect, rehearse, prove and submit its slice.
#
# Sized from the largest slice it can be handed rather than picked. A slice is clamped to twice
# the recommended size, each scenario costs about `TURNS_EACH` in practice, and before writing any
# of them a writer reads the agent under test. At a flat sixty it had 3.8 turns per scenario
# including that reading, so slices came back part-filled, their buckets reopened, and the next
# writer paid the same reading cost again to finish somebody else's work.
WRITER_TURNS = SLICE_SCENARIOS * 2 * (TURNS_EACH + 1) + 24


def turns_for(wanted: int) -> int:
    """A turn budget that grows with the suite being asked for.

    A fixed ceiling is what made asking for a large suite pointless: generation stopped partway
    through with the rest of the suite unwritten. (`save_scenarios` used to refuse a short count
    on top of that, which turned a partial run into a saved-nothing run; it saves whatever was
    proved now.) The budget has to follow the request, or the request cannot be honoured.
    """
    return max(TURNS_FLOOR, wanted * TURNS_EACH + 40)


def _working_dir(destination: Path) -> str:
    """Where a session's relative paths resolve from.

    The run directory, because that is what holds ``environment-bundle`` and therefore the agent's
    own source. It used to be the parent, so every relative path the skill talks about missed by
    one level and each writer spent turns hunting for code it had been told to read.
    """
    where = Path(destination)
    return str(where if where.is_dir() else Path.cwd())


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
    # Whether this stage is still planning decides two things below, so it is worked out first.
    planning = wanted >= WORTH_PLANNING and load_canvas(destination).planned < wanted
    # One list, shared by the stage and every writer it runs, because the stage is what saves.
    shared: list[Scenario] = load_scenarios(destination)
    workers = (
        writer_workers(contract, destination, share=shared)
        if wanted >= FEWEST_WORTH_DELEGATING
        else {}
    )
    server, kept = scenario_tools(
        contract,
        destination,
        destination,
        wanted=wanted,
        delegates=bool(workers),
        share=shared,
        # While there is a plan to write, probing the agent is the work rather than a detour.
        probing=planning,
    )
    grid_server, held = grid_tools(contract, destination, wanted=wanted)
    held.canvas = load_canvas(destination)
    # Large suites are planned before they are written. Written one at a time they converge:
    # each scenario is composed with the last few in view, and by fifty the suite has settled
    # into one shape without anyone having done anything wrong.
    # Against the count asked for here, not the blueprint's own: an empty blueprint records a
    # target of zero, so asking it for its shortfall says nothing is missing.
    spec = SessionSpec(
        # Same ordering as the slice writer: the agent and its world before the method.
        system_prompt=(
            f"## This agent\n\n{contract.brief(with_data=True)}"
            f"\n\n## Its world\n\n{world_summary(destination)}"
            # Only the method for the job in hand. A planner does not need the writing skill,
            # and carrying it cost 44KB on every turn of the stage that plans.
            + f"\n\n{compose_skills(PARENT_SKILL, PLAN_SKILL if planning else SKILL)}"
            + (
                f"\n\nPlan all {wanted} scenarios first, then write them."
                if planning
                else f"\n\nWrite {wanted} scenarios."
                if not kept
                else f"\n\n{len(kept)} scenarios already exist and are loaded: "
                + ", ".join(scenario.name for scenario in kept)
                + ". Submitting one under an existing name replaces it."
            )
            + (
                f"\n\n{held.canvas.planned} scenarios are already planned as "
                f"{len(held.canvas.angles)} angles. Work from the canvas rather than planning "
                "again: show_canvas for what is left, claim_slice for the next writer's work, "
                "fold_return when it comes back."
                if held.canvas.angles and not planning
                else ""
            )
        ),
        servers={SCENARIO_SERVER: server, GRID_SERVER: grid_server},
        builtins=STAGE_TOOLS,
        # No tool gate. This stage is reading an agent's own repository in order to write tests
        # against it, and the interesting cases are the ones a summary of that repository does
        # not mention: what its code refuses, what its data already contains, what a comment
        # admits. Withholding the tools that read those makes the suite shallower, and every
        # artifact it produces still has to pass the three gates before it is kept.
        gated=False,
        cwd=_working_dir(destination),
        max_turns=max_turns or turns_for(wanted),
        model=chosen_model(stage_model(SKILL)),
        ask=ask,
        # Off unless the run asks for it. See config.scenario_thinking for why the old
        # unconditional refusal no longer holds.
        thinking=scenario_thinking(),
        workers=workers,
    )
    # Named for this stage if it was, otherwise whatever the run chose. Writing a suite and
    # reading an unfamiliar codebase are different jobs, and a provider counts its rate limit
    # per model, so the two stages are worth pinning separately.
    named = stage_backend(SKILL)
    return Stage(spec, name=SKILL, backend=resolve(named, spec.model) if named else None), destination


def writer_workers(
    contract: AgentContract, destination: Path, share: list[Scenario] | None = None
) -> dict[str, WorkerSpec]:
    """The worker the stage may run to write one slice of the grid.

    One definition, not one per slice: the model writes the brief when it calls, so the same
    worker covers whichever cells it decides to hand out. It gets the scenario tools so it
    proves and submits its own work, and it is told the agent and its world up front because a
    worker never sees the parent's conversation.

    ``save_scenarios`` is withheld. Saving rewrites the index and deletes folders it does not
    know about, so two workers saving at once would delete each other's scenarios; the parent
    saves once when the fan-out is done.
    """
    # The writers append into the caller's own list, which is what the stage later saves from.
    # Their own list would be invisible to it.
    server, _ = scenario_tools(
        contract,
        destination,
        destination,
        wanted=0,
        can_save=False,
        start_from=None if share is not None else [],
        share=share,
    )
    return {
        WRITER: WorkerSpec(
            description=(
                "Writes and proves the scenarios for one slice of the coverage grid. Give it "
                "the cells to cover, how many scenarios, and what makes them distinct."
            ),
            instructions=(
                f"## This agent\n\n{contract.brief(with_data=True)}"
                f"\n\n## Its world\n\n{world_summary(destination)}"
                f"\n\n{compose_skills(PARENT_SKILL, SKILL)}"
                "\n\n## Your slice\n\nYou are one writer among several working on the same "
                "suite at the same time. Write only the slice you were given, submit each "
                "scenario as you prove it, and report which cells you covered and any you "
                "could not. Do not save the suite; the stage saves once when every writer is "
                "done.\n\nIf your brief names the callers to use, use those and no others: "
                "their names, accents and locations were dealt across the whole suite, and "
                "you cannot see what your siblings were given."
                "\n\n## What to report back"
                "\n\nName every scenario you wrote, per bucket. The stage checks those names "
                "against the folder rather than taking a count on trust, so an unnamed scenario "
                "does not count towards anything."
                "\n\nFor each bucket you were given: its id, how many scenarios you wrote for "
                "it, and one sentence on what you actually covered and what you did not. Say "
                "plainly if a bucket holds fewer real cases than it was sized for."
                "\n\nThen, separately, **anything worth testing that your brief did not ask "
                "for**. You are the first person to look inside this part of the agent with its "
                "source open, so you will find cases nobody could see from outside: a branch two "
                "calls deep, a state the data only reaches after something else, a refusal that "
                "is not written down. List each as a grid cell, a few words on what makes it "
                "worth testing, and roughly how many scenarios it holds."
                "\n\nDo not widen the bucket you were given to swallow those, and do not write "
                "them yourself. Report them: the stage opens a bucket for each and somebody is "
                "given it properly. Absorbing them quietly hides the discovery and makes the "
                "count wrong."
            ),
            builtins=(),
            servers={SCENARIO_SERVER: server},
            max_turns=WRITER_TURNS,
            effort=writer_effort(),
        )
    }


def opening(
    contract: AgentContract,
    wanted: int = 10,
    existing: int = 0,
    delegates: bool | None = None,
) -> str:
    # Whether writers exist follows the same threshold the stage itself uses, so a caller that
    # does not track it still describes the run it actually gets.
    if delegates is None:
        delegates = wanted >= FEWEST_WORTH_DELEGATING
    if existing and existing < wanted:
        # A suite short of what was asked for is being *continued*, not edited. Told only that
        # scenarios exist and to say what it wants changed, a stage reads a large number, finds
        # nothing to change and stops: one attempt oriented itself and exited inside a minute
        # with three hundred still to write. The number outstanding has to be the first thing
        # said, and finishing has to be named as the work.
        return (
            f"{existing} of the {wanted} scenarios asked for exist and are loaded. "
            f"**{wanted - existing} are still to write, and writing them is the work.**\n\n"
            "Start with show_canvas to see what is still open, then claim_slice and brief a "
            "writer on it. Keep claiming and dispatching until claim_slice says nothing is "
            "open; that is the only finish line. Do not stop because the number already looks "
            "large.\n\n"
            "list_scenarios and show_coverage tell you what is there so you do not repeat it. "
            "Anything submitted under an existing name replaces it, so keep the names distinct."
        )
    if existing:
        return (
            f"There are already {existing} scenarios for {contract.agent!r}, and they are "
            "loaded. Start with list_scenarios and show_coverage so you are changing a suite "
            "you have read rather than one you assume. Use inspect_scenario before changing "
            "each one so every unchanged field is preserved exactly. Say what you want changed, "
            "or add to them. Anything you submit under an existing name replaces it, and "
            "drop_scenario removes one."
        )
    return (
        f"Write {wanted} scenarios for {contract.agent!r}.\n\n"
        "Start with show_grid. It is the space of everything this agent can be asked, derived "
        "from its contract, and it is the thing coverage is measured against. It was derived "
        "from tool names and a data schema, so check it against the agent's own source, which "
        "you can read: if it missed an object, split one in two, or turned an action into a "
        "thing, correct it with set_objects before planning anything.\n\n"
        "Then plan_suite for the number asked for, and record_canvas what you settle on before "
        "writing anything. plan_suite only suggests; record_canvas is what makes the plan exist. "
        "Without it there is no ledger, so nothing knows which buckets are done, a writer cannot "
        "claim a slice, and a run that stops cannot be resumed.\n\n"
        "Then write what the canvas holds. Look at the world "
        "with inspect_world so every scenario names real records. Work out each solution with "
        "try_calls before submitting, because a scenario is only kept if its solution passes "
        "its own checks and those checks fail without it.\n\n"
        "In a source-provisioned world, keep each solution step's arguments exactly "
        "model-facing. If the raw dependency needs trusted fields injected by the worker, put "
        "its complete payload in environment_arguments; never pretend the model supplied rider "
        "ids, resolved routes, fares, or other hidden state. Treat every contract phrase like "
        "'from this call' literally: the reference solution must create that state earlier in "
        "the same conversation. Never pre-seed opaque state that the agent has no public tool "
        "or session state to retrieve.\n\n"
        "If a proof says an intended check is vacuous or broken, repair that named sub-goal "
        "with add_sub_goal and resubmit. Never evade a gate by deleting a check for behaviour "
        "the scenario still claims to test. Then save_scenarios, and finish with show_coverage "
        "so what was left untested is on the record rather than implied by a count."
        + (
            # How wide to fan out is the model's call, not a number this file can know: it
            # depends on how many buckets are open and how alike they are. Say that the writers
            # exist and that they run at the same time, and leave the count to whoever can see
            # the canvas. Hiding this behind a flag left every suite written one at a time.
            "\n\nWriters run at the same time. claim_slice a slice per writer and brief them "
            "together, one brief each naming its coordinates, rather than waiting for one to "
            f"finish before starting the next. Use the writers well: run up to {AT_ONCE} at "
            f"once, which is the most there may be, and keep {AT_ONCE} of them working whenever "
            "the canvas has that much open, spread over as much of what is open as you can. Use "
            "fewer only when the buckets left would overlap, since "
            "several writers on near-identical buckets buy nothing, or when writers come back "
            "empty or refused, in which case find out why before claiming more. fold_return "
            "each one's result so what it did not cover reopens."
            if delegates
            else ""
        )
    )


def load(destination: Path) -> list[Scenario]:
    """The scenarios written for this agent, if any have been."""
    return load_scenarios(Path(destination))


# What a suite costs, and what it is allowed to cost.
#
# Writers run as separate model sessions, so wall clock is roughly the number of scenarios
# divided by how many run at once. The two ceilings below exist for different reasons: one
# protects the machine, the other protects the person waiting. Asking for a thousand scenarios
# The ceiling on fan-out, defined once in backends.base. It is told to the model rather than used
# to quietly override it: how many writers to actually run is the model's call, from what the
# canvas has open.
MOST_AT_ONCE = MOST_WORKERS_AT_ONCE

# How many scenarios one pass writes is whatever was asked for. A cap here used to serve a large
# ask a batch at a time, which meant the number the person asked for was not the number they got.
# What a stage is told to aim at, below the enforced ceiling so normal work never trips it.
AT_ONCE = 10

# Below this, one session writes the suite itself. Delegation buys parallelism and costs turns:
# each worker is briefed, runs, and reports, and for a handful of scenarios that overhead is the
# whole bill. Measured on two N=10 runs of the same suite: 54 turns without workers, 119 with,
# for output that was identical scenario by scenario.
FEWEST_WORTH_DELEGATING = 20

# How many times the suite is reviewed and topped up after the first pass. One is enough to
# catch a slice that came back short or a use case nobody covered; more turns it into a loop
# that keeps finding smaller things to say.
TOP_UP_ROUNDS = 1


@dataclass(frozen=True)
class Slice:
    """One writer's share of a suite: which cells of the grid, and why they are worth covering.

    A slice used to be a use case, which sized every use case identically however much was in
    it, and left the writers to invent what "different" meant. It is now a set of coordinates,
    so two writers cannot land on the same test and neither has to guess what the other took.

    ``use_case`` survives because results are grouped on it and a scenario still carries the
    contract's own wording. The cells decide what gets written; the use case decides where the
    result is filed.
    """

    picks: tuple[Pick, ...] = ()
    use_case: str = ""
    angle: str = ""
    why: str = ""
    # Only used when a slice is a top-up rather than a share of the plan, where the reviewer
    # named a gap in words instead of in coordinates.
    asked: int = 0

    @property
    def count(self) -> int:
        return len(self.picks) or self.asked or 1

    def named(self) -> str:
        if self.picks:
            first = self.picks[0].cell.name
            return first if len(self.picks) == 1 else f"{first} +{len(self.picks) - 1}"
        return f"{self.use_case}: {self.angle}" if self.angle else self.use_case


def slices_for(picks: list[Pick], at_once: int) -> list[Slice]:
    """One plan dealt into shares, each small enough for one writer to finish.

    Dealt round-robin rather than in blocks. The plan is ordered by value, so the first cells
    are the ones a suite is not worth running without; cutting it into contiguous blocks would
    hand every one of those to a single writer, and lose all of them together if that writer
    fails. Round-robin spreads the important cells across the slices.
    """
    if not picks:
        return []
    shares = max(1, min(at_once, len(picks)))
    dealt: list[list[Pick]] = [[] for _ in range(shares)]
    for index, pick in enumerate(picks):
        dealt[index % shares].append(pick)
    return [Slice(picks=tuple(share)) for share in dealt if share]


def even_slices(wanted: int, use_cases: list[str]) -> list[Slice]:
    """The fallback split, when nobody said how the work should be divided.

    Evenly, with the remainder going to the ones named first, because a contract lists its
    primary use cases before its marginal ones. It is a poor plan and it is meant to be: a use
    case with one real branch gets the same share as one with six, so the first pads and the
    second under-covers. It exists so a caller that supplies no plan still gets a suite.
    """
    if not use_cases:
        return []
    if wanted <= len(use_cases):
        return [Slice(use_case=case, count=1) for case in use_cases[:wanted]]
    each, extra = divmod(wanted, len(use_cases))
    return [
        Slice(use_case=case, count=each + (1 if i < extra else 0))
        for i, case in enumerate(use_cases)
    ]


def planned(wanted: int, use_cases: list[str], given: list[dict] | None) -> list[Slice]:
    """The split this suite will actually be written to.

    A plan supplied by the caller wins, because whoever is talking to the person has just read
    the contract and the world and knows which use cases have something in them. Sizing every
    use case identically is the thing that made suites pad in one place and under-cover in
    another, and the plan is the only part of the process that knows the difference.

    Anything the plan leaves out is filled in evenly, and anything it over-asks for is trimmed,
    so a plan can be rough without producing a suite nobody asked for.
    """
    if not given:
        return even_slices(wanted, use_cases)

    known = {case.strip().lower(): case for case in use_cases}
    slices: list[Slice] = []
    for one in given:
        if not isinstance(one, dict):
            continue
        case = str(one.get("use_case") or "").strip()
        if not case:
            continue
        # Match the contract's own wording where the plan paraphrased it, so a slice is filed
        # under a use case the coverage count recognises rather than a near-miss of one.
        case = known.get(case.lower(), case)
        try:
            count = max(1, int(one.get("count") or 1))
        except (TypeError, ValueError):
            count = 1
        slices.append(
            Slice(
                use_case=case,
                angle=str(one.get("angle") or "").strip(),
                count=count,
                why=str(one.get("why") or "").strip(),
            )
        )
    if not slices:
        return even_slices(wanted, use_cases)

    # Trim from the end rather than scaling everything down: the plan put its most valuable
    # slices first, and shaving one scenario off each is how a deliberate plan becomes an even
    # one again.
    total = sum(one.count for one in slices)
    while total > wanted and slices:
        last = slices[-1]
        if last.count > 1:
            slices[-1] = Slice(last.use_case, last.angle, last.count - 1, last.why)
        else:
            slices.pop()
        total = sum(one.count for one in slices)
    return slices


def callers_for(index: int, wanted: int) -> str:
    """Which callers this slice should write, so the suite varies across slices as well as within.

    Instruction alone cannot do this. Each writer is blind to the others, so each independently
    picks the safest value and the suite converges on it: measured across three suites, more
    than half the callers came out "Professional and formal" and over three quarters American,
    with nobody doing anything wrong. Worse, a slice writing a single scenario has nothing to
    vary at all.

    So the spread is dealt out here, the same way the work is. Each slice is handed a different
    starting point in the platform's own vocabularies and told to begin there. It is a
    suggestion rather than a rule, because the caller still has to suit the scenario: a stolen
    phone is not a cheerful call whatever this hands out.
    """
    from .scenariogen.model.persona import offered

    people = offered("personality")
    accents = offered("accent")
    places = offered("location")
    if not people:
        return ""
    picks = [people[(index + step) % len(people)] for step in range(max(1, wanted))]
    said = (
        "\n\nStart from these callers, and move off them only where the scenario calls for "
        f"somebody else: {', '.join(picks)}."
    )
    if accents:
        # Spread several offered accents across this writer's callers rather than naming just one,
        # so the suite does not collapse to a single default accent and the agent's speech handling
        # is genuinely varied.
        spread = [
            accents[(index + step) % len(accents)]
            for step in range(min(len(accents), max(2, wanted)))
        ]
        said += (
            " Give your callers varied accents from the offered set, a different one per caller "
            f"where it fits rather than defaulting everyone to the same accent: {', '.join(spread)}. "
            "A suite where every caller sounds the same is a missed test of the agent's speech "
            "handling, so do not default them all to one accent unless a scenario truly requires it."
        )
    if places:
        # Dealt for the same reason accents are. Left to instruction it collapsed the same way:
        # measured across a suite, two locations for forty-one callers, where the platform offered
        # five. Where a caller is changes what they ask for and which market rules apply, so it is
        # a test dimension rather than decoration.
        here = [
            places[(index + step) % len(places)]
            for step in range(min(len(places), max(2, wanted)))
        ]
        said += (
            " Place them in different locations from the offered set rather than all in one, "
            f"choosing what the scenario supports: {', '.join(here)}."
        )
    styles = offered("communication_style")
    if styles:
        # Dealt for the reason personality and accent are, and it was the one field left out:
        # undealt across a suite of two hundred it collapsed to a single value on 158 of them,
        # where the platform offered ten. How someone says a thing decides whether the agent has
        # to work to understand them, so it is a test dimension and not a label.
        ways = [
            styles[(index + step) % len(styles)]
            for step in range(min(len(styles), max(2, wanted)))
        ]
        said += (
            " Vary how they speak as well as who they are, a different one per caller where the "
            f"scenario supports it: {', '.join(ways)}."
        )
    said += (
        " A caller who is cooperative, articulate and patient is the one an agent handles best, "
        "so a suite made only of those reports a pass it has not earned. Across your callers, "
        "spread in the ones that are harder to serve: somebody impatient who pushes before you "
        "have finished, somebody anxious who needs reassurance first, somebody sceptical who "
        "will not accept the first answer, somebody who volunteers three facts at once and "
        "somebody who answers a near-miss of the question. Let the situation choose which, and "
        "never soften a caller because it makes the scenario easier to prove."
    )
    return said


def brief_for(
    contract: AgentContract, mine: Slice, siblings: list[Slice], callers: str
) -> str:
    """What one writer is told: its coordinates, what everyone else holds, and the bar.

    Coordinates rather than a theme. A writer told "cover cancellations" and a writer told
    "cover refunds" will both write the ordinary path and one refusal, because that is what
    anyone writes when asked for a theme. A writer told which cell, with which condition moved
    off baseline, has nothing left to converge on.
    """
    others = "\n".join(f"  - {one.named()}" for one in siblings if one is not mine)

    if mine.picks:
        aim = "\n".join(
            f"    {index + 1}. {pick.name}\n"
            f"       cover: {pick.cell.described()}\n"
            f"       because: {pick.why}"
            for index, pick in enumerate(mine.picks)
        )
        heading = (
            f"Write {len(mine.picks)} scenario"
            f"{'s' if len(mine.picks) != 1 else ''} for {contract.agent!r}, one per coordinate "
            "below. Name each one exactly as its coordinate is named here, so the coverage "
            "report can find it."
        )
    else:
        aim = f"    {mine.use_case}" + (f"\n    Angle: {mine.angle}" if mine.angle else "")
        heading = (
            f"Write {mine.count} scenario{'s' if mine.count != 1 else ''} for "
            f"{contract.agent!r}, all of them within this one slice:"
        )

    return (
        f"{heading}\n\n{aim}\n\n"
        + (
            "The rest of the suite is being written at the same time by others, covering:\n"
            f"{others}\n\nStay out of theirs. A scenario that strays is either a duplicate of "
            "somebody else's or a gap in yours.\n\n"
            if others
            else ""
        )
        + _solution_shape(contract, mine)
        + "Every scenario carries the use case from the contract that its coordinate belongs "
        "to, word for word, because results are grouped on that string. Its `branch` says what "
        "makes it different from its siblings.\n\n"
        "The condition after the double underscore is the one thing moved off ordinary, and it "
        "is what the scenario is graded on. Hold everything else ordinary, or a failure cannot "
        "be attributed to anything.\n\n"
        "Set `varies` only to withhold: leave it empty when the scenario would still be the "
        "same test asked by a different sort of person, and name the axes it survives when it "
        "would not. A scenario about an accent says nothing under a different accent.\n\n"
        "What each one has to be, before you submit it:\n"
        "  - every value real, read out of the world with inspect_world, never invented\n"
        "  - an instruction that is a circumstance the person is living through, not a script "
        "of lines to say\n"
        "  - a setup that makes true whatever the instruction presumes, and a ready check that "
        "proves it\n"
        "  - a solution worked out with try_calls first, so the gates are not where you find "
        "out it cannot be passed\n"
        "  - sub-goals named from the shared catalogue, and checks that assert the right call "
        "with the right arguments or the right end state, never that something merely happened\n"
        "  - a scenario a competent agent could plausibly fail. If any correct implementation "
        "passes it for free, it teaches nothing and is not worth the run\n\n"
        "Look at the world first, and read the sub-goals already defined. Submit each scenario "
        "with submit_scenario and then stop: do not save, and do not ask what to do next. "
        "Whoever asked for this collects the suite and writes it." + callers
    )


def _solution_shape(contract: AgentContract, mine: Slice) -> str:
    """What the solution for these cells should be built out of, and what it should not.

    The agent's own rules are the reason a suite goes monotonous. They are written for the agent
    at large ("book only after an explicit read-back"), and handed to a writer for every cell
    they read as a demand that each scenario perform the whole flow. Asking for the shortest path
    while supplying those rules unscoped is a contradiction, and the writer resolves it in favour
    of the rules, which is the right call on the information it has.

    So the rules are scoped here: the ones bearing on this cell's own tools are quoted, and the
    rest are left out of the brief rather than argued with.
    """
    serving = sorted({name for pick in mine.picks for name in pick.cell.tools})
    if not serving:
        return ""

    bearing = [
        rule
        for rule in contract.hard_constraints
        if any(name in rule for name in serving)
    ]
    said = (
        "**Build each solution out of the tools that serve its own cell.** These are yours:\n"
        f"  {', '.join(serving)}\n\n"
        "Any other state the scenario needs is `setup_code`, not solution steps. An agent has one "
        "long flow it is built around, and replaying that flow to arrive at a cell which is not "
        "about it tests the flow once more and the cell not at all.\n\n"
    )
    if bearing:
        said += (
            "The agent's rules that bear on these tools, and only these:\n  - "
            + "\n  - ".join(one.strip() for one in bearing)
            + "\n\nIts other rules govern parts of the agent your cells do not reach. They are "
            "not a requirement that your scenario perform the whole flow.\n\n"
        )
    return said


async def _write_slice(
    contract: AgentContract,
    mine: Slice,
    siblings: list[Slice],
    *,
    index: int,
    destination: Path,
    on_event: Callable[..., Any] | None,
    ask: Callable[..., Any] | None,
) -> list[Scenario]:
    """One slice, written by its own session. Returns what it proved, unsaved."""
    server, kept = scenario_tools(
        contract,
        destination,
        destination,
        wanted=mine.count,
        can_save=False,
        start_from=[],
    )
    logger.info("slice starting: %s (wants %s)", mine.named(), mine.count)
    seen = 0

    def watch(event: Any) -> None:
        # Report as they land rather than at the end. A slice that proves its first scenario
        # four minutes in is the difference between a run that looks alive and one that does not.
        nonlocal seen
        if len(kept) != seen:
            seen = len(kept)
            logger.info("slice %s proved %s of %s", mine.named(), seen, mine.count)
        if on_event:
            on_event(event)

    # A slice writer never saves the suite; withholding the tool structurally means no backend
    # has to be told to deny it.
    sliced = SessionSpec(
        # The agent and its world come first, the method second. Grounding evidence read
        # before the instructions that operate on it is followed more closely than the
        # same evidence buried between the instructions and the task.
        system_prompt=(
            f"## This agent\n\n{contract.brief(with_data=True)}"
            f"\n\n## Its world\n\n{world_summary(destination)}"
            f"\n\n{load_skill(SKILL)}"
            f"\n\n## Your slice\n\nYou are writing only: {mine.named()}"
        ),
        servers={
            SCENARIO_SERVER: ToolServer(
                name=server.name,
                version=server.version,
                tools=[spec for spec in server.tools if spec.name != "save_scenarios"],
            )
        },
        cwd=_working_dir(destination),
        max_turns=turns_for(mine.count),
        model=chosen_model(),
        ask=ask,
        # The same switch the parent reads, not an unconditional yes. Only the Claude backend
        # acts on this, and thinking left on there is the configuration that stalled a run at
        # zero CPU on a read that never returned, so a run that turned thinking off must get a
        # writer that has it off too.
        thinking=scenario_thinking(),
    )
    stage = Stage(sliced, name=f"{SKILL}:{mine.named()[:40]}")
    try:
        async with stage:
            await stage.say(
                brief_for(contract, mine, siblings, callers_for(index, mine.count)),
                on_event=watch,
            )
    except Exception as broke:  # noqa: BLE001 - one slice failing must not lose the others
        logger.warning("slice %s failed after %s: %s", mine.named(), len(kept), broke)
        if on_event:
            on_event({"type": "slice_failed", "slice": mine.named(), "why": str(broke)[:300]})
        return list(kept)
    logger.info("slice %s finished with %s of %s", mine.named(), len(kept), mine.count)
    return list(kept)


def merged(written: list[list[Scenario]]) -> list[Scenario]:
    """One suite out of several writers, with folder-name collisions renamed rather than dropped.

    Asking for twenty scenarios has to return twenty. Two scenarios may legitimately share a use
    case and a branch and still test different things, so sharing them is not a reason to discard
    one; an earlier version dropped those and quietly returned eighteen.

    The one collision that cannot be tolerated is the folder name, because the folder is where a
    scenario lives on disk and the loser would overwrite the winner. Those are given a numbered
    suffix instead of being thrown away, so nothing generated is ever lost.
    """
    suite: list[Scenario] = []
    taken: set[str] = set()
    for batch in written:
        for one in batch:
            if one.name in taken:
                stem, suffix = one.name, 2
                while f"{stem}-{suffix}" in taken:
                    suffix += 1
                one = one.model_copy(update={"name": f"{stem}-{suffix}", "scenario_key": ""})
                logger.info("renamed a duplicate folder name to %s", one.name)
            taken.add(one.name)
            suite.append(one)
    return suite


def _suite_summary(suite: list[Scenario]) -> str:
    """The whole suite as a reviewer needs to see it: what each row claims to test."""
    return "\n".join(
        f"  {one.name} | use case: {one.use_case} | branch: {one.branch} | passes when: {one.tests}"
        for one in suite
    )


async def gaps_in(
    contract: AgentContract,
    suite: list[Scenario],
    *,
    destination: Path,
    wanted: int,
    ask: Callable[..., Any] | None = None,
) -> list[Slice]:
    """What the finished suite is missing, as slices that would fill it.

    Nobody looks at a suite written in parallel. Each writer sees its own slice and the merge
    only removes collisions, so a use case that came back one short, or an obvious branch that
    every writer assumed somebody else had, survives to the end and nobody notices. This is the
    one pass that reads the suite as a whole.
    """
    if not suite:
        return []
    found: list[Slice] = []

    @tool(
        "submit_gaps",
        "The gaps worth filling in this suite, as the slices that would fill them. Return "
        "nothing when the suite covers what it should: a suite that is finished is a real "
        "answer, and inventing work to report is worse than saying so.",
        schema(
            {
                "gaps": {
                    "type": "array",
                    "description": "One entry per gap. Empty when the suite is covering what "
                    "it should.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "use_case": {"type": "string"},
                            "angle": {
                                "type": "string",
                                "description": "The scenario that is missing, in one line.",
                            },
                            "why": {"type": "string"},
                        },
                        "required": ["use_case", "angle"],
                    },
                }
            },
            ["gaps"],
        ),
    )
    async def submit_gaps(args: dict[str, Any]) -> dict[str, Any]:
        for one in args.get("gaps") or []:
            if not isinstance(one, dict):
                continue
            case = str(one.get("use_case") or "").strip()
            if case:
                found.append(
                    Slice(
                        use_case=case,
                        angle=str(one.get("angle") or "").strip(),
                        asked=1,
                        why=str(one.get("why") or "").strip(),
                    )
                )
        return {
            "content": [
                {"type": "text", "text": f"{len(found)} gap(s) recorded. Nothing else to do."}
            ]
        }

    server = tool_server(name=REVIEW_SERVER, version="0.1.0", tools=[submit_gaps])
    review = SessionSpec(
        system_prompt=(
            "You are reviewing a suite of tests somebody else wrote for an AI agent, in "
            "parallel, each writer blind to the others. Your only job is to say what is "
            "missing.\n\n"
            "Look for: a use case of this agent that nothing covers; a use case covered only "
            "on its ordinary path, where the branch that cannot be completed or the rule under "
            "pressure is the interesting one; two rows that are the same test under different "
            "names, leaving the branch one of them claimed uncovered.\n\n"
            "Judge coverage of the agent, not of the plan. Do not ask for more of what is "
            "already well covered, and do not report a gap you cannot name a scenario for. "
            "A suite of the right size that covers what matters is finished, and saying so is "
            f"the useful answer.\n\n## This agent\n\n{contract.brief()}"
        ),
        servers={REVIEW_SERVER: server},
        cwd=_working_dir(destination),
        max_turns=8,
        model=chosen_model(),
        ask=ask,
    )
    stage = Stage(review, name=f"{SKILL}:review")
    try:
        async with stage:
            await stage.say(
                f"This suite has {len(suite)} scenarios against a target of {wanted}:\n\n"
                f"{_suite_summary(suite)}\n\n"
                "Say what it is missing, then submit_gaps. Submit an empty list if it is "
                "covering what it should."
            )
    except Exception:  # noqa: BLE001 - a review that fails leaves the suite as written
        return []
    return found


async def write_in_parallel(
    contract: AgentContract,
    *,
    out: Path | None = None,
    wanted: int = 10,
    use_cases: list[str] | None = None,
    slices: list[dict] | None = None,
    at_once: int = AT_ONCE,
    rounds: int = TOP_UP_ROUNDS,
    on_event: Callable[..., Any] | None = None,
    ask: Callable[..., Any] | None = None,
) -> list[Scenario]:
    """Write a suite with one session per slice, review it, fill what it missed, and save once.

    Sequentially, a suite costs roughly three turns a scenario against one budget, which is why
    asking for forty stopped around twenty-five. Here the work is split into slices that run at
    the same time, so the wall clock is the slowest slice rather than the sum of all of them.

    Saving stays here, once, for a reason: ``save_scenarios`` regenerates the index and deletes
    any folder it does not know about, so letting the writers save would have each of them
    remove the others' work.
    """
    destination = out or artifact_dir(contract.agent)
    at_once = max(1, min(at_once or AT_ONCE, MOST_AT_ONCE))

    # The grid decides what gets written. A caller-supplied split is still honoured, because
    # whoever is talking to the person may know something the contract does not say, but the
    # ordinary path is now coordinates rather than a list of use cases.
    if slices:
        cases = [case for case in (use_cases or contract.real_use_cases) if case.strip()]
        allocation = planned(wanted, cases, slices)
    else:
        axes = axes_for(contract.modality)
        state = Coverage(contract, axes)
        picks = plan_picks(state.grid, axes, wanted)
        if not picks:
            # Nothing to partition on at all. One writer, rather than no scenarios.
            return await write(contract, out=destination, wanted=wanted, on_event=on_event, ask=ask)
        allocation = slices_for(picks, at_once)
        logger.info(
            "planned %s scenarios over %s of %s cells\n%s",
            len(picks),
            len({pick.cell.name for pick in picks}),
            len(state.grid.cells),
            coverage(state.grid, axes, picks),
        )
    logger.info(
        "writing %s scenarios across %s slices, %s at a time: %s",
        wanted,
        len(allocation),
        at_once,
        ", ".join(f"{one.named()} x{one.count}" for one in allocation),
    )
    if on_event:
        on_event(
            {
                "type": "planned",
                "slices": [(one.named(), one.count) for one in allocation],
                "at_once": at_once,
            }
        )

    limit = asyncio.Semaphore(at_once)

    async def guarded(mine: Slice, siblings: list[Slice], index: int) -> list[Scenario]:
        async with limit:
            return await _write_slice(
                contract,
                mine,
                siblings,
                index=index,
                destination=destination,
                on_event=on_event,
                ask=ask,
            )

    written = await asyncio.gather(
        *(guarded(one, allocation, index) for index, one in enumerate(allocation)),
        return_exceptions=False,
    )
    # A journal left behind means an earlier run was killed before it could save. Its scenarios
    # were proved against this same world, so they are folded back in rather than rewritten.
    # Matched by name, because this run journals its own writers too: comparing against `written`
    # itself would let every scenario in twice, and `merged` renames collisions rather than
    # dropping them, so the duplicates would survive as -2 folders.
    already = {one.name for one in load_scenarios(destination)}
    already |= {one.name for batch in written for one in batch}
    recovered = [one for one in journalled(destination) if one.name not in already]
    if recovered:
        logger.info("recovered %s scenarios from a run that did not save", len(recovered))
    suite = merged([load_scenarios(destination), recovered, *written])

    # Read the whole thing and fill what nobody covered. Bounded, because a reviewer asked
    # twice will always find something smaller to say.
    for _ in range(max(0, rounds)):
        if len(suite) >= wanted:
            break
        missing = await gaps_in(
            contract, suite, destination=destination, wanted=wanted, ask=ask
        )
        missing = missing[: max(0, wanted - len(suite))]
        if not missing:
            break
        if on_event:
            on_event({"type": "topping_up", "slices": [one.named() for one in missing]})
        logger.info(
            "topping up %s of %s with %s more slices: %s",
            len(suite),
            wanted,
            len(missing),
            ", ".join(f"{one.named()} x{one.count}" for one in missing),
        )
        more = await asyncio.gather(
            *(
                guarded(one, missing, len(allocation) + index)
                for index, one in enumerate(missing)
            ),
            return_exceptions=False,
        )
        before = len(suite)
        suite = merged([suite, *more])
        allocation = [*allocation, *missing]
        if len(suite) == before:
            break

    write_scenarios(suite, destination, load_catalogue(destination))
    # The folders are the truth once they exist, so the journal has done its job and would only
    # be a stale second copy for the next run to recover from.
    forget_journal(destination)
    logger.info("suite saved: %s of %s asked for", len(suite), wanted)
    if on_event:
        on_event({"type": "saved", "kept": len(suite), "asked": wanted})
    return load(destination)


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
        await stage.say(opening(contract, wanted), on_event=on_event)
        for follow_up in follow_ups or []:
            await stage.say(follow_up, on_event=on_event)
    return load(destination)
