# V1 Engineering Handover

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
framework adapter is promoted into a normal run manifest.

## Latest Completed Slice

The latest slice hardens BYO-framework adapter probing.

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

1. Generalize signature/I-O proof beyond adapter probes into promoted framework
   run manifests.
   - Today the strong signature/I-O proof is centered on probe artifacts.
   - Promoted runs preserve proof metadata, but normal runtime evals do not yet
     score call-contract quality as a first-class metric.

2. Add first-class evaluation metrics for `framework_adapter_call_contract`.
   - Candidate metric names:
     - `framework_adapter_call_contract_quality`
     - `framework_adapter_observed_io_quality`
   - Keep these separate from generic `framework_adapter_contract_quality` so
     unrelated adapter contract gates do not regress.

3. Expand arbitrary-framework coverage.
   - Add focused cookbooks for LangChain/LangGraph-style `invoke/ainvoke`.
   - Add LiveKit/Pipecat-style voice/frame adapters.
   - Add Browser Use / CUA adapters.
   - Add provider-client adapters with nested method paths such as
     `chat.completions.create`.

4. Move from method/input optimization to task/world optimization.
   - The broader v1 goal is not just adapter choice.
   - `agent-opt` should optimize environment state, workflow hooks, memory
     policies, retrieval configs, multi-agent role boundaries, and red-team
     scenarios.

5. Keep OpenEnv compatibility as a secondary surface.
   - Do not make OpenEnv the core abstraction.
   - Accept OpenEnv/Gymnasium-shaped traces as compatibility evidence.
   - Keep Agent Learning-native contracts as the release bar.

6. Use deterministic multi-agent engineering workflow.
   - Give each subagent a small, disjoint task.
   - Keep one local critical path owner.
   - Require every slice to end with focused tests, release-check impact, docs,
     and a local commit.

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

