# Agent Learning Kit V1 Release Roadmap

This roadmap is the release checklist for `agent-learning-kit` V1. The goal is
one SDK and one CLI for simulating, evaluating, red-teaming, optimizing, and
replaying agent behavior with Future AGI as the UI/UX and observability layer.

The default path is local-first. Research informs the contracts, but V1 should
not depend on hosted optimizer/eval competitors. External services are allowed
only when they are the user workload being simulated or a necessary transport.

## V1 Definition

V1 is releasable when a user can:

1. Install one package: `agent-learning-kit`.
2. Configure one key: `AGENT_LEARNING_API_KEY`.
3. Use one CLI: `agent-learn`.
4. Run local simulation, eval, red-team, optimization, report, replay, and
   regression promotion workflows.
5. Use the Python SDK through `agent_learning.{simulate,evals,redteam,optimize,suite}`.
6. Export artifacts that Future AGI can render as UI/UX, observability, eval,
   simulation, red-team, and optimizer results.
7. Run a promptfoo-style CLI workflow without writing platform code.
8. Verify release readiness with `agent-learn doctor` and
   `agent-learn release-check`.

## Milestones

Each milestone has a small release gate. `agent-learn release-check --project-root .`
is the V1 source of truth for these gates, including research-backed red-team
coverage across required examples, canonical corpus rows, attack types,
surfaces, and source lineage.

### M0: SDK Consolidation Boundary

Status: mostly complete.

Acceptance gates:

- Public distribution is `agent-learning-kit`.
- Public import namespace is `agent_learning`.
- Public CLI is `agent-learn`.
- `simulate`, `evals`, `redteam`, `optimize`, `suite`, and `capabilities` are
  importable through `agent_learning`.
- `fi.simulate`, `fi.evals`, and `fi.opt` remain vendored engine internals.
- No new public work lands in `simulate-sdk`, `ai-evaluation`, or `agent-opt`.

Verification:

- `agent-learn doctor`
- `PYTHONPATH=src python -m pytest tests/test_config_and_facades.py::test_agent_learn_doctor_reports_module_availability -q`

### M1: Promptfoo-Style CLI

Status: mostly complete.

Acceptance gates:

- `agent-learn run`, `eval`, `eval-artifact`, `eval-task`, `redteam`,
  `redteam-corpus`, `optimize`, `optimize-eval`, `optimize-suite`, `suite`,
  `report`, `replay`, `promote-to-regression`, `actions`, `action-run`,
  `action-optimize`, `trust`, `capabilities`, `doctor`, `release-check`, and
  `init` are available.
- Each command writes JSON output; core workflows also write JUnit, SARIF, and
  Markdown where supported.
- CLI examples use `agent-learning-kit` and `agent-learn`, not legacy SDK names.

Verification:

- `PYTHONPATH=src python -m pytest tests/test_cli_examples.py -q`

### M2: Local Simulation And Evaluation

Status: mostly complete.

Acceptance gates:

- `agent-learning.run.v1`, `agent-learning.eval.v1`, and
  `agent-learning.artifact-evaluation.v1` artifacts are stable.
- Simulation supports task worlds, framework traces, lifecycle traces, memory,
  orchestration, browser/CUA, voice/realtime, workspace import, agent integration,
  and framework certification fixtures.
- Evals score task completion, tools, world contracts, red-team readiness,
  framework traces/lifecycle/capability/probe/portability, memory lineage,
  optimizer traces, behavior entropy, and artifact actions.

Verification:

- Full local suite: `PYTHONPATH=src python -m pytest -q`

### M3: AgentOptimizer And Native Evidence Scoring

Status: mostly complete.

Acceptance gates:

- Optimization runs through `agent-learn optimize`, `optimize-eval`,
  `optimize-suite`, and `agent_learning.optimize`.
- `optimize.score_simulation_evidence()` emits local components for tool
  coverage, framework trace, framework lifecycle, framework import,
  agent integration, red-team readiness/campaign, stateful tool worlds, world
  hooks, world contracts, orchestration replay, memory lineage,
  harness-trajectory replay, optimizer governance, and optimizer portfolio.
- Optimizer proofs are deterministic and based on local report/environment
  evidence, not hosted competitor calls.

Verification:

- `PYTHONPATH=src python -m pytest tests/test_config_and_facades.py -q`
- `PYTHONPATH=src python -m pytest tests/test_cli_examples.py -q`

### M4: World-Best Red-Team Core

Status: in progress.

Acceptance gates:

- Red-team runs cover prompt injection, tool misuse, policy bypass,
  persistent-state attacks, memory poisoning, multi-agent takeover,
  control-plane failures, and autonomous task-world attacks.
- Campaign outputs include attack taxonomy, surface/channel/provider coverage,
  executed run artifacts, findings, mitigations, and regression promotion.
- Red-team optimization can evolve attack packs and mitigation candidates with
  local replay evidence.
- Corpus imports use exact required benchmark cells by default, while explicit
  campaign dimensions can still request exhaustive cross-product coverage.
- `agent-learn release-check` gates the required red-team corpus/campaign
  examples plus corpus-only and broader research-backed attack types, attack
  surfaces, and source URLs.

Next implementation focus:

- Keep the required local red-team corpus, campaign, attack-evolution,
  long-horizon, persistent-state, society, causal-attribution, and autonomous
  task-world examples present and passing.
- Add native fixtures only when release-check exposes a concrete coverage gap.

### M5: Future AGI UI/UX Artifact Contract

Status: in progress.

Acceptance gates:

- Every major artifact includes `kind`, `schema_version`, `status`, `summary`,
  `actions`, `outputs_written`, and renderable report payloads where applicable.
- `agent-learn report` and `agent-learn actions` expose UI-ready cards and
  executable actions for simulation, eval, red-team, optimization, replay,
  promotion, and downloads.
- Artifacts are safe to send to Future AGI for observability/evals/simulation UI
  without leaking local keys.

Next implementation focus:

- Add release-check gates for report/action readiness across representative V1
  artifacts.

### M6: Framework/Provider Simulation Surface

Status: in progress.

Acceptance gates:

- Framework certification covers lifecycle, capability, probe, and portability.
- Provider/transport simulation distinguishes agent platform, transport,
  simulator STT/TTS, system engine, and chat engine roles.
- LiveKit/WebRTC/SIP/phone, Retell, ElevenLabs, Deepgram, Agora, Pipecat, and
  Twilio are represented as local definitions, contracts, or transport/provider
  adapters where appropriate.
- External calls are only made when the user is explicitly testing that external
  target with real keys.

Next implementation focus:

- Keep new provider work behind local contracts/tests first.
- Avoid adding hosted optimizer/eval dependencies.

### M7: Release Packaging And Proof

Status: in progress.

Current checkpoint:

- Full-repo `PYTHONPATH=src python -m ruff check .` now passes across the
  public SDK plus vendored `fi.{simulate,evals,opt}` engine tree.

Acceptance gates:

- `python -m build` succeeds.
- `agent-learn release-check --project-root .` passes.
- Full pytest passes with real local keys used by examples/tests.
- Ruff and `git diff --check` pass.
- README, development boundary, roadmap, and examples are aligned.
- Version/classifier are intentionally set for the V1 release.

Verification:

- `PYTHONPATH=src python -m ruff check .`
- `PYTHONPATH=src python -m pytest -q`
- `python -m build`
- `git diff --check`

## Current Implementation Order

1. Add `agent-learn release-check` and keep it passing.
2. Use release-check failures to drive V1 work.
3. Keep research-backed red-team campaign/corpus proof coverage gated.
4. Tighten Future AGI UI/action/report artifact gates.
5. Finish provider/framework simulation contracts that are local-first and
   verified with real user-provided target keys only where necessary.
6. Cut V1 only after the release-check, full tests, package build, and artifact
   redaction checks all pass.
