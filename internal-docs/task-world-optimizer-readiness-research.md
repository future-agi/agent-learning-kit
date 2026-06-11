# Task World Optimizer Readiness

Date: 2026-06-09

## Why This Exists

The optimizer layer should repair more than prompt text. For the V1 "holy
trinity" contract, Agent Learning Kit needs executable proof that simulation,
evaluation, and optimization can close an arbitrary task world by changing both
agent behavior and environment/world state.

## Research Inputs

- https://arxiv.org/abs/2406.12045
  Tau-bench frames agent evaluation as interactive tool use against user
  simulation and database-backed task state, with success determined by the
  final task state rather than text alone.
- https://arxiv.org/abs/2408.04682
  ToolSandbox emphasizes stateful tool execution, scenario milestones, and
  tool-call correctness, which maps directly to local world-transition
  execution checks.
- https://arxiv.org/abs/2308.03688
  AgentBench evaluates agents across diverse interactive environments, which
  supports keeping task/world optimization generic across environment and
  orchestration surfaces.

## Release Gate

`agent-learn release-check` now includes `task_world_optimizer_readiness`. The
gate runs `examples/sdk_task_world_optimization.py` locally and requires the
following contract:

- The SDK example builds an `agent-learning.optimization.v1` manifest with a
  `world_contract` environment and no starting world transitions.
- The optimizer target includes search paths for the whole agent, agent tool
  calls, and `simulation.environments.0.data.transitions`.
- The optimizer target layers include planner, tools, world, environment, and
  evaluator.
- The evaluator requires `apply_world_transition`, world-contract evidence, and
  metric weights for world-contract quality, world-contract coverage,
  tool-selection accuracy, and task completion.
- The selected candidate patches both
  `agent.responses.0.tool_calls` and
  `simulation.environments.0.data.transitions`.
- The selected world transition is `approve_refund`, the terminal world status
  is `success`, the required transition is completed, invariants remain clean,
  and `refund.status` ends as `approved`.
- The optimization result round-trips to disk, passes optimizer governance, and
  closes the required summary metrics locally without external evaluator or
  provider services.

## Implementation Rule

Keep task/world optimizer readiness tied to executable local evidence. New
framework, memory, policy, red-team, or orchestration knobs may use the same
optimizer helper, but they should preserve the core proof: agent behavior and
world/environment state are both searchable surfaces, and the final evaluator
must validate the terminal task state.
