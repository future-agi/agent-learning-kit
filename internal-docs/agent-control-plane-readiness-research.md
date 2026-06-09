# Agent Control-Plane Readiness

## Purpose

Prompt optimization is not enough for autonomous agents. A runtime that can call
tools, retain memory, route through frameworks, or take external actions also
needs a control plane that can prove identity, permissions, isolation, approval,
auditability, rollback, budgets, and emergency stops before optimized candidates
are promoted.

## Research Notes

- NIST AI RMF and the Generative AI Profile frame AI risk management around
  governed, measured, and managed controls rather than one-off model scores:
  https://www.nist.gov/itl/ai-risk-management-framework
- OWASP's AI Agent Security guidance emphasizes scoped tools, least privilege,
  human approval for high-impact actions, audit logs, memory controls, and
  sandboxing:
  https://cheatsheetseries.owasp.org/cheatsheets/AI_Agent_Security_Cheat_Sheet.html
- Microsoft's secure-agentic-system guidance treats identity, tool access,
  memory/state, and runtime monitoring as first-class security boundaries:
  https://learn.microsoft.com/security/zero-trust/sfi/secure-agentic-systems
- Agentic failure taxonomies call out tool misuse, unsafe autonomy, memory/state
  drift, and weak oversight as distinct failure modes that need runtime evidence:
  https://www.microsoft.com/security/blog/2025/04/21/ai-agent-security-risks/

## Local Contract

- The readiness gate runs without hosted optimizer, evaluator, or live provider
  calls.
- Direct simulation must emit `agent_trust_boundary_model` and
  `agent_control_plane` state through a normal `agent-learning.run.v1` report.
- Optimization must search weak versus hardened trust-boundary/control-plane
  bundles and select the hardened `simulation.environments` patch.
- Trust-boundary evidence must include identity, permissions, sandboxing, audit,
  canaries, human approval, memory isolation, network egress controls, tool
  allowlists, data boundaries, and secret handling.
- Runtime control-plane evidence must include action policy, approval gates,
  audit, budgets, circuit breakers, containment, drift detection, kill switches,
  rate limits, risk scoring, and rollback.
- Metrics must close `agent_trust_boundary_coverage`,
  `agent_trust_boundary_quality`, `agent_control_plane_coverage`,
  `agent_control_plane_quality`, and `tool_selection_accuracy`.

## Release Gate

`agent_control_plane_readiness` runs
`examples/sdk_agent_control_plane_optimization.py` and
`examples/sdk_agent_control_plane_simulation.py`. The gate requires a passing
optimizer artifact, a passing direct simulation artifact, complete trust and
control summaries, generated control-plane report events/artifacts, optimizer
governance, and local-only output roundtrips.
