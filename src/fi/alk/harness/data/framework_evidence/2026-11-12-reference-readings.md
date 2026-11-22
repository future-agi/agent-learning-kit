# Real readings and runs of the reference agents, 12 Nov 2026

A record, cited by `src/fi/alk/harness/framework_support.py`. It was run locally, on
`feat/TH-8804-source-subfolder` (c0e51b7e) with the reference PRs (#301, #303, #304, #305, #306)
merged, for about $2.75.

## How it ran

1. **A real reading per reference agent:** `authoring_entrypoint --read-agent-only`, through a
   local gateway, with Claude Sonnet as the reading model.
2. **An agent run from each reading's own contract,** without the builder: `author_bundle_v2`,
   preflight, `ProcessRuntimeProvider.provision` and the hosted chat target. It had three fixed
   user turns, each needing a tool, with `vertex_ai/gemini-3.7-flash` as the agent's model through
   the same gateway.
3. **Step 0,** before the readings: each agent run from a hand-written contract.

Each agent ran on its example's own pinned requirements. This was not a hosted run, and not the
builder (world, scenarios, judge).

## Pydantic AI

pydantic-ai-slim 2.61.0. The reading gave tools as `import` entries from module `agent`. The run
from the reading gave 3 right replies, and the trace file had the 3 tool calls with their arguments
and results.

- Reading: pass.
- Run: miss. Four of the five checks passed; stopping wasn't recorded.

## Google ADK

google-adk 2.13.0. The reading gave `adk web`; the kit replaced its interface with its own
`/run_sse` route and the port with one per world. The run gave 3 right replies (after `mcp` was
added to the example's requirements), but the transcript recorded 5 tool calls for 3: two extra
with empty arguments. The trace file had the clean 3.

- Reading: pass. No expectation was written before this reading. A correction on 17 Nov found that the kit replaces an `adk web` reading's interface with its own `/run_sse` route and the port with `$PORT` per world, so ADK needs no reading fix.
- Run: miss. Two extra tool calls with empty arguments were recorded, and stopping wasn't recorded.

## OpenAI Agents SDK

openai-agents 0.24.0. The reading marked each `@function_tool` tool `unreachable`, with its module
written from the repository root. So the run gave right replies but recorded no tool calls. #341
fixes the reading's binding. In step 0, from a hand-written contract, all three calls were recorded.

- Reading: miss. The tools were marked unreachable, with their module written from the repository root.
- Run: miss. No tool calls were recorded.

## Deep Agents

deepagents 0.7.32. The reading chose `langgraph dev`, which the agent doesn't install, so the run
failed at start. #341 sends such an app to the kit's own graph route. In step 0 the kit's LangGraph
adapter ran the agent from a hand-written contract, and its tool calls were recorded.

- Reading: miss. It saved `langgraph dev`.
- Run: miss. It failed at start.
