"""
Score authenticated paginated export coverage.

Run:
    cd python && python examples/26_export_auth_pagination.py
"""

from fi.evals.metrics.agents import evaluate_agent_report


EXPORT_METADATA = {
    "trace_export": {
        "export_source": "inline_paginated_export",
        "page_count": 2,
        "pagination_enabled": True,
        "auth_enabled": True,
        "auth_header_names": ["Authorization"],
    }
}

report = {
    "results": [
        {
            "messages": [{"role": "assistant", "content": "Paginated exports inspected."}],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "framework_trace", "framework": "traceai"},
                    "data": {
                        "kind": "framework_trace",
                        "framework": "traceai",
                        "spans": [{"name": "OpenAI chat", "signals": ["model"]}],
                        "metadata": EXPORT_METADATA,
                    },
                },
                {
                    "type": "trace",
                    "metadata": {"kind": "voice_trace"},
                    "data": {
                        "kind": "voice_trace",
                        "export_framework": "livekit",
                        "utterances": [{"id": "caller_1", "transcript": "Billing issue."}],
                        "export_metadata": EXPORT_METADATA,
                    },
                },
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_framework_trace": ["export", "export_auth", "export_pagination"],
        "required_voice_trace": ["export", "export_auth", "export_pagination"],
        "metric_weights": {"framework_trace_coverage": 3.0, "voice_trace_coverage": 3.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]
print("score:", result.score)
print("passed:", result.passed)
print("framework_trace_coverage:", metrics.get("framework_trace_coverage"))
print("voice_trace_coverage:", metrics.get("voice_trace_coverage"))
