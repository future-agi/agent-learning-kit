from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from agent_learning import configure, optimize


REQUIRED_ENV = "AGENT_LEARNING_SDK_ORCHESTRATION_EXAMPLE_KEY"


def weak_agent() -> dict[str, Any]:
    return {
        "type": "scripted",
        "responses": [
            {
                "content": (
                    "I inspected the refund request but did not apply the "
                    "world transition or collect orchestration evidence."
                ),
                "tool_calls": [],
            }
        ],
    }


def strong_agent() -> dict[str, Any]:
    return {
        "type": "scripted",
        "responses": [
            {
                "content": (
                    "First, because I optimize refund orchestration stack across "
                    "world framework retrieval memory lineage multi agent review "
                    "evidence: optimized stack approves refund, records trace, "
                    "current policy grounding, provenance, critic-reviewed "
                    "reconciliation."
                ),
                "tool_calls": [
                    {
                        "id": "approve_refund",
                        "name": "apply_world_transition",
                        "arguments": {"id": "approve_refund"},
                    },
                    {
                        "id": "framework_status",
                        "name": "framework_trace_status",
                        "arguments": {},
                    },
                ],
            },
            {
                "content": (
                    "Next, since I optimize refund orchestration stack across "
                    "world framework retrieval memory lineage multi agent review "
                    "evidence: optimized stack approves refund, records trace, "
                    "current policy grounding, provenance, critic-reviewed "
                    "reconciliation."
                ),
                "tool_calls": [
                    {
                        "id": "retrieve_policy",
                        "name": "retrieve_documents",
                        "arguments": {"query": "current refund policy"},
                    },
                    {
                        "id": "read_policy",
                        "name": "read_document",
                        "arguments": {"id": "doc_refund_2026"},
                    },
                    {
                        "id": "cite_policy",
                        "name": "cite_sources",
                        "arguments": {"doc_ids": ["doc_refund_2026"]},
                    },
                    {
                        "id": "memory_lineage",
                        "name": "agent_memory_lineage_status",
                        "arguments": {},
                    },
                    {
                        "id": "retrieval_memory",
                        "name": "retrieval_memory_status",
                        "arguments": {},
                    },
                ],
            },
            {
                "content": (
                    "Finally, therefore I optimize refund orchestration stack "
                    "across world framework retrieval memory lineage multi agent "
                    "review evidence: optimized stack approves refund, records "
                    "trace, current policy grounding, provenance, critic-reviewed "
                    "reconciliation."
                ),
                "tool_calls": [
                    {
                        "id": "room_status",
                        "name": "room_status",
                        "arguments": {},
                    },
                    {
                        "id": "critic_review",
                        "name": "request_review",
                        "arguments": {
                            "reviewer": "critic",
                            "target": "refund orchestration decision",
                            "criteria": ["policy", "memory", "world"],
                        },
                    },
                    {
                        "id": "reconcile",
                        "name": "reconcile",
                        "arguments": {
                            "summary": "approved refund orchestration accepted",
                            "accepted_source": "critic",
                            "conflicts": [],
                            "participants": ["planner", "retriever", "critic"],
                        },
                    },
                ],
            },
        ],
    }


def weak_stack() -> dict[str, Any]:
    return {
        "name": "weak-orchestration-stack",
        "world_contract": {
            "name": "refund-world",
            "actors": ["agent", "customer"],
            "resources": ["refund"],
            "initial_state": {"refund": {"status": "pending"}},
            "transitions": [],
            "success_conditions": [
                {"id": "refund_approved", "must": {"refund.status": "approved"}}
            ],
        },
        "framework_trace": {
            "framework": "langgraph",
            "spans": [],
            "adapter_required_signals": ["planner", "tool", "policy"],
        },
        "retrieval_memory": {
            "documents": [
                {
                    "id": "doc_refund_2025",
                    "title": "Archived refund policy",
                    "content": "Archived policy requires manual review.",
                    "current": False,
                }
            ],
            "require_current": True,
        },
        "agent_memory_lineage": {
            "name": "weak-lineage",
            "target": {"agent": "refund-agent"},
            "stores": [],
            "memories": [],
            "operations": [],
            "lineage": [],
        },
        "multi_agent_room": {
            "participants": {"planner": {"name": "planner", "role": "planner"}},
            "allow_unknown_roles": True,
            "state": {"case": {"status": "triage"}},
        },
    }


def strong_stack() -> dict[str, Any]:
    return {
        "name": "strong-orchestration-stack",
        "world_contract": {
            "name": "refund-world",
            "actors": ["agent", "customer"],
            "resources": ["refund"],
            "initial_state": {"refund": {"status": "pending"}},
            "transitions": [
                {
                    "id": "approve_refund",
                    "actor": "agent",
                    "resource": "refund",
                    "action": "approve_refund",
                    "required": True,
                    "preconditions": {"refund.status": "pending"},
                    "effects": {"refund.status": "approved"},
                    "postconditions": {"refund.status": "approved"},
                    "signals": ["refund_resolution"],
                }
            ],
            "success_conditions": [
                {"id": "refund_approved", "must": {"refund.status": "approved"}}
            ],
        },
        "framework_trace": {
            "framework": "langgraph",
            "spans": [
                {
                    "id": "planner",
                    "name": "planner.invoke",
                    "input": "refund workflow",
                    "output": "approved",
                    "tool_calls": [{"name": "framework_trace_status"}],
                    "signals": ["planner", "tool", "policy"],
                }
            ],
            "adapter_required_signals": ["planner", "tool", "policy"],
            "adapter_required_mappings": {"tool": ["tool_name"]},
        },
        "retrieval_memory": {
            "documents": [
                {
                    "id": "doc_refund_2026",
                    "title": "Current refund policy",
                    "content": (
                        "The current refund policy allows approved refunds "
                        "when framework trace, source grounding, memory "
                        "provenance, and critic review are recorded."
                    ),
                    "current": True,
                }
            ],
            "memory": {"prior_case": "manual_review"},
            "require_current": True,
        },
        "agent_memory_lineage": {
            "name": "refund-memory-lineage",
            "target": {"agent": "refund-agent", "tenant": "tenant_a"},
            "stores": [{"id": "episodic", "type": "vector", "tenant": "tenant_a"}],
            "memories": [
                {
                    "id": "refund_decision",
                    "store": "episodic",
                    "status": "active",
                    "source_ids": ["doc_refund_2026"],
                    "tenant": "tenant_a",
                }
            ],
            "operations": [
                {
                    "id": "write_policy_memory",
                    "operation": "write",
                    "store": "episodic",
                    "memory_id": "refund_decision",
                    "status": "allowed",
                    "policy_decision": "allowed",
                    "attribution": {"source": "doc_refund_2026"},
                }
            ],
            "lineage": [
                {
                    "from": "doc_refund_2026",
                    "to": "refund_decision",
                    "type": "source_attribution",
                }
            ],
        },
        "multi_agent_room": {
            "participants": {
                "planner": {"name": "planner", "role": "planner"},
                "retriever": {"name": "retriever", "role": "retriever"},
                "critic": {"name": "critic", "role": "critic"},
            },
            "expected_reviews": [
                {
                    "reviewer": "critic",
                    "target_contains": "refund orchestration",
                    "criteria": ["policy", "memory", "world"],
                }
            ],
            "expected_reconciliation": {
                "summary_contains": "approved refund orchestration",
                "accepted_source": "critic",
                "conflicts_empty": True,
            },
            "allow_unknown_roles": False,
            "state": {"case": {"status": "resolved"}},
        },
    }


def evaluation_config() -> dict[str, Any]:
    return {
        "task_description": (
            "Optimize a refund orchestration stack across world, framework, "
            "retrieval, memory lineage, and multi-agent review evidence."
        ),
        "expected_result": (
            "The optimized orchestration stack approves refund, records "
            "framework trace, current policy grounding, memory provenance, "
            "and critic-reviewed reconciliation."
        ),
        "required_tools": [
            "apply_world_transition",
            "framework_trace_status",
            "retrieve_documents",
            "read_document",
            "cite_sources",
            "agent_memory_lineage_status",
            "retrieval_memory_status",
            "room_status",
            "request_review",
            "reconcile",
        ],
        "available_tools": [
            "apply_world_transition",
            "framework_trace_status",
            "retrieve_documents",
            "read_document",
            "cite_sources",
            "agent_memory_lineage_status",
            "retrieval_memory_status",
            "room_status",
            "request_review",
            "reconcile",
        ],
        "success_criteria": [
            "approves refund",
            "framework trace",
            "current policy grounding",
            "memory provenance",
            "critic-reviewed reconciliation",
        ],
        "required_world_contract": ["world_contract", "transition", "refund"],
        "world_contract_quality": {
            "required_transitions": ["approve_refund"],
            "min_completed_transitions": 1,
            "require_all_required_transitions": True,
            "required_success_conditions": ["refund_approved"],
            "terminal_status": "success",
            "expected_state": {"refund": {"status": "approved"}},
        },
        "metric_weights": {
            "world_contract_quality": 8.0,
            "tool_selection_accuracy": 4.0,
            "task_completion": 2.0,
            "goal_progress": 1.0,
        },
    }


def build_manifest() -> dict[str, Any]:
    return optimize.build_orchestration_optimization_manifest(
        name="sdk-orchestration-optimization",
        required_env=[REQUIRED_ENV],
        agent_candidates=[weak_agent(), strong_agent()],
        stack_candidates=[weak_stack(), strong_stack()],
        evaluation_config=evaluation_config(),
        threshold=0.9,
        min_turns=3,
        max_turns=3,
    )


def run(output_path: str | Path | None = None) -> dict[str, Any]:
    api_key = os.environ.get(REQUIRED_ENV)
    if not api_key:
        raise RuntimeError(f"Set {REQUIRED_ENV} before running this example.")
    configure(api_key=api_key)

    result = optimize.optimize_manifest(
        build_manifest(),
        manifest_path=Path(__file__).with_suffix(".json"),
    )
    if output_path is not None:
        path = Path(output_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(result, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
    return result


if __name__ == "__main__":
    destination = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    payload = run(destination)
    if destination is None:
        print(json.dumps(payload, indent=2, sort_keys=True, default=str))
