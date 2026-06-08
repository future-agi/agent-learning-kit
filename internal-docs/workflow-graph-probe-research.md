# Workflow Graph Trace Research

## Sources

- LangGraph persistence stores graph state as checkpoints at super-step
  boundaries, organized by thread, and state history can be replayed from prior
  checkpoints. Human-in-the-loop interrupts and replay depend on this durable
  checkpoint model. Source:
  https://docs.langchain.com/oss/python/langgraph/persistence
- CrewAI Flows model workflows as start/listen/router steps with state that
  persists across the flow, supports conditional routing, and can resume
  persisted state. Source: https://docs.crewai.com/en/concepts/flows
- LlamaIndex Workflows are event-driven and step-based: steps consume event
  types, emit new events, support `StartEvent`/`StopEvent`, and can stream
  workflow events. Sources:
  https://docs.llamaindex.ai/en/latest/api_reference/workflow/events/ and
  https://docs.llamaindex.ai/en/stable/understanding/workflows/unbound_functions/

## Implementation Rules

Keep workflow graph support local-first:

- Only normalize explicit workflow/graph fields such as `workflow_trace`,
  `graph_trace`, `workflow_steps`, `graph_nodes`, `workflow_checkpoints`,
  `route_decisions`, `interrupts`, and `workflow_replay`; do not treat a generic
  `events` or `state` field as a workflow trace by itself.
- Preserve graph topology, step execution, checkpoint/thread identifiers, state
  history, route decisions, interrupts, replay, and pending writes in one
  `workflow_trace` state object so evaluator metrics can inspect durable
  execution rather than only final text.
- Emit `workflow_step`, `workflow_route`, `workflow_checkpoint`,
  `workflow_interrupt`, `workflow_replay`, and `workflow_trace` events so agent
  reports can require each execution signal independently.
- Promote step-level tool evidence into ordinary tool calls. Tool metrics should
  not need a LangGraph, CrewAI, or LlamaIndex-specific parser to check whether a
  routed workflow used the expected tool.
- Attach a trace artifact for real workflow traces. Continue filtering
  wrapper-generated `framework_runtime` trace artifacts from promoted hard
  artifact requirements so instrumentation does not become a false adapter
  output contract.

## Cookbook Contract

`examples/sdk_framework_adapter_workflow_trace.py` demonstrates the intended
shape using a local LangGraph-style adapter. The selected adapter must emit:

- a graph with four nodes and three edges,
- four workflow steps with a `policy_lookup` tool call,
- two checkpoints with thread/checkpoint identifiers and pending writes,
- one route decision, one resolved interrupt, and one replay,
- final workflow state proving an approved refund decision,
- evaluator-visible `workflow_trace` state, `workflow_*` events, a trace
  artifact, and ordinary tool-call evidence.
