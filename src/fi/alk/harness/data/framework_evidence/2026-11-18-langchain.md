# LangChain (harbor-hotels-langchain): real reading, 18 Nov 2026

## Run
- Agent: `harbor-hotels-langchain`: a local test agent, not in any git repository. The kit has no reference agent for this framework. Content hash of its files at read time (sha256 over the sorted per-file sha256 lines): `d999fd698d010eaf`.
- Kit: `d9b61c0370800a5faa3140012ec50211ba0bfd82` (`feat/TH-8804-source-subfolder`).
- Reading model: Claude Sonnet, through a local model gateway, `authoring_entrypoint --read-agent-only`.
- Spend: 8 model turns. The alias is unpriced, so `cost.json` reads $0.00; estimated from its tokens at Sonnet prices: $0.34-$1.00.
- Raw outputs: kept outside this repository.

## Expected
Written before the reading ran.

- **Agent:** chat; framework `langchain`.
- **Model:** Anthropic `claude-sonnet-5-5` when `ANTHROPIC_API_KEY` is set, else OpenAI `gpt-4o-mini`; `HARBOR_MODEL` and `HARBOR_PROVIDER` override.
- **Prompt:** `SYSTEM_PROMPT`, the Harbor Hotels reservations assistant (check availability before quoting, confirm before booking, euros, hotel bookings only).
- **Tools:**
  - `check_availability(city, room_type, nights)` and `book_room(city, room_type, nights, guest_name)`.
  - They are LangChain `@tool` objects; entries `import`, module `agent` (as both 17 Nov readings did).
- **Runtime:**
  - command `python agent.py`;
  - interface `http`, chat-completions shape at `POST /v1/chat/completions`;
  - health `GET /health`; port `$PORT` (default 8080);
  - environment one of `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`.
  - Tracing, with `FI_BASE_URL` and `HARBOR_TRACING`, is optional.

## Result
pass

Every expected line holds. Framework `langchain`; model `claude-sonnet-5-5` (provider anthropic, the default with `ANTHROPIC_API_KEY`); prompt `SYSTEM_PROMPT` (`agent.py:52-60`); tools `check_availability(city, room_type, nights)` and `book_room(city, room_type, nights, guest_name)`, entries `import`, module `agent`; command `python agent.py`; interface `http` `openai_chat`, `POST /v1/chat/completions`, health `/health`, port 8080; required environment `ANTHROPIC_API_KEY` (one of the two keys, a valid choice). One difference from both 17 Nov readings, not against the expectation: the callable is `check_availability.func` and `book_room.func` (the plain function inside the `@tool` object), where 17 Nov wrote `check_availability`. Whether the tool tracer binds `.func` was not checked here; an agent run would show it.
