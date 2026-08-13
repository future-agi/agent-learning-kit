"""Emission: rich records to typed `fi.simulate` Scenarios, a runnable smoke manifest, a report.

One generated record becomes one typed ``Scenario`` (kind ``task``) with a single-persona dataset:
``goal.states`` carries the sub-goal names, ``verification.checks`` carries one named check per
sub-goal (goal-machine vocabulary, with the full checkpoint definition preserved on the check dict),
and ``constraints.declared_tools`` bounds the action space. The persona keeps the legacy
``situation`` / ``outcome`` fields populated (the current simulator prompt drives off them) while the
typed ``knowledge`` facts carry disclosure rules for the instruction-following simulator.
"""

from __future__ import annotations

import json
import os
from typing import Any

from fi.simulate.simulation.models import (
    CoverageDeclaration,
    Persona,
    PersonaFact,
    Scenario,
    ScenarioConstraints,
    ScenarioGoal,
    VerificationSpec,
)

from .contract import AgentContract

_KIND_TO_GOAL_MACHINE = {
    "state": "world_success_condition",
    "tool_call_args": "world_success_condition",
    "conveyed": "world_success_condition",
    "absent": "world_invariant",
    "judge": "eval_template",
}


def _facts(record: dict) -> list[PersonaFact]:
    facts: list[PersonaFact] = []
    for fact in record.get("facts") or []:
        if isinstance(fact, dict) and fact.get("key"):
            facts.append(
                PersonaFact(
                    key=str(fact["key"]),
                    value=str(fact.get("value", "")),
                    disclosure=str(fact.get("disclosure", "on_request")),
                )
            )
    return facts


def _referenced_tools(record: dict, contract: AgentContract) -> list[str]:
    blob = json.dumps(record)
    return sorted(name for name in contract.tool_names() if name in blob)


def to_alk_scenario(record: dict, contract: AgentContract) -> Scenario:
    sub_goals = record.get("sub_goals") or []
    names = [
        str(sg.get("name"))
        for sg in sub_goals
        if isinstance(sg, dict) and sg.get("name")
    ]
    checks: list[dict[str, Any]] = []
    for sub_goal in sub_goals:
        if not isinstance(sub_goal, dict) or not sub_goal.get("name"):
            continue
        checkpoint = sub_goal.get("checkpoint") or {}
        kind = str(checkpoint.get("kind", "judge"))
        checks.append(
            {
                "name": str(sub_goal["name"]),
                "kind": _KIND_TO_GOAL_MACHINE.get(kind, "eval_template"),
                "rung": "settle",
                "checkpoint_kind": kind,
                "deterministic": bool(checkpoint.get("deterministic")),
                "detail": str(checkpoint.get("detail", "")),
                "definition": checkpoint.get("definition") or {},
                "milestone": str(sub_goal.get("milestone", "")),
            }
        )

    outcome = record.get("expected_outcome") or {}
    persona_payload = dict(record.get("persona") or {})
    persona_payload.setdefault("name", "Caller")

    persona = Persona(
        persona=persona_payload,
        situation=str(record.get("agent_input", "")),
        outcome=str(outcome.get("world_state") or record.get("goal", "")),
        knowledge=_facts(record),
    )
    referenced = _referenced_tools(record, contract)
    return Scenario(
        name=str(record.get("id") or record.get("use_case", "scenario")),
        description=f"{record.get('use_case', '')} :: {record.get('situation', '')}".strip(
            " :"
        ),
        dataset=[persona],
        kind="task",
        goal=ScenarioGoal(states=names, success_state=names[-1] if names else None),
        verification=VerificationSpec(checks=checks, threshold=1.0),
        constraints=ScenarioConstraints(
            declared_tools=referenced,
            observable_state=dict((record.get("environment") or {}).get("seed") or {}),
            max_user_knowledge=[fact.key for fact in _facts(record)],
        ),
        coverage=CoverageDeclaration(
            intents=[str(record.get("use_case", ""))],
            tool_obligations=[f"allow:{name}" for name in referenced],
        ),
    )


def smoke_manifest(record: dict, contract: AgentContract) -> dict[str, Any]:
    """A runnable chat-spine manifest for one record: mock tools + world-contract conditions.

    This is the offline proof that a generated scenario's deterministic state checks fire through
    the real goal machine, in the exact shape the manifest loader accepts.
    """
    environment = record.get("environment") or {}
    conditions: list[dict[str, Any]] = []
    for sub_goal in record.get("sub_goals") or []:
        checkpoint = (sub_goal or {}).get("checkpoint") or {}
        definition = checkpoint.get("definition") or {}
        if checkpoint.get("kind") == "state" and definition.get("must"):
            conditions.append(
                {
                    "name": str(sub_goal.get("name")),
                    "must": definition["must"],
                    **(
                        {"forbidden": definition["forbidden"]}
                        if definition.get("forbidden")
                        else {}
                    ),
                }
            )
    states = [c["name"] for c in conditions]
    return {
        "version": "agent-learning.run.v1",
        "name": str(record.get("id", "generated")),
        "agent": {"type": "scripted", "content": "done"},
        "evaluation": {"enabled": False},
        "scenario": {
            "name": str(record.get("id", "generated")),
            "dataset": [
                {
                    "persona": dict(record.get("persona") or {"name": "Caller"}),
                    "situation": str(record.get("agent_input", "")),
                    "outcome": str(
                        (record.get("expected_outcome") or {}).get("world_state", "")
                    ),
                }
            ],
            "goal": {"states": states, "success_state": states[-1] if states else None},
            "verification": {
                "checks": [
                    {"name": name, "kind": "world_success_condition", "rung": "settle"}
                    for name in states
                ]
            },
        },
        "simulation": {
            "engine": "local_text",
            "max_turns": 2,
            "min_turns": 1,
            "environments": [
                {
                    "type": "tool_mock",
                    "tools": dict(environment.get("mock_responses") or {}),
                    "initial_state": dict(environment.get("seed") or {}),
                },
                {
                    "type": "world_contract",
                    "name": "generated_world",
                    "initial_state": dict(environment.get("seed") or {}),
                    "success_conditions": conditions,
                },
            ],
        },
    }


def write_outputs(
    out_dir: str,
    *,
    contract: AgentContract,
    catalog: list[dict],
    records: list[dict],
    rejected: list[dict],
    usage: dict[str, Any],
) -> None:
    scenarios_dir = os.path.join(out_dir, "scenarios")
    alk_dir = os.path.join(out_dir, "alk")
    os.makedirs(scenarios_dir, exist_ok=True)
    os.makedirs(alk_dir, exist_ok=True)
    current = {f"{record.get('id', 'scenario')}.json" for record in records}
    for directory in (scenarios_dir, alk_dir):
        for stale in set(os.listdir(directory)) - current:
            os.remove(os.path.join(directory, stale))

    def _dump(path: str, payload: Any) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)

    _dump(os.path.join(out_dir, "contract.json"), contract.model_dump())
    _dump(os.path.join(out_dir, "subgoal_catalog.json"), catalog)
    _dump(os.path.join(out_dir, "usage.json"), usage)
    for record in records:
        slug = str(record.get("id", "scenario"))
        _dump(os.path.join(scenarios_dir, f"{slug}.json"), record)
        alk = to_alk_scenario(record, contract)
        _dump(os.path.join(alk_dir, f"{slug}.json"), alk.model_dump(exclude_none=True))
    if records:
        _dump(
            os.path.join(out_dir, "smoke_manifest.json"),
            smoke_manifest(records[0], contract),
        )
    with open(os.path.join(out_dir, "report.md"), "w", encoding="utf-8") as fh:
        fh.write(render_report(contract, catalog, records, rejected, usage))


def render_report(
    contract: AgentContract,
    catalog: list[dict],
    records: list[dict],
    rejected: list[dict],
    usage: dict[str, Any],
) -> str:
    catalog_names = {str(entry.get("name")) for entry in catalog}
    reuse: dict[str, int] = {}
    deterministic = 0
    total_checks = 0
    for record in records:
        for sub_goal in record.get("sub_goals") or []:
            name = str((sub_goal or {}).get("name", ""))
            total_checks += 1
            if ((sub_goal or {}).get("checkpoint") or {}).get("deterministic"):
                deterministic += 1
            if name in catalog_names:
                reuse[name] = reuse.get(name, 0) + 1

    lines = [
        f"# Generated scenarios: {contract.agent}",
        "",
        f"- scenarios accepted: **{len(records)}**, rejected by review: {len(rejected)}",
        f"- checkpoints: {total_checks}, deterministic: {deterministic} "
        f"({(100 * deterministic // max(total_checks, 1))}%)",
        f"- shared sub-goals reused across scenarios: "
        f"{sum(1 for count in reuse.values() if count >= 2)} of {len(catalog)} catalog entries",
        f"- model usage: {usage}",
        "",
        "| # | Use case | Situation | Sub-goals | Det |",
        "|---|---|---|---|---|",
    ]
    for index, record in enumerate(records, 1):
        sub_goals = record.get("sub_goals") or []
        det = sum(
            1
            for sg in sub_goals
            if ((sg or {}).get("checkpoint") or {}).get("deterministic")
        )
        lines.append(
            f"| {index} | {record.get('use_case', '')} | {record.get('situation', '')} "
            f"| {len(sub_goals)} | {det}/{len(sub_goals)} |"
        )
    if reuse:
        lines += ["", "## Sub-goal roll-up (appearances across scenarios)", ""]
        for name, count in sorted(reuse.items(), key=lambda item: -item[1]):
            lines.append(f"- `{name}`: {count}")
    if rejected:
        lines += ["", "## Rejected in review", ""]
        for record in rejected:
            lines.append(
                f"- {record.get('id', '?')}: {record.get('_reject_reason', 'rejected')}"
            )
    return "\n".join(lines) + "\n"
