"""
Score framework-neutral world contract quality.

Run:
    cd python && python examples/22_world_contract_quality.py
"""

from fi.evals.metrics.agents import evaluate_agent_report


WORLD_CONTRACT = {
    "kind": "world_contract",
    "name": "refund_world",
    "actors": [{"id": "support_agent"}, {"id": "customer"}],
    "resources": [{"id": "case"}, {"id": "refund_policy"}],
    "transitions": [
        {"id": "verify_identity", "required": True, "signals": ["transition", "milestone"]},
        {"id": "check_policy", "required": True, "signals": ["transition", "policy"]},
        {"id": "issue_refund", "required": True, "signals": ["transition", "tool"]},
    ],
    "transition_log": [
        {"id": "verify_identity", "status": "success", "required": True},
        {"id": "check_policy", "status": "success", "required": True},
        {"id": "issue_refund", "status": "success", "required": True},
    ],
    "invariant_results": [
        {"id": "refund_requires_identity", "pass": True},
        {"id": "refund_requires_policy", "pass": True},
    ],
    "success_results": [{"id": "refund_resolved", "pass": True}],
    "policy_gates": [{"id": "identity_gate"}],
    "adversarial_surfaces": [{"id": "user_message", "type": "prompt_injection"}],
    "signals": [
        "actor",
        "resource",
        "transition",
        "completed_transition",
        "required_transition",
        "invariant",
        "success_condition",
        "policy",
        "adversarial_surface",
        "state",
        "success",
    ],
    "summary": {
        "completed_transition_count": 3,
        "required_transition_count": 3,
        "completed_required_transition_count": 3,
        "forbidden_transition_count": 0,
        "violation_count": 0,
        "invariant_violation_count": 0,
        "success_condition_pass_count": 1,
        "success_condition_count": 1,
        "terminal_status": "success",
    },
    "state": {
        "case": {
            "status": "resolved",
            "identity_verified": True,
            "policy_checked": True,
            "refund_issued": True,
        }
    },
}


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Resolve refund order ord_123."},
                {
                    "role": "assistant",
                    "content": "I verified identity, checked policy, and issued the refund.",
                    "tool_calls": [
                        {"id": "status", "name": "world_contract_status", "arguments": {}},
                        {"id": "refund", "name": "apply_world_transition", "arguments": {"id": "issue_refund"}},
                    ],
                },
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "world_contract", "name": "refund_world"},
                    "data": WORLD_CONTRACT,
                }
            ],
            "metadata": {"environment_state": {"world_contract": WORLD_CONTRACT}},
        }
    ]
}


result = evaluate_agent_report(
    report,
    config={
        "required_world_contract": [
            "actor",
            "resource",
            "transition",
            "completed_transition",
            "required_transition",
            "invariant",
            "success_condition",
            "policy",
            "adversarial_surface",
            "state",
            "success",
        ],
        "world_contract_quality": {
            "required_actors": ["support_agent", "customer"],
            "required_resources": ["case", "refund_policy"],
            "required_transitions": [
                {"id": "verify_identity", "status": "success"},
                {"id": "check_policy", "status": "success"},
                {"id": "issue_refund", "status": "success"},
            ],
            "min_completed_transitions": 3,
            "require_all_required_transitions": True,
            "require_all_invariants_pass": True,
            "required_invariants": ["refund_requires_identity", "refund_requires_policy"],
            "required_success_conditions": ["refund_resolved"],
            "max_violation_count": 0,
            "max_forbidden_transitions": 0,
            "required_terminal_status": "success",
            "expected_state": {
                "case": {
                    "status": "resolved",
                    "identity_verified": True,
                    "policy_checked": True,
                    "refund_issued": True,
                }
            },
        },
        "metric_weights": {"world_contract_coverage": 4.0, "world_contract_quality": 5.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]
print("score:", result.score)
print("passed:", result.passed)
print("world_contract_coverage:", metrics.get("world_contract_coverage"))
print("world_contract_quality:", metrics.get("world_contract_quality"))
