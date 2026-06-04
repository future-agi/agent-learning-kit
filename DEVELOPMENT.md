# Agent Learning Kit Development Boundary

`agent-learning-kit` is the public SDK for agent simulation, evaluation,
red teaming, and optimization.

All new public SDK work should land here first:

- Public Python imports belong under `agent_learning.*`.
- Public CLI commands belong under `agent-learn`.
- Public examples and cookbooks should use `agent-learning-kit` install commands.
- Shared configuration and keys should flow through `agent_learning.configure()`
  and `AGENT_LEARNING_*` environment variables, with Future AGI aliases only as
  compatibility inputs.

The older repositories (`simulate-sdk`, `agent-opt`, and `ai-evaluation`) are
backing engines during the migration. They can still receive runtime fixes,
protocol support, and test coverage, but they should not introduce new public
SDK surfaces unless the same user-facing API already exists in
`agent-learning-kit`.

When moving an existing surface:

1. Add or update the `agent_learning.*` API/CLI first.
2. Verify it against real local artifacts and the relevant engine tests.
3. Update public docs/examples to use `agent-learning-kit`.
4. Only then simplify or hide the older engine-level surface.
