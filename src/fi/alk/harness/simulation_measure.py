"""Measures the model calls of an agent that traces nothing itself.

The platform asks for this (``FI_SIMULATION_INSTRUMENT``) only for an agent whose source names no
tracing, so that every run has the models, tokens and timing a fix is judged on. The build
installed traceAI's instrumentor beside each SDK below that the agent's own install holds, with
every installed version pinned, and skipped one that would not fit; that skip is the preflight. A
missing instrumentor here therefore means its calls go unmeasured, never a failed start.

When the agent first imports one of the SDKs, its instrumentor is turned on into a provider of our
own, never the global one, so any tracing the agent sets up itself is left as it is. Only those
instrumentors' spans leave the process, with the model, tokens and timing kept. The instrumentors
hide prompts and replies; our exporter also drops tool definitions, span events and error text,
and the resource holds the simulation's own attributes only. They go to the address the sandbox
names under the agent's ingest key, and the mark hook stamps each with the call it belongs to.

A model call this cannot see makes the process ``partial``: one through an SDK with no instrumentor
here, through an SDK the agent instruments itself, or a raw HTTP request to a model API: a public
one, or a host the platform names (``FI_SIMULATION_MODEL_HOSTS``, such as its model gateway). A span
stamped ``fi.simulation.instrumentation=partial`` goes out with that call, and every span after it
carries the stamp, so the platform reads the call's cost as unknown, never as too low.

Like the mark hook beside it, it runs in the customer's interpreter: standard library only until
the agent imports an SDK, and Python 3.9 syntax.
"""

from __future__ import annotations

import atexit
import functools
import importlib
import os
import signal
import sys
import threading
import urllib.parse

INSTRUMENT = "FI_SIMULATION_INSTRUMENT"
_ENDPOINT = "FI_SIMULATION_TRACES_ENDPOINT"
_INGEST_API_KEY = "FI_SIMULATION_INGEST_API_KEY"
_INGEST_SECRET_KEY = "FI_SIMULATION_INGEST_SECRET_KEY"
_CALL_FILE = "FI_SIMULATION_CALL_FILE"
# The hosts the platform names as the agent's model endpoints: its model gateway, which every
# hosted model call goes to, and any model base URL the agent declares.
_MODEL_HOSTS_ENV = "FI_SIMULATION_MODEL_HOSTS"
STATE_KEY = "fi.simulation.instrumentation"
PARTIAL = "partial"
# Each SDK's top module, and the traceAI instrumentor the build installs beside it.
SDK_INSTRUMENTORS = {
    "openai": ("traceai_openai", "OpenAIInstrumentor"),
    "anthropic": ("traceai_anthropic", "AnthropicInstrumentor"),
}
# Our own scope, for the span that says a call went unmeasured.
SCOPE = "alk_simulation_measure"
# The SDK instrumentors' scopes: a model API request inside one of their spans is the SDK's own.
_SDK_SCOPES = frozenset(name for name, _attribute in SDK_INSTRUMENTORS.values())
# Model SDKs this cannot measure whose calls the wire check may not see: some reach no HTTP
# client it watches, and google-genai pointed at the model gateway sends to a host it does not
# know.
_UNSEEN_SDKS = (
    "google.generativeai",
    "google.genai",
    "vertexai",
    "google.cloud.aiplatform",
)
# Model API hosts: a request to one outside an SDK span is a model call nothing here measured.
_MODEL_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "api.mistral.ai",
        "api.cohere.ai",
        "api.cohere.com",
        "api.groq.com",
        "api.together.xyz",
        "api.fireworks.ai",
        "openrouter.ai",
        "api.deepseek.com",
        "api.x.ai",
        "api.perplexity.ai",
        "api.cerebras.ai",
        "api.replicate.com",
    }
)
_MODEL_HOST_SUFFIXES = (".openai.azure.com", "aiplatform.googleapis.com")
# Spans leave within this of ending, so the world reset's SIGTERM after a reply finds few waiting.
_BATCH_DELAY_MS = 200
# How long a flush may hold the process at SIGTERM or exit; the sandbox kills it after 5 s.
_FLUSH_MS = 2000
_PATCHED = "_alk_simulation_measure"

_lock = threading.Lock()
_state = {
    "partial": False,
    "provider": None,
    "resource": {},
    "model_hosts": frozenset(),
    # Our own export's host and path, which is never a model call even on a shared host.
    "export": ("", ""),
}
_flagged_calls: set = set()
_write = None


def register(patches: dict, write, mark) -> bool:
    """Add this module's patches to the mark hook's, when the sandbox asks for measuring, names
    where its spans go, and gives the simulation's mark: the resource pairs (the mark and the
    hidden project) that are the only ones a measured span carries. Returns whether it did."""
    global _write
    if os.environ.get(INSTRUMENT) != "traceai":
        return False
    if not (
        mark
        and os.environ.get(_ENDPOINT)
        and os.environ.get(_INGEST_API_KEY)
        and os.environ.get(_INGEST_SECRET_KEY)
    ):
        # Without the mark its spans would read as production traces, so nothing is measured.
        write("failed")
        return False
    _write = write
    _state["resource"] = dict(mark)
    _state["model_hosts"] = frozenset(
        host
        for host in (
            _normal_host(one) for one in os.environ.get(_MODEL_HOSTS_ENV, "").split(",")
        )
        if host
    )
    export = urllib.parse.urlsplit(os.environ[_ENDPOINT])
    _state["export"] = (_normal_host(export.hostname), export.path or "/")
    for name, target in SDK_INSTRUMENTORS.items():
        patches.setdefault(name, functools.partial(_instrument_sdk, target=target))
    for name in _UNSEEN_SDKS:
        patches.setdefault(name, _unseen_sdk)
    patches.setdefault("httpx", _patch_httpx)
    patches.setdefault("http.client", _patch_http_client)
    atexit.register(_flush)
    _flush_on_sigterm()
    return True


def _say(line: str) -> None:
    if _write is not None:
        _write(line)


def _provider():
    """Our provider, made once. Only the SDK instrumentors and the unmeasured-call span use it,
    so only their spans leave: a framework's own model span (LangChain's, say) goes to the
    provider the process or the mark hook serves, and a call made through a framework and its
    SDK is billed once."""
    with _lock:
        provider = _state["provider"]
        if provider is not None:
            return provider
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
        from opentelemetry.trace import Status

        class _Stamp(SpanProcessor):
            """Stamps a span started after an unmeasured call was seen."""

            def on_start(self, span, parent_context=None):
                if _state["partial"]:
                    span.set_attribute(STATE_KEY, PARTIAL)

        def _measured_only(span):
            kept = _kept_attributes(span.attributes)
            if kept is None and not span.events and not span.status.description:
                return span
            return ReadableSpan(
                name=span.name,
                context=span.context,
                parent=span.parent,
                resource=span.resource,
                attributes=span.attributes if kept is None else kept,
                # An exception event holds the error's message and stack trace.
                events=(),
                links=span.links,
                kind=span.kind,
                # The call still reads as failed, without the error's text.
                status=Status(span.status.status_code),
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            )

        class _MeasuredOnly(SpanExporter):
            """Sends each span with the model, the tokens and the timing only. It drops what
            the instrumentors may keep: the agent's tool definitions (names, descriptions,
            argument schemas), which no TraceConfig flag hides, and error text, which an older
            fi-instrumentation does not hide. Only our exporter is wrapped; the agent's own
            are not."""

            def __init__(self, exporter):
                self._exporter = exporter

            def export(self, spans):
                return self._exporter.export([_measured_only(span) for span in spans])

            def shutdown(self):
                self._exporter.shutdown()

            def force_flush(self, timeout_millis=30000):
                return self._exporter.force_flush(timeout_millis)

        exporter = OTLPSpanExporter(
            endpoint=os.environ[_ENDPOINT],
            headers={
                "x-api-key": os.environ[_INGEST_API_KEY],
                "x-secret-key": os.environ[_INGEST_SECRET_KEY],
            },
        )
        # The simulation's pairs only. Resource.create would also read the agent's own
        # OTEL_RESOURCE_ATTRIBUTES and OTEL_SERVICE_NAME, and the collector stores every
        # resource attribute with the span.
        provider = TracerProvider(resource=Resource(_state["resource"]))
        provider.add_span_processor(_Stamp())
        provider.add_span_processor(
            BatchSpanProcessor(
                _MeasuredOnly(exporter), schedule_delay_millis=_BATCH_DELAY_MS
            )
        )
        _state["provider"] = provider
        return provider


# The attributes a measured span never carries: the request's tool definitions, as the list and
# each tool's JSON schema (traceAI) and OpenInference's older form, and the error text an older
# fi-instrumentation keeps.
_DROPPED_KEYS = (
    "gen_ai.tool.definitions",
    "llm.tools",
    "exception.message",
    "exception.stacktrace",
    "error.message",
)


def _kept_attributes(attributes):
    """The span's attributes without the dropped ones, or None when it carries none of them."""
    if not attributes:
        return None
    dropped = {
        name
        for name in attributes
        if any(name == key or name.startswith(f"{key}.") for key in _DROPPED_KEYS)
    }
    if not dropped:
        return None
    return {name: value for name, value in attributes.items() if name not in dropped}


def _hidden_content():
    """The instrumentor settings that keep prompts, replies and error text out of every span."""
    from fi_instrumentation import TraceConfig

    flags = dict(
        hide_llm_invocation_parameters=True,
        hide_inputs=True,
        hide_outputs=True,
        hide_input_messages=True,
        hide_output_messages=True,
        hide_input_images=True,
        hide_input_text=True,
        hide_output_text=True,
        hide_embedding_vectors=True,
    )
    try:
        return TraceConfig(hide_error_details=True, **flags)
    except TypeError:
        # An older fi-instrumentation has no such setting and keeps error text; our exporter
        # drops it.
        return TraceConfig(**flags)


def _instrument_sdk(module, target) -> None:
    """Turn the SDK's instrumentor on into our provider, unless the process instruments it itself."""
    name, attribute = target
    if name in sys.modules:
        return
    try:
        instrumentor = getattr(importlib.import_module(name), attribute)()
        config = _hidden_content()
    except ImportError:
        # The build could not install it: this SDK's calls go unmeasured, so none reads as free.
        _say("unmeasured " + module.__name__ + ", its instrumentor is not installed")
        _mark_partial()
        return
    if getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
        return
    try:
        instrumentor.instrument(tracer_provider=_provider(), config=config)
    except Exception:
        instrumentor = None
    # An instrumentor that finds the SDK's version outside its range declines without raising.
    if not getattr(instrumentor, "is_instrumented_by_opentelemetry", False):
        _say("unmeasured " + module.__name__ + ", its instrumentor did not turn on")
        _mark_partial()
        return
    _yield_to_process(type(instrumentor), module.__name__)


def _yield_to_process(cls, sdk: str) -> None:
    """Let the process instrument the SDK itself in place of ours. Its first instrument() call
    takes ours off, with or without arguments: with none, the instrumentor would stay on our
    provider and the process's own tracing would get nothing. The SDK's calls are then the
    process's to trace, not ours to measure, so the process reads partial."""
    original = cls.instrument
    if getattr(original, _PATCHED, False):
        return
    ours = [True]

    def instrument(self, *args, __original=original, **kwargs):
        if ours[0]:
            ours[0] = False
            if getattr(self, "is_instrumented_by_opentelemetry", False):
                self.uninstrument()
            _say("unmeasured " + sdk + ", the agent instruments it itself")
            _mark_partial()
        return __original(self, *args, **kwargs)

    functools.update_wrapper(instrument, original)
    setattr(instrument, _PATCHED, True)
    cls.instrument = instrument


def _unseen_sdk(module) -> None:
    _say("unmeasured " + module.__name__ + ", no instrumentor covers it")
    _mark_partial()


def _current_call():
    path = os.environ.get(_CALL_FILE)
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip() or None
    except OSError:
        return None


def _mark_partial() -> None:
    """Every span from now on is stamped, and the call running now gets a span that says so."""
    _state["partial"] = True
    call = _current_call()
    if call is None:
        return
    with _lock:
        if call in _flagged_calls:
            return
        _flagged_calls.add(call)
    try:
        # The mark hook stamps the call on the span as it starts.
        span = (
            _provider()
            .get_tracer(SCOPE)
            .start_span("unmeasured model call", attributes={STATE_KEY: PARTIAL})
        )
        span.end()
    except Exception:
        # Without the SDK the build installs, nothing can go out: the call sends no span at all,
        # which the platform reads as unknown too.
        _say("unmeasured model call, no exporter")


def _normal_host(host) -> str:
    return (host or "").strip().lower().rstrip(".")


def _is_model_host(host) -> bool:
    host = _normal_host(host)
    if host in _MODEL_HOSTS or host in _state["model_hosts"]:
        return True
    if host.endswith(_MODEL_HOST_SUFFIXES):
        return True
    return host.startswith("bedrock-runtime.") and host.endswith(".amazonaws.com")


def _inside_sdk_span() -> bool:
    try:
        from opentelemetry import trace

        span = trace.get_current_span()
        scope = getattr(span, "instrumentation_scope", None)
        return bool(
            span.is_recording() and scope is not None and scope.name in _SDK_SCOPES
        )
    except Exception:
        return False


def _seen_request(target_of, client, args, kwargs) -> None:
    try:
        host, path = target_of(client, args, kwargs)
        if (
            _is_model_host(host)
            and (_normal_host(host), path) != _state["export"]
            and not _inside_sdk_span()
        ):
            _mark_partial()
    except Exception:
        _say("failed")


def _wrap(cls, method: str, target_of) -> None:
    original = getattr(cls, method)
    if getattr(original, _PATCHED, False):
        return

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        _seen_request(target_of, self, args, kwargs)
        return original(self, *args, **kwargs)

    setattr(wrapper, _PATCHED, True)
    setattr(cls, method, wrapper)


def _request_target(_client, args, kwargs):
    """The host and path an httpx request goes to."""
    request = args[0] if args else kwargs.get("request")
    url = getattr(request, "url", None)
    return getattr(url, "host", None), getattr(url, "path", "")


def _patch_httpx(module) -> None:
    _wrap(module.Client, "send", _request_target)
    _wrap(module.AsyncClient, "send", _request_target)


def _connection_target(connection, args, kwargs):
    """The host and path an http.client request goes to. The host is the URL's when it is sent
    whole, as through an HTTP proxy, else the tunnel's, as through an HTTPS proxy, else the
    connection's."""
    url = args[1] if len(args) > 1 else kwargs.get("url")
    url = url if isinstance(url, str) else ""
    if "://" in url:
        parts = urllib.parse.urlsplit(url)
        return parts.hostname, parts.path
    host = getattr(connection, "_tunnel_host", None) or connection.host
    return host, url.split("?", 1)[0]


def _patch_http_client(module) -> None:
    # Every standard-library request starts here: urllib.request's, and urllib3's under
    # requests and botocore.
    _wrap(module.HTTPConnection, "putrequest", _connection_target)


def _flush() -> None:
    provider = _state["provider"]
    if provider is None:
        return
    try:
        provider.force_flush(_FLUSH_MS)
    except Exception:
        pass


def _flush_on_sigterm() -> None:
    """Flush before the default SIGTERM ends the process: the sandbox sends one at each reset,
    often within a second of the agent's reply. A process that sets its own handler later keeps
    it, and the flush at exit covers its orderly shutdown."""
    if threading.current_thread() is not threading.main_thread():
        return
    try:
        previous = signal.getsignal(signal.SIGTERM)
    except (ValueError, OSError):
        return
    if previous is signal.SIG_IGN:
        return

    def on_term(signum, frame):
        _flush()
        if callable(previous):
            previous(signum, frame)
            return
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.kill(os.getpid(), signal.SIGTERM)

    try:
        signal.signal(signal.SIGTERM, on_term)
    except (ValueError, OSError):
        pass
