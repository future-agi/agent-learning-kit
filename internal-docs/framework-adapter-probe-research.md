# Framework Adapter Probe Research Note

Date: 2026-06-07

## Why This Exists

The agent-learning-kit framework surface needs a fast local proof before a user
commits to a full simulation manifest or optimizer run. The probe API added in
this increment is the smallest executable contract for that: wrap any local
framework object or callable, run representative cases, and emit structured
evidence for method, input shape, output, tool calls, events, runtime trace, and
the local-first adapter contract.

## Current Framework Signals

- LangChain still exposes `Runnable` as the common unit with `invoke`/`ainvoke`,
  batching, streaming, schemas, tags, and metadata. Probe implication: adapters
  must accept dict-style and message-style inputs and preserve runtime metadata.
  Source: https://api.python.langchain.com/en/latest/core/runnables/langchain_core.runnables.base.Runnable.html
- LangGraph-backed agents follow a graph API with `invoke` and `stream`, and
  pass message state into the graph. Probe implication: framework evidence must
  include method, input shape, state keys, and trace events, not just final text.
  Sources: https://docs.langchain.com/oss/python/langchain/agents and
  https://reference.langchain.com/javascript/langchain-langgraph/index/CompiledGraph/invoke
- AutoGen AgentChat agents use `run` and `run_stream`, return task/message
  histories, and are explicitly stateful. Probe implication: streaming and
  multi-message outputs should be normalized into response content, tool calls,
  tool responses, transcript events, and message-history state. Source:
  https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/agents.html
- CrewAI crews call the crew entrypoint with an `inputs` payload. Probe
  implication: adapter discovery must preserve keyword-only input keys such as
  `inputs`, not only positional method names. Source:
  https://docs.crewai.com/en/concepts/crews
- LiveKit Agents is a realtime framework with agent sessions, multimodality,
  tools, handoffs, traces, and media pipelines. Probe implication: voice adapters
  need modality, lifecycle, tool, handoff, and trace evidence without requiring
  LiveKit Cloud during local release checks. Source: https://docs.livekit.io/agents/
- Pipecat centers orchestration around pipelines, frame processors, and data,
  control, and system frames. Probe implication: voice/realtime adapters should
  preserve event/frame categories, ordered runtime evidence, and side-channel
  call settings such as frame direction. Sources:
  https://docs.pipecat.ai/guides/learn/pipeline and
  https://docs.pipecat.ai/server/frames/overview
- Pydantic AI emphasizes typed output validation around agent runs. Probe
  implication: adapter responses should retain structured output metadata and
  state, not flatten everything into only text. Source:
  https://docs.pydantic.dev/dev/examples/pydantic_ai/
- OpenAI-compatible and Anthropic-style provider clients commonly expose nested
  SDK entrypoints such as chat completions or messages create calls. Probe
  implication: adapter discovery and runtime resolution should preserve dotted
  method paths like `chat.completions.create` and `messages.create`, not require
  users to write wrapper functions. Sources:
  https://platform.openai.com/docs/api-reference/chat/create and
  https://docs.anthropic.com/en/api/messages
- Provider responses carry critical evidence in nested response envelopes:
  OpenAI-compatible choices contain assistant messages, finish reasons, usage,
  and tool calls, while Anthropic messages can carry content blocks such as
  `tool_use`. Probe implication: normalization must preserve those nested
  signals as tool calls, events, metadata, and `provider_response` state so
  evals can score provider evidence directly.
- Instructor and OpenAI Agents-style structured output paths use Pydantic or
  dataclass-like response models. Probe implication: `model_dump()`/dataclass
  payloads should normalize into content, tools, events, metadata, and state so
  the evaluator can require typed-output state keys instead of accepting a
  stringified object.

## Benchmark And Optimization Signals

- AgentBench evaluates LLMs as agents across interactive environments, so local
  probes should collect environment-facing actions and not rely on prompt-only
  scores. Source: https://openreview.net/forum?id=zAdUB0aCTQ
- WebArena uses realistic web environments and end-to-end task success, which
  supports treating browser/CUA traces as first-class artifacts rather than
  optional logs. Source: https://arxiv.org/abs/2307.13854
- AgentDojo evaluates indirect prompt injection in tool-using agents; probes and
  manifests need explicit tool/action evidence and local red-team replay paths.
  Source: https://arxiv.org/abs/2406.13352
- tau-bench frames agent evaluation as multi-turn user/tool/policy interaction;
  probes should be able to grow into multi-case, policy-aware evidence before
  optimization. Source: https://arxiv.org/abs/2406.12045
- ToolSandbox adds stateful tool execution, user simulation, and milestone
  checks; local adapter probes should preserve tool calls, state keys, and
  intermediate events. Source: https://arxiv.org/abs/2408.04682
- GEPA shows that optimization of compound AI systems benefits from trajectory
  evidence and reflective search, not just scalar reward. Probe implication:
  candidate optimizers need traceable probe outputs for diagnosis and mutation.
  Source: https://arxiv.org/abs/2507.19457

## Implementation Rule

Keep framework support local-first:

- Accept arbitrary local objects/callables and known preset names.
- Do not import framework packages for release proofs.
- Reject HTTP/HTTPS targets by default; only allow them when a user explicitly
  opts into testing a live workload.
- Emit evidence that can be scored by `framework_adapter_contract_quality`,
  `framework_runtime_contract`, tool metrics, report cards, and optimizer proof
  gates.
- When several adapter shapes are plausible, search method/input-mode candidates
  with `optimize.optimize_framework_adapter_probe()` first. This keeps early
  framework onboarding local and cheap, then promotes the selected adapter into a
  full simulation manifest, framework certification, or framework-runtime
  optimization.
- Treat `astream`, `stream`, `stream_events`, and `run_stream` as first-class
  local adapter candidates. If a selected candidate emits chunks, the probe
  should preserve normalized streaming signals and promotion should require both
  framework-runtime streaming and streaming-trace coverage.
- Preserve structured typed outputs from objects/dataclasses or `model_dump()`
  payloads whose normalized form contains `content`, `tool_calls`, `events`,
  `state`, or `metadata`. Promotion should include observed typed state keys in
  the generated `framework_runtime_contract` so typed output cannot silently
  degrade into plain text.
- Preserve keyword-input call contracts from method signatures. If discovery
  selects `kickoff(inputs=...)`, `run(task=...)`, or `run(user_prompt=...)`, the
  adapter candidate, probe proof, promoted manifest, runtime trace, and
  generated eval config should all carry the selected `input_key`.
- Preserve static side-keyword contracts for native framework entrypoints. If a
  runnable method needs `process_frame(frame=..., direction=...)` or a similar
  payload-plus-kwargs shape, the adapter candidate, contract, probe summary,
  promoted manifest, runtime trace, and generated eval config should carry the
  selected `input_kwargs_keys`.
- Preserve nested SDK method paths. If discovery selects
  `chat.completions.create` or `messages.create`, the adapter candidate, probe
  proof, promoted manifest, runtime trace, and generated eval config should
  carry the full dotted method path instead of only the leaf method name.
- Preserve provider response objects. `choices[].message.tool_calls`, finish
  reasons, usage blocks, and content `tool_use` blocks should normalize into
  ordinary `AgentResponse.tool_calls`, `provider_choice` /
  `provider_tool_call` events, provider metadata, and `provider_response` state.
  Verified provider-response adapters also participate in native adapter
  promotion and `environment_10x_robustness` when explicit candidates preserve
  required provider kwargs such as `model`.
- Preserve framework transcript histories. `TaskResult(messages=[...])`,
  message-history objects, tool-call request events, and tool-call execution
  events should normalize into ordinary tool calls, tool responses, transcript
  events, and `message_history` state. Promotion should derive
  `framework_transcript_quality` checks for observed speakers, turn count, tool
  sequence, termination, output text, and message-history state from the selected
  proof.
- Preserve framework coordination semantics from transcripts. Messages carrying
  `handoff_to`, `recipient`, review, or reconciliation fields should normalize
  into `framework_handoff`, `framework_review`, and
  `framework_reconciliation` events plus `framework_handoffs` state so
  multi-agent framework handoffs can be scored without a separate parser.
  Generated transcript gates should also check handoff source/target/task specs,
  participant coverage, review counts, reconciliation counts, and termination.
- `agent-learn release-check` now includes `framework_adapter_io_readiness` for
  these advanced IO contracts. The gate executes the streaming, typed-output,
  keyword-input, side-kwargs, nested-method, provider-response, message-history,
  and handoff-transcript cookbooks locally, then verifies promoted manifest
  fields, runtime summaries, normalized state, events, artifacts, transcript
  evidence, `provider_response` state, and required metric floors.
- Preserve framework trace export semantics. Local outputs carrying OTLP-style
  `resourceSpans` / `scopeSpans`, TraceAI/Future AGI wrappers, or explicit
  `framework_trace` span/event records should normalize into `framework_trace`
  state, trace artifacts, `framework_trace_*` events, tool calls extracted from
  tool spans, adapter conformance summaries, and selected-output-derived
  `framework_trace_coverage` gates. This is a sibling release-check gate,
  `framework_trace_export_readiness`, not part of
  `framework_adapter_io_readiness`.
- Preserve realtime framework trace semantics. Local Pipecat-style frame exports
  and LiveKit-style session event exports should normalize into
  `realtime_trace` state, trace artifacts, `realtime_frame`,
  `realtime_tool_call`, `realtime_tool_response`, `realtime_transcript`, and
  `realtime_lifecycle` events plus selected-output-derived realtime
  coverage/quality gates so voice adapters can be scored without a hosted room
  or imported framework package.
- Preserve framework memory trace semantics. Local framework outputs carrying
  LangGraph-style checkpoints, Mem0/Zep-style memory operations, memory records,
  stores, retrieval/search results, or memory governance policies should
  normalize into `framework_memory`, `retrieval_memory`, and
  `agent_memory_lineage` state plus `framework_memory_*` events so framework
  adapters and memory-layer evals share the same evidence path, including
  selected-output-derived memory lineage/retrieval gates in generated adapter
  eval configs.
- Preserve framework browser/CUA trace semantics. Local framework outputs
  carrying browser/computer-use actions, DOM/screenshot snapshots, Playwright-
  like trace fields, storage/runtime/network evidence, mutation packs,
  screenshot diffs, or prompt-injection surfaces should normalize into
  `browser_cua` state, browser trace/screenshot artifacts, `browser_*` events,
  and browser tool calls so computer-use agents can be optimized through the
  same framework adapter path, with selected-output-derived browser trace,
  action outcome, grounding, and mutation-resilience gates in generated adapter
  eval configs. Verified Browser Use `execute_task(dict)` CUA trace adapters
  also participate in native adapter promotion and `environment_10x_robustness`.
- Preserve workflow graph execution semantics. Local framework outputs carrying
  graph nodes/edges, workflow steps, checkpoints, state history, route
  decisions, interrupts, replay, or step-level tool evidence should normalize
  into `workflow_trace` state, workflow trace artifacts, `workflow_*` events,
  and ordinary tool calls so LangGraph, CrewAI Flow, LlamaIndex Workflow, and
  similar orchestrators can be optimized through the same adapter path, with
  selected-output-derived workflow trace coverage and graph-quality gates.
  Verified workflow trace adapters also participate in native adapter promotion
  and `environment_10x_robustness`.
- Preserve orchestration control semantics. Local framework outputs carrying
  supervisor delegation, agent spawn, handoffs, communication, aggregation, stop
  decisions, retries, recovered errors, latency, cost, final coordination state,
  or step-level tool evidence should normalize into `orchestration_trace` state,
  trace artifacts, `orchestration_*` events, ordinary tool calls/responses, and
  selected-output-derived orchestration coverage/quality gates. Verified
  orchestration trace adapters also participate in native adapter promotion and
  `environment_10x_robustness`.
- Preserve lifecycle reliability semantics. Local framework outputs carrying
  setup, tool registration, sessions, invocation errors, retries, recovery,
  streaming, checkpoints, cancellation, resume, or cleanup evidence should
  normalize into `framework_lifecycle_trace` state, trace artifacts,
  `framework_lifecycle_*` events, and selected-output-derived lifecycle
  coverage/quality gates so reliability regressions are visible during adapter
  optimization.
- Preserve A2A/Agent2Agent protocol semantics. Local framework outputs carrying
  agent cards, `SendMessage` JSON-RPC records, messages, tasks, status updates,
  artifact updates, or protocol artifacts should normalize into
  `a2a_protocol_trace` state, trace/json artifacts, `a2a_*` events, and
  selected-output-derived A2A coverage/quality gates so remote-agent
  collaboration can be optimized through the same adapter path.
- Preserve MCP tool protocol semantics. Local framework outputs carrying MCP
  `tools/list`, `tools/call`, resources, JSON-RPC request/result records, or
  `{tools, calls}` fixtures should normalize into `mcp_tool_session` state,
  trace artifacts, `mcp_*` events, ordinary tool calls, tool responses, and
  selected-output-derived MCP coverage/quality gates so MCP client/server
  integrations can be optimized through the same adapter path.
- Use `optimize.build_framework_run_manifest_from_probe_optimization()` for the
  promotion step when the selected probe should become a normal
  `agent-learning.run.v1` manifest. The promoted manifest must retain the probe
  proof, selected contract metadata, method/input mode, target, and optional
  `agent_report` config so the next run is evaluated through the same simulator
  path as hand-written framework manifests.
