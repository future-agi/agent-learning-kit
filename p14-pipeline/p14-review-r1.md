# p14 review — round 1 (COLD)

Reviewed: worktree `/tmp/alk-callrunner` (feat/hosted-execution-guest @ 89f1ce2), four files:
`src/fi/alk/harness/call_runner.py` (new), `src/fi/alk/harness/hosted_entrypoint.py` (modified),
`tests/harness/test_call_runner.py` (new), `tests/harness/test_hosted_entrypoint.py` (modified).
Against: brief v3 (`callrunner-worker-brief.md`), `reports/p14-worker.md`,
`reports/p14-brief-review.md`, world-handle-interface.md (:95-98, :198-232),
outbound-channels.md (:267-270, :303-305), severity-grading.md v1.0.

## Read-only + import-resolution statement

- **SHASUMS**: recorded before any work and re-recorded after all of it — **identical**:
  - `159cc2ee…5138c8  call_runner.py`
  - `21c3f3d5…c1676b48  hosted_entrypoint.py`
  - `4d45bbe4…bcf78  tests/harness/test_call_runner.py`
  - `3291877e…39a7a  tests/harness/test_hosted_entrypoint.py`
  - `git status` unchanged (same 2 modified + 2 untracked, nothing else). No git mutations, no
    installs, no production-file edits. All mutations ran via **sys.modules substitution** (a
    pytest plugin compiles a mutated COPY under the canonical module name before any test import
    — production import path, worktree untouched), validated first with a no-op mutant
    (30/30 pass — no false kills from class identity).
- **Import resolution verified** (as mandated): with `PYTHONPATH=/tmp/alk-callrunner/src`,
  `import fi.alk.harness.call_runner` → `/tmp/alk-callrunner/src/fi/alk/harness/call_runner.py`;
  WITHOUT it → `/Users/khushalsonawat/Desktop/agent-learning-kit/src/...` (original repo). Every
  test/probe run in this report used the PYTHONPATH form, from inside the worktree, with the
  named venv python.

## Suite runs

`tests/harness/` run twice, clean worktree: **860 passed / 0 failed / 0 skipped** both times
(29.2s, 38.1s). Matches the worker's claim exactly (823 baseline + 37 new). `ruff check` on all
four files: clean (re-run, confirmed).

## E2E probes (real `hosted_scheduler._execute` via `HostedScheduler.run`, real `CallRunnerImpl`,
fake ONLY at the `place_call` seam; receipt wire through the REAL `OutboundAdapter.receipt`)

Probe file: scratchpad `p14rev/probe_e2e.py` — 6/6 pass:

1. **Agent never joins** (AGENT_UNAVAILABLE report) → final errored receipt
   `world_unavailable`/`environment`, a `world_unhealthy` event (world retired), and a
   `scenario_retried` event. ✓ Azain's semantics hold end to end.
2. **Mid-call drop** (place_call raises) → `call_failed`/`infrastructure`, and the receipt's
   `call` is **non-null** with measured `started_at`/`ended_at`/`duration_ms`. The SAME receipt
   pushed through the real `OutboundAdapter.receipt` (hosted_entrypoint.py:797-830) with the
   suite's FakeTransport ships `"call": {...}` non-null on the wire, timing intact. ✓
3. **Silent agent, COMPLETED shape** (zero messages) → normal CallOutcome → scheduler's own
   policy → `evidence_missing`/`simulator`, exactly one retry. ✓ (but see F2 — the real engine
   never produces this shape.)
4. **Silent agent, ENGINE shape** (FAILED/`no_conversation`) → lands `call_failed`, NOT
   `evidence_missing` — recorded as evidence for F2.
5. **Outer-timeout cancellation**: the runner-owned `wait_for` CANCELS `place_call` (fake
   observed `CancelledError`) rather than abandoning it — so the engine's `finally` cleanup
   (session close, room disconnect, managed-room delete, api close — engines/livekit.py:1307-1450)
   is actually delivered on the timeout path. ✓
6. **No secret VALUE in any runner-raised message** (pre-dial abort text names aliases only). ✓

## Mutation table (13 total: worker's 6 re-run + 7 of my own; all via the production path)

| # | Mutation (module :: what) | Killing test(s) | Result |
|---|---|---|---|
| W1 | `call_runner::_room_name` — dropped `-a{attempt_number}` | `test_room_name_matches_the_brief_pinned_format`, `test_room_name_uses_only_the_first_eight_chars_of_job_id` (+1 collateral) | 3 failed — KILLED |
| W2 | `_collect_tool_trace_calls` — `refused=ok` (V1 rule inverted) | `test_tool_trace_translates_rows_and_applies_v1_refused_rule` | 1 failed — KILLED |
| W3 | `_translate_report` — AGENT_UNAVAILABLE → `CallAborted` instead of `WorldUnavailable` | `test_agent_unavailable_status_raises_world_unavailable` | 1 failed — KILLED |
| W4 | `_check_config` — always `None` (pre-dial validation bypassed) | the 3 named pre-dial tests | 3 failed — KILLED |
| W5 | `_translate_report` — `turns = 0` | `test_completed_call_uploads_transcript_and_returns_populated_outcome` | 1 failed — KILLED |
| W6 | `hosted_entrypoint::_default_build_call_runner` — connector gate `!=`→`==` | the 3 named connector-gate tests | 3 failed — KILLED |
| R-A | both post-dial `CallAborted` branches — `partial=None` (timing lost) | `test_place_call_exception_…never_raw`, `test_place_call_outer_timeout_…` | 2 failed — KILLED |
| R-B | ALL non-completed statuses widened to `WorldUnavailable` | `test_non_completed_status_raises_call_aborted_with_partial` | 1 failed — KILLED |
| R-C | zero-turn COMPLETED aborts instead of normal outcome (silent-agent branch destroyed) | `test_zero_turns_completed_call_returns_normal_outcome_with_empty_calls` | 1 failed — KILLED |
| R-D | `_dispatch_agent_name` fabricates `agent-w{i}` when metadata absent | `test_missing_dispatch_identity_metadata_aborts_pre_dial` | 1 failed — KILLED |
| R-E | `_collect_http_tool_calls` fabricates a Call (STOP violated) | `test_http_tool_seam_always_returns_no_calls` (+2 collateral) | 3 failed — KILLED |
| R-F | room-name `scenario_attempt` counter frozen at 1 | `test_scenario_attempt_counter_increments_per_scenario_key_across_retries` | 1 failed — KILLED |
| **R-G** | `hosted_entrypoint::run_job` — secrets capture MOVED to after `await pool.start()` (into the deletion window) | **none** | **70/70 passed — SURVIVED** |

## FINDINGS

### F1 — HIGH (test-honesty on the secrets-lifetime invariant; promoted per precedent P9-S8)
*Axes: impact = whole-voice-feature availability (every scenario pre-dial-aborts with correct
config) / reachability = latent until a refactor reorders run_job — exactly the class of line a
merge moves / loudness = **silent in CI** (loud at runtime).*

Mutation R-G moved `target_provider_secret_values = deps.peek_target_provider_secret_values(secret_purposes)`
(hosted_entrypoint.py:1507) to after `await pool.start()` (:1555) — i.e. after the point where
the real `ProcessRuntimeProvider` has DELETED `/run/futureagi/secrets.json`
(process_runtime.py:3535-3544). **The full 860-test suite stays green**, including the worker's
own `test_call_runner_context_is_threaded_with_real_job_bundle_secrets_and_evidence_seam` —
because that test's FakeProvisioner never deletes `deps.secrets_path`. The worker's report
explicitly claims the new tests cover "secret capture timing"; they do not. The brief's M4
timing trap — THE load-bearing secrets requirement — is currently enforced by a comment.
**Fix (test-only, ~10 lines)**: make the wiring test's fake provisioner unlink
`deps.secrets_path` on its first `provision()` (mirroring the real provider), and keep asserting
the captured context still carries the alias map.

### F2 — MEDIUM (silent-agent case-3 semantics unreachable; engine-shaped silent agent misattributed)
*Axes: impact = wrong receipt code+domain (`call_failed`/infrastructure instead of
`evidence_missing`/simulator; retry count identical, so attribution-only) / reachability =
common (any dispatched agent that joins but never speaks — the 45s agent-first silence watchdog
path) / loudness = typed but wrong domain.*

The delivered case-3 branch (normal `CallOutcome` with real turn count) triggers only on a
COMPLETED case — but `engines/livekit.py::_conversation_outcome` NEVER returns COMPLETED for a
zero-turn session (COMPLETED requires `len(messages) >= min_turn_messages` (6) + role
alternation). A genuinely silent agent surfaces as FAILED with `no_conversation` /
`conversation_silence_timeout` → the runner raises `CallAborted` → `call_failed`/infrastructure.
world-handle v3.5's coverage guarantee says a genuinely zero-turn session is
`evidence_missing`/simulator. Probe 4 demonstrates it through the real `_execute`. The worker's
test for case 3 pins a report shape (COMPLETED + zero messages) the engine cannot produce, and
the report claims case 3 as delivered. **Fix (~10 lines) or ride with a D-entry**: in
`_translate_report`, translate a FAILED case with zero messages (codes
`no_conversation`/`conversation_silence_timeout`) into the normal zero-turn `CallOutcome` so the
scheduler's own policy classifies it; keep every other non-completed status as `CallAborted`.
Scope the mapping to zero-turn only — do not extend it to short-but-nonzero conversations.

### F3 — MEDIUM (raw-exception escape window post-dial, timing lost)
*Axes: impact = partial-call rule violated (receipt `call=null` for a started call) /
reachability = plausible edge / loudness = loud failure, wrong receipt detail.*

`await self._translate_report(...)` (call_runner.py:585) sits OUTSIDE the post-dial
try/except. `upload_artifact` itself is robust (TransportError → retry-loop result;
fenced/channel errors → `_guarded` → None — verified), but `Path.read_bytes()` on a recording
(vanish/permission between `is_file()` and read) or any model surprise in translation escapes
`run()` raw → scheduler's B3 generic handler → `call_failed` with `call=None`, losing the
measured timing the brief centers ("never let a raw exception escape post-dial"). **Fix
(~6 lines + 1 test)**: wrap the translate/upload region so any exception becomes
`CallAborted(partial=<timing-only outcome>)`.

### F4 — LOW (comments violate the brief's WHY-only / no-task-references discipline)
~14 instances shipped in production files: "the brief", "per the brief's Test seam section",
"this worker's allowlist/mission", "M4 timing trap (brief-review)" (hosted_entrypoint.py), and
literal paths to `.claude/harness-alk/reports/p14-worker.md` in call_runner.py's docstrings
(module docstring, `CallRunnerImpl`, CONTRACT NOTE pointers that resolve only against an
out-of-repo scratch file). Cosmetic; a comment sweep is cheap and this project has precedent
passes for exactly this.

### F5 — LOW (blocking I/O on the scheduler's event loop)
`_clear_tool_trace_calls` / `_collect_tool_trace_calls` run synchronous psycopg connects
(connect_timeout=5) directly on the loop; recordings are read with synchronous `read_bytes()`.
A stalled world DB blocks every world's scheduling and outbound flushing for up to ~5s per
occurrence. Bounded, sandbox-local DB, tool_trace has no producer yet → Low. Cheap fix:
`asyncio.to_thread` (or psycopg async).

### F6 — LOW (effective LiveKit room name is not the pinned name)
In managed `room_mode`, `engines/livekit.py::_resolve_room_name` appends
`-{invocation_id}-{test_case_id[-12:]}` (random per run) unless `room_name_verbatim`. The
pinned deterministic `harness-{job8}-a{n}-{key}-s{m}` is therefore only the PREFIX of the actual
room. Uniqueness/W>1 safety is preserved (strengthened); exact-name traceability degrades to
prefix-grep; the worker's report does not disclose this. Optional: `room_name_verbatim=True`
(the spec is single-persona, which the field requires) if the exact name is wanted — the `-s{n}`
counter already prevents retry collisions.

### F7 — LOW (world-DB DSN with password can reach local logs)
`logger.debug(..., exc_info=True)` in both tool_trace functions; psycopg exceptions embed the
DSN (`endpoint.address` carries the world DB password). Local sandbox logs only — outbound
events go through redaction, and these functions emit nothing outbound. Sandbox-internal
credential → Low.

Notes (sub-Low, report-only): `voice_call_timeout_seconds` given as a numeric STRING is
silently ignored (falls to 300) and a boolean would read as 1s (`isinstance(x, (int, float))`
admits bool); a `scenario_key` containing `{`/`}` would crash the engine's
`room_name.format(...)` post-dial (typed CallAborted, not raw); `_scenario_spec` drops the
local lane's `simulator_prompt.md` template rendering — brief-directed ("persona/instruction
come from the on-disk scenario document"), noted for fidelity only.

## Verified clean (evidence, not opinion)

- **Secrets lifetime**: capture at :1507 strictly precedes `pool.start()` at :1555 (the deleting
  `provision()` lives inside it); `peek_target_provider_secret_values` is a non-destructive read
  (no unlink — code + test); redaction registration PRESERVED and covering — the adapter is
  built at :1348 with `extra_secret_values=deps.peek_secret_values()`, a same-file superset of
  the captured values, before any of this; no secret VALUE appears in any raised/logged runner
  string (grep + probe 6 — messages name aliases only); env export happens ONCE, in
  `__init__` (only `os.environ` touch in the module), job-level values ⇒ no W>1 race; exported
  creds cannot leak into spawned world processes — process_runtime builds process env from an
  allowlist (`PATH/HOME/LANG/TZ/TMPDIR` + `LC_*`), never `os.environ` wholesale.
- **Wiring gate**: `"livekit"` → real runner; vapi/retell/auto/None → `NotWiredCallRunner`
  (tests + W6 mutation); connector enum verified against hosted-execution-seams.md:246. Absent
  `livekit_agent_name` (ALWAYS absent today — confirmed: the only `EnvironmentRuntime(...)`
  construction, process_runtime.py:3748, never passes `metadata=`) → typed, loud pre-dial
  `CallAborted`, never a crash (test + R-D mutation + probe). Incomplete voice config → exact
  `voice_capability_unavailable:` prefix naming missing aliases (tests + W4).
- **Evidence**: http_tool genuinely STOPPED — body is `del runtime; return ()`, zero network;
  the no-capture-surface claim independently re-verified (world/handle.py `call()` raises
  unconditionally with the not-pinned docstring; process_runtime `provision()` comment says
  evidence-seam wiring out of scope; zero `TOOLS_API_URL` hits in process_runtime). tool_trace:
  V1 `refused = not ok` (W2), string-form-only truncation at 2000 (tests), `at` 0.0-never-
  fabricated, all-errors-degrade-to-`()` (tests), pre-call best-effort clear present. Both
  disclosed deviations verified sound (protocol-based endpoint lookup mirrors
  `hosted_entrypoint._find_postgres_endpoint`, slugs are author-chosen per `build_endpoints`;
  connector value per contract).
- **Cleanup**: engine's `finally` (engines/livekit.py:1307-1450) covers success, engine-internal
  timeout, exception, AND cancellation; the runner's only cleanup obligation — cancel rather
  than abandon on the outer timeout — proven by probe 5. `CancelledError` re-raised, never
  swallowed (:571-572).
- **Contract notes 1-5**: all five audited and accurate (capture-surface absence; unpinned
  tool_trace convention isolated to two functions + one constant; metadata producer absence;
  `capability_unavailable` absent from `_CODE_DOMAIN` — confirmed in source; provider trio
  matches sdk_voice defaults, and GEMINI/GOOGLE either-or matches
  `livekit_models._google_credentials_kwargs` exactly). Credential/config list in the Done
  section verified against the engine's actual env reads (`api_key_env`/`api_secret_env`
  defaults, `DEEPGRAM_API_KEY` `_required_env`).
- Worker's "5 mechanical call-site fixes" and "+7 tests" counts verified against the diff.

## VERDICT: FIX-THEN-ROUND-2

F1 (High) blocks a clean round. Surgical fix scope — nothing else:

1. **F1 (mandatory, test-only)**: in `tests/harness/test_hosted_entrypoint.py`, make the e2e
   wiring test's provisioner delete `deps.secrets_path` on first `provision()` (mirroring
   process_runtime.py:3535-3544) so the capture-before-`pool.start()` invariant is actually
   load-bearing; keep the existing alias-map assertion. Round 2 must show mutation R-G killed.
2. **F2 (Medium, fix-or-ride at arbitration)**: map the engine's zero-turn FAILED statuses
   (`no_conversation`/`conversation_silence_timeout`, `len(messages)==0`) to the brief's case-3
   normal `CallOutcome` in `_translate_report`; replace/augment the COMPLETED-zero-turn test
   with an engine-shaped one. If ridden: known-defects entry "silent agent receipts read
   call_failed/infrastructure instead of evidence_missing/simulator".
3. **F3 (Medium, fix-or-ride)**: wrap `_translate_report`'s upload/translate region in the same
   CallAborted(partial)-conversion the dial site has; one test with a raising uploader.
4. F4-F7 (Low): report-only; fold the F4 comment sweep in only if free.

Heartbeats written to inflight.md at start, mid-run, and finish (real `date -u`).
