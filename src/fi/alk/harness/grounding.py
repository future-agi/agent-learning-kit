"""Whether each source a scenario cites says what the scenario claims it says.

A proof does not block a case with no sources (the save path refuses a new or changed one, so a
guess is written as an `assumption` source). A source that does not resolve is blocking, because a false citation is worse than none. Chat messages and evidence cases
live on the platform and are resolved there.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

from .leaks import normalise

# Kinds whose resolution ALK decides; the rest are left to the platform.
ALK_RESOLVED_KINDS = frozenset({"rule", "file", "requirement", "assumption"})
_FILE_ROOTS = ("knowledge", "")
POLICY_CHECKS = ".futureagi-policy-checks.json"
logger = logging.getLogger(__name__)


def _file(world_root: Path, ref: str) -> Path | None:
    name = ref.removeprefix("file:").strip().lstrip("/")
    if not name or ".." in name.split("/"):
        return None
    for root in _FILE_ROOTS:
        candidate = Path(world_root) / root / name if root else Path(world_root) / name
        if candidate.is_file():
            return candidate
    return None


def resolve_sources(scenario: Any, contract: Any, world_root: Path) -> list[dict[str, Any]]:
    """One entry per cited source: `{kind, ref, resolved, problem, ...}`; `resolved` None means
    the platform decides it."""
    rules = list(getattr(contract, "hard_constraints", None) or []) if contract is not None else []
    resolved: list[dict[str, Any]] = []
    for source in getattr(scenario, "sources", None) or []:
        kind, ref, quote = source.kind, source.ref, source.quote
        entry: dict[str, Any] = {"kind": kind, "ref": ref, "resolved": True, "problem": ""}
        if kind == "rule":
            wanted = normalise(quote)
            matched = next((at for at, rule in enumerate(rules) if wanted and wanted in normalise(rule)), None)
            if matched is None:
                entry.update(resolved=False, problem="no rule of the agent contains the quoted words")
            else:
                entry["resolved_ref"] = f"rule:{matched}"
        elif kind == "file":
            path = _file(world_root, ref)
            if path is None:
                # Uploaded files may live only on the platform; it resolves them there.
                entry.update(resolved=None, problem="the file is not in the authoring tree")
            else:
                text = path.read_bytes()
                entry["matched_digest"] = "sha256:" + hashlib.sha256(text).hexdigest()
                if normalise(quote) not in normalise(text.decode("utf-8", errors="replace")):
                    entry.update(resolved=False, problem=f"{path.name} does not contain the quoted words")
        elif kind == "requirement":
            if not quote.strip():
                entry.update(resolved=False, problem="a requirement needs the person's own words")
        elif kind == "assumption":
            pass
        else:
            entry.update(resolved=None, problem="resolved on the platform")
        resolved.append(entry)
    return resolved


def unresolved(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The entries ALK resolved and found false."""
    return [one for one in entries if one["kind"] in ALK_RESOLVED_KINDS and one.get("resolved") is False]


def expected_outcome(scenario: Any, catalogue: Any) -> str:
    """What the case expects, in words: each of its sub-goals' `what`."""
    lines = []
    for name in getattr(scenario, "sub_goals", None) or []:
        goal = catalogue.named(name) if catalogue is not None else None
        what = str(getattr(goal, "what", "") or "").strip()
        if what:
            lines.append(f"- {what}")
    return "\n".join(lines)


_CONFLICT_PROMPT = (
    "A test case for an agent expects the outcome below, and cites the agent's rule below as its source. "
    "Decide whether the expected outcome contradicts the rule: expecting the agent to do what the rule "
    "forbids, or a number, limit, window or condition the rule states differently. A case that only "
    "covers part of the rule, or adds details the rule does not mention, does not contradict it.\n\n"
    "Expected outcome:\n{expected}\n\nRule:\n{rule}\n\nCall decide."
)


def _harness_conflict_judge(expected: str, rule: str) -> tuple[bool | None, str]:
    """One forced judge call at temperature 0: (conflict, why). None when no judge can run here."""
    from .backends import tool
    from .judge import NoGateway, _complete
    from .tools import schema

    @tool(
        "decide",
        "Whether the expected outcome contradicts the rule.",
        schema({"conflict": bool, "why": str}, ["conflict", "why"]),
    )
    async def decide(args: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - never executed
        return {}

    conversation = [{"role": "user", "content": _CONFLICT_PROMPT.format(expected=expected, rule=rule)}]
    try:
        message = _complete(conversation, [decide], True, temperature=0)
    except NoGateway:
        return None, ""
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        if function.get("name") != "decide":
            continue
        try:
            args = json.loads(function.get("arguments") or "{}")
        except ValueError:
            continue
        if isinstance(args.get("conflict"), bool):
            return args["conflict"], str(args.get("why") or "").strip()[:300]
    return None, ""


def _load_checks(world_root: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads((Path(world_root) / POLICY_CHECKS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _save_checks(world_root: Path, checks: dict[str, dict[str, Any]]) -> None:
    root = Path(world_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=root, prefix=".policy-checks-", suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            json.dump(checks, out, indent=2, sort_keys=True, ensure_ascii=False)
        os.replace(temporary, root / POLICY_CHECKS)
    except OSError as exc:  # a cache that cannot be written only costs a repeated call
        logger.warning("policy check cache not written: %s", exc)


def policy_conflicts(
    scenario: Any,
    catalogue: Any,
    rules: list[str],
    world_root: Path,
    *,
    judge: Callable[[str, str], tuple[bool | None, str]] | None = None,
) -> list[dict[str, Any]]:
    """For each cited rule that resolves, whether the case's expected outcome contradicts it:
    `{rule, ref, conflict, why}`. `conflict` None means no judge could decide it here."""
    expected = expected_outcome(scenario, catalogue)
    if not expected:
        return []
    decide = judge or _harness_conflict_judge
    cache = _load_checks(world_root)
    changed = False
    found: list[dict[str, Any]] = []
    for entry in resolve_sources(scenario, None if not rules else _Rules(rules), world_root):
        if entry["kind"] != "rule" or entry.get("resolved") is not True:
            continue
        at = int(str(entry.get("resolved_ref", "rule:-1")).split(":")[1])
        rule = rules[at] if 0 <= at < len(rules) else ""
        if not rule:
            continue
        key = hashlib.sha256(f"{expected}\n---\n{rule}".encode()).hexdigest()
        if key in cache:
            conflict, why = cache[key].get("conflict"), str(cache[key].get("why") or "")
        else:
            try:
                conflict, why = decide(expected, rule)
            except Exception as exc:  # noqa: BLE001 - a judge that could not run decides nothing
                logger.warning("policy conflict judge could not run: %s", type(exc).__name__)
                conflict, why = None, ""
            if conflict is not None:
                cache[key] = {"conflict": conflict, "why": why}
                changed = True
        found.append({"rule": rule, "ref": entry["ref"], "conflict": conflict, "why": why})
    if changed:
        _save_checks(world_root, cache)
    return found


class _Rules:
    """A contract stand-in carrying only the rules, for resolving rule sources."""

    def __init__(self, rules: list[str]) -> None:
        self.hard_constraints = rules
