Internal acceptance fixture for multi-agent testing in CI. It is not a sample agent, not a template, and not a statement of which frameworks are supported.

A returns coordinator hands work to two workers (order lookup and refund) on the OpenAI Agents SDK.
`agent.py` reads a model key, the returns service address and the OTLP trace endpoint; its module docstring lists each one.
