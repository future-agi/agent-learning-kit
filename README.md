# ai-evaluation

`ai-evaluation` has been consolidated into `agent-learning-kit`.

The Python evaluation runtime, examples, tests, and public development now live in:

- `../agent-learning-kit/src/fi/evals`
- `../agent-learning-kit/src/agent_learning/evals.py`
- `../agent-learning-kit/examples`

The TypeScript evaluation SDK has also moved to:

- `../agent-learning-kit/typescript/agent-learning-kit`

Use the unified SDKs:

```bash
pip install agent-learning-kit
pnpm add @future-agi/agent-learning-kit
```

Python callers should import:

```python
from agent_learning import evals
```

TypeScript callers should import:

```ts
import { Evaluator } from "@future-agi/agent-learning-kit";
import { LocalEvaluator } from "@future-agi/agent-learning-kit/evals/local";
```

This repository is retained only for source history and migration context. Do
not publish it as a second evaluation SDK.
