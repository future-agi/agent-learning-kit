# Legacy TypeScript Evaluation Source

This package is intentionally marker-only.

The TypeScript evaluation runtime, examples, tests, and package metadata moved to:

- `../../../agent-learning-kit/typescript/agent-learning-kit`

Use the unified package:

```bash
pnpm add @future-agi/agent-learning-kit
```

```ts
import { Evaluator } from "@future-agi/agent-learning-kit";
import { LocalEvaluator } from "@future-agi/agent-learning-kit/evals/local";
```

Do not publish this package as a second evaluation SDK.
