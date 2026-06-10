# V1 Engineering Handover

## Status Snapshot

Hand this document to the next engineering owner as the starting point.

- Date: 2026-06-10.
- Branch observed: `main`.
- Baseline before the current handoff slice:
  `8367a91 Gate Pipecat process adapter promotion`.
- Current handoff slice:
  OpenAI-compatible `chat.completions.create(messages=...)` nested-method BYO
  framework adapter promotion.
- Full v1 is not done.
- Current evidence does not justify a broad "better than OpenEnv" claim.
  Agent Learning is broader than OpenEnv on the release-checked local adapter,
  optimization, evaluation, report/action, multi-agent, memory, workflow, and
  robustness axes. OpenEnv/Gymnasium remain compatibility input shapes only.
- The current package manifests do not list OpenEnv or Gymnasium as runtime
  dependencies.
- The recurring unrelated local file is `uv.lock`. Do not stage, delete, or
  overwrite it unless the owner decides to adopt it.

## Immediate Answer

What is done:

- The Agent Learning v1 release gate has executable proof for local framework
  adapter probing, adapter optimization, manifest promotion, runtime contract
  evaluation, report/action export, environment replay compatibility,
  environment 10x aggregation, multi-agent room probing, memory/retrieval
  checks, realtime voice checks, browser/CUA checks, workflow/orchestration
  checks, red-team coverage, and regression promotion.
- OpenEnv/Gymnasium are documented and wired as compatibility input shapes, not
  the primary product abstraction.
- Non-custom adapter promotion now covers custom `execute_task(dict)`,
  LangGraph `ainvoke(dict)`, LangChain `invoke(dict)`, Pipecat
  `process(dict)`, and OpenAI-compatible
  `chat.completions.create(messages=...)`.

What is not done:

- Do not call v1 complete yet.
- The next owner still needs more arbitrary-framework promotion surfaces, more
  provider-shaped adapters, broader frontend/product proof surfaces, release
  proof packaging, and final release discipline.
- Do not claim universal superiority over OpenEnv. Say Agent Learning is
  broader on the currently release-checked local evidence.

## First Commands

Run these before changing code:

```bash
git status --short --branch
git log --oneline -8
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
uv run pytest -q
```

For full release-cut proof:

```bash
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof.json \
  --quiet
```

## Product Direction

The v1 goal is to make the Agent Learning trinity work for arbitrary agent
systems:

- `agent-opt` optimizes prompts, worlds, framework adapters, orchestration
  targets, workflow hooks, retrieval hooks, memory policies, multi-agent role
  boundaries, red-team scenarios, and regression candidates.
- `simulate-sdk` runs local framework/runtime simulations without hosted
  service dependencies.
- `ai-evaluation` scores task behavior, runtime contracts, adapter call
  contracts, trace evidence, memory/retrieval quality, robustness, and release
  readiness.

OpenEnv compatibility is useful, but it is not the core abstraction. Keep the
release bar Agent Learning-native: deterministic local simulation, adapter
contracts, optimizer proof, evaluation metrics, reports/actions, and
release-check gates.

## Latest Nested Provider Slice

This handoff slice adds an OpenAI-compatible nested provider method promotion
path.

Implemented behavior:

- Added `examples/sdk_framework_adapter_nested_method_promotion.py`.
- The cookbook uses weak `run(text)` and passing
  `chat.completions.create(messages=...)` candidates so adapter discovery and
  optimization must select the nested provider method.
- The promoted manifest preserves:
  - `framework = openai`
  - `method = chat.completions.create`
  - `input_mode = messages`
  - `input_key = messages`
  - `trace_runtime = true`
- The selected adapter emits:
  - content containing `approved refund`
  - `framework_trace_status` tool evidence
  - `framework_trace` event and state evidence
  - `framework_runtime` and `nested_client` state evidence
  - keyword-call proof through call-contract `call_styles = ["keyword"]`
- `agent-learn release-check` includes `nested_method_promotion` under
  `framework_adapter_probe_readiness`.
- `environment_10x_robustness` counts the nested-method promotion through the
  same generic per-surface contract path and now also checks optional
  `expected_input_key`, `expected_input_kwargs`, and `expected_call_style`.

The promoted manifest should select:

```json
{
  "framework": "openai",
  "method": "chat.completions.create",
  "input_mode": "messages",
  "input_key": "messages",
  "trace_runtime": true
}
```

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`

## Previous Pipecat Slice

This handoff slice adds a Pipecat-style voice/frame adapter promotion path.

Implemented behavior:

- Added `examples/sdk_framework_adapter_pipecat_process_promotion.py`.
- The cookbook uses weak `run(text)` and passing `process(dict)` candidates so
  adapter optimization must select the Pipecat-style dict path.
- The probe case declares `modality: voice`; the strong adapter path requires
  Pipecat metadata plus voice modality.
- The selected adapter emits:
  - content containing `approved refund`
  - `framework_trace_status` tool evidence
  - `framework_trace` event evidence
  - `framework_runtime`, `framework_trace`, and `pipecat_frame` state evidence
  - `pipecat_frame.direction = downstream`
- `agent-learn release-check` includes `pipecat_process_promotion` under
  `framework_adapter_probe_readiness`.
- `environment_10x_robustness` counts the Pipecat promotion through the same
  generic per-surface contract path as custom, LangGraph, and LangChain.

The promoted manifest should select:

```json
{
  "framework": "pipecat",
  "method": "process",
  "input_mode": "dict",
  "trace_runtime": true
}
```

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`

## Current Adapter Promotion Baseline

The release-check adapter-probe gate now runs:

- raw probe
- discovery
- probe optimization
- auto-discovery optimization
- explicit promotion
- auto-discovery promotion
- one-call promotion
- one-call run
- LangGraph `ainvoke(dict)` promotion
- LangChain `invoke(dict)` promotion
- Pipecat `process(dict)` promotion
- OpenAI-compatible `chat.completions.create(messages=...)` promotion

Every promoted run must preserve proof/discovery metadata and close framework
runtime, adapter call-contract, observed-I/O, adapter-contract, framework-trace,
and tool-selection metrics.

## Latest Environment 10x Slice

The native adapter promotion axis in `environment_10x_robustness` now counts
custom, LangGraph, LangChain, Pipecat, and nested provider-method promoted
adapter contracts.

Implemented behavior:

- `V1_ENVIRONMENT_10X_NATIVE_ADAPTER_PROMOTION_SURFACES` includes
  `probe_promotion`, `auto_discovery_promotion`, `one_call_promotion`,
  `one_call_run`, `langgraph_ainvoke_promotion`,
  `langchain_invoke_promotion`, `pipecat_process_promotion`, and
  `nested_method_promotion`.
- The environment 10x aggregator derives per-surface framework, method, input
  mode, input key, input kwargs, call style, discovery, and metric-floor
  expectations from `V1_FRAMEWORK_ADAPTER_PROBE_CONTRACTS`.
- The native adapter axis enforces each contract's own `min_metrics`, including
  call-contract and observed-I/O metrics.

## Verification Ledger

Latest full-suite verification before this nested-provider handoff slice:

```bash
uv run pytest -q
```

Result:

- `292 passed, 6 warnings in 1057.81s`

The LangChain slice also passed:

```bash
python3 -m py_compile \
  examples/sdk_framework_adapter_langchain_invoke_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_langchain_invoke_promotion_example_runs \
  -q
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
uv run pytest -q
```

Result:

- focused LangChain cookbook: `1 passed, 5 warnings in 2.80s`
- release milestone test: `1 passed, 5 warnings in 412.24s`
- full suite: `292 passed, 6 warnings in 1057.81s`

Pipecat handoff verification passed:

```bash
python3 -m py_compile \
  examples/sdk_framework_adapter_pipecat_process_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_pipecat_process_promotion_example_runs \
  -q
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
uv run pytest -q
```

Result:

- `py_compile`: passed
- focused Pipecat cookbook:
  `1 passed, 5 warnings in 3.01s`
- release milestone test:
  `1 passed, 5 warnings in 540.16s (0:09:00)`
- `uv run ruff check .`: passed
- `git diff --check`: passed
- CLI release-check: passed
- standalone full suite before the final release-proof run:
  `293 passed, 6 warnings in 865.98s (0:14:25)`
- release-proof:
  `status=passed`, wrote `/tmp/agent-learning-release-proof.json`, and passed
  all 7 required checks: `release_check`, `ruff`, `pytest`, `build`,
  `typescript_build`, `typescript_test`, and `git_diff_check`
- release-proof embedded pytest on the then-current checkout:
  `296 passed, 6 warnings in 964.34s (0:16:04)`
- release-proof TypeScript test:
  `21 passed, 2 skipped test suites; 646 passed, 6 skipped tests`

Nested-provider handoff verification passed:

```bash
uv run python examples/sdk_framework_adapter_nested_method_promotion.py \
  /tmp/sdk-framework-adapter-nested-method-promotion.json
uv run python -m py_compile \
  examples/sdk_framework_adapter_nested_method_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_nested_method_promotion_example_runs \
  -q
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
uv run pytest -q
```

Result:

- nested-provider cookbook run: passed and wrote
  `/tmp/sdk-framework-adapter-nested-method-promotion.json`
- `py_compile`: passed
- focused nested-provider cookbook:
  `1 passed, 5 warnings in 3.41s`
- release milestone test:
  `1 passed, 5 warnings in 423.80s (0:07:03)`
- `uv run ruff check .`: passed
- `git diff --check`: passed
- CLI release-check: passed
- full suite: `297 passed, 6 warnings in 895.88s (0:14:55)`

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
  - Pipecat preset: `process(dict)`, `modality=voice`

Optimization and proof:

- `src/agent_learning/optimize.py`
  - `optimize_framework_adapter_probe`
  - `score_framework_adapter_probe_result`
  - `_framework_adapter_probe_proof`
  - `build_framework_adapter_probe_evaluation_config`
  - `build_framework_run_manifest_from_probe_optimization`
  - `build_framework_run_manifest_from_local_adapter`
  - `run_framework_adapter_from_local_adapter`

Evaluation:

- `src/fi/evals/metrics/agents/report.py`
  - `AgentReportEvalConfig`
  - runtime contract metrics
  - adapter call-contract metrics
  - observed-I/O metrics

Report/action surfaces:

- `src/fi/simulate/cli.py`
  - `_framework_adapter_probe_card`
  - `_framework_adapter_probe_actions`
  - `_framework_adapter_probe_markdown`

Release gate:

- `src/agent_learning/trinity.py`
  - `V1_FRAMEWORK_ADAPTER_PROBE_FILES`
  - `V1_FRAMEWORK_ADAPTER_PROBE_CONTRACTS`
  - `V1_FRAMEWORK_ADAPTER_PROBE_REQUIRED_ACTIONS`
  - `V1_ENVIRONMENT_10X_NATIVE_ADAPTER_PROMOTION_SURFACES`
  - `_release_framework_adapter_probe_status`
  - `_framework_adapter_probe_record`
  - `_append_framework_adapter_probe_errors`
  - `_release_environment_10x_robustness_status`

Cookbooks, docs, and tests:

- `examples/sdk_framework_adapter_probe.py`
- `examples/sdk_framework_adapter_probe_optimization.py`
- `examples/sdk_framework_adapter_probe_promotion.py`
- `examples/sdk_framework_adapter_auto_discovery_promotion.py`
- `examples/sdk_framework_adapter_one_call_promotion.py`
- `examples/sdk_framework_adapter_one_call_run.py`
- `examples/sdk_framework_adapter_langgraph_ainvoke_promotion.py`
- `examples/sdk_framework_adapter_langchain_invoke_promotion.py`
- `examples/sdk_framework_adapter_pipecat_process_promotion.py`
- `examples/sdk_framework_adapter_nested_method_promotion.py`
- `tests/test_cli_examples.py`
- `tests/test_config_and_facades.py`
- `README.md`
- `V1_RELEASE_ROADMAP.md`
- `internal-docs/framework-adapter-probe-readiness-research.md`
- `internal-docs/environment-10x-robustness-research.md`

## Deterministic Adapter-Probe Workflow

Use this workflow for every new framework adapter surface:

1. Build or wrap one local framework object.
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
10. Add the release-check contract only after the example is deterministic.
11. Run release-check before claiming readiness.

## Subagent Operating Model

Use multiple agents, but keep one integration owner. Subagents should audit or
produce exact implementation plans. The integration owner applies patches, runs
tests, resolves conflicts, updates docs, and commits.

Rules:

- Give each subagent one small, disjoint goal.
- Require file paths, exact assertions, expected failure modes, and commands.
- Do not let subagents make overlapping edits to the same files.
- Do not merge a slice that only changes roadmap language.
- Every robustness or 10x claim must map to local executable evidence,
  metrics, and release-check status.

Suggested next packets:

1. LiveKit session promotion.
   - Goal: add a deterministic local `respond(text)` or session-like adapter
     distinct from Pipecat `process(dict)`.
   - Constraint: no external LiveKit service dependency.
   - Output: voice modality proof with framework runtime and trace metrics.
2. Provider response promotion.
   - Goal: promote a local provider-response shape that requires
     `input_kwargs={"model": "local-provider-model"}` and normalized
     `provider_response` state.
   - Constraint: keep it local-only; do not call hosted provider APIs.
   - Output: one cookbook or promoted variant, release-check contract, and
     assertions for `provider_choice` / `provider_tool_call` evidence.
3. Browser Use / CUA promotion.
   - Goal: promote a local browser/CUA-shaped adapter through the BYO probe
     path.
   - Constraint: use existing browser/CUA trace metrics; do not invent a
     separate product claim.
4. OpenEnv boundary audit.
   - Goal: verify OpenEnv/Gymnasium remain compatibility-only.
   - Files: `pyproject.toml`, `typescript/agent-learning-kit/package.json`,
     `README.md`, `V1_RELEASE_ROADMAP.md`, `internal-docs/*openenv*`.
   - Output: dependency check, wording audit, and any accidental positioning
     drift.

## Release Discipline

Before handing a completed slice to another engineer:

```bash
git status --short
uv run ruff check .
git diff --check
uv run pytest -q
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --output /tmp/agent-learning-release-proof.json \
  --quiet
```

Commit locally with a message that names the proof surface. For this slice:

```bash
git add examples/sdk_framework_adapter_nested_method_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py \
  README.md \
  V1_RELEASE_ROADMAP.md \
  internal-docs/framework-adapter-probe-readiness-research.md \
  internal-docs/environment-10x-robustness-research.md \
  internal-docs/v1-engineering-handover.md
git commit -m "Gate nested method adapter promotion"
```

Do not stage unrelated `uv.lock` unless the owner decides to adopt it.

## Full V1 Completion Criteria

Do not mark v1 complete until current evidence proves all of these:

- `agent-opt` optimizes prompts, worlds, framework adapters, workflow hooks,
  retrieval hooks, memory layers, multi-agent interactions, red-team scenarios,
  and regression candidates.
- `simulate-sdk` can simulate local adapters for major framework shapes without
  importing those frameworks or requiring hosted services.
- `ai-evaluation` can evaluate arbitrary task outcomes plus runtime contracts,
  trace quality, memory/retrieval quality, and robustness.
- Cookbooks are runnable and documented for each major surface.
- `agent-learn release-check` and `release-proof` pass from a clean checkout.
- Reports/actions expose proof artifacts that engineering and product surfaces
  can inspect or export.
- OpenEnv compatibility remains compatibility, not product ownership.
