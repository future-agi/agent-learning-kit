# Framework Lifecycle Adapter Research

## Sources

- LangGraph durable execution persists execution state, supports interruption,
  replay, and recovery from prior checkpoints. Source:
  https://docs.langchain.com/oss/python/langgraph/durable-execution
- LangGraph persistence stores graph state as checkpoints per thread and exposes
  state history for replay. Source:
  https://docs.langchain.com/oss/python/langgraph/persistence
- CrewAI Flows expose start/listen/router steps with persisted flow state and
  conditional routing. Source: https://docs.crewai.com/en/concepts/flows
- LiveKit Agents model work around agent sessions, workers, jobs, and room
  lifecycle boundaries. Source: https://docs.livekit.io/agents/
- Pipecat pipelines expose frame-based realtime processing where interruptions,
  cancellation, and pipeline cleanup need to be observable. Source:
  https://docs.pipecat.ai/

## Implementation Rules

Keep lifecycle support local-first:

- Normalize only explicit lifecycle fields such as `framework_lifecycle_trace`,
  `lifecycle_trace`, `lifecycle_phases`, `lifecycle_events`, `retry_events`,
  `recovery_events`, `cancellation_events`, `resume_events`, and
  `teardown_events`.
- Reuse `normalize_framework_lifecycle_trace` so adapter outputs feed the same
  mature `framework_lifecycle_coverage` and `framework_lifecycle_quality`
  metrics used by framework certification manifests.
- Preserve setup, tool registration, session start, invocation/model/tool
  phases, streaming, checkpointing, retry/recovery, cancellation, resume,
  cleanup, terminal status, state persistence, and error counts.
- Emit `framework_lifecycle_trace` state, a trace artifact,
  `framework_lifecycle_phase` events, and a `framework_lifecycle_trace` event so
  agent reports can inspect both per-phase and whole-trace evidence.
- When promotion auto-builds eval config, derive lifecycle requirements from
  the selected adapter output summary. Do not require unobserved lifecycle
  signals, and do not turn wrapper-generated runtime trace artifacts into
  lifecycle proof.
- `agent-learn release-check` now runs
  `examples/sdk_framework_adapter_lifecycle_trace.py` as
  `stateful_framework_adapter_readiness`; the gate requires local
  `execute_task(dict)` selection, retry/recovery, cancellation, resume, cleanup,
  state persistence, artifacts, and passing lifecycle coverage/quality metrics.

## Cookbook Contract

`examples/sdk_framework_adapter_lifecycle_trace.py` demonstrates the intended
shape with a local LiveKit/Pipecat-style adapter. The selected adapter must emit:

- ten lifecycle phases from setup through shutdown,
- tool registration and a lifecycle status tool call,
- one invocation error and one recovered retry,
- streaming, checkpoint, cancel, resume, and cleanup evidence,
- persisted final state proving an approved refund decision,
- evaluator-visible lifecycle coverage and lifecycle quality metrics at 1.0.
