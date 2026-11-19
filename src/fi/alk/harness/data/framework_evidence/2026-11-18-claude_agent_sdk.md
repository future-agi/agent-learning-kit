# Claude Agent SDK: real reading, 18 Nov 2026

## Run
- Agent: `future-agi/agent-learning-kit`, `examples/harness/claude_agent_sdk_support`, at `d9b61c0370800a5faa3140012ec50211ba0bfd82`.
- Kit: `d9b61c0370800a5faa3140012ec50211ba0bfd82` (`feat/TH-8804-source-subfolder`).
- Reading model: Claude Sonnet, through a local model gateway, `authoring_entrypoint --read-agent-only`.
- Spend: 9 model turns. The alias is unpriced, so `cost.json` reads $0.00; estimated from its tokens at Sonnet prices: $0.19-$0.79.
- Raw outputs: kept outside this repository.

## Expected
Written before the reading ran.

- **Agent:** chat; framework `claude_agent_sdk`.
- **Model:** `vertex_ai/gemini-3.7-flash` (the `ANTHROPIC_MODEL` default), through the Anthropic-compatible gateway.
- **Prompt:** `SYSTEM_PROMPT`, "You are a customer support agent for an online store. Use the tools to look up orders and start returns, and never guess an order's status. Answer briefly."
- **Tools:**
  - The same three, by their own names; the SDK exposes them as `mcp__support__*`.
  - Entries `import`, module `agent`, callable the plain function (`look_up_order` and so on).
  - Binding to the `_look_up_order_tool` handlers is a miss.
- **Runtime:**
  - python 3.12; workdir `examples/harness/claude_agent_sdk_support`;
  - interface `callable` `fi.alk` through `agent_callback`, with no command;
  - environment `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN` (`ANTHROPIC_MODEL` optional).

## Result
miss

One line differs: the required environment. The reading requires `ANTHROPIC_MODEL`, `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY`. The expectation was `ANTHROPIC_BASE_URL` and `ANTHROPIC_AUTH_TOKEN`, with `ANTHROPIC_MODEL` optional. `ANTHROPIC_MODEL` has a default (`vertex_ai/gemini-3.7-flash`), and in gateway mode the agent blanks `ANTHROPIC_API_KEY` itself, so a run would ask the user for two values the agent doesn't need. Everything else holds: framework `claude_agent_sdk`; model `vertex_ai/gemini-3.7-flash` (provider anthropic); prompt `SYSTEM_PROMPT` (`agent.py:45-48`); the three tools, entries `import`, module `agent`, callable the plain functions (not the `_..._tool` handlers); interface `callable` `fi.alk`, no command.
