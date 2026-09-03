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

from ..plan.axes import axes_for
from ...backends import SessionSpec, ToolServer, WorkerSpec, resolve, tool, tool_server
from ...backends.base import MOST_WORKERS_AT_ONCE

from ...config import (
    artifact_dir,
    chosen_model,
    compose_skills,
    load_skill,
    skill_overlay,
    scenario_thinking,
    stage_backend,
    stage_model,
    writer_effort,
)
from ..plan.canvas import SLICE_SCENARIOS, WORTH_PLANNING
from ..plan.canvas import load as load_canvas
from ..plan.tools import GRID_SERVER, Coverage, grid_tools
from ...sample import Pick, coverage, plan as plan_picks
from ..model.catalogue import load_catalogue
from ...contract import AgentContract
from ..model.scenario import Scenario
from .delegation import (
    AT_ONCE,
    FEWEST_WORTH_DELEGATING,
    MOST_AT_ONCE,
    TOP_UP_ROUNDS,
    brief_for,
    callers_for,
    gaps_in,
    merged,
    planned,
    turns_for,
    write_in_parallel,
    writer_workers,
    _working_dir,
)
from .tools import (
    SCENARIO_SERVER,
    parallel_suites,
    scenario_tools,
    world_summary,
)
from ..store.suite import (
    forget_journal,
    journalled,
    load_scenarios,
    write_scenarios,
)
from ...session import Stage
from ...tools import schema
from . import PARENT_SKILL, PLAN_SKILL, SKILL

logger = logging.getLogger(__name__)




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

# The review pass runs its own tool server, kept apart from the writers' one so a reviewer can
# only report gaps and never submit or save a scenario itself.
REVIEW_SERVER = "suite-review"

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
            + ("" if planning else skill_overlay(f"kinds/{contract.modality}"))
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
