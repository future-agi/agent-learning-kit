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
- The CrewAI backend-tool route proves completed response streaming, but does not claim ALK has
  normalized AG-UI's split tool frames.
- CrewAI and ADK use Vertex Gemini; LangGraph uses a deterministic graph to isolate its adapter
  contract from provider behavior. The upstream commit and provider-patch digest are recorded.
- A hard controller crash can bypass `finally`; the bounded E2B TTL limits that orphan risk.

## Alternatives considered

One sandbox per case multiplies lifecycle and secret exposure without improving the requested
evidence. A user-editable case matrix makes the fixed ten-case result ambiguous and hand-syncs
scenario names with handlers. Both expose more policy than the selected one-command interface.

## Implementation reconciliation

The live matrix showed that the pinned LangGraph example's provider-specific agent emitted only
lifecycle events in this environment. The framework case now uses a checkpointed deterministic
LangGraph node through the same pinned official AG-UI adapter, separating adapter qualification
from provider behavior. CrewAI continuity was not accepted: repeated live runs streamed valid
answers but did not prove the second answer depended on the first. That case was narrowed to the
observable backend-tool route streaming contract.

## Open questions and risks

- CrewAI cross-turn continuity remains unqualified.
- AG-UI split tool-call normalization remains unqualified.

## Next implementation step

Treat continuity and split tool-call normalization as separate follow-up qualifications; do not
infer them from this successful transport matrix.
