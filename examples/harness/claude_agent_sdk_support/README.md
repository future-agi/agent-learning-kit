# Claude Agent SDK support agent

A customer support chat agent built on the Claude Agent SDK. It has three tools over a
small in-memory set of orders: `look_up_order`, `start_return` and `return_policy`.

## How the kit runs it

The kit calls `agent_callback` in `agent.py` once per turn with the whole conversation. The
SDK sends only user messages, so each turn is one stateless `query()`: the earlier turns are
rendered as a short "Conversation so far" transcript in the prompt, followed by the new user
message. The function returns `{"content": ...}` only (the final result text); it raises if
the SDK reports an error.

The three tools are plain module-level functions, and those are the tool entrypoints the
kit's tool tracer is bound to. The SDK needs async handlers, so thin `@tool` handlers with
different names (`_look_up_order_tool` and so on) call the plain functions. Bind tracing to
the plain functions, not to the handlers, which take an `args` dict and are coroutines.

The SDK's built-in tools are off (`tools=[]`), only the three `mcp__support__...` tools are
allowed, no settings files are read, and the CLI's config folder is a temporary one, removed
after each turn.

## Environment

- `ANTHROPIC_MODEL`: the model name, default `vertex_ai/gemini-3.7-flash` (a gateway name).
- `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`: the gateway origin (without `/v1`) and the
  Bearer token. When the token is set, the agent also switches off the Vertex, Bedrock and
  Foundry flags and blanks `ANTHROPIC_API_KEY`, so the token is the only credential. A token
  needs a base URL: with a token and no `ANTHROPIC_BASE_URL`, each turn is refused before the
  CLI starts, naming the missing setting. A blank value counts as unset.
- `ANTHROPIC_API_KEY`: instead of the two above, the customer's own key (no token set). Set `ANTHROPIC_MODEL` to a Claude model with it; the default `vertex_ai/...` name only works through the gateway.

On the gateway route the platform passes the customer's own key to the agent; the job supplies the rest.

## Known limits

- The SDK wheel is about 150 MB because it bundles the Claude CLI (glibc only, no musl
  wheel), so each build downloads that much.
- The gateway answers the CLI's `count_tokens` call with 501 for non-Anthropic models;
  whether the CLI tolerates that is checked by a real run.
- No timeout wraps the CLI, and its API retries can hold a turn for minutes: a bad token makes the CLI retry for about 3 minutes (11 requests) before the turn fails.
- A killed agent leaves the CLI process running until it ends.

Install with `pip install -r requirements.txt` on Python 3.12.
