Internal acceptance fixture for multi-agent testing in CI. It is not a sample agent, not a template, and not a statement of which frameworks are supported.

A returns coordinator hands work to two workers (order lookup and refund) on the OpenAI Agents SDK; `ledger.py` is the world they call.

Run it locally: `python ledger.py` (port `$LEDGER_PORT`, default 8090), then `RETURNS_API_URL=http://127.0.0.1:8090 python agent.py`.
`agent.py` also reads a model key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), `RETURNS_MODEL`, `OPENAI_BASE_URL`,
`ORDER_LOOKUP_API_URL`, `REFUND_API_URL`, `PORT` and the OTLP trace endpoint; its module docstring lists each one.
