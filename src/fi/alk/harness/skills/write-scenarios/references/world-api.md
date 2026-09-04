# Changing the world, and reading it back

Consult this while writing `setup_code`, `ready_code` and any check. None of it names what the
world is kept in, which is deliberate: the store varies more between agents than anything else.

## Changing it directly

Where no tool of the agent's can produce the state you need, change the world's collections and
records yourself:

```python
world.put(collection, record)  # add one record; a table already owns its primary key
world.change(collection, key, changes, by=...)  # change one record
world.drop(collection, key, by=...)  # remove one, or all of them with no key
```

Only use `world.put(..., key=...)` for an in-memory mapping that is not a table: a table's primary key
is already in the record. `world.state()` shows every collection and what is in it.

```python
def setup(world):
    world.change("stock", "widget", {"quantity": 5}, by="item_id")
```

Use the direct route only for states no tool can produce: a record already in a condition the agent
could never create itself.

One exception. Where the contract says the target's store is hardcoded and process-local, with no
configuration seam, `setup_code` cannot alter target records, because the world and the live target
are separate copies. Use only records already in the frozen base, keep setup empty for them, and
settle outcomes from the captured calls. If coverage needs state the base lacks, report that the
target needs a seed or reset seam rather than writing a scenario that cannot run.

### A collection is not always a list

`world.state()` gives every collection this world has, and their shapes differ by agent. A table gives
a list of records. A collection the agent's own code keeps is often a mapping keyed by identifier, and
iterating that yields the keys, which are strings, so reading a field off one fails.

```python
held = world.state()["some_collection"]
records = list(held.values()) if isinstance(held, dict) else held
```

Look before you write. `inspect_world` shows which is which, and this applies to `setup_code`,
`ready_code` and every check.

### ready_code

Python defining `ready(world)`. Return `None` when the world holds what the scenario presumes, or a
sentence naming what is missing. Check the thing your scenario depends on, not everything.

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
