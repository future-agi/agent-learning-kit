"""
Score semantic and masked browser screenshot diffs locally.

This catches browser/CUA visual regressions where raw pixels changed, but only
some semantic regions are allowed to count after masking dynamic regions such
as clocks, cursors, or live counters.
"""

from fi.evals.metrics.agents import evaluate_agent_report


semantic_diff = {
    "id": "confirm_semantic_delta",
    "source": "pixel_diff",
    "algorithm": "pixel_absdiff_v1",
    "changed_pixels": 5,
    "changed_ratio": 0.3125,
    "changed_regions": ["session_clock", "status_banner"],
    "semantic_regions": [
        {"name": "session_clock", "role": "timer", "changed": True, "masked": True},
        {"name": "status_banner", "role": "status", "changed": True, "allowed": True},
        {"name": "total_due", "role": "amount", "changed": False, "forbidden": True},
    ],
    "semantic_summary": {
        "changed_regions": ["session_clock", "status_banner"],
        "changed_semantic_regions": ["session_clock", "status_banner"],
        "masked_regions": ["session_clock"],
        "masked_changed_regions": ["session_clock"],
        "effective_changed_regions": ["status_banner"],
        "required_regions": ["status_banner"],
        "missing_required_regions": [],
        "allowed_regions": ["status_banner"],
        "unexpected_changed_regions": [],
        "forbidden_regions": ["total_due"],
        "forbidden_regions_changed": [],
        "only_allowed_regions_changed": True,
    },
}

report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Confirm checkout with visual evidence."},
                {
                    "role": "assistant",
                    "content": "The status banner changed to confirmed and the dynamic clock was masked.",
                },
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "browser_trace"},
                    "data": {
                        "kind": "browser_trace",
                        "action_replay": [
                            {
                                "tool": "browser_click",
                                "selector": "#confirm",
                                "success": True,
                                "screenshot_diff": semantic_diff,
                            }
                        ],
                        "screenshot_diffs": [semantic_diff],
                    },
                }
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_browser_trace": ["semantic_screenshot_diff", "masked_screenshot_diff"],
        "expected_browser_screenshot_diffs": [
            {
                "id": "confirm_semantic_delta",
                "semantic_regions": ["status_banner"],
                "allowed_regions": ["status_banner"],
                "masked_regions": ["session_clock"],
                "forbidden_regions": ["total_due"],
                "only_allowed_regions_changed": True,
            }
        ],
        "metric_weights": {"browser_trace_coverage": 4.0, "browser_grounding_quality": 5.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("browser_trace_coverage:", metrics.get("browser_trace_coverage"))
print("browser_grounding_quality:", metrics.get("browser_grounding_quality"))
