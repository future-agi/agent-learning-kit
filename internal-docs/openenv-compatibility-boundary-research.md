# OpenEnv Compatibility Boundary Research

## Boundary

OpenEnv/Gymnasium are compatibility inputs only.
Agent Learning remains the primary optimization, simulation, evaluation, and
pen-test layer.

The owned product surface is environment replay: deterministic reset/step/state
evidence, reward/done metadata, sandbox/isolation evidence, failure injection,
adapter contracts, optimizer proof, evaluation metrics, and report/action
artifacts. OpenEnv, Gymnasium, `openenv`, `open_env`, and `gymnasium_env` names
remain wire-format aliases accepted by local compatibility examples.

## Release Gate

`openenv_compatibility_boundary` is an executable release-check gate. It fails
if:

- `openenv`, legacy `gym`, or `gymnasium` appear as Python or TypeScript
  runtime dependencies,
- local source imports `openenv`, legacy `gym`, or `gymnasium` directly,
- required docs stop saying OpenEnv/Gymnasium are compatibility inputs rather
  than the product center.

The gate intentionally allows release-checked compatibility cookbooks such as
`examples/sdk_openenv_environment_optimization.py` and
`examples/sdk_framework_adapter_openenv_trace.py`, because those examples prove
that Agent Learning can consume OpenEnv/Gymnasium-shaped payloads without
depending on those projects.
