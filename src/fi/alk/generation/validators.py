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


def _is_identifier_shaped(value: str) -> bool:
    """An id or enum token, as opposed to text a scenario composes (a query, a message body)."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}", value))


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
    arg_values: dict[str, dict],
    argless_tools: frozenset[str] = frozenset(),
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
        if not isinstance(tool, str) or tool not in tool_names:
            problems.append(f"{where}:unknown-tool:{tool}")
        # A tool that genuinely declares no parameters is asserted by the call alone.
        if (
            not definition.get("args_equal")
            and not definition.get("args_present")
            and str(tool) not in argless_tools
        ):
            problems.append(f"{where}:tool_call_args-without-args")
        for arg, value in (definition.get("args_equal") or {}).items():
            allowed = arg_values.get(str(tool), {}).get(str(arg))
            if isinstance(allowed, list) and allowed:
                candidates = {str(item).lower() for item in allowed} | {"null", "none"}
                if value is not None and str(value).lower() not in candidates:
                    problems.append(f"{where}:arg-value-not-allowed:{arg}={value}")
                continue
            # No listed valid values for this argument: a pinned identifier must still come from
            # the contract's own vocabulary, because an id found nowhere in the contract is either
            # invented or runtime-generated and neither can be pinned in advance. This applies to
            # identifiers only. Arguments that carry composed text (a query the agent writes, a
            # message it sends) are authored per scenario and cannot appear in a contract, so
            # requiring them to would make every such agent ungeneratable.
            if (
                isinstance(value, str)
                and len(value) >= 3
                and _is_identifier_shaped(value)
                and not value.replace(".", "").replace("-", "").isdigit()
                and value.lower() not in ("null", "none")
                and value.lower() not in legit_vocabulary
            ):
                problems.append(f"{where}:pinned-value-not-in-contract:{arg}={value}")
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
        inner = definition.get("no_tool_call_with")
        inner = inner if isinstance(inner, dict) else {}
        tool = definition.get("no_tool_call") or inner.get("tool")
        if not tool:
            problems.append(f"{where}:absent-without-tool")
        elif not isinstance(tool, str):
            problems.append(f"{where}:absent-tool-not-a-single-name:{str(tool)[:60]}")
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
    arg_values = {tool.name: dict(tool.arg_values or {}) for tool in contract.tools}
    argless_tools = frozenset(tool.name for tool in contract.tools if not tool.args)

    for field in (
        "id",
        "use_case",
        "situation",
        "goal",
        "description",
        "agent_input",
        "expected_outcome",
        "target_failure",
        "why_it_matters",
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
    if not isinstance(sub_goals, list) or len(sub_goals) < 2:
        problems.append("sub_goals<2")
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
                    kind,
                    definition,
                    tool_names,
                    where,
                    legit_vocabulary,
                    arg_values,
                    argless_tools,
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
        elif problem == "sub_goals<2":
            lines.append(
                "- Provide at least 2 sub_goals: the decisive check on the correct end result, plus "
                "the behavior that leads there. Do not pad with filler; do not stop at one."
            )
        elif ":unknown-id:" in problem:
            lines.append(
                f"- A checkpoint uses an identifier that does not exist in the contract "
                f"({problem.split(':')[-1]}). If it names a real entity, copy its id character for "
                "character from the contract's data. If its value only comes into existence during "
                "the run (an order id, a generated handle), it cannot be pinned: move that argument "
                "to args_present and pin the arguments whose values the user's request determines."
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
        elif ":no-definition" in problem:
            lines.append(
                "- A checkpoint has prose but no definition object. Every checkpoint carries the "
                "machine-readable definition for its kind exactly as the vocabulary specifies; the "
                "detail sentence never replaces it."
            )
        elif ":tool_call_args-without-args" in problem:
            lines.append(
                "- A tool_call_args checkpoint lists no arguments. Pin every argument the user's "
                "request determines in args_equal; when every argument of the tool only exists at "
                "run time, list those argument names in args_present instead, and never leave both "
                "empty."
            )
        elif ":pinned-value-not-in-contract:" in problem:
            lines.append(
                f"- A checkpoint pins an argument to a value found nowhere in the contract "
                f"({problem.split(':')[-1]}). If the value is real, copy it from the contract's "
                "data; if it only exists at run time, move the argument to args_present."
            )
        elif ":arg-value-not-allowed:" in problem:
            lines.append(
                f"- A checkpoint pins an argument to a value the contract does not list as valid "
                f"({problem.split(':')[-1]}). Choose the value from that argument's listed valid "
                "values in the contract."
            )
        elif ":duplicate-name:" in problem:
            lines.append(
                f"- Two sub_goals share the name {problem.split(':')[-1]!r}; every sub-goal needs "
                "its own distinct snake_case name."
            )
        elif ":absent-tool-not-a-single-name" in problem:
            lines.append(
                "- An absent checkpoint names several tools at once; write one absent checkpoint "
                "per tool, each with a single tool name."
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
