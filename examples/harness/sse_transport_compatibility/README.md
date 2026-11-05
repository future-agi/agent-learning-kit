# Live SSE transport qualification

This reference exercises ALK against the official AG-UI CrewAI backend-tool example. It pins the
upstream repository and uses the conversational route because CrewAI keys its native conversation
state by AG-UI `threadId`.

The credential-free ALK tests use a local SSE server for framing, limits, explicit completion,
disconnects, cancellation, and completed-tool safety. The upstream qualification is separate. Its
supported `MODEL` override selects `vertex_ai/gemini-3.7-flash` in Vertex AI's `global` location.

## Verify and materialize the reference

```bash
python examples/harness/sse_transport_compatibility/verify_reference.py
python examples/harness/sse_transport_compatibility/materialize_reference.py /tmp/sse-reference
python examples/harness/sse_transport_compatibility/verify_reference.py \
  --source /tmp/sse-reference/ag-ui-crewai-backend-tool-rendering
```

Start the unmodified upstream dojo from the materialized `examples` directory:

```bash
export GOOGLE_APPLICATION_CREDENTIALS="/Users/rishavhada/Documents/futureagi/oss/future-agi/futureagi/Vertex_AI_Creds.json"
export VERTEXAI_PROJECT=futureagiprimary
export VERTEXAI_LOCATION=global
export MODEL=vertex_ai/gemini-3.7-flash
uv sync
uv pip install --python .venv/bin/python google-auth
uv run dev
```

`GOOGLE_APPLICATION_CREDENTIALS` points LiteLLM at the local service-account JSON. Keep that file
outside this reference: never copy its contents or an access token into the materialized source,
qualification output, or repository.

The pinned example's default dependency set omits `google-auth`. Install it into the materialized
virtual environment after `uv sync`; installing it only with `uv run --with` does not reach the
Uvicorn reload worker.

With the dojo running on port 8000, exercise two turns through ALK's SSE wrapper:

```bash
# Run from the ALK checkout, not the materialized upstream directory.
uv run python examples/harness/sse_transport_compatibility/qualify_reference.py
```

The qualifier prints only success flags, response lengths, and stream counts. It does not print
credentials, raw provider data, or response content.

POST AG-UI `RunAgentInput` bodies to
`/conversational_flows/backend_tool_rendering` using the request and SSE mappings in
`ag_ui_crewai/reference.json`. Reuse one `threadId` for both declared turns and use a fresh `runId`
for each turn.

`RUN_FINISHED` is the only successful terminal event and `RUN_ERROR` is failure. EOF without either
is not success. This conversational route sends its answer in repeated `MESSAGES_SNAPSHOT` events;
the mapping takes the cumulative content of the last message only when that message is an assistant.

Tool evidence is intentionally still marked pending. AG-UI streams a tool call across
`TOOL_CALL_START`, `TOOL_CALL_ARGS`, `TOOL_CALL_END`, and `TOOL_CALL_RESULT`; the existing ALK
generic collectors consume complete action/result objects. Do not claim full fixture qualification
or re-execute the streamed call until a declarative pairing proves the source tool and result.
The live Vertex run observed exactly one `get_weather` start/result sequence, so source execution is
proven; normalized ALK retention of that split evidence is the remaining gap.

Client disconnect proves that ALK closes its socket. It does not prove CrewAI stopped provider work;
the upstream conversational worker may continue after its client disconnects.
