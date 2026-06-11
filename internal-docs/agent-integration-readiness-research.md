# Agent Integration Readiness Research

Date: 2026-06-09

This note pins the V1 release-check bar for provider, channel, and framework
integration readiness. The goal is not to claim live hosted coverage for every
provider. The goal is to keep a local, reproducible contract that proves the
Agent Learning Kit can simulate and evaluate the integration shape before a
customer wires real credentials.

## Sources

- LiveKit Agents documentation: https://docs.livekit.io/agents/
- Pipecat documentation: https://docs.pipecat.ai/
- Vapi documentation: https://docs.vapi.ai/

## Release Gate

`agent-learn release-check` owns the `agent_integration_readiness` check. The
gate must run both:

- `examples/sdk_agent_integration_optimization.py`
- `examples/sdk_agent_integration_simulation.py`

The optimization path must select the verified `agent_integration` environment
candidate, preserve candidate lineage, round-trip the written artifact, and
pass deterministic `simulation_evidence` scoring. The direct simulation path
must emit a normal `agent-learning.run.v1` artifact with the
`agent_integration_manifest` state, provider readiness card, events, metrics,
and rerun/optimization actions.

## Minimum Evidence

The V1 readiness matrix must prove:

- 16 providers/framework rows: LiveKit, Vapi, Retell, Bland, ElevenLabs,
  Deepgram, Agora, Pipecat, Twilio, LangChain, LangGraph, OpenAI Agents,
  AutoGen, CrewAI, LlamaIndex, and PydanticAI.
- 22 channels across chat, voice, phone, SIP, WebRTC, WebSocket, media streams,
  webhooks, SMS/WhatsApp, LiveKit/Pipecat transport links, provider workflow
  APIs, STT/TTS, realtime state, and analysis/pathways surfaces.
- Trace framework coverage for LangChain, LangGraph, OpenAI Agents, AutoGen,
  CrewAI, LlamaIndex, PydanticAI, Pipecat, and LiveKit.
- Verified or live-verified credential status for every row.
- Zero missing providers, channels, trace frameworks, credentials, failed
  sessions, weak layers, or weak metrics.
- Passing `agent_integration_coverage`, `agent_integration_quality`,
  `tool_selection_accuracy`, `framework_trace_coverage`,
  `voice_interaction_quality`, `streaming_interaction_quality`, and
  `voice_turn_taking`.

## Robustness Rule

New provider or framework work must first extend the local readiness matrix and
release-check evidence. Live credentials, hosted transports, or external service
checks can be layered on later, but the V1 claim remains local-first: the
simulation, eval, report, and optimizer surfaces must already know how to
represent the provider/channel/framework contract without making network calls.
