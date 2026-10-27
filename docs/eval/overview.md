---
kind: agent-learning.docs-page.v1
track: eval
objective: behavior
stage: evaluate
backing:
  - examples/sdk_task_evaluation.py
artifact_kinds:
  - agent-learning.eval.v1
  - agent-learning.artifact-evaluation.v1
commands:
  - agent-learn eval examples/eval_suite.json --dry-run
  - agent-learn eval examples/eval_suite.json --output artifacts/eval-suite.json
postcondition: python -c "import json; p=json.load(open('artifacts/eval-suite.json')); assert p['kind']=='agent-learning.eval.v1'; assert p['exit_code']==0; print(p['summary'])"
claims: []
doctor_checks:
  - missing_engine_modules
opt_in_lane: false
---

# Evaluation guide

ALK includes the evaluation engine in this repository. Use `fi.alk.evals` as
the public facade; its implementation delegates to `fi.evals` for metrics and
agent-report scoring, and to `fi.simulate.suite` for suite execution. You do not
need a separate evaluation repository. These layers are related but their
inputs and results are not interchangeable.

## First evaluation: the echo fixture

After installing the package in a checkout, run from the checkout root:

```bash
export AGENT_LEARNING_SYNC=local
export AGENT_LEARNING_LEDGER_PATH="$PWD/artifacts/eval-guide/ledger"
agent-learn eval examples/eval_suite.json --dry-run
agent-learn eval examples/eval_suite.json \
  --output artifacts/eval-suite.json \
  --junit artifacts/eval-suite.xml \
  --sarif artifacts/eval-suite.sarif \
  --markdown artifacts/eval-suite.md
python -c "import json; p=json.load(open('artifacts/eval-suite.json')); assert p['kind']=='agent-learning.eval.v1'; assert p['exit_code']==0; print(p['summary'])"
```

[`examples/eval_suite.json`](../../examples/eval_suite.json) uses only echo and
`contains`: the rendered refund-policy question contains `policy`. Expect one
case, its assertion evidence, a passing summary and process exit zero. This is
an expected postcondition, not a recorded run result. `--dry-run` validates the
suite shape without executing providers and is not a measured pass.

The JSON artifact contains per-case provider output/assertions, aggregate score,
threshold and `exit_code`. The CLI returns that exit code. JUnit, SARIF and
Markdown are renderings of the same result, not additional evaluation engines.
See [Eval suites](eval-suites.md) for the SDK equivalent and suite builders.

The backing [task-evaluation example](../../examples/sdk_task_evaluation.py)
covers artifact/task scoring. It is not a test of every optional evaluation mode
on this page. Only the echo recipe above is the first-success execution lane.

## Choose inputs and outputs deliberately

| Input | Entry point | Result / detailed guide |
| --- | --- | --- |
| Providers, prompt templates, variables and assertions | `load_eval_suite_file`, `run_eval_suite_file`; CLI `eval SUITE` | `agent-learning.eval.v1`; [suites](eval-suites.md). Echo/scripted are local; model and Python-callable providers have different side effects. |
| Saved run/optimization artifact with report results | `evaluate_artifact`, `evaluate_artifact_file` | `agent-learning.artifact-evaluation.v1`, with source kind/status, metric averages, cases and findings; [artifact evals](artifact-evals.md). |
| Raw input/output, messages, tool calls/results, metrics, state and events | `build_task_evidence_artifact`, then `evaluate_task_evidence` | Normalization emits `agent-learning.task-evidence.v1`; scoring emits an artifact-evaluation result; [task evidence](task-evidence.md). |
| Task requirements | `build_task_evaluation_config` or `synthesize_task_evaluation_config` | Evaluation configuration. Inference from evidence is a convenience, not independent ground truth; inspect the criteria before using them as a gate. |
| Agent report | `evaluate_agent_report(report, config=..., threshold=...)` | Per-case/metric agent-report evaluation; [evaluate any task](evaluate-any-task.md). A runtime `SimulationReport` can supply its legacy form via `to_legacy()`. |
| Metric inputs such as output/context/reference text | `evaluate(eval_name, engine=..., model=..., **inputs)` | Engine `EvalResult` or `BatchResult`, not a CLI suite artifact. |
| Evidence sent to an external scoring service | `build_evaluation_hook_config`, `evaluate_task_evidence_with_hook` | HTTP hook result and trace; [eval hooks](eval-hooks.md). This sends data and may use credentials. |

Artifact evaluation expects `report.results`, root `results`, or a report inside
optimization history; an arbitrary JSON file is not automatically a valid
report. Task normalization can carry a supplied status/score and even derive a
default status. **Normalizing evidence is not verifying its truth.** Preserve
provenance, expected outcomes, tool results and relevant state; avoid inferring
all expectations from the same output you intend to judge.

## Local, model and platform modes

The included engine's unified `evaluate()` API accepts `engine="local"`,
`engine="turing"`, or `engine="llm"`. With no explicit engine, a Turing model
routes to Turing, another model name to LLM, and no model to local (custom prompt
routing has additional requirements). Select the engine explicitly when its
side effects matter.

| Mode | Behavior and prerequisites |
| --- | --- |
| Local metrics | Executes an installed metric in-process. String/schema/heuristic checks can be credential-free; embedding/NLI/local-model metrics may require extras, model weights, downloads or a local model service. “Local” alone does not mean no I/O. |
| Turing | Uses Future AGI's evaluation service and credentials. Named templates/models are not local heuristic checks. |
| LLM | Uses the selected model route; may call a remote provider. Custom judge prompts belong here. `augment=True` adds a model refinement step; `generate_prompt=True` generates criteria with a model. Neither is an offline shortcut. |
| `Evaluator`, templates and Protect | Included platform client surfaces, distinct from the unified local API. Consult their source/configuration before assuming local execution. |
| HTTP evaluation hooks | Execute the configured endpoint request. A loopback fixture can be local; a remote URL sends evidence off-machine. |

Sources: [`core/evaluate.py`](../../src/fi/evals/core/evaluate.py),
[`core/registry.py`](../../src/fi/evals/core/registry.py),
[`core/engines.py`](../../src/fi/evals/core/engines.py),
[`evaluator.py`](../../src/fi/evals/evaluator.py), and
[`fi.alk.evals`](../../src/fi/alk/evals.py).

Configure account keys through [the configuration guide](../reference/configure.md).
`AGENT_LEARNING_SYNC=local` disables automatic telemetry sync, **not** calls
made by an evaluation engine or hook. Custom evaluators, suite Python providers
and command graders execute code. Review and isolate them; credentials are not
the only source of side effects.

### Read results, not just a number

- `EvalResult` carries `score`, `passed`, `reason`, `status`, `error`, latency and
  metadata. Score/pass can be absent. If a score is supplied without `passed`,
  its default derivation is `score >= 0.5`; this is not the suite threshold.
- `BatchResult.success_rate` is the fraction with `status == "completed"`,
  **not** the fraction that passed a quality check.
- A suite passes only when its aggregate score meets the threshold **and every
  case passes**. Lowering `--threshold` does not waive failed cases. A simulation's
  `COMPLETED` status means execution completed, not that assertions passed.
- Artifact evaluation retains source execution status alongside scoring. Do not
  erase a source runtime failure by presenting a later numeric score alone.
- Judge disagreement or weak evidence needs calibration and diagnostics, not an
  invented deterministic pass. See [judge reliability](judge-reliability.md).

Result definitions: [`core/result.py`](../../src/fi/evals/core/result.py),
[`suite.py`](../../src/fi/simulate/suite.py),
[`agent-report scoring`](../../src/fi/evals/metrics/agents/report.py).

## Reference harness grading

The source-authoring harness has its own result objects in
[`run/grade.py`](../../src/fi/alk/harness/run/grade.py); these are not identical
to the suite's result envelope.

- **Deterministic checkpoints** execute checks against world state and recorded
  calls. A valid refusal is different from a tool crash. Setup activity is not
  agent evidence.
- **Judged conduct** evaluates claims against transcript, actions and final state.
  It can call a model or platform evaluator; “judging locally” means using the
  local harness path, not necessarily an offline model.
- **Broken grading** is marked by error fields (including `grading_error`), rather
  than silently treated as a behavioral failure. Inspect these fields as well
  as the summary label.
- **World crashes** render `VOID` in `Result.line()`. Other problems and grading
  errors can still render `FAIL`; not every `FAIL` is attributable to the agent.
  `Result.passed` requires checkpoints and no state/conduct failures, crashes or
  problems. No checkpoints means nothing was measured, not success.

Before scenario acceptance, [`prove.py`](../../src/fi/alk/harness/prove.py)
checks readiness after setup, reference solvability, and nonvacuity with no
agent actions. It also tracks weak checks, broken checks, failed-attempt credit,
and assumed reference steps. `Proof.holds` checks more than the three summary
gates. Judged-only and deferred/unbound-runtime lanes must not be described as
having the same observed proof as a stateful runtime.

These checks are Python code, including generated `setup.py`, `ready.py` and
`checks/*.py`. They may execute tool bindings and contact services. Code-based
proof is not inherently safe or offline. See [environment authoring](../simulate/environment-authoring.md)
for the trust and bundle boundaries.

## Loss, practice and benchmark validators

[`fi.alk.loss`](../../src/fi/alk/loss.py) compiles versioned objectives and
constructs loss reports from evidence/verdict rows. Declared objectives require
sentinel/canary guards. Derived objectives are for replication and are refused
by the guarded practice path. Unstable results go to a calibration channel;
void/inadmissible results are excluded **and recorded**, not converted into
zero-quality agent examples. See [practice loop](../prove/practice-loop.md)
and [optimization lifecycle](../optimize/optimization-lifecycle.md).

[`fi.alk.bench`](../../src/fi/alk/bench) is a separate Task/Verifier-oriented
benchmark surface, supporting push, artifact input and pull/RL modes. Follow
[benchmark overview](benchmark-overview.md) before selecting a validator.
For [command-graded tasks](benchmark-command-graded.md), the candidate is stopped
before the held-out grader is introduced. The grader's exit code determines
pass/fail; an optional grader-owned `reward.json` supplies a numeric scalar and
subchecks. The declared score contract is `[0, 1]`; the current
`_result_from_grader` conversion does not itself clamp numeric values, so
validate grader outputs rather than assuming bounds are enforced there.
A scalar is not an independent correctness proof.

The subprocess lane runs host code and is for trusted tasks. Docker uses a
separate isolation mechanism with its own dependencies; neither lane belongs in
the echo quickstart. Candidate code, held-out tests and grader commands all need
review. See [sandboxes](benchmark-sandboxes.md) and
[`_grader.py`](../../src/fi/alk/bench/_grader.py).

## Other included evaluation libraries

| Surface | What it does | Source |
| --- | --- | --- |
| Local metric registry | Selects and runs supported local metrics; hybrid/local-LLM routing is separate | [`fi.evals.local`](../../src/fi/evals/local) |
| Metric families | String/numeric, structured output, RAG, hallucination, agent trajectories and model judges | [`fi.evals.metrics`](../../src/fi/evals/metrics) |
| Guardrails / Protect | Scanning and protection; backend choice determines local versus service behavior | [`guardrails`](../../src/fi/evals/guardrails), [`protect.py`](../../src/fi/evals/protect.py) |
| Autoeval | Analysis/recommendation and evaluation configuration; check the chosen analyzer for model use | [`autoeval`](../../src/fi/evals/autoeval) |
| Evaluation framework | Registered evaluators, execution modes, resilience and orchestration | [`framework`](../../src/fi/evals/framework) |
| Streaming | Chunk buffers, scorers and early-stop policies; scorer implementation determines I/O | [`streaming`](../../src/fi/evals/streaming) |
| Feedback | Feedback storage and retrieval for refinement; optional backend dependencies apply | [`feedback`](../../src/fi/evals/feedback) |
| OpenTelemetry | Evaluation processors and tracing integration; exporters may send data | [`otel`](../../src/fi/evals/otel) |

The [library inventory](../../LIBRARIES.md) maps these to public facades and the
TypeScript source. Inclusion does not make every helper a stable API or every
metric an offline implementation.
