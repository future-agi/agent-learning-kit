# V1 Release Candidate Notes

Date: 2026-06-10

Observed branch: `release/v1-agent-learning-kit`

Cut commit: the commit tagged `v1.0.0-rc.1` (the tag is created only after
the full release proof passes on that exact commit).

## Decision

The v1 release-candidate gate is green and the Phase-1 release-cut decisions
are recorded and applied. Decisions D1–D7 — Python version label kept at
`0.1.0` (D1), TypeScript `@future-agi/agent-learning-kit` kept at `0.2.0`
with the root workspace private (D2), classifier moved to
`Development Status :: 4 - Beta` (D3), `uv.lock` tracked in git and excluded
from the sdist (D4), security contact kept as the owner's item (D5), annotated
local tag `v1.0.0-rc.1` on the proved commit (D6), and the `build`/`hatchling`
dev dependency group (D7) — are recorded in the Phase-1 PRD:
`internal-docs/agent-trinity/v1-program/phase1-v1-release-cut/PRD.md` §5
(in the core internal-docs repo).

This is not a claim that the full long-term Agent Learning product vision is
complete. It is a claim that the current v1 release contract has executable
evidence and packaging proof, rerun in full on the cut commit.

## Proof Artifact

Proof executed on the commit tagged `v1.0.0-rc.1` (the Phase-1 release-cut
commit). The tag is created only after this proof passes, so the tagged commit
is the proved commit.

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

`release_check` inside this proof includes the two post-stale-proof gates,
`active_ai_evaluation_source_embedded` and `package_distribution_hygiene`
(66 gates total, closed-set asserted in tests).

Sdist hygiene: `sdist_member_count=564` with `sdist_forbidden_members=[]` —
previously the sdist leaked all 45 `internal-docs/` files, `uv.lock`, the
roadmap, internal guides, and 104 `typescript/` files.

Notable command evidence:

- Python tests: `307 passed` (305 at the Phase-1 audit + 2 new
  distribution-hygiene tests)
- Python build:
  `agent_learning_kit-0.1.0.tar.gz` and
  `agent_learning_kit-0.1.0-py3-none-any.whl`
- TypeScript build:
  `@future-agi/agent-learning-kit@0.2.0`
- TypeScript tests: 646 passed tests, 6 skipped tests

Proof artifact: `/tmp/agent-learning-release-proof.json`, kind
`agent-learning.release-proof.v1`.

## Current Package Labels

- Python distribution: `agent-learning-kit==0.1.0`
- Python package classifier: `Development Status :: 4 - Beta`
- Root TypeScript workspace: `0.1.0`
- Public TypeScript package: `@future-agi/agent-learning-kit==0.2.0`
- Repository license artifact: `LICENSE` with Apache-2.0 text
- Package license metadata: Python `Apache-2.0`, TypeScript `Apache-2.0`
- Public README is intentionally concise; detailed cookbook/deep-dive material
  lives in `internal-docs/agent-learning-kit-readme-deep-dive.md`.
- TypeScript npm package includes package-local `LICENSE` and `NOTICE`, and the
  publish file list excludes tests, source maps, and TypeScript build-info
  files.

These labels are settled by decisions D1–D3: the versions stay as listed, the
classifier is Beta, and the `v1.0.0-rc.1` tag (not the semver) names the
product milestone.

## Current Worktree Notes

After the proof run:

- `uv.lock` is tracked in git and excluded from the sdist (decision D4).
- Python build output exists under ignored `dist/`.
- TypeScript build output exists under ignored
  `typescript/agent-learning-kit/dist/`.
- The release-candidate documentation and license metadata updates are
  committed on the cut commit.

Release owner decision:

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

Completed by Phase 1 (the `v1.0.0-rc.1` cut):

1. Done (D1/D2) — package version labels decided: Python `0.1.0`, TypeScript
   `@future-agi/agent-learning-kit` `0.2.0`; the tag names the milestone.
2. Done (D3) — Python classifier moved to `Development Status :: 4 - Beta`.
3. Done (D4) — `uv.lock` policy decided: tracked in git, excluded from the
   sdist.
4. Done — sdist hygiene enforced by the new `package_distribution_hygiene`
   gate (`sdist_member_count=564`, `sdist_forbidden_members=[]`).
5. Done — README claims reconciled with executable proof (edits R1–R8).
6. Done — release-candidate documentation and metadata files committed on the
   cut commit: `LICENSE`, `NOTICE`, `CHANGELOG.md`, `CONTRIBUTING.md`,
   `SECURITY.md`, `CODE_OF_CONDUCT.md`, `.github/`, `docs/assets/`,
   `typescript/package.json`, `typescript/agent-learning-kit/package.json`,
   `typescript/agent-learning-kit/LICENSE`,
   `typescript/agent-learning-kit/NOTICE`, `V1_RELEASE_ROADMAP.md`,
   `internal-docs/v1-engineering-handover.md`, and this file.
7. Done — full release-proof rerun on the cut commit (the commit tagged
   `v1.0.0-rc.1`); see Proof Artifact above.
8. Done (D6) — CHANGELOG updated, release-notes draft written
   (`internal-docs/v1-release-notes-draft.md`), and the annotated local tag
   `v1.0.0-rc.1` created on the exact proved commit.

Remaining owner items (push/publish stay owner actions):

1. Set the security-contact address in `SECURITY.md` (D5) — the single
   remaining owner edit before publishing.
2. Choose publish targets and owner: PyPI or TestPyPI first, npm
   `@future-agi/agent-learning-kit`, credentials, and 2FA process; `git push
   --tags`, PyPI, and npm publishing remain explicit owner actions.
3. Build/publish the Python and TypeScript artifacts with the same package
   versions used in the proof.
4. Verify the TypeScript package contents after build because npm publishes
   ignored `dist/` output through `files=["dist"]`.
5. Attach the release-proof JSON artifact or copy its summary into the release
   record.
6. Keep post-v1 framework/provider/frontend expansion out of the release-cut
   unless it is separately implemented, documented, and proved.
7. If any package metadata or lockfile changes are made before publishing,
   rerun on the exact publishing commit:

   ```bash
   uv run python -m agent_learning.cli release-proof \
     --project-root . \
     --output /tmp/agent-learning-release-proof.json \
     --quiet
   ```

## Post-V1 Queue

These are not blockers for this v1 release candidate:

- `trinity.py` refactor: split the 42.7k-line gate registry into a
  `trinity/gates/` package (known debt recorded in the Phase-1 PRD §6).
- More arbitrary-framework adapter promotions.
- More provider-shaped adapters.
- Broader frontend/product proof surfaces.
- Pydantic deprecation cleanup.
- TypeScript Jest open-handle cleanup.
