# agent-learning-kit

`agent-learning-kit` is the unified Future AGI SDK for agent simulation,
evaluation, red teaming, and optimization.

The package gives users one key/config layer and one import namespace. Simulation,
evals, red teaming, and optimization remain separate modules under that namespace
so teams can install only the pieces they need.

```python
from agent_learning import configure
from agent_learning import simulate, evals, optimize

configure(api_key="...")
```

Install only the pieces you need:

```bash
pip install agent-learning-kit[simulate]
pip install agent-learning-kit[evaluation]
pip install agent-learning-kit[optimize]
pip install agent-learning-kit[trinity]
```

`agent-learning-kit` is the public SDK. The lower-level packages are backing
engines for now; public docs and automation should use `agent_learning.*` and
`agent-learn`.

New public SDK development belongs here. See [DEVELOPMENT.md](DEVELOPMENT.md)
for the boundary between this package and the backing engine repos.

CLI entrypoint:

```bash
agent-learn eval examples/eval_suite.json --output artifacts/eval.json
agent-learn optimize-eval examples/eval_suite_optimization.json --output artifacts/eval-optimization.json
agent-learn run examples/run_manifest.json --no-eval --output artifacts/run.json
agent-learn redteam examples/redteam_manifest.json --output artifacts/redteam.json
agent-learn optimize examples/optimization_manifest.json --output artifacts/optimization.json
agent-learn doctor
```

`agent-learn run`, `agent-learn eval`, `agent-learn redteam`,
`agent-learn optimize`, and `agent-learn optimize-eval` write Agent Learning Kit
artifact kinds
(`agent-learning.run.v1`, `agent-learning.eval.v1`,
`agent-learning.redteam.v1`, `agent-learning.optimization.v1`, and
`agent-learning.eval-optimization.v1`) plus optional JUnit, SARIF, and Markdown
outputs for CI.

This first slice establishes the canonical package name, shared config, module
boundaries, and CLI routing needed for the staged code move into one SDK.
