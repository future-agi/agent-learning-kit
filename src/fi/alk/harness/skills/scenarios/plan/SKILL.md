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
    why_hard    the structural thing under test
    want     how many scenarios go in it
    differs  what changes between them, once it is more than one

Good, and deliberately from four different kinds of agent, because the shape is the same for all
of them:

    TH12-03 | diagnose-charge   | boundary crossed mid-transaction   | rule:disclose-before-commit | x3
             the 3 differ by: which side of the boundary, and whether the record was already sent
    TH04-13 | update-credential | reused before identity is proved   | rule:verify-before-use      | x5
             the 5 differ by: credential_state, identity_state
    TH07-02 | execute-migration | applied to a repo with dirty state | data:uncommitted-changes    | x4
             the 4 differ by: repo_state, branch_state
    TH09-05 | navigate-checkout | form submitted before it validates | precondition:validate_first | x3
             the 3 differ by: which field is invalid

Not good:

    charged 2.3x for a transaction that started one minute before the window
    closed, and the receipt shows the higher rate with no explanation

That is the scenario with its code removed. It reads like diligence and it is what breaks this
stage: at that length a plan for a thousand is 228KB and 57k tokens to emit in one response. At
angle length one line carries several scenarios and the whole plan is a few thousand tokens.

There is a second reason beyond size. The particulars are better chosen by whoever writes the
scenario, with the source in front of them. Choosing them here means choosing them from memory and
taking the decision away from the only step that can check it.

**You own coverage and spread. The writer owns the particulars.** Do not do its job.

## `why_hard` is what makes this work

`why_hard` names the structural thing under test, and the five prefixes are the whole vocabulary:
`rule:` something the agent must obey, `precondition:` something that must have happened first,
`data:` a state the data can be in, `ambiguity:` a request with two readings, `boundary:` a value
at a limit. What follows the colon is yours and comes from this agent, not from a list.

Two angles claiming one why_hard on one cell are probably one angle written twice, and at angle
length that is the only reliable way to notice: comparing words fails when a line is three words
long, because one differing word swings the comparison.

When a collision is reported, look rather than obey. Three different *input forms* for an address
legitimately share a cell, and four different *reasons* for going out of scope legitimately share
one. Name a sub-why_hard and move on. Sometimes it really is a duplicate.

## What `want` means, exactly

The number of variants **where the correct answer genuinely differs**. Not how many ways the same
answer could be phrased, and not how many people could ask it.

An angle where the agent should do the same thing every time wants one scenario, however many callers you can
imagine asking it. An angle where the answer turns on the environment, the resource, the account
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

## The method, in order

**1. Derive the grid.** `show_grid`. Operation x object, exhaustive by construction. Correct the
object list with `set_objects` if reading the source shows the contract missed something.

**2. Derive the state axes.** Read the seeded data and the rules, and write down every dimension
whose value changes *what the agent should do*. Not what changes the wording: what changes the
behaviour. Two rules keep this honest, and both matter:

- a level must exist in the data, or be reachable by seeding it
- a level must change the correct answer

Nine users are nine names, not nine levels: the agent should treat them identically. But a user
whose only credential is expired, a user with none at all, and a user with two are three levels of
one axis, because the agent has to do something different for each. The test is always the same
question: **if this value changed, would the right answer change?**

**3. Name the why_hard values on each cell.** A why_hard is the structural thing under test, and there are
five kinds, which between them cover how an agent fails:

    rule:X          a constraint it must obey
    precondition:X  something that must have happened first
    data:X          a state the data can be in
    ambiguity:X     the request has two readings
    boundary:X      a value at a limit

**4. A bucket is one cell and one why_hard.** That is the whole definition.

Also say what the agent **should do** there, which is a different question from what structure is
under test:

    succeed    it completes the task
    refuse     it must not do this
    ask        it must clarify before acting
    escalate   it hands off to a human

Exactly one is true of any bucket, and between them they cover everything an agent can do. That is
what makes the count worth reporting: a suite where the agent never has to refuse, ask or escalate
is testing one third of its job.

And separately, if something is deliberately making it hard, name the `overlay`:
`impersonation`, `injection`, `fraud`, `emergency`, `pressure`.

Keep these two apart. An injection attempt **expects a refusal and carries an injection overlay**;
it is not a choice between "adversarial" and "a path bound to fail". Mixing cause and outcome into
one label is what makes two planners label the same bucket differently.

**5. Derive `want` from the varies_by axes.** For each bucket, which axes actually move the answer for
*that why_hard*? Those are its varies_by ones; name them in `varies_by`. `want` is how many of their
combinations survive masking.

**6. Mask, do not multiply.** Drop combinations that cannot happen or that collapse to the same
answer: a capability in an environment that does not offer it, a payment route where that route is
not accepted, an anonymous user with saved preferences. Without masking, `want` is a product of levels and every bucket inflates.

## Size a bucket by the state it crosses, not by a flat number

This is where the size of a suite actually comes from, and where plans go wrong in both
directions. Do not put one scenario in every bucket, and do not put twenty in every bucket. Ask
what states this bucket crosses where the agent should behave differently, and count those.

Buckets are wildly uneven, and that is correct. Worked through on four different agents, to show
the reasoning rather than a domain:

- **a booking agent, payment blocked.** Payment state crossed with region: eight payment states,
  three regions, but only one region takes cash and several pairs collapse to the same refusal.
  Twelve survive.
- **a support agent, refund requested.** Order state (shipped, delivered, lost) crossed with
  whether the request is inside the returns window. Six survive, because a lost order behaves the
  same either side of the window.
- **a coding agent, dependency upgrade.** Repository state (clean, dirty, mid-rebase) crossed with
  whether tests currently pass. Five survive, because you cannot be mid-rebase with a clean tree.
- **a browser agent, item added to a basket.** Two: in stock and out of stock. There is no third,
  and inventing one would be padding.

So read the seeded data before sizing anything. The states that exist there are the ones a
scenario can actually be written against, and the count of them is the honest `want`.

`differs` is how a `want` above one earns itself. Naming a number is easy; naming what changes
between the variants is the part that has to be true. "the region, which decides whether cash is
accepted" is a reason. "different users" is not, because the agent should answer them the same.

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
5. **Record one theme at a time, not the whole plan at once.** Recording adds to what is there,
   so `record_canvas` can be called again and again: a theme's buckets, then the next theme's.
   Pass `target` on the first call so it can say how far short the plan still is.

   This matters more than it sounds. A plan for several hundred scenarios is a long single
   response, and a model writing it in one breath either runs long or truncates, and the whole
   plan is lost. Written a theme at a time, each instalment is validated as it lands and the
   earlier ones are already safe on disk. If you want to start over, pass `replace`.

## The plan is a starting partition, not the finished list

You are writing this from outside the code. A writer works inside one bucket with the source open
and will find cases you could not have seen: a branch two calls deep, a state the data reaches
only after something else, a refusal nobody documented. It can open new buckets when it does, and
they are dealt like any other.

So do not try to be exhaustive here, and do not pad a bucket's `want` to cover cases you cannot
name. Partition the space honestly, size each bucket at what you can actually see, and let the
writers widen it. The suite ends up larger than the plan, and the plan was still doing its job.

## What the plan has to be able to say about itself

Recording the canvas prints its own coverage, and it is worth reading rather than skimming,
because it is the only part of a plan that can be checked against the agent instead of against
its own tidiness:

- how many grid cells have a bucket, and which have none
- how many of the agent's hard rules have a bucket testing them, and which do not
- how many precondition-gated tools are named by some bucket
- what the agent should do across the suite, and how much of it carries an adversarial overlay

The two lines to act on are the uncovered rules and any outcome the agent is never asked for. A rule with no bucket is
something the agent is forbidden to get wrong that nobody is checking.

## When the number asked for is not there

Aim at it and work for it. Go back to the source and look again before concluding the agent is
exhausted; the second read usually finds cases the first missed.

If it genuinely is not there, stop and say so. A hundred real angles and an honest account of why
there are not a thousand beats a thousand where nine hundred are the same tests renamed. The run
reports what it reached, from what actually got written, rather than a number decided in advance.

Stopping because continuing was hard is a failure. Stopping because you have run out is a result.
Be sure which one you are doing.
