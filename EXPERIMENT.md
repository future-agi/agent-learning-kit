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
- **`verify_runtime_tools` is written and imports, but is not wired into the hosted pipeline.**
  The scheduler does not carry the contract, and `HostedWorld` exposes only the database surface:
  it has neither an agent-tool endpoint map nor a worker invocation/evidence bridge. Calling it
  safely needs all three, not a generic HTTP request guessed from a tool name. Until that contract
  and bridge exist, the behavioural gate is a function nobody calls. **This is the most important
  gap: the autonomy is in and the gate that makes it safe is not.**
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
6. **Nothing here has been exercised by a live run.** The shell, the transport resolution, the
   receipt boundary and the runtime-tool gate are all statically verified and unit-tested only.

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
