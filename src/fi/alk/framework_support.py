"""The frameworks the harness can take an agent from, and what is proven for each.

The list itself is data, `src/fi/alk/data/framework_support.json`, so a new real reading updates
one entry and its evidence file without touching code. `docs/frameworks/supported.md` is checked
against `markdown_table()`, and `agent-learn capabilities` carries `as_records()` under
`framework_support`. The keys are the framework reader's own words
(`scan_document.FRAMEWORK_NAMES`).

Four layers, each with one status and the evidence for it:

- **found**: the reader detects the framework in a repository;
- **read**: a real reading by the model turned an agent built with it into its contract;
- **built**: the kit builds such an agent from a contract and binds its tools (a hand-written
  contract is enough here, which is why this is a separate claim from **read**);
- **run**: an agent read that way has run (the next owners' layer, kept separate).

Statuses: `proven`; `partial` (some of the layer is shown; the note says what is missing);
`pending` (proven on an open pull request, named in the evidence); `not_proven`.

Evidence is `test:<path>::<test>[<case>]`, `record:<path>#<heading>` or
`pr:future-agi/agent-learning-kit#<n> <path>::<test>`. A test checks that each one exists. A
real reading's record lives in `docs/frameworks/evidence/` and is cited by its `Result` heading.

`tested_version` is set only where cited evidence ran that version of the package. An example's
pin is `example_pin`, which on its own proves nothing. `notes` says anything else a reader of
the list should know about that framework.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from importlib import resources

STATUSES = ("proven", "partial", "pending", "not_proven")
LAYERS = ("found", "read", "built", "run")


@dataclass(frozen=True)
class Layer:
    status: str
    evidence: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class Framework:
    key: str
    name: str
    package: str
    tested_version: str | None
    tested_in: str
    example: str | None
    example_pin: str | None
    notes: str
    found: Layer
    read: Layer
    built: Layer
    run: Layer


def _load() -> tuple[Framework, ...]:
    text = (
        resources.files("fi.alk")
        .joinpath("data", "framework_support.json")
        .read_text(encoding="utf-8")
    )
    entries = []
    for raw in json.loads(text)["frameworks"]:
        layers = {
            layer: Layer(
                raw[layer]["status"], tuple(raw[layer]["evidence"]), raw[layer]["note"]
            )
            for layer in LAYERS
        }
        plain = {key: value for key, value in raw.items() if key not in LAYERS}
        entries.append(Framework(**plain, **layers))
    return tuple(entries)


FRAMEWORKS: tuple[Framework, ...] = _load()

_STATUS_WORDS = {
    "proven": "proven",
    "partial": "partly",
    "pending": "pending",
    "not_proven": "not proven",
}


def as_records() -> list[dict]:
    """The list as plain data, for `agent-learn capabilities` and the platform."""
    records = []
    for entry in FRAMEWORKS:
        record = asdict(entry)
        for layer in LAYERS:
            record[layer]["evidence"] = list(record[layer]["evidence"])
        records.append(record)
    return records


def markdown_table() -> str:
    """The table `docs/frameworks/supported.md` carries."""
    lines = [
        "| Framework | Package | Tested version | Found | Read | Built | Run |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in FRAMEWORKS:
        words = [_STATUS_WORDS[getattr(entry, layer).status] for layer in LAYERS]
        version = entry.tested_version or "none"
        lines.append(
            f"| {entry.name} | `{entry.package}` | {version} | "
            + " | ".join(words)
            + " |"
        )
    return "\n".join(lines) + "\n"
