"""
Score browser storage-state and runtime capture locally.

This catches browser/CUA harness gaps where the final page looks correct but
cookies, localStorage/sessionStorage, runtime errors, or performance timings
were not captured for replay.
"""

from fi.evals.metrics.agents import evaluate_agent_report


storage_state = {
    "cookies": [
        {
            "name": "checkout_session",
            "value": "confirmed",
            "domain": "shop.example.com",
            "path": "/",
        }
    ],
    "origins": [
        {
            "origin": "https://shop.example.com",
            "localStorage": [{"name": "checkout_status", "value": "confirmed"}],
            "sessionStorage": [{"name": "last_action", "value": "confirm"}],
        }
    ],
}

report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Confirm checkout with browser runtime evidence."},
                {
                    "role": "assistant",
                    "content": "Checkout is confirmed and browser storage/runtime evidence was captured.",
                },
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "browser_trace"},
                    "data": {
                        "kind": "browser_trace",
                        "storage_state": storage_state,
                        "runtime_events": [
                            {
                                "type": "page_error",
                                "level": "error",
                                "message": "Recoverable hydration mismatch handled.",
                            }
                        ],
                        "performance_entries": [
                            {
                                "name": "https://shop.example.com/api/checkout",
                                "entry_type": "resource",
                                "duration_ms": 120.0,
                            }
                        ],
                        "runtime_summary": {
                            "runtime_event_count": 1,
                            "error_count": 1,
                            "performance_entry_count": 1,
                            "max_duration_ms": 120.0,
                        },
                        "final_state": {
                            "browser": {
                                "storage_state": storage_state,
                                "runtime_events": [
                                    {
                                        "type": "page_error",
                                        "level": "error",
                                        "message": "Recoverable hydration mismatch handled.",
                                    }
                                ],
                                "performance_entries": [
                                    {
                                        "name": "https://shop.example.com/api/checkout",
                                        "entry_type": "resource",
                                        "duration_ms": 120.0,
                                    }
                                ],
                            }
                        },
                    },
                }
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_browser_trace": [
            "storage_state",
            "cookie",
            "local_storage",
            "session_storage",
            "runtime_error",
            "performance_entry",
            "performance_timing",
        ],
        "expected_browser_storage": {
            "cookies": {"checkout_session": "confirmed"},
            "local_storage": {"https://shop.example.com": {"checkout_status": "confirmed"}},
            "session_storage": {"https://shop.example.com": {"last_action": "confirm"}},
        },
        "expected_browser_runtime_events": [
            {"type": "page_error", "message_contains": "hydration mismatch"}
        ],
        "forbidden_browser_runtime_events": [
            {"type": "runtime_error", "message_contains": "Fatal checkout crash"}
        ],
        "max_browser_performance_duration_ms": 150,
        "metric_weights": {
            "browser_trace_coverage": 4.0,
            "browser_action_outcome": 3.0,
            "browser_grounding_quality": 4.0,
        },
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("browser_trace_coverage:", metrics.get("browser_trace_coverage"))
print("browser_action_outcome:", metrics.get("browser_action_outcome"))
print("browser_grounding_quality:", metrics.get("browser_grounding_quality"))
