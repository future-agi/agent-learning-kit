# Agent Learning Environment 10x Robustness Gate

Date: 2026-06-09

## Why This Exists

Agent Learning Kit owns the framework and environment robustness contract. In
V1, `environment_10x_robustness` means the release artifact must prove a broad
local contract across environment replay, simulation, evaluation, optimization,
framework adapters, protocol/tool routing, browser/CUA, realtime voice, memory,
multi-agent coordination, red-team execution, orchestration replay, and
regression promotion.

OpenEnv and Gymnasium are compatibility inputs for reset/step/state-shaped
traces. They are not runtime dependencies and should not be positioned as the
product center.

## Research Inputs

- https://huggingface.co/docs/openenv/index
  OpenEnv frames agent environments around reusable production-grade
  environments, MCP-compatible access, sandboxing, and reset/step/state-style
  lifecycle evidence.
- https://gymnasium.farama.org/api/env/
  Gymnasium keeps the baseline environment API centered on `reset()` and
  `step()` returns with observation, reward, terminated/truncated, and info
  metadata.
- https://modelcontextprotocol.io/docs/concepts/tools
  MCP tools make action routing explicit through named tool calls, schemas, and
  tool results.
- https://a2a-protocol.org/latest/specification/
  A2A adds agent cards, tasks, messages, artifacts, and terminal task status as
  portable multi-agent protocol evidence.

## Release Gate

`agent-learn release-check` now includes `environment_10x_robustness`. The gate
does not rerun separate workloads. Instead, it aggregates existing local proof
outputs and requires at least ten independent axes to pass:

- `environment_replay_contract`: reset, step, action routing, reward/done,
  metadata, sandbox/isolation, deterministic reset, no external service, and
  failure injection, including OpenEnv/Gymnasium-shaped compatibility traces.
- `cross_framework_simulation_matrix`: local contracts for LangChain,
  LangGraph, LiveKit, Pipecat, Browser Use, OpenEnv, Gymnasium, MCP, A2A, and
  the rest of the V1 framework matrix.
- `local_evaluation_gates`: environment replay coverage/quality, framework
  runtime, and framework adapter metrics all score 1.0, with OpenEnv/Gymnasium
  shapes kept as compatibility aliases.
- `adaptive_optimizer_recovery`: agent-opt rejects weak/partial environment
  replay bundles and selects the verified replay.
- `native_framework_adapter_probe_promotion`: Agent Learning's adapter probe
  optimizer promotes custom `execute_task(dict)`, LangGraph `ainvoke(dict)`,
  and LangChain `invoke(dict)` adapters into normal `agent-learning.run.v1`
  artifacts with proof metadata, discovery evidence where required, adapter
  call-contract metrics, observed-I/O metrics, and framework runtime/trace/tool
  metric floors.
- `protocol_tool_routing`: MCP and A2A adapters preserve protocol state,
  events, artifacts, and tool/task records.
- `browser_cua_resilience`: browser/CUA probes prove DOM/screenshot grounding,
  mutation evidence, and prompt-injection-surface avoidance.
- `realtime_voice_streaming`: realtime probes prove voice frames, stream
  events, routing, no drops, and no streaming errors.
- `memory_lineage_retrieval`: memory probes prove current retrieval, lineage,
  isolation, governance, and no poisoning.
- `multi_agent_coordination`: multi-agent probes prove role boundaries,
  handoffs, review, reconciliation, and terminal room state.
- `world_orchestration_replay`: orchestration probes prove world, framework,
  retrieval, memory, multi-agent, and tool evidence in one promoted run.
- `redteam_pen_test_suite`: the framework-adapter trinity suite runs local
  simulation plus red-team coverage with adversarial metrics.
- `regression_promotion_replay`: baseline, compare, report,
  promote-to-regression, and replay all remain executable.

## Implementation Rule

Do not use the 10x claim unless `environment_10x_robustness` is green. New live
environment shortcuts can be added later, but the Agent Learning contract must
stay local-first and evidence-backed: every claimed robustness axis needs an
executable release-check source, metric floor, and reproducible artifact.
