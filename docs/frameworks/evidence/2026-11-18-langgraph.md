# LangGraph (page-turner-langgraph-otel): real reading, 18 Nov 2026

## Run
- Agent: `page-turner-langgraph-otel`: a local test agent, not in any git repository. The kit has no reference agent for this framework. Content hash of its files at read time (sha256 over the sorted per-file sha256 lines): `03e03e9e203491ff`.
- Kit: `d9b61c0370800a5faa3140012ec50211ba0bfd82` (`feat/TH-8804-source-subfolder`).
- Reading model: Claude Sonnet, through a local model gateway, `authoring_entrypoint --read-agent-only`.
- Spend: 7 model turns. The alias is unpriced, so `cost.json` reads $0.00; estimated from its tokens at Sonnet prices: $0.22-$0.73.
- Raw outputs: kept outside this repository.

## Expected
Written before the reading ran.

- **Agent:** chat; framework `langgraph`.
- **Model:**
  - The provider is chosen by the environment: Anthropic when `ANTHROPIC_API_KEY` is set, else OpenAI; `PAGE_TURNER_PROVIDER` decides when both keys are set.
  - Default `claude-sonnet-5-5` or `gpt-4o-mini`, overridden by `PAGE_TURNER_MODEL`.
  - The reading should name the default it picks, and the environment that switches it.
- **Prompt:** `SYSTEM_PROMPT`, the Page Turner Books shop assistant with its rules (search before quoting, confirm before ordering, US dollars, books only).
- **Tools:**
  - `search_books(query)` and `place_order(isbn, quantity, customer_name)`.
  - They are plain functions, called from the `tools` node through `TOOL_FUNCTIONS`. Entries `import`, module `agent`.
- **Runtime:**
  - command `python agent.py` (an HTTP server, not the langgraph CLI; there's no `langgraph.json`);
  - interface `http`, OpenAI chat-completions shape: `POST /v1/chat/completions` with `messages`, reply at `choices.0.message.content`;
  - health `GET /health`; port `$PORT` (default 8080);
  - environment: one of `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, and `OPENAI_BASE_URL` optional.

## Result
miss

One line differs: the model. The contract's `model` is empty (`null`). The expectation was the default the agent uses (`claude-sonnet-5-5` with `ANTHROPIC_API_KEY`, else `gpt-4o-mini`) and the environment that switches it. The reading describes the provider choice only in prose, under `runtime_dependencies`. Also noted, not counted: `required_environment` lists both `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`, where the agent needs one of them; the field has no way to say one of. Everything else holds: framework `langgraph`; prompt `SYSTEM_PROMPT` (`agent.py:52-60`); tools `search_books(query)` and `place_order(isbn, quantity, customer_name)`, entries `import`, module `agent`; command `python agent.py`, `execution: native`; interface `http` `openai_chat`, `POST /v1/chat/completions`, health `/health`, port 8080; python 3.12.
