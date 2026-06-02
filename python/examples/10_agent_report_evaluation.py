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
            "metadata": {
                "tools": [
                    {
                        "name": "search_order",
                        "parameters": {
                            "type": "object",
                            "properties": {"order_id": {"type": "string"}},
                            "required": ["order_id"],
                            "additionalProperties": False,
                        },
                    }
                ],
                "environment_state": {"case": {"resolved": True}},
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
                {"type": "voice", "name": "tts_output", "payload": {"latency_ms": 420}},
                {
                    "type": "tool_execution",
                    "name": "search_order",
                    "payload": {
                        "arguments": {"order_id": "123"},
                        "success": True,
                        "result": {"resolved": True},
                        "state_updates": {"case": {"resolved": True}},
                    },
                },
                {"type": "state_update", "payload": {"case": {"resolved": True}}},
            ],
            "artifacts": [
                {"type": "image", "uri": "file:///fixtures/receipt.png"},
                {"type": "audio", "uri": "file:///fixtures/caller.wav"},
                {
                    "type": "trace",
                    "metadata": {"kind": "autonomy_loop_trace"},
                    "data": {
                        "kind": "autonomy_loop_trace",
                        "stages_observed": [
                            "observe",
                            "orient",
                            "plan",
                            "act",
                            "verify",
                            "reflect",
                            "memory",
                        ],
                    },
                },
                {
                    "type": "trace",
                    "metadata": {"kind": "framework_trace", "framework": "openai_agents"},
                    "data": {
                        "kind": "framework_trace",
                        "framework": "openai_agents",
                        "signals": ["agent", "model", "tool", "latency"],
                        "spans": [
                            {"id": "agent_1", "name": "agent_span", "signals": ["agent"]},
                            {"id": "model_1", "name": "generation_span", "signals": ["model", "latency"]},
                            {"id": "tool_1", "name": "function_span search_order", "signals": ["tool"]},
                        ],
                    },
                },
                {
                    "type": "trace",
                    "metadata": {"kind": "retrieval_memory_trace"},
                    "data": {
                        "kind": "retrieval_memory_trace",
                        "queries": [
                            {
                                "query": "checkout order 123",
                                "documents": ["order_policy"],
                                "ranked_documents": [
                                    {
                                        "id": "order_policy",
                                        "rank": 1,
                                        "score": 3,
                                        "current": True,
                                    }
                                ],
                            }
                        ],
                        "documents": [
                            {
                                "id": "order_policy",
                                "content": "Order 123 checkout support case resolved.",
                                "version": "v2",
                                "current": True,
                            }
                        ],
                        "memory_reads": [{"key": "order_id", "value": "123"}],
                        "memory_writes": [{"key": "status", "value": "resolved"}],
                        "citations": [{"doc_ids": ["order_policy"], "claim": "checkout resolved"}],
                        "require_current": True,
                    },
                },
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
        "required_artifact_types": ["image", "audio"],
        "required_autonomy_loop": ["observe", "orient", "plan", "act", "verify", "reflect"],
        "required_framework_trace": ["agent", "model", "tool", "latency"],
        "required_retrieval_memory_trace": ["query", "document", "memory_read", "memory_write", "citation"],
        "expected_retrieval_doc_ids": ["order_policy"],
        "require_current_retrieval": True,
        "require_source_grounding": True,
        "max_voice_latency_ms": 1000,
        "expected_state": {"case": {"resolved": True}},
        "expected_tool_outcomes": {
            "search_order": {
                "success": True,
                "result": {"resolved": True},
                "state_updates": {"case": {"resolved": True}},
                "final_state": {"case": {"resolved": True}},
            }
        },
        "success_criteria": ["checkout support case resolved"],
    },
)

print("Score:", result.score)
print("Passed:", result.passed)
for metric in result.cases[0].metrics:
    print(f"{metric.name}: {metric.score:.2f} - {metric.reason}")
