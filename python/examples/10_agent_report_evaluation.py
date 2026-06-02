"""
Evaluate an agent simulation report locally.

This example uses a plain dict shaped like a simulate-sdk TestReport. You can
pass a real `fi.simulate.TestReport` object to `evaluate_agent_report` too.
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "persona": {
                "situation": "Resolve checkout support case.",
                "outcome": "Checkout support case resolved.",
            },
            "transcript": "Agent searched the order and resolved checkout.",
            "messages": [
                {"role": "user", "content": "Resolve checkout for order 123."},
                {
                    "role": "assistant",
                    "content": "First I will search the order because I need its status.",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "name": "search_order",
                            "arguments": {"order_id": "123"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "resolved"},
                {"role": "assistant", "content": "Checkout support case resolved."},
            ],
            "events": [
                {
                    "type": "memory_update",
                    "payload": {"order_id": "123", "status": "resolved"},
                },
                {"type": "state_update", "payload": {"case": {"resolved": True}}},
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_tools": ["search_order"],
        "available_tools": ["search_order", "refund_order"],
        "memory_allowed_keys": ["order_id", "status"],
        "expected_state": {"case": {"resolved": True}},
        "success_criteria": ["checkout support case resolved"],
    },
)

print("Score:", result.score)
print("Passed:", result.passed)
for metric in result.cases[0].metrics:
    print(f"{metric.name}: {metric.score:.2f} - {metric.reason}")
