# Supported frameworks

Generated from `src/fi/alk/harness/data/framework_support.json` (decision record ADR-006); a test keeps this page equal to it. Each framework is at the highest level its evidence reaches: Recognised (we name it from the code), Read (a real reading described a reference agent correctly), Runs (the five checks passed from that reading: start, a two-turn session, tool binding, evidence, stop), Through the platform (connected on the platform and saved as Agent and version 1). The versions are the reference agent's own, as its reading and run used them.

| Framework | Level | Versions on record | Python | Start | Two turns | Tools bound | Evidence | Stop |
|---|---|---|---|---|---|---|---|---|
| Claude Agent SDK | Recognised, not yet tested | none | 3.12 | - | - | - | - | - |
| CrewAI | Recognised, not yet tested | none | 3.12 | - | - | - | - | - |
| Deep Agents | Read, not yet tested | `deepagents==0.7.32` | 3.12 | failed | - | - | - | - |
| Google ADK | Read, not yet tested | `google-adk==2.13.0` | 3.12 | passed | passed | passed | failed | - |
| LangChain | Recognised, not yet tested | none | - | - | - | - | - | - |
| LangGraph | Recognised, not yet tested | none | - | - | - | - | - | - |
| LiveKit Agents | Recognised, not yet tested | none | - | - | - | - | - | - |
| OpenAI Agents SDK | Read, not yet tested | `openai-agents==0.24.0` | 3.12 | passed | passed | failed | failed | - |
| Pipecat | Recognised, not yet tested | none | - | - | - | - | - | - |
| Pydantic AI | Read, not yet tested | `pydantic-ai-slim==2.61.0` | 3.12 | passed | passed | passed | passed | - |

## Known limits

- **Claude Agent SDK:** No real reading is on record yet; one is being run (TH-8952).
- **Claude Agent SDK:** The reference runs on the real SDK and its bundled CLI against a local stub model in CI, from a hand-written contract, not from a reading.
- **CrewAI:** No real reading is on record yet; one is being run (TH-8952).
- **CrewAI:** The kit's tests run the reference only against a stand-in CrewAI.
- **Deep Agents:** The reading described the agent, but saved `langgraph dev`, which the agent doesn't install, so the run from it failed to start. Kit PR #341 sends such an app to the kit's own graph route; until it merges, readings of Deep Agents agents need reading again.
- **Google ADK:** The run from its reading gave right replies, but the transcript recorded 5 tool calls for 3 (two extra with empty arguments); not rechecked since.
- **Google ADK:** The reference pins google-adk 2.13.0, outside the kit's own google-adk range (below 2.8, held there by OpenTelemetry). The tested version is the agent's.
- **LangChain:** No reference agent in the kit and no real reading on record; one is being run on an outside agent (TH-8952).
- **LangGraph:** No reference agent in the kit and no real reading on record; one is being run on an outside agent (TH-8952).
- **LangGraph:** Deep Agents, built on LangGraph, has its own row.
- **LiveKit Agents:** No reference agent in the kit and no real reading on record; one is being run on an outside agent (TH-8952).
- **LiveKit Agents:** 15 real calls on three livekit/agents examples are recorded (Aug 2026, src/fi/alk/harness/IMPLEMENTATION_AND_VALIDATION_STATUS.md), from hand-made setups, not from a reading; the record doesn't give the livekit-agents version.
- **OpenAI Agents SDK:** The reading described the agent, but marked its tools unreachable, so the run from it replied right with no tool calls recorded. Kit PR #341 binds them; until it merges, readings of Agents SDK agents need reading again.
- **OpenAI Agents SDK:** The kit's own hook also runs on openai-agents 0.18.3 and 0.20.0 in CI.
- **Pipecat:** Recognised by name only: no reference agent and no reading. Hosted Pipecat runs are not built (decision record ADR-001).
- **Pydantic AI:** The run from its reading passed four of the five checks; stopping wasn't recorded, so it isn't at Runs yet.
