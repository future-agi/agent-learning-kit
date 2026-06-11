# Release Readiness Research

Date: 2026-06-10

Scope: repository documents and metadata needed before opening or publishing the
Agent Learning Kit v1 release candidate.

## Sources Checked

- GitHub community profile guidance:
  https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/about-community-profiles-for-public-repositories
- GitHub code of conduct guidance:
  https://docs.github.com/en/communities/setting-up-your-project-for-healthy-contributions/adding-a-code-of-conduct-to-your-project
- Apache License 2.0 application guidance:
  https://www.apache.org/licenses/LICENSE-2.0
- Apache legal guidance for applying ALv2:
  https://www.apache.org/legal/apply-license.html
- Python Packaging User Guide for `pyproject.toml`:
  https://packaging.python.org/en/latest/guides/writing-pyproject-toml/
- npm `package.json` publishing metadata:
  https://docs.npmjs.com/cli/v11/configuring-npm/package-json/
- SPDX Apache-2.0 identifier:
  https://spdx.org/licenses/Apache-2.0.html

## Applied Decisions

- Add a root `LICENSE` containing the Apache License 2.0 text.
- Add a root `NOTICE` so the source distribution has project attribution
  metadata alongside the license.
- Add `Apache-2.0` license metadata to TypeScript package manifests and package
  local `LICENSE`/`NOTICE` artifacts for npm.
- Align `typescript/agent-learning-kit/README.md` with the Apache-2.0 license
  and clarify that the TypeScript v1 package is evaluation-focused.
- Add `Issues` and `Changelog` project URLs in `pyproject.toml`.
- Add explicit Python `license-files` metadata for `LICENSE` and `NOTICE`.
- Add `CHANGELOG.md` so PyPI and GitHub have a release-history target.
- Use Future AGI mark and reliability blueprint assets under `docs/assets/`
  for README branding.
- Move the long README cookbook/deep-dive material into
  `internal-docs/agent-learning-kit-readme-deep-dive.md` so the public README
  stays focused on install, quickstart, release proof, and contribution paths.
- Keep package version/classifier decisions explicit in
  `internal-docs/v1-release-candidate-notes.md`; do not silently convert the
  release tag into semver `1.0.0`.
- Add `CONTRIBUTING.md`, `SECURITY.md`, and `CODE_OF_CONDUCT.md` at the root so
  GitHub and developers can find them without repository-specific knowledge.
- Add GitHub PR and issue templates under `.github/` for a cleaner public
  contribution path.
- Rewrite the README opening around install, quickstart, workflows, release
  proof, repository map, and community links, while preserving the detailed
  cookbook-heavy remainder.

## Remaining Owner Decisions

- Choose whether v1 is a release tag only or package semver `1.0.0`.
- Choose whether Python should keep `Development Status :: 3 - Alpha` for the
  first public v1 tag.
- Choose whether `uv.lock` is tracked, ignored, or removed before publishing.
- Confirm the vulnerability reporting address before making the repository
  public.
