# Ten-case E2B SSE qualification design

## Problem

The current qualification proves one CrewAI SSE endpoint locally. It does not prove that the
current ALK branch works inside E2B, across multiple frameworks, or across the stream lifecycle
behaviors ALK promises. The E2B template can also contain an older ALK copy, so a successful run
must prove which source file each case imported.

## Usage

```bash
export E2B_API_KEY=...
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/Vertex_AI_Creds.json
export VERTEXAI_PROJECT=...
uv run --with e2b==2.37.1 \
  python examples/harness/sse_transport_compatibility/e2b_qualify.py \
  --output /tmp/alk-sse-e2b.json
```

The command creates one private sandbox, runs exactly ten cases, downloads one sanitized report,
and destroys the sandbox. It exits zero only when all ten receipts pass, the imported SSE module
matches the uploaded branch, and teardown succeeds. Missing credentials fail before provisioning.

## Shape

`e2b_qualify.py` owns the local trust boundary: input validation, an explicit source bundle,
private credential upload, one E2B lifecycle, report validation, and restrictive output. It never
uploads the E2B API key or downloads framework/provider logs.

`e2b_cases.py` runs inside the sandbox. It owns the fixed ten-case registry, framework process
lifecycle, calls through `HTTPAgentWrapper`, per-process imported-module hashing, and allowlisted
receipts. The registry contains three framework cases and seven deterministic transport cases.
Each case always gets one of `passed`, `failed`, or `withheld`; sandbox loss cannot silently omit
later cases.

The report contains only case IDs, framework/source provenance where applicable, bounded counts,
booleans, error codes, and module hashes. It excludes prompts, model responses, raw SSE payloads,
headers, tokens, credential paths, and arbitrary exception strings.

The public surface is one command. Framework pins, endpoint mappings, scenario dispatch,
sanitization, and process control remain internal. Validation occurs at the controller/sandbox and
JSON/report boundaries; the fixed registry is the single source of truth for case enumeration.

## Synthesis decision

The selected arena design was the two-file controller/runner candidate. It scored highest on the
small public surface, strict exact-ten result shape, per-process branch provenance, secret cleanup,
and behavior-level assertions. The alternate public JSON matrix/package design was rejected
because it added shallow modules and a second way to alter a qualification that must stay fixed.
Its useful failure-accounting rule was retained: unattempted cases become explicit `withheld`
receipts.

## Tradeoffs accepted

- One sandbox reduces secret uploads and orphan surfaces, but framework environments must remain
  isolated inside it.
- Source-side tool counts prove framework execution without claiming ALK has normalized AG-UI's
  split tool frames.
- Provider-only patches select Vertex Gemini for upstream examples; the upstream commit and patch
  digest must both be recorded.
- A hard controller crash can bypass `finally`; the bounded E2B TTL limits that orphan risk.

## Alternatives considered

One sandbox per case multiplies lifecycle and secret exposure without improving the requested
evidence. A user-editable case matrix makes the fixed ten-case result ambiguous and hand-syncs
scenario names with handlers. Both expose more policy than the selected one-command interface.

## Implementation reconciliation

No accepted deviations yet. If live framework installation requires a different provider adapter,
update this design and record the exact patch rather than weakening a receipt.

## Open questions and risks

- Will the pinned LangGraph `langchain-google-genai` version accept `gemini-3.7-flash` through
  Vertex in E2B?
- Will the pinned ADK example accept the same model name with ADC in the selected region?
- Which E2B template is available to this account? The generic `base` template is the default.

## Next implementation step

Build strict result validation and credential-free lifecycle tests before running the live matrix.
