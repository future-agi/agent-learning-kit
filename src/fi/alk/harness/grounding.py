"""Whether each source a scenario cites says what the scenario claims it says.

A proof does not block a case with no sources (the save path refuses a new or changed one, so a
guess is written as an `assumption` source). A source that does not resolve is blocking, because a false citation is worse than none. Chat messages and evidence cases
live on the platform and are resolved there.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .backends import tool
from .catalogue import Catalogue
from .json_file import write_json_atomic
from .judge import NoGateway, chat_completion
from .leaks import normalise
from .proof_records import PolicyCheck, SourceResult
from .scenario import Scenario
from .tools import schema

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


class RuleSource(Protocol):
    """Anything holding the agent's rules a `rule` source cites, as an `AgentContract` does."""

    hard_constraints: list[str]


@dataclass
class Rules:
    """Only the agent's rules, for resolving rule sources where no whole contract is at hand."""

    hard_constraints: list[str]


def resolve_sources(scenario: Scenario, contract: RuleSource | None, world_root: Path) -> list[SourceResult]:
    """One entry per cited source: `{kind, ref, resolved, problem, ...}`; `resolved` None means
    the platform decides it. A resolved rule carries its place among the rules in `rule_index`."""
    rules = list(contract.hard_constraints or []) if contract is not None else []
    resolved: list[SourceResult] = []
    for source in getattr(scenario, "sources", None) or []:
        kind, ref, quote = source.kind, source.ref, source.quote
        entry: SourceResult = {"kind": kind, "ref": ref, "resolved": True, "problem": ""}
        if kind == "rule":
            wanted = normalise(quote)
            matched = next((at for at, rule in enumerate(rules) if wanted and wanted in normalise(rule)), None)
            if matched is None:
                entry.update(resolved=False, problem="no rule of the agent contains the quoted words")
            else:
                entry["resolved_ref"] = f"rule:{matched}"
                entry["rule_index"] = matched
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


def unresolved(entries: list[SourceResult]) -> list[SourceResult]:
    """The entries ALK resolved and found false."""
    return [one for one in entries if one["kind"] in ALK_RESOLVED_KINDS and one.get("resolved") is False]


def expected_outcome(scenario: Scenario, catalogue: Catalogue) -> str:
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
    @tool(
        "decide",
        "Whether the expected outcome contradicts the rule.",
        schema({"conflict": bool, "why": str}, ["conflict", "why"]),
    )
    async def decide(args: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover - never executed
        return {}

    conversation = [{"role": "user", "content": _CONFLICT_PROMPT.format(expected=expected, rule=rule)}]
    try:
        message = chat_completion(conversation, [decide], True, temperature=0)
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
    try:
        write_json_atomic(Path(world_root) / POLICY_CHECKS, checks)
    except OSError as exc:  # a cache that cannot be written only costs a repeated call
        logger.warning("policy check cache not written: %s", exc)


def policy_conflicts(
    scenario: Scenario,
    catalogue: Catalogue,
    rules: list[str],
    world_root: Path,
    *,
    judge: Callable[[str, str], tuple[bool | None, str]] | None = None,
) -> list[PolicyCheck]:
    """For each cited rule that resolves, whether the case's expected outcome contradicts it:
    `{rule, ref, conflict, why}`. `conflict` None means no judge could decide it here."""
    expected = expected_outcome(scenario, catalogue)
    if not expected:
        return []
    decide = judge or _harness_conflict_judge
    cache = _load_checks(world_root)
    changed = False
    found: list[PolicyCheck] = []
    for entry in resolve_sources(scenario, Rules(rules) if rules else None, world_root):
        if entry["kind"] != "rule" or entry.get("resolved") is not True:
            continue
        at = entry.get("rule_index", -1)
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

