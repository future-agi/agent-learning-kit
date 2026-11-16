Internal acceptance fixture for multi-agent testing in CI. It is not a sample agent, not a template, and not a statement of which frameworks are supported.

A returns coordinator hands work to two workers (order lookup and refund) on the OpenAI Agents SDK; `ledger.py` is the world they call.

A voice entry, `voice_agent.py` (dependencies in `requirements-voice.txt`), puts the same coordinator behind a LiveKit
voice agent: each spoken turn is answered by `agent.py`'s `reply`, so the topology and the records' defects are the same.

Run it locally: `python ledger.py` (port `$LEDGER_PORT`, default 8090), then `RETURNS_API_URL=http://127.0.0.1:8090 python agent.py`.
`agent.py` also reads a model key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), `RETURNS_MODEL`, `OPENAI_BASE_URL`,
`ORDER_LOOKUP_API_URL`, `REFUND_API_URL`, `PORT` and the OTLP trace endpoint; its module docstring lists each one.

Deliberate defects for negative controls belong to ledger records, never to the process, so one pinned build carries
every one and a scenario picks a defect by the order it names. A record's `fault` field (ledger answers carry it; the
agent keeps it from the model):
`skip_refund_check` (order 1047, refunded already: both workers' refund check comes back empty, so a second refund goes
through), `payment_times_out` (order 1045: the ledger takes no refund and `issue_refund` waits `RETURNS_HANG_SECONDS`,
default 20, before reporting the timeout), `refund_never_returns` (order 1046: the same, with the wait set above the
chat target timeout), `bypass_channel` (order 1048: the Refund worker issues the refund on the shared address). Only a
turn about one of those orders waits, so a probe that names none of them never does. `RETURNS_TRACING=off` exports no
spans.

With `REFUND_WORKER_URL` set (`docker-compose.worker-service.yml`), the Refund worker runs as a service of its own
(`worker_service.py`) and the coordinator reaches it over HTTP; the contract then declares that worker with
`process: refund-worker`, and a harness ends that process alone when the worker's deadline passes.
