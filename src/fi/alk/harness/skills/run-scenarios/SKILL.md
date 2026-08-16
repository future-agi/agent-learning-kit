---
name: run-scenarios
description: Run the written scenarios against the real agent and say what the results mean.
---

# Run the scenarios

## Talking

You are talking to a person, not running a script. Answer what they ask, briefly. Run what they
ask you to run. Keep replies short — they can see every tool you call and what it answered.

Each call costs real money and takes minutes. Do not run the whole suite because somebody said
hello, and do not re-run a scenario that just passed.

## What happens when you run one

`run_scenario` does all of it: restores the world, applies the scenario's setup, stands up the
webhook, points the assistant's **own** tools at it, places the call through ALK's voice case, and
runs the sub-goals' checks against what the world holds afterwards plus the calls that were made.

It blocks for several minutes. Run one at a time and read the result before starting the next.

`preflight` first, before the first call of a session. It costs nothing and catches the failures
that would otherwise arrive after the expensive part.

## Reading a result

You get the sub-goals settled by code, the ones left to a judge, and **every tool call the agent
made, with its arguments and whether the world accepted it**. That last list is where the answer
usually is.

Before you report a failure as a finding about the agent, ask which of these it is:

- **The agent did the wrong thing.** A real finding. Say what it did and what it should have done.
- **The world refused a call the agent was entitled to make.** Look at the arguments. If the agent
  sent something the contract permits and the world said no, the world or the contract is wrong,
  not the agent.
- **The check is wrong.** The commonest one. A sub-goal that encodes *how* an agent should comply
  fails a correct agent that complied differently — a check that demands a refusal tool call fails
  an agent that refused from its own prompt without calling anything. Check the outcome, not the
  route.
- **The simulated caller did not do its job.** If the caller hung up before asking for what the
  instruction said, the scenario never happened. That is a simulator prompt problem.

A run where nothing reached the world says nothing about the agent. Report it as that, not as a
failure.

## What to say

Say what passed, what failed, and for each failure which of the four causes above it is. Where it
is ours, say what would fix it — the sub-goal to rewrite, the contract argument to correct — and
do not report it as a finding about the agent.

Judged sub-goals are reported as judged and not counted. Say so rather than letting a `2/2` read
as though everything was checked.
