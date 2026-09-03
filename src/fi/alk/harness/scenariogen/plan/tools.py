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
from .canvas import EXPECTS, OVERLAYS, SLICE_SCENARIOS, Angle, Canvas, StateAxis, Theme
from .canvas import load as load_canvas
from ...backends import ToolServer, tool, tool_server
from ...backends.base import MOST_WORKERS_AT_ONCE
from ...contract import AgentContract
from ..quality.diversity import measure
from ..quality.expand import expand_all, summarise
from .grid import Grid, derive
from ...sample import coverage, plan
from ..model.scenario import Scenario
from ...semantic import duplicates as semantic_duplicates
from ..store.suite import journalled, load_scenarios, write_scenarios
from ...tools import schema

logger = logging.getLogger(__name__)

GRID_SERVER = "grid"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


_LABELS: dict[Path, dict[str, str]] = {}


def entity_labels(destination: Path) -> dict[str, str]:
    """Every value in the world that names a row rather than describing its state.

    A column whose values are distinct in every row identifies those rows: an id, a name, a phone
    number, the last four digits of a card. A column whose values repeat describes a state they
    can be in. Only the second kind can be an axis, because the agent behaves the same for every
    value of the first kind.

    Returns nothing at all rather than raising: this feeds a check, and a check is never worth
    stopping a run over.
    """
    # Cached per destination for the stage's lifetime: the seed does not change while a plan is
    # being recorded, and building this reads the world, which restores it - a full truncate and
    # reload of every table. A sixteen-instalment plan was rewriting the world sixteen times to
    # answer the same question.
    if destination in _LABELS:
        return _LABELS[destination]
    try:
        from ..write.tools import world_state

        held: dict[str, str] = {}
        for collection, rows in (world_state(destination) or {}).items():
            if len(rows) < 3:
                continue
            for column in rows[0]:
                seen = [str(row.get(column)) for row in rows]
                if len(set(seen)) == len(seen):
                    for value in seen:
                        held.setdefault(value.strip().lower(), f"{collection}.{column}")
        _LABELS[destination] = held
        return held
    except Exception as why:  # noqa: BLE001 - the check degrades, the run does not
        logger.info("no entity labels, the identity-axis check is skipped: %s", why)
        return {}


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
        self.canvas: Canvas = Canvas()

    def rebuild(self, objects: list[str]) -> None:
        """Re-derive against an object list the model has corrected."""
        self.grid = derive(self.contract, self.axes, objects=tuple(objects))


def planning_tools(
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
        "record_canvas",
        "Write down what this suite will cover, before any of it is written. Themes group the "
        "work; an angle is one thing worth testing on one grid cell, in a few words, with how "
        "many scenarios it holds and what makes them differ.\n\n"
        "An angle says what is worth testing, never how it goes. 'surge boundary confusion' "
        "is an angle. 'charged 2.3x, receipt shows the higher rate, agent explains the window "
        "closed' is the scenario with its code removed: at that length a plan for a thousand is "
        "228KB and 57k tokens to emit in one response, which cannot be done.\n\n"
        "Give each angle a `why_hard`: the structural thing under test, like `rule:surge-disclosure`, "
        "`precondition:book_ride` or `data:expired-card`. Two angles claiming one why_hard on one "
        "cell are probably one angle twice, and this is the only reliable way to notice at angle "
        "length.\n\n"
        "You own coverage and spread. Whoever writes the scenarios owns the particulars, decided "
        "with the agent's source open. Recording again replaces the plan but keeps the progress "
        "of any angle whose id you reuse.",
        schema(
            {
                "axes": {
                    "type": "array",
                    "description": "The state axes you derived from the agent's data and rules: "
                    "dimensions whose value changes what the agent should do. A level must exist "
                    "in the data or be reachable by seeding it, and must change the correct "
                    "answer. Nine riders are nine names, not nine levels.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "levels": {"type": "array", "items": {"type": "string"}},
                            "why": {"type": "string"},
                        },
                        "required": ["name", "levels"],
                    },
                },
                "themes": {
                    "type": "array",
                    "description": "Groups of angles. The unit this is read and dispatched in.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "name": {"type": "string"},
                            "why": {"type": "string"},
                        },
                        "required": ["id", "name"],
                    },
                },
                "buckets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "theme": {"type": "string"},
                            "cell": {"type": "string"},
                            "angle": {"type": "string"},
                            "why_hard": {"type": "string"},
                            "want": {"type": "integer"},
                            "expects": {
                                "type": "string",
                                "enum": list(EXPECTS),
                                "description": "What the agent should do here. Exactly one: "
                                "succeed, refuse, ask before acting, or escalate to a human.",
                            },
                            "overlay": {
                                "type": "string",
                                "enum": list(OVERLAYS),
                                "description": "What is deliberately making it hard, if "
                                "anything. Separate from what the agent should do: an injection "
                                "attempt expects a refusal and carries an injection overlay.",
                            },
                            "varies_by": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "The state axes that make this bucket's scenarios "
                                "differ from each other. `want` is how many of their combinations "
                                "genuinely need a different answer, so the count is derived.",
                            },
                        },
                        "required": ["id", "theme", "cell", "angle", "why_hard", "expects"],
                    },
                },
                "target": {"type": "integer", "description": "The size of the finished suite."},
                "replace": {
                    "type": "boolean",
                    "description": "Throw away everything recorded so far and keep only what is "
                    "in this call. Off by default: calls add to the plan, so it can be built up "
                    "a theme at a time rather than emitted in one breath.",
                },
            },
            ["buckets"],
        ),
    )
    async def record_canvas(args: dict[str, Any]) -> dict[str, Any]:
        rows = args.get("buckets") or args.get("angles") or []
        if not isinstance(rows, list) or not rows:
            return _err("Nothing to record. Pass the planned angles.")

        # Recording adds to the plan unless told otherwise. A canvas for a large suite is far
        # too much to emit in one response, and a model that tries will run long or truncate and
        # lose the lot. Building it up a theme at a time is the safe way, and it only works if a
        # later call does not silently drop the earlier ones.
        replace = bool(args.get("replace"))
        standing = {} if replace else {one.id: one for one in state.canvas.angles}
        before = dict(standing)
        held = Canvas(
            # The stage's own count wins over anything the model types. Every whole-plan check
            # is guarded on target, so a lowballed or omitted target quietly disarmed all of
            # them, and the checks exist precisely for the model that would rather not meet them.
            target=wanted or int(args.get("target") or state.canvas.target or 0),
            axes=[
                StateAxis(
                    name=str((one or {}).get("name") or "").strip(),
                    levels=[str(x) for x in ((one or {}).get("levels") or [])],
                    why=str((one or {}).get("why") or "").strip(),
                )
                for one in args.get("axes") or []
                if isinstance(one, dict)
            ],
            themes=[
                Theme(
                    id=str((one or {}).get("id") or "").strip(),
                    name=str((one or {}).get("name") or "").strip(),
                    why=str((one or {}).get("why") or "").strip(),
                )
                for one in args.get("themes") or []
                if isinstance(one, dict)
            ],
            angles=[
                Angle(
                    id=str((one or {}).get("id") or "").strip(),
                    theme=str((one or {}).get("theme") or "").strip(),
                    cell=str((one or {}).get("cell") or "").strip(),
                    angle=str((one or {}).get("angle") or "").strip(),
                    why_hard=str((one or {}).get("why_hard") or "").strip(),
                    want=max(1, int((one or {}).get("want") or 1)),
                    varies_by=[str(x) for x in ((one or {}).get("varies_by") or [])],
                    expects=str((one or {}).get("expects") or "").strip().lower(),
                    overlay=str((one or {}).get("overlay") or "").strip().lower(),
                )
                for one in rows
                if isinstance(one, dict)
            ],
        )
        # Replanning mid-run must not throw away what writers have already done.
        for one in held.angles:
            was = before.get(one.id)
            if was is not None:
                one.done, one.refused, one.attempts = was.done, was.refused, was.attempts
                one.state, one.notes = was.state, list(was.notes)
            standing[one.id] = one
        held.angles = list(standing.values())
        if not replace:
            known = {one.id for one in held.themes}
            held.themes += [one for one in state.canvas.themes if one.id not in known]
            named = {one.name for one in held.axes}
            held.axes += [one for one in state.canvas.axes if one.name not in named]

        problems = held.problems(
            {cell.name for cell in state.grid.cells},
            entity_labels(destination),
            [one.name for one in contract.tools if one.requires],
        )
        if problems:
            # Refused rather than stored: a plan is the cheapest thing here to fix, and every
            # fault left in it costs a proof and a folder once writers act on it.
            return _err(
                "Not recorded. Fix these and record it again:\n  - " + "\n  - ".join(problems)
            )

        state.canvas = held
        path = held.written_to(destination)
        said = [
            held.coverage(
                {cell.name for cell in state.grid.cells},
                list(contract.hard_constraints or []),
                [one.name for one in contract.tools if one.requires],
            ),
            f"Written to {path.name}.",
        ]
        if held.shortfall():
            said.append(
                f"{held.shortfall()} short of the {held.target} asked for. Keep planning, or if "
                "there is genuinely nothing else distinct left, say so and the run will report "
                "what it reached rather than padding to the number."
            )
        missing = sorted({cell.name for cell in state.grid.cells} - held.covered)
        if missing:
            said.append(
                f"{len(missing)} cells have nothing planned on them: "
                + ", ".join(missing[:12])
                + ("" if len(missing) <= 12 else " ...")
            )
        # Embedding-based, and only ever additional: the lexical pass below still runs, and this
        # returns nothing at all when there are no credentials.
        alike = semantic_duplicates(
            [(one.id, one.angle) for one in held.angles],
            within={one.id: one.cell for one in held.angles},
        )
        if alike:
            said.append(
                f"{len(alike)} pairs read as the same test despite sharing no wording: "
                + "; ".join(f"{one.one}/{one.two} at {one.score}" for one in alike[:6])
                + ". Worth a look; two cells may legitimately share a situation."
            )

        clashes = held.collisions()
        if clashes:
            said.append(
                f"{len(clashes)} pairs may be the same angle twice. Worth a look, not "
                "necessarily wrong: "
                + "; ".join(f"{one}/{two} ({why})" for one, two, why in clashes[:6])
            )
        return _ok("\n".join(said))

    @tool(
        "show_canvas",
        "The plan and how far it has got. Without a theme, the theme table and the totals; with "
        "one, every angle in that theme with its state. Read a theme at a time: the whole canvas "
        "does not need to be in view, and at a few thousand angles it will not fit.",
        schema({"theme": str}, []),
    )
    async def show_canvas(args: dict[str, Any]) -> dict[str, Any]:
        held = state.canvas if state.canvas.angles else load_canvas(destination)
        if not held.angles:
            return _ok("No canvas yet. Plan the suite with record_canvas first.")
        state.canvas = held
        theme = str(args.get("theme") or "").strip()
        if theme:
            mine = held.of_theme(theme)
            if not mine:
                return _err(f"no theme called {theme!r}")
            return _ok("\n".join([f"{theme}:"] + [f"  {one.line()}" for one in mine]))

        owed = held.debt()
        lines = [
            f"{held.written} written of {held.planned} planned, as {len(held.angles)} angles "
            f"in {len(held.themes)} themes."
        ]
        for one in held.themes:
            mine = held.of_theme(one.id)
            asked = sum(max(1, angle.want) for angle in mine)
            got = sum(angle.done for angle in mine)
            stuck = sum(1 for angle in mine if angle.state == "blocked")
            lines.append(
                f"  {one.id} {one.name}: {got}/{asked} written, {len(mine)} angles"
                + (f", {stuck} blocked" if stuck else "")
                + f", {int(owed.get(one.id, 0) * 100)}% outstanding"
            )
        lines.append("")
        lines.append(
            held.coverage(
                {cell.name for cell in state.grid.cells},
                list(contract.hard_constraints or []),
                [one.name for one in contract.tools if one.requires],
            )
        )
        if held.reached():
            lines.append("")
            lines.append(held.reached())
        return _ok("\n".join(lines))

    @tool(
        "claim_slice",
        "The angles to give the next writer, marked as claimed so nothing is written twice. "
        "Ranked by what is outstanding weighted by how much of its theme is untouched, so a "
        "theme nobody has started outranks one nearly finished. Never two angles from one cell: "
        "a writer handed a whole cell has to invent that cell's whole variety alone.\n\n"
        "Dispatch one writer per slice and fold its return in with fold_return before claiming "
        "again.",
        schema(
            {
                "scenarios": {
                    "type": "integer",
                    "description": "Roughly how many scenarios to put in front of one writer.",
                },
                "writer": {"type": "string", "description": "A name for the writer taking it."},
            },
            [],
        ),
    )
    async def claim_slice(args: dict[str, Any]) -> dict[str, Any]:
        held = state.canvas if state.canvas.angles else load_canvas(destination)
        if not held.angles:
            return _err("No canvas to deal. Plan the suite with record_canvas first.")
        state.canvas = held
        # The ceiling on writers running at once is enforced here rather than trusted to the
        # brief: a slice is only ever handed out by this tool, so refusing one is the only place
        # the limit can actually hold. Counted from the canvas, so a writer that returned or died
        # frees its place without any bookkeeping of our own.
        working = {
            str(one.claimed_by)
            for one in held.angles
            if one.state == "claimed" and one.claimed_by
        }
        if len(working) >= MOST_WORKERS_AT_ONCE:
            # Says nothing about the ceiling's value: the brief names ten as the most, and a
            # refusal quoting a different number would read as the instruction being wrong.
            return _err(
                "Every writer is already holding a slice. Wait for one to report and "
                "fold_return it before claiming again."
            )
        # Clamped to twice the recommended size, because a writer's turn budget is finite and a
        # thirty-scenario slice comes back part-filled, burning an attempt on every bucket in it.
        asked = int(args.get("scenarios") or SLICE_SCENARIOS)
        taken = held.next_slice(max(1, min(asked, SLICE_SCENARIOS * 2)))
        if not taken:
            done = held.reached()
            return _ok(
                "Nothing is open. " + (done or f"{held.written} scenarios written as planned.")
            )
        held.claim(taken, str(args.get("writer") or "writer"))
        held.written_to(destination)
        due = sum(one.outstanding for one in taken)
        lines = [
            f"{len(taken)} angles, {due} scenarios, cells: "
            + ", ".join(sorted({one.cell for one in taken})),
            "",
        ]
        lines += [f"  {one.line()}" for one in taken]
        lines.append("")
        lines.append(
            "Brief one writer on exactly these, and give it the callers: a name, an accent and "
            "a location per scenario, distinct across the whole suite. Ask it to report, for "
            "each bucket, the names of the scenarios it wrote: that is what fold_return checks "
            "against the disk.\n\nName the cells above in the brief, verbatim. A writer has no "
            "grid tools and cannot look a cell up, so one left to guess coins a name that is on "
            "no grid, and coverage is read back from those names: such a scenario counts towards "
            "nothing, however good it is."
        )
        return _ok("\n".join(lines))

    @tool(
        "fold_return",
        "Take back what a writer covered, and reopen what it did not. Pass one entry per angle "
        "it was given, with its own count and one sentence on what it actually covered.\n\n"
        "The count is not trusted: what counts as written is read off disk. A stage once "
        "finished a run having saved one scenario of fifty and called it a success, so a "
        "writer's own number is kept only to notice when it disagrees with what is there.",
        schema(
            {
                "returns": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "angle_id": {"type": "string"},
                            "wrote": {"type": "integer"},
                            "names": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "The scenarios the writer says it wrote for this "
                                "bucket. Each is checked against what is on disk; only the ones "
                                "that are really there are counted.",
                            },
                            "short": {
                                "type": "string",
                                "description": "One sentence on what was covered.",
                            },
                            "blocked_reason": {
                                "type": "string",
                                "description": "Only if nothing more can be written here.",
                            },
                        },
                        "required": ["angle_id"],
                    },
                },
                "found": {
                    "type": "array",
                    "description": "Buckets the writer found that nobody planned. The plan was "
                    "written from outside the code; a writer works inside one bucket with the "
                    "source open and finds cases the planner could not have seen.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "theme": {"type": "string"},
                            "cell": {"type": "string"},
                            "angle": {"type": "string"},
                            "why_hard": {"type": "string"},
                            "want": {"type": "integer"},
                        },
                        "required": ["cell", "angle"],
                    },
                },
            },
            ["returns"],
        ),
    )
    async def fold_return(args: dict[str, Any]) -> dict[str, Any]:
        held = state.canvas if state.canvas.angles else load_canvas(destination)
        if not held.angles:
            return _err("No canvas to fold into.")
        state.canvas = held
        # Folders *and* journal. A delegated writer cannot write folders - saving would delete its
        # siblings' work - so it journals each scenario as it proves it, and checking folders alone
        # found nothing, credited nothing, and blocked buckets whose scenarios existed all along.
        saved = load_scenarios(destination)
        known = {one.name for one in saved}
        saved += [one for one in journalled(destination) if one.name not in known]
        lines: list[str] = []
        for row in args.get("returns") or []:
            if not isinstance(row, dict):
                continue
            angle_id = str(row.get("angle_id") or "").strip()
            one = held.named(angle_id)
            if one is None:
                lines.append(f"  {angle_id}: no such angle")
                continue
            # Verified against disk rather than believed, and rather than relying on a naming
            # convention nobody enforces: the writer says which scenarios it wrote, and each is
            # counted only if a scenario of that name is really there. Matching on the bucket id
            # inside a free-text field was the earlier approach and would have matched nothing,
            # leaving every bucket looking unfilled while its scenarios sat on disk.
            claimed_names = [str(one) for one in (row.get("names") or [])]
            really = {s.name for s in saved}
            found = [one for one in claimed_names if one in really]
            if not found:
                # The writer names its own scenarios and the report passes through another model,
                # so the names can come back approximate or invented. A bucket's cell is the one
                # link a scenario name always carries, so fall back to it and credit what has not
                # been credited already. Reported-but-absent names are still called out below:
                # this recovers the work, it does not hide the disagreement.
                taken = {name for a in held.angles for name in a.credited}
                found = [
                    s.name
                    for s in saved
                    if s.name.split("__", 1)[0] == one.cell and s.name not in taken
                ][: max(0, one.want - one.done)]
            # Credited through the canvas ledger: each name fills one bucket only, ever, and a
            # bucket filled over two rounds adds up instead of the second fold erasing the first.
            on_disk = held.credit(angle_id, found)
            claimed = int(row.get("wrote") or 0)
            was = held.fold(
                angle_id,
                done=on_disk,
                short=str(row.get("short") or ""),
                blocked_reason=str(row.get("blocked_reason") or ""),
            )
            note = f"  {angle_id}: {on_disk}/{one.want} on disk, now {was}"
            # A bucket asking for several says which axes tell them apart, and the plan is checked
            # against that. Nothing checked the delivery, so a bucket could be credited with three
            # scenarios that move the caller and nothing else, and still report done.
            if one.want > 1 and len(one.credited) > 1:
                by_name = {s.name: s for s in saved}
                shapes = {
                    (
                        tuple(step.tool for step in got.solution),
                        tuple(sorted(got.sub_goals)),
                    )
                    for name in one.credited
                    if (got := by_name.get(name)) is not None
                }
                if len(shapes) == 1:
                    note += (
                        f" -- all {len(one.credited)} run the same tools and check the same "
                        f"sub-goals, so they are one test written {len(one.credited)} times; "
                        f"they were meant to differ by {', '.join(one.varies_by) or 'nothing named'}"
                    )
            missing = [x for x in claimed_names if x not in {s.name for s in saved}]
            if missing:
                note += f" ({len(missing)} named but not on disk: {', '.join(missing[:3])})"
            elif claimed and claimed != on_disk:
                note += f" (writer said {claimed}, which does not match and is worth checking)"
            lines.append(note)
        opened = held.add(
            [
                Angle(
                    id="",
                    theme=str((row or {}).get("theme") or "").strip(),
                    cell=str((row or {}).get("cell") or "").strip(),
                    angle=str((row or {}).get("angle") or "").strip(),
                    why_hard=str((row or {}).get("why_hard") or "").strip(),
                    want=max(1, int((row or {}).get("want") or 1)),
                )
                for row in args.get("found") or []
                if isinstance(row, dict)
            ]
        )
        if opened:
            lines.append("")
            lines.append(f"{len(opened)} buckets opened that nobody planned:")
            lines += [f"  {one.line()}" for one in opened]

        held.written_to(destination)
        lines.append("")
        lines.append(f"{held.written} of {held.planned} written.")
        if held.reached():
            lines.append(held.reached())
        return _ok("\n".join(lines))

    @tool(
        "show_diversity",
        "The shape of the saved suite: how it spreads across cells, who is in it, how much work "
        "each scenario asks for, and any pair that reads as the same test written twice. Read "
        "this before saving a large suite, where nobody can read the scenarios themselves.\n\n"
        "It is lexical, so it catches near-copies and rewordings. Two scenarios describing one "
        "situation in entirely different words will pass it, and that is a real limit rather "
        "than a detail.",
        schema({}, []),
    )
    async def show_diversity(_args: dict[str, Any]) -> dict[str, Any]:
        held = load_scenarios(destination)
        if not held:
            return _ok("No scenarios saved yet.")
        return _ok(measure(held).rendered())

    @tool(
        "plan_suite",
        "One way to cover the grid in a given number of scenarios. **A suggestion, not an "
        "instruction.** It is arithmetic over the grid and knows nothing about this agent: it "
        "cannot tell which of its cells are dangerous in practice, where its real users spend "
        "their time, or which of its operations you have just read the source for and know to "
        "be fragile. You can. Take what fits, drop what does not, write cells it did not "
        "choose, and say what you changed and why. It is most useful when the count is large "
        "enough that choosing by hand would be the whole job.",
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
        lines = [
            f"A suggested {len(picks)}. Yours to change: this is arithmetic over the grid, and "
            "you have read the agent.",
            "",
        ]
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
        tools=[
            show_grid,
            set_objects,
            record_canvas,
            show_canvas,
            claim_slice,
            fold_return,
            show_diversity,
            plan_suite,
            list_scenarios,
            show_coverage,
            expand_suite,
        ],
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
    return (
        "show_grid",
        "set_objects",
        "record_canvas",
        "show_canvas",
        "claim_slice",
        "fold_return",
        "show_diversity",
        "plan_suite",
        "list_scenarios",
        "show_coverage",
        "expand_suite",
    )
