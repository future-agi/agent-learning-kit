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

from ..environment import (
    SubGoal,
    load_simulator_prompt,
    load_catalogue,
    save_catalogue,
    save_simulator_prompt,
    validate_simulator_prompt,
    validate_sub_goal,
)
from ..tools import schema
from ..amend import add_rule, drop_rule, fix_tool, widen
from ..contract import AgentContract
from .kinds import for_contract
from .probe import dirty_state, probe
from .runtime import GeneratedWorld
from .snapshot import DATABASE, read_manifest, restore, save

WORLD_SERVER = "world"

# What a handler is actually given. Said again here, and not only in the skill, because this is
# where the mistake surfaces: a handler that crashed has a model reading *this* message, and an
# error naming the failure without naming the API produces the same wrong guess again. Three
# identical attempts at one handler is what that costs.
DB_API = (
    "Inside a handler, `db` has exactly three methods and no cursors:\n"
    '    db.query("SELECT * FROM t WHERE id = ?", [x])   -> list of dicts, [] if none\n'
    '    db.one("SELECT * FROM t WHERE id = ?", [x])      -> one dict, or None\n'
    '    db.execute("INSERT INTO t (a) VALUES (?)", [x])  -> number of rows changed\n'
    "Rows are dicts, read by column name. db.execute returns a count, not a cursor, so "
    "calling .fetchone(), .fetchall() or .lastrowid on any of these is a mistake. You also have "
    "`args`, `ToolError` and `json`, and nothing else — do not import anything."
)

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
    # An existing world is picked up rather than replaced. Amending one is the ordinary case
    # once it has been built once, and starting empty every time would mean rebuilding a
    # catalogue from scratch to add a single item to it.
    existing = (destination / DATABASE).exists()
    world = restore(destination) if existing else GeneratedWorld(":memory:")
    world.name = contract.agent
    kind = for_contract(contract)
    catalogue = load_catalogue(destination)
    scores: list[float] = []
    sequences: list[dict[str, Any]] = (
        list(read_manifest(destination).get("sequences") or []) if existing else []
    )

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
        "change_data",
        "Change or remove rows already in the world: one UPDATE or DELETE statement. Seeding "
        "only ever inserts, so without this a row put in wrong can never be taken out, and the "
        "only way left to make a check pass is to change the contract, which is the wrong "
        "repair. Use inspect_world to read; this is for changing.",
        {"sql": str},
    )
    async def change_data(args: dict[str, Any]) -> dict[str, Any]:
        statement = str(args.get("sql") or "").strip()
        verb = statement.split(None, 1)[0].upper() if statement else ""
        if verb not in ("UPDATE", "DELETE"):
            return _err(
                "this runs one UPDATE or DELETE. Use seed to add rows, create_schema to change "
                "the shape of a table, and inspect_world to look."
            )
        try:
            changed = world.connection.execute(statement).rowcount
            world.connection.commit()
        except Exception as failed:
            world.connection.rollback()
            return _err(f"rejected: {failed}")
        counts = ", ".join(f"{n}: {len(r)}" for n, r in sorted(world.state().items()))
        return _ok(f"{changed} rows changed. The world now holds {counts}")

    @tool(
        "define_handler",
        "Define one tool's implementation. The source must define handle(args, db) and is run "
        "immediately against the seeded world, so errors come straight back.",
        schema(
            {"tool_name": str, "source": str, "smoke_arguments": dict},
            ["tool_name", "source"],
        ),
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
            said = f"{name} not kept, it crashed on its smoke call: {call.error}"
            # A crash is nearly always the handler reaching for something it does not have, so
            # the answer says what it does have rather than only what went wrong.
            return _err(f"{said}\n\n{DB_API}")
        return _ok(f"{name} defined and ran. Returned {_brief(call.result)}")

    @tool(
        "run_tool",
        "Call a defined tool and see what the world does. Use this to check a refusal works.",
        schema({"tool_name": str, "arguments": dict}, ["tool_name"]),
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
        "'table.count'. Declaring the same name again replaces it.\n\n"
        "Every sequence runs on its own from the frozen world: the state is put back before each "
        "one, so they never see each other's rows and expect_state is an absolute count, not a "
        "running total. If a sequence fails, the fault is in that sequence, not in the ones "
        "declared before it.",
        schema({"name": str, "calls": list, "expect_state": dict}, ["name", "calls"]),
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
        "amend_contract",
        "Let one of the agent's tools accept values it did not before. Use this when the world "
        "holds something the agent has no way to name: an item added to the menu that item_id "
        "does not list is dead data, and a scenario about it can only fail.\n\n"
        "Only widen where the agent genuinely should accept the value. Say why in one line; it "
        "is recorded on the contract, because a contract nobody can audit is worth nothing.",
        {"tool_name": str, "argument": str, "values": list, "why": str},
    )
    async def amend_contract(args: dict[str, Any]) -> dict[str, Any]:
        done, said = widen(
            contract,
            destination,
            tool_name=str(args.get("tool_name") or ""),
            argument=str(args.get("argument") or ""),
            values=[str(value) for value in (args.get("values") or [])],
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "add_rule",
        "Give the agent a hard rule its source did not state, when the operator asks for one. "
        "The agent under test is told every rule and the judge grades against them, so this "
        "changes what is being tested. Say why in one line; it is recorded on the contract.",
        {"rule": str, "why": str},
    )
    async def add_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = add_rule(
            contract,
            destination,
            rule=str(args.get("rule") or ""),
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "inspect_world",
        "Look at what is in the world you are building. Without a table, lists the tables and "
        "how many rows each holds; with one, returns rows from it.",
        schema({"table": str, "limit": int}, []),
    )
    async def inspect_world(args: dict[str, Any]) -> dict[str, Any]:
        state = world.state()
        table = str(args.get("table") or "")
        if not table:
            return _ok(
                "\n".join(
                    f"{name}: {len(rows)} rows" for name, rows in sorted(state.items())
                )
                or "no tables yet"
            )
        if table not in state:
            return _err(
                f"no table {table!r}; there is {', '.join(sorted(state)) or 'nothing'}"
            )
        rows = state[table]
        shown = rows[: int(args.get("limit") or 15)]
        return _ok(
            f"{len(rows)} rows, showing {len(shown)}:\n"
            + "\n".join(json.dumps(row, default=str) for row in shown)
        )

    @tool(
        "drop_rule",
        "Take away a hard rule the agent does not really have. A rule nobody has is worse than "
        "a missing one: the agent is told to obey it and graded for not doing something it was "
        "never supposed to do. Say why.",
        {"rule": str, "why": str},
    )
    async def drop_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = drop_rule(
            contract,
            destination,
            rule=str(args.get("rule") or ""),
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "fix_tool",
        "Correct a tool that was read wrong, or remove one the agent does not have. `args` "
        "replaces its argument names in order; `arg_types` and `description` update those. Set "
        "`remove` to take the tool away entirely. Everything downstream is built from these, so "
        "a wrong argument name produces a world that refuses everything. Say why.",
        schema(
            {
                "tool_name": str,
                "args": list,
                "arg_types": dict,
                "description": str,
                "remove": bool,
                "why": str,
            },
            ["tool_name", "why"],
        ),
    )
    async def fix_tool_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = fix_tool(
            contract,
            destination,
            tool_name=str(args.get("tool_name") or ""),
            why=str(args.get("why") or ""),
            args=[str(a) for a in args["args"]] if args.get("args") else None,
            arg_types={
                str(k): str(v) for k, v in (args.get("arg_types") or {}).items()
            },
            description=str(args.get("description") or ""),
            remove=bool(args.get("remove")),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "write_simulator_prompt",
        "Write the prompt that drives the simulated user of this agent, for a conversational "
        "agent only. It is written once and every scenario fills its slots, so leave variables "
        "as {{ instruction }} and any others this agent needs.\n\n"
        "It has to cover how a person in this conversation actually behaves: that they are "
        "living the situation rather than describing it, that they speak one turn at a time, "
        "that they never break character or explain that they are testing anything, what they "
        "know and when they may say it, and when the conversation is over. Write it for this "
        "agent, not in general.",
        schema({"prompt": str}, ["prompt"]),
    )
    async def write_simulator_prompt(args: dict[str, Any]) -> dict[str, Any]:
        prompt = str(args.get("prompt") or "")
        problems = validate_simulator_prompt(prompt)
        if problems:
            return _err("Not saved:\n  - " + "\n  - ".join(problems))
        path = save_simulator_prompt(prompt, destination)
        from ..environment import variables_in

        return _ok(
            f"Saved to {path}. Scenarios must fill: "
            + ", ".join(sorted(variables_in(prompt)))
        )

    @tool(
        "add_sub_goal",
        "Add a named thing this agent can be checked on, shared by every scenario that needs "
        "it. Defined here, once, so results roll up: the same sub-goal failing in seven of "
        "twelve scenarios is one sentence.\n\n"
        "`check` is Python: define check(world, calls) returning a sentence when something is "
        "wrong, or None when it held. `world` is the environment afterwards; `calls` is every "
        "tool call made, each with .name, .arguments, .ok and .refused — so a check can insist "
        "a call happened with the right arguments, not merely that it happened.\n\n"
        "Use `judged` only where nothing observable settles it, saying what a model has to "
        "decide and why code cannot.",
        schema(
            {"name": str, "what": str, "check": str, "judged": str}, ["name", "what"]
        ),
    )
    async def add_sub_goal(args: dict[str, Any]) -> dict[str, Any]:
        sub_goal = SubGoal(
            name=str(args.get("name") or ""),
            what=str(args.get("what") or ""),
            check=str(args.get("check") or ""),
            judged=str(args.get("judged") or ""),
        )
        problems = validate_sub_goal(sub_goal)
        if problems:
            return _err("Not added:\n  - " + "\n  - ".join(problems))
        catalogue.sub_goals = [
            one for one in catalogue.sub_goals if one.name != sub_goal.name
        ]
        catalogue.sub_goals.append(sub_goal)
        save_catalogue(catalogue, destination)
        settled = sum(1 for one in catalogue.sub_goals if one.deterministic())
        return _ok(
            f"{sub_goal.name} added. The catalogue has {len(catalogue.sub_goals)}, "
            f"{settled} settled by code: " + ", ".join(sorted(catalogue.names()))
        )

    @tool(
        "check_world",
        "Exercise every tool with a valid call, a nonexistent id, and a missing argument, then "
        "run the declared sequences. Reports what is wrong without saving anything.\n\n"
        "Sequences are run independently from the frozen world, so a failure is never caused by "
        "another sequence. Fix the failures it names; declaring more sequences only adds more "
        "probes to pass.",
        {},
    )
    async def check_world(_args: dict[str, Any]) -> dict[str, Any]:
        report = probe(world, contract, sequences=sequences, kind=kind)
        scores.append(report.score)
        # Saying the score is going nowhere, rather than leaving it to be noticed. A stage that
        # has misdiagnosed something will otherwise keep applying the same non-fix, and every
        # round of that costs money and gets no closer.
        stuck = ""
        if len(scores) >= 3 and len(set(round(s, 2) for s in scores[-3:])) == 1:
            stuck = (
                "\n\nThis is the third check with the same score. Whatever you are changing is "
                "not what is failing. Read the failures above literally and fix one of them, or "
                "say what you are stuck on."
            )
        return _ok(f"{report.summary()}\nscore {report.score:.2f}{stuck}")

    @tool(
        "save_world",
        "Freeze the world and write it out. Refused unless it passes its own checks.",
        schema({"notes": str}, []),
    )
    async def save_world(args: dict[str, Any]) -> dict[str, Any]:
        report = probe(world, contract, sequences=sequences, kind=kind)
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
        # The environment is not only the world. Every scenario is a delta on what is built
        # here, so a catalogue nobody wrote means every scenario invents its own wording and
        # nothing rolls up across the suite.
        if not catalogue.sub_goals:
            return _err(
                "Not saved. No sub-goals yet. They are defined here, once, and every scenario "
                "names the ones it needs — that is what makes results add up across the suite. "
                "Add them with add_sub_goal."
            )
        settled = [one for one in catalogue.sub_goals if one.deterministic()]
        if not settled:
            return _err(
                "Not saved. Every sub-goal is judged by a model. Most of what this agent does "
                "leaves a trace in the world or in its calls, and those should be settled by "
                "code; a judge is the fallback for what leaves none."
            )
        if contract.conversational and not load_simulator_prompt(destination):
            return _err(
                "Not saved. This agent is conversational, so it needs a simulator prompt for "
                "the person on the other side. Write it with write_simulator_prompt."
            )
        dirty = dirty_state(world, sequences, kind)
        if dirty:
            counts = world.state()
            listed = ", ".join(f"{name} ({len(counts[name])} rows)" for name in dirty)
            return _err(
                f"Not saved. These hold rows left over from building: {listed}.\n"
                "This is the state every scenario starts from, so those rows would appear in "
                "every test as somebody else's order already in the cart. Clear them with "
                "change_data (DELETE FROM ...), keep the catalogue, and save again."
            )
        # What the world publishes when something resets it. Without this a restored world
        # announces no tools at all, so anything driving it through the environment interface
        # sees an agent with nothing to call.
        world.tools = [
            {
                "name": spec.name,
                "description": spec.description,
                "parameters": {
                    arg: {
                        "type": spec.arg_types.get(arg, "str"),
                        "values": spec.arg_values.get(arg),
                    }
                    for arg in spec.args
                },
            }
            for spec in contract.tools
        ]
        path = save(
            world,
            destination,
            notes=str(args.get("notes") or ""),
            sequences=sequences,
        )
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
            change_data,
            define_handler,
            run_tool,
            declare_sequence,
            drop_sequence,
            amend_contract,
            add_rule_tool,
            drop_rule_tool,
            fix_tool_tool,
            inspect_world,
            write_simulator_prompt,
            add_sub_goal,
            check_world,
            save_world,
        ],
    )
    return server, world


TOOL_NAMES = (
    "create_schema",
    "seed",
    "change_data",
    "define_handler",
    "run_tool",
    "declare_sequence",
    "drop_sequence",
    "amend_contract",
    "add_rule",
    "drop_rule",
    "fix_tool",
    "inspect_world",
    "write_simulator_prompt",
    "add_sub_goal",
    "check_world",
    "save_world",
)
