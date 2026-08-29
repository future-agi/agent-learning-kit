---
name: authoring-scenario-code
description: "Read when you are writing the code fields of a scenario: setup_code, ready_code, or the reference solution. Covers how setup composes against the frozen base world, the two ways to change state and why neither names the store, the shape a collection actually has, what ready must assert, and why the solution is not optional. Read it at the point of writing those fields, not before: choosing WHICH scenarios to write needs _framework.md and the per-type file instead."
---

# Writing a scenario's code

> **Selection check.** You are in the right file if you are filling in `setup_code`, `ready_code`
> or `solution` for a scenario you have already decided to write. If you are still deciding what
> scenarios the suite needs, read `_framework.md` and the reference for this agent type first.

## Writing setup_code

Python defining `setup(world)`. Leave it empty when the base world is already right.

**Write every setup against the base world, never against a scenario you wrote before it.** At run
time each scenario restores its own copy of the frozen base and applies only its own setup, so
nothing another scenario did is there. This is easy to get wrong while writing several in a row:
you have just set an order to "delivered" for one scenario, and the next one reads as though that
still holds. It does not. If a scenario needs a record in a particular state, its own setup puts
it there, whatever any earlier scenario happened to do. The same goes for the calls you make while
rehearsing with `try_calls`: those run on a throwaway copy and change nothing anybody else sees.

You have two ways to change things, and **neither of them names what the world is kept in**. A
scenario that wrote SQL would only work against a world that happened to be a database, and the
store is the thing that varies most between agents.

**Prefer the agent's own tools.** It goes through the same path the agent will, so anything the
world would refuse to you would have refused the agent too.

```python
def setup(world):
    world.call("add_to_stock", {"item_id": "widget", "quantity": 5})
```

**Otherwise change the world directly**, in collections and records:

```python
world.put(collection, record, key=...)  # add one record
world.change(collection, key, changes, by=...)  # change one record
world.drop(collection, key, by=...)  # remove one, or all of them with no key
```

The keyed-on argument names the column a table is keyed on, and is not needed for a collection
that is keyed already. `world.state()` shows you every collection and what is in it, which is how you find out
which you are dealing with.

```python
def setup(world):
    world.change("stock", "widget", {"quantity": 5}, by="item_id")
```

Use the direct route only for states no tool can produce: a record already in a condition the
agent could never create itself.

## A collection is not always a list

`world.state()` gives every collection this world has, and their shapes differ by agent. A table
gives a list of records. A collection the agent's own code keeps is often a mapping keyed by
identifier, and iterating that yields the keys, which are strings, so reading a field off one fails.

```python
held = world.state()["some_collection"]
records = list(held.values()) if isinstance(held, dict) else held
```

Look before you write. `inspect_world` shows you which is which, and this applies to `setup_code`,
`ready_code` and every check.

## Writing ready_code

Python defining `ready(world)`. Return `None` when the world holds what the scenario presumes,
or a sentence naming what is missing.

Check the thing your scenario actually depends on, not everything.

```python
def ready(world):
    rows = world.state()["stock"]
    widget = next((r for r in rows if r["item_id"] == "widget"), None)
    if widget is None:
        return "no widget in stock at all; this scenario is about its last five"
    if widget["quantity"] != 5:
        return f"stock says {widget['quantity']} widgets, this scenario needs exactly 5"
    return None
```

## The solution is not optional

Every scenario carries what a correct agent would do. It is never run against the agent under
test. It exists to prove the scenario can be passed at all, and it is what gate 2 uses.

Work it out with `try_calls` before you submit. Run the calls, pass your `setup_code` so you see
the world the agent would actually face, look at the state they leave, and confirm the sub-goals
you are naming respond to it.

**A one-call solution is almost always wrong.** The agent does not begin the call knowing who it
is talking to or what is true of their account, so before the call that resolves the scenario it
has to find that out: identify the caller, read the record, check the state that decides the
answer. Those lookups belong in the solution, and the sub-goals have to name them. Write the
single terminal call on its own and the scenario passes for an agent that fires it blind, having
established nothing, which is the one behaviour a refusal scenario exists to rule out.

Refusals and transfers are where this goes wrong most often, because the terminal call is so
obviously the point of the scenario. It is not: *deciding* to refuse is the point, and a decision
that was never reached from evidence was never tested.
