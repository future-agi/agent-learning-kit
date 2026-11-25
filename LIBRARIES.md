# Library inventory

ALK is one Python distribution containing public workflow facades and the
simulation, evaluation and optimization engines. This is the source map for
using those libraries **without separate engine clones**. Start with the
[README](README.md) and [docs index](docs/index.md).

“Public facade” below means the documented entry point, not a promise that every
helper in its directory is stable. Underscore-prefixed modules and harness
implementation internals should be treated as internal/version-sensitive.
Pin and test any integration that imports them directly.

## Workflow facades and contained libraries

| Surface | Responsibility and boundary | Guide / source |
| --- | --- | --- |
| `fi.alk` / `configure` | Public configuration and lazy workflow imports. Keys do not make every operation platform-backed. | [Configuration](docs/reference/configure.md), [`__init__.py`](src/fi/alk/__init__.py), [`config.py`](src/fi/alk/config.py) |
| `fi.alk.simulate` | Public simulation/manifests, runtime specs/reports, target and environment integration. | [Spec + Runner](docs/simulate/spec-and-runner.md), [worlds/hooks](docs/simulate/worlds-and-hooks.md), [`simulate.py`](src/fi/alk/simulate.py) |
| `fi.simulate` | Included engine: scenarios/personas, environments, wrappers, adapters, runtime registry, manifests, suites, results and reports. Runtime plugins return legacy `TestReport`; `SimulationRunner` converts it. | [Extension contracts](docs/simulate/environment-authoring.md#supported-extension-seams), [`src/fi/simulate`](src/fi/simulate) |
| `fi.alk.evals` | Public suite/artifact/task evaluation facade, config builders, engine exports and HTTP hooks. | [Evaluation guide](docs/eval/overview.md), [`evals.py`](src/fi/alk/evals.py) |
| `fi.evals` | Included evaluation engine: unified local/Turing/LLM evaluation, metrics, agent-report scoring, templates, guardrails, streaming, feedback and OTel. Not all modes are offline. | [Evaluation library map](docs/eval/overview.md#other-included-evaluation-libraries), [`src/fi/evals`](src/fi/evals) |
| `fi.alk.optimize` | Public optimization manifests, candidate/evidence workflows, governance and backend routing. | [Optimize an agent](docs/optimize/optimize-any-agent.md), [lifecycle](docs/optimize/optimization-lifecycle.md), [`optimize.py`](src/fi/alk/optimize.py) |
| `fi.opt` | Included optimization engine: base classes, agent optimizers, mutation/evidence types, simulation integrations, deployment/observability utilities. Provider-backed algorithms can spend money. | [Optimizer portfolio](docs/optimize/optimizer-portfolio.md), [`src/fi/opt`](src/fi/opt) |
| `fi.alk.redteam` | Public campaign/corpus/adaptive evaluation workflows over simulate/evals, not another external engine. | [Red-team guide](docs/redteam/red-team-anything.md), [`redteam.py`](src/fi/alk/redteam.py) |
| `fi.alk.harness` | Bundled OSS reference harness: source understanding, world/data/scenario authoring, supported execution and grading. CLI is the user entry point; implementation protocols are version-sensitive. | [Harness README](src/fi/alk/harness/README.md), [authoring and seams](docs/simulate/environment-authoring.md), [`src/fi/alk/harness`](src/fi/alk/harness) |
| `fi.alk.studio` | Public persona/scenario facade; create/load/save/validate/calibrate, coverage, vendor import and platform generation/pull. Shares engine classes, not a duplicate persona format. | [Studio](docs/simulate/persona-scenario-studio.md), [simulation contract](docs/prove/simulation-contract.md), [`studio/__init__.py`](src/fi/alk/studio/__init__.py) |
| `fi.alk.bench` | Task/Verifier benchmark surface; push, artifact input and pull/RL control modes, coding and voice validators. Subprocess/Docker grading executes code. | [Benchmark overview](docs/eval/benchmark-overview.md), [sandbox boundary](docs/eval/benchmark-sandboxes.md), [`bench`](src/fi/alk/bench) |
| `fi.alk.loss` | Versioned objective compilation and loss reports; guarded objectives, verdict/evidence admissibility and exclusions. | [Loss semantics](docs/eval/overview.md#loss-practice-and-benchmark-validators), [`loss.py`](src/fi/alk/loss.py) |
| `fi.alk.practice` / `fi.alk.improve` | Practice execution, budget/schedule/report surfaces and improvement workflows. Review the selected execution lane; internal operators are not separate public APIs. | [Practice loop](docs/prove/practice-loop.md), [`practice`](src/fi/alk/practice), [`improve.py`](src/fi/alk/improve.py) |
| `fi.alk.tasks` / `fi.alk.rewardhack` | Task contracts and objective/anchor integrity checks used by improvement/benchmark workflows. Advanced, version-sensitive surfaces. | [`tasks.py`](src/fi/alk/tasks.py), [`rewardhack.py`](src/fi/alk/rewardhack.py), [benchmark authoring](docs/eval/benchmark-write-a-suite.md) |
| `fi.alk.episode` | Typed voice episode/event record, provider converters, JSON serialization, schema export and conformance. Records preserve coverage/absence; conversion is not a new execution harness. | [`episode/__init__.py`](src/fi/alk/episode/__init__.py), [`episode/models.py`](src/fi/alk/episode/models.py), [voice benchmark](docs/eval/benchmark-voice.md) |
| `fi.alk.telemetry` | Local run ledger, identity/provenance and opt-in account sync. Account delivery is separate from evaluation provider calls. | [Run ledger](docs/prove/run-ledger.md), [account sync](docs/prove/account-sync.md), [`telemetry`](src/fi/alk/telemetry) |
| `fi.alk.live` / `fi.alk.providers` | Opt-in framework/provider execution lanes and provider integration code. Distinct from deterministic fixture evidence; internal adapters are not universal backend support. | [Framework guides](docs/cookbooks/index.md#by-framework), [hosted targets](docs/simulate/provider-hosted-targets.md), [`live`](src/fi/alk/live), [`providers`](src/fi/alk/providers) |
| `voice_loop`, `image_loop`, `cua_loop`, `image_perturb` | Channel-specific loop/perturbation helpers. Deterministic fixture substrates do not establish real-device/provider coverage. | [Voice](docs/simulate/voice-loopback.md), [image](docs/simulate/image-loop.md), [CUA](docs/simulate/cua-loop.md), [`src/fi/alk`](src/fi/alk) |
| `suite`, `actions`, `capabilities`, `extensions`, `trinity` | Workflow composition, artifact actions/catalogs, descriptive extension metadata and release checks. `extensions.register_environment` is not the runtime plugin registry. | [Suite](docs/prove/trinity-suite.md), [actions](docs/prove/actions.md), [capabilities](docs/prove/capabilities.md), [seams](docs/simulate/environment-authoring.md#supported-extension-seams), [`src/fi/alk`](src/fi/alk) |
| CLI | `agent-learn` and `alk` point to the same main function. Worker/sandbox entry points are separate hosted interfaces. | [CLI reference](docs/reference/cli.md), [`cli.py`](src/fi/alk/cli.py), [`pyproject.toml`](pyproject.toml) |

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

[`typescript/agent-learning-kit/package.json`](typescript/agent-learning-kit/package.json)
declares `@future-agi/agent-learning-kit`, source version `0.2.0`, with exports:

- Root and `/evals`: evaluation SDK entry point.
- `/evals/local` and `/local`: local evaluation entry point.

The source contains evaluators, local metrics, templates, execution/manager,
protection/scanning, streaming and RAG/heuristic code. It is not a TypeScript
implementation of the Python source-authoring harness, `SimulationRunner`, or
all optimizer workflows. A package name/publish configuration is not evidence
of npm availability. Build and test the workspace source as described in the
[package README](typescript/agent-learning-kit/README.md); inspect the manifest
and exports for the version you use.

## Contribution boundary

Use public `fi.alk` imports in new workflow examples where a facade exists;
import engine protocols directly when the documented seam requires them.
Keep new runtime implementation, tests and library docs together in this repo.
No separate `ai-evaluation`, `simulate-sdk`, or `agent-opt` checkout is needed
to consume the consolidated package. The public inventories are this file,
[`pyproject.toml`](pyproject.toml), source trees and [`tests`](tests), not an
out-of-repository inventory.
