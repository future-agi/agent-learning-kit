# Google ADK support agent

A customer support chat agent built on Google ADK. It has three tools over a small
in-memory set of orders: `look_up_order`, `start_return` and `return_policy`.

## How it runs

The agent is an ADK app: the `support_agent` package exposes `root_agent`, and ADK's own
server, `adk web`, serves it. Start it from this folder:

```
adk web --port 8080 .
```

ADK keeps each conversation's history, so a caller sends only the new message of a turn.
The model is a `LiteLlm` with the `openai/` prefix, so LiteLLM sends the model name
unchanged to the endpoint on chat completions. ADK runs the tools itself, and the kit's tool
tracer records each call made to the three tool functions in `support_agent/agent.py`.

`adk web` writes its session store to `support_agent/.adk/`, which `.gitignore` covers.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint (required for a turn).
- `OPENAI_BASE_URL`: the endpoint's base URL. Set it to the model gateway for the default model; unset means the OpenAI API, which then needs `OPENAI_MODEL` set to an OpenAI model.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions. Install with `pip install -r requirements.txt` on
Python 3.12.
`mcp` is listed because LiteLLM imports it on a model call that carries tools, which this
agent's calls do, and neither other pin brings it in.
