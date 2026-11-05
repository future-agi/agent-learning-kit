# Live SSE transport qualification

This reference exercises ALK against the official AG-UI CrewAI backend-tool example. It pins the
upstream repository and uses the conversational route because CrewAI keys its native conversation
state by AG-UI `threadId`.

The credential-free ALK tests use a local SSE server for framing, limits, explicit completion,
disconnects, cancellation, and completed-tool safety. The upstream qualification is separate: the
pinned example calls `openai/gpt-5.4` and requires `OPENAI_API_KEY`.

## Verify and materialize the reference

```bash
python examples/harness/sse_transport_compatibility/verify_reference.py
python examples/harness/sse_transport_compatibility/materialize_reference.py /tmp/sse-reference
python examples/harness/sse_transport_compatibility/verify_reference.py \
  --source /tmp/sse-reference/ag-ui-crewai-backend-tool-rendering
```

Start the unmodified upstream dojo from the materialized `examples` directory:

```bash
OPENAI_API_KEY=... uv sync
OPENAI_API_KEY=... uv run dev
```

POST AG-UI `RunAgentInput` bodies to
`/conversational_flows/backend_tool_rendering` using the request and SSE mappings in
`ag_ui_crewai/reference.json`. Reuse one `threadId` for both declared turns and use a fresh `runId`
for each turn.

`RUN_FINISHED` is the only successful terminal event and `RUN_ERROR` is failure. EOF without either
is not success. The example's `TEXT_MESSAGE_CONTENT.delta` values are appended.

Tool evidence is intentionally still marked pending. AG-UI streams a tool call across
`TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, and `TOOL_CALL_RESULT`; the existing ALK
generic collectors consume complete action/result objects. Do not claim full fixture qualification
or re-execute the streamed call until a declarative pairing proves the source tool and result.

Client disconnect proves that ALK closes its socket. It does not prove CrewAI stopped provider work;
the upstream conversational worker may continue after its client disconnects.
