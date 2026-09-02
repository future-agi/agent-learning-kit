"""The plan for a suite, written before any scenario is.

Asking a model for a thousand scenarios in one context does not work, and asking it for a
thousand one at a time converges: each scenario is written with the last few in view, so the
suite drifts toward whatever the first few were. The way out is to decide what all N are before
writing any of them, cheaply enough that all N fit in one head at once.

That is what a blueprint is, and the level it is pitched at is the whole design. It says *what is
worth testing*, never *how it resolves*. An angle is a short phrase, not a script: "surge boundary
confusion", not "charged 2.3x, receipt shows the higher rate, agent explains the window closed at
19:00". The second is the scenario with the code removed, and writing it in the plan costs the
plan its reason to exist.

Measured, when the level slipped: situations averaged 179 characters, so a plan for a thousand
scenarios would be 228KB and the model would have to emit 57k tokens in one response. It cannot.
At a short angle and a count it is a few thousand tokens for the same thousand scenarios, because
one angle carries several.

So the plan owns coverage and spread; the writer owns specifics. Asking the plan for specifics is
what breaks it, and the specifics are better decided with the agent's source open anyway.

The grid supplies the skeleton and cannot supply this. A grid of 39 cells asked for 1000
scenarios gives 26 per cell, and coordinates alone make those 26 identical. What separates them
is situational: what the person actually wants, what is in the way, what the agent has to notice.
Dial settings were the earlier answer and are not counted as variety, because the same situation
told by a different persona is the same test.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# Two situations sharing this much of their vocabulary are the same situation wearing different
# words. Set from the observed gap: rewordings of one situation ran past 0.7, genuinely different
# situations in the same cell sat well below it.
TOO_ALIKE = 0.7

# Below this there is nothing to plan; the writing stage handles small suites directly.
WORTH_PLANNING = 20

# An angle past this length has stopped being an angle. Set from the run where it slipped:
# situations averaged 179 characters and a thousand of them would not fit anywhere.
MOST_ANGLE_CHARS = 90

_WORD = re.compile(r"[a-z0-9]+")
# Carried by nearly every line in a suite, so they say nothing about whether two differ.
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
    """How much two situations share, as a fraction of the smaller one.

    Against the smaller rather than the union, because a one-line situation and a padded
    restatement of it are the same situation, and union would score that pair as different
    purely because one of them used more words.
    """
    if not one or not two:
        return 0.0
    return len(one & two) / min(len(one), len(two))


@dataclass
class Entry:
    """One angle on one cell, and how many scenarios to write from it."""

    cell: str
    angle: str
    count: int = 1

    def line(self) -> str:
        return f"{self.cell} | {self.angle}" + (f" | x{self.count}" if self.count != 1 else "")


@dataclass
class Blueprint:
    """Every scenario a suite intends to contain, before any of them exist."""

    entries: list[Entry] = field(default_factory=list)
    wanted: int = 0
    # Why the plan stops short of what was asked for, when it does. Empty while the planner is
    # still adding. This is the honest answer to a request for more scenarios than an agent has
    # distinct things worth testing, and it is a last resort rather than an early exit: the point
    # is to meet the number asked for, and to say so plainly when meeting it would mean padding.
    ceiling: str = ""

    @property
    def covered(self) -> set[str]:
        return {one.cell for one in self.entries}

    @property
    def scenarios(self) -> int:
        """How many scenarios this plan asks for, which is not how many lines it has."""
        return sum(max(1, one.count) for one in self.entries)

    def problems(self, cells: set[str]) -> list[str]:
        """What is wrong with this plan, said once, before a writer acts on any of it.

        Everything here is cheaper to catch now than after the scenarios exist: a plan that
        repeats itself becomes a suite that repeats itself, and by then each duplicate has cost
        a proof.
        """
        found: list[str] = []
        if not self.entries:
            return ["the blueprint is empty"]

        unknown = sorted({one.cell for one in self.entries} - cells)
        if unknown:
            found.append(
                f"{len(unknown)} entries name a cell that is not on the grid: "
                + ", ".join(unknown[:8])
                + ". Use show_grid, or correct the grid with set_objects if it is the grid that "
                "is wrong."
            )

        thin = [one.angle for one in self.entries if len(_words(one.angle)) < 2]
        if thin:
            found.append(
                f"{len(thin)} angles say too little to write from: "
                + ", ".join(thin[:8])
                + ". An angle names what makes a case worth testing, in a few words."
            )
        # An angle is a phrase. Past this it has stopped naming what to test and started
        # scripting how it goes, which is the writer's decision and does not fit at scale.
        wordy = [one.angle for one in self.entries if len(one.angle) > MOST_ANGLE_CHARS]
        if wordy:
            found.append(
                f"{len(wordy)} angles are written as scripts rather than angles: "
                + "; ".join(one[:60] + "..." for one in wordy[:4])
                + f". Keep an angle under {MOST_ANGLE_CHARS} characters and leave the "
                "particulars to whoever writes it, with the source in front of them."
            )

        alike = self.duplicates()
        if alike:
            found.append(
                f"{len(alike)} pair{'s' if len(alike) != 1 else ''} describe the same "
                "angle in different words: "
                + "; ".join(f"{one} / {two}" for one, two, _ in alike[:6])
            )
        return found

    def duplicates(self) -> list[tuple[str, str, float]]:
        """Pairs too alike to be worth writing twice, compared only inside a cell.

        Two cells can legitimately share a situation: retrieving a booking and cancelling one
        both start from a caller who cannot find it. Comparing across cells would report those
        as duplicates and push the plan toward making cells artificially unlike each other.
        """
        by_cell: dict[str, list[Entry]] = {}
        for one in self.entries:
            by_cell.setdefault(one.cell, []).append(one)

        found: list[tuple[str, str, float]] = []
        for group in by_cell.values():
            seen = [(one, _words(one.angle)) for one in group]
            for index, (one, words) in enumerate(seen):
                for other, other_words in seen[index + 1 :]:
                    score = _overlap(words, other_words)
                    if score >= TOO_ALIKE:
                        found.append((one.angle, other.angle, round(score, 2)))
        return sorted(found, key=lambda row: -row[2])

    def shortfall(self) -> int:
        return max(0, self.wanted - self.scenarios)

    def honest(self) -> str:
        """What to say about a plan that came in under target, if anything.

        A suite short of its number is not automatically wrong. An agent with six tools and one
        collection does not have a thousand distinct things worth testing, and inventing the
        difference produces a thousand rows that look like coverage and are not. But this is the
        last resort and not the first move: a planner that stops at a hundred because a hundred
        was easy has failed at the actual job.
        """
        if not self.shortfall():
            return ""
        if not self.ceiling:
            return (
                f"{self.scenarios} planned against {self.wanted} asked for, with no reason "
                "given. Keep planning. Only if you genuinely cannot find another distinct "
                "situation worth running, record the plan again with `ceiling` saying what you "
                "exhausted and what you would need to go further."
            )
        return (
            f"{self.scenarios} of the {self.wanted} asked for. More would be the same tests "
            f"under different names, so the honest number is {self.scenarios}: {self.ceiling}"
        )

    def slices(self, size: int) -> list[list[Entry]]:
        """The blueprint cut into pieces a writer can hold, dealt so no writer gets one cell.

        Round-robin rather than contiguous: the entries arrive grouped by cell, and a contiguous
        cut hands one writer every scenario for one cell. That writer then has the whole of a
        cell's variety to invent alone, which is the position the blueprint exists to avoid.
        """
        if size < 1:
            return [list(self.entries)]
        count = max(1, (len(self.entries) + size - 1) // size)
        dealt: list[list[Entry]] = [[] for _ in range(count)]
        for index, one in enumerate(self.entries):
            dealt[index % count].append(one)
        return [one for one in dealt if one]

    def written(self, destination: Path) -> Path:
        path = Path(destination) / "blueprint.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "wanted": self.wanted,
                    "ceiling": self.ceiling,
                    "entries": [
                        {"cell": one.cell, "angle": one.angle, "count": one.count}
                        for one in self.entries
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path


def load(destination: Path) -> Blueprint:
    """The blueprint on disk, or an empty one. A missing or damaged file is not fatal.

    A plan is worth redoing; it is never worth stopping a run over, and the stage that reads this
    can always write a new one.
    """
    path = Path(destination) / "blueprint.json"
    if not path.exists():
        return Blueprint()
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
        return Blueprint(
            wanted=int(held.get("wanted") or 0),
            ceiling=str(held.get("ceiling") or ""),
            entries=[
                Entry(
                    cell=str(one.get("cell") or ""),
                    angle=str(one.get("angle") or ""),
                    count=int(one.get("count") or 1),
                )
                for one in held.get("entries") or []
                if one.get("cell")
            ],
        )
    except Exception:
        return Blueprint()
