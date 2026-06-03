"""
Score WebRTC getStats-style voice evidence locally.

Use this after a LiveKit/WebRTC export has been normalized into voice trace
metadata. It checks RTP, track, codec, audio-level, jitter, packet loss, and
speaker evidence without a model or API key.
"""

from fi.evals.metrics.agents import evaluate_agent_report


webrtc_stats = [
    {
        "id": "inbound_audio_1",
        "type": "inbound-rtp",
        "kind": "audio",
        "trackIdentifier": "caller-track",
        "codecId": "codec_opus",
        "packetsReceived": 1000,
        "packetsLost": 5,
        "jitter": 0.012,
        "audioLevel": 0.18,
    },
    {
        "id": "remote_inbound_audio_1",
        "type": "remote-inbound-rtp",
        "kind": "audio",
        "fractionLost": 0.004,
        "jitter": 0.006,
    },
    {
        "id": "codec_opus",
        "type": "codec",
        "mimeType": "audio/opus",
        "payloadType": 111,
    },
]

report = {
    "results": [
        {
            "messages": [
                {"role": "user", "content": "Replay the billing call."},
                {"role": "assistant", "content": "The WebRTC stats pass quality gates."},
            ],
            "artifacts": [
                {
                    "type": "trace",
                    "metadata": {"kind": "voice_trace"},
                    "data": {
                        "kind": "voice_trace",
                        "export_framework": "livekit",
                        "webrtc_stats": webrtc_stats,
                        "diarization": [
                            {"speaker": "caller", "start_ms": 0, "end_ms": 900},
                            {"speaker": "agent", "start_ms": 940, "end_ms": 1300},
                        ],
                    },
                }
            ],
            "metadata": {
                "environment_state": {
                    "voice": {
                        "webrtc_stats": webrtc_stats,
                        "diarization": [
                            {"speaker": "caller", "start_ms": 0, "end_ms": 900},
                            {"speaker": "agent", "start_ms": 940, "end_ms": 1300},
                        ],
                    }
                }
            },
        }
    ]
}

result = evaluate_agent_report(
    report,
    config={
        "required_voice_speakers": ["caller", "agent"],
        "max_voice_jitter_ms": 20,
        "max_voice_packet_loss_pct": 1.0,
        "required_voice_trace": [
            "livekit_export",
            "webrtc",
            "rtp",
            "track",
            "codec",
            "audio_level",
            "jitter",
            "packet_loss",
            "diarization",
        ],
    },
    threshold=0.9,
)

metrics = result.summary["metric_averages"]

print("score:", result.score)
print("passed:", result.passed)
print("voice_interaction_quality:", metrics.get("voice_interaction_quality"))
print("voice_trace_coverage:", metrics.get("voice_trace_coverage"))
