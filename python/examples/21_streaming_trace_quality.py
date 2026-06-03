"""
Score framework-neutral streaming trace quality.

Run:
    cd python && python examples/21_streaming_trace_quality.py
"""

from fi.evals.metrics.agents import evaluate_agent_report


STREAMING_TRACE = {
    "kind": "streaming_trace",
    "framework": "mixed-realtime",
    "events": [
        {"id": "start", "type": "start", "timestamp_ms": 1000, "signals": ["start", "stream"]},
        {
            "id": "chunk_1",
            "type": "chunk",
            "delta": "Refund ",
            "timestamp_ms": 1120,
            "latency_ms": 120,
            "signals": ["chunk", "latency", "langgraph"],
        },
        {
            "id": "tool_delta",
            "type": "tool_delta",
            "tool_call": {"name": "lookup_order", "arguments": "{\"order_id\":\"ord_123\""},
            "timestamp_ms": 1148,
            "signals": ["tool_delta", "openai_agents"],
        },
        {"id": "interruption", "type": "interruption", "timestamp_ms": 1175, "signals": ["interruption"]},
        {"id": "drop", "type": "drop", "dropped": 1, "timestamp_ms": 1180, "signals": ["drop"]},
        {"id": "recovered", "type": "event", "status": "resumed", "timestamp_ms": 1210, "signals": ["recovered"]},
        {
            "id": "chunk_2",
            "type": "chunk",
            "delta": "approved.",
            "gap_ms": 18,
            "timestamp_ms": 1228,
            "signals": ["chunk", "gap"],
        },
        {"id": "usage", "type": "usage", "usage": {"output_tokens": 9}, "timestamp_ms": 1240, "signals": ["usage"]},
        {"id": "final", "type": "final", "status": "completed", "timestamp_ms": 1250, "signals": ["final"]},
    ],
    "signals": [
        "stream",
        "chunk",
        "tool_delta",
        "interruption",
        "recovered",
        "drop",
        "latency",
        "gap",
        "usage",
        "final",
        "state",
    ],
    "summary": {
        "chunk_count": 2,
        "tool_delta_count": 1,
        "interruption_count": 1,
        "recovered_interruption_count": 1,
        "dropped_event_count": 1,
        "error_count": 0,
        "first_token_latency_ms": 120,
        "max_gap_ms": 28,
        "assembled_text": "Refund approved.",
        "completion_status": "completed",
    },
    "state": {"response": {"status": "completed"}},
}


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Stream refund approval."},
                {
                    "role": "assistant",
                    "content": "Streaming trace inspected.",
                    "tool_calls": [
                        {"id": "status", "name": "streaming_trace_status", "arguments": {}},
                        {"id": "chunks", "name": "list_stream_events", "arguments": {"signal": "chunk"}},
                    ],
                },
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "streaming_trace", "framework": "mixed-realtime"},
                    "data": STREAMING_TRACE,
                }
            ],
            "metadata": {"environment_state": {"streaming_trace": STREAMING_TRACE}},
        }
    ]
}


result = evaluate_agent_report(
    report,
    config={
        "required_streaming_trace": [
            "stream",
            "chunk",
            "tool_delta",
            "interruption",
            "recovered",
            "drop",
            "latency",
            "gap",
            "usage",
            "final",
            "state",
        ],
        "streaming_trace_quality": {
            "expected_output_contains": ["Refund approved"],
            "required_chunks": ["Refund ", "approved."],
            "expected_chunk_sequence": ["Refund ", "approved."],
            "expected_tool_deltas": [{"name": "lookup_order", "arguments": {"order_id": "ord_123"}}],
            "min_chunk_count": 2,
            "min_tool_delta_count": 1,
            "max_first_token_latency_ms": 200,
            "max_gap_ms": 50,
            "max_dropped_events": 1,
            "max_error_count": 0,
            "require_completion": True,
            "require_interruption_recovery": True,
            "expected_state": {"response": {"status": "completed"}},
        },
        "metric_weights": {
            "streaming_trace_coverage": 4.0,
            "streaming_interaction_quality": 5.0,
        },
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]
print("score:", result.score)
print("passed:", result.passed)
print("streaming_trace_coverage:", metrics.get("streaming_trace_coverage"))
print("streaming_interaction_quality:", metrics.get("streaming_interaction_quality"))
