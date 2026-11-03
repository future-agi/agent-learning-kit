"""What each scenario needs from the agent and its world, so a gap is counted against the scenarios it affects and
never estimated. Derived from the scenario documents, the contract and the sub-goal checks; nothing is guessed."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable

from .capabilities import CATALOG_VERSION

SCHEMA_VERSION = "futureagi.scenario-requirements.v1"


def _tools(path: Iterable[dict[str, Any]]) -> set[str]:
    return {str(step.get("tool")) for step in path if isinstance(step, dict) and step.get("tool")}


def required_tools(scenario: dict[str, Any]) -> list[str]:
    """Tools every path to the answer uses: a tool one correct path avoids is not needed."""
    paths = [scenario.get("solution") or []] + [
        alternative.get("solution") if isinstance(alternative, dict) else alternative
        for alternative in scenario.get("alternatives") or []
    ]
    sets = [_tools(path or []) for path in paths]
    return sorted(set.intersection(*sets)) if sets else []


def check_reads(code: str) -> set[str]:
    """Which of `world` and `calls` a check's code uses, from its parameters, by parsing it."""
    try:
        tree = ast.parse(code or "")
    except SyntaxError:
        return set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "check":
            params = [arg.arg for arg in node.args.args][:2]
            roles = dict(zip(params, ("world", "calls")))
            used = {child.id for child in ast.walk(node) if isinstance(child, ast.Name)}
            return {role for name, role in roles.items() if name in used}
    return set()


def scenario_requirements(
    scenarios: list[dict[str, Any]],
    contract: dict[str, Any],
    sub_goal_checks: dict[str, str],
    *,
    seeded_world: bool,
    selection_size: int,
) -> dict[str, Any]:
    """Per scenario key: the capabilities it needs, the tools every path uses, and which sub-goals read the world
    or the calls."""
    voice = str(contract.get("modality") or "") == "voice"
    conversational = bool(contract.get("conversational", True))
    out: dict[str, Any] = {}
    for scenario in scenarios:
        key = str(scenario.get("scenario_key") or scenario.get("name") or "")
        if not key:
            continue
        needs = {"start_health", "one_turn"}
        if conversational and int(scenario.get("max_turns") or 0) > 1:
            needs.add("multi_turn")
        tools = required_tools(scenario)
        if tools:
            needs |= {"tool_discovery", "tool_routing"}
        reads: dict[str, list[str]] = {"world": [], "calls": []}
        for goal in scenario.get("sub_goals") or []:
            name = goal if isinstance(goal, str) else str((goal or {}).get("name") or "")
            for role in check_reads(sub_goal_checks.get(name, "")):
                reads[role].append(name)
        if reads["world"] or reads["calls"]:
            needs.add("state_evidence")
        if voice:
            needs.add("audio_evidence")
        if seeded_world and selection_size > 1:
            needs.add("world_reset")
        out[key] = {
            "capabilities": sorted(needs),
            "tools": {"required": tools, "any_of": []},
            "sub_goal_evidence": {role: sorted(names) for role, names in reads.items()},
        }
    return {"schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION, "scenarios": out}


REQUIREMENTS_FILE = "scenario-requirements.json"


def requirements_from_bundle(bundle: Path) -> dict[str, Any] | None:
    """Requirements for every scenario in a built bundle, read from its scenario documents, contract and
    sub-goal checks; None when the folder holds no bundle."""
    import json

    contract_path = bundle / "contract.json"
    if not contract_path.is_file():
        return None
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    catalogue_path = bundle / "sub_goals.json"
    entries = (
        json.loads(catalogue_path.read_text(encoding="utf-8")).get("sub_goals", [])
        if catalogue_path.is_file()
        else []
    )
    checks = {str(entry.get("name")): str(entry.get("check") or "") for entry in entries if isinstance(entry, dict)}
    scenarios = []
    for path in sorted((bundle / "scenarios").glob("*/scenario.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        document.setdefault("scenario_key", path.parent.name)
        scenarios.append(document)
    seeded = (bundle / "seed").is_dir() or bool(contract.get("data_store"))
    return scenario_requirements(
        scenarios, contract, checks, seeded_world=seeded, selection_size=len(scenarios)
    )
