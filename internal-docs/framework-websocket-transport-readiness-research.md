# Framework WebSocket Transport Readiness Research

The WebSocket transport cookbook proves that Agent Learning can simulate a
framework runtime across a local realtime-style protocol boundary, not only an
in-process adapter or an HTTP request. The fixture uses LiveKit as the first
framework label because realtime agents naturally rely on persistent transport
channels, but the SDK helper accepts any framework name.

OpenEnv and Gymnasium remain compatibility shapes for environment replay. This
gate is Agent Learning-native: the manifest uses `agent.type=websocket`, a
local `ws://` loopback endpoint, a redacted bearer token, framework runtime
state, trace artifacts, protocol events, and evaluator-visible tool evidence.

## Sources

- https://www.rfc-editor.org/rfc/rfc6455 defines the WebSocket opening
  handshake and framed message exchange used by the local transport fixture.
- https://www.w3.org/TR/trace-context/ is used for cross-boundary trace context
  evidence that can survive a transport hop.
- https://opentelemetry.io/docs/specs/semconv/ is used for
  transport-normalized trace signal naming.
- https://arxiv.org/abs/2604.16762 is used for bearer-token mediation and
  redacted-auth evidence.

## Release Contract

`framework_websocket_transport_readiness` runs
`examples/sdk_framework_adapter_websocket_transport.py` with a local release
key. The gate requires:

- `required_env=["AGENT_LEARNING_SDK_FRAMEWORK_WEBSOCKET_TRANSPORT_KEY"]` with
  no serialized secret leakage.
- `agent.type=websocket`, `agent.protocol=agent_learning`, and a local
  loopback `ws://` endpoint.
- Framework `livekit`, transport `websocket`, status `101`, accepted handshake,
  one JSON request frame, and one JSON response frame.
- `framework_websocket_status` tool routing.
- `external_agent_websocket_trace`, `framework_websocket_transport`,
  `framework_runtime`, and `framework_trace` state.
- `framework_websocket_transport`, `framework_trace`, and
  `framework_trace_span` events plus trace artifacts.
- `tool_selection_accuracy`, `framework_runtime_contract`,
  `framework_trace_coverage`, and `framework_trace_quality` at 1.0.

The 10x robustness rollup counts this as
`local_websocket_framework_transport`, separate from
`local_http_framework_transport`, because realtime transport behavior has its
own handshake, frame, and protocol evidence.
