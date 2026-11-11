"""Stages that stand in for the save checks in the save checker's child.

A patch in the test's own process can't reach the child, so a test names this module and the
child swaps these in after its trial. Each one runs the real check unless told otherwise:

- a save's stages read what to do from the contract's ``notes``, as ``<stage>:<action>[:<arg>]``
  words. The ``contract`` stage runs first in every save, so it keeps the words for the rest.
  Actions: ``sleep`` (``arg`` seconds, past the deadline by default), ``refuse``, ``raise``,
  ``exit`` (the child ends), and ``wait`` (writes ``.<stage>-running`` in the source root, then
  waits for ``.<stage>-go`` there, within the deadline);
- the session's two parts read ``save-stand-in.json`` at the root (instrumentation looks in the
  folder it is given and up to three folders above it). ``"scan"``: ``"raise"``, ``"cut"``,
  ``"real"``, or ``{"after": <file name>, "sleep": <seconds>}``, which writes ``.scan-waiting``,
  waits for that file at the root, then sleeps. A scan told anything writes ``.scan-done`` once
  it answers. ``"instrumentation"``: ``"raise"``, ``{"hold": <seconds>}``, or
  ``{"value": <str>}``.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fi.alk.harness import instrumentation_evidence, source_tool_evidence, tools

PAST_THE_DEADLINE = 3.0
SESSION_FILE = "save-stand-in.json"
# Long enough for any test's signal, and short of the child's session bound.
_LONGEST_WAIT = 20.0

_real_scan = tools._tools_in_agent_folder
_real_instrumentation = instrumentation_evidence.detect_instrumentation
_real_validate = tools.validate_contract
_real_tool_evidence = source_tool_evidence.tool_evidence_problems
_real_copy = tools._copy_prompt
_real_model = tools.model_problems
_real_sibling = tools._builds_a_sibling

# The words of the save now running; the child runs one save at a time.
_said: dict[str, tuple[str, str]] = {}


def _words(notes: str) -> dict[str, tuple[str, str]]:
    said = {}
    for word in notes.split():
        stage, _, rest = word.partition(":")
        action, _, arg = rest.partition(":")
        if stage and action:
            said[stage] = (action, arg)
    return said


def _act(stage: str, root: Path | None) -> str | None:
    """Carry out what the save said for ``stage``; ``"refuse"`` when it should refuse."""
    action, arg = _said.get(stage, ("", ""))
    if action == "sleep":
        time.sleep(float(arg) if arg else PAST_THE_DEADLINE)
    elif action == "raise":
        raise RuntimeError(f"stand-in {stage} raised")
    elif action == "exit":
        os._exit(3)
    elif action == "wait" and root is not None:
        (root / f".{stage}-running").write_text("")
        go = root / f".{stage}-go"
        until = time.monotonic() + (float(arg) if arg else 1.2)
        while not go.exists() and time.monotonic() < until:
            time.sleep(0.005)
    elif action == "refuse":
        return "refuse"
    return None


def _session_said(root: Path | None, part: str) -> Any:
    folder = root
    for _ in range(4):
        if folder is None:
            return None
        found = folder / SESSION_FILE
        if found.is_file():
            return json.loads(found.read_text(encoding="utf-8")).get(part)
        folder = folder.parent
    return None


def scan(scan_root, subfolder, reached=None):
    said = _session_said(Path(scan_root) if scan_root else None, "scan")
    if said == "raise":
        raise OSError("stand-in scan raised")
    if said == "cut":
        return [], frozenset({"scan_total"})
    if isinstance(said, dict):
        # Said first, so a test starts its clock only once the session job is here.
        (Path(scan_root) / ".scan-waiting").write_text("")
        go = Path(scan_root) / said["after"]
        until = time.monotonic() + _LONGEST_WAIT
        while not go.exists() and time.monotonic() < until:
            time.sleep(0.005)
        time.sleep(float(said.get("sleep", 0)))
    answer = _real_scan(scan_root, subfolder, reached)
    if said is not None:
        (Path(scan_root) / ".scan-done").write_text("")
    return answer


def instrumentation(source_root, reached=()):
    said = _session_said(Path(source_root) if source_root else None, "instrumentation")
    if said == "raise":
        raise OSError("stand-in instrumentation raised")
    if isinstance(said, dict) and "hold" in said:
        time.sleep(float(said["hold"]))
    if isinstance(said, dict) and "value" in said:
        return str(said["value"])
    return _real_instrumentation(source_root, reached)


def validate(contract):
    _said.clear()
    _said.update(_words(contract.notes or ""))
    problems = _real_validate(contract)
    if _act("contract", None) == "refuse":
        problems.append("stand-in: contract refused")
    return problems


def tool_evidence(contract, source, subfolder=None):
    problems = _real_tool_evidence(contract, source, subfolder)
    if _act("tool_evidence", Path(source)) == "refuse":
        problems.append("stand-in: tool_evidence refused")
    return problems


def copy_prompt(contract, source_root):
    problems = _real_copy(contract, source_root)
    if _act("prompt_copy", Path(source_root)) == "refuse":
        problems.append("stand-in: prompt_copy refused")
    return problems


def model(contract, source_root):
    problems = _real_model(contract, source_root)
    if _act("model", Path(source_root)) == "refuse":
        problems.append("stand-in: model refused")
    return problems


def sibling(source_root, runtime, subfolder):
    refusal = _real_sibling(source_root, runtime, subfolder)
    if _act("runtime", Path(source_root)) == "refuse":
        return "stand-in: runtime refused"
    return refusal


STAGES = {
    ("tools", "_tools_in_agent_folder"): scan,
    ("instrumentation_evidence", "detect_instrumentation"): instrumentation,
    ("tools", "validate_contract"): validate,
    ("source_tool_evidence", "tool_evidence_problems"): tool_evidence,
    ("tools", "_copy_prompt"): copy_prompt,
    ("tools", "model_problems"): model,
    ("tools", "_builds_a_sibling"): sibling,
}
