---
name: plan
description: Decide what every scenario in a suite will be, one line each, before any of them are written.
---

# Plan the suite before writing it

You are deciding what a suite contains. Not writing it: deciding. One line per scenario, all of
them settled before the first is written.

This step exists because the other two ways of getting to a large suite both fail, differently.

Asking for a thousand finished scenarios at once does not fit in a context and never will.

Writing them one at a time does fit, and produces a worse suite than it looks like it should.
Each scenario is composed with the last few in view, so the third resembles the second, the tenth
resembles the ninth, and by fifty the suite has settled into one shape. Nobody does anything
wrong at any step. Measured here: fifty scenarios contained nine distinct people, forty-two of
them American, living in two places, and every writer had been told to vary its work.

A thousand one-line intentions do fit. That is the whole trick. You can hold the entire suite in
view while it is still cheap to change, and see that thirty of your lines are the same line.

## What one line is

    name | cell | situation

`name` becomes the scenario's folder. Unique, and descriptive of what is in it rather than its
position in a list.

`cell` is a grid coordinate: an operation and an object, like `diagnose-fare`. `show_grid` lists
them. If the grid is missing something the agent obviously does, correct it with `set_objects`
rather than planning around the gap.

`situation` is the part only you can write. It names **what the person wants and what is in the
way**. Not their mood, not their accent, not how they phrase it.

Good:

    diagnose-fare-surge-boundary | diagnose-fare | charged 2.3x for a trip that
      started one minute before the surge window closed, and the receipt shows
      the higher rate with no explanation

Not good:

    diagnose-fare-2 | diagnose-fare | an impatient caller asks about a fare

The second names a mood and a cell. Every writer handed it writes the same test.

There is a second way to get this wrong, and it looks like diligence. A situation can be so
grounded in the code that it stops being a situation:

    compare-booking | prepare_booking_confirmation returns a summary string
      listing car type, fare range, pickup, dropoff and payment method

That is a unit test of one tool wearing a scenario's clothes. It will be written, it will pass,
and it will not catch anything a person would have hit, because no person ever asked for it.

The test to apply: **could the person on the other end have wanted this?** Nobody wants a summary
string. Somebody does want to know what they are about to be charged before they say yes. Naming
real tools, real ids and real return values is right and stays right; what matters is that the
line describes something a caller was trying to do.

## Where situations actually come from

From the agent's code, not from your general knowledge of what goes wrong with software. Read it
first and plan second. Read the handlers, the data it starts with, the validation, the error
paths, the comments.

What you are looking for is anything that creates a case the agent has to get right and might
not: a condition a handler refuses under, two records that are hard to tell apart, a field that
is optional in one place and assumed in another, an order of operations that matters, a value at
a boundary, a state the data can be in that the happy path never produces.

Deliberately not listed here: a taxonomy of situation types to work through. Given one, you would
produce those types and stop, and the ceiling would be the list's rather than the agent's. The
best line in a suite is usually the one that could only have been written by somebody who had
read that particular code.

The grid gives you coverage and stops there. A thousand scenarios over forty cells is twenty-five
per cell, and their coordinates are identical by construction. What separates them is the
situation, and nothing but you invents those.

Do not reach for a different persona to tell the same story twice. Two scenarios differing only
in who is calling are one test run twice, and they will be reported as duplicates.

## How to work

1. Read the agent. `show_grid` to see the coordinates.
2. Work cell by cell rather than writing a flat list of N. A flat list drifts; a cell with a
   quota makes you keep inventing.
3. `record_blueprint` with what you have. It refuses a bad plan rather than storing it, and says
   what is wrong: repeated names, cells that do not exist, situations too thin to write from,
   pairs that say the same thing in different words.
4. Fix and record again. This loop is cheap. Every fault left here costs a proof and a folder
   once writers act on it.
5. For a large suite, record in instalments and pass `wanted` so it can say how far short you are.

Plan the whole suite before any writer starts. A blueprint half-written is worse than none,
because the second half gets planned in the shadow of the first half's scenarios.

## When you cannot reach the number

Aim at what was asked for and work for it. Go back to the source and look again before concluding
the agent is exhausted; the second read usually finds cases the first missed.

If you genuinely run out, record the plan with `ceiling` set: what you exhausted, and what would
be needed to go further. A hundred real scenarios and an honest account of why there are not a
thousand is a better result than a thousand rows where nine hundred are the same tests renamed.

Stopping because continuing was hard is a failure. Stopping because you have run out is a result.
Be sure which one you are doing.
