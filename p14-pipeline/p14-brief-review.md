# p14 brief review — CallRunner worker brief (cold read)

Reviewed: `.claude/harness-alk/callrunner-worker-brief.md`
Against: worktree `/tmp/alk-callrunner` (feat/hosted-execution-guest @ 89f1ce2, verified `git log`) and `api_contracts/harness/` (world-handle v3.5, hosted-execution-seams v1.15, outbound-channels v1.4). Read-only; no writes outside this report + heartbeats.

---

## MUST-FIX

### M1 — The brief never mentions `CallOutcome.calls`, and without it every scenario errors
The scheduler protocol the worker must satisfy returns evidence, not just a transcript:

- `hosted_scheduler.py:191-197` — `CallOutcome.calls: tuple[Call, ...]` (the `world/runtime.py:140` `Call` dataclass: one WORLD tool call).
- `hosted_scheduler.py:1683-1689` — `_execute`: `if not calls:` → `evidence_missing` → `_Retry`; second occurrence is a final errored receipt. So a CallRunner that places the call perfectly and returns `calls=()` makes **every scenario in every job errored**.
- There is NO evidence collector anywhere in the hosted lane: `evidence_seam` exists only as a bundle-schema enum (`bundle_v2.py:62-79`, `"http_tool" | "tool_trace"`); grep for `tool_trace`/`evidence_seam` finds no reader. The local flow's calls come from a webhook world reached over a cloudflared tunnel (`run/live.py:6-10, 69-101`, `world.calls` at 138-168) — machinery that does not exist and cannot exist in the hosted guest.
- The contract defines the derivation (world-handle-interface.md:198-232: calls come from the bundle-declared evidence seam; `tool_trace` V1 rule at 224-227) but the brief's mission, study list, hard requirements, and Done-section never say the word "calls" or "evidence".

**Fix:** the brief must explicitly scope evidence collection (e.g. "read the agent's tool_trace via `runtime.endpoints['database']` after the call; the bundle's `runtime.evidence_seam` declares the source") or explicitly descope it with an accepted consequence. As written, the worker discovers mid-build that the largest unbuilt sub-system was never commissioned.

### M2 — `capability_unavailable` cannot be produced within the allowlist; the contract says so itself
Hard requirement 2 ("Missing/incomplete voice credentials → typed `capability_unavailable`-family failure") and study item 6 ("world-handle v3.5 capability_unavailable row — missing voice config at runtime maps HERE, typed") are not implementable:

- The scheduler classifies EVERY exception out of `CallRunner.run` itself: `WorldUnavailable` → `world_unavailable` (retry + **mark_unhealthy** — retires a healthy world), `CallAborted` → `call_failed`, anything else → `call_failed` (`hosted_scheduler.py:1661-1678`). `capability_unavailable` is not in `_CODE_DOMAIN` (`hosted_scheduler.py:298-313`); `_failure()` at 408-414 would KeyError on it. Grep: the string appears nowhere in `/tmp/alk-callrunner/src` or `/tests`.
- The contract itself: "Scheduler-side implementation of `capability_unavailable` is a follow-up, **not shipped with this text**" (world-handle-interface.md:14-15). And the row's defined trigger is a WORLD-capability mismatch — "`WorldUnavailable` where the capability was never provisioned for ANY world (empty `public` schema, `call` under `tool_trace`)" (line 95) — not voice credentials.
- Producing that receipt code requires editing `hosted_scheduler.py` — **off-allowlist**. This is exactly the "allowlist forces a rule violation" bug.

**Fix:** the brief must choose and state the real behavior: (a) config-present-but-incomplete detected at WIRING time in run_job (config detection is in-allowlist) with a stated job-level outcome, (b) pre-dial `CallAborted` whose message carries the capability-unavailable diagnosis, accepted as `call_failed`/infrastructure with one wasted retry, plus a CONTRACT NOTE, or (c) widen the allowlist to land the v3.5 follow-up in the scheduler. Note the current requirement also collides with the contract row's own "do NOT retry" semantics — `call_failed` IS retried once (`_CODE_DOMAIN` infrastructure + `_is_retryable`, hosted_scheduler.py:390-393).

### M3 — "bounded by the scenario/job budget the scheduler already passes" is factually false
The scheduler passes NO budget. The protocol is `async def run(self, scenario: Scenario, runtime: EnvironmentRuntime) -> CallOutcome` (`hosted_scheduler.py:210-211`) and `_execute` invokes it with no `asyncio.wait_for` (line 1662) — the `SETUP/READY/CHECK_TIMEOUT_SECONDS` constants (381-383) govern only the three phases, never the call. The `Scenario` protocol (176-184) and `_CompiledScenario` (`scenario_source.py:172-184`) carry no budget; `EnvironmentRuntime` carries endpoints, not budgets. The only budget-shaped inputs are `job.runtime.max_duration_seconds` (job-level TTL) and sdk_voice's own `VOICE_MAX_SECONDS` default (sdk_voice.py:137). **Fix:** name the budget source and value the worker must enforce; as written the worker will hunt for a parameter that does not exist.

### M4 — Secrets timing: the file is gone before the wiring point the brief names
- `ProcessRuntimeProvider` loads and **deletes** `/run/futureagi/secrets.json` on its FIRST `provision()` call (`process_runtime.py:3535-3544`; `_load_and_delete_secrets` at 3347, unlink at 3374; values held in the provider's private `_secret_values` and injected only into spawned target processes scoped to `purpose: target_provider`, 435-457).
- `deps.build_call_runner(adapter)` runs at `hosted_entrypoint.py:1662` — AFTER `pool.start()` at 1495. A worker who reads secrets at construction time reads a deleted file.
- The existing early read, `peek_secret_values` (hosted_entrypoint.py:140-154, called at 1295), returns **values only, no aliases** — useless for picking LIVEKIT_API_KEY out of the map. And the `build_call_runner` seam signature takes only the adapter (1160-1162), so job config / bundle_dir / secrets all require extending the seam (in-allowlist, but a design decision, not "wiring only").
- Compounding: Karthik's engine consumes creds from **ambient env** — `os.environ.get(runtime.api_key_env)` (`engines/livekit.py:644-645`), `_required_env("DEEPGRAM_API_KEY")` etc. (`livekit_models.py`). The brief's "NEVER read them from ambient env" therefore needs an explicit reconciliation (runner receives values from the wiring and exports them to the process env for the engine — or the rule is unimplementable with reuse-by-import).

**Fix:** state that the alias→value map must be captured in run_job BEFORE `pool.start()` (same place `peek_secret_values` already runs), threaded into construction, and that exporting to `os.environ` for the engine's own reads is the sanctioned mechanism (values are already in the redaction set via `extra_secret_values`).

### M5 — The artifact-upload obligation is missing entirely
- outbound-channels.md:303-305: "referenced artifacts uploaded and acked BEFORE the receipt (`422 artifact_unknown`)"; `transcript_artifact` is `"sha256:<id> | null"` (line 270).
- The adapter defensively NULLS any un-acked transcript/recording id on the receipt (`hosted_entrypoint.py:797-830`) — so a runner that returns a path or an un-uploaded digest silently loses the transcript the whole feature exists to capture.
- The deps comment says it outright: "The real call runner needs `OutboundAdapter.upload_artifact` to satisfy the invariant that referenced artifacts are uploaded+acked BEFORE the receipt" (`hosted_entrypoint.py:1157-1159`; `upload_artifact` at 906-916 returns the `sha256:<64-hex>` wire id).

**Fix:** add a hard requirement: upload transcript (and recordings, artifact-level permitting) through `adapter.upload_artifact` and put the returned ids in `CallOutcome.transcript_artifact`/`recording_artifacts`. Also note artifact LEVEL gating (`_ARTIFACT_LEVEL_FORBIDDEN_KINDS`, 924-933) so a refused upload is handled as null-not-crash.

---

## SHOULD-FIX

### S1 — The caller's prompt is NOT on the object the scheduler hands over
Study item 5 says "the caller's prompt comes from there," but `_CompiledScenario` — the object `CallRunner.run` receives — carries ONLY `scenario_key`/`scenario_id`/`sub_goals`/`setup`/`ready` (`scenario_source.py:172-184`); `_load_one` (216-285) reads `scenario.json` and **drops** every other field. The persona/instruction DO exist in the on-disk document (`folder.py:138-150` dumps the full `Scenario` model — `instruction` and `persona` fields, `scenario.py:152-210`), but reaching them requires re-reading `scenarios/<folder>/scenario.json` from `bundle_dir` and matching by `scenario_key` — and `scenario_source.py` is off-allowlist, so the runner must do this itself and must be constructed with `bundle_dir`. The brief should say this explicitly; otherwise the first thing the worker tries (`scenario.persona`) is an AttributeError.

### S2 — "Fakes at the seam of Karthik's machinery (fake room/session objects)" names a seam that does not exist
`rtc.Room()` is constructed INSIDE the engine's case function (`engines/livekit.py:655`), `AgentSession` likewise; neither is injectable. `SimulationRunner.run` (fi/simulate/runtime/runner.py:30-43) accepts adapters/sinks but the LiveKit engine arrives via the plan/registry, not a parameter. Faking "room/session objects" therefore means monkeypatching engine internals — brittle and the wrong lesson. The implementable seam is the CallRunner's own boundary: an injected "place the call" callable (real = builds a `SimulationSpec` and awaits `SimulationRunner().run`; tests inject a fake returning/raising scripted outcomes). The brief should name that seam so all workers converge on it.

### S3 — The §2f framing points the worker at the wrong table
§2f is the closed PROVISIONER build/run code table — `source_tree_unavailable`, `build_failed`, `spawn_failed`, `seed_failed`, ... (hosted-execution-seams.md:676-711). No call-related code exists there and **none is needed**: per-scenario call failures classify via world-handle's errored-receipt table (`call_failed`, v3.3, line 98), which the scheduler applies automatically to any exception. Precise answer to the review question: *no §2f code fits a call failure, and that is not a gap* — §2f governs a different stage (`building_environment`) and §5.4 job-level aborts. Study item 6's "call failures must classify into EXISTING [§2f] codes; if none fits, STOP and flag" would make an obedient worker halt over a non-issue. Also there is no `_SECTION_2F_DOMAIN` map in hosted_entrypoint — the name is `SECTION_2F_DOMAIN`, imported from `process_runtime` (hosted_entrypoint.py:59; derived `_SECTION_2F_CODES` at 312), and CallRunner code has no business touching it. Rewrite item 6 to: receipt-level codes come free from the scheduler; the only §-mapping the worker does is NONE.

### S4 — Study item 3 contains nonexistent facts and steers at the wrong flow
- `ALK_BACKGROUND_NOISE` appears **nowhere** in the repo (full-tree grep). Delete or name the real variable.
- There is no Cartesia "missing-key warning": `livekit_models.py` `_required_env` RAISES `ValueError` on a missing `CARTESIA_API_KEY` (line ~129), and Cartesia is not the default TTS (deepgram is, sdk_voice.py:45-47).
- `run/call.py`'s `place_the_call` shells a SUBPROCESS with no `env` parameter (call.py:44-72) — it inherits ambient env, so reusing it under W>1 with per-call env vars is a shared-mutable-state race the brief's own hard requirement forbids. And `run/call.py::main`'s `wire()` is the webhook + cloudflared tunnel flow (run/live.py) — inapplicable in the guest. The reusable import surface is really `fi.simulate` (`SimulationSpec` construction mirroring `sdk_voice.build_spec`, `SimulationRunner`, the livekit engine) — sdk_voice's own functions are all env-driven too (`_required("LIVEKIT_TARGET_AGENT_NAME")` etc.). The brief should say plainly: study call.py/sdk_voice.py as the TEMPLATE, but drive SimulationRunner in-process with a directly-built spec; do not exec the subprocess path.
- Positive verifications, for balance: Gemini-or-Vertex is real (`livekit_models.py:203-224`), Deepgram STT/TTS default is real, LiveKit url/key/secret via `LIVEKIT_URL` + `api_key_env`/`api_secret_env` is real, and dispatch-by-agent-name is real (`engines/livekit.py:918-927`, `create_dispatch(agent_name=agent_definition.agent_name or agent_definition.name)`; the `LIVEKIT_AGENT_NAME` convention matches `provision.py:2682-2700`).

---

## NOTES

### N1 — `voice_prompt.py` path
It lives at `src/fi/simulate/simulation/voice_prompt.py`, not in the harness `run/` tree the surrounding list implies. Give the full path in the do-not-modify list.

### N2 — Study pointer "hosted_entrypoint.py:370-420"
Covers `NotWiredCallRunner` (actual: 375-392) but NOT "how run_job constructs the scheduler's collaborators" — that is `HostedEntrypointDeps` at 1146-1191 and the wiring at 1662-1666. The scenario_source swap pattern the brief wants mirrored is at 1598-1600. Point at those lines.

### N3 — Claims verified CORRECT (no action)
- Protocol signature exact: `hosted_scheduler.py:210-211`.
- `_execute` handling as the brief implies: 1661-1678 (`WorldUnavailable` → `world_unavailable` retry + mark_unhealthy; `CallAborted` → `call_failed` with `exc.partial`; any other exception → `call_failed`, `call=None`).
- Partial-call rule at 200-208 — brief's "~line 201" quote is a fair paraphrase of "the receipt's `call` field must not be null once the call has genuinely started". Corollary worth adding to the brief: since a generic exception yields `call=None`, the RUNNER must convert any post-dial failure into `CallAborted(partial=...)` itself or the measured timing is lost.
- Receipt `call` shape (outbound v1.4:267-270) matches `CallSummary` field-for-field; the adapter already serializes `receipt.call` (797-830), so the allowlist IS sufficient for the call field to flow — no scheduler/receipt-path edits needed for that.
- `[livekit]` extra covers the deps (pyproject.toml:65-69) and the sandbox image installs it (Dockerfile.sandbox:22-26). No Django imports anywhere under `fi/simulate` or `fi/alk`.
- `target_provider` is the correct (and only) guest-relevant secret purpose (hosted-execution-seams.md:301-304); job voice config detection has real material in `job.agent` (`connector`/`config`/`secret_refs`, job.py:80-83, 132-146).

### N4 — State the absent-config job outcome
"NotWired STAYS as the fallback" means an absent-config job produces one `call_failed` errored receipt per scenario after a burned retry each (CallRunnerNotWired docstring, hosted_entrypoint.py:380-384) and the job still completes. Say this is the intended outcome so the worker doesn't try to "improve" it — or reconcile it with hard requirement 2, which currently reads as demanding something different for the incomplete-config case (see M2).

### N5 — Deterministic room naming
sdk_voice uses `room_name=f"harness-{run_id}"` with a random `new_run_id()`. The brief's determinism requirement ("cannot collide across attempts") is satisfiable — suggest pinning the expected scheme (e.g. job_id/attempt/scenario_key/scenario_attempt) so tests can assert it.

---

## VERDICT: FIX-FIRST

Two of the five MUST-FIXes (M1 evidence, M2 capability_unavailable-vs-allowlist) change the shape of the deliverable, not just its wording; launching the worker now produces either a runner that errors every scenario or a worker stopped on an unimplementable requirement.
