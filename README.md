# agent-learning-kit

`agent-learning-kit` is the unified Future AGI SDK for agent simulation,
evaluation, red teaming, and optimization.

The package gives users one key/config layer and one import namespace.
Simulation, evals, red teaming, and optimization live under that namespace and
ship in this package.

```python
from agent_learning import configure
from agent_learning import simulate, evals, redteam, optimize, suite

configure(api_key="...")
```

Install the unified SDK:

```bash
pip install agent-learning-kit
```

Optional extras are only for heavier integrations:

```bash
pip install agent-learning-kit[livekit]
pip install agent-learning-kit[nli]
pip install agent-learning-kit[all]
```

The TypeScript eval surface is also housed here under the same SDK name:

```bash
pnpm add @future-agi/agent-learning-kit
```

```typescript
import { Evaluator } from "@future-agi/agent-learning-kit";
import { LocalEvaluator } from "@future-agi/agent-learning-kit/evals/local";
```

`agent-learning-kit` is the public SDK. The simulation, evaluation, and
optimization engine code is vendored into this package; public docs and
automation should use `agent_learning.*` and `agent-learn`.

New public SDK development belongs here. See [DEVELOPMENT.md](DEVELOPMENT.md)
for the boundary between this package and the backing engine repos.
Run `agent-learn doctor` to verify the active consolidation boundary: the
public package/CLI, shared `AGENT_LEARNING_API_KEY` /
`AGENT_LEARNING_SECRET_KEY` config, unified
`agent_learning.{simulate,evals,redteam,optimize,suite,trinity}` APIs, vendored
`fi.{simulate,evals,opt}` engines, and legacy Python distributions that should
not be project dependencies. `release-check` also verifies the TypeScript
public package boundary: `@future-agi/agent-learning-kit` owns the moved eval
SDK source and the legacy TypeScript eval package is marker-only. For one-key local usage,
`configure(api_key=...)` mirrors the key into legacy `FI_API_KEY` and
`FI_SECRET_KEY` engine paths.
Run `agent-learn release-check --project-root .` before cutting V1. It emits
`agent-learning.release-check.v1` with the milestone gates from
[`V1_RELEASE_ROADMAP.md`](V1_RELEASE_ROADMAP.md): SDK consolidation,
promptfoo-style CLI, native optimizer evidence, required docs/examples, schema
kinds, packaging metadata, and research-backed red-team corpus/campaign
coverage across required examples, attack types, surfaces, source lineage, and
the canonical local corpus rows. It also executes the canonical local red-team
corpus through the campaign builder and requires full row/cell/run/artifact/
finding/mitigation closure before M4 passes. It also gates representative
Future AGI UI/action/report readiness: renderable report payloads, executable
action catalogs, saved action-run output evidence, and key-like secret-marker
redaction across representative run, action-run, optimization, red-team,
red-team campaign, provider-integration, and suite artifacts.
It also runs a local retrospective harness optimization and verifies the
rendered `harness_diagnosis` card, diagnosis actions, rollout plan, proof, and
2026 research lineage without calling hosted optimizer/eval services.
Framework/provider readiness is executable too: release-check builds the native
adapter matrix for LangChain, LangGraph, LlamaIndex, OpenAI Agents, AutoGen,
CrewAI, PydanticAI, LiveKit, and Pipecat, requires local fixture targets with
no external service dependency, and validates representative text, voice, and
realtime manifests.

For the heavier release cut, run `agent-learn release-proof --project-root .`.
It emits `agent-learning.release-proof.v1` with command evidence for the full
local proof stack: release-check, full-repo ruff, pytest, Python package build,
TypeScript package build/test, and `git diff --check`. Use `--only <check>` for
a partial proof during development or `--dry-run` to print the exact plan
without executing commands.

Python code can verify the same boundary without shelling out:

```python
from agent_learning import trinity

status = trinity.assert_trinity_ready()
assert status["modules"]["simulate"]["available"]
assert status["modules"]["evaluation"]["available"]
assert status["modules"]["optimize"]["available"]
```

CLI entrypoint:

```bash
agent-learn eval examples/eval_suite.json --output artifacts/eval.json
agent-learn eval examples/artifact_task_eval_suite.json \
  --output artifacts/artifact-task-eval.json
agent-learn eval-artifact examples/fixtures/task_artifacts/refund_task_run.json \
  --config examples/artifact_task_eval_config.json \
  --output artifacts/direct-artifact-eval.json
agent-learn eval-task examples/task_evidence.json \
  --eval-hook http://127.0.0.1:8080/eval/task \
  --output artifacts/evaluation-hook-task.json
agent-learn optimize-eval examples/artifact_task_optimization_suite.json \
  --output artifacts/artifact-task-optimization.json
agent-learn optimize-eval examples/eval_suite_optimization.json \
  --output artifacts/eval-optimization.json
agent-learn run examples/run_manifest.json --no-eval --output artifacts/run.json
agent-learn redteam examples/redteam_manifest.json --output artifacts/redteam.json
agent-learn redteam examples/long_horizon_redteam_manifest.json \
  --output artifacts/long-horizon-redteam.json
agent-learn redteam-corpus \
  --corpus examples/redteam_corpus.json \
  --output artifacts/redteam-corpus.json
agent-learn redteam-corpus \
  --hook http://127.0.0.1:8080/redteam/corpus \
  --output artifacts/redteam-corpus-hook.json
agent-learn optimize examples/optimization_manifest.json --output artifacts/optimization.json
agent-learn optimize examples/world_framework_memory_optimization.json \
  --output artifacts/world-framework-memory-optimization.json
agent-learn optimize examples/voice_streaming_realtime_optimization.json \
  --output artifacts/voice-streaming-realtime-optimization.json
agent-learn optimize examples/redteam_campaign_optimization.json \
  --output artifacts/redteam-campaign-optimization.json
agent-learn optimize examples/redteam_autogen_optimization.json \
  --output artifacts/redteam-autogen-optimization.json
agent-learn optimize examples/long_horizon_redteam_optimization.json \
  --output artifacts/long-horizon-redteam-optimization.json
agent-learn optimize examples/redteam_society_optimization.json \
  --output artifacts/redteam-society-optimization.json
agent-learn optimize examples/redteam_causal_attribution_optimization.json \
  --output artifacts/redteam-causal-attribution-optimization.json
agent-learn optimize examples/workspace_observability_optimization.json \
  --output artifacts/workspace-observability-optimization.json
agent-learn optimize examples/agent_integration_optimization.json \
  --output artifacts/agent-integration-optimization.json
agent-learn optimize examples/multi_agent_framework_handoff_optimization.json \
  --output artifacts/multi-agent-framework-handoff-optimization.json
agent-learn optimize examples/optimizer_governance_optimization.json \
  --output artifacts/optimizer-governance-optimization.json
agent-learn optimize examples/agent_control_plane_optimization.json \
  --output artifacts/agent-control-plane-optimization.json
agent-learn optimize examples/browser_cua_optimization.json \
  --output artifacts/browser-cua-optimization.json
agent-learn optimize examples/framework_certification_optimization.json \
  --output artifacts/framework-certification-optimization.json
agent-learn optimize examples/framework_import_repair_optimization.json \
  --output artifacts/framework-import-repair-optimization.json
agent-learn optimize examples/autonomous_redteam_task_world_optimization.json \
  --output artifacts/autonomous-redteam-task-world-optimization.json
agent-learn optimize examples/multimodal_image_optimization.json \
  --output artifacts/multimodal-image-optimization.json
agent-learn suite examples/agent_learning_suite.json --output artifacts/suite.json
agent-learn suite examples/multi_framework_simulation_suite.json \
  --output artifacts/multi-framework-suite.json
agent-learn optimize-suite examples/suite_optimization.json \
  --output artifacts/suite-optimization.json
agent-learn action-optimize artifacts/sdk-framework-certification-simulation.json \
  --id report_framework_readiness \
  --id rerun_framework_certification \
  --source-card framework_readiness \
  --output artifacts/artifact-action-optimization.json
agent-learn run examples/framework_custom_manifest.json --no-eval \
  --output artifacts/framework-custom.json
agent-learn optimize examples/custom_framework_optimization.json \
  --output artifacts/custom-framework-optimization.json
agent-learn optimize examples/social_memory_framework_optimization.json \
  --output artifacts/social-memory-framework-optimization.json
agent-learn suite examples/regression_artifact_suite.json \
  --output artifacts/regression-artifact-suite.json
agent-learn init ./agent-learning-project --preset optimize --force
agent-learn init ./agent-learning-trinity-project --preset all --force
agent-learn run examples/voice_streaming_realtime_manifest.json --no-eval \
  --output artifacts/voice-streaming-realtime.json
agent-learn eval-cli list categories --format json
agent-learn eval-cli init ./eval-project --template basic --force
agent-learn doctor --output artifacts/agent-learning-doctor.json
agent-learn release-proof --project-root . --output artifacts/release-proof.json
```

`agent-learn init` scaffolds runnable Agent Learning projects. The optimize
preset generates a local task/world optimization manifest that patches both an
agent action and a world-contract transition, then emits the full CLI lifecycle:
dry-run, optimize, report, promote-to-regression, promotion report, replay, and
replay report with JSON, JUnit, SARIF, and Markdown artifacts. The promoted
manifest is written under `regressions/` with the public
`agent-learning.run.v1` schema so it can be replayed by CLI users, SDK tests,
CI, and Future AGI UI cards from the same evidence. The all preset generates a
self-contained trinity workspace with local run, promptfoo-style eval,
structured artifact eval, direct artifact-report eval, red-team, eval-suite
optimization, task/world optimization, internal world-model optimization, a
fixture-backed artifact action-run, child action evidence under
`artifacts/action-loop/`, a saved task artifact fixture, and
`manifests/suite.json` as the single CI entrypoint. The world-model optimizer
searches native L1 predictor, L2 executable simulator, and L3 verifiable
evolver bundles; it does not require an external endpoint.
`suite.build_optimization_lifecycle_plan()` and
`suite.run_optimization_lifecycle_file()` expose the same lifecycle from the
SDK; `examples/sdk_optimization_lifecycle.py` writes the task/world optimize
manifest, runs the lifecycle, and verifies promotion plus replay artifacts.
Optimization, promotion, and replay reports also include a structured
`harness_diagnosis` card and `## Harness Diagnosis` Markdown section. The card
maps observed search paths, weak metrics, environment types, findings, and
replay status onto execution/tooling/context/lifecycle/observability/
verification/governance layers, then suggests deterministic repair operators
for the implicated harness layers. The same card now emits executable
diagnosis actions, including follow-up report, rerun, promotion, and replay
commands, with `target_layers`, `repair_operators`, and relevant search paths
attached so CLI users, SDK callers, CI, and Future AGI UI cards can move from
diagnosis to a reproducible next step without inventing ad hoc commands.
Diagnosis reports also add
`report.harness_diagnosis.retrospective_rollout_plan`, a
`retrospective_harness_rollout_plan` produced with
`evidence_calibrated_candidate_lineage`. The plan records the
`selected_candidate_id`, `candidate_count`, `weak_metric_names`,
`target_layers`, `candidate_lineage`, `repair_frontier`, and `rollout_steps`,
and renders Markdown sections named `Retrospective Rollout Plan`,
`Candidate Lineage`, `Repair Frontier`, and `Rollout Steps`.
Optimization artifacts themselves also carry
`agent-learning.optimization.candidate-lineage.v1`, a content-addressed
candidate lineage contract with selected candidate ID, score delta from the
seed, candidate count, patch/search paths, metric names, and per-candidate
SHA-256 freezes for patch, metrics, config, and report-summary evidence. This
lets SDK, CLI, CI, and Future AGI UI compare optimizer behavior across prompt,
world, framework, memory, and multi-agent runs without relying on a separate
report action.
Retrospective harness optimization is also native and local-only:
`simulate.build_harness_trajectory_replay_run_manifest()` builds a replayable
trajectory coreset, failure attribution, repair plan, candidate update, and
provenance artifact, while
`optimize.build_retrospective_harness_optimization_manifest()` and
`optimize.optimize_retrospective_harness()` search weak versus verified harness
repair bundles without calling an external grader or competitor platform. The
same manifest can run from Python or through the promptfoo-style `agent-learn
optimize <manifest>` CLI path, and passing results attach
`agent-learning.optimization.retrospective-harness-proof.v1` with closed
coreset, attribution, repair-plan, metric, report-state, and local-only checks.
Optimization artifacts also attach
`agent-learning.optimization.governance.v1`, a deterministic admission verdict
over that lineage. Required checks verify that the selected candidate resolves
to a lineage row, all candidates are content-addressed, the selected candidate
is top-ranked, the selected score does not regress from the seed, and metric
evidence exists; advisory checks flag missing evaluation/report/search-path
evidence without changing the top-level run status. This gives the SDK, CLI,
CI, and Future AGI UI a native promotion gate before an optimized prompt,
world, framework, memory layer, red-team harness, or multi-agent orchestration
is trusted.
Red-team run artifacts and reports also include a `redteam_strategy` card plus
`## Red Team Strategy` Markdown. The card maps attack types, surfaces,
channels, providers, frameworks, campaign coverage, and risk focus into a
strategy-response matrix. It also reports per-surface coverage/execution,
blind-spot surfaces, and a worst-surface adaptive gap rate so aggregate
campaign pass/fail cannot hide an untested tool, memory, retrieval, or
environment surface. The card then emits report, rerun, and optimization
commands so promptfoo-style CLI users and SDK callers can move from a red-team
result to the next campaign or optimization step from the same artifact.
World/framework/memory/multi-agent orchestration artifacts and reports include
an `orchestration_strategy` card plus `## Orchestration Strategy` Markdown. The
card normalizes runtime evidence into world, framework, retrieval, memory,
multi-agent, and orchestration layers; summarizes graph nodes, edges, routes,
and steps; and emits report, rerun, and optimization commands so users can move
from a selected orchestration stack to the next verified simulation or optimizer
run.
Optimization reports also include
`report.orchestration_strategy.orchestration_rollout_plan`, an
`orchestration_candidate_rollout_plan` produced with
`structure_guided_counterfactual_rollout`. The plan records the selected
candidate, selected layers, weak layers/metrics, selected environment bundle,
candidate lineage, rollout steps, and an exported selected orchestration
manifest artifact. Markdown reports render `Orchestration Rollout Plan`,
`Orchestration Candidate Lineage`, and `Orchestration Rollout Steps`, and the
card exposes export/replay actions for the selected stack.
Framework certification and import artifacts also expose a
`framework_readiness` card plus `## Framework Readiness` Markdown. The card
normalizes lifecycle, capability, probe, portability, import, and adapter
evidence into readiness layers, names weak framework metrics, and emits
`report_framework_readiness`, `rerun_framework_certification` or
`rerun_framework_optimization`, and `optimize_framework_readiness` commands for
CLI, SDK, CI, and Future AGI UI surfaces.
`agent-learn actions <artifact>` and `agent_learning.actions.action_catalog()`
turn those embedded card actions into a standalone
`agent-learning.actions.v1` catalog. The catalog prefers raw embedded actions,
adds synthesized report actions as fallbacks, de-dupes actions by id, marks
actions that need placeholder inputs, and can be filtered with `--id`, giving
promptfoo-style users a direct way to discover the next runnable command or
artifact export from any saved run/report/optimization artifact. `agent-learn
action-run <artifact> --id <action_id>` and
`agent_learning.actions.run_action()` execute one selected CLI action without
shelling out or materialize one selected `kind: download` export action with
`--artifact-output`. Relative command outputs and default download paths are
resolved inside the requested `--cwd`, and the returned
`agent-learning.action-run.v1` artifact records the action kind, command or
artifact ref, exit code, captured stdout/stderr logs, declared outputs, and
generated files. Public embedded CLI actions must use `agent-learn`; legacy
`agent-simulate` action commands are intentionally rejected by the unified SDK
runner and excluded from artifact-action optimization candidates.
`actions` and `action-run` both write JSON/JUnit/SARIF/Markdown outputs for CI.
Suites can include the same loop with `{"command": "action-run", "path":
"...artifact.json", "action_id": "..."}` jobs, so CI can run a saved artifact,
inspect the recommended next action, execute it, and keep the action-run result
inside one `agent-learning.suite.v1` artifact.
`optimize.build_artifact_action_optimization_manifest()` and
`optimize.optimize_artifact_actions()` turn the same action catalog into an
AgentOptimizer-backed suite search over `jobs.0`. `agent-learn action-optimize
<artifact>` exposes the same loop from the CLI with action id, source-card,
target-layer, and CLI subcommand filters, optional generated suite output, and
normal JSON/JUnit/SARIF/Markdown result outputs. CLI or SDK users can let the
optimizer choose between report, export, rerun, replay, repair, or follow-up
optimization actions from a real artifact trajectory and still get child
JSON/JUnit/SARIF/Markdown outputs plus captured logs and download output
records for the selected `action-run`. Suite optimization artifacts and reports
also include an
`artifact_action_plan` card with the selected action, candidate score lineage,
output completion, evidence depth, generated files, and the selection reason for
Future AGI UI/API rendering.

`agent-learn run`, `agent-learn eval`, `agent-learn redteam`,
`agent-learn redteam-corpus`, `agent-learn optimize`, `agent-learn optimize-eval`,
`agent-learn optimize-suite`, `agent-learn suite`, `agent-learn actions`,
`agent-learn action-run`, and `agent-learn action-optimize`
write Agent Learning Kit artifact kinds
(`agent-learning.run.v1`, `agent-learning.eval.v1`,
`agent-learning.redteam.v1`, `agent-learning.optimization.v1`,
`agent-learning.eval-optimization.v1`, `agent-learning.suite.v1`, and
`agent-learning.actions.v1` / `agent-learning.action-run.v1` /
`agent-learning.suite-optimization.v1`) plus
optional JUnit, SARIF, and Markdown outputs for CI.
`agent-learn capabilities` and `agent_learning.capabilities.capability_catalog()`
emit an `agent-learning.capabilities.v1` preflight artifact covering supported
commands, result kinds, providers, provider channels, frameworks, environment
types, state keys, metrics, modalities, and search paths. Add `--require
providers=vapi,retell --require frameworks=langgraph,pipecat` to fail fast
before a suite or optimizer run when a required integration surface is missing.
The catalog also records 2026 capability-discovery/governance research sources
for Future AGI UI/API rendering and exposes the unified SDK boundary as
gateable `command_policies` and `sdk_boundaries` capabilities.
`agent-learn eval-cli ...` bridges the vendored evaluation management CLI under
the unified command for template listing, project scaffolding,
configuration validation, history viewing, export, and config management.

This package now contains the actual `fi.simulate`, `fi.evals`, and `fi.opt`
engine code while keeping `agent_learning.*` as the public API.
`agent_learning.simulate` mirrors the vendored `fi.simulate` public SDK surface:
agent definitions/wrappers, local/cloud/realtime engines, environments,
normalizers, manifest helpers, eval-suite helpers, and artifact renderers.
`agent_learning.evals` mirrors the vendored `fi.evals` public SDK surface:
cloud/client evals, built-in templates, streaming evals, framework evaluators,
Protect helpers, execution handles, and the unified `evaluate` API.
It also promotes AutoEval pipeline builders/templates and local/offline metric
routing, so users can generate eval configs and run local heuristic checks
without importing legacy `fi.evals.*` paths.
The TypeScript eval SDK has also moved from
`ai-evaluation/typescript/ai-evaluation` into
`typescript/agent-learning-kit` and publishes as
`@future-agi/agent-learning-kit`, with eval exports available from the package
root and `@future-agi/agent-learning-kit/evals/local`.
`agent_learning.redteam` exposes the red-team runtime directly: manifest
execution, adversarial/campaign/readiness environments, local guardrail scanner
pipelines, Protect/guardrail config types, code-security metrics, CWE detector
helpers, and agent trajectory safety metrics.
`agent_learning.redteam.build_redteam_manifest()` builds direct SDK red-team
run manifests with `redteam.auto_generate: true`, so Python callers can create
the same attack-pack and campaign evidence as `agent-learn redteam` without
hand-writing JSON.

The `world_framework_memory_optimization.json` example optimizes a
LangGraph-style world orchestration across framework trace, retrieval, memory
lineage, and multi-agent review evidence.
The SDK-native wrapper is
`optimize.build_world_framework_memory_optimization_manifest()` /
`optimize.optimize_world_framework_memory()`; it searches agent behavior and a
coherent `simulation.environments` bundle, emits the same orchestration-stack
proof, and stays local unless the caller explicitly supplies external
environment wiring.

The `agent_learning_suite.json` example is the promptfoo-style CI entrypoint:
one manifest runs simulation, the nested multi-framework adapter suite,
suite-level optimization over that nested suite, eval, artifact-task eval,
direct artifact-report eval, artifact-evidence optimization, red-team, eval-suite
optimization, world/framework/memory optimization, voice/streaming optimization, red-team optimization,
internal world-model optimization,
workspace/observability optimization, agent-integration optimization,
multi-agent framework handoff optimization, optimizer-governance optimization,
social-memory framework optimization,
agent control-plane
optimization, browser/CUA red-team optimization, framework-certification
optimization, autonomous task/world red-team optimization, and multimodal image
optimization jobs, then emits aggregate artifacts plus a capability summary of
commands, result kinds, environment types, providers, frameworks, channels, and
metrics observed from the child run outputs, including environment-state keys
such as framework adapter runtime evidence. Suites can also declare
`required_capabilities`; if any required command, result kind, environment type,
environment-state key, provider, framework, channel, or metric is absent from
the executed child artifacts, `agent-learn suite` fails the run and records the
missing capability in JSON, JUnit, SARIF, and Markdown outputs.
Suites also emit an `evidence_admission` contract. Each job can set
`evidence_role` to `admitted`, `fixture`, `smoke`, `diagnostic`, `preflight`,
or `calibration`; fixture paths are detected automatically. The suite summary
records admitted, non-admitted, and rejected row counts plus per-row provenance,
so CI and Future AGI UI cards can separate claim-supporting evidence from
fixture and diagnostic support artifacts. Add
`"evidence_policy": {"min_admitted": 1}` when a suite must fail unless at least
one child row is admitted. Each row also carries an
`agent-learning.suite.evidence-freeze.v1` block with SHA-256 digests for the
child manifest, child result payload, and declared child outputs; add
`"require_freeze": true` to the same policy when admitted rows must be
content-addressed.
Suites can also require optimizer governance before CI trusts optimization
children. Add
`"optimizer_governance_policy": {"require_optimizer_governance": true, "min_governed": 1}`
to a suite manifest, or pass `agent-learn suite ... --require-optimizer-governance`.
The suite then emits `agent-learning.suite.optimizer-governance.v1` with
per-child rows, governed/passed/failed/missing counts, and findings when an
optimizer child lacks a passed `agent-learning.optimization.governance.v1`
verdict. The trinity suite builder and `agent-learn init --preset all`
scaffold enable this gate by default.
Every suite also emits `agent-learning.suite.trust-certificate.v1`, a
machine-verifiable deployment certificate with `approved`, `conditional`, or
`rejected` verdicts. The certificate combines execution status, admitted/frozen
evidence, framework coverage, optimizer governance, and trinity coverage
(simulation, evaluation, red-team, optimization) into a single
`promotion_ready` signal for CI and Future AGI UI. Trinity suites with governed
optimizer children can become `approved`; narrower passing suites remain
`conditional`; failed gates are `rejected`.
Saved suite artifacts can be promoted without re-running the suite by using
`agent-learn trust artifacts/suite.json`, or from Python with
`suite.verify_trust_certificate_file("artifacts/suite.json")`. The verifier is
strict by default and fails unless the certificate is `approved` and
`promotion_ready=true`, giving promptfoo-style CLI/SDK users a local CI gate
with no external observability or red-team platform dependency.
Suite jobs can call other suite manifests, so the top-level suite can enforce
coverage from composed child suites without losing nested child artifacts.
`agent-learn optimize-suite` and `suite.optimize_suite()` search over the suite
itself: the `suite_optimization.json` and `sdk_suite_optimization.py` cookbooks
start from a single-framework run job and select the nested 10-framework suite
because only that candidate satisfies the required framework/capability gate.

SDK users can also build the same composed CI entrypoint without hand-writing
suite JSON:

```python
from agent_learning import suite

manifest = suite.build_trinity_suite_manifest(
    name="sdk-trinity-suite",
    run_path="run_manifest.json",
    eval_path="eval_suite.json",
    artifact_eval_path="artifact_task_eval_suite.json",
    artifact_report_path="fixtures/task_artifacts/refund_task_run.json",
    artifact_action_id="report_orchestration_strategy",
    artifact_eval_config_path="artifact_task_eval_config.json",
    artifact_optimization_path="artifact_task_optimization_suite.json",
    redteam_path="redteam_manifest.json",
    eval_optimization_path="eval_suite_optimization.json",
    optimization_path="world_framework_memory_optimization.json",
    world_model_optimization_path="world_model_optimization.json",
)
result = suite.run_suite(manifest, suite_path="examples/sdk_trinity_suite.json")
```

The `multi_framework_simulation_suite.json` example runs local LangChain,
LangGraph, LlamaIndex, OpenAI Agents, AutoGen, CrewAI, PydanticAI, Pipecat,
LiveKit-style, and custom proprietary agents through the same manifest
framework adapter path, proving text, voice, retrieval, handoff, groupchat,
crew, typed-output, and custom framework shims can be simulated without adding
framework-specific runtime dependencies. Unknown framework names are accepted as
custom adapters when the manifest supplies the target method/input mode, as
shown in `framework_custom_manifest.json`.

SDK users can build the same framework simulations without hand-writing run
manifests:

```python
from agent_learning import simulate

manifest = simulate.build_framework_run_manifest(
    name="my-langgraph-smoke",
    framework="langgraph",
    target="framework_shims.py:build_langgraph_agent",
    required_env=["AGENT_LEARNING_API_KEY"],
)
simulate.write_manifest_file(manifest, "manifests/langgraph.json")
```

Framework simulation manifests now carry
`agent-learning.framework-adapter-contract.v1` under
`metadata.framework_adapter_contract`, `agent.metadata.framework_adapter_contract`,
and `agent.runtime_metadata.framework_adapter_contract`. The same contract is
attached to runtime traces emitted by `wrap_framework()`, including hand-written
CLI manifests. Use `simulate.framework_adapter_contract("langgraph", ...)` to
inspect the local adapter method, input mode, modality, transport, lifecycle
hooks, capabilities, schemas, trace requirements, and executable-fixture status
without importing the target framework.
Before writing a full manifest, use `simulate.run_framework_adapter_probe()` to
point at any local framework object or callable and prove the adapter method,
input shape, output content, tool calls, events, runtime trace, and local-first
contract evidence:

```python
from agent_learning import simulate

result = simulate.run_framework_adapter_probe(
    "custom_refund_orchestrator",
    LocalRefundOrchestrator(),
    target="framework_shims.py:build_custom_refund_orchestrator",
    method="execute_task",
    input_mode="dict",
    cases=[
        {
            "id": "refund-status",
            "input": "Approve the refund.",
            "expected_contains": ["approved refund"],
            "required_tools": ["framework_trace_status"],
            "required_events": ["framework_trace"],
            "required_state_keys": ["framework_runtime"],
        }
    ],
)
assert result["status"] == "passed"
```

When several adapter shapes are plausible, run the same probe through
AgentOptimizer before building a full manifest:

```python
from agent_learning import optimize

result = optimize.optimize_framework_adapter_probe(
    name="my-adapter-probe-search",
    framework="custom_refund_orchestrator",
    target="framework_shims.py:build_custom_refund_orchestrator",
    agent_factory=LocalRefundOrchestrator,
    adapter_candidates=[
        {"method": "run", "input_mode": "text"},
        {"method": "execute_task", "input_mode": "dict"},
    ],
    cases=[
        {
            "id": "refund-status",
            "input": "Approve the refund and emit adapter evidence.",
            "expected_contains": ["approved refund"],
            "required_tools": ["framework_trace_status"],
            "required_events": ["framework_trace"],
            "required_state_keys": ["framework_runtime"],
        }
    ],
)
assert result["optimization"]["best_config"]["adapter"]["method"] == "execute_task"
assert result["framework_adapter_probe_proof"]["status"] == "passed"
```

Agent-report evaluation can now score that same metadata with
`framework_adapter_contract_quality`; framework optimization weights it as a
native gate alongside runtime and trace metrics, so an HTTP target or
external-service contract is diagnosed locally instead of delegated to an
external optimizer/eval platform.
Use `simulate.framework_adapter_contract_matrix([...])` when Future AGI UI,
CI, or CLI needs to certify many framework adapters in one artifact. The matrix
emits `agent-learning.framework-adapter-contract-matrix.v1`, expands to one
local `agent-learning.framework-adapter-contract.v1` per framework, rejects
HTTP/HTTPS targets by default, and carries a `contract_quality_gate` that
`framework_adapter_contract_quality` can score with plural requirements such as
`required_frameworks`. The default matrix covers LangChain, LangGraph,
LlamaIndex, CrewAI, AutoGen, OpenAI Agents, LiveKit, and Pipecat without
importing or calling those packages.
`simulate.build_framework_adapter_matrix_run_manifest()` turns the same matrix
into a normal local run artifact. `optimize.optimize_framework_adapter_matrix()`
then searches weak versus verified matrix candidates through AgentOptimizer,
selects the matrix from simulation evidence, and emits
`agent-learning.optimization.framework-adapter-matrix-proof.v1` with local
fixture, no-external-target, coverage, metric, and report-state checks.
The attached 2026 paper references are provenance for the design direction,
not runtime integrations; this cookbook runs through local simulation,
Future AGI-native eval evidence, and the SDK optimizer.

For arbitrary task/world simulation, build a normal run manifest from an agent
spec, task description, environment bundle, and optional agent-report eval
config:

```python
import asyncio

from agent_learning import simulate

manifest = simulate.build_task_run_manifest(
    name="refund-world-smoke",
    agent=scripted_or_framework_agent,
    task_description="Approve the refund by applying the world transition.",
    expected_result="The refund final state is approved.",
    environments=[refund_world_contract],
    required_tools=["apply_world_transition"],
    available_tools=["apply_world_transition", "world_contract_status"],
    required_env=["AGENT_LEARNING_API_KEY"],
)
result = asyncio.run(
    simulate.run_manifest(manifest, manifest_path="manifests/refund.json")
)
```

For batches, `simulate.build_multi_framework_suite_manifest()` composes those
generated run manifests into an `agent-learning.suite.v1` capability-gated
suite. Suite outputs now include
`agent-learning.suite.framework-coverage.v1`, a per-child framework coverage
contract with observed/required/missing frameworks, method/input-mode/modality
maps, trace signals, and adapter-conformance failures. This lets CI and Future
AGI UI prove that LangChain, LangGraph, LiveKit, Pipecat, custom adapters, or
any other framework actually ran through the generic simulator path.

The `custom_framework_optimization.json` example runs the same bring-your-own
framework path through `agent-learn optimize`. It starts with a runnable but weak
`run`/`text` adapter and deterministically selects the `execute_task`/`dict`
adapter because only that candidate emits tool evidence and passes the framework
runtime contract.
Framework-runtime optimization artifacts also emit
`agent-learning.optimization.framework-runtime-proof.v1`, a native proof
attached at the top level and under `optimization.framework_runtime_proof`.
The proof is derived from the selected candidate and selected report state:
local adapter target, runtime summary, framework trace conformance, normalized
adapter contract quality, trace/tool bridge, patch surface, optimizer lineage,
optional social-memory governance, and closed framework runtime metrics. It
does not require an external observability, eval, or optimizer service.

The `social_memory_framework_optimization.json` example selects
`optimization.optimizer.algorithm: "social_memory"`, a multi-round optimizer
with metric-bound role memory. It starts with both a weak adapter and weak trace,
then synthesizes the high-credit adapter patch and framework-trace patch into a
`sangha` best candidate. The emitted optimizer society trace records the social
roles (`smriti`, `arjuna`, `vidura`, `sangha`, `dharma_steward`), proposal
rounds, role credit, governance checks, and the final synthesized patch.
The same adapter synthesis is available from Python through
`optimize.build_social_memory_framework_optimization_manifest()` and
`examples/sdk_social_memory_framework_optimization.py`, which generate the
adapter candidates, trace candidates, and social-memory optimizer config.
For direct, non-optimizer simulation,
`simulate.build_social_memory_framework_run_manifest()` and
`examples/sdk_social_memory_framework_simulation.py` run the selected
`execute_task`/`dict` adapter plus complete `framework_trace` evidence as a
normal `agent-learning.run.v1` artifact.

SDK users can build the same kind of runnable framework optimization manifest
without hand-writing JSON:

```python
from agent_learning import optimize

manifest = optimize.build_framework_optimization_manifest(
    name="sdk-framework-adapter-optimization",
    framework="custom_refund_orchestrator",
    target="framework_shims.py:build_custom_refund_orchestrator",
    adapter_candidates=[
        {"method": "run", "input_mode": "text"},
        {"method": "execute_task", "input_mode": "dict"},
    ],
    environments=[{"type": "framework_trace", "data": framework_trace}],
    evaluation_config=agent_report_config,
    required_env=["AGENT_LEARNING_SDK_FRAMEWORK_OPT_KEY"],
)
result = optimize.optimize_manifest(manifest, manifest_path="examples/sdk.json")
```

The dedicated cookbook for that SDK path is
`examples/sdk_framework_adapter_optimization.py`; it optimizes a proprietary
`custom_refund_orchestrator` adapter from a weak `run/text` candidate to the
verified `execute_task/dict` runtime with local framework trace evidence.

For arbitrary task/world optimization, pass complete agent candidates plus the
world environments and eval config. Extra search paths can target any manifest
path, so the same helper covers memory, policy, provider, red-team, and custom
framework knobs:

```python
from agent_learning import optimize

result = optimize.optimize_task(
    name="refund-world-optimization",
    agent_candidates=[weak_agent],
    environments=[refund_world_contract],
    evaluation_config=agent_report_config,
    search_space={
        "agent.responses.0.tool_calls": [[], [approve_refund_tool_call]],
        "simulation.environments.0.data.transitions": [
            [],
            [approve_refund_transition],
        ],
    },
    required_env=["AGENT_LEARNING_API_KEY"],
)
```

For component-diagnosed architecture/config repair, pass failed report text or
metric evidence to `optimize.build_component_optimization_manifest()`. The SDK
maps failure evidence to component diagnoses, filters the search space to
relevant non-prompt paths, and runs AgentOptimizer over complete agent configs,
world/framework/memory evidence bundles, and any explicit component config
candidates you provide. The same flow is runnable through
`examples/sdk_component_optimization.py`.

For full orchestration-stack optimization, pass coherent stack candidates with
world, framework, retrieval, memory-lineage, and multi-agent room evidence. The
SDK searches each stack as one `simulation.environments` bundle so evidence
from different candidates cannot be mixed accidentally:

```python
from agent_learning import optimize

manifest = optimize.build_orchestration_optimization_manifest(
    name="refund-orchestration-optimization",
    agent_candidates=[weak_agent, strong_agent],
    stack_candidates=[weak_stack, strong_stack],
    evaluation_config=agent_report_config,
    required_env=["AGENT_LEARNING_API_KEY"],
)
result = optimize.optimize_manifest(manifest, manifest_path="examples/sdk.json")
```

For direct, non-optimizer orchestration simulation,
`simulate.build_orchestration_stack_run_manifest()` and
`examples/sdk_orchestration_simulation.py` run a selected world/framework/
retrieval/memory/multi-agent stack as a normal `agent-learning.run.v1` artifact.
For the product-named whole architecture flow,
`simulate.build_world_framework_memory_run_manifest()` derives a direct run
from the same native defaults that power
`optimize.optimize_world_framework_memory()`.
Both simulation and optimization artifacts expose an `orchestration_strategy`
report card for Future AGI UI, CLI, SDK, and CI surfaces; run artifacts rerun
with `agent-learn run`, while optimization artifacts rerun with
`agent-learn optimize`.
Orchestration-stack optimization artifacts also emit
`agent-learning.optimization.orchestration-stack-proof.v1`, a native proof
derived from the selected candidate and selected report. It checks that the
selected stack is local and has no endpoint/auth/key dependency, the world,
framework, retrieval, memory-lineage, and multi-agent environments move as one
bundle, the UI/CLI strategy card is closed, trace provenance exists across
world transitions, framework spans, retrieval citations, memory lineage, and
multi-agent review/reconciliation, world/framework/retrieval/memory/
multi-agent/tool evidence closes, the selected patch covers both agent behavior
and the orchestration environment bundle, topology/replay metadata exists, the
optimizer did not regress from the seed, and orchestration metrics pass.
The same optimization result can be promoted with
`simulate.promote_to_regression()` or `agent-learn promote-to-regression` into
an `orchestration_stack_optimization` regression manifest. The promoted manifest
freezes the selected local world/framework/retrieval/memory/multi-agent
environment bundle, preserves the orchestration proof and replay lock under
`metadata.regression`, and replays through `agent-learn replay` without importing
the target framework or calling external observability/eval services. Promotion
is fail-closed: endpoint/auth/API-key/secret/token markers or
`requires_external_service=true` refuse admission instead of falling through to a
generic optimized-manifest regression.

For retrieval and memory layers, pass candidates with `retrieval_memory` and
`agent_memory_lineage` data. The SDK builds a runnable local manifest that
searches current-document grounding, source attribution, memory writes, policy
checks, canaries, observability, and memory-lineage artifacts as one coherent
candidate bundle.
Memory optimization artifacts also emit
`agent-learning.optimization.memory-lineage-proof.v1`, a native proof derived
from the selected candidate and selected report. It checks that the selected
memory bundle is local, current retrieval cites only current documents with
freshness evidence, source lineage is closed, read/write/recall operations are
audited, tenant isolation/audit/retention/deletion/redaction policies are
enforced, poisoning/isolation/retention gaps are closed, observability and audit
artifacts exist, and retrieval/provenance/integrity metrics pass.
For direct, non-optimizer simulation,
`simulate.build_memory_layer_run_manifest()` and `examples/sdk_memory_simulation.py`
run a selected retrieval/memory-lineage bundle as a normal
`agent-learning.run.v1` artifact.

For live RAG/retrieval services, `retrieval_hook` environments call real HTTP
retriever endpoints with bearer/API-key env auth, then normalize ranked
documents, top-k, freshness, citations, latency, and redacted request metadata
into the same `retrieval_memory_trace` evidence used by the evaluator. The
`sdk_retrieval_hook_optimization.py` example searches stale static retrieval,
missing-auth HTTP retrieval, and verified authenticated retrieval bundles with
AgentOptimizer. SDK entry points are
`simulate.build_retrieval_hook_run_manifest()`,
`optimize.build_retrieval_hook_optimization_manifest()`, and
`optimize.optimize_retrieval_hooks()`; generated artifacts work with
`agent-learn report`, `agent-learn actions`, and `agent-learn action-run`.

For task-specific external judges, evaluation hooks POST normalized task/run
evidence to real HTTP evaluator endpoints with bearer/API-key env auth and
record a redacted `evaluation_hook_trace` on every returned metric. Use
`evals.build_evaluation_hook_config()` or
`evals.evaluate_task_evidence_with_hook()` for direct scoring, or pass
`--eval-hook` to `agent-learn eval-task` for promptfoo-style CLI usage. The
`sdk_evaluation_hook_optimization.py` example lets AgentOptimizer search agent
candidates against the live external metric; SDK entry points are
`simulate.build_evaluation_hook_run_manifest()`,
`optimize.build_evaluation_hook_optimization_manifest()`, and
`optimize.optimize_evaluation_hooks()`.

For saved task/run artifacts, pass artifact field-extraction candidates and
fixed structured assertions. The SDK builds a promptfoo-style optimization
suite that selects the artifact adapter fields needed to evaluate saved
evidence without rerunning the agent. Assertions can target JSON paths such as
`fields.task_completion` with typed equality, existence, numeric bounds, and
containment checks, so artifact gates do not depend on serialized JSON
formatting.

For raw task evidence that is not yet a full run artifact, use
`evals.build_task_evidence_artifact()` or `evals.evaluate_task_evidence()`.
The SDK normalizes messages, tool calls, metrics, environment state, artifacts,
and events into the same agent-report shape used by `agent-learn eval-artifact`.
The same path is available from the CLI and suite runner with
`agent-learn eval-task`, so CI can score arbitrary task transcripts or tool
run exports without writing Python glue.

Behavior entropy is a native local agent-report metric for loop detection and
action-pattern quality. `behavior_entropy_quality` measures action entropy,
tool entropy, trajectory entropy, information gain, exploration efficiency,
repetition rate, and adjacent loop rate. Use
`evals.behavior_entropy_report()` or `simulate.behavior_entropy_artifact()` for
direct artifacts, or add `behavior_entropy_quality` to `metric_weights` so
AgentOptimizer can select candidates that solve the task without repeatedly
calling the same tool. The metric is inspired by 2026 behavior-entropy research
and runs without external eval, observability, or optimizer services.

Collaborative competence is a native local multi-agent process metric.
`collaborative_competence_quality` scores explicit common-ground updates, shared
task state, mental-model annotations, partner-intent predictions, misalignment
repair moves, balanced role participation, value-diversity preservation, and
protocol trace evidence. Use `evals.collaborative_competence_report()` or
`simulate.collaborative_competence_artifact()` for direct artifacts, or add the
metric to AgentOptimizer weights so candidate search selects teams that
collaborate well instead of only producing a final answer. The metric builds on
June 2026 collaboration, mental-model, value-diversity, critique, and
protocol-aligned trajectory research, but remains deterministic and local-only.

For multi-agent coordination, pass explicit participant roles, agent trace
candidates, and room-contract candidates. The SDK builds a runnable
`multi_agent_room` optimization manifest that can search handoff, review,
reconciliation, and shared room-state behavior together.
Multi-agent coordination optimization artifacts also emit
`agent-learning.optimization.multi-agent-coordination-proof.v1`, a native proof
derived from the selected candidate and selected report. It checks that the
selected room is local, roles are explicit, unknown roles are blocked, handoff
contracts match, expected handoffs/reviews/reconciliation close, critic review
and accepted-source reconciliation are conflict-free, shared room state reaches a
terminal case status, temporal agent trace and structural room patches are both
covered, and multi-agent metrics pass.
For direct, non-optimizer simulation,
`simulate.build_multi_agent_coordination_run_manifest()` and
`examples/sdk_multi_agent_simulation.py` run one selected agent trace plus room
contract as a normal `agent-learning.run.v1` artifact.

For realtime voice and streaming stacks, pass paired candidates with `voice`
and `streaming_trace` data. The SDK builds a runnable local optimization
manifest that searches call routing, voice timing/audio quality, and streaming
tool-delta evidence as one coherent candidate bundle.

Runnable SDK cookbook:

```bash
AGENT_LEARNING_SDK_TASK_WORLD_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_task_world_optimization.py \
  artifacts/sdk-task-world-optimization.json

AGENT_LEARNING_SDK_BEHAVIOR_ENTROPY_KEY=... \
  PYTHONPATH=src python examples/sdk_behavior_entropy_optimization.py \
  artifacts/sdk-behavior-entropy-optimization.json

AGENT_LEARNING_SDK_COLLABORATIVE_COMPETENCE_KEY=... \
  PYTHONPATH=src python examples/sdk_collaborative_competence_optimization.py \
  artifacts/sdk-collaborative-competence-optimization.json

AGENT_LEARNING_SDK_COMPONENT_OPTIMIZATION_KEY=... \
  PYTHONPATH=src python examples/sdk_component_optimization.py \
  artifacts/sdk-component-optimization.json

AGENT_LEARNING_SDK_ORCHESTRATION_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_orchestration_optimization.py \
  artifacts/sdk-orchestration-optimization.json

AGENT_LEARNING_SDK_WORLD_FRAMEWORK_MEMORY_KEY=... \
  PYTHONPATH=src python examples/sdk_world_framework_memory_optimization.py \
  artifacts/sdk-world-framework-memory-optimization.json

AGENT_LEARNING_SDK_WORLD_HOOKS_KEY=... \
  PYTHONPATH=src python examples/sdk_world_hooks_optimization.py \
  artifacts/sdk-world-hooks-optimization.json

AGENT_LEARNING_SDK_ORCHESTRATION_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_orchestration_simulation.py \
  artifacts/sdk-orchestration-simulation.json

AGENT_LEARNING_SDK_MEMORY_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_memory_optimization.py \
  artifacts/sdk-memory-optimization.json

AGENT_LEARNING_SDK_MEMORY_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_memory_simulation.py \
  artifacts/sdk-memory-simulation.json

AGENT_LEARNING_SDK_ARTIFACT_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_artifact_optimization.py \
  artifacts/sdk-artifact-optimization.json

AGENT_LEARNING_SDK_TASK_EVAL_KEY=... \
  PYTHONPATH=src python examples/sdk_task_evaluation.py \
  artifacts/sdk-task-evaluation.json

AGENT_LEARNING_SDK_TASK_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_task_simulation.py \
  artifacts/sdk-task-simulation.json

AGENT_LEARNING_SDK_REALTIME_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_realtime_voice_simulation.py \
  artifacts/sdk-realtime-voice-simulation.json

AGENT_LEARNING_SDK_EVAL_SUITE_KEY=... \
  PYTHONPATH=src python examples/sdk_eval_suite.py \
  artifacts/sdk-eval-suite.json

AGENT_LEARNING_SDK_EVAL_SUITE_OPTIMIZATION_KEY=... \
  PYTHONPATH=src python examples/sdk_eval_suite_optimization.py \
  artifacts/sdk-eval-suite-optimization.json

PYTHONPATH=src agent-learn eval-task examples/task_evidence.json \
  --config examples/task_evidence_eval_config.json \
  --output artifacts/task-evidence-eval.json

PYTHONPATH=src agent-learn suite examples/task_evidence_suite.json \
  --output artifacts/task-evidence-suite.json

AGENT_LEARNING_SDK_MULTI_AGENT_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_multi_agent_optimization.py \
  artifacts/sdk-multi-agent-optimization.json

AGENT_LEARNING_SDK_MULTI_AGENT_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_multi_agent_simulation.py \
  artifacts/sdk-multi-agent-simulation.json

AGENT_LEARNING_SDK_MULTI_AGENT_FRAMEWORK_HANDOFF_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_multi_agent_framework_handoff_optimization.py \
  artifacts/sdk-multi-agent-framework-handoff-optimization.json

AGENT_LEARNING_SDK_MULTI_AGENT_FRAMEWORK_HANDOFF_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_multi_agent_framework_handoff_simulation.py \
  artifacts/sdk-multi-agent-framework-handoff-simulation.json

AGENT_LEARNING_SDK_OPTIMIZER_GOVERNANCE_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_optimizer_governance_optimization.py \
  artifacts/sdk-optimizer-governance-optimization.json

AGENT_LEARNING_SDK_OPTIMIZER_PORTFOLIO_KEY=... \
  PYTHONPATH=src python examples/sdk_optimizer_portfolio_optimization.py \
  artifacts/sdk-optimizer-portfolio-optimization.json

AGENT_LEARNING_SDK_OPTIMIZER_GOVERNANCE_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_optimizer_governance_simulation.py \
  artifacts/sdk-optimizer-governance-simulation.json

AGENT_LEARNING_SDK_REALTIME_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_realtime_voice_optimization.py \
  artifacts/sdk-realtime-voice-optimization.json

AGENT_LEARNING_SDK_REDTEAM_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_optimization.py \
  artifacts/sdk-redteam-optimization.json

AGENT_LEARNING_SDK_REDTEAM_AUTOGEN_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_autogen_optimization.py \
  artifacts/sdk-redteam-autogen-optimization.json

AGENT_LEARNING_SDK_ADAPTIVE_REDTEAM_OPT_KEY=... \
  PYTHONPATH=src python examples/sdk_adaptive_redteam_optimization.py \
  artifacts/sdk-adaptive-redteam-optimization.json

AGENT_LEARNING_SDK_REDTEAM_ADAPTIVE_LOOP_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_adaptive_loop_optimization.py \
  artifacts/sdk-redteam-adaptive-loop-optimization.json

AGENT_LEARNING_SDK_REDTEAM_ATTACK_EVOLUTION_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_attack_evolution_optimization.py \
  artifacts/sdk-redteam-attack-evolution-optimization.json

AGENT_LEARNING_SDK_REDTEAM_RUN_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_simulation.py \
  artifacts/sdk-redteam-run.json

AGENT_LEARNING_SDK_LONG_HORIZON_REDTEAM_KEY=... \
  PYTHONPATH=src python examples/sdk_long_horizon_redteam_simulation.py \
  artifacts/sdk-long-horizon-redteam.json

AGENT_LEARNING_SDK_LONG_HORIZON_REDTEAM_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_long_horizon_redteam_optimization.py \
  artifacts/sdk-long-horizon-redteam-optimization.json

AGENT_LEARNING_SDK_REDTEAM_SOCIETY_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_society_optimization.py \
  artifacts/sdk-redteam-society-optimization.json

AGENT_LEARNING_SDK_REDTEAM_CAUSAL_ATTRIBUTION_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_causal_attribution_optimization.py \
  artifacts/sdk-redteam-causal-attribution-optimization.json

AGENT_LEARNING_SDK_AGENT_CONTROL_PLANE_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_agent_control_plane_optimization.py \
  artifacts/sdk-agent-control-plane-optimization.json

AGENT_LEARNING_SDK_AGENT_CONTROL_PLANE_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_agent_control_plane_simulation.py \
  artifacts/sdk-agent-control-plane-simulation.json

AGENT_LEARNING_SDK_BROWSER_CUA_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_browser_cua_optimization.py \
  artifacts/sdk-browser-cua-optimization.json

AGENT_LEARNING_SDK_BROWSER_CUA_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_browser_cua_simulation.py \
  artifacts/sdk-browser-cua-simulation.json

AGENT_LEARNING_SDK_AGENT_INTEGRATION_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_agent_integration_optimization.py \
  artifacts/sdk-agent-integration-optimization.json

AGENT_LEARNING_SDK_AGENT_INTEGRATION_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_agent_integration_simulation.py \
  artifacts/sdk-agent-integration-simulation.json

AGENT_LEARNING_SDK_WORKSPACE_OBSERVABILITY_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_workspace_observability_optimization.py \
  artifacts/sdk-workspace-observability-optimization.json

AGENT_LEARNING_SDK_WORKSPACE_IMPORT_CERTIFICATION_KEY=... \
  PYTHONPATH=src python examples/sdk_workspace_import_certification_optimization.py \
  artifacts/sdk-workspace-import-certification-optimization.json

AGENT_LEARNING_SDK_REDTEAM_READINESS_CERTIFICATION_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_readiness_certification_optimization.py \
  artifacts/sdk-redteam-readiness-certification-optimization.json

AGENT_LEARNING_SDK_WORKSPACE_OBSERVABILITY_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_workspace_observability_simulation.py \
  artifacts/sdk-workspace-observability-simulation.json

AGENT_LEARNING_SDK_FRAMEWORK_CERTIFICATION_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_framework_certification_optimization.py \
  artifacts/sdk-framework-certification-optimization.json

AGENT_LEARNING_SDK_FRAMEWORK_OPT_KEY=... \
  PYTHONPATH=src python examples/sdk_framework_adapter_optimization.py \
  artifacts/sdk-framework-adapter-optimization.json

AGENT_LEARNING_SDK_FRAMEWORK_CERTIFICATION_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_framework_certification_simulation.py \
  artifacts/sdk-framework-certification-simulation.json

AGENT_LEARNING_SDK_ARTIFACT_ACTION_OPTIMIZATION_KEY=... \
  PYTHONPATH=src python examples/sdk_artifact_action_optimization.py \
  artifacts/sdk-artifact-action-optimization.json

AGENT_LEARNING_SDK_SOCIAL_MEMORY_FRAMEWORK_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_social_memory_framework_optimization.py \
  artifacts/sdk-social-memory-framework-optimization.json

AGENT_LEARNING_SDK_SOCIAL_MEMORY_FRAMEWORK_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_social_memory_framework_simulation.py \
  artifacts/sdk-social-memory-framework-simulation.json

AGENT_LEARNING_SDK_AUTONOMOUS_REDTEAM_TASK_WORLD_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_autonomous_redteam_task_world_optimization.py \
  artifacts/sdk-autonomous-redteam-task-world-optimization.json

AGENT_LEARNING_SDK_AUTONOMOUS_REDTEAM_TASK_WORLD_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_autonomous_redteam_task_world_simulation.py \
  artifacts/sdk-autonomous-redteam-task-world-simulation.json

AGENT_LEARNING_SDK_MULTIMODAL_IMAGE_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_multimodal_image_optimization.py \
  artifacts/sdk-multimodal-image-optimization.json

AGENT_LEARNING_SDK_MULTIMODAL_IMAGE_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_multimodal_image_simulation.py \
  artifacts/sdk-multimodal-image-simulation.json

AGENT_LEARNING_SDK_MULTI_FRAMEWORK_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_multi_framework_simulation.py \
  artifacts/sdk-multi-framework-simulation.json

AGENT_LEARNING_SDK_SUITE_OPT_EXAMPLE_KEY=... \
AGENT_LEARNING_MULTI_FRAMEWORK_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_suite_optimization.py \
  artifacts/sdk-suite-optimization.json

AGENT_LEARNING_SDK_TRINITY_SUITE_KEY=... \
  PYTHONPATH=src python examples/sdk_trinity_suite.py \
  artifacts/sdk-trinity-suite.json

AGENT_LEARNING_SDK_REGRESSION_ARTIFACT_SUITE_KEY=... \
  PYTHONPATH=src python examples/sdk_regression_artifact_suite.py \
  artifacts/sdk-regression-artifact-suite.json
```

The `artifact_task_eval_suite.json` example evaluates a saved
`agent-learning.run.v1` task artifact as first-class evidence. Its `artifact`
provider loads JSON/YAML artifacts, extracts named paths such as task
completion, verification status, framework runtime, and world-contract metrics,
then runs structured promptfoo-style JSON-path assertions through
`agent-learn eval`. This is the CI path for evaluating task/world artifacts
without rerunning the agent.
`evals.build_eval_suite_manifest()` and `evals.write_eval_suite_file()` provide
the same promptfoo-style eval-suite path from Python; the
`sdk_eval_suite.py` cookbook writes an eval manifest plus a one-job
`agent-learning.suite.v1` wrapper so SDK-built evals can run through both
`agent-learn eval` and `agent-learn suite`.
`optimize.build_eval_suite_optimization_manifest()` and
`examples/sdk_eval_suite_optimization.py` add the matching SDK optimization
path: keep promptfoo-style tests fixed and search provider response candidates
through `optimize.optimize_eval_suite_response()`.
For direct agent-report metrics over an existing artifact, `agent-learn
eval-artifact` consumes the same saved artifact plus
`artifact_task_eval_config.json` and emits JSON, JUnit, SARIF, and Markdown
without requiring an eval-suite wrapper.

The `regression_artifact_suite.json` example runs the artifact lifecycle that
teams usually script around promptfoo-style CI: create a compact baseline,
compare current vs baseline, render a report, promote a red-team finding into a
runnable regression manifest, and replay that manifest, all as first-class
`agent-learn suite` jobs with a capability gate.
`suite.build_regression_artifact_suite_manifest()` and
`examples/sdk_regression_artifact_suite.py` expose the same lifecycle from the
SDK; the cookbook writes local baseline/current/finding/replay artifacts, runs
the generated suite, and verifies promotion plus replay evidence.

The `voice_streaming_realtime_manifest.json` example makes `voice` and
`streaming_trace` first-class manifest environments. It replays voice timing,
transcription, call routing, TTS, and streaming token/tool events through one
local realtime simulation artifact.
`simulate.build_realtime_run_manifest()` and
`examples/sdk_realtime_voice_simulation.py` expose the same LiveKit-style voice
plus streaming replay from Python, including transcript, routing,
timing-distribution, TTS, and streaming tool-delta evidence.

The `voice_streaming_realtime_optimization.json` example optimizes the same
voice plus streaming harness through `agent-learn optimize`, selecting the
candidate with clean call routing, voice timing/audio quality, and streaming
tool-delta evidence.

The `redteam_campaign_optimization.json` example optimizes an adversarial
attack-pack, campaign matrix, readiness, observability, and mitigation harness
through `agent-learn optimize`, selecting the candidate with clean red-team
campaign and readiness gates. The optimizer uses deterministic
`simulation_evidence` scoring for the readiness preflight, so target,
framework-import, campaign, workspace-run, trust-boundary, control-plane,
observability, artifact, and blocking-gap evidence must all close before the
harness scores as ready.

The `redteam_autogen_optimization.json` example starts from
`redteam.auto_generate: true` and optimizes the declared attack/surface matrix;
each candidate regenerates local adversarial attack-pack and campaign evidence
before scoring.
The same generated-matrix workflow is available from Python through
`optimize.build_redteam_autogen_optimization_manifest()` and
`examples/sdk_redteam_autogen_optimization.py`, which starts from a tool-only
prompt-injection seed and searches the tool-plus-memory prompt-injection plus
credential-exfiltration campaign. Red-team reports expose `surface_matrix` and
`adaptive_surface_risk` fields so CI and the Future AGI UI can show which
attack surface is still blind even when aggregate campaign quality is high.
Red-team optimization artifacts also emit
`agent-learning.optimization.redteam-campaign-proof.v1`, a native proof derived
from the selected candidate and selected report. It checks that the selected
campaign is local and has no endpoint/auth/key dependency, attack-pack payloads
are replayable with verifier terms, attack/surface/channel/provider matrix
cells have scenario, run, artifact, executed evidence, mitigation, and
passed-run closure, observability and high-risk findings are closed, selected
attack-system candidates include planner/check/research/canary/blocked-tool
evidence, red-team councils close handoff/review/reconciliation contracts when
present, causal attribution graphs close root-cause/mitigation/evidence mapping
when present, the selected patch changes a red-team harness surface, the
optimizer does not regress from the seed, and red-team metrics pass.
Proof-carrying campaign results can be promoted with
`simulate.promote_to_regression()` or `agent-learn promote-to-regression` into a
typed `redteam_campaign_optimization` replay manifest. Promotion freezes the
selected attack/surface/channel/provider matrix, stores a local-only
`replay_lock`, carries selected metric thresholds and 2026 research provenance,
and fails closed if the selected artifact contains endpoint/auth/key markers.
`agent-learn replay` then reruns the local campaign judges from the promoted
manifest, giving promptfoo-style CLI users and SDK users the same regression
gate without requiring an external optimizer, judge, or observability service.

For evidence-driven adaptive red teaming, pass a prior red-team artifact/path or
failure text to `optimize.build_adaptive_redteam_optimization_manifest()`. The
SDK reads `redteam_strategy.adaptive_surface_risk`, missing campaign cells, and
raw red-team findings when present, converts them into component diagnoses, and
searches coherent campaign candidates where attacks, surfaces, personas,
trajectory-refinement strategy, canaries, and blocked tools move together. The
same helper is exposed as `optimize_adaptive_redteam_strategy()` for report
action-card workflows, and `examples/sdk_adaptive_redteam_optimization.py`
selects the hardened adaptive campaign from a blind memory-surface source
artifact.

`red_team_adaptive_loop_quality` makes the adaptive loop itself measurable:
strategy generation, execution evidence, trajectory refinement, outcome
feedback, verifier checks, monitor calibration, and memory/tool/retrieval/
multi-agent vectors are scored separately from broad campaign matrix quality.
Use `evals.redteam_adaptive_loop_report()` or
`simulate.redteam_adaptive_loop_artifact()` for direct local artifacts, or add
the metric to optimization weights. `examples/sdk_redteam_adaptive_loop_optimization.py`
proves a static probe fails and the hardened adaptive loop passes without any
external service; it also writes a `.manifest.json` that promptfoo-style CLI
users can rerun with `agent-learn optimize`.

`red_team_attack_evolution_coverage` and
`red_team_attack_evolution_quality` add a native 2026-style evolutionary
red-team proof loop: seed attacks, mutation rounds, cross-round feedback,
verifier predicates, counterexamples, minimized replays, replay regressions,
path/surface expansion, and local-only execution are scored separately from
campaign breadth. Use `evals.redteam_attack_evolution_report()`,
`simulate.redteam_attack_evolution_artifact()`, or
`optimize.build_redteam_attack_evolution_optimization_manifest()`.
`examples/sdk_redteam_attack_evolution_optimization.py` proves a weak seed-only
loop fails and the verified attack-evolution loop passes, writes a
`.manifest.json` for `agent-learn optimize`, and attaches
`agent-learning.optimization.redteam-attack-evolution-proof.v1`. Its optimized
result can be promoted with `simulate.promote_to_regression()` or
`agent-learn promote-to-regression` into a replayable
`red_team_attack_evolution` regression manifest; `agent-learn replay` then
reruns status, mutation, counterexample, minimized-replay, and gap tools with
`red_team_attack_evolution_coverage=1.0` and
`red_team_attack_evolution_quality=1.0`.
For a smaller regression gate, use `simulate.shrink_attack_evolution()` or
`agent-learn shrink <artifact> --manifest artifacts/attack-evolution-shrink.json`
(`agent-learn minimize` is an alias). Shrink turns one verified counterexample
into a typed, content-addressed, local-only minimal repro manifest with
counterexample minimization, independent replay assertions, JSON/JUnit/SARIF/
Markdown outputs, and the same replay command surface; it deliberately does not
require broad path/surface expansion, because the gate is proving minimal
reproducibility rather than campaign coverage.
`agent-learn report` now renders the same loop as a native
`attack_evolution` action card for Future AGI UI, SDK, and CI users. The card
summarizes mutation lineage, counterexample minimization, replay closure,
proof status, metrics, and exact CLI actions to report, promote, replay, and
export local artifacts: `attack-evolution-action-card.json`,
`attack-evolution-trace.jsonl`, `attack-evolution-minimal-repro.json`, and
`attack-evolution-replay.lock.json`. This follows the 2026 research direction
toward trajectory-aware attack evolution, feedback-driven attack refinement,
self-evolving skill red teams, dynamic integration benchmarks, and replaying
failed trajectories into regression evidence without adding external runtime
services.

The `long_horizon_redteam_optimization.json` example builds on recent agentic
red-team research by searching coherent attack-system candidates instead of
independent labels. It starts from one objective-integrity probe, escalates into
stateful tool and memory pressure, and selects the full long-horizon campaign
across intent hijacking, task injection, objective drift, tool chaining, and
memory poisoning over instruction, tool, memory, retrieval, and environment
surfaces. The same attack-system search is available from Python through
`optimize.build_long_horizon_redteam_optimization_manifest()` and
`examples/sdk_long_horizon_redteam_optimization.py`.

The `redteam_society_optimization.json` example adds a multi-agent red-team
council around the long-horizon attack system. It searches weak, partial, and
verified `multi_agent_room` candidates until the selected council has
orchestrator-leak, tool-chain, memory-privacy, critic, and steward roles with
explicit handoff contracts, review, reconciliation, and full 25-cell red-team
campaign evidence. The same society search is available from Python through
`optimize.build_redteam_society_optimization_manifest()` and
`examples/sdk_redteam_society_optimization.py`.

The `redteam_causal_attribution_optimization.json` example takes the council one
step further: it searches for a candidate that can diagnose a multi-agent
red-team failure with an acyclic causal graph, mapped root causes, mitigations,
and run evidence. The design builds on 2026 causal graph tracing, strategy
network red-teaming, and client-side agent optimization research, then adds our
own deterministic contract for root-cause and mitigation closure. The same
causal search is available from Python through
`optimize.build_redteam_causal_attribution_optimization_manifest()` and
`examples/sdk_redteam_causal_attribution_optimization.py`.

The `sdk_stateful_tool_world_optimization.py` example adds a benchmark-style
stateful tool-world red-team cookbook. It searches weak, partial, and verified
environment bundles until the selected candidate closes executable state deltas,
unsafe-action blocking, temporal takeover localization, persistent-state
containment, utility-under-attack, and world-contract success together. The
SDK entry points are `simulate.build_stateful_tool_world_run_manifest()`,
`simulate.build_stateful_tool_world_environments()`,
`optimize.build_stateful_tool_world_optimization_manifest()`, and
`optimize.optimize_stateful_tool_world()`.

The `sdk_world_model_optimization.py` example is the internal world-model arena:
no external endpoint is required. It builds on 2026 world-model and environment
synthesis work by searching L1 predictor, L2 simulator, and L3 evolver
environment bundles, then selecting the candidate whose executable transitions,
verifier contracts, adversarial dynamics, curriculum metadata, and
world-contract evidence all close. SDK entry points are
`simulate.build_world_model_run_manifest()`,
`optimize.build_world_model_optimization_manifest()`, and
`optimize.optimize_world_model()`. Use
`optimize.build_world_hooks_optimization_manifest()` or
`optimize.optimize_world_hooks()` when the product surface should be named as
world hooks: it still searches the same native executable world-state hooks and
does not require an external endpoint. Generated world-hook candidates carry
`agent-learning.world-hooks-contract.v1`, which declares in-process callable
hooks, state scopes, replay semantics, evidence channels, and
`requires_external_service=false`.
World-model and world-hook optimization artifacts also emit
`agent-learning.optimization.world-hook-proof.v1`. The proof is derived from
the selected candidate and selected report, then checks that the hook is native
(`requires_external_service=false` and no `endpoint`/`auth` keys), executable
hook contract closed, executable state transitions closed, world-contract
invariants and success conditions closed, adversarial pressure was contained,
persistent memory/provenance channels were contained, and closed
`world_hook_contract_quality`, world, and eval metrics are present. Passing L3
candidates report `l3_verified_native_world_hooks` in both `world_hook_proof`
and the artifact summary. `examples/sdk_world_hooks_optimization.py` is the
dedicated cookbook for this native surface; it uses a local key only for SDK
configuration and redaction checks, not for any external hook endpoint.
`agent-learn report <world-hooks-result.json>` also emits a `world_hooks`
action card with the native proof, hook contract, replay lock, selected
metrics, research sources, Markdown proof-check tables, and CLI/download
actions for Future AGI UI, SDK, and CI users. The same result can now be
promoted with `simulate.promote_to_regression()` or
`agent-learn promote-to-regression` into a replayable
`world_hooks_optimization` regression manifest; `agent-learn replay` then
re-executes the frozen local `stateful_tool_world` + `world_contract` bundle
and gates `world_hook_contract_quality=1.0` and
`world_contract_quality=1.0` without any endpoint/auth hook. Promotion is
fail-closed for world-hook results: if the frozen environments contain
`endpoint`, `auth`, API-key/secret/token markers, or
`requires_external_service=true`, the result is not admitted as a replayable
world-hook regression.
`optimize.score_simulation_evidence()` also treats world hooks as a first-class
native component: it extracts `agent-learning.world-hooks-contract.v1` from the
local `stateful_tool_world` evidence and scores the same contract fields as
ai-evaluation (`mode`, `runtime`, callable hooks, output channels, state scopes,
surfaces, replay semantics, evidence requirements, and no external dependency).
That gives AgentOptimizer metric-based diagnosis for executable world hooks
without turning `optimize_world_hooks()` into an HTTP-hook integration.
The same scorer now treats `framework_lifecycle_trace` as a native
`framework_lifecycle` component. Framework certification and migration runs can
diagnose setup, tool registration, sessions, invocation, streaming, checkpoints,
retry/recovery, cancellation/resume, cleanup, terminal status, state persistence,
and required lifecycle signals directly from local simulator evidence. This keeps
framework optimization aligned with the 2026 harness direction while avoiding any
hosted optimizer or external lifecycle service.

The `report_repair_optimization.json` example turns a failed agent
report/trace into a deterministic repair search. It scores normalized
simulation evidence directly, then selects the candidate whose framework trace,
runtime semantics, orchestration replay, memory lineage, tool evidence, and
world contract all close. The design builds on 2026 trace-provenance, causal
repair, runtime-semantics, and agent-harness optimization research, then adds
our own optimizer-native simulation evidence scorer. The same repair search is
available from Python through
`optimize.build_report_repair_optimization_manifest()` and
`examples/sdk_report_repair_optimization.py`.

The `framework_import_repair_optimization.json` example optimizes BYO
framework/provider import readiness before Future AGI exposes the agent through
UI observability, evals, simulation, red-team, and optimization workflows. It
scores normalized import evidence directly, then selects the candidate whose
target, adapter, source coverage, trace/event/lifecycle/capability/probe/
portability exports, observability hooks, artifacts, and failed-source gaps all
close. The design builds on 2026 harness optimization, runtime-semantics,
trace-provenance, and causal-repair research, then adds our own deterministic
framework import readiness contract. The same repair search is available from
Python through
`optimize.build_framework_import_repair_optimization_manifest()` and
`examples/sdk_framework_import_repair_optimization.py`.
For direct promptfoo-style preflight, `simulate.probe_framework_imports()` now
performs real Python imports and optional explicit callable invocation, then
returns normalized `framework_import_manifest` evidence. Use
`simulate.build_framework_import_run_manifest()` or
`examples/sdk_framework_import_probe_simulation.py` to write a runnable
`agent-learning.run.v1` artifact, execute it with `agent-learn run`, report it
with `agent-learn report`, and feed the same evidence into framework-readiness
optimization.

`optimize.build_workspace_import_certification_optimization_manifest()` and
`examples/sdk_workspace_import_certification_optimization.py` combine those live
import probes with repository/workspace evidence: checked-out path, provenance,
commands, logs, artifacts, eval/optimizer readiness, security gates,
observability hooks, credentials, and framework import sources are optimized as
one `simulation.environments` bundle. This builds on 2026 workspace/repository
agent benchmarks and eval-integrity work, then adds our own Future AGI
certification contract so a UI/API workflow can checkout a user repo, certify
that it is runnable, and then expose `agent-learn report`, `agent-learn
actions`, and `agent-learn action-run` follow-ups from the saved artifact.

`optimize.build_redteam_readiness_certification_optimization_manifest()` and
`examples/sdk_redteam_readiness_certification_optimization.py` add the next
preflight: a single optimized readiness gate that binds workspace-run evidence,
live framework-import probes, a multi-turn red-team campaign matrix,
trust-boundary controls, runtime control-plane controls, observability, and
artifacts before adaptive adversarial search starts. The helper searches weak
versus verified `simulation.environments` bundles with deterministic
`simulation_evidence` scoring, using 2026 agentic red-team, monitor red-team,
runtime trust, controllable environment, autonomous red-team, and stored prompt
injection research as input, then adds our own zero-blocking-gap Future AGI
certification contract.

`redteam.build_redteam_corpus_campaign()`,
`simulate.build_redteam_corpus_run_manifest()`, and
`optimize.build_redteam_corpus_optimization_manifest()` add benchmark/corpus
red-team import inside the same SDK. Rows from RedBench/HarmBench/
JailbreakBench/DTap-style datasets become normalized `red_team_campaign`
evidence with taxonomy, domain, source lineage, trajectories, findings,
artifacts, mitigations, observability, and verifiable-judge metadata. The
optimizer searches weak/partial/verified corpus candidates as
`simulation.environments` and scores the campaign directly with deterministic
`simulation_evidence`, so missing taxonomy/source/matrix evidence becomes an
optimizer diagnosis rather than a silent prompt-list gap. See
`examples/sdk_redteam_corpus_optimization.py`.
CLI users can import the same local corpus rows without a hook:
`agent-learn redteam-corpus --corpus examples/redteam_corpus.json --output
artifacts/redteam-corpus.json`. The command accepts a top-level list or an
object with `rows`, `corpus_rows`, `attacks`, or `cases`, then emits the same
campaign evidence and report/action follow-ups used by SDK callers.
Corpus imports require the exact benchmark cells represented by their rows by
default; callers can still pass explicit campaign dimensions when they want an
exhaustive attack/surface/channel/provider matrix.
V1 release readiness also gates this evidence: `agent-learn release-check`
expects the required red-team examples plus corpus-only and broader
research-backed attack/surface/source coverage to remain present.

`redteam.fetch_redteam_corpus_hook()` and
`redteam.build_redteam_corpus_hook_campaign()` add an optional authenticated
external red-team corpus hook. A live HTTP endpoint can return RedBench/DTap/
MonitoringBench/SOAR-style rows under `rows`, `corpus_rows`, `attacks`, or
`cases`; the SDK fetches them with bearer env auth, records a redacted
`redteam_corpus_hook_trace`, and normalizes them into the same
`red_team_campaign` evidence used by static corpus imports. The
`agent-learn redteam-corpus --hook ...` command writes the campaign artifact
directly, and `examples/sdk_redteam_corpus_hook.py` starts a real local
authenticated hook for SDK/CLI verification.

The `workspace_observability_optimization.json` example migrates the old
workspace-run and observability-replay cookbooks into one CLI manifest. It
optimizes the Future AGI UI/control-plane evidence loop: repository checkout,
command logs, artifacts, simulations, evals, red-team runs, UI verification,
live credential checks, security gates, AgentOptimizer results, and failed
observability replay rows.
The same evidence loop is available from Python through
`optimize.build_workspace_observability_optimization_manifest()` and
`examples/sdk_workspace_observability_optimization.py`, which generate the
workspace-run plus observability-replay candidates and search them as one
`simulation.environments` bundle.
For direct, non-optimizer simulation,
`simulate.build_workspace_observability_run_manifest()` and
`examples/sdk_workspace_observability_simulation.py` run the verified Future AGI
workspace and observability replay evidence as a normal `agent-learning.run.v1`
artifact.

The `agent_integration_optimization.json` example optimizes provider and
framework integration coverage for the Future AGI UI/observability/evals layer.
It verifies agent definition, personas, simulations, observability hooks, eval
metrics, credentials, sessions, and channel coverage across LiveKit, Vapi,
Retell, Bland, ElevenLabs, Deepgram, Agora, Pipecat, Twilio, and
TraceAI-supported frameworks. The optimizer uses deterministic
`simulation_evidence` scoring for the integration manifest, so provider,
channel, provider-channel, TraceAI/framework trace, credential, transcript,
session, observability, and eval-metric evidence must all close before the
candidate scores as integrated.
Run `agent-learn capabilities --require
providers=livekit,vapi,retell,elevenlabs,deepgram,agora,pipecat,twilio
--require channels=voice,webrtc,phone,sip,websocket` to inspect the installed
provider matrix before wiring a customer integration.
The same workflow is available from Python through
`optimize.build_agent_integration_optimization_manifest()` and
`examples/sdk_agent_integration_optimization.py`, which generate the provider
matrix and search it as one `agent_integration` environment candidate.
For direct, non-optimizer simulation,
`simulate.build_agent_integration_run_manifest()` and
`examples/sdk_agent_integration_simulation.py` run the verified provider matrix
as a normal `agent-learning.run.v1` artifact across LiveKit, Vapi, Retell,
Bland, ElevenLabs, Deepgram, Agora, Pipecat, Twilio, and TraceAI-supported
framework traces. Run `agent-learn report <artifact>` on either artifact to get
the `agent_integration_readiness` card with provider/channel/framework gaps,
credential/session/observability/eval counts, provider matrix rows, Markdown,
and actions for report, rerun, and optimization.

The `sdk_external_http_agent_optimization.py` example adds the first external
target-agent adapter cookbook. It starts from a real HTTP/OpenAI-compatible
endpoint, keeps auth in `AGENT_LEARNING_SDK_EXTERNAL_HTTP_AGENT_KEY`, preserves
OpenAI `tool_calls`, records a redacted HTTP trace in simulation state, and uses
AgentOptimizer to select the complete adapter contract that actually supplies
tool evidence. The SDK entry points are
`simulate.build_external_agent_run_manifest()`,
`optimize.build_external_agent_adapter_optimization_manifest()`, and
`optimize.optimize_external_agent_adapter()`, and the artifact works with the
same `agent-learn report`, `actions`, and `action-run` CLI flow as the other
promptfoo-style cookbooks.

The `sdk_workflow_hook_optimization.py` example adds executable HTTP workflow
hooks to the simulator. A manifest can expose `workflow_hook` tools backed by
real POST endpoints, bearer/API-key env auth, redacted request metadata,
HTTP status/latency traces, and state updates. The optimizer searches whole
workflow-hook environment bundles, so it can reject mocked hooks and missing
auth before selecting the verified authenticated hook. The SDK entry points are
`simulate.build_workflow_hook_run_manifest()`,
`optimize.build_workflow_hook_optimization_manifest()`, and
`optimize.optimize_workflow_hooks()`.

The `sdk_retrieval_hook_optimization.py` example adds executable HTTP
retrieval/RAG hooks. A manifest can expose `retrieval_hook` tools backed by a
real retriever endpoint, bearer/API-key env auth, ranked source documents,
currentness/freshness flags, citations, status/latency traces, and redacted
request metadata. The optimizer searches complete retrieval environment bundles
so it can reject stale static context and missing auth before selecting the
verified authenticated hook.

The `sdk_evaluation_hook_optimization.py` example adds authenticated external
evaluator hooks. Agent-report configs can declare `evaluation_hooks` that POST
normalized case evidence to a real evaluator endpoint, accept returned
`metrics` or a top-level `score`, and attach redacted endpoint/auth/status/
latency metadata to each metric. The optimizer searches agent candidates while
keeping the evaluator fixed as an executable metric source, and the same hook
is available from `agent-learn eval-task --eval-hook`.

The `multi_agent_framework_handoff_optimization.json` example optimizes
captured multi-agent framework transcripts across OpenAI Agents, AutoGen,
CrewAI, and LangGraph. It uses `AgentEvolutionOptimizer` through
`agent-learn optimize` and verifies handoffs, review, reconciliation, source
grounding, checkpoint lineage, and framework transcript quality from local JSONL
fixtures.
The same harness is available from Python through
`optimize.build_multi_agent_framework_handoff_optimization_manifest()` and
`examples/sdk_multi_agent_framework_handoff_optimization.py`, which search
weak, partial, and complete framework-transcript plus multi-agent-room bundles
with the evolutionary optimizer.
For direct, non-optimizer simulation,
`simulate.build_multi_agent_framework_handoff_run_manifest()` and
`examples/sdk_multi_agent_framework_handoff_simulation.py` run the verified
OpenAI Agents, AutoGen, CrewAI, LangGraph, and `multi_agent_room` handoff
evidence as a normal `agent-learning.run.v1` artifact. The SDK helper can also
resolve relative transcript export sources against an explicit
`export_source_base_dir`, so generated manifests remain runnable from CI
artifact directories.

The `optimizer_governance_optimization.json` example optimizes an optimizer
society trace, making multi-interaction search auditable. It verifies roles,
proposals, rounds, diagnostics, role credit, best-candidate selection, and
governance checks for role diversity, mediator review, contract gates,
rollback, search locality, and dependency audit.
Optimization manifests can select the mutation-aware evolutionary optimizer with
`optimization.optimizer.algorithm: "evolution"`, then tune `population_size`,
`generations`, `elite_count`, `mutation_rate`, `crossover_rate`, `seed`,
`target_score`, and `max_library_candidates`. This reuses the same
`agent-learn optimize` command, but searches coherent framework/world/memory/
multi-agent patches from the Agent Mutation Library instead of only enumerating
flat candidate values.
The governed society-trace harness is available from Python through
`optimize.build_optimizer_governance_optimization_manifest()` and
`examples/sdk_optimizer_governance_optimization.py`, which search weak versus
governed optimizer traces and verify role diversity, diagnostics, search-path
locality, rollback lineage, and governance pass rate.
`simulate.build_optimizer_governance_run_manifest()` and
`examples/sdk_optimizer_governance_simulation.py` run the selected governed
optimizer trace as a normal `agent-learning.run.v1` artifact, so optimizer
decisions can be audited through simulation evidence without starting another
optimizer loop.
They can also select the social-memory optimizer with
`optimization.optimizer.algorithm: "social_memory"`, then tune `max_rounds`,
`beam_width`, `max_proposals_per_round`, `target_score`, `include_seed`, and
`auto_diagnose` for multi-round role/credit-ledger search over framework,
world, memory, and evaluator patches.

The `sdk_optimizer_portfolio_optimization.py` example makes optimizer selection
itself a native SDK evidence object. It builds on 2026 client-side agent
optimization, retrospective harness, black-box test-time control, causal tool
frontier, and Pareto archive research, but does not call AgentOpt, Foundry, or
any hosted optimizer. The SDK searches weak versus verified
`optimizer_backend_portfolio` candidates, then gates the selected portfolio on
backend plan/run breadth, lineage, consensus ablation, diagnoses, feedback
cases, search paths, rollback evidence, and metric closure. Public entry points
are `simulate.optimizer_backend_portfolio_artifact()`,
`simulate.build_optimizer_backend_portfolio_run_manifest()`,
`optimize.build_optimizer_portfolio_optimization_manifest()`, and
`optimize.optimize_optimizer_portfolio()`. Passing runs emit
`agent-learning.optimization.optimizer-portfolio-proof.v1` with
`l3_native_optimizer_portfolio_verified`, no `endpoint`/`auth`/API-key
dependency, and closed `optimizer_portfolio_quality` plus
`optimizer_portfolio_coverage` metrics. The generated manifest is also runnable
from the promptfoo-style CLI with `agent-learn optimize <manifest>`.
`optimize.score_simulation_evidence()` now emits native `optimizer_governance`
and `optimizer_portfolio` components from those same local environment-state
artifacts. This lets AgentOptimizer diagnose the optimizer itself from role
credit, governance gates, backend lineage, consensus, rollback, and local-only
dependency evidence instead of delegating optimizer selection to a hosted
service. The design tracks the 2026 direction from trace-guided harness repair,
validation-gated multi-agent governance, social agent evolution, and
evidence-calibrated credit assignment while keeping the SDK contract portable.

The `agent_learning.optimize` SDK facade exposes the advanced optimizer,
deployment, replay, research, and governance APIs from the vendored engine:
multi-interaction optimizers, council/society search, Future AGI replay
optimizers, deployment export/promotion/rollback checks, regression replay-pack
builders, research corpus helpers, optimizer society traces, component
diagnosis, mutation libraries, simulation/eval-suite optimization bridges,
deep-merge/path helpers, and concrete optimizer classes.

The `agent_control_plane_optimization.json` example optimizes a red-team
readiness gate for autonomous agents. It verifies the trust boundary and runtime
agency controls: identity, permissions, sandboxing, audit, canaries, human
approval, memory isolation, network egress, tool allowlists, data boundaries,
secret handling, risk scoring, action policy, rollback, kill switches, circuit
breakers, rate limits, budgets, containment, and drift detection.
The same gate is available from Python through
`optimize.build_agent_control_plane_optimization_manifest()` and
`examples/sdk_agent_control_plane_optimization.py`, which search weak versus
hardened `agent_trust_boundary` plus `agent_control_plane` candidates as one
`simulation.environments` bundle.
For direct, non-optimizer simulation,
`simulate.build_agent_control_plane_run_manifest()` and
`examples/sdk_agent_control_plane_simulation.py` run the hardened trust-boundary
plus runtime control-plane evidence as a normal `agent-learning.run.v1`
artifact.

The `browser_cua_optimization.json` example optimizes a browser/computer-use
red-team harness. It verifies selector-drift recovery, refreshed screenshots,
coordinate grounding, semantic screenshot diffs, storage/runtime evidence,
network traces, layout-shift resilience, mutation-pack mitigations, and
prompt-injection surface avoidance.
The same harness is available from Python through
`optimize.build_browser_cua_optimization_manifest()` and
`examples/sdk_browser_cua_optimization.py`, which search weak versus hardened
browser/CUA replay candidates as one `simulation.environments` bundle.
For direct, non-optimizer simulation, `simulate.build_browser_cua_run_manifest()`
and `examples/sdk_browser_cua_simulation.py` run the hardened browser/CUA replay
as a normal `agent-learning.run.v1` artifact with selector fallback,
mutation-pack, storage/runtime, network, visual grounding, and
prompt-injection-safety evidence.

The `framework_certification_optimization.json` example optimizes a framework
certification harness before rollout or migration. It verifies lifecycle
session evidence, capability matrices, adapter smoke probes, and source-target
portability mappings for framework-neutral agent stacks.
The same harness is available from Python through
`optimize.build_framework_certification_optimization_manifest()` and
`examples/sdk_framework_certification_optimization.py`, which search weak
versus certified lifecycle, capability, probe, and portability candidates as
one `simulation.environments` bundle.
For direct, non-optimizer simulation,
`simulate.build_framework_certification_run_manifest()` and
`examples/sdk_framework_certification_simulation.py` run the certified
lifecycle, capability, probe, and portability evidence as a normal
`agent-learning.run.v1` artifact. Both run and optimization artifacts include a
`framework_readiness` report card; run artifacts rerun with `agent-learn run`,
optimization artifacts rerun with `agent-learn optimize`, and both expose
`optimize_framework_readiness` for the next certification/search pass.
Framework-certification optimization artifacts also emit
`agent-learning.optimization.framework-certification-proof.v1`, a native proof
derived from the selected candidate and selected report. The proof builds on
2026 findings that framework failures cluster around orchestration control,
memory/failure handling, retry/cost behavior, protocol risk, and deterministic
harness evidence, then turns that direction into our own local verifier: no
endpoint/auth/key dependency, all four certification environments present,
lifecycle evidence closed, capability/probe/portability layers closed,
cross-protocol boundaries present, framework metrics closed, and the UI/CLI
readiness card ready.
The same optimization result can be promoted with
`simulate.promote_to_regression()` or `agent-learn promote-to-regression` into a
`framework_certification_optimization` regression manifest. The promoted manifest
freezes the selected lifecycle/capability/probe/portability evidence bundle,
preserves the framework-certification proof and replay lock under
`metadata.regression`, and replays through `agent-learn replay` without importing
the target framework or calling external observability/eval services. Promotion
is fail-closed: endpoint/auth/API-key/secret/token markers or
`requires_external_service=true` refuse admission instead of falling through to a
generic optimized-manifest regression.
`examples/sdk_artifact_action_optimization.py` takes the next step: it creates a
real certification artifact, extracts the readiness action cards, and runs an
`agent-learning.suite.v1` optimization where each candidate is an `action-run`
job. `agent-learn action-optimize` runs the same action search directly from a
saved artifact for promptfoo-style CLI workflows.

The `autonomous_redteam_task_world_optimization.json` example optimizes a
local autonomous task/world red-team harness. It verifies structured artifacts,
domain package invariants, world-state progress, adversarial canary resistance,
tool argument schemas, autonomy-loop stages, memory writes, skill storage, and
stop decisions through `agent-learn optimize`.
The same harness is available from Python through
`optimize.build_autonomous_redteam_task_world_optimization_manifest()` and
`examples/sdk_autonomous_redteam_task_world_optimization.py`, which search weak
versus hardened artifact, domain-package, world-attack, and autonomy-loop
evidence as one `simulation.environments` bundle.
For direct, non-optimizer simulation,
`simulate.build_autonomous_redteam_task_world_run_manifest()` and
`examples/sdk_autonomous_redteam_task_world_simulation.py` run the hardened
artifact, domain-package, world-attack, adversarial, and autonomy-loop evidence
as a normal `agent-learning.run.v1` artifact.

The `multimodal_image_optimization.json` example optimizes a local vision
fixture harness. It verifies image artifacts, image inspection tools, structured
OCR/layout evidence, artifact grounding, artifact semantics, and trajectory
multimodal faithfulness before approving an image-grounded refund.
The same harness is available from Python through
`optimize.build_multimodal_image_optimization_manifest()` and
`examples/sdk_multimodal_image_optimization.py`, which search weak versus
hardened image evidence as one `simulation.environments` bundle.
For direct, non-optimizer simulation,
`simulate.build_multimodal_image_run_manifest()` and
`examples/sdk_multimodal_image_simulation.py` run the hardened receipt image,
OCR/layout, artifact-grounding, artifact-semantics, and multimodal trajectory
evidence as a normal `agent-learning.run.v1` artifact.
