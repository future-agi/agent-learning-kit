# Orchestration Trace Adapter Research Note

Date: 2026-06-08

## Sources Checked

- LangChain/LangGraph handoffs:
  https://docs.langchain.com/oss/python/langchain/multi-agent/handoffs
- LangGraph multi-agent graph concepts:
  https://langchain-ai.github.io/langgraph/concepts/multi_agent/
- CrewAI Flows:
  https://docs.crewai.com/en/concepts/flows
- OpenAI Agents SDK handoffs:
  https://openai.github.io/openai-agents-js/guides/handoffs/
- OpenAI Agents SDK tracing:
  https://openai.github.io/openai-agents-python/tracing/

## Adapter Implications

Orchestration evidence is adjacent to, but different from, durable workflow
trace evidence. Workflow traces prove graph execution mechanics such as nodes,
edges, checkpoints, state history, interrupts, and replay. Orchestration traces
prove cross-agent control decisions: supervisor delegation, agent spawn,
handoffs, communication, aggregation, stop decisions, retries, recovered
failures, latency, cost, and the final coordination state.

LangGraph/LangChain handoffs model agent-to-agent transfer through graph control
and state updates. CrewAI Flows expose start/listen/router steps that coordinate
stateful execution. OpenAI Agents SDK handoffs are tool-like delegation actions,
and its tracing surface emits agent, generation, function, guardrail, handoff,
and audio spans. A generic adapter therefore needs a portable graph contract
that can consume explicit orchestration records without forcing every framework
into a single workflow-trace schema.

## Implemented Contract

The generic adapter now recognizes local outputs carrying explicit orchestration
fields such as:

- `orchestration_trace`, `agent_orchestration_trace`, `agent_graph_trace`,
  `coordination_trace`, `multi_agent_orchestration`, `handoff_trace`,
  `delegation_trace`, or `supervisor_trace`.
- `orchestration_nodes`, `orchestration_edges`, `orchestration_steps`,
  `orchestration_records`, `orchestration_events`, and related agent graph or
  coordination field names.
- Dedicated orchestration exports, while generic `trace_export` remains a
  framework-trace input unless an orchestration marker is present.

Those outputs normalize into:

- `orchestration_trace` state with nodes, edges, steps, signals, summary,
  runtime state, and metadata.
- A `trace` artifact with `metadata.kind == "orchestration_trace"`.
- `orchestration_step` and final `orchestration_trace` events.
- Ordinary tool calls and responses extracted from step-level tool evidence.
- Generated adapter-probe eval configs with `required_orchestration_trace`,
  `orchestration_trace_quality`, `orchestration_trace_coverage`, and
  `orchestration_flow_quality` when the selected candidate emits orchestration
  evidence.

The cookbook in `examples/sdk_framework_adapter_orchestration_trace.py` covers a
strong local path: adapter discovery selects `execute_task(dict)`, the adapter
emits LangGraph-style supervisor orchestration records with delegation, handoff,
communication, aggregation, retry recovery, latency, cost, tool, and stop
signals, and the promoted run requires the resulting trace state, events,
artifact, tool evidence, and orchestration coverage/quality metrics.
`agent-learn release-check` now runs this cookbook as
`stateful_framework_adapter_readiness` and as
`orchestration_trace_promotion` under `framework_adapter_probe_readiness`. The
promotion gate requires discovery/proof metadata in the promoted manifest and
closes the normal adapter metric floors plus `orchestration_trace_coverage` and
`orchestration_flow_quality`; the stateful gate additionally requires the
framework runtime contract to close at 1.0 for the orchestration signal.
