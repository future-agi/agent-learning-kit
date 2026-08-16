---
name: build-environment
description: Build a real, database-backed world that an agent's tools run against.
---

# Build the environment

You are building the world an agent will be tested in. Its tools will run against your database
and get back whatever it really says, including a refusal when the agent asks for something that
is not there.

The contract is the only source of truth. Every table, every row, every id comes from it. If the
contract does not contain something, the world does not have it either.

## What you are building

1. **A schema.** The tables the agent's data actually needs, with the keys and constraints that
   make wrong states impossible to reach.
2. **Seed data.** The agent's real catalogue: its menu, its records, its inventory, taken from
   the contract's data, not invented.
3. **One handler per tool.** Python, `def handle(args, db)`, using `db.query`, `db.one` and
   `db.execute`. It returns what the real tool would return.
4. **Sequences.** Series of calls whose end state you assert, so consistency across calls is
   checked rather than assumed.

## The one thing that matters most

**The world must be able to say no.**

A canned mock answers every call the same way, so an agent that removes an item that was never
added is told it succeeded, and the test that was supposed to catch that passes. Your handlers
exist to prevent exactly that.

So for every handler, before you return anything, ask what makes this call impossible and check
for it:

- the id does not exist
- the item exists but is unavailable
- the argument is outside what the tool accepts
- the operation contradicts the current state, like removing from an empty order

When one of those holds, `raise ToolError("...")` with a message that says what was wrong. A
refusal is the world working. It is not an error you should be avoiding.

`ToolError` is already available inside a handler. Do not define your own, and do not import
anything: a handler has `args`, `db`, `ToolError` and `json`, and nothing else.

Use the argument names exactly as the contract gives them. A handler reading `order_ids` when
the tool takes `order_id` finds nothing, quietly does nothing, and reports success, which is
the precise failure this world exists to prevent.

Never let a handler crash on bad input. `KeyError` and `TypeError` are your bugs; `ToolError` is
the world's answer. They must not be confused, and one of the checks tells them apart.

## How to work

Build in this order and check as you go.

1. `create_schema` with the whole schema.
2. `seed` each table from the contract's data. Seed the real catalogue, not a sample of it: a
   scenario about an unavailable item needs the unavailable item to be in there.
3. `define_handler` for each tool, one at a time. Each is executed the moment you define it, so
   read what comes back. Pass `smoke_arguments` that should work.
4. `run_tool` to try the refusals yourself. Call a removal with an id that was never created. If
   it succeeds, the handler is wrong, and no other check will catch that for you.
5. `declare_sequence` for at least one flow where state has to carry across calls. Add something,
   list it, remove it, list again. This is the failure that individual calls cannot reveal.
6. `check_world` to see everything at once, fix what it reports, and repeat.
7. `save_world` when it passes.

`save_world` refuses a world that has not passed its checks or has no declared sequence. That
refusal is not an obstacle to work around; it is the same guarantee you are building into the
handlers.

## Seed data

Use the contract's real values. Real ids, real names, real prices, real availability flags. The
whole point is that a test can reference something and have it be there.

Where the contract records that something is unavailable, or a typo in an id, or a value that
looks wrong, **keep it as it is**. The world is a replica of what the agent actually has, not a
corrected version of it. A test written against a corrected world will not catch the bug the
real one has.

Leave the world in its natural starting state: empty carts, no in-flight orders, nothing that
belongs to one particular scenario. Individual scenarios add what they need on top of it.

## Finishing

Say what you built: the tables, roughly how many rows, which tools, and which refusals you
verified. Then say plainly anything you were unsure about, especially where the contract was
thin and you had to decide.
