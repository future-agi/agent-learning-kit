# p6-r3-sweep — progress log

## Job 1 — R3 fix
- Read p10-review-r1.md / p10-review-r2.md R3 finding + spine §5.4 degrade payload rule
  (`1 <= effective < requested`).
- Root cause: `process_runtime.py` `_provision_sync` copied `port_plan.degraded_reason`
  verbatim onto `build.json`, independent of whether `instances` (requested) actually
  exceeded `effective`. At `instances=1` + `fixed_port`, `effective=1` too — no degrade.
- Fix: `degrade_reason = port_plan.degraded_reason if effective < requested else None`
  (process_runtime.py, in `_provision_sync`, right after `requested = instances`).
- Added 2 tests: W=1+fixed_port -> no degrade; W=3+fixed_port -> degrade recorded.
- Mutation evidence: reverted the guard in a scratch copy -> new W=1 test fails. KILLED.
- SHA of process_runtime.py post-fix: dd8803b0a195f821eca8ad78270d4c9c822df643d45af2c01787252a6a6d20e9

## Job 2 — mutation sweep
- Built scratch harness: mutrun.py (sys.modules substitution, real module name, unmodified
  test files) + run_sweep.py (29 hand-picked mutants across 7 areas, scratch copies only).
- First full run used no `-x` and hung on a "return before respawn" mutant (real subprocess
  spawn tests polling a world that never got spawned) — killed, switched to `-x` (as the task's
  own test invocation specifies) + retry-once-on-timeout. Re-run clean, ~2s/mutant.
- 29 mutants: 24 KILLED on first pass, 2 ANCHOR_ERROR (non-unique anchors, fixed), 5 SURVIVED.
- Survivors graded per severity-grading.md:
  - 1d (seed execution, reset path bundle_dir->work_directory swap): HIGH, promoted from
    test-honesty Medium per P9-S8 precedent (critical-path seed-source invariant).
  - 4b (conformance gate, drop _verify_canary_absent): CRITICAL — matches the doc's own
    named example "vacuous canary pass".
  - 4d (conformance gate, ignore reset's own sentinel_ok): CRITICAL — same family.
  - 6c (rabbitmq seed_failed -> store_statement_failed): HIGH — wrong retry/domain semantics.
  - 6d (redis store_statement_failed -> seed_failed): HIGH — same.
- Added 5 killing tests (all in tests/harness/test_process_runtime.py), verified each kills
  its mutant and all pass against real code. Full suite (test_process_runtime.py +
  test_process_preflight.py): 228 passed, stable across 3 consecutive runs.
- Final SHA of process_runtime.py: unchanged from Job 1 (dd8803b0...) — mutation testing
  never touched the repo file.

Status: DONE. Writing final report.
