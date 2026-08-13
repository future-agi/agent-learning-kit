"""Pure-Python evaluation of generated checkpoints. No model calls, ever.

This is the downstream consumer contract: after a simulation run, feed the recorded tool calls, the
agent's transcript turns, and the final environment state to ``evaluate_scenario`` and get a
pass/fail verdict per sub-goal. Checkpoints of kind ``judge`` are returned as ``skipped`` (they are
the one non-deterministic kind, flagged as such at generation time); everything else is plain
comparisons.

Expected inputs:
- tool_calls: [{"name": str, "arguments": {...}}, ...] in call order
- transcript_turns: the agent-side utterances as strings
- final_state: nested dict of the world state after the run (dotted paths resolve into it)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class CheckResult:
    name: str
    kind: str
    passed: bool | None  # None = not evaluated here (judge)
    reason: str


def _resolve_path(state: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    cursor: Any = state
    for part in str(path).split("."):
        if isinstance(cursor, Mapping) and part in cursor:
            cursor = cursor[part]
        else:
            return False, None
    return True, cursor


def _call_matches(
    call: Mapping[str, Any], tool: str, args_equal: Mapping[str, Any]
) -> bool:
    if str(call.get("name") or call.get("tool") or "") != tool:
        return False
    arguments = call.get("arguments") or call.get("args") or {}
    if not isinstance(arguments, Mapping):
        return False
    return all(arguments.get(key) == value for key, value in args_equal.items())


def _eval_tool_call_args(
    definition: Mapping[str, Any], tool_calls: Sequence[Mapping[str, Any]]
) -> tuple[bool, str]:
    tool = str(definition.get("tool", ""))
    args_equal = definition.get("args_equal") or {}
    args_present = definition.get("args_present") or []
    # min_count: how many matching calls the run must contain (quantity semantics).
    # call_nth is a synonym models produce naturally; nth-call-exists == at least n matches.
    raw_count = definition.get("min_count", definition.get("call_nth", 1))
    try:
        required = max(1, int(raw_count))
    except (TypeError, ValueError):
        required = 1
    matched = 0
    for call in tool_calls:
        if not _call_matches(call, tool, args_equal):
            continue
        arguments = call.get("arguments") or call.get("args") or {}
        if any(arg not in arguments for arg in args_present):
            continue
        matched += 1
        if matched >= required:
            suffix = f" x{matched}" if required > 1 else ""
            return True, f"call to {tool} matched{suffix}"
    return False, (
        f"only {matched} of {required} required matching calls to {tool}"
        if required > 1
        else f"no call to {tool} carried the expected arguments"
    )


def _eval_state(
    definition: Mapping[str, Any], final_state: Mapping[str, Any]
) -> tuple[bool, str]:
    for path, expected in (definition.get("must") or {}).items():
        found, actual = _resolve_path(final_state, path)
        if not found or actual != expected:
            return False, f"state {path} = {actual!r}, expected {expected!r}"
    for path, forbidden in (definition.get("forbidden") or {}).items():
        found, actual = _resolve_path(final_state, path)
        if found and actual == forbidden:
            return False, f"state {path} carries the forbidden value {forbidden!r}"
    return True, "state matched"


def _eval_conveyed(
    definition: Mapping[str, Any], transcript_turns: Sequence[str]
) -> tuple[bool, str]:
    variants = [str(v) for v in definition.get("must_include_any") or []]
    joined = "\n".join(str(turn) for turn in transcript_turns)
    for variant in variants:
        if variant and variant.lower() in joined.lower():
            return True, f"value {variant!r} conveyed"
    return False, f"none of {variants!r} appeared in the agent's turns"


def _eval_absent(
    definition: Mapping[str, Any], tool_calls: Sequence[Mapping[str, Any]]
) -> tuple[bool, str]:
    tool = definition.get("no_tool_call")
    if tool:
        hit = any(
            str(c.get("name") or c.get("tool") or "") == str(tool) for c in tool_calls
        )
        return (not hit, f"call to {tool} {'occurred' if hit else 'never occurred'}")
    inner = definition.get("no_tool_call_with") or {}
    tool = str(inner.get("tool", ""))
    args_equal = inner.get("args_equal") or {}
    hit = any(_call_matches(call, tool, args_equal) for call in tool_calls)
    return (
        not hit,
        f"matching call to {tool} {'occurred' if hit else 'never occurred'}",
    )


def evaluate_checkpoint(
    kind: str,
    definition: Mapping[str, Any],
    *,
    tool_calls: Sequence[Mapping[str, Any]] = (),
    transcript_turns: Sequence[str] = (),
    final_state: Mapping[str, Any] | None = None,
) -> tuple[bool | None, str]:
    """Evaluate one checkpoint definition. Returns (passed, reason); passed None for judge."""
    if kind == "tool_call_args":
        return _eval_tool_call_args(definition, tool_calls)
    if kind == "state":
        return _eval_state(definition, final_state or {})
    if kind == "conveyed":
        return _eval_conveyed(definition, transcript_turns)
    if kind == "absent":
        return _eval_absent(definition, tool_calls)
    if kind == "judge":
        return None, "judge checkpoints are not evaluated deterministically"
    return False, f"unknown checkpoint kind: {kind}"


def evaluate_scenario(
    record: Mapping[str, Any],
    *,
    tool_calls: Sequence[Mapping[str, Any]] = (),
    transcript_turns: Sequence[str] = (),
    final_state: Mapping[str, Any] | None = None,
) -> list[CheckResult]:
    """Evaluate every sub-goal of one generated scenario record against run evidence."""
    results: list[CheckResult] = []
    for sub_goal in record.get("sub_goals") or []:
        checkpoint = (sub_goal or {}).get("checkpoint") or {}
        kind = str(checkpoint.get("kind", ""))
        passed, reason = evaluate_checkpoint(
            kind,
            checkpoint.get("definition") or {},
            tool_calls=tool_calls,
            transcript_turns=transcript_turns,
            final_state=final_state,
        )
        results.append(
            CheckResult(
                name=str(sub_goal.get("name")), kind=kind, passed=passed, reason=reason
            )
        )
    return results
