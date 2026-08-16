"""Freezing a world, and starting every scenario from the same frozen copy.

The database is built once and snapshotted; that snapshot is the base state. A scenario restores
its own copy and layers on whatever it additionally needs, so scenarios cannot inherit each
other's leftovers and a run is repeatable a week later.

Which is why the overlay exists: a scenario that needs a customer with three open orders adds
those rows to a restored copy rather than editing the snapshot. The base world stays the shared
starting point instead of drifting toward whichever scenario was written last.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from .runtime import GeneratedWorld

DATABASE = "world.sqlite"
HANDLERS = "handlers"
MANIFEST = "manifest.json"
WORLD_MODULE = "world.py"

_MODULE = '''"""Generated world for {agent}. Do not edit by hand; regenerate instead.

{notes}
"""

from pathlib import Path

from fi.alk.harness.world.runtime import GeneratedWorld

_HERE = Path(__file__).parent

TOOLS = {tools}


class World(GeneratedWorld):
    name = {agent!r}
    tools = TOOLS
    handlers = {{
        name: (_HERE / "handlers" / f"{{name}}.py").read_text(encoding="utf-8")
        for name in {handler_names}
    }}


def load(database=None):
    """The world, restored from its snapshot unless another database is given."""
    return World(database or (_HERE / "world.sqlite"))
'''


def save(
    world: GeneratedWorld,
    path: str | Path,
    *,
    notes: str = "",
    sequences: list[dict[str, Any]] | None = None,
) -> Path:
    """Write the world out: the snapshot, the handlers, the module, and a manifest."""
    root = Path(path)
    (root / HANDLERS).mkdir(parents=True, exist_ok=True)

    frozen = sqlite3.connect(root / DATABASE)
    with frozen:
        world.connection.backup(frozen)
    frozen.close()

    for name, source in world.handlers.items():
        (root / HANDLERS / f"{name}.py").write_text(source, encoding="utf-8")

    (root / WORLD_MODULE).write_text(
        _MODULE.format(
            agent=world.name,
            notes=notes or "Generated from the agent's contract.",
            tools=json.dumps(world.tools, indent=4),
            handler_names=json.dumps(sorted(world.handlers)),
        ),
        encoding="utf-8",
    )

    state = world.state()
    (root / MANIFEST).write_text(
        json.dumps(
            {
                "agent": world.name,
                "tools": sorted(world.handlers),
                # Written because restore reads it. Without it a restored world publishes no
                # tool descriptions at all, and every later stage has to reconstruct them.
                "tool_specs": list(world.tools),
                "tables": {name: len(rows) for name, rows in state.items()},
                # Kept because they are judgement about this agent, not something a schema
                # implies. A world picked up again can be re-verified without redeclaring them.
                "sequences": list(sequences or []),
                "notes": notes,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return root


def restore(path: str | Path, *, into: str | Path | None = None) -> GeneratedWorld:
    """A fresh, independent copy of the frozen world.

    In memory by default, because a scenario should not be able to write back into the snapshot
    every later scenario depends on.
    """
    root = Path(path)
    source = root / DATABASE
    if not source.exists():
        raise FileNotFoundError(f"no world snapshot at {root}")

    manifest = read_manifest(root)
    handlers = {
        name: (root / HANDLERS / f"{name}.py").read_text(encoding="utf-8")
        for name in manifest.get("tools", [])
        if (root / HANDLERS / f"{name}.py").exists()
    }

    if into is None:
        world = GeneratedWorld(":memory:")
        origin = sqlite3.connect(source)
        with world.connection:
            origin.backup(world.connection)
        origin.close()
    else:
        target = Path(into)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        world = GeneratedWorld(target)

    world.name = manifest.get("agent", "generated")
    world.handlers = handlers
    world.tools = manifest.get("tool_specs", [])
    return world


def read_manifest(path: str | Path) -> dict[str, Any]:
    return json.loads((Path(path) / MANIFEST).read_text(encoding="utf-8"))


def apply_overlay(world: GeneratedWorld, overlay: Mapping[str, Any] | None) -> int:
    """Layer one scenario's own rows onto a restored world.

    ``{"table": [{"column": value}, ...]}``. The only sanctioned way a scenario adds data, so the
    base world stays the shared starting point rather than drifting per scenario.
    """
    written = 0
    for table, rows in (overlay or {}).items():
        for row in rows or []:
            if not isinstance(row, Mapping) or not row:
                continue
            columns = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            world.connection.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({marks})", list(row.values())
            )
            written += 1
    world.connection.commit()
    return written
