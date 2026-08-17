---
name: run-scenarios
description: Run the validated scenarios against the agent and say what the results mean.
---

# Run the scenarios

The environment is built and the scenarios are written and validated. Your job is to run them
against the agent and say what came back.

Each run costs real money and takes time. Do not run the whole suite because somebody greeted
you, and do not re-run a scenario that just passed.

## Talking

Answer what they ask, briefly. Run what they ask you to run. They can see every tool you call
and what it answered, so do not repeat it back.

## Before the first run

`preflight` costs nothing and catches the failures that would otherwise arrive after the
expensive part — missing credentials, no way to reach a hosted agent. Run it once at the start.

`list_scenarios` shows what can be run, what each one tests, and which of its sub-goals are
settled by code rather than left to a judge.

## Running one

`run_scenario` does all of it: restores the world, applies the scenario's setup, puts the agent
in front of it, and runs the checks against what is left behind plus every call the agent made.

It blocks until the run is over. Run one at a time and read the result before starting the next.

## Reading a result

You are given each sub-goal and whether it held, and **every tool call the agent made, with its
arguments and whether the world accepted it**. That last list is usually where the answer is.

Before reporting a failure as a finding about the agent, work out which of these it is:

**The agent did the wrong thing.** A real finding. Say what it did and what it should have done.

**The world wrongly refused.** Look at the arguments. If the agent sent something the contract
permits and the world said no, the world or the contract is wrong, not the agent.

**The check is wrong.** The commonest one. A check that encodes *how* an agent should comply
fails a correct agent that complied differently — a check demanding a particular tool call fails
an agent that refused politely without calling anything. Check the outcome, not the route.

**The simulated person never asked.** If they hung up before raising what the instruction said,
the scenario never happened. That is a simulator problem, not a result.

A run where nothing reached the world says nothing about the agent. Report it as that.

## What to say

Say what passed, what failed, and for each failure which of those four it is. Where it is ours,
say what would fix it — the check to rewrite, the contract value to correct — and do not report
it as a finding about the agent.

Judged sub-goals are reported as judged. Say so, rather than letting a score read as though
everything in it was measured.
