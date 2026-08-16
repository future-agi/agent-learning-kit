"""The tools that write scenarios, and the gates that decide one may be kept.

A scenario is accepted by being *proved*, not by looking right. ``submit_scenario`` restores a
fresh world, applies the scenario's own setup, plays its reference solution through it, and runs
the checks of every sub-goal it names. They must pass. Then it does the same with no solution at
all, and they must fail. Only then is it kept.

Both gates are code. No model is asked whether a scenario is good; the environment decides.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claude_agent_sdk import create_sdk_mcp_server, tool

from .amend import add_rule, drop_rule, fix_tool, widen
from .contract import AgentContract
from .environment import (
    Catalogue,
    SubGoal,
    load_catalogue,
    load_simulator_prompt,
    save_catalogue,
    validate_sub_goal,
)
from .prove import prove
from .scenario import Scenario, validate_scenario
from .tools import schema
from .world.snapshot import apply_overlay, restore

SCENARIO_SERVER = "scenarios"
SCENARIOS = "scenarios.json"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def write_scenarios(scenarios: list[Scenario], destination: Path) -> Path:
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / SCENARIOS
    path.write_text(
        json.dumps([one.model_dump() for one in scenarios], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_scenarios(destination: Path) -> list[Scenario]:
    path = Path(destination) / SCENARIOS
    if not path.exists():
        return []
    try:
        return [
            Scenario.model_validate(entry)
            for entry in json.loads(path.read_text(encoding="utf-8"))
        ]
    except Exception:
        # Written in an older shape. Better to start clean than to half-read them.
        return []


def accept_scenario(
    payload: dict[str, Any],
    *,
    world_root: Path,
    catalogue: Catalogue,
    kept: list[Scenario],
    simulator_prompt: str = "",
) -> dict[str, Any]:
    """Validate one scenario, then prove it. A plain function so both halves are testable."""
    try:
        scenario = Scenario.model_validate(payload)
    except Exception as invalid:
        return _err(f"Not kept. {invalid}"[:600])

    trial = restore(world_root)
    try:
        try:
            apply_overlay(trial, scenario.setup)
        except Exception as failed:
            return _err(
                f"Not kept. The setup rows would not go into the world: {failed}\n"
                "setup is {table: [{column: value}]}, and every column has to be one the table has."
            )
        problems = validate_scenario(scenario, catalogue, trial.state(), simulator_prompt)
    finally:
        trial.close()

    if problems:
        return _err("Not kept. Fix these and submit again:\n  - " + "\n  - ".join(problems))

    proof = prove(scenario, catalogue, world_root)
    if not proof.holds:
        return _err(f"Not kept. {proof.why()}")

    replaced = any(one.name == scenario.name for one in kept)
    kept[:] = [one for one in kept if one.name != scenario.name]
    kept.append(scenario)
    return _ok(
        f"{scenario.name} {'replaced' if replaced else 'kept'}. Proved: the solution passes its "
        f"checks, and they fail without it.\n{len(kept)} so far: "
        + ", ".join(one.name for one in kept)
    )


def not_ready(kept: list[Scenario], wanted: int, catalogue: Catalogue) -> list[str]:
    """Why this suite is not worth saving yet."""
    problems: list[str] = []
    if len(kept) < wanted:
        problems.append(
            f"{len(kept)} of {wanted} scenarios so far. Keep writing; the ones that find "
            "something are usually the awkward ones."
        )
    elif len(kept) > wanted:
        problems.append(
            f"{len(kept)} scenarios but {wanted} were asked for. Drop the ones that add least "
            "with drop_scenario. The number came from the person who asked."
        )
    # Sub-goals are shared so results roll up. A suite where every scenario invents its own is a
    # suite whose results cannot be added together.
    used = [name for one in kept for name in one.sub_goals]
    if kept and len(used) > 2 and len(set(used)) == len(used):
        problems.append(
            "no sub-goal is used by more than one scenario, so nothing rolls up across the "
            "suite. Reuse the catalogue where the same thing is being checked."
        )
    return problems


def scenario_tools(
    contract: AgentContract, world_root: Path, destination: Path, *, wanted: int
) -> tuple[Any, list[Scenario]]:
    """A server for writing scenarios against one built environment."""
    kept: list[Scenario] = load_scenarios(destination)
    catalogue = load_catalogue(destination)
    simulator_prompt = load_simulator_prompt(destination)
    target = {"count": wanted}

    @tool(
        "inspect_world",
        "Look at what is in the world. Without a table, lists the tables and how many rows each "
        "holds; with one, returns rows from it. `matching` is plain text, not SQL.",
        schema({"table": str, "limit": int, "matching": str}, []),
    )
    async def inspect_world(args: dict[str, Any]) -> dict[str, Any]:
        world = restore(world_root)
        try:
            state = world.state()
            table = str(args.get("table") or "")
            if not table:
                lines = [f"{n}: {len(r)} rows" for n, r in sorted(state.items())]
                if catalogue.sub_goals:
                    lines.append(
                        "\nsub-goals available: " + ", ".join(sorted(catalogue.names()))
                    )
                return _ok("\n".join(lines) or "this world has no tables")
            if table not in state:
                return _err(f"no table {table!r}; this world has {', '.join(sorted(state))}")
            rows = state[table]
            matching = str(args.get("matching") or "").strip()
            if matching:
                needle = matching.lower()
                found = [r for r in rows if needle in json.dumps(r, default=str).lower()]
                if not found:
                    return _ok(
                        f"nothing in {table} contains {matching!r}, but it holds {len(rows)} rows."
                    )
                rows = found
            shown = rows[: int(args.get("limit") or 20)]
            return _ok(
                f"{len(rows)} rows, showing {len(shown)}:\n"
                + "\n".join(json.dumps(r, default=str) for r in shown)
            )
        finally:
            world.close()

    @tool(
        "try_calls",
        "Run calls against a throwaway copy of the world and see the state they leave. Use it to "
        "work out a scenario's solution and what its checks should assert. Nothing is saved.",
        schema({"calls": list, "setup": dict}, ["calls"]),
    )
    async def try_calls(args: dict[str, Any]) -> dict[str, Any]:
        world = restore(world_root)
        try:
            try:
                apply_overlay(world, args.get("setup") or {})
            except Exception as failed:
                return _err(f"the setup rows would not go in: {failed}")
            lines: list[str] = []
            for step in args.get("calls") or []:
                if not isinstance(step, dict):
                    return _err("each call must be an object with a tool and arguments")
                call = world.call(str(step.get("tool") or ""), step.get("arguments") or {})
                if call.refused:
                    lines.append(f"{call.name}: refused — {call.error}")
                elif not call.ok:
                    lines.append(f"{call.name}: CRASHED — {call.error}")
                else:
                    lines.append(
                        f"{call.name}: ok — {json.dumps(call.result, default=str)[:200]}"
                    )
            state = world.state()
            lines.append(
                "state afterwards: "
                + ", ".join(f"{n}.count={len(r)}" for n, r in sorted(state.items()))
            )
            for name, rows in sorted(state.items()):
                if rows and len(rows) <= 6:
                    lines.append(f"{name}: " + json.dumps(rows, default=str)[:700])
            return _ok("\n".join(lines) or "no calls were made")
        finally:
            world.close()

    @tool(
        "add_sub_goal",
        "Add a named thing this agent can be checked on, shared by every scenario that needs it. "
        "`check` is Python: define check(world, calls) returning a sentence when something is "
        "wrong, or None when it held. `world` is the environment afterwards; `calls` is every "
        "tool call made, each with .name, .arguments, .ok and .refused — so a check can insist a "
        "call happened with the right arguments, not merely that it happened.\n\n"
        "Use `judged` only where nothing observable settles it, saying what a model must decide "
        "and why code cannot.",
        schema({"name": str, "what": str, "check": str, "judged": str}, ["name", "what"]),
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
        catalogue.sub_goals = [one for one in catalogue.sub_goals if one.name != sub_goal.name]
        catalogue.sub_goals.append(sub_goal)
        save_catalogue(catalogue, destination)
        return _ok(
            f"{sub_goal.name} added"
            + ("" if sub_goal.deterministic() else " (judged, not deterministic)")
            + f". The catalogue has {len(catalogue.sub_goals)}: "
            + ", ".join(sorted(catalogue.names()))
        )

    @tool(
        "submit_scenario",
        "Keep one scenario. It is proved before it is kept: its solution is played through a "
        "fresh world and its sub-goals' checks must pass, then the same checks run with nothing "
        "done and must fail.\n\n"
        "  name / use_case / tests\n"
        "  setup: {table: [{column: value}]} — what this scenario changes after reset\n"
        "  instruction: the task. For a conversational agent it fills the simulator prompt\n"
        "  variables: any other slot that prompt asks for\n"
        "  solution: [{tool, arguments}] — what a correct agent would do\n"
        "  sub_goals: names from the catalogue that must hold",
        schema(
            {
                "name": str,
                "use_case": str,
                "tests": str,
                "setup": dict,
                "instruction": str,
                "variables": dict,
                "solution": list,
                "sub_goals": list,
                "max_turns": int,
            },
            ["name", "instruction", "solution", "sub_goals"],
        ),
    )
    async def submit_scenario(args: dict[str, Any]) -> dict[str, Any]:
        return accept_scenario(
            args,
            world_root=world_root,
            catalogue=catalogue,
            kept=kept,
            simulator_prompt=simulator_prompt,
        )

    @tool(
        "amend_contract",
        "Let one of the agent's tools accept values it did not before, when the world holds "
        "something the agent has no way to name. Say why; it is recorded on the contract.",
        schema(
            {"tool_name": str, "argument": str, "values": list, "why": str},
            ["tool_name", "argument", "values", "why"],
        ),
    )
    async def amend_contract(args: dict[str, Any]) -> dict[str, Any]:
        done, said = widen(
            contract,
            world_root,
            tool_name=str(args.get("tool_name") or ""),
            argument=str(args.get("argument") or ""),
            values=[str(v) for v in (args.get("values") or [])],
            why=str(args.get("why") or ""),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "add_rule",
        "Give the agent a hard rule its source did not state, when asked for one. It is told to "
        "the agent under test and graded, so this changes what is being tested. Say why.",
        schema({"rule": str, "why": str}, ["rule", "why"]),
    )
    async def add_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = add_rule(
            contract, world_root, rule=str(args.get("rule") or ""), why=str(args.get("why") or "")
        )
        return _ok(said) if done else _err(said)

    @tool(
        "drop_rule",
        "Take away a hard rule the agent does not really have. Say why.",
        schema({"rule": str, "why": str}, ["rule", "why"]),
    )
    async def drop_rule_tool(args: dict[str, Any]) -> dict[str, Any]:
        done, said = drop_rule(
            contract, world_root, rule=str(args.get("rule") or ""), why=str(args.get("why") or "")
        )
        return _ok(said) if done else _err(said)

    @tool(
        "fix_tool",
        "Correct a tool that was read wrong, or remove one the agent does not have. Everything "
        "is built from these, so a wrong argument name produces a world that refuses everything.",
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
            world_root,
            tool_name=str(args.get("tool_name") or ""),
            why=str(args.get("why") or ""),
            args=[str(a) for a in args["args"]] if args.get("args") else None,
            arg_types={str(k): str(v) for k, v in (args.get("arg_types") or {}).items()},
            description=str(args.get("description") or ""),
            remove=bool(args.get("remove")),
        )
        return _ok(said) if done else _err(said)

    @tool(
        "aim_for",
        "Set how many scenarios are wanted. Only when the person you are talking to says a "
        "number, never to get past a refusal about having written too many.",
        schema({"count": int}, ["count"]),
    )
    async def aim_for(args: dict[str, Any]) -> dict[str, Any]:
        count = int(args.get("count") or 0)
        if count < 1:
            return _err("that is not a number of scenarios worth writing")
        target["count"] = count
        return _ok(f"aiming for {count}. {len(kept)} written so far")

    @tool(
        "drop_scenario",
        "Remove a scenario by name, or all of them with name '*'.",
        schema({"name": str}, ["name"]),
    )
    async def drop_scenario(args: dict[str, Any]) -> dict[str, Any]:
        name = str(args.get("name") or "")
        if name == "*":
            kept.clear()
            return _ok("all scenarios dropped")
        before = len(kept)
        kept[:] = [one for one in kept if one.name != name]
        if len(kept) == before:
            return _err(f"no scenario called {name!r}")
        return _ok(f"{name} dropped. {len(kept)} left")

    @tool("save_scenarios", "Write the kept scenarios out.", schema({}, []))
    async def save_scenarios(_args: dict[str, Any]) -> dict[str, Any]:
        problems = not_ready(kept, target["count"], catalogue)
        if problems:
            return _err("Not saved. " + "\n  - ".join(problems))
        path = write_scenarios(kept, destination)
        judged = sum(
            1
            for one in kept
            for name in one.sub_goals
            if (found := catalogue.named(name)) and not found.deterministic()
        )
        return _ok(
            f"Saved {len(kept)} scenarios to {path}.\n"
            "Every one is proved: its solution passes its checks, and they fail without it.\n"
            f"{judged} sub-goal references are judged rather than settled by code."
        )

    server = create_sdk_mcp_server(
        name=SCENARIO_SERVER,
        version="0.1.0",
        tools=[
            inspect_world,
            try_calls,
            add_sub_goal,
            submit_scenario,
            amend_contract,
            add_rule_tool,
            drop_rule_tool,
            fix_tool_tool,
            aim_for,
            drop_scenario,
            save_scenarios,
        ],
    )
    return server, kept


TOOL_NAMES = (
    "inspect_world",
    "try_calls",
    "add_sub_goal",
    "submit_scenario",
    "amend_contract",
    "add_rule",
    "drop_rule",
    "fix_tool",
    "aim_for",
    "drop_scenario",
    "save_scenarios",
)


def world_summary(world_root: Path) -> str:
    """What is in the built environment, for grounding the writer before it asks."""
    world = restore(world_root)
    try:
        state = world.state()
        lines = [f"  {name}: {len(rows)} rows" for name, rows in sorted(state.items())]
        catalogue = load_catalogue(world_root)
        if catalogue.sub_goals:
            lines.append("\nSUB-GOALS already defined (reuse these, do not restate them):")
            lines += [f"  {one.name}: {one.what}" for one in catalogue.sub_goals]
        return "THE BUILT WORLD (restored fresh for every scenario):\n" + "\n".join(lines)
    finally:
        world.close()
