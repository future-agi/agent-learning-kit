---
name: plan-scenarios
description: Decide what every scenario in a suite will be, one line each, before any of them are written.
---

# Plan the suite before writing it

You are deciding what a suite of tests will contain. Not writing them: deciding. One line per
scenario, all of them settled before the first one is written.

This step exists because the other two ways of getting to a large suite both fail, and they fail
differently.

Asking for a thousand finished scenarios at once does not fit in a context and never will.

Writing them one at a time does fit, and produces a worse suite than it looks like it should.
Each scenario gets written with the last few in view, so the third resembles the second, the
tenth resembles the ninth, and by fifty the suite has quietly settled into one shape. Nobody did
anything wrong at any step. A suite of fifty came back with nine distinct people in it, forty-two
of them American, living in two places, and every writer had been told to vary its work.

A thousand one-line intentions do fit. That is the whole trick: you can hold the entire suite in
view while it is still cheap to change, and see that thirty of your lines are the same line.

## What one line is

    name | cell | situation

`name` becomes the scenario's folder, so it has to be unique and it should describe what is in
the scenario rather than its number in a list.

`cell` is a coordinate from the grid: an operation and an object, like `diagnose-fare` or
`cancel-ride`. Use `show_grid` to see them. If the grid is missing something the agent obviously
does, correct it with `set_objects` rather than planning around the gap.

`situation` is the part only you can write. It names **what the person actually wants and what is
in the way**. Not the persona, not the tone, not how they say it.

Good:

    diagnose-fare-surge-boundary | diagnose-fare | charged 2.3x for a trip that
      started one minute before the surge window closed, and the receipt shows
      the higher rate with no explanation

Not good:

    diagnose-fare-2 | diagnose-fare | an impatient caller asks about a fare

The second one names a mood and a cell. Every writer handed it will write the same test.

## The one thing that makes this hard

The grid gives you coverage and stops there. A thousand scenarios over forty cells is twenty-five
per cell, and the coordinates of those twenty-five are identical by construction. What separates
them has to be the situation, and there is nothing in the grid that invents situations.

So the work is per cell: given this operation on this object, what are twenty-five genuinely
different ways this goes wrong, or goes unusually, or goes right in a way worth checking?

Sources of real difference, in rough order of how much they are worth:

- **What the person has got wrong.** They think they were charged twice and were not. They think
  the booking is cancelled and it is not. They are describing yesterday's trip as today's.
- **What the data makes awkward.** Two records that look identical. A record that is missing the
  field the agent wants to key on. A value at a boundary.
- **What the agent has to refuse or escalate.** Not only fraud: things it is simply not allowed to
  do, or not allowed to do for this person.
- **What is ambiguous.** The request has two readings and the agent has to notice, not guess.
- **What arrives incomplete.** They stop halfway, change their mind, or supply the wrong thing
  first.

Do not reach for a different persona to tell the same story twice. Two scenarios that differ only
in who is calling are one test run twice, and they will be reported as duplicates.

## How to work

1. `show_grid`, and read the agent's source. The plan is only as good as your understanding of
   what this agent actually does.
2. Work cell by cell rather than writing a flat list of N. A flat list drifts; a cell with a
   quota makes you keep inventing.
3. `record_blueprint` with what you have. It refuses a plan rather than storing a bad one, and
   names what is wrong: repeated names, cells that do not exist, situations too thin to write
   from, and pairs that say the same thing in different words.
4. Fix what it named and record again. This loop is cheap. Every fault left here costs a proof, a
   folder and a slot once writers act on it.
5. For a large suite, record in instalments and pass `wanted` so it can tell you how far short
   the plan still is.

Plan the whole suite before any writer starts. A blueprint half-written is worse than none,
because the second half gets planned in the shadow of the first half's scenarios.
