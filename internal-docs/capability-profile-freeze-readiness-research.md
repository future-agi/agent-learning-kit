# Capability Profile Freeze Readiness — Research Note

Distilled from the Phase-4 optimizer-expansion research (R§1 2605.11030
setting-relative admissibility; R§3.6 stored-injection red-team rows; the
Nyaya tarka/hetvabhasa operationalization precedent, arXiv:2604.04937 —
scholarly design devices, zero doctrinal claims).

## The claim the gate makes falsifiable

Capability profiles today are point-in-time descriptions: nothing is
content-addressed, nothing floors a metric, nothing must be re-closed at
promotion. An optimizer can therefore "improve" a searched metric while
silently regressing a capability the platform already shipped on. The
`capability_profile_freeze_readiness` gate proves the closed loop:

1. **Freeze** (`freeze_capability_profile`): the capability-profile bundle
   becomes a frozen evidence contract
   (`agent-learning.frozen-capability-profile.v1`) of rows
   `{framework, capability, metric, floor, setting, security, source}` with
   `row_id = sha256(sorted-JSON of the other fields)` — the
   `AgentCandidate.from_config` content-addressing idiom — and
   `contract_digest = sha256(sorted row_ids)`. The canonical deterministic
   fixture is committed at
   `examples/frozen_profiles/frozen_capability_profile.json` (never in the
   pinned `examples/regression_artifacts/` surface).
2. **Attach** (`attach_frozen_profile`): the contract rides the promotion
   artifact under the `frozen_capability_profile` key.
3. **Replay-veto** (`replay_frozen_profile`, the tarka step): every row is
   re-closed against candidate evidence. The gate asserts five executable
   checks:
   - `rows_content_addressed` — recomputed row ids match; a tampered row is
     detected (`asiddha`: the cited row is not the row);
   - `improving_candidate_with_broken_row_vetoed` — a candidate that improves
     its searched metric while breaking one frozen row is vetoed with
     `hetvabhasa_class: "badhita"` (defeated by stronger admissible
     evidence), regardless of the win;
   - `veto_recorded_in_governance` — the veto lands as a steward nirnaya
     record citing the broken `row_id`s, so the audit trail shows improvement
     rejected over frozen-row regression;
   - `out_of_setting_win_non_admissible` — wins measured under a different
     setting digest are recorded as `non_admissible_wins`, visible and never
     promotable (orderings invert across settings);
   - `security_row_non_tradable` — a candidate patch touching
     context-memory path prefixes with any `security: true` row not re-passed
     at floor is vetoed regardless of score.

## Lifecycle wiring

`build_optimization_lifecycle_plan(frozen_profile_path=...)` inserts a
`replay_frozen_profile` step between promotion and regression replay, so the
CLI lifecycle exercises the veto loop end-to-end; replay execution itself
rides the existing `FutureAGIRegressionReplayOptimizer` machinery (ARCH
Decision 3 — freezing re-orders nothing, it only adds row-mapped evidence
requirements).
