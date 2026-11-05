# SSE E2E matrix

- [x] Read the Principles section of poteto-mode.
- [x] Phase A: Frame the falsifiable definition of done, quantified scope, blockers, and rigor.
- [x] Phase B: Design atomic units, build the verification harness first, and choose safe fan-out seams.
- [ ] Phase C: Run each unit as hypothesis, smallest change, real measurement, and VERIFIED / NOT VERIFIED / INCONCLUSIVE verdict (local unit verified; live E2B pending).
- [x] Phase D: Append every material decision and pivot to `.audit/sse-e2e-matrix.tsv`.
- [ ] Phase E: Run the whole matrix against real E2B artifacts and hand back verified and open results.

Task-specific units, refined after discovery:

- [x] Discover the existing E2B launch, upload, port, secret-file, and teardown path.
- [x] Define a typed ten-case matrix across at least three open-source agent frameworks.
- [x] Build one rerunnable E2B runner that provisions, deploys, exercises, captures evidence, and always tears down.
- [x] Add credential-free contract tests for matrix validation and result accounting.
- [ ] Run the ten live cases in E2B and retain a redacted JSON report.
- [ ] Independently review the runner, evidence, and decision trail.

Definition of done:

- One command creates an E2B sandbox, runs exactly ten named SSE cases across at least three pinned open-source frameworks, records per-case transport/framework/behavior/evidence/verdict, and deletes the sandbox.
- A passing case must observe its promised user-visible behavior through ALK. Setup-only, import-only, mocked-E2B, and local-only checks do not count as E2E passes.
- The final report contains ten non-ambiguous verdicts. INCONCLUSIVE and NOT VERIFIED are not counted as passes.
