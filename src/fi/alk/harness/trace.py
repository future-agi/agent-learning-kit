"""What a stage actually spent its turns on.

A turn count says a run was expensive and nothing about why, and the difference matters: a stage
that spends sixty turns writing scenarios is working, and one that spends them re-reading the
same file is not. Reconstructing that afterwards from a rendered log is possible and horrible,
so it is recorded as the run goes.

What it answers, in the order the answers are usually needed:

  where the turns went     calls by tool, so a stage stuck in one place is obvious
  what was repeated        identical calls made more than once, which is pure waste
  what failed              calls whose result came back an error
  what it cost per result  calls spent between one artifact being produced and the next

Measured on two runs of the same ten-scenario suite: 57% and 60% of all calls were byte-identical
repeats of an earlier call, one source file was read eighteen times, and the expensive run spent
seventy-one calls before its first scenario was accepted against thirty-one for the cheap one.
None of that was visible in the turn count.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# A result longer than this is summarised rather than stored. The trace is for shape, and a
# stage that reads a large file should not produce a trace the size of the file.
MOST_RESULT_CHARS = 400


@dataclass
class Call:
    """One tool call and how it turned out."""

    tool: str
    target: str = ""
    failed: bool = False
    result: str = ""
    # Index of the earlier call this one is identical to, when it is a repeat.
    repeat_of: int | None = None

    @property
    def key(self) -> str:
        return f"{self.tool}|{self.target}"


@dataclass
class Trace:
    """Every call a stage made, in order, with the repeats marked."""

    name: str = ""
    calls: list[Call] = field(default_factory=list)
    # Where in the call sequence each named artifact appeared, so cost per result is recoverable.
    produced: list[tuple[int, str]] = field(default_factory=list)
    turns: int = 0
    cost_usd: float = 0.0

    def record(self, event: Any) -> None:
        """Take one stage event. Unknown kinds are ignored rather than guessed at."""
        kind = getattr(event, "kind", "")
        if kind == "tool":
            target = str((getattr(event, "detail", None) or {}).get("target") or "")
            call = Call(tool=str(getattr(event, "tool", "")), target=target)
            seen = self._first(call.key)
            if seen is not None:
                call.repeat_of = seen
            self.calls.append(call)
        elif kind == "result" and self.calls:
            detail = getattr(event, "detail", None) or {}
            text = str(getattr(event, "text", "") or "")
            self.calls[-1].failed = bool(detail.get("is_error"))
            self.calls[-1].result = text[:MOST_RESULT_CHARS]
        elif kind == "artifact":
            path = str((getattr(event, "detail", None) or {}).get("path") or "")
            self.produced.append((len(self.calls), path))
        elif kind == "done":
            detail = getattr(event, "detail", None) or {}
            self.turns = int(detail.get("turns") or 0)
            cost = detail.get("cost_usd")
            self.cost_usd = float(cost) if isinstance(cost, (int, float)) else 0.0

    def _first(self, key: str) -> int | None:
        for index, one in enumerate(self.calls):
            if one.key == key:
                return index
        return None

    @property
    def repeated(self) -> int:
        """Calls that were byte-identical to an earlier one, so bought nothing."""
        return sum(1 for one in self.calls if one.repeat_of is not None)

    @property
    def failures(self) -> int:
        return sum(1 for one in self.calls if one.failed)

    def worst_repeats(self, most: int = 5) -> list[tuple[str, int]]:
        counts = Counter(one.key for one in self.calls if one.repeat_of is not None)
        return counts.most_common(most)

    def summary(self) -> str:
        """The shape of the run, for a person reading it after the fact."""
        if not self.calls:
            return f"{self.name}: no calls recorded"
        by_tool = Counter(one.tool for one in self.calls)
        share = 100 * self.repeated // len(self.calls)
        lines = [
            f"{self.name}: {self.turns} turns, {len(self.calls)} calls, "
            f"{self.repeated} of them repeats ({share}%), {self.failures} failed",
            "  " + ", ".join(f"{name} {n}" for name, n in by_tool.most_common(8)),
        ]
        worst = self.worst_repeats()
        if worst:
            lines.append("  repeated most: " + "; ".join(f"{key} x{n + 1}" for key, n in worst))
        if self.produced:
            last = 0
            spans = []
            for at, what in self.produced:
                spans.append(f"{at - last}->{Path(what).name or what}")
                last = at
            lines.append("  calls per result: " + ", ".join(spans[:10]))
        return "\n".join(lines)

    def write(self, destination: str | Path) -> Path:
        """The whole trace beside the artifacts it produced."""
        path = Path(destination) / "stage-trace.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "stage": self.name,
                    "turns": self.turns,
                    "cost_usd": self.cost_usd,
                    "calls": [
                        {
                            "tool": one.tool,
                            "target": one.target,
                            "failed": one.failed,
                            "repeat_of": one.repeat_of,
                        }
                        for one in self.calls
                    ],
                    "produced": [{"after_calls": at, "path": what} for at, what in self.produced],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return path
