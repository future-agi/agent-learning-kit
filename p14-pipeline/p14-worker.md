# p14 worker report — CallRunner wire (v3)

Status: DONE. Implementation, wiring, and tests all landed; full suite green;
6 mutations installed and killed via the production path, then cleanly
reverted (byte-identical to pre-mutation backups).

Brief: `.claude/harness-alk/callrunner-worker-brief.md` (v3, authoritative).
Read: brief-review (`reports/p14-brief-review.md`), skimmed the killed attempt1
(`reports/p14-worker-attempt1-killed.md`, scratch only — several of its
assumptions are corrected below against v3 and against code I re-verified
myself).

## Study findings that shape the design (evidence-seam question, answered)

**http_tool: NO guest-side capture surface exists anywhere in this repo.**
Verified independently, not taken on the brief's word:
- `world/handle.py::HostedWorld.call()` (the run-time World object's own
  `call` verb) raises `WorldUnavailable` unconditionally, with a docstring
  that says outright: "the `http_tool` evidence seam's wire format is not
  pinned anywhere in the contracts yet... raising unconditionally is the
  honest answer until the evidence layer pins it." This is the SAME
  conclusion world-handle-interface.md's own changelog records as an "OPEN
  DEFECT" (3.1).
- `process_runtime.py:3505-3506`'s `provision()` signature comment: "accepted
  for §4 shape-compatibility; not consumed here — evidence-seam wiring is out
  of this phase's scope."
- No `TOOLS_API_URL` wiring exists in `process_runtime.py` at all (grep: zero
  hits) — the local flow's `TOOLS_API_URL`/`ProvisionedWorld` (which forwards
  tool calls to a submitted HTTP service and records them) lives in
  `fi/alk/harness/provision.py`/`world/provisioned.py`, the LOCAL SDK lane,
  not the hosted `process_runtime.py` lane, and is not read-only-importable
  here anyway.
- `"tools-api"` appears only as an EXAMPLE bundle process name in the
  contract (a customer-authored HTTP service the customer's own `agent`
  process would call) — nothing in this codebase interposes on that traffic
  to capture calls.
- No reserved table/queue/spool name exists for `http_tool` evidence anywhere
  (the only harness-reserved table in the whole codebase is
  `_alk_conformance`, unrelated).

Per the brief's own instruction ("If NO guest-side capture surface exists for
http_tool, STOP that sub-system, write a precise CONTRACT NOTE... continue
the others — do NOT invent a capture proxy"): **http_tool derivation is
STOPPED.** `_collect_http_tool_calls` (implemented) always returns `()` and
logs nothing invented — see CONTRACT NOTE 1 below for the smallest wire.

**tool_trace: also unpinned (no producer, no reserved table anywhere), but
the brief directs implementing the read side against `runtime.endpoints`.**
One correction to the brief's literal wording: Study item 2 says read via
`runtime.endpoints["database"]` (a hardcoded slug). The established,
already-proven-correct convention in this SAME file
(`hosted_entrypoint.py::_find_postgres_endpoint`, used by
`ProcessWorldFactory.create`) is to search `runtime.endpoints.values()` for
`.protocol == "postgres"` — capability slugs are bundle-author-chosen, not a
fixed key, and `EnvironmentRuntime.endpoints` (`process_runtime.py:139-156`)
is built purely from the bundle's declared `capabilities` map keyed by
whatever slug the author picked (`build_endpoints`,
`process_runtime.py:318-339`). I follow the protocol-based lookup (same
pattern, re-implemented locally in `call_runner.py` since
`hosted_entrypoint.py`'s private helper isn't importable without a circular
import) rather than the brief's literal `["database"]` key — this is
executing the brief's intent more correctly, not deviating from it.
`_alk_tool_trace` (table name, column shape mirroring the shipped `Call`
dataclass) is this worker's own convention, isolated in ONE query function,
documented as CONTRACT NOTE 2. Missing table / connection failure / any
psycopg error → `()`, never a crash, never fabricated evidence (matches the
contract's "coverage guarantee" retry semantics exactly).

**Dispatch identity: `EnvironmentRuntime.metadata` is ALWAYS `{}` today.**
Verified: process_runtime.py's only `EnvironmentRuntime(...)` construction
site (`:3748-3752`) never passes `metadata=`; the model's field defaults to
`{}` (`Field(default_factory=dict)`). Nothing anywhere populates
`livekit_agent_name`. So with the worktree exactly as it stands, EVERY real
hosted voice job hits the "missing metadata key" pre-dial `CallAborted` path,
100% of the time, until a separate (out-of-allowlist) change to
`process_runtime.py` starts populating it. This is expected/by-design per
the brief ("still being confirmed with Azain") — I implement the consumer
side correctly (one isolated accessor, typed abort + CONTRACT NOTE) and flag
the missing producer prominently rather than silently.

**Connector value is `"livekit"`, not `"livekit_voice"`** (attempt1's
guess). Verified against `hosted-execution-seams.md:246`:
`"connector": "livekit | vapi | retell | auto"`. `CallRunnerImpl` is wired
only when `job.agent.connector == "livekit"`; anything else (including
`"vapi"`/`"retell"`/`"auto"`) keeps `NotWiredCallRunner` — out of scope for
this worker (v3's mission is the LiveKit-dispatched in-sandbox agent path
only).

**`TestCaseResult.tool_calls` is never populated by the LiveKit engine** —
confirmed by reading `engines/livekit.py`'s only two `TestCaseResult(...)`
construction sites (:538, :581): neither sets `tool_calls`, so it is always
`[]` for a voice run. This forecloses the tempting shortcut of pulling
evidence out of the `SimulationReport` itself; the seam-based collection is
the only real option, matching the contract's evidence-seam design.

**Turns**: the engine's own convention (`engines/livekit.py:2314`,
`"turn_count": str(len(messages))`) is `turns = len(messages)`. Reused
verbatim for `CallOutcome.turns`.

## Design

One new file, `src/fi/alk/harness/call_runner.py`, plus a wiring extension in
`hosted_entrypoint.py`.

### CallRunnerImpl.run(scenario, runtime)

1. **Pre-dial config/secret check** (built once at construction from
   `job.agent.config` + the captured `target_provider` secret map): all of
   `LIVEKIT_URL` (config, non-secret), plus secrets `LIVEKIT_API_KEY`,
   `LIVEKIT_API_SECRET`, `DEEPGRAM_API_KEY`, `GEMINI_API_KEY` (or
   `GOOGLE_API_KEY`) must be present. Missing → pre-dial `CallAborted`
   message `"voice_capability_unavailable: missing ..."` naming the exact
   missing aliases, no partial (dialing never starts). Checked once and
   cached (cheap), applied to every `run()` call so every scenario in the
   job gets the same typed abort, not just the first.
2. **Dispatch identity**: `runtime.metadata.get("livekit_agent_name")`
   through ONE small accessor (`_dispatch_agent_name`). Missing → pre-dial
   `CallAborted` naming the key, typed, loud, + CONTRACT NOTE 3.
3. **Scenario document re-read**: `bundle_dir/scenarios/*/scenario.json`,
   matched by `scenario_key` (the `_CompiledScenario` the scheduler hands
   over carries none of persona/instruction — confirmed against
   `scenario_source.py:170-184`). Missing/invalid → pre-dial `CallAborted`.
4. **Deterministic room name**:
   `harness-{job_id[:8]}-a{attempt_number}-{scenario_key}-s{n}` where `n` is
   a per-`scenario_key` call counter on the runner instance (pinned in a
   test).
5. **Build `SimulationSpec` in-process**, mirroring `sdk_voice.py::build_spec`
   field-for-field but sourcing values from job config / the re-read
   scenario document instead of env vars: `AgentDefinition(agent_name=<from
   step 2>, system_prompt=<scenario.instruction>, transport={"kind":
   "webrtc"})`, `LiveKitSimulatorRuntime(url=<job config>,
   room_name=<step 4>, room_mode="managed")`, simulator defaults matching
   sdk_voice.py's own (google/gemini LLM, deepgram STT+TTS — the only
   provider combo this runner validates credentials for; a job config
   requesting a different provider is out of scope, flagged in Done).
6. **`await asyncio.wait_for(place_call(spec), timeout=budget)`** —
   `place_call` is the injectable test seam; real impl is `await
   SimulationRunner().run(spec)`. `budget = call_timeout_seconds +
   connect_timeout + readiness_timeout + cleanup_timeout + 60`, mirroring
   `sdk_voice.build_spec`'s own `run_seconds` formula — deliberately larger
   than `spec.execution.timeout.run_seconds` (set to the same formula) so
   the SDK's OWN internal `asyncio.wait_for` (`runner.py:73-80`) fires first
   and produces a graceful `SimulationReport(status=TIMED_OUT)` in the
   ordinary case; this runner's outer wait_for is the last-resort bound for
   a genuinely hung SDK. `started_at` is recorded (wall clock) immediately
   before this call so ANY downstream failure — including this outer
   timeout, where `place_call` never returns — can still build a partial
   `CallOutcome` with real timing (world-handle-interface's "the receipt's
   `call` field must not be null once the call has genuinely started").
7. **Translate `SimulationReport`**: `case = report.test_cases[0]`. Upload
   the transcript (always, if `case.result.transcript` is non-empty) and any
   of the four recording paths present on disk via `adapter.upload_artifact`
   BEFORE building the returned `CallOutcome`/`CallAborted.partial` — refusal
   (artifact-level forbidden, budget exhausted) returns `None` from
   `upload_artifact` itself, never raises; this runner treats `None` as "no
   id," never as an error. `case.status != COMPLETED` → `CallAborted(partial=
   CallOutcome(calls=(), turns=len(messages), ...))`. `status == COMPLETED`
   → collect evidence per the declared seam and return the real
   `CallOutcome`.

### Evidence collection

`bundle.runtime.evidence_seam` (`EvidenceSeam.HTTP_TOOL` /
`EvidenceSeam.TOOL_TRACE`) threaded in at construction (from the `manifest`
`run_job` already loaded). Exactly one derivation runs, per scenario:
- `http_tool` → `_collect_http_tool_calls` → always `()` (STOPPED; see
  CONTRACT NOTE 1).
- `tool_trace` → `_collect_tool_trace_calls(runtime)` → finds the
  postgres-protocol endpoint (protocol match, not slug), connects with
  `psycopg` (already a harness dependency, not a new one), reads
  `_alk_tool_trace`, translates rows into `Call` (`refused = not ok`, per V1
  rule), truncates `result`/`error` at 2000 chars, `at` epoch-seconds or
  `0.0`. Any error (missing table, connection refused, malformed row) →
  `()`. Before the call starts, best-effort `DELETE FROM` the same table
  (swallowed on error) — matches world-handle-interface.md's "setup's tool
  calls are NOT evidence (the runner clears them before the call starts, as
  the local runner does)".
- An unrecognized/`None` `evidence_seam` also degrades to `()` (never
  crashes) — the scheduler's own `evidence_missing` retry-once policy is the
  correct, already-built handling for "no evidence," so degrading here
  rather than raising keeps the failure classification on the seam the
  contract already specifies.

### Failure semantics (mapped 1:1 to the brief's three cases)

- Dispatch/config never reachable pre-dial → raise `WorldUnavailable` only
  where the brief's "agent unreachable pre/at dial" case applies (dispatch
  actually attempted and the agent never joins — detected from
  `SimulationReport`/`_CaseOutcome` failure metadata, e.g.
  `agent_unavailable`/timeout-at-readiness) — everything else pre-dial
  (missing config/secrets/dispatch-metadata/scenario-doc) is a typed
  `CallAborted`, no partial, per the brief's explicit pre-dial-validation
  requirement (never `WorldUnavailable` for a config gap — that code is
  reserved by the contract for a world-level capability mismatch, not a
  job-level voice config gap).
- Mid-call drop/timeout/post-dial failure → `CallAborted(partial=<measured
  timing>)`, `__cause__` preserved, never a raw exception past `run()`.
- Agent joined, zero turns → normal `CallOutcome(calls=(), turns=0, ...)` —
  never fabricated, scheduler's own v3.5 coverage guarantee does the rest.

### Wiring (`hosted_entrypoint.py`)

- `CallRunnerContext` (new, frozen dataclass): `job`, `bundle_dir`,
  `evidence_seam: str | None`, `target_provider_secret_values: Mapping[str,
  str]`, `attempt_number: int`.
- `HostedEntrypointDeps.build_call_runner` extended:
  `Callable[[OutboundAdapter], CallRunner]` →
  `Callable[[OutboundAdapter, CallRunnerContext], CallRunner]`. Default
  factory unchanged in effect (`NotWiredCallRunner()`) but now takes the
  2nd arg (ignored) — every existing fake in `test_hosted_entrypoint.py`
  updated mechanically (5 call sites, `context` unused, exactly as
  brief-review S1 predicted).
- New module-level `peek_target_provider_secret_values(secrets_path,
  secret_purposes) -> dict[str, str]`: same non-destructive, no-unlink read
  as `peek_secret_values`, same file, filtered to `purpose ==
  "target_provider"`, alias preserved. Added as
  `HostedEntrypointDeps.peek_target_provider_secret_values(secret_purposes)`
  mirroring the existing `peek_secret_values` method.
- Call site: `secret_purposes = job_secret_purposes(job)` already runs at
  `run_job` line ~1449 — directly after that (still well before
  `pool.start()` at ~1495, which is what deletes `secrets.json`), capture
  `target_provider_secret_values = deps.peek_target_provider_secret_values
  (secret_purposes)` into a local variable threaded to the extended
  `build_call_runner(adapter, CallRunnerContext(...))` call at the existing
  wiring point (~1662) — `job`, `bundle_dir`, `manifest.runtime.evidence_seam`
  and `capabilities.attempt_number` are all already in scope there.
- `_default_build_call_runner(adapter, context)`: `NotWiredCallRunner()` when
  `context.job.agent.connector != "livekit"` (unchanged NotWired-stays-the-
  fallback behavior for every other/absent connector); otherwise a real
  `CallRunnerImpl(...)`, whose OWN pre-dial validation is what surfaces an
  incomplete-but-present `"livekit"` config as `call_failed`/infrastructure
  (one scheduler-side retry) — `capability_unavailable` stays out of reach
  (off-allowlist, scheduler-side follow-up per the contract itself), exactly
  as brief-review M2 established and v3's hard-requirements section already
  concedes.
- The runner exports `LIVEKIT_API_KEY`/`LIVEKIT_API_SECRET`/
  `DEEPGRAM_API_KEY`/`GEMINI_API_KEY` to `os.environ` ONCE, in
  `CallRunnerImpl.__init__` — WHY comment: the engine reads these via
  `os.environ.get(...)` deep inside `engines/livekit.py`/`livekit_models.py`
  (not spec fields), values are job-level (same for every scenario/attempt
  on this job), and W>1 workers each get their OWN sandboxed process
  (per-world isolation is at the process/sandbox level, not this env
  export), so one job-level export at construction is race-free.

## CONTRACT NOTES

1. **http_tool has no capture surface in the hosted lane.** What exists: the
   bundle-schema enum (`bundle_v2.EvidenceSeam.HTTP_TOOL`) and contract prose
   ("the harness-routed tools API"). What's missing: any code that sits
   between the customer's `agent` process and its `tools-api` process (or
   between the agent and its own in-process tool execution) to capture and
   expose calls to the guest's control process — `world/handle.py::call()`
   documents this exact gap as an open defect. Smallest wire that would
   close it: a harness-provided reverse-proxy process (or sidecar) the
   bundle points `TOOLS_API_URL` at instead of the real tools-api, logging
   each forwarded call to a per-world file/table the CallRunner can read
   after the call — mirroring the local lane's `ProvisionedWorld.forward`
   mechanism, but that is provisioner/bundle-authoring work, off this
   worker's allowlist. This worker's `_collect_http_tool_calls` always
   returns `()`; a job whose bundle declares `evidence_seam: http_tool`
   today gets `evidence_missing` → retry → `errored` for every scenario,
   which is the honest, contract-correct behavior for "the seam has no
   producer yet" (never a crash, never fabricated evidence).
2. **tool_trace has no pinned schema either.** `_alk_tool_trace` (table
   name) and its column shape (mirroring the `Call` dataclass) are this
   worker's own convention, isolated in one query function
   (`_collect_tool_trace_calls`) and one clear function
   (`_clear_tool_trace_calls`) — a future producer that disagrees on the
   name/shape needs to change only those two functions. Until a producer
   exists, every real tool_trace job also reads zero rows and gets the same
   `evidence_missing` retry-then-errored outcome as http_tool, correctly.
3. **`runtime.metadata["livekit_agent_name"]` has no producer today** —
   `process_runtime.py`'s only `EnvironmentRuntime` construction site never
   sets `metadata`. Every real "livekit" job hits the pre-dial
   `CallAborted` for a missing dispatch identity until a separate,
   out-of-allowlist change starts populating this key. The accessor
   (`_dispatch_agent_name`) is the single point that needs no other change
   when that lands.
4. **`capability_unavailable` stays unreachable from this worker** (brief-
   review M2, unchanged by v3): the code is not in `hosted_scheduler.
   _CODE_DOMAIN`, producing it needs a scheduler edit (off-allowlist), and
   the contract itself calls it "a follow-up, not shipped with this text."
   An incomplete-but-configured `"livekit"` job surfaces as
   `call_failed`/infrastructure with the scheduler's one automatic retry;
   the pre-dial message still carries the real `voice_capability_unavailable:
   ...` diagnosis in `failure.message` for anyone reading logs/receipts.
5. **Only one provider combination is supported/validated**: LLM=google
   (Gemini), STT=deepgram, TTS=deepgram — matching `sdk_voice.py`'s own
   shipped defaults exactly. A job whose `agent.config` requests a different
   provider trio is not rejected outright (the value is honored if given)
   but this runner's pre-dial credential check only validates the aliases
   this combination needs; a mismatched provider/credential pair surfaces as
   a post-dial engine failure (`CallAborted`) rather than a pre-dial one.
   Widening this is straightforward but out of scope for a first wire.

---

## Implementation

Two files edited (of the four allowed), two new — exactly the brief's allowlist, nothing else
touched:

- NEW `src/fi/alk/harness/call_runner.py` (~600 lines) — `CallRunnerImpl`, `CallRunnerContext`,
  the `place_call`/`ArtifactUploader` seams, pre-dial validation, scenario-document re-read,
  deterministic room naming, `SimulationSpec` construction, both evidence derivations, and the
  three failure-semantics mappings.
- `src/fi/alk/harness/hosted_entrypoint.py` — `peek_target_provider_secret_values` (module
  function + `HostedEntrypointDeps` method), `_default_build_call_runner`, the extended
  `build_call_runner: Callable[[OutboundAdapter, CallRunnerContext], CallRunner]` field, the
  `target_provider_secret_values` capture right after `secret_purposes = job_secret_purposes(job)`
  (still well before `pool.start()`), and `CallRunnerContext` construction at the existing wiring
  point (`call_runner = deps.build_call_runner(adapter, call_runner_context)`).
- NEW `tests/harness/test_call_runner.py` (30 tests) — `CallRunnerImpl`'s own boundary
  (`place_call`) exclusively; no monkeypatching of LiveKit internals, no live calls.
- `tests/harness/test_hosted_entrypoint.py` (+7 tests, 5 mechanical call-site fixes for the
  extended `build_call_runner` signature) — the wiring itself: secret capture timing, context
  threading through a real (fake-provider) `run_job`, and the connector gate.

### Verifying the worktree's code is what actually ran

Per the task brief's own warning, a plain `python -c "import fi.alk.harness.hosted_entrypoint as
m"` from inside `/tmp/alk-callrunner` resolves to the ORIGINAL repo
(`/Users/khushalsonawat/Desktop/agent-learning-kit/src/...`), not the worktree — the venv's
editable install points at the original repo path and wins the namespace-package resolution
order. Verified this concretely (ran the check, got the original-repo path back) before writing
a single test. Fix: `PYTHONPATH=/tmp/alk-callrunner/src` prepended forces the worktree's code to
resolve first; re-verified after the fix:

```
$ cd /tmp/alk-callrunner && PYTHONPATH=/tmp/alk-callrunner/src <venv>/bin/python -c \
    "import fi.alk.harness.hosted_entrypoint as m; print(m.__file__)"
/tmp/alk-callrunner/src/fi/alk/harness/hosted_entrypoint.py
```

Every test run in this report used `PYTHONPATH=/tmp/alk-callrunner/src` for exactly this reason.
Without it, the entire test suite would silently exercise the ORIGINAL repo's `hosted_entrypoint.py`
(which does not have this worker's changes) and every result in this report would be meaningless.

## Test results

- `tests/harness/test_call_runner.py`: 30/30 passed.
- `tests/harness/test_hosted_entrypoint.py`: 70/70 passed (63 pre-existing + 7 new).
- Full `tests/harness/`: **860/860 passed**, 0 failed, 0 skipped (823 baseline + 37 new).
- `ruff check` on all four touched files: clean.

## Mutation table (6 installed via the production path, each killed by its named test, each
cleanly reverted — verified byte-identical to a pre-mutation backup afterward)

| # | Mutation (file:what) | Killed by | Result |
|---|---|---|---|
| 1 | `call_runner.py::_room_name` — dropped the `-a{attempt_number}` component | `test_room_name_matches_the_brief_pinned_format`, `test_room_name_uses_only_the_first_eight_chars_of_job_id` | 2 failed |
| 2 | `call_runner.py::_collect_tool_trace_calls` — `refused=ok` instead of `refused=not ok` (V1 rule) | `test_tool_trace_translates_rows_and_applies_v1_refused_rule` | 1 failed |
| 3 | `call_runner.py::_translate_report` — `AGENT_UNAVAILABLE` raised `CallAborted` instead of `WorldUnavailable` | `test_agent_unavailable_status_raises_world_unavailable` | 1 failed (mutant exception escaped uncaught, proving the test's narrow `except WorldUnavailable` genuinely discriminates) |
| 4 | `call_runner.py::_check_config` — always returned `None` (pre-dial validation bypassed) | `test_missing_target_provider_secrets_aborts_pre_dial_without_calling_place_call`, `test_missing_llm_credential_names_the_either_or_pair`, `test_missing_livekit_url_config_aborts_pre_dial` | 3 failed |
| 5 | `call_runner.py::_translate_report` — `turns = 0` instead of `len(case.result.messages)` | `test_completed_call_uploads_transcript_and_returns_populated_outcome` | 1 failed |
| 6 | `hosted_entrypoint.py::_default_build_call_runner` — connector-gate `==` instead of `!=` (inverted) | `test_default_build_call_runner_returns_notwired_for_a_non_livekit_connector`, `test_default_build_call_runner_returns_notwired_for_retell_and_auto_too`, `test_default_build_call_runner_returns_a_real_call_runner_impl_for_livekit` | 3 failed |

All six reverted; `diff` against pre-mutation backups of both files came back empty (byte-identical)
before the final full-suite run, which passed 860/860.

## Done = (per the brief)

**Plain-language design, wiring explanation, evidence-seam findings**: see "Study findings" and
"Design" above — written before implementation, unchanged by it except where the mutation table
and test results above are now the promised evidence.

**The exact list of voice credential aliases + config keys a job must carry** (Khushal configures
the UI upload from this):

Secrets (`agent.secret_refs`, `purpose: "target_provider"`, alias = the exact key below):
- `LIVEKIT_API_KEY`
- `LIVEKIT_API_SECRET`
- `DEEPGRAM_API_KEY`
- `GEMINI_API_KEY` (or `GOOGLE_API_KEY` — either one satisfies the LLM leg; this runner does not
  currently support the Vertex/ADC credential path, only the direct-API-key path)

Non-secret config (`agent.config`):
- `livekit_url` — the LiveKit WebSocket URL (`wss://...`)
- `voice_call_timeout_seconds` (optional, defaults to `300`) — the per-call speech budget; the
  runner adds its own fixed connect/readiness/cleanup overhead (210s) plus a safety pad on top of
  whatever this is set to, so it does not need to include that overhead itself

Also required, but NOT part of the UI's credential/config upload — the guest-side wiring gap this
report's CONTRACT NOTE 3 documents: `agent.connector` must be exactly `"livekit"`, and (separately,
out of this worker's allowlist) `EnvironmentRuntime.metadata["livekit_agent_name"]` must be
populated by `process_runtime.py`'s provisioner — it is NOT today.

**Contract notes**: five, numbered, referenced throughout `call_runner.py`'s own docstrings —
reproduced in full under "CONTRACT NOTES" above (http_tool: no capture surface exists anywhere,
STOPPED per the brief; tool_trace: unpinned schema, isolated convention; dispatch-identity
metadata: no producer exists today; `capability_unavailable`: unreachable from this seam,
off-allowlist; provider combination: only google/deepgram/deepgram validated).

## Deviations from the brief's literal wording (both reasoned, both verified against code)

1. **Evidence-seam study pointer #2** (`runtime.endpoints["database"]`) → implemented as a
   protocol-based lookup (`endpoint.protocol == "postgres"`) instead, matching
   `hosted_entrypoint.py::_find_postgres_endpoint`'s own established, already-correct convention.
   Capability slugs are bundle-author-chosen (`build_endpoints`, process_runtime.py:318-339); a
   hardcoded `"database"` key would silently misbehave for any bundle naming its slug differently.
   Verified this is a strictly more-correct execution of the same intent, not a contradiction of
   it — a direct test (`test_find_postgres_endpoint_matches_by_protocol_not_a_fixed_slug_name`)
   pins the behavior against a differently-named slug.
2. **Connector value**: used `"livekit"` (verified against `hosted-execution-seams.md:246`:
   `"connector": "livekit | vapi | retell | auto"`), not `"livekit_voice"` (the killed attempt1's
   unverified guess).

## Scope not covered (explicitly, not silently)

- `http_tool` evidence collection produces zero calls today — CONTRACT NOTE 1. This is the
  correct, contract-honest behavior for "no producer exists yet," not a partial implementation.
- Only the google(Gemini)/deepgram/deepgram provider trio is credential-validated pre-dial — a job
  configuring a different provider combination is not rejected outright but surfaces any
  credential mismatch as a post-dial `CallAborted` instead of a pre-dial one (CONTRACT NOTE 5).
- `capability_unavailable` (world-handle v3.5) is not reachable from this worker's code at all —
  by design, matching brief-review M2 and the contract's own "not shipped with this text."

## Discipline confirmation

No git commands were run at any point. No installs. No network calls to LiveKit/Deepgram/anyone —
every test uses an injected `place_call` fake or a monkeypatched `psycopg`, never a real
connection. No writes outside the four allowed files + this report + `inflight.md` heartbeats
(scratchpad backups of the two edited files, used only to verify a clean mutation-testing revert,
live under this session's own scratchpad directory, not the repo). Worktree HEAD state: only the
four allowed files differ from the pre-existing content at `89f1ce2`; verified via `diff` against
pre-edit backups for the mutation-testing round-trip (not via any git command).
