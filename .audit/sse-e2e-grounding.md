# SSE E2E grounding

## Existing ALK path

`HTTPAgentWrapper` owns an HTTP client session and delegates `text/event-stream` bodies to
`SSEDecoder` and `SSEReducer`. A successful call requires an explicitly mapped terminal event;
EOF without it is failure. The reducer supports delta, cumulative, and final text mappings,
deduplicates event IDs, carries request continuity through ALK's existing templates, and closes
the response when the caller cancels. Existing local tests cover framing and lifecycle behavior.

The current upstream qualification is one pinned AG-UI CrewAI example. It proves two live Vertex
turns locally, but it does not run in E2B and split AG-UI tool events are not normalized into ALK's
completed-tool evidence.

## Framework boundary

Use one upstream source pin, `ag-ui-protocol/ag-ui@2b967c34c49759c4afe0f0a176b281a0f645de34`,
which contains official CrewAI, LangGraph, and Google ADK middleware HTTP/SSE examples. They all
accept AG-UI `RunAgentInput` and emit explicit `RUN_FINISHED` or `RUN_ERROR`, while exercising three
different agent runtimes. Native ADK `/run_sse` and LangServe are out of scope because their
terminal framing does not currently satisfy ALK's explicit-completion contract.

## E2B boundary

One controller creates one private E2B sandbox, uploads only the current ALK branch plus the
qualification bundle, uploads the authorized Vertex credential as a mode-0600 file outside the
artifact tree, runs all cases, downloads sanitized JSON evidence, and kills the sandbox in a
`finally` block. Every sandbox process must use the uploaded branch via `PYTHONPATH`, and the run
must record and compare the imported SSE module hash so the preinstalled template copy cannot be
mistaken for the branch under test.

The current shell has no `E2B_API_KEY` or template reference. The controller therefore fails
closed before provisioning when the key is absent and may default to E2B's generic `base`
template. The live remote verdict stays unverified until the key is supplied.

## Qualification contract

Exactly ten cases must produce exactly ten receipts. Three cases are real-framework smoke tests
(CrewAI continuity and tool call, LangGraph streamed text, ADK middleware streamed text/tool);
seven are deterministic HTTP/SSE lifecycle cases (delta, cumulative, final-only, explicit done
before EOF, terminal error, incomplete EOF/reset, cancellation and recovery). Only behavior
observed through ALK counts. A setup/import/mock-only check cannot pass a case. Any missing,
failed, or withheld receipt prevents an aggregate pass.

The deterministic cases remain transport qualification, not framework compatibility evidence.
Framework receipts record the pinned repository, commit, subdirectory, installed versions,
endpoint, terminal event, and sanitized source-side tool count where applicable.
