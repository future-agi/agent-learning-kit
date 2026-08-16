"""The tools that build a world, and the gate that decides it may be saved.

A deliberately narrow surface. The builder gets no generic file write, because a guardrail needs
something to sit behind: every action goes through a tool that can execute it, check it, and say
what went wrong. Interface design work on coding agents is consistent that this beats handing
over raw access and hoping.

Three habits throughout, for the same reason:

- **execute immediately.** A handler is run the moment it is defined, so a mistake comes back on
  the next turn rather than at save time.
- **say what happened, briefly.** Counts and names, never dumps. More context measurably makes
  agents worse at this.
- **never answer with nothing.** "0 rows inserted" is a result; an empty string is a puzzle.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from ..contract import AgentContract
from .probe import probe
from .runtime import GeneratedWorld
from .snapshot import save

WORLD_SERVER = "world"

# Below this, the world is not good enough to build tests on. Synthesis work that measures this
# converges on roughly this bar, and rejects a quarter to a third of what it generates.
ACCEPTABLE = 0.85


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _brief(value: Any, limit: int = 400) -> str:
    rendered = value if isinstance(value, str) else json.dumps(value, default=str)
    return rendered if len(rendered) <= limit else rendered[: limit - 3] + "..."


def world_tools(contract: AgentContract, destination: Path) -> Any:
    """A server exposing the world-building surface for one agent."""
    world = GeneratedWorld(":memory:")
    world.name = contract.agent
    sequences: list[dict[str, Any]] = []

    @tool(
        "create_schema",
        "Run CREATE TABLE statements. Call once with the whole schema; call again to alter it.",
        {"sql": str},
    )
    async def create_schema(args: dict[str, Any]) -> dict[str, Any]:
        try:
            world.connection.executescript(args["sql"])
            world.connection.commit()
        except Exception as failed:
            return _err(f"schema rejected: {failed}")
        tables = sorted(world.state())
        return _ok(f"{len(tables)} tables: {', '.join(tables) or 'none'}")

    @tool(
        "seed",
        "Insert rows. Rows is a list of objects whose keys are column names.",
        {"table": str, "rows": list},
    )
    async def seed(args: dict[str, Any]) -> dict[str, Any]:
        table, rows = str(args["table"]), args.get("rows") or []
        written = 0
        for row in rows:
            if not isinstance(row, dict) or not row:
                continue
            columns = ", ".join(row)
            marks = ", ".join("?" for _ in row)
            try:
                world.connection.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({marks})",
                    list(row.values()),
                )
                written += 1
            except Exception as failed:
                world.connection.rollback()
                return _err(
                    f"{written} rows written, then {table} rejected a row: {failed}"
                )
        world.connection.commit()
        total = len(world.state().get(table, []))
        return _ok(f"{written} rows inserted into {table}; {total} rows there now")

    @tool(
        "define_handler",
        "Define one tool's implementation. The source must define handle(args, db) and is run "
        "immediately against the seeded world, so errors come straight back.",
        {"tool_name": str, "source": str, "smoke_arguments": dict},
    )
    async def define_handler(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args["tool_name"])
        if name not in contract.tool_names():
            return _err(
                f"{name!r} is not a tool this agent has. It has: "
                f"{', '.join(sorted(contract.tool_names()))}"
            )
        world.handlers[name] = str(args["source"])
        call = world.call(name, args.get("smoke_arguments") or {})
        if call.refused:
            return _ok(
                f"{name} defined. Smoke call refused, which is a working refusal: {call.error}"
            )
        if not call.ok:
            del world.handlers[name]
            return _err(f"{name} not kept, it crashed on its smoke call: {call.error}")
        return _ok(f"{name} defined and ran. Returned {_brief(call.result)}")

    @tool(
        "run_tool",
        "Call a defined tool and see what the world does. Use this to check a refusal works.",
        {"tool_name": str, "arguments": dict},
    )
    async def run_tool(args: dict[str, Any]) -> dict[str, Any]:
        call = world.call(str(args["tool_name"]), args.get("arguments") or {})
        if call.refused:
            return _ok(f"refused: {call.error}")
        if not call.ok:
            return _err(f"crashed: {call.error}")
        return _ok(f"ok: {_brief(call.result)}")

    @tool(
        "declare_sequence",
        "Declare a series of calls whose end state should hold, so consistency across calls is "
        "checked. Each call is {tool, arguments}. expect_state keys are 'table.column' or "
        "'table.count'. Declaring the same name again replaces it, so a mistake is fixed by "
        "redeclaring rather than accumulating.",
        {"name": str, "calls": list, "expect_state": dict},
    )
    async def declare_sequence(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or f"sequence-{len(sequences)}")
        calls = args.get("calls") or []

        # Checked here rather than at save time. A malformed sequence that only fails three
        # tools later reads as a mystery, and there is nothing to learn from it in between.
        problems: list[str] = []
        if not calls:
            problems.append("no calls: a sequence with no calls checks nothing")
        for index, step in enumerate(calls):
            if not isinstance(step, dict):
                problems.append(
                    f"call {index} is not an object with a tool and arguments"
                )
                continue
            called = str(step.get("tool") or "")
            if not called:
                problems.append(f"call {index} has no tool name")
            elif called not in world.handlers:
                problems.append(
                    f"call {index} names {called!r}, which has no handler yet. Defined: "
                    f"{', '.join(sorted(world.handlers)) or 'none'}"
                )
        if problems:
            return _err(f"{name} not declared:\n  - " + "\n  - ".join(problems))

        replaced = any(existing["name"] == name for existing in sequences)
        sequences[:] = [existing for existing in sequences if existing["name"] != name]
        sequences.append(
            {
                "name": name,
                "calls": calls,
                "expect_state": args.get("expect_state") or {},
            }
        )
        verb = "replaced" if replaced else "declared"
        return _ok(
            f"{name} {verb}. {len(sequences)} sequences: {', '.join(s['name'] for s in sequences)}"
        )

    @tool(
        "drop_sequence",
        "Remove a declared sequence by name, or all of them with name '*'.",
        {"name": str},
    )
    async def drop_sequence(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if name == "*":
            sequences.clear()
            return _ok("all sequences dropped")
        before = len(sequences)
        sequences[:] = [existing for existing in sequences if existing["name"] != name]
        if len(sequences) == before:
            return _err(
                f"no sequence called {name!r}. Declared: "
                f"{', '.join(s['name'] for s in sequences) or 'none'}"
            )
        return _ok(f"{name} dropped. {len(sequences)} left")

    @tool(
        "check_world",
        "Exercise every tool with a valid call, a nonexistent id, and a missing argument, then "
        "run the declared sequences. Reports what is wrong without saving anything.",
        {},
    )
    async def check_world(_args: dict[str, Any]) -> dict[str, Any]:
        report = probe(world, contract, sequences=sequences)
        return _ok(f"{report.summary()}\nscore {report.score:.2f}")

    @tool(
        "save_world",
        "Freeze the world and write it out. Refused unless it passes its own checks.",
        {"notes": str},
    )
    async def save_world(args: dict[str, Any]) -> dict[str, Any]:
        report = probe(world, contract, sequences=sequences)
        if report.score < ACCEPTABLE:
            return _err(
                f"Not saved, the world does not hold up yet.\n{report.summary()}\n"
                f"score {report.score:.2f}, needs {ACCEPTABLE:.2f}"
            )
        if not sequences:
            return _err(
                "Not saved. Declare at least one sequence first: a world whose calls each work "
                "alone can still forget what the previous one did."
            )
        path = save(world, destination, notes=str(args.get("notes") or ""))
        tables = world.state()
        return _ok(
            f"Saved to {path}.\n"
            f"{len(world.handlers)} tools, {len(tables)} tables, "
            f"{sum(len(rows) for rows in tables.values())} rows.\n"
            f"score {report.score:.2f}"
        )

    server = create_sdk_mcp_server(
        name=WORLD_SERVER,
        version="0.1.0",
        tools=[
            create_schema,
            seed,
            define_handler,
            run_tool,
            declare_sequence,
            drop_sequence,
            check_world,
            save_world,
        ],
    )
    return server, world


TOOL_NAMES = (
    "create_schema",
    "seed",
    "define_handler",
    "run_tool",
    "declare_sequence",
    "drop_sequence",
    "check_world",
    "save_world",
)
