---
kind: agent-learning.docs-page.v1
track: frameworks
backing: []
artifact_kinds: []
claims: []
---

# Supported frameworks

Generated from `src/fi/alk/harness/data/framework_support.json`; a test keeps this page equal to it.

- **Found:** the kit names the framework from the code.
- **Read:** a real reading described the reference agent correctly.
- **Run:** the agent ran from that reading, with five checks: start, a two-turn session, tool binding, evidence, stop. It is proven only when all five passed.
- **Platform:** connected through the platform's own screens and saved as Agent and version 1.
- **Versions** are the reference agent's own environment, not the kit's.
- **A proof holds only while the reference agent is unchanged:** each proving run records the hash of the agent's folder, and a test refuses the proof once the folder differs, until a new run.

| Framework | Level | Versions | Python | Found | Read | Run | Platform | Start | Two turns | Tools bound | Evidence | Stop |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Claude Agent SDK | Recognised, not yet tested | none | 3.12 | proven | - | - | - | - | - | - | - | - |
| CrewAI | Read, not yet tested | none | 3.12 | proven | proven | - | - | - | - | - | - | - |
| Deep Agents | Recognised, not yet tested | none | 3.12 | proven | pending | pending | - | - | - | - | - | - |
| Google ADK | Recognised, not yet tested | none | 3.12 | proven | pending | - | - | - | - | - | - | - |
| LangChain | Recognised, not yet tested | none | - | proven | pending | - | - | - | - | - | - | - |
| LangGraph | Recognised, not yet tested | none | - | proven | - | - | - | - | - | - | - | - |
| LiveKit Agents | Recognised, not yet tested | none | - | proven | pending | - | - | - | - | - | - | - |
| OpenAI Agents SDK | Recognised, not yet tested | none | 3.12 | proven | pending | pending | - | - | - | - | - | - |
| Pipecat | Recognised, not yet tested | none | - | proven | - | - | - | - | - | - | - | - |
| Pydantic AI | Recognised, not yet tested | none | 3.12 | proven | pending | - | - | - | - | - | - | - |

## Notes and limits

- **Claude Agent SDK, read:** The 18 Nov reading missed one line: it made ANTHROPIC_MODEL and ANTHROPIC_API_KEY required, though the model has a default and the agent blanks the API key in gateway mode. Everything else matched.
- **Claude Agent SDK, run:** No run from a reading. CI runs the reference on the real SDK and CLI against a local stub model, from a hand-written contract, which is not a real run.
- **Claude Agent SDK:** The kit builds and binds the reference from a hand-written contract (test_build_runs_the_callback_bridge_with_the_generic_tool_tracer).
- **CrewAI, run:** No run from a reading; the kit's tests use a stand-in CrewAI.
- **CrewAI:** The kit builds and binds the reference from a hand-written contract, against a stand-in CrewAI only.
- **Deep Agents, read:** Read on kit PR #341's head (5aa31fdb) before #341 merged; #341 is now merged with this folder unchanged, and the 18 Nov reading there matched its expected contract fully (the kit's graph route). Held pending until the first full run reads it again with production's model, rather than proven on a laptop-model reading made before the merge. The 12 Nov reading, on the kit before #341, saved `langgraph dev`.
- **Deep Agents, run:** Waits on the first full run, from a reading on a kit commit merged here. #341 sends such an app to the kit's own graph route; the 12 Nov run, from the reading before #341 and on no single kit commit, failed to start. Its check results are not shown, because no single commit holds what ran.
- **Deep Agents:** Readings stored before #341 merges need reading again.
- **Google ADK, read:** The 12 Nov reading described the agent, but it ran on a local merge of then-open pull requests: no single kit commit holds that reference agent and reading code, and no expectation was written first. Waits on a new reading on a kit commit merged here, against a written expectation.
- **Google ADK, run:** The 12 Nov run, on no single kit commit, started and replied right over two turns with its tools bound, but the transcript recorded 5 tool calls for 3 and stopping wasn't recorded; not rechecked since. Its check results are not shown, because no single commit holds what ran.
- **Google ADK:** The reference pins google-adk 2.13.0, outside the kit's own google-adk range (below 2.8, held there by OpenTelemetry). The tested version is the agent's.
- **LangChain, found:** The reader's file check names LangChain for the kit's LangChain example. For harbor-hotels-langchain, the one real LangChain agent tried, the offline walk (scan_walk) does not name it; only the reading does.
- **LangChain, read:** The 18 Nov reading of an outside agent (harbor-hotels-langchain) passed against its written expectation. Waits on a reference agent in the kit, at a fixed commit, with pinned versions.
- **LangChain, run:** No LangChain agent has run in the kit's tests.
- **LangChain:** No reference agent in the kit.
- **LangChain:** The 18 Nov reading binds the tools as `check_availability.func` (the plain function inside the `@tool` object); whether the tool tracer binds `.func` is unchecked until an agent run.
- **LangGraph, read:** The 18 Nov reading (of page-turner-langgraph-otel, an outside agent) left the contract's model empty, describing the provider and default model only in prose. Everything else matched.
- **LangGraph, run:** Only the kit's LangGraph adapter against a stand-in graph.
- **LangGraph:** No reference agent in the kit. Deep Agents, built on LangGraph, has its own entry.
- **LiveKit Agents, read:** The 18 Nov reading of an outside agent (harbor-voice-livekit) passed against its written expectation. Waits on a reference agent in the kit, at a fixed commit, with pinned versions.
- **LiveKit Agents, run:** No run from a reading. 15 real calls on three livekit/agents examples are recorded (Aug 2026, src/fi/alk/harness/IMPLEMENTATION_AND_VALIDATION_STATUS.md), from hand-made setups; the record doesn't give the livekit-agents version.
- **LiveKit Agents:** No reference agent in the kit.
- **OpenAI Agents SDK, read:** Read on kit PR #341's head (5aa31fdb) before #341 merged; #341 is now merged with this folder unchanged, and the 18 Nov reading there matched its expected contract fully. Held pending until the first full run reads it again with production's model, rather than proven on a laptop-model reading made before the merge. The 12 Nov reading, on the kit before #341, described the agent but marked its tools unreachable from the repository root.
- **OpenAI Agents SDK, run:** Waits on the first full run, from a reading on a kit commit merged here. The 12 Nov run, on no single kit commit, started and replied right over two turns but recorded no tool calls, so tool binding and evidence failed; #341 has since bound the tools. Its check results are not shown, because no single commit holds what ran.
- **OpenAI Agents SDK:** Readings stored before #341 merges need reading again.
- **OpenAI Agents SDK:** The kit's own hook also runs on openai-agents 0.18.3 and 0.20.0 in CI.
- **Pipecat, read:** No reference agent and no reading.
- **Pipecat, run:** Hosted Pipecat runs are not built.
- **Pipecat:** Recognised by name only.
- **Pydantic AI, read:** The 12 Nov reading described the agent, but it ran on a local merge of then-open pull requests: no single kit commit holds that reference agent and reading code, and no expectation was written first. Waits on a new reading on a kit commit merged here, against a written expectation.
- **Pydantic AI, run:** The 12 Nov run, on no single kit commit, passed four of the five checks (start, two turns, tool binding, evidence); stopping wasn't recorded. Its check results are not shown, because no single commit holds what ran.
