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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Callable

from .actors import is_pre_run
from .world.handler_child import (
    answered,
    came_before,
    evaluate_authored,
    exactly_once,
    same_id,
    same_value,
    succeeded,
)
from .world.handler_child import takes_reference as _takes_reference
from .world.runtime import Call, GeneratedWorld
from .world_door import AGENT, is_committed, is_read, logged_as, named_alike, row_arguments, same_write


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
        helpers=check_namespace(),
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


# What the action log says of a call the world timed out on: refused before its commit, or committed with the answer
# lost (`world_door._faulted`).
TIMED_OUT = ("timeout_before_commit", "response_lost")


def _agent_writes(world: Any) -> list[Mapping[str, Any]]:
    """The agent's attempts to change the world this trial, in the order the world saw them."""
    return [
        row
        for row in world.events(actor_id=AGENT)
        if not is_read(row) and not is_pre_run(row.get("idempotency_key"))
    ]


def _fixed(reference: Sequence[Mapping[str, Any]], action: str) -> set[str]:
    """The arguments the reference fixes for this action: a check's reference keeps only those of each step."""
    return {
        field
        for step in reference or ()
        if named_alike(action, str(step.get("tool") or ""))
        for field in step.get("arguments") or {}
    }


def _repeats(
    first: Mapping[str, Any],
    later: Mapping[str, Any],
    reference: Sequence[Mapping[str, Any]],
    own_words: Sequence[str] = (),
) -> bool:
    """The same write of one record, as duplicate commits read it: rewording an argument the check names as the
    agent's own words (`own_words`) is still the same write."""
    action = logged_as(first)
    return logged_as(later) == action and same_write(first, later, _fixed(reference, action), own_words)


def _pair(first: Mapping[str, Any], later: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "action": logged_as(first),
        "arguments": row_arguments(first.get("arguments")),
        "first_action_id": first.get("action_id"),
        "later_action_id": later.get("action_id"),
        "later_outcome": later.get("outcome"),
    }


def retried_timeouts(
    world: Any, reference: Sequence[Mapping[str, Any]] = (), own_words: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Each write of the agent's the world timed out on that the agent then attempted again on the same record.
    `own_words` names arguments the agent may reword on a retry (a note); every other argument has to match."""
    rows = _agent_writes(world)
    found = []
    for index, first in enumerate(rows):
        if first.get("error") not in TIMED_OUT:
            continue
        retry = next((later for later in rows[index + 1 :] if _repeats(first, later, reference, own_words)), None)
        if retry is not None:
            found.append(_pair(first, retry))
    return found


def repeated_commits(
    world: Any, reference: Sequence[Mapping[str, Any]] = (), own_words: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Each write of the agent's the world committed again on the same record: one entry per extra commit.
    `own_words` names arguments the agent may reword on a retry (a note); every other argument has to match."""
    committed = [row for row in _agent_writes(world) if is_committed(row)]
    found = []
    for index, later in enumerate(committed):
        first = next((row for row in committed[:index] if _repeats(row, later, reference, own_words)), None)
        if first is not None:
            found.append(_pair(first, later))
    return found


def committed_writes(world: Any) -> list[dict[str, Any]]:
    """The agent's writes the world committed. None means any change the reply reports never happened."""
    return [
        {"action": logged_as(row), "arguments": row_arguments(row.get("arguments")), "action_id": row.get("action_id")}
        for row in _agent_writes(world)
        if is_committed(row)
    ]


# Read-only helpers every check is given beside `world` and `calls`, each with whether it reads what a call came to
# (went through, answered, committed, timed out): a check that uses one is never proof of a step that did not run, as
# a call recorded but not run came to nothing. One table, so a new helper is never given without saying which.
_HELPERS: dict[str, tuple[Callable[..., Any], bool]] = {
    "answered": (answered, True),
    "succeeded": (succeeded, True),
    "came_before": (came_before, True),
    "exactly_once": (exactly_once, True),
    "same_id": (same_id, False),
    "same_value": (same_value, False),
    "retried_timeouts": (retried_timeouts, True),
    "repeated_commits": (repeated_commits, True),
    "committed_writes": (committed_writes, True),
}
CHECK_HELPERS = {name: helper for name, (helper, _reads) in _HELPERS.items()}
# The helpers that read what a call came to.
OUTCOME_HELPERS = frozenset(name for name, (_helper, reads) in _HELPERS.items() if reads)


def check_namespace() -> dict[str, Any]:
    """The names every check is given when it runs, wherever it runs (authoring, a hosted trial's phase worker, an
    injected local world): the helpers authoring tells checks to use, such as ``same_id``. One set, so a check that
    proves while authoring never fails on a name a trial does not give it."""
    return dict(CHECK_HELPERS)


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
