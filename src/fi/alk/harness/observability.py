"""Emit a hosted harness run to Observe: one session per job, a span per stage and per scenario,
with the model and tool calls each stage made nested inside it.

Every job reports into the platform's own account, so tenancy travels as attributes. Identifiers
only. Configured by HARNESS_OBSERVABILITY, FI_API_KEY, FI_SECRET_KEY, FI_BASE_URL and
FI_HARNESS_PROJECT; absent credentials mean untraced, and every failure here is swallowed.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from typing import Any, Iterator, Mapping

from opentelemetry.sdk.trace.export import SpanExporter

logger = logging.getLogger(__name__)

_OFF = {"0", "off", "false", "no"}

_provider: Any = None
_tracer: Any = None
_session = contextlib.ExitStack()
_stage_span: Any = None
_stage_token: Any = None
_context: dict[str, Any] = {}


def enabled() -> bool:
    if os.getenv("HARNESS_OBSERVABILITY", "").strip().lower() in _OFF:
        return False
    return bool(os.getenv("FI_API_KEY") and os.getenv("FI_SECRET_KEY"))


def begin(
    job_id: str,
    run_id: str,
    context: Mapping[str, Any] | None = None,
    secret_values: tuple[str, ...] = (),
) -> None:
    """Open the job's session, once, as early as the job id is known."""
    global _provider, _tracer, _context
    if not enabled() or _provider is not None:
        return
    _context = {str(k): v for k, v in (context or {}).items() if v not in ("", None)}
    _context.setdefault("job_id", job_id)
    _context.setdefault("run_id", run_id)
    try:
        from fi_instrumentation import (
            FITracer,
            register,
            using_metadata,
            using_session,
            using_tags,
            using_user,
        )
        from fi_instrumentation.fi_types import ProjectType
        from fi_instrumentation.otel import BatchSpanProcessor

        _provider = register(
            project_name=os.getenv("FI_HARNESS_PROJECT") or "hosted-harness",
            project_type=ProjectType.OBSERVE,
            # The platform binds the global provider to its own infrastructure tracing.
            set_global_tracer_provider=False,
            verbose=False,
        )
        cleaner = _CleaningExporter(_exporter(), secret_values)
        _swap(_provider, BatchSpanProcessor(span_exporter=cleaner))
        _instrument(_provider)
        # After the instrumentors, which are handed the provider and may add a processor of their
        # own: any second processor would send spans to Observe uncleaned.
        _only_the_cleaner(_provider, cleaner)
        # Only FITracer writes the baggage-carried session, user, metadata and tags onto a span.
        _tracer = FITracer(_provider.get_tracer(__name__))
        _session.enter_context(using_session(job_id))
        organization = str(_context.get("organization_id") or "")
        if organization:
            _session.enter_context(using_user(organization))
        _session.enter_context(using_metadata(dict(_context)))
        _session.enter_context(using_tags(_tags()))
    except Exception as error:
        # Untraced must mean nothing can still export. On fi 1.0.0 the provider's shutdown leaves
        # a SimpleSpanProcessor holding fi's HTTPSpanExporter posting, so every processor is
        # taken off the provider first, then shut down.
        removed: Any = ()
        with contextlib.suppress(Exception):
            removed = _provider._active_span_processor._span_processors
            _provider._active_span_processor._span_processors = ()
        for processor in removed:
            with contextlib.suppress(Exception):
                processor.shutdown()
        with contextlib.suppress(Exception):
            if _provider is not None:
                _provider.shutdown()
        with contextlib.suppress(Exception):
            # The class only: an error's text can repeat what it failed on.
            logger.warning("tracing off: %s", type(error).__name__)
        _provider = None
        _tracer = None


def _exporter() -> Any:
    from fi_instrumentation.otel import HTTPSpanExporter

    # Endpoint from FI_BASE_URL, headers from FI_API_KEY and FI_SECRET_KEY, as register() does.
    return HTTPSpanExporter()


def _swap(provider: Any, processor: Any) -> None:
    """Make processor the provider's only one, in place of whatever register() installed.

    Private surface of fi-instrumentation's TracerProvider and the SDK's: _active_span_processor,
    its _span_processors tuple, and _default_processor. Clearing the flag stops fi 1.0.0's
    add_span_processor from shutting the cleaner down and dropping it. No tracer from this
    provider exists yet, so the processors replaced hold no spans.
    """
    old = provider._active_span_processor._span_processors
    provider._active_span_processor._span_processors = (processor,)
    provider._default_processor = False
    for each in old:
        each.shutdown()


class _TracingNotClean(Exception):
    pass


def _only_the_cleaner(provider: Any, cleaner: Any) -> None:
    processors = provider._active_span_processor._span_processors
    if (
        len(processors) != 1
        or getattr(processors[0], "span_exporter", None) is not cleaner
    ):
        raise _TracingNotClean()


# The lengths traceai cuts span text to. A cut between ":" and "@" leaves a numeric password
# looking like a port, which the URL cleaner keeps.
_CUTS = (5000, 2000, 1000, 500)
_SCHEME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789+.-"
)


def _cut(value: str) -> str:
    """A value of a cut length that ends inside an address loses it, from its scheme on.

    Run on the text as traceai recorded it, before anything else shortens it. A 5,000-character
    value ends in traceai's "...", which goes with the address.
    """
    if len(value) not in _CUTS:
        return value
    return _cut_address(value)


def _cut_address(value: str) -> str:
    at = value.rfind("://")
    if at < 0 or any(character.isspace() for character in value[at + 3 :]):
        return value
    start = at
    while start > 0 and value[start - 1] in _SCHEME_CHARACTERS:
        start -= 1
    return value[:start] + "[redacted]"


class _CleaningExporter(SpanExporter):
    """Hand the real exporter copies of each span with every string in it cleaned.

    A span that can't be gathered or copied is dropped, and a batch that can't be cleaned is
    dropped whole: nothing raw reaches the exporter, and no exception reaches the SDK, whose own
    log line would carry the error's text.
    """

    def __init__(self, inner: Any, secret_values: tuple[str, ...] = ()) -> None:
        self._inner = inner
        # The job's own secret values, replaced whole as the other outbound channels replace them
        # (outbound.redact_outbound_text): a key a tool printed has no shape to recognise.
        self._secrets = _secret_patterns(secret_values)
        # One cleaned copy per resource, made the first time it is seen.
        self._resources: dict[int, tuple[Any, Any]] = {}

    def export(self, spans: Any) -> Any:
        from opentelemetry.sdk.trace.export import SpanExportResult

        error: Exception | None = None
        try:
            spans = list(spans)
            flat: list[str] = []
            plans: list[Any] = []
            for span in spans:
                try:
                    plans.append(_gather(span, flat, self._secrets))
                except Exception as dropped:
                    # Strings this span already put in flat stay there, unused.
                    error = error or dropped
                    plans.append(None)
            from fi.alk.providers import agent

            # One call for the whole batch, so the cleaner logs at most once per batch. Each
            # attribute's key has already been applied in _take: a value whose key names a
            # secret never reaches this list.
            cleaned = agent._redact(flat)
            copies = []
            for span, plan in zip(spans, plans):
                if plan is None:
                    continue
                try:
                    copies.append(_rebuild(span, plan, cleaned, self._resource(span.resource)))
                except Exception as dropped:
                    error = error or dropped
            if not copies:
                # Every span was dropped: send nothing, rather than an empty request.
                return SpanExportResult.FAILURE
            return self._inner.export(copies)
        except Exception as failed:
            error = failed
            return SpanExportResult.FAILURE
        finally:
            if error is not None:
                with contextlib.suppress(Exception):
                    logger.warning("trace spans dropped: %s", type(error).__name__)

    def _resource(self, resource: Any) -> Any:
        known = self._resources.get(id(resource))
        if known is not None and known[0] is resource:
            return known[1]
        from opentelemetry.sdk.resources import Resource

        from fi.alk.providers import agent

        flat: list[str] = []
        # No declared values here: they would also hit platform configuration (the project name),
        # which the platform routes the job's spans by.
        plan = {
            key: _take(value, flat, key, ())
            for key, value in (resource.attributes or {}).items()
        }
        cleaned = agent._redact(flat)
        copy = Resource(
            {key: _give(where, cleaned) for key, where in plan.items()}, resource.schema_url
        )
        self._resources[id(resource)] = (resource, copy)
        return copy

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)


# outbound.redact_outbound_text's floor: a shorter declared value would mask ordinary words.
_SHORTEST_SECRET = 8
_SHORTEST_PIN = 4


# Platform settings the simulator and gateway channels carry beside their credentials. A span
# keeps these: masking them hid every LLM span's model and sent a job's traces to a project
# named "***". Any name not listed is treated as a secret, so a new setting fails closed (masked)
# until it is added here.
NON_SECRET_SETTINGS = frozenset(
    {
        "AGENTCC_BASE_URL",
        "ALK_BACKGROUND_NOISE",
        "ALK_BACKGROUND_NOISE_CATALOG",
        "ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER",
        "ALK_HARNESS",
        "ALK_HARNESS_MODEL",
        "ALK_HARNESS_THINKING",
        "ALK_JUDGE_MODE",
        "ALK_VERTEX_LOCATION",
        "ALK_VOICEMAIL_SCENARIOS",
        "FI_BASE_URL",
        "FI_HARNESS_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_GENAI_USE_VERTEXAI",
        "HARNESS_BACKGROUND_NOISE_VOLUME",
        "HARNESS_CALLER_BARGE_IN_RATE",
        "HARNESS_OBSERVABILITY",
        "LIVEKIT_URL",
        "SIMULATOR_LLM_FALLBACK_MODELS",
        "SIMULATOR_LLM_MODEL",
        "SIMULATOR_LLM_PROVIDER",
        "SIMULATOR_LLM_THINKING",
        "SIMULATOR_STT_MODEL",
        "SIMULATOR_STT_PROVIDER",
        "SIMULATOR_TTS_MODEL",
        "SIMULATOR_TTS_PROVIDER",
    }
)


def declared_values(
    values: Mapping[str, str], settings: frozenset[str] = NON_SECRET_SETTINGS
) -> tuple[str, ...]:
    """Every value in one alias -> value source, except those under a name in ``settings``.

    Call it once per source and join the tuples. Merging the sources first would let one value
    overwrite another under the same name, and the overwritten one would never be masked.
    """
    return tuple(
        str(value) for alias, value in values.items() if value and alias not in settings
    )


def _secret_patterns(secret_values: tuple[str, ...]) -> tuple[Any, ...]:
    """What _mask replaces: each value of 8 or more characters, also as JSON escapes it (text that
    isn't parsed as JSON carries it escaped), longest first; then one pattern for short all-digit
    values such as a 4-digit PIN, matched only between non-digits so it can't hit inside a longer
    number. Other short values would hit ordinary words, so they are left."""
    long = {value for value in secret_values if len(value) >= _SHORTEST_SECRET}
    forms = set(long)
    for value in long:
        for ascii_only in (True, False):
            forms.add(json.dumps(value, ensure_ascii=ascii_only)[1:-1])
    short = {
        value
        for value in secret_values
        if _SHORTEST_PIN <= len(value) < _SHORTEST_SECRET
        and value.isascii()
        and value.isdigit()
    }
    patterns: list[Any] = sorted(forms, key=len, reverse=True)
    if short:
        alternatives = "|".join(re.escape(value) for value in sorted(short, key=len, reverse=True))
        patterns.append(re.compile(rf"(?<![0-9])(?:{alternatives})(?![0-9])"))
    return tuple(patterns)


def _mask(text: str, secrets: tuple[Any, ...]) -> str:
    for secret in secrets:
        if isinstance(secret, str):
            text = text.replace(secret, "***")
        else:
            text = secret.sub("***", text)
    return text


def _named_secret(key: str) -> bool:
    """The cleaner's own key rule (providers/agent.py _redact_in), applied to one string."""
    from fi.alk.providers.agent import _REDACTION_MARKERS

    lowered = key.lower()
    return lowered == "headers" or any(marker in lowered for marker in _REDACTION_MARKERS)


def _take(value: Any, flat: list[str], key: str, secrets: tuple[Any, ...]) -> Any:
    """Put each string of an attribute value in flat; return where to find it again.

    Only strings are cleaned: a number or a bool under a key such as gen_ai.usage.input_tokens
    passes as it is.
    """
    if isinstance(value, str):
        return _take_text(value, flat, key, secrets)
    if isinstance(value, (tuple, list)):
        return ("sequence", [_take(item, flat, key, secrets) for item in value])
    return ("as is", value)


def _take_text(value: str, flat: list[str], key: str, secrets: tuple[Any, ...]) -> Any:
    if _named_secret(key):
        return ("as is", "[redacted]")
    # _cut and _as_json read the value as recorded: replacing a secret first changes its length
    # (so a value traceai cut is no longer of a cut length) and, inside JSON, its escaping.
    cut = _cut(value)
    if cut == value:
        # traceai records tool input, messages and metadata as JSON. Cleaned as prose, a URL with
        # a query takes the rest of its whitespace-free run, the JSON's closing quote and comma
        # with it; cleaned as a structure, each string is cleaned on its own and the JSON stays
        # JSON. A value cut short isn't JSON any more, so it is cleaned as text.
        tree = _as_json(value)
        if tree is not None:
            return ("json", _take_json(tree, flat, key, secrets))
    # The cut is decided on the value as recorded, but made on the masked text: cutting first can
    # take a secret's tail with the address and leave its head, which no longer matches.
    flat.append(_cut_address(_mask(value, secrets)) if cut != value else _mask(value, secrets))
    return ("text", len(flat) - 1)


def _as_json(value: str) -> Any:
    opening = value.lstrip()[:1]
    if opening not in ("{", "["):
        return None
    try:
        tree = json.loads(value)
    except (ValueError, RecursionError):
        return None
    if not isinstance(tree, (dict, list)) or _too_deep(tree):
        return None
    return tree


# Deeper than this is cleaned as text: the walks below recurse, and the interpreter's limit is
# not ours to hit inside a batch.
_JSON_DEPTH = 64


def _too_deep(tree: Any) -> bool:
    """Whether tree nests past _JSON_DEPTH, measured without recursion."""
    pending = [(tree, 1)]
    while pending:
        node, depth = pending.pop()
        if depth > _JSON_DEPTH:
            return True
        children = node.values() if isinstance(node, dict) else node
        pending.extend(
            (child, depth + 1) for child in children if isinstance(child, (dict, list))
        )
    return False


def _take_json(node: Any, flat: list[str], key: str, secrets: tuple[Any, ...]) -> Any:
    if isinstance(node, dict):
        pairs = []
        for name, item in node.items():
            # The key is a string that goes out too. The key rule reads it raw.
            flat.append(_mask(str(name), secrets))
            pairs.append((len(flat) - 1, _take_json(item, flat, str(name), secrets)))
        return ("dict", pairs)
    if isinstance(node, list):
        return ("list", [_take_json(item, flat, key, secrets) for item in node])
    if isinstance(node, str):
        if _named_secret(key):
            return ("as is", "[redacted]")
        # A string inside JSON can itself be JSON (a tool's input dumped into a message).
        tree = _as_json(node)
        if tree is not None:
            return ("json text", _take_json(tree, flat, key, secrets))
        flat.append(_mask(node, secrets))
        return ("text", len(flat) - 1)
    return ("as is", node)


def _give(where: Any, cleaned: list[Any]) -> Any:
    kind, body = where
    if kind == "text":
        return cleaned[body]
    if kind == "sequence":
        return tuple(_give(item, cleaned) for item in body)
    if kind == "json":
        return json.dumps(_give_json(body, cleaned))
    return body


def _give_json(where: Any, cleaned: list[Any]) -> Any:
    kind, body = where
    if kind == "dict":
        rebuilt: dict[str, Any] = {}
        for where_name, item in body:
            name = cleaned[where_name]
            # Two keys can clean to the same text: keep both values.
            unique, count = name, 1
            while unique in rebuilt:
                count += 1
                unique = f"{name} ({count})"
            rebuilt[unique] = _give_json(item, cleaned)
        return rebuilt
    if kind == "list":
        return [_give_json(item, cleaned) for item in body]
    if kind == "text":
        return cleaned[body]
    if kind == "json text":
        return json.dumps(_give_json(body, cleaned))
    return body


def _gather(span: Any, flat: list[str], secrets: tuple[Any, ...]) -> Any:
    def mapping(attributes: Any) -> Any:
        return {
            key: _take(value, flat, key, secrets) for key, value in (attributes or {}).items()
        }

    description = span.status.description
    return (
        _take(span.name, flat, "", secrets),
        mapping(span.attributes),
        [
            (_take(event.name, flat, "", secrets), mapping(event.attributes))
            for event in span.events
        ],
        [mapping(link.attributes) for link in span.links],
        _take(description, flat, "", secrets)
        if isinstance(description, str)
        else ("as is", description),
    )


def _rebuild(span: Any, plan: Any, cleaned: list[Any], resource: Any) -> Any:
    from opentelemetry.attributes import BoundedAttributes
    from opentelemetry.sdk import trace as sdk_trace
    from opentelemetry.sdk.util import BoundedList
    from opentelemetry.trace import Link, Status

    name, attributes, events, links, description = plan

    # The SDK reports a dropped count only off these two classes' .dropped, so the copy uses
    # them and carries each count over.
    def bounded(where: Any, dropped: int) -> Any:
        rebuilt = BoundedAttributes(
            maxlen=None,
            attributes={key: _give(each, cleaned) for key, each in where.items()},
            immutable=True,
        )
        rebuilt.dropped = dropped
        return rebuilt

    new_events = BoundedList(None)
    new_events.extend(
        sdk_trace.Event(
            _give(event_name, cleaned),
            bounded(where, event.dropped_attributes),
            event.timestamp,
        )
        for event, (event_name, where) in zip(span.events, events)
    )
    new_events.dropped = span.dropped_events
    new_links = BoundedList(None)
    new_links.extend(
        Link(link.context, bounded(where, link.dropped_attributes))
        for link, where in zip(span.links, links)
    )
    new_links.dropped = span.dropped_links
    return sdk_trace.ReadableSpan(
        name=_give(name, cleaned),
        context=span.context,
        parent=span.parent,
        resource=resource,
        attributes=bounded(attributes, span.dropped_attributes),
        events=new_events,
        links=new_links,
        kind=span.kind,
        status=Status(span.status.status_code, _give(description, cleaned)),
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def stage_event(event_type: str, name: str, payload: Mapping[str, Any] | None = None) -> None:
    """Drive stage spans from the harness's own stage announcements."""
    if _tracer is None:
        return
    with contextlib.suppress(Exception):
        if event_type.endswith(".started"):
            _open_stage(name)
        elif event_type.endswith((".completed", ".failed")) and _stage_span is not None:
            record(
                _stage_span,
                stage_outcome="failed" if event_type.endswith(".failed") else "completed",
                stage_status=(payload or {}).get("status"),
            )
            _end_stage()


def stage(name: str) -> None:
    if _tracer is None:
        return
    with contextlib.suppress(Exception):
        _open_stage(name)


@contextlib.contextmanager
def scenario(key: str, index: int) -> Iterator[Any]:
    if _tracer is None:
        yield None
        return
    span = None
    token = None
    try:
        from opentelemetry import context as otel_context
        from opentelemetry import trace as otel_trace

        span = _tracer.start_span(f"harness.scenario.{key}")
        span.set_attribute("gen_ai.span.kind", "CHAIN")
        span.set_attribute("harness.scenario.key", key)
        span.set_attribute("harness.scenario.index", index)
        _apply_context(span)
        # Current for this task only, so the scenario's own model and tool calls nest inside it.
        token = otel_context.attach(otel_trace.set_span_in_context(span))
    except Exception:
        span = None
    try:
        yield span
    finally:
        with contextlib.suppress(Exception):
            if token is not None:
                from opentelemetry import context as otel_context

                otel_context.detach(token)
        with contextlib.suppress(Exception):
            if span is not None:
                span.end()


def record(span: Any, **attributes: Any) -> None:
    if span is None:
        return
    with contextlib.suppress(Exception):
        for name, value in attributes.items():
            if value is not None:
                span.set_attribute(f"harness.{name}", _scalar(value))


def end() -> None:
    """Close the session and flush; the guest exits next, taking the exporter with it."""
    global _provider, _tracer
    with contextlib.suppress(Exception):
        _end_stage()
    with contextlib.suppress(Exception):
        _session.close()
    if _provider is not None:
        with contextlib.suppress(Exception):
            _provider.force_flush()
    _provider = None
    _tracer = None


def _open_stage(name: str) -> None:
    global _stage_span, _stage_token
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace

    _end_stage()
    _stage_span = _tracer.start_span(f"harness.stage.{name}")
    _stage_span.set_attribute("gen_ai.span.kind", "CHAIN")
    _stage_span.set_attribute("harness.stage", name)
    _apply_context(_stage_span)
    # Current, so the stage's model and tool calls nest inside it rather than arriving as roots.
    _stage_token = otel_context.attach(otel_trace.set_span_in_context(_stage_span))


def _end_stage() -> None:
    global _stage_span, _stage_token
    if _stage_token is not None:
        with contextlib.suppress(Exception):
            from opentelemetry import context as otel_context

            otel_context.detach(_stage_token)
        _stage_token = None
    if _stage_span is not None:
        _stage_span.end()
        _stage_span = None


def _tags() -> list[str]:
    tags = ["hosted-harness"]
    for key in ("connector", "deployment"):
        value = str(_context.get(key) or "")
        if value:
            tags.append(f"{key}:{value}")
    return tags


def _scalar(value: Any) -> Any:
    return value if isinstance(value, (str, bool, int, float)) else str(value)


def _apply_context(span: Any) -> None:
    for name, value in _context.items():
        with contextlib.suppress(Exception):
            span.set_attribute(f"harness.{name}", _scalar(value))


def _instrument(provider: Any) -> None:
    """Enable whichever instrumentors the image ships; the backend in use is the one that emits."""
    for module, name in (
        ("traceai_google_adk", "GoogleADKInstrumentor"),
        ("traceai_claude_agent_sdk", "ClaudeAgentInstrumentor"),
        ("traceai_google_genai", "GoogleGenAIInstrumentor"),
        ("traceai_litellm", "LiteLLMInstrumentor"),
        ("traceai_openai", "OpenAIInstrumentor"),
        ("traceai_anthropic", "AnthropicInstrumentor"),
    ):
        with contextlib.suppress(Exception):
            instrumentor = getattr(__import__(module, fromlist=[name]), name)
            instrumentor().instrument(tracer_provider=provider)
