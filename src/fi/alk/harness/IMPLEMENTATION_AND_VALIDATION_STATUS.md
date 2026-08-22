# Harness implementation and validation status

Status date: 22 August 2026

This document records what is implemented and what has actually been exercised. It complements
`ARCHITECTURE.md` (the target design) and `ENVIRONMENT_CONFORMANCE.md` (the packaging contract).

## Product boundary now implemented

ALK owns the execution plane: repository understanding, environment construction, test-data
setup, scenario execution, evidence collection, grading and artifact production. The platform is
the control and data plane: it creates jobs, supplies job-scoped secret references, receives
events/results and presents history. Local CLI execution and hosted execution use the same
`HarnessJob`, `EnvironmentBundle`, executor and result contracts.

The central invariant is enforced throughout provisioning: ALK provides the environment in which
the submitted agent and its existing tools run. It does not rewrite tools, invent proprietary
service behavior or silently replace an unsupported dependency.

## Completed implementation

### Portable jobs, bundles and runtimes

- Immutable local and hosted job contracts, with local paths rejected for hosted jobs.
- Content-addressed environment bundles with provenance, per-file hashes and sizes.
- Bundle verification before execution; symlinks, path traversal and changed content are rejected.
- A runtime-provider boundary so local Docker/Compose can later be replaced by a hosted sandbox
  provider without changing the harness pipeline.
- Structured lifecycle events, cancellation and terminal result delivery for platform-driven jobs.
- Retry classification that separates transient infrastructure/transport failures from agent
  failures and avoids rerunning deterministic agent outcomes.

### Repository and environment provisioning

- **Compose repository:** adopt the submitted Compose model, isolate project names, ports, networks
  and volumes, start dependency services, inject test endpoints and clean up only that run.
- **Dockerfile-only repository:** keep the submitted runtime unchanged and generate only the
  declared infrastructure around it. Managed adapters currently cover Postgres, ClickHouse and
  Redis, including SQL initialization, readiness and reset behavior.
- **Neither Compose nor Dockerfile:** fail admission with an actionable packaging error when an
  external runtime is required. Automatic packaging for this third case remains open work.
- Source fingerprints prevent reuse of stale builds.
- Protocol-aware readiness avoids treating a merely open socket as a ready database.

### Credentials, GitHub input and isolation

- Static credential discovery from source, Compose, Dockerfile and known SDK connectors.
- Preflight reports missing credential names before a sandbox is started.
- Jobs and bundles contain `SecretRef` references, not resolved values; secret values are rejected
  from persisted payloads and redacted from emitted output.
- GitHub repository/revision references are represented in the hosted job contract. The platform
  remains responsible for repository authorization and job-scoped credential resolution.
- Unsafe Compose features such as privileged execution and host escape configuration are rejected.
- Resource/lifecycle ownership is scoped to the run so cleanup cannot target another environment.

### Evidence and artifact integrity

- Setup activity is kept separate from agent activity and cannot earn evaluation credit.
- A missing tool call cannot satisfy a tool-dependent check; dependent checks cannot pass when the
  prerequisite action is absent.
- Results distinguish environment, connectivity, simulator, agent, grading and artifact failures.
- Artifact manifests include hashes and sizes, are verified on ingestion and are delivered with
  an idempotency identity for safe platform retries.
- LiveKit recording selection now ignores ambient/background publications and selects the actual
  conversational speech track.

## Environment conformance tested

The checked-in fixtures are small conformance repositories, not product demo agents:

| Packaging case | Dependencies | What was proven |
|---|---|---|
| Compose | ClickHouse + Redis | Two copies can run simultaneously despite fixed submitted ports; each worker reaches its own seeded services; resetting one does not affect the other. |
| Dockerfile only | ClickHouse + Redis | ALK composes managed dependencies around the unchanged image, injects endpoints and verifies seeded data from the running agent. |
| Dockerfile only | Postgres | ALK creates the database, applies submitted SQL, verifies application readiness and performs isolated cleanup. |

The real Docker conformance matrix exercised build, startup, readiness, state mutation, reset and
teardown. The detailed contract and commands are in `ENVIRONMENT_CONFORMANCE.md`.

## Real voice-agent validation

Three agents from the official `livekit/agents` examples repository were validated at commit
`da6af86ac640a3bc54585764e64321d7048c1c16`:

- `drive_thru` — multi-tool ordering with in-process state;
- `frontdesk` — scheduling with an optional external Cal.com connector and source fallback; and
- `hotel_receptionist` — a larger booking flow with local SQLite state.

For all three, ALK detected the Dockerfile-only runtime, built the upstream image, produced and
verified a sealed bundle, started the worker, checked readiness and source integrity, and cleaned
up the execution environment.

Five real WebRTC calls were then made to each agent (15 total):

| Agent | Calls | Completed | Environment failures | Preserved agent failures |
|---|---:|---:|---:|---:|
| Drive-through | 5 | 5 | 0 | 0 |
| Front desk | 5 | 5 | 0 | 0 |
| Hotel receptionist | 5 | 4 | 0 | 1 |
| **Total** | **15** | **14** | **0** | **1** |

The hotel failure is a useful deterministic finding rather than a harness failure: the agent
repeatedly treated a valid 16-digit card number as 15 digits, entered a validation loop and reached
the seven-minute scenario limit.

Artifact checks passed for every call:

- 15/15 reports and transcripts were created;
- 15/15 combined recordings and 15/15 stereo recordings were present and larger than 100 KB;
- all recordings contained measurable audio; and
- all campaign containers were stopped after execution.

The public example repositories, temporary model-adapted checkouts, recordings and campaign
artifacts are intentionally not committed to ALK.

### Test-only compatibility note

The current LiveKit server returned HTTP 401 for the example agents' LiveKit Inference models. To
exercise real calls, temporary external checkouts used direct Deepgram and OpenAI model providers
for STT/LLM/TTS. This was a test setup change only: no agent tools, prompts, database behavior or
harness source was rewritten. The temporary checkouts are not part of this branch.

## Automated release gate

The harness-focused gate completed with:

```text
456 passed, 22 skipped
```

The skipped cases are opt-in Docker/real-agent checks and require their documented runtime flags
and credentials. The WebRTC engine regression suite is included in the 456 passing tests. Ruff and
`git diff --check` also pass for this change set.

## Findings that remain open

These items should not be represented as complete:

1. Implement the production hosted provider (for example Daytona or a microVM fleet) behind the
   existing runtime interface, with quotas, egress policy and hard cleanup guarantees.
2. Add an explicit packaging adapter for repositories with neither Compose nor a Dockerfile.
3. Expand managed dependency conformance beyond Postgres, ClickHouse and Redis based on real voice
   agent repositories; unsupported services must continue to fail explicitly.
4. Add generic target-tool event ingestion for uninstrumented third-party LiveKit agents. Audio and
   transcripts work today, but source-owned tool calls cannot always be normalized automatically.
5. Productize GitHub authorization (GitHub App), secret storage/resolution and credential UX on the
   platform. The ALK contracts and preflight are present; the production integrations are not.
6. Improve platform run UX with granular live stages, logs, actionable waiting states and artifact
   reconciliation rather than a long generic status.
7. Continue work on diverse scenario/persona generation and controllable simulator behavior.
8. Add production object-storage upload/reconciliation, retention policy and load/chaos testing.
9. Track LiveKit transport instability observed as intermittent signal-resume 502/EOF responses.

## Current conclusion

The local execution architecture and the environment creation path for well-packaged and
Dockerfile-only voice agents are functioning end to end. Real agents could be built, started and
called through the generated environment, and complete transcripts and audio artifacts were
produced. The remaining work is primarily the production hosted sandbox/provider integration,
broader packaging/service coverage, third-party tool telemetry and platform product experience.
