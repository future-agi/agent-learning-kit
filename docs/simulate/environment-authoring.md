---
kind: agent-learning.docs-page.v1
track: simulate
objective: capability
stage: simulate
backing:
  - examples/sdk_spec_runner_quickstart.py
artifact_kinds: []
commands:
  - python examples/sdk_spec_runner_quickstart.py artifacts/spec-and-runner.json
postcondition: python -c "import json; p=json.load(open('artifacts/spec-and-runner.json')); assert 'COMPLETED' in p['status']; print('ok')"
claims: []
doctor_checks:
  - missing_engine_modules
opt_in_lane: true
---

# Environment authoring and extension contracts

ALK is a common framework for creating simulation environments. Its bundled
harness is an OSS reference implementation for source understanding, world/data
construction, scenario authoring and execution. Production systems and users may
use different execution systems, but only through a compatible seam; swapping a
stage backend is not replacing the whole executor.

**Start offline first:** [README quickstart](../../README.md#first-success-deterministic-and-credential-free)
and [Spec + Runner](spec-and-runner.md). The frontmatter's echo twin exercises
that runtime path only. It does **not** certify source authoring, Docker,
credentials, hosted execution or an independent harness. The source-authoring
commands below are an **opt-in model-backed lane**.

## Choose the right entry point

| You have | Start with | What it establishes |
| --- | --- | --- |
| A callable agent and explicit scenarios | `SimulationSpec` + `SimulationRunner` | A runtime report for your registered environment/target; see the echo twin. |
| A repository to understand and test | `agent-learn harness auto --path SOURCE` | Reference pipeline: understand, build, scenarios, execute and retain evidence. |
| A repository to author without the final scenario run | `agent-learn harness author --path SOURCE --out OUTPUT` | Logical authoring output; not proof that a runtime is ready. |
| Existing task/trajectory evidence from another system | `fi.alk.evals` artifact/task functions | Scoring of the supplied evidence, not execution or compatibility certification. |

`auto` and `author` accept `--name`, `--kind`, and `--count` (default 10).
Use a fresh `--out` directory: existing non-hidden contents are rejected.
`author` defers source provisioning and the final run; it still calls models,
writes generated code, and can execute authoring checks. **It is not an offline,
no-spend, or side-effect-free preview.**

There is no installed `agent-harness` binary in `pyproject.toml`; that name is an
internal parser label. Use `agent-learn harness`, `alk harness`, or
`python -m fi.alk.harness`. Bare `harness` opens interactive chat; use `--help`
when you want help rather than a conversation.

## Prepare the model-backed lane

1. Install the Python package. Use a checkout for examples and the web UI;
   [`harness-ui/README.md`](../../harness-ui/README.md) owns UI setup.
2. Choose a backend and its authentication route before running authoring.
   `ALK_HARNESS` selects the stage backend; `ALK_HARNESS_MODEL` selects a model it
   can drive. Built-ins are `claude` (default; Claude Agent SDK) and
   `vertex-gemini` (Google ADK/Vertex). These are implementation choices, not
   requirements on every consumer of ALK. See the [backend sources](../../src/fi/alk/harness/backends).
   Vertex resolves `GOOGLE_CLOUD_PROJECT` and application credentials;
   `ALK_VERTEX_LOCATION` defaults to `global`. Claude setup depends on its SDK
   authentication route, not a universal mandatory Vertex key.
3. Review source inputs, dependency installation, services, network destinations,
   budgets, and permissions. Use test-owned data and least-privilege credentials,
   never production databases. The local auto path can load missing connection
   variables from the submitted repository's environment files.
4. Provision only in an isolated execution environment. Docker/Compose and store
   drivers are lane-dependent requirements, not requirements of the echo fixture.
   `doctor` checks imports/consolidation, not these live prerequisites.

`AGENT_LEARNING_SYNC=local` prevents automatic account telemetry sync; it is not a
network sandbox and does not turn authoring, judges or live targets into local
fixtures. Never commit resolved credentials or generated artifacts containing
private source, conversations, identifiers or recordings without reviewing them.

## Author in inspectable stages

After choosing credentials and an approved execution environment, the staged
route lets you inspect one output before requesting the next. Run these from a
checkout or another working directory where `artifacts/` is appropriate; replace
`../my-agent` with your source directory.

```bash
agent-learn harness understand --name my_agent --path ../my-agent \
  --out artifacts/my-agent --once
agent-learn harness build --name my_agent --path ../my-agent \
  --out artifacts/my-agent --once
agent-learn harness scenarios --name my_agent --count 10 \
  --out artifacts/my-agent --once
```

Without `--once`, these stages stay interactive. `scenarios` without `--count`
uses the existing scenario count. `build` can start source-declared services;
it is not just a JSON generator.

For an unattended end-to-end attempt use a **different, empty** directory:

```bash
agent-learn harness auto --path ../my-agent --name my_agent \
  --out artifacts/my-agent-auto --count 10
```

Do not infer success from a folder's existence. Read stage events and diagnostics,
then inspect the artifacts below. A source-backed agent's implementation remains
authoritative: missing tool/service behavior must not be silently replaced with
an invented implementation.

## Trace the artifacts and validation boundaries

| Stage | Inputs and outputs to inspect | Implementation |
| --- | --- | --- |
| Understand | Source → `contract.json`: tools/arguments, constraints, modality, runtime, source evidence and amendments. Check fidelity to the submitted code. | [`understand.py`](../../src/fi/alk/harness/understand.py), [`contract.py`](../../src/fi/alk/harness/contract.py) |
| Build world/data | Contract → world snapshot, state, handlers/bindings, simulator prompt and `sub_goals.json`. Examine probe failures, sequence checks, world checks and clean baseline state. | [`build.py`](../../src/fi/alk/harness/build.py), [`world/tools.py`](../../src/fi/alk/harness/world/tools.py), [`world/snapshot.py`](../../src/fi/alk/harness/world/snapshot.py) |
| Typed data pipeline | `SourceModel` + `WorldIR` → validation/compile diagnostics and candidate artifacts. Fingerprints bind rows to a source schema; validation checks types, required values and references. This is a separate internal pipeline, not a CLI promise that every build emits identical IR artifacts. | [`world_ir.py`](../../src/fi/alk/harness/world_ir.py), [`generic_pipeline.py`](../../src/fi/alk/harness/generic_pipeline.py) |
| Scenarios | Contract + world → `scenarios/<name>/scenario.json`, `setup.py`, `ready.py`, deterministic `checks/` and reference steps. Keep proof details, not just a count. | [`scenarios.py`](../../src/fi/alk/harness/scenarios.py), [`scenario_tools.py`](../../src/fi/alk/harness/scenario_tools.py), [`prove.py`](../../src/fi/alk/harness/prove.py) |
| Bundle/runtime | Runtime description, provenance, capabilities, readiness and content hashes. Check the bundle version and the consuming runtime before reuse. | [`bundle.py`](../../src/fi/alk/harness/bundle.py), [`bundle_v2.py`](../../src/fi/alk/harness/bundle_v2.py) |
| Execute/evaluate | Job/session metadata, progress, transcripts, calls/state, recordings when present, results and failure domains. A stage run commonly writes `runs.json`; certified runtime lanes have additional receipts/events. | [`executor.py`](../../src/fi/alk/harness/executor.py), [`local_certified_runtime.py`](../../src/fi/alk/harness/local_certified_runtime.py), [`run/grade.py`](../../src/fi/alk/harness/run/grade.py) |

World checks and tool probes are executable validations, not a security review.
Current `save_world` checks have lane-specific exemptions for data-free,
external-runtime and runtime-only worlds. Some repeated schema/seed refusals can
be released by `_still_refusing`; do not equate “saved” with complete source or
data conformance. Inspect diagnostics and require runtime evidence for your lane.

Scenario proof distinguishes **ready**, **reference-solvable**, and
**nonvacuous** checks. Broken checks, weak checks and assumed/unbound reference
steps require attention. Deferred-runtime and judged-only cases have less proof
than an observed stateful tool execution. See [evaluation semantics](../eval/overview.md#reference-harness-grading).

`setup.py`, `ready.py`, handlers and checks are executable Python. Validation may
run them and invoke tools/services. Hashes prove content identity, not safety;
local execution is not isolation.

## Run and manage the reference environment

For a staged repository-backed **chat** environment with the required HTTP or
JSON WebSocket ingress and a provisioned runtime:

```bash
agent-learn harness run --name my_agent --out artifacts/my-agent \
  --target repository --quiet
```

`--only NAME [NAME ...]` filters scenarios and `--model` selects the run model
where supported. The default `--target local` is for contract-only agents; it
rejects repository-backed implementations rather than reconstructing them from
prompts. Target registration is in [`run/targets.py`](../../src/fi/alk/harness/run/targets.py).
Voice/provider execution has its own supported paths; the command above is not
a generic voice recipe. `auto` selects execution from the source contract.

For a source-provisioned environment recorded in that session:

```bash
agent-learn harness environment status --out artifacts/my-agent
agent-learn harness environment reset --out artifacts/my-agent
agent-learn harness environment down --out artifacts/my-agent
```

`status` loads recorded environment metadata; it is not a fresh readiness probe.
`reset` mutates test state; `down` removes the recorded runtime and test volumes.
To start again use `environment up --path ../my-agent --out artifacts/my-agent`,
or `up --bundle BUNDLE_DIRECTORY --out artifacts/my-agent` for a compatible V1
bundle with a source snapshot. This command uses the V1 loader; it is **not** a
V2 process-bundle launcher.

## Supported extension seams

| What you want to change | Actual seam | Scope and limitation |
| --- | --- | --- |
| Stage conversation/tool loop | `HarnessBackend.create(SessionSpec)` → `HarnessSession`; `backends.register(name, loader)`; select with `ALK_HARNESS` | Real in-process registration, not automatic package entry-point discovery. Retains ALK stages/tools and executor. Built-ins: `claude`, `vertex-gemini`; no other built-in is implied. |
| Simulation environment | `fi.simulate.registry.register_environment` or entry points `fi.simulate.environments` | Runnable plugin consumed by `SimulationRunner`; not a replacement for `HarnessExecutor`. |
| Simulation target or simulator policy | `register_endpoint` / `register_simulator`; entry points `fi.simulate.endpoints` / `fi.simulate.simulators` | Must implement the corresponding runtime profile/adapter contract, not the stage-session protocol. |
| Harness conversation target | `run.targets.register_target(key, factory)` | Implements `Target.open/say/close/spent_usd` and binds effects to the world. Separate from the simulation endpoint registry. |
| Catalog/Studio metadata | `fi.alk.extensions.register_environment` | Descriptive registration only; does not install a runnable environment factory. |
| Execution location | `RuntimeProvider.provision/reset/healthy/close` in `runtime.py` | Protocol and local Compose adapter exist. CLI build calls the provisioner directly; no arbitrary-provider selector is wired through the executor. |
| Entire harness/executor | Integration around explicit artifacts and evidence | No independent executor conformance example or universal drop-in switch is established here. `HarnessExecutor` calls ALK's `_auto`. |
| Bundle interchange | V1 `EnvironmentBundle`; V2 `EnvironmentBundleV2` | Different schemas/loaders and Compose/process contracts. V2 local certified and hosted machinery does not establish interchange with V1 or arbitrary third-party runners. |
| Evaluation outside ALK execution | Normalize task/report evidence via `fi.alk.evals` | You own faithful conversion and sufficient evidence. A score is not proof of runtime portability. |

### Implementing a stage backend

Read [`backends/base.py`](../../src/fi/alk/harness/backends/base.py) before writing
an adapter. A backend supplies `name`, `default_model`, `can_drive(model)` and
`create(spec)`. Its session supplies async `start`, `stop`, `send`, `interrupt`,
`resume`, plus an async `replies()` iterator ending with `StageDone`.

Preserve `SessionSpec` tool grants, model/turn budget, working directory,
permissions and worker requirements. Translate replies into `SessionOpened`,
`ModelReply` (`Say`/`Call` parts), `ToolReturned` and `StageDone`. Unsupported
models/tools must fail explicitly, not disappear from the evidence. Register
your loader **in the process that will execute the stages**, before resolution;
setting an environment variable alone cannot import your implementation.

### Implementing a simulation plugin

[`EnvironmentPlugin`](../../src/fi/simulate/environments/base.py) exposes a
manifest and `async run(spec, *, target, **kwargs)`. Currently it returns the
legacy `fi.simulate.simulation.models.TestReport`; the runner converts that into
`SimulationReport`. Follow the full callback signature in the protocol.

For a concrete credential-free example, read
[`test_runner_is_world_agnostic_over_a_third_party_environment`](../../tests/runtime/test_registry.py).
It registers a dummy plugin, passes a sentinel target, returns one legacy test
case, and asserts a completed runtime report. It proves **that seam**, not
whole-harness compatibility. Installed plugin entry points import executable
Python; load only trusted packages.

## Next steps

- [Evaluation guide](../eval/overview.md): evidence requirements and failure semantics.
- [Studio](persona-scenario-studio.md): persona/scenario construction and calibration.
- [Library inventory](../../LIBRARIES.md): public versus internal surfaces.
- [Reference architecture](../../src/fi/alk/harness/ARCHITECTURE.md): implemented
  local/hosted boundaries, without prescribing a production deployment.
