# Memory Target Optimizer Readiness Research Note

Date: 2026-06-10

## Why This Exists

The generic target optimizer must prove it can optimize memory-layer state, not
only prompts, full agents, world transitions, framework adapter methods, or
multi-agent room rosters. The existing memory optimizer and memory-layer probe
select complete memory bundles. This gate is narrower: retrieval, stores,
records, policies, lineage, observability, artifacts, and the scripted agent
stay fixed while `optimize_target()` searches one explicit memory-lineage
operations path.

## Design

- The cookbook reuses the strong memory fixture from
  `examples/sdk_memory_optimization.py`.
- The base memory lineage intentionally has no operations, so read/write/recall
  evidence is missing.
- The only target candidate path is
  `simulation.environments.1.data.operations`.
- The selected operations restore audited `read`, `write`, and `recall`
  lineage with attribution to `doc_refund_2026`.
- The selected run closes memory lineage coverage, memory lineage quality,
  retrieval attribution, memory integrity, and tool selection at 1.0 while
  keeping task completion above the 0.9 guardrail, and closes native L3
  `memory_lineage_proof`.

## Release Rule

`agent-learn release-check` runs
`examples/sdk_memory_target_optimization.py` as
`memory_target_optimizer_readiness`. The gate requires:

- `agent_learning.optimize.build_target_optimization_manifest` source metadata
  with `generic_target` task kind.
- Search over exactly `simulation.environments.1.data.operations`.
- No whole-agent, prompt, framework-method, multi-agent-roster, or world
  transition search paths.
- Fixed scripted agent, retrieval memory, stores, memory records, policies,
  lineage, observability, and artifacts.
- Selected operation types containing `read`, `write`, and `recall`.
- `agent_memory_lineage_coverage=1.0`,
  `agent_memory_lineage_quality=1.0`, `retrieval_memory_attribution=1.0`,
  `retrieval_context_quality=1.0`, `memory_integrity=1.0`, and
  `tool_selection_accuracy=1.0`, with task completion remaining above the 0.9
  guardrail.
- `agent-learning.optimization.memory-lineage-proof.v1` passed with
  `l3_native_memory_lineage_verified`.
