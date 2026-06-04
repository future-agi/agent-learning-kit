# agent-learning-kit

`agent-learning-kit` is the unified Future AGI SDK for agent simulation,
evaluation, red teaming, and optimization.

The package gives users one key/config layer and one import namespace while
preserving independent modules:

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

The old SDKs remain usable during migration:

- `agent-simulate` maps to `agent_learning.simulate`
- `ai-evaluation` maps to `agent_learning.evals`
- `agent-opt` maps to `agent_learning.optimize`

CLI entrypoint:

```bash
agent-learn eval suite.json --output artifacts/eval.json
agent-learn run manifest.json --output artifacts/run.json
agent-learn optimize manifest.json --output artifacts/optimization.json
agent-learn doctor
```

This first slice is an umbrella/facade package. It does not copy all code from
the existing SDKs yet; it establishes the canonical package name, shared config,
module boundaries, and CLI delegation needed for a staged migration.
