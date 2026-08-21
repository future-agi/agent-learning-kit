# ALK harness execution architecture

## Ownership

ALK owns all execution behavior. The Future AGI platform is a control and data plane only.

| Concern | ALK package | Platform | Hosted sandbox fleet |
|---|---:|---:|---:|
| Understand repository and agent | yes | no | runs ALK |
| Build databases, tools, mocks and seed data | yes | no | runs ALK |
| Generate/validate scenarios and personas | yes | no | runs ALK |
| Connect to and simulate the agent | yes | no | runs ALK |
| Grade evidence and create artifacts | yes | no | runs ALK |
| Repository/secret authorization | consumes references | yes | resolves job-scoped values |
| Job UI, chat, cancellation and history | no | yes | reports status |
| Event/result/artifact storage | emits data | yes | forwards data |

No harness stage imports Temporal, Django, platform models, or platform worker code.

## One engine, two deployments

```text
Local CLI                              Hosted product
─────────                              ──────────────
agent-learn harness auto               platform creates HarnessJob
        │                                        │
        ▼                                        ▼
HarnessExecutor                         isolated ALK sandbox
        │                               + HarnessExecutor
        └──────────── same pipeline ─────────────┘
                         │
                         ▼
 understand → environment → bundle → data → scenarios → connect → simulate → grade
```

`HarnessJob` is the immutable input boundary. Local jobs use a local repository path. Hosted
jobs use a GitHub installation/repository reference, archive, image, or remote endpoint and can
never contain a local path. Agent credentials are `SecretRef` values; resolved secrets are
rejected from serialized jobs.

## Environment boundary

The environment is infrastructure owned by the harness, not a connection to customer
production. ALK may adopt schemas, migrations, fixtures and mock services from the submitted
repository. It then fills missing test data and dependencies itself.

Every successful build is sealed as an `EnvironmentBundle`:

- versioned schema;
- SHA-256 content address;
- exact source/generator provenance;
- runtime document and service list;
- named capabilities and readiness probes;
- per-file hashes and sizes;
- no symlinks or resolved secrets;
- immutable verification before execution.

This removes repository-path assumptions from the runtime. Local Compose and a future hosted
Kubernetes/Firecracker provider implement the same `RuntimeProvider` interface and consume the
same manifest.

## Provisioning policy

The current local provider follows this order:

1. Detect the submitted Compose definition and declared default infrastructure services.
2. Give the run a unique Compose project and free host ports.
3. Exclude opt-in agent/worker services from infrastructure startup.
4. Build and wait for declared health checks once.
5. Derive only the endpoint configuration the agent already reads.
6. Reuse a healthy build only when its complete source fingerprint matches.
7. If no Compose file exists, generate the required Postgres/runtime definition from the
   contract where the repository supplies enough schema/runtime evidence.
8. Reset between scenarios from a verified snapshot or isolated lifecycle reset.
9. Remove the exact project and its test volumes during cleanup.

Unknown database engines do not silently fall back to SQLite or Postgres. A store adapter can be
generated against the engine's native driver, but it must pass generic freeze/restore/counter
drift and mutation gates before scenarios may use it.

## Evidence and grading invariants

- Setup calls are never credited to the agent.
- A missing agent call cannot satisfy a call-dependent check.
- Checks must fail against an empty or deliberately damaged world.
- Dependent checks cannot pass when their prerequisite action never happened.
- Tool refusal, agent failure, simulator failure, connectivity failure, environment failure,
  infrastructure failure and grading failure remain distinct.
- Transcripts, semantic calls, resulting state, state diffs and recordings are retained according
  to artifact policy.
- Agent behavior failures are valid RL results; harness/infrastructure failures are not scored as
  agent failures.

## Data and scenario quality

Scenario validation rejects predictable/demo fixtures such as `123456`, recycled identities,
and reused payment/booking placeholders. A suite must vary identities, communication styles,
locations, account/payment states, instructions and expected paths. Submitted seed data is
preserved where useful and expanded with synthetic records when it is too sparse to exercise the
contract.

The simulator is constrained by literal scenario facts, tracks facts already stated, answers the
agent's current question, detects rephrased loops, and only retries infrastructure failures.
Deterministic agent weaknesses remain deterministic failures.

## Delivery and recovery

All progress uses ALK's canonical, versioned event envelope. `EventOutbox` writes events and
fsyncs them before attempting upload. Platform delivery is batchable and idempotent by event ID;
partial acknowledgements leave the remainder pending. A local run therefore completes offline
and can sync later. Hosted execution uses the same protocol.

The existing Future AGI result sink remains responsible for platform run rows, transcripts,
evaluations and recording upload. Platform views render stored data; they do not reconstruct or
run harness stages.

## Scaling and isolation

One job maps to one ephemeral hosted sandbox and one resource envelope. The scheduler may place
those sandboxes on Kubernetes pods or micro-VMs, but that decision is outside ALK. Required
production controls are:

- dedicated execution cluster/account, never ordinary platform workers;
- per-job filesystem, network namespace and service identity;
- deny-by-default egress with explicit provider/GitHub/platform destinations;
- CPU, memory, disk, duration and concurrency quotas from `RuntimeRequirements`;
- short-lived repository and provider credentials;
- no privileged containers or host Docker socket inside untrusted sandboxes;
- artifact size/retention enforcement;
- cancellation, orphan reconciliation and guaranteed cleanup;
- cache only content-addressed dependency/image layers, never mutable customer workspaces.

## Extension points

- `SourceAcquirer`: GitHub, archive, image or other source materialization.
- `RuntimeProvider`: local Compose today; isolated hosted provider next.
- ALK endpoint adapters: callable/local, HTTP, WebSocket, LiveKit, Vapi and Retell today; MCP and
  process/container connectors are the next adapters and must use the same registry.
- Store registry: Postgres, SQLite and in-process today; generated native adapters for new
  engines after conformance proofs.
- `EventTransport` and result sinks: local filesystem, Future AGI platform or customer-owned
  telemetry.

Adding an environment engine, agent connector, source type or scheduler should be one adapter;
it must not add a branch to scenario generation or grading.
