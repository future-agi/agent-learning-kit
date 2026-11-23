"""The harness's span shape: stages and scenarios must parent the calls made inside them.

And what reaches Observe: begin() installs a cleaning exporter as the provider's only processor,
so every string a span carries is cleaned of credentials first (TH-9504).
"""

from __future__ import annotations

import asyncio
import contextlib
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


def test_tracing_never_takes_over_the_harness_s_stop_signal(monkeypatch):
    """The tracing library installs its own SIGTERM handler that exits at once; the platform's Stop is a SIGTERM the
    harness must keep, or a stopped call seals nothing."""
    import signal

    monkeypatch.setenv("FI_API_KEY", "k")
    monkeypatch.setenv("FI_SECRET_KEY", "s")
    monkeypatch.setattr(observability, "_provider", None)
    monkeypatch.setattr(observability, "_tracer", None)
    def ours(signum, frame):
        return None

    previous = {one: signal.getsignal(one) for one in (signal.SIGTERM, signal.SIGINT)}
    signal.signal(signal.SIGTERM, ours)
    try:
        observability.begin("job-1", "run-1")
        assert observability._provider is not None
        assert signal.getsignal(signal.SIGTERM) is ours
        assert signal.getsignal(signal.SIGINT) is previous[signal.SIGINT]
    finally:
        for one, handler in previous.items():
            signal.signal(one, handler)
        with contextlib.suppress(Exception):
            observability._provider.shutdown()
        observability._session.close()


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


def test_declared_values_keeps_every_value_but_the_listed_settings():
    channel = {
        **GATEWAY_CHANNEL,
        "SOME_NEW_SETTING": "unlisted-value-1234",
        "EMPTY": "",
    }

    assert set(observability.declared_values(channel)) == GATEWAY_CREDENTIALS | {
        "unlisted-value-1234"
    }
    assert observability.declared_values({"A": "x-value-123"}, frozenset({"A"})) == ()


def test_the_non_secret_settings_are_simulator_aliases_and_none_names_a_credential():
    from fi.alk.harness.hosted_entrypoint import _SIMULATOR_SECRET_ALIASES

    assert observability.NON_SECRET_SETTINGS <= _SIMULATOR_SECRET_ALIASES
    assert not [
        name
        for name in observability.NON_SECRET_SETTINGS
        if observability._named_secret(name)
    ]


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
    _assert_platform_config_survives(observability.declared_values(GATEWAY_CHANNEL))


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


@pytest.mark.parametrize(
    "secret", ["pässword-1234567", 'pa"ss\\word-12345', 'pä"ss-wörd-123']
)
@pytest.mark.parametrize("ensure_ascii", [True, False])
def test_a_secret_json_escapes_is_replaced_in_text_that_is_not_parsed(
    secret, ensure_ascii
):
    node = {"pw": "x", "note": "login " + secret}
    for _ in range(65):
        node = [node]
    deep = json.dumps(node, ensure_ascii=ensure_ascii)
    short = json.dumps(
        {"note": "login " + secret + " " + "z" * 1200}, ensure_ascii=ensure_ascii
    )[:1000]

    for value in (deep, short):
        out = _export_one(_readable(**{"input.value": value}), (secret,)).attributes[
            "input.value"
        ]
        assert json.dumps(secret)[1:-1] not in out and secret not in out
        assert json.dumps(secret, ensure_ascii=False)[1:-1] not in out


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


def test_a_secret_escaped_twice_in_text_that_is_not_parsed_is_replaced():
    secret = 'line one\n-----KEY "fake" 1234567'
    once = json.dumps(secret)[1:-1]
    twice = json.dumps(once)[1:-1]
    assert twice != once
    value = "tool said " + twice + " and stopped"

    out = _export_one(_readable(**{"output.value": value}), (secret,)).attributes[
        "output.value"
    ]

    assert out == "tool said *** and stopped"


def test_a_declared_all_digit_value_written_as_a_json_number_is_replaced():
    value = json.dumps(
        {"guest_pin": 4821, "max_tokens": 4096, "stream": True, "nested": [4821, 7]}
    )

    out = _export_one(_readable(**{"input.value": value}), ("4821",)).attributes[
        "input.value"
    ]

    assert json.loads(out) == {
        "guest_pin": "***",
        "max_tokens": 4096,
        "stream": True,
        "nested": ["***", 7],
    }


def test_a_number_nobody_declared_is_kept_as_a_number():
    value = json.dumps({"max_tokens": 4096, "stream": True, "pin": 4821})

    out = _export_one(_readable(**{"input.value": value}), ()).attributes["input.value"]

    assert json.loads(out) == {"max_tokens": 4096, "stream": True, "pin": 4821}


@pytest.mark.parametrize(
    "spoken",
    [
        "4 8 2 1",
        "4-8-2-1",
        "4.8.2.1",
        "four eight two one",
        "Four-Eight-Two-One",
        "FOUR EIGHT TWO ONE",
    ],
)
def test_a_four_digit_value_spoken_or_spaced_out_is_replaced(spoken):
    out = _export_one(
        _readable(**{"output.value": f"the pin is {spoken}, thanks"}), ("4821",)
    ).attributes["output.value"]

    assert out == "the pin is ***, thanks"


@pytest.mark.parametrize(
    "text",
    [
        "code 14821 ok",
        "run 4 8 2 1 5 ok",
        "run 5 4 8 2 1 ok",
        "fourteen eight two one",
    ],
)
def test_a_longer_run_than_the_four_digit_value_is_kept(text):
    out = _export_one(_readable(**{"output.value": text}), ("4821",)).attributes[
        "output.value"
    ]

    assert out == text


def test_the_non_secret_settings_are_exactly_these_27_names():
    assert observability.NON_SECRET_SETTINGS == frozenset(
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
    assert len(observability.NON_SECRET_SETTINGS) == 27


def test_a_lone_surrogate_in_plain_text_does_not_cost_the_attribute_its_export():
    encoders = pytest.importorskip(
        "opentelemetry.exporter.otlp.proto.common.trace_encoder"
    )

    copy = _export_one(_readable(**{"input.value": "a \ud83d b", "other": "kept"}), ())

    assert copy.attributes["input.value"] == "a \ufffd b"
    encoded = encoders.encode_spans([copy]).SerializeToString()
    assert b"a \xef\xbf\xbd b" in encoded


# -- A declared secret that traceai's cut runs through ----------------------------------------

CUT_SECRET = "sk-live-A1b2C3d4E5f6G7h8J9k0"


def _cut_through(length, secret=CUT_SECRET):
    """What traceai records when it cuts a value of `length` characters one character short of
    the end of the secret: a short .env-like head, then all of the secret but its last character."""
    # An ordinary name before the secret: under a credential name the shared cleaner masks the
    # whole value itself, and the cut rule this checks would never be reached.
    lead = "HOME=/home/app PATH=/usr/bin:/bin LANG=C.UTF-8 TERM=xterm OPENAI_NOTE="
    lead = lead + "x" * (length - len(secret) + 1 - len(lead))
    return lead, (lead + secret)[:length]


# A bare end of text counts only at a length traceai cuts to, and its cut at 100 always has "...".
@pytest.mark.parametrize(
    "length, ending",
    [
        (length, ending)
        for length in (100, 500, 1000, 2000, 5000)
        for ending in ("...", "...]", "")
        if not (length == 100 and ending == "")
    ],
)
def test_a_declared_secret_cut_through_by_traceai_is_replaced(length, ending):
    lead, cut = _cut_through(length)
    assert cut.endswith(CUT_SECRET[:-1])

    out = _export_one(
        _readable(**{"output.value": cut + ending}), (CUT_SECRET,)
    ).attributes["output.value"]

    assert out == lead + "***" + ending


def test_a_declared_secrets_first_characters_in_honest_text_are_kept():
    # A cut is told by what follows the prefix: "..." or the end of the string. The same
    # characters followed by anything else are somebody's text, and the whole-value rule
    # already covers the secret itself.
    text = "keys look like sk-live-A1b2 and sk-live-X; sk-live- alone is a prefix. sk-live-A1b2..x"

    out = _export_one(_readable(**{"output.value": text}), (CUT_SECRET,)).attributes[
        "output.value"
    ]

    assert out == text
    whole = _export_one(
        _readable(**{"output.value": f"k={CUT_SECRET} then {CUT_SECRET[:12]}..."}),
        (CUT_SECRET,),
    ).attributes["output.value"]
    assert whole == "k=*** then ***..."


def test_a_cut_prefix_that_reaches_a_dot_of_the_secret_is_still_found():
    secret = "abcdefgh.ijklmnop"

    out = _export_one(
        _readable(**{"output.value": "k=abcdefgh... and k=abcdefgh.ij..."}), (secret,)
    ).attributes["output.value"]

    assert out == "k=***... and k=***..."


# -- What the spoken-PIN patterns cost on text that has no PIN ---------------------------------


# -- The same cut, in the JSON-escaped form of a secret ----------------------------------------

SECRET_WITH_QUOTE = 'Zq7"Hk2mVx9pLr4\\Tn8!wBc'
PEM_LINES = [
    f"{chr(65 + n)}b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQ{n}" for n in range(10)
]
PEM = (
    "-----BEGIN PRIVATE KEY-----\n"
    + "\n".join(PEM_LINES)
    + "\n-----END PRIVATE KEY-----"
)
SECRET_NON_ASCII = "Geheimnis-Schl\u00fcssel-\u00e49Kd2xQ"


def _dumped_cut_inside(secret, ensure_ascii, keep, mid):
    """json.dumps of {"pad": ..., "k": secret}, padded so a cut at `keep` characters lands `mid`
    characters into the secret's escaped form. Returns the dumped text and that form."""
    escaped = json.dumps(secret, ensure_ascii=ensure_ascii)[1:-1]
    head = json.dumps({"pad": "", "k": secret}, ensure_ascii=ensure_ascii)
    pad = keep - mid - head.index(escaped)
    assert pad > 0
    text = json.dumps({"pad": "x" * pad, "k": secret}, ensure_ascii=ensure_ascii)
    assert text[keep - mid : keep] == escaped[:mid]
    return text, escaped


def test_a_secret_with_a_quote_cut_through_in_traceais_json_dump_is_replaced():
    # traceai: json.dumps(tool_response, ensure_ascii=False)[:4997] + "...".
    text, escaped = _dumped_cut_inside(SECRET_WITH_QUOTE, False, 4997, 15)
    cut = text[:4997] + "..."
    assert len(cut) == 5000

    out = _export_one(
        _readable(**{"output.value": cut}), (SECRET_WITH_QUOTE,)
    ).attributes["output.value"]

    assert escaped[:8] not in out
    assert out.endswith('"k": "***...')


def test_a_private_key_cut_through_in_traceais_json_dump_is_replaced():
    text, escaped = _dumped_cut_inside(PEM, False, 4997, 400)
    out = _export_one(
        _readable(**{"output.value": text[:4997] + "..."}), (PEM,)
    ).attributes["output.value"]

    assert PEM_LINES[3] not in out and PEM_LINES[5] not in out
    assert out.endswith('"k": "***...')


def test_a_non_ascii_secret_cut_through_in_a_json_dumped_system_prompt_is_replaced():
    # traceai: json.dumps(system_prompt)[:1000], which escapes non-ASCII.
    text, escaped = _dumped_cut_inside(
        SECRET_NON_ASCII, True, 1000, len(SECRET_NON_ASCII) + 5
    )
    out = _export_one(
        _readable(**{"input.value": text[:1000]}), (SECRET_NON_ASCII,)
    ).attributes["input.value"]

    assert escaped[:10] not in out
    assert out.endswith('"k": "***')


def test_a_cut_in_the_twice_escaped_form_is_replaced():
    once = json.dumps(SECRET_WITH_QUOTE)[1:-1]
    twice = json.dumps(once)[1:-1]
    text = "y" * 50 + twice[:-4] + "..."

    out = _export_one(
        _readable(**{"output.value": text}), (SECRET_WITH_QUOTE,)
    ).attributes["output.value"]

    assert out == "y" * 50 + "***..."


# -- Where two declared values overlap, and honest text ----------------------------------------


def test_a_value_inside_a_longer_one_does_not_break_the_longer_ones_cut():
    inner = "C3d4E5f6G7"
    text = f"KEY={CUT_SECRET[:24]}... and {CUT_SECRET[:20]}..."

    out = _export_one(
        _readable(**{"output.value": text}), (inner, CUT_SECRET)
    ).attributes["output.value"]

    assert out == "KEY=***... and ***..."


def test_a_value_holding_three_dots_is_replaced_whole_not_cut_at_them():
    value = "token-abc...xyz123456"

    out = _export_one(
        _readable(**{"output.value": f"k={value} and k={value[:14]}..."}), (value,)
    ).attributes["output.value"]

    assert out == "k=*** and k=***..."


def test_a_head_that_is_not_a_cut_does_not_hide_a_cut_later_in_the_text():
    text = f"OLD=sk-live-ZZZZ9999 NEW={CUT_SECRET[:20]}..."

    out = _export_one(_readable(**{"output.value": text}), (CUT_SECRET,)).attributes[
        "output.value"
    ]

    assert out == "OLD=sk-live-ZZZZ9999 NEW=***..."


def test_a_prefix_ending_a_json_string_or_an_attribute_is_not_a_cut():
    dsn = "prod-database-password-Zq7Hk2"
    tree = json.dumps({"engine": "prod-database", "path": "prod-database-pass"})

    span = _readable(**{"input.value": tree, "db.system": "prod-database"})
    copy = _export_one(span, (dsn,))

    assert json.loads(copy.attributes["input.value"]) == json.loads(tree)
    assert copy.attributes["db.system"] == "prod-database"


def test_a_prefix_ending_text_that_is_not_a_cut_length_is_kept():
    dsn = "prod-database-password-Zq7Hk2"

    out = _export_one(
        _readable(**{"output.value": "Connected to prod-database-pass"}), (dsn,)
    ).attributes["output.value"]

    assert out == "Connected to prod-database-pass"


# -- What the cut rule costs on text built to be slow ------------------------------------------


def test_the_cut_rule_costs_a_small_multiple_of_a_plain_replace_on_repeating_text():
    # A value that repeats itself, and 30 keys sharing a head, on text dense with that head:
    # character-by-character extension at every hit once took seconds per 1,280 strings.
    # Measured against str.replace on the same strings, so a slow runner slows both alike.
    repeating = "0" * 64
    shared = tuple(f"sk-ant-api03-{n:02d}" + "Ab9" * 12 for n in range(30))
    # Each family has its own multiplier and floor: the third runs 30 values against one
    # replace, and needs room for that. A bound shared by all three let the first two pass
    # without the jump to the next "...".
    families = [
        ((repeating,), [(("0" * 63 + "x") * 80)[:4997] + "..."] * 1280, 10, 0.3),
        ((repeating,), ["... " + ("0" * 63 + "x") * 80] * 1280, 10, 0.3),
        (shared, [("sk-ant-api03-00 " * 320)[:4997] + "..."] * 1280, 60, 0.6),
    ]

    for values, texts, times, floor in families:
        patterns = observability._secret_patterns(values)
        plain = _best_of(2, lambda: [t.replace(values[0], "***") for t in texts])
        ours = _best_of(2, lambda: [observability._mask(t, patterns) for t in texts])
        assert ours < times * plain + floor, (values[0][:16], plain, ours)


class _CountingText(str):
    starts = 0

    def startswith(self, *args):
        type(self).starts += 1
        return super().startswith(*args)


def test_a_head_hit_that_leaves_the_value_before_the_first_dots_costs_one_comparison():
    # Dense "..." makes every head hit a candidate for a cut. The text here follows the value
    # for 60 characters, then leaves it, so none is a cut: one comparison rejects each, where a
    # binary search over the value's length took about six. Counted, because no timing bound
    # separates the two with margin on a shared runner.
    blocks = 80
    text = _CountingText(("0" * 60 + "x...") * blocks)
    hits = blocks * (60 - 7)
    _CountingText.starts = 0

    out = observability._CutSecret("0" * 64).sub("***", text, True, False)

    assert out == text
    assert _CountingText.starts <= 2 * hits, (_CountingText.starts, hits)


def _best_of(runs, work):
    import time

    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        work()
        best = min(best, time.perf_counter() - started)
    return best


def test_declared_pins_cost_a_fraction_of_a_lookbehind_first_pattern_on_prose():
    # 1,280 strings of 5,000 characters with no PIN in them. A pattern that starts with a
    # lookbehind is tried at every position of every string; one that starts with the PIN's
    # first digit lets re skip ahead to it, and the plain short-number pattern runs only on text
    # that holds a declared value. Measured against the lookbehind-first form on the same
    # strings, so a slow runner slows both sides alike: the bound is one fifth of its time.
    import re

    words = "the agent opened the report and read the totals back to the caller slowly ".split()
    prose = " ".join(words[i % len(words)] for i in range(1000))[:5000]
    texts = [prose[i % 7 :] + " " * (i % 7) for i in range(1280)]
    pins = ("4821", "7305")

    def lookbehind_first(pin):
        separated = r"[ .\-]".join(pin)
        return re.compile(
            rf"(?<![0-9])(?<![0-9][ .\-]){separated}(?![0-9])(?![ .\-][0-9])"
        )

    old = [lookbehind_first(pin) for pin in pins]
    old.append(re.compile(r"(?<![0-9])(?:4821|7305)(?![0-9])"))
    # What the exporter runs for the digits: the plain and the spaced patterns. The spoken-words
    # patterns are left out; they start with a word boundary and are not what this pins.
    ours = [
        pattern
        for pattern in observability._secret_patterns(pins)
        if not (isinstance(pattern, re.Pattern) and pattern.flags & re.IGNORECASE)
        and not isinstance(pattern, (observability._CutTail, observability._CutSecret))
    ]
    assert len(ours) == 3

    def mask_with(patterns):
        return lambda: [
            [pattern.sub("***", text) for pattern in patterns] for text in texts
        ]

    before = _best_of(2, mask_with(old))
    after = _best_of(2, mask_with(ours))

    assert after * 5 < before, (before, after)


# -- Where an attribute's own length limit cuts, repr and deeper escapes -----------------------

LIMIT_VARIABLES = (
    "OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT",
    "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT",
)


def _cut_by_limit(length):
    # What the SDK leaves when a limit cuts an attribute: no "...", the secret's head last.
    lead = "x" * (length - 20 - len("OPENAI_API_KEY="))
    return lead + "OPENAI_API_KEY=" + CUT_SECRET[:20]


def _with_limits(monkeypatch, **limits):
    for name in LIMIT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    for name, value in limits.items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("variable", LIMIT_VARIABLES)
def test_a_secret_cut_by_the_environments_attribute_limit_is_replaced(
    monkeypatch, variable
):
    _with_limits(monkeypatch, **{variable: "4000"})
    cut = _cut_by_limit(4000)
    assert len(cut) == 4000 and not cut.endswith("...")
    sink = InMemorySpanExporter()

    observability._CleaningExporter(sink, (CUT_SECRET,)).export(
        [_readable(**{"output.value": cut})]
    )

    out = sink.get_finished_spans()[0].attributes["output.value"]
    assert CUT_SECRET[:8] not in out and out.endswith("OPENAI_API_KEY=***")


def test_text_of_the_limits_length_is_kept_when_no_limit_is_set(monkeypatch):
    _with_limits(monkeypatch)
    # An ordinary name before the head: under a credential name the shared cleaner masks it.
    text = _cut_by_limit(4000).replace("OPENAI_API_KEY=", "OPENAI_NOTE====")

    out = _export_one(_readable(**{"output.value": text}), (CUT_SECRET,)).attributes[
        "output.value"
    ]

    assert out == text


def test_the_span_specific_limit_wins_and_a_bad_value_is_ignored(monkeypatch):
    _with_limits(
        monkeypatch,
        OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT="3000",
        OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT="4000",
    )
    # A span's own attributes are cut at the span-specific limit, its events and links at the
    # general one, so both are cut lengths.
    cuts = observability._CleaningExporter(InMemorySpanExporter())._secrets.cuts
    assert {3000, 4000} <= set(cuts)
    for bad in ("", "abc", "0", "-5", "12.5"):
        _with_limits(
            monkeypatch,
            OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT=bad,
            OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT="4000",
        )
        exporter = observability._CleaningExporter(InMemorySpanExporter())
        assert 4000 in exporter._secrets.cuts, bad
        assert 3000 not in exporter._secrets.cuts, bad
    _with_limits(monkeypatch, OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT="abc")
    exporter = observability._CleaningExporter(InMemorySpanExporter())
    assert tuple(exporter._secrets.cuts) == observability._CUTS


def test_a_secret_with_both_quotes_printed_by_repr_is_replaced():
    value = "Zq7'Hk2mVx9\"pLr4TsW8nYb3c"
    status = repr({"is_error": True, "content": f"DB_PASSWORD={value} rejected"})
    listed = f"[Tool Result: {[{'type': 'text', 'text': 'k=' + value}]!r}...]"

    for text in (status, listed):
        out = _export_one(_readable(**{"output.value": text}), (value,)).attributes[
            "output.value"
        ]
        assert "Zq7" not in out and "***" in out


def test_a_secret_escaped_three_times_and_cut_is_replaced():
    value = 'Zq7"Hk2mVx9\\pLr4TsW8nYb3c'
    thrice = value
    for _ in range(3):
        thrice = json.dumps(thrice)[1:-1]
    text = ("y" * (4997 - 20) + thrice)[:4997] + "..."
    assert len(text) == 5000 and thrice[:12] in text

    out = _export_one(_readable(**{"output.value": text}), (value,)).attributes[
        "output.value"
    ]

    assert thrice[:10] not in out and out.endswith("***...")
    whole = _export_one(
        _readable(**{"output.value": f"k={thrice} done"}), (value,)
    ).attributes["output.value"]
    assert whole == "k=*** done"


def test_a_value_with_nothing_to_escape_has_exactly_one_form():
    forms = [
        p
        for p in observability._secret_patterns(("plainvalue12345",))
        if isinstance(p, str)
    ]

    assert forms == ["plainvalue12345"]


def test_a_secret_cut_in_an_exception_message_by_the_general_limit_is_replaced(
    monkeypatch,
):
    _with_limits(
        monkeypatch,
        OTEL_SPAN_ATTRIBUTE_VALUE_LENGTH_LIMIT="3000",
        OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT="4000",
    )
    cut = _cut_by_limit(4000)
    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer("test").start_as_current_span("tool") as span:
        span.add_event("exception", {"exception.message": cut})
    sink = InMemorySpanExporter()

    observability._CleaningExporter(sink, (CUT_SECRET,)).export(
        memory.get_finished_spans()
    )

    (event,) = sink.get_finished_spans()[0].events
    message = event.attributes["exception.message"]
    assert CUT_SECRET[:8] not in message and message.endswith("OPENAI_API_KEY=***")


@pytest.mark.parametrize("declared", [(), ("8462",)])
def test_an_address_ending_a_value_cut_by_the_environments_limit_goes_whole(
    monkeypatch, declared
):
    _with_limits(monkeypatch, OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT="4000")
    tail = "postgresql://admin:8462"
    text = "x" * (4000 - len(tail)) + tail

    out = _export_one(_readable(**{"output.value": text}), declared).attributes[
        "output.value"
    ]

    assert "8462" not in out and out.endswith("[redacted]")


@pytest.mark.parametrize("limit", [4998, 4999])
def test_a_limit_that_cuts_inside_traceais_dots_still_masks_the_head(
    monkeypatch, limit
):
    _with_limits(monkeypatch, OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT=str(limit))
    full = "x" * (5000 - 20 - 3 - 15) + "OPENAI_API_KEY=" + CUT_SECRET[:20] + "..."
    assert len(full) == 5000
    cut = full[:limit]
    assert cut.endswith(CUT_SECRET[:20] + "." * (limit - 4997))

    out = _export_one(_readable(**{"output.value": cut}), (CUT_SECRET,)).attributes[
        "output.value"
    ]

    assert CUT_SECRET[:8] not in out
    assert out.endswith("OPENAI_API_KEY=***" + "." * (limit - 4997))


# -- A short head at a place traceai cut, and PINs as people say them ---------------------------


def _cut_head(head, length=5000, ending="..."):
    """Text of `length` characters that traceai cut: the head of a secret, then the ending."""
    return (
        "x" * (length - len(ending) - len(head) - len("KEY=")) + "KEY=" + head + ending
    )


@pytest.mark.parametrize(
    "value, head",
    [
        ("hunter12", "hunter1"),
        ("Zq7Hk", "Zq7"),
        ("Zq7Hk", "Zq7Hk"),
        ("hunter12", "hunt"),
    ],
)
def test_a_short_head_of_a_declared_value_at_a_traceai_cut_is_replaced(value, head):
    for length, ending in ((5000, "..."), (503, "..."), (500, "")):
        out = _export_one(
            _readable(**{"output.value": _cut_head(head, length, ending)}), (value,)
        ).attributes["output.value"]

        assert out.endswith("KEY=***" + ending), (length, out[-30:])


def test_a_short_head_at_the_environments_limit_is_replaced(monkeypatch):
    _with_limits(monkeypatch, OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT="4000")

    out = _export_one(
        _readable(**{"output.value": _cut_head("Zq7", 4000, "")}), ("Zq7Hk",)
    ).attributes["output.value"]

    assert out.endswith("KEY=***")


@pytest.mark.parametrize(
    "text",
    [
        "NOTE=hunter1... then more",
        "the key Zq7...",
        "x" * 4980 + "Zq7 and more...",
        "x" * 4990 + "Zq7Hk is it",
        "x" * 4500 + "KEY=Zq7...",
    ],
)
def test_a_short_head_that_is_not_at_a_cut_is_kept(text):
    out = _export_one(_readable(**{"output.value": text}), ("hunter12", "Zq7Hk"))

    assert out.attributes["output.value"] == text


@pytest.mark.parametrize(
    "spoken",
    [
        "4, 8, 2, 1",
        "4,8,2,1",
        "four, eight, two, one",
        "Four , Eight , Two , One",
        "4 - 8 - 2 - 1",
        "4  8  2  1",
        "4-8-2-1",
        "4.8.2.1",
    ],
)
def test_a_four_digit_value_as_people_write_it_out_is_replaced(spoken):
    out = _export_one(
        _readable(**{"output.value": f"the pin is {spoken}, thanks"}), ("4821",)
    ).attributes["output.value"]

    assert out == "the pin is ***, thanks"


@pytest.mark.parametrize(
    "text",
    [
        "run 4, 8, 2, 1, 5 ok",
        "run 5, 4, 8, 2, 1 ok",
        "run 48 215 ok",
        "run 548 21 ok",
        "run 5 - 4 - 8 - 2 - 1 ok",
    ],
)
def test_a_longer_run_than_the_pin_in_the_wider_forms_is_kept(text):
    out = _export_one(_readable(**{"output.value": text}), ("4821",)).attributes[
        "output.value"
    ]

    assert out == text


@pytest.mark.parametrize(
    "head, kept",
    [
        ("h", True),
        ("hu", True),
        ("hun", True),
        ("hunt", False),
    ],
)
def test_a_head_of_a_long_value_at_a_cut_is_masked_from_four_characters(head, kept):
    text = _cut_head(head)

    out = _export_one(_readable(**{"output.value": text}), ("hunter12",)).attributes[
        "output.value"
    ]

    assert (out == text) is kept


def test_a_three_character_head_of_a_long_key_at_a_cut_is_kept():
    text = _cut_head(CUT_SECRET[:3])

    out = _export_one(_readable(**{"output.value": text}), (CUT_SECRET,)).attributes[
        "output.value"
    ]

    assert out == text


@pytest.mark.parametrize("head, kept", [("Z", True), ("Zq", True), ("Zq7", False)])
def test_a_head_of_a_five_character_value_needs_half_of_it(head, kept):
    text = _cut_head(head)

    out = _export_one(_readable(**{"output.value": text}), ("Zq7Hk",)).attributes[
        "output.value"
    ]

    assert (out == text) is kept


@pytest.mark.parametrize(
    "declared, text",
    [
        ("4821", "balance is $48.21 today"),
        ("4821", "used 4,821 tokens"),
        ("4821", "used 48 21 times"),
        ("1500", "costs $15.00"),
        ("1500", "1,500 users"),
        ("8080", "80.80%"),
    ],
)
def test_a_price_or_a_count_with_the_pins_digits_is_kept(declared, text):
    out = _export_one(_readable(**{"output.value": text}), (declared,)).attributes[
        "output.value"
    ]

    assert out == text
