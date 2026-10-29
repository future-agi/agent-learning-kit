---
kind: agent-learning.docs-page.v1
track: reference
backing: []
artifact_kinds: []
commands:
  - agent-learn doctor --boundary
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

`agent-learn` is one console script over the three vendored engines
(`simulate`, `evals`, `optimize`). Every evidence-producing command takes a
manifest (JSON or YAML) and an `--output` path, and writes a single JSON
artifact whose `kind` identifies it — see
[reference/artifacts.md](artifacts.md). Running `agent-learn` with no
arguments prints help; an unknown command prints help plus
`unknown command: <name>` and exits non-zero.

The regression-lifecycle subcommands (`baseline`, `compare`, `replay`,
`report`, `promote-to-regression`, `shrink`) are also reachable through the
`simulate` namespace; the top-level spellings below are the documented surface.

## 2. Run it

```bash
agent-learn doctor --boundary
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
| `doctor` | — | platform checks: signed in, workspace resolved, platform reachable (`--boundary`: the kit's module-boundary diagnostics, `--write PATH` for the JSON) | (checks; `--boundary`: `agent-learning.doctor.v1`) |
| `auth` | — | `login` / `status` / `logout` against a FutureAGI platform | (platform output) |
| `profile` | — | saved profiles: `list` / `show` / `use` / `set NAME` | (platform output) |
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

### Platform commands

`auth`, `profile` and `doctor` talk to a FutureAGI platform through
`fi.alk.platform`, the same client Python code uses:

```python
from fi.alk.platform import Client
with Client.from_profile() as client:   # or Client(api_url=..., api_key=..., secret_key=...)
    print(client.whoami().workspace)
```

`agent-learn auth login` takes an API key and a secret key from the platform's
Keys page, checks them, and saves them to a profile. In a terminal it prompts;
otherwise, or with `--method token-env`, it reads `FI_API_KEY` and `FI_SECRET_KEY`
(other names with `--token-env NAME` / `--secret-env NAME`). The profile's
platform URL, workspace and run defaults go to `$AGENT_LEARNING_HOME/profiles.json`
(default `~/.agent-learning`); the key pair goes to the OS keychain, or, without
one, to `credentials.json` there, readable only by you. Only a key's last four
characters are ever shown. `auth logout` erases the current profile's key pair
and keeps its settings; `--all-profiles` signs out of every profile after
confirmation (`--yes`). `profile set NAME` creates or updates a profile from
`--api-url`, `--workspace`, `--default-mode`, `--default-budget` and
`--default-budget-unit`, and prints what changed.

Every platform command accepts these options after its name:

| Option | Default | Effect |
| --- | --- | --- |
| `--profile NAME` | the current profile | the saved profile to use |
| `--workspace ID` | the profile's workspace | the workspace to act in |
| `--api-url URL` | the profile's URL | the platform to talk to |
| `--request-timeout SECONDS` | 30 | timeout for one platform request |
| `--output human\|json\|jsonl` | human in a terminal, json otherwise | result format |
| `--non-interactive` | off | never prompt; missing input is an error |
| `--yes` | off | accept the shown confirmation |

With `json` or `jsonl` the result, or the error, is one JSON document on stdout.
An error is always `{"error": {"category", "code", "message", "field",
"retryable", "request_id", "fix"}}`. `request_id` is the `X-Request-ID` the
client sent, under which the platform logged the request. The exit code is the
category's:

| Exit code | Category | For example |
| --- | --- | --- |
| 0 | — | success |
| 2 | `validation` | invalid arguments or input; a missing key pair; a workspace the key cannot act in; a retry key reused for a different request |
| 3 | `auth`, `permission` | a bad key pair; not signed in; no access to the workspace |
| 4 | `allowance` | the plan's limit or credit is used up |
| 5 | `infrastructure` | the platform is unreachable or unavailable (`retryable: true`) |

A workspace the platform cannot use is refused, never silently replaced by its
default one. Requests that create something carry a retry key (generated per
submission, or given): when an answer is lost they are sent again with the same
key, so the platform returns the original instead of doing it twice.

## 4. When it fails

| Symptom | First-mile class | Doctor check |
| --- | --- | --- |
| `<command> could not import the vendored Agent Learning Kit engine` | infra — broken install; reinstall `agent-learning-kit` | `agent-learn doctor --boundary` → `summary.missing_engine_modules` |
| `unknown command: <name>` | config fault — alias typo; check the Aliases column | n/a (help text lists the surface) |
| command exits asking for an API key | config fault — platform-backed step without a key | `summary.api_key_configured`; see [configure](configure.md) |

## 5. Prove it / keep it

The release gate pins this surface: `V1_REQUIRED_CLI_COMMANDS` in
`src/fi/alk/trinity.py` lists the commands `release-check` requires,
so a removed or renamed command is a red gate, not a silent doc rot. Continue
with [prove/release-check-in-your-ci.md](../prove/release-check-in-your-ci.md)
to run that check in your pipeline, or start producing artifacts with the
[run golden path](../quickstart/golden-path-run.md).
