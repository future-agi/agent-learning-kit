# World Hooks Readiness

Date: 2026-06-09

## Why This Exists

World hooks are the native executable-world surface for Agent Learning Kit.
They should not be a thin HTTP hook integration. For the trinity goal, the
optimizer must search complete in-process world-state bundles, the simulator
must replay those bundles deterministically, and evaluation must score the
terminal world state plus the hook contract.

## Research Inputs

- https://arxiv.org/abs/2606.05558
  Offline agent evaluation with world-model rollouts motivates proving agent
  behavior against a simulated world trajectory rather than only final text.
- https://arxiv.org/abs/2606.03892
  Verified stateful execution environments motivate explicit state transitions,
  replay semantics, and executable tool-world contracts.
- https://arxiv.org/abs/2606.02372
  Closed-loop world-model and policy co-evolution motivates optimizing the
  world model and the policy/harness together instead of treating the world as
  static prompt context.
- https://arxiv.org/abs/2605.30880
  Executable, inspectable world models motivate local verifier contracts and
  reproducible transition evidence.

## Release Gate

`agent-learn release-check` now includes `world_hooks_readiness`. The gate runs
`examples/sdk_world_hooks_optimization.py` locally and requires:

- The optimization manifest to use task kind `world_hooks`, search
  `simulation.environments`, and target model, harness, world, tools, security,
  planner, and evaluator layers.
- The selected candidate to include `stateful_tool_world` and `world_contract`
  environments.
- The native hook contract to be
  `agent-learning.world-hooks-contract.v1` with mode
  `native_world_state_hooks`, runtime `in_process`, callable hooks for
  `stateful_tool_world_status`, `localize_temporal_takeover`, and
  `apply_world_transition`, and no external service requirement.
- The world-hook proof to pass at L3 with checks for no external hook,
  executable world-model verifier, closed hook contract, closed state
  transitions, closed world-contract invariants, contained adversarial pressure,
  contained memory/provenance channels, and closed metric evidence.
- Local report and action evidence to expose the `world_hooks` card plus
  report, promote, rerun, proof export, contract export, and replay-lock export
  actions.
- Regression promotion and replay to freeze the local world-hook bundle, reject
  endpoint/auth/key/secret/token surfaces, and replay with
  `world_hook_contract_quality=1.0` and `world_contract_quality=1.0`.

## Implementation Rule

Keep world hooks native and local-first. External endpoint hooks can be added
later as explicit user-selected workloads, but the release readiness gate must
continue proving that AgentOptimizer, simulate, and evals can optimize, run,
score, promote, and replay executable in-process world-state hooks without
hosted evaluator, optimizer, observability, or hook services.
