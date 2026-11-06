"""Tool calls and the agent that made each, read from framework spans by the sandbox's startup hook.

Each case runs a real interpreter with the hook as its ``sitecustomize`` and makes spans the way one
framework's instrumentation shapes them, so no framework has to be installed here.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from fi.alk.harness import (
    call_runner,
    simulation_mark_bootstrap,
    simulation_worker_events,
)
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState
from fi.alk.harness.world.runtime import Call, trace_row

_PRELUDE = """
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode
{provider}
tracer = trace.get_tracer("probe")

def span(name, **attributes):
    return tracer.start_as_current_span(name, attributes=attributes)
"""


def _place_hook(hook: Path) -> None:
    for module in (simulation_mark_bootstrap, simulation_worker_events):
        (hook / Path(module.__file__).name).write_bytes(
            Path(module.__file__).read_bytes()
        )


def _run(
    tmp_path: Path,
    body: str,
    *,
    provider: str = "trace.set_tracer_provider(TracerProvider())",
) -> list[dict]:
    hook = tmp_path / "hook"
    hook.mkdir()
    _place_hook(hook)
    (hook / "sitecustomize.py").write_text(
        "import simulation_mark_bootstrap\nsimulation_mark_bootstrap.install()\n"
    )
    trace = tmp_path / "calls.jsonl"
    script = _PRELUDE.format(provider=provider) + textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": str(hook),
            "HARNESS_TOOL_TRACE": str(trace),
            "ALK_OBSERVE_FRAMEWORKS": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "hook: failed" not in result.stderr
    if not trace.exists():
        return []
    return [json.loads(line) for line in trace.read_text().splitlines()]


def _sealed(tmp_path: Path) -> list[dict]:
    """The hook's lines as the sandbox reads and seals them."""
    runtime = EnvironmentRuntime(
        runtime_id="rt-1",
        world_index=0,
        bundle_digest="sha256:" + "0" * 64,
        state=RuntimeState.READY,
        endpoints={},
        metadata={"tool_trace_path": str(tmp_path / "calls.jsonl")},
    )
    return [trace_row(call) for call in call_runner._collect_file_tool_calls(runtime)]


def _placed(rows: list[dict]) -> list[tuple]:
    return [
        (
            row["name"],
            row["kind"],
            row["agent"],
            row["relationship"],
            row["parent_span_id"],
        )
        for row in rows
    ]


def _by_tool(records: list[dict]) -> dict[str, tuple]:
    return {r["name"]: (r.get("agent_key"), r.get("delegated_to")) for r in records}


def test_a_tool_call_names_the_agent_above_it_and_a_hand_off_names_the_worker(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        with span("Lead agent", **{"gen_ai.span.kind": "AGENT", "graph.node.id": "Lead agent"}):
            with span("ask_research", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_research"}):
                with span("Research worker", **{"gen_ai.span.kind": "AGENT", "graph.node.id": "Research worker"}):
                    with span("search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes",
                                              "input.value": '{"query": "q"}', "output.value": "found"}):
                        pass
    """,
    )
    assert _by_tool(records) == {
        "search_notes": ("Research worker", None),
        "ask_research": ("Lead agent", "Research worker"),
    }
    lookup = next(r for r in records if r["name"] == "search_notes")
    assert (lookup["arguments"], lookup["output"], lookup["is_error"]) == (
        '{"query": "q"}',
        "found",
        False,
    )


def test_a_langgraph_tool_is_the_subgraph_above_the_node_that_ran_it(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        with span("tools", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "publish_note",
                              "gen_ai.agent.name": "tools", "langgraph_node": "tools",
                              "langgraph_checkpoint_ns": "writer_worker:1|tools:2"}):
            pass
        with span("tools", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup",
                              "langgraph_node": "tools", "langgraph_checkpoint_ns": "tools:3"}):
            pass
    """,
    )
    assert _by_tool(records) == {
        "publish_note": ("writer_worker", None),
        "lookup": (None, None),
    }


def test_a_background_job_is_a_hand_off_from_the_tool_call_that_launched_it(
    tmp_path: Path,
) -> None:
    # A supervisor starts two jobs; each runs later as its own run (no parent here), named by the id its launch
    # returned, with its tools attributed to the job's agent. A status check that echoes the id is not the launch.
    records = _run(
        tmp_path,
        """
        sup = {"gen_ai.conversation.id": "thread-sup"}
        with span("agent", **{"gen_ai.span.kind": "AGENT", **sup}):
            with span("tools", **{"gen_ai.span.kind": "CHAIN", **sup}):
                for job, kind in (("job-r", "researcher"), ("job-c", "coder")):
                    with span("start_async_task", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "start_async_task",
                                                     "input.value": kind, **sup,
                                                     "output.value": "Launched async subagent. task_id: " + job}):
                        pass
        with span("agent", **{"gen_ai.span.kind": "AGENT", **sup}):
            with span("check_async_task", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "check_async_task",
                                             "output.value": '{"status": "running", "thread_id": "job-r"}', **sup}):
                pass
        for job, kind, tool in (("job-c", "coder", "run_code_check"), ("job-r", "researcher", "analyze_topic")):
            own = {"lc_agent_name": kind, "gen_ai.conversation.id": job}
            with span(kind, **{"gen_ai.span.kind": "CHAIN", "output.value": kind + " done", **own}):
                with span("tools", **{"gen_ai.span.kind": "CHAIN", "langgraph_node": "tools",
                                      "langgraph_checkpoint_ns": "tools:1", **own}):
                    with span(tool, **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": tool, "langgraph_node": "tools",
                                       "langgraph_checkpoint_ns": "tools:1", **own}):
                        pass
    """,
    )
    launches = {
        r["output"].rsplit(" ", 1)[-1]: r
        for r in records
        if r["name"] == "start_async_task" and not r.get("delegated_to")
    }
    hand_offs = {r["delegated_to"]: r for r in records if r.get("delegated_to")}
    assert {
        r["name"]: r.get("agent_key")
        for r in records
        if r["name"] in ("analyze_topic", "run_code_check")
    } == {
        "analyze_topic": "researcher",
        "run_code_check": "coder",
    }
    assert sorted(hand_offs) == ["coder", "researcher"]
    for worker, job in (("researcher", "job-r"), ("coder", "job-c")):
        hand_off = hand_offs[worker]
        assert (hand_off["name"], hand_off["agent_key"], hand_off["output"]) == (
            "start_async_task",
            "agent",
            worker + " done",
        )
        assert hand_off["parent_event_id"] == launches[job]["event_id"]
        assert hand_off["arguments"] == worker
        assert 0 < hand_off["at"] <= hand_off["ended_at"]


def test_a_job_s_answer_is_what_its_model_last_said_not_its_whole_conversation(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        sup = {"gen_ai.conversation.id": "thread-sup"}
        with span("agent", **{"gen_ai.span.kind": "AGENT", **sup}):
            with span("start_async_task", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "start_async_task",
                                             "output.value": "Launched. task_id: job-c", **sup}):
                pass
        own = {"lc_agent_name": "coder", "gen_ai.conversation.id": "job-c"}
        with span("coder", **{"gen_ai.span.kind": "CHAIN", "output.value": '{"messages": ["<the task>", "<the answer>"]}', **own}):
            for said in ("<the answer>", ""):
                with span("model", **{"gen_ai.span.kind": "LLM", "gen_ai.output.messages.0.message.content": said, **own}):
                    pass
    """,
    )
    assert [
        r["output"] for r in records if r.get("delegated_to") and "ended_at" in r
    ] == ["<the answer>"]


def test_a_job_never_joins_a_launch_from_an_earlier_call(tmp_path: Path) -> None:
    records = _run(
        tmp_path,
        """
        import os
        sup = {"gen_ai.conversation.id": "thread-sup"}
        with span("agent", **{"gen_ai.span.kind": "AGENT", **sup}):
            with span("start_async_task", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "start_async_task",
                                             "output.value": "Launched. task_id: job-c", **sup}):
                pass
        # The harness removes the trace between calls.
        os.remove(os.environ["HARNESS_TOOL_TRACE"])
        own = {"lc_agent_name": "coder", "gen_ai.conversation.id": "job-c"}
        with span("coder", **{"gen_ai.span.kind": "CHAIN", **own}):
            with span("run_code_check", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "run_code_check", **own}):
                pass
    """,
    )
    assert [(r["name"], r.get("delegated_to")) for r in records] == [
        ("run_code_check", None)
    ]


def test_a_job_still_running_when_the_call_ends_keeps_its_hand_off(
    tmp_path: Path,
) -> None:
    # The job starts and makes a tool call, but has not finished when the process stops.
    records = _run(
        tmp_path,
        """
        sup = {"gen_ai.conversation.id": "thread-sup"}
        with span("agent", **{"gen_ai.span.kind": "AGENT", **sup}):
            with span("start_async_task", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "start_async_task",
                                             "input.value": "coder", **sup,
                                             "output.value": "Launched async subagent. task_id: job-c"}):
                pass
        own = {"lc_agent_name": "coder", "gen_ai.conversation.id": "job-c"}
        job = tracer.start_span("coder", attributes={"gen_ai.span.kind": "CHAIN", **own})
        with trace.use_span(job):
            with span("run_code_check", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "run_code_check", **own}):
                pass
    """,
    )
    hand_offs = [r for r in records if r.get("delegated_to")]
    assert [
        (r["delegated_to"], r["arguments"], "ended_at" in r) for r in hand_offs
    ] == [("coder", "coder", False)]
    assert {
        r["name"]: r.get("agent_key") for r in records if r["name"] == "run_code_check"
    } == {"run_code_check": "coder"}


def test_a_sub_agent_run_through_a_tool_is_its_hand_off_and_owns_its_tool_calls(
    tmp_path: Path,
) -> None:
    # Deep Agents' `task` tool runs a sub-agent inside the supervisor's `tools` node. The tool span is marked a tool
    # only when it ends; the sub-agent's nodes sit under the host node in the namespace and name their agent. A tool
    # inside a named agent whose own inner run names the same agent is not a hand-off.
    records = _run(
        tmp_path,
        """
        sup_tools = {"langgraph_node": "tools", "langgraph_checkpoint_ns": "tools:a"}
        own = {"lc_agent_name": "researcher", "langgraph_node": "tools", "langgraph_checkpoint_ns": "tools:a|tools:b"}
        with span("LangGraph", **{"gen_ai.span.kind": "CHAIN"}):
            with span("tools", **{"gen_ai.span.kind": "CHAIN", **sup_tools}):
                with span("task", **sup_tools) as task:
                    with span("researcher", **{"gen_ai.span.kind": "CHAIN", **own}):
                        with span("tools", **{"gen_ai.span.kind": "CHAIN", **own}):
                            with span("ls", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ls", **own}):
                                pass
                    task.set_attribute("gen_ai.span.kind", "TOOL")
                    task.set_attribute("gen_ai.tool.name", "task")
                    task.set_attribute("output.value", "researcher answer")
        named = {"lc_agent_name": "writer", "langgraph_node": "tools", "langgraph_checkpoint_ns": "writer:c|tools:d"}
        with span("writer", **{"gen_ai.span.kind": "CHAIN", **named}):
            with span("summarise", **named) as tool:
                with span("summary chain", **{"gen_ai.span.kind": "CHAIN", **named}):
                    pass
                tool.set_attribute("gen_ai.span.kind", "TOOL")
                tool.set_attribute("gen_ai.tool.name", "summarise")
    """,
    )
    assert _by_tool(records) == {
        "ls": ("researcher", None),
        "task": (None, "researcher"),
        "summarise": ("writer", None),
    }


@pytest.mark.parametrize("inner_first", [False, True])
def test_a_job_named_only_inside_it_that_starts_before_its_launch_returns_is_its_hand_off(
    tmp_path: Path, inner_first: bool
) -> None:
    # The job's run starts while the launching tool call is still returning, carries no attributes until it ends (as
    # LangChain's instrumentation sets them), and is still running when the call ends: the spans inside it name it,
    # whether they finish after the launch returns or before.
    inner = """
with trace.use_span(job):
    with span("run_code_check", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "run_code_check", **own}):
        pass
"""
    launched = """
launch.set_attribute("output.value", "Launched async subagent. task_id: job-c")
launch.end()
"""
    records = _run(
        tmp_path,
        """
sup = {"gen_ai.conversation.id": "thread-sup"}
own = {"lc_agent_name": "coder", "gen_ai.conversation.id": "job-c"}
agent = tracer.start_span("agent", attributes={"gen_ai.span.kind": "AGENT", **sup})
launch = tracer.start_span("start_async_task", context=trace.set_span_in_context(agent), attributes={
    "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "start_async_task", "input.value": "coder", **sup})
job = tracer.start_span("coder", context=trace.set_span_in_context(trace.INVALID_SPAN))
"""
        + (inner + launched if inner_first else launched + inner)
        + "agent.end()\n",
    )
    hand_offs = [r for r in records if r.get("delegated_to")]
    assert [
        (r["delegated_to"], r["agent_key"], "ended_at" in r) for r in hand_offs
    ] == [("coder", "agent", False)]
    assert {
        r["name"]: r.get("agent_key") for r in records if r["name"] == "run_code_check"
    } == {"run_code_check": "coder"}


def test_a_run_whose_conversation_no_tool_returned_is_not_a_hand_off(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        with span("agent", **{"gen_ai.span.kind": "AGENT", "gen_ai.conversation.id": "thread-a"}):
            with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup", "output.value": "x",
                                   "gen_ai.conversation.id": "thread-a"}):
                pass
        with span("agent", **{"gen_ai.span.kind": "AGENT", "gen_ai.conversation.id": "thread-a"}):
            pass
    """,
    )
    assert [r.get("delegated_to") for r in records] == [None]


def test_an_agent_named_on_the_tool_span_or_by_operation_name_is_read(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        with span("execute_tool search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes",
                                              "gen_ai.agent.name": "research_worker"}):
            pass
        with span("invoke_agent", **{"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "writer_worker"}):
            with span("execute_tool", **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "publish_note"}):
                pass
        with span("Task", **{"claude_agent.span_kind": "subagent", "claude_agent.subagent_type": "researcher"}):
            with span("WebSearch", **{"claude_agent.span_kind": "tool", "tool.name": "WebSearch"}):
                pass
    """,
    )
    assert _by_tool(records) == {
        "search_notes": ("research_worker", None),
        "publish_note": ("writer_worker", None),
        "WebSearch": ("researcher", None),
    }


def test_an_unnamed_agent_takes_the_tool_that_handed_it_work_and_otherwise_stays_unnamed(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        with span("Agent.run", **{"gen_ai.span.kind": "AGENT"}):
            with span("ask_researcher", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_researcher"}):
                with span("Agent.run", **{"gen_ai.span.kind": "AGENT"}):
                    with span("search", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search"}):
                        pass
    """,
    )
    assert _by_tool(records) == {
        "search": ("ask_researcher", None),
        "ask_researcher": (None, "ask_researcher"),
    }


def test_one_call_wrapped_by_two_instrumentations_is_written_once_and_a_failure_is_an_error(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        with span("Worker", **{"gen_ai.span.kind": "AGENT", "agent.name": "Worker"}):
            with span("ToolUsage._use", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "Publish note"}):
                with span("publish_note", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "publish_note"}) as inner:
                    inner.set_status(Status(StatusCode.ERROR))
            with span("Delegate work", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "Delegate work"}):
                with span("search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes"}):
                    pass
    """,
    )
    assert [(r["name"], r["is_error"]) for r in records] == [
        ("publish_note", True),
        ("search_notes", False),
        ("Delegate work", False),
    ]


def test_an_autogen_core_tool_is_the_agent_the_runtime_handed_the_message_to(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        process = {"messaging.operation": "process", "recipient_agent_type": "Writer", "sender_agent_type": "Triage"}
        with span("autogen process Writer.(s1)-A", **process):
            with span("execute_tool look_up_tide", **{"gen_ai.operation.name": "execute_tool",
                                                      "gen_ai.tool.name": "look_up_tide"}):
                pass
        with span("autogen publish Writer.(s1)-T", **{"messaging.operation": "publish"}):
            with span("execute_tool draft_note", **{"gen_ai.operation.name": "execute_tool",
                                                    "gen_ai.tool.name": "draft_note"}):
                pass
    """,
    )
    assert _by_tool(records) == {
        "look_up_tide": ("Writer", None),
        "draft_note": (None, None),
    }


def test_a_process_that_sets_no_provider_is_still_observed(tmp_path: Path) -> None:
    records = _run(
        tmp_path,
        """
        with span("Worker", **{"gen_ai.span.kind": "AGENT", "agent.name": "Worker"}):
            with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
                pass
    """,
        provider="",
    )
    assert _by_tool(records) == {"lookup": ("Worker", None)}


def test_a_process_that_sets_its_own_provider_later_keeps_it(tmp_path: Path) -> None:
    records = _run(
        tmp_path,
        """
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        own = TracerProvider(); memory = InMemorySpanExporter(); own.add_span_processor(SimpleSpanProcessor(memory))
        trace.set_tracer_provider(own)
        with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
            pass
        assert [s.name for s in memory.get_finished_spans()] == ["lookup"]
    """,
        provider="",
    )
    assert [r["name"] for r in records] == ["lookup"]


def test_a_span_made_before_the_process_sets_its_provider_leaves_it_free_to_set_one(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        with span("startup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "startup"}):
            pass
        own = TracerProvider(); memory = InMemorySpanExporter(); own.add_span_processor(SimpleSpanProcessor(memory))
        trace.set_tracer_provider(own)
        assert trace.get_tracer_provider() is own
        with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
            pass
        assert [s.name for s in memory.get_finished_spans()] == ["lookup"]
    """,
        provider="",
    )
    assert [r["name"] for r in records] == ["startup", "lookup"]


@pytest.mark.parametrize("native_worker", ["", "Writer worker"])
def test_a_span_line_lends_its_agent_to_another_tracer_s_line_for_the_same_call(
    native_worker: str,
) -> None:
    native = Call(
        name="publish_note",
        arguments={},
        result=None,
        ok=True,
        worker_label=native_worker,
        at=2.0,
    )
    span_line = Call(
        name="Publish note",
        arguments={},
        result=None,
        ok=True,
        worker_id="w",
        worker_label="From span",
        at=2.1,
    )
    other = Call(
        name="search_notes",
        arguments={},
        result=None,
        ok=True,
        worker_id="w",
        worker_label="From span",
        at=1.0,
    )
    merged = call_runner._merge_span_calls([native], [span_line, other])
    assert [(c.name, c.worker_label) for c in merged] == [
        ("search_notes", "From span"),
        ("publish_note", native_worker or "From span"),
    ]


def test_a_refusal_read_differently_by_the_two_tracers_is_still_one_call() -> None:
    native = Call(name="issue_refund", arguments={}, result="refused", ok=False, at=2.0)
    span_line = Call(
        name="issue_refund",
        arguments={},
        result="refused",
        ok=True,
        worker_label="Refunds",
        at=2.0,
    )
    merged = call_runner._merge_span_calls([native], [span_line])
    assert [(c.name, c.worker_label) for c in merged] == [("issue_refund", "Refunds")]


def test_a_span_pairs_with_the_call_of_its_own_outcome_when_one_tool_ran_twice() -> (
    None
):
    passed = Call(
        name="issue_refund", arguments={"order": "1"}, result="issued", ok=True, at=2.0
    )
    failed = Call(
        name="issue_refund",
        arguments={"order": "2"},
        result="refused",
        ok=False,
        at=3.0,
    )
    span_line = Call(
        name="issue_refund",
        arguments={},
        result=None,
        ok=False,
        worker_label="Refunds",
        at=2.9,
    )
    merged = call_runner._merge_span_calls([passed, failed], [span_line])
    assert [(c.arguments, c.worker_label) for c in merged] == [
        ({"order": "1"}, ""),
        ({"order": "2"}, "Refunds"),
    ]


def test_a_call_another_tracer_saw_takes_the_span_s_event_ids_and_keeps_its_own() -> (
    None
):
    plain = Call(name="lookup", arguments={}, result=None, ok=True, at=1.0)
    own = Call(
        name="lookup",
        arguments={},
        result=None,
        ok=True,
        at=2.0,
        runtime_ids={"event_id": "own"},
    )
    spans = [
        Call(
            name="lookup",
            arguments={},
            result=None,
            ok=True,
            at=1.0,
            ended_at=1.5,
            runtime_ids={"event_id": "s1", "parent_event_id": "a1"},
        ),
        Call(
            name="lookup",
            arguments={},
            result=None,
            ok=True,
            at=2.0,
            runtime_ids={"event_id": "s2"},
        ),
    ]
    merged = call_runner._merge_span_calls([plain, own], spans)
    assert [c.runtime_ids for c in merged] == [
        {"event_id": "s1", "parent_event_id": "a1"},
        {"event_id": "own"},
    ]


def test_a_running_job_without_an_event_id_is_kept_beside_a_finished_one() -> None:
    def job(name, **fields):
        return Call(
            name="start_async_task",
            arguments={},
            result=None,
            ok=True,
            delegated_to=name,
            **fields,
        )

    merged = call_runner._merge_span_calls(
        [], [job("coder", at=1.0), job("researcher", at=2.0, ended_at=5.0)]
    )
    assert [c.delegated_to for c in merged] == ["coder", "researcher"]


def test_span_lines_alone_read_in_the_order_the_calls_started() -> None:
    late_end = Call(name="ask_research", arguments={}, result=None, ok=True, at=1.0)
    inner = Call(name="search_notes", arguments={}, result=None, ok=True, at=1.5)
    assert [c.name for c in call_runner._merge_span_calls([], [inner, late_end])] == [
        "ask_research",
        "search_notes",
    ]


def test_a_job_hand_off_stays_its_own_event_and_is_kept_once() -> None:
    # The sandbox's tracer and the span both report the launch; the job's hand-off is written when it starts and again
    # with its return, under the launch, and is never paired with the launch itself, whose event id it names.
    def line(at, **fields):
        return Call(
            name="start_async_task", arguments={}, result=None, ok=True, at=at, **fields
        )

    natives = [line(1.0), line(1.1)]
    first = line(1.0, runtime_ids={"event_id": "l1"})
    started = line(
        1.5,
        delegated_to="coder",
        runtime_ids={"event_id": "j1", "parent_event_id": "l1"},
    )
    returned = line(
        1.5,
        delegated_to="coder",
        ended_at=9.0,
        runtime_ids={"event_id": "j1", "parent_event_id": "l1"},
    )
    second = line(1.1, runtime_ids={"event_id": "l2"})
    merged = call_runner._merge_span_calls(natives, [first, started, returned, second])
    assert [
        (c.delegated_to, c.ended_at, c.runtime_ids.get("event_id")) for c in merged
    ] == [
        ("", 0.0, "l1"),
        ("", 0.0, "l2"),
        ("coder", 9.0, "j1"),
    ]


def _search(query, at: float, **fields) -> Call:
    return Call(
        **{
            "name": "search",
            "arguments": {"q": query},
            "result": query,
            "ok": True,
            "at": at,
            **fields,
        }
    )


def _search_span(query, at: float, worker: str, **fields) -> Call:
    return _search(
        query,
        at,
        **{
            "worker_id": worker,
            "runtime_ids": {"event_id": f"event-{query}"},
            "parent_span_id": f"hand-off-{worker}",
            "relationship": "observed",
            **fields,
        },
    )


def _hand_offs(*workers: str) -> list[Call]:
    return [
        Call(
            name="ask_worker",
            arguments={},
            ok=True,
            at=0.5,
            delegated_to=worker,
            runtime_ids={"event_id": f"hand-off-{worker}"},
        )
        for worker in workers
    ]


def _searches(merged) -> list[tuple]:
    return [
        (c.arguments, c.runtime_ids.get("event_id"), c.worker_id, c.parent_span_id)
        for c in merged
        if c.name == "search"
    ]


_EACH_OWN = [
    ({"q": "a"}, "event-a", "worker-a", "hand-off-worker-a"),
    ({"q": "b"}, "event-b", "worker-b", "hand-off-worker-b"),
]


@pytest.mark.parametrize("reversed_spans", [True, False])
@pytest.mark.parametrize(
    "stamps",
    [
        (1.0, 2.0),  # Stamped when issued.
        (9.2, 9.1),  # Stamped once each result was read, the later call first.
    ],
)
def test_two_overlapping_calls_of_one_tool_keep_their_own_spans_when_the_spans_end_in_the_other_order(
    stamps: tuple[float, float], reversed_spans: bool
) -> None:
    natives = [_search("a", stamps[0]), _search("b", stamps[1])]
    spans = [
        _search_span("a", 1.0, "worker-a", ended_at=4.0),
        _search_span("b", 2.0, "worker-b", ended_at=3.0),
    ]
    if reversed_spans:
        spans.reverse()
    merged = call_runner._merge_span_calls(
        natives, [*_hand_offs("worker-a", "worker-b"), *spans]
    )
    assert sorted(_searches(merged), key=str) == _EACH_OWN


@pytest.mark.parametrize("reversed_spans", [True, False])
def test_arguments_two_tracers_spell_differently_still_tell_overlapping_calls_apart(
    reversed_spans: bool,
) -> None:
    natives = [_search(5, 9.2), _search(6, 9.1)]
    spans = [
        _search_span("5", 1.0, "worker-a", ended_at=4.0),
        _search_span("6", 2.0, "worker-b", ended_at=3.0),
    ]
    if reversed_spans:
        spans.reverse()
    merged = call_runner._merge_span_calls(natives, spans)
    assert sorted((c.arguments["q"], c.worker_id) for c in merged) == [
        (5, "worker-a"),
        (6, "worker-b"),
    ]


def test_a_span_and_another_tracer_s_call_sharing_an_event_id_pair_whatever_else_they_say() -> (
    None
):
    natives = [
        _search("a", 9.2, runtime_ids={"event_id": "event-b"}),
        _search("b", 9.1, runtime_ids={"event_id": "event-a"}),
    ]
    spans = [_search_span("b", 2.0, "worker-b"), _search_span("a", 1.0, "worker-a")]
    merged = call_runner._merge_span_calls(natives, spans)
    assert [(c.runtime_ids["event_id"], c.worker_id) for c in merged] == [
        ("event-a", "worker-a"),
        ("event-b", "worker-b"),
    ]


def test_a_call_holding_another_span_s_event_id_is_left_to_that_span() -> None:
    # The span with that id is no search span, so only the other call is this span's to take.
    held = _search("a", 1.0, runtime_ids={"event_id": "event-x"})
    natives = [held, _search("b", 2.0)]
    spans = [
        Call(
            name="lookup",
            arguments={},
            ok=True,
            at=0.5,
            runtime_ids={"event_id": "event-x"},
        ),
        _search_span("b", 2.0, "worker-b", input_omitted=True),
    ]
    merged = call_runner._merge_span_calls(natives, spans)
    assert sorted(_searches(merged), key=str) == [
        ({"q": "a"}, "event-x", "", ""),
        ({"q": "b"}, "event-b", "worker-b", ""),
    ]


@pytest.mark.parametrize("reversed_spans", [True, False])
@pytest.mark.parametrize("offset", [-0.5, 0.0, 0.5, 7.0])
def test_calls_that_did_not_overlap_pair_in_order_whatever_the_offset_between_the_two_clocks(
    offset: float, reversed_spans: bool
) -> None:
    natives = [_search("a", 1.0 + offset), _search("b", 2.0 + offset)]
    spans = [
        _search_span("a", 1.0, "worker-a", ended_at=1.4, input_omitted=True),
        _search_span("b", 2.0, "worker-b", ended_at=2.4, input_omitted=True),
    ]
    if reversed_spans:
        spans.reverse()
    merged = call_runner._merge_span_calls(
        natives, [*_hand_offs("worker-a", "worker-b"), *spans]
    )
    assert sorted(_searches(merged), key=str) == _EACH_OWN


@pytest.mark.parametrize("reversed_spans", [True, False])
@pytest.mark.parametrize("stamps", [(0.5, 1.5), (1.0, 2.0), (1.3, 1.4), (9.2, 9.1)])
def test_overlapping_calls_of_one_tool_nothing_tells_apart_keep_their_agent_and_place_unknown(
    stamps: tuple[float, float], reversed_spans: bool
) -> None:
    # The spans left out their arguments, and no stamp of the other tracer's clock is read against a span's. A call
    # made in either hand-off then has no place either, while the hand-off both spans record is kept.
    from fi.alk.harness.world.runtime import trace_row

    natives = [_search("a", stamps[0]), _search("b", stamps[1])]
    spans = [
        _search_span(
            "a",
            1.0,
            "worker-a",
            ended_at=4.0,
            input_omitted=True,
            delegated_to="reader",
        ),
        _search_span(
            "b",
            2.0,
            "worker-b",
            ended_at=3.0,
            input_omitted=True,
            delegated_to="reader",
        ),
    ]
    if reversed_spans:
        spans.reverse()
    child = Call(
        name="summarise",
        arguments={},
        ok=True,
        at=2.5,
        worker_id="worker-b",
        runtime_ids={"event_id": "event-c"},
        parent_span_id="event-b",
        relationship="observed",
    )
    merged = call_runner._merge_span_calls(natives, [*spans, child])
    assert sorted(
        (
            (
                c.name,
                c.arguments,
                c.worker_id,
                c.delegated_to,
                c.runtime_ids,
                c.parent_span_id,
            )
            for c in merged
        ),
        key=str,
    ) == [
        ("search", {"q": "a"}, "", "reader", {}, ""),
        ("search", {"q": "b"}, "", "reader", {}, ""),
        ("summarise", {}, "worker-b", "", {"event_id": "event-c"}, ""),
    ]
    (row,) = (trace_row(c) for c in merged if c.name == "summarise")
    assert row["relationship"] == "unknown"


@pytest.mark.parametrize(
    "native",
    [
        _search(5, 9.0),
        Call(name="search", arguments={"raw": '{"q": "a'}, ok=True, at=9.0),
        Call(
            name="search", arguments={"q": "a-cut"}, ok=True, at=9.0, input_omitted=True
        ),
        _search("other", 9.0),
    ],
)
def test_one_span_and_one_call_of_a_tool_pair_however_each_tracer_spelled_the_arguments(
    native: Call,
) -> None:
    (merged,) = call_runner._merge_span_calls(
        [native], [_search_span("5", 1.0, "worker-a")]
    )
    assert (merged.worker_id, merged.runtime_ids) == (
        "worker-a",
        {"event_id": "event-5"},
    )


@pytest.mark.parametrize("reversed_spans", [True, False])
def test_two_spans_claiming_one_call_go_to_the_stronger_claim_and_a_tie_leaves_it_unknown(
    reversed_spans: bool,
) -> None:
    # The other tracer saw one call; the failed one never reached it.
    failed = _search_span(
        "a", 1.0, "worker-a", ok=False, result=None, input_omitted=True
    )
    spans = [failed, _search_span("b", 2.0, "worker-b")]
    if reversed_spans:
        spans.reverse()
    merged = call_runner._merge_span_calls([_search("b", 9.0)], spans)
    assert sorted(
        (
            (c.arguments, c.ok, c.worker_id, c.runtime_ids.get("event_id"))
            for c in merged
        ),
        key=str,
    ) == [
        ({"q": "a"}, False, "worker-a", "event-a"),
        ({"q": "b"}, True, "worker-b", "event-b"),
    ]
    tied = [
        _search_span("a", 1.0, "worker-a", input_omitted=True),
        _search_span("b", 2.0, "worker-b", input_omitted=True),
    ]
    if reversed_spans:
        tied.reverse()
    (merged,) = call_runner._merge_span_calls([_search("b", 9.0)], tied)
    assert (merged.worker_id, merged.runtime_ids) == ("", {})


@pytest.mark.parametrize("reversed_spans", [True, False])
def test_calls_the_same_in_all_but_time_pair_with_their_overlapping_spans_one_each(
    reversed_spans: bool,
) -> None:
    spans = [
        _search_span(
            "a", 1.0, "worker-a", ended_at=4.0, runtime_ids={"event_id": "e1"}
        ),
        _search_span(
            "a", 2.0, "worker-b", ended_at=3.0, runtime_ids={"event_id": "e2"}
        ),
    ]
    if reversed_spans:
        spans.reverse()
    merged = call_runner._merge_span_calls(
        [_search("a", 9.2), _search("a", 9.1)], spans
    )
    assert sorted((c.worker_id, c.runtime_ids["event_id"]) for c in merged) == [
        ("worker-a", "e1"),
        ("worker-b", "e2"),
    ]


def test_two_hundred_identical_calls_pair_in_well_under_a_second() -> None:
    import time

    natives = [_search("a", 9.0) for _ in range(200)]
    spans = [
        _search_span(
            "a", 1.0, f"worker-{i}", ended_at=2.0, runtime_ids={"event_id": f"e{i}"}
        )
        for i in range(200)
    ]
    started = time.perf_counter()
    merged = call_runner._merge_span_calls(natives, spans)
    assert time.perf_counter() - started < 0.5
    assert len(merged) == 200
    assert {c.runtime_ids["event_id"] for c in merged} == {f"e{i}" for i in range(200)}


_AGENTS_SDK = {
    "agents/__init__.py": "from agents.tracing import get_trace_provider\n\n"
    "def set_trace_processors(processors):\n    get_trace_provider().set_processors(processors)\n",
    "agents/tracing/__init__.py": "from agents.tracing.provider import DefaultTraceProvider\n_PROVIDER = DefaultTraceProvider()\n\n"
    "def get_trace_provider():\n    return _PROVIDER\n",
    "agents/tracing/provider.py": "class SynchronousMultiTracingProcessor:\n    _processors = ()\n\n"
    "    def set_processors(self, processors):\n        self._processors = tuple(processors)\n\n\n"
    "class DefaultTraceProvider:\n    def __init__(self):\n        self._multi_processor = SynchronousMultiTracingProcessor()\n\n"
    "    def set_processors(self, processors):\n        self._multi_processor.set_processors(processors)\n",
    "traceai_openai_agents/__init__.py": "class FiTracingProcessor:\n    pass\n\n\nclass OpenAIAgentsInstrumentor:\n"
    "    def instrument(self):\n        from agents import set_trace_processors\n"
    "        set_trace_processors([FiTracingProcessor()])\n",
}


@pytest.mark.parametrize(
    ("app", "kept"),
    [
        ("set_trace_processors([])", ["FiTracingProcessor"]),
        ("set_trace_processors([Own()])", ["Own", "FiTracingProcessor"]),
        (
            "from traceai_openai_agents import FiTracingProcessor\nset_trace_processors([FiTracingProcessor()])",
            ["FiTracingProcessor"],
        ),
    ],
)
def test_an_openai_agents_app_that_sets_its_own_trace_processors_is_still_observed_once(
    tmp_path: Path, app: str, kept: list[str]
) -> None:
    for path, source in {
        **_AGENTS_SDK,
        "hook/sitecustomize.py": "import simulation_mark_bootstrap\n"
        "simulation_mark_bootstrap.install()\n",
    }.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)
    _place_hook(tmp_path / "hook")
    script = (
        f"from agents import get_trace_provider, set_trace_processors\nclass Own: pass\n{app}\n"
        "print([type(p).__name__ for p in get_trace_provider()._multi_processor._processors])"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": f"{tmp_path / 'hook'}:{tmp_path}",
            "ALK_OBSERVE_FRAMEWORKS": "1",
            "HARNESS_TOOL_TRACE": str(tmp_path / "calls.jsonl"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "hook: failed" not in result.stderr
    assert json.loads(result.stdout.strip().replace("'", '"')) == kept


_AUTOGEN_CORE = {
    "autogen_core/__init__.py": "",
    "autogen_core/tools/__init__.py": "class BaseTool:\n    def __init__(self, name, run):\n"
    "        self.name, self._run = name, run\n\n"
    "    async def run_json(self, args, cancellation_token, call_id=None):\n        return self._run(**args)\n\n"
    "    def return_value_as_string(self, value):\n        return str(value)\n",
}


def test_an_autogen_tool_call_is_written_with_its_arguments_and_result_beside_its_span(
    tmp_path: Path,
) -> None:
    for path, source in {
        **_AUTOGEN_CORE,
        "hook/sitecustomize.py": "import simulation_mark_bootstrap\n"
        "simulation_mark_bootstrap.install()\n",
    }.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)
    _place_hook(tmp_path / "hook")
    script = textwrap.dedent("""
        import asyncio
        from autogen_core.tools import BaseTool
        def fail(note):
            raise ValueError("no such note")
        async def main():
            await BaseTool("look_up_tide", lambda place: "high at 06:10").run_json({"place": "North Quay"}, None)
            try:
                await BaseTool("read_note", fail).run_json({"note": "n1"}, None)
            except ValueError:
                pass
        asyncio.run(main())
    """)
    trace = tmp_path / "calls.jsonl"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={
            **os.environ,
            "PYTHONPATH": f"{tmp_path / 'hook'}:{tmp_path}",
            "ALK_OBSERVE_FRAMEWORKS": "1",
            "HARNESS_TOOL_TRACE": str(trace),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "hook: failed" not in result.stderr
    lines = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [
        (one["name"], one["arguments"], one["output"], one["is_error"]) for one in lines
    ] == [
        ("look_up_tide", {"place": "North Quay"}, "high at 06:10", False),
        ("read_note", {"note": "n1"}, "no such note", True),
    ]


def test_checks_grade_a_launch_once_and_a_hand_off_made_by_a_tool_call() -> None:
    from fi.alk.harness.world.runtime import graded_calls

    launch = Call(name="start_async_task", arguments={}, result=None, ok=True, at=1.0)
    job = Call(
        name="start_async_task",
        arguments={},
        result=None,
        ok=True,
        at=1.5,
        delegated_to="coder",
        runtime_ids={"event_id": "j1", "parent_event_id": "l1"},
    )
    task = Call(
        name="task",
        arguments={},
        result=None,
        ok=True,
        at=2.0,
        delegated_to="researcher",
        runtime_ids={"event_id": "t1", "parent_event_id": "a1"},
    )
    assert graded_calls([launch, job, task]) == [launch, task]


def test_a_job_joins_the_launch_that_named_its_whole_id() -> None:
    from fi.alk.harness.simulation_worker_events import _names

    assert _names('{"task_id": "1"}', "1")
    assert _names("job a1b2-c3 started", "a1b2-c3")
    assert not _names('{"task_id": "17"}', "1")
    assert not _names("thread_1x", "1")


def test_spans_that_never_end_do_not_stop_observation(monkeypatch) -> None:
    from types import SimpleNamespace

    from fi.alk.harness import simulation_worker_events as bootstrap

    monkeypatch.setattr(bootstrap, "_LIVE_SPANS_MAX", 2)
    observer = bootstrap._ToolSpans("/dev/null")

    def span(span_id):
        return SimpleNamespace(
            get_span_context=lambda: SimpleNamespace(span_id=span_id),
            parent=SimpleNamespace(span_id=0),
            attributes={},
            name="s",
            end=lambda: None,
        )

    for span_id in (1, 2, 3):
        observer.started(span(span_id))
    assert list(observer._live) == [2, 3]


def test_a_span_starting_or_ending_waits_for_another_thread_s_bookkeeping(
    tmp_path: Path,
) -> None:
    import threading
    from types import SimpleNamespace

    from fi.alk.harness import simulation_worker_events as bootstrap

    observer = bootstrap._ToolSpans(str(tmp_path / "calls.jsonl"))
    one = SimpleNamespace(
        get_span_context=lambda: SimpleNamespace(span_id=1),
        parent=None,
        attributes={},
        name="s",
        end=lambda: None,
        status=None,
        start_time=0,
        end_time=0,
    )
    for step in (lambda: observer.started(one), lambda: one.end()):
        with observer._lock:
            thread = threading.Thread(target=step)
            thread.start()
            thread.join(0.2)
            assert thread.is_alive()
        thread.join(5)
        assert not thread.is_alive()


def test_the_process_instrumenting_after_the_hook_with_its_own_provider_gets_it() -> (
    None
):
    from fi.alk.harness.simulation_mark_bootstrap import _yield_to_process

    class Instrumentor:
        # As opentelemetry's BaseInstrumentor: a second instrument() is ignored until uninstrument().
        is_instrumented_by_opentelemetry = False
        settings = None

        def instrument(self, **kwargs):
            if self.is_instrumented_by_opentelemetry:
                return
            self.settings, self.is_instrumented_by_opentelemetry = kwargs, True

        def uninstrument(self):
            self.is_instrumented_by_opentelemetry = False

    ours = Instrumentor()
    ours.instrument()
    _yield_to_process(Instrumentor)
    ours.instrument()
    assert ours.settings == {}
    ours.instrument(tracer_provider="own")
    assert ours.settings == {"tracer_provider": "own"}


def test_the_hook_turns_on_every_instrumentor_the_build_installs() -> None:
    from fi.alk.harness.generated_runtime import FRAMEWORK_INSTRUMENTORS

    built = {
        one.split("==")[0].replace("-", "_")
        for _framework, one in FRAMEWORK_INSTRUMENTORS
        if one.startswith("traceai")
    }
    assert built == {
        module for module, _cls in simulation_mark_bootstrap._INSTRUMENTORS.values()
    }


def test_a_framework_whose_instrumentor_is_missing_is_reported_unobserved(
    tmp_path: Path,
) -> None:
    hook = tmp_path / "hook"
    hook.mkdir()
    _place_hook(hook)
    (hook / "sitecustomize.py").write_text(
        "import simulation_mark_bootstrap\nsimulation_mark_bootstrap.install()\n"
    )
    (hook / "smolagents.py").write_text("")
    result = subprocess.run(
        [sys.executable, "-c", "import smolagents"],
        env={
            **os.environ,
            "PYTHONPATH": str(hook),
            "HARNESS_TOOL_TRACE": str(tmp_path / "calls.jsonl"),
            "ALK_OBSERVE_FRAMEWORKS": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert (
        "simulation mark hook: unobserved smolagents, its instrumentor is not installed"
        in result.stderr
    )


def test_two_open_hand_offs_to_one_worker_each_hold_their_own_calls(
    tmp_path: Path,
) -> None:
    # The coordinator asks the same worker twice at once; the two runs end in the other order.
    _run(
        tmp_path,
        """
        from opentelemetry.trace import set_span_in_context
        lead = tracer.start_span("Lead", attributes={"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Lead"})
        asks, runs, searches = [], [], []
        for query in ("q1", "q2"):
            ask = tracer.start_span("ask_research", context=set_span_in_context(lead), attributes={
                "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_research", "input.value": query})
            run = tracer.start_span("Research", context=set_span_in_context(ask), attributes={
                "gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Research"})
            searches.append(tracer.start_span("search_notes", context=set_span_in_context(run), attributes={
                "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes", "input.value": query}))
            asks.append(ask)
            runs.append(run)
        for one in (1, 0):
            searches[one].end()
            runs[one].end()
            asks[one].end()
        lead.end()
    """,
    )
    rows = _sealed(tmp_path)
    asks = {
        row["arguments"]["raw"]: row for row in rows if row["name"] == "ask_research"
    }
    searches = {
        row["arguments"]["raw"]: row for row in rows if row["name"] == "search_notes"
    }
    for query in ("q1", "q2"):
        assert _placed([asks[query], searches[query]]) == [
            ("ask_research", "delegated", "Lead", "observed", ""),
            (
                "search_notes",
                "tool_call",
                "Research",
                "observed",
                asks[query]["span_id"],
            ),
        ]
    assert asks["q1"]["span_id"] != asks["q2"]["span_id"]


_ROOT_TRANSFER = """
from opentelemetry.trace import set_span_in_context
root = tracer.start_span("invoke_agent root_agent", attributes={"gen_ai.operation.name": "invoke_agent"})
root.set_attribute("gen_ai.agent.name", "root_agent")
transfer = tracer.start_span("execute_tool transfer_to_agent", context=set_span_in_context(root), attributes={
    "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "transfer_to_agent",
    "gen_ai.tool.type": "TOOL_TYPE", "gcp.vertex.agent.tool_call_args": '{"agent_name": "leaf_agent"}'})
transfer.end()
leaf = tracer.start_span("invoke_agent leaf_agent", context=set_span_in_context(root), attributes={
    "gen_ai.operation.name": "invoke_agent"})
# As the framework does, the agent is named once its span has started.
leaf.set_attribute("gen_ai.agent.name", "leaf_agent")
with trace.use_span(leaf, end_on_exit=True):
    with span("execute_tool look_up_tide", **{"gen_ai.operation.name": "execute_tool",
                                              "gen_ai.tool.name": "look_up_tide"}):
        pass
root.end()
"""


def test_a_framework_s_own_transfer_is_a_control_pass_and_holds_the_leaf_worker_s_calls(
    tmp_path: Path,
) -> None:
    records = _run(tmp_path, _ROOT_TRANSFER.replace("TOOL_TYPE", "TransferToAgentTool"))
    transfer = next(r for r in records if r["name"] == "transfer_to_agent")
    assert (transfer["transfer"], transfer["delegated_to"], "ended_at" in transfer) == (
        True,
        "leaf_agent",
        False,
    )
    assert _placed(_sealed(tmp_path)) == [
        ("transfer_to_agent", "transferred", "root_agent", "observed", ""),
        ("look_up_tide", "tool_call", "leaf_agent", "observed", transfer["event_id"]),
    ]


def test_adk_s_built_in_transfer_recorded_as_a_plain_function_is_a_control_pass_once_the_named_agent_runs(
    tmp_path: Path,
) -> None:
    # Older ADK releases record their own transfer_to_agent as a FunctionTool; the named agent then starting in the
    # same run is the evidence that control passed.
    records = _run(tmp_path, _ROOT_TRANSFER.replace("TOOL_TYPE", "FunctionTool"))
    transfer = next(r for r in records if r["name"] == "transfer_to_agent")
    assert (transfer.get("transfer"), transfer["delegated_to"]) == (True, "leaf_agent")
    assert _placed(_sealed(tmp_path)) == [
        ("transfer_to_agent", "transferred", "root_agent", "observed", ""),
        ("look_up_tide", "tool_call", "leaf_agent", "observed", transfer["event_id"]),
    ]


def test_a_transfer_named_call_whose_agent_never_runs_is_a_tool_call_and_the_leaf_s_place_stays_unknown(
    tmp_path: Path,
) -> None:
    # The call names an agent that never starts: nothing records a control pass, so it stays an ordinary tool call
    # and the agent that did run is not placed under it.
    _run(
        tmp_path,
        _ROOT_TRANSFER.replace("TOOL_TYPE", "FunctionTool").replace(
            '{"agent_name": "leaf_agent"}', '{"agent_name": "billing_agent"}'
        ),
    )
    assert _placed(_sealed(tmp_path)) == [
        ("transfer_to_agent", "tool_call", "root_agent", "observed", ""),
        ("look_up_tide", "tool_call", "leaf_agent", "unknown", ""),
    ]


def test_the_top_agent_under_the_caller_s_context_hands_off_at_the_top_level(
    tmp_path: Path,
) -> None:
    # The app's coordinator runs inside a context its own tracer never recorded (the request that reached it): the
    # coordinator is the top of the app's work, so its hand-off is observed at the top level.
    _run(
        tmp_path,
        """
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context
        outside = NonRecordingSpan(SpanContext(trace_id=7, span_id=99, is_remote=True, trace_flags=TraceFlags(1)))
        lead = tracer.start_span("Lead", context=set_span_in_context(outside), attributes={
            "gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Lead"})
        ask = tracer.start_span("ask_research", context=set_span_in_context(lead), attributes={
            "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_research", "input.value": "q"})
        run = tracer.start_span("Research", context=set_span_in_context(ask), attributes={
            "gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Research"})
        run.end()
        ask.end()
        lead.end()
    """,
    )
    assert _placed(_sealed(tmp_path)) == [
        ("ask_research", "delegated", "Lead", "observed", ""),
    ]


def test_a_call_with_no_agent_above_it_has_an_unknown_place(tmp_path: Path) -> None:
    _run(
        tmp_path,
        """
        with span("execute_tool search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes",
                                              "gen_ai.agent.name": "research_worker"}):
            pass
    """,
    )
    assert _placed(_sealed(tmp_path)) == [
        ("search_notes", "tool_call", "research_worker", "unknown", "")
    ]


def test_an_agent_the_framework_records_as_handed_control_holds_its_calls(
    tmp_path: Path,
) -> None:
    # The OpenAI Agents SDK runs each agent at the top of the trace and names, on the agent handed control, the agent
    # that handed it, only when that agent's span ends.
    records = _run(
        tmp_path,
        """
        with span("Triage", **{"fi.span.kind": "AGENT", "graph.node.id": "Triage"}):
            with span("lookup", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
                pass
        with span("Refunds", **{"fi.span.kind": "AGENT", "graph.node.id": "Refunds"}) as refunds:
            with span("issue_refund", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": "issue_refund"}):
                pass
            refunds.set_attribute("graph.node.parent_id", "Triage")
    """,
    )
    passed = next(r for r in records if r.get("transfer"))
    rows = sorted(_sealed(tmp_path), key=lambda row: row["name"])
    assert _placed(rows) == [
        ("Refunds", "transferred", "Triage", "unknown", ""),
        ("issue_refund", "tool_call", "Refunds", "observed", passed["event_id"]),
        ("lookup", "tool_call", "Triage", "observed", ""),
    ]
    assert "ended_at" not in rows[0]


def test_an_agent_handed_control_inside_an_openai_agents_run_holds_its_calls(
    tmp_path: Path,
) -> None:
    # The same hand-off as traceAI records a real Runner.run: both agents sit under the run's name-less agent span.
    records = _run(
        tmp_path,
        """
        from opentelemetry.trace import set_span_in_context
        run = tracer.start_span("Agent workflow", attributes={"gen_ai.span.kind": "AGENT"})
        steps = tracer.start_span("Agent workflow", context=set_span_in_context(run),
                                  attributes={"gen_ai.span.kind": "CHAIN"})
        with trace.use_span(steps):
            with span("Triage", **{"fi.span.kind": "AGENT", "graph.node.id": "Triage"}):
                with span("lookup", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
                    pass
            with span("Refunds", **{"fi.span.kind": "AGENT", "graph.node.id": "Refunds"}) as refunds:
                with span("issue_refund", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": "issue_refund"}):
                    pass
                refunds.set_attribute("graph.node.parent_id", "Triage")
        steps.end()
        run.end()
    """,
    )
    passed = next(r for r in records if r.get("transfer"))
    refund = next(row for row in _sealed(tmp_path) if row["name"] == "issue_refund")
    assert (refund["parent_span_id"], refund["relationship"]) == (
        passed["event_id"],
        "observed",
    )


def test_each_payload_says_whether_part_of_it_was_left_out(tmp_path: Path) -> None:
    records = _run(
        tmp_path,
        """
        with span("Worker", **{"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Worker"}):
            with span("long_in", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "long_in",
                                    "input.value": "a" * 5000, "output.value": "ok"}):
                pass
            with span("long_out", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "long_out",
                                     "input.value": "{}", "output.value": "b" * 5000}):
                pass
            with span("fits", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "fits",
                                 "input.value": "{}", "output.value": "ok"}):
                pass
    """,
    )
    assert {
        r["name"]: (r.get("input_omitted", False), r.get("output_omitted", False))
        for r in records
    } == {
        "long_in": (True, False),
        "long_out": (False, True),
        "fits": (False, False),
    }
    assert {
        row["name"]: (row.get("input_omitted", False), row.get("output_omitted", False))
        for row in _sealed(tmp_path)
    } == {"long_in": (True, False), "long_out": (False, True), "fits": (False, False)}


def test_a_parent_no_recorded_hand_off_carries_is_unknown_not_kept() -> None:
    # The hand-off a call ran in had not ended when the call did, so no line records it.
    leaf = Call(
        name="search_notes",
        arguments={},
        worker_id="Research",
        runtime_ids={"event_id": "b"},
        parent_span_id="a",
        relationship="observed",
        at=1.0,
    )
    (merged,) = call_runner._merge_span_calls([], [leaf])
    assert {
        key: trace_row(merged)[key] for key in ("parent_span_id", "relationship")
    } == {"parent_span_id": "", "relationship": "unknown"}


_OPENAI_AGENTS_RUN = """
from opentelemetry.trace import set_span_in_context
# The OpenAI Agents SDK opens each run as an agent-kind span with no agent name; agents run inside it.
# Between them sit the SDK's own run and turn spans, as traceAI records a real Runner.run.
run = tracer.start_span("Agent workflow", attributes={"gen_ai.span.kind": "AGENT"})
steps = tracer.start_span("Agent workflow", context=set_span_in_context(run), attributes={"gen_ai.span.kind": "CHAIN"})
lead = tracer.start_span("Lead", context=set_span_in_context(steps), attributes={"gen_ai.span.kind": "AGENT"})
turn = tracer.start_span("turn", context=set_span_in_context(lead), attributes={"gen_ai.span.kind": "CHAIN"})
ask = tracer.start_span("ask_research", context=set_span_in_context(turn), attributes={
    "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_research", "input.value": "q"})
worker = tracer.start_span("Research", context=set_span_in_context(ask), attributes={"gen_ai.span.kind": "AGENT"})
worker.end()
ask.end()
turn.end()
lead.end()
LATER
steps.end()
run.end()
"""


def test_the_first_agent_in_an_openai_agents_run_hands_off_at_the_top_level(
    tmp_path: Path,
) -> None:
    _run(tmp_path, _OPENAI_AGENTS_RUN.replace("LATER", ""))
    assert _placed(_sealed(tmp_path)) == [
        ("ask_research", "delegated", "Lead", "observed", ""),
    ]


def test_a_later_agent_in_the_same_run_without_a_recorded_transfer_stays_unknown(
    tmp_path: Path,
) -> None:
    # A second agent opened in the run was handed control; until the framework records who handed it, its calls are
    # not placed at the top.
    later = """
billing = tracer.start_span("Billing", context=set_span_in_context(steps), attributes={"gen_ai.span.kind": "AGENT"})
with trace.use_span(billing, end_on_exit=True):
    with span("refund", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "refund"}):
        pass
"""
    _run(tmp_path, _OPENAI_AGENTS_RUN.replace("LATER", later))
    refund = next(r for r in _sealed(tmp_path) if r["name"] == "refund")
    assert refund["relationship"] == "unknown"


def test_a_transfer_recorded_by_another_instrumentation_counts_once_the_named_agent_runs_under_a_model_call(
    tmp_path: Path,
) -> None:
    # traceAI's ADK instrumentation records the transfer as a plain tool with its arguments in `input.value`, under the
    # model call; the named agent then runs inside the root agent with no tool calls of its own.
    _run(
        tmp_path,
        """
        from opentelemetry.trace import set_span_in_context
        root = tracer.start_span("root_agent", attributes={"fi.span.kind": "AGENT", "gen_ai.agent.name": "root_agent"})
        llm = tracer.start_span("call_llm", context=set_span_in_context(root), attributes={"fi.span.kind": "LLM"})
        move = tracer.start_span("transfer_to_agent", context=set_span_in_context(llm), attributes={
            "fi.span.kind": "TOOL", "tool.name": "transfer_to_agent", "input.value": '{"agent_name": "leaf_agent"}'})
        move.end()
        llm.end()
        leaf = tracer.start_span("leaf_agent", context=set_span_in_context(root), attributes={
            "fi.span.kind": "AGENT", "gen_ai.agent.name": "leaf_agent"})
        leaf.end()
        root.end()
    """,
    )
    assert _placed(_sealed(tmp_path)) == [
        ("transfer_to_agent", "transferred", "root_agent", "observed", ""),
    ]


def test_an_adk_transfer_without_its_arguments_hands_control_to_the_agent_that_runs_next_in_the_workflow(
    tmp_path: Path,
) -> None:
    # ADK 2 records the transfer tool without its arguments unless content capture is on, ends the root agent, then
    # runs the agent it transferred to beside it in the same workflow span.
    _run(
        tmp_path,
        """
        from opentelemetry.trace import set_span_in_context
        flow = tracer.start_span("invoke_workflow root_agent", attributes={"gen_ai.operation.name": "invoke_workflow",
                                                                         "gen_ai.span.kind": "CHAIN"})
        root = tracer.start_span("invoke_agent root_agent", context=set_span_in_context(flow), attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "root_agent", "gen_ai.span.kind": "AGENT"})
        move = tracer.start_span("execute_tool transfer_to_agent", context=set_span_in_context(root), attributes={
            "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "transfer_to_agent",
            "gen_ai.tool.type": "TransferToAgentTool", "gen_ai.span.kind": "TOOL"})
        move.end()
        root.end()
        leaf = tracer.start_span("invoke_agent leaf_agent", context=set_span_in_context(flow), attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "leaf_agent", "gen_ai.span.kind": "AGENT"})
        with trace.use_span(leaf, end_on_exit=True):
            with span("execute_tool look_up_tide", **{"gen_ai.operation.name": "execute_tool",
                                                      "gen_ai.tool.name": "look_up_tide", "gen_ai.span.kind": "TOOL"}):
                pass
        flow.end()
    """,
    )
    rows = _sealed(tmp_path)
    transfer = next(r for r in rows if r["name"] == "transfer_to_agent")
    assert transfer["delegated_to"] == "leaf_agent"
    assert _placed(rows) == [
        ("transfer_to_agent", "transferred", "root_agent", "observed", ""),
        ("look_up_tide", "tool_call", "leaf_agent", "observed", transfer["span_id"]),
    ]


_ADK_UNNAMED_TRANSFER = """
from opentelemetry.trace import set_span_in_context
flow = tracer.start_span("invoke_workflow root_agent", attributes={"gen_ai.operation.name": "invoke_workflow",
                                                                 "gen_ai.span.kind": "CHAIN"})
BEFORE
root = tracer.start_span("invoke_agent root_agent", context=set_span_in_context(flow), attributes={
    "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "root_agent", "gen_ai.span.kind": "AGENT"})
move = tracer.start_span("execute_tool transfer_to_agent", context=set_span_in_context(root), attributes={
    "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "transfer_to_agent",
    "gen_ai.tool.type": "TransferToAgentTool", "gen_ai.span.kind": "TOOL"})
move.end()
root.end()
AFTER
with trace.use_span(other, end_on_exit=True):
    with span("execute_tool look_up_tide", **{"gen_ai.operation.name": "execute_tool",
                                              "gen_ai.tool.name": "look_up_tide", "gen_ai.span.kind": "TOOL"}):
        pass
flow.end()
"""


def test_an_adk_transfer_without_its_arguments_is_not_taken_by_an_agent_that_started_before_it(
    tmp_path: Path,
) -> None:
    started = """
other = tracer.start_span("invoke_agent side_agent", context=set_span_in_context(flow), attributes={
    "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "side_agent", "gen_ai.span.kind": "AGENT"})
"""
    _run(
        tmp_path, _ADK_UNNAMED_TRANSFER.replace("BEFORE", started).replace("AFTER", "")
    )
    rows = _sealed(tmp_path)
    transfer = next(r for r in rows if r["name"] == "transfer_to_agent")
    tide = next(r for r in rows if r["name"] == "look_up_tide")
    assert "delegated_to" not in transfer
    assert tide["parent_span_id"] != transfer["span_id"]


def test_a_later_agent_under_an_adk_workflow_with_no_transfer_into_it_has_an_unknown_place(
    tmp_path: Path,
) -> None:
    later = """
leaf = tracer.start_span("invoke_agent leaf_agent", context=set_span_in_context(flow), attributes={
    "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "leaf_agent", "gen_ai.span.kind": "AGENT"})
leaf.end()
other = tracer.start_span("invoke_agent other_agent", context=set_span_in_context(flow), attributes={
    "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "other_agent", "gen_ai.span.kind": "AGENT"})
"""
    _run(tmp_path, _ADK_UNNAMED_TRANSFER.replace("BEFORE", "").replace("AFTER", later))
    tide = next(r for r in _sealed(tmp_path) if r["name"] == "look_up_tide")
    assert (tide["parent_span_id"], tide["relationship"]) == ("", "unknown")


def test_a_call_under_an_agent_whose_parent_gave_way_to_newer_spans_has_an_unknown_place(
    tmp_path: Path,
) -> None:
    _run(
        tmp_path,
        """
        import simulation_worker_events
        from opentelemetry.trace import set_span_in_context
        simulation_worker_events._LIVE_SPANS_MAX = 3
        lead = tracer.start_span("Lead", attributes={"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Lead"})
        held = [tracer.start_span(f"step {n}", context=set_span_in_context(lead), attributes={
            "gen_ai.span.kind": "CHAIN"}) for n in range(2)]
        worker = tracer.start_span("Research", context=set_span_in_context(lead), attributes={
            "gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Research"})
        with trace.use_span(worker, end_on_exit=True):
            with span("search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes"}):
                pass
        for one in held:
            one.end()
        lead.end()
    """,
    )
    search = next(r for r in _sealed(tmp_path) if r["name"] == "search_notes")
    assert search["relationship"] == "unknown"


def test_held_transfers_are_bounded_across_one_agent_s_calls(tmp_path: Path) -> None:
    _run(
        tmp_path,
        """
        import simulation_worker_events
        from opentelemetry.trace import set_span_in_context
        simulation_worker_events._LAUNCHES_MAX = 2
        root = tracer.start_span("invoke_agent root_agent", attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "root_agent", "gen_ai.span.kind": "AGENT"})
        for _ in range(3):
            tracer.start_span("execute_tool transfer_to_agent", context=set_span_in_context(root), attributes={
                "gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "transfer_to_agent",
                "gen_ai.tool.type": "TransferToAgentTool", "gen_ai.span.kind": "TOOL"}).end()
        root.end()
    """,
    )
    assert sorted(row["kind"] for row in _sealed(tmp_path)) == [
        "tool_call",
        "transferred",
        "transferred",
    ]


def test_an_adk_transfer_without_its_arguments_is_not_taken_by_the_agent_that_made_it(
    tmp_path: Path,
) -> None:
    again = """
other = tracer.start_span("invoke_agent root_agent", context=set_span_in_context(flow), attributes={
    "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "root_agent", "gen_ai.span.kind": "AGENT"})
"""
    _run(tmp_path, _ADK_UNNAMED_TRANSFER.replace("BEFORE", "").replace("AFTER", again))
    rows = _sealed(tmp_path)
    transfer = next(r for r in rows if r["name"] == "transfer_to_agent")
    tide = next(r for r in rows if r["name"] == "look_up_tide")
    assert "delegated_to" not in transfer
    assert tide["parent_span_id"] != transfer["span_id"]
