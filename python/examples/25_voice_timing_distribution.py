"""
Score voice timing-distribution quality.

Run:
    cd python && python examples/25_voice_timing_distribution.py
"""

from fi.evals.metrics.agents import evaluate_agent_report


TIMING_DISTRIBUTION = {
    "kind": "voice_timing_distribution",
    "stage_order": ["vad", "eou", "stt", "llm", "tts", "turn"],
    "stages": {
        "vad": {"samples_ms": [18, 20, 22], "source": "vad_metrics"},
        "eou": {"samples_ms": [95, 105, 110], "source": "eou_metrics"},
        "stt": {"samples_ms": [170, 190, 210], "source": "stt_metrics"},
        "llm": {"samples_ms": [240, 260, 280], "source": "llm_metrics"},
        "tts": {"samples_ms": [280, 300, 320], "source": "tts_metrics"},
        "turn": {"samples_ms": [780, 820, 840, 860], "source": "session_metrics"},
    },
}

report = {
    "results": [
        {
            "messages": [
                {
                    "role": "assistant",
                    "content": "I inspected timing evidence and answered quickly.",
                    "tool_calls": [
                        {"id": "timing", "name": "voice_timing", "arguments": {}},
                        {"id": "stt", "name": "transcribe_audio", "arguments": {"id": "caller_1"}},
                        {"id": "tts", "name": "speak", "arguments": {"text": "I can help."}},
                    ],
                }
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "voice_trace"},
                    "data": {
                        "kind": "voice_trace",
                        "utterances": [{"id": "caller_1", "transcript": "Billing issue."}],
                        "timing_distribution": TIMING_DISTRIBUTION,
                        "latency_profile": {"stt": [170, 190, 210], "tts": [280, 300, 320]},
                    },
                }
            ],
            "events": [
                {
                    "type": "voice_timing",
                    "name": "voice_timing_distribution_ready",
                    "payload": TIMING_DISTRIBUTION,
                }
            ],
            "metadata": {
                "environment_state": {
                    "voice": {
                        "timing_distribution": TIMING_DISTRIBUTION,
                    }
                }
            },
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_voice_trace": [
            "timing_distribution",
            "timing_stage",
            "vad",
            "eou",
            "stt",
            "llm",
            "tts",
            "turn",
        ],
        "voice_timing_distribution": {
            "required_stages": ["vad", "eou", "stt", "llm", "tts", "turn"],
            "required_order": ["vad", "eou", "stt", "llm", "tts", "turn"],
            "min_samples_per_stage": 3,
            "max_stage_p95_ms": {
                "vad": 24,
                "eou": 112,
                "stt": 212,
                "llm": 282,
                "tts": 322,
                "turn": 870,
            },
            "max_turn_p95_ms": 870,
        },
        "metric_weights": {"voice_timing_distribution_quality": 5.0},
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]
print("score:", result.score)
print("passed:", result.passed)
print("voice_timing_distribution_quality:", metrics.get("voice_timing_distribution_quality"))
print("voice_trace_coverage:", metrics.get("voice_trace_coverage"))
