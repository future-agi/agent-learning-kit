# Regression Artifact Readiness

## Purpose

Agent optimization is not robust until a good or bad result can be turned into a
replayable regression artifact. The local artifact lifecycle closes that loop:
baseline the current evidence, compare it against prior evidence, render a
report, promote a red-team finding into a regression manifest, and replay that
manifest.

## Local Contract

- The suite must run locally with no hosted optimizer, evaluator, or replay
  dependency.
- The lifecycle must execute `baseline`, `compare`, `report`,
  `promote_to_regression`, and `replay` jobs as first-class suite children.
- Compare output must preserve score-delta and new-finding evidence.
- Promotion output must freeze at least one warning-level red-team finding into
  an `adversarial_attack_pack` regression manifest.
- Replay output must execute the promoted manifest with `replay_pass_rate=1.0`.
- Suite evidence admission must mark every lifecycle child as admitted and
  frozen so CI and Future AGI UI can trust the emitted artifact graph.

## Release Gate

`regression_artifact_readiness` runs
`examples/sdk_regression_artifact_suite.py` in a temporary local workspace. The
gate requires all five lifecycle commands to pass, all required child result
kinds to be present, capability and evidence-admission gates to close, one
finding to be promoted, no compare regressions to appear, and replay pass rate
to remain 1.0.
