# The reference harness

ALK is a common framework for creating simulation environments. This directory
contains its **open-source reference harness**: source understanding, world/data
construction, scenario authoring, supported target execution and evidence-based
grading. It is not a requirement that production systems or users deploy this
exact executor. Alternative integrations must satisfy the actual contracts they
use; whole-harness portability is not established by the backend registry.

- **[Start offline](../../../../README.md#first-success-deterministic-and-credential-free)**:
  deterministic echo evaluation and a scripted simulation, without source authoring.
- **[Author an environment](../../../../docs/simulate/environment-authoring.md)**:
  model-backed setup, commands, artifacts, trust boundaries and extension matrix.
- **[Evaluate results](../../../../docs/eval/overview.md)**: suites, task evidence,
  deterministic/judged outcomes, proof, loss and benchmark validation.
- **[Architecture](ARCHITECTURE.md)**: reference implementation components and
  supported hosted interfaces, not a claim about a production deployment.
- **[Web UI](../../../../harness-ui/README.md)**: checkout-only chat interface.

## Install and select a lane

Python 3.10+ and `pip install agent-learning-kit` install the Python code and
`agent-learn` / `alk` CLIs. A checkout is required for `examples/` and
`harness-ui/`. Do not check out an old feature branch to use these docs.
Declared optional integrations are listed in
[`pyproject.toml`](../../../../pyproject.toml); voice/store/UI requirements depend
on the lane. The echo recipe does not require LiveKit or Docker.

Source authoring is **not** the offline quickstart. It calls models and may
provision services, execute submitted/generated code, or contact target/eval
providers. Select the stage backend with `ALK_HARNESS` and a compatible model
with `ALK_HARNESS_MODEL`. Built-ins are `claude` (Claude Agent SDK, default) and
`vertex-gemini` (Google ADK/Vertex). Configure credentials for your chosen route;
there is no universal mandatory one-provider credential setup. Backend sources
and exact protocols are in [`backends`](backends).

`agent-learn doctor` checks imports and consolidation, not live credentials or
runtime readiness. `AGENT_LEARNING_SYNC=local` controls account telemetry only;
it does not block authoring/provider traffic.

## Choose a command

After reviewing the [authoring prerequisites](../../../../docs/simulate/environment-authoring.md#prepare-the-model-backed-lane),
run from the repository root or a working directory where you want artifacts.
Replace `../my-agent` with your source path.

```bash
# Model-backed: author AND execute supported scenarios, unattended.
agent-learn harness auto --path ../my-agent --name my_agent \
  --out artifacts/my-agent-auto --count 10

# Alternative: logical authoring without the final scenario run.
# Still model-backed and executable; NOT an offline preview.
agent-learn harness author --path ../my-agent --name my_agent \
  --out artifacts/my-agent-author --count 10
```

These are alternative attempts with fresh output directories, not a requirement
to run both. Default `--count` is 10. `auto` chooses a unique session directory
when `--out` is omitted; `author` requires `--out`. Non-hidden contents in an
existing destination are rejected.

For an inspect-and-correct workflow, use another directory:

```bash
agent-learn harness understand --name my_agent --path ../my-agent \
  --out artifacts/my-agent --once
agent-learn harness build --name my_agent --path ../my-agent \
  --out artifacts/my-agent --once
agent-learn harness scenarios --name my_agent --count 10 \
  --out artifacts/my-agent --once
```

Without `--once`, these stages remain interactive. Bare `agent-learn harness`
opens chat; `agent-learn harness --help` is the help path. `agent-harness` is a
parser label, not a declared installed binary.

`harness run` accepts `--target`, `--only`, `--model`, and `--quiet`.
The default `local` target is contract-only and rejects a submitted repository
implementation. A staged repository-backed chat run uses `--target repository`
and requires the recorded HTTP/WebSocket ingress plus its provisioned runtime.
See the [run/lifecycle recipe](../../../../docs/simulate/environment-authoring.md#run-and-manage-the-reference-environment)
for exact commands and V1 bundle limits. A source-backed agent is not recreated
from its prompt when an ingress or tool implementation is missing.

## What to inspect

| Output | Meaning |
| --- | --- |
| `job.json`, `session.json` where emitted | Requested attempt and source/session context, not proof of completion. |
| `contract.json` | Source-grounded tools, runtime, rules and amendments. Review it against the source. |
| World snapshot/state and bindings | Test-owned data, handlers or source-runtime bindings, simulator prompt and shared sub-goal catalogue. Format depends on the world/runtime lane. |
| `scenarios/<name>/` | Scenario instruction, reference steps, setup/readiness code and deterministic checks. |
| Bundle manifest | Versioned runtime/provenance/capabilities and hashes; V1 and V2 are different contracts. |
| `runs.json` or lane-specific results/events/receipts | Transcript, actions/state, checkpoints, judgments, crashes and other problems. Preserve the failure domain. |

The default auto/chat session root is `artifacts/sessions/<id>/`; explicit
`--out` overrides it. Keep attempts separate instead of deleting all sessions to
start over. Generated content may include private source or runtime data, so
review it before sharing.

World checks probe tool/data behavior, sequences and baseline state. Scenario
proof checks readiness, reference solvability and nonvacuity. These are
lane-dependent executable checks, not blanket safety or correctness claims:
data-free/deferred paths, assumed calls and relaxed schema/seed refusals require
inspection. The [authoring guide](../../../../docs/simulate/environment-authoring.md#trace-the-artifacts-and-validation-boundaries)
records these limitations explicitly.

## How grading works

[`run/grade.py`](run/grade.py) keeps deterministic state/call checkpoints distinct
from judged conduct. Models or platform evals can decide the latter from the
transcript, actions and final state. A fluent claim is not evidence of a tool
effect. Setup calls must not count as agent actions.

A world crash renders `VOID`. Broken checks and judge errors need their own
attribution; inspect `grading_error`, crashes and problems, not only `PASS`/`FAIL`.
No checkpoints is not a pass. See the [evaluation guide](../../../../docs/eval/overview.md#reference-harness-grading).

Generated Python handlers, setup/readiness functions and checks execute code.
Use isolated test resources and least-privilege credentials; a local folder or a
content hash is not a sandbox. There is no fixed cost/time promise: authoring,
simulators, targets, judges and retries can incur separate costs.

## Extension and deployment boundaries

- **Stage backend:** implement `HarnessBackend` / `HarnessSession`, register a
  loader in-process, then select its name. This replaces the conversation/tool
  loop, not the environment executor.
- **Simulation runtime plugin:** use `fi.simulate.registry` and its environment,
  endpoint or simulator entry points. This is separate from stage backends and
  from `fi.alk.extensions` metadata registration.
- **Target:** `run/targets.py` supports registered conversation targets with
  lifecycle and world-effect bindings.
- **Location/bundles:** `RuntimeProvider` and local Compose exist, but an arbitrary
  provider is not wired through the CLI/executor. V2 certified process runtime
  machinery is distinct from V1 Compose bundles.
- **Hosted interfaces:** `alk-harness-worker` and `alk-harness-sandbox` are declared
  entry points. Hosted source/authentication, process runtime, scheduling and
  evidence handling have additional contracts. Their presence does not prove
  deployment identity or third-party executor conformance.

Read the [supported seam matrix](../../../../docs/simulate/environment-authoring.md#supported-extension-seams)
before integrating another harness. Read [`ARCHITECTURE.md`](ARCHITECTURE.md) and
[`ENVIRONMENT_CONFORMANCE.md`](ENVIRONMENT_CONFORMANCE.md) for implementation detail.

## Troubleshooting

| Symptom | Check first |
| --- | --- |
| Missing `fi` or an engine import | Python environment/package installation; inspect doctor JSON. |
| Model fails immediately | Selected backend/model compatibility and that backend's SDK/authentication setup. |
| No contract/world | Earlier stage output and the exact `--out` directory. |
| Unsupported runtime/tool/ingress | Grounded contract and source implementation; do not invent a substitute. |
| Empty UI tabs | Active session and stage events; artifacts may not be saved yet. |
| Destination is not empty | Use a fresh auto/author directory, or the staged correction workflow. |
| Saved world but runtime fails | Read probes, schema/seed diagnostics, runtime certification and readiness evidence. |
| No visible effect after code change | Restart a long-lived UI/worker; do not assume hot reload. |

For source ownership and contained libraries, see [LIBRARIES.md](../../../../LIBRARIES.md).
