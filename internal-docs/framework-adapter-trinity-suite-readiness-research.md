# Framework Adapter Trinity Suite Readiness

## Why this gate exists

The framework adapter path already proves that an unknown local framework object
can be discovered, probed, optimized, and promoted into a run manifest. V1 also
needs to prove that the promoted adapter can move through the full local trinity:
simulation, evaluation, red-team, and optimization. A prompt-only optimizer can
pass a single run; the framework-adapter trinity suite must pass a composed
artifact that keeps the adapter contract visible across run, adversarial
campaign, and suite optimization boundaries.

## Local contract

- `examples/sdk_framework_adapter_trinity_suite.py` writes a local
  `agent-learning.suite.v1` workspace with a promoted framework run manifest
  and a red-team campaign manifest pinned to the same adapter contract.
- `examples/sdk_framework_adapter_trinity_suite_optimization.py` writes an
  `agent-learning.suite-optimization.v1` workspace that compares a run-only seed
  against the nested run+redteam suite and must select the full suite job.
- Both cookbooks stay local-first: the selected adapter is
  `custom_refund_orchestrator.execute_task(dict)`, the target is an in-process
  fixture, `required_env` is empty, and generated manifests preserve framework
  adapter discovery/probe proof metadata.

## Release-check evidence

`framework_adapter_trinity_suite_readiness` executes both SDK cookbooks in
temporary workspaces and verifies:

- the plain suite passes `run` and `redteam` child jobs with admitted evidence;
- the run child closes `framework_runtime_contract` and
  `framework_adapter_contract_quality`;
- the red-team child closes `adversarial_resilience` and
  `red_team_campaign_quality`;
- the red-team manifest covers prompt-injection and credential-exfiltration
  attacks across instruction and tool surfaces;
- the suite optimizer selects the nested `suite.json` job instead of the
  run-only seed;
- optimizer trace governance keeps role diversity, contract gate, rollback,
  locality, steward, final score, pass rate, and terminal completion evidence.

This keeps the "holy trinity" boundary executable: local framework adapters must
survive simulation/eval/red-team composition and optimizer selection before V1
claims arbitrary framework robustness.
