# Library inventory

Install `agent-learning-kit` once to use the Python libraries below. Start with
`from fi.alk import simulate` to [run a scenario](docs/quickstart/golden-path-run.md),
`from fi.alk import evals` to [score a result](docs/eval/overview.md), or
`from fi.alk import optimize` to [compare changes to your agent](docs/optimize/optimize-any-agent.md).

## Workflow facades and contained libraries

Each row links to the implementation and guide for this revision. The status
column uses these labels:

- **Public** identifies documented entry points in this version. The linked tests show which operations were exercised. Compatibility guarantees for future releases still need maintainer confirmation
- **Optional** identifies integrations with additional setup
- **Experimental** identifies version-sensitive interfaces
- **Private** identifies hosting internals

For credentials, outputs and tested examples, see [operational evidence by entry point](#operational-evidence-by-entry-point).

| Surface | Status | What you can use it for | Guide / source |
| --- | --- | --- | --- |
| `fi.alk` / `configure` | Public | Public configuration and lazy workflow imports. Keys do not make every operation platform-backed. | [Configuration](docs/reference/configure.md), [`__init__.py`](src/fi/alk/__init__.py), [`config.py`](src/fi/alk/config.py) |
| `fi.alk.simulate` | Public | Public simulation/manifests, runtime specs/reports, target and environment integration. | [Spec + Runner](docs/simulate/spec-and-runner.md), [worlds/hooks](docs/simulate/worlds-and-hooks.md), [`simulate.py`](src/fi/alk/simulate.py) |
| `fi.simulate` | Public | Included compatibility engine: scenarios/personas, environments, wrappers, adapters, runtime registry, manifests, suites, results and reports. Runtime plugins return legacy `TestReport`; `SimulationRunner` converts it. | [Extension contracts](docs/simulate/environment-authoring.md#supported-extension-seams), [`src/fi/simulate`](src/fi/simulate), [`tests/runtime/test_registry.py`](tests/runtime/test_registry.py) |
| `fi.alk.evals` | Public | Public suite/artifact/task evaluation facade, config builders, engine exports and HTTP hooks. | [Evaluation guide](docs/eval/overview.md), [`evals.py`](src/fi/alk/evals.py) |
| `fi.evals` | Public | Included compatibility engine: unified local/Turing/LLM evaluation, metrics, agent-report scoring, templates, guardrails, streaming, feedback and OTel. Not all modes are offline. | [Evaluation library map](docs/eval/overview.md#other-included-evaluation-libraries), [`src/fi/evals`](src/fi/evals) |
| `fi.alk.optimize` | Public | Public optimization manifests, candidate/evidence workflows, governance and backend routing. | [Optimize an agent](docs/optimize/optimize-any-agent.md), [lifecycle](docs/optimize/optimization-lifecycle.md), [`optimize.py`](src/fi/alk/optimize.py) |
| `fi.opt` | Public | Included compatibility engine: base classes, agent optimizers, mutation/evidence types, simulation integrations, deployment/observability utilities. Provider-backed algorithms can spend money. | [Optimizer portfolio](docs/optimize/optimizer-portfolio.md), [`src/fi/opt`](src/fi/opt) |
| `fi.alk.redteam` | Public | Public campaign/corpus/adaptive evaluation workflows over simulate/evals, not another external engine. | [Red-team guide](docs/redteam/red-team-anything.md), [`redteam.py`](src/fi/alk/redteam.py) |
| `fi.alk.harness` | Public | Public names are limited to `fi.alk.harness.__all__`. The bundled reference harness handles source understanding, world/data/scenario authoring, supported execution and grading. | [Harness README](src/fi/alk/harness/README.md), [authoring and seams](docs/simulate/environment-authoring.md), [`src/fi/alk/harness/__init__.py`](src/fi/alk/harness/__init__.py) |
| `fi.alk.studio` | Public | Public persona/scenario facade; create/load/save/validate/calibrate, coverage, vendor import and platform generation/pull. Shares engine classes, not a duplicate persona format. | [Studio](docs/simulate/persona-scenario-studio.md), [simulation contract](docs/prove/simulation-contract.md), [`studio/__init__.py`](src/fi/alk/studio/__init__.py) |
| `fi.alk.bench` | Public | Task/Verifier benchmark API with push, artifact input, and pull/RL control modes, plus coding and voice validators. Subprocess and Docker grading execute code. | [Benchmark overview](docs/eval/benchmark-overview.md), [sandbox boundary](docs/eval/benchmark-sandboxes.md), [`bench`](src/fi/alk/bench) |
| `fi.alk.loss` | Public | Versioned objective compilation and loss reports; guarded objectives, verdict/evidence admissibility and exclusions. | [Loss semantics](docs/eval/overview.md#loss-practice-and-benchmark-validators), [`loss.py`](src/fi/alk/loss.py) |
| `fi.alk.practice` / `fi.alk.improve` | Public | Practice execution, budget/schedule/report APIs and improvement workflows. Review the selected execution lane; internal operators are not separate public APIs. | [Practice loop](docs/prove/practice-loop.md), [`practice`](src/fi/alk/practice), [`improve.py`](src/fi/alk/improve.py) |
| `fi.alk.tasks` / `fi.alk.rewardhack` | Experimental | Task contracts and objective/anchor integrity checks used by improvement/benchmark workflows. These are advanced, version-sensitive APIs. | [`tasks.py`](src/fi/alk/tasks.py), [`rewardhack.py`](src/fi/alk/rewardhack.py), [benchmark authoring](docs/eval/benchmark-write-a-suite.md) |
| `fi.alk.episode` | Public | Typed voice episode/event record, provider converters, JSON serialization, schema export and conformance. Records preserve coverage/absence; conversion is not a new execution harness. | [`episode/__init__.py`](src/fi/alk/episode/__init__.py), [`episode/models.py`](src/fi/alk/episode/models.py), [voice benchmark](docs/eval/benchmark-voice.md) |
| `fi.alk.telemetry` | Public | Local run ledger, identity/provenance and opt-in account sync. Account delivery is separate from evaluation provider calls. | [Run ledger](docs/prove/run-ledger.md), [account sync](docs/prove/account-sync.md), [`telemetry`](src/fi/alk/telemetry) |
| `fi.alk.live` / `fi.alk.providers` | Optional | Framework/provider execution lanes and provider integration code. Deterministic fixture evidence does not prove a live adapter. | [Framework guides](docs/cookbooks/index.md#by-framework), [hosted targets](docs/simulate/provider-hosted-targets.md), [`live`](src/fi/alk/live), [`providers`](src/fi/alk/providers) |
| `voice_loop`, `image_loop`, `cua_loop`, `image_perturb` | Optional | Channel-specific loop/perturbation helpers. Deterministic fixtures do not establish real-device/provider coverage. | [Voice](docs/simulate/voice-loopback.md), [image](docs/simulate/image-loop.md), [CUA](docs/simulate/cua-loop.md), [`src/fi/alk`](src/fi/alk) |
| `suite`, `actions`, `capabilities`, `extensions`, `trinity` | Public | Workflow composition, artifact actions/catalogs, descriptive extension metadata and release checks. `extensions.register_environment` is not the runtime plugin registry. | [Suite](docs/prove/trinity-suite.md), [actions](docs/prove/actions.md), [capabilities](docs/prove/capabilities.md), [seams](docs/simulate/environment-authoring.md#supported-extension-seams), [`src/fi/alk`](src/fi/alk) |
| `agent-learn`, `alk` | Public | Installed aliases for `fi.alk.cli:main`. Each command states its inputs, credentials, network behavior and output in the CLI reference. | [CLI reference](docs/reference/cli.md), [`cli.py`](src/fi/alk/cli.py), [`pyproject.toml`](pyproject.toml) |
| `alk-harness-worker` | Private | Hosted job worker. Job-scoped source and secret references come from the hosting system. `tests/harness/test_hosted_entrypoint.py` verifies the entry point. | [`hosted_entrypoint.py`](src/fi/alk/harness/hosted_entrypoint.py), [`tests/harness/test_hosted_entrypoint.py`](tests/harness/test_hosted_entrypoint.py) |
| `alk-harness-sandbox` | Private | Hosted sandbox HTTP service. Sandbox and job credentials come from the hosting system. | [`sandbox_server.py`](src/fi/alk/harness/sandbox_server.py), [`tests/test_harness_sandbox_server.py`](tests/test_harness_sandbox_server.py), [`Dockerfile.sandbox`](Dockerfile.sandbox) |
| `@future-agi/agent-learning-kit` workspace | Experimental (source workspace) | TypeScript evaluation workspace with declared root, `/evals`, `/evals/local`, and `/local` exports. The manifest is evidence of source and build configuration, not npm publication or Python runtime parity. | [Evaluate responses in TypeScript](https://docs.futureagi.com/docs/sdk/agent-learning-kit/evaluate-responses-in-typescript), [TypeScript boundaries](#typescript-boundaries), [`package.json`](typescript/agent-learning-kit/package.json), [package README](typescript/agent-learning-kit/README.md) |

## Operational evidence by entry point

`Base` means the Python package with its default dependencies. Check the row for your workflow before supplying credentials or running code.

| Entry point | Installation | Execution / network class | Credentials | Output | Verification | Limits |
| --- | --- | --- | --- | --- | --- | --- |
| `fi.alk` / `configure` | Base | In-process configuration; no I/O until a selected workflow uses it | None for local lanes; Future AGI key for platform operations | Process configuration and lazy imports | [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py), `agent-learn doctor` | Configuration does not prove provider or service readiness. |
| `fi.alk.simulate`, `fi.simulate` | Base; adapter extras as selected | The `EchoAgent` + synthetic-user spec and custom `order_status_world` plugin are in-process, credential-free, and make no provider call. HTTP, framework, model, voice, and hosted adapters can execute caller code or use the network. | None for the two named fixtures; selected adapter credentials otherwise | `SimulationReport`; compatibility plugins can return `TestReport`; JSON summaries in the examples | [`examples/sdk_spec_runner_quickstart.py`](examples/sdk_spec_runner_quickstart.py), [`examples/sdk_environment_plugin.py`](examples/sdk_environment_plugin.py), [`tests/runtime/test_registry.py`](tests/runtime/test_registry.py), clean-install SDK smoke | `COMPLETED` proves execution, not response quality. The examples are checkout resources; the clean-install job copies the plugin outside the checkout after installing the wheel. |
| `fi.alk.evals`, `fi.evals` | Base | `examples/eval_suite.json` uses the local `echo` provider with no network. Turing, LLM, HTTP, feedback, and exporter modes can use services. | None for the echo manifest; mode-specific keys otherwise | `agent-learning.eval.v1`, metric/verdict data, or engine-specific results | [`tests/test_vendored_simulate_engine.py`](tests/test_vendored_simulate_engine.py), [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py) `test_eval_suite_builder_and_sdk_cookbook_runs` | An echo/fixture result does not certify a model evaluator or platform rule. Some metrics have additional model/data dependencies. |
| `fi.alk.optimize`, `fi.opt` | Base; provider extras as selected | Local manifest search/evidence lanes can be credential-free; provider-backed candidates, evaluators, deployment, and observability can use network and spend money. | None for documented local fixtures; provider/deployment keys otherwise | Optimization manifest, candidates, evidence, best configuration, and optimization artifact | [`tests/test_vendored_opt_eval_engine.py`](tests/test_vendored_opt_eval_engine.py), optimization examples in [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py) | Results cover only the declared search space, budgets, evaluator, and evidence lane. |
| `fi.alk.redteam` | Base; providers as selected | Local corpus/fixture campaigns can run in-process; adaptive generation, judges, hooks, and hosted targets can use the network. | None for named local fixtures; provider/platform keys otherwise | Campaign/corpus findings and red-team artifacts | Red-team example tests in [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py), [`tests/test_phase12_voice_redteam.py`](tests/test_phase12_voice_redteam.py) | Fixture findings do not establish live-target coverage. Attack generation and grading are separate lanes. |
| `fi.alk.harness` | Base; `harness-ui`, store, tracing, or framework extras as selected | Source authoring is model-backed and can execute generated/submitted code; run/live/environment lanes can start services or contact targets. It is not the offline first step. | Selected backend/model credentials; target, store, platform, and runtime credentials as applicable | Contract, world, scenarios, bundles, run events/results, grades, and evidence | `scripts/harness-stability-gate.sh`, [`tests/harness/test_public_material_admission.py`](tests/harness/test_public_material_admission.py) | Public names are only `fi.alk.harness.__all__`. Stage, runtime-plugin, bundle, and whole-executor contracts are not interchangeable. |
| `fi.alk.studio` | Base | Typed local create/load/save/validate/calibrate paths are local; platform generation/pull uses the network. | None locally; Future AGI credentials for platform operations | Personas, scenarios, coverage and calibration data | Studio tests in [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py), [`tests/simulation_contract/test_contract_models.py`](tests/simulation_contract/test_contract_models.py) | Local objects do not prove platform generation or live simulation quality. |
| `fi.alk.bench` | Base; Docker or channel dependencies for selected sandbox/validator | Artifact-input and deterministic validators can be local; subprocess/Docker modes execute untrusted code; push/pull or provider graders can use network. | None for local artifact fixtures; registry/provider credentials as selected | Benchmark result, verifier evidence and artifacts | [`tests/test_bench.py`](tests/test_bench.py), [`tests/test_bench_cli.py`](tests/test_bench_cli.py), [`tests/test_bench_voice.py`](tests/test_bench_voice.py) | Sandbox strength depends on the selected executor. Pull/RL control mode is not implemented by every command. |
| `fi.alk.loss` | Base | Pure objective compilation/reporting is in-process; upstream evaluators determine external I/O. | None for pure compilation | Compiled objectives and loss reports | Loss/evidence tests in [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py) | A loss report inherits the admissibility and quality limits of its evidence. |
| `fi.alk.practice`, `fi.alk.improve` | Base; operator/provider dependencies as selected | Local fixtures and report readers can be offline; operators, candidates, judges, stores, and deployment can run code or use network. | None for documented local fixtures; integration credentials otherwise | Practice schedule/run/report and improvement artifacts | [`tests/practice`](tests/practice), practice/improvement tests in [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py) | Budget and gate success apply only to the selected operator and evidence. Internal operators are not separate public APIs. |
| `fi.alk.tasks`, `fi.alk.rewardhack` | Base | Typed contracts and deterministic integrity checks are in-process; task executors can introduce their own effects. | None for deterministic checks | Task, objective, anchor, and reward-hack findings | [`tests/test_rewardhack_bench.py`](tests/test_rewardhack_bench.py), task tests in [`tests`](tests) | Advanced, version-sensitive contracts; deterministic detectors do not cover every reward attack. |
| `fi.alk.episode` | Base; provider/channel dependencies as selected | Model conversion and JSON/schema operations are local; capture/provider ingestion is lane-dependent. | None for local conversion; provider keys for live ingestion | Typed episode/events, JSON and schema | Episode tests in [`tests`](tests), voice conformance in [`tests/test_bench_voice.py`](tests/test_bench_voice.py) | Conversion records available evidence; it does not recreate missing events or execute an agent. |
| `fi.alk.telemetry` | Base | Local ledger is file-backed. Account sync is explicit network I/O; `forget` writes a local tombstone only. | None locally; Future AGI credentials for account sync | Ledger rows, verification result, sync receipt, or local tombstone | [`tests/telemetry/test_telemetry_ledger.py`](tests/telemetry/test_telemetry_ledger.py), telemetry boundary tests in [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py) | Local mode does not block network chosen by other workflows. Previously synced content requires separate account-side erasure. |
| `fi.alk.live`, `fi.alk.providers` | Base plus the selected integration extra/package | Live/provider lane; uses the selected framework, service, endpoint, or transport network | Provider, framework, target, and region/account configuration as selected | Provider responses, traces and adapter evidence | Credentialed lane: `AGENT_LEARNING_LIVE_CREDENTIALED=1 ... tests/live`; deterministic adapter tests under [`tests`](tests) | Fixture tests do not certify a live account, model, region, quota, or future provider version. |
| `voice_loop`, `image_loop`, `cua_loop`, `image_perturb` | Base; channel extras/hardware as selected | Documented fixtures are credential-free and offline; real devices, browsers, transports, models, or media services are separate lanes. | None for fixtures; integration credentials otherwise | Channel episodes, observations, actions and readiness evidence | Phase/channel tests under [`tests`](tests) and linked channel guides in the catalog | Fixture substrates do not prove real-time, device, transport, or provider behavior. |
| `suite`, `actions`, `capabilities`, `extensions`, `trinity` | Base | Catalog/manifest operations can be local; embedded commands and selected jobs inherit their code execution, credential, and network behavior. | None for local fixtures; child-job credentials otherwise | Suite/action/capability manifests and aggregate artifacts | Suite/action/capability tests in [`tests/test_config_and_facades.py`](tests/test_config_and_facades.py) | Metadata registration is not runtime plugin registration. Aggregate success is bounded by admitted child evidence. |
| `agent-learn`, `alk` | Base; aliases are installed from `pyproject.toml` | Command-specific. The installed `init --preset run` + `run` scaffold uses a scripted agent and `local_text`: credential-free, no model, external grader, or provider call. Other commands follow the [CLI execution profiles](docs/reference/cli.md#execution-profiles). | None for the named first-success scaffold; command-specific otherwise | JSON artifacts, reports, ledgers, or terminal output by command | The `run` case of [`test_init_preset_golden_path_offline`](tests/test_init_golden_paths.py); clean-install local simulation in [`sdk-smoke.yml`](.github/workflows/sdk-smoke.yml) | The offline scaffold proves the installed simulation path and artifact format only. It does not claim the other scaffold presets passed. `doctor` is an import/consolidation check, not live readiness. |
| `alk-harness-worker` | Base entry point, deployed by the hosting system | Internal hosted job process; reads job/source/secret references and can use hosted services and network | Hosting-system job identity and secret references | Hosted job events, artifacts, results and failure state | [`tests/harness/test_hosted_entrypoint.py`](tests/harness/test_hosted_entrypoint.py); release `sdk-build` imports `fi.simulate.hosted.child_entrypoint` and `fi.alk.harness` | Not a platform submission client or supported direct user command. Hosted deployment adds contracts not certified by a local import. |
| `alk-harness-sandbox` | Base entry point; hosted image/service dependencies | Internal HTTP sandbox service; executes admitted work inside the hosting boundary | Hosting-system sandbox/job credentials | Service responses, execution evidence and errors | [`tests/test_harness_sandbox_server.py`](tests/test_harness_sandbox_server.py), [`Dockerfile.sandbox`](Dockerfile.sandbox) | The entry point alone does not establish isolation, production identity, scheduling, or arbitrary-code safety. |

There is no `fi.alk.eval` module. Use `fi.alk.evals`. Modules named
`fi.alk.harness.hosted_*` remain internal unless `fi.alk.harness.__all__`
exports a name. The [gaps ledger](docs/reference/gaps-ledger.md) tracks missing
extension hooks without presenting internal modules as supported libraries.

Additional included infrastructure lives under [`fi.api`](src/fi/api),
[`fi.utils`](src/fi/utils), [`fi.cli`](src/fi/cli), and
[`fi.agentcc`](src/fi/agentcc.py). These support engine/API/authentication,
compatibility CLI and gateway behavior; they are not extra repositories to clone
or a replacement for the documented `fi.alk` workflow entry points.

## Harness and runtime source ownership

These are separate implementation components, not one universal plugin system:

| Concern | Source of truth |
| --- | --- |
| Source understanding and contract | [`understand.py`](src/fi/alk/harness/understand.py), [`contract.py`](src/fi/alk/harness/contract.py) |
| World/data creation and validation | [`build.py`](src/fi/alk/harness/build.py), [`world/tools.py`](src/fi/alk/harness/world/tools.py), [`world_ir.py`](src/fi/alk/harness/world_ir.py), [`generic_pipeline.py`](src/fi/alk/harness/generic_pipeline.py) |
| Scenario generation/proof | [`scenarios.py`](src/fi/alk/harness/scenarios.py), [`prove.py`](src/fi/alk/harness/prove.py) |
| Stage backend protocol/registration | [`backends/base.py`](src/fi/alk/harness/backends/base.py), [`backends/__init__.py`](src/fi/alk/harness/backends/__init__.py) |
| Reference pipeline execution | [`HarnessExecutor`](src/fi/alk/harness/executor.py), [`local_certified_runtime.py`](src/fi/alk/harness/local_certified_runtime.py) |
| General simulation runtime/plugins | [`SimulationRunner`](src/fi/simulate/runtime/runner.py), [`registry.py`](src/fi/simulate/registry.py), [`EnvironmentPlugin`](src/fi/simulate/environments/base.py) |
| Runtime/bundle lifecycle | [`runtime.py`](src/fi/alk/harness/runtime.py), [`bundle.py`](src/fi/alk/harness/bundle.py) (V1), [`bundle_v2.py`](src/fi/alk/harness/bundle_v2.py) (V2) |
| Results and grading | [`run/grade.py`](src/fi/alk/harness/run/grade.py), [`fi.alk.evals`](src/fi/alk/evals.py), [`fi.evals.metrics.agents`](src/fi/evals/metrics/agents) |

The public `fi.alk.harness` package is the set exported by its `__all__`. Types
such as `HarnessExecutor`, `RuntimeProvider`, `HarnessBackend`, and
`EnvironmentPlugin` live in their owning modules and are version-sensitive
extension contracts. Do not infer public exports from files in the directory.

`HarnessExecutor` still invokes ALK's pipeline. V1 Compose and V2 process bundles
have different consumers. A production or user-owned harness needs explicit
integration and conformance at the chosen boundary; these files do not prove
whole-executor portability. See the [practical seam matrix](docs/simulate/environment-authoring.md#supported-extension-seams).

## Dependencies and packaging

The wheel configuration in [`pyproject.toml`](pyproject.toml) is:

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/fi"]
```

The Python namespace above is included. Repository examples and `harness-ui`
are checkout resources, not wheel-installed commands/assets. The source archive
has its own inclusion list; do not infer wheel contents from it.

**Included code is different from a third-party dependency.** The project
currently declares provider SDKs, LiteLLM, Google ADK, GEPA, Optuna, Retell,
OpenTelemetry, Pydantic and other libraries as **base dependencies**, even when
some imports are lazy. Lazy loading does not make a declared dependency optional.

Declared extras include LiveKit, LangChain/LangGraph, Pipecat, MCP, A2A, NLI,
embeddings, feedback, notebooks, harness UI, harness stores and hosted tracing.
Check the actual extra before selecting it: `all` is not a synonym for every
extra listed in the file. `simulate`, `evaluation`, and `optimize` are empty
compatibility extras because those engines are already included.

The first-success echo/run recipes do not require live provider credentials or
containers. A minimal installation must still satisfy the declared base
requirements. Do not use `doctor` to infer that a model, service, driver or
external framework is ready for a live lane.

## TypeScript boundaries

To install the published npm package and score two replies locally, follow
[Evaluate responses in TypeScript](https://docs.futureagi.com/docs/sdk/agent-learning-kit/evaluate-responses-in-typescript).

[`typescript/agent-learning-kit/package.json`](typescript/agent-learning-kit/package.json)
declares `@future-agi/agent-learning-kit`, source version `0.2.0`, with exports:

- Root and `/evals`: evaluation SDK entry point.
- `/evals/local` and `/local`: local evaluation entry point.

The source contains evaluators, local metrics, templates, execution/manager,
protection/scanning, streaming and RAG/heuristic code. It is not a TypeScript
implementation of the Python source-authoring harness, `SimulationRunner`, or
all optimizer workflows. Build and test the workspace source as described
in the [package README](typescript/agent-learning-kit/README.md); inspect the
manifest and exports for the version you use.

## Contribution boundary

Use public `fi.alk` imports in new workflow examples where a facade exists;
import engine protocols directly when the documented seam requires them.
Keep new runtime implementation, tests and library docs together in this repo.
No separate `ai-evaluation`, `simulate-sdk`, or `agent-opt` checkout is needed
to consume the consolidated package. The public inventories are this file,
[`pyproject.toml`](pyproject.toml), source trees and [`tests`](tests), not an
out-of-repository inventory.
