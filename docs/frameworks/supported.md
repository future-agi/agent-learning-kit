# Frameworks the harness can take an agent from

This page lists what is proven for each framework a user can bring an agent in, with the version
it was proven on.
- **Source of truth:** `src/fi/alk/data/framework_support.json`, read by
  `src/fi/alk/framework_support.py`. A new real reading changes one entry there and adds its
  record to `docs/frameworks/evidence/`.
- **Checked:** a test keeps this table equal to that file.
- **Exposed:** `agent-learn capabilities` carries the same list, with the evidence for every
  claim, under `framework_support`.

| Framework | Package | Tested version | Found | Read | Built | Run |
|---|---|---|---|---|---|---|
| Claude Agent SDK | `claude-agent-sdk` | 0.2.174 | proven | not proven | proven | partly |
| CrewAI | `crewai` | none | proven | not proven | proven | not proven |
| Deep Agents | `deepagents` | 0.7.32 | proven | pending | proven | pending |
| Google ADK | `google-adk` | 2.13.0 | proven | proven | proven | partly |
| LangChain | `langchain-core` | none | proven | not proven | not proven | not proven |
| LangGraph | `langgraph` | none | proven | not proven | not proven | not proven |
| LiveKit Agents | `livekit-agents` | none | proven | not proven | not proven | partly |
| OpenAI Agents SDK | `openai-agents` | 0.24.0 | proven | pending | proven | pending |
| Pipecat | `pipecat-ai` | none | proven | not proven | not proven | not proven |
| Pydantic AI | `pydantic-ai-slim` | 2.61.0 | proven | proven | proven | partly |
- **Found:** the kit detects the framework in a repository.
- **Read:** a real reading by the model turned an agent built with it into its contract.
- **Built:** the kit builds such an agent from a contract and binds its tools. A hand-written
  contract is enough for this, which is why it's a separate claim from Read.
- **Run:** an agent read that way has run. This is the layer after reading, kept separate.
- **Tested version:** the version a cited test or record actually ran. It is "none" where nothing
  ran the real package.
- **Statuses:**
  - **partly:** some of the layer is shown, and the file says what is missing.
  - **pending:** shown on an open pull request (#341), not yet merged.
  - **not proven:** no evidence yet.

Each framework's evidence (test names and records), notes and the version it ran are in that file.
