# Orchestration Stack Probe Research Note

Date: 2026-06-08

## Why This Exists

Full orchestration-stack optimization already evaluates coherent world,
framework, retrieval, memory-lineage, and multi-agent room bundles. It still
needs the same cheap local preflight path as framework, memory, multi-agent,
realtime, and browser probes: pass local fixtures, prove the whole stack closes
without external services, then promote the selected stack into a normal
`agent-learning.run.v1` orchestration simulation.

## Current Orchestration Signals

- LangGraph emphasizes durable execution, persistence, human-in-the-loop
  control, and memory. Probe implication: orchestration evidence must prove
  local world state transitions and replayable state, not only final text.
  Sources: https://docs.langchain.com/oss/python/langgraph/durable-execution,
  https://docs.langchain.com/oss/python/langgraph/persistence,
  https://docs.langchain.com/oss/python/langgraph/memory, and
  https://docs.langchain.com/oss/python/langgraph/human-in-the-loop
- AutoGen AgentChat teams coordinate multiple agents and expose grouped message
  traces. Probe implication: local orchestration checks should require role
  boundaries, review handoffs, and reconciliation evidence in the room trace.
  Source:
  https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html
- CrewAI separates crews and flows for multi-agent orchestration and process
  control. Probe implication: stack candidates should carry workflow/room
  evidence as one bundle so the optimizer cannot mix framework traces from one
  candidate with memory or review evidence from another. Sources:
  https://docs.crewai.com/concepts/crews and
  https://docs.crewai.com/concepts/flows
- OpenAI Agents SDK documents handoffs, tracing, and guardrails as core agent
  runtime features. Probe implication: a passing orchestration preflight should
  include tool calls, trace status, explicit review/reconciliation, and
  fail-closed local contract checks. Sources:
  https://openai.github.io/openai-agents-python/handoffs/,
  https://openai.github.io/openai-agents-python/tracing/, and
  https://openai.github.io/openai-agents-python/guardrails/

## Implementation Rule

Keep orchestration support local-first:

- Accept the same stack shorthand blocks as full orchestration optimization:
  `world_contract`, `framework_trace`, `retrieval_memory`,
  `agent_memory_lineage`, and `multi_agent_room`.
- Reject HTTP/HTTPS targets and export sources by default.
- Execute scripted agent tool calls directly against local environment adapters
  instead of reimplementing world, trace, memory, retrieval, or room semantics.
- Require world transition/state success, framework trace signals, current
  retrieval citations, source-attributed governed memory lineage, explicit
  multi-agent review and reconciliation, and successful tool handling.
- Use `optimize.optimize_orchestration_stack_probe()` to select a local stack
  cheaply, then use
  `optimize.build_orchestration_run_manifest_from_probe_optimization()` when
  the selected stack should become a normal evaluated orchestration simulation.
- `agent-learn release-check` now runs
  `examples/sdk_orchestration_stack_probe_optimization.py` as
  `orchestration_stack_probe_readiness`; the gate requires strong local stack
  selection over weak candidates, native orchestration-stack proof, promoted
  `agent-learning.run.v1` execution, and closed world transition, LangGraph
  trace, current retrieval citation, governed memory lineage, multi-agent
  review/reconciliation, required tool, source-grounding, and run metrics.
