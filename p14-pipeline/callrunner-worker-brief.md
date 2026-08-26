# CallRunner wire — worker brief (p14, v3 — post brief-review + Azain's topology)

**Decision of record (Khushal): REAL VOICE.** v3 supersedes v2 after Azain's
message (his side of the pipeline is DONE: v2 bundle authoring, in-sandbox
process provisioning, scenario loading, scheduler, receipts, snapshot v7 —
this runner is the LAST gap to end-to-end in Daytona). Where v3, the
brief-review (reports/p14-brief-review.md), or a prior partial attempt
disagree, V3 WINS. reports/p14-worker-attempt1-killed.md is scratch from a
killed attempt — you may skim for ideas; it carries NO authority.

## Topology (corrected — this changes who you call)
The customer agent runs **inside the Daytona sandbox as a world process**: the
bundle declares it, the provisioner (process_runtime) spawns it per world with
`LIVEKIT_AGENT_NAME=agent-w{WORLD_INDEX}`-style identity, and it registers
itself with LiveKit cloud. Your caller dials THAT registered identity via
LiveKit dispatch. You never start or manage the agent process.

**Dispatch identity**: read `runtime.metadata["livekit_agent_name"]` from the
`EnvironmentRuntime` you receive. If the key is absent → pre-dial
`CallAborted` naming the missing metadata key (typed, loud) + CONTRACT NOTE.
Isolate the lookup in ONE small accessor so a change of key/convention (still
being confirmed with Azain) is a one-line adapt. Never guess a name.

## Mission — three sub-systems
1. **Place the call**: drive `SimulationRunner` IN-PROCESS with a directly
   built `SimulationSpec` (template: `run/sdk_voice.py::build_spec`). Do NOT
   exec the `run/call.py` subprocess path; do NOT touch `run/live.py`
   (webhook/cloudflared — local-only). Caller persona/instruction come from
   the on-disk scenario document (see Study 4).
2. **Collect evidence** per world-handle-interface.md:198-232: the bundle
   declares `runtime.evidence_seam: "http_tool" | "tool_trace"` — exactly one
   active. Implement BOTH derivations per the contract's table:
   - `http_tool` (PRIMARY — Azain's v2 bundles declare it): captured at the
     tools-api boundary; refusal response → `refused=True, ok=True`; crash →
     `ok=False` + error.
   - `tool_trace` V1: read the trace via `runtime.endpoints["database"]`;
     `refused` mirrors `not ok`.
   Entries are the shipped `Call` dataclass exactly (world/runtime.py:140);
   result/error truncated at 2000 chars; `at` epoch seconds or 0.0, never
   fabricated; one entry per attempt, evidence order.
   **Study FIRST where http_tool captures physically land in the hosted lane**
   (process_runtime's capture surface / the spooled `calls.json` shape that
   `folder.py::_RUNNABLE` loads; bundle_v2's evidence declarations). If NO
   guest-side capture surface exists for http_tool, STOP that sub-system,
   write a precise CONTRACT NOTE (what exists, what's missing, smallest
   wire), and continue the others — do NOT invent a capture proxy.
3. **Upload artifacts**: transcript (and recordings if produced) through
   `adapter.upload_artifact` (hosted_entrypoint.py:906-916) BEFORE returning;
   returned `sha256:<id>` ids into the CallOutcome fields. Artifact-level
   refusals (`_ARTIFACT_LEVEL_FORBIDDEN_KINDS`, 924-933) → null, not crash.

## Failure semantics (three distinct cases — pin each in a test)
- **Agent unreachable pre/at dial** (dispatch fails, agent never joins): raise
  `WorldUnavailable` — the agent is part of the world; the scheduler retires
  the world and retries. (Azain's explicit semantics.)
- **Mid-call drop / timeout / post-dial machinery failure**: `CallAborted
  (partial=<measured timing>)` — never let a raw exception escape post-dial
  (a generic exception loses timing: scheduler sets call=None).
- **Agent joined but silent (zero conversational turns)**: return a NORMAL
  CallOutcome with the session's real turn count and `calls=()` — the
  scheduler's v3.5 coverage guarantee turns that into `evidence_missing`
  correctly. The simulator session owns the turn count; report it faithfully;
  never fabricate calls or turns.

## Worktree `/tmp/alk-callrunner` (feat/hosted-execution-guest @ 89f1ce2, freshly reset)
Files you may edit: NEW `src/fi/alk/harness/call_runner.py`,
`src/fi/alk/harness/hosted_entrypoint.py` (config detection, secrets capture,
deps-seam extension, construction), NEW `tests/harness/test_call_runner.py`,
`tests/harness/test_hosted_entrypoint.py`. NOTHING else. Read-only imports:
`run/call.py`, `run/sdk_voice.py`, `run/simulation.py`, `run/models.py`,
`fi/simulate/simulation/engines/livekit.py`,
`fi/simulate/simulation/voice_prompt.py`, `fi/simulate/runtime/runner.py`,
`process_runtime.py`, `bundle_v2.py`, `world/runtime.py`.

## Study (verified pointers)
1. `hosted_scheduler.py:210-211` (protocol), `:1661-1678` (_execute exception
   handling: WorldUnavailable → world_unavailable retry+mark_unhealthy;
   CallAborted → call_failed with partial; other → call_failed, call=None),
   `:200-208` (partial rule), `:191-197` (CallOutcome), `:1683-1689`
   (empty calls policy).
2. `hosted_entrypoint.py:375-392` (NotWiredCallRunner), `:1146-1191`
   (HostedEntrypointDeps), `:1662-1666` (wiring point), `:1598-1600` (swap
   pattern), `:140-154`+`:1295` (peek_secret_values), `:797-830` (receipt
   serialization — allowlist sufficient, no scheduler edits).
3. Karthik's pipeline as TEMPLATE: `sdk_voice.py::build_spec`,
   `VOICE_MAX_SECONDS` (:137), dispatch-by-agent-name
   (`engines/livekit.py:918-927`), creds via AMBIENT env
   (`engines/livekit.py:644-645`, `livekit_models.py` — raises on missing;
   Deepgram default TTS; no background-noise var exists on this branch).
4. Scenario document: `_CompiledScenario` has NO persona/instruction
   (scenario_source.py:172-184); re-read `scenarios/<folder>/scenario.json`
   from `bundle_dir` matched by `scenario_key` (shape: folder.py:138-150,
   scenario.py:152-210). Missing doc/fields → pre-dial CallAborted.
5. Evidence plumbing: world-handle:198-232 (quoted above); process_runtime's
   process/capture surfaces; bundle_v2 evidence/process declarations; the
   spooled calls.json convention (folder.py::_RUNNABLE).
6. Contracts: outbound v1.4:267-270 (call shape), :303-305 (artifacts acked
   before receipt). **§2f does NOT apply to you**; receipt codes come from
   the scheduler; you map nothing.

## Secrets (timing trap — follow exactly)
`ProcessRuntimeProvider` DELETES `/run/futureagi/secrets.json` on first
`provision()` (process_runtime.py:3535-3544). In run_job, BEFORE
`pool.start()` (where peek_secret_values already runs, :1295), capture the
alias→value map for `purpose: target_provider` (extend/replace
peek_secret_values keeping its `extra_secret_values` redaction registration).
Thread through an EXTENDED `build_call_runner` seam into the constructor. The
runner exports needed variables to `os.environ` ONCE at construction (the
engine reads ambient env — sanctioned; job-level values, no W>1 race; WHY
comment). Never log values; never re-read files.

## Hard requirements (unchanged from v2 unless noted)
- Protocol satisfied exactly; the three failure semantics above.
- Pre-dial validation: missing config/secrets → pre-dial `CallAborted`
  message starting `voice_capability_unavailable:` naming missing ALIASES
  (accepted: call_failed/infrastructure + one retry; CONTRACT NOTE that
  scheduler-side capability_unavailable stays a deferred v3.5 follow-up).
- Absent voice config entirely at wiring → NotWiredCallRunner stays (by
  design; do not improve).
- Budget: runner-owned `asyncio.wait_for`; `call_timeout_seconds` from
  `job.agent.config["voice_call_timeout_seconds"]` else 300; timeout → clean
  room shutdown → CallAborted(partial=…).
- W>1: no shared mutable state (one-time env export excepted); one room per
  call; deterministic room name
  `harness-{job_id_prefix8}-a{attempt}-{scenario_key}-s{scenario_attempt}`
  (pin in a test).
- Cleanup on EVERY exit path (rooms/sessions/dispatch), proven by test.
- No new dependencies; `[livekit]` extra only. Comments WHY-only.

## Test seam (named, mandatory)
The runner's own boundary: injectable async `place_call(spec) -> engine
outcome`. Real impl builds SimulationSpec + awaits `SimulationRunner().run`;
tests inject fakes (return/raise scripted outcomes). NO live calls; NO
monkeypatching LiveKit internals. Evidence derivations tested against real
store fixtures where the suite has them, else seam-level fakes of the same
shape.

## Discipline (unchanged)
No git commands; no installs; no writes outside the four files + report +
heartbeats. Heartbeats q15min (real `date -u`) →
`.claude/harness-alk/inflight.md`. Report →
`.claude/harness-alk/reports/p14-worker.md` (fresh file). Tests once at the
end; ≥3 mutations installed via the production path, each with its named
killing test. Disclose deviations. Contract ambiguity → CONTRACT NOTE +
strictest reversible reading. Scope extensions mid-run refused.

## Done =
Report: plain-language design; wiring explanation; the evidence-seam findings
(what exists for http_tool in the hosted lane); mutation table; full
`tests/harness/` green; contract notes; and the EXACT list of voice
credential aliases + config keys a job must carry — Khushal configures the UI
upload from it.
