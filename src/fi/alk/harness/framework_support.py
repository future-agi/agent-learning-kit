"""The supported-frameworks list: the frameworks a user can bring an agent in, and how far each is
proven (decision record ADR-006).

The list is data, `data/framework_support.json` beside this file, and this loader is its only
reader. `docs/frameworks/supported.md` is generated from it (`markdown_page()`), and a test keeps
the two equal. `fi.simulate.supported_frameworks()` is a different thing (the simulate SDK's
adapter names) and is never this list.

One row per framework, keyed by the reader's ten words (`scan_document.FRAMEWORK_NAMES`):

- `name`, `title`, `package`;
- `level`, the highest reached, in order:
  - `recognised`: we name the framework from the code (the detector's tests);
  - `read`: a real reading described a reference agent correctly (the reading's record and date);
  - `runs`: the plan's five checks all passed from that reading (start, a two-turn session, tool
    binding, evidence, stop), one result each in `checks`;
  - `platform`: connected on the platform's own screens and saved as Agent and version 1;
- `tested_versions`: the exact `package==version` the reference agent ran on, not the kit's own
  range, each equal to a pin in its `requirements.txt` or a CI row;
- `python`, `reference` (the reference agent's folder), `checks` (`passed`, `failed`, `not_run`);
- `evidence`: per level, `recognised` as test ids, the others as `{date, place}`;
- `limits`: plain sentences on what is not yet shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LEVELS = ("recognised", "read", "runs", "platform")
CHECKS = ("start", "two_turn_session", "tool_binding", "evidence", "stop")
RESULTS = ("passed", "failed", "not_run")
DATA = Path(__file__).resolve().parent / "data" / "framework_support.json"

# The words a page shows for each level (ADR-006's proposal; Khushal may change them).
LEVEL_WORDS = {
    "recognised": "Recognised, not yet tested",
    "read": "Read, not yet tested",
    "runs": "Tested",
    "platform": "Tested through the platform",
}
_RESULT_MARKS = {"passed": "passed", "failed": "failed", "not_run": "-"}


@dataclass(frozen=True)
class Row:
    name: str
    title: str
    package: str
    level: str
    tested_versions: tuple[str, ...]
    python: str | None
    reference: str | None
    checks: dict[str, str]
    evidence: dict[str, Any]
    limits: tuple[str, ...]


def load(path: Path = DATA) -> tuple[Row, ...]:
    rows = []
    for raw in json.loads(path.read_text(encoding="utf-8"))["frameworks"]:
        rows.append(
            Row(
                name=raw["name"],
                title=raw["title"],
                package=raw["package"],
                level=raw["level"],
                tested_versions=tuple(raw["tested_versions"]),
                python=raw["python"],
                reference=raw["reference"],
                checks=dict(raw["checks"]),
                evidence=dict(raw["evidence"]),
                limits=tuple(raw["limits"]),
            )
        )
    return tuple(rows)


def records(path: Path = DATA) -> list[dict[str, Any]]:
    """The rows as plain data, for a copy the platform shows."""
    return json.loads(path.read_text(encoding="utf-8"))["frameworks"]


def markdown_page(rows: tuple[Row, ...] | None = None) -> str:
    """`docs/frameworks/supported.md`, generated from the list."""
    rows = load() if rows is None else rows
    lines = [
        "# Supported frameworks",
        "",
        "Generated from `src/fi/alk/harness/data/framework_support.json` (decision record "
        "ADR-006); a test keeps this page equal to it. Each framework is at the highest level its "
        "evidence reaches: Recognised (we name it from the code), Read (a real reading described "
        "a reference agent correctly), Runs (the five checks passed from that reading: start, a "
        "two-turn session, tool binding, evidence, stop), Through the platform (connected on the "
        "platform and saved as Agent and version 1). The versions are the reference agent's own, as "
        "its reading and run used them.",
        "",
        "| Framework | Level | Versions on record | Python | Start | Two turns | Tools bound "
        "| Evidence | Stop |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        marks = [_RESULT_MARKS[row.checks[check]] for check in CHECKS]
        versions = ", ".join(f"`{v}`" for v in row.tested_versions) or "none"
        lines.append(
            f"| {row.title} | {LEVEL_WORDS[row.level]} | {versions} | {row.python or '-'} | "
            + " | ".join(marks)
            + " |"
        )
    lines += ["", "## Known limits", ""]
    for row in rows:
        for limit in row.limits:
            lines.append(f"- **{row.title}:** {limit}")
    return "\n".join(lines) + "\n"
