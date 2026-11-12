"""Measures the model calls of an agent that traces nothing itself.

The platform asks for this (``FI_SIMULATION_INSTRUMENT``) only for an agent whose source names no
tracing, so that every run has the models, tokens and timing a fix is judged on. The build
installed traceAI's instrumentor beside each SDK below that the agent's own install holds, with
every installed version pinned, and skipped one that would not fit; that skip is the preflight. A
missing instrumentor here therefore means its calls go unmeasured, never a failed start.

When the agent first imports one of the SDKs, its instrumentor is turned on into a provider of our
own, never the global one, so any tracing the agent sets up itself is left as it is. Only those
instrumentors' spans leave the process, with no prompt, reply or error text (the instrumentors
hide it) and with the model, tokens and timing kept. They go to the address the sandbox names
under the agent's ingest key, and the mark hook stamps each with the call it belongs to.

A model call this cannot see, through an SDK with no instrumentor here or as a raw HTTP request to
a model API, makes the process ``partial``. A span stamped ``fi.simulation.instrumentation=partial``
goes out with that call, and every span after it carries the stamp, so the platform reads the
call's cost as unknown, never as too low.

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

INSTRUMENT = "FI_SIMULATION_INSTRUMENT"
_ENDPOINT = "FI_SIMULATION_TRACES_ENDPOINT"
_INGEST_API_KEY = "FI_SIMULATION_INGEST_API_KEY"
_INGEST_SECRET_KEY = "FI_SIMULATION_INGEST_SECRET_KEY"
_CALL_FILE = "FI_SIMULATION_CALL_FILE"
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
# Model SDKs this cannot measure whose calls reach no HTTP client the wire check sees.
_UNSEEN_SDKS = ("google.generativeai", "vertexai", "google.cloud.aiplatform")
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
_state = {"partial": False, "provider": None}
_flagged_calls: set = set()
_write = None


def register(patches: dict, write) -> bool:
    """Add this module's patches to the mark hook's, when the sandbox asks for measuring and
    names where its spans go. Returns whether it did."""
    global _write
    if os.environ.get(INSTRUMENT) != "traceai":
        return False
    if not (
        os.environ.get(_ENDPOINT)
        and os.environ.get(_INGEST_API_KEY)
        and os.environ.get(_INGEST_SECRET_KEY)
    ):
        write("failed")
        return False
    _write = write
    for name, target in SDK_INSTRUMENTORS.items():
        patches.setdefault(name, functools.partial(_instrument_sdk, target=target))
    for name in _UNSEEN_SDKS:
        patches.setdefault(name, _unseen_sdk)
    patches.setdefault("httpx", _patch_httpx)
    patches.setdefault("urllib3.connectionpool", _patch_urllib3)
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

        class _Stamp(SpanProcessor):
            """Stamps a span started after an unmeasured call was seen."""

            def on_start(self, span, parent_context=None):
                if _state["partial"]:
                    span.set_attribute(STATE_KEY, PARTIAL)

        def _without_tool_definitions(span):
            kept = _kept_attributes(span.attributes)
            if kept is None:
                return span
            return ReadableSpan(
                name=span.name,
                context=span.context,
                parent=span.parent,
                resource=span.resource,
                attributes=kept,
                events=span.events,
                links=span.links,
                kind=span.kind,
                status=span.status,
                start_time=span.start_time,
                end_time=span.end_time,
                instrumentation_scope=span.instrumentation_scope,
            )

        class _WithoutToolDefinitions(SpanExporter):
            """Sends each span without the agent's tool definitions (names, descriptions,
            argument schemas), which no TraceConfig flag hides: what leaves is the model, the
            tokens and the timing. Only our exporter is wrapped; the agent's own are not."""

            def __init__(self, exporter):
                self._exporter = exporter

            def export(self, spans):
                return self._exporter.export(
                    [_without_tool_definitions(span) for span in spans]
                )

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
        # Resource.create reads OTEL_RESOURCE_ATTRIBUTES: the mark and the hidden project.
        provider = TracerProvider(resource=Resource.create())
        provider.add_span_processor(_Stamp())
        provider.add_span_processor(
            BatchSpanProcessor(
                _WithoutToolDefinitions(exporter), schedule_delay_millis=_BATCH_DELAY_MS
            )
        )
        _state["provider"] = provider
        return provider


# The attributes the SDK instrumentors write the request's tool definitions under: the list
# and each tool's JSON schema (traceAI), and OpenInference's older form.
_TOOL_DEFINITION_KEYS = ("gen_ai.tool.definitions", "llm.tools")


def _kept_attributes(attributes):
    """The span's attributes without its tool definitions, or None when it carries none."""
    if not attributes:
        return None
    dropped = {
        name
        for name in attributes
        if any(
            name == key or name.startswith(f"{key}.") for key in _TOOL_DEFINITION_KEYS
        )
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
        # An older fi-instrumentation has no such setting; it hides error text with the rest.
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
    _yield_to_process(type(instrumentor))


def _yield_to_process(cls) -> None:
    """Let the process instrument the SDK itself, with its own provider, in place of ours."""
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


def _is_model_host(host) -> bool:
    host = (host or "").lower().rstrip(".")
    if host in _MODEL_HOSTS or host.endswith(_MODEL_HOST_SUFFIXES):
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


def _seen_request(host) -> None:
    try:
        if _is_model_host(host) and not _inside_sdk_span():
            _mark_partial()
    except Exception:
        _say("failed")


def _wrap(cls, method: str, host_of) -> None:
    original = getattr(cls, method)
    if getattr(original, _PATCHED, False):
        return

    @functools.wraps(original)
    def wrapper(self, *args, **kwargs):
        _seen_request(host_of(self, args, kwargs))
        return original(self, *args, **kwargs)

    setattr(wrapper, _PATCHED, True)
    setattr(cls, method, wrapper)


def _request_host(_client, args, kwargs):
    request = args[0] if args else kwargs.get("request")
    return getattr(getattr(request, "url", None), "host", None)


def _patch_httpx(module) -> None:
    _wrap(module.Client, "send", _request_host)
    _wrap(module.AsyncClient, "send", _request_host)


def _patch_urllib3(module) -> None:
    # requests and botocore both send through a urllib3 pool, which knows its host.
    _wrap(module.HTTPConnectionPool, "urlopen", lambda pool, _a, _k: pool.host)


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
