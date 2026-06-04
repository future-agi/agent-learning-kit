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

`agent-learning-kit` is the public SDK. The simulation, evaluation, and
optimization engine code is vendored into this package; public docs and
automation should use `agent_learning.*` and `agent-learn`.

New public SDK development belongs here. See [DEVELOPMENT.md](DEVELOPMENT.md)
for the boundary between this package and the backing engine repos.

CLI entrypoint:

```bash
agent-learn eval examples/eval_suite.json --output artifacts/eval.json
agent-learn eval examples/artifact_task_eval_suite.json \
  --output artifacts/artifact-task-eval.json
agent-learn eval-artifact examples/fixtures/task_artifacts/refund_task_run.json \
  --config examples/artifact_task_eval_config.json \
  --output artifacts/direct-artifact-eval.json
agent-learn optimize-eval examples/eval_suite_optimization.json \
  --output artifacts/eval-optimization.json
agent-learn run examples/run_manifest.json --no-eval --output artifacts/run.json
agent-learn redteam examples/redteam_manifest.json --output artifacts/redteam.json
agent-learn optimize examples/optimization_manifest.json --output artifacts/optimization.json
agent-learn optimize examples/world_framework_memory_optimization.json \
  --output artifacts/world-framework-memory-optimization.json
agent-learn optimize examples/voice_streaming_realtime_optimization.json \
  --output artifacts/voice-streaming-realtime-optimization.json
agent-learn optimize examples/redteam_campaign_optimization.json \
  --output artifacts/redteam-campaign-optimization.json
agent-learn optimize examples/redteam_autogen_optimization.json \
  --output artifacts/redteam-autogen-optimization.json
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
agent-learn optimize examples/autonomous_redteam_task_world_optimization.json \
  --output artifacts/autonomous-redteam-task-world-optimization.json
agent-learn optimize examples/multimodal_image_optimization.json \
  --output artifacts/multimodal-image-optimization.json
agent-learn suite examples/agent_learning_suite.json --output artifacts/suite.json
agent-learn suite examples/multi_framework_simulation_suite.json \
  --output artifacts/multi-framework-suite.json
agent-learn run examples/framework_custom_manifest.json --no-eval \
  --output artifacts/framework-custom.json
agent-learn optimize examples/custom_framework_optimization.json \
  --output artifacts/custom-framework-optimization.json
agent-learn optimize examples/social_memory_framework_optimization.json \
  --output artifacts/social-memory-framework-optimization.json
agent-learn suite examples/regression_artifact_suite.json \
  --output artifacts/regression-artifact-suite.json
agent-learn init ./agent-learning-project --preset optimize --force
agent-learn run examples/voice_streaming_realtime_manifest.json --no-eval \
  --output artifacts/voice-streaming-realtime.json
agent-learn eval-cli list categories --format json
agent-learn eval-cli init ./eval-project --template basic --force
agent-learn doctor
```

`agent-learn init` scaffolds runnable Agent Learning projects. The optimize
preset generates a local task/world optimization manifest that patches both an
agent action and a world-contract transition, then can be run with
`agent-learn optimize`.

`agent-learn run`, `agent-learn eval`, `agent-learn redteam`,
`agent-learn optimize`, `agent-learn optimize-eval`, and `agent-learn suite`
write Agent Learning Kit artifact kinds
(`agent-learning.run.v1`, `agent-learning.eval.v1`,
`agent-learning.redteam.v1`, `agent-learning.optimization.v1`, and
`agent-learning.eval-optimization.v1`, plus `agent-learning.suite.v1`) plus
optional JUnit, SARIF, and Markdown outputs for CI.
`agent-learn eval-cli ...` bridges the vendored ai-evaluation management CLI
under the unified command for template listing, project scaffolding,
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
`agent_learning.redteam` exposes the red-team runtime directly: manifest
execution, adversarial/campaign/readiness environments, local guardrail scanner
pipelines, Protect/guardrail config types, code-security metrics, CWE detector
helpers, and agent trajectory safety metrics.

The `world_framework_memory_optimization.json` example optimizes a
LangGraph-style world orchestration across framework trace, retrieval, memory
lineage, and multi-agent review evidence.

The `agent_learning_suite.json` example is the promptfoo-style CI entrypoint:
one manifest runs simulation, the nested multi-framework adapter suite, eval,
artifact-task eval, direct artifact-report eval, red-team, eval-suite
optimization, world/framework/memory optimization, voice/streaming optimization, red-team optimization,
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
Suite jobs can call other suite manifests, so the top-level suite can enforce
coverage from composed child suites without losing nested child artifacts.

The `multi_framework_simulation_suite.json` example runs local LangChain,
LangGraph, Pipecat, LiveKit-style, and custom proprietary agents through the
same manifest framework adapter path, proving text and voice framework shims can
be simulated without adding framework-specific runtime dependencies. Unknown
framework names are accepted as custom adapters when the manifest supplies the
target method/input mode, as shown in `framework_custom_manifest.json`.

The `custom_framework_optimization.json` example runs the same bring-your-own
framework path through `agent-learn optimize`. It starts with a runnable but weak
`run`/`text` adapter and deterministically selects the `execute_task`/`dict`
adapter because only that candidate emits tool evidence and passes the framework
runtime contract.

The `social_memory_framework_optimization.json` example selects
`optimization.optimizer.algorithm: "social_memory"`, a multi-round optimizer
with metric-bound role memory. It starts with both a weak adapter and weak trace,
then synthesizes the high-credit adapter patch and framework-trace patch into a
`sangha` best candidate. The emitted optimizer society trace records the social
roles (`smriti`, `arjuna`, `vidura`, `sangha`, `dharma_steward`), proposal
rounds, role credit, governance checks, and the final synthesized patch.

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

For multi-agent coordination, pass explicit participant roles, agent trace
candidates, and room-contract candidates. The SDK builds a runnable
`multi_agent_room` optimization manifest that can search handoff, review,
reconciliation, and shared room-state behavior together.

Runnable SDK cookbook:

```bash
AGENT_LEARNING_SDK_TASK_WORLD_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_task_world_optimization.py \
  artifacts/sdk-task-world-optimization.json

AGENT_LEARNING_SDK_MULTI_AGENT_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_multi_agent_optimization.py \
  artifacts/sdk-multi-agent-optimization.json

AGENT_LEARNING_SDK_REDTEAM_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_optimization.py \
  artifacts/sdk-redteam-optimization.json
```

The `artifact_task_eval_suite.json` example evaluates a saved
`agent-learning.run.v1` task artifact as first-class evidence. Its `artifact`
provider loads JSON/YAML artifacts, extracts named paths such as task
completion, verification status, framework runtime, and world-contract metrics,
then runs normal promptfoo-style assertions through `agent-learn eval`. This is
the CI path for evaluating task/world artifacts without rerunning the agent.
For direct agent-report metrics over an existing artifact, `agent-learn
eval-artifact` consumes the same saved artifact plus
`artifact_task_eval_config.json` and emits JSON, JUnit, SARIF, and Markdown
without requiring an eval-suite wrapper.

The `regression_artifact_suite.json` example runs the artifact lifecycle that
teams usually script around promptfoo-style CI: create a compact baseline,
compare current vs baseline, render a report, promote a red-team finding into a
runnable regression manifest, and replay that manifest, all as first-class
`agent-learn suite` jobs with a capability gate.

The `voice_streaming_realtime_manifest.json` example makes `voice` and
`streaming_trace` first-class manifest environments. It replays voice timing,
transcription, call routing, TTS, and streaming token/tool events through one
local realtime simulation artifact.

The `voice_streaming_realtime_optimization.json` example optimizes the same
voice plus streaming harness through `agent-learn optimize`, selecting the
candidate with clean call routing, voice timing/audio quality, and streaming
tool-delta evidence.

The `redteam_campaign_optimization.json` example optimizes an adversarial
attack-pack, campaign matrix, readiness, observability, and mitigation harness
through `agent-learn optimize`, selecting the candidate with clean red-team
campaign and readiness gates.

The `redteam_autogen_optimization.json` example starts from
`redteam.auto_generate: true` and optimizes the declared attack/surface matrix;
each candidate regenerates local adversarial attack-pack and campaign evidence
before scoring.

The `workspace_observability_optimization.json` example migrates the old
workspace-run and observability-replay cookbooks into one CLI manifest. It
optimizes the Future AGI UI/control-plane evidence loop: repository checkout,
command logs, artifacts, simulations, evals, red-team runs, UI verification,
live credential checks, security gates, AgentOptimizer results, and failed
observability replay rows.

The `agent_integration_optimization.json` example optimizes provider and
framework integration coverage for the Future AGI UI/observability/evals layer.
It verifies agent definition, personas, simulations, observability hooks, eval
metrics, credentials, sessions, and channel coverage across LiveKit, Vapi,
Retell, Bland, ElevenLabs, Deepgram, Agora, Pipecat, Twilio, and
TraceAI-supported frameworks.

The `multi_agent_framework_handoff_optimization.json` example optimizes
captured multi-agent framework transcripts across OpenAI Agents, AutoGen,
CrewAI, and LangGraph. It uses `AgentEvolutionOptimizer` through
`agent-learn optimize` and verifies handoffs, review, reconciliation, source
grounding, checkpoint lineage, and framework transcript quality from local JSONL
fixtures.

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
They can also select the social-memory optimizer with
`optimization.optimizer.algorithm: "social_memory"`, then tune `max_rounds`,
`beam_width`, `max_proposals_per_round`, `target_score`, `include_seed`, and
`auto_diagnose` for multi-round role/credit-ledger search over framework,
world, memory, and evaluator patches.

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

The `browser_cua_optimization.json` example optimizes a browser/computer-use
red-team harness. It verifies selector-drift recovery, refreshed screenshots,
coordinate grounding, semantic screenshot diffs, storage/runtime evidence,
network traces, layout-shift resilience, mutation-pack mitigations, and
prompt-injection surface avoidance.

The `framework_certification_optimization.json` example optimizes a framework
certification harness before rollout or migration. It verifies lifecycle
session evidence, capability matrices, adapter smoke probes, and source-target
portability mappings for framework-neutral agent stacks.

The `autonomous_redteam_task_world_optimization.json` example optimizes a
local autonomous task/world red-team harness. It verifies structured artifacts,
domain package invariants, world-state progress, adversarial canary resistance,
tool argument schemas, autonomy-loop stages, memory writes, skill storage, and
stop decisions through `agent-learn optimize`.

The `multimodal_image_optimization.json` example optimizes a local vision
fixture harness. It verifies image artifacts, image inspection tools, structured
OCR/layout evidence, artifact grounding, artifact semantics, and trajectory
multimodal faithfulness before approving an image-grounded refund.
