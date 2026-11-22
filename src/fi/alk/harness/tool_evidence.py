"""Where a tool call's record came from, and how the records several sources kept of one call become one.

Four sources can see a tool call. In order:

1. ``world``: the world's own record, where one record is one call the kit carried out (a call the agent
   returned and the kit ran, or a call through the agent's tool service).
2. ``span``: a tool span from the agent's own tracing (OpenTelemetry or traceAI).
3. ``tracer``: a line of the tool-trace file: the kit's tracer on the tool's function, a framework hook, or a line
   the agent wrote itself. The line's own mark is kept in ``trace_mark``.
4. ``agent``: the agent's own account of its calls: the calls in its reply, with or without their responses, and a
   list it serves from its own endpoint. The kit did not see these run, and never replays them through the world.

Each source hands over one record per call. When two sources saw one call, the record of the higher source wins:

- The winner fixes the graded facts: that the call happened, its tool, arguments, outcome and result. They are
  never blended from two sources.
- A record with no arguments and no result never wins over one that has them; it is only listed as having seen
  the call.
- The other record supplies only what the winner does not carry at all: times, the hand-off mark, the agent that
  made the call, the runtime's ids.
- The record names the source that won (``source``) and lists the others that saw the call (``seen_by``).

A call no source saw is not here at all; a check that needs it reads as evidence missing, never as passed.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from .world.runtime import Call

WORLD = "world"
SPAN = "span"
TRACER = "tracer"
AGENT = "agent"
ORDER = (WORLD, SPAN, TRACER, AGENT)

# The facts a check grades. The winning source gives all of them, or none.
_GRADED = (
    "name",
    "arguments",
    "result",
    "ok",
    "error",
    "refused",
    "refusal_code",
    "input_omitted",
    "output_omitted",
)


def tagged(calls: Iterable[Call], source: str) -> list[Call]:
    """``calls`` with ``source`` named on each that names none yet."""
    kept = list(calls)
    for call in kept:
        if not call.source:
            call.source = source
    return kept


def rank(call: Call) -> int:
    """The call's source's place in the order; a record that names no source comes after all four."""
    return ORDER.index(call.source) if call.source in ORDER else len(ORDER)


def bare(call: Call) -> bool:
    """A record that says a call happened and nothing about what it was given or gave back. A span that recorded
    neither writes empty text for both (the Google ADK span of 19 Nov), which says nothing either."""
    return not call.arguments and _nothing(call.result) and not call.error


def _nothing(value: object) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def _beats(one: Call, other: Call) -> bool:
    return (bare(one), rank(one)) < (bare(other), rank(other))


def _seen(winner: Call, loser: Call) -> list[str]:
    seen = {*winner.seen_by, *loser.seen_by, loser.source} - {"", winner.source}
    return sorted(seen, key=lambda source: ORDER.index(source) if source in ORDER else len(ORDER))


def settle(kept: Call, other: Call) -> None:
    """``kept`` and ``other`` are one call: make ``kept`` the one record of it.

    ``kept`` stays where it is in its list; it takes the graded facts of whichever of the two wins, and from the
    loser only what the winner carries nothing of."""
    winner, loser = (other, kept) if _beats(other, kept) else (kept, other)
    graded = {name: getattr(winner, name) for name in _GRADED}
    seen = _seen(winner, loser)
    filled = {
        "at": winner.at or loser.at,
        "ended_at": winner.ended_at or loser.ended_at,
        "delegated_to": winner.delegated_to or loser.delegated_to,
        "transferred": winner.transferred or loser.transferred,
        "handoff": winner.handoff or loser.handoff,
        "answer_lost": winner.answer_lost or loser.answer_lost,
        "trace_mark": winner.trace_mark or loser.trace_mark,
    }
    # Who made the call, and its place among the agents, each go whole from one record: a parent is an id in the
    # same record, and a worker's id and label name one worker.
    worker = winner if (winner.worker_id or winner.worker_label) else loser
    task = winner if (winner.task_id or winner.task_label) else loser
    ids = winner if winner.runtime_ids else loser
    filled.update(
        worker_id=worker.worker_id,
        worker_label=worker.worker_label,
        task_id=task.task_id,
        task_label=task.task_label,
        runtime_ids=dict(ids.runtime_ids),
        parent_span_id=ids.parent_span_id,
        relationship=ids.relationship,
    )
    for name, value in {**graded, **filled}.items():
        setattr(kept, name, value)
    kept.source = winner.source
    kept.seen_by = seen


def call_id(call: Call) -> str:
    """The id the agent's runtime gave the call, when its record carries one."""
    return call.runtime_ids.get("call_id") or call.runtime_ids.get("tool_call_id") or ""


def join(
    *levels: Sequence[Call],
    same_tool: Callable[[Call, Call], bool],
    same_arguments: Callable[[Call, Call], bool],
) -> tuple[Call, ...]:
    """One record per call from records of the same calls, a level per source or group of sources, highest first.

    A record pairs with a record already kept when both name one tool and: both carry a call id and it is the same
    (the one with the same arguments first, since an id may repeat within a reply); or not both carry one and their
    arguments are the same; or, failing that, either names no arguments. The first such kept record in order is
    taken, and each kept record pairs at most once per level, so a level's two calls never fold into one. A record
    that pairs with none is a call only its source saw. Hand-offs are kept as they are."""
    kept: list[Call] = []
    for level in levels:
        taken: set[int] = set()
        added: list[Call] = []
        for call in level:
            match = None if call.handoff else _match(call, kept, taken, same_tool, same_arguments)
            if match is None:
                added.append(call)
                continue
            settle(match, call)
            taken.add(id(match))
        kept.extend(added)
    return tuple(sorted(kept, key=lambda call: call.at))


def _match(
    call: Call,
    kept: Sequence[Call],
    taken: set[int],
    same_tool: Callable[[Call, Call], bool],
    same_arguments: Callable[[Call, Call], bool],
) -> Call | None:
    candidates = [one for one in kept if id(one) not in taken and not one.handoff and same_tool(one, call)]
    mine = call_id(call)
    if mine:
        same_id = [one for one in candidates if call_id(one) == mine]
        # An id may repeat within a reply, so of the records under it, the one with the same arguments first.
        alike = next((one for one in same_id if same_arguments(one, call)), None)
        if alike is not None or same_id:
            return alike or same_id[0]
    # Two ids, and not the same: two calls.
    open_ = [one for one in candidates if not (mine and call_id(one))]
    exact = next((one for one in open_ if same_arguments(one, call)), None)
    if exact is not None:
        return exact
    # A record that names no arguments contradicts none, as in the span pairing; it is taken only after every exact
    # match, so it never takes the place of a call recorded in full.
    return next((one for one in open_ if not one.arguments or not call.arguments), None)
