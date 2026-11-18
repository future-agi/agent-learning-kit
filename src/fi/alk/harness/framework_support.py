"""The supported-frameworks list: the frameworks a user can bring an agent in, and how far each is
proven (decision record ADR-006).

The list is data, `data/framework_support.json` beside this file, because the platform doesn't
import the kit and copies the file. This loader reads it, `agent-learn capabilities` carries it
as `framework_support`, and `docs/frameworks/supported.md` is generated from it
(`markdown_page()`); a test keeps the page equal to the file. `fi.simulate.supported_frameworks()`
(the simulate SDK's adapter names) is a different thing and is never this list.

Schema `futureagi.framework-support.v1`. Changes are additive, a reader ignores fields it doesn't
know, and a rename or removal needs a new schema version agreed with the platform first.

One entry per framework, keyed by the reader's ten words (`scan_document.FRAMEWORK_NAMES`):
`key`, `name`, `package`, `tested_versions` (exact `package==version`, the reference agent's own
environment, each equal to a pin in its `requirements.txt` or a CI row), `python`, `reference`
(the reference agent's folder), `layers` and `limits` (plain sentences).

`layers` has `found`, `read`, `run` and `platform`, each `{status, evidence, note}`:

- **found**: the reader names the framework from the code (its tests are proof);
- **read**: a real reading described the reference agent correctly;
- **run**: the agent ran from that reading; `run.checks` has one result per check of the plan
  (`start`, `two_turn`, `tool_binding`, `evidence`, `stop`: `passed`, `failed` or `not_run`), and it
  is proven only when all five passed;
- **platform**: connected through the platform's own screens and saved as Agent and version 1.

Status is `proven`, `pending` (the note says what it waits on) or `not_proven`. Evidence is a list
of `{kind, ref, date}`, kind `test` (a test id) or `real_run` (a record and its date). Read and run
are proven only by a real run: a real model on a real reference agent, never stand-in modules.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "futureagi.framework-support.v1"
LAYERS = ("found", "read", "run", "platform")
STATUSES = ("proven", "pending", "not_proven")
CHECKS = ("start", "two_turn", "tool_binding", "evidence", "stop")
RESULTS = ("passed", "failed", "not_run")
EVIDENCE_KINDS = ("test", "real_run")
DATA = Path(__file__).resolve().parent / "data" / "framework_support.json"

_STATUS_WORDS = {"proven": "proven", "pending": "pending", "not_proven": "-"}
_RESULT_WORDS = {"passed": "passed", "failed": "failed", "not_run": "-"}
# A page's word for the highest layer proven (ADR-006's proposal; Khushal may change them).
_LEVEL_WORDS = {
    None: "Not recognised",
    "found": "Recognised, not yet tested",
    "read": "Read, not yet tested",
    "run": "Tested",
    "platform": "Tested through the platform",
}


def load(path: Path = DATA) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"unknown framework-support schema: {document.get('schema_version')!r}"
        )
    return document


def frameworks(path: Path = DATA) -> list[dict[str, Any]]:
    return load(path)["frameworks"]


def level_reached(entry: dict[str, Any]) -> str | None:
    """The highest layer proven, counting only an unbroken run of proven layers from `found`."""
    reached = None
    for layer in LAYERS:
        if entry["layers"][layer]["status"] != "proven":
            break
        reached = layer
    return reached


def markdown_page(path: Path = DATA) -> str:
    """`docs/frameworks/supported.md`, generated from the list."""
    entries = frameworks(path)
    lines = [
        "# Supported frameworks",
        "",
        "Generated from `src/fi/alk/harness/data/framework_support.json` (decision record "
        "ADR-006); a test keeps this page equal to it, and `agent-learn capabilities` carries "
        "the file as `framework_support`.",
        "",
        "- **Found:** the kit names the framework from the code.",
        "- **Read:** a real reading described the reference agent correctly.",
        "- **Run:** the agent ran from that reading, with five checks: start, a two-turn "
        "session, tool binding, evidence, stop. It is proven only when all five passed.",
        "- **Platform:** connected through the platform's own screens and saved as Agent and "
        "version 1.",
        "- **Versions** are the reference agent's own environment, not the kit's.",
        "",
        "| Framework | Level | Versions | Python | Found | Read | Run | Platform "
        "| Start | Two turns | Tools bound | Evidence | Stop |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in entries:
        layers = entry["layers"]
        statuses = [_STATUS_WORDS[layers[layer]["status"]] for layer in LAYERS]
        checks = [_RESULT_WORDS[layers["run"]["checks"][check]] for check in CHECKS]
        versions = ", ".join(f"`{v}`" for v in entry["tested_versions"]) or "none"
        lines.append(
            f"| {entry['name']} | {_LEVEL_WORDS[level_reached(entry)]} | {versions} "
            f"| {entry['python'] or '-'} | " + " | ".join(statuses + checks) + " |"
        )
    lines += ["", "## Notes and limits", ""]
    for entry in entries:
        for layer in LAYERS:
            note = entry["layers"][layer]["note"]
            if note and layer != "platform":
                lines.append(f"- **{entry['name']}, {layer}:** {note}")
        for limit in entry["limits"]:
            lines.append(f"- **{entry['name']}:** {limit}")
    return "\n".join(lines) + "\n"
