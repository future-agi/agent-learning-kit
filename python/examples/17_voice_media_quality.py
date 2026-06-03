"""
Score decoded voice media quality locally.

Use this after a LiveKit/Pipecat/realtime export has been normalized with
decoded WAV/PCM media metadata. It checks sample rate, duration, RMS/peak level,
clipping, speakers, and trace coverage without a model or API key.
"""

from fi.evals.metrics.agents import evaluate_agent_report


report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Replay the billing call."},
                {"role": "assistant", "content": "The billing call was decoded and routed."},
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "voice_trace"},
                    "data": {
                        "kind": "voice_trace",
                        "export_framework": "livekit",
                        "waveforms": [
                            {
                                "id": "caller_wav",
                                "speaker": "caller",
                                "decoded_audio": True,
                                "media_format": "wav",
                                "sample_rate_hz": 24000,
                                "duration_ms": 900,
                                "sample_count": 21600,
                                "rms_db": -18.4,
                                "peak_db": -9.8,
                                "clipping_ratio": 0.0,
                            }
                        ],
                        "diarization": [
                            {"speaker": "caller", "start_ms": 0, "end_ms": 900},
                            {"speaker": "agent", "start_ms": 920, "end_ms": 1400},
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
        "required_voice_speakers": ["caller", "agent"],
        "max_voice_clipping_ratio": 0.01,
        "min_voice_sample_rate_hz": 16000,
        "min_voice_duration_ms": 750,
        "max_voice_duration_ms": 1500,
        "min_voice_rms_db": -35,
        "max_voice_peak_db": -0.1,
        "required_voice_trace": [
            "livekit_export",
            "waveform",
            "media",
            "diarization",
            "sample_rate",
            "duration",
            "rms",
            "peak",
            "clipping",
        ],
    },
    threshold=0.85,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("voice_interaction_quality:", metrics.get("voice_interaction_quality"))
print("voice_trace_coverage:", metrics.get("voice_trace_coverage"))
