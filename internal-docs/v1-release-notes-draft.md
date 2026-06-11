# Agent Learning Kit v1.0.0-rc.1 — Release Notes (draft)

> Internal draft, ready to paste as the GitHub Release body when the owner
> publishes. Kept in `internal-docs/` so the `package_distribution_hygiene`
> gate keeps it out of the sdist automatically.

---

Agent Learning Kit is the local-first SDK and CLI for testing, simulating,
red-teaming, and optimizing AI agents. It brings the three core Future AGI
engines — `simulate`, `evals`, and `optimize` — into one public developer
surface: three engines, four workflows (red-teaming rides on the `simulate` and
`evals` engines rather than being a fourth engine). Every capability claim is
backed by an executable release gate; nothing in this release depends on a
hosted service.

## The loop

1. Simulate an agent or framework workflow.
2. Evaluate the behavior and runtime evidence.
3. Optimize the weak layer.
4. Promote the result into a replayable artifact.
5. Prove release readiness with local gates.

## Framework coverage

- Framework adapter probes (probe-promoted coverage) for LangChain, LangGraph,
  LlamaIndex, AutoGen, CrewAI, LiveKit, Pipecat, Browser Use, MCP, A2A, and
  custom orchestration objects.
- Runtime-simulated coverage for PydanticAI (multi-framework runtime
  simulation) and OpenAI Agents (handoff-transcript promotion).
- OpenEnv/Gymnasium shapes are compatibility inputs, not the product center;
  the bar is the executable `environment_10x_robustness` release gate.

## Proof

- Cut commit: the commit tagged `v1.0.0-rc.1`.
- Proof artifact: `agent-learning.release-proof.v1`, status `passed`,
  `summary.ready=true`, `full_proof=true`, 7/7 required checks
  (`release_check`, `ruff`, `pytest`, `build`, `typescript_build`,
  `typescript_test`, `git_diff_check`).
- 66 executable release gates, including the new
  `package_distribution_hygiene` gate (the sdist ships only source, tests,
  examples, docs, and standard release files — verified by building and
  inspecting real distributions on every release-check).
- Python: 307 tests passed. TypeScript: 646 tests passed.
- Package labels: Python `agent-learning-kit==0.1.0`, TypeScript
  `@future-agi/agent-learning-kit==0.2.0` (the tag, not the semver, names the
  product milestone).

## Install

PyPI and npm publishing land at the v1 launch. Today, install from source:

```bash
git clone https://github.com/future-agi/agent-learning-kit
cd agent-learning-kit
pip install -e .
```

(or `uv sync` for contributors)

At launch:

```bash
pip install agent-learning-kit
```

TypeScript evaluation package (npm at launch; today build from
`typescript/agent-learning-kit`):

```bash
pnpm add @future-agi/agent-learning-kit
```

---

PyPI/npm publishing and the final `v1.0.0` tag are owner actions.
