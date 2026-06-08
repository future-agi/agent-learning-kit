# Multi-Agent Room Probe Research Note

Date: 2026-06-08

## Why This Exists

Framework and memory probes now let users prove local evidence before a full
simulation manifest. Multi-agent rooms need the same preflight path: a user
should be able to hand the SDK participant roles, candidate agent traces, and
candidate room contracts, prove handoff/review/reconciliation locally, then
promote the selected pair into the normal `agent-learning.run.v1` multi-agent
simulation path.

## Current Multi-Agent Signals

- AutoGen AgentChat teams coordinate multiple participants through selectable
  team patterns such as round-robin and selector-based group chat. Probe
  implication: role membership and team coordination evidence must be explicit,
  not inferred from final text alone. Source:
  https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html
- AutoGen Swarm teams model handoffs between agents. Probe implication: handoff
  targets, reasons, and accepted participants should be first-class evidence.
  Source:
  https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/swarm.html
- CrewAI crews define agents, tasks, process, planning, manager behavior, memory,
  output logs, and embedder/process settings as structured crew attributes.
  Probe implication: a probe should preserve orchestration contracts and not
  flatten a crew into a single prompt string. Source:
  https://docs.crewai.com/en/concepts/crews
- CrewAI collaboration support emphasizes delegating work, asking questions, and
  validating task outputs across agents. Probe implication: review and
  reconciliation evidence should be scored beside task completion. Source:
  https://docs.crewai.com/en/concepts/collaboration
- LangGraph multi-agent systems organize specialist agents with supervisor,
  handoff, and swarm patterns. Probe implication: handoff contracts, role
  boundaries, and terminal shared state should be closed before promotion into a
  run manifest. Source:
  https://langchain-ai.github.io/langgraph/concepts/multi_agent/

## Implementation Rule

Keep multi-agent room support local-first:

- Accept explicit participants plus manifest-style room candidates.
- Reject HTTP/HTTPS targets by default.
- Emit a `multi_agent_room` environment and a local room contract so existing
  coordination metrics can score the promoted run.
- Require at least two explicit participants, `allow_unknown_roles=false`, known
  handoff/review targets, handoff contract matches, expected handoff/review/
  reconciliation matches, conflict-free accepted-source reconciliation, and a
  terminal shared case state before a probe is considered closed.
- Use `optimize.optimize_multi_agent_room_probe()` to select among local
  agent/room pairs cheaply, then use
  `optimize.build_multi_agent_run_manifest_from_probe_optimization()` when the
  selected pair should become a normal evaluated multi-agent simulation.
