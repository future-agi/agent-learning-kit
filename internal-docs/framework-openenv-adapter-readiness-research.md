# Framework Environment Replay Adapter Readiness

## Purpose

Agent Learning environment robustness has to apply to arbitrary framework
adapters, not only to hand-authored environment manifests. A local framework can
return a plain OpenEnv/Gymnasium-style payload, and the generic adapter path
should turn that compatibility payload into evaluator-visible environment
replay evidence.

## Local Contract

- The adapter stays local and in-process: no HTTP, WebSocket, container, or
  hosted environment is required for release metadata.
- Returned payloads with `openenv`/`open_env`, reset and step trajectories,
  reward/done fields, sandbox metadata, or failure-injection records normalize
  into `openenv` state.
- The wrapper emits `openenv` events, trace artifacts, and framework-runtime
  output summaries that include state keys, event types, artifact types, and an
  environment replay summary.
- Adapter promotion derives `required_openenv` and `openenv_quality` gates so
  evaluator metrics can enforce reset, step, reward, done, termination,
  sandbox, metadata, local transport, deterministic reset, and failure evidence.
  These names remain compatibility aliases under the native environment replay
  release surface.

## Release Gate

`framework_environment_replay_adapter_readiness` runs
`examples/sdk_framework_adapter_openenv_trace.py` locally. The gate requires the
promoted local adapter to emit normalized environment replay evidence, OpenEnv
wire-compatible state/events, trace artifacts, generated quality gates, and passing
`framework_runtime_contract`, `framework_adapter_contract_quality`,
`environment_replay_coverage`, and `environment_replay_quality` metrics.
