# LangChain support agent

A customer support chat agent built on LangChain's `create_agent`, written the way LangChain's
documentation writes one: a module-level `agent` built from a chat model, the tools and a
system prompt. It has three tools over a small in-memory set of orders: `look_up_order`,
`start_return` and `return_policy`.

## How the kit runs it

`create_agent` returns a LangGraph graph, and `langgraph.json` names it, `agent.py:agent`,
which is how LangChain deploys an agent. The kit runs it with its LangGraph adapter.
- Each turn the adapter sends the whole conversation as `messages` and replies with the last
  message's text.
- It names the conversation as the run's thread, `config={"configurable": {"thread_id": ...}}`,
  and the tools read it with LangGraph's `get_config()`, so each conversation has its own copy
  of the orders.
- Tool calls are not returned: the agent runs the tools itself, and the kit's tool tracer
  records each call made to the three tool functions, including the ones made from a worker
  thread.

The agent and its model are built once, at import. They are not built per turn, and not run
through the kit's callback bridge: that bridge runs each request in an event loop of its own,
and a client built once would carry its connections into a loop that has closed.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint (required for a turn). Without it the module
  still imports, with a placeholder key, and the turn fails. A blank value counts as unset.
- `OPENAI_BASE_URL`: the endpoint's base URL, required whenever a key is set. Set it to the
  model gateway for the default model.
  - With a key and no base URL, the module refuses to import, naming the missing setting.
  - The check reads `OPENAI_BASE_URL` only, so set it even where `OPENAI_API_BASE` is set.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions: the model is built with `use_responses_api=False`.
Install with `pip install -r requirements.txt` on Python 3.12.

## Known limits

- The kit's LangGraph adapter runs every request on one event loop. The plain tools run on a
  worker thread and don't block it.
