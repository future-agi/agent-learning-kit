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
  still imports, with a placeholder key, and the turn fails). A blank value counts as unset.
- `OPENAI_BASE_URL`: the endpoint's base URL, required whenever a key is set. Set it to the
  model gateway for the default model. With a key and no base URL the module refuses to
  import, naming the missing setting. The check reads `OPENAI_BASE_URL` only, so set it even
  where `OPENAI_API_BASE` is set.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions: the model is built with `use_responses_api=False`.
Install with `pip install -r requirements.txt` on Python 3.12.

## Known limits

- The kit's LangGraph adapter runs every request on one event loop, so a graph node that
  blocks that loop (synchronous I/O inside an `async` node, for example) stalls the other
  requests in flight until it returns. A stalled request answers with a timeout error once
  `ALK_LANGGRAPH_TIMEOUT_SECONDS` has passed. Plain (non-`async`) tools such as this
  example's run on a worker thread and don't block the loop.
- The orders are shared by every conversation the agent process serves, so a return started
  in one scenario is there in the next. The kit's LangGraph adapter passes each conversation
  to the graph as its LangGraph thread, but this example's tools keep one set of orders for
  the whole process and don't read the thread.
