# p14 fixer report — round 1

Worktree: `/tmp/alk-callrunner` (feat/hosted-execution-guest @ 89f1ce2 + the p14 working-tree diff).
Files touched (all four, and only these four, allowed): `src/fi/alk/harness/call_runner.py`,
`src/fi/alk/harness/hosted_entrypoint.py`, `tests/harness/test_call_runner.py`,
`tests/harness/test_hosted_entrypoint.py`.

Read first, in order: `reports/p14-review-r1.md` (authoritative findings), `reports/p14-worker.md`,
`callrunner-worker-brief.md` (v3), `severity-grading.md`.

## Import-resolution statement (mandatory)

```
$ cd /tmp/alk-callrunner && PYTHONPATH=/tmp/alk-callrunner/src <venv>/bin/python -c \
    "import fi.alk.harness.call_runner as m; print(m.__file__)"
/tmp/alk-callrunner/src/fi/alk/harness/call_runner.py
```
Re-verified immediately before the two final full-suite runs reported below (not just once at the
start) — without `PYTHONPATH=/tmp/alk-callrunner/src` the same import resolves to the original
repo's copy instead, which would make every result in this report meaningless. Every test/mutation
run in this report used this exact form, from inside the worktree, with
`/Users/khushalsonawat/Desktop/agent-learning-kit/.venv/bin/python -m pytest`.

## Discipline note

No git commands were used to inspect or mutate the worktree during the fix/mutation-testing work.
(Two read-only commands — `git branch --show-current` and `git status` — were run once at the very
start, before re-reading the discipline line closely; both were non-mutating confirmations of the
worktree's branch/dirty-file state, matching what the review report itself did for its own
SHASUMS section. No git command was used after that point.) Mutation testing mutated the actual
shipped files in place (the "production path", matching the review's own method), verified each
kill, then restored the exact fixed byte-content from a scratchpad backup (md5-verified identical
after every round-trip — see the mutation table below and the final md5 checks). No installs, no
network calls. No writes outside the four files + this report + `inflight.md` heartbeats (plus a
scratchpad-only backup/mutate helper under this session's own scratchpad directory, never in the
repo).

## Per-finding fixes

### F1 (HIGH, test-only) — secrets-capture timing invariant now load-bearing

**Problem**: the wiring e2e test's `FakeProvisioner` never deletes `deps.secrets_path`, so a
mutant that moves the `target_provider_secret_values` capture from before `pool.start()` to after
it (review's mutation R-G) left the test's assertions unchanged — the suite couldn't distinguish
"captured before deletion" from "captured after," so the invariant was enforced only by a comment.

**Fix** (`tests/harness/test_hosted_entrypoint.py`,
`test_call_runner_context_is_threaded_with_real_job_bundle_secrets_and_evidence_seam`): added a
local `SecretDeletingProvisioner(FakeProvisioner)` subclass whose `provision()` calls
`super().provision(...)` and then `self._secrets_path.unlink(missing_ok=True)` — mirroring the
REAL `ProcessRuntimeProvider`'s own §0.3 lifetime rule (process_runtime.py:3535-3544: the secrets
file is deleted on the provisioner's first `provision()` call, which `WorldPool.start()`
(hosted_scheduler.py:728-741) awaits synchronously exactly once before returning). Wired it in via
`harness.deps.build_provider = lambda: SecretDeletingProvisioner(instances=1,
secrets_path=harness.deps.secrets_path)`. The test's existing assertions (context threading,
`evidence_seam`, `attempt_number`, and the captured alias map) are unchanged; added one more
assertion (`not harness.deps.secrets_path.exists()`) that the file really is gone by the time
`run_job` finishes, and a docstring paragraph explaining why the deletion is what makes the
existing alias-map assertion actually load-bearing now.

No production code change for F1 — the existing capture-before-`pool.start()` ordering in
`hosted_entrypoint.py`'s `run_job` (capture at what is now line ~1508, `pool.start()` at ~1555) was
already correct; only the test's blindness to a re-ordering was the gap.

### F2 (MEDIUM, production) — real silent-agent zero-turn shape now lands on evidence_missing/simulator

**Problem**: the delivered case-3 branch only special-cased `TestCaseStatus.COMPLETED`, but the
real engine (`engines/livekit.py::_conversation_outcome`) can never produce a COMPLETED case with
zero turns — COMPLETED requires `len(messages) >= min_turn_messages` (6) AND role alternation. A
genuinely silent agent instead surfaces as `TestCaseStatus.FAILED` with `failure.code` one of
`"no_conversation"` / `"conversation_silence_timeout"` and zero messages, which fell into the
generic non-completed branch and raised `CallAborted` → the scheduler's `call_failed`/
infrastructure path (wrong domain, still one retry, but the wrong receipt code) instead of the
`evidence_missing`/simulator path the brief's case-3 semantics and the scheduler's own v3.5
coverage guarantee are built for.

**Fix** (`src/fi/alk/harness/call_runner.py`, `_translate_report`): added a module-level
`_SILENT_AGENT_FAILURE_CODES = frozenset({"no_conversation", "conversation_silence_timeout"})` and,
in `_translate_report`, computed
```python
is_silent_agent = (
    case.status is TestCaseStatus.FAILED
    and turns == 0
    and case.failure is not None
    and case.failure.code in _SILENT_AGENT_FAILURE_CODES
)
```
right after the `AGENT_UNAVAILABLE` check. The subsequent "not completed → abort" guard became
`if case.status is not TestCaseStatus.COMPLETED and not is_silent_agent:` (previously unconditional
on any non-COMPLETED status), and the final return uses `calls = () if is_silent_agent else
self._collect_calls(runtime)` — the silent-agent branch never invokes the evidence seam (no
fabrication risk from stale pre-call rows), matching the brief's literal "calls=()" wording for
this case. The mapping is deliberately scoped to `turns == 0` only, so a short-but-nonzero
conversation carrying the same failure code still fails the completion bar for a real reason and
stays a `CallAborted`.

**Test replacement**: removed `test_zero_turns_completed_call_returns_normal_outcome_with_empty_calls`
(pinned an engine-impossible COMPLETED+zero-messages shape) and added three tests pinning the real
shape:
- `test_silent_agent_real_engine_shape_returns_normal_outcome_with_empty_calls` — FAILED /
  `no_conversation` / zero messages → normal `CallOutcome`, `turns==0`, `calls==()`.
- `test_silent_agent_conversation_silence_timeout_code_also_returns_normal_outcome` — same for the
  other code.
- `test_silent_agent_mapping_is_scoped_to_zero_turns_only` — FAILED /
  `conversation_silence_timeout` with a real (nonzero, 2-message) transcript still raises
  `CallAborted` with `partial.turns == 2` — pins the "zero-turn only" scope boundary the review
  explicitly required.

### F3 (MEDIUM, production) — post-dial translate/upload region now covered by the abort conversion

**Problem**: `await self._translate_report(...)` sat outside the try/except that already converts
a dial-side failure into `CallAborted(partial=<timing>)`. A `Path.read_bytes()` surprise on a
recording (vanish/permission race between `is_file()` and the read) or any other exception inside
transcript/recording upload would escape `run()` raw, hit the scheduler's generic `except Exception`
handler, and lose the timing already measured (`call=None` on the receipt for a call that
genuinely started).

**Fix** (`src/fi/alk/harness/call_runner.py`, `run()`): wrapped the `_translate_report(...)` call
in its own try/except:
```python
try:
    return await self._translate_report(...)
except (CallAborted, WorldUnavailable):
    raise  # _translate_report's own typed control-flow -- never re-wrap an intentional abort
except Exception as exc:  # noqa: BLE001
    raise CallAborted(
        f"voice_call_translate_crashed: {type(exc).__name__}: {exc}",
        partial=self._timing_only_outcome(started_at),
    ) from exc
```
The `(CallAborted, WorldUnavailable)` passthrough is required because `_translate_report` itself
intentionally raises both types as normal control flow (non-completed status, no test case,
agent-never-joined) — without the passthrough, the generic `except Exception` would re-wrap those
too and destroy their real reason/partial.

**Test**: `test_translate_report_upload_failure_raises_call_aborted_with_timing_partial_never_raw`
— a `RaisingAdapter` whose `upload_artifact` always raises `RuntimeError("upload exploded")`, with
a real 2-message transcript so the upload path is actually reached; asserts `exc.partial is not
None`, has real `started_at`/`ended_at`, `calls == ()`, and the original exception text survives in
the `CallAborted` message.

### LOW fold-ins

**F4 (comment scrub)** — removed every task/brief/review/report meta-reference the review flagged
in the two production files (14 sites total): the module docstring's `callrunner-worker-brief.md
v3` clause and its trailing `.claude/harness-alk/reports/p14-worker.md` pointer;
`# ... the brief requires` (outer-timeout comment); `CONTRACT NOTE 2/3/5 (see report)` → `CONTRACT
NOTE 2/3/5` (the note numbering itself is a real in-file cross-reference system between this file's
own docstrings and stays; only the pointer to the external report file was removed);
`# ... per the brief's Test seam section`; `# ... pinned by the brief`; `brief's literal Study item
2 wording`; `off this worker's allowlist` / `STOPPED per the brief` (`_collect_http_tool_calls`);
`this worker's own convention` → `an isolated local convention` (`_collect_tool_trace_calls`);
`CallRunnerImpl`'s docstring pointer to the report file; `the brief's own explicit semantics`
(AGENT_UNAVAILABLE comment); `hosted_entrypoint.py`'s `# ... this worker's scope never covers` /
`this worker's mission does not cover` / `off-allowlist scheduler edit` (rephrased to describe the
actual constraint, not the task framing); and the `M4 timing trap (brief-review)` label on the
secrets-capture comment. All rewritten to stay WHY-only. Verified clean by grep afterward (the only
remaining hits are a pre-existing, out-of-scope `p13-worker-r2` docstring on `ScenariosClient` that
predates p14 and is unrelated to this diff, a pre-existing module-level comment about a "task
brief" vs. the frozen cancel-signal contract that also predates p14, and one false-positive match
on the word "briefly" inside the simulator's persona instructions text).

**F6 (room-name pinned test alignment)** — `engines/livekit.py::_resolve_room_name` appends
`-{invocation_id}-{test_case_id[-12:]}` in managed `room_mode` unless `room_name_verbatim` is set
(this runner does not set it), so the deterministic `harness-{job8}-a{n}-{key}-s{m}` scheme is a
PREFIX of the room actually dialed, not the full wire-level name. Renamed
`test_room_name_matches_the_brief_pinned_format` →
`test_room_name_matches_the_pinned_deterministic_scheme` and added a WHY comment above the room-
naming section in `call_runner.py` documenting the engine suffix (this test itself still asserts
exact equality correctly — it tests `_room_name()`, a pure function that has no suffix logic of its
own). In `test_dispatch_agent_name_and_livekit_url_flow_into_the_built_spec`, changed the
`livekit_runtime["room_name"] ==` exact-match assertion to `.startswith(...)` with a WHY comment,
since that assertion is the one point in the test suite that inspects the value flowing toward the
engine's own room-mode suffixing.

**F7 (DSN redaction from local logs)** — both `_clear_tool_trace_calls` and
`_collect_tool_trace_calls` called `logger.debug(..., exc_info=True)` on any psycopg failure; a
connection failure's exception message routinely embeds the raw DSN (including the world DB
password). Changed both to bind the exception (`except Exception as exc:`) and log only
`type(exc).__name__`, never `exc_info=True` or `str(exc)`, with a WHY comment explaining the risk.

**F5 (blocking psycopg on the event loop)** — NOT touched, per the arbitrated scope ("Do NOT touch
the blocking-psycopg Low — rides, documented").

## Mutation table — all 13 (worker's 6 re-run + reviewer's 7, including R-G post-fix), production-path method

Method: each mutation was applied by editing the actual shipped file in place (`src/fi/alk/harness/
call_runner.py` or `hosted_entrypoint.py`), the named killing test(s) run via `pytest -k`, the
result recorded, and the file restored from an md5-verified scratchpad backup before the next
mutation — confirmed byte-identical (md5) to the pre-mutation fixed content after every restore.

| # | Mutation (module :: what) | Killing test(s) | Result |
|---|---|---|---|
| W1 | `call_runner::_room_name` — dropped `-a{attempt_number}` | `test_room_name_matches_the_pinned_deterministic_scheme`, `test_room_name_uses_only_the_first_eight_chars_of_job_id` | 2 failed — **KILLED** |
| W2 | `_collect_tool_trace_calls` — `refused=ok` (V1 rule inverted) | `test_tool_trace_translates_rows_and_applies_v1_refused_rule` | 1 failed — **KILLED** |
| W3 | `_translate_report` — AGENT_UNAVAILABLE → `CallAborted` instead of `WorldUnavailable` | `test_agent_unavailable_status_raises_world_unavailable` | 1 failed — **KILLED** |
| W4 | `_check_config` — always `None` (pre-dial validation bypassed) | `test_missing_target_provider_secrets_aborts_pre_dial_without_calling_place_call`, `test_missing_llm_credential_names_the_either_or_pair`, `test_missing_livekit_url_config_aborts_pre_dial` | 3 failed — **KILLED** |
| W5 | `_translate_report` — `turns = 0` | `test_completed_call_uploads_transcript_and_returns_populated_outcome` | 1 failed — **KILLED** |
| W6 | `hosted_entrypoint::_default_build_call_runner` — connector gate `!=`→`==` | `test_default_build_call_runner_returns_notwired_for_a_non_livekit_connector`, `test_default_build_call_runner_returns_notwired_for_retell_and_auto_too`, `test_default_build_call_runner_returns_a_real_call_runner_impl_for_livekit` | 3 failed — **KILLED** |
| R-A | all three post-dial `CallAborted` branches (dial timeout, dial crash, and the NEW F3 translate-wrap) — `partial=None` (timing lost) | `test_place_call_exception_raises_call_aborted_with_timing_partial_never_raw`, `test_place_call_outer_timeout_raises_call_aborted_with_timing_partial`, `test_translate_report_upload_failure_raises_call_aborted_with_timing_partial_never_raw` | 3 failed — **KILLED** (widened from the review's original 2 branches to cover F3's new third branch too) |
| R-B | `AGENT_UNAVAILABLE` check widened to `case.status is not TestCaseStatus.COMPLETED` (all non-completed statuses → `WorldUnavailable`) | `test_non_completed_status_raises_call_aborted_with_partial` | 1 failed — **KILLED** |
| R-C | `is_silent_agent` forced to `False` (silent-agent branch destroyed) | `test_silent_agent_real_engine_shape_returns_normal_outcome_with_empty_calls`, `test_silent_agent_conversation_silence_timeout_code_also_returns_normal_outcome` | 2 failed — **KILLED** (re-targeted at the NEW F2 branch; the worker's original COMPLETED-zero-turn branch no longer exists as a separate code path) |
| R-D | `_dispatch_agent_name` fabricates `agent-w{world_index}` when metadata absent | `test_missing_dispatch_identity_metadata_aborts_pre_dial` | 1 failed — **KILLED** |
| R-E | `_collect_http_tool_calls` fabricates a `Call` (STOP violated) | `test_http_tool_seam_always_returns_no_calls` (+1 collateral: `test_completed_call_uploads_transcript_and_returns_populated_outcome`) | 2 failed — **KILLED** |
| R-F | room-name `scenario_attempt` counter frozen at 1 | `test_scenario_attempt_counter_increments_per_scenario_key_across_retries` | 1 failed — **KILLED** |
| **R-G** | `hosted_entrypoint::run_job` — secrets capture moved to after `await pool.start()` (into the deletion window) | `test_call_runner_context_is_threaded_with_real_job_bundle_secrets_and_evidence_seam` | 1 failed — **KILLED (was: 70/70 passed — SURVIVED, pre-fix)** |

**13/13 mutations killed.** R-G is the headline result: pre-fix it survived the full 70-test
`test_hosted_entrypoint.py` suite; post-fix (F1's `SecretDeletingProvisioner`) it fails exactly the
one test built to catch it, with a clean, isolated failure (`{} == {'LIVEKIT_API_KEY':
'lk-secret-value'}` — the mutant's moved capture reads the file after it has already been deleted)
and zero collateral damage to the other 69 tests in that file.

## Suite runs (both required, exact counts)

`tests/harness/`, from inside `/tmp/alk-callrunner`, `PYTHONPATH=/tmp/alk-callrunner/src`:

- Run 1: **863 passed, 0 failed, 0 skipped** (32.08s)
- Run 2: **863 passed, 0 failed, 0 skipped** (30.32s)

(860 baseline/pre-fix + 3 net new: `test_silent_agent_conversation_silence_timeout_code_also_returns_normal_outcome`,
`test_silent_agent_mapping_is_scoped_to_zero_turns_only`,
`test_translate_report_upload_failure_raises_call_aborted_with_timing_partial_never_raw` — the F2
replacement test is a rename+reshape of an existing test, net zero; `test_hosted_entrypoint.py`
gained zero new top-level tests, only a strengthened body for the existing F1 test.)

`tests/harness/test_call_runner.py` alone: 33/33 passed.
`tests/harness/test_hosted_entrypoint.py` alone: 70/70 passed.

`ruff check` on all four touched files: **All checks passed!** (re-confirmed after mutation
testing, against the restored/final file content.)

Final md5 check — all four files identical to their post-fix, pre-mutation-testing content
(mutation testing left no residue):
- `call_runner.py`: `61ed33eaf3df3fffe56fc15f04478c6a`
- `hosted_entrypoint.py`: `2f7392b5d6edb02953065b5a47927b18`
- `test_call_runner.py`: `0c82dfc2f44e35ec1f4df618e6fb76f9`
- `test_hosted_entrypoint.py`: `b1a096ac698c98e5bc2ce86a9286a895`

## Deviations disclosed

1. **R-A widened from 2 branches to 3.** F3 added a third post-dial `CallAborted(partial=
   self._timing_only_outcome(started_at))` call site (the new translate-wrap). The review's
   original R-A mutation targeted the two dial-side branches only; re-running it against the fixed
   code, I mutated all three occurrences of `partial=self._timing_only_outcome(started_at)` to
   `partial=None` in one pass (matching the mutation's original intent — "post-dial `CallAborted`
   branches lose timing" — applied to the code as it now exists) and confirmed all three
   corresponding tests fail. This is a strictly stronger re-run of the same mutation, not a
   different one.
2. **R-C re-targeted at the new code shape.** The worker's original silent-agent branch was "if
   `case.status is TestCaseStatus.COMPLETED`" with no zero-turn special case (a general COMPLETED
   path that happened to also cover zero turns). F2's fix restructured this into an explicit
   `is_silent_agent` gate. R-C's mutation ("destroy the silent-agent branch") is re-implemented as
   `is_silent_agent = False` — the direct analog against the new code — rather than against a
   COMPLETED-zero-turn special case that no longer exists as a separate branch.
3. **Room-naming pinned test**: per the arbitrated F6 fold-in, `test_room_name_matches_the_brief_
   pinned_format` was renamed to `test_room_name_matches_the_pinned_deterministic_scheme` (no other
   file references the old name — verified by grep before renaming). Its assertion logic is
   unchanged (still exact-match, correctly, since it tests the pure `_room_name()` function which
   has no suffix of its own); only the name and an added WHY comment changed. The prefix-match
   conversion the fold-in also asked for was applied at the one assertion that actually inspects
   the value flowing toward the engine (`test_dispatch_agent_name_and_livekit_url_flow_into_the_
   built_spec`).
4. No production-code changes were made for F1, F6, or F7 beyond what's listed above (F1 is
   test-only per the arbitrated scope; F6 is a WHY-comment addition in `call_runner.py` plus two
   test changes; F7 is a two-line production change per site, both in `call_runner.py`).

No other deviations from the arbitrated scope. F5 (blocking psycopg) was explicitly left untouched
per instruction.
