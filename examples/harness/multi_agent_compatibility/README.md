# Multi-agent compatibility references

This corpus tests ALK against complete upstream multi-agent applications. ALK
must inspect and run each application's native orchestration. The corpus does
not replace an upstream coordinator or reduce its worker graph.

| Reference | Native topology | Pinned source |
| --- | --- | --- |
| Google ADK Travel Concierge | One root, six top-level agents, and fourteen nested agents | [`google/adk-samples`](https://github.com/google/adk-samples/tree/b822efc7710498dfa4ff0d405c20ecdc6b92c8e3/python/agents/travel-concierge) |
| LangGraph Async Deep Agents | One supervisor launches researcher and coder agents as independent background jobs | [`langchain-ai/async-deep-agents`](https://github.com/langchain-ai/async-deep-agents/tree/6cafef62b2783a3d16edacd7df151ad1a33ee7a5) |
| CrewAI Self-evaluation Flow | One flow alternates a generator crew and verifier crew until the output passes or reaches the retry bound | [`crewAIInc/crewAI-examples`](https://github.com/crewAIInc/crewAI-examples/tree/da94a91e691e1cf5b3151416bb15b5b62729bea8/flows/self_evaluation_loop_flow) |

Each `reference.json` records the exact source revision, native launch command,
model environment, complete worker topology, scenario seeds, expected state
effects, and raw evidence required from a hosted run. Small patches route an
existing application through AgentCC or update an obsolete dependency API.
Each patch is checksummed and leaves the native prompts, coordinator, workers,
and execution flow unchanged.

The Google reference is the full Travel Concierge application. Its top-level
agents are `inspiration_agent`, `planning_agent`, `booking_agent`,
`pre_trip_agent`, `in_trip_agent`, and `post_trip_agent`. The manifest also
names every nested agent. The verifier rejects a reduced Google topology.

## Verify the references

Run the offline contract check.

```bash
python examples/harness/multi_agent_compatibility/verify_references.py
```

The check validates pinned revisions, topology integrity, scenario ownership,
and the common evidence contract.

## Materialize the upstream applications

Clone each pinned revision and extract its application directory.

```bash
python examples/harness/multi_agent_compatibility/materialize_references.py \
  /tmp/multi-agent-references
```

The script copies the pinned upstream source and applies the checksummed
compatibility patch recorded by its manifest. It adds a
`.futureagi-source.json` provenance file beside the materialized application.
Supply the `runtime.model_environment` values through the harness job. Do not
write credentials into the source tree.

Verify that each materialized tree still contains the orchestration recorded
by its manifest.

```bash
python examples/harness/multi_agent_compatibility/verify_references.py \
  --source google-adk-travel-concierge=/tmp/multi-agent-references/google-adk-travel-concierge \
  --source langgraph-async-deep-agents=/tmp/multi-agent-references/langgraph-async-deep-agents \
  --source crewai-self-evaluation-flow=/tmp/multi-agent-references/crewai-self-evaluation-flow
```

Submit each materialized directory through the existing source-repository or
source-upload flow. A valid hosted result must retain the framework's raw child
IDs, delegation events, return events, worker-attributed tool calls, and state
effects. TH-8886 through TH-8888 own normalized storage, worker-level
evaluation, and timeout or cancellation behavior.
