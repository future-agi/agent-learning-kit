# Multi-Agent Target Optimizer Readiness Research Note

Date: 2026-06-09

## Why This Exists

The generic target optimizer must prove it can optimize multi-agent
configuration, not only prompts, complete agents, world transitions, or
framework adapter methods. The specialized multi-agent optimizer already
selects complete agent/room bundles. This gate is narrower: the agent and room
contract stay fixed, while `optimize_target()` searches one explicit nested
participant roster path.

## Design

- The cookbook reuses the strong scripted agent and room contract from
  `examples/sdk_multi_agent_optimization.py`.
- The base room intentionally omits the `critic` participant while keeping
  `allow_unknown_roles=False`.
- The only target candidate path is
  `simulation.environments.0.data.participants`.
- The weak roster lowers multi-agent coordination quality because the critic
  review is not a known room role.
- The selected roster restores `planner`, `retriever`, and `critic`, passes
  `multi_agent_coordination_quality=1.0`, and closes the native
  `multi_agent_coordination_proof` at L3.

## Release Rule

`agent-learn release-check` runs
`examples/sdk_multi_agent_target_optimization.py` as
`multi_agent_target_optimizer_readiness`. The gate requires:

- `agent_learning.optimize.build_target_optimization_manifest` source metadata
  with `generic_target` task kind.
- Search over exactly `simulation.environments.0.data.participants`.
- No whole-agent, prompt, framework-method, or world-transition search paths.
- Fixed scripted agent responses and fixed non-participant room contract fields.
- Selected room participants containing `planner`, `retriever`, and `critic`.
- `multi_agent_coordination_quality=1.0`, `multi_agent_trace_coverage=1.0`,
  `tool_selection_accuracy=1.0`, and `task_completion=1.0`.
- `agent-learning.optimization.multi-agent-coordination-proof.v1` passed with
  `l3_native_multi_agent_coordination_verified`.
