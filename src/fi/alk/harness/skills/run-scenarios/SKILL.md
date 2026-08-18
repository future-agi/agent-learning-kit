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

## Running the suite

**`run_simulation` runs everything, once.** It restores a separate world for each scenario,
applies that scenario's own setup, puts the agent in front of it, and grades what is left behind
along with every call that was made. One call from you; the simulation owns the rest.

That is how a suite is run. Do not work through the scenarios yourself: a run made of one tool
call per scenario takes as many of your turns as there are scenarios, costs that much more, and
produces the same results slower.

Its concurrency argument is how many run at once. **Leave it at 1 for a spoken agent** — every scenario there
is a real phone call that costs real money and holds a real tunnel. For a typed agent, raising it
is the difference between the slowest scenario and the sum of all of them.

It blocks until the whole suite is done, which is minutes, and says so.

`run_scenario` still exists for looking into a single failure after the fact. It is not how you
get results.

## Looking into a run

`read_run` with no arguments lists the runs this session has done; given a run id it gives that
run in full, and given a scenario name as well it gives one case. A run holds the conversation, every
tool call with its arguments and what came back, what each check decided, and for a spoken run the
recording and what the call measured.

Runs accumulate. The same suite against the same world, run twice, is two runs you can compare —
which is the point of keeping them rather than overwriting.

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

Say what passed, what failed, and for each failure which of those four it is. Where the fault is on the test's side,
say what would fix it — the check to rewrite, the contract value to correct — and do not report
it as a finding about the agent.

Judged sub-goals are reported as judged. Say so, rather than letting a score read as though
everything in it was measured.

A sub-goal whose kind is "eval" was decided by a named eval on the FutureAGI platform rather than by a
model in this process, and its result names the eval that decided it. Report that name: it is
something the person can open, re-run and change, which a verdict reached here is not.
