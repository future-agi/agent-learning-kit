# Framework Adapter Probe Readiness

## Purpose

The bring-your-own framework path has to work before a user has written a full
manifest. The local probe flow closes that gap: inspect candidate methods,
select the runnable method/input mode, prove runtime evidence, and promote the
selected adapter into a normal `agent-learning.run.v1` manifest.

## Local Contract

- `simulate.run_framework_adapter_probe()` proves one explicit adapter shape
  against expected output, tool calls, events, runtime trace, and local adapter
  contract evidence.
- `simulate.discover_framework_adapter()` ranks local method/input candidates
  without calling HTTP or hosted framework endpoints.
- `optimize.optimize_framework_adapter_probe()` searches explicit or
  auto-discovered adapter candidates and emits a native probe proof.
- `optimize.build_framework_run_manifest_from_probe_optimization()` preserves
  the selected method, input mode, target, proof, and discovery metadata in the
  promoted run manifest.
- One-call helpers keep the full target-to-evaluated-run workflow local while
  still emitting the same adapter contract and runtime metrics.

## Release Gate

`agent-learn release-check` runs the representative probe, discovery,
optimization, auto-discovery, promotion, and one-call cookbooks as
`framework_adapter_probe_readiness`. The gate requires `execute_task(dict)` to
be selected, discovery to be used where expected, probe proofs to pass, promoted
manifests to carry proof/discovery metadata, and evaluated runs to close
framework runtime, adapter-contract, framework-trace, and tool-selection
metrics.

The optimization surfaces also render a first-class `framework_adapter_probe`
report/action card. Release-check verifies that the card is local-only,
contains the selected adapter method/input mode, exposes the native probe proof,
and can export that proof through `action-run`. This keeps BYO-framework
adapter optimization visible to Future AGI UI/report surfaces instead of
leaving the proof buried inside optimizer history.
