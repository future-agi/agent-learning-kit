# CrewAI support agent

A customer support chat agent built on CrewAI. It is a standalone `Agent` with three tools
over a small in-memory set of orders: `look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

The kit calls `agent_callback` in `agent.py` once per turn with the whole conversation. The
function passes the user and assistant turns to `Agent.kickoff_async`, which takes the last
user message as the request and the earlier ones as history, and returns
`{"content": ...}` only. Tool calls are not returned: CrewAI runs the tools itself, and the
kit's generic tool tracer records each call made to the three tool functions. (The kit's
CrewAI observer is for command-run agents and is not used for a callback agent.)

CrewAI's telemetry and first-run tracing are switched off in `agent.py` before CrewAI is
imported, unless the environment already sets those variables.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint (required for a turn).
- `OPENAI_BASE_URL`: the endpoint's base URL (required for a turn). Set it to the model gateway for the default model. CrewAI refuses to build a `custom_openai` model without one, so a missing URL fails every turn.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions. The model is built as `LLM(..., custom_openai=True)`,
which keeps CrewAI's OpenAI client on chat completions with the name unchanged, and stops
CrewAI switching to the Responses API after a "responses-only" error from the endpoint. A plain
`openai/<a known OpenAI model>` string with a base URL can make that switch, and an `openai/`
string whose URL comes only from the environment goes to LiteLLM instead, which this example
doesn't install.

## Known limits

- The agent and model are built on every turn, because each request runs in its own event
  loop. CrewAI has no close for its two OpenAI SDK clients, so each turn leaves them to
  garbage collection and there is no connection reuse between turns.
- CrewAI sends function tools with `strict: true`; whether the gateway's Gemini route accepts
  that is checked by a real run.

Install with `pip install -r requirements.txt` on Python 3.12.

`agent-simulate` is pinned in `requirements.txt` because the kit's callback bridge imports
`fi.simulate.AgentInput`.
