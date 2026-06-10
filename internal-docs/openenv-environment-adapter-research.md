# Environment Replay Compatibility Adapter Research

## Primary Sources

- OpenEnv documentation: https://huggingface.co/docs/openenv/index
- Gymnasium Env API: https://gymnasium.farama.org/api/env/

## Implementation Implications

OpenEnv positions environments as reusable agent-facing runtimes with reset,
step, and state lifecycle boundaries. The docs also separate simulation-style
environment control from production/MCP exposure, which maps to Agent Learning
Kit as a local-first environment replay compatibility adapter: tests can prove
the environment contract without requiring external servers.

Gymnasium's environment API gives the stable reset/step shape used by the
adapter: reset returns an initial observation/info pair, and step returns
observation, reward, terminated/truncated, and info-style metadata. The
environment replay compatibility adapter accepts that shape while adding
sandbox, replay transport, and failure-injection evidence so agent-opt can
compare environment candidates. OpenEnv/Gymnasium are executable compatibility
boundaries for environment replay wire formats; Agent Learning environment
replay remains the owned product surface.

## Roadmap Bar

The executable environment replay compatibility boundary should prove more than
action success:

- deterministic reset evidence
- routed step/action evidence
- state and observation snapshots
- reward and done/terminated/truncated capture
- sandbox/isolation declaration
- no unexpected external-service dependency in local replay
- failure-injection/adversarial-state replay
- optimizer feedback for weak, partial, and verified environment bundles
- framework-adapter promotion when arbitrary local frameworks return
  OpenEnv/Gymnasium-style reset/step/state traces

## Current Local Surface

- `EnvironmentReplayEnvironment` is the owned surface. Compatibility manifest
  specs may use `type: openenv`, `open_env`, `gymnasium_env`, or
  `environment_replay`.
- SDK builders expose `simulate.build_environment_replay_environments()` and
  `simulate.build_environment_replay_run_manifest()` as the owned names; the
  `build_openenv_*` names remain compatibility aliases.
- Agent report configs can require environment replay evidence. The
  `required_openenv` and `openenv_quality` names remain compatibility aliases.
- AgentOptimizer configs can use `build_environment_replay_optimization_manifest()`
  and `optimize_environment_replay()` as owned names; `build_openenv_*` and
  `optimize_openenv()` remain compatibility aliases with simulation-evidence
  scoring for the `openenv` wire format.
- Framework adapter presets include `openenv` and `gymnasium`, and generic local
  adapter outputs with `openenv`, `open_env`, reset/step trajectories, sandbox
  metadata, reward/done fields, or failure-injection records are normalized into
  evaluator-visible `openenv` state, trace artifacts, and `openenv` events.
- Auto-generated adapter eval configs now derive `required_openenv` and
  `openenv_quality` from selected framework-probe output; see
  `examples/sdk_framework_adapter_openenv_trace.py`.
- V1 release-check now includes OpenEnv and Gymnasium in the native framework
  adapter contract matrix and validates `examples/framework_openenv_manifest.json`
  for OpenEnv runtime state, events, artifacts, coverage, and quality gates.
- `examples/sdk_openenv_environment_optimization.py` is now a release-checked
  AgentOptimizer cookbook: local release-check runs the weak/partial/verified
  OpenEnv bundle search without external env and requires the verified replay
  candidate plus `openenv_coverage` and `openenv_quality` scores of 1.0.
