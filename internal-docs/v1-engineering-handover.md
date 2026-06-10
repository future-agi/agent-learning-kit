# V1 Engineering Handover

## Executive Status

Hand this document to the next engineering owner as the starting point.

Snapshot:

- Date: 2026-06-10.
- Branch observed: `main`.
- Baseline before the current handoff slice:
  `661a1c0 Broaden environment 10x adapter promotion`.
- Full v1 is not done.
- Do not claim universal "better than OpenEnv" yet. The current evidence says
  Agent Learning is broader than OpenEnv on the release-checked local adapter,
  optimization, evaluation, report/action, multi-agent, memory, workflow, and
  robustness axes. OpenEnv/Gymnasium remain compatibility input shapes, not the
  product center.
- Current Python and TypeScript package manifests do not list OpenEnv or
  Gymnasium as package dependencies.
- The current handoff slice adds LangChain-style `invoke(dict)` adapter
  promotion coverage on top of that baseline.
- The only unrelated local state observed during this handoff is `uv.lock`.

Treat `uv.lock` as unrelated until the owner reviews it. Do not stage, delete,
or overwrite it unless explicitly asked.

## First Commands

Run these before changing code:

```bash
git status --short --branch
git log --oneline -8
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
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

- `agent-opt` should optimize worlds, framework adapters, orchestration
  targets, workflow hooks, retrieval hooks, memory policies, multi-agent role
  boundaries, and red-team scenarios.
- `simulate-sdk` should run local framework/runtime simulations without hosted
  service dependencies.
- `ai-evaluation` should score task behavior, runtime contracts, adapter call
  contracts, trace evidence, memory/retrieval quality, robustness, and release
  readiness.

OpenEnv compatibility is useful, but it is not the core abstraction. The
release bar should stay Agent Learning-native: deterministic local simulation,
adapter contracts, optimizer proof, evaluation metrics, reports/actions, and
release-check gates.

## Completed Baseline

The latest committed slice hardened promoted BYO-framework adapter evaluation.

Implemented behavior:

- `AgentReportEvalConfig` supports:
  - `framework_adapter_call_contract_quality`
  - `framework_adapter_observed_io_quality`
- `AgentReportEvaluator` extracts runtime adapter call contracts and probe
  observed-I/O contracts from report evidence.
- `build_framework_adapter_probe_evaluation_config()` emits both metric config
  blocks and weights them in generated promoted-run configs.
- Generated promotion paths and one-call local-adapter helpers require both
  metrics alongside runtime, adapter-contract, trace, and tool metrics.
- `agent-learn release-check` requires both metrics at `1.0` for:
  - explicit probe promotion
  - auto-discovery promotion
  - one-call promotion
  - one-call run
- `capabilities.DEFAULT_METRICS` exposes both metric names.
- The explicit adapter-probe promotion cookbook has matching handwritten metric
  gates.

The previous committed slice hardened BYO-framework adapter probing.

Implemented behavior:

- `GenericAgentWrapper` runtime traces include
  `agent-learning.framework-adapter-call-contract.v1`.
- Call contracts record method, input mode, call style, selected input key,
  callable signature, and observed input/output shape.
- `simulate.run_framework_adapter_probe()` attaches deterministic callable
  signatures when the local callable is inspectable.
- Probe cases include
  `agent-learning.framework-adapter-observed-io-contract.v1`.
- Probe summaries expose call-contract, observed-I/O, signature, input type,
  output type, input key, and call-style counts.
- `optimize.optimize_framework_adapter_probe()` scoring includes
  `framework_adapter_probe_io_contract_quality`.
- Native adapter probe proof requires
  `framework_adapter_probe_signature_io_contract_closed`.
- `agent-learn report` and `agent-learn actions` expose adapter-probe proof,
  callable signature, observed-I/O contract, selected report, contract, and
  replay-lock artifacts.
- The raw adapter-probe cookbook uses keyword-only payload syntax:
  `async def execute_task(self, *, payload)`.

## Verification Evidence

Latest full-suite verification after the LangGraph `ainvoke(dict)` slice:

```bash
uv run pytest -q
```

Result:

- `292 passed, 6 warnings in 1057.81s`

Also passed:

```bash
python3 -m py_compile \
  src/fi/evals/metrics/agents/report.py \
  src/agent_learning/optimize.py \
  src/agent_learning/capabilities.py \
  src/agent_learning/trinity.py
```

```bash
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
```

Focused tests that passed during the latest slice:

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_agent_report_scores_framework_adapter_call_contract_and_observed_io \
  tests/test_config_and_facades.py::test_probe_optimization_promotes_to_framework_run_manifest \
  tests/test_config_and_facades.py::test_auto_discovery_probe_optimization_promotes_discovery_metadata \
  tests/test_config_and_facades.py::test_build_framework_run_manifest_from_local_adapter_optimizes_and_promotes \
  tests/test_config_and_facades.py::test_run_framework_adapter_from_local_adapter_optimizes_promotes_and_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_probe_promotion_example_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_auto_discovery_promotion_example_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_one_call_promotion_example_runs \
  tests/test_cli_examples.py::test_sdk_framework_adapter_one_call_run_example_runs \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
```

Additional verification for the current LangGraph `ainvoke(dict)` slice:

```bash
python3 -m py_compile \
  examples/sdk_framework_adapter_langgraph_ainvoke_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
```

```bash
uv run python examples/sdk_framework_adapter_langgraph_ainvoke_promotion.py \
  /tmp/sdk-framework-adapter-langgraph-ainvoke-promotion.json
```

```bash
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_langgraph_ainvoke_promotion_example_runs \
  -q
```

Result:

- `1 passed, 5 warnings in 3.05s`

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
```

Result:

- `1 passed, 5 warnings in 369.47s`

Additional verification for the environment 10x native adapter promotion
refactor:

```bash
python3 -m py_compile \
  src/agent_learning/trinity.py \
  tests/test_config_and_facades.py
```

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
```

Result:

- `1 passed, 5 warnings in 371.09s`

Additional verification for the LangChain `invoke(dict)` slice:

```bash
python3 -m py_compile \
  examples/sdk_framework_adapter_langchain_invoke_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
```

```bash
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_langchain_invoke_promotion_example_runs \
  -q
```

Result:

- `1 passed, 5 warnings in 2.80s`

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
```

Result:

- `1 passed, 5 warnings in 412.24s`

## Previous LangGraph Slice

This handoff slice broadens BYO framework adapter-probe/promotion beyond the
custom `execute_task(dict)` fixture.

Implemented behavior:

- Added `examples/sdk_framework_adapter_langgraph_ainvoke_promotion.py`.
- The cookbook uses `optimize.build_framework_run_manifest_from_local_adapter()`
  against a local target string.
- Local adapter discovery ranks `ainvoke(dict)` above weak alternatives.
- The selected async method emits:
  - content containing `approved refund`
  - `framework_trace_status` tool evidence
  - `framework_trace` event evidence
  - `framework_runtime` and `framework_trace` state evidence
- `agent-learn release-check` now includes
  `langgraph_ainvoke_promotion` under `framework_adapter_probe_readiness`.
- `V1_FRAMEWORK_PROVIDER_EXAMPLES` includes the new cookbook so the broader
  provider inventory tracks this non-custom adapter path.
- Tests assert both the cookbook output and release-check evidence.

The promoted manifest selects:

```json
{
  "framework": "langgraph",
  "method": "ainvoke",
  "input_mode": "dict",
  "trace_runtime": true
}
```

Promoted-run metrics verified at `1.0`:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`

## Latest Environment 10x Slice

The native adapter promotion axis in `environment_10x_robustness` now counts
custom, LangGraph, and LangChain promoted adapter contracts.

Implemented behavior:

- `V1_ENVIRONMENT_10X_NATIVE_ADAPTER_PROMOTION_SURFACES` now includes
  `langgraph_ainvoke_promotion` and `langchain_invoke_promotion`.
- The environment 10x aggregator no longer assumes every promotion is
  `custom_refund_orchestrator / execute_task / dict`.
- The aggregator derives per-surface framework, method, input mode, discovery,
  and metric-floor expectations from `V1_FRAMEWORK_ADAPTER_PROBE_CONTRACTS`.
- The native adapter axis now enforces each contract's own `min_metrics`,
  including call-contract and observed-I/O metrics.
- Release-check tests assert the surface contract map and prove custom,
  LangGraph `ainvoke(dict)`, and LangChain `invoke(dict)` promotions without
  diluting any contract.

## Latest LangChain Slice

The adapter-probe path now proves a synchronous LangChain-style invocation
surface in addition to the custom and LangGraph promotion surfaces.

Implemented behavior:

- Added `examples/sdk_framework_adapter_langchain_invoke_promotion.py`.
- The cookbook uses weak `run(text)` and passing `invoke(dict)` candidates so
  adapter optimization must select the synchronous LangChain-style dict path.
- The promoted manifest selects `langchain / invoke / dict` with runtime trace
  enabled.
- Release-check now includes `langchain_invoke_promotion` under
  `framework_adapter_probe_readiness` and `environment_10x_robustness`.
- The promoted run closes framework runtime, adapter call-contract,
  observed-I/O, adapter-contract, framework-trace, and tool metrics.

## Immediate Next Slice

Add the next non-custom framework promotion path after LangGraph and LangChain.

Recommended candidates:

1. Provider nested method promotion such as `chat.completions.create`.
2. LiveKit/Pipecat voice/frame adapter promotion.
3. Browser Use / CUA adapter-probe promotion.

Keep the same rule: one deterministic local fixture, one focused cookbook test,
one release-check contract only when the evidence is stable, and docs in the
same commit.

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

Cookbooks/docs/tests:

- `examples/sdk_framework_adapter_probe.py`
- `examples/sdk_framework_adapter_probe_optimization.py`
- `examples/sdk_framework_adapter_probe_promotion.py`
- `examples/sdk_framework_adapter_auto_discovery_promotion.py`
- `examples/sdk_framework_adapter_one_call_promotion.py`
- `examples/sdk_framework_adapter_one_call_run.py`
- `examples/sdk_framework_adapter_langgraph_ainvoke_promotion.py`
- `examples/sdk_framework_adapter_langchain_invoke_promotion.py`
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
10. Run release-check before claiming readiness.

## Subagent Operating Model

Use multiple agents, but keep one integration owner. Subagents should audit or
produce exact implementation plans. The integration owner applies patches,
runs tests, resolves conflicts, updates docs, and commits.

Rules:

- Give each subagent one small, disjoint goal.
- Require file paths, exact assertions, expected failure modes, and commands.
- Do not let subagents make overlapping edits to the same files.
- Do not merge a slice that only changes roadmap language.
- Every robustness or 10x claim must map to local executable evidence,
  metrics, and release-check status.

Suggested next packets:

Subagent A: LangGraph cookbook release-check wiring.

- Goal: audit exact code/test/doc changes needed for
  `langgraph_ainvoke_promotion`.
- Files:
  - `src/agent_learning/trinity.py`
  - `tests/test_cli_examples.py`
  - `tests/test_config_and_facades.py`
  - `README.md`
  - `V1_RELEASE_ROADMAP.md`
- Output:
  - exact contract dictionary
  - exact test assertions
  - release-check pitfalls

Subagent B: environment 10x native adapter refactor.

- Goal: remove hard-coded custom-adapter assumptions so non-custom promotion
  surfaces can eventually count toward `environment_10x_robustness`.
- Files:
  - `src/agent_learning/trinity.py`
  - `tests/test_config_and_facades.py`
  - `internal-docs/environment-10x-robustness-research.md`
- Output:
  - proposed data model for per-surface expected framework/method/input mode
  - exact failing/passing release-check assertions

Subagent C: OpenEnv boundary audit.

- Goal: verify OpenEnv/Gymnasium remain compatibility only.
- Files:
  - `pyproject.toml`
  - `typescript/agent-learning-kit/package.json`
  - `README.md`
  - `V1_RELEASE_ROADMAP.md`
  - `internal-docs/*openenv*`
- Output:
  - dependency check result
  - docs language that should stay
  - any accidental product-positioning drift

Subagent D: next arbitrary-framework cookbook.

- Goal: add one deterministic local cookbook after LangGraph.
- Candidate surfaces:
  - LangChain `invoke`
  - provider nested method `chat.completions.create`
  - LiveKit/Pipecat voice/frame adapter
  - Browser Use / CUA adapter-probe variant
- Output:
  - one fixture
  - one test
  - one release-check contract, only if mature enough

## Verification For The Next Slice

Minimum inner loop for the environment 10x native-adapter refactor:

```bash
python3 -m py_compile \
  src/agent_learning/trinity.py \
  tests/test_config_and_facades.py
```

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
```

```bash
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
```

Add a focused environment 10x test during that refactor if the milestone test
becomes too broad for the new per-surface expectations.

```bash
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
```

Run the full suite before claiming the release gate is stable:

```bash
uv run pytest -q
```

## Release Discipline

Before merging or handing a completed slice to another engineer:

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

Commit locally with a message that names the proof surface, for example:

```bash
git add examples/sdk_framework_adapter_langgraph_ainvoke_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py \
  README.md \
  V1_RELEASE_ROADMAP.md \
  internal-docs/framework-adapter-probe-readiness-research.md \
  internal-docs/v1-engineering-handover.md
git commit -m "Gate LangGraph ainvoke adapter promotion"
```

Do not stage unrelated `uv.lock` unless the owner decides to adopt it.

## Full V1 Completion Criteria

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
