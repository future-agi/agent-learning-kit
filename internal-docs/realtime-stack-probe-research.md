# Realtime Stack Probe Research Note

Date: 2026-06-08

## Why This Exists

Realtime voice/streaming manifests already exercise local `voice` and
`streaming_trace` environments. They still need the same cheap preflight path as
framework, memory, and multi-agent rooms: a user should be able to pass local
LiveKit/Pipecat-style voice exports or manifest-style fixtures, prove routing,
audio quality, timing, and stream tool deltas locally, then promote the selected
stack into the normal `agent-learning.run.v1` realtime simulation path.

## Current Realtime Signals

- LiveKit Agents centers realtime applications around sessions, speech/audio
  pipelines, tools, handoffs, and telephony/WebRTC integration. Probe
  implication: voice fixtures should preserve route decisions, STT/TTS timing,
  audio quality, and tool calls as structured evidence instead of only final
  text. Source: https://docs.livekit.io/agents/
- Pipecat models realtime voice agents as pipelines of processors that exchange
  frames. Probe implication: replayable frame, timing, and interruption evidence
  should be normalized before optimization. Sources:
  https://docs.pipecat.ai/guides/learn/pipeline and
  https://docs.pipecat.ai/server/frames/overview
- Twilio Media Streams exposes bidirectional WebSocket media events for live
  audio streaming. Probe implication: streaming traces need event ordering,
  latency/gap checks, and completion/drop/error evidence. Source:
  https://www.twilio.com/docs/voice/media-streams
- Vapi documents voice-assistant tool calls/functions as part of call behavior.
  Probe implication: realtime probes should require stream tool-delta evidence
  and not accept a transcript-only voice pass. Source:
  https://docs.vapi.ai/assistants/tools

## Implementation Rule

Keep realtime support local-first:

- Accept manifest-style `voice` plus `streaming_trace` candidates and explicit
  `environments` bundles.
- Reject HTTP/HTTPS targets and export sources by default.
- Emit `voice` and `streaming_trace` environments so existing
  `voice_interaction_quality`, `voice_timing_distribution_quality`,
  `streaming_trace_coverage`, and `streaming_interaction_quality` metrics can
  score the promoted run.
- Require transcript, route call, TTS, audio frames, sample-rate closure, timing
  stages, audio quality, stream chunks, stream tool deltas, completion, and no
  dropped/error stream events before a probe is considered closed.
- Use `optimize.optimize_realtime_stack_probe()` to select among local realtime
  candidates cheaply, then use
  `optimize.build_realtime_run_manifest_from_probe_optimization()` when the
  selected stack should become a normal evaluated realtime simulation.
