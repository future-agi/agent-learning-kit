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
contract. Four written for the build stage: `voice-livekit.md`, `voice-hosted-platform.md`
(Vapi/Retell, where their platform calls us), `browser-and-computer-use.md`,
`retrieval-and-assistants.md`. Each points at the ALK code to reuse rather than describing how to
rewrite it. Adding a kind of agent is now a markdown file.

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
| 3 tests in `test_harness.py` | they asserted the deny-by-default gate: `test_a_stage_may_use_nothing_it_was_not_given`, `test_a_tool_a_stage_was_not_given_is_denied_by_the_hook`, `test_every_stage_gates_with_the_hook_not_only_the_callback` |

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
Three source files deleted, four skills added.

## How to run it

Unchanged. `python -m fi.alk.harness.cli` as before, or the hosted path through the platform.
The build stage will now have a shell.

## Tests

Baseline on the branch point: **74 failed, 2789 passed, 38 skipped** (24m14s). Those failures are
pre-existing and concentrated in `test_config_and_facades.py` and `test_harness_architecture.py`.
Three tests were deleted as described. The fast structural suites (livekit engine, lane
equivalence, call runner) pass at 133.

## What is not proven

Being honest about this, because none of it has run in anger:

- **No hosted Daytona job has been run on this branch.** Everything below is unverified in a real
  run, and the shell in particular has never been exercised by a model.
- **`verify_runtime_tools` is written and imports, but is not wired into the hosted pipeline.**
  The scheduler does not carry the contract, so calling it there means threading the contract
  through, which is a bigger change than I was willing to make untested. Until it is wired, the
  behavioural gate exists as a function nobody calls. **This is the most important gap: the
  autonomy is in and the gate that makes it safe is not.**
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

1. Wire `verify_runtime_tools` into the hosted run after the world comes up. Without it this
   branch grants autonomy and removes the check that made it defensible.
2. Run one hosted job and read what the model does with a shell. That is the experiment.
3. Then the world handle, because it is what makes "any stack" true rather than aspirational.
