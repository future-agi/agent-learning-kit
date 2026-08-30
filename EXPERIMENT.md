# Autonomous harness experiment

Branch `experiment/autonomous-harness`, off `feat/pluggable-harness` at `0d56c93`.
Local only, never pushed. Nothing here is proposed for merge as it stands.

The question being tested: if the build stage is given the tools an engineer would have and a
gate it has to pass, does it stop needing a person to hand-author each agent's world?

## What changed

**The build stage can now do engineering.** It has `Bash`, `Write`, `Edit`, `Read`, `Glob`,
`Grep` alongside its world tools. It can read the submitted repository, write files, install
things, run them, read the error and fix it. The sandbox is the boundary.

**The deny-by-default gate is gone.** `UNWANTED`, `gate_hooks` and the denying half of
`permission_gate` are deleted. What survives is `operator_ask`, which only routes the model's
questions to a human when one is attached. `disallowed_tools` now subtracts what a stage was
granted, so a stage asking for a tool is no longer silently outranked by a global denial.

**`read_only_session` is now `working_session`.** The understand stage could previously only read
the agent under test. It can now change it. That was a deliberate guarantee and is deliberately
removed: the eventual goal is a loop that improves the agent, and a stage that cannot touch it
cannot participate. The reason the guarantee existed is real and still applies, so it now belongs
in a skill rather than in the tool list.

**Skill selection is a model decision.** `load_skill` appends a catalogue of the `*.md` files
sitting beside a stage's `SKILL.md`, and the model reads whichever fits after it has seen the
contract. Five are written for the build stage: `voice-livekit.md`, `voice-hosted-platform.md`,
`voice-multi-actor.md`, `browser-and-computer-use.md` and `retrieval-and-assistants.md`. Each
points at the ALK code to reuse rather than describing how to rewrite it. Adding guidance for an
agent class is now a markdown file.

**Probe stopped claiming unexecuted tools pass.** It recorded every runtime tool as a passing
probe with "executes inside the submitted agent runtime" without calling it, so a voice world,
where every tool is a runtime tool, scored perfectly having executed nothing. They are now
reported as `unproven` and counted in neither direction. `verify_runtime_tools()` executes them
against a live runtime and returns what is broken.

**Scenarios gained `extras`.** An open region the model fills and reads back, carried through
JSON untouched. The named fields stay fixed because the platform renders them and every scenario
is validated against them. This is how a second speaker or a browser journey travels without the
display contract changing.

**Postgres is a registered world kind.** A contract naming its store as postgres previously asked
for a kind that did not exist and silently got the sqlite fallback.

## What was deleted

| what | why |
|---|---|
| `world/workspace.py` (143 lines) | existed only to hold an allowlist of two container commands |
| `run_env_command`, `write_env_file` | `Bash` and `Write` do this without an allowlist |
| `UNWANTED`, `gate_hooks`, denying `permission_gate` (110 lines) | the restriction itself |
| `skills/provision-environment/` | dead code, no Python referenced it |
| 4 tests in `test_harness.py` | they asserted the deny-by-default gate: `test_a_stage_may_use_nothing_it_was_not_given`, `test_a_tool_a_stage_was_not_given_is_denied_by_the_hook`, `test_every_stage_gates_with_the_hook_not_only_the_callback`, `test_granting_a_tool_rebuilds_the_gate_not_just_the_list` |

`test_a_question_still_reaches_the_operator` was kept and updated: operator routing survived.

## What was deliberately kept

The validating submit boundary. `submit_scenario` runs `validate_scenario` then `prove` and
refuses with "Not kept. Fix these and submit again", and that is untouched. A long autonomous run
drifts past advice; it cannot drift past a tool that refuses. The `Scenario` model is the single
description of the shape, and the skill now points at it instead of restating it.

`save_world` also still refuses to freeze a world that fails its probes, has no checks of its own,
or has checks that stay green against an emptied world.

## Delta

Seven commits, `0d56c93..HEAD`. 33 files, roughly 500 added and 600 removed, net negative.
Three source files deleted, five skills added.

## How to run it

Unchanged. `python -m fi.alk.harness.cli` as before, or the hosted path through the platform.
The build stage will now have a shell.

## Tests

Baseline on the branch point: **74 failed, 2789 passed, 38 skipped** (24m14s). Those failures are
pre-existing and concentrated in `test_config_and_facades.py` and `test_harness_architecture.py`.

A full run mid-way showed 75 failed / 2785 passed, one worse than baseline once the deleted tests
were accounted for. That one was real and is worth recording, because it is the argument for
keeping this kind of test: `test_a_skill_only_names_tools_its_stage_actually_has` caught that
`build-environment/SKILL.md` still instructed the model to call `run_env_command` and
`write_env_file` after I had deleted both. Nothing else would have noticed until a run wasted
turns on tools that were not there. The skill now points at `Bash`, `Write` and `Edit`.

After that fix `tests/test_harness.py` passes at **288**, and the voice surface passes at **136**.
Four tests were deleted, all of them assertions about the gate that no longer exists.

## Voice, which is the acceptance test

Voice still works, and the strongest evidence is negative: **the voice run path was not modified
at all.** `git diff 0d56c93..HEAD` against `src/fi/simulate/`, `call_runner.py`,
`simulator_voice.py`, `hosted_scheduler.py` and `hosted_entrypoint.py` is empty. Every change here
is in the authoring stages, so what a voice run emits is byte-identical to the branch it came
from: the per-scenario receipt with status, turns, duration and sub-goal verdicts; the transcript
with real `started_speaking_at` / `stopped_speaking_at` timing; the recordings; the tool trace.
136 voice tests pass (engine, call runner, lane equivalence, voice prompt, model selection).

One change did put the voice path at risk and was caught before it left the branch. Making probe
honest about unexecuted runtime tools moved them out of `results`, and for a voice agent every
tool is a runtime tool. `ProbeReport.score` returns `0.00` for an empty `results`, and
`save_world` refuses below `0.85`, so a voice world would have become unsaveable with a failure no
amount of fixing could clear. `save_world` now recognises "nothing here is executable from this
stage" and saves, carrying the tools as unproven rather than inventing a score. Verified by
construction: a report with ten passing runtime probes scores 1.00 before and 1.00 after, because
those entries leave the numerator and the denominator together.

What remains unverified for voice is the same thing that is unverified for everything else: no
hosted job has been run on this branch. The claim is that the voice path is unchanged, not that it
was re-run.

## What is not proven

Being honest about this, because none of it has run in anger:

- **No hosted Daytona job has been run on this branch.** Everything below is unverified in a real
  run, and the shell in particular has never been exercised by a model.

  Worth stating the reason precisely, because a wrong one circulated for a while and cost real
  time. A hosted run builds its sandbox from *this checkout*: the gateway takes the
  `Image.from_dockerfile` branch whenever `ALK_DAYTONA_DOCKERFILE` is set, which it is, and
  `/opt/alk-source` is bind-mounted from this repository. `ALK_DAYTONA_SNAPSHOT` is named in the
  environment but never read on that path, so nothing about snapshot publication gates a run and
  it never needed anyone's intervention. What the last attempt actually failed on was the egress
  proxy Daytona injects into the sandbox refusing the authoring call to Vertex
  (`ClientHttpProxyError: 502`, `172.20.0.1:18080`). Whether that is still live is unknown: it has
  not been retried since roughly 2026-08-29, and nothing here should be read as saying it is
  fixed.
- **`verify_runtime_tools` is wired and called, and on the hosted lane it still proves nothing.**
  Corrected 2026-08-30, having been settled against the artefacts of run `fe0d2397` rather than by
  reading. The scheduler carries the contract now and `_verify_world` runs, but two halves of the
  chain are missing and either one alone is enough:

  - `HostedWorld` has no `forward` seam, so nothing can be called from the scheduler.
  - The bundle the hosted lane compiles ships thirteen files -- `contract.json`, a tool proxy,
    the scenario checks, `seed/world.sql`, the simulator prompt -- and the world snapshot manifest
    is not among them. `runtime_tools` lives in that manifest (`world/snapshot.py:149`), so the
    leased world could not name its runtime tools even if it could call them.

  Until 2026-08-30 this reported as a **pass**. `verify_runtime_tools` asked "do you declare any
  runtime tools" before "can you call anything", and a hosted world answers "none" to the first
  because the attribute is absent, which returned `checked=True` with no faults. That is the
  verdict type's own definition of ok, and `_verify_world` said nothing on that path, so three
  live runs read as clean. Both halves are fixed: the seam question is asked first, and every
  outcome now logs a distinct line. **The gap itself is unchanged and is still the most important
  one: the autonomy is in and the gate that would make it safe cannot reach the agent's tools.**
  What changed is that it now says so instead of reporting success.
- **The world handle (`reset -> step -> reward`) is not implemented.** Downstream still reaches
  for stores directly, and `world/snapshot.py` still decides "is there a world" by looking for a
  file called `world.sqlite`.
- **The run receipt has no validating boundary**, same as the world handle.
- **Vapi/Retell still route to `NotWiredCallRunner`.** There is now a skill telling the model how
  to think about them and no code path for them to run on.
- **Multi-actor is carriable, not runnable.** `extras` can hold a second speaker;
  `SimulationSpec.simulator` is still singular, so nothing would stage them.
- Whether a model actually uses a shell well here is the entire open question, and it is exactly
  what a run would tell us.

## What I would do next, in order

1. Define a runtime-tool invocation/evidence contract, then wire `verify_runtime_tools` into the
   hosted run after the world comes up. Without it this branch grants autonomy and removes the
   check that made it defensible.
2. Run one hosted job and read what the model does with a shell. That is the experiment.
3. Then the world handle, because it is what makes "any stack" true rather than aspirational.

## The runtime-tool gate (the thing that earns the shell)

`verify_runtime_tools` had no callers. The autonomy was in and the verification that justifies it
was not, which is the one configuration worse than the restriction it replaced.

It now returns a `RuntimeToolVerdict` with three outcomes, not a list. `checked=False` means
nothing was proven and is **not** the same as an empty `broken` list, because reading absence as
success is exactly the defect this gate exists to close. `HostedScheduler` verifies a world once,
caches by index, demotes a world whose tools do not answer (`runtime_tools_broken`, marked
unhealthy so the pool reconciles), and logs at WARNING when a world cannot be asked at all.

The honest limit: `HostedWorld` has no `forward` seam and `HostedWorld.call()` raises by design
("the http_tool shim wire format is not yet pinned by the contracts"). So in the hosted lane the
gate currently reports that N runtime tools go ungraded rather than proving them. That is the
truthful state, and it is loud instead of silent. Wiring it to pass quietly would have looked
finished and proven nothing. The seam has to exist before this gate can bite in hosted; in the
provisioned lane, where `forward` is real, it bites today.

## A crash found on the way

`hosted_scheduler.py` logged `scenario.name` in the readiness path, but the `Scenario` protocol
has `scenario_key` and no `name`. Every `ready_not_ready` verdict therefore raised `AttributeError`
and surfaced as `driver_crashed`. It predates this branch and is on `feat/pluggable-harness`, so it
affects PR #69: the logging added to diagnose a 0-turn readiness failure crashed on exactly that
path. Fixed here in `e47a5e5`.

## The skill library

`voice-livekit.md` is now 170 lines against the Anthropic `pptx` skill's anatomy: frontmatter that
routes toward and away from itself, a routing table, a scripts table with each gotcha inline, real
import and assembly code taken from `call_runner._build_spec`, a footguns section where every
entry names the symptom the reader will actually observe, a required QA section, and an avoid
list. Its thesis is borrowed: the model knows Python and HTTP, so spend the skill on what it
cannot guess.

Two scripts ship beside it:

- `scripts/probe_voice_providers.py` asks Cartesia and Deepgram a trivial question and prints the
  HTTP truth. A provisioning pass costs ~13 minutes before the first word, so a dead key is
  otherwise found at the worst moment; `402` is out of credit.
- `scripts/check_call_evidence.py` is the QA gate as a command. It catches the mute-simulator case
  specifically, because caller turns with text and null `started_speaking_at` read as a stalled
  agent and cost a full night to diagnose by hand.

### Canonical skill structure

Restructured to the layout `skill-creator` specifies, so progressive disclosure is real rather
than nominal:

```
build-environment/
├── SKILL.md          (498 lines: workflow, selection, QA, shared footguns)
├── references/       (one domain per file, loaded only when chosen)
└── scripts/          (executable, never loaded into context)
```

Three levels: the {name, description} index is always in context; SKILL.md loads when the stage
runs; a reference body is read only once the model has decided it applies. `config.sub_skills`
now globs `references/*.md` and publishes each file's frontmatter `description`.

### Selection is the model's judgement, from evidence

No constant names a reference and nothing is passed in. SKILL.md sequences it: gather evidence
from the repository and contract, state the conclusion and the evidence for it out loud, read the
matching reference, then build. Stating it out loud is deliberate, so a wrong turn is visible in
the log rather than only in the outcome an hour later.

Descriptions are triggers, not summaries. Each names the discriminating evidence: an agent process
with `livekit-agents` and an `rtc_session` entrypoint, versus webhook handlers and a Vapi key with
no agent process at all, versus a browser driver, versus a vector store. The failure mode this
guards against is silent: a vague description gets a plausible-but-wrong reference loaded, followed
confidently, with nothing erroring. So every reference also opens with a **selection check** that
restates the evidence justifying it, which makes a wrong load self-detecting.

When the evidence does not settle it, SKILL.md directs the model to `AskUserQuestion` rather than
guess, and lists what is worth asking about: no agent process and no platform credentials, two
plausible transports, a datastore referenced but never configured, a missing credential.

**Stage-level selection remains hardcoded** (`understand.py:22`, `build.py:26`, `scenarios.py:40`,
`run/stage.py:29`). That is a different question and defensibly so: the pipeline decides which
stage runs, and a stage choosing whether to be the build stage is not autonomy but confusion. The
within-stage choice, which is the one that varies by agent, is now fully the model's.

### Adding an agent kind is a markdown file

Demonstrated, not asserted. `voice-bland.md` was added as a stub and became selectable in the
catalogue with **zero Python changes**. The one `config.py` edit in this pass is a mechanism fix
made once (read the frontmatter `description` rather than the first line of prose, which would
have summarised every new skill as `---`); Bland was selectable before it, just with a poor
summary. Adding Vapi, Retell or a browser platform tomorrow is the same single file.

## Still unproven

- No hosted run has happened. Docker is down on this machine. The shell has never been exercised
  by a model, which is the actual experiment.
- The hosted gate reports ungraded tools rather than proving them, pending the `http_tool` seam.
- World handle (`reset -> step -> reward`) is not implemented; `world/snapshot.py` still decides
  "is there a world" by looking for `world.sqlite`.
- Vapi/Retell/Bland have skills and no code path: `NotWiredCallRunner` still refuses them.
- Multi-actor is carriable through `Scenario.extras` and not runnable; `SimulationSpec.simulator`
  is still singular.
- The remaining sub-skills (browser, retrieval, hosted-platform, multi-actor) have routing
  frontmatter but not the depth `voice-livekit.md` now has.


---

# Phase 2: generality beyond the build stage

## The run stage stopped enumerating connectors

`_default_build_call_runner` was an if/elif over two known connectors with a comment saying
Vapi and Retell were "deliberately not inferred", so a Vapi agent could not run because a Python
branch refused it however well its environment had been built.

It now resolves. `transports.py` holds a registry in the shape `world/kinds.py` already used:
each transport carries its own `claims` predicate, so recognising a LiveKit agent is knowledge
that lives with the LiveKit transport rather than in a branch the run stage owns. Resolution order
is the environment's declaration first, then self-recognition.

The declaration is `transport.json` in the bundle, written by whoever built the environment,
because that is the only stage that has read the repository and knows:

```json
{"transport": "whatsapp_business", "runner": "runners.whatsapp:WhatsappCallRunner",
 "requires": ["turns", "transcript", "timing"]}
```

`runner` is imported with the bundle on `sys.path`, so a runner the build stage wrote for a
transport nobody has implemented is loaded and used. **Demonstrated in a test**: a connector named
`whatsapp_business`, which appears nowhere in this codebase, resolves to a written runner and
executes while ALK knows only `livekit` and `repository_chat`.

`NotWiredCallRunner`'s role as a catch-all refusal is gone. An unresolvable transport now raises
`TransportUnresolved` **before any world is leased**, naming what was asked for, what is
registered, and what to declare. The two tests that asserted the old silent refusal were replaced
with tests asserting the new contract.

## The receipt is a validated boundary

A runner the build stage wrote is free in how it works and not in what it returns. `_run_call` now
validates the outcome and raises `CallEvidenceMissing` with repair instructions, the same treatment
`submit_scenario` gives a scenario.

One correction worth recording, because the first version was wrong. The boundary initially
demanded recordings from every runner, which would have rejected a correct text agent, and it
enforced against every `CallOutcome` ever constructed, which broke 43 scheduler tests whose fakes
are deliberately minimal. Both were the same mistake: assuming a single fixed set of evidence.
**What a runner owes is now declared by its transport** (`Transport.requires`, overridable in
`transport.json`). Voice owes turns, transcript, recordings and timing; text owes the same minus
recordings; a caller that declares nothing is held to nothing, because it is exercising the
scheduler rather than shipping a receipt.

## Scenario generation is a first-class skill

Built on the PM's framework (internal-docs PR 44), in the same shape as `build-environment`:

```
write-scenarios/
├── SKILL.md            points at the framework before any scenario is written
├── references/
│   ├── _framework.md   the invariant part: six orthogonal axes, the 12 canonical operations,
│   │                   the compatibility mask and sampling strategy
│   ├── voice.md  chat.md  cua.md  coding.md    the axis VALUES per agent type
└── scripts/
```

The framework's own claim is that onboarding a new agent type means answering five questions about
axis X with its levels and nothing else moves, which is exactly the structure the skill library
already had. Task intent is derived rather than listed: the agent's domain objects crossed with
Retrieve, Compare, Explain, Diagnose, Create, Update, Cancel, Execute, Configure, Authenticate,
Navigate, Handoff. That is what makes coverage provable instead of ad hoc, and it puts the
irreversible Execute cell where it belongs, always covered.

## The doctrine that shapes all of it

`harness.md` is the preamble every stage receives, so three things now sit there rather than in
one stage's file:

**You decide and write; the code executes.** Be as inventive as you like up to the moment the first
call is placed, and a machine after it. A model improvising mid-run destroys reproducibility, the
frozen baseline and the flakiness answer, and destroys them invisibly because the results still
look like results. If you want to intervene during a run, the runner is wrong: stop, fix it,
restart.

**One loop, and you may go back.** Phases are checkpoints, not doors that lock. Discovering a
broken world while writing scenarios must send you back to fix the world. Writing scenarios against
it instead is what produced most of yesterday's debugging: the failure surfaces in a graded call an
hour later and blames the agent.

**Memory is on disk.** `contract.json`, the world, the scenarios, the receipts. Re-read rather than
remember, because a run of this length outlasts any context window.

---

## Two defects in the written-runner loader

Both were invisible to the suite and both fired only where the feature earns its keep: a runner a
model wrote, in a job with more than one world.

**A bad runner crashed instead of failing typed.** `_load_written_runner` caught only
`ImportError`, so a module that exists and raises while executing -- a `SyntaxError`, a
module-level exception, a missing dependency re-raised as something else -- propagated raw and the
scheduler saw an untyped crash. Model-written code is precisely the code most likely to carry a
module-level mistake, and that is the one path where a typed failure carrying "here is what to fix"
matters most. Now every failure is a `TransportUnresolved` naming the file, the exception type and
its text, and a module that fails is removed from the cache rather than left half-loaded.

**The module cache served one world another world's runner.** `import_module` caches by module
name, and a skill teaches one conventional name, so two bundles in a job both calling their runner
`runner` or `runners.voice` resolved to whichever imported first. Every later world silently ran
the earlier world's runner: nothing errors, and the receipts look plausible while belonging to the
wrong environment. Runners are now loaded by file location under a name namespaced by the bundle's
path, so the cache cannot alias them, and `sys.path` is restored in a `finally` so one bundle
cannot shadow the next.

Mutation-tested, and the first attempt was not good enough. Reverting the per-bundle namespace left
the two-bundle test passing, because file-location loading bypasses `sys.modules` regardless of the
name -- so that test was not pinning the defect. Forcing the original `import_module` path instead
reproduced it exactly: `assert 'from-world-0' == 'from-world-1'`. Twelve tests now cover both
defects plus sibling imports, dotted module names, and `sys.path` restoration on the failure path.

## The receipt boundary was masking the diagnosis it exists to protect

The worst of the three, because this one destroyed a finding rather than merely crashing.

`call_runner.py` deliberately maps the engine's "agent joined but never spoke" codes
(`no_conversation`, `conversation_silence_timeout`, and only at zero turns) to a normal
`CallOutcome` with no calls, so the scheduler's own coverage rule reports
`evidence_missing`/simulator. That is what tells an operator the agent was silent or the caller's
speech never rendered. The receipt boundary I added then fired first on `turns < 2`, raised
`CallEvidenceMissing`, and -- because nothing caught it specifically -- fell through the generic
`except Exception` and arrived as `call_failed: CallEvidenceMissing: the call runner produced an
outcome the platform cannot render`.

So a dead TTS key or a silent agent would have reported as a broken runner. That is exactly the
misleading-message failure that cost a full day to diagnose, and this time we would have built it
ourselves, into the very contract meant to prevent it.

Three changes:

- **A zero-turn outcome is a diagnosis, not a receipt.** The contract now returns early on
  `turns == 0` and lets it pass through untouched. `CallOutcome` carries no status field, so the
  boundary cannot ask "did this claim to be a completed call"; zero turns is the honest proxy, and
  it is exactly the shape the runner deliberately produces.
- **`CallEvidenceMissing` is caught explicitly**, next to `CallAborted`, with its own code
  `call_evidence_missing` (domain simulator, not retryable -- a runner that omits a transcript
  omits it again). Two different problems no longer arrive under one label.
- **The turn floor is gone entirely.** A one-turn call where the agent answered and the caller rang
  off is a real call, and whether a conversation went far enough to judge is what sub-goal grading
  already decides.

One thing the fix nearly introduced: `_failure` does `_CODE_DOMAIN[code]`, a closed table that
raises `KeyError` on an unmapped code. Adding a new code without adding its domain would have
crashed the scheduler -- the same class of defect again. Mapped and tested.

Mutation-verified in three directions: policing zero-turn outcomes again fails 2 tests, removing
the domain mapping fails 1, restoring the turn floor fails 1.

## The verification gate cached by position, so it only ever caught a world once

`_verified_worlds` was a `set[int]` keyed by world index, written in two places and never
discarded. `mark_unhealthy` did not touch it and neither did reconcile.

An index is a position in the pool and it is reused. Once index 0 was verified, every future world
landing at index 0 was treated as already checked, and the replacement path is precisely where
checking matters most: the previous world there may have been demoted **by this gate** via
`runtime_tools_broken`, reconcile builds a fresh one, and the fresh one skipped verification
entirely. So the gate caught a broken world once and then silently accepted its successors, which
is the "passes quietly" failure its own docstring says it must never do.

The codebase already knew this. `lease()` a few lines above goes out of its way to compare object
identity, with a comment that a concurrent reconcile may have replaced this index's runtime and
that a verdict computed against the old object no longer describes the new one.

Now keyed by the runtime object: `dict[int, Any]` mapping index to the runtime that was verified,
compared with `is`. The runtime is held rather than its `id()`, so a freed object's address cannot
be recycled into a false match. Caching still works for the case it exists for -- ten scenarios on
one world verify once -- because that is the same runtime object throughout.

Mutation-verified: keying by index again fails 2 tests. Four new tests cover replacement at the
same index, a rebuilt-and-still-broken world, per-index independence, and that the once-per-world
caching survives.

One existing test needed correcting rather than working around: it looped with a fresh runtime per
iteration, which under the fix is correctly re-verified. Its intent was "one world, verified once",
so it now holds one runtime across the loop.

## The scenario skill was layered upside down

`write-scenarios/SKILL.md` was 586 lines against a 500 ideal, while its per-type references were
34 to 47. Both halves of that are wrong, and the second matters more.

The body loads every time the skill triggers; a reference loads only when chosen. So length in the
body is the expensive kind and length in a reference is nearly free, and this is the one place that
asymmetry actually bites.

More importantly the richness was in the wrong file. The per-type reference is what someone edits
to add an agent kind, so a 40-line type file makes "adding an agent type is one file" true in
mechanism and hollow in practice: the file they add carries almost nothing.

Two changes. The code-authoring cluster (setup_code, collection shape, ready_code, the solution)
moved to `references/_authoring-code.md`, which is a real hierarchy layer rather than a filing
exercise: it is needed at the point of writing those three fields and not at all while deciding
which scenarios the suite needs. SKILL.md is now 498, matching build-environment.

Then the per-type files were thickened toward the depth `voice-livekit.md` reached: voice 47 to
107, chat 34 to 85, cua 36 to 81, coding 37 to 85. Each now states, per axis, what the values
actually are for that modality and **what failure varying that lever surfaces**, plus a footguns
section and a minimum-coverage list. The framework stayed lean at 82 lines, which is the right
shape: invariant and short, per-type and rich.

Worth recording what the rewrite forced into the open, because it is the part that could not have
been produced by moving text around. Each modality has one structural fact the others do not have,
and once named it explains most of that modality's failures: voice is lossy and interrupting with
no private field; chat is durable and asynchronous, so its risk is a confident well-formatted wrong
answer and pasted-content injection; browser use has an interface that is not a contract and
actions that cannot be undone; and a coding agent can see and modify its own grader, which is why
gaming verification is its dominant harm class.

## The scenario stage was told to read a file it could not open

Found by review, not by a run, and it could not have been found by a run either: nothing errors.

`sub_skills()` appends a catalogue of reference files to every stage prompt, ending with the
instruction to pick one and "read that file with the Read tool". Both scenario writers were
granted `("AskUserQuestion",)` and `()` respectively. So the stage was handed an index of six
files, told to choose one and read it, and could open none of them. It would have proceeded on
the prompt alone or invented what the reference said, which is the failure that same paragraph
warns about in its own words, one step worse: not reading the wrong file, reading none.

The whole per-modality generality of scenario writing, the voice/chat/cua/coding split I had
just spent a commit deepening, was inert. That is the uncomfortable part. I thickened those
files without checking that anything could open them.

Two things made it survive a green suite:

- The tests assert the catalogue string renders. Rendering is not honouring. A prompt naming a
  tool and a session granting one live in different files, and both halves were individually
  valid.
- There was already a guard, `test_a_skill_only_names_tools_its_stage_actually_has`, and it
  checks the exact complement of this. Its pattern is `` `[a-z_]... ``, lowercase-initial, so
  CapitalCase builtins never match it; it reads only `SKILL.md`, never `references/`; and it
  compares against the MCP server's tools, not `builtins` at all. It even whitelists
  `AskUserQuestion` explicitly. A guard aimed one field over from the hole.

Fixed by granting both writers `("Read", "Glob", "Grep", "AskUserQuestion")` through one shared
`WRITER_BUILTINS`, matching `reception.py`. Read-only plus the question is the right grant:
`Glob`/`Grep` because the catalogue says to decide "from the evidence in the repository", which
is a search instruction, and no `Write`/`Edit`/`Bash` because what a writer must not do is save
the suite behind its own back, and that stays withheld structurally by leaving `save_scenarios`
out of the slice writer's server.

The review spec was reported as a third instance and is not one: it never calls `load_skill`,
its prompt names no tool, and granting it `Read` would be the same error inverted, handing a
session a tool its prompt does not assume.

The guard is an AST sweep over the package rather than a list of the three specs that exist
today: it finds every `SessionSpec`/`working_session` call, resolves which skill it injects and
what it grants (following `builtins=WRITER_BUILTINS` back to the constant), and asserts both
that a stage offered references can `Read`, and that every builtin the stage's text names is
granted. It discovers five sessions across four files. Mutation-tested three ways, including
removing `Read` from `build-environment`, a stage I did not touch, to show it is not a spot
check. The second assertion covers `references/` too, which is safe only because builtins are a
closed set of CapitalCase names; I deliberately did not extend the older MCP-name guard the same
way, because references legitimately backtick field names, transport names and failure codes, so
it would need an ignore list wide enough to stop guarding anything.

This is the fifth defect in a row of one shape: a safety mechanism that fails open. The gate that
returned nothing, the loader that cached across worlds, the boundary that masked its diagnosis,
the cache keyed by position, and now an instruction that cannot be obeyed. None of them raise.

## The grant meant nothing, because the gate under it was removed

Second review finding, and the more serious of the two. I had removed the deny-by-default
permission regime in `e53b800`, on the premise that "a stage runs with the tools it was given, in
a sandbox". Two things are wrong with that.

The first is mechanical, and the SDK settles it rather than my reading of it. `allowed_tools` is
an auto-approval list, not an exposure list. `claude_agent_sdk.types` says the callback is "used
solely for tools outside allowed_tools", and ships a `CanUseToolShadowedWarning` for exactly this
configuration. So `can_use_tool` is consulted only for the tools the harness did NOT grant, and
`operator_ask` returned Allow for all of them. The grant was not narrowing anything: the run
stage, holding `("AskUserQuestion",)`, could call `Bash`. Every stage could call anything the
host exposed. That is the opposite of what the code read as, and it is the precise hole the old
docstring said the gate was added to close after a host search tool cost a stage its turn budget.

The second is the premise. The sandbox is the boundary for the hosted lane. `agent-harness build`
runs the same stages in-process on the operator's machine, where `cwd` does not confine a shell,
and this checkout sits next to ones holding live provider keys. So the reasoning held for one
lane and I applied it to both.

I restored the regime rather than gating it per lane. A lane-dependent grant would mean the local
run is no longer a rehearsal of the hosted one, and the whole value of running locally first is
that it is the same thing. The grant now means the same in both places, and the build stage keeps
the shell it earned, because the hidden list is filtered against the grant rather than applied
over it.

Enforcement is the `PreToolUse` hook, not the callback, for the reason above: the callback is
shadowed for everything granted, so it is structurally incapable of being the gate. The callback
stays as the backstop and the operator-question route.

Two things worth recording:

- The removal commit touched **no test file**. That is why deleting a security boundary left the
  suite green, and it is the same shape as every other defect on this branch: nothing errored.
  There are now seven tests on it, mutation-checked three ways.
- The two findings interlock. With the gate restored and the scenario stage still holding
  `("AskUserQuestion",)`, `Read` is now a hard `PermissionResultDeny` rather than a silent
  no-op. Fixing the catalogue grant first was a precondition for restoring the gate at all; had
  I done these in the other order, the scenario stage would have been denied the very file it is
  instructed to open.

## The postgres entry: the comment was wrong, but so was calling it a no-op

Asked which it was, a comment overstating a symptom or a live bug it was masking. Neither, and
the third answer is the interesting one.

There is no live postgres bug. `for_contract` is the only consumer that takes a store name;
`probe.py` calls `resolve("sqlite")` as a literal and `supported_kinds` is exported but never
consumed. So nothing was masked and there is nothing to hunt.

But the entry was not inert either. `for_contract` checks the registry, then a no-store list,
then **modality**, then falls back to sqlite. Registry membership therefore decides whether the
modality branch is reached at all:

    store=postgres modality=chat      before=SqliteWorld   after=SqliteWorld
    store=postgres modality=browser   before=BrowserWorld  after=SqliteWorld

So for voice and chat the change really was a no-op, which is most cases and is why it reads as
one. For a browser or computer-use agent it silently flipped which world gets built. The comment
describes a symptom that could not have happened, and misses the effect that did.

Following that through found the actual defect, which is bigger than the entry: whether a store
was *named in the registry* decided how a browser agent was inspected.

    store=postgres modality=browser  ->  SqliteWorld
    store=mysql    modality=browser  ->  BrowserWorld

Same agent, same shape of state, different world, because one engine had been added by hand and
the other had not. Adding one alias fixed one name and deepened the inconsistency for every name
still missing.

Fixed by registering the row engines as a set rather than as a special case, so postgres,
postgresql, mysql, mariadb and clickhouse are all the same kind, and by making the fallback say
what it assumed. An unrecognised store still gets a world, because refusing to build over a name
would be worse than inspecting it imperfectly, but it now names the store, names the shape it
assumed, and names `register_kind` as the way out. A document or key-value store inspected as
rows reports state shaped like the question rather than like the store, and that reads as a real
answer, which is the same failure mode as everything else on this branch.

What I did **not** do is reorder the precedence so the store always beats modality. The module
says the store is the honest source, and taken literally that means a CUA agent with any declared
store should be inspected as rows, which would change behaviour for every browser agent that
declares one. That is a design decision about what a CUA world is for, not a defect, and it is
the coordinator's call. Left as is, and recorded here.

One test of mine was weak and mutation testing caught it: the row-store parametrize drew its
cases from `ROW_STORES`, the constant under test, so shrinking the constant deleted the coverage
instead of failing it. Reverting to the postgres-only registry passed 12 tests happily. The names
are written out literally now, and the same revert fails on exactly the browser and cua cases.

`HOW-IT-WORKS.md` still documented `write_env_file` / `run_env_command`, removed when the build
stage got a real shell, and still claimed sixteen tools and "no file access at all" when there
are twenty-one and a shell. Corrected, and there is now a guard that reads the `| Tool |` tables
and checks every name against the real servers. It is scoped to those tables rather than every
backticked word because the same document tabulates contract fields, which are not tools.

## The evidence gate was inert for the only thing it was built to police

`call_evidence_faults` exists because the build stage can write its own runner, so nothing
guarantees a new one emits what the platform renders. A written runner was exactly what escaped
it.

`resolve()` returned a freshly constructed `Transport` for any declaration carrying a `runner`
and never passed `requires`, so it defaulted to `()`. The declared transport name was used only
as the key; the registry was never consulted for its default. Reproduced end to end:

    builtin livekit requires        : ('turns', 'transcript', 'recordings', 'timing')
    written runner, requires omitted: ()   key = livekit
    faults on a 6-turn call with no transcript, no audio, no timing: []

An author following the skill, which said "omit it and the built-in default for a named transport
applies", shipped a runner that could return a silent voice call and the gate reported nothing.
That is the empty-conversation-view failure the docstring describes, restored by omission.

Fixed by inheriting the registered transport's `requires` when a written runner names one. Two
adjacent conflations came out of the same read:

- `"requires": []` was treated as unset and inherited the default, so an author who said this
  runner owes nothing was overridden by a guess. An empty list is now a declaration; only an
  absent key inherits. That is the same defect as the main one, pointing the other way.
- A written runner for a transport ALK has never seen has nothing to inherit, and `()` there is
  truthful. It now says so rather than reporting a clean gate.

**I got the `TransportUnresolved` path wrong first, and the tests caught it.** The review called
it probably unreachable because the runner is built from the same Evidence first; I agreed and
made it raise. That broke 29 tests. The reason is worth keeping: building the runner is what
resolves the transport, so any caller injecting its own `build_call_runner` never resolves at
all, and injection is the normal path for an embedder and for most of the entrypoint's own tests.
The path is not unreachable, it is routine. It now returns `()` with a warning, because with no
declaration there is genuinely nothing to hold anyone to, and the only thing wrong with the
original was that it was silent.

Four mutations, all caught: delete the inheritance, treat `[]` as unset, silence either warning.

The pattern holds for the sixth time. Every one of these is a gate that fails open, and in this
case the gate was written specifically to catch this class and had been disabled for it.

## The permission tests never presented a harness tool to the gate

Not a defect: the wiring is right. `allowed` is builtins plus the qualified server tools, and the
same list reaches `gate_hooks`. But every one of the seven tests used a bare builtin, and the
harness's own tools arrive as `mcp__{server}__{tool}`, so nothing held the part that matters.

Substituting `gate_hooks(spec.builtins)` for `gate_hooks(allowed)` reads like a tidy-up, since
`builtins` is the natural phrase for what a stage was given. It denies every harness tool call in
every stage, and all seven tests still passed under it. The build stage would have died on its
first `save_world`, minutes into a real run, having passed the whole suite.

    a harness tool is named: mcp__environment-world__save_world
    gate_hooks(allowed)       -> allow
    gate_hooks(spec.builtins) -> deny

Two tests now, driven through the options the backend actually builds rather than a hook
constructed in the test, so they pin the wiring and not just `gate_hooks`. The second checks that
qualifying a name does not make it safe: a stage holding the world server is still refused the run
server's tools. Three mutations caught, including the exact substitution above.

Worth naming the pattern, because it is the same one as the write-scenarios grant: the mechanism
was asserted and the thing it exists for was not. A gate tested only against tools no stage uses
is a gate tested against nothing.

## "The sandbox is the boundary" was in three places, one of them a prompt

The review named `build.py`. It was also in `config.py`'s `working_session` docstring and, worse,
in `build-environment/SKILL.md`, which tells the model **"There is no allowlist."** That is now
false, and it is false in a prompt: the model is told it may reach for anything while the restored
gate refuses what the stage was not granted. Same defect as the scenario catalogue, inverted.
Prompt text asserting a permission model the code does not implement is not a stale comment, it is
an instruction to try things that will be refused.

All three corrected. The grant is the boundary and it is the same in both lanes; the sandbox is
only present hosted. The skill now tells the model what is actually true: no command is filtered
and it never needs to ask, but reaching for a tool it was not given is refused rather than
ignored, and it should keep its work inside the run's own directories because this stage is not
always inside a sandbox.

## The evidence gate threw away the evidence

Live defect, and the same family as `305fdf9`: the diagnosis is discarded at the moment it
becomes useful.

`CallEvidenceMissing` carried only `faults`. `_run_call` had the complete `CallOutcome` in hand
when it raised, and dropped it, so the handler reported `call=None`. A voice runner returning six
turns, ninety seconds and four tool calls but no transcript produced a receipt saying the call
did not happen, next to a message saying the call produced the wrong thing. The fault text asks
the author to "return the outcome again" while withholding the outcome that would show what was
returned.

The sibling handler one block up already had this right: `CallAborted` carries `partial`, and its
docstring states the rule both are bound by, that the receipt's `call` must not be null once the
call has genuinely started. This is the **stronger** instance of that rule. An aborted call may
legitimately have no partial. Everything raised here has a complete outcome and is refused for a
single missing field, so it was the one case guaranteed to have evidence and the only one
reporting none.

What it cost is the distinction between "the runner forgot to upload the transcript" and "the
runner is broken and produced nothing" -- a one-line fix versus a rewrite, and the turns and
timing that tell them apart were exactly what got dropped.

Fixed by mirroring `CallAborted`: `outcome` alongside `faults`, passed at the raise, and
`call=self._call_summary(exc.outcome)` in the handler. `_call_summary` already returned None for
None, so no new null handling. The separate failure code stays untouched.

Two tests, and the second is paired on purpose: the handlers sit next to each other under one
rule, so a test naming only `CallEvidenceMissing` invites the next person to fix one and not the
other. That paid off immediately -- of the four mutations, dropping `CallAborted`'s partial is
caught **only** by the paired test. The others: restoring `call=None`, dropping the outcome at
the raise site, and collapsing the distinct failure code.

Seventh in a row of one shape. Worth being precise about the variant, though, because it is not
quite "fails open" like the others: this one fails *closed* and then discards the reason. The
receipt is correctly marked errored. What is lost is the evidence that makes the error
actionable, which is the same harm arriving by the opposite route.

## A crashed review returned the value that means "approved"

The cleanest instance of the pattern, because here the empty value is the *documented* success
signal. `submit_gaps` asks for an empty list when the suite covers what it should, so `[]` means
"reviewed, and this suite is complete". The exception handler returned exactly that, and logged
nothing.

The caller makes it concrete. The top-up loop runs only while the suite is below target, so
review is the mechanism for reaching `wanted`:

    missing = await gaps_in(...)
    if not missing:
        break

Ask for 20, have the slices produce 12, and let the review session hit a transient model error:
`gaps_in` returns `[]`, the loop breaks, and a 12-scenario suite is saved and reported as the
finished product. No warning, no event, nothing anywhere saying the review never happened. The
operator concludes the writers found only 12 worth writing. That lands directly on the open work
to get suites to 20-30.

Fixed with a `SuiteReview` carrying `reviewed` and `gaps`, which is `RuntimeToolVerdict` again in
a file that did not have it, down to the `complete` property existing so that "no gaps" cannot be
read without also asking whether anyone looked. The intent behind the original catch is preserved
exactly: the suite as written is still kept and a failed review still does not take the run down.
What changed is that it no longer counts as approval, and the remaining rounds are tried rather
than abandoned, which folds in the retry the review suggested at no extra cost since the loop was
already bounded. The empty-suite early return is the same state and now says so too.

Five mutations, all caught, including the exact revert asked for and one on `complete` itself.

## Sweeping the other twelve

Assessed each against the one question worth asking: can a caller tell this apart from the
success value? Two are genuine, both now fixed to say what happened without changing behaviour:

- `transports.declared` returned `{}` for a `transport.json` that exists and will not parse,
  which is identical to no declaration at all. After the last two passes that costs more than it
  used to: the runner the build stage wrote is silently ignored, resolution falls through to
  recognition or fails naming no declaration, and the evidence contract goes with it. `is_file`
  has already separated the two states by the time the parse fails, so nothing was ambiguous
  except the return value.
- `peek_secret_values` returned `()` for an unreadable secrets file, the same value as "no extra
  values to scrub". The cost is not a disabled feature but a quietly weaker one: outbound
  redaction runs without the values it was supposed to strip, so the failure mode is a secret in
  an event or a log.

The rest I am satisfied with. `:516` and `:593` already warn. `:916`/`:922` record the channel
error before returning None, so nothing is lost. `:183`, `:491` and `:1477` conflate absent with
malformed, but each degrades into a typed failure or a normal default downstream rather than into
a false success, and warning on all of them would dilute the two above. `probe_voice_providers`
returning 0 is a probe reporting an unreachable host, which is what its caller reads it as.

One I am flagging rather than changing: `hosted_scheduler.py:1542` returns `""`, meaning "no
fault", when `verify_runtime_tools` raises, and marks the world verified so it is never
re-checked. It does log. But `RuntimeToolVerdict` exists precisely so that "not checked" cannot
read as "ok", and catching the exception one layer up reintroduces that at the scheduler. Whether
an unverifiable world should fault its scenario is a policy decision about run resilience rather
than a defect, so it is the coordinator's call, the same as the CUA precedence question.

## The stage that reads the customer's agent had been given a shell

Not deliberate, and the history says so plainly. `e53b800` renamed `read_only_session` to
`working_session` and widened it in the same commit. The diff to `understand.py` is nothing but
the rename:

    -from .config import artifact_dir, load_skill, read_only_session
    +from .config import artifact_dir, load_skill, working_session

The shell was meant for the build stage. Build constructs its own `SessionSpec` and never calls
the helper, so the only stage that actually received `Write`, `Edit` and `Bash` was the one stage
that must not have them. The docstring I wrote to justify the grant describes a stage that builds
infrastructure and proves it answers, and that caller does not exist.

`understand` runs with cwd on the customer's own source, and its skill never asks for any of it:
160 lines, one `submit_contract` at the end, and the single mention of running a command is
reading an install command out of a lockfile to record it. With an editor there it could tidy an
import or run a formatter while characterising the agent, and the contract would then describe
something nobody shipped. Every later stage is built from that contract and nothing anywhere
records that the subject was touched. It is the one stage where mutating its input silently
invalidates all the work that follows.

Reverted: the helper is `read_only_session` again, narrowed to the read-only three plus the
question, and the docstring now describes the stage that actually calls it. A source needing more
still says so through `extra_builtins`, per source and visible at the call. Nothing was using that
for anything mutating, so nothing broke.

This is my own argument from the pass before, one file over. I wrote that the grant is the
boundary and not the sandbox, which means a grant wider than the stage's own skill is the entire
exposure, and I had left the widest one on the stage whose input everything else depends on.

The guard is on the grant rather than on which helper is called, because a name is what failed
here. The sweep now recognises a session by the `system_prompt` argument every builder takes
instead of by a list of names, so the same drift cannot arrive again behind a rename. Three
mutations, all caught: widen the helper, grant `Bash` inline at the call, and swap `understand`
onto a brand new wide helper added by somebody else.

## A principled refusal reported as a crashed sandbox

Found on the first ever run of the generate path (job `3a86806e`, archive source). The environment
stage declined to build, correctly and with the best failure message this system produces: one
line per tool naming what the repository must expose. It reached the control plane as

    code = "guest_crashed", domain = "infrastructure"

Neither is true. Nothing crashed; the guest reasoned, declined, explained and stopped cleanly. And
the domain is the submitted agent, whose repository ships no runnable seam.

The mechanism runs through both repos. `require_buildable` raised a bare `RuntimeError`;
`cli._build` caught it, printed it and returned `1`, and from that point the reason existed only
as an exit status. The guest runs `authoring && bundle && run` as one shell chain, so a non-zero
authoring exit short-circuits it and `hosted_entrypoint`, the only component holding an outbound
channel, never starts. No terminal event is ever sent, and the gateway synthesises
`guest_crashed`/`infrastructure` from the exit code alone.

**The cost is not only the label.** Defaults are `retryable_domains = ["infrastructure",
"connectivity"]` and `max_infrastructure_attempts = 2`, so a refusal that will be identical every
time gets a second sandbox to re-derive it. Confirmed by reading `_should_retry`, which is only
reachable on the three `not terminal_event_received` paths. So delivering a terminal event fixes
the label and the retry together, with no separate retry change: `agent` is not in the retryable
set.

Fixed entirely inside ALK. `EnvironmentNotBuildable` carries its problems as data; the refusal is
recorded to `environment-refusal.json` because the deciding stage and the reporting process are
different processes with only an exit status between them; and the authoring entrypoint builds an
outbound channel and emits the terminal event itself, since nothing downstream will.

One contract detail worth knowing: `TerminalFailure` is `{domain, stage, code, message}` with
`extra="forbid"`, so there is no `details` to put the remedy in. It travels in `message` or it
does not reach the operator.

Five mutations caught; one survived first time and mattered. Testing the emitter and the recorder
separately left `main()`'s wiring uncovered, so "the stage declined and nobody looked for the
decline" passed clean. That is the same silence the fix exists to remove, reproduced inside my own
test suite. Three tests now cover the wiring.

## The generate path required a file called agent.py

From the first successful generate-path build (job `93400f09`). The harness authored a contract,
built a world for an agent shipping no Compose file, no Dockerfile and no data store, seeded it,
drove the repository's real code (`get_note: refused - this note is not yours`, the fixture's own
ownership rule executing), rejected a vacuous check for grading nothing, and reached a 14-check
catalogue. Then the bundler killed it, because the repository had no `agent.py`.

    entry = "agent.py"
    if not (root / entry).is_file():
        candidates = sorted(root.glob("**/agent.py"))
        if len(candidates) != 1:
            raise BundleAuthorError("component_ambiguous: expected exactly one agent.py")

Two stages disagreeing about what a runnable agent is. When the environment stage refuses it tells
the operator to "expose the real implementation as an importable callable or an HTTP service" --
a statement about seams. Nothing anywhere asks for a filename, and no skill documents one. It is
also the single convention that most undermines "a new agent type is a skill file, not a code
change": a repository can satisfy every documented requirement and fail on a name.

The contract already had the answer. That run's `runtime` block carried

    command   = ["uvicorn", "notesagent.app:app", "--host", "0.0.0.0", "--port", "8080"]
    interface = {kind: http, port: 8080, path: /chat, health_path: /health}
    install   = "pip install -e .", language = "python", version = ">=3.11"

`Runtime.command`'s own definition says it "is optional when one conventional entrypoint can be
proven from source" -- the precedence was documented and inverted. So this was not a design
question, it was a lookup that should never have existed.

Worth checking rather than assuming, and the answers changed the size of the job in both
directions: `resolve_environment_plan` did **not** receive the contract, only a `contract_modality`
string, which is why it globbed. But its only production caller already parses the whole
`contract.json` and keeps one field of it, so threading the runtime through was one argument, not
a refactor. And nothing downstream needs `component` to contain `agent.py`: it is the working
directory for build and run, so it needs the dependency manifest, not a named script.

Now: a declared command wins and is rewritten into the environment the build actually creates
(`uv run --no-sync` for a uv-synced project, `.venv/bin/...` for a requirements one, because the
submitted command assumes its own machine where its dependencies are on PATH). The filename search
survives as what it always should have been, a fallback, widened past `agent.py` and resolving the
component to the directory owning the manifest rather than the one holding the script, since a
package layout separates them.

The eleventh instance of the one shape came with it: `len(candidates) != 1` reported **zero**
candidates as `component_ambiguous`, telling an operator to disambiguate something that does not
exist. Split into `entrypoint_undeclared` and `entrypoint_ambiguous`, both naming `runtime.command`
as the remedy.

And the skill now requires what the bundler consumes. It asked for the *install* command, the
language and the ingress, but never the *start* command; the model recorded one anyway on this
run, so nothing guaranteed it. Five mutations caught, including reverting to the glob.

## Credentials, and a risk this experiment created

Granting the build stage a shell was only safe to reason about while it could not read anything
sensitive. The sandbox holds live secrets (`/run/futureagi/secrets.json`, LiveKit, Deepgram,
Cartesia, and a Google service-account private key), and a model with `Bash` can now print any of
them. Anything printed reaches the guest log, which is captured into the run artifacts and outlives
the sandbox, so one debug `echo` leaks a live key permanently.

Scrubbing on the operator's side is a net, not a fix, so the instruction is now in the skills: a
named **Credentials** section in `build-environment/SKILL.md` with the symptom attached, and the
same rule in the three references where credentials actually appear (`voice-livekit.md`,
`_writing-a-runner.md`, `voice-hosted-platform.md`). Report the variable and the status code, never
the value.

Audit of what this experiment had already written: no credential literals, no `echo $VAR`, no
placeholder shaped like a real key. One latent risk found and fixed in
`scripts/probe_voice_providers.py`, which bound the HTTP response body it never used. A provider
error can quote the credential you sent it, so it now returns a status code and nothing else.

# What still requires a human

Honest list. Each item says why it is still there and what would remove it.

1. **Credentials for a hosted assistant.** An assistant id, API key or phone number cannot be
   inferred from a repository. The harness asks via `AskUserQuestion`. This one is irreducible:
   it is a secret the operator holds.
2. **The `http_tool` wire format.** `HostedWorld.call()` raises by design because the shape is not
   pinned by any contract. Until it is, the hosted lane cannot execute the agent's own tools from
   the scheduler, so `verify_runtime_tools` reports "N tools go ungraded" rather than proving them.
   Removing this needs a decision about the seam, not code.
3. **Stage entry is still sequential.** The doctrine and the backtracking rule are written and the
   validated boundaries are the checkpoints, but the entrypoint still calls stages in order. A true
   single loop with model-driven phase re-entry was not attempted inside the timebox. What exists
   is re-enterable in principle (each stage reads its inputs from disk) and not yet driven that way.
4. **Voice remains the only transport with a runner in this repo.** Vapi, Retell and Bland have
   references and a declaration mechanism, and no shipped runner. The next one written proves
   whether `_writing-a-runner.md` is sufficient; until then it is untested guidance.
5. **The five non-voice build references are shallow.** They route correctly and carry selection
   checks, but only `voice-livekit.md` has the depth (170 lines, real code, footguns with symptoms)
   that makes a skill usable by a cold model.
6. **~~Nothing here has been exercised by a live run.~~ Corrected 2026-08-30.** Job `2b213927`
   ran this branch end to end on the hosted lane: queued through generating_environment,
   generating_scenarios and running to completed, with a receipt carrying 14 turns, 138657ms, a
   transcript, four recordings and `failure=null`. Nine of ten sub-goals held; the one that did
   not, `sends_confirmation_sms`, is a real gap in the agent under test rather than a harness
   fault, which is the outcome this whole apparatus exists to produce. What remains unproven is
   narrower: the shell has still not been exercised by a model writing a runner for a transport
   nobody has implemented, and the five non-voice build references remain unexercised.

## For the operator to check on the next run

Please confirm, and send me the evidence if any of these do not hold:

- A LiveKit job still resolves its runner and completes exactly as before. The resolution path is
  new; the runner is not.
- The authoring log contains `world N: M runtime tools go ungraded` naming the tools. That is the
  gate reporting honestly rather than passing silently.
- No receipt is rejected with `CallEvidenceMissing` on a normal voice run. If one is, the message
  names exactly what was missing and that is the bug report.
- `endCall accepted after N messages` still appears, confirming the WARNING-level diagnostics
  survive the changes.
