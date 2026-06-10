# V1 Engineering Handover

## Start Here

Hand this document to the next engineer as the entry point.

Current handoff snapshot:

- Date: 2026-06-10.
- Branch observed during handoff: `main`.
- Latest verified baseline before the metric slice:
  `34373fd Gate adapter probe signature IO contracts`.
- Local worktree status observed during handoff: clean except unrelated
  untracked `uv.lock`.
- Full v1 is not done. Do not communicate v1 completion from this handoff.
- Latest verified functional slice: promoted BYO-framework runs now close
  first-class adapter call-contract and observed-I/O evaluation metrics.

First commands for a new engineer:

```bash
git status --short
git log --oneline -8
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
```

If the engineer needs the full release-cut proof, run:

```bash
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof.json \
  --quiet
```

## Current Status

This repository is moving toward the v1 goal of making the Agent Learning
trinity usable for arbitrary agent work:

- `agent-opt`: optimize more than prompts, including worlds, framework
  adapters, orchestration targets, memory layers, multi-agent rooms, and
  workflow/retrieval hooks.
- `simulate-sdk`: simulate local or wrapped framework runtimes without relying
  on hosted services.
- `ai-evaluation`: score task behavior, runtime contracts, trace evidence,
  memory/retrieval quality, robustness, and release readiness.

The full v1 objective is not complete. The current verified state is a strong
increment toward it: framework adapter probes now prove deterministic callable
signatures and observed input/output contracts before an arbitrary local
framework adapter is promoted, and promoted run manifests now score those
contracts as first-class `ai-evaluation` metrics.

## Latest Completed Slices

The latest slice hardens promoted BYO-framework adapter evaluation.

Implemented behavior:

- `AgentReportEvalConfig` now supports:
  - `framework_adapter_call_contract_quality`
  - `framework_adapter_observed_io_quality`
- `AgentReportEvaluator` extracts adapter call contracts from framework runtime
  invocations and scores method, input mode, call style, callable signature,
  signature binding, input/output types, output tools/events/artifacts, content
  observation, and error count.
- `build_framework_adapter_probe_evaluation_config()` emits both metric config
  blocks and gives each an `8.0` metric weight for promoted adapter-probe runs.
- Generated promotion paths and one-call local-adapter helpers now require both
  metrics alongside `framework_runtime_contract` and
  `framework_adapter_contract_quality`.
- `agent-learn release-check` now requires both metrics at `1.0` for explicit
  probe promotion, auto-discovery promotion, one-call promotion, and one-call
  run surfaces.
- `capabilities.DEFAULT_METRICS` exposes both metric names.
- The explicit adapter-probe promotion cookbook has the same handwritten metric
  gates as the generated promotion path.

The previous slice hardened BYO-framework adapter probing.

Implemented behavior:

- `GenericAgentWrapper` runtime traces now include a per-invocation
  `agent-learning.framework-adapter-call-contract.v1`.
- The call contract records method, input mode, call style, selected input key,
  static callable signature, and observed input/output shape.
- `simulate.run_framework_adapter_probe()` attaches a deterministic
  `callable_signature` to the probe contract when the local callable can be
  inspected.
- Each probe case now includes an
  `agent-learning.framework-adapter-observed-io-contract.v1`.
- Probe summaries expose `call_contract_count`,
  `observed_io_contract_count`, `signature_bound_count`, `input_types`,
  `output_types`, `input_keys`, and `call_styles`.
- `optimize.optimize_framework_adapter_probe()` scoring includes
  `framework_adapter_probe_io_contract_quality`.
- The native adapter probe proof requires
  `framework_adapter_probe_signature_io_contract_closed`.
- `agent-learn report` now surfaces signature/I-O fields in the
  `framework_adapter_probe` card.
- `agent-learn actions` can export:
  - `export_framework_adapter_probe_callable_signature`
  - `export_framework_adapter_probe_observed_io_contract`
- `agent-learn release-check` fails closed if the adapter probe readiness gate
  loses signature/I-O evidence.
- The raw adapter-probe cookbook now uses keyword-only payload syntax:
  `async def execute_task(self, *, payload)`.

Important boundary:

- This keeps OpenEnv/Gymnasium as compatibility input shapes only.
- The product bar remains Agent Learning-native: local-first simulation,
  deterministic adapter contracts, optimizer proof, and evaluation gates.
- There is no direct OpenEnv package dependency in the current checked state.
- Keep repeating this check before release: OpenEnv compatibility is allowed,
  but OpenEnv should not become the core abstraction, dependency, or product
  positioning.

## Key Files

Runtime and simulation:

- `src/fi/simulate/agent/generic.py`
  - `GenericAgentWrapper.call`
  - `_framework_runtime_trace`
  - `_framework_callable_signature`
  - `_call_contract_signature_bound`
- `src/fi/simulate/agent/frameworks.py`
  - `probe_framework_adapter`
  - `_run_probe_case`
  - `_adapter_callable_signature`
  - `_probe_observed_io_contract`
  - `_probe_summary`

Optimization and proof:

- `src/agent_learning/optimize.py`
  - `optimize_framework_adapter_probe`
  - `score_framework_adapter_probe_result`
  - `_framework_adapter_probe_proof`

Report/action surfaces:

- `src/fi/simulate/cli.py`
  - `_framework_adapter_probe_card`
  - `_framework_adapter_probe_actions`
  - `_framework_adapter_probe_markdown`

Release gate:

- `src/agent_learning/trinity.py`
  - `V1_FRAMEWORK_ADAPTER_PROBE_CONTRACTS`
  - `V1_FRAMEWORK_ADAPTER_PROBE_REQUIRED_ACTIONS`
  - `_release_framework_adapter_probe_status`
  - `_framework_adapter_probe_record`
  - `_append_framework_adapter_probe_errors`

Cookbooks/docs/tests:

- `examples/sdk_framework_adapter_probe.py`
- `README.md`
- `V1_RELEASE_ROADMAP.md`
- `internal-docs/framework-adapter-probe-readiness-research.md`
- `tests/test_vendored_simulate_engine.py`
- `tests/test_cli_examples.py`
- `tests/test_config_and_facades.py`

## Verification Passed

Commands run and passed:

```bash
python3 -m py_compile \
  src/fi/simulate/agent/frameworks.py \
  src/fi/simulate/agent/generic.py \
  src/agent_learning/optimize.py \
  src/agent_learning/capabilities.py \
  src/fi/simulate/cli.py \
  src/agent_learning/trinity.py \
  tests/test_vendored_simulate_engine.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
```

```bash
uv run ruff check .
```

```bash
git diff --check
```

```bash
uv run pytest \
  tests/test_vendored_simulate_engine.py::test_framework_adapter_probe_runs_custom_framework_runtime \
  tests/test_vendored_simulate_engine.py::test_framework_adapter_probe_proves_keyword_only_signature_io_contract \
  tests/test_config_and_facades.py::test_optimize_framework_adapter_probe_selects_working_adapter \
  tests/test_config_and_facades.py::test_optimize_framework_adapter_probe_discovers_candidates_when_omitted \
  tests/test_cli_examples.py::test_sdk_framework_adapter_probe_example_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_probe_optimization_example_runs \
  -q
```

```bash
uv run pytest tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones -q
```

```bash
uv run pytest -q
```

Current full-suite result after the metric slice:

- `290 passed, 6 warnings in 725.32s`

```bash
uv run pytest -q
```

Result:

- `289 passed, 6 warnings in 895.79s`

```bash
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof-signature-io.json \
  --quiet
```

Release proof result:

- `exit_code: 0`
- `release_check=passed`
- `ruff=passed`
- `pytest=passed`
- `build=passed`
- `typescript_build=passed`
- `typescript_test=passed`
- `git_diff_check=passed`

Additional verification passed for the call-contract metric slice:

```bash
python3 -m py_compile \
  src/fi/evals/metrics/agents/report.py \
  src/agent_learning/optimize.py \
  src/agent_learning/capabilities.py \
  src/agent_learning/trinity.py
```

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_agent_report_scores_framework_adapter_call_contract_and_observed_io \
  -q
```

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_probe_optimization_promotes_to_framework_run_manifest \
  tests/test_config_and_facades.py::test_auto_discovery_probe_optimization_promotes_discovery_metadata \
  tests/test_config_and_facades.py::test_build_framework_run_manifest_from_local_adapter_optimizes_and_promotes \
  tests/test_config_and_facades.py::test_run_framework_adapter_from_local_adapter_optimizes_promotes_and_runs \
  -q
```

```bash
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_probe_promotion_example_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_auto_discovery_promotion_example_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_one_call_promotion_example_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_one_call_run_example_runs \
  -q
```

```bash
uv run pytest tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones -q
```

## Deterministic Adapter-Probe Workflow

Use this workflow for new framework adapter surfaces:

1. Build or wrap a local framework object.
2. Run `simulate.discover_framework_adapter()` if method/input shape is not
   known.
3. Run `simulate.run_framework_adapter_probe()` for one explicit adapter shape.
4. Verify the probe has:
   - `status == "passed"`
   - `summary.runtime_trace_count >= case_count`
   - `summary.call_contract_count >= case_count`
   - `summary.observed_io_contract_count >= case_count`
   - `summary.signature_bound_count >= case_count`
   - `contract.callable_signature.inspectable is True`
5. Run `optimize.optimize_framework_adapter_probe()` across candidate adapter
   specs.
6. Require proof check:
   `framework_adapter_probe_signature_io_contract_closed`.
7. Render report/actions:
   - `agent-learn report <result.json>`
   - `agent-learn actions <result.json>`
8. Export proof and contracts through actions before promotion.
9. Promote with `build_framework_run_manifest_from_probe_optimization()` or
   the one-call local-adapter helpers.
10. Run release-check before claiming readiness.

## What Engineering Should Do Next

Recommended next slices, in order:

1. Expand arbitrary-framework coverage.
   - Add focused cookbooks for LangChain/LangGraph-style `invoke/ainvoke`.
   - Add LiveKit/Pipecat-style voice/frame adapters.
   - Add Browser Use / CUA adapters.
   - Add provider-client adapters with nested method paths such as
     `chat.completions.create`.

2. Move from method/input optimization to task/world optimization.
   - The broader v1 goal is not just adapter choice.
   - `agent-opt` should optimize environment state, workflow hooks, memory
     policies, retrieval configs, multi-agent role boundaries, and red-team
     scenarios.

3. Generalize call-contract metrics beyond adapter-probe promotion.
   - Apply the same metric family to other framework manifest builders where
     runtime traces already carry call contracts.
   - Keep the metrics separate from generic `framework_adapter_contract_quality`.

4. Keep OpenEnv compatibility as a secondary surface.
   - Do not make OpenEnv the core abstraction.
   - Accept OpenEnv/Gymnasium-shaped traces as compatibility evidence.
   - Keep Agent Learning-native contracts as the release bar.

5. Use deterministic multi-agent engineering workflow.
   - Give each subagent a small, disjoint task.
   - Keep one local critical path owner.
   - Require every slice to end with focused tests, release-check impact, docs,
     and a local commit.

## Completed Slice: Call-Contract Eval Metrics

This slice is now implemented. `ai-evaluation` can see framework runtime
contracts, static adapter contract quality, adapter call-contract quality, and
observed-I/O quality as separate promoted-run metrics.

Implemented metrics:

- `framework_adapter_call_contract_quality`
- `framework_adapter_observed_io_quality`

Do not add this evidence to `framework_adapter_contract_quality`. That metric
must stay focused on static adapter-contract quality.

Implementation anchors:

- `src/fi/evals/metrics/agents/report.py`
  - Add config fields on `AgentReportEvalConfig`.
  - Wire metric creation near the current `framework_runtime_contract` and
    `framework_adapter_contract_quality` metrics.
  - Extract runtime invocation `call_contract` entries from existing
    framework-runtime payloads.
  - Extract probe `observed_io_contract` entries from framework-probe payloads
    when present.
- `src/agent_learning/optimize.py`
  - Update `build_framework_adapter_probe_evaluation_config()` so promoted
    adapter-probe run manifests include both metric config blocks.
  - Add both metric weights; use `8.0` unless a later calibration changes the
    adapter-probe weighting scheme.
- `src/agent_learning/capabilities.py`
  - Add the two metric names to default metric discovery.
- `src/agent_learning/trinity.py`
  - Requires both metrics for probe promotion, auto-discovery promotion,
    one-call promotion, and one-call run surfaces.

Suggested config shape:

```json
{
  "framework_adapter_call_contract_quality": {
    "framework": "custom_refund_orchestrator",
    "method": "execute_task",
    "input_mode": "dict",
    "call_style": "keyword",
    "input_key": "payload",
    "require_signature_inspectable": true,
    "require_signature_bound": true,
    "required_parameter_names": ["payload"],
    "required_keyword_only_parameters": ["payload"],
    "max_error_count": 0,
    "min_contract_count": 1
  },
  "framework_adapter_observed_io_quality": {
    "framework": "custom_refund_orchestrator",
    "method": "execute_task",
    "input_mode": "dict",
    "required_call_styles": ["keyword"],
    "required_input_keys": ["payload"],
    "required_input_types": ["dict"],
    "required_output_types": ["agent_response"],
    "require_content_observed": true,
    "require_signature_bound": true,
    "min_contract_count": 1,
    "min_invocation_count": 1
  }
}
```

Acceptance tests covered for this slice:

- Add a focused report-metric test where a tiny runtime report with
  `metadata.environment_state.framework_runtime.invocations[0].call_contract`
  scores both metrics at `1.0`, then mutating `signature_bound` or `input_key`
  lowers the relevant score.
- Update probe-promotion tests so promoted runs assert both new metric averages
  are `1.0`.
- Update auto-discovery promotion tests so generated config includes both
  metric blocks and weights.
- Update release-check tests so promoted adapter-probe surfaces require both
  metrics.
- `README.md` and `V1_RELEASE_ROADMAP.md` describe the executable metric gates.

## Recommended Subagent Packets

Use subagents for narrow audits or isolated implementation plans. Keep one
engineer as the integration owner who applies patches, runs tests, resolves
conflicts, and commits.

Subagent A: evaluation metric implementation audit.

- Goal: add first-class `ai-evaluation` metrics for framework adapter call
  contracts and observed I/O quality.
- Files to inspect first:
  - `src/fi/evals/metrics/agents/report.py`
  - `src/agent_learning/optimize.py`
  - `src/agent_learning/capabilities.py`
  - `tests/test_config_and_facades.py`
  - `tests/test_cli_examples.py`
- Expected output:
  - Exact field names and helper insertion points.
  - Focused tests that prove the metrics score `1.0` on promoted probe runs.
  - Failure-mode tests where missing signature or observed I/O lowers score.
- Constraints:
  - Do not expand `framework_adapter_contract_quality` to cover this evidence.
  - Keep `framework_adapter_call_contract_quality` and
    `framework_adapter_observed_io_quality` separate metrics.

Subagent B: promoted-manifest release gate audit.

- Goal: make probe-promoted run manifests fail closed when call-contract or
  observed-I/O evidence disappears.
- Files to inspect first:
  - `src/agent_learning/trinity.py`
  - `src/agent_learning/optimize.py`
  - `tests/test_config_and_facades.py`
- Expected output:
  - Required metric weights for probe promotion, auto-discovery promotion,
    one-call promotion, and one-call run surfaces.
  - Exact release-check assertions that need to change.
  - A minimal targeted test command list.

Subagent C: arbitrary-framework cookbook expansion.

- Goal: expand BYO-framework evidence beyond the current `execute_task(dict)`
  path.
- Candidate cookbooks:
  - LangChain/LangGraph-style `invoke` and `ainvoke`.
  - Provider-style nested methods such as `chat.completions.create`.
  - LiveKit/Pipecat voice or frame-shaped adapters.
  - Browser/CUA adapter probe variants.
- Expected output:
  - One cookbook at a time.
  - One deterministic local fixture per cookbook.
  - Generated report/action evidence and release-check impact.

Subagent D: OpenEnv compatibility boundary audit.

- Goal: prevent accidental dependency or positioning drift.
- Files to inspect first:
  - `pyproject.toml`
  - `typescript/agent-learning-kit/package.json`
  - `README.md`
  - `V1_RELEASE_ROADMAP.md`
  - `internal-docs/*openenv*`
- Expected output:
  - Confirm no package dependency was introduced.
  - Confirm docs say OpenEnv/Gymnasium are compatibility inputs only.
  - Confirm Agent Learning-native contracts remain the primary release bar.

## Deterministic Slice Workflow

For each engineering slice:

1. Define one small target and its expected release-check impact.
2. Assign read-only audit tasks to subagents before implementation.
3. Let the integration owner make the code changes.
4. Run focused tests for the touched behavior.
5. Run `uv run ruff check .` and `git diff --check`.
6. Run `agent-learn release-check --project-root . --quiet`.
7. Update docs in the same commit when behavior or release status changes.
8. Commit locally with a message that names the proof surface.

Do not merge a slice that only updates roadmap language. Every robustness or
10x claim should map to executable local evidence, metrics, and release-check
status.

## Release Discipline

Before merging or handing a slice to another engineer:

```bash
uv run ruff check .
git diff --check
uv run pytest -q
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof.json \
  --quiet
```

For faster inner-loop work on this adapter-probe area:

```bash
uv run pytest \
  tests/test_vendored_simulate_engine.py::test_framework_adapter_probe_proves_keyword_only_signature_io_contract \
  tests/test_config_and_facades.py::test_optimize_framework_adapter_probe_selects_working_adapter \
  tests/test_cli_examples.py::test_sdk_framework_adapter_probe_optimization_example_runs \
  -q
```

## Known Local State

- The only unrelated untracked file observed during this handover was
  `uv.lock`.
- It was not staged or edited as part of this slice.
- Do not remove or overwrite it unless the owner explicitly asks.

## Completion Criteria For Full V1

Do not mark v1 complete until current evidence proves all of these:

- `agent-opt` optimizes prompts, worlds, framework adapters, workflow hooks,
  retrieval hooks, memory layers, multi-agent interactions, and red-team
  scenarios.
- `simulate-sdk` can simulate local adapters for major framework shapes without
  importing those frameworks or requiring hosted services.
- `ai-evaluation` can evaluate arbitrary task outcomes plus runtime contracts,
  trace quality, memory/retrieval quality, and robustness.
- Cookbooks are runnable and documented for each major surface.
- `agent-learn release-check` and `release-proof` pass.
- Reports/actions expose proof artifacts that engineering and product surfaces
  can inspect or export.
- OpenEnv compatibility remains compatibility, not product ownership.
