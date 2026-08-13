"""Oracle self-consistency: a scenario must pass the run it itself predicts. Pure code.

A well-formed scenario fully determines one predicted run: the tool calls its checkpoints pin, the
final state its seed plus declared mock state updates produce, and the values it says the agent must
convey. Evaluating the scenario's own deterministic checkpoints against that predicted evidence is
an execution check with no model involved: a checkpoint that fails on the run its own scenario
predicts can never pass a real run, so the scenario is internally contradictory and must be
repaired before it costs anything downstream.
"""

from __future__ import annotations

import copy
from typing import Any, Mapping

from .checks import evaluate_checkpoint


def _deep_merge(target: dict, updates: Mapping[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def predicted_evidence(record: Mapping[str, Any]) -> dict[str, Any]:
    """The run this scenario predicts, derived from its own definitions only."""
    tool_calls: list[dict[str, Any]] = []
    for sub_goal in record.get("sub_goals") or []:
        checkpoint = (sub_goal or {}).get("checkpoint") or {}
        definition = checkpoint.get("definition") or {}
        if checkpoint.get("kind") == "tool_call_args":
            arguments = dict(definition.get("args_equal") or {})
            for arg in definition.get("args_present") or []:
                arguments.setdefault(str(arg), "<runtime>")
            tool_calls.append({"name": definition.get("tool"), "arguments": arguments})

    # The transcript is predicted ONLY from what the scenario says the agent must communicate.
    # Seeding it from the conveyed definitions themselves would make those checks self-satisfying;
    # sourcing it from must_convey makes the oracle verify that every conveyed checkpoint asserts
    # a value the scenario actually commits the agent to saying.
    transcript: list[str] = []

    environment = record.get("environment") or {}
    final_state: dict[str, Any] = copy.deepcopy(dict(environment.get("seed") or {}))
    for mock in (environment.get("mock_responses") or {}).values():
        if isinstance(mock, Mapping) and isinstance(mock.get("state_updates"), Mapping):
            _deep_merge(final_state, mock["state_updates"])

    outcome = record.get("expected_outcome") or {}
    for value in outcome.get("must_convey") or []:
        transcript.append(str(value))
    return {
        "tool_calls": tool_calls,
        "transcript_turns": transcript,
        "final_state": final_state,
    }


def oracle_problems(record: Mapping[str, Any]) -> list[str]:
    """Deterministic checkpoints that fail the scenario's own predicted run."""
    evidence = predicted_evidence(record)
    problems: list[str] = []
    for sub_goal in record.get("sub_goals") or []:
        checkpoint = (sub_goal or {}).get("checkpoint") or {}
        if not checkpoint.get("deterministic"):
            continue
        passed, reason = evaluate_checkpoint(
            str(checkpoint.get("kind", "")),
            checkpoint.get("definition") or {},
            tool_calls=evidence["tool_calls"],
            transcript_turns=evidence["transcript_turns"],
            final_state=evidence["final_state"],
        )
        if passed is False:
            problems.append(f"oracle:{sub_goal.get('name')}:{reason}")
    return problems


def oracle_hint(problems: list[str]) -> str:
    lines = [
        "- These checkpoints fail even on the run this scenario itself predicts, so they can never "
        "pass a real run. Make the scenario self-consistent: the environment seed plus the declared "
        "mock state_updates must produce the state the `state` checkpoints assert, expected values "
        "asserted by `conveyed` checkpoints must appear in must_convey, and `absent` checkpoints "
        "must not name calls the other checkpoints require:"
    ]
    lines += [f"  - {p}" for p in problems[:8]]
    return "\n".join(lines)
