"""Every tool-call record names its source, and naming the sources changes no call list (tool_evidence.py)."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import call_runner, tool_evidence
from fi.alk.harness.chat_call_runner import _HostedChatTarget, _record_completed_tool_call
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState
from fi.alk.harness.tool_evidence import AGENT, SPAN, TRACER, WORLD, seen, tagged
from fi.alk.harness.world.runtime import Call, trace_row
from fi.simulate.agent.wrappers.http import HTTPAgentWrapper, _decode_json_or_sse


def _call(name="look_up_order", source="", arguments=None, result=None, *, at=0.0, **fields):
    return Call(
        name=name,
        arguments={"order_id": "A1001"} if arguments is None else arguments,
        result=result,
        at=at,
        source=source,
        **fields,
    )


def _runtime(tmp_path: Path, lines: list[dict]) -> EnvironmentRuntime:
    trace = tmp_path / "calls.jsonl"
    trace.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    return EnvironmentRuntime(
        runtime_id="rt-1",
        world_index=0,
        bundle_digest="sha256:" + "0" * 64,
        state=RuntimeState.READY,
        endpoints={},
        metadata={"tool_trace_path": str(trace)},
    )


class _World:
    def __init__(self) -> None:
        self.calls: list[Call] = []


_NAMED = {"source", "seen_by", "trace_mark"}


def _unnamed(calls) -> list[dict]:
    return [{key: value for key, value in dataclasses.asdict(call).items() if key not in _NAMED} for call in calls]


# -- the names ------------------------------------------------------------------------------------


def test_the_order_is_the_decided_one() -> None:
    assert tool_evidence.ORDER == (WORLD, SPAN, TRACER, AGENT)


def test_a_record_keeps_the_source_it_already_names() -> None:
    told, ran = _call(source=AGENT), _call()

    tagged([told, ran], WORLD)

    assert (told.source, ran.source) == (AGENT, WORLD)


def test_the_sources_that_saw_a_call_are_listed_once_in_order_and_never_its_own() -> None:
    call = _call(source=AGENT)

    for source in (TRACER, WORLD, AGENT, TRACER, SPAN):
        seen(call, source)

    assert call.seen_by == [WORLD, SPAN, TRACER]
    assert trace_row(call)["source"] == AGENT
    assert trace_row(call)["seen_by"] == [WORLD, SPAN, TRACER]


def test_two_records_that_differ_only_in_their_source_names_are_the_same_call() -> None:
    one = _call(result="shipped", at=1.0, source=WORLD)
    other = _call(result="shipped", at=2.0, source=TRACER, seen_by=[SPAN], trace_mark="crewai")

    assert dataclasses.replace(one, at=2.0) == other
    assert call_runner._digest(one) == call_runner._digest(other)
    assert set(tool_evidence.FIELDS) == {field.name for field in dataclasses.fields(Call) if not field.compare}


def test_the_trace_file_names_its_lines_sources_and_keeps_each_lines_mark(tmp_path) -> None:
    runtime = _runtime(
        tmp_path,
        [
            {"name": "look_up_order", "arguments": {"order_id": "A1001"}, "output": "shipped", "at": 1.0},
            {"name": "start_return", "arguments": {}, "output": "ok", "at": 2.0, "source": "crewai"},
            {"name": "return_policy", "arguments": "{}", "output": "30 days", "at": 3.0, "source": "otel",
             "event_id": "e3"},
        ],
    )

    calls = call_runner._with_span_calls([], runtime)

    assert [(call.name, call.source, call.trace_mark) for call in calls] == [
        ("look_up_order", TRACER, ""),
        ("start_return", TRACER, "crewai"),
        ("return_policy", SPAN, ""),
    ]


def test_the_agents_own_account_is_named_where_it_is_recorded() -> None:
    world = _World()

    _record_completed_tool_call(
        world, name="look_up_order", arguments={"order_id": "A1001"}, response={"result": "shipped"}
    )

    assert world.calls[0].source == AGENT


# -- a span never takes a record's graded facts ---------------------------------------------------
#
# A span paired with a trace line lends its agent, hand-off, times and ids, and is listed as having seen the call.
# The shapes are the ones found on 23 Nov, where a span that recorded less would otherwise replace the line's record.

_FRAMEWORK_WRAPPER = {"output": "content='Booked table 7' name='book' tool_call_id='call_1'"}
_SPAN_ROWS = {
    "the span kept fewer arguments": (
        dict(arguments={"date": "fri", "party": 2}, result="ok"),
        dict(arguments={"date": "fri"}, result="ok"),
    ),
    "the span kept no result": (
        dict(arguments={"date": "fri"}, result="booked #7"),
        dict(arguments={"date": "fri"}, result=None),
    ),
    "the span typed an argument differently": (
        dict(arguments={"party": "2"}, result="ok"),
        dict(arguments={"party": 2}, result="ok"),
    ),
    "the span kept only the framework's wrapper": (
        dict(arguments={"date": "fri"}, result={"table": 7}),
        dict(arguments={"date": "fri"}, result=_FRAMEWORK_WRAPPER, output_omitted=True),
    ),
}


@pytest.mark.parametrize("row", list(_SPAN_ROWS), ids=lambda row: row.replace(" ", "-"))
def test_a_paired_span_lends_only_what_it_measured(row) -> None:
    line, span = _SPAN_ROWS[row]
    traced = _call("book", TRACER, at=10.0, **line)
    spanned = _call("book", SPAN, at=9.5, ended_at=10.5, worker_label="Front desk", **span)

    (call,) = call_runner._merge_span_calls([traced], [spanned])

    assert (call.source, call.seen_by) == (TRACER, [SPAN])
    assert (call.arguments, call.result, call.output_omitted) == (line["arguments"], line["result"], False)
    assert (call.at, call.ended_at, call.worker_label) == (9.5, 10.5, "Front desk")


# -- a chat trial reads its calls as before sources were named ------------------------------------
#
# The same inputs through the rule as it was (the calls the trial recorded, else the trace file's lines; spans joined
# last with the span pairing as it was) and as it is now, compared field for field but for the three new fields.


def _lend_span_as_it_was(span: Call, other: Call) -> None:
    if not (other.worker_id or other.worker_label):
        other.worker_id, other.worker_label = span.worker_id, span.worker_label
    other.delegated_to = other.delegated_to or span.delegated_to
    other.transferred = other.transferred or span.transferred
    other.ended_at = other.ended_at or span.ended_at
    if not other.runtime_ids:
        other.runtime_ids = span.runtime_ids
        other.parent_span_id, other.relationship = span.parent_span_id, span.relationship
    other.at = span.at or other.at


_LOST = [{"name": "look_up_order", "arguments": {"order_id": "A1001"}, "actor_id": "agent",
          "error": "response_lost", "outcome": "committed"}]


def _digest_as_it_was(call: Call) -> str:
    # The pairing's digest before a call named its sources: everything but its times.
    fields = {key: value for key, value in vars(call).items() if key not in ("at", "ended_at", *_NAMED)}
    return json.dumps(fields, sort_keys=True, default=repr)


def _chat_readings(tmp_path, monkeypatch, observed, lines, log=None):
    runtime = _runtime(tmp_path, lines)
    lend_now, digest_now = call_runner._lend_span, call_runner._digest

    def as_it_was(recorded):
        monkeypatch.setattr(call_runner, "_lend_span", _lend_span_as_it_was)
        monkeypatch.setattr(call_runner, "_digest", _digest_as_it_was)
        try:
            written, from_spans = call_runner._read_file_tool_calls(runtime)
            return call_runner._merge_span_calls(list(recorded) or written, from_spans)
        finally:
            monkeypatch.setattr(call_runner, "_lend_span", lend_now)
            monkeypatch.setattr(call_runner, "_digest", digest_now)

    before = as_it_was(copy.deepcopy(observed))
    now = call_runner._with_span_calls(copy.deepcopy(observed), runtime)
    if log is not None:
        before = call_runner.one_call_per_lost_answer(before, log)
        now = call_runner.one_call_per_lost_answer(now, log)
    return before, now


def _told(name="look_up_order", arguments=None, response=None):
    world = _World()
    _record_completed_tool_call(
        world, name=name, arguments={"order_id": "A1001"} if arguments is None else arguments,
        response=response or {"result": {"status": "shipped"}, "success": True},
    )
    return world.calls[0]


_LINE = {"name": "look_up_order", "arguments": {"order_id": "A1001"}, "output": "shipped", "at": 1.1}
_CHAT_CASES = {
    "the world and the tracer saw one call": (
        [_call(result="shipped", at=1.0)], [_LINE],
    ),
    "the tracer recorded a default the world did not": (
        [_call("search", arguments={"q": "x"}, result="r", at=1.0)],
        [{"name": "search", "arguments": {"q": "x", "limit": 10}, "output": "r", "at": 1.1}],
    ),
    "the tracer recorded a number the world had as text": (
        [_call("book", arguments={"party": "2"}, result="ok", at=1.0)],
        [{"name": "book", "arguments": {"party": 2}, "output": "ok", "at": 1.1}],
    ),
    "one call twice, seen twice by both": (
        [_call("ping", arguments={}, result="a", at=1.0), _call("ping", arguments={}, result="a", at=2.0)],
        [{"name": "ping", "arguments": {}, "output": "a", "at": 1.1},
         {"name": "ping", "arguments": {}, "output": "a", "at": 2.1}],
    ),
    "one call twice in the world, once by the tracer": (
        [_call("ping", arguments={"a": 1}, result="a", at=1.0), _call("ping", arguments={"a": 1}, result="a", at=2.0)],
        [{"name": "ping", "arguments": {"a": 1}, "output": "a", "at": 1.1}],
    ),
    "no recorded call, the tracer's lines with and without times": (
        [],
        [{"name": "a", "arguments": {}, "output": "1"}, {"name": "b", "arguments": {}, "output": "2", "at": 5.0},
         {"name": "c", "arguments": {}, "output": "3"}],
    ),
    "a call only the tracer saw": (
        [_call(result="shipped", at=1.0)],
        [{"name": "return_policy", "arguments": {}, "output": "30 days", "at": 2.0}],
    ),
    "the callback route, its span with empty text": (
        [_told()],
        [{"name": "look_up_order", "arguments": "", "output": "", "source": "otel", "agent_key": "Support", "at": 1.0}],
    ),
    "the callback route, a tracer line of the same call": (
        [_told()], [_LINE],
    ),
    "no recorded call, two lines of one call, one marked by the CrewAI observer": (
        [],
        [{"name": "book", "arguments": {"date": "fri"}, "output": "ok", "at": 10.0, "ended_at": 10.5,
          "source": "crewai"},
         {"name": "book", "arguments": {"date": "fri"}, "output": "ok", "at": 11.0, "ended_at": 11.5},
         {"name": "pay", "arguments": {"date": "fri"}, "output": "ok", "at": 12.0, "ended_at": 12.5},
         *({"name": name, "arguments": {"date": "fri"}, "output": "ok", "at": at, "ended_at": at + 3,
            "source": "otel", "event_id": event_id, "agent_key": agent}
           for name, at, event_id, agent in (("book", 9.0, "e1", "booker"), ("book", 9.5, "e2", "booker"),
                                             ("pay", 9.7, "e3", "cashier")))],
    ),
    "the world's call and a span that recorded less": (
        [_call("book", arguments={"date": "fri", "party": 2}, result="booked #7", at=1.0)],
        [{"name": "book", "arguments": "{\"date\": \"fri\"}", "output": None, "source": "otel", "event_id": "e1",
          "at": 0.9, "ended_at": 1.2}],
    ),
}


@pytest.mark.parametrize("case", list(_CHAT_CASES), ids=lambda case: case.replace(" ", "-"))
def test_a_chat_trial_reads_its_calls_as_before_sources_were_named(tmp_path, monkeypatch, case) -> None:
    observed, lines = _CHAT_CASES[case]

    before, now = _chat_readings(tmp_path, monkeypatch, observed, lines)

    assert _unnamed(now) == _unnamed(before)
    assert all(call.source for call in now)


def test_a_chat_trial_with_recorded_calls_reads_none_of_the_tracers_lines(tmp_path, monkeypatch) -> None:
    observed, lines = _CHAT_CASES["the tracer recorded a default the world did not"]

    _before, now = _chat_readings(tmp_path, monkeypatch, observed, lines)

    assert [(call.arguments, call.source) for call in now] == [({"q": "x"}, WORLD)]


def test_on_the_callback_route_a_span_is_listed_and_the_agents_record_stands(tmp_path, monkeypatch) -> None:
    observed, lines = _CHAT_CASES["the callback route, its span with empty text"]

    _before, now = _chat_readings(tmp_path, monkeypatch, observed, lines)

    assert [(call.source, call.seen_by, call.arguments, call.result, call.worker_id) for call in now] == [
        (AGENT, [SPAN], {"order_id": "A1001"}, {"status": "shipped"}, "Support")
    ]


@pytest.mark.parametrize(
    ("observed", "lines"),
    [
        # The tool's tracer saw it run; the agent's span saw the failure the agent heard.
        ([], [_LINE, {"name": "look_up_order", "arguments": "{\"order_id\": \"A1001\"}", "output": "timed out",
                      "is_error": True, "source": "otel", "event_id": "e1", "at": 1.5}]),
        # The world ran it, its answer lost on the way back; the agent reported the failure it heard.
        ([_call(result="shipped", at=1.0, error="response_lost"),
          _told(response={"error": "timed out", "success": False})], []),
    ],
    ids=["tracer-ran-span-failed", "world-ran-agent-failed"],
)
def test_a_chat_lost_answer_is_one_done_call_with_the_tools_own_result(tmp_path, monkeypatch, observed, lines) -> None:
    before, now = _chat_readings(tmp_path, monkeypatch, observed, lines, log=_LOST)

    assert _unnamed(now) == _unnamed(before)
    assert [(call.ok, call.error, call.answer_lost, call.result) for call in now] == [(True, "", True, "shipped")]


def test_a_google_adk_reply_streamed_in_parts_reads_as_one_record_per_call(tmp_path, monkeypatch) -> None:
    # The Google ADK agent of 19 Nov: each tool call streamed in unfinished parts, then once finished with its
    # arguments and its response; its tracing wrote a span for each call with empty text for both.
    fixture = Path(__file__).parent / "fixtures" / "google_adk_streamed_replies.json"
    replies = json.loads(fixture.read_text(encoding="utf-8"))["replies"]
    reader = HTTPAgentWrapper(
        endpoint="http://localhost:8080/run_sse",
        protocol="json_template",
        request_template={"new_message": "{{new_message_content}}"},
        response_path="content.parts.0.text",
    )

    class _Agent:
        def __init__(self) -> None:
            self.turn = 0

        async def call(self, _request):
            events = "".join(f"data: {json.dumps(event)}\n\n" for event in replies[self.turn])
            self.turn += 1
            return reader._agent_response_from_payload(_decode_json_or_sse(events))

    world = _World()
    target = _HostedChatTarget(
        wrapper=_Agent(),
        contract=SimpleNamespace(runtime=None, tool_entrypoints=[]),
        world=world,
        scenario_key="adk",
        scenario_id="adk-1",
        door=object(),
    )
    for utterance in ("Where is A1001?", "Return it, it is too small.", "What is the policy?"):
        asyncio.run(target.say(utterance))
    lines = [
        {"name": call.name, "arguments": "", "output": "", "source": "otel", "at": call.at} for call in world.calls
    ]

    before, now = _chat_readings(tmp_path, monkeypatch, world.calls, lines)

    assert _unnamed(now) == _unnamed(before)
    assert [(call.name, call.arguments, call.source, call.seen_by) for call in now] == [
        ("look_up_order", {"order_id": "A1001"}, AGENT, [SPAN]),
        ("start_return", {"order_id": "A1001", "reason": "too small"}, AGENT, [SPAN]),
        ("return_policy", {}, AGENT, [SPAN]),
    ]
    assert all(call.result is not None for call in now)
