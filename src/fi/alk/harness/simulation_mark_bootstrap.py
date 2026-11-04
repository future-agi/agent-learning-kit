"""Startup hook that stamps the simulation mark on every OpenTelemetry resource a process builds.

The sandbox copies this file into a customer process's world directory beside a
``sitecustomize.py`` that calls :func:`install`, so it runs inside the CUSTOMER's interpreter,
which has no ``fi``. It therefore uses the standard library only and is written for Python 3.9
syntax. It imports OpenTelemetry only inside a process that already uses it, to give a span asked
for before any provider was set the sandbox's provider.

Why it exists: OpenTelemetry's own environment parsing reaches ``Resource.create`` only. Code
that builds ``Resource(attributes=...)`` directly, or that names its own project, never sees
``OTEL_RESOURCE_ATTRIBUTES``, so its traces would carry neither the mark that hides test traces
from production views nor the project they must land in. This hook wraps the one constructor
every resource goes through and merges those pairs over whatever the caller passed.

When the launcher also hands the process the agent's ingest key, the hook makes the two base
OTLP span exporters, and traceAI's exporters built on them, send to our collector with that key
instead of the customer's own ``FI_API_KEY``: only the Future AGI authentication headers are
replaced, only on exports to the collector, and the customer's key stays in the environment for
the rest of their code.

When the sandbox names a call file, every span the process starts is also stamped with the id of
the call the sandbox is running at that moment. The stamp rides on the SDK tracer rather than a
span processor, because a provider subclass such as traceAI's may drop processors it did not add.

When the sandbox names a tool trace, each tool span the process ends is written to it with the
agent that made the call, read from the agent and tool spans any framework instrumentation marks,
so a multi-agent app is observed without changes to it. With the observe switch on, the traceAI
instrumentor the build installed for each framework is turned on when that framework is imported,
unless the process instruments it itself.

It never reads or writes the customer's files and never imports their modules on its own. It does
run the next ``sitecustomize`` on the path, which is theirs, so a repository's own startup hook is
not lost behind this one; a failure of that hook is reported as Python reports it, never as ours.
A failure of this patch leaves the process as it would be without the hook and writes one fixed
line to stderr.
"""

from __future__ import annotations

import collections.abc
import functools
import importlib
import importlib.machinery
import importlib.util
import inspect
import json
import os
import sys
import threading
import time
import urllib.parse

_RESOURCES = "opentelemetry.sdk.resources"
_TRACE = "opentelemetry.sdk.trace"
_CALL_FILE = "FI_SIMULATION_CALL_FILE"
_CALL_KEY = "fi.simulator.call_execution_id"
_EXPORTERS = {
    "opentelemetry.exporter.otlp.proto.http.trace_exporter": False,
    "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": True,
}
_INGEST_API_KEY = "FI_SIMULATION_INGEST_API_KEY"
_INGEST_SECRET_KEY = "FI_SIMULATION_INGEST_SECRET_KEY"
_TRACES_ENDPOINT = "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
_GENERIC_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
_TRACES_HEADERS = "OTEL_EXPORTER_OTLP_TRACES_HEADERS"
_GENERIC_HEADERS = "OTEL_EXPORTER_OTLP_HEADERS"
_AUTH_HEADERS = ("x-api-key", "x-secret-key")
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MARK_PREFIX = "fi.simulation."
_ORIGIN_KEY = "fi.simulation.origin"
_PROJECT_KEY = "project_name"
_PATCHED = "_alk_simulation_mark"
_LOADER = "_alk_simulation_mark_loader"
_FINDER = "_alk_simulation_mark_finder"
_CHAINED_NAME = "_alk_chained_sitecustomize"
_TOOL_END = "_alk_tool_span_end"

_installed = False
_failed_written = False
_PATCHES: dict = {}


def _write(outcome: str) -> None:
    """Write the one fixed stderr line. ``failed`` at most once, so a broken merge on every
    resource the process builds cannot flood its log."""
    global _failed_written
    try:
        if outcome == "failed":
            if _failed_written:
                return
            _failed_written = True
        sys.stderr.write(f"simulation mark hook: {outcome}\n")
    except Exception:
        pass


def _read_pairs() -> dict[str, str] | None:
    """The pairs of ``OTEL_RESOURCE_ATTRIBUTES`` that are ours, parsed as the SDK parses the
    variable. The customer's own pairs are left to the SDK, whose precedence is already defined."""
    raw = os.environ.get("OTEL_RESOURCE_ATTRIBUTES")
    if not raw:
        return None
    pairs: dict[str, str] = {}
    for item in raw.split(","):
        key, separator, value = item.partition("=")
        if not separator:
            continue
        key = key.strip()
        if key == _PROJECT_KEY or key.startswith(_MARK_PREFIX):
            pairs[key] = urllib.parse.unquote(value.strip())
    if _ORIGIN_KEY not in pairs:
        return None
    return pairs


def _patch(module, pairs: dict[str, str]) -> None:
    cls = module.Resource
    original = cls.__init__
    if getattr(original, _PATCHED, False):
        return

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        new_args, new_kwargs = args, kwargs
        try:
            # Only attributes that can be merged are touched. Any other call reaches the
            # constructor as the caller wrote it, so it behaves as it would without the hook.
            if args:
                supplied = args[0]
                if supplied is None or isinstance(supplied, collections.abc.Mapping):
                    merged = dict(supplied or {})
                    merged.update(pairs)
                    new_args = (merged,) + args[1:]
            elif "attributes" in kwargs:
                supplied = kwargs["attributes"]
                if supplied is None or isinstance(supplied, collections.abc.Mapping):
                    merged = dict(supplied or {})
                    merged.update(pairs)
                    new_kwargs = dict(kwargs, attributes=merged)
        except Exception:
            _write("failed")
        # Outside the try: a failure of the original constructor is the caller's, not ours.
        return original(self, *new_args, **new_kwargs)

    setattr(wrapper, _PATCHED, True)
    cls.__init__ = wrapper
    _write("applied")


def _wrap_loader(loader) -> None:
    if getattr(loader, _LOADER, False):
        return
    exec_module = getattr(loader, "exec_module", None)
    if exec_module is None:
        # A third-party import hook written before ``exec_module`` existed still loads modules
        # with ``load_module``; the patch cannot land there, and the log must say the SDK is
        # going to load unmarked.
        _write("failed")
        return

    def patching_exec_module(module):
        exec_module(module)
        # One zipimporter serves every module of its archive, so the name is looked up here.
        _apply(module)

    loader.exec_module = patching_exec_module
    setattr(loader, _LOADER, True)


def _apply(module) -> None:
    try:
        patch = _PATCHES.get(module.__name__)
        if patch is not None:
            patch(module)
    except Exception:
        _write("failed")


class _MarkFinder:
    """Sits first on ``sys.meta_path`` for the life of the process, so the SDK is patched when
    it is imported later and again when it is reloaded."""

    def __init__(self) -> None:
        setattr(self, _FINDER, True)

    def find_spec(self, fullname, path=None, target=None):
        # Not removed after the first import: live-reload dev servers and test runners call
        # ``importlib.reload``, which builds a new constructor that must be patched again.
        if fullname not in _PATCHES:
            return None
        spec = None
        try:
            for finder in list(sys.meta_path):
                # The module can run twice under two names (the world directory copy and a copy
                # on the customer's own path); two finders would otherwise call each other until
                # ``RecursionError``.
                if finder is self or getattr(finder, _FINDER, False):
                    continue
                find_spec = getattr(finder, "find_spec", None)
                if find_spec is None:
                    continue
                try:
                    spec = find_spec(fullname, path, target)
                except Exception:
                    # Not ours: the import system reaches the same finder next and reports it.
                    return None
                if spec is not None:
                    break
            if spec is not None:
                _wrap_loader(spec.loader)
        except Exception:
            _write("failed")
        return spec


def _read_ingest() -> tuple[list[tuple[str, str]], tuple[str, int | None]] | None:
    api_key = os.environ.get(_INGEST_API_KEY)
    secret_key = os.environ.get(_INGEST_SECRET_KEY)
    collector = _host_port(os.environ.get(_TRACES_ENDPOINT))
    if not api_key or not secret_key or collector is None:
        return None
    return [(_AUTH_HEADERS[0], api_key), (_AUTH_HEADERS[1], secret_key)], collector


def _host_port(endpoint) -> tuple[str, int | None] | None:
    if not endpoint:
        return None
    parts = urllib.parse.urlsplit(endpoint if "://" in endpoint else f"//{endpoint}")
    host = (parts.hostname or "").lower()
    if not host:
        return None
    return host, parts.port or _DEFAULT_PORTS.get(parts.scheme)


def _header_entries(headers) -> list[tuple[str, str]]:
    if headers is None:
        return []
    if isinstance(headers, str):
        entries = []
        for item in headers.split(","):
            name, separator, value = item.partition("=")
            if separator and name.strip():
                entries.append(
                    (
                        urllib.parse.unquote(name.strip()),
                        urllib.parse.unquote(value.strip()),
                    )
                )
        return entries
    if isinstance(headers, collections.abc.Mapping):
        return list(headers.items())
    if isinstance(headers, (list, tuple)):
        return [(name, value) for name, value in headers]
    raise TypeError("unsupported headers")


def _environment_headers() -> list[tuple[str, str]]:
    merged = {
        name.lower(): value
        for name, value in _header_entries(os.environ.get(_GENERIC_HEADERS))
    }
    merged.update(
        (name.lower(), value)
        for name, value in _header_entries(os.environ.get(_TRACES_HEADERS))
    )
    return list(merged.items())


def _with_ingest(
    entries: list[tuple[str, str]], ours: list[tuple[str, str]], lower: bool
) -> dict[str, str]:
    kept = [
        (name, value) for name, value in entries if name.lower() not in _AUTH_HEADERS
    ]
    return {(name.lower() if lower else name): value for name, value in kept + ours}


def _without_auth(bound, supplied) -> None:
    """The fail-closed form of the headers: the customer's own key is replaced by blank ones, so
    our collector refuses the span instead of storing it under the customer's project."""
    try:
        entries = (
            _environment_headers() if supplied is None else _header_entries(supplied)
        )
    except (TypeError, ValueError):
        entries = []
    kept = [
        (name, value) for name, value in entries if name.lower() not in _AUTH_HEADERS
    ]
    bound.arguments["headers"] = dict(kept + [(name, "") for name in _AUTH_HEADERS])


def _patch_exporter(module, ingest, lower: bool) -> None:
    cls = module.OTLPSpanExporter
    original = cls.__init__
    if getattr(original, _PATCHED, False):
        return
    signature = inspect.signature(original)
    ours, collector = ingest

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        call_args, call_kwargs = (self,) + args, kwargs
        aimed_at_us = None
        try:
            bound = signature.bind(self, *args, **kwargs)
            endpoint = (
                bound.arguments.get("endpoint")
                or os.environ.get(_TRACES_ENDPOINT)
                or os.environ.get(_GENERIC_ENDPOINT)
            )
            if _host_port(endpoint) == collector:
                aimed_at_us = bound
                supplied = bound.arguments.get("headers")
                entries = (
                    _environment_headers()
                    if supplied is None
                    else _header_entries(supplied)
                )
                bound.arguments["headers"] = _with_ingest(entries, ours, lower)
                call_args, call_kwargs = bound.args, bound.kwargs
        except Exception:
            _write("failed")
            if aimed_at_us is not None:
                try:
                    _without_auth(aimed_at_us, aimed_at_us.arguments.get("headers"))
                    call_args, call_kwargs = aimed_at_us.args, aimed_at_us.kwargs
                except Exception:
                    pass
        return original(*call_args, **call_kwargs)

    setattr(wrapper, _PATCHED, True)
    cls.__init__ = wrapper
    _write("applied")


class _CallFile:
    def __init__(self, path: str) -> None:
        self._path = path
        self._seen = None
        self._call = None

    def current(self):
        try:
            stat = os.stat(self._path)
        except FileNotFoundError:
            return None
        seen = (stat.st_ino, stat.st_mtime_ns)
        if seen != self._seen:
            with open(self._path, encoding="utf-8") as handle:
                self._call = handle.read().strip() or None
            self._seen = seen
        return self._call


def _restamp_at_end(span, call: str) -> None:
    """Set the call id again just before the span ends. It is the span's first attribute, so a
    span that outgrows its attribute limit drops it first; the last one set is the last dropped."""
    original = span.end
    if getattr(original, _PATCHED, False):
        return

    @functools.wraps(original)
    def end(*args, **kwargs):
        try:
            if span.end_time is None:
                span.set_attribute(_CALL_KEY, call)
        except Exception:
            pass
        return original(*args, **kwargs)

    setattr(end, _PATCHED, True)
    span.end = end


_TOOL_TRACE = "HARNESS_TOOL_TRACE"
# Where the span kind, the agent's name and a tool call's parts sit, in the conventions the
# framework instrumentors and the frameworks' own tracing use (traceAI and OpenInference, the
# OpenTelemetry GenAI names, then names a single framework uses).
_KIND_KEYS = ("fi.span.kind", "gen_ai.span.kind", "openinference.span.kind")
_OPERATION_KINDS = {"execute_tool": "TOOL", "invoke_agent": "AGENT", "create_agent": "AGENT"}
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
_SAID_KEYS = ("gen_ai.output.messages.0.message.content", "llm.output_messages.0.message.content")


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
    if attributes.get("messaging.operation") == "process" and _first(attributes, ("recipient_agent_type",)):
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
            self._live[context.span_id] = (span, parent.span_id if parent is not None else None)
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
        if _first(agent.attributes or {}, _KIND_KEYS) and agent.name and "." not in agent.name:
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
                if root is not None and (root in self._said or len(self._said) < _LAUNCHES_MAX):
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
            (one for one in self._launches if one["conversation"] != conversation and _names(one["output"], conversation)),
            None,
        )
        if launch is None:
            return False
        worker = _first(attributes, _AGENT_NAME_KEYS) or _graph_worker(attributes) or span.name
        if not worker:
            return False
        status = getattr(getattr(span, "status", None), "status_code", None)
        record = {
            "name": launch["name"],
            "arguments": launch["arguments"],
            "output": (self._said.get(span_id) or _first(attributes, _TOOL_RESULT_KEYS) or "")[:4096] if ended else "",
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

    async def run_json(self, args, cancellation_token, call_id=None, __original=original):
        record = {"name": str(self.name)[:256], "arguments": dict(args), "at": time.time()}
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


_OBSERVE = "ALK_OBSERVE_FRAMEWORKS"
# A framework's top module, and the traceAI instrumentor that marks its agent and tool spans.
# The build installs the instrumentors beside the frameworks it finds installed; one that is
# missing or fails leaves that framework untraced, never the process stopped.
_INSTRUMENTORS = {
    "agents": ("traceai_openai_agents", "OpenAIAgentsInstrumentor"),
    "langchain_core": ("traceai_langchain", "LangChainInstrumentor"),
    "crewai": ("traceai_crewai", "CrewAIInstrumentor"),
    "google.adk": ("traceai_google_adk", "GoogleADKInstrumentor"),
    "autogen_agentchat": ("traceai_autogen", "AutogenInstrumentor"),
    "smolagents": ("traceai_smolagents", "SmolagentsInstrumentor"),
    "claude_agent_sdk": ("traceai_claude_agent_sdk", "ClaudeAgentInstrumentor"),
    "llama_index.core": ("traceai_llamaindex", "LlamaIndexInstrumentor"),
    "pydantic_ai": ("traceai_pydantic_ai", "PydanticAIInstrumentor"),
    "strands": ("traceai_strands", "StrandsInstrumentor"),
}

@functools.lru_cache(maxsize=1)
def _sandbox_provider():
    """A provider for a process that set none, exporting where the sandbox names."""
    from opentelemetry.sdk.trace import TracerProvider

    provider = TracerProvider()
    if os.environ.get(_TRACES_ENDPOINT) or os.environ.get(_GENERIC_ENDPOINT):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        except Exception:
            pass
    return provider


def _patch_proxy(module) -> None:
    """Serve a span asked for before any provider is set from ours, leaving the process free to set its own."""
    cls = module.ProxyTracer
    for method in ("start_span", "start_as_current_span"):
        original = getattr(cls, method)
        if getattr(original, _PATCHED, False):
            continue

        def wrapper(self, *args, __original=original, __method=method, **kwargs):
            try:
                if getattr(module, "_TRACER_PROVIDER", None) is None:
                    tracer = _sandbox_provider().get_tracer(self._instrumenting_module_name)
                    return getattr(tracer, __method)(*args, **kwargs)
            except Exception:
                pass
            return __original(self, *args, **kwargs)

        functools.update_wrapper(wrapper, original)
        setattr(wrapper, _PATCHED, True)
        setattr(cls, method, wrapper)


def _instrument(module, target) -> None:
    """Turn on the framework's instrumentor unless the process already has; it reads the global provider per span."""
    name, attribute = target
    if name in sys.modules:
        # The process imports the instrumentor itself, and this framework import is part of
        # that: it instruments with its own settings.
        return
    try:
        instrumentor = getattr(importlib.import_module(name), attribute)()
    except ImportError:
        # The build could not install it: this framework's calls go unobserved, and the log says so.
        _write("unobserved " + module.__name__ + ", its instrumentor is not installed")
        return
    if getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
        return
    instrumentor.instrument()
    _yield_to_process(type(instrumentor))
    if module.__name__ == "agents":
        _keep_agents_processors()


def _yield_to_process(cls) -> None:
    """Let the process instrument again with its own settings, which the instrumentor would otherwise ignore."""
    original = cls.instrument
    if getattr(original, _PATCHED, False):
        return

    def instrument(self, *args, __original=original, **kwargs):
        if kwargs and getattr(self, "is_instrumented_by_opentelemetry", False):
            self.uninstrument()
        return __original(self, *args, **kwargs)

    functools.update_wrapper(instrument, original)
    setattr(instrument, _PATCHED, True)
    cls.instrument = instrument


def _keep_agents_processors() -> None:
    """Keep our processor when an OpenAI Agents app replaces its trace processors, unless the new list has its kind."""
    from agents.tracing import get_trace_provider, provider

    ours = tuple(get_trace_provider()._multi_processor._processors)
    cls = provider.SynchronousMultiTracingProcessor
    original = cls.set_processors

    def wrapper(self, processors, __original=original):
        kinds = {type(one) for one in processors}
        return __original(self, [*processors, *(one for one in ours if type(one) not in kinds)])

    functools.update_wrapper(wrapper, original)
    cls.set_processors = wrapper


def _patch_tracer(module, path, tool_trace) -> None:
    cls = module.Tracer
    original = cls.start_span
    if getattr(original, _PATCHED, False):
        return

    call_file = _CallFile(path) if path else None
    tools = _ToolSpans(tool_trace) if tool_trace else None

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        span = original(self, *args, **kwargs)
        try:
            call = call_file.current() if call_file is not None else None
            if call and span.is_recording():
                span.set_attribute(_CALL_KEY, call)
                _restamp_at_end(span, call)
            if tools is not None and span.is_recording():
                tools.started(span)
        except Exception:
            pass
        return span

    setattr(wrapper, _PATCHED, True)
    cls.start_span = wrapper
    _write("applied")


def _report_chained_failure(err: Exception) -> None:
    """Say what Python itself says when a ``sitecustomize`` fails, in its words, so the owner of
    that hook still learns of the failure. It is theirs, not a failure of our patch, so it is
    never our ``failed`` line."""
    try:
        if sys.flags.verbose:
            sys.excepthook(*sys.exc_info())
        else:
            sys.stderr.write(
                "Error in sitecustomize; set PYTHONVERBOSE for traceback:\n%s: %s\n"
                % (err.__class__.__name__, err)
            )
    except Exception:
        pass


def _chain() -> None:
    """Run the next ``sitecustomize`` on the path: ours sits first, so a repository's or the
    environment's own hook would otherwise never run."""
    try:
        own_dir = os.path.dirname(os.path.realpath(__file__))
        own_hook = os.path.realpath(os.path.join(own_dir, "sitecustomize.py"))
        remaining = None
        for index, entry in enumerate(sys.path):
            try:
                if os.path.realpath(entry) == own_dir:
                    remaining = sys.path[index + 1 :]
                    break
            except Exception:
                continue
        if remaining is None:
            return
        for entry in remaining:
            try:
                spec = importlib.machinery.PathFinder.find_spec(
                    "sitecustomize", [entry]
                )
                origin = getattr(spec, "origin", None)
                # The world directory may sit on the path twice; our own file is not "next".
                if not origin or os.path.realpath(origin) == own_hook:
                    continue
                chained = importlib.util.spec_from_file_location(_CHAINED_NAME, origin)
                module = importlib.util.module_from_spec(chained)
            except Exception:
                continue
            sys.modules[_CHAINED_NAME] = module
            try:
                chained.loader.exec_module(module)
            except BaseException as err:
                sys.modules.pop(_CHAINED_NAME, None)
                if not isinstance(err, Exception):
                    raise
                _report_chained_failure(err)
            return
    except Exception:
        # Looking for the next hook must never cost the process its start.
        pass


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    try:
        pairs = _read_pairs()
        if pairs is not None:
            _PATCHES[_RESOURCES] = functools.partial(_patch, pairs=pairs)
        call_file = os.environ.get(_CALL_FILE)
        tool_trace = os.environ.get(_TOOL_TRACE)
        if call_file or tool_trace:
            _PATCHES[_TRACE] = functools.partial(_patch_tracer, path=call_file, tool_trace=tool_trace)
        ingest = None
        try:
            ingest = _read_ingest()
        except ValueError:
            _write("failed")
        if ingest is not None:
            for name, lower in _EXPORTERS.items():
                _PATCHES[name] = functools.partial(
                    _patch_exporter, ingest=ingest, lower=lower
                )
        if os.environ.get(_OBSERVE) == "1" and tool_trace:
            _PATCHES["opentelemetry.trace"] = _patch_proxy
            for name, target in _INSTRUMENTORS.items():
                _PATCHES.setdefault(name, functools.partial(_instrument, target=target))
            _PATCHES["autogen_core.tools"] = functools.partial(_patch_autogen_tools, path=tool_trace)
        if not _PATCHES:
            return
        # The finder goes in first so that a failed patch of an already imported module still
        # leaves a later reload to be patched.
        sys.meta_path.insert(0, _MarkFinder())
        for name in list(_PATCHES):
            if name in sys.modules:
                _apply(sys.modules[name])
    except Exception:
        _write("failed")
    finally:
        _chain()
