# Trinity Stack Probe Research Note

Date: 2026-06-08

## Why This Exists

The local orchestration-stack probe proves the world, framework, retrieval,
memory-lineage, and multi-agent room evidence as one selected stack. The local
evaluation-hook probe proves a task-specific evaluator contract. The trinity
stack probe composes those two surfaces so the same selected orchestration agent
must satisfy the local task judge before it can be promoted into a normal
`agent-learning.run.v1` manifest.

## Implementation Rule

Keep the composed path coherent and local-first:

- Select the orchestration stack through `optimize.optimize_orchestration_stack_probe()`.
- Reuse that selected agent for the evaluation-hook probe instead of running an
  independent evaluator-agent search.
- Reject non-local orchestration targets and non-local HTTP/HTTPS evaluator
  endpoints unless the caller explicitly opts into external testing.
- Require passing orchestration-stack proof, passing evaluator hook trace,
  matching selected agent, local contracts, optimizer governance, and promotion
  readiness before emitting
  `agent-learning.optimization.trinity-stack-probe-proof.v1`.
- Promote with
  `optimize.build_trinity_run_manifest_from_probe_optimization()` so one run
  manifest carries the selected agent, selected stack environments, and local
  evaluation-hook config together.
