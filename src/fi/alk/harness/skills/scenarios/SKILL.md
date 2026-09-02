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

## Which part you are doing

**Planning a suite** and the count is more than a couple of dozen: load the plan sub-skill.
Decide what every scenario is, one line each, before any of them are written.

**Writing scenarios**, whether all of them or one slice of a plan: load the write sub-skill.

For a handful of scenarios, skip planning and write them.

## Meet the number, or say why not

Give the person as much of what they asked for as genuinely exists. Aim at their number and work
for it. If the agent really does have that many distinct things worth testing, find them.

If it does not, say so plainly and say what you exhausted. A suite padded out to a requested
number with the same tests under different names looks like coverage and is not, and it is worse
than the honest smaller number because it hides the gap it should have shown.

This is a last resort, not an opening position. Stopping early because continuing was hard is a
failure; stopping because you have genuinely run out is a result.
