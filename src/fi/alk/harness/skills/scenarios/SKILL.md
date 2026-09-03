---
name: scenarios
description: Build a suite of tests for an AI agent, planned before it is written and proved before it is kept.
---

# Scenarios

You are building tests for an AI agent, and you are doing it mostly through writers you brief
rather than alone. Two jobs, then: decide what is worth testing, and get writers to produce it
well. The environment already exists: a world the agent's tools really act on, a prompt for the
person it talks to, and a catalogue of named sub-goals with checks.

Two things are true of every suite, whatever its size.

**Nothing is kept unless it is proved.** Every scenario passes three gates before it is saved: the
world is ready for it, a reference solution passes its checks, and those checks fail when nothing
is done. The gates are code and they are not negotiable. When one refuses, the scenario is wrong
or the checks are wrong. Work out which and fix that, rather than working around it.

**A suite is judged on what it would catch, not on how many rows it has.** Fifty scenarios that
find fifty different ways this agent breaks are worth more than a thousand that find the same
thing repeatedly. That decides most of the judgement calls below.

## Read the agent before you do anything else

The contract is a summary, and summaries lose exactly what you need. The scenarios worth writing
come from the agent's own source: what its handlers refuse and under what conditions, what its
data already contains, which paths carry a comment admitting something, where two fields could be
confused, what happens at a boundary.

You have full read access. Use it properly rather than skimming: `Read`, `Grep`, `Glob`, `Bash`.
An hour spent reading the agent is repaid many times, because it is the only thing that produces a
scenario nobody who built the agent had thought of. That is the bar.

Nothing here hands you a list of scenario types to work through. A list produces the scenarios on
the list and stops, and the ceiling is then ours rather than the agent's.

## What makes a scenario worth having

**It can fail.** If the agent cannot plausibly get it wrong, it is a demonstration, not a test.
The interesting ones sit where the agent must choose: two readings of the same request, a
precondition it should check and might not, a state that makes the obvious action wrong.

**It fails for one reason.** When a scenario can fail three ways, a red result tells you nothing.
Vary one thing against a background you control.

**The person behaves like a person.** They change their mind, arrive with the wrong information,
answer a different question, go quiet. A caller who recites exactly what the agent needs, in
order, is testing nothing but the happy path. This is where scripted-sounding suites come from,
and it is the most common way a large suite turns out worthless.

**The caller does not know the agent's rules, so never write them into the instruction.** Giving
the caller the expected agent behaviour and telling them to accept it is the most common way a
scenario stops testing anything: a compliant agent and a lucky one then look identical. Write what
the person wants and how hard they will push for it, and let the agent's behaviour be the thing
under test.

> Not: "if the assistant says card details cannot be read aloud, agree to receive a payment link."
>
> Instead: "you would rather just read the number out. If refused, you are mildly annoyed but you
> will use another method."

The caller also does not know they are in a test. An instruction that says "see whether the
assistant will refuse" breaks the frame and belongs in what the scenario claims to test, not in
what the person is told.

**It is grounded in this agent's world.** Real record ids, real balances, real prices. An invented
id fails the first gate; a plausible-but-absent one produces a test of error handling you did not
mean to write.

**It is not another dressing of one you already have.** The same test with a different name is
worse than nothing: it inflates the count and hides the gap it should have shown.

## Write the check that would catch the failure

A scenario is only as good as the thing that decides whether it passed. The usual failure is
quiet: the check asserts that a step *happened*, while the rule being tested is about *order* or
*values*. Then an agent that did the wrong thing in the wrong order still passes, and the suite
reports green while testing almost nothing.

A check is `def check(world, calls)` returning `None` to pass or a sentence saying what was wrong.
Each call carries its name, its arguments, its result, whether it succeeded and when it happened, and the list is in order. So order
and values are both available. Use them.

**If the rule says "before", assert the order.** "Verify before charging", "quote the fee before
cancelling", "read back before booking".

This is the one most often got wrong, and the wrong version looks right. A check that gathers two
calls and asserts each happened is testing occurrence, and an agent that did them in the forbidden
order passes it:

```python
# WRONG for an ordering rule: passes even when the card was charged first
def check(world, calls):
    if not [c for c in calls if c.name == "send_otp" and c.ok]:
        return "send_otp was not called"
    if not [c for c in calls if c.name == "verify_otp" and c.ok]:
        return "verify_otp was not called"
    return None
```

If your check never compares two positions, it cannot be testing an order. Compare them:

```python
def check(world, calls):
    ok = [c for c in calls if c.ok]
    verified = next((i for i, c in enumerate(ok) if c.name == "verify_otp"), None)
    charged = next((i for i, c in enumerate(ok) if c.name == "select_payment_method"), None)
    if charged is None:
        return "no payment method was selected"
    if verified is None or verified > charged:
        return "selected the payment method before verifying"
    return None
```

**If the scenario names a value, assert the value.** A destination, a tier, an amount, a code. A
check that only asks whether the tool was called cannot tell the right answer from the wrong one,
which is the whole point of naming it.

**If the scenario is about a refusal, assert the refusal.** This is where suites are weakest,
because "nothing bad happened" is easy to leave unwritten. Two ways, and prefer the first:

- **Positively**, by asserting what should have happened instead. An agent that granted the
  request would not also have transferred to a human or asked for the real code.
- **By absence**, when there is no such trace: assert the forbidden call did not happen, or
  happened without the injected argument.

An adversarial scenario whose checks only cover the steps taken on the way in is decorative. If
the agent could comply with the attack and still pass, the check is not testing the scenario.

**Look at the world, not only at the calls.** The world was built, seeded and frozen so it can be
inspected afterwards. `world.state()` tells you whether the booking row exists, whether the status
really changed, whether the balance moved. A call having been made is not the same as the world
having changed.

**Refine a catalogue sub-goal when the scenario deserves it.** The shared catalogue is what makes
results roll up across a suite, so keep using it. Where a scenario asserts something of its own,
add a check of its own beside it rather than leaving the generic one to stand for both.

**Some rules cannot be settled by code**, tone, turn length, saying one thing at a time. Those
belong to a judged sub-goal that names the rule. Do not fold them into a catch-all, and do not
pretend a coded check covers them.

## Planning, when the count is more than a couple of dozen

Decide what every scenario is, one line each, before any of them is written.

`show_grid` gives the space to cover, derived from tool names and a data schema. Check it against
the source and correct it with `set_objects` first: if it missed an object, split one in two, or
turned an action into a thing, everything planned on top inherits that.

`plan_suite` proposes an arithmetic spread across the grid. Treat it as a suggestion. It cannot
know which cells are dangerous in practice, where real users spend their time, or which operation
you have just read and know to be fragile. Take what fits, drop what does not, add what it missed.

**Then `record_canvas`, because a plan you did not record does not exist.** It is the ledger every
later step reads: which buckets are filled, what a writer may claim, what coverage is measured
against, and what a stopped run resumes from. Skipping it is the easiest mistake here and the most
expensive. `show_canvas` reads it back a theme at a time.

A good plan is made of buckets that differ in *kind*, not in wording. If two buckets would be
briefed with the same sentence, they are one bucket.

## Briefing writers, which is most of what you do

`claim_slice` takes the next angles and marks them claimed so nothing is written twice.
`fold_return` takes back what a writer covered and reopens what it did not, one entry per angle
with its own count and a sentence on what was actually covered. A writer that returns nothing must
reopen its slice rather than silently consume it.

**Writers run at the same time, up to ten of them, and ten is the most there may be.** Brief them
together rather than waiting for one to finish before starting the next, and keep that many
working whenever the canvas has that much open. Use fewer only when the buckets left would
overlap, or when writers come back empty or refused, in which case find out why before claiming
more.

The quality of a slice is decided by its brief. A writer sees the coordinates you name and little
else, so:

- **Name the cells verbatim.** A writer that has to guess its scope writes something adjacent.
- **Say what the angle is for**, not just what it is called. "Caller gives an address that matches
  two saved places" produces a better test than "ambiguous address".
- **Hand it the callers**, a name, an accent and a location per scenario, distinct across the whole
  suite. Left to choose, every writer picks the same few names and the suite reads as one voice.
- **Say what has already been written nearby**, so it does not rediscover a scenario a sibling
  just wrote.
- **Ask for the scenario names back per bucket.** That is what `fold_return` checks against disk,
  and it is how you catch a writer that reported more than it produced.

## Proving, and what a refusal means

A gate that refuses is information, not an obstacle. Use `inspect_world` so a scenario names
records that exist, and `try_calls` to work out the reference solution before submitting. If a
proof reports a check is vacuous or broken, repair that sub-goal with `add_sub_goal` and resubmit.

Never evade a gate by deleting a check for behaviour the scenario still claims to test. A suite
that reports every gate green while holding scenarios whose solution never touched the world is
worse than a smaller honest one.

`save_scenarios` folds the journal into folders. A delegated writer journals rather than writing
folders, so anything asking what exists must read both.

## Finishing

`show_coverage` against the grid, so what was left untested is on the record rather than implied by
a count. `show_diversity` shows how the saved suite spreads and names any pair that reads as the
same test twice. `expand_suite` copies proved scenarios across caller conditions that do not change
the world, when more of the same situation under different people is what is wanted.

## Meet the number, or say why not

Give as much of what was asked for as genuinely exists, and work for it. If the agent really has
that many distinct things worth testing, find them.

If it does not, say so plainly and say what you exhausted. A suite padded to a requested number
with the same tests under different names looks like coverage and is not, and it is worse than the
honest smaller number because it hides the gap it should have shown.

This is a last resort, not an opening position. Stopping early because continuing was hard is a
failure. Stopping because you have genuinely run out is a result.
