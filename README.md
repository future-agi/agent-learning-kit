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
agent-learn init ./agent-learning-trinity-project --preset all --force
agent-learn run examples/voice_streaming_realtime_manifest.json --no-eval \
  --output artifacts/voice-streaming-realtime.json
agent-learn eval-cli list categories --format json
agent-learn eval-cli init ./eval-project --template basic --force
agent-learn doctor
```

`agent-learn init` scaffolds runnable Agent Learning projects. The optimize
preset generates a local task/world optimization manifest that patches both an
agent action and a world-contract transition, then can be run with
`agent-learn optimize`. The all preset generates a self-contained trinity
workspace with local run, promptfoo-style eval, structured artifact eval,
direct artifact-report eval, red-team, eval-suite optimization, task/world
optimization, a saved task artifact fixture, and `manifests/suite.json` as the
single CI entrypoint.

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
`agent_learning.redteam.build_redteam_manifest()` builds direct SDK red-team
run manifests with `redteam.auto_generate: true`, so Python callers can create
the same attack-pack and campaign evidence as `agent-learn redteam` without
hand-writing JSON.

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
    artifact_eval_config_path="artifact_task_eval_config.json",
    redteam_path="redteam_manifest.json",
    eval_optimization_path="eval_suite_optimization.json",
    optimization_path="world_framework_memory_optimization.json",
)
result = suite.run_suite(manifest, suite_path="examples/sdk_trinity_suite.json")
```

The `multi_framework_simulation_suite.json` example runs local LangChain,
LangGraph, Pipecat, LiveKit-style, and custom proprietary agents through the
same manifest framework adapter path, proving text and voice framework shims can
be simulated without adding framework-specific runtime dependencies. Unknown
framework names are accepted as custom adapters when the manifest supplies the
target method/input mode, as shown in `framework_custom_manifest.json`.

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
suite.

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

For retrieval and memory layers, pass candidates with `retrieval_memory` and
`agent_memory_lineage` data. The SDK builds a runnable local manifest that
searches current-document grounding, source attribution, memory writes, policy
checks, canaries, observability, and memory-lineage artifacts as one coherent
candidate bundle.
For direct, non-optimizer simulation,
`simulate.build_memory_layer_run_manifest()` and `examples/sdk_memory_simulation.py`
run a selected retrieval/memory-lineage bundle as a normal
`agent-learning.run.v1` artifact.

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

For multi-agent coordination, pass explicit participant roles, agent trace
candidates, and room-contract candidates. The SDK builds a runnable
`multi_agent_room` optimization manifest that can search handoff, review,
reconciliation, and shared room-state behavior together.
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

AGENT_LEARNING_SDK_ORCHESTRATION_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_orchestration_optimization.py \
  artifacts/sdk-orchestration-optimization.json

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

AGENT_LEARNING_SDK_REALTIME_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_realtime_voice_optimization.py \
  artifacts/sdk-realtime-voice-optimization.json

AGENT_LEARNING_SDK_REDTEAM_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_optimization.py \
  artifacts/sdk-redteam-optimization.json

AGENT_LEARNING_SDK_REDTEAM_AUTOGEN_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_autogen_optimization.py \
  artifacts/sdk-redteam-autogen-optimization.json

AGENT_LEARNING_SDK_REDTEAM_RUN_KEY=... \
  PYTHONPATH=src python examples/sdk_redteam_simulation.py \
  artifacts/sdk-redteam-run.json

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

AGENT_LEARNING_SDK_WORKSPACE_OBSERVABILITY_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_workspace_observability_simulation.py \
  artifacts/sdk-workspace-observability-simulation.json

AGENT_LEARNING_SDK_FRAMEWORK_CERTIFICATION_EXAMPLE_KEY=... \
  PYTHONPATH=src python examples/sdk_framework_certification_optimization.py \
  artifacts/sdk-framework-certification-optimization.json

AGENT_LEARNING_SDK_FRAMEWORK_CERTIFICATION_SIMULATION_KEY=... \
  PYTHONPATH=src python examples/sdk_framework_certification_simulation.py \
  artifacts/sdk-framework-certification-simulation.json

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
campaign and readiness gates.

The `redteam_autogen_optimization.json` example starts from
`redteam.auto_generate: true` and optimizes the declared attack/surface matrix;
each candidate regenerates local adversarial attack-pack and campaign evidence
before scoring.
The same generated-matrix workflow is available from Python through
`optimize.build_redteam_autogen_optimization_manifest()` and
`examples/sdk_redteam_autogen_optimization.py`, which starts from a tool-only
prompt-injection seed and searches the tool-plus-memory prompt-injection plus
credential-exfiltration campaign.

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
TraceAI-supported frameworks.
The same workflow is available from Python through
`optimize.build_agent_integration_optimization_manifest()` and
`examples/sdk_agent_integration_optimization.py`, which generate the provider
matrix and search it as one `agent_integration` environment candidate.
For direct, non-optimizer simulation,
`simulate.build_agent_integration_run_manifest()` and
`examples/sdk_agent_integration_simulation.py` run the verified provider matrix
as a normal `agent-learning.run.v1` artifact across LiveKit, Vapi, Retell,
Bland, ElevenLabs, Deepgram, Agora, Pipecat, Twilio, and TraceAI-supported
framework traces.

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
`agent-learning.run.v1` artifact.

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
