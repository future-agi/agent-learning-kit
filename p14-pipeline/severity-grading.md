# Issue Severity Grading — v1.0 (2026-08-25)

Status: EVOLVING — this document is expected to change. Every change appends
a changelog entry; every contested grading appends a PRECEDENT entry, so the
system accretes case law instead of re-arguing calibration.
Owner: Khushal. Applies to: every review round's findings, from P10-R2 onward.
Legacy mapping (rounds before this doc): BLOCKER→Critical, MAJOR→High,
MODERATE/MINOR→Medium, NIT→Low.

## The grade is a function of three axes

1. **Impact** — what breaks: correctness of results, security/credentials,
   data loss, availability (job dies/hangs) > wrong retry/attribution
   semantics > telemetry/observability accuracy > diagnostics/docs/cosmetics.
2. **Reachability** — how a real job hits it: common configuration >
   plausible edge > requires another bug or a contract violation to trigger >
   unreachable today (latent: dead branch, no producer of the trigger).
3. **Loudness** — silent wrongness grades HIGHER than loud failure of the
   same impact. A silently wrong grade is worse than a crashed job; a typed
   failure with the wrong domain is worse than a typed failure with a noisy
   message.

## The four levels

### CRITICAL
Silent wrong results, credential/privilege escape, data loss, or a
whole-job hang/loss — reachable in a common or plausible configuration.
**Response: the round cannot clear; fix before anything else; the fix gets
mutation evidence in the next round.**
Calibration examples (today): seeds executing from the unverified checkout
(P6-B1); untrusted `rejected[].sequence` silently orphaning the spool tail
incl. the terminal event (outbound-N1); leaked threads starving the shared
executor → job hangs with zero receipts (P9-R1); a fence on the final drain
exiting 0 with an empty event stream (P10-B1); vacuous canary pass.

### HIGH
Deterministic loud failure of a legitimate configuration; wrong
retry/failure-domain semantics (deterministic fault retried to exhaustion,
or a retryable one made terminal); a security control that is absent where
the contract requires one; a regression introduced by a fix.
**Response: fix before the phase commits. Deferral requires an explicit
orchestrator ruling logged as a decision (D-entry), never a silent ride.**
Examples: every rabbitmq bundle failing at boot (P6-N5/N6/R2); §2f codes
flattened to `infrastructure` breaking retry semantics (P10-B5); the
redaction gaps that let DSNs/tokens leave the sandbox (outbound-P3/P4 —
security-tinged Highs; grade Critical instead when the leaked value is a
live credential on a common path).

### MEDIUM
Real defects with bounded consequence: telemetry/attribution inaccuracy
(orphan events, wrong `cause`, under-reported receipts fields), latent
hazards in dead-or-guarded branches, performance/stall classes that are
contract-conformant but costly, test-honesty gaps on non-critical
assertions.
**Response: fix-or-ride at arbitration; if riding, it MUST appear on the
phase's known-defects list with a one-line consequence. Test-honesty
Mediums on critical-path invariants get promoted to High (a green suite
that cannot see a load-bearing invariant is not Medium — precedent: P9-S8).**
Examples: `scenario_retried` emitted for a retry that never ran (P9-S2);
`effective_size` staleness (P9-S3); the 120s promote-poll stall (P6-Q8).

### LOW
Cosmetic, diagnostic-quality, stale pins/comments, dead-seam annotations,
optional tests for already-proven behavior, style.
**Response: fold into the current fix pass only if genuinely free
(reviewer-authored text, ≤5 lines); otherwise backlog. Never blocks
anything; listed in the review report, not necessarily in the commit note.**
Examples: version-pin drift (P6-R8), a KeyError instead of a bespoke
assertion message (P6-R9), duplicated-prefix typos.

## Standing modifiers (apply after the base grade)

- **Silent-and-wrong ⇒ +1 level.** The system's worst failure class is a
  green result nobody can distrust.
- **Fix-introduced regression ⇒ at least High**, regardless of impact class
  (precedent: P6-R2 — one env line, graded MAJOR/High, correctly).
- **Unreachable-today ⇒ −1 level, floor Medium if the branch would hang or
  corrupt when it becomes live** (precedent: P9-S1 — dead branch containing
  a pool hang: Medium, fix-cheap).
- **Contract-silent ⇒ cap at Medium** and raise a contract defect instead of
  grading the code (precedent: P6-Q5 — the sentinel guard: the contract
  places no enforcement obligation, so the gap is defense-in-depth).

## Process couplings

- CRITICAL/HIGH block a clean round (the clean-round rule keys off "nothing
  above Medium" from now on — Medium is the new "minor").
- Riding Mediums = the phase's known-defects list; Lows = report-only.
- Reviewer reports state the grade AND the axis values ("Critical: silent /
  common / correctness") so arbitration reviews the reasoning, not a label.
- Disputes: the orchestrator arbitrates; the ruling + reasoning become a
  PRECEDENT entry here.

## Precedents

- 2026-08-25 passb-review ruff-E402: a fix-introduced regression that is
  purely mechanical/lint-level (no behavior change, caught loudly by the
  linter/CI) grades MEDIUM, not the "fix-introduced ⇒ ≥High" floor — the
  modifier's intent is behavioral regressions that ship wrong behavior.
  Arbitrated by the orchestrator; fixed in-round regardless (one line).

- 2026-08-25 P9-S8: a test-honesty gap on the §4.5b lock invariant —
  assertions swallowed by production error-handling = zero coverage on a
  load-bearing invariant ⇒ treated as promote-to-High territory even with
  correct code.
- 2026-08-25 P6-Q5: bypassable read-only sentinel guard capped at Medium —
  the contract imposes no enforcement obligation; raised as contract note
  instead.
- 2026-08-25 P6-R2: one-line fix-introduced regression = High even though
  the failure is loud and typed.

## Changelog

- v1.0 (2026-08-25): initial four-level system; mapped from the legacy
  BLOCKER/MAJOR/MODERATE/MINOR/NIT vocabulary; three modifiers; three
  founding precedents.
