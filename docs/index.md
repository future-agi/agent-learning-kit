---
kind: agent-learning.docs-page.v1
track: reference
backing: []
artifact_kinds: []
commands:
  - agent-learn doctor
postcondition: python -c "import fi.alk; print('ok')"
claims: []
doctor_checks:
  - public_boundary_passed
opt_in_lane: false
---

# Agent Learning Kit Documentation

> A common framework for simulation environments, with an OSS reference harness
> and included evaluation/optimization libraries. Start with an offline fixture;
> choose model-backed authoring or live execution explicitly.

## Start here: install to evidence

1. **Install and get a first result:** the [README quickstart](../README.md#first-success-deterministic-and-credential-free)
   runs the checked-in echo eval suite, scripted run and SDK echo example. The
   wheel does not contain checkout-only examples or the harness UI. For a
   package-only scaffold use [Golden path: run](quickstart/golden-path-run.md).
2. **Inspect and evaluate:** [Evaluation guide](eval/overview.md) distinguishes
   suite results, artifact/task evidence, local metrics, and model/platform modes.
3. **Create world/data/scenarios from source:** [Environment authoring](simulate/environment-authoring.md)
   is an opt-in model-backed workflow, with artifact/proof and execution boundaries.
4. **Choose execution:** use the [reference harness](../src/fi/alk/harness/README.md),
   [Spec + Runner](simulate/spec-and-runner.md), or a supported
   [extension seam](simulate/environment-authoring.md#supported-extension-seams).
   Production need not use the reference executor; arbitrary whole-executor
   substitution and V1/V2 bundle interchange are not established contracts.
5. **Improve and keep evidence:** follow optimization and regression guides below.

## Included library map

| Need | Documentation |
| --- | --- |
| Public facades, included engines, dependencies and TypeScript boundaries | [Library inventory](../LIBRARIES.md) |
| `fi.alk.simulate` / `fi.simulate` runtime | [Spec + Runner](simulate/spec-and-runner.md) · [extension contracts](simulate/environment-authoring.md#supported-extension-seams) |
| `fi.alk.evals` / `fi.evals` | [Evaluation guide](eval/overview.md) |
| `fi.alk.optimize` / `fi.opt`, loss and practice | [Optimization](optimize/optimization-lifecycle.md) · [loss](eval/overview.md#loss-practice-and-benchmark-validators) · [practice](prove/practice-loop.md) |
| Harness and Studio | [Environment authoring](simulate/environment-authoring.md) · [personas/scenarios](simulate/persona-scenario-studio.md) |
| Benchmarks | [Benchmark overview](eval/benchmark-overview.md) |
| Episodes and telemetry | [Episode API source](../src/fi/alk/episode/__init__.py) · [run ledger](prove/run-ledger.md) · [account sync](prove/account-sync.md) |

## The spine

One loop, taught once:

```
simulate ──▶ evaluate ──▶ optimize ──▶ promote ──▶ prove
```

Each stage opens with a task guide; red-teaming is the fourth workflow, not a
fourth engine — it rides on the `simulate` and `evals` engines, so its pages
sit in the simulate and evaluate rows of the index below.

| Stage | Start here |
| --- | --- |
| Simulate | [Simulate any framework](simulate/simulate-any-framework.md) |
| Evaluate | [Evaluate any task](eval/evaluate-any-task.md) |
| Optimize | [Optimize any agent](optimize/optimize-any-agent.md) |
| Red-team | [Red-team anything](redteam/red-team-anything.md) |
| Promote · Prove | [Regression lifecycle](simulate/regression-lifecycle.md) · [Trinity suite](prove/trinity-suite.md) |

## Pick a cookbook (stage × objective)

Rows are the spine; columns are what you care about. This is the compact
mirror — use [cookbooks/index.md](cookbooks/index.md) for task navigation and
[llms.txt](llms.txt) for the generated, metadata-backed page index.

| | Behavior | Capability | Reliability | Safety |
| --- | --- | --- | --- | --- |
| **simulate** | [spec-and-runner](simulate/spec-and-runner.md) · [first-run](simulate/first-run.md) · [worlds-and-hooks](simulate/worlds-and-hooks.md) | [simulate-any-framework](simulate/simulate-any-framework.md) · 14 [framework pages](cookbooks/index.md#by-framework) | [multi-agent](simulate/multi-agent.md) · [orchestration](simulate/orchestration.md) | [first-campaign](redteam/first-campaign.md) · [stored-prompt-injection](redteam/stored-prompt-injection.md) |
| **evaluate** | [eval-suites](eval/eval-suites.md) · [artifact-evals](eval/artifact-evals.md) | [task-evidence](eval/task-evidence.md) | [judge-reliability](eval/judge-reliability.md) | [causal-attribution](redteam/causal-attribution.md) |
| **optimize** | [optimization-lifecycle](optimize/optimization-lifecycle.md) | [workflow-profile-matrix](optimize/workflow-profile-matrix.md) | [governance](optimize/governance.md) · [optimizer-portfolio](optimize/optimizer-portfolio.md) | [society-of-agents](optimize/society-of-agents.md) |
| **promote** | [regression-lifecycle](simulate/regression-lifecycle.md) | [promote-to-regression](redteam/promote-to-regression.md) | [attack-evolution-shrink](redteam/attack-evolution-shrink.md) | [promote-to-regression](redteam/promote-to-regression.md) |
| **prove** | [trinity-suite](prove/trinity-suite.md) | [capabilities](prove/capabilities.md) | [release-check-in-your-ci](prove/release-check-in-your-ci.md) | [trust-certificates](prove/trust-certificates.md) |

## Golden paths

First replayable artifact in three commands or fewer, offline — no API keys,
no provider accounts:

From an empty working directory, after installing the package:

```bash
export AGENT_LEARNING_SYNC=local
export AGENT_LEARNING_LEDGER_PATH="$PWD/artifacts/ledger"
agent-learn init . --preset run --quiet
agent-learn run manifests/run.json --output artifacts/run.json
python -c "import json; p=json.load(open('artifacts/run.json')); assert p['kind']=='agent-learning.run.v1'; assert p['exit_code']==0; print('ok')"
```

This validates the scaffolded scripted fixture, not source authoring or a live
agent. `doctor` reports package/import health, not provider readiness.

- [Golden path: run](quickstart/golden-path-run.md) — scaffold, simulate, verify the artifact.
- [Golden path: red-team](quickstart/golden-path-redteam.md) — scaffold, campaign, verify the findings.
- [Golden path: optimize](quickstart/golden-path-optimize.md) — scaffold, search, verify the lineage.
- [Golden path: CI](quickstart/golden-path-ci.md) — run → redteam → replay, the loop your pipeline keeps.

## Layer vocabulary

If you arrive from the agent-infrastructure literature (memory, skills/tools,
protocols, harness engineering), this maps that vocabulary onto kit surfaces:

| Layer | Kit pages |
| --- | --- |
| memory | [optimize/memory-targets](optimize/memory-targets.md) · [simulate/memory](simulate/memory.md) |
| skills / tools | [prove/actions](prove/actions.md) · [redteam/first-campaign](redteam/first-campaign.md) |
| protocol | [frameworks/mcp](frameworks/mcp.md) · [frameworks/a2a](frameworks/a2a.md) · [frameworks/openenv](frameworks/openenv.md) |
| harness | [environment authoring and seams](simulate/environment-authoring.md) · [reference harness](../src/fi/alk/harness/README.md) |

## How these docs stay honest

Page frontmatter records backing examples, artifact kinds, commands,
postconditions and opt-in lanes. Reference indexes can have no executable twin.
The `docs_executability` release gate checks metadata, backing coverage and
licensed claims. A passing fixture covers that fixture, not every optional
provider, authoring command or production integration discussed beside it.
Read each page's execution boundary and verify the revision/lane you use.
The artifact catalog is [reference/artifacts.md](reference/artifacts.md); release
validation is separate from the first-success path.

Framework coverage is lane-specific; start from the [framework guides](cookbooks/index.md#by-framework)
and distinguish probe evidence from real runtime execution:

> - Framework adapter probes (probe-promoted coverage) for LangChain, LangGraph,
>   LlamaIndex, AutoGen, CrewAI, LiveKit, Pipecat, Browser Use, MCP, A2A, and
>   custom orchestration objects.
> - Runtime-simulated coverage for PydanticAI (multi-framework runtime
>   simulation) and OpenAI Agents (handoff-transcript promotion).
