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

    cell | angle | count

`cell` is a grid coordinate: an operation and an object, like `diagnose-fare`. `show_grid` lists
them. If the grid is missing something the agent obviously does, correct it with `set_objects`
rather than planning around the gap.

`angle` is what makes a case on that cell worth testing, in a few words. Not how it goes.

`count` is how many scenarios to write from that angle. One angle can carry several.

Good:

    diagnose-fare | surge boundary confusion | 3
    diagnose-fare | duplicate charge that is not one | 2
    compare-address | same street name in two cities | 2

Not good:

    diagnose-fare | charged 2.3x for a trip that started one minute before the
      surge window closed, and the receipt shows the higher rate with no
      explanation, so the agent has to find the window and explain it

That last one is the scenario with its code removed. It reads like diligence and it is the thing
that breaks this stage: written at that length, a plan for a thousand scenarios is 228KB and you
would have to emit 57k tokens in one response. You cannot. At angle length the same thousand is a
few thousand tokens, because one angle carries several scenarios.

There is a second reason beyond size. The particulars are better chosen by whoever writes the
scenario, with the agent's source open in front of them. Choosing them here means choosing them
from memory, and it takes the decision away from the only step that can check it.

**You own coverage and spread. The writer owns the particulars.** Do not do its job.

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
