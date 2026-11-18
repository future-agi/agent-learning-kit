"""The supported-frameworks list: the frameworks a user can bring an agent in, and how far each is
proven.

The list is data, `data/framework_support.json` beside this file, so that the platform, which doesn't
import the kit, can copy the file. This loader reads it, and `docs/frameworks/supported.md` is
generated from it (`markdown_page()`); a test keeps the page equal to the file. Each real run is
described in a record in `data/framework_evidence/`, named by the run's date. `fi.simulate.supported_frameworks()`
(the simulate SDK's adapter names) is a different thing and is never this list.

Schema `futureagi.framework-support.v1`. Changes are additive, a reader ignores fields it doesn't
know, and a rename or removal needs a new schema version agreed with the platform first.

One entry per framework, keyed by the reader's ten words (`scan_document.FRAMEWORK_NAMES`):
`key`, `name`, `package`, `tested_versions`, `pinned_versions`, `python`, `reference` (the
reference agent's folder), `layers` and `limits` (plain sentences).

- `tested_versions`: exact `package==version` strings a real run of the reference agent used (a
  run-layer real run on a kit commit merged here, or a CI row that runs the agent). A reading doesn't start the framework, so it
  doesn't make a version tested.
- `pinned_versions`: the entry's package as pinned in the reference's `requirements.txt` (empty
  with no reference).

`layers` has `found`, `read`, `run` and `platform`, each `{status, evidence, note}`:

- **found**: the reader names the framework from the code (its tests are proof);
- **read**: a real reading described the reference agent correctly;
- **run**: the agent ran from that reading; `run.checks` has one result per check of the plan
  (`start`, `two_turn`, `tool_binding`, `evidence`, `stop`: `passed`, `failed` or `not_run`), and it
  is proven only when all five passed. A check has a result only from a real run on a kit commit
  merged here; a run on no single commit is described in the note instead;
- **platform**: connected through the platform's own screens and saved as Agent and version 1.

Status is `proven`, `pending` (the note says what it waits on) or `not_proven`. Evidence is a list
of `{kind, ref, date}`, kind `test` (a test id) or `real_run` (a record and its date, which is the
date in the record's name). A `real_run` item also has `model` (the reading model, as the run's logs
name it), `result` (`pass` or `miss`) and `kit` (the kit commit it ran on, or null when no single
commit holds what ran). A proven layer has evidence. Read, run and platform are proven only by a
passing real run on a kit commit merged here that holds the reference agent: a real model on a real
reference agent, never stand-in modules.

A proof holds only while the reference folder is unchanged. A proving `real_run` item carries
`reference_sha256`, the hash of the folder as the run used it (computed from the folder at its
`kit` commit, and stated on its own line in the run's record), and a layer is proven only when that
equals the folder's hash in the checkout. A pull request that changes a reference either records a
new run of the changed folder or moves the layer to `pending`, with a note that the reference
changed after its last reading; an earlier record is never edited to match.

`reference_sha256(folder)` computes it from exactly the files git tracks in the folder, dotfiles
such as `.python-version` included and untracked leftovers such as `__pycache__` not: for each one,
in sorted order of its path relative to the folder (with `/`), it hashes `f` (or `l` for a link,
whose target is hashed and not followed), the path, a NUL byte, the byte count in decimal, a NUL
byte and the bytes. To recompute it by hand for a commit, check the commit out and run the
function on the folder. A run on an agent outside the kit leaves its layer
pending until the kit holds a reference agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "futureagi.framework-support.v1"
LAYERS = ("found", "read", "run", "platform")
STATUSES = ("proven", "pending", "not_proven")
CHECKS = ("start", "two_turn", "tool_binding", "evidence", "stop")
RESULTS = ("passed", "failed", "not_run")
EVIDENCE_KINDS = ("test", "real_run")
DATA = Path(__file__).resolve().parent / "data" / "framework_support.json"
# The docs pages' frontmatter kind; the page is a table generated from data, with no example to run.
DOCS_PAGE_KIND = "agent-learning.docs-page.v1"

_STATUS_WORDS = {"proven": "proven", "pending": "pending", "not_proven": "-"}
_RESULT_WORDS = {"passed": "passed", "failed": "failed", "not_run": "-"}
# A page's word for the highest layer proven.
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


def reference_sha256(folder: Path) -> str:
    """The hash of a reference folder's tracked files, as the module docstring describes."""
    if not folder.is_dir():
        raise FileNotFoundError(folder)
    listed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=folder,
        capture_output=True,
        check=True,
    ).stdout
    paths = sorted(path for path in listed.decode("utf-8").split("\0") if path)
    if not paths:
        raise FileNotFoundError(f"no tracked files in {folder}")
    digest = hashlib.sha256()
    for path in paths:
        file = folder / path
        if file.is_symlink():
            kind, data = b"l", os.readlink(file).encode("utf-8")
        else:
            kind, data = b"f", file.read_bytes()
        digest.update(
            kind + path.encode("utf-8") + b"\0" + str(len(data)).encode() + b"\0"
        )
        digest.update(data)
    return digest.hexdigest()


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
        "---",
        f"kind: {DOCS_PAGE_KIND}",
        "track: frameworks",
        "backing: []",
        "artifact_kinds: []",
        "claims: []",
        "---",
        "",
        "# Supported frameworks",
        "",
        "Generated from `src/fi/alk/harness/data/framework_support.json`; a test keeps this "
        "page equal to it.",
        "",
        "- **Found:** the kit names the framework from the code.",
        "- **Read:** a real reading described the reference agent correctly.",
        "- **Run:** the agent ran from that reading, with five checks: start, a two-turn "
        "session, tool binding, evidence, stop. It is proven only when all five passed.",
        "- **Platform:** connected through the platform's own screens and saved as Agent and "
        "version 1.",
        "- **Versions** are the reference agent's own environment, not the kit's.",
        (
            "- **A proof holds only while the reference agent is unchanged:** each proving run "
            "records the hash of the agent's folder, and a test refuses the proof once the folder "
            "differs, until a new run."
        ),
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
