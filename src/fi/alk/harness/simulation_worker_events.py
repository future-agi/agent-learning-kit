"""Tool calls and the worker that made each, read from the spans a process's framework instrumentation ends.

The sandbox's startup hook (``simulation_mark_bootstrap``) owns the hook's lifecycle and hands every span its process
starts to :class:`_ToolSpans`; this module turns those spans into the lines of the tool trace. It sits beside the hook
in a customer process's world directory, so it runs inside the CUSTOMER's interpreter, which has no ``fi``: standard
library only, Python 3.9 syntax, and it never imports the customer's modules.

A stated framework exception: each framework's span keys and values are named here, as data tables the code reads
alike. A background job that starts after the call has ended is outside the trace: the call is sealed when it ends.
"""

from __future__ import annotations

import collections
import functools
import json
import os
import threading
import time

# The hook's own marker for a callable it already wrapped.
_PATCHED = "_alk_simulation_mark"
_TOOL_END = "_alk_tool_span_end"

# Where the span kind, the agent's name and a tool call's parts sit, in the conventions the
# framework instrumentors and the frameworks' own tracing use (traceAI and OpenInference, the
# OpenTelemetry GenAI names, then names a single framework uses).
_KIND_KEYS = ("fi.span.kind", "gen_ai.span.kind", "openinference.span.kind")
_OPERATION_KINDS = {
    "execute_tool": "TOOL",
    "invoke_agent": "AGENT",
    "create_agent": "AGENT",
}
# Frameworks whose instrumentors name the span kind under their own key, with their own values (the Claude Agent SDK's
# `subagent` is its Task tool, which runs the sub-agent).
_OWN_KINDS = {
    "claude_agent.span_kind": {
        "tool": "TOOL",
        "tool_execution": "TOOL",
        "mcp_tool": "TOOL",
        "subagent": "AGENT",
        "agent": "AGENT",
    },
    "pydantic_ai.span_kind": {"agent_run": "AGENT", "tool_call": "TOOL"},
}
# A span that is both the tool call that handed work to a worker and that worker's run, named by the worker it ran:
# the Claude Agent SDK's Task tool.
_SELF_HANDING_KEYS = ("claude_agent.subagent.type", "claude_agent.subagent_type")
_AGENT_NAME_KEYS = (
    "gen_ai.agent.name",
    "agent.name",
    "autogen.agent.name",
    "claude_agent.subagent.type",
    "claude_agent.subagent_type",
    "pydantic_ai.agent.name",
    "graph.node.id",
    "recipient_agent_type",
    "lc_agent_name",
)
# The conversation a span belongs to; a background job runs as its own conversation, named by the id the launching
# tool call returned.
_CONVERSATION_KEYS = ("gen_ai.conversation.id", "session.id")
# The text of a model call's first output message, as traceAI and OpenInference flatten it.
_SAID_KEYS = (
    "gen_ai.output.messages.0.message.content",
    "llm.output_messages.0.message.content",
)


# The trace line written when a hand-off could not be recorded (read by `call_runner.hand_off_notices`).
HAND_OFF_UNRECORDED = "handoff_unrecorded"
_LAUNCHES_MAX = 256
_GRAPH_NAMESPACE = "langgraph_checkpoint_ns"
_TOOL_NAME_KEYS = (
    "gen_ai.tool.name",
    "tool.name",
    "claude_agent.tool.name",
    "pydantic_ai.tool.name",
)
_TOOL_ARGUMENT_KEYS = (
    "gen_ai.tool.call.arguments",
    "tool.parameters",
    "input.value",
    "claude_agent.tool.input",
    "pydantic_ai.tool.args",
)
_TOOL_RESULT_KEYS = (
    "gen_ai.tool.call.result",
    "output.value",
    "claude_agent.tool.output",
    "pydantic_ai.tool.result",
)
_LIVE_SPANS_MAX = 4096
_TRACE_LINE_MAX = 65536
_PAYLOAD_MAX = 4096
# A control pass the framework records as one. Google ADK marks its agent-transfer tool by the tool's own class and
# names the agent in its ``agent_name`` argument; the OpenAI Agents SDK's instrumentation names, on the agent that was
# handed control, the agent that handed it.
_TRANSFER_TOOL_TYPES = ("TransferToAgentTool",)
_BUILTIN_TRANSFER = "transfer_to_agent"
_TRANSFER_ARGUMENT_KEYS = ("gcp.vertex.agent.tool_call_args",) + _TOOL_ARGUMENT_KEYS
_HANDED_FROM_KEYS = ("graph.node.parent_id",)
# The agent a hand-off tool's own metadata names as taking over (LangGraph's supervisor and swarm hand-off tools).
_HANDOFF_DESTINATION_KEYS = ("__handoff_destination",)
# A flag a framework sets on a self-handing span whose worker failed.
_ERROR_FLAG_KEYS = ("claude_agent.tool.is_error",)


def _names(text: str, value: str) -> bool:
    """Whether ``value`` appears in ``text`` as a whole id, not inside a longer one."""
    start = text.find(value)
    while start != -1:
        before = text[start - 1] if start else " "
        end = start + len(value)
        after = text[end] if end < len(text) else " "
        if not any(c.isalnum() or c in "-_" for c in (before, after)):
            return True
        start = text.find(value, start + 1)
    return False


def _first(attributes, keys):
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, str):
            # A NUL byte is never stored (Postgres JSON refuses it), so a name or payload carrying one would lose the call.
            value = value.replace("\x00", "")
        if isinstance(value, str) and value:
            return value
    return None


def _named_at_start(span):
    """The agent an agent span names when it starts, before its name attribute is set: the OpenAI Agents SDK's
    instrumentor names the span after the agent and sets the name attribute only when the span ends. A method-like
    span name (with a dot) names no agent."""
    name = getattr(span, "name", None)
    attributes = getattr(span, "attributes", None) or {}
    return (
        name
        if isinstance(name, str)
        and name
        and "." not in name
        and _first(attributes, _KIND_KEYS)
        else None
    )


def _kind(span):
    attributes = getattr(span, "attributes", None) or {}
    if _first(attributes, _HANDOFF_DESTINATION_KEYS):
        # A hand-off tool is a tool call whatever kind its instrumentor labels it.
        return "TOOL"
    value = _first(attributes, _KIND_KEYS)
    if value:
        return value.upper()
    operation = _first(attributes, ("gen_ai.operation.name",))
    if operation in _OPERATION_KINDS:
        return _OPERATION_KINDS[operation]
    if attributes.get("messaging.operation") == "process" and _first(
        attributes, ("recipient_agent_type",)
    ):
        # AutoGen Core's runtime handing a message to the agent that handles it.
        return "AGENT"
    for key, kinds in _OWN_KINDS.items():
        own = kinds.get(_first(attributes, (key,)) or "")
        if own:
            return own
    return None


def _self_handing(span):
    """The worker a span that is both a hand-off and the worker's run ran, else None."""
    attributes = getattr(span, "attributes", None) or {}
    return _first(attributes, _SELF_HANDING_KEYS) if _kind(span) == "AGENT" else None


def _graph_worker(attributes):
    """The subgraph a LangGraph node ran in: the namespace segment above that node, or the agent named as running it."""
    namespace = _first(attributes, (_GRAPH_NAMESPACE,))
    if not namespace:
        return None
    names = [segment.split(":")[0] for segment in namespace.split("|")]
    if names and names[-1] == attributes.get("langgraph_node"):
        names.pop()
    if not names:
        return None
    return _first(attributes, ("lc_agent_name",)) or names[-1] or None


def _hex(span_id) -> str:
    return format(span_id, "016x")


def _bounded(record, key, value, flag) -> None:
    """Keep at most the payload limit of ``value``, and say when some of it was left out."""
    record[key] = value[:_PAYLOAD_MAX]
    if len(value) > _PAYLOAD_MAX:
        record[flag] = True


def _transfer_target(attributes):
    """The agent a framework's own transfer tool handed control to, when the span records the call."""
    destination = _first(attributes, _HANDOFF_DESTINATION_KEYS)
    if destination:
        return destination
    if attributes.get("gen_ai.tool.type") not in _TRANSFER_TOOL_TYPES:
        return None
    try:
        target = json.loads(_first(attributes, _TRANSFER_ARGUMENT_KEYS) or "").get(
            "agent_name"
        )
    except (ValueError, AttributeError):
        return None
    return target if isinstance(target, str) and target else None


def _builtin_transfer_target(attributes, name):
    """The agent named by a call to ADK's own ``transfer_to_agent`` function, which older releases and other
    instrumentations record as a plain function. Only a candidate: it becomes a control pass once that agent starts in
    the same agent run, and an ordinary tool call if the run ends first."""
    if name != _BUILTIN_TRANSFER:
        return None
    try:
        target = json.loads(_first(attributes, _TRANSFER_ARGUMENT_KEYS) or "").get(
            "agent_name"
        )
    except (ValueError, AttributeError):
        return None
    return target if isinstance(target, str) and target else None


def _same_tool(one, other):
    """Whether two spans name the same tool, as two instrumentations of one call spell it."""

    def key(span):
        name = _first(span.attributes or {}, _TOOL_NAME_KEYS) or span.name or ""
        return "".join(ch for ch in name.lower() if ch.isalnum())

    return key(one) == key(other) != ""


class _ToolSpans:
    """Writes each finished tool span to the tool trace, with the nearest agent span above it as its worker."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._live = {}
        self._handed = {}
        # Tool calls that passed control to the agent they ran, as the framework recorded it.
        self._passed = set()
        self._wrapped = set()
        # Tool calls that returned something, oldest first, so a job started by one can be joined to it.
        self._launches = collections.deque(maxlen=_LAUNCHES_MAX)
        # Runs with no parent not yet joined to a launch, with the attributes last seen in them: a run's own span is
        # often named only when it ends, so the spans inside it name it first.
        self._waiting = {}
        # What a model last said in each open run, read as a job's answer when the run ends.
        self._said = {}
        # Job runs whose hand-off was written: the calls inside one ran in that hand-off.
        self._jobs = set()
        # Transfers a framework recorded, by the agent run they were made in, until the agent they name starts in it;
        # then each agent run handed control, with the event id of the transfer that handed it.
        self._transfers = {}
        self._transferred = {}
        # Transfers made where no agent span is open (a graph's nodes), by the run they were made in: the event id of
        # the latest transfer to each agent, which holds that agent's later calls in the run.
        self._run_transfers = {}
        # Those transfers by the run they were made in, each as [its line, the agent that made it, whether the agent it
        # named has run since], until the agent that made it runs again after it (a supervisor's worker returning).
        self._returns = {}
        # Calls to ADK's built-in transfer function by the run they were made in, held until the agent they name
        # starts there (a control pass) or the run ends (a plain tool call).
        self._candidates = {}
        # The first agent run opened directly inside each open agent span.
        self._opened = {}
        # Tool calls whose hand-off was written when the worker they ran started, so one still out when the process
        # stops (the OpenAI Agents SDK ends a hand-off's span only when the worker returns) still has its hand-off.
        self._opened_hand_offs = collections.OrderedDict()
        # Spans that ended, so a parent never seen can be told from one that already closed.
        self._closed = collections.OrderedDict()
        self._appended = False
        self._lost_marked = False
        # Spans start and end on the process's own threads.
        self._lock = threading.RLock()

    def started(self, span) -> None:
        context = span.get_span_context()
        parent = getattr(span, "parent", None)
        with self._lock:
            if len(self._live) >= _LIVE_SPANS_MAX:
                # A span that never ended (a dropped generator, a long-lived server) gives way to new ones; it then
                # reads as closed, so what ran under it is unknown rather than the top of the app's work.
                evicted = next(iter(self._live))
                self._live.pop(evicted)
                self._closed[evicted] = None
            self._live[context.span_id] = (
                span,
                parent.span_id if parent is not None else None,
            )
            above = self._live.get(parent.span_id) if parent is not None else None
            if above is not None and parent.span_id not in self._opened_hand_offs:
                # Its attributes are set just after it starts, so a self-handing run is known by its first span.
                worker = _self_handing(above[0])
                if worker:
                    try:
                        self._self_hand_off(
                            above[0], parent.span_id, worker, ended=False
                        )
                    except Exception:
                        self._lost()
            try:
                self._returned_to(span, context.span_id)
            except Exception:
                self._lost()
            if _kind(span) == "AGENT":
                try:
                    self._hand_off_opened(span, context.span_id)
                except Exception:
                    self._lost()
                # Through the framework's own turn and run spans, to the agent span this run opened in.
                holder = self._above(context.span_id, ("AGENT",))
                if holder is not None:
                    self._opened.setdefault(
                        holder.get_span_context().span_id, context.span_id
                    )
                else:
                    # The first agent under the trace's own top span (a workflow or chain) is the top of the work.
                    top = self._top(context.span_id)
                    if top is not None:
                        self._opened.setdefault(top, context.span_id)
            if parent is None and _kind(span) != "TOOL":
                try:
                    joined = self._job_record(span, context.span_id, ended=False)
                    if not joined and len(self._waiting) < _LAUNCHES_MAX:
                        self._waiting[context.span_id] = (span, None)
                except Exception:
                    self._lost()
        original = span.end
        if getattr(original, _TOOL_END, False):
            return

        @functools.wraps(original)
        def end(*args, **kwargs):
            result = original(*args, **kwargs)
            try:
                with self._lock:
                    self._ended(span)
            except Exception:
                self._lost()
            return result

        setattr(end, _TOOL_END, True)
        span.end = end

    def _hand_off_opened(self, span, span_id) -> None:
        """Write the hand-off a tool call makes when the worker it runs starts, before either ends: the line has no
        end and no answer, and the line written when the tool call ends replaces it. Only a worker named at its start,
        never one named after the tool that ran it."""
        name = _first(span.attributes or {}, _AGENT_NAME_KEYS) or _named_at_start(span)
        handing = self._above(span_id, ("TOOL",))
        if not name or handing is None:
            return
        tool_id = handing.get_span_context().span_id
        attributes = handing.attributes or {}
        tool = _first(attributes, _TOOL_NAME_KEYS) or handing.name
        if not tool or tool_id in self._opened_hand_offs:
            return
        self._opened_hand_offs[tool_id] = None
        if len(self._opened_hand_offs) > _LAUNCHES_MAX:
            self._opened_hand_offs.popitem(last=False)
        parent_span_id, relationship = self._delegation(tool_id)
        record = {
            "name": tool[:256],
            "output": "",
            "is_error": False,
            "at": (handing.start_time or 0) / 1e9,
            "source": "otel",
            "event_id": _hex(tool_id),
            "parent_span_id": parent_span_id,
            "relationship": relationship,
            "delegated_to": name[:256],
            "opened": True,
        }
        _bounded(
            record,
            "arguments",
            _first(attributes, _TOOL_ARGUMENT_KEYS) or "",
            "input_omitted",
        )
        holder = self._above(tool_id, ("AGENT",))
        if holder is not None:
            record["parent_event_id"] = _hex(holder.get_span_context().span_id)
            holder_name = self._agent_name(holder)
            if holder_name:
                record.update(agent_key=holder_name[:256], agent_role=holder_name[:256])
        self._forget_cleared_call()
        _append(self._path, record)
        self._appended = True

    def _lost(self) -> None:
        """A hand-off or its return could not be written: say so once in the trace, so the call reads its hand-offs as
        incomplete rather than as none."""
        if self._lost_marked:
            return
        self._lost_marked = True
        try:
            _append(self._path, {"kind": HAND_OFF_UNRECORDED, "at": time.time()})
        except Exception:
            pass

    def _returned_to(self, span, span_id) -> None:
        """Write a graph's transfer again as a hand-off that returned, when the agent that made it starts again in the
        run after the agent it named ran there: that line replaces the control pass written when the transfer ended."""
        pending = self._returns.get(self._root(span_id))
        if not pending:
            return
        # A graph's node span is named after its node at start, its attributes often set only when it ends.
        attributes = getattr(span, "attributes", None) or {}
        names = {
            _graph_worker(attributes),
            _first(attributes, _AGENT_NAME_KEYS),
            getattr(span, "name", None),
        }
        for held in list(pending):
            if held[0]["delegated_to"] in names:
                held[2] = True
            elif held[1] in names and held[2]:
                pending.remove(held)
                returned = {
                    key: value
                    for key, value in held[0].items()
                    if key not in ("transfer", "opened")
                }
                returned["ended_at"] = (span.start_time or 0) / 1e9
                _append(self._path, returned)
                self._appended = True

    def _self_hand_off(self, span, span_id, worker, *, ended: bool) -> None:
        """Write a span that is both a hand-off and the worker's run as that hand-off: once when its first span starts
        (no end, no answer) and again when it ends, which replaces the first line."""
        attributes = span.attributes or {}
        if not ended:
            self._opened_hand_offs[span_id] = None
            if len(self._opened_hand_offs) > _LAUNCHES_MAX:
                self._opened_hand_offs.popitem(last=False)
        status = getattr(getattr(span, "status", None), "status_code", None)
        parent_span_id, relationship = self._delegation(span_id)
        record = {
            "name": (_first(attributes, _TOOL_NAME_KEYS) or span.name or worker)[:256],
            "is_error": ended
            and (
                getattr(status, "name", "") == "ERROR"
                or any(attributes.get(key) is True for key in _ERROR_FLAG_KEYS)
            ),
            "at": (span.start_time or 0) / 1e9,
            "source": "otel",
            "event_id": _hex(span_id),
            "parent_span_id": parent_span_id,
            "relationship": relationship,
            "delegated_to": worker[:256],
        }
        if ended:
            record["ended_at"] = (span.end_time or 0) / 1e9
        else:
            record["opened"] = True
        _bounded(
            record,
            "arguments",
            _first(attributes, _TOOL_ARGUMENT_KEYS) or "",
            "input_omitted",
        )
        _bounded(
            record,
            "output",
            (_first(attributes, _TOOL_RESULT_KEYS) or "") if ended else "",
            "output_omitted",
        )
        holder = self._above(span_id, ("AGENT",))
        if holder is not None:
            record["parent_event_id"] = _hex(holder.get_span_context().span_id)
            holder_name = self._agent_name(holder)
            if holder_name:
                record.update(agent_key=holder_name[:256], agent_role=holder_name[:256])
        self._forget_cleared_call()
        _append(self._path, record)
        self._appended = True

    def _above(self, span_id, kinds):
        """The nearest open ancestor of one of ``kinds``, stopping at an agent span."""
        entry = self._live.get(span_id)
        parent = entry[1] if entry else None
        while parent is not None:
            above = self._live.get(parent)
            if above is None:
                return None
            kind = _kind(above[0])
            if kind in kinds:
                return above[0]
            if kind == "AGENT":
                return None
            parent = above[1]
        return None

    def _root(self, span_id):
        """The run with no parent that ``span_id`` sits in, if it is still open."""
        entry = self._live.get(span_id)
        while entry is not None and entry[1] is not None:
            span_id, entry = entry[1], self._live.get(entry[1])
        return span_id if entry is not None else None

    def _run_name(self, span_id):
        root = self._root(span_id)
        return self._live[root][0].name or None if root not in (None, span_id) else None

    def _join_waiting(self, root_id, attributes=None) -> None:
        waiting = self._waiting.get(root_id)
        if waiting is None:
            return
        seen = attributes or waiting[1]
        if self._job_record(waiting[0], root_id, ended=False, attributes=seen):
            self._waiting.pop(root_id, None)
        elif attributes:
            self._waiting[root_id] = (waiting[0], attributes)

    def _named_above(self, span_id):
        """The agent the nearest open span from ``span_id`` up names as running it."""
        while span_id is not None:
            entry = self._live.get(span_id)
            if entry is None:
                return None
            name = _first(entry[0].attributes or {}, ("lc_agent_name",))
            if name:
                return name
            span_id = entry[1]
        return None

    def _top(self, span_id):
        """The parentless span the app's trace for ``span_id`` started at, when it is not ``span_id`` itself."""
        top, parent, seen = None, (self._live.get(span_id) or (None, None))[1], set()
        while parent is not None and parent in self._live and parent not in seen:
            seen.add(parent)
            top, parent = parent, self._live[parent][1]
        return top if parent is None else None

    def _delegation(self, span_id):
        """The hand-off ``span_id`` ran in, read from the span tree only: ``(event id, "observed")``, ``("",
        "observed")`` under an agent that nothing handed work to, and ``("", "unknown")`` when the tree does not say."""
        agent = last = None
        entry = self._live.get(span_id)
        parent = entry[1] if entry else None
        while parent is not None:
            last = parent
            above = self._live.get(parent)
            if above is None:
                # A parent the app's tracer never recorded (the caller's context) is outside the app: the agent
                # below it is the top of the app's own work. One that already closed says nothing.
                outside = agent is not None and parent not in self._closed
                return "", "observed" if outside else "unknown"
            if parent in self._jobs:
                return _hex(parent), "observed"
            kind = _kind(above[0])
            if agent is None and _self_handing(above[0]):
                # Inside a worker's run that is its own hand-off (the Claude Agent SDK's Task tool).
                return _hex(parent), "observed"
            if (
                agent is not None
                and kind not in ("AGENT", "TOOL")
                and (parent in self._transfers or parent in self._candidates)
            ):
                # A transfer made by an agent that has since ended, in the run this agent started in.
                handed = self._transfer_into(parent, agent)
                if handed:
                    return handed, "observed"
            if kind == "AGENT":
                if (
                    agent is not None
                    and self._opened.get(parent) == agent
                    and self._container(above)
                ):
                    # The framework's trace container (the OpenAI Agents SDK opens one per run as an agent span with
                    # no agent name): the first agent run inside it is the top of the app's work. A later one was
                    # handed control and stays unknown until the framework records the transfer.
                    return "", "observed"
                if agent is not None:
                    handed = self._transfer_into(parent, agent)
                    return (handed, "observed") if handed else ("", "unknown")
                agent = parent
            elif kind == "TOOL" and agent is not None:
                return _hex(parent), "observed"
            elif kind != "TOOL" and above[1] in self._live:
                name = _first(above[0].attributes or {}, ("lc_agent_name",))
                handing = self._live[above[1]][0]
                if (
                    name
                    and name != self._named_above(above[1])
                    and _kind(handing) in ("TOOL", None)
                ):
                    return _hex(above[1]), "observed"
            parent = above[1]
        if (
            agent is not None
            and last != agent
            and self._opened.get(last, agent) != agent
        ):
            # A later agent under the trace's top span, with no transfer recorded into it: handed control by someone.
            return "", "unknown"
        return "", "observed" if agent is not None else "unknown"

    def _container(self, entry) -> bool:
        """An agent-kind span at the top of the app's trace that names no agent: a run's container, not an agent."""
        above = entry[1]
        top = above is None or (above not in self._live and above not in self._closed)
        return top and not _first(entry[0].attributes or {}, _AGENT_NAME_KEYS)

    def _transfer_into(self, scope, agent):
        """The transfer made in the run ``scope`` that names the agent run ``agent`` started under it."""
        if agent not in self._transferred:
            name = self._agent_name(self._live[agent][0])
            self._confirm_above(agent, name)
            pending = self._transfers.get(scope) or []
            match = next((one for one in pending if one[1] == name), None)
            if match is None:
                return None
            pending.remove(match)
            self._transferred[agent] = match[0]
        return self._transferred[agent]

    def _confirm_transfer(self, scope, name, started) -> None:
        """Write the held built-in transfer in ``scope`` that names ``name`` as a control pass, now that it ran; one
        with no recorded target goes only to another agent that started after it ended."""
        held = self._candidates.get(scope) or []
        match = next((one for one in held if one[1] == name), None) or next(
            (
                one
                for one in held
                if one[1] is None
                and started >= one[3]
                and name != one[0].get("agent_key")
            ),
            None,
        )
        if match is None:
            return False
        held.remove(match)
        record, target, _, _ = match
        target = target or name
        record.update(transfer=True, delegated_to=target[:256])
        record.pop("ended_at", None)
        pending = self._transfers.get(scope, [])
        if len(self._transfers) + len(pending) < _LAUNCHES_MAX:
            self._transfers[scope] = pending + [(record["event_id"], target)]
        with self._lock:
            _append(self._path, record)
            self._appended = True
        return True

    def _confirm_above(self, span_id, name) -> None:
        """Confirm a held transfer for the agent run ``span_id`` named ``name``, in the spans it opened inside, up to
        the nearest agent; an agent started by a tool call was handed work by that call, not control."""
        entry = self._live.get(span_id)
        parent = entry[1] if entry else None
        started = (entry[0].start_time or 0) if entry else 0
        while parent is not None and parent in self._live:
            if parent in self._candidates and self._confirm_transfer(
                parent, name, started
            ):
                return
            if _kind(self._live[parent][0]) in ("AGENT", "TOOL"):
                return
            parent = self._live[parent][1]

    def _settle_candidates(self, scope) -> None:
        """A run ended without starting the agent a held transfer named: an ordinary tool call, or the framework's
        transfer with no agent recorded as taking over."""
        for record, _, framework, _ in self._candidates.pop(scope, None) or []:
            if framework:
                record.update(transfer=True)
                record.pop("ended_at", None)
            with self._lock:
                _append(self._path, record)
                self._appended = True

    def _agent_name(self, agent):
        """An agent span's own name, else the tool that handed it the work; never a method-like span name."""
        name = _first(agent.attributes or {}, _AGENT_NAME_KEYS)
        if name:
            return name
        if (
            _first(agent.attributes or {}, _KIND_KEYS)
            and agent.name
            and "." not in agent.name
        ):
            return agent.name
        handing = self._above(agent.get_span_context().span_id, ("TOOL",))
        if handing is not None:
            return _first(handing.attributes or {}, _TOOL_NAME_KEYS) or handing.name
        return None

    def _ended(self, span) -> None:
        span_id = span.get_span_context().span_id
        kind = _kind(span)
        try:
            entry = self._live.get(span_id)
            said = _first(span.attributes or {}, _SAID_KEYS)
            if said and said.strip() and entry is not None and entry[1] is not None:
                root = self._root(span_id)
                if root is not None and (
                    root in self._said or len(self._said) < _LAUNCHES_MAX
                ):
                    self._said[root] = said
            if entry is not None and entry[1] is None and kind != "TOOL":
                self._waiting.pop(span_id, None)
                self._job_record(span, span_id, ended=True)
                self._said.pop(span_id, None)
            elif self._waiting:
                self._join_waiting(self._root(span_id), span.attributes)
            if kind == "AGENT":
                worker = _self_handing(span)
                if worker:
                    self._self_hand_off(span, span_id, worker, ended=True)
                handing = self._above(span_id, ("TOOL",))
                name = self._agent_name(span)
                if name:
                    self._confirm_above(span_id, name)
                if handing is not None and name:
                    self._handed[handing.get_span_context().span_id] = name
                handed_from = _first(span.attributes or {}, _HANDED_FROM_KEYS)
                if handed_from and name and handed_from != name:
                    if handing is not None:
                        # The tool that ran it is the control pass: its own line says so, once.
                        self._passed.add(handing.get_span_context().span_id)
                    else:
                        self._transfer_record(span, span_id, handed_from, name)
            elif kind != "TOOL" and entry is not None and entry[1] in self._live:
                # A run opened right under a span by an agent other than the one running that span is the span
                # handing it the work, as a sub-agent started through a tool is. A tool's kind is often set only
                # when it ends, so the hand-off is kept on the parent and written if the parent is a tool call.
                name = _first(span.attributes or {}, ("lc_agent_name",))
                if name and name != self._named_above(entry[1]):
                    self._handed[entry[1]] = name
            elif kind == "TOOL":
                if span_id in self._wrapped:
                    # Two instrumentations wrapped this one call; the inner span was written.
                    self._wrapped.discard(span_id)
                    self._handed.pop(span_id, None)
                    self._passed.discard(span_id)
                else:
                    outer = self._above(span_id, ("TOOL",))
                    if outer is not None and _same_tool(outer, span):
                        self._wrapped.add(outer.get_span_context().span_id)
                    self._write(span, span_id)
        finally:
            above = entry[1] if entry is not None else None
            if kind == "AGENT" and above in self._live:
                # A transfer outlives the agent that made it: the agent it names may start after it, in the same run.
                for held in (self._candidates, self._transfers):
                    moved = held.pop(span_id, None)
                    if moved:
                        held[above] = held.get(above, []) + moved
            if span_id in self._candidates:
                self._settle_candidates(span_id)
            self._opened.pop(span_id, None)
            self._closed[span_id] = None
            if len(self._closed) > _LIVE_SPANS_MAX:
                self._closed.popitem(last=False)
            if kind != "TOOL":
                self._handed.pop(span_id, None)
            for one in (
                self._transfers,
                self._transferred,
                self._run_transfers,
                self._returns,
            ):
                one.pop(span_id, None)
            self._jobs.discard(span_id)
            self._live.pop(span_id, None)

    def _forget_cleared_call(self) -> None:
        """Forget an earlier call's launches once the harness removes the trace, as it does between calls."""
        if self._appended and not os.path.exists(self._path):
            self._launches.clear()
            self._waiting.clear()
            self._jobs.clear()
            self._appended = False

    def _job_record(self, span, span_id, *, ended: bool, attributes=None) -> bool:
        """Write a parentless run whose conversation id an earlier tool call returned as that call's hand-off."""
        attributes = attributes or span.attributes or {}
        conversation = _first(attributes, _CONVERSATION_KEYS)
        if not conversation:
            return False
        self._forget_cleared_call()
        launch = next(
            (
                one
                for one in self._launches
                if one["conversation"] != conversation
                and _names(one["output"], conversation)
            ),
            None,
        )
        if launch is None:
            return False
        worker = (
            _first(attributes, _AGENT_NAME_KEYS)
            or _graph_worker(attributes)
            or span.name
        )
        if not worker:
            return False
        status = getattr(getattr(span, "status", None), "status_code", None)
        record = {
            "name": launch["name"],
            "arguments": launch["arguments"],
            "is_error": ended and getattr(status, "name", "") == "ERROR",
            "at": (span.start_time or 0) / 1e9,
            **({"ended_at": (span.end_time or 0) / 1e9} if ended else {}),
            "source": "otel",
            "event_id": _hex(span_id),
            "parent_event_id": launch["event_id"],
            # The job's hand-off sits where the call that launched it did.
            "parent_span_id": launch["parent_span_id"],
            "relationship": launch["relationship"],
            "delegated_to": worker[:256],
        }
        if launch.get("input_omitted"):
            record["input_omitted"] = True
        said = self._said.get(span_id) or _first(attributes, _TOOL_RESULT_KEYS) or ""
        _bounded(record, "output", said if ended else "", "output_omitted")
        if launch["agent"]:
            record.update(agent_key=launch["agent"], agent_role=launch["agent"])
        self._jobs.add(span_id)
        with self._lock:
            _append(self._path, record)
            self._appended = True
        return True

    def _transfer_record(self, span, span_id, handed_from, name) -> None:
        """Write an agent run the framework records as handed control by another agent, as that control pass. The
        tree does not say where the agent that passed control ran, so the pass's own place is unknown."""
        record = {
            "name": name[:256],
            "arguments": "",
            "output": "",
            "is_error": False,
            "at": (span.start_time or 0) / 1e9,
            "source": "otel",
            "event_id": _hex(span_id),
            "parent_span_id": "",
            "relationship": "unknown",
            "agent_key": handed_from[:256],
            "agent_role": handed_from[:256],
            "delegated_to": name[:256],
            "transfer": True,
            # A worker's run, not a tool call: kept, never graded.
            "kind": "handoff",
        }
        self._forget_cleared_call()
        _append(self._path, record)
        self._appended = True

    def _write(self, span, span_id) -> None:
        attributes = span.attributes or {}
        name = _first(attributes, _TOOL_NAME_KEYS) or span.name
        if not name:
            return
        agent = self._above(span_id, ("AGENT",))
        status = getattr(getattr(span, "status", None), "status_code", None)
        parent_span_id, relationship = self._delegation(span_id)
        record = {
            "name": name[:256],
            "is_error": getattr(status, "name", "") == "ERROR",
            "at": (span.start_time or 0) / 1e9,
            "ended_at": (span.end_time or 0) / 1e9,
            "source": "otel",
            "event_id": _hex(span_id),
            "parent_span_id": parent_span_id,
            "relationship": relationship,
        }
        _bounded(
            record,
            "arguments",
            _first(attributes, _TOOL_ARGUMENT_KEYS) or "",
            "input_omitted",
        )
        _bounded(
            record,
            "output",
            _first(attributes, _TOOL_RESULT_KEYS) or "",
            "output_omitted",
        )
        worker = (
            _graph_worker(attributes)
            or _first(attributes, ("gen_ai.agent.name",))
            or (self._agent_name(agent) if agent is not None else None)
            # No agent span above it: the agent the span itself names, as a job running in its own run does.
            or _first(attributes, _AGENT_NAME_KEYS)
            # Nothing names it, in a served conversation's run: the run's own name, which a graph server sets to the
            # graph it runs and a launch from this run already takes as its agent.
            or (
                self._run_name(span_id)
                if _first(attributes, _CONVERSATION_KEYS)
                else None
            )
        )
        if worker:
            record.update(agent_key=worker[:256], agent_role=worker[:256])
            handed_in = self._run_transfers.get(self._root(span_id), {}).get(worker)
            if relationship == "unknown" and handed_in:
                parent_span_id, relationship = handed_in, "observed"
                record.update(parent_span_id=parent_span_id, relationship=relationship)
        if agent is not None:
            record["parent_event_id"] = _hex(agent.get_span_context().span_id)
        delegated = self._handed.pop(span_id, None)
        if delegated:
            record["delegated_to"] = delegated[:256]
            if span_id in self._passed:
                record["transfer"] = True
                record.pop("ended_at")
        self._passed.discard(span_id)
        target = _transfer_target(attributes)
        # The framework's own transfer tool with no recorded target hands control to the next agent run in this run.
        framework = attributes.get("gen_ai.tool.type") in _TRANSFER_TOOL_TYPES
        candidate = (
            None if target or framework else _builtin_transfer_target(attributes, name)
        )
        if (
            (candidate or (framework and not target))
            and agent is not None
            and sum(len(held) for held in self._candidates.values()) < _LAUNCHES_MAX
        ):
            scope = agent.get_span_context().span_id
            self._candidates.setdefault(scope, []).append(
                (record, candidate, framework, span.end_time or 0)
            )
            return
        if target:
            # Control passed to the agent the framework named; nothing returns from a control pass.
            record.update(transfer=True, delegated_to=target[:256])
            record.pop("ended_at")
            scope = agent.get_span_context().span_id if agent is not None else None
            pending = self._transfers.get(scope, [])
            if (
                scope is not None
                and len(self._transfers) + len(pending) < _LAUNCHES_MAX
            ):
                self._transfers[scope] = pending + [(record["event_id"], target)]
            run = self._root(span_id) if scope is None else None
            if run is not None and (
                run in self._run_transfers or len(self._run_transfers) < _LAUNCHES_MAX
            ):
                self._run_transfers.setdefault(run, {})[target] = record["event_id"]
                pending = self._returns.setdefault(run, [])
                # An agent handed control again starts by that pass, never by a return to it.
                pending[:] = [held for held in pending if held[1] != target]
                source = record.get("agent_key")
                if source and source != target:
                    # A control pass unless a later line says the agent that made it ran again after its worker.
                    record["opened"] = True
                    pending.append([record, source, False])
        self._forget_cleared_call()
        if record["output"]:
            self._launches.append(
                {
                    "output": record["output"],
                    "name": record["name"],
                    "arguments": record["arguments"],
                    "event_id": record["event_id"],
                    "parent_span_id": parent_span_id,
                    "relationship": relationship,
                    "input_omitted": record.get("input_omitted", False),
                    # No agent names the launching call: the run it sits in, which a graph server names by the graph.
                    "agent": record.get("agent_key") or self._run_name(span_id),
                    "conversation": _first(attributes, _CONVERSATION_KEYS),
                }
            )
        with self._lock:
            _append(self._path, record)
            self._appended = True
        for job_id in list(self._waiting):
            self._join_waiting(job_id)


def _append(path, record) -> None:
    line = json.dumps(record, sort_keys=True, default=str)
    if len(line) > _TRACE_LINE_MAX:
        record.update(
            arguments="<truncated>",
            output="<truncated>",
            input_omitted=True,
            output_omitted=True,
        )
        line = json.dumps(record, sort_keys=True, default=str)
    with open(path, "a", encoding="utf-8") as trace:
        trace.write(line + "\n")


def _patch_autogen_tools(module, path) -> None:
    """Write each AutoGen tool call with its arguments and result, which AutoGen's tool spans do not carry."""
    cls = module.BaseTool
    original = cls.run_json
    if getattr(original, _PATCHED, False):
        return

    async def run_json(
        self, args, cancellation_token, call_id=None, __original=original
    ):
        record = {
            "name": str(self.name)[:256],
            "arguments": dict(args),
            "at": time.time(),
        }
        try:
            result = await __original(self, args, cancellation_token, call_id)
        except Exception as err:
            _record_tool(path, record, str(err), True)
            raise
        _record_tool(path, record, self.return_value_as_string(result), False)
        return result

    functools.update_wrapper(run_json, original)
    setattr(run_json, _PATCHED, True)
    cls.run_json = run_json


def _record_tool(path, record, output, is_error) -> None:
    try:
        line = {**record, "is_error": is_error}
        _bounded(line, "output", output, "output_omitted")
        _append(path, line)
    except Exception:
        pass
