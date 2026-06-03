"""
Score cross-trial memory and skill quality locally.

This catches agents that write the wrong long-term keys, fail to recall them in
later trials, or regress a reusable skill procedure. No model, framework, or API
key is required.
"""

from fi.evals.metrics.agents import evaluate_agent_report


def framework_trace(memory_events, skill_steps):
    return {
        "type": "trace",
        "metadata": {"kind": "framework_trace", "framework": "langgraph"},
        "data": {
            "kind": "framework_trace",
            "framework": "langgraph",
            "events": [
                *memory_events,
                {
                    "method": "updates",
                    "skill": {
                        "name": "refund_policy_check",
                        "steps": skill_steps,
                    },
                    "signals": ["skill"],
                },
            ],
        },
    }


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Refund order ord_123."},
                {"role": "assistant", "content": "I saved the refund context."},
            ],
            "artifacts": [
                framework_trace(
                    [
                        {
                            "method": "updates",
                            "memory": {"operation": "write", "key": "order_id", "value": "ord_123"},
                            "signals": ["memory"],
                        },
                        {
                            "method": "updates",
                            "memory": {"operation": "write", "key": "policy_version", "value": "2026-05"},
                            "signals": ["memory"],
                        },
                    ],
                    ["lookup", "verify", "respond"],
                )
            ],
        },
        {
            "messages": [
                {"role": "user", "content": "Continue the refund workflow."},
                {"role": "assistant", "content": "I recalled the refund context."},
            ],
            "artifacts": [
                framework_trace(
                    [
                        {
                            "method": "values",
                            "memory": {"operation": "recall", "key": "order_id"},
                            "signals": ["memory"],
                        },
                        {
                            "method": "values",
                            "memory": {"operation": "recall", "key": "policy_version"},
                            "signals": ["memory"],
                        },
                    ],
                    ["lookup", "verify", "respond"],
                )
            ],
        },
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "expected_cross_trial_memory": {
            "required_keys": ["order_id", "policy_version"],
            "required_recall_keys": ["order_id", "policy_version"],
            "forbidden_keys": ["raw_user_secret"],
            "min_precision": 1.0,
            "min_recall": 1.0,
            "min_trials_present": 2,
            "require_persistence": True,
        },
        "expected_cross_trial_skills": [
            {
                "name": "refund_policy_check",
                "required_steps": ["lookup", "verify", "respond"],
                "min_trials_present": 2,
                "require_persistent_after_first": True,
            }
        ],
    },
    threshold=0.85,
)

cross_trial = result.summary["cross_trial_memory_skill"]

print("score:", result.score)
print("passed:", result.passed)
print("cross_trial_memory_skill:", cross_trial["score"])
print("checks:", cross_trial["matched_checks"], "/", cross_trial["check_count"])
