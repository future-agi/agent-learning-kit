# LangChain support agent

A customer support chat agent built on LangChain's `create_agent`. It has three tools over a
small in-memory set of orders: `look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

The kit calls `agent_callback` in `agent.py` once per turn with the whole conversation. The
function builds the agent and passes it the user and assistant turns as `messages`. It
replies with the last message's text as `{"content": ...}` only.

Tool calls are not returned: the agent runs the tools itself, and the kit's generic tool
tracer records each call made to the three tool functions, including the ones LangChain
makes from a worker thread. There is no `langgraph.json`, so the kit runs this agent through
its callback bridge, not its LangGraph adapter, though `create_agent` returns a LangGraph
graph.

Each conversation the kit names gets its own copy of the orders, so a return started in one
scenario is not there in the next.

## Environment

- `OPENAI_API_KEY`: the key for the endpoint (required for a turn). A blank value counts as
  unset.
- `OPENAI_BASE_URL`: the endpoint's base URL, required whenever a key is set. Set it to the
  model gateway for the default model.
  - With a key and no base URL (unset, empty or blank), the turn is refused before a model is
    built, naming the missing setting.
  - The check reads `OPENAI_BASE_URL` only, so `OPENAI_API_BASE` alone doesn't satisfy it.
  - With neither a key nor a base URL, `ChatOpenAI` itself refuses to build the client.
- `OPENAI_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).

The endpoint must speak chat completions: the model is built with `use_responses_api=False`.

## Known limits

- The agent and the model are built on every turn, because each request runs in its own
  event loop and the client's async connection belongs to the loop it was first used in. So
  there is no connection reuse between turns.

Install with `pip install -r requirements.txt` on Python 3.12.

`agent-simulate` is pinned in `requirements.txt` because the kit's callback bridge imports
`fi.simulate.AgentInput`.
