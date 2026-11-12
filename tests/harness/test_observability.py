"""The harness's span shape: stages and scenarios must parent the calls made inside them.

And what reaches Observe: begin() installs a cleaning exporter as the provider's only processor,
so every string a span carries is cleaned of credentials first (TH-9504).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
import requests
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import Link, SpanContext, Status, StatusCode, TraceFlags

from fi.alk.harness import observability

# The real one, kept before any fixture swaps it out.
_REAL_EXPORTER = observability._exporter


@pytest.fixture
def exporter(monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(observability, "_tracer", provider.get_tracer(__name__))
    monkeypatch.setattr(observability, "_context", {"job_id": "job-1"})
    yield exporter, provider.get_tracer(__name__)
    observability._end_stage()


def _by_name(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


def test_a_stage_span_parents_the_calls_made_during_it(exporter):
    exporter, tracer = exporter
    observability.stage_event("harness.stage.started", "environment")
    with tracer.start_as_current_span("call_llm"):
        pass
    observability.stage_event("harness.stage.completed", "environment")

    spans = _by_name(exporter)
    stage = spans["harness.stage.environment"]
    assert spans["call_llm"].parent.span_id == stage.context.span_id
    assert stage.parent is None


def test_a_scenario_span_parents_the_calls_made_inside_it(exporter):
    exporter, tracer = exporter
    with observability.scenario("checkout", 0):
        with tracer.start_as_current_span("execute_tool"):
            pass

    spans = _by_name(exporter)
    scenario = spans["harness.scenario.checkout"]
    assert spans["execute_tool"].parent.span_id == scenario.context.span_id


def test_a_stage_records_the_outcome_it_ended_with(exporter):
    exporter, _ = exporter
    observability.stage_event("harness.stage.started", "calls")
    observability.stage_event("harness.stage.failed", "calls", {"status": "boom"})

    stage = _by_name(exporter)["harness.stage.calls"]
    assert stage.attributes["harness.stage_outcome"] == "failed"
    assert stage.attributes["harness.stage_status"] == "boom"


def test_a_second_stage_closes_the_one_before_it(exporter):
    exporter, _ = exporter
    observability.stage_event("harness.stage.started", "understand")
    observability.stage_event("harness.stage.started", "environment")

    assert "harness.stage.understand" in _by_name(exporter)


def test_tracing_off_is_silent_rather_than_fatal(monkeypatch):
    monkeypatch.setattr(observability, "_tracer", None)
    observability.stage_event("harness.stage.started", "environment")
    with observability.scenario("k", 0) as span:
        assert span is None


URL = "https://u:pw@h/x?key=v"
CLEAN = "https://h/x"


def _leaks(text):
    return "pw@" in text or "key=v" in text


class _Run:
    """The real begin() and register(), with the cleaner's exporter in memory."""

    def __init__(self, memory, sent, posts, restore_export):
        self.memory = memory
        self.sent = sent
        self.posts = posts
        self.provider = None
        self._restore_export = restore_export

    def begin(self, secret_values=()):
        observability.begin("job", "run", secret_values=secret_values)
        self.provider = observability._provider
        return self

    def live_transport(self):
        """Let fi's HTTPSpanExporter run for real, so a span it sends shows in posts."""
        self._restore_export()

    @property
    def tracer(self):
        return self.provider.get_tracer("test")

    @property
    def cleaner(self):
        return self.provider._active_span_processor._span_processors[0].span_exporter

    def spans(self):
        self.provider.force_flush()
        return self.memory.get_finished_spans()

    def one(self):
        spans = self.spans()
        assert len(spans) == 1
        return spans[0]


@pytest.fixture
def _real_begin(monkeypatch, caplog):
    from fi_instrumentation import otel as fi_otel

    monkeypatch.setenv("FI_API_KEY", "fake-api-key")
    monkeypatch.setenv("FI_SECRET_KEY", "fake-secret-key")
    # A closed port: nothing is listening, and the recorders below stop any send first.
    monkeypatch.setenv("FI_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("HARNESS_OBSERVABILITY", raising=False)
    monkeypatch.setattr(observability, "_provider", None)
    monkeypatch.setattr(observability, "_tracer", None)
    monkeypatch.setattr(observability, "_context", {})
    memory = InMemorySpanExporter()
    monkeypatch.setattr(observability, "_exporter", lambda: memory)

    sent = []

    def recorder(self, spans, *args, **kwargs):
        sent.append(list(spans))
        return SpanExportResult.SUCCESS

    original_export = fi_otel.HTTPSpanExporter.export
    # On the class, so any exporter register() built records what it was sent.
    monkeypatch.setattr(fi_otel.HTTPSpanExporter, "export", recorder)

    posts = []

    def post(self, url, *args, **kwargs):
        posts.append(url)
        # Accepted, so a send let through doesn't retry with backoff.
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    caplog.set_level(logging.WARNING)
    run = _Run(
        memory,
        sent,
        posts,
        lambda: monkeypatch.setattr(
            fi_otel.HTTPSpanExporter, "export", original_export
        ),
    )
    yield run
    observability.end()
    if run.provider is not None:
        # Stops the batch processor's worker thread; end() only flushes.
        run.provider.shutdown()


def _warnings(caplog):
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == observability.logger.name
        and record.levelno >= logging.WARNING
    ]


def _readable(name="span", limits=None, links=(), **attributes):
    """A finished span, built on a provider of its own."""
    memory = InMemorySpanExporter()
    provider = TracerProvider(span_limits=limits) if limits else TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer("test").start_as_current_span(
        name, attributes=attributes, links=list(links)
    ):
        pass
    return memory.get_finished_spans()[0]


def _context():
    return SpanContext(1, 2, is_remote=False, trace_flags=TraceFlags(1))


def test_t1_a_span_is_exported_and_cleaned(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        span.set_attribute("tool.input", URL)

    assert run.one().attributes["tool.input"] == CLEAN


def test_t2_the_cleaner_is_the_only_processor_and_registers_exporter_gets_nothing(
    _real_begin,
):
    run = _real_begin.begin()
    assert observability._tracer is not None
    processors = run.provider._active_span_processor._span_processors
    assert len(processors) == 1
    cleaner = processors[0].span_exporter
    assert isinstance(cleaner, observability._CleaningExporter)
    assert cleaner._inner is run.memory
    assert run.provider._default_processor is False
    with run.tracer.start_as_current_span("tool") as span:
        span.set_attribute("tool.input", URL)

    run.one()
    assert run.sent == []
    assert run.posts == []


def test_t3_a_raw_processor_left_after_the_swap_turns_tracing_off(
    _real_begin, monkeypatch, caplog
):
    kept = []

    def append(provider, processor):
        kept.append(provider)
        multi = provider._active_span_processor
        multi._span_processors = multi._span_processors + (processor,)

    monkeypatch.setattr(observability, "_swap", append)
    run = _real_begin.begin()

    assert observability._tracer is None
    with kept[0].get_tracer("test").start_as_current_span("tool") as span:
        span.set_attribute("tool.input", URL)
    kept[0].force_flush()
    assert run.memory.get_finished_spans() == ()
    assert run.sent == []
    assert _warnings(caplog) == ["tracing off: _TracingNotClean"]


def test_t3b_an_instrumentor_that_adds_a_processor_turns_tracing_off_and_stops_export(
    _real_begin, monkeypatch, caplog
):
    from fi_instrumentation import otel as fi_otel

    held = []
    kept = []

    def adds(provider):
        # As an instrumentor would: a tracer kept from the provider, and an exporter of its own.
        kept.append(provider)
        held.append(provider.get_tracer("instrumentor"))
        provider.add_span_processor(SimpleSpanProcessor(fi_otel.HTTPSpanExporter()))

    monkeypatch.setattr(observability, "_instrument", adds)
    _real_begin.live_transport()
    run = _real_begin.begin()

    assert observability._tracer is None
    assert _warnings(caplog) == ["tracing off: _TracingNotClean"]
    span = held[0].start_span("tool")
    span.set_attribute("claude_agent.tool.input", URL)
    span.end()
    assert run.posts == []
    assert run.memory.get_finished_spans() == ()
    # On any fi version, nothing is left on the provider to export through.
    assert kept[0]._active_span_processor._span_processors == ()


def test_t3c_turning_tracing_off_shuts_down_every_processor_it_takes_off(
    _real_begin, monkeypatch
):
    # Taking a processor off the provider is what stops it exporting; shutting it down is what
    # stops its worker thread and closes its exporter, so a job that ran untraced leaves neither.
    closed = []

    class _Recording(SimpleSpanProcessor):
        def shutdown(self):
            closed.append(self)
            super().shutdown()

    added = _Recording(InMemorySpanExporter())
    kept = []

    def adds(provider):
        kept.append(provider)
        provider.add_span_processor(added)

    monkeypatch.setattr(observability, "_instrument", adds)
    _real_begin.begin()

    assert observability._tracer is None
    assert added in closed
    assert kept[0]._active_span_processor._span_processors == ()


def test_t3y_a_processor_added_after_begin_leaves_the_cleaner_in_place(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    run.provider.add_span_processor(SimpleSpanProcessor(InMemorySpanExporter()))
    with run.tracer.start_as_current_span("after"):
        pass

    assert [span.name for span in run.spans()] == ["after"]


def test_t4_a_list_valued_attribute_is_cleaned(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        span.set_attribute("tool.args", ("a", URL))

    assert run.one().attributes["tool.args"] == ("a", CLEAN)


def test_t5_events_exceptions_and_the_status_are_cleaned(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span(
        "tool", record_exception=False, set_status_on_exception=False
    ) as span:
        span.add_event("note", {"text": "fetched " + URL})
        span.add_event("saw " + URL)
        try:
            raise ValueError("bad " + URL)
        except ValueError as error:
            span.record_exception(error)
        span.set_status(Status(StatusCode.ERROR, "failed " + URL))

    out = run.one()
    note, named, exception = out.events
    assert note.attributes["text"] == "fetched " + CLEAN
    assert named.name == "saw " + CLEAN
    assert exception.attributes["exception.message"] == "bad " + CLEAN
    stacktrace = exception.attributes["exception.stacktrace"]
    assert "ValueError: bad " + CLEAN in stacktrace
    assert not _leaks(stacktrace)
    assert out.status.status_code is StatusCode.ERROR
    assert out.status.description == "failed " + CLEAN


def test_t6_the_span_name_is_cleaned(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("GET " + URL):
        pass

    assert run.one().name == "GET " + CLEAN


def test_t16_link_attributes_are_cleaned(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    link = Link(_context(), {"why": URL, "many": ("a", URL)})
    with run.tracer.start_as_current_span("tool", links=[link]):
        pass

    out = run.one().links[0]
    assert out.attributes["why"] == CLEAN
    assert out.attributes["many"] == ("a", CLEAN)


def test_t7_numbers_and_bools_pass_unchanged_with_no_key_to_mask_them(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("llm") as span:
        span.set_attribute("gen_ai.usage.input_tokens", 12)
        span.set_attribute("tool.is_error", True)

    out = run.one()
    assert out.attributes["gen_ai.usage.input_tokens"] == 12
    assert out.attributes["tool.is_error"] is True


def test_t8_dropped_counts_are_carried_over():
    limits = SpanLimits(
        max_span_attributes=1,
        max_events=1,
        max_links=1,
        max_event_attributes=1,
        max_link_attributes=1,
    )
    memory = InMemorySpanExporter()
    provider = TracerProvider(span_limits=limits)
    provider.add_span_processor(SimpleSpanProcessor(memory))
    # Each bounded list keeps its latest items.
    links = [Link(_context(), {"d": 1}), Link(_context(), {"a": 1, "b": 2, "c": 3})]
    with provider.get_tracer("test").start_as_current_span("span", links=links) as span:
        span.set_attributes({"x": 1, "y": 2, "z": 3})
        span.add_event("first")
        span.add_event("second")
        span.add_event("kept", {"a": 1, "b": 2})
    original = memory.get_finished_spans()[0]
    sink = InMemorySpanExporter()

    observability._CleaningExporter(sink).export([original])

    copy = sink.get_finished_spans()[0]
    assert (copy.dropped_attributes, copy.dropped_events, copy.dropped_links) == (
        2,
        2,
        1,
    )
    assert (
        copy.events[0].dropped_attributes == original.events[0].dropped_attributes == 1
    )
    assert copy.links[0].dropped_attributes == original.links[0].dropped_attributes == 2
    assert (copy.context, copy.parent) == (original.context, original.parent)
    assert copy.kind == original.kind
    assert (copy.start_time, copy.end_time) == (original.start_time, original.end_time)
    assert copy.instrumentation_scope == original.instrumentation_scope
    # A cleaned copy of the resource; with no secret in it, the same attributes.
    assert copy.resource.attributes == original.resource.attributes


EARLIER = "see https://a.example/p?q=1 then "
CUT_TAIL = "https://deploy:8374"


def _ending(length, tail=CUT_TAIL, after=""):
    """A value of length characters: a URL with a query, padding, then tail last."""
    end = tail + after
    value = EARLIER + "x" * (length - len(EARLIER) - len(end) - 1) + " " + end
    assert len(value) == length
    return value


@pytest.mark.parametrize("length", [5000, 2000, 1000, 500])
def test_t9_an_address_ending_a_value_cut_by_traceai_goes_whole(_real_begin, length):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        # traceai cuts at 5,000 as result[:4997] + "...".
        span.set_attribute(
            "tool.input", _ending(length, after="..." if length == 5000 else "")
        )

    value = run.one().attributes["tool.input"]
    assert "8374" not in value
    assert "q=1" not in value
    assert value.endswith(" [redacted]")


def test_t9_a_value_not_of_a_cut_length_keeps_its_port(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        span.set_attribute("tool.input", _ending(4999))

    value = run.one().attributes["tool.input"]
    assert "q=1" not in value
    assert value.endswith(" " + CUT_TAIL)


def test_t9c_a_cut_value_whose_last_address_is_followed_by_text_keeps_it(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        span.set_attribute("bash.command", _ending(500, after=" done"))

    assert run.one().attributes["bash.command"].endswith(" " + CUT_TAIL + " done")


def test_t10_an_empty_scheme_is_replaced_from_the_separator(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        span.set_attribute(
            "bash.command", _ending(500, tail="${DB_SCHEME}://admin:8374")
        )

    value = run.one().attributes["bash.command"]
    assert "8374" not in value
    assert value.endswith(" ${DB_SCHEME}[redacted]")


def test_t11_traceais_embedded_tool_result_cut_is_cleaned(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("conversation") as span:
        # _client_wrapper.py writes f"[Tool Result: {result[:100]}...]" mid-value.
        span.set_attribute("output.full", "[Tool Result: " + URL + "...]")
        span.set_attribute("output.cut", "[Tool Result: https://user:8374...]")

    out = run.one().attributes
    assert not _leaks(out["output.full"])
    assert "8374" not in out["output.cut"]
    assert "[redacted]" in out["output.cut"]


def test_t12_a_raise_while_cleaning_drops_the_batch_and_logs_only_the_class(
    _real_begin, monkeypatch, caplog
):
    from fi.alk.providers import agent

    run = _real_begin.begin()
    assert observability._tracer is not None
    span = _readable("tool", **{"tool.input": URL})

    def raises(value, key=""):
        raise ValueError("cannot parse " + URL)

    monkeypatch.setattr(agent, "_redact", raises)
    caplog.clear()

    assert run.cleaner.export([span]) is SpanExportResult.FAILURE
    assert run.spans() == ()
    for record in caplog.records:
        assert not _leaks(record.getMessage())
        assert not _leaks(str(record.exc_text or ""))
    assert _warnings(caplog) == ["trace spans dropped: ValueError"]


def test_t13_a_span_that_cannot_be_rebuilt_is_dropped_alone(
    _real_begin, monkeypatch, caplog
):
    from opentelemetry.sdk import trace as sdk_trace

    run = _real_begin.begin()
    assert observability._tracer is not None
    # Built before the patch, which would otherwise break ending them.
    spans = [_readable("boom", **{"tool.input": URL}), _readable("fine")]
    real = sdk_trace.ReadableSpan

    def picky(*args, **kwargs):
        if kwargs.get("name") == "boom":
            raise KeyError("planted")
        return real(*args, **kwargs)

    monkeypatch.setattr(sdk_trace, "ReadableSpan", picky)
    caplog.clear()

    run.cleaner.export(spans)

    assert [span.name for span in run.memory.get_finished_spans()] == ["fine"]
    assert _warnings(caplog) == ["trace spans dropped: KeyError"]


def test_t14_end_flushes_through_the_cleaner(_real_begin):
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("last") as span:
        span.set_attribute("tool.input", URL)

    observability.end()

    spans = run.memory.get_finished_spans()
    assert [span.name for span in spans] == ["last"]
    assert spans[0].attributes["tool.input"] == CLEAN


def test_t14b_the_cleaners_force_flush_reaches_the_inner_exporter():
    calls = []

    class Inner(InMemorySpanExporter):
        def force_flush(self, timeout_millis=30000):
            calls.append(timeout_millis)
            return "inner's answer"

    assert observability._CleaningExporter(Inner()).force_flush(7) == "inner's answer"
    assert calls == [7]


def test_t15_traceais_recorded_tool_input_is_cleaned(_real_begin):
    pytest.importorskip("traceai_claude_agent_sdk")
    from traceai_claude_agent_sdk import ClaudeAgentInstrumentor
    from traceai_claude_agent_sdk._hooks import (
        create_post_tool_use_hook,
        create_pre_tool_use_hook,
    )

    # A singleton: undone first, so begin()'s _instrument hands it this provider.
    instrumentor = ClaudeAgentInstrumentor()
    instrumentor.uninstrument()
    try:
        run = _real_begin.begin()
        assert observability._tracer is not None
        tracer = instrumentor._tracer
        pre = create_pre_tool_use_hook(tracer)
        post = create_post_tool_use_hook(tracer)
        asyncio.run(
            pre({"tool_name": "WebFetch", "tool_input": {"url": URL}}, "tool-1", None)
        )
        asyncio.run(
            post({"tool_name": "WebFetch", "tool_response": "ok"}, "tool-1", None)
        )

        (span,) = [span for span in run.spans() if span.name == "tool.WebFetch"]
        value = span.attributes["claude_agent.tool.input"]
        assert "https://h/x" in value
        assert not _leaks(value)
    finally:
        instrumentor.uninstrument()


# -- the copy's structure, the check's identity half, the inner exporter, the real exporter -----


def test_r1_the_copy_keeps_parent_kind_link_context_and_event_time():
    from opentelemetry.trace import SpanKind

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("root"):
        with tracer.start_as_current_span(
            "child", kind=SpanKind.CLIENT, links=[Link(_context(), {"a": 1})]
        ) as span:
            span.add_event("e", timestamp=123)
    original = [s for s in memory.get_finished_spans() if s.name == "child"][0]
    sink = InMemorySpanExporter()

    observability._CleaningExporter(sink).export([original])

    copy = sink.get_finished_spans()[0]
    # A lost parent would reach Observe as a root, flattening the stage -> call nesting.
    assert original.parent is not None
    assert copy.parent == original.parent
    assert copy.kind is SpanKind.CLIENT
    assert copy.links[0].context == original.links[0].context
    assert copy.events[0].timestamp == 123


def test_r2_a_lone_processor_that_is_not_the_cleaner_turns_tracing_off(
    _real_begin, monkeypatch, caplog
):
    def replaces(provider):
        provider._active_span_processor._span_processors = (
            SimpleSpanProcessor(InMemorySpanExporter()),
        )

    monkeypatch.setattr(observability, "_instrument", replaces)
    _real_begin.begin()

    assert observability._tracer is None
    assert _warnings(caplog) == ["tracing off: _TracingNotClean"]


def test_r3_two_spans_dropped_in_one_batch_log_one_line(
    _real_begin, monkeypatch, caplog
):
    from opentelemetry.sdk import trace as sdk_trace

    run = _real_begin.begin()
    assert observability._tracer is not None
    spans = [_readable("boom"), _readable("boom"), _readable("fine")]
    real = sdk_trace.ReadableSpan

    def picky(*args, **kwargs):
        if kwargs.get("name") == "boom":
            raise KeyError("planted")
        return real(*args, **kwargs)

    monkeypatch.setattr(sdk_trace, "ReadableSpan", picky)
    caplog.clear()
    run.cleaner.export(spans)

    assert _warnings(caplog) == ["trace spans dropped: KeyError"]


class _Inner(InMemorySpanExporter):
    def __init__(self, result=SpanExportResult.FAILURE, error=None):
        super().__init__()
        self.result, self.error, self.closed = result, error, False

    def export(self, spans):
        if self.error is not None:
            raise self.error
        return self.result

    def shutdown(self):
        self.closed = True


def test_r4_the_inner_exporters_raise_is_kept_and_logged_by_class(caplog):
    # A transport error's text can carry the request it failed on; the SDK would log it whole.
    caplog.set_level(logging.WARNING)
    inner = _Inner(error=ConnectionError("POST " + URL))

    result = observability._CleaningExporter(inner).export([_readable()])

    assert result is SpanExportResult.FAILURE
    assert _warnings(caplog) == ["trace spans dropped: ConnectionError"]
    assert not any(URL in record.getMessage() for record in caplog.records)


def test_r5_the_inner_exporters_result_and_shutdown_pass_through():
    inner = _Inner(result=SpanExportResult.FAILURE)
    cleaner = observability._CleaningExporter(inner)

    assert cleaner.export([_readable()]) is SpanExportResult.FAILURE
    cleaner.shutdown()
    assert inner.closed


def test_r6_the_swap_shuts_down_what_register_installed(_real_begin):
    from fi_instrumentation import register
    from fi_instrumentation.fi_types import ProjectType

    provider = register(
        project_name="p",
        project_type=ProjectType.OBSERVE,
        set_global_tracer_provider=False,
        verbose=False,
    )
    (old,) = provider._active_span_processor._span_processors
    closed = []
    old.shutdown = lambda *a, **k: closed.append(True)

    observability._swap(provider, SimpleSpanProcessor(InMemorySpanExporter()))

    assert closed == [True]
    provider.shutdown()


def _bodies(monkeypatch):
    bodies = []

    def post(self, url, *args, **kwargs):
        bodies.append((url, kwargs.get("data") or b""))
        response = requests.Response()
        response.status_code = 200
        return response

    monkeypatch.setattr(requests.Session, "post", post)
    return bodies


def test_t2b_the_real_exporter_posts_clean_spans_to_registers_endpoint(
    _real_begin, monkeypatch
):
    # Every other test swaps _exporter out; this is the one path that reaches Observe.
    monkeypatch.setattr(observability, "_exporter", _REAL_EXPORTER)
    _real_begin.live_transport()
    bodies = _bodies(monkeypatch)
    run = _real_begin.begin()
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        span.set_attribute("tool.input", URL)
    run.provider.force_flush()

    assert [url for url, _ in bodies] == ["http://127.0.0.1:9/tracer/v1/traces"]
    assert b"https://h/x" in bodies[0][1] and b"pw@" not in bodies[0][1]


def test_a_batch_whose_every_span_is_dropped_sends_nothing(_real_begin, monkeypatch):
    from fi_instrumentation import otel as fi_otel
    from opentelemetry.sdk import trace as sdk_trace

    _real_begin.live_transport()
    bodies = _bodies(monkeypatch)
    span = _readable("boom", **{"tool.input": URL})

    def refuses(*args, **kwargs):
        raise KeyError("planted")

    monkeypatch.setattr(sdk_trace, "ReadableSpan", refuses)

    result = observability._CleaningExporter(fi_otel.HTTPSpanExporter()).export([span])

    assert result is SpanExportResult.FAILURE
    assert bodies == []


# -- JSON values, declared secrets, the key rule, the resource ---------------------------------


def _export_one(span, secret_values=()):
    sink = InMemorySpanExporter()
    observability._CleaningExporter(sink, secret_values).export([span])
    return sink.get_finished_spans()[0]


@pytest.mark.parametrize(
    "value",
    [
        # traceai's own json.dumps of a WebFetch input, and the same compact.
        '{"url": "https://u:pw@docs.example.com/guide?section=auth#setup", '
        '"prompt": "Summarise the auth section"}',
        '{"url":"https://u:pw@h/a?b=1","next":"keep me"}',
        '[{"role": "user", "content": "fetch https://h/x?key=v please"}, {"role": "x"}]',
    ],
)
def test_a_json_value_is_cleaned_as_a_structure_and_stays_json(value):
    copy = _export_one(_readable(**{"input.value": value}))

    out = copy.attributes["input.value"]
    tree = json.loads(out)
    assert "pw@" not in out and "key=v" not in out and "b=1" not in out
    if isinstance(tree, dict) and "prompt" in tree:
        assert tree == {
            "url": "https://docs.example.com/guide",
            "prompt": "Summarise the auth section",
        }
    if isinstance(tree, dict) and "next" in tree:
        assert tree["next"] == "keep me"
    if isinstance(tree, list):
        assert tree[1] == {"role": "x"}


def test_a_json_string_inside_json_is_cleaned_as_a_structure_too():
    inner = json.dumps({"url": "https://h/a?key=v", "next": "keep me"})
    value = json.dumps({"role": "tool", "content": inner})

    out = _export_one(_readable(**{"input.value": value})).attributes["input.value"]

    tree = json.loads(out)
    assert json.loads(tree["content"]) == {"url": "https://h/a", "next": "keep me"}


def test_a_json_value_cut_by_traceai_is_cleaned_as_text():
    value = ('{"note": "' + "x" * 4900 + " https://deploy:8374")[:4997] + "..."
    out = _export_one(_readable(**{"tool.input": value})).attributes["tool.input"]
    assert "8374" not in out


def test_a_declared_secret_never_reaches_the_inner_exporter():
    secret = "sk-JOBSECRET-1234567890"
    span = _readable(
        **{
            "output.value": "OPENAI_API_KEY=" + secret,
            "tool.input": '{"k": "' + secret + '"}',
        }
    )

    copy = _export_one(span, (secret, "short"))

    assert secret not in copy.attributes["output.value"]
    assert copy.attributes["output.value"] == "OPENAI_API_KEY=***"
    assert secret not in copy.attributes["tool.input"]


def test_a_declared_secret_in_an_exception_is_replaced(_real_begin):
    secret = "sk-JOBSECRET-1234567890"
    run = _real_begin.begin(secret_values=(secret,))
    assert observability._tracer is not None
    with run.tracer.start_as_current_span("tool") as span:
        try:
            raise RuntimeError("login failed key=" + secret)
        except RuntimeError as error:
            span.record_exception(error)

    (exception,) = run.one().events
    assert secret not in exception.attributes["exception.message"]
    assert secret not in exception.attributes["exception.stacktrace"]


def test_a_string_whose_key_names_a_secret_is_masked_and_a_count_is_kept():
    span = _readable(
        **{
            "http.request.header.authorization": ("Bearer sk-abc-123456",),
            "api_key": "plain-text-value",
            "gen_ai.usage.input_tokens": 12,
            "input.value": '{"usage": {"input_tokens": 12}, "token": "abc123def"}',
        }
    )

    attributes = _export_one(span).attributes

    assert attributes["http.request.header.authorization"] == ("[redacted]",)
    assert attributes["api_key"] == "[redacted]"
    assert attributes["gen_ai.usage.input_tokens"] == 12
    assert json.loads(attributes["input.value"]) == {
        "usage": {"input_tokens": 12},
        "token": "[redacted]",
    }


def test_the_resource_is_cleaned_once_and_reused():
    from opentelemetry.sdk.resources import Resource

    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({"note": "postgres://u:pw@db/x?k=v"}))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    for name in ("one", "two"):
        with provider.get_tracer("test").start_as_current_span(name):
            pass
    sink = InMemorySpanExporter()

    observability._CleaningExporter(sink).export(memory.get_finished_spans())

    first, second = sink.get_finished_spans()
    assert first.resource.attributes["note"] == "postgres://db/x"
    assert first.resource is second.resource


# -- which declared values, the order, keys, depth ----------------------------------------------

# What the gateway sends on the simulator channel: credentials among platform configuration.
GATEWAY_CHANNEL = {
    "ALK_HARNESS_MODEL": "vertex_ai/claude-sonnet-4-5",
    "SIMULATOR_LLM_MODEL": "gemini-2.5-flash-lite",
    "FI_HARNESS_PROJECT": "hosted-harness-prod",
    "FI_BASE_URL": "https://api.futureagi.example",
    "GOOGLE_CLOUD_PROJECT": "platform-project-1",
    "AGENTCC_API_KEY": "agentcc-key-1234567890",
    "OPENAI_API_KEY": "sk-OPENAI-1234567890",
    "FI_SECRET_KEY": "fi-secret-1234567890",
    "LIVEKIT_API_SECRET": "livekit-secret-1234567890",
}
GATEWAY_CREDENTIALS = {
    "agentcc-key-1234567890",
    "sk-OPENAI-1234567890",
    "fi-secret-1234567890",
    "livekit-secret-1234567890",
}


def test_credential_values_keeps_the_aliases_that_name_a_credential():
    assert set(observability.credential_values(GATEWAY_CHANNEL)) == GATEWAY_CREDENTIALS
    assert observability.credential_values(
        {"A_TOKEN": "", "B_SECRET": "kept-value"}
    ) == ("kept-value",)


def _platform_span():
    """An LLM span on a resource that names the Observe project, carrying a real credential."""
    from opentelemetry.sdk.resources import Resource

    memory = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource({"project_name": "hosted-harness-prod"})
    )
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer("test").start_as_current_span(
        "llm",
        attributes={
            "gen_ai.request.model": "vertex_ai/claude-sonnet-4-5",
            "llm.model_name": "gemini-2.5-flash-lite",
            "input.value": "the key is sk-OPENAI-1234567890",
        },
    ):
        pass
    return memory.get_finished_spans()[0]


def _assert_platform_config_survives(secret_values):
    copy = _export_one(_platform_span(), secret_values)

    assert copy.attributes["gen_ai.request.model"] == "vertex_ai/claude-sonnet-4-5"
    assert copy.attributes["llm.model_name"] == "gemini-2.5-flash-lite"
    assert copy.resource.attributes["project_name"] == "hosted-harness-prod"
    assert copy.attributes["input.value"] == "the key is ***"


def test_the_gateways_default_values_leave_models_and_project_unchanged():
    _assert_platform_config_survives(observability.credential_values(GATEWAY_CHANNEL))


def test_declared_values_never_touch_the_resource():
    # Even a value that is the project's name: the platform routes the job's spans by it.
    span = _platform_span()

    copy = _export_one(span, ("hosted-harness-prod",))

    assert copy.resource.attributes["project_name"] == "hosted-harness-prod"


def test_a_declared_value_under_eight_characters_is_not_replaced():
    copy = _export_one(_readable(**{"output.value": "word abc1234 ok"}), ("abc1234",))

    assert copy.attributes["output.value"] == "word abc1234 ok"


def test_a_short_all_digit_value_is_replaced_only_as_a_whole_number():
    copy = _export_one(
        _readable(**{"output.value": "pin 4821, again 4821. not 14821 or 48210"}),
        ("4821",),
    )

    assert copy.attributes["output.value"] == "pin ***, again ***. not 14821 or 48210"


def test_a_declared_value_under_four_digits_is_not_replaced():
    copy = _export_one(_readable(**{"output.value": "code 482 ok"}), ("482",))

    assert copy.attributes["output.value"] == "code 482 ok"


def test_an_array_value_is_read_as_json():
    # As prose, the URL would take the closing quote and bracket with it and break the JSON.
    out = _export_one(_readable(**{"input.value": '["https://h/x?key=v", "keep"]'}))

    assert json.loads(out.attributes["input.value"]) == ["https://h/x", "keep"]


def test_a_declared_secret_does_not_disarm_the_cut_rule():
    secret = "sk-JOBSECRET-1234567890"
    value = _ending(2000)
    value = secret + value[len(secret) :]
    assert len(value) == 2000 and secret in value

    out = _export_one(_readable(**{"tool.input": value}), (secret,))

    assert out.attributes["tool.input"].endswith(" [redacted]")
    assert (
        "8374" not in out.attributes["tool.input"]
        and secret not in out.attributes["tool.input"]
    )


def test_a_cut_json_value_is_not_read_as_json_even_when_it_is_complete_json():
    # The guard is cut == value: a value of a cut length that ends in an address goes as text,
    # whatever else it parses as.
    tail = " https://deploy:8374"
    complete = '["' + "x" * (2000 - 4 - len(tail)) + tail + '"]'
    assert len(complete) == 2000 and json.loads(complete)

    out = _export_one(_readable(**{"tool.input": complete})).attributes["tool.input"]

    assert "8374" not in out
    with pytest.raises(ValueError):
        json.loads(out)


@pytest.mark.parametrize("secret", ['pa"ss\\word-12345', "pässword-1234567"])
def test_a_declared_secret_with_json_escapes_is_replaced_inside_json(secret):
    value = json.dumps({"note": "login with " + secret, "list": [secret]})
    assert secret not in value

    out = _export_one(_readable(**{"input.value": value}), (secret,)).attributes[
        "input.value"
    ]

    assert json.loads(out) == {"note": "login with ***", "list": ["***"]}


def test_a_declared_secret_in_a_json_key_is_replaced():
    secret = "sk-JOBVALUE-1234567890"
    value = json.dumps({secret: "ok"})

    out = _export_one(_readable(**{"input.value": value}), (secret,)).attributes[
        "input.value"
    ]

    assert json.loads(out) == {"***": "ok"}


def test_a_json_key_is_cleaned_as_a_string():
    value = json.dumps({"https://u:pw@h/x?key=v": "ok"})

    out = _export_one(_readable(**{"input.value": value})).attributes["input.value"]

    assert "pw" not in out and "key=v" not in out
    assert list(json.loads(out).values()) == ["ok"]


def test_two_keys_that_clean_to_the_same_text_both_keep_their_values():
    value = json.dumps({"https://a:1@h/x": "first", "https://b:2@h/x": "second"})

    out = json.loads(
        _export_one(_readable(**{"input.value": value})).attributes["input.value"]
    )

    assert sorted(out.values()) == ["first", "second"]
    assert len(out) == 2


def test_a_key_that_names_a_secret_still_masks_its_value_when_the_key_is_cleaned():
    value = json.dumps({"token": "abc123def", "n": 1})

    out = _export_one(_readable(**{"input.value": value})).attributes["input.value"]

    assert json.loads(out) == {"token": "[redacted]", "n": 1}


def test_json_nested_past_the_cap_is_cleaned_as_text_and_the_batch_goes_on():
    deep = "[" * 1500 + "]" * 1500
    spans = [
        _readable("deep", **{"input.value": deep, "tool.input": URL}),
        _readable("normal", **{"tool.input": URL}),
    ]
    sink = InMemorySpanExporter()

    result = observability._CleaningExporter(sink).export(spans)

    sent = {span.name: span for span in sink.get_finished_spans()}
    assert set(sent) == {"deep", "normal"}
    assert sent["normal"].attributes["tool.input"] == CLEAN
    # Sent as text, unchanged: there is nothing in it to clean.
    assert sent["deep"].attributes["input.value"] == deep
    assert not _leaks(sent["deep"].attributes["tool.input"])
    assert result is not SpanExportResult.FAILURE


def test_json_at_the_depth_cap_is_still_read_as_json_and_one_past_it_is_not():
    depth = observability._JSON_DEPTH
    inside = "[" * depth + '"https://h/x?key=v"' + "]" * depth
    past = "[" * (depth + 1) + '"https://h/x?key=v"' + "]" * (depth + 1)

    assert observability._as_json(inside) is not None
    assert observability._as_json(past) is None
    assert observability._as_json("[" * 100000 + "]" * 100000) is None


def test_a_span_that_cannot_be_gathered_is_dropped_alone(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    real = observability._gather

    def picky(span, flat, secrets):
        if span.name == "boom":
            raise KeyError("planted")
        return real(span, flat, secrets)

    monkeypatch.setattr(observability, "_gather", picky)
    sink = InMemorySpanExporter()

    observability._CleaningExporter(sink).export(
        [_readable("boom"), _readable("fine", **{"tool.input": URL})]
    )

    assert [span.name for span in sink.get_finished_spans()] == ["fine"]
    assert sink.get_finished_spans()[0].attributes["tool.input"] == CLEAN
    assert _warnings(caplog) == ["trace spans dropped: KeyError"]


def test_a_declared_secret_running_into_a_cut_address_goes_whole():
    # The cut is decided on the recorded length but made on the masked text, so it can't take the
    # secret's tail with the address and leave its head.
    secret = "A1B2C3_D4E5F6G7"
    tail = " key=" + secret + "https://deploy:8374"
    value = "x" * (2000 - len(tail)) + tail

    out = _export_one(_readable(**{"tool.input": value}), (secret,)).attributes[
        "tool.input"
    ]

    assert "A1B2C3" not in out and "8374" not in out
    assert out.endswith(" key=***[redacted]")


@pytest.mark.parametrize("secret", ["pässword-1234567", 'pa"ss\\word-12345'])
def test_a_secret_json_escapes_is_replaced_in_text_that_is_not_parsed(secret):
    node = {"pw": "x", "note": "login " + secret}
    for _ in range(65):
        node = [node]
    deep = json.dumps(node)
    short = json.dumps({"note": "login " + secret + " " + "z" * 1200})[:1000]

    for value in (deep, short):
        out = _export_one(_readable(**{"input.value": value}), (secret,)).attributes[
            "input.value"
        ]
        assert json.dumps(secret)[1:-1] not in out and secret not in out


def test_a_secret_in_json_text_inside_json_text_is_replaced():
    secret = "sk-NESTED-FAKE-1234567890"
    inner = json.dumps({"k": "token " + secret})
    value = json.dumps({"role": "tool", "content": json.dumps({"message": inner})})

    out = _export_one(_readable(**{"input.value": value}), (secret,)).attributes[
        "input.value"
    ]

    assert secret not in out
    level1 = json.loads(json.loads(out)["content"])
    assert json.loads(level1["message"]) == {"k": "token ***"}


def test_three_json_keys_that_clean_alike_keep_all_three_values():
    value = json.dumps(
        {"https://a:1@h/x": "1", "https://b:2@h/x": "2", "https://c:3@h/x": "3"}
    )

    out = json.loads(
        _export_one(_readable(**{"input.value": value})).attributes["input.value"]
    )

    assert sorted(out.values()) == ["1", "2", "3"]
