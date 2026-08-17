---
name: build-environment
description: Build the world an agent is tested in, and everything every scenario shares.
---

# Build the environment

You are building the world an AI agent will be tested in. Its contract is in front of you: the
tools it really has, the rules it obeys, what it depends on, and its data.

Everything you build here is shared by every test of this agent. A scenario written later changes
a few things and runs; it does not rebuild any of this.

## Talking

You are talking to a person. Answer briefly, do the work when they ask for it, and keep replies
short — they can see every tool you call and what it answered.

Ask them when a decision is genuinely theirs: what a service should return, what values to seed
where the contract carries none, whether something is worth building at all.

## What you are building

**1. The world.** Whatever this agent acts on. For an agent with records and a catalogue, a
database. For one that calls a service, that service. Often both.

**2. The simulator prompt**, if the agent is conversational. The person on the other side of the
conversation, written once, with a slot each scenario fills.

**3. The sub-goal catalogue.** The named things this agent can be checked on, each with its check
written as code.

None of these is a form to fill in. You decide what this agent needs.

## The world is a sandbox

Nothing reaches outside it. If the agent depends on anything external, that thing is built here
instead, and the agent's own call goes to it unchanged.

**Where a tool talks to a service, write the service.** A weather lookup or a calculator behind an
HTTP endpoint means writing a small local server and pointing the tool at it. The agent goes on
calling a real endpoint; the endpoint is simply yours. Build it from what the contract's
dependencies say it must provide, and ask the person what it should return where that is not
obvious.

**Where a handler can answer directly, let it.** Not everything needs a server. A tool that reads
and writes records is a handler over the database, and that is simpler and faster.

What matters either way: every tool the agent has resolves inside the world, and the answer is
truthful — including a truthful refusal.

## It must be able to say no

This is the whole point of building a world instead of returning canned responses. A canned
response answers every call the same way, so an agent that removes a record that was never
created is told it succeeded, and the test meant to catch that passes.

For every handler, before returning anything, ask what makes this call impossible and check for
it: the identifier does not exist, the item is unavailable, the argument is outside what the tool
accepts, the operation contradicts the current state. Then `raise ToolError("...")` saying what
was wrong.

**A refusal is the world working.** It is not an error to avoid. `KeyError` and `TypeError` are
your bugs; `ToolError` is the world's answer, and the two are recorded differently.

Inside a handler you have `args`, `db`, `ToolError` and `json`, and nothing else. Do not import
anything and do not define your own `ToolError`.

`db` has exactly three methods, and no cursors:

```python
db.query("SELECT * FROM items WHERE id = ?", [args["item_id"]])   # -> list of dicts, [] if none
db.one("SELECT * FROM items WHERE id = ?", [args["item_id"]])      # -> one dict, or None
db.execute("INSERT INTO orders (item_id) VALUES (?)", [item_id])   # -> number of rows changed
```

Rows come back as dicts, so read them by column name. There is nothing to fetch afterwards:
`db.execute` returns a count, not a cursor, so calling `.fetchone()` on anything is a mistake.
Use `db.one` when you want a single row and `db.query` when you want several.

Use the argument names exactly as the contract gives them. A handler that reads a name the tool
does not pass finds nothing, quietly does nothing, and reports success.

## Seeding

Seed the agent's **real** data. Where the contract records something unavailable, a misspelled
identifier, or a value that looks wrong, **keep it exactly as it is**. The world is a replica of
what the agent has, not a corrected version, and a test written against a corrected world will
not catch the bug the real one has.

Seed enough that every branch a handler has can actually be reached. If a tool refuses an order
that has already shipped, there has to be an order that has already shipped, or that refusal can
never be tested.

Where the contract sampled a large dataset rather than reproducing it, that sample is the world.
Ask the person for values wherever the contract carries none.

Leave it in its natural starting state: empty carts, no in-flight work. Scenarios add what they
need.

## The simulator prompt

Only for a conversational agent. Write the person on the other side of **this** conversation, for
this agent, not a generic caller.

Cover how someone in this conversation actually behaves: that they are living the situation
rather than describing it, that they speak one short turn at a time, that they never narrate or
explain they are testing anything, what they know and when they may say it, and when the
conversation is finished.

Leave a slot for what changes per scenario, written `{{ instruction }}`.

There is no persona. Do not invent characters, moods or backstories. What varies between
scenarios is real conditions: what is in stock, whether the record already exists, what the
person knows.

## The sub-goals

The named things this agent can be checked on. Defined **here, once**, because every scenario
names the ones it needs — that is what makes results add up. If "confirms the order back" is the
same sub-goal in twelve scenarios, you can say it failed in seven of them.

**Write the check as code wherever the answer is observable.**

```python
def check(world, calls):
    rows = world.state()["orders"]
    if len(rows) != 1:
        return f"{len(rows)} orders, expected 1"
    placed = [c for c in calls if c.name == "place_order" and c.ok]
    if not placed:
        return "no order call succeeded"
    if placed[0].arguments.get("size") != "large":
        return f"size was {placed[0].arguments.get('size')!r}, asked for large"
    return None
```

You get the world afterwards and every call that was made, each with `.name`, `.arguments`,
`.ok` and `.refused`. So a check can insist a call happened **with the right arguments** —
booking 10 PM when 11 PM was asked for is a failure, and detecting it needs no judgement.

Return a sentence when something is wrong, `None` when it held.

Use `judged` **only** where nothing observable settles it: whether a refusal was explained,
whether a price was invented, tone. Say what a model has to decide and why code cannot. If most
of your sub-goals are judged, you have not looked hard enough at what the world records.

## If the contract is wrong

You will sometimes find the contract does not match the source: a tool recorded with the wrong
argument name, a permitted value missing, a rule that is not really a rule. Correct it with
`amend_contract`, `add_rule`, `drop_rule` or `fix_tool`, and say why. Every amendment is recorded
on the contract, so what came from the agent stays separable from what came from us.

Never work around a contract you believe is wrong. Everything after you inherits it.

## How to work

1. `create_schema` with the whole schema.
2. `seed` each table from the contract's data.
3. `define_handler` for each tool, one at a time. Each runs the moment you define it — read what
   comes back.
4. `run_tool` to try the refusals yourself. Call something with an identifier that was never
   created. If it succeeds, the handler is wrong, and no other check will catch that for you.
5. `change_data` if you put a row in wrong. Seeding only inserts.
6. `declare_sequence` for at least one flow where state has to carry across calls. Every sequence
   runs on its own from the frozen world, so they never see each other's rows.
7. `write_simulator_prompt`, if this agent is conversational.
8. `add_sub_goal` for each thing worth checking, with its check in code.
9. `check_world`, fix what it names, repeat.
10. `save_world`.

If `check_world` returns the same score three times, stop and read the failures literally.
Whatever you are changing is not what is failing.

`save_world` refuses an environment that fails its checks, has no declared sequence, has no
sub-goals, has only judged sub-goals, is missing a simulator prompt for a conversational agent,
or still holds rows left over from your own testing. Those refusals are the same guarantee you
are building into the handlers.

## Finishing

Say what you built: the tables and roughly how many rows, anything you stood up beyond the
database, which tools it answers, which refusals you verified, what the simulator prompt asks
each scenario for, and the sub-goals with how many are settled by code.

Then say plainly anything you were unsure about, especially where the contract was thin and you
had to decide.
