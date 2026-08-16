# The harness: what it builds and how it proves it

The reference for the rebuild. Written after the corrections in `_scenario-generation/context/`
7.2, 8.1, 9.1, 10.1 and 12, and it supersedes anything in the code that contradicts it.

---

## The model, in one paragraph

The **environment step** builds everything that is common to every test of one agent: the world
its tools act on, the prompt that drives a simulated user if it has one, and the catalogue of
sub-goals it can be checked against. Every **scenario** is then only a *delta* on that base — a
few values changed after reset, the values substituted into the simulator's prompt, and which
sub-goals must hold. Nothing about a scenario is a template with slots; the harness writes each
one, and proves it works before keeping it.

```
environment step  ─────────────────────────────►  base: world + simulator prompt + sub-goals
                                                          │
scenario 1 ──► reset → setup delta → run → check ─────────┤
scenario 2 ──► reset → setup delta → run → check ─────────┤
scenario N ──► reset → setup delta → run → check ─────────┘
```

---

## 1. The environment step

> *"You have to first understand what the f\*\*\* this agent is, from that you will create
> databases, you'll create a snapshot of the databases first."* — Nikhil, 12

It produces four things. All of them are written by the harness. None are hardcoded here.

### 1.1 The world

Whatever **this** agent needs, and nothing more. For the drive-thru agent that is a database.
For a browser agent it is a site. For something else it is a filesystem, a queue, a service — the
harness decides from the contract what has to exist.

It **subclasses ALK's `EnvironmentAdapter`**, so the runners that already exist can drive it:
`reset` publishes the tools and the starting state, `handle_tool_call` executes one call, and the
state afterwards is what gets graded. Nothing the harness writes should re-implement a runner.

It is **frozen once** as a snapshot. Every scenario restores from that snapshot, so a run is
repeatable and no scenario can inherit another's leftovers.

### 1.2 The simulator prompt — only where the agent is conversational

> *"For voice and chat there is a simulator, and the input is an instruction to that simulator
> rather than an input to the agent under test. Where there is no actor, variability comes from
> how the environment is designed."* — 10.1 §4

The harness writes one prompt for the simulated user of **this** agent, with variables left open.
Each scenario supplies the values. The prompt is an artifact of the environment step because it
is the same for every scenario; only the substituted instruction differs.

There is no persona field and no persona library.

> *"Drop the gimmicky persona characters. Variability comes from real conditions instead: a new
> versus an existing user, whether a payment method is on file, addresses."* — 8.1

For a browser or coding agent there is no simulator at all; the instruction goes to the agent
directly.

### 1.3 The sub-goal catalogue

> *"Defining the sub-goals is our call. The important property is that they are common across
> scenarios so the results roll up: if a payment step appears in 50 scenarios, the analytics
> should show where payment fails and how often."* — 10.1 §8

Defined **once**, here, as a named list. Scenarios reference them; they do not invent their own
wording. That is what makes `order-confirmation fails in 7 of 12 scenarios` a sentence anyone can
say. Each entry carries its own check (see §3).

### 1.4 The gate

The environment is not accepted because it looks right. It is exercised: every tool called with a
valid call, a nonexistent id, and a missing argument; sequences where state has to carry across
calls. **A refusal is the environment working; a crash is a defect.** It cannot be saved dirty
(rows left over from building) or unverified.

---

## 2. A scenario is a delta

```
name         identifier
use_case     which branch of the agent's real use cases this belongs to
setup        what changes in the world after reset — a few rows, files, state
instruction  the task. For a conversational agent this is substituted into the
             simulator prompt; for a browser or coding agent it goes to the agent
solution     the reference trajectory: what a correct agent would do
sub_goals    which catalogue entries must hold, and what each must show
```

Gone from the old shape: `persona`, `opening`, `goal`, and free-text `must` / `must_not` as the
primary grading. Scenarios are organised **use case → branch**, not by adversarial flavour.

> *"A login flow is not one row with happy/edge inside it; it is many rows: login-with-Google,
> login-with-Microsoft, forgot-password, sign-up-with-email."* — Nikhil, 7.2

---

## 3. Checks: deterministic by default, judge as the fallback

> *"When you have `==` or a python script, then I'll call that deterministic."*
> *"Most likely we can make things deterministic."*
> *"Deterministic, if possible. And LLM also, obviously."* — Nikhil, 10

| | |
|---|---|
| **Deterministic** — an assert, an equality, **a python script** | The default |
| **Non-deterministic** — an LLM judging whether a sub-goal was met | Only where nothing observable settles it |

The trap, in his words: *"you are judging by LLM [so it is non-deterministic], but if you want an
exact output to be 50, then that is deterministic."* An exact fact checked by a judge is **still
non-deterministic**. What matters is who decides, not how precise the fact is.

A check is code the harness writes, and it has two observable things to work from:

1. **the world afterwards** — rows, files, whatever this environment is
2. **the recorded tool calls** — that the call happened, *and with the right arguments*

That second one answers the question left open in 7.2: a booking made for 10 PM when 11 PM was
asked for is a failure, and it is deterministic to detect.

The judge is left only with what leaves no trace: whether a refusal was explained, whether a price
was invented, tone.

> Warning from the previous run: *"Judge checkpoints, about a third of all checkpoints, are
> returned as skipped and not graded."* Leaning on the judge does not merely weaken a result — it
> silently produces holes.

---

## 4. Two gates on every scenario, before it is kept

Terminal-bench's oracle run, which is the reason its tasks are known to be solvable.

### Gate 1 — solvable

```
reset → apply setup → run the solution → run the checks   ⇒ must PASS
```

If the checks fail with the reference solution, either the scenario is impossible or the check is
wrong. Both have already happened here: a scenario asserted a value the agent was never permitted
to send, and another demanded confirmation of an item that could not be ordered. This catches
them at write time, with no model involved.

### Gate 2 — not vacuous

```
reset → apply setup → run NOTHING → run the checks        ⇒ must FAIL
```

A check that passes without the agent doing anything grades nothing while reporting a result.
This is the failure that makes a suite quietly green.

Neither gate asks a model anything. The environment decides.

**Three things fall out of the solution for free:** it is the expected trajectory; comparing the
agent's trajectory against it gives efficiency (Nikhil's point about the agent that succeeds on
the 21st call after 20 failures); and a scenario that cannot be run is caught before a call is
ever placed.

---

## 5. Running it

> *"Use an existing harness. Just for Claude agents. Use any existing harness that is there."*
> — Nikhil, 12

The simulation runs through **ALK's own path**, not a loop written here. The world is passed in
as the environment; the agent under test is the real agent, in its real runtime. For the voice
case that means the Vapi assistant we already have, over LiveKit, with the tool webhook answered
by **our world** rather than by canned mocks.

That last part is the whole point of the environment. The previous run's known issues were:

- *"Mocked tools always succeed, including removing an item that was never added."*
- *"Mock responses do not vary by argument, so read-after-write flows are wrong."*
- *"World state does not change unless a scenario sets `state_updates`, which is often empty."*

A world that really holds rows and can really refuse removes all three.

---

## 6. What changes per kind of agent, and what does not

| | Voice / chat | Browser | Coding |
|---|---|---|---|
| World | database, KB | a site | a filesystem, a repo |
| Simulated user | yes — prompt written by the harness | none | none |
| Instruction goes to | the simulator | the agent | the agent |
| Solution | tool calls | actions | commands |
| Check | code over world + calls | code over the page + actions | code over the tree |

**What never changes:** the environment is built once and frozen; a scenario is a delta; a
solution proves it is solvable; a check that cannot fail is rejected; deterministic first.

---

## 7. Order of work

1. **Environment step** — the world, the simulator prompt, the sub-goal catalogue, all written by
   the harness rather than by a fixed schema here.
2. **Scenario shape** — setup / instruction / solution / sub-goal references.
3. **The two gates** — solvable, and not vacuous.
4. **Run through ALK** — the world serving the tool calls of the real agent.

---

## 8. Instructions, not code

> *"This is a flow, this is not a harness. You will give your harness the instructions that you
> are supposed to do all this and then the harness will do all that. It's not a code that your
> harness follows."* — Nikhil, 12

Every stage's method lives in a `SKILL.md`, editable without touching code. What stays in code is
only what must be exact: executing a call, restoring a snapshot, running a check, and refusing
something that does not hold up. **The harness decides what to do. Code decides what is true.**
