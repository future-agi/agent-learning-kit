# Voice-agent environment validation matrix

Status date: 22 August 2026

This is the release matrix for the environment plane. Repository count is not a useful success
metric by itself: several agents can share the same framework, transport and dependency shape.
ALK is qualified by crossing packaging, runtime, infrastructure, networking and state behavior.

The invariant remains unchanged: ALK may provide and isolate infrastructure around submitted
code, but it does not rewrite the agent or recreate its tools.

## Evidence levels

Every row must state the highest level actually reached:

| Level | Evidence required |
|---|---|
| E0 — discovered | Repository and declared configuration were inspected without executing source. |
| E1 — admitted | Packaging, security and credentials passed deterministic preflight. |
| E2 — built | The submitted runtime image or repository Compose model built unchanged. |
| E3 — ready | The complete selected environment started and passed protocol-aware readiness. |
| E4 — exercised | The submitted runtime performed real reads/writes against its dependencies. |
| E5 — isolated | Mutation, reset, concurrent-run isolation and complete cleanup were proven. |
| E6 — called | A real bidirectional voice call completed and transcript/audio evidence was ingested. |
| E7 — platform | The job was launched by the platform and its verified artifacts were reconciled there. |

No lower level may be described as end-to-end. A deterministic agent failure at E6 is still a
valid environment success when media, evidence and failure attribution are correct.

## Dimensions that must be covered

1. **Packaging:** root/nested Compose, Dockerfile-only, monorepo component selection, prebuilt
   images, multi-language runtimes and repositories with no container packaging.
2. **State services:** relational/analytical SQL, cache, document store, vector store, object
   storage, queues/event buses, local files and embedded databases.
3. **Runtime services:** browser automation, code execution, MCP/tool servers, local model
   servers, native libraries, CPU architecture constraints and optional GPU devices.
4. **Networking:** HTTP, WebSocket, WebRTC, SIP/RTP/UDP, callbacks requiring public ingress,
   egress allow-lists and profile-gated services with large port ranges.
5. **Lifecycle:** migrations, seed generation, per-scenario setup, destructive reset, parallel
   isolation, cancellation, retry and cleanup after partial failure.
6. **Supply chain and credentials:** private Git repositories, private images/packages, external
   provider keys, OAuth credential files and harness-generated internal service secrets.

## Current executable coverage

| Environment shape | Evidence | Result |
|---|---:|---|
| Uber reference agent — local SDK path | E7 | One SDK request completed environment creation, 72 records across 12 collections, 10 validated scenarios, 10/10 terminal LiveKit calls, grading, and platform ingestion with transcripts and recording URLs for every call. |
| Uber reference agent — platform/hosted path | E7 | One typed platform job autonomously completed the full ALK pipeline through the locally hosted sandbox: 23/23 environment probes, 10 diverse scenarios, 10/10 terminal calls, and reconciled transcripts, checks, and recordings. |
| Submitted Compose: ClickHouse + Redis + unchanged worker | E5 | Two concurrent copies, independent seeded state, reset and cleanup passed. |
| Dockerfile-only: managed ClickHouse + Redis | E5 | ALK generated only the declared services; runtime read both and reset passed. |
| Dockerfile-only: managed Postgres | E4 | Submitted SQL init, runtime query, readiness and cleanup passed. |
| Dockerfile-only: managed MongoDB + Qdrant | E5 | Runtime wrote both stores; volume recreation made both clean on the second run. Integration gate completed in 40.81 seconds. |
| Public Dograh root Compose | E3 | Unchanged API, UI, pgvector/Postgres, authenticated Redis and MinIO became ready in 36.391 seconds and were cleaned up. Profile-gated TURN/init/tunnel services stayed dormant. |
| Public Voice Noob Compose | E5 infrastructure / E0 runtime | Submitted Postgres and Redis started/reset cleanly; its host-run OpenAI Realtime application is not container-packaged. |
| Official LiveKit Python examples | E6 | 15 calls across three agents; 14 completed and one deterministic agent validation loop was preserved. |
| Pipecat Cloud Starter compatibility checkout | E6 test-only | One real Pipecat-over-LiveKit call completed with audible recordings; upstream archived entrypoint is not currently reproducible unchanged. |
| Vapi WebSocket | E6 | Real call completed, evaluation 0.8401, 14 messages, 1,071 transcript characters and audible combined/provider recordings. |
| Retell Web Call | E6 | Real call completed, evaluation 0.8422, 11 messages, 1,432 transcript characters and audible combined/provider recordings. |

## Uber reference-agent acceptance

The Uber ride voice agent is the strongest full-pipeline acceptance case because it exercises the
environment plane, scenario/data generation, WebRTC execution, evidence grading, artifact
ingestion and both product entry paths. The pass count below is an agent-evaluation result, not an
environment health signal.

| Entry path | Run identity | Environment and scenario result | Call and platform result |
|---|---|---|---|
| Local SDK | Voice run `run-20260821-213433`; platform execution `a9657a47-ea7c-481b-bf48-5acbf60ab902` | Isolated Postgres and tools API; 72 records/12 collections; sealed bundle; 10 diverse scenarios passed reference, no-op and diversity gates. | 10/10 terminal; 10/10 transcripts; 10/10 recording URLs; 3 scenario passes and 7 valid agent-learning failures. |
| Platform to locally hosted ALK sandbox | Job `554dface-4441-4801-93d4-8bd4687ae196`; execution `77230197-f9f5-4d0b-b885-ae6a269bf3de`; voice run `run-20260821-224823` | One typed job autonomously performed repository understanding, isolated provisioning, 23/23 probes, data expansion and 10 three-gate-validated scenarios. | 10/10 terminal; 10/10 transcripts; 10/10 recordings and URLs; complete result reconciliation; no manual stage-control chat. |

The data covered ten callers across San Francisco, Oakland and Bengaluru with distinct account
states, addresses, payment methods, card endings and non-trivial six-digit OTPs. Missing target
tool calls did not receive evaluation credit, and setup actions were excluded from agent evidence.
The seven failed scenario grades therefore remain useful agent findings rather than being
misclassified as environment failures.

## Public repositories in the expanded batch

| Repository | Environment dimensions | Current evidence and finding |
|---|---|---|
| `dograh-hq/dograh` | Prebuilt API/UI, Postgres/pgvector, authenticated Redis, MinIO, optional TURN/tunnel profiles | E3. This run found and fixed authenticated-Redis readiness, empty-body HTTP readiness and dormant-profile port allocation. |
| `Mainer-g00t/voice-asterisk-agent` | Asterisk, AudioSocket, Postgres, Redis, migrations, local STT/LLM/TTS, monitoring, Docker socket | E1 blocked. Missing external env injection, several independently runnable components and forbidden Docker-socket access require explicit policy/component handling. |
| `Aeyetech/voice-agent` | Fully local LiveKit, Whisper, llama.cpp, Kokoro, large model downloads, CPU/GPU variants | E1. Packaging is found, but a machine-specific absolute model bind mount and high resource requirements must be admitted explicitly before a costly run. |
| `SalesforceAIResearch/VoiceAgentRAG` | Python, FAISS/Qdrant, local/Ollama or cloud models, no container packaging | E0. Correctly exposes the case-three packaging gap; ALK must not invent its runtime silently. |
| `livekit-examples/agent-starter-node` | Node 24, pnpm, Dockerfile-only | E1 blocked. The published Dockerfile requires an absent `pnpm-lock.yaml`. ALK now detects every source in multi-source `COPY` before invoking BuildKit. |

These checkouts remain outside the product repository. Findings are preserved against their
immutable commit revisions; compatibility edits are never counted as unchanged-repository runs.

## Next qualification batches

The following are separate gates, not a single large demo:

1. **Queue and object gate:** RabbitMQ, NATS and MinIO with real publish/consume/upload/download,
   reset and two concurrent environments.
2. **Browser/code gate:** Playwright/Chromium plus a restricted Python code executor producing a
   file/graph, with CPU/memory/time/egress controls and artifact collection.
3. **Local inference gate:** CPU-only Whisper + local OpenAI-compatible LLM + local TTS, followed
   by an explicit GPU-capability admission test. No silent CPU/GPU substitution.
4. **Telephony gate:** Asterisk AudioSocket and SIP/RTP port ranges, then real inbound/outbound
   calls. Host networking and Docker-socket requirements must remain denied unless a dedicated
   runtime provider exposes a safer capability.
5. **Supply-chain gate:** private GitHub App checkout, private container registry, private Python
   and npm package credentials, all job-scoped and redacted.
6. **Hosted/platform gate:** repeat E5/E6 cases through the hosted provider and prove event,
   result and artifact reconciliation at E7.

Each new service family needs both a small deterministic conformance agent and at least one public
repository. The fixture isolates lifecycle bugs cheaply; the public repository catches packaging
and configuration assumptions that a fixture would hide.
