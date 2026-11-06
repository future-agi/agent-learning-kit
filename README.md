<p align="center">
  <img src="https://raw.githubusercontent.com/future-agi/agent-learning-kit/main/docs/assets/futureagi-mark-email.png" alt="Future AGI" width="72" />
</p>

<h1 align="center">Agent Learning Kit</h1>

<p align="center">A common framework for creating simulation environments and evaluating AI agents.</p>

<p align="center">
  <a href="LICENSE">License</a> · <a href="docs/index.md">Docs</a> ·
  <a href="LIBRARIES.md">Library inventory</a> · <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="SECURITY.md">Security</a>
</p>

![Agent Learning lifecycle blueprint](docs/assets/hero-agent-blueprint.jpg)

Agent Learning Kit (ALK) brings environment creation, simulation, evaluation,
red teaming, and optimization into one repository. Start with deterministic
fixtures, inspect their evidence, then move to your own agent and environment.

The bundled **open-source reference harness** reads agent source, authors a
world and scenarios, and runs and grades supported targets. It is a usable
implementation of the framework, not a requirement to deploy that harness in
production. Production systems and users can integrate different execution
systems at the contracts they actually support. Today there are separate seams
for stage backends, simulation plugins, target adapters, and evidence; there is
**not** a universal replacement-executor or interchangeable-bundle contract.
See the [extension matrix](docs/simulate/environment-authoring.md#supported-extension-seams).

Three included engines support the loop:

- `simulate`: environments, targets, scenarios, runtime reports, and replay.
- `evals`: suite assertions, artifact/task scoring, local metrics, and opt-in
  model/platform evaluation.
- `optimize`: candidate search and evidence-driven improvement. Red teaming uses
  the simulation and evaluation engines rather than requiring a fourth engine.

## Install

Python **3.10 or newer**:

```bash
pip install agent-learning-kit
```

The installed CLI names are `agent-learn` and `alk` (aliases). `python -m
fi.alk.cli` also works. The wheel includes `fi.alk`, `fi.simulate`, `fi.evals`,
and `fi.opt`; **it does not install the repository's `examples/` or
`harness-ui/` directories**. No separate engine clones are required.

For the checked-in examples below, use a checkout and install it:

```bash
git clone https://github.com/future-agi/agent-learning-kit
cd agent-learning-kit
pip install -e .
```

Installation downloads dependencies; the offline claims below apply to execution
of the named fixtures after installation, not installation itself. Optional
integrations have extras, for example `agent-learning-kit[livekit]`,
`agent-learning-kit[langchain]`, or `agent-learning-kit[harness-ui]`.
The [library inventory](LIBRARIES.md#dependencies-and-packaging) distinguishes
these extras from base dependencies.

## First success: deterministic and credential-free

Run from the checkout root. Keep this attempt's artifacts and local ledger
together, and disable automatic account sync even if your shell has keys:

```bash
export AGENT_LEARNING_SYNC=local
export AGENT_LEARNING_LEDGER_PATH="$PWD/artifacts/first-success/ledger"
agent-learn eval examples/eval_suite.json --dry-run
agent-learn eval examples/eval_suite.json \
  --output artifacts/first-success/eval.json
```

This specific suite uses `echo`, one prompt, and a `contains` assertion. It does
not call a model, execute an external grader, or require credentials. On success
the CLI prints `wrote <path>` and returns zero. Inspect the **actual** result:

```bash
python -c "import json; p=json.load(open('artifacts/first-success/eval.json')); assert p['kind']=='agent-learning.eval.v1'; assert p['exit_code']==0; print(p['summary'])"
```

Then run the scripted simulation fixture:

```bash
AGENT_LEARNING_RUN_EXAMPLE_KEY=offline-demo-key \
  agent-learn run examples/run_manifest.json --no-eval \
  --output artifacts/first-success/run.json
python -c "import json; p=json.load(open('artifacts/first-success/run.json')); assert p['kind']=='agent-learning.run.v1'; assert p['exit_code']==0; print(p['summary'])"
```

That fixture's `required_env` value is a placeholder for wiring validation, not
a provider credential. `--no-eval` skips grading, not simulation. A completed run
is not by itself proof that an agent met a behavioral goal. Inspect transcripts,
tool calls and state before choosing assertions.

For an SDK-first version, run the [Spec + Runner echo example](docs/simulate/spec-and-runner.md):

```bash
python examples/sdk_spec_runner_quickstart.py artifacts/first-success/spec.json
```

It writes a compact example summary (`status`, `transcript`, registry names),
not the CLI's `agent-learning.run.v1` envelope.

**Only installed the package?** Use `agent-learn init demo --preset run` to
create your own scaffold; follow [Golden path: run](docs/quickstart/golden-path-run.md).
Do not refer to `examples/...` paths until you have the checkout.

**Import trouble?** `agent-learn doctor --output artifacts/doctor.json` checks
package imports and consolidation. It is not a provider-authentication,
container-readiness, or live-endpoint check. A failed doctor report should be
read, not replaced with a claimed passing result.

## Build your toolchain

| Step | Guide | Execution boundary |
| --- | --- | --- |
| Inspect and score evidence | [Evaluation guide](docs/eval/overview.md) · [Eval suites](docs/eval/eval-suites.md) | Echo and selected metrics are local; model/platform modes are not. |
| Create an environment from source | [Environment authoring](docs/simulate/environment-authoring.md) | Opt-in, model-backed; may provision services and execute generated/submitted code. |
| Use the reference harness | [Harness README](src/fi/alk/harness/README.md) · [Architecture](src/fi/alk/harness/ARCHITECTURE.md) | `auto` authors and runs; `author` authors without the final run, but is not offline. |
| Integrate a different execution system | [Supported extension seams](docs/simulate/environment-authoring.md#supported-extension-seams) | Stage sessions, runtime plugins and evidence have different contracts. |
| Author personas and scenarios | [Persona & Scenario Studio](docs/simulate/persona-scenario-studio.md) | Local typed authoring is distinct from platform generation. |
| Improve and replay | [Optimization](docs/optimize/optimize-any-agent.md) · [Regression lifecycle](docs/simulate/regression-lifecycle.md) | Choose the relevant local or credentialed lane explicitly. |
| Find included libraries | [Library inventory](LIBRARIES.md) · [Documentation index](docs/index.md) | Public facades, engine implementations, and internal modules are labeled separately. |

`AGENT_LEARNING_SYNC=local` controls telemetry delivery. It does **not** prevent
model, platform-evaluation, HTTP-hook, or target calls made by a workflow.
Do not use source authoring as an offline installation smoke test.

## TypeScript

The evaluation SDK source is in [`typescript/agent-learning-kit`](typescript/agent-learning-kit).
Its package manifest declares root, `/evals`, `/evals/local`, and `/local`
exports. Build/use the workspace source; this README does not assert npm
publication or Python harness/runtime parity. See [TypeScript boundaries](LIBRARIES.md#typescript-boundaries).

## Repository map

- [`src/fi/alk`](src/fi/alk): public facades and CLI, reference harness, studio,
  benchmarks, loss/practice, episodes and telemetry.
- [`src/fi/evals`](src/fi/evals): included evaluation engine.
- [`src/fi/simulate`](src/fi/simulate): included simulation engine and runtime registry.
- [`src/fi/opt`](src/fi/opt): included optimization engine.
- [`examples`](examples): checkout-only runnable examples and manifests.
- [`harness-ui`](harness-ui): checkout-only UI; its own setup is in its README.
- [`docs/index.md`](docs/index.md): guides and reference navigation.
- [`LIBRARIES.md`](LIBRARIES.md): contained libraries, dependencies and source ownership.
- [`DEVELOPMENT.md`](DEVELOPMENT.md): contribution and release boundaries.
- [`ROADMAP.md`](ROADMAP.md): public roadmap, not a list of shipped commands.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and [DEVELOPMENT.md](DEVELOPMENT.md)
for the implementation boundary. Source and packaging evidence live in this
repository: [`pyproject.toml`](pyproject.toml), the module trees above, and
[`tests`](tests).

## Project status

The package contains both credential-free fixtures and opt-in integrations.
A source checkout or a successful import is not evidence that every integration
has been exercised. Release checks and release proof are separate contributor
workflows; see [Release checks](docs/prove/release-check-in-your-ci.md).
Neither is needed for the first success above. Use results from the revision
and execution lane you actually tested rather than assuming all gates are green.

## Community

- [Contributing](CONTRIBUTING.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Security reporting](SECURITY.md)
- [License](LICENSE) and [notice](NOTICE)
