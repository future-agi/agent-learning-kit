# Pydantic AI support agent

A customer support chat agent built on Pydantic AI. It has three tools over a small
in-memory set of orders: `look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

The kit calls `agent_callback` in `agent.py` once per turn with the whole conversation. The
function passes the last message to `Agent.run` as the prompt and the earlier ones as
message history, and returns `{"content": ...}` only. Tool calls are not returned: Pydantic
AI runs the tools itself, and the kit's tool tracer records each call made to the three
tool functions. The persona is given as `instructions`, which Pydantic AI sends on every
turn; a `system_prompt` would be dropped once there is history.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint (required for a turn).
- `OPENAI_BASE_URL`: the endpoint's base URL. Set it to the model gateway for the default model; unset means the OpenAI API, which then needs `OPENAI_MODEL` set to an OpenAI model.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions. The model is built as an `OpenAIChatModel`;
a name string such as `openai:...` would select the Responses API instead. Install with
`pip install -r requirements.txt` on Python 3.12.
