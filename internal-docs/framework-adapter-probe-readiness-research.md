# Framework Adapter Probe Readiness

## Purpose

The bring-your-own framework path has to work before a user has written a full
manifest. The local probe flow closes that gap: inspect candidate methods,
select the runnable method/input mode, prove runtime evidence, and promote the
selected adapter into a normal `agent-learning.run.v1` manifest.

## Local Contract

- `simulate.run_framework_adapter_probe()` proves one explicit adapter shape
  against expected output, tool calls, events, runtime trace, deterministic
  callable signature evidence, observed input/output contract evidence, and
  local adapter contract evidence.
- `simulate.discover_framework_adapter()` ranks local method/input candidates
  without calling HTTP or hosted framework endpoints.
- `optimize.optimize_framework_adapter_probe()` searches explicit or
  auto-discovered adapter candidates and emits a native probe proof.
- `optimize.build_framework_run_manifest_from_probe_optimization()` preserves
  the selected method, input mode, target, proof, and discovery metadata in the
  promoted run manifest.
- One-call helpers keep the full target-to-evaluated-run workflow local while
  still emitting the same adapter contract and runtime metrics.

## Release Gate

`agent-learn release-check` runs the representative probe, discovery,
optimization, auto-discovery, promotion, and one-call cookbooks as
`framework_adapter_probe_readiness`. The gate requires custom
`execute_task(dict)` coverage plus LangGraph-style `ainvoke(dict)` and
LangChain-style `invoke(dict)`, Pipecat-style `process(dict)`, and
OpenAI-compatible `chat.completions.create(messages=...)` nested-method
promotion plus LiveKit `run_session(dict)` session promotion and
provider-response promotion with required provider kwargs and normalized
`provider_response` state plus Browser Use `execute_task(dict)` CUA trace
promotion plus AutoGen-style `run(task=...)` message-history promotion and
OpenAI Agents-style `execute_task(dict)` handoff-transcript promotion plus
LangGraph `execute_task(dict)` workflow and orchestration trace promotions plus
LiveKit `execute_task(dict)` lifecycle trace promotion plus MCP
`execute_task(dict)` tool-session promotion and A2A `send_message(dict)`
protocol-trace promotion plus Agent Learning Kit `execute_task(dict)` agent
control-plane promotion, callable
signatures to be inspectable, observed I/O contracts and call contracts to cover
the selected probe cases, discovery to be used where expected, probe proofs to
pass, promoted manifests to carry proof/discovery metadata, and evaluated runs
to close framework runtime, adapter call-contract, observed-I/O,
adapter-contract, framework-trace, transcript, workflow, orchestration,
lifecycle, protocol, agent control-plane, and tool-selection metrics. The
message-history and handoff-transcript promotions additionally require
`message_history` state, transcript events, `framework_transcript_quality`,
tool-call/tool-response transcript evidence, and for handoff transcripts
`framework_handoffs` state with handoff/review/reconciliation counts,
participants, passed review, and accepted reconciliation evidence. The lifecycle
promotion
additionally requires `framework_lifecycle_trace` state, runtime required-state
keys, lifecycle/runtime event types, artifact kinds, phase/session/retry/error/
recovery/cancellation/resume/cleanup/checkpoint counts, state persistence, and
terminal cleanup evidence. The MCP/A2A promotions
additionally require protocol state keys, runtime required-state keys, event
types, artifact kinds, summary counts, and summary membership evidence so
protocol semantics cannot regress behind passing metric averages. The
`agent_control_plane_promotion` contract similarly requires
`agent_trust_boundary_model` and `agent_control_plane` state keys, runtime
required-state keys, trust/control event types, artifact kinds, no summary gaps,
required control rates of 1.0, no unmitigated high-risk threats, no uncontained
high-risk incidents, and explicit approval, rollback, budget, containment, and
audit evidence.

The optimization surfaces also render a first-class `framework_adapter_probe`
report/action card. Release-check verifies that the card is local-only,
contains the selected adapter method/input mode, exposes the native probe proof,
callable signature, and observed I/O contract, and can export those artifacts
through report actions. This keeps BYO-framework adapter optimization visible
to Future AGI UI/report surfaces instead of leaving the proof buried inside
optimizer history.
