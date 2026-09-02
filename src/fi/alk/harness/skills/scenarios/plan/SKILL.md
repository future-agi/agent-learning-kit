---
name: plan
description: Decide what a suite will cover, as themes and angles, before any of it is written.
---

# Plan the suite before writing it

You are deciding what a suite covers. Not writing it: deciding. Then the writing happens against
that plan, one writer at a time, and the plan keeps score.

This step exists because the other two ways of reaching a large suite both fail, differently.

Asking for a thousand finished scenarios at once does not fit in a context and never will.

Writing them one at a time does fit and produces a worse suite than it looks like it should. Each
is composed with the last few in view, so the third resembles the second, the tenth resembles the
ninth, and by fifty the suite has settled into one shape. Nobody does anything wrong at any step.
Measured here: fifty scenarios contained nine distinct people, forty-two of them American, living
in two places, and every writer had been told to vary its work.

## Read the agent first

The good angles come from the agent's own source, not from general knowledge of what goes wrong
with software. Read the handlers, the data it starts with, the validation, the error paths, the
comments. You have `Read`, `Grep`, `Glob` and `Bash`, and an hour spent here is repaid many times.

What you are looking for is anything that creates a case the agent has to get right and might not:
a condition a handler refuses under, two records hard to tell apart, a field optional in one place
and assumed in another, an order of operations that matters, a value at a boundary, a state the
data can reach that the happy path never produces.

There is deliberately no list of scenario types here. Given one you would produce those types and
stop, and the ceiling would be the list's rather than the agent's.

## The shape of a plan

**A theme** groups related angles. It is also the unit this is read and dispatched in, so a plan
of any size stays workable: nobody ever holds the whole thing at once.

**A bucket** is one thing worth testing on one grid cell, and it holds several scenarios. Its
`angle` says what makes it worth testing, in a few words; its `want` says how many scenarios go in
it. Each bucket carries:

    id       stable, like TH04-13. Never rewritten, because progress is joined on it.
    theme    which group it belongs to
    cell     a grid coordinate, from show_grid
    angle    what makes this worth testing, in a few words
    facet    the structural thing under test
    want     how many scenarios go in it
    differs  what changes between them, once it is more than one

Good:

    TH12-03 | diagnose-fare | surge boundary confusion | rule:surge-disclosure | x3
             differs: which side of the window the trip started, and whether the
                      receipt was already sent
    TH04-13 | update-payment-method | saved card asked for with no otp this call | rule:otp-before-card | x5
    TH02-01 | compare-address | same street name in two cities | place:ambiguous-city | x5

Not good:

    charged 2.3x for a trip that started one minute before the surge window
    closed, and the receipt shows the higher rate with no explanation

That is the scenario with its code removed. It reads like diligence and it is what breaks this
stage: at that length a plan for a thousand is 228KB and 57k tokens to emit in one response. At
angle length one line carries several scenarios and the whole plan is a few thousand tokens.

There is a second reason beyond size. The particulars are better chosen by whoever writes the
scenario, with the source in front of them. Choosing them here means choosing them from memory and
taking the decision away from the only step that can check it.

**You own coverage and spread. The writer owns the particulars.** Do not do its job.

## `facet` is what makes this work

`facet` names the structural thing under test: `rule:surge-disclosure`, `precondition:book_ride`,
`data:expired-card`, `place:ambiguous-city`, `state:suspended`.

Two angles claiming one facet on one cell are probably one angle written twice, and at angle
length that is the only reliable way to notice: comparing words fails when a line is three words
long, because one differing word swings the comparison.

When a collision is reported, look rather than obey. Three different *input forms* for an address
legitimately share a cell, and four different *reasons* for going out of scope legitimately share
one. Name a sub-facet and move on. Sometimes it really is a duplicate.

## What `want` means, exactly

The number of variants **where the correct answer genuinely differs**. Not how many ways the same
answer could be phrased, and not how many people could ask it.

An angle where the agent should do the same thing every time wants one scenario, however many callers you can
imagine asking it. An angle where the answer turns on the market, the product, the account state
or which precondition is missing is worth as many as there are genuinely different answers.

Do not reach for a different persona to make a number bigger. Two scenarios differing only in who
is calling are one test run twice.

## One bucket is not one scenario

A plan whose buckets outnumber roughly half its target is not a plan, it is a list of scenarios
with extra fields, and it will be refused. The first canvas written against this stage came back
fifty buckets for a target of fifty, every `want` set to one: at a target of a thousand that means
writing a thousand buckets, which is the wall planning exists to avoid.

If a bucket really does hold exactly one case, that is fine and common. If *every* bucket does,
then either the cases want grouping, or this agent supports fewer scenarios than were asked for
and the honest move is to say so rather than to enumerate your way to the number.

`differs` is how a `want` above one earns itself. Naming a number is easy; naming what changes
between the variants is the part that has to be true. "the market, which decides whether cash is
offered" is a reason. "different callers" is not, because the agent should answer them the same.

## Where the size actually comes from

Depth comes from what the agent does and the entities it does it to: its operations, its objects,
its rules, its preconditions, the states its data can be in. Not from personas, tones or channels.
Those are how a scenario is *told*, and telling one situation five ways is one test five times.

Cover, at least:

- **the spine**: the agent's main flow, and every place along it where somebody could arrive out
  of order, change their mind, abandon, or ask for the end before the middle
- **every rule the agent must obey**: once where it holds, and once where something pushes against it
- **every precondition**: what happens when the thing it depends on has not happened yet
- **the states the seeded data can actually be in**, including the awkward ones
- **the boundaries**: capacity, expiry, zero balances, limits, values at a threshold
- **whatever is genuinely ambiguous**, where the agent has to notice rather than guess

## How to work

1. Read the agent. `show_grid` for the coordinates.
2. Work theme by theme rather than writing a flat list. A flat list drifts; a theme with a
   purpose makes you keep inventing.
3. `record_canvas` with the themes and angles you have. It refuses a bad plan rather than storing
   it, and says what is wrong.
4. Fix and record again. This loop is cheap. Every fault left here costs a proof and a folder once
   writers act on it.
5. Record in instalments for a large suite, passing `target`, so it can say how far short you are.

## The plan is a starting partition, not the finished list

You are writing this from outside the code. A writer works inside one bucket with the source open
and will find cases you could not have seen: a branch two calls deep, a state the data reaches
only after something else, a refusal nobody documented. It can open new buckets when it does, and
they are dealt like any other.

So do not try to be exhaustive here, and do not pad a bucket's `want` to cover cases you cannot
name. Partition the space honestly, size each bucket at what you can actually see, and let the
writers widen it. The suite ends up larger than the plan, and the plan was still doing its job.

## When the number asked for is not there

Aim at it and work for it. Go back to the source and look again before concluding the agent is
exhausted; the second read usually finds cases the first missed.

If it genuinely is not there, stop and say so. A hundred real angles and an honest account of why
there are not a thousand beats a thousand where nine hundred are the same tests renamed. The run
reports what it reached, from what actually got written, rather than a number decided in advance.

Stopping because continuing was hard is a failure. Stopping because you have run out is a result.
Be sure which one you are doing.
