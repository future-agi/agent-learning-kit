# Agent Learning Kit Development Boundary

`agent-learning-kit` is the public SDK and code home for agent simulation,
evaluation, red teaming, and optimization.

All new public SDK work should land here first:

- Public Python imports belong under `fi.alk.*`.
- Public TypeScript package work belongs under `typescript/agent-learning-kit`
  and publishes as `@future-agi/agent-learning-kit`.
- Public CLI commands belong under `agent-learn`.
- Public examples and cookbooks should use `agent-learning-kit` install commands.
- Runtime implementation should live under this repo, either in
  `fi.alk.*` for public APIs or vendored `fi.*` engine packages while
  migration is in progress.
- Shared configuration and keys should flow through `fi.alk.configure()`
  and `AGENT_LEARNING_*` environment variables. Vendored engine aliases
  (`FI_API_KEY`, `FI_SECRET_KEY`, and Future AGI variants) are synced from that
  public config for compatibility only; new public code should not introduce a
  separate key model.

`ai-evaluation` is an active engine for this release, not legacy history. Its
Python runtime must be present under `src/fi/evals`, and its TypeScript SDK
source must be present under `typescript/agent-learning-kit/src`.
`agent-learn release-check` compares those source trees with
the ai-evaluation source inventory (maintained in the internal-docs repo) so missing ai-evaluation
files fail the v1 release gate.

The older `simulate-sdk` and `agent-opt` repositories are source/history during
the migration. New runtime code should be moved into `agent-learning-kit`, not
merely wrapped here. If a fix must first land in an old repo to stabilize an
engine, copy the verified implementation into this repo before treating the
public SDK work as done.

For the current source map, see [LIBRARIES.md](LIBRARIES.md). In short:

- `ai-evaluation` lives under `src/fi/evals`.
- `ai-evaluation` TypeScript source lives under `typescript/agent-learning-kit/src`.
- `simulate-sdk` lives under `src/fi/simulate`.
- `agent-opt` lives under `src/fi/opt`.
- Public Python APIs live under `src/fi/alk`.

When moving an existing surface:

1. Move or add the implementation code under this repository.
2. Add or update the `fi.alk.*` API/CLI.
3. For TypeScript surfaces, add/update the package under
   `typescript/agent-learning-kit` and verify `pnpm --dir typescript --filter
   @future-agi/agent-learning-kit build` plus the package test command.
4. Verify it against real local artifacts and relevant engine tests using this
   repository as the source path.
5. Update public docs/examples to use `agent-learning-kit`.
6. Only then simplify or hide the older engine-level surface.
# Automatic E2B templates

The `E2B template` GitHub Actions workflow builds and certifies the hosted runtime
on every push to `main`, including merges. It can also be rerun manually with
**Actions → E2B template → Run workflow**, selecting `main`. Pull requests that
change runtime inputs run credential-free validation only.

One-time setup: add the E2B team's API key as the repository Actions secret
`E2B_API_KEY`. The workflow uses `GITHUB_TOKEN` with `packages: write` to push to
`ghcr.io/future-agi/agent-learning-kit/alk-hosted-runtime`, and supplies that
short-lived credential to the E2B builder for the image import. No Docker Hub
credential is needed. Organization policy must permit the repository to create
GHCR packages; if the package already exists, grant this repository Actions access
in its package settings. See GitHub's
[Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

The workflow runs `scripts/e2b-template.py` against the checked-out commit. It builds
`Dockerfile.hosted` for Linux amd64, pushes a uniquely tagged image, imports its
immutable digest into `alk-hosted-<12-character commit SHA>`, and certifies the
capabilities in `hosted-snapshot/catalog.json` in a temporary sandbox. The existing
publisher defaults apply: 4 CPUs, 8192 MB RAM, and at least 10 GB verified disk.
The publisher cleans up its certification sandbox when the check finishes.

A successful run exposes the immutable `template-name:build-id` reference in the
job summary and saves `e2b-template-release.json` as a 90-day workflow artifact.
That file includes the source commit, image digest, resource sizes, and certification
results. A failed certification fails the job and produces no certified release
artifact; its image/template may already exist, so use only successful releases.
Download the artifact and set the platform's `ALK_E2B_TEMPLATE_REFERENCE` to its
`template_reference` when deploying. This workflow creates the templates; it does
not change platform deployment configuration or move a production alias.

Each commit has its own concurrency group, so a newer merge does not cancel or
replace an older commit's publication. Reruns of the same commit are serialized.
Reruns create a new image tag and E2B build; always use the immutable reference
from the desired run.
