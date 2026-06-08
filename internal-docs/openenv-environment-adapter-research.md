# OpenEnv Environment Adapter Research

## Primary Sources

- OpenEnv documentation: https://huggingface.co/docs/openenv/index
- Gymnasium Env API: https://gymnasium.farama.org/api/env/

## Implementation Implications

OpenEnv positions environments as reusable agent-facing runtimes with reset,
step, and state lifecycle boundaries. The docs also separate simulation-style
environment control from production/MCP exposure, which maps to Agent Learning
Kit as a local-first replay adapter: tests can prove the environment contract
without requiring external servers.

Gymnasium's environment API gives the stable reset/step shape used by the
adapter: reset returns an initial observation/info pair, and step returns
observation, reward, terminated/truncated, and info-style metadata. The
OpenEnv adapter keeps that shape while adding sandbox, replay transport, and
failure-injection evidence so agent-opt can compare environment candidates.

## Roadmap Bar

The executable OpenEnv bar should prove more than action success:

- deterministic reset evidence
- routed step/action evidence
- state and observation snapshots
- reward and done/terminated/truncated capture
- sandbox/isolation declaration
- no unexpected external-service dependency in local replay
- failure-injection/adversarial-state replay
- optimizer feedback for weak, partial, and verified environment bundles

## Current Local Surface

- `OpenEnvEnvironment` accepts `type: openenv`, `open_env`, `gymnasium_env`, or
  `environment_replay` manifest specs.
- SDK builders expose `simulate.build_openenv_environments()` and
  `simulate.build_openenv_run_manifest()`.
- Agent report configs can require `required_openenv` and `openenv_quality`.
- AgentOptimizer configs can use `build_openenv_optimization_manifest()` and
  `optimize_openenv()` with simulation-evidence scoring for `openenv`.
