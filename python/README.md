# ai-evaluation

`ai-evaluation` has been consolidated into `agent-learning-kit`.

The evaluation runtime, examples, tests, and new development now live in:

- `../../agent-learning-kit/src/fi/evals`
- `../../agent-learning-kit/src/agent_learning/evals.py`
- `../../agent-learning-kit/examples`

Use the unified SDK:

```bash
pip install agent-learning-kit
agent-learn eval examples/eval_suite.json
agent-learn eval-artifact examples/fixtures/task_artifacts/refund_task_run.json \
  --config examples/artifact_task_eval_config.json
```

Python callers should import:

```python
from agent_learning import evals
```

This repository is retained only for source history and migration context. Do
not publish it as a second evaluation SDK.
