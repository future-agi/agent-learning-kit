<p align="center">
  <img src="docs/assets/futureagi-mark-email.png" alt="Future AGI" width="72" />
</p>

<h1 align="center">Agent Learning Kit</h1>

<p align="center">
  Local-first testing, simulation, red teaming, and optimization for AI agents.
</p>

<p align="center">
  <a href="LICENSE">Apache-2.0</a>
  ·
  <a href="CONTRIBUTING.md">Contributing</a>
  ·
  <a href="SECURITY.md">Security</a>
  ·
  <a href="V1_RELEASE_ROADMAP.md">V1 roadmap</a>
  ·
  <a href="LIBRARIES.md">Library inventory</a>
</p>

![Agent Learning lifecycle blueprint](docs/assets/hero-agent-blueprint.jpg)

Agent Learning Kit is the local-first SDK and CLI for testing, simulating,
red-teaming, and optimizing AI agents.

It brings the three core Future AGI engines into one public developer surface:

- `simulate`: run local worlds, tasks, framework-shaped adapters, replays, and
  regression artifacts.
- `evals`: evaluate prompts, task outputs, runtime contracts, traces, memory,
  retrieval, safety, and robustness evidence.
- `optimize`: search over prompts, agents, framework adapters, worlds,
  multi-agent interactions, memory layers, workflows, and red-team scenarios.

Use it when you want one reproducible loop:

1. Simulate an agent or framework workflow.
2. Evaluate the behavior and runtime evidence.
3. Optimize the weak layer.
4. Promote the result into a replayable artifact.
5. Prove release readiness with local gates.

OpenEnv/Gymnasium remain compatibility input shapes, not product ownership.
They are compatibility inputs, not the product center. Agent Learning Kit is the
primary runtime and release contract.
OpenEnv/Gymnasium-shaped traces remain compatibility evidence inside that bar.

## Install

```bash
pip install agent-learning-kit
```

Optional Python extras:

```bash
pip install "agent-learning-kit[livekit]"
pip install "agent-learning-kit[nli]"
pip install "agent-learning-kit[all]"
```

TypeScript evaluation package:

```bash
pnpm add @future-agi/agent-learning-kit
```

## Quickstart

Configure once:

```python
from agent_learning import configure
from agent_learning import evals, optimize, redteam, simulate, suite

configure(api_key="...")
```

Run the local doctor:

```bash
agent-learn doctor
```

Evaluate a suite:

```bash
agent-learn eval examples/eval_suite.json \
  --output artifacts/eval.json
```

Simulate a run manifest:

```bash
agent-learn run examples/run_manifest.json \
  --no-eval \
  --output artifacts/run.json
```

Optimize an agent workflow:

```bash
agent-learn optimize examples/optimization_manifest.json \
  --output artifacts/optimization.json
```

Run a red-team campaign:

```bash
agent-learn redteam examples/redteam_manifest.json \
  --output artifacts/redteam.json
```

Cut local release proof:

```bash
agent-learn release-check --project-root .
agent-learn release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof.json \
  --quiet
```

## TypeScript

```typescript
import { Evaluator } from "@future-agi/agent-learning-kit";
import { LocalEvaluator } from "@future-agi/agent-learning-kit/evals/local";
```

## What You Can Build

- Prompt and response evaluations.
- Local task and world simulations.
- Framework adapter probes for LangChain, LangGraph, OpenAI Agents, AutoGen,
  CrewAI, PydanticAI, LiveKit, Pipecat, Browser Use, MCP, A2A, and custom
  orchestration objects.
- Runtime-contract and trace-quality checks.
- Multi-agent coordination and handoff tests.
- Retrieval and memory quality checks.
- Voice, realtime, browser/CUA, workflow, lifecycle, and protocol traces.
- Red-team corpus, campaign, adaptive-loop, and persistent-state checks.
- Optimizer governance, candidate lineage, rollback, and release proof.

## Why It Exists

Most agent stacks split testing, simulation, optimization, and safety review
across separate tools. Agent Learning Kit keeps those steps in one artifact
model so a developer can inspect what happened, score it, improve it, and replay
it in CI.

The public SDK is `agent-learning-kit`, the Python namespace is
`agent_learning`, the CLI is `agent-learn`, and the TypeScript package is
`@future-agi/agent-learning-kit`.

The active `ai-evaluation` code is included here under `src/fi/evals`, with its
TypeScript SDK source under `typescript/agent-learning-kit/src`. The
`simulate-sdk` and `agent-opt` engine code is included under `src/fi/simulate`
and `src/fi/opt`. See [LIBRARIES.md](LIBRARIES.md) for the complete source map.

## Repository Map

- [`examples/`](examples): runnable cookbooks and manifests.
- [`src/agent_learning`](src/agent_learning): public Python SDK facade and CLI.
- [`src/fi/evals`](src/fi/evals): active `ai-evaluation` engine code.
- [`src/fi/simulate`](src/fi/simulate): migrated `simulate-sdk` engine code.
- [`src/fi/opt`](src/fi/opt): migrated `agent-opt` engine code.
- [`typescript/agent-learning-kit`](typescript/agent-learning-kit): public
  TypeScript package, including the active evaluation SDK source.
- [`LIBRARIES.md`](LIBRARIES.md): source map for the consolidated engines.
- [`V1_RELEASE_ROADMAP.md`](V1_RELEASE_ROADMAP.md): executable v1 gate map.
- [`internal-docs/`](internal-docs): handover, research, and release notes.
- [`CONTRIBUTING.md`](CONTRIBUTING.md): local development and PR workflow.
- [`SECURITY.md`](SECURITY.md): vulnerability reporting policy.
- [`LICENSE`](LICENSE): Apache-2.0 license.
- [`NOTICE`](NOTICE): Apache notice metadata.

## Development

New public SDK development belongs here. See [DEVELOPMENT.md](DEVELOPMENT.md)
for the boundary between this package and the backing engine repos.

```bash
uv sync
uv run ruff check .
uv run pytest -q
uv run python -m build
pnpm --dir typescript --filter @future-agi/agent-learning-kit build
pnpm --dir typescript --filter @future-agi/agent-learning-kit test -- --runInBand
```

For the heavier release cut, run `agent-learn release-proof --project-root .`.
It emits `agent-learning.release-proof.v1` with command evidence for the full
local proof stack.

Before a release:

```bash
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof.json \
  --quiet
```

`release-proof` includes release-check, full-repo ruff, pytest, Python package
build, TypeScript package build/test, and `git diff --check`. Use
`--only <check>` for partial proof during development or `--dry-run` to emit the
exact command plan without executing commands.

## Project Status

The v1 release gate is local-first and executable. It covers SDK consolidation,
promptfoo-style CLI usage, native optimizer evidence, docs/examples, schema
kinds, packaging metadata, red-team corpus/campaign coverage, Future AGI
UI/action/report artifacts, framework/provider compatibility, environment
robustness, regression replay, and release proof.

Current package labels may intentionally differ from the v1 release tag. See
[`internal-docs/v1-release-candidate-notes.md`](internal-docs/v1-release-candidate-notes.md)
before publishing.

## Community

- Contributions: [CONTRIBUTING.md](CONTRIBUTING.md)
- Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
- Security reports: [SECURITY.md](SECURITY.md)
- License: [Apache-2.0](LICENSE)

## Deep Dive

The detailed CLI and SDK cookbook material lives in
[internal-docs/agent-learning-kit-readme-deep-dive.md](internal-docs/agent-learning-kit-readme-deep-dive.md).
Keep this README focused on public onboarding, install, quickstart, release
proof, and contribution paths.
