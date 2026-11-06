# ALK harness execution architecture

Implementation and test evidence are tracked in
[`IMPLEMENTATION_AND_VALIDATION_STATUS.md`](IMPLEMENTATION_AND_VALIDATION_STATUS.md). Repository
packaging behavior and the conformance matrix are documented in
[`ENVIRONMENT_CONFORMANCE.md`](ENVIRONMENT_CONFORMANCE.md).

## Scope and ownership

ALK is a common simulation-environment framework; this directory is its OSS
reference harness. The ownership table below describes the responsibilities of
that reference integration, not a requirement that production systems or users
run this executor. A different execution system can integrate through supported
contracts, but whole-executor compatibility is not established by this document.

Hosted entry points in this repository show supported implementation paths,
not evidence of which engine a production deployment uses. Platform-side
behavior described below is an integration expectation; verify it against the
consumer you actually deploy. See the [supported seam matrix](../../../../docs/simulate/environment-authoring.md#supported-extension-seams).

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

## Reference execution paths

- The local `agent-learn harness auto` command enters `cli._auto`: source
  understanding, environment/data construction, scenarios and supported execution.
- `HarnessExecutor` is a job-oriented adapter around that ALK pipeline; it
  explicitly invokes `_auto`. It is not a registry for arbitrary executors.
- Hosted interfaces include `hosted_entrypoint.py` (`alk-harness-worker`) and
  `sandbox_server.py` (`alk-harness-sandbox`), with additional authoring,
  scheduling, process-runtime and evidence contracts.
- `local_certified_runtime.py` reuses V2 process bundles and scheduler/call-runner
  components locally. That reuse does not make every local/hosted path identical.
- `fi.simulate.runtime.runner.SimulationRunner` is a separate spec/plugin runtime,
  not another name for `HarnessExecutor`.

Source ownership: [`executor.py`](executor.py), [`cli.py`](cli.py),
[`local_certified_runtime.py`](local_certified_runtime.py),
[`hosted_entrypoint.py`](hosted_entrypoint.py), and
[`SimulationRunner`](../../simulate/runtime/runner.py).

`HarnessJob` is the immutable input boundary. Local jobs use a local repository path. Hosted
jobs use a GitHub installation/repository reference, archive, image, or remote endpoint and can
never contain a local path. Agent credentials are `SecretRef` values; resolved secrets are
rejected from serialized jobs.

## Environment boundary

The environment is infrastructure owned by the harness, not a connection to customer
production. ALK may adopt schemas, migrations, fixtures and mock services from the submitted
repository. It then fills missing test data and dependencies itself.

The V1 bundle path seals an `EnvironmentBundle` with:

- versioned schema;
- SHA-256 content address;
- exact source/generator provenance;
- runtime document and service list;
- named capabilities and readiness probes;
- per-file hashes and sizes;
- no symlinks or resolved secrets;
- immutable verification before execution.

This records content and provenance for the consuming runtime; it does not
establish interchange with arbitrary execution systems. [`runtime.py`](runtime.py)
defines a `RuntimeProvider` protocol and `LocalComposeRuntimeProvider`, but CLI
build currently calls the provisioner directly. An arbitrary provider cannot be
selected through the executor simply by implementing that protocol.

[`bundle.py`](bundle.py) (V1, including Compose) and [`bundle_v2.py`](bundle_v2.py)
(V2, managed/source processes) have different schemas and loaders. The
`harness environment up --bundle` CLI consumes V1 with a source snapshot;
V2 certified runtime paths have their own consumers. A sealed bundle is evidence
of content identity, not a sandbox or a portable whole-harness contract.

## Provisioning policy

The current local provider follows this order:

1. Detect the submitted Compose definition and declared default infrastructure services.
2. Give the run a unique Compose project and free host ports.
3. Exclude opt-in agent/worker services from infrastructure startup.
4. Build and wait for declared health checks once.
5. Derive only the endpoint configuration the agent already reads.
6. Reuse a healthy build only when its complete source fingerprint matches.
7. If no Compose file exists but a Dockerfile does, generate only supported infrastructure
   declared by the contract (currently Postgres, ClickHouse and Redis) and compose it around the
   submitted runtime. Never generate agent tools or proprietary service behavior.
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

## V1 repository-backed chat execution

This section describes the Compose-oriented repository target, not every V2
process adapter. Chat and voice share the autonomous lifecycle at this level. Voice has a standard realtime rendezvous;
chat runtimes instead declare the conversational ingress they already implement in the grounded
agent contract. Today a repository runtime can expose either:

- HTTP using ALK's turn envelope; or
- HTTP using an OpenAI Chat Completions-compatible envelope; or
- a JSON turn exchange over WebSocket.

For every scenario ALK binds the restored world, starts the submitted Compose/Dockerfile/generated
runtime with the environment's private endpoint overrides, exposes the declared container port on
an ephemeral loopback port locally (or only the private project network in a hosted runner), waits
for readiness, drives the conversation, records tool effects and removes the runtime. A default
Compose API is identified by its declared ingress port and excluded from infrastructure startup;
ambiguous services fail admission rather than being guessed.

If the submitted agent returns model-facing tool calls, ALK executes them against the same world
and continues the turn with tool results. If the agent executes tools itself through the injected
environment endpoints, the world records those calls at the service boundary. Either way, setup
activity remains separate from agent evidence.

An agent with no external HTTP/WebSocket ingress is not silently reconstructed. Callable/CLI-only
repository runtimes need a sandbox-side process adapter in a later extension; until then admission
reports the missing interface explicitly. This preserves the invariant that ALK runs submitted
agent behavior rather than inventing it.

## Data and scenario quality

Data/scenario validation checks for predictable fixtures, recycled identities
and reused placeholders. The intended suite varies identities, communication
styles, locations, account states, instructions and expected paths, preserving
useful submitted data and adding synthetic records where needed. Inspect the
actual diagnostics: `world/tools.py` has lane-specific exemptions and can stop
refusing repeated schema/seed problems via `_still_refusing`. A saved world
alone is not proof of data/source conformance. The typed `WorldIR` validators and
runtime certification provide additional, distinct evidence.

The simulator is constrained by literal scenario facts, tracks facts already stated, answers the
agent's current question, detects rephrased loops, and only retries infrastructure failures.
Deterministic agent weaknesses remain deterministic failures.

## Delivery and recovery

All progress uses ALK's canonical, versioned event envelope. `EventOutbox` writes events and
fsyncs them before attempting upload. Platform delivery is batchable and idempotent by event ID;
partial acknowledgements leave the remainder pending. Event delivery can be
buffered independently of the run; this does not make source authoring,
simulation targets or model-based grading offline. Hosted interfaces also use
canonical events, subject to their own transport configuration.

The existing Future AGI result sink remains responsible for platform run rows, transcripts,
evaluations and recording upload. Platform views render stored data; they do not reconstruct or
run harness stages.

## Scaling and isolation

A hosted integration should provide job-scoped isolation and resource envelopes.
The concrete scheduler/deployment is outside the framework contract. The
following are deployment requirements to verify, not controls proved by a
local invocation or a declaration of `RuntimeProvider`:

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

- **Stage sessions:** `HarnessBackend` / `HarnessSession` and explicit in-process
  `backends.register(name, loader)`, selected by `ALK_HARNESS`. Built-ins are
  `claude` and `vertex-gemini`; this changes the stage loop, not the executor.
- **Simulation runtime:** decorators and entry points in `fi.simulate.registry`
  for environment, endpoint and simulator factories. `EnvironmentPlugin.run`
  returns a legacy report which `SimulationRunner` converts.
- **Harness targets:** `run.targets.register_target` has its own conversation and
  lifecycle protocol; it is not the simulation endpoint registry.
- **Metadata:** `fi.alk.extensions` describes catalog entries; registration there
  is not executable factory registration.
- **Source, stores and telemetry:** source acquisition, store adapters,
  `EventTransport` and result sinks are additional implementation boundaries,
  each with separate lifecycle and evidence obligations.
- **Location and interchange:** `RuntimeProvider` is not an arbitrary-provider
  CLI switch, and V1/V2 bundles are not interchangeable. No independent whole-
  executor conformance example is established here.

Prefer adapters that preserve scenario and grading semantics, but test the
actual seam rather than assuming a new scheduler/backend makes the entire
pipeline portable. Practical contracts and a tested dummy simulation plugin are
linked in the [authoring guide](../../../../docs/simulate/environment-authoring.md#supported-extension-seams).

## Agent/tool ownership boundary

ALK provisions the environment around submitted agent code; it never supplies missing agent
behavior. The submitted repository remains authoritative for prompts, tool schemas, tool
implementations, orchestration and business rules. Environment adaptation is limited to:

- starting declared infrastructure such as databases, queues, object stores and media services;
- injecting non-secret endpoints through configuration seams the submitted code already reads;
- resolving referenced credentials at runtime;
- seeding and resetting test-owned dependency state; and
- capturing calls, tool evidence and generated artifacts without changing their meaning.

A customer-specific API or missing tool implementation is not infrastructure. If its
implementation is absent, admission fails with an unsupported/missing dependency result. The
harness must not generate a substitute, proxy invented behavior, or grade against its own
replacement. A code-execution service follows the same rule: ALK may provide an isolated runtime
when the agent already declares that dependency, but the customer's tool decides what code to
execute and how its outputs are used.

## Admission, credentials and source trust

Repository admission is a read-only static preflight. It does not import or execute submitted
code. The scanner ignores dependencies, tests, generated output, symlinks and oversized files;
recognizes Python, JavaScript, env-template and Compose declarations; and emits only names,
purposes and statuses. It models provider alternatives explicitly, so one Gemini API key or one
complete Vertex credential route satisfies model authentication without asking for every option.

Public GitHub source is cloned anonymously. Private source carries a GitHub App installation
reference; the sandbox's credential broker resolves the short-lived token only for clone and
injects it through Git configuration environment variables, never process arguments or the job.
The resolved commit is verified when a commit SHA is supplied. Source fingerprinting hashes
symlink metadata without following links outside the repository, and the supervisor rejects a
source tree that changes during execution.

Non-secret connector configuration and secret references have separate contracts. Inline secret-
like configuration keys are rejected by the platform. The worker builds a fresh allowlisted
environment, resolves only the job's `SecretRef` entries, and does not inherit the supervisor's
model, cloud, repository or customer credentials.

## Retry and ingestion invariants

The supervisor retries only structured failures marked retryable in the infrastructure,
connectivity or platform-sync domains. Each failed worker attempt is archived separately before
a clean attempt starts. Agent behavior and grading outcomes are never retried to manufacture a
pass. GitHub clone uses the same bounded exponential-backoff policy.

Before terminal success, the artifact directory is sealed with a content-addressed manifest. The
seal requires a terminal result and non-empty transcript per scenario, validates referenced
recordings, rejects secret material and unsafe links, enforces the byte budget, and records every
retained file's SHA-256, media type and size. Platform result payloads and recording uploads carry
digests. Ingestion locks the call row, accepts identical retries idempotently, rejects conflicting
evidence, and stores combined, stereo, customer and assistant recordings as distinct artifacts.
