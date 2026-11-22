"""One record per tool call, naming the source it came from, read in one order (tool_evidence.py)."""

from __future__ import annotations

import json
from pathlib import Path

from fi.alk.harness import call_runner, tool_evidence
from fi.alk.harness.chat_call_runner import _record_completed_tool_call
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState
from fi.alk.harness.tool_evidence import AGENT, SPAN, TRACER, WORLD, bare, join, settle
from fi.alk.harness.world.runtime import Call, trace_row


def _call(name="look_up_order", source="", arguments=None, result=None, *, at=0.0, ids=None, **fields):
    return Call(
        name=name,
        arguments={"order_id": "A1001"} if arguments is None else arguments,
        result=result,
        at=at,
        runtime_ids=dict(ids or {}),
        source=source,
        **fields,
    )


def _join(*levels):
    return join(
        *levels,
        same_tool=call_runner._same_tool,
        same_arguments=call_runner._same_arguments,
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


# -- the winner -----------------------------------------------------------------------------------


def test_the_higher_source_fixes_the_graded_facts_and_the_lower_fills_only_what_it_lacks() -> None:
    kept = _call(source=TRACER, result="shipped", at=10.0, worker_label="Support agent")
    span = _call(
        source=SPAN,
        arguments={"order_id": "A1001", "verbose": True},
        result={"status": "shipped"},
        at=9.5,
        ended_at=11.0,
        ids={"event_id": "e1"},
    )

    settle(kept, span)

    assert kept.source == SPAN
    assert kept.seen_by == [TRACER]
    # All the graded facts are the span's, none blended from the tracer's line.
    assert (kept.arguments, kept.result) == ({"order_id": "A1001", "verbose": True}, {"status": "shipped"})
    # The span starts when the call does, so its time is the call's.
    assert (kept.at, kept.ended_at) == (9.5, 11.0)
    assert kept.runtime_ids == {"event_id": "e1"}
    # The span named no agent; the tracer's line did.
    assert kept.worker_label == "Support agent"


def test_a_record_with_no_arguments_and_no_result_never_wins() -> None:
    # The Google ADK span of 19 Nov: the call, with empty arguments and output.
    kept = _call(source=SPAN, arguments={}, result=None, ids={"event_id": "e1"})
    traced = _call(source=TRACER, result="shipped", at=3.0)
    assert bare(kept) and not bare(traced)

    settle(kept, traced)

    assert kept.source == TRACER
    assert kept.seen_by == [SPAN]
    assert (kept.arguments, kept.result) == ({"order_id": "A1001"}, "shipped")
    # What the tracer's line does not carry still comes from the span.
    assert kept.runtime_ids == {"event_id": "e1"}


def test_the_sources_that_saw_a_call_are_listed_once_in_order() -> None:
    kept = _call(source=AGENT, result="shipped")
    settle(kept, _call(source=TRACER, result="shipped"))
    settle(kept, _call(source=WORLD, result="shipped"))

    assert kept.source == WORLD
    assert kept.seen_by == [TRACER, AGENT]
    assert trace_row(kept)["source"] == WORLD
    assert trace_row(kept)["seen_by"] == [TRACER, AGENT]


# -- pairing --------------------------------------------------------------------------------------


def test_one_tool_called_twice_with_the_same_arguments_stays_two_calls() -> None:
    first, second = _call(source=TRACER, at=1.0), _call(source=TRACER, at=2.0)

    calls = _join([first, second])

    assert calls == (first, second)


def test_a_lower_source_never_adds_a_second_copy_but_keeps_a_call_only_it_saw() -> None:
    world = [_call(source=WORLD, result="shipped", at=1.0)]
    traced = [_call(source=TRACER, result="shipped", at=1.1), _call(source=TRACER, result="shipped", at=2.0)]

    calls = _join(world, traced)

    # The world saw one of the tracer's two identical calls: two calls, not three, not one.
    assert [(call.source, call.seen_by, call.at) for call in calls] == [
        (WORLD, [TRACER], 1.0),
        (TRACER, [], 2.0),
    ]


def test_ids_that_restart_inside_a_reply_still_pair_call_for_call() -> None:
    # A hand-written agent numbers its calls from zero at each model step, so one reply holds two "call_0".
    traced = [
        _call(source=TRACER, arguments={"order_id": "A1"}, result="one", at=1.0, ids={"call_id": "call_0"}),
        _call(source=TRACER, arguments={"order_id": "B2"}, result="two", at=2.0, ids={"call_id": "call_0"}),
    ]
    told = [
        _call(source=AGENT, arguments={"order_id": "B2"}, result="two", at=5.0, ids={"call_id": "call_0"}),
        _call(source=AGENT, arguments={"order_id": "A1"}, result="one", at=5.0, ids={"call_id": "call_0"}),
    ]

    calls = _join(traced, told)

    assert [(call.arguments, call.source, call.seen_by) for call in calls] == [
        ({"order_id": "A1"}, TRACER, [AGENT]),
        ({"order_id": "B2"}, TRACER, [AGENT]),
    ]


def test_two_different_ids_are_two_calls_whatever_else_they_share() -> None:
    calls = _join(
        [_call(source=TRACER, ids={"call_id": "a"}, at=1.0)],
        [_call(source=AGENT, ids={"call_id": "b"}, at=2.0)],
    )

    assert [call.source for call in calls] == [TRACER, AGENT]


def test_different_tools_never_pair() -> None:
    calls = _join([_call(source=WORLD)], [_call("start_return", source=TRACER, at=1.0)])

    assert [call.name for call in calls] == ["look_up_order", "start_return"]


def test_a_hand_off_is_kept_as_it_is() -> None:
    handoff = _call("Support agent", source=TRACER, arguments={}, handoff=True, at=1.0)

    calls = _join([_call("Support agent", source=WORLD, arguments={})], [handoff])

    assert handoff in calls and len(calls) == 2


# -- the chat trial -------------------------------------------------------------------------------


def test_a_chat_trial_counts_a_call_the_world_and_the_tracer_both_saw_once(tmp_path) -> None:
    runtime = _runtime(
        tmp_path,
        [{"name": "look_up_order", "arguments": {"order_id": "A1001"}, "output": "shipped", "at": 2.0}],
    )
    ran = _call(result="shipped", at=1.0)

    calls = call_runner._with_span_calls([ran], runtime)

    assert len(calls) == 1
    assert (calls[0].source, calls[0].seen_by) == (WORLD, [TRACER])


def test_a_chat_trial_keeps_a_call_only_the_tracer_saw(tmp_path) -> None:
    # Before this, a trial that recorded any call ignored the trace file's lines.
    runtime = _runtime(
        tmp_path,
        [{"name": "return_policy", "arguments": {}, "output": "30 days", "at": 2.0}],
    )

    calls = call_runner._with_span_calls([_call(result="shipped", at=1.0)], runtime)

    assert [(call.name, call.source) for call in calls] == [
        ("look_up_order", WORLD),
        ("return_policy", TRACER),
    ]


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


def test_on_the_callback_route_the_agents_arguments_and_result_survive_the_join(tmp_path) -> None:
    world = _World()
    _record_completed_tool_call(
        world,
        name="look_up_order",
        arguments={"order_id": "A1001"},
        response={"result": {"status": "shipped"}, "success": True},
        call_id="call_1",
    )
    told = world.calls[0]
    assert (told.source, told.runtime_ids) == (AGENT, {"call_id": "call_1"})
    # The tracer saw the same call and recorded nothing of what it was given or gave back.
    runtime = _runtime(tmp_path, [{"name": "look_up_order", "arguments": {}, "output": None, "at": told.at}])

    calls = call_runner._with_span_calls(world.calls, runtime)

    assert len(calls) == 1
    assert calls[0].source == AGENT
    assert calls[0].seen_by == [TRACER]
    assert (calls[0].arguments, calls[0].result) == ({"order_id": "A1001"}, {"status": "shipped"})


def test_the_agents_account_loses_to_the_tracer_that_saw_the_call_run(tmp_path) -> None:
    world = _World()
    _record_completed_tool_call(
        world, name="look_up_order", arguments={"order_id": "A1001"}, response={"result": "shipped"}
    )
    runtime = _runtime(
        tmp_path,
        [{"name": "look_up_order", "arguments": {"order_id": "A1001"}, "output": "shipped", "at": 1.0}],
    )

    calls = call_runner._with_span_calls(world.calls, runtime)

    assert [(call.source, call.seen_by) for call in calls] == [(TRACER, [AGENT])]


def test_the_order_is_the_decided_one() -> None:
    assert tool_evidence.ORDER == (WORLD, SPAN, TRACER, AGENT)


def test_a_span_that_wrote_empty_text_for_both_says_nothing(tmp_path) -> None:
    # The readiness route of a callback agent: its reply's call with arguments and result, and the span of the
    # same call that recorded empty text for both.
    world = _World()
    _record_completed_tool_call(
        world, name="look_up_order", arguments={"order_id": "A1001"}, response={"result": {"status": "shipped"}}
    )
    runtime = _runtime(
        tmp_path,
        [{"name": "look_up_order", "arguments": "", "output": "", "source": "otel", "agent_key": "Support", "at": 1.0}],
    )

    calls = call_runner._with_span_calls(world.calls, runtime)

    assert [(call.source, call.seen_by, call.arguments, call.result, call.worker_id) for call in calls] == [
        (AGENT, [SPAN], {"order_id": "A1001"}, {"status": "shipped"}, "Support")
    ]
