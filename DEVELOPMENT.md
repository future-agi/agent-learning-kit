# Agent Learning Kit Development Boundary

`agent-learning-kit` is the public SDK and code home for agent simulation,
evaluation, red teaming, and optimization.

All new public SDK work should land here first:

- Public Python imports belong under `agent_learning.*`.
- Public CLI commands belong under `agent-learn`.
- Public examples and cookbooks should use `agent-learning-kit` install commands.
- Runtime implementation should live under this repo, either in
  `agent_learning.*` for public APIs or vendored `fi.*` engine packages while
  migration is in progress.
- Shared configuration and keys should flow through `agent_learning.configure()`
  and `AGENT_LEARNING_*` environment variables. Vendored engine aliases
  (`FI_API_KEY`, `FI_SECRET_KEY`, and Future AGI variants) are synced from that
  public config for compatibility only; new public code should not introduce a
  separate key model.

The older repositories (`simulate-sdk`, `agent-opt`, and `ai-evaluation`) are
legacy source/history during the migration. New runtime code should be moved
into `agent-learning-kit`, not merely wrapped here. If a fix must first land in
an old repo to stabilize an engine, copy the verified implementation into this
repo before treating the public SDK work as done.

When moving an existing surface:

1. Move or add the implementation code under this repository.
2. Add or update the `agent_learning.*` API/CLI.
3. Verify it against real local artifacts and relevant engine tests using this
   repository as the source path.
4. Update public docs/examples to use `agent-learning-kit`.
5. Only then simplify or hide the older engine-level surface.
