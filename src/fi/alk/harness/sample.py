"""Choosing which of the grid's cells to actually write, for whatever number was asked for.

The count is the caller's, not ours. One scenario and a hundred thousand are both reasonable
things to want, and the same rules have to serve both: a suite of four has to be four scenarios
worth running, and a suite of a hundred thousand has to be produced without comparing every pick
against every other one.

Three passes, in this order, because each is worth more per scenario than the one after it:

1. **The ladder.** A short ordered list of the things a suite is not worth running without: the
   ordinary path, the request that must be refused, the thing that has already gone wrong, the
   escalation. It lives in the axis file, so what a small suite contains is tuned by editing data.
2. **Forced coverage.** Every setting on an axis marked ``force_every_setting``, and at least one
   cell of each kind. These are too rare to survive weighting and too important to lose.
3. **Even fill.** Cells by weight, each paired with dial settings dealt round-robin so the suite
   spreads rather than converging on whatever the first writer happened to pick.

Nothing here calls a model. It decides what to ask for; the writers decide what to say.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from .axes import AxisSet
from .grid import Cell, Grid

logger = logging.getLogger(__name__)

# How many slots one unit of weight buys in the fill. Four is enough resolution to tell an axis
# that should appear half as often from one that should not, without making the cycle so long
# that a small suite never reaches its later entries.
_SCALE = 4

# The ordinary caller is not one condition among many. Most of what an agent meets is somebody
# with nothing unusual about them, and a suite that forgets this tests the exceptions well and
# the job badly.
BASELINE_WEIGHT = 2.0


def _slots(weight: float) -> int:
    return max(1, round(weight * _SCALE))


@dataclass(frozen=True)
class Pick:
    """One scenario to write: which cell, under which conditions, and why it is worth writing."""

    cell: Cell
    # Axis name to setting name, for the dials moved off baseline. Usually one.
    dials: dict[str, str] = field(default_factory=dict)
    why: str = ""
    # Which branch of this coordinate. Nought is the first and unnumbered. A cell has more than
    # one scenario in it whenever the interesting difference is in the records rather than in the
    # conditions: the ride that has already started, the one paid from a wallet with nothing in
    # it, the one belonging to somebody else.
    branch: int = 0

    @property
    def name(self) -> str:
        """The scenario's identity, and its row in the coverage report."""
        moved = "-".join(self.dials[key] for key in sorted(self.dials))
        stem = f"{self.cell.name}__{moved}" if moved else f"{self.cell.name}__baseline"
        return stem if not self.branch else f"{stem}__b{self.branch + 1}"

    def described(self) -> str:
        conditions = ", ".join(f"{axis}={value}" for axis, value in sorted(self.dials.items()))
        return f"{self.cell.described()}" + (f" | {conditions}" if conditions else " | baseline")


def _unique(pick: Pick, taken: set[str]) -> Pick:
    """The same pick, moved onto the next free branch if its name is already spoken for.

    Two rungs of the ladder can legitimately land on the same coordinate: on an agent with no
    state-changing tools, "the ordinary path" and "identity has to be established" are both the
    authenticate cell at baseline. Dropping the second loses a case the caller asked for, so it
    becomes another branch of the same cell instead.
    """
    while pick.name in taken:
        pick = Pick(cell=pick.cell, dials=pick.dials, why=pick.why, branch=pick.branch + 1)
    taken.add(pick.name)
    return pick


def _matching(grid: Grid, kind: str = "", prefer: tuple[str, ...] = ()) -> list[Cell]:
    """Cells that fit a rung of the ladder, best first.

    ``prefer`` names operations rather than requiring them: a rung asking for a diagnose cell on
    an agent with none should fall through to something adjacent rather than be skipped, because
    a skipped rung is a scenario the caller asked for and did not get.
    """
    pool = [cell for cell in grid.cells if not kind or cell.kind == kind]
    if not pool:
        pool = list(grid.cells)
    if prefer:
        ranked = [cell for cell in pool if cell.operation in prefer]
        if ranked:
            pool = ranked
    return sorted(pool, key=lambda cell: (-cell.weight, cell.name))


def _usable(axes: AxisSet, env: dict[str, str] | None) -> dict[str, list[str]]:
    """Every dial setting that would actually change a run, by axis."""
    found: dict[str, list[str]] = {}
    for axis in axes.axes:
        live = [one.name for one in axis.settings if one.live(env)]
        if live:
            found[axis.name] = live
    return found


def _ladder(grid: Grid, axes: AxisSet, priorities: list[dict], usable: dict[str, list[str]]) -> list[Pick]:
    """The rungs, in order, each landing on a distinct cell where one exists."""
    picks: list[Pick] = []
    taken: set[str] = set()
    names: set[str] = set()
    for rung in priorities:
        if not isinstance(rung, dict):
            continue
        prefer = tuple(str(one) for one in rung.get("prefer") or ())
        candidates = _matching(grid, str(rung.get("kind") or ""), prefer)
        # A rung that repeats a cell already taken teaches less than the same rung on a fresh
        # one, so a used cell is only reused when nothing else is left.
        cell = next((one for one in candidates if one.name not in taken), None) or (
            candidates[0] if candidates else None
        )
        if cell is None:
            continue
        dials = {
            axis: value
            for axis, value in (rung.get("dials") or {}).items()
            if value in usable.get(axis, [])
        }
        taken.add(cell.name)
        picks.append(
            _unique(
                Pick(cell=cell, dials=dials, why=str(rung.get("want") or "").strip()),
                names,
            )
        )
    return picks


def _forced(grid: Grid, axes: AxisSet, usable: dict[str, list[str]], have: list[Pick]) -> list[Pick]:
    """What the suite is rejected without, once the ladder has had its turn."""
    picks: list[Pick] = []
    names = {pick.name for pick in have}
    covered = {(axis, value) for pick in have for axis, value in pick.dials.items()}
    kinds = {pick.cell.kind for pick in have}

    for axis in axes.axes:
        if not axis.force_every_setting:
            continue
        for value in usable.get(axis.name, []):
            if (axis.name, value) in covered:
                continue
            pool = _matching(grid, "change") or _matching(grid)
            cell = next((one for one in pool if one.name not in {p.cell.name for p in have + picks}), pool[0])
            picks.append(
                _unique(
                    Pick(cell=cell, dials={axis.name: value},
                         why=f"every {axis.name} has to appear at least once"),
                    names,
                )
            )
            covered.add((axis.name, value))

    for kind in ("read", "change", "manage"):
        if kind in kinds:
            continue
        pool = _matching(grid, kind)
        if pool and pool[0].kind == kind:
            picks.append(
                _unique(Pick(cell=pool[0], why=f"nothing else covers a {kind} operation"), names)
            )
            kinds.add(kind)

    # Every remaining setting, once. The fill after this is weighted, so an ordinary caller gets
    # the share of the suite an ordinary caller should have; without this pass that weighting
    # would decide whether a setting appears at all, and a dial the suite never moves is a dial
    # nobody knows is broken.
    pool = _matching(grid)
    for axis, values in sorted(usable.items()):
        for value in values:
            if (axis, value) in covered:
                continue
            cell = next(
                (one for one in pool if one.name not in {p.cell.name for p in have + picks}),
                pool[0] if pool else None,
            )
            if cell is None:
                continue
            picks.append(
                _unique(Pick(cell=cell, dials={axis: value}, why=f"nothing else moves {axis} to {value}"), names)
            )
            covered.add((axis, value))
    return picks


def _fill(grid: Grid, axes: AxisSet, usable: dict[str, list[str]], wanted: int, have: list[Pick]) -> list[Pick]:
    """The rest, dealt evenly so the suite spreads instead of clustering.

    Cells cycle by weight and dial settings cycle alongside them, one dial moved at a time. Every
    writer is blind to the others, so left to instruction alone each independently picks the
    safest value and the suite converges on it. Dealing the spread here is the only thing that
    reliably prevents that.
    """
    if len(have) >= wanted:
        return []
    ordered = sorted(grid.cells, key=lambda cell: (-cell.weight, cell.name))
    if not ordered:
        return []

    # One flat list of every (axis, setting) worth moving, plus the baseline, each repeated in
    # proportion to how often it should appear. Weighting matters most for the adversarial axis:
    # every one of its settings is already guaranteed a place by the forcing pass, so letting it
    # take an equal share of the fill as well produces a suite that is half attack, which is not
    # what the agent mostly meets and buries the ordinary paths it mostly fails on.
    by_axis = {axis.name: axis for axis in axes.axes}
    conditions: list[dict[str, str]] = [{}] * _slots(BASELINE_WEIGHT)
    for name, values in sorted(usable.items()):
        weight = by_axis[name].weight if name in by_axis else 1.0
        for value in values:
            conditions.extend([{name: value}] * _slots(weight))

    # Pairs of dials, but only when singles cannot fill the request. Two conditions at once is
    # where the interaction bugs live, and it is also what keeps a very large request from
    # running out of coordinates. It costs the thing that makes a result readable, though: a
    # failure with one dial moved says which condition caused it and a failure with two does not.
    # So pairs are a last resort before branches rather than an equal part of the mix.
    if wanted > len(ordered) * len(conditions):
        singles = [one for one in {tuple(sorted(c.items())) for c in conditions if c}]
        for first in range(len(singles)):
            for second in range(first + 1, len(singles)):
                left, right = dict(singles[first]), dict(singles[second])
                if set(left) & set(right):
                    continue
                conditions.append({**left, **right})

    seen = {pick.name for pick in have}
    picks: list[Pick] = []
    needed = wanted - len(have)
    # Branches are the last resort and the reason any count is reachable. A cell paired with a
    # condition it already carries is not a duplicate if the records underneath it differ, which
    # is what a branch says: same coordinate, different thing true in the world. The writer is
    # told which branch it is on and what the others are, so it writes a different test rather
    # than the same one again.
    # One more branch than could possibly be needed: every branch above the first yields a fresh
    # name for every coordinate, so a single extra pass always covers whatever the earlier ones
    # lost to collisions with what the ladder already took.
    for branch in range(needed + 1):
        for index in range(len(ordered) * len(conditions)):
            if len(picks) >= needed:
                return picks
            # Both cycle on the same index rather than one nesting inside the other. Nested, the
            # condition does not advance until the cell list has been walked once, so any suite
            # smaller than the grid comes out entirely at baseline and the dials go untested.
            cell = ordered[index % len(ordered)]
            condition = conditions[index % len(conditions)]
            pick = Pick(
                cell=cell,
                dials=dict(condition),
                why="filling the grid by weight" if not branch else "another branch of the same cell",
                branch=branch,
            )
            if pick.name in seen:
                continue
            seen.add(pick.name)
            picks.append(pick)
        if len(picks) >= needed:
            break
    return picks


def plan(
    grid: Grid,
    axes: AxisSet,
    wanted: int,
    *,
    priorities: list[dict] | None = None,
    env: dict[str, str] | None = None,
) -> list[Pick]:
    """Which scenarios to write, for any count.

    Always returns exactly ``wanted`` picks where the grid is large enough to supply them, and
    says so in the log when it is not, rather than quietly returning fewer. A caller who asked
    for a hundred and got sixty needs to know which of those two numbers to believe.
    """
    wanted = max(0, int(wanted))
    if not wanted or not grid.cells:
        return []

    usable = _usable(axes, env)
    rungs = list(priorities if priorities is not None else axes.priorities)
    picks = _ladder(grid, axes, rungs, usable)[:wanted]
    if len(picks) < wanted:
        picks.extend(_forced(grid, axes, usable, picks))
        picks = picks[:wanted]
    if len(picks) < wanted:
        picks.extend(_fill(grid, axes, usable, wanted, picks))

    picks = picks[:wanted]
    if len(picks) < wanted:
        logger.warning(
            "asked for %s scenarios and the grid supports %s distinct ones; "
            "the rest would repeat a cell and a condition already covered",
            wanted,
            len(picks),
        )
    return picks


def coverage(grid: Grid, axes: AxisSet, picks: list[Pick]) -> str:
    """What a plan covers, as the report a person reads instead of a count."""
    cells = {pick.cell.name for pick in picks}
    by_kind: dict[str, int] = {}
    for pick in picks:
        by_kind[pick.cell.kind or "other"] = by_kind.get(pick.cell.kind or "other", 0) + 1
    lines = [
        f"{len(picks)} scenarios over {len(cells)} of {len(grid.cells)} cells.",
        "  by operation kind: " + ", ".join(f"{kind} {n}" for kind, n in sorted(by_kind.items())),
    ]
    for axis in axes.axes:
        used = {pick.dials.get(axis.name) for pick in picks} - {None}
        every = {one.name for one in axis.settings}
        missing = sorted(every - used)
        lines.append(
            f"  {axis.name}: {len(used)}/{len(every)} settings"
            + (f", not covered: {', '.join(missing)}" if missing else "")
        )
    untouched = sorted({cell.name for cell in grid.cells} - cells)
    if untouched:
        lines.append(f"  cells with nothing on them ({len(untouched)}): {', '.join(untouched[:12])}"
                     + (" ..." if len(untouched) > 12 else ""))
    return "\n".join(lines)
