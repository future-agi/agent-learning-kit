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
6. Use the TypeScript eval surface through `@future-agi/agent-learning-kit`.
7. Export artifacts that Future AGI can render as UI/UX, observability, eval,
   simulation, red-team, and optimizer results.
8. Run a promptfoo-style CLI workflow without writing platform code.
9. Verify release readiness with `agent-learn doctor` and
   `agent-learn release-check`, then cut V1 with
   `agent-learn release-proof`.
10. Prove the Agent Learning framework/environment layer is the primary
   robustness surface by keeping at least ten independent local evidence axes
   green across replay, simulation, evals, optimizer recovery, adapters,
   protocols, browser/CUA, realtime, memory, retrieval hooks, multi-agent,
   red-team, and regression workflows. OpenEnv and Gymnasium are compatibility
   inputs, not the product center.

## Milestones

Each milestone has a small release gate. `agent-learn release-check --project-root .`
is the V1 source of truth for these gates, including research-backed red-team
coverage across required examples, canonical corpus rows, attack types,
surfaces, source lineage, UI/action/report readiness, and executable
framework/provider contract readiness. `agent-learn release-proof --project-root .`
is the heavier release-cut artifact; it runs release-check, full-repo ruff,
pytest, Python package build, TypeScript package build/test, and `git diff --check`, then stores their command
evidence in `agent-learning.release-proof.v1`.

## Release-Cut Breakdown

| Milestone | Release promise | Executable gate |
| --- | --- | --- |
| M0 | One public SDK boundary | `single_public_boundary`, `typescript_sdk_consolidation_boundary` |
| M1 | Promptfoo-style CLI and examples | `cli_command_surface`, `v1_examples_present` |
| M2 | Local simulation and evaluation | `local_sim_eval_examples_present`, `task_artifact_evaluation_readiness`, `task_evaluation_synthesis_readiness`, `task_world_optimizer_readiness`, `evaluation_hook_probe_readiness`, `evaluation_hook_readiness` |
| M3 | Native AgentOptimizer evidence scoring | `native_optimizer_evidence_components`, `generic_target_optimizer_readiness`, `framework_adapter_target_optimizer_readiness`, `multi_agent_target_optimizer_readiness`, `memory_target_optimizer_readiness`, `orchestration_target_optimizer_readiness`, `workflow_target_optimizer_readiness`, `workflow_target_profile_matrix_readiness`, `optimizer_governance_readiness`, `optimizer_portfolio_readiness`, `world_hooks_readiness` |
| M4 | Research-backed red-team core | `redteam_core_examples_present`, `redteam_research_coverage`, `redteam_corpus_execution_readiness`, `redteam_readiness_certification`, `redteam_society_causal_readiness`, `redteam_attack_evolution_readiness` |
| M5 | Future AGI UI/action/report artifacts | `schema_kind_contract`, `ui_action_report_readiness`, `regression_artifact_readiness`, `harness_diagnosis_readiness`, `agent_control_plane_readiness` |
| M6 | Framework/provider simulation surface, including the Agent Learning environment robustness bar | `framework_provider_examples_present`, `framework_provider_contract_readiness`, `multi_framework_runtime_readiness`, `agent_integration_readiness`, `external_agent_adapter_readiness`, `environment_replay_optimizer_readiness`, `framework_environment_replay_adapter_readiness`, `framework_trace_export_readiness`, `framework_http_transport_readiness`, `framework_websocket_transport_readiness`, `framework_adapter_matrix_optimization_readiness`, `framework_optimizer_readiness`, `multi_agent_room_probe_readiness`, `framework_adapter_probe_readiness`, `framework_adapter_io_readiness`, `protocol_adapter_readiness`, `browser_realtime_adapter_readiness`, `browser_cua_probe_readiness`, `realtime_stack_probe_readiness`, `memory_layer_probe_readiness`, `stateful_framework_adapter_readiness`, `workflow_hook_readiness`, `retrieval_hook_readiness`, `framework_adapter_trinity_suite_readiness`, `orchestration_stack_probe_readiness`, `trinity_stack_probe_readiness`, `environment_10x_robustness` |
| M7 | Packaging and release proof | `release_docs_present`, `package_metadata`, `agent-learn release-proof` |

### M0: SDK Consolidation Boundary

Status: complete for the Python and TypeScript SDK boundary; still keep the gate
green as features move.

Acceptance gates:

- Public distribution is `agent-learning-kit`.
- Public import namespace is `agent_learning`.
- Public CLI is `agent-learn`.
- Public TypeScript package is `@future-agi/agent-learning-kit`.
- `simulate`, `evals`, `redteam`, `optimize`, `suite`, and `capabilities` are
  importable through `agent_learning`.
- `fi.simulate`, `fi.evals`, and `fi.opt` remain vendored engine internals.
- No new public work lands in `simulate-sdk`, `ai-evaluation`, or `agent-opt`.
- Legacy TypeScript eval source is marker-only; the moved source lives in
  `agent-learning-kit/typescript/agent-learning-kit`.

Verification:

- `agent-learn doctor`
- `PYTHONPATH=src python -m agent_learning.cli release-check --project-root . --quiet`
- `PYTHONPATH=src python -m pytest tests/test_config_and_facades.py::test_agent_learn_doctor_reports_module_availability -q`

### M1: Promptfoo-Style CLI

Status: mostly complete.

Acceptance gates:

- `agent-learn run`, `eval`, `eval-artifact`, `eval-task`, `redteam`,
  `redteam-corpus`, `optimize`, `optimize-eval`, `optimize-suite`, `suite`,
  `report`, `replay`, `promote-to-regression`, `actions`, `action-run`,
  `action-optimize`, `trust`, `capabilities`, `doctor`, `release-check`,
  `release-proof`, and `init` are available.
- Each command writes JSON output; core workflows also write JUnit, SARIF, and
  Markdown where supported.
- CLI examples use `agent-learning-kit` and `agent-learn`, not legacy SDK names.

Verification:

- `PYTHONPATH=src python -m pytest tests/test_cli_examples.py -q`

### M2: Local Simulation And Evaluation

Status: mostly complete.

Current checkpoint:

- Task artifact evaluation readiness is now an executable release-check gate:
  `examples/sdk_task_evaluation.py`, direct task-evidence file evaluation,
  direct saved run-artifact evaluation, and
  `examples/artifact_task_eval_suite.json` must run locally, emit
  `agent-learning.task-evidence.v1`, `agent-learning.artifact-evaluation.v1`,
  and `agent-learning.eval.v1` evidence, preserve task/framework/world state,
  and close task-completion, tool, world-contract, framework-runtime, memory,
  source-grounding, and secret-leakage metrics.
- Task evaluation synthesis readiness is now an executable release-check gate:
  `examples/sdk_task_evaluation_synthesis.py` must evaluate arbitrary task
  evidence without a hand-written config by inferring task criteria, required
  tools, forbidden patterns, source grounding, world/framework/retrieval/memory
  state requirements, and metric weights from the evidence itself.
- Task/world optimizer readiness is now an executable release-check gate:
  `examples/sdk_task_world_optimization.py` must run locally, start from a
  world contract with no transitions, search both
  `agent.responses.0.tool_calls` and
  `simulation.environments.0.data.transitions`, select
  `apply_world_transition` plus the `approve_refund` world transition, and
  finish with terminal world status `success` and `refund.status=approved`.
- Evaluation hook probe readiness is now an executable release-check gate:
  `examples/sdk_evaluation_hook_probe_optimization.py` must run a localhost
  task evaluator, select the policy-grounded candidate over the generic
  candidate, pass native evaluation-hook proof checks, promote to
  `agent-learning.run.v1`, execute the promoted run, and close
  `external_task_quality`, source-grounding, secret-leakage, task-completion,
  and tool-schema metrics without external evaluator credentials.
- Direct evaluation hook readiness is now an executable release-check gate:
  `examples/sdk_evaluation_hook_optimization.py` must run a local authenticated
  evaluator hook, select the policy-grounded candidate over generic and
  secret-leaking candidates, attach native L3 evaluation-hook proof, prove
  redacted auth plus no serialized secret, and close external-task,
  task-completion, and secret-leakage metrics.

Acceptance gates:

- `agent-learning.run.v1`, `agent-learning.eval.v1`, and
  `agent-learning.artifact-evaluation.v1` artifacts are stable.
- Simulation supports task worlds, framework traces, lifecycle traces, memory,
  orchestration, browser/CUA, voice/realtime, workspace import, agent integration,
  and framework certification fixtures.
- Evals score task completion, tools, world contracts, red-team readiness,
  framework traces/lifecycle/capability/probe/portability, memory lineage,
  optimizer traces, behavior entropy, and artifact actions.
- AgentOptimizer can repair task/world runs by searching agent behavior and
  world/environment state, with terminal task state validated by local evals.

Verification:

- Full local suite: `PYTHONPATH=src python -m pytest -q`

### M3: AgentOptimizer And Native Evidence Scoring

Status: mostly complete.

Current checkpoint:

- Generic target optimizer readiness is now an executable release-check gate:
  `examples/sdk_target_optimization.py` must run local `optimize_target()`,
  search exactly `simulation.environments.0.data.transitions`, keep `agent`,
  `agent.responses`, and `agent.responses.0.tool_calls` out of the optimizer
  search/patch set, preserve the fixed scripted agent, and select the
  `approve_refund` world transition with terminal world status `success`.
- Framework adapter target optimizer readiness is now an executable
  release-check gate: `examples/sdk_framework_adapter_target_optimization.py`
  must run local `optimize_target()`, search exactly `agent.method`, keep
  whole-agent, prompt, response, and world-transition paths out of the patch set,
  preserve the fixed local framework adapter config, and select `execute_task`
  with `dict` input plus passing L3 framework runtime proof.
- Multi-agent target optimizer readiness is now an executable release-check
  gate: `examples/sdk_multi_agent_target_optimization.py` must run local
  `optimize_target()`, search exactly
  `simulation.environments.0.data.participants`, keep whole-agent, prompt,
  framework-method, and world-transition paths out of the patch set, preserve
  the fixed scripted agent and room contract fields, and select the
  planner/retriever/critic roster with passing L3 multi-agent coordination
  proof.
- Memory target optimizer readiness is now an executable release-check gate:
  `examples/sdk_memory_target_optimization.py` must run local
  `optimize_target()`, search exactly
  `simulation.environments.1.data.operations`, keep whole-agent, prompt,
  framework-method, multi-agent-roster, retrieval-document, and world-transition
  paths out of the patch set, preserve the fixed scripted agent, retrieval
  memory, stores, policies, lineage, observability, and artifacts, and select
  audited read/write/recall operations with passing L3 memory-lineage proof.
- Orchestration target optimizer readiness is now an executable release-check
  gate: `examples/sdk_orchestration_target_optimization.py` must run local
  `optimize_target()`, search exactly
  `simulation.environments.1.data.spans`, keep whole-agent, prompt,
  framework-method, world-transition, retrieval-document, memory-operation, and
  multi-agent-roster paths out of the patch set, preserve the fixed scripted
  agent plus world/retrieval/memory/multi-agent stack, and select the LangGraph
  `planner.invoke` framework span with passing L3 orchestration-stack proof.
- Workflow graph target optimizer readiness is now an executable release-check
  gate: `examples/sdk_workflow_target_optimization.py` must run local
  `optimize_target()`, search exactly
  `simulation.environments.0.data.trace`, keep prompt, whole-agent,
  framework-method, hook, and endpoint paths out of the patch set, preserve the
  fixed scripted agent and task, and select a LangGraph runtime workflow trace
  with cross-framework source evidence for LangGraph, CrewAI, and LlamaIndex
  plus graph topology, route decisions, checkpoints, replay, interrupts,
  writes, final state, and step-level tool evidence.
- Workflow target profile matrix readiness is now an executable release-check
  gate: `examples/sdk_workflow_target_profile_matrix.py` must run the same
  generic target path as separate LangGraph, CrewAI, LlamaIndex, LangChain,
  Pipecat, and LiveKit profiles, select the strong trace for every profile,
  close workflow trace coverage/graph quality/tool/artifact/task metrics, and
  keep the Agent Learning workflow target contract primary while treating
  outside framework shapes as compatibility inputs. The same gate now renders the
  `workflow_target_profile_matrix` report/action card, verifies catalog actions,
  and exports profile evidence through `action-run`.
- Optimizer governance readiness is now an executable release-check gate:
  `examples/sdk_optimizer_governance_optimization.py` must run local search over
  weak and governed optimizer-society traces, select the governed
  `SocietyAgentOptimizer` trace, preserve candidate lineage and top-rank
  governance checks, and close optimizer trace coverage/quality plus tool
  selection metrics.
- Optimizer portfolio readiness is now an executable release-check gate:
  `examples/sdk_optimizer_portfolio_optimization.py` must run local search over
  weak and verified optimizer backend portfolios, select `bandit`, prove three
  completed backend runs and two consensus backends, close
  `optimizer_portfolio_quality` plus `optimizer_portfolio_coverage`, and keep
  proof/component/security evidence local-only. This makes optimizer backend
  selection executable evidence for the testing/simulation/optimization trinity.
- World hooks readiness is now an executable release-check gate:
  `examples/sdk_world_hooks_optimization.py` must optimize the native
  in-process world-state hook bundle, pass L3 world-hook proof, emit the
  `world_hooks` report/action card, export the hook contract, promote a
  local-only regression, and replay it with world-hook and world-contract
  metrics closed.

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

Status: gated and passing for the local V1 corpus/campaign contract.

Current checkpoint:

- `agent-learn release-check` now executes the canonical local
  `examples/redteam_corpus.json` through the same
  `build_redteam_corpus_campaign()` path used by `agent-learn redteam-corpus`.
- The gate requires all 12 canonical corpus rows to become local campaign
  evidence with 12 coverage cells, 12 executed cells, 12 passed runs,
  artifact-backed trajectories, mapped findings, implemented mitigations, no
  blocking gaps, all required research-backed attack types and surfaces, and
  the expected `agent_learning_kit`/`local_cli`/`chat` execution context.
- Red-team readiness certification is now an executable release-check gate:
  `examples/sdk_redteam_readiness_certification_optimization.py` must optimize
  weak versus verified workspace/import/campaign/trust/control/readiness
  bundles, select the zero-gap readiness candidate, prove all five components
  ready, close campaign artifacts/findings/mitigations across prompt-injection
  and credential-exfiltration cells, and pass red-team readiness metrics.
- Red-team society plus causal attribution readiness is now an executable
  release-check gate: `examples/sdk_redteam_society_optimization.py` and
  `examples/sdk_redteam_causal_attribution_optimization.py` must run locally,
  preserve the required Agent Learning red-team council roles, close the
  25-cell campaign matrix, map the causal DAG to root causes, mitigations, and
  evidence, and keep proof/secret checks clean. This is the pen-testing leg of
  the Agent Learning testing/simulation/pen-testing trinity.
- Adaptive loop plus attack-evolution readiness is now an executable
  release-check gate: `examples/sdk_redteam_adaptive_loop_optimization.py` and
  `examples/sdk_redteam_attack_evolution_optimization.py` must run locally,
  select hardened adaptive/evolution candidates, satisfy proof and metric
  floors, prove shrink/promotion/replay/action-card evidence, and keep secret
  leakage/security checks clean.

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
  surfaces, source URLs, executable local corpus campaign evidence, and
  executable red-team readiness certification before adaptive attack search.

Next implementation focus:

- Keep the required local red-team corpus, campaign, attack-evolution,
  long-horizon, persistent-state, society, causal-attribution, and autonomous
  task-world examples present and passing.
- Add native fixtures only when release-check exposes a concrete execution,
  coverage, or UI-readiness gap.

### M5: Future AGI UI/UX Artifact Contract

Status: in progress.

Current checkpoint:

- `agent-learn release-check` now gates representative UI/action/report
  readiness across run, action-run, optimization, red-team, red-team campaign
  optimization, provider-integration optimization, and suite artifacts. It
  renders reports, builds action catalogs, verifies required report sections,
  required UI card keys, required action ids, saved `action-run` output
  evidence, and scans generated UI payloads for key-like secret markers.
- Regression artifact readiness is now an executable release-check gate:
  `examples/sdk_regression_artifact_suite.py` must run the baseline, compare,
  report, promote-to-regression, and replay lifecycle locally, admit and freeze
  every child artifact, promote one red-team finding into an
  `adversarial_attack_pack` regression manifest, and replay with pass rate 1.0.
- The same release-check now runs a local retrospective harness optimization and
  proves the rendered `harness_diagnosis` card, diagnosis actions, retrospective
  rollout plan, retrospective-harness proof, required diagnosis layers, and 2026
  research-source lineage.
- Provider integration fixtures use UI-safe credential-slot references instead
  of key-like labels such as `*_API_KEY`; actual real-key execution remains
  declared through env requirements and explicit live-target runs.
- Agent control-plane readiness is now executable: release-check runs the SDK
  optimizer and direct simulation cookbooks, verifies trust-boundary controls,
  runtime control-plane controls, report events/artifacts, optimizer governance,
  output roundtrips, and 1.0 control-plane/trust-boundary metrics.

Acceptance gates:

- Every major artifact includes `kind`, `schema_version`, `status`, `summary`,
  `actions`, `outputs_written`, and renderable report payloads where applicable.
- `agent-learn report` and `agent-learn actions` expose UI-ready cards and
  executable actions for simulation, eval, red-team, optimization, replay,
  promotion, and downloads.
- Harness diagnosis cards expose layer attribution, repair operators, rollout
  plans, and reproducible follow-up commands for failed or weak harness layers.
- Autonomous-agent runtime control planes expose identity, permissions,
  sandboxing, approval, audit, memory isolation, egress, allowlist, data
  boundary, secret handling, budgets, rollback, kill-switch, circuit-breaker,
  containment, rate-limit, risk-scoring, and drift-detection evidence.
- Artifacts are safe to send to Future AGI for observability/evals/simulation UI
  without leaking local keys.

Next implementation focus:

- Extend the same gate to additional artifact classes only when V1 adds a new
  user-visible report/action surface.

### M6: Framework/Provider Simulation Surface

Status: gated and passing for the local V1 contract.

Current checkpoint:

- `agent-learn release-check` now builds
  `agent-learning.framework-adapter-contract-matrix.v1` for LangChain,
  LangGraph, LlamaIndex, OpenAI Agents, AutoGen, CrewAI, PydanticAI, LiveKit,
  Pipecat, Browser Use, OpenEnv, Gymnasium, MCP, and A2A.
- The gate requires local executable fixture targets, `trace_runtime`, text,
  voice, and CUA modality coverage, `in_process` transport, no external service
  dependency, no external targets, and no HTTP/WebSocket values in
  representative provider manifests.
- Representative manifests are validated for text framework simulation,
  LiveKit/Pipecat voice framework simulation, and LiveKit realtime
  voice/streaming trace simulation, plus a static environment replay
  compatibility manifest that accepts OpenEnv/Gymnasium-shaped runtime state,
  events, artifacts, coverage, and quality gates.
- Multi-framework runtime readiness is now an executable M6 release-check gate
  through `multi_framework_runtime_readiness`:
  `examples/sdk_multi_framework_simulation.py` must run locally across
  LangChain, LangGraph, LlamaIndex, OpenAI Agents, AutoGen, CrewAI, PydanticAI,
  Pipecat, LiveKit, and a custom orchestrator, require `framework_runtime` plus
  `framework_trace` evidence, validate LiveKit/Pipecat voice modality,
  LangChain/LangGraph `ainvoke`, no external service dependency, and no secret
  leakage. Agent Learning remains the primary runtime and release bar;
  OpenEnv/Gymnasium are compatibility inputs only.
- Agent integration readiness is now an executable release-check gate:
  `examples/sdk_agent_integration_optimization.py` and
  `examples/sdk_agent_integration_simulation.py` must run locally, prove the
  16-provider and 22-channel readiness matrix across LiveKit, Vapi, Retell,
  Bland, ElevenLabs, Deepgram, Agora, Pipecat, Twilio, and TraceAI-supported
  frameworks, close credential/session/observability/eval gaps, emit report and
  rerun actions, and pass integration, framework-trace, voice, and streaming
  metrics.
- External agent adapter readiness is now an executable release-check gate:
  `examples/sdk_external_http_agent_optimization.py` must run against a local
  OpenAI-compatible HTTP target, keep bearer auth redacted, preserve native
  tool-call evidence, and require AgentOptimizer to select the verified
  `openai_chat` adapter with `external_agent_status` evidence before external
  target-agent claims are release-ready.
- Environment replay optimizer readiness is now an executable release-check
  gate through `environment_replay_optimizer_readiness`:
  `examples/sdk_openenv_environment_optimization.py` runs local AgentOptimizer
  bundle search over weak, partial, and verified OpenEnv/Gymnasium-shaped
  replays and must select the verified environment replay with
  `environment_replay_coverage=1.0` and
  `environment_replay_quality=1.0`. The public API should lead with
  `build_environment_replay_optimization_manifest()` and
  `optimize_environment_replay()` while preserving `build_openenv_*`,
  `optimize_openenv()`, and OpenEnv metrics as compatibility aliases.
- Framework environment replay adapter readiness is now an executable
  release-check gate through `framework_environment_replay_adapter_readiness`:
  `examples/sdk_framework_adapter_openenv_trace.py` must
  promote a local environment replay adapter, generate compatibility eval gates,
  normalize reset/step/reward/done/sandbox/failure evidence into `openenv` wire
  state, events, and trace artifacts, and pass environment replay coverage and
  quality metrics. OpenEnv and Gymnasium remain compatibility input shapes, not
  the product center.
- Framework trace export readiness is now an executable release-check gate:
  `examples/sdk_framework_adapter_trace_export.py` must promote a local
  `langgraph.execute_task(dict)` adapter, normalize OTLP-style trace export
  spans into evaluator-visible `framework_trace` state, events, artifacts, and
  tool evidence, and pass framework runtime, adapter-contract, trace coverage,
  and trace quality metrics.
- Framework WebSocket transport readiness is now an executable release-check
  gate: `examples/sdk_framework_adapter_websocket_transport.py` must run a
  local `ws://` loopback framework transport, preserve accepted handshake and
  JSON-frame evidence, redact bearer auth, emit LiveKit framework runtime state,
  framework trace events/artifacts, and close framework runtime, trace coverage,
  trace quality, and tool-selection metrics.
- Framework optimizer readiness is now an executable release-check gate:
  `examples/custom_framework_optimization.json`,
  `examples/social_memory_framework_optimization.json`,
  `examples/world_framework_memory_optimization.json`,
  `examples/multi_agent_framework_handoff_optimization.json`,
  `examples/framework_certification_optimization.json`, and
  `examples/framework_import_repair_optimization.json` must run locally, select
  the expected best adapter/world/framework/memory/multi-agent/certification/
  import-repair candidates, preserve candidate lineage, pass required optimizer
  metrics, and emit native optimizer proofs where available.
- Workspace import certification readiness is now an executable release-check
  gate: `examples/sdk_workspace_import_certification_optimization.py` must run
  locally, select the verified workspace plus framework-import bundle, prove
  local-only execution, close workspace/import metrics and component evidence,
  emit the native workspace-import certification proof, render the
  `workspace_import_certification` report/action surface, expose report,
  rerun, and export action-run evidence, promote and replay a local regression,
  and keep secret/security buckets empty before arbitrary project/framework
  import surfaces count as simulation and optimization targets.
- Multi-agent room probe readiness is now an executable release-check gate:
  `examples/sdk_multi_agent_room_probe_optimization.py` must select a local
  planner/retriever/critic room, close role boundaries, handoff contracts,
  review, accepted-source reconciliation, terminal room state, native probe
  proof checks, optimizer governance, promoted run metrics, and the normal
  `agent-learning.run.v1` multi-agent simulation artifact.
- Framework adapter probe readiness is now an executable release-check gate:
  the raw probe, discovery, probe-optimization, auto-discovery optimization,
  explicit promotion, auto-discovery promotion, one-call promotion, and one-call
  run SDK cookbooks must run locally, prove custom `execute_task(dict)` plus
  LangGraph-style `ainvoke(dict)` promotion, pass native probe proofs, prove
  deterministic callable signature plus observed I/O contracts, preserve
  proof/discovery metadata in promoted manifests, and close framework runtime,
  adapter call-contract, observed-I/O, adapter-contract, trace, and tool
  metrics. The same gate now renders the
  `framework_adapter_probe` report/action card and exports the native probe
  proof, callable signature, observed I/O contract, selected probe report,
  contract, and replay lock through report/action artifacts.
- Framework adapter IO readiness is now an executable release-check gate:
  streaming, typed-output, keyword-input, side-kwargs, nested-method,
  provider-response, message-history, and handoff-transcript cookbooks must run
  locally, preserve the selected manifest/runtime contract, normalize state,
  events, artifacts, and transcript evidence, and close the relevant runtime,
  adapter-contract, streaming, transcript, and tool metrics.
- Protocol adapter readiness is now an executable release-check gate:
  `examples/sdk_framework_adapter_mcp_tool_session.py` and
  `examples/sdk_framework_adapter_a2a_protocol_trace.py` must run locally,
  select the protocol-native adapter methods, emit MCP/A2A state, events, and
  artifacts, and pass protocol coverage/quality metrics.
- Browser/realtime adapter readiness is now an executable release-check gate:
  `examples/sdk_framework_adapter_realtime_trace.py` and
  `examples/sdk_framework_adapter_browser_cua_trace.py` must run locally, select
  the local trace-capable adapter methods, emit realtime/browser state, events,
  and artifacts, and pass coverage, grounding, mutation, and quality metrics.
- Browser CUA probe readiness is now an executable release-check gate:
  `examples/sdk_browser_cua_probe_optimization.py` must select hardened local
  `browser_cua` over weak browser-only candidates, pass native browser-CUA proof
  checks, promote to `agent-learning.run.v1`, execute the promoted CUA run, and
  close browser trace/action/outcome/safety/grounding/mutation, selector
  fallback, storage/runtime/network, layout-shift, prompt-injection avoidance,
  and run metrics.
- Realtime stack probe readiness is now an executable release-check gate:
  `examples/sdk_realtime_stack_probe_optimization.py` must select the local
  LiveKit-style support-route stack over weak realtime candidates, pass native
  realtime-stack proof checks, promote to `agent-learning.run.v1`, and close
  voice, timing, streaming, route/tool, no-drop/no-error, and run metrics.
- Memory layer probe readiness is now an executable release-check gate:
  `examples/sdk_memory_layer_probe_optimization.py` must select current
  `doc_refund_2026` retrieval evidence over stale `doc_refund_2025`, pass
  native memory-layer proof checks, promote to `agent-learning.run.v1`, and
  close retrieval attribution, read/write/recall lineage, tenant isolation,
  audit, retention/deletion/redaction, canary, no-gap, and run metrics.
- Stateful framework adapter readiness is now an executable release-check gate:
  `examples/sdk_framework_adapter_memory_trace.py`,
  `examples/sdk_framework_adapter_workflow_trace.py`,
  `examples/sdk_framework_adapter_orchestration_trace.py`, and
  `examples/sdk_framework_adapter_lifecycle_trace.py` must run locally, select
  the local stateful adapter methods, emit memory/workflow/orchestration/
  lifecycle state, events, and artifacts, and pass framework runtime, coverage,
  quality, retrieval, and recovery metrics. The workflow-trace slice must also
  expose a Future AGI-visible `stateful_framework_adapter` report/action card,
  promote a local-only regression, and replay that promoted run with workflow
  coverage, graph-quality, and framework runtime-contract metrics closed.
- Workflow hook readiness is now an executable release-check gate:
  `examples/sdk_workflow_hook_optimization.py` must run a local authenticated
  HTTP workflow hook, select the verified authenticated hook over mocked/missing
  auth candidates, attach native workflow-hook proof, prove secret redaction plus
  `workflow_hooks`/`refund_workflow` runtime state, and count as the
  `authenticated_workflow_hooks` axis in `environment_10x_robustness`.
- Evaluation hook readiness now also feeds the Agent Learning-native 10x bar:
  `examples/sdk_evaluation_hook_optimization.py` must count as the
  `authenticated_evaluation_hooks` axis by proving local authenticated evaluator
  scoring, redacted auth, selected policy-grounded agent lineage, rejected weak
  and secret-leaking candidates, native L3 proof checks, and no serialized
  secret. OpenEnv/Gymnasium compatibility remains compatibility coverage only;
  this is the Agent Learning-native evaluator optimization proof.
- Retrieval hook readiness is now an executable release-check gate:
  `examples/sdk_retrieval_hook_optimization.py` must run a local authenticated
  HTTP retrieval/RAG hook, select the verified authenticated hook over stale
  static or missing-auth candidates, attach native retrieval-hook proof, prove
  redacted auth, current-document citations, `retrieval_memory_trace` state
  trace, and passing retrieval metrics, and count as the
  `authenticated_retrieval_hooks` axis in `environment_10x_robustness`.
  OpenEnv/Gymnasium compatibility remains compatibility coverage only; this is
  the Agent Learning-native local RAG/retrieval optimization proof.
- Framework adapter trinity suite readiness is now an executable release-check
  gate: `examples/sdk_framework_adapter_trinity_suite.py` must pass the local
  run+redteam suite with the same promoted adapter contract, and
  `examples/sdk_framework_adapter_trinity_suite_optimization.py` must optimize
  from a run-only seed to the nested full-trinity `suite.json` job while
  preserving framework runtime, adapter-contract, adversarial, campaign, and
  optimizer-governance evidence.
- Orchestration stack probe readiness is now an executable release-check gate:
  `examples/sdk_orchestration_stack_probe_optimization.py` must select the
  strong local world/framework/retrieval/memory/multi-agent stack over weak
  candidates, pass native orchestration-stack proof checks, promote to
  `agent-learning.run.v1`, execute the promoted orchestration run, and close
  world transition, LangGraph trace, current retrieval citation, governed memory
  lineage, multi-agent review/reconciliation, required tool, source-grounding,
  and run metrics.
- Trinity stack probe readiness is now an executable release-check gate:
  `examples/sdk_trinity_stack_probe_optimization.py` must select the local
  orchestration stack, reuse the same selected agent through a localhost
  evaluation hook, pass native trinity-stack proof checks, promote to
  `agent-learning.run.v1`, execute that promoted run, and close external
  task-quality, world, framework, retrieval, memory-lineage, multi-agent, tool,
  and task metrics.
- Agent Learning environment 10x robustness is now an executable release-check gate:
  `environment_10x_robustness` aggregates the existing local proof outputs and
  requires at least ten independent axes to pass across the Agent Learning
  replay contract, framework simulation, local HTTP framework transport,
  local WebSocket framework transport, framework matrix optimization, local
  evals, optimizer recovery, native adapter probe promotion, protocol routing,
  browser/CUA,
  realtime, memory, multi-agent coordination, authenticated
  evaluation/workflow/retrieval hooks, world orchestration, workspace import
  certification, red-team suite coverage, and regression replay. Workspace
  import certification, local HTTP framework transport, local WebSocket
  framework transport, framework matrix optimization, native adapter probe
  promotion, and authenticated hooks are counted as native proof-backed axes;
  OpenEnv/Gymnasium-shaped traces are compatibility inputs inside that bar.

Acceptance gates:

- Framework certification covers lifecycle, capability, probe, and portability.
- Agent Learning Kit owns the framework/environment robustness target:
  release-check must keep `environment_10x_robustness` green before V1 claims
  material robustness beyond a single environment replay format.
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
- Add real-key live-target checks only for explicitly selected user workloads
  and keep those results out of release metadata.
- Keep the explicit Agent Learning environment robustness target executable:
  support local-first environment replay probes for our reset, step, state,
  reward/done/metadata, sandbox/isolation, failure-injection, protocol/tool
  routing, and container/WebSocket replay contracts. Local HTTP framework
  transport is release-gated by `framework_http_transport_readiness`; local
  WebSocket framework transport is release-gated by
  `framework_websocket_transport_readiness`. Keep extending the same owned
  Agent Learning transport contract rather than making OpenEnv the primary
  system. OpenEnv/Gymnasium shapes should stay compatible inputs, while the
  Agent Learning Kit framework/provider surface remains the owned system of
  record.
- Treat 10x robustness as a measurable release bar, not wording for marketing
  copy: every claim must map to executable artifacts, passing metrics,
  failure-injection coverage, adversarial state coverage, sandbox evidence, and
  local reproducibility before it appears in release notes.
- Keep backing that target with Agent Learning run/eval/optimization artifacts,
  local environment replay gates, framework adapter cookbooks, non-custom
  LangGraph `ainvoke(dict)` promotion evidence, and release-checked agent-opt
  bundle search. OpenEnv-specific fixtures are compatibility coverage only.
- Keep the framework optimizer gate ahead of new claims: agent-opt should prove
  best-candidate selection across custom adapters, social-memory synthesis,
  world/framework/memory stacks, multi-agent framework handoffs, framework
  certification, and framework import repair before V1 claims prompt-agnostic
  optimization.
- Broaden workflow profile-matrix coverage only when the six-profile
  LangGraph/CrewAI/LlamaIndex/LangChain/Pipecat/LiveKit report/action matrix
  stays green. Keep proving local selection over graph topology, route
  decisions, checkpoints, replay, interrupts, and recovery while
  `ai-evaluation` scores workflow trace coverage/graph quality and
  `simulate-sdk` supplies deterministic workflow trace simulation.
- Keep the adapter-probe gate as the BYO-framework entry bar: unknown local
  framework objects should be discoverable, optimizable, promotable, and
  evaluable without external services before adding live framework-specific
  shortcuts.
- Keep extending that target through framework adapters: local
  framework/provider outputs that return OpenEnv/Gymnasium-style reset, step,
  state, reward/done, sandbox, or failure-injection traces must keep
  normalizing into evaluator-visible `openenv` state, artifacts, events, and
  generated OpenEnv quality gates before adding live environment shortcuts.
- Keep trace-export adapters on the same local-first bar: OTLP, TraceAI,
  OpenInference, and framework-native span exports should normalize into
  evaluator-visible `framework_trace` state, events, artifacts, ordinary tool
  evidence, adapter conformance, and generated trace coverage/quality gates
  before relying on hosted observability backends.
- Treat environment compatibility comparisons as evidence, not positioning:
  V1 should exercise deterministic resets, failure-injection scenarios,
  adversarial environment states, tool/action contract drift, transcript/replay
  fidelity, sandbox escape evidence, and optimizer recovery loops across
  representative framework, provider, protocol, browser, voice, and multi-agent
  fixtures before calling the Agent Learning framework materially more robust.
- Keep protocol adapters on the same local-first bar: MCP and A2A release gates
  should prove protocol state, tool/task records, artifacts, generated eval
  gates, and no external service dependency before expanding to additional
  protocol transports.
- Keep browser/CUA and realtime adapters on that same bar: local release gates
  should prove screenshots/DOM/action replay, browser mutation resilience,
  prompt-injection-surface avoidance, voice frames, session events, lifecycle
  events, and realtime tool calls before expanding to live browser or voice
  transports.
- Keep stateful framework adapters on the same bar: local release gates should
  prove governed memory lineage, retrieval attribution, durable workflow
  checkpoints/replay, supervisor orchestration, lifecycle retry/recovery, and
  full runtime-contract evidence, including report/action, regression
  promotion, and replay proof surfaces, before expanding to additional
  framework control planes.
- Keep evaluation hooks on the Agent Learning-native bar: OpenEnv compatibility
  is useful, but authenticated evaluator-hook optimization must remain our own
  local proof surface with redacted auth, candidate lineage, rejected
  secret-leaking candidates, native proof, and the
  `authenticated_evaluation_hooks` 10x axis before adding third-party evaluator
  providers.
- Keep workflow hooks on the Agent Learning-native bar: OpenEnv compatibility is
  useful, but authenticated workflow-hook optimization must remain our own local
  proof surface with redacted auth, state updates, candidate lineage, and replay
  evidence before adding third-party workflow providers.
- Keep retrieval hooks on the Agent Learning-native bar: OpenEnv compatibility
  is useful, but authenticated retrieval/RAG hook optimization must remain our
  own local proof surface with redacted auth, current-document citations, state
  trace, native proof, and the `authenticated_retrieval_hooks` 10x axis before
  adding third-party retrieval providers.

### M7: Release Packaging And Proof

Status: in progress.

Current checkpoint:

- Full-repo `PYTHONPATH=src python -m ruff check .` now passes across the
  public SDK plus vendored `fi.{simulate,evals,opt}` engine tree.
- `agent-learn release-proof` emits one release-cut artifact with command,
  duration, exit-code, timeout, and output-tail evidence for each required local
  proof check.
- The release proof includes `typescript_build` and `typescript_test` for
  `@future-agi/agent-learning-kit`.

Acceptance gates:

- `python -m build` succeeds.
- `pnpm --dir typescript --filter @future-agi/agent-learning-kit build` succeeds.
- `pnpm --dir typescript --filter @future-agi/agent-learning-kit test -- --runInBand --silent` succeeds.
- `agent-learn release-check --project-root .` passes.
- `agent-learn release-proof --project-root .` passes with
  `agent-learning.release-proof.v1` and `ready=true`.
- Full pytest passes with real local keys used by examples/tests.
- Ruff and `git diff --check` pass.
- README, development boundary, roadmap, and examples are aligned.
- Version/classifier are intentionally set for the V1 release.

Verification:

- `PYTHONPATH=src python -m ruff check .`
- `PYTHONPATH=src python -m pytest -q`
- `python -m build`
- `pnpm --dir typescript --filter @future-agi/agent-learning-kit build`
- `pnpm --dir typescript --filter @future-agi/agent-learning-kit test -- --runInBand --silent`
- `git diff --check`
- `PYTHONPATH=src python -m agent_learning.cli release-proof --project-root . --output /tmp/agent-learning-release-proof.json --quiet`

## Current Implementation Order

1. Add `agent-learn release-check` and keep it passing.
2. Use release-check failures to drive V1 work.
3. Keep research-backed red-team campaign/corpus proof coverage gated.
4. Tighten Future AGI UI/action/report artifact gates.
5. Finish provider/framework simulation contracts that are local-first and
   verified with real user-provided target keys only where necessary.
6. Cut V1 only after `agent-learn release-proof --project-root .` passes and
   the saved artifact has `ready=true`.
