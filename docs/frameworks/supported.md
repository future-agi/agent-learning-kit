# Frameworks the harness can take an agent from

This page lists what is proven for each framework a user can bring an agent in, with the version
it was proven on. The source of truth is `src/fi/alk/framework_support.py`. A test keeps this
table equal to it, and `agent-learn capabilities` carries the same list, with the evidence for
every claim, under `framework_support`.

| Framework | Package | Tested version | Found | Read | Run |
|---|---|---|---|---|---|
| Claude Agent SDK | `claude-agent-sdk` | 0.2.174 | proven | not proven | partly |
| CrewAI | `crewai` | none | proven | not proven | not proven |
| Deep Agents | `deepagents` | 0.7.32 | proven | pending | pending |
| Google ADK | `google-adk` | 2.13.0 | proven | proven | partly |
| LangChain | `langchain-core` | none | proven | partly | not proven |
| LangGraph | `langgraph` | none | proven | partly | not proven |
| LiveKit Agents | `livekit-agents` | none | proven | not proven | partly |
| OpenAI Agents SDK | `openai-agents` | 0.24.0 | proven | pending | pending |
| Pipecat | `pipecat-ai` | none | proven | not proven | not proven |
| Pydantic AI | `pydantic-ai-slim` | 2.61.0 | proven | proven | partly |
- **Found:** the kit detects the framework in a repository.
- **Read:** an agent built with it is read into its contract, which the kit builds and binds.
- **Run:** an agent read that way has run. This is the layer after reading, kept separate.
- **Tested version:** the version a cited test or record actually ran. It is "none" where nothing
  ran the real package.
- **Statuses:**
  - **partly:** some of the layer is shown, and the source says what is missing.
  - **pending:** shown on an open pull request (#341), not yet merged.
  - **not proven:** no evidence yet.

Each framework's evidence (test names and records) and notes are in the source.
