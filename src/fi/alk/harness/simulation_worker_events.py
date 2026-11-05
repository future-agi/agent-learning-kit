"""Tool calls and the worker that made each, read from the spans a process's framework instrumentation ends.

The sandbox's startup hook (``simulation_mark_bootstrap``) owns the hook's lifecycle and hands every span its process
starts to :class:`_ToolSpans`; this module turns those spans into the lines of the tool trace. It sits beside the hook
in a customer process's world directory, so it runs inside the CUSTOMER's interpreter, which has no ``fi``: standard
library only, Python 3.9 syntax, and it never imports the customer's modules.
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
_OWN_KINDS = {"tool": "TOOL", "subagent": "AGENT", "agent": "AGENT"}
_AGENT_NAME_KEYS = (
    "gen_ai.agent.name",
    "agent.name",
    "autogen.agent.name",
    "claude_agent.subagent_type",
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


_LAUNCHES_MAX = 256
_GRAPH_NAMESPACE = "langgraph_checkpoint_ns"
_TOOL_NAME_KEYS = ("gen_ai.tool.name", "tool.name")
_TOOL_ARGUMENT_KEYS = ("gen_ai.tool.call.arguments", "tool.parameters", "input.value")
_TOOL_RESULT_KEYS = ("gen_ai.tool.call.result", "output.value")
_LIVE_SPANS_MAX = 4096
_TRACE_LINE_MAX = 65536


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
        if isinstance(value, str) and value:
            return value
    return None


def _kind(span):
    attributes = getattr(span, "attributes", None) or {}
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
    return _OWN_KINDS.get(_first(attributes, ("claude_agent.span_kind",)) or "")


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
        self._wrapped = set()
        # Tool calls that returned something, oldest first, so a job started by one can be joined to it.
        self._launches = collections.deque(maxlen=_LAUNCHES_MAX)
        # Runs with no parent not yet joined to a launch, with the attributes last seen in them: a run's own span is
        # often named only when it ends, so the spans inside it name it first.
        self._waiting = {}
        # What a model last said in each open run, read as a job's answer when the run ends.
        self._said = {}
        self._appended = False
        # Spans start and end on the process's own threads.
        self._lock = threading.RLock()

    def started(self, span) -> None:
        context = span.get_span_context()
        parent = getattr(span, "parent", None)
        with self._lock:
            if len(self._live) >= _LIVE_SPANS_MAX:
                # A span that never ended (a dropped generator, a long-lived server) gives way to new ones.
                self._live.pop(next(iter(self._live)))
            self._live[context.span_id] = (
                span,
                parent.span_id if parent is not None else None,
            )
            if parent is None and _kind(span) != "TOOL":
                try:
                    joined = self._job_record(span, context.span_id, ended=False)
                    if not joined and len(self._waiting) < _LAUNCHES_MAX:
                        self._waiting[context.span_id] = (span, None)
                except Exception:
                    pass
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
                pass
            return result

        setattr(end, _TOOL_END, True)
        span.end = end

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
                handing = self._above(span_id, ("TOOL",))
                name = self._agent_name(span)
                if handing is not None and name:
                    self._handed[handing.get_span_context().span_id] = name
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
                else:
                    outer = self._above(span_id, ("TOOL",))
                    if outer is not None and _same_tool(outer, span):
                        self._wrapped.add(outer.get_span_context().span_id)
                    self._write(span, span_id)
        finally:
            if kind != "TOOL":
                self._handed.pop(span_id, None)
            self._live.pop(span_id, None)

    def _forget_cleared_call(self) -> None:
        """Forget an earlier call's launches once the harness removes the trace, as it does between calls."""
        if self._appended and not os.path.exists(self._path):
            self._launches.clear()
            self._waiting.clear()
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
            "output": (
                self._said.get(span_id) or _first(attributes, _TOOL_RESULT_KEYS) or ""
            )[:4096]
            if ended
            else "",
            "is_error": ended and getattr(status, "name", "") == "ERROR",
            "at": (span.start_time or 0) / 1e9,
            **({"ended_at": (span.end_time or 0) / 1e9} if ended else {}),
            "source": "otel",
            "event_id": format(span_id, "016x"),
            "parent_event_id": launch["event_id"],
            "delegated_to": worker[:256],
        }
        if launch["agent"]:
            record.update(agent_key=launch["agent"], agent_role=launch["agent"])
        with self._lock:
            _append(self._path, record)
            self._appended = True
        return True

    def _write(self, span, span_id) -> None:
        attributes = span.attributes or {}
        name = _first(attributes, _TOOL_NAME_KEYS) or span.name
        if not name:
            return
        agent = self._above(span_id, ("AGENT",))
        status = getattr(getattr(span, "status", None), "status_code", None)
        record = {
            "name": name[:256],
            "arguments": (_first(attributes, _TOOL_ARGUMENT_KEYS) or "")[:4096],
            "output": (_first(attributes, _TOOL_RESULT_KEYS) or "")[:4096],
            "is_error": getattr(status, "name", "") == "ERROR",
            "at": (span.start_time or 0) / 1e9,
            "ended_at": (span.end_time or 0) / 1e9,
            "source": "otel",
            "event_id": format(span_id, "016x"),
        }
        worker = (
            _graph_worker(attributes)
            or _first(attributes, ("gen_ai.agent.name",))
            or (self._agent_name(agent) if agent is not None else None)
            # No agent span above it: the agent the span itself names, as a job running in its own run does.
            or _first(attributes, _AGENT_NAME_KEYS)
        )
        if worker:
            record.update(agent_key=worker[:256], agent_role=worker[:256])
        if agent is not None:
            record["parent_event_id"] = format(agent.get_span_context().span_id, "016x")
        delegated = self._handed.pop(span_id, None)
        if delegated:
            record["delegated_to"] = delegated[:256]
        self._forget_cleared_call()
        if record["output"]:
            self._launches.append(
                {
                    "output": record["output"],
                    "name": record["name"],
                    "arguments": record["arguments"],
                    "event_id": record["event_id"],
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
        record.update(arguments="<truncated>", output="<truncated>")
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
        _append(path, {**record, "output": output[:4096], "is_error": is_error})
    except Exception:
        pass
