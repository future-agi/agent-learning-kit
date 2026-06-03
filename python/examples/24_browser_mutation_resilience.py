"""
Score browser mutation-pack resilience.

Run:
    cd python && python examples/24_browser_mutation_resilience.py
"""

from fi.evals.metrics.agents import evaluate_agent_report


MUTATION_PACK = {
    "kind": "browser_mutation_pack",
    "mutations": [
        {
            "id": "confirm_selector_drift",
            "type": "selector_alias",
            "selector": "#confirm",
            "alternate_selectors": ["#confirm-now"],
            "signals": ["selector_fallback"],
        },
        {
            "id": "cart_storage_drift",
            "type": "storage_drift",
            "signals": ["storage_state"],
        },
        {
            "id": "hydration_runtime_warning",
            "type": "runtime_error",
            "signals": ["runtime_event"],
        },
        {
            "id": "checkout_api_latency",
            "type": "network_latency",
            "signals": ["performance_timing"],
        },
    ],
}

STORAGE_STATE = {
    "origins": [
        {
            "origin": "https://shop.example.com",
            "localStorage": [{"name": "cart_version", "value": "mutated"}],
        }
    ]
}

report = {
    "results": [
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": (
                        "I inspect browser mutations, refresh the snapshot, recheck storage "
                        "and runtime, then use the fallback selector."
                    ),
                    "tool_calls": [
                        {"id": "m", "name": "browser_mutations", "arguments": {}},
                        {"id": "r", "name": "browser_refresh_snapshot", "arguments": {}},
                        {"id": "s", "name": "browser_storage", "arguments": {}},
                        {"id": "rt", "name": "browser_runtime", "arguments": {}},
                        {
                            "id": "c",
                            "name": "browser_click",
                            "arguments": {"selector": "#confirm-now", "action": "click confirm"},
                        },
                    ],
                }
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "browser_mutation_pack"},
                    "data": MUTATION_PACK,
                },
                {
                    "type": "trace",
                    "metadata": {"kind": "browser_trace"},
                    "data": {
                        "kind": "browser_trace",
                        "mutation_pack": MUTATION_PACK,
                        "snapshots": [
                            {
                                "url": "https://shop.example.com/checkout",
                                "dom": "<button id='confirm-now'>Confirm</button>",
                            }
                        ],
                        "action_replay": [
                            {
                                "tool": "browser_click",
                                "selector": "#confirm-now",
                                "success": True,
                                "mutation_id": "confirm_selector_drift",
                                "mutation_type": "selector_alias",
                            }
                        ],
                        "actionability_timeline": [
                            {
                                "mutation_id": "confirm_selector_drift",
                                "checks": {"attached": False},
                                "passed": False,
                            }
                        ],
                        "storage_state": STORAGE_STATE,
                        "runtime_events": [
                            {
                                "type": "runtime_error",
                                "level": "error",
                                "message": "Recoverable hydration warning after mutation.",
                                "mutation_id": "hydration_runtime_warning",
                            }
                        ],
                        "performance_entries": [
                            {
                                "name": "https://shop.example.com/api/checkout",
                                "entry_type": "resource",
                                "duration_ms": 240.0,
                                "mutation_id": "checkout_api_latency",
                            }
                        ],
                    },
                },
            ],
            "events": [
                {"type": "browser_mutation_pack", "name": "browser_mutation_pack_loaded", "payload": MUTATION_PACK},
                {"type": "browser_snapshot", "name": "browser_refresh_snapshot", "payload": {"refreshed": True}},
                {
                    "type": "browser_action",
                    "name": "browser_click",
                    "payload": {
                        "selector": "#confirm-now",
                        "success": True,
                        "mutation_id": "confirm_selector_drift",
                        "mutation_type": "selector_alias",
                    },
                },
            ],
            "metadata": {
                "environment_state": {
                    "browser": {
                        "checkout": {"status": "confirmed"},
                        "storage_state": STORAGE_STATE,
                    }
                }
            },
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_browser_trace": [
            "browser_mutation_pack",
            "selector_alias",
            "storage_drift",
            "runtime_error",
            "performance_timing",
        ],
        "required_browser_mutations": [
            "confirm_selector_drift",
            "cart_storage_drift",
            "hydration_runtime_warning",
            "checkout_api_latency",
        ],
        "browser_mutation_resilience": {
            "required_types": [
                "selector_alias",
                "storage_drift",
                "runtime_error",
                "network_latency",
            ],
            "required_mitigations": [
                "browser_mutations",
                "refresh_snapshot",
                "storage_recheck",
                "runtime_recheck",
                "selector_fallback",
            ],
            "expected_actions": [
                {"selector": "#confirm-now", "success": True, "mutation_id": "confirm_selector_drift"}
            ],
            "expected_storage": {
                "local_storage": {
                    "https://shop.example.com": {"cart_version": "mutated"}
                }
            },
            "expected_state": {"checkout.status": "confirmed"},
            "max_runtime_errors": 1,
        },
        "metric_weights": {"browser_mutation_resilience": 5.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]
print("score:", result.score)
print("passed:", result.passed)
print("browser_mutation_resilience:", metrics.get("browser_mutation_resilience"))
print("browser_trace_coverage:", metrics.get("browser_trace_coverage"))
