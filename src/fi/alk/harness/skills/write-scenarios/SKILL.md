---
name: write-scenarios
description: Write scenarios as deltas on the built environment, each proved before it is kept.
---

# Write the scenarios

## Talking

You are talking to a person, not running a script. Answer what they ask, briefly. Do the work
when they ask for it. Keep replies short — they can see every tool you call and what it answered.

## What a scenario is

The environment is already built: the world, the simulator prompt, the catalogue of sub-goals. A
scenario is only a **delta** on that base.

```
name         short identifier
use_case     which branch of the agent's real use cases this belongs to
setup        what changes in the world after reset — a few rows
instruction  the task. For a conversational agent it fills the simulator prompt's slot
variables    any other slot that prompt asks for
solution     what a correct agent would do: [{tool, arguments}]
sub_goals    names from the catalogue that must hold
```

There is no persona and no opening line. **Variability comes from real conditions**, which live
in `setup`: the item is out of stock, the customer already exists, the order already has three
items in it. Not from invented characters.

## Organise by use case, then by branch

A login flow is not one row with happy and edge cases inside it. It is many rows:
login-with-Google, login-with-Microsoft, forgot-password, sign-up-with-email. Do the same here:
find the agent's real use cases, and let their branches be the scenarios.

Different outcomes are different scenarios. The customer who accepts a substitute and the customer
who refuses one are two rows, not one.

## The solution is not optional

Every scenario carries what a correct agent would do. It is the only way to show the scenario can
be passed at all, and it is checked before the scenario is kept:

- Your solution is played through a fresh world, and the checks of your sub-goals must **pass**.
- The same checks are then run with nothing done at all, and must **fail**.

If the first fails, either the scenario is impossible or the sub-goal's check is wrong. If the
second fails, the checks grade nothing and the scenario would report a result nobody should
believe.

Work the solution out with `try_calls` before you submit. Run the calls, look at the state they
leave, and confirm the sub-goals you are naming actually respond to it.

## Reuse the sub-goals

Name entries from the catalogue. Do not restate them in your own words, and do not invent a new
one where an existing one means the same thing — the whole point is that "confirms the order back"
is the same sub-goal in every scenario, so the results can be added together.

If something genuinely needs checking and no entry covers it, add one with `add_sub_goal`, with
its check in code. Prefer code over a judged check: you have the world afterwards and every call
with its arguments, and most things worth checking are visible in one of them.

## What makes a suite worth running

Spread across these. Ten happy paths tell you nothing you did not know.

- **The ordinary branch**, done cleanly. You need a baseline.
- **The branch that cannot be completed**: the item is not there, the record does not exist, the
  option is outside what the tool accepts. The right behaviour is to refuse clearly and offer
  what is possible.
- **The rule under pressure**: the customer pushes for something a hard constraint forbids, twice.
  Giving way under pressure is the failure most worth catching.
- **State that has to carry**: add, change your mind, remove, confirm. The agent has to know what
  it did two turns ago.
- **The same use case with the world seeded differently**: in stock and out of stock are two
  rows, not one.

## How to work

1. `inspect_world` with no table, then look at the ones that matter. Read the sub-goals already
   defined.
2. Read the contract's hard constraints. Each is a branch waiting to be written.
3. For each scenario: work out the solution, `try_calls` it, then `submit_scenario`.
4. Read what comes back. A refusal tells you exactly what could not be proved.
5. `save_scenarios` when you have the number that was asked for.

## Finishing

Say what the suite covers and what it does not, which sub-goals carry the most scenarios, and name
anything you could not test because the environment or the contract does not support it.
