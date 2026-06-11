# Red-Team Readiness Certification

Date: 2026-06-09

## Why This Exists

Red-team optimization should not start from attack labels alone. Before adaptive
attack evolution or live agent testing, the SDK needs a local preflight that
proves the target workspace is runnable, importable, observable, controlled, and
ready to produce trustworthy adversarial evidence. This is the pen-testing side
of the trinity: simulation gathers the workspace/import/campaign/trust/control
state, evaluation scores readiness, and agent-opt selects the zero-gap bundle.

## Research Inputs

The cookbook keeps the research inputs in manifest metadata so release-check can
verify that the gate is still grounded in current agentic red-team work:

- https://arxiv.org/abs/2605.04019
- https://arxiv.org/abs/2605.09684
- https://arxiv.org/abs/2605.13940
- https://arxiv.org/abs/2605.04808
- https://arxiv.org/abs/2601.13518
- https://arxiv.org/abs/2606.04425

These sources motivate runtime evidence, monitor/detector loops, third-party
workspace trust, controllable test environments, autonomous multi-step attacks,
and persistent/stored prompt-injection coverage.

## Release Gate

`agent-learn release-check` runs `redteam_readiness_certification`. The gate
executes `examples/sdk_redteam_readiness_certification_optimization.py` locally
and requires:

- A two-candidate optimizer search over weak and verified
  `simulation.environments` bundles.
- Six required environment layers:
  `workspace_run_manifest`, `framework_import`, `red_team_campaign`,
  `agent_trust_boundary`, `agent_control_plane`, and `red_team_readiness`.
- The verified candidate to reach `red_team_readiness_coverage=1.0`,
  `red_team_readiness_quality=1.0`, and `tool_selection_accuracy=1.0`.
- The weak candidate to score below the verified candidate.
- Five ready components: workspace run, framework import, red-team campaign,
  trust boundary, and control plane.
- Zero blocking gaps, failed components, missing readiness evidence, missing
  readiness signals, failed red-team runs, open high findings, or unmapped
  findings.
- Campaign coverage over prompt injection and credential exfiltration across
  tool and memory surfaces on the local CLI chat channel, with artifacts,
  findings, and implemented mitigations bound to each cell.

## Implementation Rule

Keep this gate local-first. It should prove readiness before any external
red-team hook, hosted evaluator, or live provider is trusted. Future live-target
work can add explicit user-selected checks, but the release metadata must remain
reproducible from local workspace/import/campaign/trust/control evidence.
