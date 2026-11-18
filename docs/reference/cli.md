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

# CLI reference

> **Twin:** none, reference page (`backing: []`). The dispatch below is read
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
| `doctor` | No required input | import/consolidation and config status, not live provider/runtime readiness | `agent-learning.doctor.v1` |
| `harness` | No required input | reference harness: `auto`, `author`, `understand`, `build`, `scenarios`, `run`, `simulate`, `live`, `environment`, `chat`; [authoring guide](../simulate/environment-authoring.md) | lane-specific session/bundle/results |
| `release-check` | `v1-check`, `release` | run the full local gate matrix | `agent-learning.release-check.v1` |
| `release-proof` | `v1-proof` | cut the seven-check release proof | `agent-learning.release-proof.v1` |
| `init` | None | scaffold manifests for a preset: `ci` (default), `run`, `redteam`, `optimize`, `all` | `agent-learning.init.v1` |
| `run` | None | execute a run manifest (simulation; `--no-eval` skips attached evals) | `agent-learning.run.v1` |
| `bench` | `benchmark` | run a benchmark suite through the unified harness (`--mode` push / artifact_in / pull; any modality), see [benchmark-overview](../eval/benchmark-overview.md) | `agent-learning.bench-result.v1` |
| `eval` | None | execute an eval suite | `agent-learning.eval.v1` |
| `eval-artifact` | `eval-report` | evaluate an already-saved artifact | `agent-learning.artifact-evaluation.v1` |
| `eval-task` | `eval-evidence`, `eval-task-evidence` | evaluate raw task evidence or a saved task-evidence artifact | `agent-learning.artifact-evaluation.v1` |
| `redteam` | None | run a red-team campaign manifest | `agent-learning.redteam.v1` |
| `redteam-corpus` | `redteam-corpus-hook`, `redteam-hook` | run a red-team corpus hook | `agent-learning.redteam.v1` |
| `optimize` | None | run an optimization manifest | `agent-learning.optimization.v1` |
| `optimize-eval` | None | optimize an eval suite | `agent-learning.eval-optimization.v1` |
| `optimize-suite` | None | optimize a suite | `agent-learning.suite-optimization.v1` |
| `suite` | None | run a combined multi-step suite | `agent-learning.suite.v1` |
| `baseline` | None | pin a regression baseline from a saved artifact | `agent-learning.baseline.v1` |
| `compare` | None | compare current output against a baseline | `agent-learning.compare.v1` |
| `replay` | None | re-execute a kept artifact deterministically | `agent-learning.replay.v1` |
| `report` | None | render a report over saved artifacts | `agent-learning.report.v1` |
| `promote-to-regression` | None | promote a finding into the regression set | `agent-learning.regression-promotion.v1` |
| `shrink` | `minimize`, `minimize-counterexample` | minimize an evolved attack to its smallest failing form | `agent-learning.attack-evolution-shrink.v1` |
| `actions` | `list-actions` | list available actions for an artifact | `agent-learning.actions.v1` |
| `action-run` | `run-action` | execute one action | `agent-learning.action-run.v1` |
| `action-optimize` | `optimize-actions`, `actions-optimize` | optimize over the actions surface | `agent-learning.suite-optimization.v1` |
| `trust` | `verify-trust`, `trust-cert`, `trust-certificate`, `certify` | verify a saved suite trust certificate for CI | (verification verdict) |
| `capabilities` | `capability-catalog`, `caps` | print the capability catalog, optionally over saved artifacts | (catalog output) |
| `persona` | None | persona studio: `create` / `validate` / `calibrate` / `admit` / `lint` / `list` / `import` / `pull`, see [persona-scenario-studio](../simulate/persona-scenario-studio.md) | per subcommand |
| `scenario` | None | scenario studio: `generate` / `synth` / `expand` / `coverage` / `list` | per subcommand |
| `simulation` | None | simulation contract family: `lift` / `validate` / `run`, see [simulation-contract](../prove/simulation-contract.md) | `agent-learning.simulation.v1` |
| `practice` | None | practice loop: `run` / `report` / `ladder` / `replay` / `ab` / `image` / `cua`, see [practice-loop](../prove/practice-loop.md) | per subcommand |
| `runs` | `ledger` (hidden) | local run ledger: `list` / `show` / `verify` / `sync` / `forget`, see [run-ledger](../prove/run-ledger.md) | `agent-learning.ledger-row.v1` |
| `simulate` | None | namespace passthrough to the regression-lifecycle subcommands | per subcommand |
| `eval-cli` | `fi` | passthrough to the vendored evaluation CLI | per subcommand |

### Execution profiles

The profile in the next table defines credentials and network behavior:

| Profile | Credentials and network behavior |
| --- | --- |
| Local | Uses local files and processes. It needs no provider credentials. Set `AGENT_LEARNING_SYNC=local` when the command records telemetry. |
| Manifest-defined | The input can select a target, model, hook, evaluator, optimizer, or deployment action. Each selected integration names its credentials and can use the network. `--dry-run` validates supported manifest commands without running their providers. |
| Harness authoring | Calls the backend selected by `ALK_HARNESS` and `ALK_HARNESS_MODEL`. It uses that backend's credentials and network route. Build, run, live, simulate, and environment commands can also start services or contact the target. |
| Account | Calls Future AGI with the configured account credentials. It sends the data named by the command. |
| Mixed | The subcommand or embedded action selects one of the profiles above. Read its `--help`, manifest, and action before execution. |

Every command accepts only the inputs shown by its own `--help`. `--output`,
`--junit`, `--sarif`, and `--markdown` write the named files when that command
offers them. Without an output flag, commands print their result or summary.

| Command family | Required input and files read | Files written and expected result | Profile |
| --- | --- | --- | --- |
| `doctor` | None | Optional doctor JSON; import and configuration status | Local |
| `release-check` | Optional project root | Optional `agent-learning.release-check.v1` JSON | Local |
| `release-proof` | Optional project root and check selection | Optional `agent-learning.release-proof.v1` JSON plus the selected local check outputs | Local |
| `init` | Optional destination directory | Scaffolded manifests and optional `agent-learning.init.v1` JSON | Local |
| `run`, `redteam`, `optimize` | One manifest | Versioned run, campaign, or optimization artifact and selected report formats | Manifest-defined |
| `eval`, `optimize-eval`, `optimize-suite`, `suite` | One suite file | Versioned evaluation or optimization artifact and selected report formats | Manifest-defined |
| `eval-artifact` | One saved result artifact; optional evaluation config | `agent-learning.artifact-evaluation.v1` and selected report formats | Manifest-defined. Built-in report metrics can remain local. |
| `eval-task` | One task-evidence file; optional config or hook URL | `agent-learning.artifact-evaluation.v1` and selected report formats | Local without `--eval-hook`; Manifest-defined with a hook |
| `redteam-corpus` | Either a local corpus file or a hook URL | `agent-learning.redteam.v1` campaign evidence | Local for `--corpus`; Manifest-defined for `--hook` |
| `bench` | Benchmark suite plus mode-specific agent, submission, or artifact input | `agent-learning.bench-result.v1` | Mixed. A subprocess or Docker grader executes code. Pull mode can contact an agent. |
| `baseline`, `compare`, `report`, `promote-to-regression`, `shrink` | Saved result artifacts; some commands accept a source manifest | Versioned regression, comparison, report, promotion, or shrink artifact | Local unless the embedded manifest or action invokes an integration |
| `replay` | One or more manifests, directories, or globs | `agent-learning.replay.v1` and child results | Manifest-defined because replay re-executes the inputs |
| `actions`, `trust`, `capabilities` | Saved artifact files; `capabilities` permits none | Action catalog, trust verdict, or capability catalog | Local |
| `action-run`, `action-optimize` | Saved artifact plus action IDs and action-specific input | Action or optimization artifact plus embedded command outputs | Mixed. Embedded actions can execute code or use the network. |
| `persona` | A studio subcommand and its files or identifiers | Persona records, validation, calibration, admission, import, or pull result | Mixed. Local authoring is local; platform generation and pull use Account. |
| `scenario` | A studio subcommand and its files | Generated scenarios, coverage, or list output | Mixed. Inspect the selected generator before execution. |
| `simulation` | `validate`, `lift`, or `run` plus that subcommand's manifest | Validation, lifted manifest, or `agent-learning.simulation.v1` result | Local for validate and lift; Manifest-defined for run |
| `practice` | A practice subcommand and its manifest or store | Practice, report, ladder, replay, experiment, image, or CUA result | Manifest-defined |
| `runs` | A ledger subcommand and optional run identifier | Ledger rows, verification, sync receipt, or local tombstone | Local for list, show, verify, and forget. Account for sync. `forget` does not erase content that a previous sync sent to the account; request account-side erasure separately. |
| `simulate` | A simulation namespace subcommand and its manifest or artifact | The same result as the corresponding top-level lifecycle command | Same profile as the selected subcommand |
| `eval-cli` | A vendored evaluation CLI subcommand | Subcommand-specific evaluation output | Mixed. Template and service commands can use Future AGI credentials and the network. |
| `harness` | A harness subcommand, source path, session directory, or bundle as documented by that subcommand | Contracts, world data, scenarios, bundles, transcripts, and run evidence | Harness authoring for authoring stages. Run and environment operations also use the selected runtime and target. |

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
| `<command> could not import the vendored Agent Learning Kit engine` | infra, broken install; reinstall `agent-learning-kit` | `agent-learn doctor` → `summary.missing_engine_modules` |
| `unknown command: <name>` | config fault, alias typo; check the Aliases column | n/a (help text lists the surface) |
| command exits asking for an API key | config fault, platform-backed step without a key | `summary.api_key_configured`; see [configure](configure.md) |

## 5. Prove it / keep it

The release gate pins this surface: `V1_REQUIRED_CLI_COMMANDS` in
`src/fi/alk/trinity.py` lists the commands `release-check` requires,
so a removed or renamed command is a red gate, not a silent doc rot. Continue
with [prove/release-check-in-your-ci.md](../prove/release-check-in-your-ci.md)
to run that check in your pipeline, or start producing artifacts with the
[run golden path](../quickstart/golden-path-run.md).
