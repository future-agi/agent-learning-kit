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
  Bearer token. When the token is set, the agent also switches off the CLI's other provider
  routes (Vertex, Bedrock, Foundry, Anthropic on AWS and Google Cloud, Mantle, Gateway, and a
  Unix socket) and blanks `ANTHROPIC_API_KEY`, so the token is the only credential and the base
  URL the only place it goes. A token
  needs a base URL: with a token and no `ANTHROPIC_BASE_URL`, each turn is refused before the
  CLI starts, naming the missing setting. A blank value counts as unset.
- `ANTHROPIC_API_KEY`: instead of the two above, the customer's own Anthropic key (no token
  set). Set `ANTHROPIC_MODEL` to a Claude model with it; the default `vertex_ai/...` name only
  works through the gateway.

On the platform's gateway route, a customer's own `ANTHROPIC_API_KEY`, saved as a provider
secret, also reaches the agent; the job supplies the other variables.

## Known limits

- The SDK wheel is about 150 MB because it bundles the Claude CLI (glibc only, no musl
  wheel), so each build downloads that much.
- The gateway answers the CLI's `count_tokens` call with 501 for non-Anthropic models;
  whether the CLI tolerates that is checked by a real run.
- A turn is stopped after 280 s, below the kit's 300 s per-turn deadline, and its CLI and
  config folder are cleaned up. Until then the CLI's own API retries can hold it: a bad token
  made the CLI retry for about 3 minutes (11 requests) before the turn failed.
- An error from the CLI or the model comes back as a short message (at most 500 characters),
  with the token and key values removed.
- Stopping the agent stops its CLI too: the SDK starts the CLI in the agent's own process
  group, and the kit stops an agent by signalling that whole group. Killing only the agent's
  own process, not its group, would leave the CLI running until it ends.

Install with `pip install -r requirements.txt` on Python 3.12.
