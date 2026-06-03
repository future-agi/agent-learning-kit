"""
Score real-framework multi-agent transcripts locally.

This accepts exported transcript-like records from frameworks such as AutoGen,
CrewAI, or OpenAI Agents SDK traces and checks speakers, speaker order, handoffs,
tool ownership, turn count, and termination evidence without a model or API key.
"""

from fi.evals.metrics.agents import evaluate_agent_report


records = [
    {
        "type": "TextMessage",
        "source": "PlanningAgent",
        "content": "1. WebSearchAgent: search order 123. 2. DataAnalystAgent: verify refund.",
        "signals": ["agent", "model"],
    },
    {
        "type": "ToolCallRequestEvent",
        "source": "WebSearchAgent",
        "content": [{"id": "call_search", "name": "search_policy", "arguments": {"order_id": "123"}}],
        "signals": ["tool"],
    },
    {
        "type": "handoff_span",
        "handoff_from": "triage_agent",
        "handoff_to": "refund_agent",
        "task": "order 123 refund policy escalation",
        "signals": ["handoff"],
    },
    {
        "type": "TextMessage",
        "source": "DataAnalystAgent",
        "content": "The refund is policy-compliant. TERMINATE",
        "termination": "TERMINATE",
        "signals": ["agent", "model"],
    },
]

report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Resolve refund for order 123."},
                {"role": "assistant", "content": "The refund is policy-compliant."},
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "framework_trace", "framework": "autogen"},
                    "data": {"kind": "framework_trace", "framework": "autogen", "events": records},
                }
            ],
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "framework_transcript_quality": {
            "required_speakers": ["PlanningAgent", "WebSearchAgent", "DataAnalystAgent"],
            "expected_speaker_sequence": ["PlanningAgent", "WebSearchAgent", "DataAnalystAgent"],
            "min_turns": 3,
            "expected_messages": [
                {"speaker": "PlanningAgent", "contains": ["WebSearchAgent", "DataAnalystAgent"]},
                {"speaker": "DataAnalystAgent", "contains": ["refund is policy-compliant"]},
            ],
            "expected_handoffs": [
                {
                    "from_agent": "triage_agent",
                    "to_agent": "refund_agent",
                    "task_contains": ["order 123"],
                }
            ],
            "required_tools_by_speaker": {"WebSearchAgent": ["search_policy"]},
            "termination_contains": ["TERMINATE"],
        },
        "metric_weights": {"framework_transcript_quality": 5.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("framework_transcript_quality:", metrics.get("framework_transcript_quality"))
