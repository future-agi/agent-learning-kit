"""Deterministic structural validators. Free, run before any critic call.

Structure and grounding are code's job, not the model's: completeness, the sub-goal/checkpoint join,
checkpoint definitions matching their declared kind, tool references existing in the contract, and
the hallucinated-interface guard (interface-shaped tokens that appear only in the contract's
anti_hallucination list). No domain vocabulary lives here.
"""

from __future__ import annotations

import json
import re

from .contract import AgentContract

_CHECK_KINDS = ("tool_call_args", "state", "conveyed", "absent", "judge")
_DISCLOSURES = ("volunteer", "on_request", "withhold")
_TOKEN = re.compile(r"/[a-z][a-z0-9_]{2,}|[A-Za-z_][A-Za-z0-9_]{2,}")


def _interface_shaped(token: str) -> bool:
    return (
        "_" in token or token.startswith("/") or bool(re.search(r"[a-z][A-Z]", token))
    )


def _legit_vocabulary(contract: AgentContract) -> set[str]:
    payload = contract.model_dump(exclude={"anti_hallucination"})
    return {match.lower() for match in _TOKEN.findall(json.dumps(payload))}


def banned_tokens(contract: AgentContract) -> set[str]:
    """Interface-shaped tokens appearing ONLY in anti_hallucination (the known fakes)."""
    legit = _legit_vocabulary(contract)
    banned: set[str] = set()
    for entry in contract.anti_hallucination:
        for match in _TOKEN.findall(str(entry)):
            if _interface_shaped(match) and match.lower() not in legit:
                banned.add(match)
    return banned


def _identifier_values(payload) -> set[str]:
    """Underscore-shaped string values anywhere in a definition (the id-like ones)."""
    values: set[str] = set()
    if isinstance(payload, str):
        if re.fullmatch(r"[a-z][a-z0-9]*(_[a-z0-9]+)+", payload):
            values.add(payload)
    elif isinstance(payload, dict):
        for value in payload.values():
            values |= _identifier_values(value)
    elif isinstance(payload, list):
        for value in payload:
            values |= _identifier_values(value)
    return values


def _validate_definition(
    kind: str,
    definition: dict,
    tool_names: set[str],
    where: str,
    legit_vocabulary: set[str],
) -> list[str]:
    problems: list[str] = []
    unknown_ids = sorted(
        value
        for value in _identifier_values(
            {k: v for k, v in definition.items() if k != "tool"}
        )
        if value.lower() not in legit_vocabulary
    )
    if unknown_ids:
        problems.append(f"{where}:unknown-id:{','.join(unknown_ids)[:100]}")
    if kind == "tool_call_args":
        tool = definition.get("tool")
        if tool not in tool_names:
            problems.append(f"{where}:unknown-tool:{tool}")
        if not definition.get("args_equal") and not definition.get("args_present"):
            problems.append(f"{where}:tool_call_args-without-args")
    elif kind == "state":
        if not definition.get("must") and not definition.get("forbidden"):
            problems.append(f"{where}:state-without-must-or-forbidden")
    elif kind == "conveyed":
        variants = definition.get("must_include_any")
        if not isinstance(variants, list) or not any(
            str(v).strip() for v in variants or []
        ):
            problems.append(f"{where}:conveyed-without-variants")
    elif kind == "absent":
        inner = definition.get("no_tool_call_with") or {}
        tool = definition.get("no_tool_call") or inner.get("tool")
        if not tool:
            problems.append(f"{where}:absent-without-tool")
        elif tool not in tool_names:
            problems.append(f"{where}:unknown-tool:{tool}")
    elif kind == "judge":
        if not str(definition.get("rubric", "")).strip():
            problems.append(f"{where}:judge-without-rubric")
    else:
        problems.append(f"{where}:unknown-kind:{kind}")
    return problems


def validate_scenario(scenario: dict, contract: AgentContract) -> list[str]:
    """Return problems; empty means structurally complete and grounded enough for the critic."""
    problems: list[str] = []
    tool_names = contract.tool_names()
    legit_vocabulary = _legit_vocabulary(contract)

    for field in (
        "id",
        "use_case",
        "situation",
        "goal",
        "description",
        "agent_input",
        "expected_outcome",
    ):
        if scenario.get(field) in (None, "", [], {}):
            problems.append(f"empty:{field}")
    description = scenario.get("description")
    if isinstance(description, str) and 0 < len(description) < 60:
        problems.append("description-too-short")

    facts = scenario.get("facts")
    if contract.conversational and not isinstance(facts, list):
        problems.append("facts-not-a-list")
    for index, fact in enumerate(facts or []):
        if not isinstance(fact, dict) or not fact.get("key"):
            problems.append(f"fact[{index}]:malformed")
        elif fact.get("disclosure") not in _DISCLOSURES:
            problems.append(f"fact[{index}]:bad-disclosure")

    sub_goals = scenario.get("sub_goals")
    if not isinstance(sub_goals, list) or len(sub_goals) < 3:
        problems.append("sub_goals<3")
    else:
        seen_names: set[str] = set()
        deterministic_count = 0
        for index, sub_goal in enumerate(sub_goals):
            where = f"sub_goal[{index}]"
            if not isinstance(sub_goal, dict) or not sub_goal.get("name"):
                problems.append(f"{where}:no-name")
                continue
            name = str(sub_goal["name"])
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                problems.append(f"{where}:name-not-snake_case:{name}")
            if name in seen_names:
                problems.append(f"{where}:duplicate-name:{name}")
            seen_names.add(name)
            checkpoint = sub_goal.get("checkpoint")
            if not isinstance(checkpoint, dict):
                problems.append(f"{where}:no-checkpoint")
                continue
            kind = str(checkpoint.get("kind", ""))
            definition = checkpoint.get("definition")
            if not isinstance(definition, dict) or not definition:
                problems.append(f"{where}:no-definition")
            else:
                problems += _validate_definition(
                    kind, definition, tool_names, where, legit_vocabulary
                )
            deterministic = bool(checkpoint.get("deterministic"))
            if deterministic and kind == "judge":
                problems.append(f"{where}:judge-marked-deterministic")
            if deterministic:
                deterministic_count += 1
        if sub_goals and deterministic_count == 0:
            problems.append("no-deterministic-checkpoint")

    outcome = scenario.get("expected_outcome")
    if isinstance(outcome, dict):
        if not str(outcome.get("world_state", "")).strip():
            problems.append("empty:expected_outcome.world_state")

    environment = scenario.get("environment")
    if not isinstance(environment, dict):
        problems.append("environment-not-a-dict")
    else:
        for tool in environment.get("mock_responses") or {}:
            if tool not in tool_names:
                problems.append(f"mock_responses:unknown-tool:{tool}")

    blob = json.dumps(scenario)
    if re.search(r"\{[a-z_]+\}", blob):
        problems.append("template-placeholders-present")
    banned = banned_tokens(contract)
    hits = sorted(
        {b for b in banned if re.search(r"(?<![\w/])" + re.escape(b) + r"\b", blob)}
    )
    if hits:
        problems.append("banned-interface:" + ",".join(hits)[:120])
    return problems


def repair_hint(problems: list[str]) -> str:
    """Targeted, imperative fix instructions from validator problems."""
    lines: list[str] = []
    for problem in problems:
        if problem.startswith("empty:"):
            lines.append(
                f"- Field '{problem.split(':', 1)[1]}' was empty; fill it with real, complete content."
            )
        elif problem == "description-too-short":
            lines.append("- Write a proper 2-3 sentence description, not a stub.")
        elif problem == "sub_goals<3":
            lines.append(
                "- Provide at least 3 branch-specific sub_goals, each with a concrete checkpoint, "
                "ending with a final verification of the resulting state."
            )
        elif ":unknown-id:" in problem:
            lines.append(
                f"- A checkpoint uses an identifier that does not exist in the contract "
                f"({problem.split(':')[-1]}). Copy ids character for character from the contract's "
                "data and arg values; do not reorder or rename their parts."
            )
        elif ":unknown-tool:" in problem:
            lines.append(
                f"- A checkpoint or mock references a tool that does not exist ({problem.split(':')[-1]}). "
                "Use ONLY the contract's real tools with exact names."
            )
        elif problem == "template-placeholders-present":
            lines.append(
                "- Remove every {placeholder}; write concrete values from the contract data."
            )
        elif problem.startswith("banned-interface:"):
            lines.append(
                f"- You referenced a non-existent interface ({problem.split(':', 1)[1]}). "
                "Use only the contract's real tools, args and ids."
            )
        elif problem == "no-deterministic-checkpoint":
            lines.append(
                "- Every checkpoint is a judge; make the tool-argument and end-state checks "
                "deterministic per the vocabulary."
            )
        elif ":conveyed-without-variants" in problem:
            lines.append(
                "- A conveyed checkpoint listed no values. must_include_any needs at least one real "
                "value from the contract data; when no data value can witness this sub-goal, change "
                "the checkpoint to the kind that can (absent for something that must not happen, "
                "judge as the last resort)."
            )
        elif ":" in problem:
            lines.append(f"- Fix: {problem}")
    return "\n".join(dict.fromkeys(lines))
