"""How varied a suite actually is, past the point where reading it is possible.

Fifty scenarios can be read. Five hundred cannot, and the failure mode at that size is not
scenarios that are obviously wrong but scenarios that are quietly the same: the suite grows, the
count looks like progress, and the number of distinct things it would catch stopped rising a
hundred rows ago. Nothing in the pipeline noticed that, because every one of those rows passed
all three gates on its own merits.

So this reports the shape of a suite rather than its size. What it measures:

  spread        how evenly the scenarios fall across the cells they claim to cover
  repetition    pairs whose situations are near enough to be one test written twice
  people        distinct callers, accents, locations
  work          how much the agent has to do, as solution length

It is deliberately lexical. Two scenarios describing one situation in entirely different words
will not be caught here, and calling this a semantic measure would overstate it: it catches
rewordings and near-copies, which is what a suite actually accumulates. A real semantic measure
needs embeddings and is a separate thing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from statistics import median

from .scenariogen.plan.canvas import TOO_ALIKE, _overlap, _words
from .scenariogen.model.scenario import Scenario


@dataclass
class Report:
    """What a suite looks like from far enough back to see all of it."""

    total: int = 0
    cells: Counter = field(default_factory=Counter)
    names: Counter = field(default_factory=Counter)
    accents: Counter = field(default_factory=Counter)
    locations: Counter = field(default_factory=Counter)
    steps: list[int] = field(default_factory=list)
    alike: list[tuple[str, str, float]] = field(default_factory=list)

    @property
    def busiest(self) -> int:
        """How many scenarios sit on the most crowded cell."""
        return self.cells.most_common(1)[0][1] if self.cells else 0

    def concerns(self) -> list[str]:
        """What a person should look at, in the order it matters. Silence is a good suite."""
        found: list[str] = []
        if self.alike:
            found.append(
                f"{len(self.alike)} pairs read as the same test twice: "
                + "; ".join(f"{one}/{two}" for one, two, _ in self.alike[:6])
            )
        # A suite piling onto one cell has stopped covering and started repeating, and the count
        # hides it: the total still climbs while the number of distinct things tested does not.
        if self.total >= 20 and self.busiest > max(3, self.total // 4):
            cell, count = self.cells.most_common(1)[0]
            found.append(
                f"{count} of {self.total} scenarios sit on {cell}, which is a suite about one "
                "thing wearing the shape of a broad one"
            )
        for what, held in (("caller names", self.names), ("locations", self.locations)):
            if self.total >= 8 and held:
                top, count = held.most_common(1)[0]
                if count > max(2, self.total // 5):
                    found.append(f"{top!r} is {count} of {self.total} {what}")
        if self.total >= 8 and len(self.accents) == 1:
            found.append(
                f"every caller has the same accent ({next(iter(self.accents))}), so the agent's "
                "speech handling is never tested"
            )
        return found

    def rendered(self) -> str:
        lines = [
            f"{self.total} scenarios over {len(self.cells)} cells.",
            f"  people: {len(self.names)} names, {len(self.accents)} accents, "
            f"{len(self.locations)} locations",
        ]
        if self.steps:
            lines.append(
                f"  solution steps: median {median(self.steps):.0f}, longest {max(self.steps)}"
            )
        if self.cells:
            crowded = ", ".join(f"{name} x{n}" for name, n in self.cells.most_common(5))
            lines.append(f"  most covered: {crowded}")
        concerns = self.concerns()
        lines.append("")
        lines += [f"  {one}" for one in concerns] if concerns else ["  nothing stands out"]
        return "\n".join(lines)


def _cell_of(scenario: Scenario) -> str:
    """The coordinate a scenario claims, which its name carries before the dial suffix."""
    return scenario.name.split("__", 1)[0]


def measure(scenarios: list[Scenario]) -> Report:
    """Read a whole suite and say what shape it is."""
    report = Report(total=len(scenarios))
    for one in scenarios:
        report.cells[_cell_of(one)] += 1
        report.steps.append(len(one.solution or []))
        persona = one.persona
        if persona:
            for held, value in (
                (report.names, getattr(persona, "name", "")),
                (report.accents, getattr(persona, "accent", "")),
                (report.locations, getattr(persona, "location", "")),
            ):
                if value and str(value).strip():
                    held[str(value).strip()] += 1

    # Compared within a cell, for the reason the blueprint compares within one: two cells sharing
    # a situation is legitimate, and flagging it would push a suite into making its cells
    # artificially unlike each other.
    by_cell: dict[str, list[Scenario]] = {}
    for one in scenarios:
        by_cell.setdefault(_cell_of(one), []).append(one)
    for group in by_cell.values():
        seen = [(one, _words(f"{one.tests} {one.instruction}")) for one in group]
        for index, (one, words) in enumerate(seen):
            for other, other_words in seen[index + 1 :]:
                score = _overlap(words, other_words)
                if score >= TOO_ALIKE:
                    report.alike.append((one.name, other.name, round(score, 2)))
    report.alike.sort(key=lambda row: -row[2])
    return report
