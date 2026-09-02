"""The plan for a suite, and the ledger of what has been written against it.

Asking a model for a thousand scenarios in one context does not work, and asking it for a thousand
one at a time converges: each is composed with the last few in view, so the suite drifts toward
whatever the opening ones were. Measured here, fifty scenarios came back with nine people in them,
forty-two of them American, living in two places, and every writer had been told to vary its work.

So the suite is decided before it is written, at a level cheap enough that all of it fits in one
head at once. A line says *what is worth testing*, never *how it resolves*: "surge boundary
confusion", not "charged 2.3x, receipt shows the higher rate, agent explains the window closed".
The second is the scenario with its code removed, and writing it here is what makes a plan for a
thousand impossible to emit at all: at that length a thousand lines is 228KB and 57k tokens in one
response.

The plan owns coverage and spread. The writer owns the particulars, decided with the agent's
source open, which is the only place they can be checked.

## Why it is a ledger and not a document

A plan handed out whole cannot answer the question the loop actually has, which is what is left.
So each angle carries its own state, the loop dispatches one writer at a time against what is
still open, folds back what returned, and re-ranks. Two consequences worth naming.

``done`` is counted from disk and never from the writer's report. A stage once finished a run
having saved one scenario of fifty and described it as a success; the writer's own count is kept
only to notice when it disagrees with the disk, which is itself a bug signal.

The ceiling arrives as evidence rather than prediction. An agent with twenty tools does not have a
thousand distinct things worth testing, and the honest number is not guessed up front: angles
nobody can fill after repeated attempts become ``blocked``, and what remains when nothing is open
is what this agent actually supports.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Two angles sharing this much wording are the same angle twice. A backstop only: at angle length
# one differing word swings the ratio hard, so ``why_hard`` is what dedup really keys on.
TOO_ALIKE = 0.7

# Below this there is nothing to plan; the writing stage handles small suites directly.
WORTH_PLANNING = 20

# What the agent should do. Exactly one is true of any scenario and between them they cover
# everything an agent can do, which is what makes the coverage line worth reading.
#
# An earlier version of this axis was happy / edge / adversarial / failing, taken from a
# conversation rather than derived. Those overlap: an injection attempt is adversarial and also a
# path bound to fail, "edge" is an intensity rather than a kind, and outcome and cause were mixed
# into one field. Two planners would label the same bucket differently, which makes the count
# meaningless. Splitting outcome from cause fixes it.
EXPECTS = ("succeed", "refuse", "ask", "escalate")

# Why it is hard, when something is deliberately making it hard. PR 44's overlay axis, and
# orthogonal to EXPECTS on purpose: an injection is `refuse` plus `injection`, never a choice
# between the two.
OVERLAYS = ("impersonation", "injection", "fraud", "emergency", "pressure")

# An angle past this has stopped naming what to test and started scripting how it goes.
MOST_ANGLE_CHARS = 90

# Dispatches spent on one angle before it is called blocked rather than merely unlucky. Three,
# because the second attempt usually goes to a writer not carrying the first one's assumptions,
# and a third failure is evidence rather than noise.
MOST_ATTEMPTS = 3

# A plan whose buckets outnumber this share of the target has stopped being a plan and become a
# list of scenarios with extra fields. Measured: the first real canvas came back 50 buckets for a
# target of 50, every want 1, which at a target of a thousand would mean writing a thousand
# buckets and hitting the wall that planning exists to avoid.
MOST_BUCKETS_PER_TARGET = 0.6

# Roughly how many scenarios to put in front of one writer: small enough to hold the whole brief,
# large enough that dispatch is not most of the run.
SLICE_SCENARIOS = 8

_WORD = re.compile(r"[a-z0-9]+")
_NOISE = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "for", "with", "without", "to", "of", "in",
        "on", "at", "by", "from", "then", "than", "that", "this", "it", "its", "is", "are",
        "was", "be", "been", "has", "have", "had", "do", "does", "did", "not", "no",
        "caller", "person", "user", "agent", "asks", "ask", "wants", "want", "tries", "try",
    }
)


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _NOISE}


def _overlap(one: set[str], two: set[str]) -> float:
    """How much two lines share, against the smaller one.

    Against the smaller rather than the union, because a short line and a padded restatement of it
    are the same line, and union would score that pair apart purely on length.
    """
    if not one or not two:
        return 0.0
    return len(one & two) / min(len(one), len(two))


@dataclass
class StateAxis:
    """A dimension of the world whose value changes what the agent should do.

    Derived from the agent's own data and rules, never invented. Two rules keep the list honest:
    a level has to exist in the seeded data or be reachable by seeding it, and a level has to
    change the correct answer. Nine riders are nine names, not nine levels.

    This is the axis PR 44 leaves as "domain entities, expand within each task". It is the only
    one that produces different tests rather than different tellings of one test, which is why
    it is the one that decides how many scenarios a bucket holds.
    """

    name: str
    levels: list[str] = field(default_factory=list)
    why: str = ""


@dataclass
class Theme:
    """A group of angles, and the unit the loop pages in and out.

    Hierarchy is what keeps this usable past a few hundred angles: the loop holds the theme table
    and the one theme it is working on, never the whole canvas.
    """

    id: str
    name: str
    why: str = ""


@dataclass
class Angle:
    """One thing worth testing, how many variants exist, and how it is going."""

    id: str
    theme: str
    cell: str
    angle: str
    # The structural thing under test: `rule:surge-disclosure`, `precondition:book_ride`,
    # `data:expired-card`. Declared rather than inferred, which is what makes dedup work at a
    # length where comparing words does not.
    why_hard: str = ""
    want: int = 1
    # Which state axes actually move the answer for this bucket. `want` is the number of their
    # combinations that survive masking, so a count stops being a guess and becomes a derivation.
    varies_by: list[str] = field(default_factory=list)
    # One of EXPECTS: what the agent should do here.
    expects: str = ""
    # One of OVERLAYS, or empty. What is deliberately making it hard, if anything.
    overlay: str = ""
    # What differs between this bucket's scenarios. Required once it claims more than one, because
    # a number is easy to write and "what changes between them" is the thing that has to be true.
    differs: str = ""
    done: int = 0
    refused: int = 0
    attempts: int = 0
    state: str = "open"
    claimed_by: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def outstanding(self) -> int:
        return max(0, self.want - self.done)

    def line(self) -> str:
        """One bucket as a writer is given it.

        `varies_by` and `differs` belong here even though they read like planning notes. They are
        the only thing standing between a bucket of five and one scenario written five times: the
        plan deliberately does not name the five, so what it must say instead is the dimension
        they differ along. Left out of this line, as it was at first, a writer is told to produce
        five and never told what makes them five.
        """
        held = f"{self.id} | {self.cell} | {self.angle} | x{self.want}"
        if self.why_hard:
            held += f" | {self.why_hard}"
        if self.expects:
            held += f" | expects {self.expects}"
        if self.overlay:
            held += f" | overlay {self.overlay}"
        if self.want > 1:
            reason = ", ".join(self.varies_by) or self.differs
            if reason:
                held += f"\n      the {self.want} differ by: {reason}"
        if self.done or self.state != "open":
            held += f"\n      {self.state}, {self.done} of {self.want} written"
        return held


@dataclass
class Canvas:
    """Every angle a suite intends to cover, and what has been written against each."""

    themes: list[Theme] = field(default_factory=list)
    angles: list[Angle] = field(default_factory=list)
    axes: list[StateAxis] = field(default_factory=list)
    target: int = 0
    ceiling: str = ""

    @property
    def planned(self) -> int:
        """Scenarios this plan asks for, which is not how many lines it has."""
        return sum(max(1, one.want) for one in self.angles)

    @property
    def written(self) -> int:
        return sum(one.done for one in self.angles)

    @property
    def covered(self) -> set[str]:
        return {one.cell for one in self.angles}

    def named(self, angle_id: str) -> Angle | None:
        return next((one for one in self.angles if one.id == angle_id), None)

    def of_theme(self, theme: str) -> list[Angle]:
        return [one for one in self.angles if one.theme == theme]

    def shortfall(self) -> int:
        return max(0, self.target - self.planned)

    def problems(self, cells: set[str]) -> list[str]:
        """What must be fixed before a writer acts on any of it.

        Everything here is cheaper to catch now: a plan that repeats itself becomes a suite that
        repeats itself, and by then each duplicate has cost a proof and a folder.
        """
        found: list[str] = []
        if not self.angles:
            return ["the canvas is empty"]

        ids = [one.id for one in self.angles]
        repeated = sorted({one for one in ids if ids.count(one) > 1})
        if repeated:
            found.append(f"{len(repeated)} angle ids appear twice: " + ", ".join(repeated[:8]))

        known = {one.id for one in self.themes}
        orphans = sorted({one.theme for one in self.angles if one.theme not in known})
        if orphans:
            found.append(
                f"{len(orphans)} angles name a theme that is not declared: "
                + ", ".join(orphans[:8])
            )

        unknown = sorted(self.covered - cells)
        if unknown:
            found.append(
                f"{len(unknown)} angles name a cell that is not on the grid: "
                + ", ".join(unknown[:8])
                + ". Use show_grid, or correct the grid with set_objects if the grid is wrong."
            )

        thin = [one.id for one in self.angles if len(_words(one.angle)) < 2]
        if thin:
            found.append(
                f"{len(thin)} angles say too little to write from: "
                + ", ".join(thin[:8])
                + ". An angle names what makes a case worth testing, in a few words."
            )

        known = {one.name for one in self.axes}
        stray = sorted(
            {name for one in self.angles for name in one.varies_by if name not in known}
        )
        if stray:
            found.append(
                f"{len(stray)} buckets name a state axis that was never derived: "
                + ", ".join(stray[:8])
                + ". Every axis has to come from the agent's data or its rules."
            )

        wrong = sorted(
            {one.expects for one in self.angles if one.expects and one.expects not in EXPECTS}
        )
        if wrong:
            found.append(
                f"{len(wrong)} buckets expect something that is not one of "
                + ", ".join(EXPECTS)
                + ": "
                + ", ".join(wrong[:6])
            )
        odd = sorted(
            {one.overlay for one in self.angles if one.overlay and one.overlay not in OVERLAYS}
        )
        if odd:
            found.append(
                f"{len(odd)} buckets name an overlay that is not one of "
                + ", ".join(OVERLAYS)
                + ": "
                + ", ".join(odd[:6])
            )

        # The arithmetic has to hold: a bucket cannot hold more scenarios than its axes can tell
        # apart. Masking only removes combinations, so a want above the product came from
        # somewhere other than the axes. Measured on the first real plan, 19 of 167 multi-scenario
        # buckets failed this, one asking for eight from an axis with three levels, and the
        # reasons given were lists of data values: six riders each using their own card is one
        # test run six times, not six tests.
        levels = {one.name: max(1, len(one.levels)) for one in self.axes}
        overreach: list[str] = []
        for one in self.angles:
            if one.want <= 1 or not one.varies_by:
                continue
            room = 1
            for name in one.varies_by:
                room *= levels.get(name, 1)
            if one.want > room:
                overreach.append(f"{one.id} wants {one.want} from {room}")
        if overreach:
            found.append(
                f"{len(overreach)} buckets ask for more scenarios than the axes they name can "
                "tell apart: "
                + "; ".join(overreach[:6])
                + ". Name the other axis that varies, or lower the count. Different data with the "
                "same answer is one test repeated, not several."
            )

        # Refused, not merely noted, and only once most of the plan is like this. A written reason
        # can be perfectly good and a few of them are expected; what cannot stand is a plan whose
        # sizes rest on prose throughout, because then no number in it can be checked by anything.
        # The planner can almost always name the axis it means, and being made to is the point.
        unchecked = [one.id for one in self.angles if one.want > 1 and not one.varies_by]
        if len(unchecked) > max(3, len(self.angles) // 4):
            found.append(
                f"{len(unchecked)} buckets justify their count in words rather than by naming "
                "axes, so nothing can check them. Name the axes wherever you can."
            )

        unjustified = [
            one.id
            for one in self.angles
            if one.want > 1 and not one.varies_by and len(_words(one.differs)) < 2
        ]
        if unjustified:
            found.append(
                f"{len(unjustified)} buckets ask for more than one scenario without saying what "
                "differs between them: "
                + ", ".join(unjustified[:8])
                + ". Name what changes, like 'the market, which decides whether cash is offered'. "
                "If nothing changes the right answer, the bucket holds one scenario."
            )

        # Checked against the target rather than the count, because the failure is a plan that
        # enumerates instead of grouping, and that only shows up relative to what was asked for.
        if self.target >= WORTH_PLANNING and len(self.angles) > self.target * MOST_BUCKETS_PER_TARGET:
            found.append(
                f"{len(self.angles)} buckets for a target of {self.target} is a list of scenarios "
                "with extra fields, not a plan. A bucket holds several scenarios; that is what "
                "keeps a plan small while the suite grows. Group them, or if these cases really "
                "are all singular then this agent supports fewer scenarios than were asked for "
                "and the honest move is to say so rather than to enumerate."
            )

        wordy = [one.id for one in self.angles if len(one.angle) > MOST_ANGLE_CHARS]
        if wordy:
            found.append(
                f"{len(wordy)} angles are written as scripts rather than angles: "
                + ", ".join(wordy[:6])
                + f". Keep one under {MOST_ANGLE_CHARS} characters and leave the particulars to "
                "whoever writes it, with the source in front of them."
            )
        return found

    def collisions(self) -> list[tuple[str, str, str]]:
        """Angles that may be one angle twice: same why_hard on one cell, or near-identical wording.

        Reported, never refused. The first real canvas produced seven of these and six were
        legitimate: three different input *forms* for one address, four different reasons for
        going out of scope. One was a genuine duplicate. A check that auto-rejected all seven
        would have been wrong six times, so this asks rather than decides.
        """
        found: list[tuple[str, str, str]] = []
        seen: dict[tuple[str, str], str] = {}
        for one in self.angles:
            if not one.why_hard:
                continue
            key = (one.cell, one.why_hard)
            if key in seen:
                found.append((seen[key], one.id, f"same why_hard {one.why_hard!r} on {one.cell}"))
            else:
                seen[key] = one.id

        by_theme: dict[str, list[Angle]] = {}
        for one in self.angles:
            by_theme.setdefault(one.theme, []).append(one)
        for group in by_theme.values():
            words = [(one, _words(one.angle)) for one in group]
            for index, (one, mine) in enumerate(words):
                for other, theirs in words[index + 1 :]:
                    if one.cell == other.cell and _overlap(mine, theirs) >= TOO_ALIKE:
                        found.append((one.id, other.id, "near-identical wording"))
        return found

    def reclaim(self) -> int:
        """Put back angles nobody is going to return.

        Called when a canvas is read from disk, never while dealing: a claim belongs to the run
        that made it, so anything still claimed in a file is a writer that died with its process.
        Reclaiming on every deal instead re-opens the claim just made, and the same angles go out
        to every writer.
        """
        loose = [one for one in self.angles if one.state == "claimed"]
        for one in loose:
            one.state = "open"
            one.claimed_by = ""
        return len(loose)

    def debt(self) -> dict[str, float]:
        """How much of each theme is still unwritten, as a fraction."""
        held: dict[str, float] = {}
        for theme in {one.theme for one in self.angles}:
            mine = self.of_theme(theme)
            asked = sum(max(1, one.want) for one in mine) or 1
            held[theme] = sum(one.outstanding for one in mine) / asked
        return held

    def next_slice(self, scenarios: int = SLICE_SCENARIOS) -> list[Angle]:
        """The angles to put in front of the next writer.

        Ranked by what is outstanding, weighted by how much of its theme is still unwritten, so a
        theme nobody has touched outranks one nearly finished even with a smaller remainder. That
        is what stops a suite covering the booking path beautifully and never testing the rules.

        Bundled across cells and never within one: a writer handed a whole cell has to invent that
        cell's entire variety alone, which is the position planning exists to remove.
        """
        owed = self.debt()
        open_angles = [one for one in self.angles if one.state == "open" and one.outstanding > 0]
        open_angles.sort(
            key=lambda one: (-(one.outstanding * (1 + owed.get(one.theme, 0))), one.id)
        )

        taken: list[Angle] = []
        cells: set[str] = set()
        budget = 0
        for one in open_angles:
            if one.cell in cells:
                continue
            taken.append(one)
            cells.add(one.cell)
            budget += one.outstanding
            if budget >= scenarios:
                break
        return taken

    def claim(self, angles: list[Angle], writer: str) -> None:
        for one in angles:
            one.state = "claimed"
            one.claimed_by = writer
            one.attempts += 1

    def add(self, found: list[Angle]) -> list[Angle]:
        """Buckets a writer found that nobody planned.

        The canvas the planner writes is a partition of what it could see from outside the code.
        A writer works inside one bucket with the source open and routinely finds that the bucket
        holds cases the planner could not have known about. Without somewhere to put them the
        writer either silently drops them or crams them into the bucket it was given, and the
        canvas keeps claiming a completeness it never had.

        Ids are made here rather than by the writer, so two writers finding something at the same
        time cannot collide.
        """
        taken = {one.id for one in self.angles}
        kept: list[Angle] = []
        for one in found:
            if not one.angle.strip() or not one.cell.strip():
                continue
            stem = one.theme if any(t.id == one.theme for t in self.themes) else "TH00"
            index = 1
            while f"{stem}-F{index:02d}" in taken:
                index += 1
            one.id = f"{stem}-F{index:02d}"
            one.theme = stem
            taken.add(one.id)
            self.angles.append(one)
            kept.append(one)
        if kept and not any(one.id == "TH00" for one in self.themes):
            if any(one.theme == "TH00" for one in kept):
                self.themes.append(
                    Theme(id="TH00", name="Found while writing", why="Not planned from outside.")
                )
        return kept

    def fold(
        self,
        angle_id: str,
        *,
        done: int,
        short: str = "",
        refused: int = 0,
        blocked_reason: str = "",
    ) -> str:
        """Take one writer's return. ``done`` comes from disk, never from the writer's report."""
        one = self.named(angle_id)
        if one is None:
            return f"no angle called {angle_id!r}"
        one.done = done
        one.refused += refused
        one.claimed_by = ""
        if short:
            one.notes.append(short.strip())
        if blocked_reason:
            one.state = "blocked"
            one.notes.append(f"blocked: {blocked_reason.strip()}")
        elif one.outstanding <= 0:
            one.state = "done"
        elif one.attempts >= MOST_ATTEMPTS:
            one.state = "blocked"
        else:
            one.state = "open"
        return one.state

    def coverage(self, cells: set[str], rules: list[str], tools: list[str] | None = None) -> str:
        """What this plan covers, said against something outside itself.

        A plan can only be checked against the agent, not against its own tidiness, so this
        reports the two things that are falsifiable: which cells nothing sits on, and whether
        every rule the agent must obey has a bucket that tests it.
        """
        expects: dict[str, int] = {one: 0 for one in EXPECTS}
        unset = 0
        overlaid = 0
        for one in self.angles:
            if one.expects in expects:
                expects[one.expects] += max(1, one.want)
            else:
                unset += 1
            if one.overlay:
                overlaid += max(1, one.want)

        kinds: dict[str, int] = {}
        for one in self.angles:
            kind = (one.why_hard.split(":", 1)[0] or "unnamed") if one.why_hard else "unnamed"
            kinds[kind] = kinds.get(kind, 0) + 1

        tools = tools or []
        empty = sorted(cells - self.covered)
        tested = " ".join(one.why_hard.lower() + " " + one.angle.lower() for one in self.angles)
        # A rule with nothing resembling it anywhere in the plan is the gap worth shouting about:
        # these are the things the agent is forbidden to get wrong.
        untested = [
            one
            for one in rules
            if not any(word in tested for word in sorted(_words(one), key=len, reverse=True)[:3])
        ]

        lines = [
            f"{len(cells)} cells, {len(self.covered)} with a bucket on them, "
            f"{len(empty)} with nothing.",
            f"{len(self.angles)} buckets over {len(kinds)} why_hard kinds: "
            + ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items(), key=lambda k: -k[1])),
            f"{len(self.axes)} state axes derived from the data.",
            f"{self.planned} scenarios planned: "
            + ", ".join(f"{n} {kind}" for kind, n in expects.items())
            + (f", {unset} buckets not saying" if unset else "")
            + f". {overlaid} carry an adversarial overlay.",
        ]
        nothing = [kind for kind, n in expects.items() if not n]
        if nothing:
            lines.append(
                "  nothing the agent should "
                + ", ".join(nothing)
                + ". A suite where the agent never has to refuse, ask or escalate is testing "
                "one third of its job."
            )
        if empty:
            lines.append("  nothing on: " + ", ".join(empty[:12]) + ("" if len(empty) <= 12 else " ..."))
        if tools:
            # Only the tools that refuse until something else has happened. Each one is a real
            # test - what does the agent do when somebody asks for it too early - and the grid
            # cannot show the hole, because a cell is an object and says nothing about order.
            named = " ".join(one.angle.lower() + " " + one.why_hard.lower() for one in self.angles)
            missed = [one for one in tools if one.lower() not in named]
            lines.append(
                f"  {len(tools) - len(missed)} of {len(tools)} tools with preconditions have a "
                "bucket that names them."
                + ("" if not missed else " Not named: " + ", ".join(missed[:10]))
            )
        if rules:
            lines.append(
                f"  {len(rules) - len(untested)} of {len(rules)} rules have a bucket."
                + ("" if not untested else " Not covered: " + "; ".join(one[:60] for one in untested[:5]))
            )
        return "\n".join(lines)

    def reached(self) -> str:
        """What this suite supports, once nothing is open. Evidence, not a prediction."""
        stuck = [one for one in self.angles if one.state == "blocked"]
        if not stuck:
            return ""
        lost = sum(one.outstanding for one in stuck)
        return (
            f"{self.written} written against {self.planned} planned. {len(stuck)} angles could "
            f"not be filled, {lost} scenarios short. This is what the agent supports without "
            "repeating itself, rather than a number decided before anyone tried."
        )

    def slices(self, size: int) -> list[list[Angle]]:
        """Dealt round-robin so no writer is handed one whole cell."""
        if size < 1:
            return [list(self.angles)]
        count = max(1, (len(self.angles) + size - 1) // size)
        dealt: list[list[Angle]] = [[] for _ in range(count)]
        for index, one in enumerate(self.angles):
            dealt[index % count].append(one)
        return [one for one in dealt if one]

    def written_to(self, destination: Path) -> Path:
        path = Path(destination) / "blueprint.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "target": self.target,
                    "planned": self.planned,
                    "ceiling": self.ceiling,
                    "axes": [
                        {"name": one.name, "levels": one.levels, "why": one.why}
                        for one in self.axes
                    ],
                    "themes": [
                        {"id": one.id, "name": one.name, "why": one.why} for one in self.themes
                    ],
                    "angles": [
                        {
                            "id": one.id,
                            "theme": one.theme,
                            "cell": one.cell,
                            "angle": one.angle,
                            "why_hard": one.why_hard,
                            "want": one.want,
                            "varies_by": one.varies_by,
                            "expects": one.expects,
                            "overlay": one.overlay,
                            "differs": one.differs,
                            "done": one.done,
                            "refused": one.refused,
                            "attempts": one.attempts,
                            "state": one.state,
                            "claimed_by": one.claimed_by,
                            "notes": one.notes,
                        }
                        for one in self.angles
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path


def load(destination: Path) -> Canvas:
    """The canvas on disk, or an empty one. A damaged file is not worth stopping a run over."""
    path = Path(destination) / "blueprint.json"
    if not path.exists():
        return Canvas()
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        found = Canvas(
            target=int(held.get("target") or 0),
            ceiling=str(held.get("ceiling") or ""),
            axes=[
                StateAxis(
                    name=str(one.get("name") or ""),
                    levels=list(one.get("levels") or []),
                    why=str(one.get("why") or ""),
                )
                for one in held.get("axes") or []
                if one.get("name")
            ],
            themes=[
                Theme(
                    id=str(one.get("id") or ""),
                    name=str(one.get("name") or ""),
                    why=str(one.get("why") or ""),
                )
                for one in held.get("themes") or []
                if one.get("id")
            ],
            angles=[
                Angle(
                    id=str(one.get("id") or ""),
                    theme=str(one.get("theme") or ""),
                    cell=str(one.get("cell") or ""),
                    angle=str(one.get("angle") or ""),
                    why_hard=str(one.get("why_hard") or ""),
                    want=max(1, int(one.get("want") or 1)),
                    varies_by=list(one.get("varies_by") or []),
                    expects=str(one.get("expects") or ""),
                    overlay=str(one.get("overlay") or ""),
                    differs=str(one.get("differs") or ""),
                    done=int(one.get("done") or 0),
                    refused=int(one.get("refused") or 0),
                    attempts=int(one.get("attempts") or 0),
                    state=str(one.get("state") or "open"),
                    claimed_by=str(one.get("claimed_by") or ""),
                    notes=list(one.get("notes") or []),
                )
                for one in held.get("angles") or []
                if one.get("id")
            ],
        )
        # Anything still claimed was claimed by a run that is over.
        found.reclaim()
        return found
    except Exception:
        return Canvas()
