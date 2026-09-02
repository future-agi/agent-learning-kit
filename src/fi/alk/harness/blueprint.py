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
# one differing word swings the ratio hard, so ``facet`` is what dedup really keys on.
TOO_ALIKE = 0.7

# Below this there is nothing to plan; the writing stage handles small suites directly.
WORTH_PLANNING = 20

# An angle past this has stopped naming what to test and started scripting how it goes.
MOST_ANGLE_CHARS = 90

# Dispatches spent on one angle before it is called blocked rather than merely unlucky. Three,
# because the second attempt usually goes to a writer not carrying the first one's assumptions,
# and a third failure is evidence rather than noise.
MOST_ATTEMPTS = 3

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
    facet: str = ""
    want: int = 1
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
        held = f"{self.id} | {self.cell} | {self.angle} | x{self.want}"
        if self.facet:
            held += f" | {self.facet}"
        if self.done or self.state != "open":
            held += f" | {self.state} {self.done}/{self.want}"
        return held


@dataclass
class Canvas:
    """Every angle a suite intends to cover, and what has been written against each."""

    themes: list[Theme] = field(default_factory=list)
    angles: list[Angle] = field(default_factory=list)
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
        """Angles that may be one angle twice: same facet on one cell, or near-identical wording.

        Reported, never refused. The first real canvas produced seven of these and six were
        legitimate: three different input *forms* for one address, four different reasons for
        going out of scope. One was a genuine duplicate. A check that auto-rejected all seven
        would have been wrong six times, so this asks rather than decides.
        """
        found: list[tuple[str, str, str]] = []
        seen: dict[tuple[str, str], str] = {}
        for one in self.angles:
            if not one.facet:
                continue
            key = (one.cell, one.facet)
            if key in seen:
                found.append((seen[key], one.id, f"same facet {one.facet!r} on {one.cell}"))
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
        """Put back angles whose writer never returned. A crashed writer must not park one."""
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
                    "themes": [
                        {"id": one.id, "name": one.name, "why": one.why} for one in self.themes
                    ],
                    "angles": [
                        {
                            "id": one.id,
                            "theme": one.theme,
                            "cell": one.cell,
                            "angle": one.angle,
                            "facet": one.facet,
                            "want": one.want,
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
        return Canvas(
            target=int(held.get("target") or 0),
            ceiling=str(held.get("ceiling") or ""),
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
                    facet=str(one.get("facet") or ""),
                    want=max(1, int(one.get("want") or 1)),
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
    except Exception:
        return Canvas()
