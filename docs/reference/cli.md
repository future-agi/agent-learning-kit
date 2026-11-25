---
kind: agent-learning.docs-page.v1
track: reference
backing: []
artifact_kinds: []
commands:
  - agent-learn doctor
postcondition: python -c "from fi.alk import cli; assert callable(cli.main); print('ok')"
claims: []
doctor_checks:
  - missing_public_modules
opt_in_lane: false
---

# CLI Reference

> **Twin:** none — reference page (`backing: []`). The dispatch below is read
> directly from `main()` in `src/fi/alk/cli.py`.

## 1. What you are testing

`agent-learn` and `alk` are aliases over the included engines and reference
harness. Manifest commands such as `run` and `eval` accept JSON/YAML and output
flags; other families have their own contracts. Harness stages use `--out`
for a session directory, not `--output` for one result file. See the
[artifact catalog](artifacts.md). Running `agent-learn` with no arguments prints
help; an unknown command exits non-zero. Bare `agent-learn harness`, however,
opens interactive chat. Use `agent-learn harness --help` for help.

There is no declared `agent-harness` console script; it is a harness parser
label. `alk-harness-worker` and `alk-harness-sandbox` are separate hosted entry
points, not the beginner CLI.

The regression-lifecycle subcommands (`baseline`, `compare`, `replay`,
`report`, `promote-to-regression`, `shrink`) are also reachable through the
`simulate` namespace; the top-level spellings below are the documented surface.

## 2. Run it

```bash
agent-learn doctor
```

```python
from fi.alk import trinity
payload = trinity.trinity_status()
print(payload["summary"]["public_boundary_passed"])
```

## 3. What you built

```bash
python -c "from fi.alk import cli; assert callable(cli.main); print('ok')"
```

The command surface, one row per command (aliases from the `main()` dispatch):

| Command | Aliases | Does | Artifact kind |
| --- | --- | --- | --- |
| `doctor` | — | import/consolidation and config status, not live provider/runtime readiness | `agent-learning.doctor.v1` |
| `harness` | — | reference harness: `auto`, `author`, `understand`, `build`, `scenarios`, `run`, `simulate`, `live`, `environment`, `chat`; [authoring guide](../simulate/environment-authoring.md) | lane-specific session/bundle/results |
| `release-check` | `v1-check`, `release` | run the full local gate matrix | `agent-learning.release-check.v1` |
| `release-proof` | `v1-proof` | cut the seven-check release proof | `agent-learning.release-proof.v1` |
| `init` | — | scaffold manifests for a preset: `ci` (default), `run`, `redteam`, `optimize`, `all` | `agent-learning.init.v1` |
| `run` | — | execute a run manifest (simulation; `--no-eval` skips attached evals) | `agent-learning.run.v1` |
| `bench` | `benchmark` | run a benchmark suite through the unified harness (`--mode` push / artifact_in / pull; any modality) — see [benchmark-overview](../eval/benchmark-overview.md) | `agent-learning.bench-result.v1` |
| `eval` | — | execute an eval suite | `agent-learning.eval.v1` |
| `eval-artifact` | `eval-report` | evaluate an already-saved artifact | `agent-learning.artifact-evaluation.v1` |
| `eval-task` | `eval-evidence`, `eval-task-evidence` | synthesize task evidence from task artifacts | `agent-learning.task-evidence.v1` |
| `redteam` | — | run a red-team campaign manifest | `agent-learning.redteam.v1` |
| `redteam-corpus` | `redteam-corpus-hook`, `redteam-hook` | run a red-team corpus hook | `agent-learning.redteam.v1` |
| `optimize` | — | run an optimization manifest | `agent-learning.optimization.v1` |
| `optimize-eval` | — | optimize an eval suite | `agent-learning.eval-optimization.v1` |
| `optimize-suite` | — | optimize a suite | `agent-learning.suite-optimization.v1` |
| `suite` | — | run a combined multi-step suite | `agent-learning.suite.v1` |
| `baseline` | — | pin a regression baseline from a saved artifact | `agent-learning.baseline.v1` |
| `compare` | — | compare current output against a baseline | `agent-learning.compare.v1` |
| `replay` | — | re-execute a kept artifact deterministically | `agent-learning.replay.v1` |
| `report` | — | render a report over saved artifacts | `agent-learning.report.v1` |
| `promote-to-regression` | — | promote a finding into the regression set | `agent-learning.regression-promotion.v1` |
| `shrink` | `minimize`, `minimize-counterexample` | minimize an evolved attack to its smallest failing form | `agent-learning.attack-evolution-shrink.v1` |
| `actions` | `list-actions` | list available actions for an artifact | `agent-learning.actions.v1` |
| `action-run` | `run-action` | execute one action | `agent-learning.action-run.v1` |
| `action-optimize` | `optimize-actions`, `actions-optimize` | optimize over the actions surface | `agent-learning.suite-optimization.v1` |
| `trust` | `verify-trust`, `trust-cert`, `trust-certificate`, `certify` | verify a saved suite trust certificate for CI | (verification verdict) |
| `capabilities` | `capability-catalog`, `caps` | print the capability catalog, optionally over saved artifacts | (catalog output) |
| `persona` | — | persona studio: `create` / `validate` / `calibrate` / `admit` / `lint` / `list` / `import` / `pull` — see [persona-scenario-studio](../simulate/persona-scenario-studio.md) | per subcommand |
| `scenario` | — | scenario studio: `synth` / `expand` / `coverage` / `list` | per subcommand |
| `simulation` | — | simulation contract family: `lift` / `validate` / `run` — see [simulation-contract](../prove/simulation-contract.md) | `agent-learning.simulation.v1` |
| `practice` | — | practice loop: `run` / `report` / `ladder` / `replay` / `ab` / `image` / `cua` — see [practice-loop](../prove/practice-loop.md) | per subcommand |
| `runs` | `ledger` (hidden) | local run ledger: `list` / `show` / `verify` / `sync` / `forget` — see [run-ledger](../prove/run-ledger.md) | `agent-learning.ledger-row.v1` |
| `simulate` | — | namespace passthrough to the regression-lifecycle subcommands | per subcommand |
| `eval-cli` | `fi` | passthrough to the vendored evaluation CLI | per subcommand |

### First-mile options

| Command | Relevant options / boundary |
| --- | --- |
| `doctor` | `--output`, `--quiet`; import/consolidation checks only. |
| `init DIRECTORY` | `--preset run\|ci\|redteam\|optimize\|all`, `--name`, repeatable `--required-env`, `--force`, `--quiet`; no required env by default. |
| `eval SUITE` | `--dry-run`, `--threshold`, `--output`, `--junit`, `--sarif`, `--markdown`; dry run does not execute providers. |
| `run MANIFEST` | `--dry-run`, `--no-eval`, output/report flags; skipping evaluation does not skip target execution. |
| `harness auto` / `author` | `--path`, `--name`, `--out`, `--count` (default 10); authoring calls models even without the final run. |
| `harness understand/build/scenarios` | `--once` makes a stage non-interactive. Use the same `--out` to continue staged work. |
| `harness run` | `--target`, `--only`, `--model`, `--quiet`; target and runtime must be compatible. |
| `harness environment up/status/reset/down` | `--out`; start from `--path` or a compatible V1 `--bundle`. Reset/down change runtime state. |

Implementation: [`fi.alk.cli`](../../src/fi/alk/cli.py),
[`harness/cli.py`](../../src/fi/alk/harness/cli.py). See
[environment authoring](../simulate/environment-authoring.md) for prerequisites
and exact staged commands, not proposed roadmap commands.

## 4. When it fails

| Symptom | First-mile class | Doctor check |
| --- | --- | --- |
| `<command> could not import the vendored Agent Learning Kit engine` | infra — broken install; reinstall `agent-learning-kit` | `agent-learn doctor` → `summary.missing_engine_modules` |
| `unknown command: <name>` | config fault — alias typo; check the Aliases column | n/a (help text lists the surface) |
| command exits asking for an API key | config fault — platform-backed step without a key | `summary.api_key_configured`; see [configure](configure.md) |

## 5. Prove it / keep it

The release gate pins this surface: `V1_REQUIRED_CLI_COMMANDS` in
`src/fi/alk/trinity.py` lists the commands `release-check` requires,
so a removed or renamed command is a red gate, not a silent doc rot. Continue
with [prove/release-check-in-your-ci.md](../prove/release-check-in-your-ci.md)
to run that check in your pipeline, or start producing artifacts with the
[run golden path](../quickstart/golden-path-run.md).
