# OpenAI Agents SDK support agent

A customer support chat agent built on the OpenAI Agents SDK. It has three tools over a
small in-memory set of orders: `look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

The kit calls `agent_callback` in `agent.py` once per turn with the whole conversation. The
function runs the agent through `Runner.run` and returns `{"content": ...}` only. Tool calls
are not returned: the SDK runs the tools itself, and the kit's tool tracer records each call
made to the three tool functions.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint (required for a turn).
- `OPENAI_BASE_URL`: the endpoint's base URL. Set it to the model gateway for the default model; unset means the OpenAI API, which then needs `OPENAI_MODEL` set to an OpenAI model.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions. Install with `pip install -r requirements.txt` on
Python 3.12.
