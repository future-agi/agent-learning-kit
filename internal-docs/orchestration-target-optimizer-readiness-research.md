# Orchestration Target Optimizer Readiness Research Note

Date: 2026-06-10

## Why This Exists

AgentOptimizer must optimize orchestration and framework surfaces directly, not
only prompts, whole agents, world transitions, framework adapter methods,
multi-agent rosters, or memory operations. The existing orchestration optimizer
and stack probe search whole candidate bundles. This gate is narrower: the
scripted agent plus world, retrieval, memory, and multi-agent evidence stay
fixed while `optimize_target()` searches one explicit framework-trace span path
inside the orchestration environment bundle.

## Design

- The cookbook reuses the strong orchestration stack from
  `examples/sdk_orchestration_optimization.py`.
- The base stack intentionally removes the LangGraph framework spans, so
  framework trace evidence is missing while the rest of the stack remains fixed.
- The only target candidate path is
  `simulation.environments.1.data.spans`.
- The selected spans restore `planner.invoke` framework trace evidence and the
  `framework_trace_status` tool signal.
- The selected run closes orchestration flow, framework trace, world replay,
  retrieval, memory lineage, multi-agent coordination, tool selection, and task
  completion at 1.0, with source grounding above the native 0.7 proof threshold.

## Release Rule

`agent-learn release-check` runs
`examples/sdk_orchestration_target_optimization.py` as
`orchestration_target_optimizer_readiness`. The gate requires:

- `agent_learning.optimize.build_target_optimization_manifest` source metadata
  with `generic_target` task kind.
- Search over exactly `simulation.environments.1.data.spans`.
- No whole-agent, prompt, framework-method, world-transition,
  retrieval-document, memory-operation, or multi-agent-roster search paths.
- Fixed scripted agent plus fixed world contract, retrieval memory, memory
  lineage, and multi-agent room fields.
- Selected framework spans containing the LangGraph `planner.invoke` span and
  `framework_trace_status` tool call.
- `agent-learning.optimization.orchestration-stack-proof.v1` passed with
  `l3_native_orchestration_stack_verified`.
