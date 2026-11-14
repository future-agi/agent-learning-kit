Internal acceptance fixture for multi-agent testing in CI. It is not a sample agent, not a template, and not a statement of which frameworks are supported.

A returns coordinator hands work to two workers (order lookup and refund) on the OpenAI Agents SDK; `ledger.py` is the world they call.

A voice entry, `voice_agent.py` (dependencies in `requirements-voice.txt`), puts the same coordinator behind a LiveKit
voice agent: each spoken turn is answered by `agent.py`'s `reply`, so the topology and the fault switches are the same.

Run it locally: `python ledger.py` (port `$LEDGER_PORT`, default 8090), then `RETURNS_API_URL=http://127.0.0.1:8090 python agent.py`.
`agent.py` also reads a model key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), `RETURNS_MODEL`, `OPENAI_BASE_URL`,
`ORDER_LOOKUP_API_URL`, `REFUND_API_URL`, `PORT` and the OTLP trace endpoint; its module docstring lists each one.

Fault switches (`RETURNS_FAULT`, comma-separated, read once at import; unset means none), each a deliberate defect for a negative control:
`skip_refund_check` (the Refund worker has only `issue_refund` and the coordinator always asks for a refund),
`payment_times_out` (`issue_refund` waits `RETURNS_HANG_SECONDS`, default 20, then returns a timeout error),
`refund_never_returns` (`issue_refund` waits `RETURNS_HANG_SECONDS`; set it above the chat target timeout),
`bypass_channel` (the Refund worker's client uses the shared address). The two waiting faults wait only on a
scenario call (the harness's `X-ALK-Scenario-Call` header), never on a readiness or certification probe. `RETURNS_TRACING=off` exports no spans.
