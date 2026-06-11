# Framework Adapter Matrix Optimization Readiness Research

The matrix optimization cookbook makes AgentOptimizer search a framework
support surface, not a prompt. The candidate being optimized is
`simulation.environments`: a weak framework-adapter matrix competes with a
verified local matrix that covers LangChain, LangGraph, LlamaIndex, CrewAI,
AutoGen, OpenAI Agents, LiveKit, and Pipecat.

This release gate keeps the ownership boundary clear. OpenEnv/Gymnasium remain
compatibility shapes elsewhere; this proof is Agent Learning-native and verifies
that framework breadth, modality coverage, local executable fixtures,
no-external-target policy, and evaluator evidence can be selected together.

## Sources

- https://arxiv.org/abs/2605.18747 is used for treating executable code and
  harnesses as first-class agent optimization artifacts.
- https://arxiv.org/abs/2605.13357 is used for harness engineering patterns
  where scenarios, adapters, metrics, and report state are selected together.
- https://arxiv.org/abs/2606.04990 is used for trace-backed provenance: the
  selected matrix must reappear in report state, not only in the manifest.
- https://arxiv.org/abs/2606.05922 is used for retrospective harness
  optimization: weak and verified candidates are compared through local run
  evidence.

## Release Contract

`framework_adapter_matrix_optimization_readiness` runs
`examples/sdk_framework_adapter_matrix_optimization.py` with a local release
key. The gate requires:

- `required_env=["AGENT_LEARNING_SDK_FRAMEWORK_MATRIX_OPT_KEY"]` with no
  serialized secret leakage.
- `optimization.target.layers` covering `framework`, `integration`, `harness`,
  and `evaluator`.
- The only search path is `simulation.environments`.
- At least two candidates, where the weak candidate has lower framework count
  than the verified candidate.
- The verified and selected matrix have zero `external_target_count` and zero
  `requires_external_service_count`.
- The selected patch is exactly `simulation.environments`.
- `framework_adapter_contract_quality`, `framework_trace_coverage`,
  `task_completion`, and `tool_selection_accuracy` are all 1.0.
- `framework_adapter_matrix_proof` passes with the L3 matrix assurance level and
  all seven required proof checks.

The 10x robustness rollup counts this as `framework_matrix_optimization`,
separate from `cross_framework_simulation_matrix`: the first proves optimizer
selection, while the second proves broad static framework coverage.
