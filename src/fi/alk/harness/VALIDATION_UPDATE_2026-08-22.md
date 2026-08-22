# ALK harness validation update — 22 August 2026

## Executive summary

ALK's local environment plane is now proven across both full Uber acceptance paths, three voice
connectors, multiple repository packaging shapes, and five managed state-service families. The
strongest evidence is the Uber reference agent: a clean SDK run and a clean platform-submitted run
each autonomously provisioned the environment, generated ten scenarios, completed ten LiveKit
calls, graded the results and reconciled transcripts and recordings to the platform.

The broader public-repository batch is intentionally reported by evidence level. Some repositories
ran end to end, some proved only environment startup, and others exposed valid packaging,
security, architecture or upstream reproducibility failures. None of those lower-level checks is
presented as an end-to-end agent success.

## Evidence scale

| Level | Meaning |
|---|---|
| E0 | Repository/configuration discovered. |
| E1 | Deterministic packaging, credential and security admission passed. |
| E2 | Submitted image or Compose model built unchanged. |
| E3 | Complete selected environment became ready. |
| E4 | Submitted runtime exercised real dependency reads/writes. |
| E5 | Reset, isolation and complete cleanup were also proven. |
| E6 | A real bidirectional voice call produced transcript/audio evidence. |
| E7 | Platform launch and verified artifact reconciliation were proven. |

## Uber reference-agent acceptance

### Local SDK path — E7

- Agent: `uber-voice-sdk-clean`
- Voice run: `run-20260821-213433`
- Platform execution: `a9657a47-ea7c-481b-bf48-5acbf60ab902`
- Local evidence: `alk-pr58/artifacts/e2e-validation/sdk-clean-20260822`
- Environment: isolated Postgres and tools API; 72 records across 12 collections.
- Scenario generation: ten diverse scenarios accepted only after reference-solution, no-op and
  diversity validation.
- Calls: 10/10 terminal, 10/10 transcripts and 10/10 stored recording URLs.
- Evaluation: 3 passed; 7 retained valid agent-learning failures. The score is not an
  infrastructure failure.

### Platform to locally hosted ALK sandbox — E7

- Harness job: `554dface-4441-4801-93d4-8bd4687ae196`
- Platform execution: `77230197-f9f5-4d0b-b885-ae6a269bf3de`
- Voice run: `run-20260821-224823`
- Local evidence: `alk-pr58/artifacts/e2e-validation/platform-clean-20260822`
- One typed platform job autonomously performed understanding, provisioning, data setup, scenario
  generation, calling, grading and delivery. No manual stage-control chat was used.
- Environment: 23/23 probes passed; data expanded to 72 records across 12 collections.
- Calls: 10/10 terminal with 10/10 transcripts, recordings and recording URLs reconciled on the
  platform.

The final scenario data used ten callers across San Francisco, Oakland and Bengaluru with varied
identities, account states, addresses, payment methods, card endings and non-trivial six-digit
OTPs. Setup actions were excluded from agent evidence, and missing tool calls could not satisfy a
tool-dependent check.

## Current executable environment and connector matrix

| Case | Highest evidence | What was actually proven |
|---|---:|---|
| Uber via local SDK | E7 | Full autonomous ten-scenario pipeline and platform persistence. |
| Uber via platform/hosted runner | E7 | Full platform job to local ALK sandbox, ten calls and artifact reconciliation. |
| Submitted Compose: ClickHouse + Redis | E5 | Concurrent copies, isolated seeded state, reset and cleanup. |
| Dockerfile-only: ClickHouse + Redis | E5 | ALK-added infrastructure around unchanged runtime; reads and reset. |
| Dockerfile-only: Postgres | E4 | SQL initialization, runtime query, readiness and cleanup. |
| Dockerfile-only: MongoDB + Qdrant | E5 | Writes/reads to both, clean volume recreation, isolation and cleanup; 40.81-second gate. |
| Dograh Compose stack | E3 | Unchanged API/UI, pgvector/Postgres, authenticated Redis and MinIO ready in 36.391 seconds. |
| LiveKit Python examples | E6 | 15 real calls across three agents; 14 completed and one deterministic agent loop preserved. |
| Pipecat compatibility checkout | E6 test-only | One audible Pipecat-over-LiveKit call; unchanged archived upstream runtime remains unreproducible. |
| Vapi WebSocket | E6 | Completed real call; score 0.8401; 14 messages; transcript plus audible SDK/provider recordings. |
| Retell Web Call | E6 | Completed real call; score 0.8422; 11 messages; transcript plus audible SDK/provider recordings. |
| Voice Noob dependencies | E5 infrastructure / E0 runtime | Postgres and Redis lifecycle passed; its host-run direct OpenAI Realtime app is not packaged or callable by the current transport. |

## Public-repository admission and runtime findings

| Repository | Result |
|---|---|
| Bolna | Nested Compose discovered; correctly stopped on required provider/telephony credentials and host AWS mounts. |
| Pipecat `websocket` example | Missing published `uv.lock` now rejected statically in about 1.2 seconds instead of failing late in BuildKit. |
| TEN Framework `ai_agents` | Production Dockerfile selected and `linux/amd64` preserved; Bun exited 132 under ARM emulation, classified as host/runtime compatibility. |
| Pipecat Cloud Starter | Image builds, but unchanged archived entrypoint breaks against unpinned current Pipecat; compatibility call reported separately. |
| Voice Noob | Compose infrastructure lifecycle passed; agent runtime and direct OpenAI Realtime transport remain separate gaps. |
| Vocode `telephony_app` | Upstream Dockerfile fails because current Poetry removed its `--no-dev` usage; no silent patch was applied. |
| Open Telephony Stack | Ambiguous components, host networking/system mounts and non-local build context are reported instead of guessed. |
| Dograh | E3 unchanged stack success; also drove readiness and profile-port fixes. |
| Voice Asterisk Agent | Complex components found; external env injection, component selection and forbidden Docker socket remain admission requirements. |
| Aeyetech local voice agent | Local-model stack found; absolute model mount and large CPU/GPU requirements require explicit resource admission. |
| Salesforce VoiceAgentRAG | Correctly classified as the unsupported no-Compose/no-Dockerfile packaging case. |
| LiveKit Node starter | Every multi-source `COPY` input is checked; absent published `pnpm-lock.yaml` is rejected before Docker. |

## Robustness improvements driven by this matrix

- Nested Compose/Dockerfile discovery and conservative monorepo ambiguity handling.
- Static missing-build-input checks, including multi-source `COPY`.
- Correct handling of prebuilt API/backend services and profile-gated services.
- Runtime-scoped credential discovery instead of scanning every optional provider module.
- Authenticated Redis and empty-body HTTP readiness support.
- Managed MongoDB and Qdrant provisioning, readiness and reset adapters.
- Bounded Docker failure output so one build cannot overwhelm job state or the platform UI.
- Separation of setup evidence from agent evidence and strict prerequisite/tool-call grading.
- Artifact hashes, sizes and idempotent ingestion identities.

## Automated gate

```text
508 passed, 11 skipped
```

Ruff and `git diff --check` also pass. Skips are explicit opt-in Docker/real-agent cases requiring
runtime flags or credentials.

## Honest remaining gaps

1. Production hosted runtime provider such as Daytona/microVMs, including quotas, egress and hard
   cleanup guarantees.
2. Explicit packaging adapter for repositories with neither Compose nor Dockerfile.
3. Queue/event and object-storage E4/E5 gates (RabbitMQ, NATS, MinIO/S3), followed by browser/code
   execution and local-model resource gates.
4. Explicit monorepo component/build-context selection and job-scoped external env injection.
5. Generic target-tool telemetry for uninstrumented agents.
6. Media adapters for Daily, direct OpenAI Realtime, generic bidirectional WebSocket and later
   SIP/AudioSocket; switching a model does not validate a different transport.
7. Production GitHub App, secret-resolution UX, object-storage reconciliation, retention, load and
   chaos testing.

## Conclusion

The environment plane is working correctly for the tested Compose and Dockerfile-only shapes, and
the complete ALK/product contract is demonstrated twice by Uber at E7. The evidence does not yet
justify claiming universal voice-agent support: hosted isolation, uncontainerized repositories,
additional state/runtime services and non-LiveKit transports remain explicit qualification gates.
