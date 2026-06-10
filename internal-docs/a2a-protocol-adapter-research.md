# A2A Protocol Adapter Research Note

Date: 2026-06-08

## Sources Checked

- Agent2Agent Protocol latest specification:
  https://a2a-protocol.org/latest/specification/
- Agent2Agent Protocol GitHub organization:
  https://github.com/a2aproject/A2A

## Adapter Implications

A2A is a remote-agent task protocol, not just a chat transcript. The adapter
surface needs to retain the protocol evidence that proves a local framework
actually delegated work and received task state back from another agent:

- Agent Card metadata, including capabilities, transports, default modalities,
  and skills.
- Message send records, including JSON-RPC-style `SendMessage` requests and the
  message parts sent to the remote agent.
- Task records, including task IDs, context IDs, status state, history, and
  artifacts.
- Streaming or event-style task status and task artifact updates.
- Part-level modalities: text, data, and file parts.

That evidence is the minimum needed for the same run to be useful to simulate,
evaluate, red-team, and optimize cross-agent systems. Without it, an A2A client
can look like ordinary text output and hide whether remote-agent collaboration,
status transitions, or artifacts were actually produced.

## Implemented Contract

The generic framework adapter now treats local outputs as A2A protocol traces
when they carry explicit A2A fields such as `a2a_protocol_trace`,
`a2a_events`, `a2a_messages`, `a2a_tasks`, `a2a_artifacts`, or `agent_card`, or
when a payload is explicitly marked as A2A and includes protocol fields such as
`messages`, `tasks`, `events`, `requests`, `responses`, or `artifacts`.

Those outputs normalize into:

- `a2a_protocol_trace` state with agent-card, skill, message, task, artifact,
  status, part, context, and protocol-event summaries.
- A `trace` artifact with `metadata.kind == "a2a_protocol_trace"`.
- JSON/text/file artifacts for A2A artifacts, depending on their part types.
- `a2a_agent_card`, `a2a_message`, `a2a_task`, `a2a_artifact`,
  `a2a_message_send`, `a2a_task_status`, `a2a_task_artifact`, and final
  `a2a_protocol_trace` events.
- Generated adapter-probe eval configs with `required_a2a_protocol`,
  `a2a_protocol_quality`, `a2a_protocol_coverage`, and `a2a_protocol_quality`
  metric weights when the selected candidate emits A2A evidence.

The cookbook in `examples/sdk_framework_adapter_a2a_protocol_trace.py` covers
the strongest local path: adapter discovery selects `send_message(dict)`, the
adapter emits an Agent Card, one `SendMessage` record, task status updates, a
task artifact update, and a final task with a structured decision artifact. The
generated eval config requires the resulting events, trace/json artifacts, and
`a2a_protocol_trace` state, plus protocol-specific coverage/quality metrics.

V1 release-check now runs the same cookbook as `protocol_adapter_readiness` and
requires the selected `send_message(dict)` adapter, `a2a_protocol_trace` state,
required A2A event types, protocol/json artifacts, and
`a2a_protocol_coverage` / `a2a_protocol_quality` scores of 1.0. The cookbook is
also promoted through `framework_adapter_probe_readiness` as
`a2a_protocol_trace_promotion`, where release-check requires discovery/probe
metadata, protocol state/event/artifact/summary evidence, and closed A2A metric
floors before counting it under the native adapter axis in
`environment_10x_robustness`.
