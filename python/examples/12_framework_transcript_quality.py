"""
Score LangGraph/LangChain event-stream transcript quality locally.

Use this when you already captured framework events from `stream_events` or an
export. The report is plain dictionaries, so no LangChain/LangGraph dependency
or API key is required.
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Refund order ord_123."},
                {"role": "assistant", "content": "Refund approved for order ord_123."},
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "framework_trace", "framework": "langgraph"},
                    "data": {
                        "kind": "framework_trace",
                        "framework": "langgraph",
                        "events": [
                            {
                                "name": "message support_agent",
                                "method": "messages",
                                "namespace": ["refund_graph:run_1", "support_agent:task_1"],
                                "node": "support_agent",
                                "subgraph": "refund_graph",
                                "message_text": "I will inspect the order first.",
                                "signals": ["agent", "model"],
                            },
                            {
                                "name": "tool lookup_order",
                                "method": "tools",
                                "namespace": ["refund_graph:run_1", "support_agent:task_1"],
                                "tool_name": "lookup_order",
                                "signals": ["tool"],
                            },
                            {
                                "name": "tool issue_refund",
                                "method": "tools",
                                "namespace": ["refund_graph:run_1", "support_agent:task_1"],
                                "tool_name": "issue_refund",
                                "signals": ["tool"],
                            },
                            {
                                "name": "updates policy_node",
                                "method": "updates",
                                "namespace": ["refund_graph:run_1", "policy_node:task_2"],
                                "node": "policy_node",
                                "subgraph": "refund_graph",
                                "state": {"case": {"status": "resolved", "approval": "captured"}},
                                "signals": ["state"],
                            },
                            {
                                "name": "final output",
                                "method": "values",
                                "namespace": ["refund_graph:run_1", "support_agent:task_1"],
                                "final_output": "Refund approved for order ord_123.",
                                "signals": ["state"],
                            },
                        ],
                    },
                }
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "framework_transcript_quality": {
            "required_event_methods": ["messages", "tools", "updates"],
            "required_nodes": ["support_agent", "policy_node"],
            "required_subgraphs": ["refund_graph"],
            "expected_tool_sequence": ["lookup_order", "issue_refund"],
            "expected_state": {"case": {"status": "resolved", "approval": "captured"}},
            "output_contains": ["Refund approved for order ord_123"],
        },
        "metric_weights": {"framework_transcript_quality": 4.0},
        "success_criteria": ["Refund approved for order ord_123"],
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("framework_transcript_quality:", metrics.get("framework_transcript_quality"))
