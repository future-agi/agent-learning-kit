# V1 Engineering Handover

## Status Snapshot

Hand this document to the next engineering owner as the starting point.

- Date: 2026-06-10.
- Branch observed: `main`.
- Baseline before the current handoff slice:
  `76406a2 Promote lifecycle adapter trace`.
- Current handoff slice:
  Message-history and handoff-transcript adapter promotion.
- Full v1 is not done.
- Current evidence does not justify a broad "better than OpenEnv" claim.
  Agent Learning is broader than OpenEnv on the release-checked local adapter,
  optimization, evaluation, report/action, multi-agent, memory, workflow, and
  robustness axes. OpenEnv/Gymnasium remain compatibility input shapes only.
- The current package manifests do not list OpenEnv, legacy Gym, or Gymnasium as
  runtime dependencies.
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
- OpenEnv/Gymnasium are documented, release-checked, and wired as compatibility
  input shapes, not the primary product abstraction.
- Non-custom adapter promotion now covers custom `execute_task(dict)`,
  LangGraph `ainvoke(dict)`, LangChain `invoke(dict)`, Pipecat
  `process(dict)`, OpenAI-compatible
  `chat.completions.create(messages=...)`, LiveKit `run_session(dict)`, and
  provider-response `chat.completions.create(messages=..., model=...)`, plus
  Browser Use `execute_task(dict)` CUA trace promotion, LangGraph-style
  `execute_task(dict)` workflow trace promotion, and LangGraph-style
  `execute_task(dict)` orchestration trace promotion, plus LiveKit-style
  `execute_task(dict)` lifecycle trace promotion, plus AutoGen-style
  `run(task=...)` message-history promotion and OpenAI Agents-style
  `execute_task(dict)` handoff-transcript promotion, plus MCP
  `execute_task(dict)` tool-session promotion and A2A `send_message(dict)`
  protocol-trace promotion, plus Agent Learning Kit `execute_task(dict)` agent
  trust-boundary/control-plane promotion.

What is not done:

- Do not call v1 complete yet.
- The next owner still needs more arbitrary-framework promotion surfaces, more
  provider-shaped adapters, broader frontend/product proof surfaces, full release
  proof hardening, and final release discipline.
- Do not claim universal superiority over OpenEnv. Say Agent Learning is
  broader on the currently release-checked local evidence.

## Latest Transcript Adapter Promotion Slice

This handoff slice promotes the existing message-history and handoff-transcript
framework adapter cookbooks through the generic framework adapter probe gate and
the environment 10x native adapter axis.

Implemented behavior:

- Reuses `examples/sdk_framework_adapter_message_history.py`, a local-only
  AutoGen-style cookbook that discovers and promotes
  `LocalAutoGenTeam.run(task=...)` as `framework=autogen`, `input_mode=text`,
  `input_key=task`, keyword call style.
- Reuses `examples/sdk_framework_adapter_handoff_transcript.py`, a local-only
  OpenAI Agents-style cookbook that discovers and promotes
  `LocalHandoffTeam.execute_task` as `framework=openai_agents`,
  `input_mode=dict`, positional call style.
- `message_history_promotion` requires discovery/proof metadata,
  `message_history` state, transcript/tool events, framework runtime/trace
  artifacts, transcript summary gates for messages/tool calls/tool responses,
  planner/reviewer/tool sources, and `framework_transcript_quality == 1.0`.
- `handoff_transcript_promotion` requires discovery/proof metadata,
  `message_history` and `framework_handoffs` state, handoff/review/
  reconciliation events, runtime/trace artifacts, participants, passed review,
  accepted reconciliation, and `framework_transcript_quality == 1.0`.
- `_framework_adapter_probe_state_summary()` now falls back to deterministic
  direct state maps when a state object has no nested `summary`, which lets the
  existing generic `state_summary_*` validator enforce transcript and handoff
  state without bespoke transcript validator code.
- `environment_10x_robustness` now counts `message_history_promotion` and
  `handoff_transcript_promotion` through the native adapter promotion axis.

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `framework_transcript_quality == 1.0`
- `tool_selection_accuracy == 1.0`

Current slice verification:

- `uv run python -m py_compile src/agent_learning/trinity.py tests/test_config_and_facades.py`
  passed.
- `uv run ruff check src/agent_learning/trinity.py tests/test_config_and_facades.py`
  passed.
- Focused message-history and handoff-transcript cookbook/behavior tests passed:
  `4 passed, 5 warnings in 11.14s`.
- `uv run pytest tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones -q`
  passed: `1 passed, 8 warnings in 406.94s (0:06:46)`.
- Selected release-proof passed for `release_check` and `git_diff_check`, writing
  `/tmp/agent-learning-transcript-promotion-release-proof.json`. Its
  `summary.ready` is `false` because non-selected checks are intentionally
  skipped.
- `uv run ruff check .` passed.
- `git diff --check` passed.
- `uv run pytest -q` passed: `302 passed, 10 warnings in 916.37s (0:15:16)`.

## Previous Lifecycle Adapter Promotion Slice

This handoff slice promotes the existing LiveKit-style lifecycle trace cookbook
through the generic framework adapter probe gate and the environment 10x native
adapter axis.

Implemented behavior:

- Reuses `examples/sdk_framework_adapter_lifecycle_trace.py`, a local-only
  cookbook that discovers and promotes
  `LocalRealtimeLifecycleAgent.execute_task` as `framework=livekit`,
  `input_mode=dict`, positional call style.
- `lifecycle_trace_promotion` in `V1_FRAMEWORK_ADAPTER_PROBE_CONTRACTS`
  requires discovery/proof metadata, required state keys, runtime required-state
  keys, lifecycle/runtime event types, artifact kinds, exact metric floors, and
  state-summary gates for phases, sessions, retry/recovery, cancellation,
  resume, cleanup, checkpointing, streaming, tool registration, state
  persistence, and terminal cleanup.
- `environment_10x_robustness` now counts `lifecycle_trace_promotion` through
  the native adapter promotion axis.
- The promotion stays under the existing generic release-record boundary:
  `_framework_adapter_probe_record()` exposes sanitized `state_summaries`, and
  `_append_framework_adapter_probe_errors()` enforces `state_summary_minimums`
  and `state_summary_equals` without adding lifecycle-specific validator code.

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`
- `framework_lifecycle_coverage == 1.0`
- `framework_lifecycle_quality == 1.0`

Current slice verification:

- `uv run python -m py_compile src/agent_learning/trinity.py tests/test_config_and_facades.py`
  passed.
- `uv run ruff check src/agent_learning/trinity.py tests/test_config_and_facades.py`
  passed.
- Focused lifecycle cookbook and behavior tests passed:
  `2 passed, 5 warnings in 5.99s`.
- `uv run pytest tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones -q`
  passed: `1 passed, 8 warnings in 385.91s (0:06:25)`.
- Selected release-proof passed for `release_check` and `git_diff_check`, writing
  `/tmp/agent-learning-lifecycle-promotion-release-proof.json`. Its
  `summary.ready` is `false` because non-selected checks are intentionally
  skipped.
- `uv run ruff check .` passed.
- `git diff --check` passed.
- `uv run pytest -q` passed: `302 passed, 10 warnings in 701.71s (0:11:41)`.

## Previous Agent Control-Plane Adapter Promotion Slice

This handoff slice promotes Agent Learning Kit's own trust-boundary and runtime
control-plane evidence through the generic framework adapter probe gate and the
environment 10x native adapter axis.

Implemented behavior:

- Adds `examples/sdk_framework_adapter_agent_control_plane.py`, a local-only
  cookbook that discovers and promotes `LocalAgentControlPlaneRuntime.execute_task`
  as `framework=agent_learning_kit`, `input_mode=dict`, positional call style.
- The generic framework wrapper now preserves normalized
  `agent_trust_boundary_model` and `agent_control_plane` state, artifacts, and
  trust/control events from arbitrary adapter outputs.
- Framework runtime traces now advertise `control_plane` and `trust_boundary`
  runtime signals when those states are present, allowing generated runtime
  contracts to close at `1.0`.
- `build_framework_adapter_probe_evaluation_config()` derives
  trust-boundary/control-plane coverage and quality gates from selected probe
  output summaries.
- `agent_control_plane_promotion` in `V1_FRAMEWORK_ADAPTER_PROBE_CONTRACTS`
  requires discovery/proof metadata, required state keys, runtime required-state
  keys, trust/control event types, artifact kinds, exact metric floors, and
  state-summary gates for required controls, zero gaps, zero unmitigated
  high-risk threats, zero uncontained high-risk incidents, approvals, rollback,
  budgets, containment, and audit evidence.
- `_framework_adapter_probe_record()` now exposes sanitized generic
  `state_summaries` plus direct `agent_trust_boundary_summary` and
  `agent_control_plane_summary` fields. Timing fields such as `duration_ms` are
  removed so CLI and direct release-status payloads remain deterministic.
- `environment_10x_robustness` now counts `agent_control_plane_promotion` through
  the native adapter promotion axis.

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`
- `agent_trust_boundary_coverage == 1.0`
- `agent_trust_boundary_quality == 1.0`
- `agent_control_plane_coverage == 1.0`
- `agent_control_plane_quality == 1.0`

Current slice verification:

- `uv run python -m py_compile src/fi/simulate/agent/generic.py src/fi/simulate/agent/frameworks.py src/agent_learning/optimize.py src/agent_learning/trinity.py examples/sdk_framework_adapter_agent_control_plane.py tests/test_cli_examples.py tests/test_config_and_facades.py`
  passed.
- `uv run ruff check src/fi/simulate/agent/generic.py src/fi/simulate/agent/frameworks.py src/agent_learning/optimize.py src/agent_learning/trinity.py examples/sdk_framework_adapter_agent_control_plane.py tests/test_cli_examples.py tests/test_config_and_facades.py`
  passed.
- Focused agent-control-plane cookbook and behavior tests passed:
  `2 passed, 5 warnings in 8.97s`.
- `uv run pytest tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones -q`
  passed: `1 passed, 8 warnings in 654.10s (0:10:54)`.
- `git diff --check` passed.
- `uv run ruff check .` passed.
- Selected release-proof passed for `release_check` and `git_diff_check`, writing
  `/tmp/agent-learning-control-plane-promotion-release-proof.json`. Its
  `summary.ready` is `false` because non-selected checks are intentionally
  skipped.
- `uv run pytest -q` passed: `302 passed, 10 warnings in 1232.82s (0:20:32)`.

## Previous MCP/A2A Protocol Adapter Probe Promotion Slice

This handoff slice promotes the existing local MCP and A2A protocol cookbooks
through the generic adapter-probe release gate and the environment 10x native
adapter axis.

Implemented behavior:

- Reuses `examples/sdk_framework_adapter_mcp_tool_session.py` and
  `examples/sdk_framework_adapter_a2a_protocol_trace.py`.
- `mcp_tool_session_promotion` requires a selected local
  `execute_task(dict)` adapter with `framework=mcp`, positional call style,
  discovery/probe metadata, `mcp_tool_session` state, runtime required-state
  evidence, MCP events, framework/runtime/tool-session artifacts, summary
  counts/membership checks, and closed MCP coverage/quality metrics.
- `a2a_protocol_trace_promotion` requires a selected local
  `send_message(dict)` adapter with `framework=a2a`, positional call style,
  discovery/probe metadata, `a2a_protocol_trace` state, runtime required-state
  evidence, A2A events, framework/runtime/protocol/artifact outputs, summary
  counts/membership checks, and closed A2A coverage/quality metrics.
- `_framework_adapter_probe_record()` now extracts protocol state keys, runtime
  required-state keys, event types, artifact kinds, and protocol summaries from
  the promoted run report.
- `_append_framework_adapter_probe_errors()` enforces optional contract fields
  for protocol state, events, artifacts, and summary evidence so these
  promotions cannot pass on metrics alone.
- `environment_10x_robustness` counts both promotions through the generic
  per-surface contract path.

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`
- `mcp_tool_session_coverage == 1.0`
- `mcp_tool_session_quality == 1.0`
- `a2a_protocol_coverage == 1.0`
- `a2a_protocol_quality == 1.0`

Current slice verification:

- `uv run python -m py_compile src/agent_learning/trinity.py tests/test_config_and_facades.py`
  passed.
- `uv run ruff check src/agent_learning/trinity.py tests/test_config_and_facades.py`
  passed.
- Focused MCP/A2A example and behavior tests passed:
  `4 passed, 5 warnings in 8.20s`.
- `uv run pytest tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones -q`
  passed: `1 passed, 8 warnings in 609.97s (0:10:09)`.
- `git diff --check` passed.
- `uv run ruff check .` passed.
- Selected release-proof passed for `release_check` and `git_diff_check`, writing
  `/tmp/agent-learning-protocol-promotion-release-proof.json`. Its
  `summary.ready` is `false` because non-selected checks are intentionally
  skipped.
- `uv run pytest -q` passed: `300 passed, 10 warnings in 1161.68s (0:19:21)`.

## Previous Release-Proof Handover Packaging Slice

This handoff slice makes release-proof packaging executable and
machine-readable.

Implemented behavior:

- `agent-learn release-proof --dry-run` emits planned command evidence for every
  selected proof check, including `command`, `cwd`, `planned=true`,
  `exit_code=null`, and zero output byte counts.
- `agent-learning.release-proof.v1` carries a `handover` block with:
  - required handover docs,
  - required doc phrases,
  - product surfaces,
  - completion invariants,
  - the first-release command plan.
- `agent-learn release-check` gates the same contract as
  `release_handover_packaging` under M7.
- Dry-run output remains a plan only: `status="planned"` and `ready=false`.

First handover preflight:

```bash
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --dry-run \
  --output /tmp/agent-learning-release-proof-plan.json \
  --quiet
```

## Previous OpenEnv Compatibility Boundary Slice

This handoff slice makes the OpenEnv/Gymnasium boundary executable instead of
only documented.

Implemented behavior:

- Adds `openenv_compatibility_boundary` to `agent-learn release-check`.
- The gate verifies:
  - Python and TypeScript package manifests do not add `openenv`, legacy `gym`,
    or `gymnasium` runtime dependencies.
  - Local Python/TypeScript source does not directly import `openenv`, legacy
    `gym`, or `gymnasium`.
  - Required docs keep the compatibility-only positioning explicit.
- The gate intentionally allows existing compatibility cookbooks and wire-format
  names:
  - `examples/sdk_openenv_environment_optimization.py`
  - `examples/sdk_framework_adapter_openenv_trace.py`
  - `openenv`, `open_env`, and `gymnasium_env` compatibility wire formats
- The owned product surface remains `environment_replay`.

Key evidence fields:

- `owned_surface == "environment_replay"`
- `compatibility_boundary == "openenv_gymnasium_wire_format"`
- `dependency_errors == []`
- `import_errors == []`
- `doc_errors == []`

## Previous Workflow/Orchestration Trace Slice

This handoff slice promotes the existing local workflow and orchestration trace
framework-adapter cookbooks into the adapter-probe release gate and environment
10x native adapter axis.

Implemented behavior:

- Reuses `examples/sdk_framework_adapter_workflow_trace.py` and
  `examples/sdk_framework_adapter_orchestration_trace.py`.
- Both cookbooks use local LangGraph-style `execute_task(dict)` agents selected
  through adapter discovery, then promoted into normal `agent-learning.run.v1`
  manifests with probe proof and discovery metadata preserved.
- `workflow_trace_promotion` requires graph topology, checkpoints, route
  decisions, interrupts, replay, tool evidence, trace artifacts, and closed
  workflow coverage/graph-quality metrics.
- `orchestration_trace_promotion` requires supervisor/delegate/handoff
  evidence, communication, retry recovery, aggregation/stop state, tool
  evidence, trace artifacts, and closed orchestration coverage/flow-quality
  metrics.
- `agent-learn release-check` includes both surfaces under
  `framework_adapter_probe_readiness`.
- `environment_10x_robustness` counts both promotions through the generic
  per-surface contract path and checks positional call style plus trace-specific
  metric floors.

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`
- `workflow_trace_coverage == 1.0`
- `workflow_graph_quality == 1.0`
- `orchestration_trace_coverage == 1.0`
- `orchestration_flow_quality == 1.0`

## Previous Browser/CUA Trace Slice

This handoff slice promotes the existing local Browser/CUA framework-adapter
cookbook into the adapter-probe release gate and environment 10x native adapter
axis.

Implemented behavior:

- Reuses `examples/sdk_framework_adapter_browser_cua_trace.py`.
- The cookbook uses weak `run(text)` plus trace-capable `execute_task(dict)`
  candidates so adapter discovery and optimization must select the Browser Use
  task path.
- The promoted manifest preserves:
  - `framework = browser_use`
  - `method = execute_task`
  - `input_mode = dict`
  - `trace_runtime = true`
  - `simulation.modality = cua`
- The selected adapter emits:
  - content containing `approved refund`
  - `browser_click` tool evidence
  - `browser_snapshot`, `browser_action`, `browser_trace`, `browser_network`,
    `browser_runtime`, `browser_storage`, `browser_mutation_pack`, and
    `environment_injection` events
  - `framework_runtime` and `browser_cua` state evidence
  - browser trace/screenshot artifacts
  - positional-call proof through call-contract `call_styles = ["positional"]`
- `agent-learn release-check` includes `browser_cua_trace_promotion` under
  `framework_adapter_probe_readiness`.
- `environment_10x_robustness` counts the Browser/CUA trace promotion through
  the same generic per-surface contract path and checks CUA modality plus
  browser-specific metric floors.

The promoted manifest should select:

```json
{
  "framework": "browser_use",
  "method": "execute_task",
  "input_mode": "dict",
  "trace_runtime": true,
  "simulation": {
    "modality": "cua"
  }
}
```

Expected promoted-run metric floors:

- `browser_action_outcome == 1.0`
- `browser_action_safety == 1.0`
- `browser_grounding_quality == 1.0`
- `browser_mutation_resilience == 1.0`
- `browser_trace_coverage == 1.0`
- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`

## Previous Provider Response Slice

This handoff slice promotes the existing local provider-response cookbook into
the adapter-probe release gate and environment 10x native adapter axis.

Implemented behavior:

- Reuses `examples/sdk_framework_adapter_provider_response.py`.
- The cookbook uses weak `run(text)` plus
  `chat.completions.create(messages=...)` candidates and requires the selected
  candidate to preserve `input_kwargs={"model": "local-provider-model"}`.
- The promoted manifest preserves:
  - `framework = openai`
  - `method = chat.completions.create`
  - `input_mode = messages`
  - `input_key = messages`
  - `input_kwargs = {"model": "local-provider-model"}`
  - `trace_runtime = true`
- The selected adapter emits:
  - content containing `approved refund`
  - `framework_trace_status` tool evidence
  - `provider_choice` and `provider_tool_call` events
  - `framework_runtime` and `provider_response` state evidence
  - provider response summary with `choice_count=1`, `tool_call_count=1`,
    `finish_reasons=["tool_calls"]`, and `model="local-provider-model"`
- `agent-learn release-check` includes `provider_response_promotion` under
  `framework_adapter_probe_readiness`.
- `environment_10x_robustness` counts the provider-response promotion through
  the same generic per-surface contract path and checks expected input kwargs.

The promoted manifest should select:

```json
{
  "framework": "openai",
  "method": "chat.completions.create",
  "input_mode": "messages",
  "input_key": "messages",
  "input_kwargs": {
    "model": "local-provider-model"
  },
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

## First Commands

Run these before changing code:

```bash
git status --short --branch
git log --oneline -8
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --dry-run \
  --output /tmp/agent-learning-release-proof-plan.json \
  --quiet
uv run python -m agent_learning.cli release-check --project-root . --quiet
uv run pytest -q
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --only release_check \
  --only git_diff_check \
  --output /tmp/agent-learning-release-proof-selected.json \
  --quiet
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

## Previous LiveKit Session Slice

This handoff slice adds a LiveKit-style room/session adapter promotion path.

Implemented behavior:

- Added `examples/sdk_framework_adapter_livekit_run_session_promotion.py`.
- The cookbook uses weak `respond(text)` and passing `run_session(dict)`
  candidates so adapter discovery and optimization must select the session path.
- The promoted manifest preserves:
  - `framework = livekit`
  - `method = run_session`
  - `input_mode = dict`
  - `trace_runtime = true`
  - `simulation.modality = voice`
- The selected adapter emits:
  - content containing `approved refund`
  - `framework_trace_status` tool evidence
  - `framework_trace`, `livekit_session_event`, and `livekit_transcript` events
  - `framework_runtime`, `framework_trace`, and `livekit_session` state evidence
  - positional-call proof through call-contract `call_styles = ["positional"]`
- `agent-learn release-check` includes `livekit_run_session_promotion` under
  `framework_adapter_probe_readiness`.
- `environment_10x_robustness` counts the LiveKit session promotion through the
  same generic per-surface contract path and now also checks optional
  `expected_modality`.

The promoted manifest should select:

```json
{
  "framework": "livekit",
  "method": "run_session",
  "input_mode": "dict",
  "trace_runtime": true,
  "simulation": {
    "modality": "voice"
  }
}
```

Expected promoted-run metric floors:

- `framework_adapter_call_contract_quality == 1.0`
- `framework_adapter_contract_quality == 1.0`
- `framework_adapter_observed_io_quality == 1.0`
- `framework_runtime_contract == 1.0`
- `framework_trace_coverage == 1.0`
- `tool_selection_accuracy == 1.0`

## Previous Nested Provider Slice

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
- LiveKit `run_session(dict)` promotion
- provider-response `chat.completions.create(messages=..., model=...)`
  promotion
- Browser Use `execute_task(dict)` CUA trace promotion
- AutoGen-style `run(task=...)` message-history promotion
- OpenAI Agents-style `execute_task(dict)` handoff-transcript promotion
- LangGraph-style `execute_task(dict)` workflow trace promotion
- LangGraph-style `execute_task(dict)` orchestration trace promotion
- MCP `execute_task(dict)` tool-session promotion
- A2A `send_message(dict)` protocol-trace promotion
- Agent Learning Kit `execute_task(dict)` trust-boundary/control-plane promotion

Every promoted run must preserve proof/discovery metadata and close framework
runtime, adapter call-contract, observed-I/O, adapter-contract, framework-trace,
tool-selection, and any trace/control/protocol-specific metrics required by its
contract.

## Latest Environment 10x Slice

The native adapter promotion axis in `environment_10x_robustness` now counts
custom, LangGraph, LangChain, Pipecat, nested provider-method, and LiveKit
session promoted adapter contracts plus provider-response, Browser/CUA trace,
message-history, handoff-transcript, workflow trace, orchestration trace,
lifecycle trace, MCP tool-session, and A2A protocol-trace promotion plus Agent
Learning Kit control-plane promotion.

Implemented behavior:

- `V1_ENVIRONMENT_10X_NATIVE_ADAPTER_PROMOTION_SURFACES` includes
  `probe_promotion`, `auto_discovery_promotion`, `one_call_promotion`,
  `one_call_run`, `langgraph_ainvoke_promotion`,
  `langchain_invoke_promotion`, `pipecat_process_promotion`,
  `nested_method_promotion`, `livekit_run_session_promotion`, and
  `provider_response_promotion`, `browser_cua_trace_promotion`,
  `message_history_promotion`, `handoff_transcript_promotion`,
  `workflow_trace_promotion`, `orchestration_trace_promotion`,
  `lifecycle_trace_promotion`, `mcp_tool_session_promotion`,
  `a2a_protocol_trace_promotion`, and `agent_control_plane_promotion`.
- The environment 10x aggregator derives per-surface framework, method, input
  mode, input key, input kwargs, call style, modality, discovery, and metric-floor
  expectations from `V1_FRAMEWORK_ADAPTER_PROBE_CONTRACTS`.
- The native adapter axis enforces each contract's own `min_metrics`, including
  call-contract and observed-I/O metrics.

## Verification Ledger

Historical verification ledger for prior adapter-promotion slices; do not treat
these results as current proof for the release-proof handover slice:

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

LiveKit handoff verification passed:

```bash
uv run python examples/sdk_framework_adapter_livekit_run_session_promotion.py \
  /tmp/sdk-framework-adapter-livekit-run-session-promotion.json
uv run python -m py_compile \
  examples/sdk_framework_adapter_livekit_run_session_promotion.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_livekit_run_session_promotion_example_runs \
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

- LiveKit cookbook run: passed and wrote
  `/tmp/sdk-framework-adapter-livekit-run-session-promotion.json`
- `py_compile`: passed
- focused LiveKit cookbook:
  `1 passed, 5 warnings in 3.81s`
- release milestone test:
  `1 passed, 5 warnings in 462.46s (0:07:42)`
- `uv run ruff check .`: passed
- `git diff --check`: passed
- CLI release-check: passed
- full suite: `298 passed, 6 warnings in 835.34s (0:13:55)`

Provider-response handoff verification passed:

```bash
uv run python -m py_compile \
  examples/sdk_framework_adapter_provider_response.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_provider_response_example_runs \
  -q
uv run pytest \
  tests/test_config_and_facades.py::test_provider_response_framework_adapter_preserves_nested_tool_evidence \
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
- focused provider-response cookbook:
  `1 passed, 5 warnings in 8.21s`
- focused provider-response runtime evidence:
  `1 passed, 5 warnings in 14.42s`
- release milestone test:
  `1 passed, 5 warnings in 486.47s (0:08:06)`
- `uv run ruff check .`: passed
- `git diff --check`: passed
- CLI release-check: passed
- full suite:
  `298 passed, 6 warnings in 1533.12s (0:25:33)`
- selected release-proof:
  `status=passed`, selected checks `release_check` and `git_diff_check` passed,
  wrote the then-current provider-response selected proof artifact

Browser/CUA trace handoff verification passed:

```bash
uv run python -m py_compile \
  examples/sdk_framework_adapter_browser_cua_trace.py \
  src/agent_learning/trinity.py \
  tests/test_cli_examples.py \
  tests/test_config_and_facades.py
uv run pytest \
  tests/test_cli_examples.py::test_sdk_framework_adapter_browser_cua_trace_example_runs \
  -q
uv run pytest \
  tests/test_config_and_facades.py::test_browser_cua_framework_adapter_preserves_visual_action_trace \
  -q
uv run pytest \
  tests/test_config_and_facades.py::test_agent_learn_release_check_reports_v1_milestones \
  -q
uv run ruff check .
git diff --check
uv run python -m agent_learning.cli release-check --project-root . --quiet
uv run pytest -q
uv run python -m agent_learning.cli release-proof \
  --project-root . \
  --only release_check \
  --only git_diff_check \
  --output /tmp/agent-learning-release-proof-selected.json \
  --quiet
```

Result:

- `py_compile`: passed
- focused Browser/CUA cookbook:
  `1 passed, 5 warnings in 11.68s`
- focused Browser/CUA runtime evidence:
  `1 passed, 5 warnings in 20.51s`
- release milestone test:
  `1 passed, 5 warnings in 1132.01s (0:18:52)`
- `uv run ruff check .`: passed
- `git diff --check`: passed
- CLI release-check: passed
- full suite:
  `298 passed, 6 warnings in 1567.07s (0:26:07)`
- selected release-proof:
  `status=passed`, selected checks `release_check` and `git_diff_check` passed,
  wrote the then-current Browser/CUA selected proof artifact

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
- `examples/sdk_framework_adapter_livekit_run_session_promotion.py`
- `examples/sdk_framework_adapter_provider_response.py`
- `examples/sdk_framework_adapter_browser_cua_trace.py`
- `examples/sdk_framework_adapter_workflow_trace.py`
- `examples/sdk_framework_adapter_orchestration_trace.py`
- `examples/sdk_framework_adapter_lifecycle_trace.py`
- `examples/sdk_framework_adapter_mcp_tool_session.py`
- `examples/sdk_framework_adapter_a2a_protocol_trace.py`
- `examples/sdk_framework_adapter_agent_control_plane.py`
- `tests/test_cli_examples.py`
- `tests/test_config_and_facades.py`
- `README.md`
- `V1_RELEASE_ROADMAP.md`
- `internal-docs/framework-adapter-probe-research.md`
- `internal-docs/framework-adapter-probe-readiness-research.md`
- `internal-docs/environment-10x-robustness-research.md`
- `internal-docs/agent-control-plane-readiness-research.md`
- `internal-docs/mcp-tool-session-adapter-research.md`
- `internal-docs/a2a-protocol-adapter-research.md`
- `internal-docs/workflow-graph-probe-research.md`
- `internal-docs/orchestration-trace-adapter-research.md`

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

1. Additional non-protocol framework control-plane promotion.
   - Goal: promote another local framework-shaped adapter through the BYO probe
     path only if it adds a new runtime contract beyond the protocol, workflow,
     orchestration, browser, provider, and realtime shapes already covered.
   - Constraint: keep it local-only and map claims to release-check metrics.
2. Full release proof hardening.
   - Goal: run and preserve the full `agent-learn release-proof --project-root .`
     artifact on the final tree, including Python build and TypeScript
     build/test evidence.
   - Constraint: do not call v1 complete until the artifact has
     `summary.ready=true` and the working tree is clean except owner-approved
     generated files.

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
git add src/agent_learning/trinity.py \
  tests/test_config_and_facades.py \
  README.md \
  V1_RELEASE_ROADMAP.md \
  internal-docs/framework-adapter-probe-readiness-research.md \
  internal-docs/environment-10x-robustness-research.md \
  internal-docs/a2a-protocol-adapter-research.md \
  internal-docs/mcp-tool-session-adapter-research.md \
  internal-docs/v1-engineering-handover.md
git commit -m "Promote protocol adapter probes"
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
