# LangGraph support agent

A customer support chat agent built on LangGraph's own API: a `StateGraph` over
`MessagesState`, with a model node and a `ToolNode`. It has three tools over a small in-memory
set of orders: `look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

`langgraph.json` names the one graph, `agent.py:graph`, which is built once when the module
is imported. The graph is the usual tool loop:
- `START` goes to the `agent` node, which calls the model with the system prompt in front of
  the conversation;
- `tools_condition` sends a reply that asks for tools to the `tools` node, and any other reply
  to the end;
- the `tools` node's results go back to `agent`.

The kit runs it with its LangGraph adapter. Each turn, the adapter sends the whole conversation
as `messages` and replies with the last message's text. It names the conversation as the run's
thread, `config={"configurable": {"thread_id": ...}}`, and the tools read it with
`get_config()`, so each conversation has its own copy of the orders. The graph is compiled
without a checkpointer: the kit sends the whole history each turn, so a checkpointer would make
the graph see it twice. Tool calls are not returned: the graph
runs the tools itself, and the kit's tool tracer records each call made to the three tool
functions, including the ones made from a worker thread.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint. It's required for a turn. Without it the module
  still imports, with a placeholder key, and the turn fails. A blank value counts as unset.
- `OPENAI_BASE_URL`: the endpoint's base URL, required whenever a key is set. Set it to the
  model gateway for the default model.
  - With a key and no base URL, the module refuses to import, naming the missing setting.
  - The check reads `OPENAI_BASE_URL` only, so set it even where `OPENAI_API_BASE` is set.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions: the model is built with `use_responses_api=False`.
Install with `pip install -r requirements.txt` on Python 3.12.

## Known limits

- The kit's LangGraph adapter runs every request on one event loop. The model node is
  `async` and awaits the client, and the plain tools run on a worker thread, so neither blocks
  the loop. A node added later that blocks it would stall the other requests in flight until
  it returns. A stalled request answers with a timeout error once
  `ALK_LANGGRAPH_TIMEOUT_SECONDS` has passed.
