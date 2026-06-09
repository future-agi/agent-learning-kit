# Generic Target Optimizer Readiness

Date: 2026-06-09

## Why This Exists

AgentOptimizer should not be limited to prompt or whole-agent search. V1 needs
executable proof that a caller can provide an explicit manifest dot path and
have the optimizer tune only that world, framework, memory, retrieval, policy,
or orchestration surface.

## Research Inputs

- https://arxiv.org/abs/2406.12045
  Tau-bench evaluates agents through interactive task state, so optimizer
  feedback should be able to change the environment/world contract that governs
  success, not just response text.
- https://arxiv.org/abs/2408.04682
  ToolSandbox uses stateful tool execution and scenario milestones, which maps
  to explicit optimizer search paths over tools, state, and world transitions.
- https://arxiv.org/abs/2308.03688
  AgentBench motivates broad interactive-environment coverage. A generic target
  helper keeps the optimizer surface open for future frameworks beyond a single
  environment adapter shape.

## Release Gate

`agent-learn release-check` includes
`generic_target_optimizer_readiness`. The gate runs
`examples/sdk_target_optimization.py` locally and requires this contract:

- The manifest is `agent-learning.optimization.v1` and its target metadata
  source is `agent_learning.optimize.build_target_optimization_manifest`.
- The target task kind is `generic_target`.
- The search space is exactly
  `simulation.environments.0.data.transitions`; no `agent`,
  `agent.responses`, or `agent.responses.0.tool_calls` paths are searchable.
- The fixed scripted agent remains present as runtime input, but the selected
  optimizer patch does not touch agent or prompt state.
- The selected world-contract transition is `approve_refund`, terminal status
  is `success`, invariants remain clean, and `refund.status` ends as
  `approved`.
- Best-candidate metrics for world-contract quality, world-contract coverage,
  and tool-selection accuracy are all `1.0`.

## Implementation Rule

Keep this gate path-exact. Task/world optimizer readiness may prove broad
multi-surface repair, but generic target readiness proves caller-directed
optimization of one explicit surface. OpenEnv and Gymnasium remain
compatibility inputs for environment replay; they are not required for this
native Agent Learning optimizer contract.
