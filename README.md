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

Use Agent Learning Kit (ALK) to test how an agent responds to a scenario. A run
gives you the conversation and its evaluation as JSON. Start with the scripted
agent included in the Python package, then use the same libraries with your agent.

## Install

In an active Python 3.10 or newer environment, install the package:

```bash
python -m pip install agent-learning-kit
```

## First success: deterministic and credential-free

Run this example in your terminal. Installation downloads dependencies. After
installation, the example runs offline without Docker, a model key, or an account.

```bash
mkdir alk-quickstart
cd alk-quickstart
export LITELLM_LOCAL_MODEL_COST_MAP=True
export AGENT_LEARNING_SYNC=local
export AGENT_LEARNING_LEDGER_PATH="$PWD/artifacts/ledger"
agent-learn init . --preset run
agent-learn run manifests/run.json --output artifacts/run.json
python -c "import json; r=json.load(open('artifacts/run.json')); print(r['status'], r['summary']['case_count'])"
```

You should see `passed 1`. ALK ran one conversation and scored the scripted
reply against the expected outcome. Open `artifacts/run.json` to read it, or
[run the same scenario from Python](docs/quickstart/golden-path-run.md).
The settings use bundled model prices, keep account sync local and store the run history in this directory.

## Choose a library

| What you want to do | Where to start |
| --- | --- |
| Run a simulation with your own agent | [Simulation in Python](docs/simulate/spec-and-runner.md) with `fi.alk.simulate` |
| Check a response against an expectation | [Evaluation guide](docs/eval/overview.md) with `fi.alk.evals` |
| Build an environment for a scenario | [Environment authoring](docs/simulate/environment-authoring.md) covers a local Python plugin and model-backed authoring from source |
| Try changes to an agent and compare results | [Optimization](docs/optimize/optimize-any-agent.md) with `fi.alk.optimize` |
| Find another module or integration | [Library inventory](LIBRARIES.md) lists imports, credentials and examples |

The [documentation index](docs/index.md) also covers red teaming, replay and
regression testing. For hosted Environment jobs and results in the Future AGI
dashboard, see [Platform Environments](https://docs.futureagi.com/docs/environments).

## Working from a checkout

Clone the repository to run files under `examples/` or use `harness-ui/`. These
directories are available in source. The quickstart above ships in the wheel.

```bash
git clone https://github.com/future-agi/agent-learning-kit
cd agent-learning-kit
python -m pip install -e .
```

The [reference harness](src/fi/alk/harness/README.md) uses a model to read
agent source and generate an environment. See the [extension guide](docs/simulate/environment-authoring.md#supported-extension-seams)
to connect a stage backend or register a simulation plugin. To score responses in Node.js, follow
[Evaluate responses in TypeScript](https://docs.futureagi.com/docs/sdk/agent-learning-kit/evaluate-responses-in-typescript).
To contribute to the package, see the [TypeScript workspace](typescript/agent-learning-kit).

If imports fail, run `agent-learn doctor` in the environment where you installed
ALK. For TLS errors with openai 3 in a minimal image or behind a TLS-inspecting
proxy, install the appropriate system CA certificates or set `SSL_CERT_FILE` to
your CA bundle.

## Community

- [Contributing](CONTRIBUTING.md) and [development setup](DEVELOPMENT.md)
- [Code of conduct](CODE_OF_CONDUCT.md)
- [Security reporting](SECURITY.md)
- [License](LICENSE) and [notice](NOTICE)
