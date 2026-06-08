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
  multi-message outputs should be normalized into response content, events, and
  tool evidence. Source:
  https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/agents.html
- LiveKit Agents is a realtime framework with agent sessions, multimodality,
  tools, handoffs, traces, and media pipelines. Probe implication: voice adapters
  need modality, lifecycle, tool, handoff, and trace evidence without requiring
  LiveKit Cloud during local release checks. Source: https://docs.livekit.io/agents/
- Pipecat centers orchestration around pipelines, frame processors, and data,
  control, and system frames. Probe implication: voice/realtime adapters should
  preserve event/frame categories and ordered runtime evidence. Sources:
  https://docs.pipecat.ai/guides/learn/pipeline and
  https://docs.pipecat.ai/server/frames/overview
- Pydantic AI emphasizes typed output validation around agent runs. Probe
  implication: adapter responses should retain structured output metadata and
  state, not flatten everything into only text. Source:
  https://docs.pydantic.dev/dev/examples/pydantic_ai/

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
