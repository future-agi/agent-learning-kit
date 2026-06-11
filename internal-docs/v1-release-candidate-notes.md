# V1 Release Candidate Notes

Date: 2026-06-10

Observed branch: `main`

Baseline candidate commit before release-candidate handoff metadata:
`a21cbbb Promote transcript adapter probes`

## Decision

The v1 release-candidate gate is green. The codebase is ready to enter release
cut operations once the owner decides package version labels and lockfile
policy.

This is not a claim that the full long-term Agent Learning product vision is
complete. It is a claim that the current v1 release contract has executable
evidence and packaging proof.

Update after this proof: the active-goal framework adapter capability-profile
increment changed code and tests after the proof artifact below. Treat the
artifact as evidence for the earlier release-readiness commit; rerun full
`agent-learn release-proof` before publishing from a commit that includes the
profile increment.

## Proof Artifact

Command:

```bash
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof.json \
  --quiet
```

Result summary:

- Overall status: `passed`
- `summary.ready`: `true`
- `full_proof`: `true`
- Required checks selected: 7/7
- Required checks passed: 7/7
- Failed checks: 0
- Pending checks: 0
- Skipped checks: 0
- Unknown selected checks: 0

Checks that passed:

- `release_check`
- `ruff`
- `pytest`
- `build`
- `typescript_build`
- `typescript_test`
- `git_diff_check`

Notable command evidence:

- Python tests: `302 passed, 10 warnings`
- Python build:
  `agent_learning_kit-0.1.0.tar.gz` and
  `agent_learning_kit-0.1.0-py3-none-any.whl`
- TypeScript build:
  `@future-agi/agent-learning-kit@0.2.0`
- TypeScript tests:
  21 passed suites, 2 skipped suites, 646 passed tests, 6 skipped tests
- TypeScript test note:
  Jest exits 0 but still reports an open async handle warning after completion.

## Current Package Labels

- Python distribution: `agent-learning-kit==0.1.0`
- Python package classifier: `Development Status :: 3 - Alpha`
- Root TypeScript workspace: `0.1.0`
- Public TypeScript package: `@future-agi/agent-learning-kit==0.2.0`
- Repository license artifact: `LICENSE` with Apache-2.0 text
- Package license metadata: Python `Apache-2.0`, TypeScript `Apache-2.0`
- Public README is intentionally concise; detailed cookbook/deep-dive material
  lives in `internal-docs/agent-learning-kit-readme-deep-dive.md`.
- TypeScript npm package includes package-local `LICENSE` and `NOTICE`, and the
  publish file list excludes tests, source maps, and TypeScript build-info
  files.

Release owner decision:

- Keep these package versions and call the release "v1" at the release/tag
  level, or
- Bump package versions before publishing and rerun full release-proof on the
  exact publishing commit.
- Keep the Python alpha classifier for a conservative first public release, or
  change it before publishing and rerun full release-proof.

## Current Worktree Notes

After the proof run:

- `uv.lock` is untracked.
- Python build output exists under ignored `dist/`.
- TypeScript build output exists under ignored
  `typescript/agent-learning-kit/dist/`.
- The release-candidate documentation and license metadata updates are intended
  to be committed before handoff.

Release owner decision:

- Decide whether `uv.lock` should be adopted, ignored, or removed from the
  release worktree.
- Do not publish from a dirty worktree unless the dirtiness is explicitly
  approved and documented.

## Public Positioning

Use this wording:

> Agent Learning v1 provides a local-first SDK and CLI for evaluating,
> simulating, optimizing, red-teaming, and release-checking agent workflows
> across prompt, task, framework-adapter, memory, multi-agent, workflow,
> browser/realtime, and environment robustness surfaces.

Avoid these claims:

- Do not say the full long-term product vision is complete.
- Do not claim universal superiority over OpenEnv.
- Do not present OpenEnv as the product owner or runtime dependency.
- Do not claim all external frameworks are imported and executed natively. The
  v1 proof uses deterministic local adapter-shaped cookbooks for broad
  framework compatibility.

## Release-Cut Checklist

Before publishing:

1. Decide package version labels.
2. Decide whether the Python `Development Status :: 3 - Alpha` classifier is
   intentional for v1.
3. Decide `uv.lock` policy.
4. Choose publish targets and owner: PyPI or TestPyPI first, npm
   `@future-agi/agent-learning-kit`, credentials, and 2FA process.
5. Commit the release-candidate documentation and metadata files:
   `LICENSE`, `NOTICE`, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`,
   `CODE_OF_CONDUCT.md`, `.github/`, `docs/assets/`, `typescript/package.json`,
   `typescript/agent-learning-kit/package.json`,
   `typescript/agent-learning-kit/LICENSE`,
   `typescript/agent-learning-kit/NOTICE`,
   `V1_RELEASE_ROADMAP.md`,
   `internal-docs/v1-engineering-handover.md`, and this file.
6. If any package metadata or lockfile changes are made, rerun:

   ```bash
   uv run python -m agent_learning.cli release-proof \
     --project-root . \
     --output /tmp/agent-learning-release-proof.json \
     --quiet
   ```

7. Create the release branch or tag from the exact proved commit.
8. Build/publish Python artifacts with the same package version used in the
   proof.
9. Build/publish the TypeScript package with the same package version used in
   the proof.
10. Verify the TypeScript package contents after build because npm publishes
   ignored `dist/` output through `files=["dist"]`.
11. Attach the release-proof JSON artifact or copy its summary into the release
   record.
12. Keep post-v1 framework/provider/frontend expansion out of the release-cut
   unless it is separately implemented, documented, and proved.

## Post-V1 Queue

These are not blockers for this v1 release candidate:

- More arbitrary-framework adapter promotions.
- More provider-shaped adapters.
- Broader frontend/product proof surfaces.
- Pydantic deprecation cleanup.
- TypeScript Jest open-handle cleanup.
