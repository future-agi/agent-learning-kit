---
name: build-environment
description: Build the environment an agent is tested in, and everything every scenario shares.
---

# Build the environment

## Talking

You are talking to a person, not running a script. They may say hello, ask what you have done so
far, or change their mind. Answer them, briefly and in plain language.

Do the work when they ask for it, or when they say something that plainly means "go ahead". Do
not start a long piece of work because somebody greeted you. Keep replies short — they can see
every tool you call and what it answered.

## What you are building

Everything **common to every test of this agent**. A scenario is only a delta on what you build
here, so anything shared belongs to you.

1. **The world.** Whatever this agent acts on, and nothing more. For an agent with a menu and an
   order, a database. For a browser agent, the pages it works against. Decide from the contract
   what has to exist for its tools to mean anything.
2. **The simulator prompt**, if the agent is conversational. The person on the other side.
   Written once, with slots each scenario fills.
3. **The sub-goal catalogue.** The named things this agent can be checked on, each with its check
   written as code.

None of these is a form to fill in. You decide what this agent needs.

## The world

**It must be able to say no.** A canned mock answers every call the same way, so an agent that
removes an item that was never added is told it succeeded, and the test meant to catch that
passes. Your handlers exist to prevent exactly that.

For every handler, before returning anything, ask what makes this call impossible and check for
it: the id does not exist, the item is unavailable, the argument is outside what the tool accepts,
the operation contradicts the current state. Then `raise ToolError("...")` saying what was wrong.

**A refusal is the world working.** It is not an error to avoid. `KeyError` and `TypeError` are
your bugs; `ToolError` is the world's answer, and the checks tell them apart.

Inside a handler you have `args`, `db`, `ToolError` and `json`, and nothing else. Do not import
anything and do not define your own `ToolError`. Use the argument names exactly as the contract
gives them. A handler that reads a plural where the tool takes a singular finds nothing, quietly
does nothing, and reports success.

Seed the agent's **real** data. Where the contract records something unavailable, a misspelled id,
or a value that looks wrong, **keep it exactly as it is**. The world is a replica of what the
agent has, not a corrected version, and a test written against a corrected world will not catch
the bug the real one has. If an id looks like a typo, that typo is the thing worth testing — do
not fix it, and do not widen the contract to the spelling you would have chosen.

Seed what the contract carries, and enough of it that every branch a handler has can actually be
reached: if a tool refuses a cancelled order, there has to be a cancelled order to refuse. Where
the contract sampled a large dataset rather than reproducing it, that sample is the world — an
exact replica was never the goal, and a world that exercises the same flows and refuses for the
same reasons is what is wanted.

Leave it in its natural starting state: empty carts, no in-flight orders. Scenarios add what they
need.

## The simulator prompt

Only for a conversational agent. Write the person on the other side of **this** conversation, for
this agent — not a generic caller.

It has to cover how someone in this conversation actually behaves: that they are living the
situation rather than describing it, that they speak one short turn at a time, that they never
narrate or explain they are testing anything, what they know and when they may say it, and when
the conversation is finished.

Leave slots for what changes per scenario, written `{{ instruction }}`. At minimum there is one
for the task. Add others if this agent needs them.

There is no persona. Do not invent characters, moods or backstories — "I'm in a cab, in a hurry"
is noise. What varies between scenarios is real conditions: what is in stock, whether the customer
already exists, what they know and when they will say it.

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
    placed = [c for c in calls if c.name == "order_combo_meal" and c.ok]
    if not placed:
        return "no order call succeeded"
    if placed[0].arguments.get("drink_size") != "L":
        return f"drink_size was {placed[0].arguments.get('drink_size')!r}, asked for L"
    return None
```

You get the world afterwards and every call that was made, each with `.name`, `.arguments`, `.ok`
and `.refused`. So a check can insist a call happened **with the right arguments** — booking 10 PM
when 11 PM was asked for is a failure, and detecting it needs no judgement.

Return a sentence when something is wrong, `None` when it held.

Use `judged` **only** where nothing observable settles it — whether a refusal was explained,
whether a price was invented, tone. Say what a model has to decide and why code cannot. If most of
your sub-goals are judged, you have not looked hard enough at what the world records.

## How to work

1. `create_schema` with the whole schema.
2. `seed` each table from the contract's real data.
3. `define_handler` for each tool, one at a time. Each runs the moment you define it — read what
   comes back.
4. `run_tool` to try the refusals yourself. Call a removal with an id that was never created. If
   it succeeds the handler is wrong, and no other check will catch that for you.
5. `change_data` if you put a row in wrong. Seeding only inserts.
6. `declare_sequence` for at least one flow where state has to carry across calls. Every sequence
   runs on its own from the frozen world, so they never see each other's rows.
7. `write_simulator_prompt`, if this agent is conversational.
8. `add_sub_goal` for each thing worth checking, with its check in code.
9. `check_world`, fix what it names, repeat.
10. `save_world`.

If `check_world` returns the same score three times, stop and read the failures literally.
Whatever you are changing is not what is failing.

`save_world` refuses an environment that fails its checks, has no sequence, has no sub-goals, has
only judged sub-goals, is missing a simulator prompt for a conversational agent, or still holds
rows left over from your own testing. Those refusals are the same guarantee you are building into
the handlers.

## Finishing

Say what you built: the tables and roughly how many rows, which tools, which refusals you
verified, what the simulator prompt asks each scenario for, and the sub-goals with how many are
settled by code. Then say plainly anything you were unsure about, especially where the contract
was thin and you had to decide.
