# Deep Agents: real reading, 18 Nov 2026

## Run
- Agent: `future-agi/agent-learning-kit`, `examples/harness/deep_agents_support`, at `5aa31fdb30aa02260d2d006cad121991a3b3828c`.
- Kit: `5aa31fdb30aa02260d2d006cad121991a3b3828c` (kit PR #341's head, read before #341 merged).
- Reading model: Claude Sonnet, through a local model gateway, `authoring_entrypoint --read-agent-only`.
- Spend: 8 model turns. The alias is unpriced, so `cost.json` reads $0.00; estimated from its tokens at Sonnet prices: $0.10-$0.54.
- Raw outputs: kept outside this repository.

## Expected
Written before the reading ran.

- **Agent:** chat; framework `deepagents` (or `deep_agents`).
- **Model:** `vertex_ai/gemini-3.7-flash` through `ChatOpenAI` (chat completions, not Responses).
- **Prompt:** `create_deep_agent(system_prompt=...)`, "You are a customer support agent for an online store. Use the order tools to look up orders and start returns, and never guess an order's status. Answer briefly."
- **Tools:** the same three, all `import` with module `agent` and callable the function name.
- **Runtime:**
  - #341's shape: no command, `execution: auto`, interface `callable` `fi.alk` (the kit's graph route from `langgraph.json`, graph `support` = `agent.py:graph`).
  - `langgraph dev` with an http `/runs/wait` interface is a miss. #341's bundling would correct it, but the reading is what's being proved.
  - Required environment `OPENAI_API_KEY` and `OPENAI_BASE_URL`.

## Result
pass

Every expected line holds. Framework `deepagents`; model `vertex_ai/gemini-3.7-flash`; prompt from `agent.py:68-72`; the three tools, entries `import`, module `agent`; no command, `execution: auto`, interface `callable` `fi.alk` (#341's graph route; not `langgraph dev`); required environment `OPENAI_API_KEY`, `OPENAI_BASE_URL`. Read on #341's head: the status stays pending until #341 merges.
