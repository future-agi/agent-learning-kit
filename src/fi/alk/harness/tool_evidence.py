"""Where a tool call's record came from.

Four sources can see a tool call, in this order:

1. ``world``: the world's own record, where one record is one call the kit carried out (a call the agent
   returned and the kit ran, or a call through the agent's tool service).
2. ``span``: a tool span from the agent's own tracing (OpenTelemetry or traceAI).
3. ``tracer``: a line of the tool-trace file: the kit's tracer on the tool's function, a framework hook, or a line
   the agent wrote itself. The line's own mark is kept in ``trace_mark``.
4. ``agent``: the agent's own account of its calls: the calls in its reply, with or without their responses, and a
   list it serves from its own endpoint. The kit did not see these run, and never replays them through the world.

Every record names the source it came from (``source``) and lists the other sources that saw the same call
(``seen_by``). Naming the sources changes no call list: each runner reads its calls as it did before.

- A chat trial's calls are the ones it recorded as they ran (the world's, and the agent's own account where the
  agent reported a call as already run), else the trace file's lines.
- A voice trial's calls are the trace file's lines whenever they hold a graded call, else the world's table.
- On both, the file's span lines are joined last. A span paired with another record never takes that record's
  graded facts (the tool, arguments, outcome and result). It is listed as having seen the call, and lends only what
  it measured: the agent that made the call, the hand-off mark, the times and the runtime's ids. A call only a span
  saw is a call from the span.
- A call's time is not a graded fact. Its start and end come from a paired source that measured them (a span, or the
  hook's line); a source that stamps a call only when it reads the result carries no start time. Where no paired
  source measured them, the record's own stamp stays.

A call no source saw is not here at all; a check that needs it reads as evidence missing, never as passed.
"""

from __future__ import annotations

from collections.abc import Iterable

from .world.runtime import Call

WORLD = "world"
SPAN = "span"
TRACER = "tracer"
AGENT = "agent"
ORDER = (WORLD, SPAN, TRACER, AGENT)
# The fields of a call that name its sources. They are not part of the call: no comparison, pairing or grading reads
# them.
FIELDS = ("source", "seen_by", "trace_mark")


def tagged(calls: Iterable[Call], source: str) -> list[Call]:
    """``calls`` with ``source`` named on each that names none yet."""
    kept = list(calls)
    for call in kept:
        if not call.source:
            call.source = source
    return kept


def seen(call: Call, source: str) -> None:
    """List ``source`` among the sources that saw ``call``, in the order, unless the record is its own."""
    if not source or source == call.source or source in call.seen_by:
        return
    call.seen_by = sorted(
        [*call.seen_by, source], key=lambda one: ORDER.index(one) if one in ORDER else len(ORDER)
    )
