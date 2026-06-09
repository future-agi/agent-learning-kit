# Optimizer Governance Readiness

## Purpose

Agent optimization should be auditable before a candidate is promoted. Prompt
search alone cannot explain which roles proposed changes, why a candidate won,
whether the patch stayed local to diagnosed search paths, or whether rollback
and dependency checks passed. Optimizer-governance readiness makes that evidence
executable.

## Local Contract

- The optimizer search remains local and deterministic; no hosted optimizer or
  judge is required for release metadata.
- The search compares a weak seed trace against a governed optimizer society
  trace.
- The selected trace must include role diversity, critique, synthesis, mediator
  review, steward selection, diagnostics, role credit, rollback, search
  locality, contract gates, dependency audit, and no duplicate candidates.
- The selected candidate must be top-ranked, content-addressed in candidate
  lineage, and non-regressing from the seed score.
- Scored evidence must close `optimizer_trace_coverage`,
  `optimizer_trace_quality`, and `tool_selection_accuracy`.

## Release Gate

`optimizer_governance_readiness` runs
`examples/sdk_optimizer_governance_optimization.py` locally. The gate requires a
passing `agent-learning.optimization.v1` result, a selected
`SocietyAgentOptimizer` trace with `c_steward` as best candidate, passing
`agent-learning.optimization.governance.v1` checks, and 1.0 optimizer trace
coverage/quality metrics.
