# Framework HTTP Transport Readiness Research

V1 needs framework simulation to extend past in-process shims without turning
hosted external services into release prerequisites. The local HTTP transport
proof keeps that boundary deterministic: the cookbook starts a loopback
`agent_learning` protocol endpoint, authenticates it with an env-backed bearer
token, returns framework runtime and trace payloads, and verifies that the
simulation report preserves tool calls, events, trace artifacts, state, and
redacted auth.

This is not an OpenEnv dependency. OpenEnv/Gymnasium remain compatibility
targets for environment replay semantics; Agent Learning stays the primary
optimization, simulation, evaluation, and pen-test layer.

## Sources

- https://opentelemetry.io/docs/concepts/signals/traces/ defines trace signals
  as the portable evidence surface for cross-boundary framework runtime spans.
- https://www.w3.org/TR/trace-context/ motivates carrying trace context across
  protocol boundaries instead of treating local process calls as the only
  observable execution path.
- https://arxiv.org/abs/2604.16762 is used for the release-check requirement
  that bearer-token material stays outside manifests, traces, and result JSON.
- https://arxiv.org/abs/2604.04820 is used for the protocol-first adapter
  shape: framework evidence travels through a normalized HTTP request/response
  contract that evaluators can inspect.

## Release Contract

`framework_http_transport_readiness` runs
`examples/sdk_framework_adapter_http_transport.py` with a release-only secret.
The gate requires:

- `agent.type=http`, `protocol=agent_learning`, and loopback-only endpoint host.
- `required_env=["AGENT_LEARNING_SDK_FRAMEWORK_HTTP_TRANSPORT_KEY"]` with no
  serialized secret leakage.
- `framework_http_status` tool call and local tool result state.
- `framework_http_transport`, `framework_runtime`, `framework_trace`, and
  `external_agent_trace` state keys.
- `framework_trace` and `external_agent_http_trace` trace artifacts.
- `framework_http_transport`, `framework_trace`, `framework_trace_span`, and
  `external_agent` events.
- `tool_selection_accuracy`, `framework_runtime_contract`,
  `framework_trace_coverage`, and `framework_trace_quality` at 1.0.

The `environment_10x_robustness` rollup includes this as
`local_http_framework_transport`, separate from the in-process framework matrix.
