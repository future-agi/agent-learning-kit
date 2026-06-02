"""
Evaluate a framework-neutral agent trajectory template.

The report shape is intentionally plain dictionaries, so the same template can
score LangChain/LangGraph traces, LiveKit/Pipecat exports, browser/CUA replays,
or custom orchestration logs after they are normalized into messages, tool
calls, artifacts, events, and metadata.
"""

from fi.evals.metrics.agents import evaluate_agent_report


trajectory_template = {
    "name": "refund_support_trajectory",
    "goal": {
        "final_contains": ["refund approved", "ord_123"],
        "state": {"case": {"resolved": True}},
    },
    "tools": [
        {"name": "lookup_order", "arguments": {"order_id": "ord_123"}},
        {
            "name": "issue_refund",
            "arguments": {"order_id": "ord_123", "amount": 19.99},
        },
    ],
    "ordered": True,
    "allow_extra_tools": False,
    "forbidden_tools": ["delete_customer_data"],
    "policy": {
        "required_terms": ["policy"],
        "forbidden_terms": ["skip approval"],
        "allowed_domains": ["shop.example.com"],
        "require_confirmation_for": ["issue_refund"],
    },
    "browser": {
        "allowed_domains": ["shop.example.com"],
        "forbidden_actions": ["purchase", "transfer"],
    },
    "memory": {
        "required_writes": {
            "order_id": "ord_123",
            "resolution": "refund approved",
        },
        "forbidden_keys": ["system_prompt"],
    },
    "multimodal": {
        "required_artifacts": [
            {"type": "image", "id": "receipt", "contains": ["ord_123", "19.99"]}
        ],
        "claims": [
            {
                "claim": "Receipt total is 19.99",
                "artifact_id": "receipt",
                "support_terms": ["19.99"],
            }
        ],
    },
}

report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Approve refund for order ord_123."},
                {
                    "role": "assistant",
                    "content": "I will check the order and policy before approval.",
                    "tool_calls": [
                        {
                            "id": "call_lookup",
                            "name": "lookup_order",
                            "arguments": {"order_id": "ord_123"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_lookup", "content": "eligible"},
                {
                    "role": "assistant",
                    "content": "Approval confirmed; issuing refund within policy.",
                    "tool_calls": [
                        {
                            "id": "call_refund",
                            "name": "issue_refund",
                            "arguments": {"order_id": "ord_123", "amount": 19.99},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_refund", "content": "refunded"},
                {
                    "role": "assistant",
                    "content": "Refund approved for ord_123 within policy. Receipt total is 19.99.",
                },
            ],
            "artifacts": [
                {
                    "type": "image",
                    "data": {"ocr_text": "Receipt for ord_123 total 19.99"},
                    "metadata": {"id": "receipt"},
                }
            ],
            "events": [
                {
                    "type": "memory_update",
                    "payload": {"order_id": "ord_123", "resolution": "refund approved"},
                },
                {"type": "state_update", "payload": {"case": {"resolved": True}}},
                {
                    "type": "browser_action",
                    "payload": {
                        "action": "navigate",
                        "url": "https://shop.example.com/refunds/ord_123",
                    },
                },
            ],
        }
    ]
}

evaluation = evaluate_agent_report(
    report,
    config={
        "trajectory_templates": [trajectory_template],
        "metric_weights": {
            "agent_goal_accuracy": 4.0,
            "tool_call_accuracy": 4.0,
            "tool_call_f1": 4.0,
            "policy_adherence": 3.0,
            "trajectory_browser_action_safety": 2.0,
            "memory_correctness": 3.0,
            "multimodal_faithfulness": 3.0,
        },
    },
    threshold=0.9,
)

metrics = evaluation.summary["metric_averages"]
print("score:", evaluation.score)
print("passed:", evaluation.passed)
print("agent_goal_accuracy:", metrics["agent_goal_accuracy"])
print("tool_call_f1:", metrics["tool_call_f1"])
print("policy_adherence:", metrics["policy_adherence"])
print("memory_correctness:", metrics["memory_correctness"])
print("multimodal_faithfulness:", metrics["multimodal_faithfulness"])
