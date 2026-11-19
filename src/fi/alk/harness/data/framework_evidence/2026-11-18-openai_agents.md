# OpenAI Agents SDK: real reading, 18 Nov 2026

## Run
- Agent: `future-agi/agent-learning-kit`, `examples/harness/openai_agents_support`, at `5aa31fdb30aa02260d2d006cad121991a3b3828c`.
- Kit: `5aa31fdb30aa02260d2d006cad121991a3b3828c` (kit PR #341's head, read before #341 merged).
- Reading model: Claude Sonnet, through a local model gateway, `authoring_entrypoint --read-agent-only`.
- Spend: 9 model turns. The alias is unpriced, so `cost.json` reads $0.00; estimated from its tokens at Sonnet prices: $0.20-$0.74.
- Raw outputs: kept outside this repository.

## Expected
Written before the reading ran.

- **Agent:** chat; framework `openai_agents`.
- **Model:** `vertex_ai/gemini-3.7-flash` (the `OPENAI_MODEL` default), on an OpenAI-compatible chat-completions client.
- **Prompt:** the `Agent(instructions=...)` text, "You are a customer support agent for an online store. Use the tools to look up orders and start returns, and never guess an order's status. Answer briefly."
- **Tools:**
  - `look_up_order(order_id)`
  - `start_return(order_id, reason)`
  - `return_policy()`
- **Tool entries:** #341's guidance shape is `unreachable` with module `agent` and callable the tool's name. The tools are `@function_tool` objects, and #341 traces that shape. A module from the repo root, or no callable, is a miss.
- **Runtime:**
  - python 3.12; `pip install -r requirements.txt`; workdir and subfolder `examples/harness/openai_agents_support`;
  - interface `callable` `fi.alk` (through `agent_callback`), with no command;
  - required environment `OPENAI_API_KEY` and `OPENAI_BASE_URL`.

## Result
pass

Every expected line holds. Framework `openai_agents`; model `vertex_ai/gemini-3.7-flash` (provider openai); prompt from `agent.py:74-77`; the three tools with their arguments; entries `unreachable`, module `agent`, callable the tool name (#341's shape, which #341 traces); no command, `execution: auto`, interface `callable` `fi.alk`; required environment `OPENAI_API_KEY`, `OPENAI_BASE_URL`; python 3.12, `pip install -r requirements.txt`. Read on #341's head: the status stays pending until #341 merges.
