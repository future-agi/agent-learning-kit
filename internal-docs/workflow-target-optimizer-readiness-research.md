# Workflow Target Optimizer Readiness Research Note

Date: 2026-06-10

## Why This Exists

AgentOptimizer must optimize durable workflow graph behavior directly, not only
prompts, whole agents, world transitions, framework adapter methods,
multi-agent rosters, memory operations, or orchestration spans. Workflow systems
make routing, checkpointing, replay, interrupt handling, and recovery part of
the agent contract. The target optimizer needs a deterministic local slice that
searches those workflow fields while the scripted agent and task stay fixed.

## Research Inputs

- `ai-evaluation` already scores workflow trace coverage and graph quality from
  normalized `workflow_trace` state, workflow events, artifacts, checkpoints,
  routes, interrupts, replay, and step-level tool evidence.
- `simulate-sdk` already normalizes local framework workflow exports into
  `workflow_trace` evidence through the workflow trace adapter cookbook.
- The optimizer slice should reuse those signals as Agent Learning-native
  evidence. OpenEnv, Gymnasium, or hosted workflow providers are compatibility
  inputs only, not the primary optimizer contract.

## Implemented Gate Shape

The `workflow_target_optimizer_readiness` gate runs a local workflow graph
target optimization cookbook and requires:

- `agent_learning.optimize.build_target_optimization_manifest` source metadata
  with `generic_target` task kind.
- Search paths limited to `simulation.environments.0.data.trace`, so the patch
  changes workflow graph, route, checkpoint, replay, interrupt, write, final
  state, and recovery evidence without changing the agent.
- No whole-agent, prompt, framework-method, workflow-hook, endpoint, or generic
  orchestration-span search paths.
- Fixed scripted agent, task inputs, workflow environment wrapper, and
  evaluation config.
- Selected workflow evidence that restores complete graph topology, route
  decisions, durable checkpoints, replay, resolved interrupt, writes, final
  state, and step-level tool evidence.
- Selected workflow evidence must retain source-framework coverage for
  LangGraph, CrewAI, and LlamaIndex exports while remaining one canonical Agent
  Learning workflow trace.
- Passing local `ai-evaluation` workflow trace coverage and graph-quality
  metrics, plus task completion and tool-selection metrics.
- Deterministic local-only execution through `simulate-sdk`; no external
  service dependency, no live provider calls, and no secret leakage.

## Implementation Rule

Keep this slice narrower than workflow hook optimization and stateful framework
adapter readiness. Those gates prove hooks and framework normalization. This
gate proves `agent-opt` can optimize a precise workflow graph target as
first-class Agent Learning state while evaluation and simulation verify the
selected workflow behavior locally.

The current release gate uses one deterministic optimizer run and requires the
selected trace to carry `source_frameworks=["crewai", "langgraph",
"llamaindex"]`. A later profile matrix can run the same target path once per
framework export shape without weakening the local-only evidence bar.
