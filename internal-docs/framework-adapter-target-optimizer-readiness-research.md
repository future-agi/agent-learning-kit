# Framework Adapter Target Optimizer Readiness

Date: 2026-06-09

## Why This Exists

Generic target optimization should cover framework adapter configuration without
falling back to prompt search or a specialized framework optimizer helper. This
gate proves that `optimize_target()` can tune one explicit framework adapter
field while simulation and evaluation verify the selected runtime.

## Research Inputs

- https://arxiv.org/abs/2606.05920
  Multi-round agent refinement motivates optimizing executable framework
  runtime choices from observed feedback rather than changing prompt text.
- https://arxiv.org/abs/2606.03892
  Stateful tool environments motivate evaluating framework adapters by runtime
  evidence, tool calls, and state transitions.
- https://arxiv.org/abs/2606.05872
  Framework-agnostic trace metrics support scoring adapter behavior through
  runtime and trace evidence independent of a single framework brand.

## Release Gate

`agent-learn release-check` includes
`framework_adapter_target_optimizer_readiness`. The gate runs
`examples/sdk_framework_adapter_target_optimization.py` locally and requires:

- The manifest is built by
  `agent_learning.optimize.build_target_optimization_manifest`.
- The target task kind is `generic_target`.
- The optimized surface is `framework_adapter_method`.
- The search space is exactly `agent.method`; whole-agent replacement, prompt
  paths, response paths, and world-transition paths are forbidden.
- `agent.input_mode` stays fixed at `dict`, avoiding invalid cross-product
  adapter pairs.
- The selected method is `execute_task`, the rejected method is `run`, and the
  selected adapter stays local with `trace_runtime=True`.
- Framework runtime, framework adapter contract, framework trace, and
  tool-selection metrics close at `1.0`.
- The native framework runtime proof passes at
  `l3_native_framework_runtime_verified`.

## Implementation Rule

Keep this gate about caller-directed path specificity. The existing
`optimize_framework_adapter()` helper can optimize complete framework agents;
this gate proves the generic target helper can optimize a precise adapter field
while the rest of the framework config remains fixed and local.
