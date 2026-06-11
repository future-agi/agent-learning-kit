# Evaluation Hook Probe Research Note

Date: 2026-06-08

## Why This Exists

Evaluation hooks let a task-specific judge score normalized agent evidence, but
the full optimization path previously assumed a live HTTP endpoint and auth env.
The local probe path gives that evaluator surface the same preflight contract as
framework, memory, multi-agent, realtime, browser, and orchestration probes:
exercise a localhost hook, verify redacted trace evidence, select the agent
candidate that passes the task-specific metric, then promote the selected agent
into the normal `agent-learning.run.v1` evaluation-hook simulation.

## Current Evaluation Signals

- Agent-report evaluation already normalizes messages, tool calls, artifacts,
  events, and metadata into one task evidence object. Probe implication:
  evaluation-hook candidates should be tested against normalized evidence, not
  prompt text alone.
- The built-in evaluation hook metric records an `evaluation_hook_trace` with
  endpoint host, status code, latency, auth header names, token env, and
  redaction state. Probe implication: a passing hook must include successful
  trace evidence and must not serialize auth secrets.
- Existing evaluation-hook optimization searches weak and policy-grounded
  scripted agents. Probe implication: the cheap path should optimize those same
  agent candidates locally, then promote the selected agent into the existing
  HTTP-hook run manifest builder.

## Implementation Rule

Keep evaluation-hook support local-first:

- Allow localhost HTTP endpoints by default.
- Reject non-local HTTP/HTTPS endpoints unless the user explicitly opts into
  live evaluator testing.
- Score candidates through `evals.evaluate_artifact()` so the same
  agent-report metrics, task evidence normalization, hook metric, and redacted
  trace path are exercised.
- Require a returned hook metric, successful hook trace, passing score, task
  evidence, agent-report pass, and auth redaction before the probe passes.
- Use `optimize.optimize_evaluation_hook_probe()` to select a local agent
  candidate cheaply, then use
  `optimize.build_evaluation_hook_run_manifest_from_probe_optimization()` when
  the selected candidate should become a normal evaluated simulation.
- `agent-learn release-check` runs the SDK cookbook as
  `evaluation_hook_probe_readiness`; the gate requires local hook execution,
  policy-grounded candidate selection, native proof, promoted
  `agent-learning.run.v1`, successful local hook traces, and passing
  `external_task_quality`, source-grounding, secret-leakage, task-completion,
  and tool-schema metrics.
