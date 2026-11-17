"""Running a check the harness wrote, and deciding what its answer means.

A check is Python because an environment can be a database, a filesystem or a page, and any
little assertion language invented here would fit only the first. It is given the two things a
run leaves behind and returns a sentence when something is wrong:

    def check(world, calls):
        rows = world.state()["orders"]
        if len(rows) != 1:
            return f"{len(rows)} orders, expected 1"
        if not any(c.name == "order_combo_meal" for c in calls):
            return "the combo was never ordered"
        return None

``world`` is the environment afterwards. ``calls`` is every tool call that was made, each with
its arguments and whether it succeeded — so a check can insist not only that a call happened but
that it happened with the right arguments, which is the difference between booking 11 PM and
booking 10 PM.

A check that raises is a broken check, not a failed one, and is reported that way. Confusing the
two would let a typo read as a finding about the agent.
"""

from __future__ import annotations

import ast
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .world.handler_child import evaluate_authored, same_id
from .world.handler_child import takes_reference as _takes_reference
from .world.runtime import Call, GeneratedWorld


@dataclass
class Outcome:
    """What one check said."""

    name: str
    held: bool
    said: str = ""
    broken: bool = False

    def line(self) -> str:
        mark = "!" if self.broken else ("x" if self.held else " ")
        return f"  [{mark}] {self.name}" + (f" — {self.said}" if self.said else "")


def takes_reference(checker: object) -> bool:
    """Whether a check, or its source, accepts the reference call sequence."""
    return _takes_reference(checker)


def reference_of(solution: Sequence[Any]) -> list[dict[str, Any]]:
    """The reference a check is given: each step's tool and only the arguments it marks fixed."""
    steps = []
    for step in solution or ():
        step = step if isinstance(step, dict) else step.model_dump()
        arguments = dict(step.get("arguments") or {})
        fixed = step.get("fixed")
        if isinstance(fixed, (dict, list)):
            arguments = {
                name: value for name, value in arguments.items() if name in fixed
            }
        steps.append({"tool": step.get("tool"), "arguments": arguments})
    return steps


def _evaluate(
    source: str,
    world: Any,
    *,
    kind: str,
    name: str,
    calls: Sequence[Any] = (),
    reference: Sequence[Any] = (),
) -> Any:
    if getattr(world, "isolated", False):
        from .world.handler_host import isolation_mode, run_authored

        if mode := isolation_mode():
            return run_authored(
                source,
                world,
                kind=kind,
                name=name,
                calls=calls,
                reference=reference,
                mode=mode,
            )
    return evaluate_authored(
        source,
        world,
        kind=kind,
        name=name,
        calls=calls,
        reference=reference,
    )


def check_reads(checker: object) -> tuple[str, ...]:
    """The evidence a check reads, from its own code: `world_state` when it reads the world, `tool_calls` when it
    reads the agent's calls or the world's action log (`world.events`). A check whose code cannot be read is taken to
    read both."""
    both = ("world_state", "tool_calls")
    source = checker if isinstance(checker, str) else getattr(checker, "_alk_source", None)
    if not source:
        return both
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return both
    entry = getattr(checker, "_alk_entry", "check")
    function = next(
        (node for node in body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == entry),
        None,
    )
    if function is None:
        return both
    params = [one.arg for one in function.args.args]
    world, calls = (params + ["", ""])[:2]
    names = [node.id for node in ast.walk(function) if isinstance(node, ast.Name)]
    used = set(names)
    log_reads = sum(
        1
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute)
        and node.attr == "events"
        and isinstance(node.value, ast.Name)
        and node.value.id == world
    )
    reads_log = log_reads > 0
    receivers = sum(
        1
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == world
    )
    if world and names.count(world) > receivers:
        # The world handed to a helper or another name: what that reads is not followed, so it may read either.
        return both
    out = []
    # The world read for anything but its action log is its state.
    if world and names.count(world) > log_reads:
        out.append("world_state")
    if (calls and calls in used) or reads_log:
        out.append("tool_calls")
    return tuple(out)




def check_namespace() -> dict[str, Any]:
    """The names every check is given when it runs, wherever it runs (authoring, a hosted trial's phase worker, an
    injected local world): the helpers authoring tells checks to use, such as ``same_id``. One set, so a check that
    proves while authoring never fails on a name a trial does not give it."""
    return {"same_id": same_id}


def run_check(
    source: str,
    world: GeneratedWorld,
    calls: Sequence[Call],
    *,
    name: str = "check",
    reference: Sequence[dict[str, Any]] = (),
) -> Outcome:
    """Execute one check against what the run left behind, with the reference when it takes one."""
    result = _evaluate(
        source,
        world,
        kind="check",
        name=name,
        calls=calls,
        reference=reference,
    )
    return Outcome(name, result.ok, result.said, result.broken)


def all_held(outcomes: Sequence[Outcome]) -> bool:
    return all(one.held for one in outcomes) and not any(one.broken for one in outcomes)


def broken(outcomes: Sequence[Outcome]) -> list[Outcome]:
    return [one for one in outcomes if one.broken]


def run_world_check(
    source: str, world: GeneratedWorld, *, name: str = "check"
) -> Outcome:
    """Execute one check about the world itself, rather than about a run.

    A world check asks whether the environment is usable at all, so it is written ``check(world)``
    and there are no calls to give it. Both arities are accepted, because the difference is not
    worth a rejection: a check written ``check(world, calls)`` out of habit is answering the same
    question, and gets an empty list.
    """
    result = _evaluate(source, world, kind="world-check", name=name)
    return Outcome(name, result.ok, result.said, result.broken)
