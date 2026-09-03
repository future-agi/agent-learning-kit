---
name: scenarios
description: Build a suite of tests for an AI agent, planned before it is written and proved before it is kept.
---

# Scenarios

You are building tests for an AI agent. The environment already exists: a world its tools really
act on, a prompt for the person it talks to, and a catalogue of named sub-goals with checks.

Two things are true of every suite, whatever size it is.

**Nothing is kept unless it is proved.** Every scenario passes three gates before it is saved:
the world is ready for it, a reference solution passes its checks, and those checks fail when
nothing is done. The gates are code and they are not negotiable. When one refuses, the scenario
is wrong, or the checks are wrong; work out which and fix that, rather than working around it.

**A suite is judged on what it would catch, not on how many rows it has.** Fifty scenarios that
find fifty different ways this agent breaks are worth more than a thousand that find the same
thing repeatedly. This decides most of the judgement calls below.

## Read the agent before you do anything else

The contract is a summary and summaries lose exactly what you need. The scenarios worth writing
come from the agent's own source: what its handlers refuse and under what conditions, what its
data already contains, which paths have a comment admitting something, where two fields could be
confused, what happens at a boundary.

You have full read access. Use it properly rather than skimming: `Read`, `Grep`, `Glob`, `Bash`.
An hour of the suite's cost spent reading the agent is repaid many times over, because it is the
only thing that produces a scenario nobody who built the agent had thought of. That is the bar.

Nothing here hands you a list of scenario types to work through. A list produces the scenarios on
the list and stops, and the ceiling is then ours rather than the agent's.

## The words, so they mean one thing each

A **scenario** is one test: a folder, a setup, checks, a reference solution. The concrete thing.

A **bucket** is a kind of case that holds several scenarios. Its *angle* is what makes it worth
testing. Buckets are what a plan is made of, because "scenario" and "situation" both name single
instances and neither works as the container.

A **theme** groups buckets, and is the unit a large plan is read and dispatched in.

The **canvas** is the recorded plan: themes, buckets, how many each wants, and how far each has
got. It is also the ledger the run resumes from.

The **grid** is the space of everything this agent can be asked, derived from its contract. It is
what coverage is measured against.

## Which part you are doing

**Planning** decides what every scenario is, one line each, before any is written. Do it whenever
the count is more than a couple of dozen. **Writing** produces and proves them, whether the whole
suite or one slice of a plan. For a handful, skip planning and write them.

## Planning

1. `show_grid` — the space to cover. It was derived from tool names and a data schema, so check
   it against the agent's own source: if it missed an object, split one in two, or turned an
   action into a thing, correct it with `set_objects` before planning on top of it.
2. `plan_suite` — one arithmetic spread across that grid for a given count. **A suggestion, not
   an instruction.** It knows nothing about this agent: which cells are dangerous in practice,
   where real users spend their time, which operation you have just read and know to be fragile.
   Take what fits, drop what does not, add cells it did not choose, and say what you changed.
3. **`record_canvas` — a plan you did not record does not exist.** This is the step that is
   easiest to skip and most expensive to lose. Without it there is no ledger: nothing knows which
   buckets are filled, no writer can claim a slice, coverage cannot be reported against the plan,
   and a run that stops has nothing to resume from. Everything downstream reads the canvas, not
   your intention.

`show_canvas` reads it back, a theme at a time, with each angle's state.

## Writing

With a canvas: `claim_slice` takes the next angles and marks them claimed so nothing is written
twice, and `fold_return` takes back what a writer covered and reopens what it did not. Pass one
entry per angle with its own count and a sentence on what was actually covered — a writer that
returns nothing must reopen its slice rather than silently consume it.

Without a canvas, write the suite directly.

For each scenario:

- `inspect_world` so it names records that really exist. Invented ids fail the first gate.
- `try_calls` to work out the reference solution before submitting. A scenario is kept only if
  its solution passes its own checks and those checks fail without it.
- Keep every solution step's arguments exactly model-facing. If a dependency needs trusted fields
  the model never supplied, put its complete payload in the environment_arguments field; never pretend
  the model produced hidden state. Treat a contract phrase like "from this call" literally: the
  reference solution must create that state earlier in the same conversation.
- `submit_scenario` to put it through the gates. If a proof reports a check is vacuous or broken,
  repair that named sub-goal with `add_sub_goal` and resubmit. Never evade a gate by deleting a
  check for behaviour the scenario still claims to test.
- `inspect_scenario` before changing an existing one, so unchanged fields survive; `drop_scenario`
  removes one.

The person on the other end is part of the test. `name`, `personality`, `accent`, `languages`,
`communication_style`, `keywords` and `initial_message` shape the simulated caller. Vary them
deliberately: an agent that only ever meets one kind of person has only been tested against one.

Then `save_scenarios` to fold the journal into folders. A delegated writer journals rather than
writing folders, so anything asking what exists must read both.

## Finishing

`show_coverage` against the grid, so what was left untested is on the record rather than implied
by a count. `show_diversity` shows how the saved suite spreads, and names any pair that reads as
the same test written twice. `expand_suite` copies proved scenarios across caller conditions that
do not change the world, when more of the same situation under different people is what is wanted.

## Meet the number, or say why not

Give the person as much of what they asked for as genuinely exists. Aim at their number and work
for it. If the agent really does have that many distinct things worth testing, find them.

If it does not, say so plainly and say what you exhausted. A suite padded out to a requested
number with the same tests under different names looks like coverage and is not, and it is worse
than the honest smaller number because it hides the gap it should have shown.

This is a last resort, not an opening position. Stopping early because continuing was hard is a
failure; stopping because you have genuinely run out is a result.
