# Framework Trace Export Adapter Research Note

Date: 2026-06-09

## Sources Checked

- OpenTelemetry trace concepts:
  https://opentelemetry.io/docs/concepts/signals/traces/
- OpenTelemetry Protocol exporter specification:
  https://opentelemetry.io/docs/specs/otlp/
- OpenTelemetry GenAI semantic conventions:
  https://opentelemetry.io/docs/specs/semconv/gen-ai/
- OpenInference semantic conventions:
  https://arize-ai.github.io/openinference/spec/semantic_conventions.html
- W3C Trace Context recommendation:
  https://www.w3.org/TR/trace-context/
- Future AGI TraceAI concepts:
  https://docs.futureagi.com/docs/tracing/concepts/traceai/

## Adapter Implications

Framework trace exports are not just debug blobs. They carry the execution graph
needed to prove what an adapter actually did: model calls, tool calls/results,
state or checkpoint writes, retrieval, memory, latency, cost, and error signals.
OpenTelemetry exports can arrive as OTLP JSON with `resourceSpans` and
`scopeSpans`; TraceAI/Future AGI exports and framework-specific SDKs can also
wrap spans under `data`, `traces`, `records`, or `spans`.
GenAI semantic conventions and OpenInference-style attributes make model,
tool, retrieval, and chain/agent spans portable enough for local simulation to
score them without importing the originating framework. Trace Context remains
important for preserving trace/span lineage when these exports are stitched back
into multi-agent, browser, voice, or orchestration runs.

The generic framework adapter should therefore treat explicit trace-export
payloads as first-class simulation evidence instead of leaving them in opaque
metadata. That lets the same local adapter run satisfy task, tool, runtime,
trace coverage, and adapter conformance gates.

## Implemented Contract

The generic adapter now recognizes local outputs carrying:

- `framework_trace`, `framework_trace_export`, `trace_export`, `traceai_export`,
  `otel_trace_export`, `otlp_export`, `opentelemetry_export`, or
  `open_telemetry_export`.
- OTLP-shaped `resourceSpans`, `resource_spans`, `scopeSpans`, or
  `scope_spans`.
- Explicit `framework_spans`, `trace_spans`, `span_records`,
  `framework_events`, `trace_events`, or `framework_trace_events`.

Those outputs normalize into:

- `framework_trace` state with spans, events, signals, sessions, checkpoints,
  summary counts, metadata, and optional adapter conformance results.
- A `trace` artifact with `metadata.kind == "framework_trace"`.
- `framework_trace_span`, `framework_trace_event`, and final
  `framework_trace` events.
- Ordinary tool calls and tool responses extracted from tool spans.
- Generated adapter-probe eval configs with `required_framework_trace` and
  `framework_trace_coverage` when the selected candidate emits trace evidence.
- Selected-output-derived `framework_trace_quality` gates for framework,
  span/event counts, model/tool/state/latency/cost signals, tool names, zero
  errors, and adapter-conformance findings.

The cookbook in `examples/sdk_framework_adapter_trace_export.py` covers the
strongest local path: adapter discovery selects `execute_task(dict)`, the
adapter emits an OTLP `resourceSpans` export with model, tool, state, latency,
and cost signals, and the promoted run requires the resulting trace state,
events, artifact, tool evidence, adapter conformance, trace coverage, and trace
quality metrics.

`agent-learn release-check` now runs that cookbook as
`framework_trace_export_readiness`. The gate requires the promoted local
LangGraph adapter to keep trace export evidence executable and evaluator-visible:
the result must be `agent-learning.run.v1`, select `execute_task(dict)`, emit
`framework_trace` state, `framework_trace_span`/`framework_trace` events,
`framework_runtime` and `framework_trace` artifacts, preserve `policy_lookup`
tool evidence, close adapter conformance, and pass framework runtime,
adapter-contract, trace coverage, and trace quality metrics.
