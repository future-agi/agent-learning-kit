# agent-learning-kit

`agent-learning-kit` is the unified Future AGI SDK for agent simulation,
evaluation, red teaming, and optimization.

The package gives users one key/config layer and one import namespace.
Simulation, evals, red teaming, and optimization live under that namespace and
ship in this package.

```python
from agent_learning import configure
from agent_learning import simulate, evals, redteam, optimize

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
agent-learn optimize-eval examples/eval_suite_optimization.json \
  --output artifacts/eval-optimization.json
agent-learn run examples/run_manifest.json --no-eval --output artifacts/run.json
agent-learn redteam examples/redteam_manifest.json --output artifacts/redteam.json
agent-learn optimize examples/optimization_manifest.json --output artifacts/optimization.json
agent-learn optimize examples/world_framework_memory_optimization.json \
  --output artifacts/world-framework-memory-optimization.json
agent-learn doctor
```

`agent-learn run`, `agent-learn eval`, `agent-learn redteam`,
`agent-learn optimize`, and `agent-learn optimize-eval` write Agent Learning Kit
artifact kinds
(`agent-learning.run.v1`, `agent-learning.eval.v1`,
`agent-learning.redteam.v1`, `agent-learning.optimization.v1`, and
`agent-learning.eval-optimization.v1`) plus optional JUnit, SARIF, and Markdown
outputs for CI.

This package now contains the actual `fi.simulate`, `fi.evals`, and `fi.opt`
engine code while keeping `agent_learning.*` as the public API.

The `world_framework_memory_optimization.json` example optimizes a
LangGraph-style world orchestration across framework trace, retrieval, memory
lineage, and multi-agent review evidence.
