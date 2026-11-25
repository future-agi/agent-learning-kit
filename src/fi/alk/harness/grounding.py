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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence

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


# Where an entrypoint publishes its `--source` (the agent's own source, `/work/source` in the hosted sandbox).
SOURCE_ROOT_ENV = "ALK_HARNESS_SOURCE_ROOT"


def publish_source_root(source: Path | str) -> None:
    """Say where the agent's own source is, for every proof this process and its children run."""
    os.environ[SOURCE_ROOT_ENV] = str(Path(source).expanduser().resolve())


def agent_source_root(world_root: Path | None = None) -> Path | None:
    """The agent's own source a `file:` source may cite: what the entrypoint published, else the source a local
    session recorded beside its authoring tree. None when neither is known."""
    given = os.environ.get(SOURCE_ROOT_ENV, "").strip()
    if given:
        return Path(given)
    if world_root is not None:
        try:
            recorded = json.loads((Path(world_root) / "session.json").read_text(encoding="utf-8")).get("source")
        except (OSError, ValueError, AttributeError):
            recorded = ""
        if isinstance(recorded, str) and recorded.strip():
            return Path(recorded)
    return None


def _file(world_root: Path, ref: str, source_root: Path | None = None) -> tuple[Path, str] | None:
    """The cited file and where it was found: `authoring` (knowledge/ or the authoring tree), else `agent_source` (the
    agent's own source). Never a path that climbs out of its root."""
    name = ref.removeprefix("file:").strip().lstrip("/")
    if not name or ".." in name.split("/"):
        return None
    for root in _FILE_ROOTS:
        candidate = Path(world_root) / root / name if root else Path(world_root) / name
        if candidate.is_file():
            return candidate, "authoring"
    if source_root is not None:
        base = Path(source_root).resolve()
        candidate = (base / name).resolve()
        if candidate.is_relative_to(base) and candidate.is_file():
            return candidate, "agent_source"
    return None


def _holds_a_secret(quote: str) -> bool:
    """Whether the kit's one outbound secret scrub would change these words."""
    from .outbound import redact_outbound_text

    return redact_outbound_text(quote) != quote


class RuleSource(Protocol):
    """Anything holding the agent's rules a `rule` source cites, as an `AgentContract` does."""

    hard_constraints: list[str]


@dataclass
class Rules:
    """Only the agent's rules, for resolving rule sources where no whole contract is at hand."""

    hard_constraints: list[str]


def _field(record: Any, name: str) -> str:
    return str((record.get(name) if isinstance(record, dict) else getattr(record, name, "")) or "")


def _replacement(wanted: str, rules: list[str], records: Sequence[Any]) -> int | None:
    """The rule now held that replaced the one whose words a rule source quotes (already normalised), following the
    contract's `amendment_records` through every rewording (a removed rule's `replaced_by`, or an added rule's
    `previous`, by `rule_id`); None for a rule removed with no replacement, or for words no recorded rule held. The
    platform reads it the same way (future-agi ``harness_scenario_sources.replacement``)."""
    from .contract import rule_id

    ruled = [one for one in records if _field(one, "kind") == "rule"]
    held = {rule_id(rule): at for at, rule in enumerate(rules)}

    def after(key: str) -> str:
        # A rule key has at most one successor: `replaces` names one rule by id, so the chain never forks.
        return next(
            (_field(one, "replaced_by") for one in ruled if _field(one, "key") == key and _field(one, "replaced_by")),
            "",
        ) or next((_field(one, "key") for one in ruled if _field(one, "previous") == key and _field(one, "key")), "")

    for removed in ruled:
        text = _field(removed, "text")
        if _field(removed, "action") != "removed" or not wanted or wanted not in normalise(text):
            continue
        key, seen = _field(removed, "key") or rule_id(text), set()
        while key and key not in seen:
            seen.add(key)
            key = after(key)
            if key in held:
                return held[key]
    return None


# The longest quote a source keeps (`ScenarioSource.quote`).
_LONGEST_QUOTE = 500


def recited_sources(scenario: Scenario, contract: RuleSource | None) -> list[dict[str, Any]] | None:
    """The case's sources with every rule source that stands only on a replacement (`_replacement`) citing the rule
    now held, in its words and at its place; None when none does. A case reviewed against a reworded rule then quotes
    a rule the agent has, not the words it no longer has."""
    rules = list(getattr(contract, "hard_constraints", None) or []) if contract is not None else []
    records = getattr(contract, "amendment_records", None) or []
    out, changed = [], False
    for source in getattr(scenario, "sources", None) or []:
        entry = source.model_dump() if hasattr(source, "model_dump") else dict(source)
        wanted = normalise(entry.get("quote") or "")
        if entry.get("kind") == "rule" and wanted and not any(wanted in normalise(rule) for rule in rules):
            at = _replacement(wanted, rules, records)
            if at is not None:
                entry = {**entry, "ref": f"rule:{at}", "quote": rules[at][:_LONGEST_QUOTE]}
                changed = True
        out.append(entry)
    return out if changed else None


def resolve_sources(
    scenario: Scenario, contract: RuleSource | None, world_root: Path, source_root: Path | None = None
) -> list[SourceResult]:
    """One entry per cited source: `{kind, ref, resolved, problem, ...}`; `resolved` None means
    the platform decides it. A resolved rule carries its place among the rules in `rule_index`; a file found in the
    agent's own source (`source_root`) carries `origin` "agent_source"."""
    rules = list(contract.hard_constraints or []) if contract is not None else []
    resolved: list[SourceResult] = []
    for source in getattr(scenario, "sources", None) or []:
        kind, ref, quote = source.kind, source.ref, source.quote
        entry: SourceResult = {"kind": kind, "ref": ref, "resolved": True, "problem": ""}
        if kind == "rule":
            wanted = normalise(quote)
            matched = next((at for at, rule in enumerate(rules) if wanted and wanted in normalise(rule)), None)
            if matched is None:
                # A rule reworded as another: the source stands on its replacement, and the case is reviewed.
                matched = _replacement(wanted, rules, getattr(contract, "amendment_records", None) or [])
            if matched is None:
                entry.update(resolved=False, problem="no rule of the agent contains the quoted words")
            else:
                entry["resolved_ref"] = f"rule:{matched}"
                entry["rule_index"] = matched
        elif kind == "file":
            found = _file(world_root, ref, source_root)
            if found is None:
                # Uploaded files may live only on the platform; it resolves them there.
                entry.update(resolved=None, problem="the file is not in the authoring tree or the agent's source")
            else:
                path, origin = found
                if origin == "agent_source":
                    entry["origin"] = origin
                text = path.read_bytes()
                entry["matched_digest"] = "sha256:" + hashlib.sha256(text).hexdigest()
                if normalise(quote) not in normalise(text.decode("utf-8", errors="replace")):
                    entry.update(resolved=False, problem=f"{path.name} does not contain the quoted words")
                elif origin == "agent_source" and _holds_a_secret(quote):
                    entry.update(
                        resolved=False,
                        problem=f"the words quoted from {path.name} hold what reads as a secret (a key, token or "
                        "credential), and a citation is shown wherever the case is: quote words of the agent's "
                        "source that hold none",
                    )
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


def expected_outcome(
    scenario: Scenario, catalogue: Catalogue, workers: Sequence[Any] = (), *, situation: bool = False
) -> str:
    """What the case expects, in words: each of its sub-goals' `what`; with `situation`, also the situation the person
    is told to play, which says who is asking. Where the app hands work to `workers` (the contract's), also how the
    case reaches it: each reference step's tool with the worker that holds it."""
    lines = []
    for name in getattr(scenario, "sub_goals", None) or []:
        goal = catalogue.named(name) if catalogue is not None else None
        what = str(getattr(goal, "what", "") or "").strip()
        if what:
            lines.append(f"- {what}")
    played = str(getattr(scenario, "instruction", "") or "").strip() if situation else ""
    if lines and played:
        lines.append(
            "\nThe situation, as the person is told to play it. Who is asking is part of the outcome: where a rule "
            "limits an action to a particular person, expecting it for someone else without what the rule asks for "
            f"contradicts the rule:\n{played[:_SITUATION_CHARS]}"
        )
    if not lines or not workers:
        return "\n".join(lines)
    holders: dict[str, list[str]] = {}
    for one in workers:
        for name in getattr(one, "tools", None) or ():
            holders.setdefault(str(name), []).append(str(getattr(one, "name", "") or getattr(one, "id", "")))

    def shown(name: str) -> str:
        # A tool is put down to a worker only when that worker alone holds it.
        held = holders.get(name) or []
        if len(held) == 1:
            return f"{name} (done by the worker {held[0]})"
        return f"{name} (a tool {', '.join(held)} all hold)" if held else name

    path = [shown(str(step.tool)) for step in getattr(scenario, "solution", None) or ()]
    lines.append(
        "\nThe app's workers, the agents it hands work to, and the tools each holds: "
        + "; ".join(
            f"{getattr(one, 'name', '') or one.id}: {', '.join(getattr(one, 'tools', None) or ()) or 'no tools'}"
            for one in workers
        )
    )
    if path:
        lines.append("The case reaches its outcome through: " + ", then ".join(path))
    return "\n".join(lines)


# How much of the person's instruction the all-rules check reads: enough to say who is asking and for what.
_SITUATION_CHARS = 1500
# The uncited rules handed to the judge in one call, at most.
RULES_PER_CALL = 20
ALL_RULES_SWITCH = "ALK_ALL_RULES_CHECK"


def all_rules_check_on() -> bool:
    """Whether a case that calls the agent's tools is also judged against the rules it does not cite, as advice that
    never changes whether it is proved. Off unless `ALK_ALL_RULES_CHECK` is 1."""
    return str(os.environ.get(ALL_RULES_SWITCH) or "").strip() == "1"

_CONFLICT_PROMPT = (
    "A test case for an agent expects the outcome below, and cites the agent's rule below as its source. "
    "Decide whether the expected outcome contradicts the rule: expecting the agent to do what the rule "
    "forbids, or a number, limit, window or condition the rule states differently. A case that only "
    "covers part of the rule, or adds details the rule does not mention, does not contradict it. Where the app "
    "hands work to workers, a rule that sends a kind of request to a worker is how that work gets done: an "
    "outcome the case reaches through that worker follows the rule; only an outcome reached some other way, "
    "or one the rule's worker is not to produce, contradicts it. The path lists tool calls only: a worker that "
    "uses no tools leaves no step, a worker may hand work on to further workers, and a tool several workers hold "
    "does not say which one called it, so a path that does not name the rule's worker is no sign the work went "
    "around it.\n\n"
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
    source_root: Path | None = None,
    workers: Sequence[Any] = (),
) -> list[PolicyCheck]:
    """For each cited rule that resolves, whether the case's expected outcome contradicts it:
    `{rule, ref, conflict, why}`. `conflict` None means no judge could decide it here. With `all_rules_check_on`, a
    case whose reference calls the agent's tools is also judged, with its situation, against the rules it does not
    cite, `RULES_PER_CALL` at a time, each entry `advisory` under ref `rules`: advice, never a conflict that holds it."""
    expected = expected_outcome(scenario, catalogue, workers)
    if not expected:
        return []
    decide = judge or _harness_conflict_judge
    cache = _load_checks(world_root)
    changed = False
    found: list[PolicyCheck] = []

    def judged(outcome: str, rule: str) -> tuple[bool | None, str]:
        nonlocal changed
        key = hashlib.sha256(f"{outcome}\n---\n{rule}".encode()).hexdigest()
        if key in cache:
            return cache[key].get("conflict"), str(cache[key].get("why") or "")
        try:
            conflict, why = decide(outcome, rule)
        except Exception as exc:  # noqa: BLE001 - a judge that could not run decides nothing
            logger.warning("policy conflict judge could not run: %s", type(exc).__name__)
            conflict, why = None, ""
        if conflict is not None:
            cache[key] = {"conflict": conflict, "why": why}
            changed = True
        return conflict, why

    cited: set[int] = set()
    for entry in resolve_sources(scenario, Rules(rules) if rules else None, world_root, source_root):
        if entry["kind"] != "rule" or entry.get("resolved") is not True:
            continue
        at = entry.get("rule_index", -1)
        rule = rules[at] if 0 <= at < len(rules) else ""
        if not rule:
            continue
        cited.add(at)
        conflict, why = judged(expected, rule)
        found.append({"rule": rule, "ref": entry["ref"], "conflict": conflict, "why": why})
    uncited = [rule for at, rule in enumerate(rules) if at not in cited and str(rule).strip()]
    if uncited and getattr(scenario, "solution", None) and all_rules_check_on():
        situated = expected_outcome(scenario, catalogue, workers, situation=True)
        for start in range(0, len(uncited), RULES_PER_CALL):
            rule = "\n".join(f"- {one}" for one in uncited[start : start + RULES_PER_CALL])
            conflict, why = judged(situated, rule)
            found.append({"rule": rule, "ref": "rules", "conflict": conflict, "why": why, "advisory": True})
    if changed:
        _save_checks(world_root, cache)
    return found
