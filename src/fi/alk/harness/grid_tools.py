"""Tools that let the stage see the grid, correct it, and act on a suite that already exists.

The grid is derived from the contract by reading tool names and a data schema. That is a good
starting point and it is not the truth: the contract is a summary, and the model has the agent's
own source. So the derivation is shown rather than assumed, and can be corrected.

The rest of these exist because a suite is not written once. Somebody looks at what came back and
says "add twenty more adversarial ones", "drop the weak ones", "make these harder". That is a
conversation about a suite that already exists, so the tools have to operate on the saved suite
rather than only on what this session happened to write.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .axes import AxisSet, axes_for
from .backends import ToolServer, tool, tool_server
from .contract import AgentContract
from .expand import expand_all, summarise
from .grid import Grid, derive
from .sample import coverage, plan
from .scenario import Scenario
from .scenario_tools import load_scenarios, write_scenarios
from .tools import schema

logger = logging.getLogger(__name__)

GRID_SERVER = "grid"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


class Coverage:
    """The grid this stage is working against, and the corrections made to it.

    Held rather than recomputed so a correction sticks for the rest of the session: the model
    reads the agent's source, finds that the contract missed an object, says so, and everything
    afterwards is planned against the corrected grid.
    """

    def __init__(self, contract: AgentContract, axes: AxisSet | None = None) -> None:
        self.contract = contract
        self.axes = axes or axes_for(contract.modality)
        self.grid: Grid = derive(contract, self.axes)
        self.corrections: list[str] = []

    def rebuild(self, objects: list[str]) -> None:
        """Re-derive against an object list the model has corrected."""
        self.grid = derive(self.contract, self.axes, objects=tuple(objects))


def grid_tools(
    contract: AgentContract,
    destination: Path,
    *,
    wanted: int = 0,
    held: Coverage | None = None,
) -> tuple[ToolServer, Coverage]:
    """The grid and suite tools, and the coverage object they share."""
    state = held or Coverage(contract)
    destination = Path(destination)

    @tool(
        "show_grid",
        "The space of everything this agent can be asked, derived from its contract: its "
        "objects crossed with the twelve operations, minus the cells it has no way to serve. "
        "Read this before planning a suite. It is derived from tool names and a data schema, "
        "so it is a starting point rather than the truth, and you have the agent's source.",
        schema({}, []),
    )
    async def show_grid(_args: dict[str, Any]) -> dict[str, Any]:
        said = state.grid.report()
        if state.corrections:
            said += "\n\nCorrections made this session:\n  - " + "\n  - ".join(state.corrections)
        return _ok(said)

    @tool(
        "set_objects",
        "Correct the objects the grid is built from, after reading the agent's source. Use it "
        "when the derivation missed something the agent plainly acts on, split one thing into "
        "two, or invented a name out of a tool that describes an action rather than a thing. "
        "The whole grid is rebuilt, so say the complete list, not only what changed.",
        schema(
            {
                "objects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Every noun this agent acts on, lower case with underscores.",
                },
                "why": {"type": "string", "description": "What the derivation got wrong."},
            },
            ["objects"],
        ),
    )
    async def set_objects(args: dict[str, Any]) -> dict[str, Any]:
        objects = [str(one).strip().lower() for one in args.get("objects") or [] if str(one).strip()]
        if not objects:
            return _err("An empty object list would leave nothing to write scenarios about.")
        before = len(state.grid.cells)
        state.rebuild(objects)
        why = str(args.get("why") or "").strip()
        state.corrections.append(f"objects set to {', '.join(objects)}" + (f" ({why})" if why else ""))
        return _ok(
            f"Grid rebuilt from {len(objects)} objects: {before} cells before, "
            f"{len(state.grid.cells)} now.\n\n{state.grid.report()}"
        )

    @tool(
        "plan_suite",
        "How many scenarios to write and which cell each one covers, for a given count. The "
        "plan puts the cases a suite is not worth running without first, then forces in every "
        "safety overlay, then fills the grid evenly. Use it to brief writers.",
        schema(
            {
                "count": {
                    "type": "integer",
                    "description": "How many scenarios. Any number; the plan escalates through "
                    "dial pairs and further branches of a cell rather than falling short.",
                }
            },
            ["count"],
        ),
    )
    async def plan_suite(args: dict[str, Any]) -> dict[str, Any]:
        try:
            count = int(args.get("count") or 0)
        except (TypeError, ValueError):
            return _err("count has to be a whole number.")
        if count <= 0:
            return _err("Ask for at least one scenario.")
        picks = plan(state.grid, state.axes, count)
        lines = [f"{len(picks)} scenarios planned:"]
        lines += [f"  {pick.name}  ({pick.described()})  because: {pick.why}" for pick in picks]
        lines.append("")
        direct = [one for one in picks if not one.cell.after]
        gated = [one for one in picks if one.cell.after]
        lines.append(
            "Build each solution out of the tools listed against its own cell. Anything else the "
            "scenario needs is setup_code, not solution steps."
        )
        if direct:
            lines.append(
                f"\n**{len(direct)} of these cells are reachable directly.** Their tools have no "
                "precondition, so their solutions start where the scenario starts. Replaying the "
                "agent's main flow to arrive at one of them tests the flow once more and the cell "
                "not at all:\n  " + "\n  ".join(one.name for one in direct)
            )
        if gated:
            lines.append(
                f"\n**{len(gated)} need earlier calls first**, and only these. What each one is "
                "reachable after is listed against it above; those steps are unavoidable and "
                "belong in the solution."
            )
        if not any(one.cell.after for one in picks) and not any(
            tool.requires for tool in contract.tools
        ):
            lines.append(
                "\nNo tool on this contract records a precondition. That may be true, or it may "
                "be that nobody wrote them down. If you find in the source that a tool refuses "
                "until something else has happened, say so rather than assuming every scenario "
                "must replay the whole flow to be safe."
            )
        lines.append("")
        lines.append(coverage(state.grid, state.axes, picks))
        return _ok("\n".join(lines))

    @tool(
        "list_scenarios",
        "Every scenario saved for this agent, one line each: what it covers and what it passes "
        "on. Read this before changing a suite that already exists.",
        schema({}, []),
    )
    async def list_scenarios(_args: dict[str, Any]) -> dict[str, Any]:
        kept = load_scenarios(destination)
        if not kept:
            return _ok("No scenarios are saved for this agent yet.")
        lines = [f"{len(kept)} saved:"]
        for one in kept:
            lines.append(
                f"  {one.name} | use case: {one.use_case} | branch: {one.branch} "
                f"| passes when: {one.tests}"
            )
        return _ok("\n".join(lines))

    @tool(
        "show_coverage",
        "What the saved suite covers against the grid, and what it leaves untouched. This is "
        "the answer to 'what did we not test', which a count cannot give.",
        schema({}, []),
    )
    async def show_coverage(_args: dict[str, Any]) -> dict[str, Any]:
        kept = load_scenarios(destination)
        if not kept:
            return _ok("Nothing is saved, so nothing is covered.")
        return _ok(_covered(state, kept))

    @tool(
        "expand_suite",
        "Copy every proved scenario across the caller conditions that do not change its world, "
        "then save. Each copy reuses the setup, the checks and the reference solution unchanged, "
        "so it needs no proving and costs no model call. A scenario limits this by naming axes "
        "in `varies` when its own point would be lost under a different caller.",
        schema(
            {
                "total": {
                    "type": "integer",
                    "description": "Stop at this many scenarios in total, originals included. "
                    "Left out, every scenario is expanded across every free axis.",
                }
            },
            [],
        ),
    )
    async def expand_suite(args: dict[str, Any]) -> dict[str, Any]:
        kept = load_scenarios(destination)
        if not kept:
            return _err("There is nothing saved to expand. Write and save scenarios first.")
        try:
            total = int(args.get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        grown = expand_all(kept, state.axes, wanted=total)
        if len(grown) <= len(kept):
            return _ok(
                "Nothing was copied. Either every scenario withholds the free axes in `varies`, "
                "or no axis of this agent leaves the world alone."
            )
        write_scenarios(grown, destination)
        return _ok(summarise(len(kept), grown, state.axes) + "\n\n" + _covered(state, grown))

    server = tool_server(
        name=GRID_SERVER,
        version="0.1.0",
        tools=[show_grid, set_objects, plan_suite, list_scenarios, show_coverage, expand_suite],
    )
    return server, state


def _covered(state: Coverage, kept: list[Scenario]) -> str:
    """The saved suite read back onto the grid it was planned from.

    Scenario names carry their cell, so coverage is recoverable from the suite on disk rather
    than from anything this session happens to remember. A suite written last week reports the
    same way as one written a minute ago.
    """
    cells = {cell.name for cell in state.grid.cells}
    seen: set[str] = set()
    dials: dict[str, set[str]] = {}
    unplaced: list[str] = []
    for one in kept:
        stem, _, rest = one.name.partition("__")
        if stem in cells:
            seen.add(stem)
        else:
            unplaced.append(one.name)
        for axis in state.axes.axes:
            for setting in axis.settings:
                if setting.name and setting.name in rest:
                    dials.setdefault(axis.name, set()).add(setting.name)

    lines = [f"{len(kept)} scenarios covering {len(seen)} of {len(cells)} cells."]
    for axis in state.axes.axes:
        used = dials.get(axis.name, set())
        every = {one.name for one in axis.settings}
        missing = sorted(every - used)
        lines.append(
            f"  {axis.name}: {len(used)}/{len(every)}"
            + (f", not covered: {', '.join(missing)}" if missing else "")
        )
    empty = sorted(cells - seen)
    if empty:
        lines.append(f"  cells with nothing on them ({len(empty)}): {', '.join(empty[:15])}"
                     + (" ..." if len(empty) > 15 else ""))
    if unplaced:
        lines.append(
            f"  {len(unplaced)} scenario(s) whose name does not match a cell, so they count "
            f"toward no coverage: {', '.join(unplaced[:8])}"
        )
    return "\n".join(lines)


def tool_names() -> tuple[str, ...]:
    return ("show_grid", "set_objects", "plan_suite", "list_scenarios", "show_coverage", "expand_suite")
