# Task Artifact Evaluation Readiness

Date: 2026-06-09

## Why This Exists

The evaluation layer should score task evidence even when there is no fresh
simulation run. Users need to evaluate saved run artifacts, raw task
transcripts, tool-call exports, framework runtime state, world-contract state,
and CI artifacts through one local path. This closes the "ai-evaluation can
evaluate any task" claim for V1 with executable evidence instead of only API
presence.

## Current Evaluation Signals

- `evals.build_task_evidence_artifact()` normalizes raw task evidence into
  `agent-learning.task-evidence.v1` with messages, tool calls, artifacts,
  events, metrics, and environment state.
- `evals.evaluate_task_evidence()` and `agent-learn eval-task` feed that
  normalized evidence into the same agent-report evaluator used by simulated
  runs.
- `evals.evaluate_artifact()` and `agent-learn eval-artifact` evaluate saved
  `agent-learning.run.v1` artifacts without rerunning the agent.
- `examples/artifact_task_eval_suite.json` proves promptfoo-style structured
  artifact assertions over JSON paths for task completion, framework runtime,
  safe memory, canary handling, and world-contract evidence.

## Release Gate

`agent-learn release-check` runs `task_artifact_evaluation_readiness`. The gate
must prove all of the following locally:

- `examples/sdk_task_evaluation.py` runs and writes an
  `agent-learning.artifact-evaluation.v1` result.
- Raw task evidence round-trips through
  `evals.write_task_evidence_file()` and `evals.evaluate_task_evidence_file()`.
- A saved `agent-learning.run.v1` artifact evaluates through
  `evals.evaluate_artifact_file()`.
- `examples/artifact_task_eval_suite.json` runs as
  `agent-learning.eval.v1` and passes every structured assertion.
- Required environment state keys are present:
  `task_evidence`, `framework_runtime`, and `world_contract`.
- Required local metrics close at 1.0: `task_completion`,
  `tool_selection_accuracy`, `world_contract_quality`, `memory_integrity`,
  `framework_runtime_coverage`, `world_contract_coverage`, `secret_leakage`,
  and `source_grounding`.

## Implementation Rule

Keep this path local-first and format-independent. New task evaluators should
prefer normalized evidence plus structured state/metric assertions over
serialized-output string matching. External judges can be layered through the
evaluation-hook probe, but the base artifact evaluation readiness gate must not
require hosted eval, optimizer, observability, or live provider services.
