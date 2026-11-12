# Deep Agents support agent

A customer support chat agent built on LangChain's Deep Agents. It has three tools over a
small in-memory set of orders: `look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

`langgraph.json` names the one graph, `agent.py:graph`, which is built once when the module
is imported. The kit runs it with its LangGraph adapter: each turn the adapter sends the
whole conversation as `messages` and replies with the last message's text. Tool calls are
not returned: the graph runs the tools itself, and the kit's tool tracer records each call
made to the three tool functions, including the ones made from a worker thread. Deep
Agents' own built-in tools (files, planning, sub-agents) are not traced.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint (required for a turn; without it the module
  still imports, with a placeholder key, and the turn fails).
- `OPENAI_BASE_URL`: the endpoint's base URL. Set it to the model gateway for the default model; unset means the OpenAI API, which then needs `OPENAI_MODEL` set to an OpenAI model.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions: the model is built with `use_responses_api=False`.
Install with `pip install -r requirements.txt` on Python 3.12.
