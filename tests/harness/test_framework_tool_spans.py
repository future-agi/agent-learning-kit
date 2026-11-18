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
    simulation_measure,
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


def test_a_served_coordinator_s_own_calls_and_its_jobs_hand_offs_name_the_same_agent(
    tmp_path: Path,
) -> None:
    # As a graph server traces a deep agent: the coordinator's run and its tools node carry no span kind and no agent
    # name, only the conversation; its jobs name their own agent. The launch is the coordinator's call, the same
    # agent its job's hand-off names, and a job's tools stay the job's.
    records = _run(
        tmp_path,
        """
        sup = {"gen_ai.conversation.id": "thread-sup"}
        with span("agent", **sup):
            with span("tools", **{"langgraph_node": "tools", **sup}):
                with span("start_async_task", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "start_async_task",
                                                 "langgraph_node": "tools", "langgraph_checkpoint_ns": "tools:1",
                                                 "input.value": "researcher", **sup,
                                                 "output.value": "Launched async subagent. task_id: job-r"}):
                    pass
        own = {"lc_agent_name": "researcher", "gen_ai.conversation.id": "job-r"}
        with span("researcher", **{"output.value": "done", **own}):
            with span("tools", **{"langgraph_node": "tools", **own}):
                with span("analyze_topic", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "analyze_topic",
                                              "langgraph_node": "tools", "langgraph_checkpoint_ns": "tools:2", **own}):
                    pass
    """,
    )
    launch = next(
        r for r in records if r["name"] == "start_async_task" and not r.get("delegated_to")
    )
    hand_off = next(r for r in records if r.get("delegated_to") == "researcher" and "ended_at" in r)
    assert launch.get("agent_key") == hand_off["agent_key"] == "agent"
    assert hand_off["parent_event_id"] == launch["event_id"]
    assert next(r for r in records if r["name"] == "analyze_topic")["agent_key"] == "researcher"


def test_a_call_in_a_run_with_no_conversation_and_no_agent_stays_unnamed(
    tmp_path: Path,
) -> None:
    # Outside a served conversation the run's name is only a span name (a request handler, a chain), not an agent.
    records = _run(
        tmp_path,
        """
        with span("POST /chat"):
            with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
                pass
    """,
    )
    assert [r.get("agent_key") for r in records if r["name"] == "lookup"] == [None]


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
        # The Task span is the hand-off and the sub-agent's run at once.
        "Task": (None, "researcher"),
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


def test_a_voice_agent_s_livekit_session_spans_are_never_served_read_or_exported(tmp_path: Path) -> None:
    """Observation is on in a voice agent too; LiveKit's own tracer (`livekit-agents`) stays as it was, a no-op with no
    provider, so its session and tool spans never reach the trace or an exporter. A framework's tracer is served."""
    records = _run(
        tmp_path,
        """
        livekit = trace.get_tracer("livekit-agents")
        with livekit.start_as_current_span("function_tool", attributes={
            "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "issue_refund", "lk.function_tool.name": "issue_refund",
        }) as session_span:
            assert not session_span.is_recording()
        with span("Refund worker", **{"gen_ai.span.kind": "AGENT", "agent.name": "Refund worker"}):
            with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
                pass
    """,
        provider="",
    )
    assert _by_tool(records) == {"lookup": ("Refund worker", None)}


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


@pytest.mark.parametrize("native_worker", ["", "from span"])
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
def test_calls_the_other_tracer_never_stamped_are_not_paired_by_order(
    reversed_spans: bool,
) -> None:
    natives = [_search("a", 0.0), _search("b", 0.0)]
    spans = [
        _search_span(
            "a", 1.0, "worker-a", ended_at=1.4, input_omitted=True, result=None
        ),
        _search_span(
            "b", 2.0, "worker-b", ended_at=2.4, input_omitted=True, result=None
        ),
    ]
    if reversed_spans:
        spans.reverse()
    merged = call_runner._merge_span_calls(natives, spans)
    assert sorted((c.arguments["q"], c.worker_id, c.runtime_ids) for c in merged) == [
        ("a", "", {}),
        ("b", "", {}),
    ]


def test_a_span_that_cannot_be_told_apart_lends_its_worker_to_no_call_when_fewer_spans_than_calls() -> (
    None
):
    # One of the calls may be one no span saw, such as the coordinator's own.
    natives = [_search("a", 9.1), _search("b", 9.2)]
    spans = [_search_span("a", 1.0, "worker-a", input_omitted=True, result=None)]
    merged = call_runner._merge_span_calls(natives, spans)
    assert [(c.arguments["q"], c.worker_id) for c in merged] == [("a", ""), ("b", "")]


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
            result=None,
            delegated_to="reader",
        ),
        _search_span(
            "b",
            2.0,
            "worker-b",
            ended_at=3.0,
            input_omitted=True,
            result=None,
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


@pytest.mark.parametrize(
    "span",
    [
        _search_span("b", 9.0, "worker-b", ok=False, result="search failed"),
        _search_span("a", 9.0, "worker-b", ok=False, result="search failed"),
    ],
    ids=["arguments-differ", "outcome-differs"],
)
def test_one_span_and_one_call_that_disagree_are_two_calls_each_tracer_missed_one(
    span: Call,
) -> None:
    native = _search("a", 1.0)
    merged = call_runner._merge_span_calls([native], [span])
    assert sorted(
        (c.arguments["q"], c.ok, c.worker_id, c.runtime_ids.get("event_id"))
        for c in merged
    ) == sorted(
        [
            ("a", True, "", None),
            (span.arguments["q"], False, "worker-b", span.runtime_ids["event_id"]),
        ]
    )


def test_a_span_whose_only_fitting_call_ended_otherwise_is_kept_as_its_own_call() -> (
    None
):
    natives = [_search("a", 1.0), _search("c", 2.0)]
    spans = [
        _search_span("a", 1.0, "worker-a"),
        _search_span("c", 2.0, "worker-b", ok=False, result="search failed"),
    ]
    merged = call_runner._merge_span_calls(natives, spans)
    assert sorted((c.arguments["q"], c.ok, c.worker_id) for c in merged) == [
        ("a", True, "worker-a"),
        ("c", False, "worker-b"),
        ("c", True, ""),
    ]


def test_spans_paired_by_order_must_agree_with_their_calls_on_the_outcome() -> None:
    natives = [
        Call(name="search", arguments={"raw": "x"}, result="r1", ok=True, at=10.0),
        Call(name="search", arguments={"raw": "y"}, result="r2", ok=True, at=20.0),
    ]
    spans = [
        Call(
            name="search",
            arguments={"raw": "x"},
            result="r1",
            ok=True,
            at=1.0,
            ended_at=2.0,
            worker_id="w",
        ),
        Call(
            name="search",
            arguments={"raw": "y"},
            result="boom",
            ok=False,
            at=3.0,
            ended_at=4.0,
            worker_id="w",
        ),
    ]
    merged = call_runner._merge_span_calls(natives, spans)
    assert sorted((c.result, c.ok) for c in merged) == [
        ("boom", False),
        ("r1", True),
        ("r2", True),
    ]


def _calls(merged) -> list[tuple]:
    return sorted(
        (
            c.name,
            c.ok,
            str(c.result),
            c.worker_id,
            c.runtime_ids.get("event_id") or "",
            c.delegated_to,
        )
        for c in merged
    )


@pytest.mark.parametrize(
    ("native", "span"),
    [
        (
            Call(name="send_email", arguments={"to": "a"}, ok=True, at=1.0),
            Call(
                name="send_email",
                arguments={"to": "a"},
                ok=False,
                error="boom",
                worker_id="B",
                at=1.0,
                runtime_ids={"event_id": "sB"},
            ),
        ),
        (
            _search("a", 1.0, worker_id="A"),
            _search_span("a", 1.0, "B"),
        ),
        (
            Call(name="delegate", arguments={}, ok=True, delegated_to="X", at=1.0),
            Call(
                name="delegate",
                arguments={},
                ok=True,
                delegated_to="Y",
                worker_id="root",
                at=1.0,
                runtime_ids={"event_id": "d1"},
            ),
        ),
        (
            Call(name="researcher", arguments={}, ok=True, at=1.0),
            Call(
                name="researcher",
                arguments={},
                ok=True,
                worker_id="researcher",
                handoff=True,
                at=1.0,
                runtime_ids={"event_id": "r1"},
            ),
        ),
        (
            _search("a", 1.0, result="first"),
            _search_span("a", 5.0, "B", result="second"),
        ),
    ],
    ids=["void-outcome", "worker", "hand-off-target", "kind", "output"],
)
def test_a_span_and_a_call_that_disagree_on_any_recorded_field_are_two_calls(
    native: Call, span: Call
) -> None:
    expected = _calls([native, span])
    assert _calls(call_runner._merge_span_calls([native], [span])) == expected


def test_order_pairs_only_calls_that_ended_the_same_way() -> None:
    # Each tracer missed a different call: three calls happened.
    natives = [
        Call(name="roll", arguments={}, result="b", at=10.0),
        Call(name="roll", arguments={}, result="c", at=20.0),
    ]
    spans = [
        Call(
            name="roll", arguments={}, result="a", worker_id="A", at=1.0, ended_at=1.5
        ),
        Call(
            name="roll", arguments={}, result="b", worker_id="B", at=2.0, ended_at=2.5
        ),
    ]
    assert [
        (c.result, c.worker_id)
        for c in sorted(
            call_runner._merge_span_calls(natives, spans), key=lambda c: c.result
        )
    ] == [("a", "A"), ("b", "B"), ("c", "")]


@pytest.mark.parametrize("natives", [1, 2])
def test_spans_that_outnumber_the_calls_left_are_each_kept_without_a_guessed_owner(
    natives: int,
) -> None:
    calls = [_search("a", float(at)) for at in range(1, natives + 1)]
    spans = [
        _search_span("a", 1.0 + at / 10, worker, runtime_ids={"event_id": f"s{at}"})
        for at, worker in enumerate("ABC"[: natives + 1])
    ]
    merged = call_runner._merge_span_calls(calls, spans)
    assert len(merged) == natives + 1
    assert {c.worker_id for c in merged} == {""}
    assert {c.runtime_ids.get("event_id") for c in merged} == {None}


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
        _search_span("a", 1.0, "worker-a", input_omitted=True, result=None),
        _search_span("b", 2.0, "worker-b", input_omitted=True, result=None),
    ]
    if reversed_spans:
        tied.reverse()
    # Two spans are two calls; which is the one the other tracer saw, nothing says.
    merged = call_runner._merge_span_calls([_search("b", 9.0)], tied)
    assert [(c.worker_id, c.runtime_ids) for c in merged] == [("", {}), ("", {})]


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
    # About 0.3 s on a laptop and up to three times that on a shared CI runner; a pairing that grew a power would take
    # far longer than this bound.
    assert time.perf_counter() - started < 2.0
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


def test_a_smolagents_tool_that_uses_a_module_helper_never_fails_the_call_through_the_instrumentor(
    tmp_path: Path,
) -> None:
    # traceai-smolagents 0.1.6 serialises a call's arguments with each object's to_dict(); smolagents' to_dict() on a
    # tool whose code uses a module-level helper raises its self-contained check. The guard records that tool by name.
    files = {
        "smolagents/__init__.py": "",
        "traceai_smolagents/__init__.py": "from traceai_smolagents._wrappers import SmolagentsInstrumentor\n",
        "traceai_smolagents/_wrappers.py": textwrap.dedent("""
            def _to_dict(result):
                if hasattr(result, "to_dict"):
                    return result.to_dict()
                if isinstance(result, list):
                    return [_to_dict(item) for item in result]
                return result

            class SmolagentsInstrumentor:
                is_instrumented_by_opentelemetry = False

                def instrument(self, **kwargs):
                    self.is_instrumented_by_opentelemetry = True

                def uninstrument(self):
                    self.is_instrumented_by_opentelemetry = False
        """),
        "hook/sitecustomize.py": "import simulation_mark_bootstrap\nsimulation_mark_bootstrap.install()\n",
    }
    for path, source in files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)
    _place_hook(tmp_path / "hook")
    script = textwrap.dedent("""
        import smolagents
        from traceai_smolagents import _wrappers

        def _ledger(order_id):
            return order_id

        class GetOrder:
            name = "get_order"

            def to_dict(self):
                raise ValueError("SimpleTool validation failed for get_order: Name '_ledger' is undefined.")

        class Plain:
            def to_dict(self):
                return {"plain": True}

        print(_wrappers._to_dict([GetOrder(), Plain(), "order 1043"]))
    """)
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
    assert result.stdout.strip() == "['get_order', {'plain': True}, 'order 1043']"


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
    """A framework's instrumentor is turned on by the mark hook, a model SDK's by the measuring
    module beside it; between them, every one the build installs."""
    from fi.alk.harness.generated_runtime import FRAMEWORK_INSTRUMENTORS

    built = {
        one.split("==")[0].replace("-", "_")
        for _framework, one in FRAMEWORK_INSTRUMENTORS
        if one.startswith("traceai")
    }
    frameworks = {
        module for module, _cls in simulation_mark_bootstrap._INSTRUMENTORS.values()
    }
    sdks = {module for module, _cls in simulation_measure.SDK_INSTRUMENTORS.values()}
    assert not frameworks & sdks
    assert built == frameworks | sdks


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


def test_a_worker_still_out_when_the_process_stops_keeps_its_hand_off_with_no_return(
    tmp_path: Path,
) -> None:
    # The OpenAI Agents SDK ends a hand-off's span only when the worker returns: Stop while the worker is out leaves
    # the tool span open. The hand-off written when the worker started is kept, with no end and no answer.
    _run(
        tmp_path,
        """
        from opentelemetry.trace import set_span_in_context
        lead = tracer.start_span("Lead", attributes={"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Lead"})
        ask = tracer.start_span("refund", context=set_span_in_context(lead), attributes={
            "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "refund", "input.value": "order 1042"})
        run = tracer.start_span("Refund worker", context=set_span_in_context(ask), attributes={
            "gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Refund worker"})
        with trace.use_span(run):
            with span("list_refunds", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "list_refunds"}):
                pass
    """,
    )
    rows = _sealed(tmp_path)
    (hand_off,) = [row for row in rows if row["name"] == "refund"]
    lookup = next(row for row in rows if row["name"] == "list_refunds")
    assert (hand_off["delegated_to"], "ended_at" in hand_off, bool(hand_off["result"])) == ("Refund worker", False, False)
    assert _placed([hand_off, lookup]) == [
        ("refund", "delegated", "Lead", "observed", ""),
        ("list_refunds", "tool_call", "Refund worker", "observed", hand_off["span_id"]),
    ]


def test_a_worker_the_openai_agents_instrumentor_names_only_by_its_span_keeps_its_hand_off_at_stop(
    tmp_path: Path,
) -> None:
    # traceai-openai-agents 0.1.6 starts an agent span named after the agent with an empty input and sets
    # gen_ai.agent.name only when the span ends; a worker still out at Stop never gets that attribute.
    _run(
        tmp_path,
        """
        from opentelemetry.trace import set_span_in_context
        lead = tracer.start_span("Lead", attributes={"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Lead"})
        ask = tracer.start_span("refund", context=set_span_in_context(lead), attributes={
            "gen_ai.span.kind": "TOOL", "input.value": '{"name": "refund", "input": "order 1042"}'})
        run = tracer.start_span("Refund worker", context=set_span_in_context(ask), attributes={
            "gen_ai.span.kind": "AGENT", "gen_ai.provider.name": "openai", "input.value": "{}"})
        with trace.use_span(run):
            with span("list_refunds", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "list_refunds"}):
                pass
    """,
    )
    rows = _sealed(tmp_path)
    (hand_off,) = [row for row in rows if row["name"] == "refund"]
    lookup = next(row for row in rows if row["name"] == "list_refunds")
    assert (hand_off["delegated_to"], "ended_at" in hand_off) == ("Refund worker", False)
    assert lookup["parent_span_id"] == hand_off["span_id"]


def test_a_hand_off_written_at_its_start_is_replaced_by_the_line_written_when_the_worker_returns(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        with span("Lead", **{"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Lead"}):
            with span("refund", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "refund",
                                   "output.value": "refund r-1"}):
                with span("Refund worker", **{"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Refund worker"}):
                    pass
    """,
    )
    assert [(r.get("opened", False), "ended_at" in r) for r in records if r["name"] == "refund"] == [
        (True, False),
        (False, True),
    ]
    (hand_off,) = [row for row in _sealed(tmp_path) if row["name"] == "refund"]
    assert (hand_off["delegated_to"], bool(hand_off.get("ended_at"))) == ("Refund worker", True)


def test_a_worker_named_only_after_the_tool_that_ran_it_writes_no_hand_off_at_its_start(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        """
        from opentelemetry.trace import set_span_in_context
        lead = tracer.start_span("Lead", attributes={"gen_ai.span.kind": "AGENT", "gen_ai.agent.name": "Lead"})
        ask = tracer.start_span("refund", context=set_span_in_context(lead), attributes={
            "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "refund"})
        tracer.start_span("agent.run", context=set_span_in_context(ask), attributes={"gen_ai.span.kind": "AGENT"})
    """,
    )
    assert [r for r in records if r.get("opened")] == []


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


def test_a_root_agent_that_hands_off_to_two_top_level_agents_has_two_workers_each_under_its_transfer(
    tmp_path: Path,
) -> None:
    turn = _ROOT_TRANSFER.replace("TOOL_TYPE", "TransferToAgentTool")
    second = turn.replace("leaf_agent", "billing_agent").replace("look_up_tide", "look_up_invoice")
    records = _run(tmp_path, turn + second)
    first_pass, second_pass = (r["event_id"] for r in records if r["name"] == "transfer_to_agent")
    assert _placed(_sealed(tmp_path)) == [
        ("transfer_to_agent", "transferred", "root_agent", "observed", ""),
        ("look_up_tide", "tool_call", "leaf_agent", "observed", first_pass),
        ("transfer_to_agent", "transferred", "root_agent", "observed", ""),
        ("look_up_invoice", "tool_call", "billing_agent", "observed", second_pass),
    ]


# A LangGraph graph as traceai-langchain 0.2.1 records it: every attribute is set only when the span ends, a run whose name
# says "agent" is labelled an agent, and a hand-off tool's metadata names the agent that takes over.
_GRAPH = """
from opentelemetry.trace import set_span_in_context
graph = tracer.start_span("LangGraph")

def node(name, parent):
    return tracer.start_span(name, context=set_span_in_context(parent))

def call(parent, tool, agent, step, **extra):
    one = node(tool, parent)
    one.set_attributes({"fi.span.kind": "AGENT" if "agent" in tool else "TOOL", "tool.name": tool,
                        "lc_agent_name": agent, "langgraph_node": "tools",
                        "langgraph_checkpoint_ns": agent + ":" + step + "|tools:" + step, **extra})
    one.end()

def turn(agent, step, tool, **extra):
    run = node(agent, graph)
    tools = node("tools", run)
    call(tools, tool, agent, step, **extra)
    tools.end()
    run.end()
"""


def test_a_langgraph_hand_off_tool_is_a_transfer_that_holds_the_named_agent_s_calls(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        _GRAPH
        + """
turn("supervisor", "1", "transfer_to_billing_agent", __handoff_destination="billing_agent")
turn("billing_agent", "2", "look_up_invoice")
graph.end()
""",
    )
    handed = next(r for r in records if r["name"] == "transfer_to_billing_agent")
    assert (handed["transfer"], handed["delegated_to"]) == (True, "billing_agent")
    assert _placed(_sealed(tmp_path)) == [
        ("transfer_to_billing_agent", "transferred", "supervisor", "unknown", ""),
        ("look_up_invoice", "tool_call", "billing_agent", "observed", handed["event_id"]),
    ]


def test_an_agent_handed_control_again_holds_its_later_calls_under_the_latest_transfer(
    tmp_path: Path,
) -> None:
    records = _run(
        tmp_path,
        _GRAPH
        + """
turn("triage", "1", "transfer_to_billing", __handoff_destination="billing")
turn("billing", "2", "transfer_to_triage", __handoff_destination="triage")
turn("triage", "3", "close_ticket")
graph.end()
""",
    )
    back = next(r for r in records if r["name"] == "transfer_to_triage")
    rows = {row["name"]: row for row in _sealed(tmp_path)}
    assert (rows["close_ticket"]["agent"], rows["close_ticket"]["parent_span_id"]) == ("triage", back["event_id"])


def test_a_supervisor_s_hand_off_whose_worker_comes_back_to_it_is_a_hand_off_that_returned(
    tmp_path: Path,
) -> None:
    # langgraph-supervisor adds an edge from each worker back to the supervisor: the supervisor's node running again
    # after its worker's is the worker returning, so the transfer reads as a hand-off with its return.
    records = _run(
        tmp_path,
        _GRAPH
        + """
turn("supervisor", "1", "transfer_to_billing_agent", __handoff_destination="billing_agent")
turn("billing_agent", "2", "look_up_invoice")
turn("supervisor", "3", "close_ticket")
graph.end()
""",
    )
    handed = next(r for r in records if r["name"] == "transfer_to_billing_agent" and "ended_at" in r)
    assert "transfer" not in handed and handed["delegated_to"] == "billing_agent"
    rows = _sealed(tmp_path)
    assert _placed(rows)[:2] == [
        ("transfer_to_billing_agent", "delegated", "supervisor", "unknown", ""),
        ("look_up_invoice", "tool_call", "billing_agent", "observed", handed["event_id"]),
    ]
    assert [one["type"] for one in rows[0]["worker_events"]] == ["delegated", "returned"]


def test_a_swarm_agent_handed_control_back_is_passed_control_not_returned_to(tmp_path: Path) -> None:
    # Each agent of a swarm passes control on; the first agent running again because control was passed back to it is
    # no return, so both transfers stay control passes with nothing returned.
    _run(
        tmp_path,
        _GRAPH
        + """
turn("triage", "1", "transfer_to_billing", __handoff_destination="billing")
turn("billing", "2", "transfer_to_triage", __handoff_destination="triage")
turn("triage", "3", "close_ticket")
graph.end()
""",
    )
    rows = [row for row in _sealed(tmp_path) if row["name"].startswith("transfer_to_")]
    assert [row["kind"] for row in rows] == ["transferred", "transferred"]
    assert all("returned" not in [one["type"] for one in row["worker_events"]] for row in rows)


def test_a_graph_agent_no_transfer_named_keeps_an_unknown_place(tmp_path: Path) -> None:
    _run(
        tmp_path,
        _GRAPH
        + """
turn("supervisor", "1", "transfer_to_billing", __handoff_destination="billing")
turn("reviewer", "2", "read_notes")
graph.end()
""",
    )
    rows = {row["name"]: row for row in _sealed(tmp_path)}
    assert (rows["read_notes"]["agent"], rows["read_notes"]["relationship"]) == ("reviewer", "unknown")


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


def test_a_control_pass_made_through_a_tool_is_written_once_as_the_transfer(
    tmp_path: Path,
) -> None:
    # The agent handed control runs inside the tool that passed it, and the framework names who passed it.
    _run(
        tmp_path,
        """
        with span("Triage", **{"fi.span.kind": "AGENT", "graph.node.id": "Triage"}):
            with span("transfer_to_refunds", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": "transfer_to_refunds"}):
                with span("Refunds", **{"fi.span.kind": "AGENT", "graph.node.id": "Refunds"}) as refunds:
                    with span("issue_refund", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": "issue_refund"}):
                        pass
                    refunds.set_attribute("graph.node.parent_id", "Triage")
    """,
    )
    rows = _sealed(tmp_path)
    passes = [row for row in rows if row.get("delegated_to")]
    assert [(row["name"], row["kind"], row["agent"]) for row in passes] == [
        ("transfer_to_refunds", "transferred", "Triage")
    ]
    assert "ended_at" not in passes[0]


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


@pytest.mark.parametrize(
    "livekit_tracer",
    [
        # LiveKit Cloud observability on: LiveKit records on a provider of its own.
        'from opentelemetry.sdk.trace import TracerProvider as _Own\nlivekit = _Own().get_tracer("livekit-agents")',
        # The process set a global provider, which LiveKit's tracer then records on.
        'livekit = trace.get_tracer("livekit-agents")',
    ],
)
def test_a_voice_turn_run_inside_livekit_s_own_recorded_spans_hands_off_at_the_top_level(
    tmp_path: Path, livekit_tracer: str
) -> None:
    """A voice agent answering a turn inside LiveKit's `llm_node`, with LiveKit's session spans recorded: the app's run
    opens under them. Those spans are LiveKit's, never the app's, so the run's first agent still hands off at the top,
    as the same app does over chat."""
    voice = """
""" + livekit_tracer + """
session = livekit.start_span("agent_session")
turn_node = livekit.start_span("llm_node", context=set_span_in_context(session))
with trace.use_span(turn_node):
""" + "\n".join("    " + line for line in _OPENAI_AGENTS_RUN.replace("LATER", "").splitlines())
    voice = voice.replace("    from opentelemetry.trace import set_span_in_context", "    pass")
    voice = "from opentelemetry.trace import set_span_in_context\n" + voice + "\nturn_node.end()\nsession.end()\n"
    _run(tmp_path, voice)
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


def test_spans_count_only_against_the_calls_they_could_be_and_an_unrelated_call_keeps_no_owner() -> None:
    # Two spans prove two q=a calls by worker A; the other tracer saw one q=a and one unrelated q=b.
    natives = [
        Call(name="search", arguments={"q": "a"}, ok=True, result="r", at=1.0),
        Call(name="search", arguments={"q": "b"}, ok=True, result="s", at=2.0),
    ]
    spans = [
        Call(
            name="search", arguments={"q": "a"}, ok=True, result="r", worker_id="A", worker_label="A",
            at=1.0, ended_at=5.0, runtime_ids={"event_id": "s1"},
        ),
        Call(
            name="search", arguments={"q": "a"}, ok=True, result="r", worker_id="A", worker_label="A",
            at=1.2, ended_at=5.0, runtime_ids={"event_id": "s2"},
        ),
    ]
    merged = call_runner._merge_span_calls(natives, spans)
    assert sorted((c.arguments["q"], c.worker_id) for c in merged) == [("a", "A"), ("a", "A"), ("b", "")]


@pytest.mark.parametrize(
    "native, span",
    [
        # A framework's hashed key on one side, its role name on the other: one worker.
        (
            {"worker_id": "5f0b6ebc4e2a3f1c9d8e7a6b5c4d3e2f", "worker_label": "Researcher"},
            {"worker_id": "Researcher", "worker_label": "Researcher"},
        ),
        # The role spelled differently by two tracers.
        ({"worker_id": "", "worker_label": "Order lookup"}, {"worker_id": "order_lookup", "worker_label": ""}),
    ],
    ids=["hashed-key-vs-role", "spelling"],
)
def test_one_worker_named_differently_by_two_tracers_is_one_call(native, span) -> None:
    merged = call_runner._merge_span_calls(
        [Call(name="lookup", arguments={"q": "a"}, ok=True, result="found", at=10.0, **native)],
        [
            Call(
                name="lookup", arguments={"q": "a"}, ok=True, result="found", at=10.0, ended_at=10.5,
                runtime_ids={"event_id": "sp1"}, **span,
            )
        ],
    )
    assert len(merged) == 1


def test_a_call_named_by_its_worker_label_never_takes_another_worker_s_place() -> None:
    merged = call_runner._merge_span_calls(
        [Call(name="search", arguments={"q": "a"}, ok=True, result="r", worker_label="A", at=1.0)],
        [
            Call(
                name="search", arguments={"q": "a"}, ok=True, result="r", worker_id="B", worker_label="B",
                at=1.0, runtime_ids={"event_id": "sB"}, parent_span_id="hB", relationship="observed",
            ),
            Call(
                name="ask_b", arguments={}, ok=True, delegated_to="B", at=0.5, ended_at=3.0,
                runtime_ids={"event_id": "hB"},
            ),
        ],
    )
    searches = sorted(
        (c.worker_label, c.runtime_ids.get("event_id"), c.parent_span_id) for c in merged if c.name == "search"
    )
    assert searches == [("A", None, ""), ("B", "sB", "hB")]


# Two workers, order_lookup and refund, under one coordinator, as each framework's pinned instrumentor shapes the spans
# (the shapes read from traceai-claude-agent-sdk 0.1.1, traceai-pydantic-ai 0.1.0, traceai-smolagents 0.1.6,
# traceai-llamaindex 0.1.10 and Strands' own telemetry).
_TWO_WORKERS = {
    "claude_agent_sdk": """
        from opentelemetry.trace import set_span_in_context
        root = tracer.start_span("claude_agent.conversation", attributes={"claude_agent.span_kind": "conversation"})
        for worker, tools in (("order_lookup", ("get_order", "list_refunds")), ("refund", ("issue_refund",))):
            # The SDK's hook starts the Task span, then sets what it is.
            task = tracer.start_span("tool.Task", context=set_span_in_context(root))
            task.set_attribute("claude_agent.span_kind", "subagent")
            task.set_attribute("claude_agent.tool.name", "Task")
            task.set_attribute("claude_agent.subagent.type", worker)
            task.set_attribute("claude_agent.tool.input", '{"subagent_type": "%s"}' % worker)
            for tool in tools:
                call = tracer.start_span("tool.mcp__%s__%s" % (worker, tool), context=set_span_in_context(task))
                call.set_attribute("claude_agent.span_kind", "tool_execution")
                call.set_attribute("claude_agent.tool.name", "mcp__%s__%s" % (worker, tool))
                call.set_attribute("claude_agent.tool.input", '{"order_id": "1043"}')
                call.set_attribute("claude_agent.tool.output", "ok")
                call.end()
            task.set_attribute("claude_agent.tool.output", worker + " done")
            task.end()
        root.end()
    """,
    "pydantic_ai": """
        lead = {"pydantic_ai.span_kind": "agent_run", "pydantic_ai.agent.name": "returns_coordinator"}
        with span("agent_run", **lead):
            for worker, tools in (("order_lookup", ("get_order", "list_refunds")), ("refund", ("issue_refund",))):
                with span("tool_call." + worker, **{"pydantic_ai.span_kind": "tool_call",
                                                    "pydantic_ai.tool.name": worker,
                                                    "pydantic_ai.tool.args": '{"order_id": "1043"}'}):
                    with span("agent_run", **{"pydantic_ai.span_kind": "agent_run",
                                              "pydantic_ai.agent.name": worker}):
                        for tool in tools:
                            with span("tool_call." + tool, **{"pydantic_ai.span_kind": "tool_call",
                                                              "pydantic_ai.tool.name": tool,
                                                              "pydantic_ai.tool.args": '{"order_id": "1043"}',
                                                              "pydantic_ai.tool.result": "ok"}):
                                pass
    """,
    # smolagents names an agent's span by its class only; each worker is a Tool that runs it, so the worker takes the
    # name of the tool that handed it the work.
    "smolagents": """
        with span("CodeAgent.run", **{"gen_ai.span.kind": "AGENT"}):
            for worker, tools in (("order_lookup", ("get_order", "list_refunds")), ("refund", ("issue_refund",))):
                with span("WorkerTool", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": worker,
                                           "input.value": '{"task": "order 1043"}'}):
                    with span("ToolCallingAgent.run", **{"gen_ai.span.kind": "AGENT"}):
                        for tool in tools:
                            with span("SimpleTool", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": tool,
                                                       "input.value": '{"order_id": "1043"}'}):
                                pass
    """,
    # LlamaIndex's workflow agents carry no agent name either: each worker is a FunctionTool that runs it.
    "llama_index": """
        with span("FunctionAgent.run", **{"fi.span.kind": "AGENT"}):
            for worker, tools in (("order_lookup", ("get_order", "list_refunds")), ("refund", ("issue_refund",))):
                with span("FunctionTool.acall", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": worker,
                                                   "input.value": '{"order_id": "1043"}'}):
                    with span("FunctionAgent.run", **{"fi.span.kind": "AGENT"}):
                        for tool in tools:
                            with span("FunctionTool.acall", **{"fi.span.kind": "TOOL", "gen_ai.tool.name": tool,
                                                               "input.value": '{"order_id": "1043"}'}):
                                pass
    """,
    "strands": """
        with span("invoke_agent returns_coordinator", **{"gen_ai.operation.name": "invoke_agent",
                                                       "gen_ai.agent.name": "returns_coordinator"}):
            for worker, tools in (("order_lookup", ("get_order", "list_refunds")), ("refund", ("issue_refund",))):
                with span("execute_tool " + worker, **{"gen_ai.operation.name": "execute_tool",
                                                       "gen_ai.tool.name": worker}):
                    with span("invoke_agent " + worker, **{"gen_ai.operation.name": "invoke_agent",
                                                           "gen_ai.agent.name": worker}):
                        for tool in tools:
                            with span("execute_tool " + tool, **{"gen_ai.operation.name": "execute_tool",
                                                                 "gen_ai.tool.name": tool}):
                                pass
    """,
}
# The tool each framework's worker calls is recorded under the name its instrumentor gives it.
_WORKER_TOOLS = {
    "claude_agent_sdk": {
        "order_lookup": ["mcp__order_lookup__get_order", "mcp__order_lookup__list_refunds"],
        "refund": ["mcp__refund__issue_refund"],
    },
}


@pytest.mark.parametrize("framework", sorted(_TWO_WORKERS))
def test_each_framework_s_two_workers_are_handed_off_to_and_own_their_tool_calls(tmp_path: Path, framework) -> None:
    _run(tmp_path, _TWO_WORKERS[framework])
    rows = _sealed(tmp_path)
    hand_offs = {row["delegated_to"]: row for row in rows if row.get("delegated_to")}
    assert set(hand_offs) == {"order_lookup", "refund"}
    assert all(row.get("ended_at") for row in hand_offs.values())
    tools = _WORKER_TOOLS.get(
        framework, {"order_lookup": ["get_order", "list_refunds"], "refund": ["issue_refund"]}
    )
    for worker, names in tools.items():
        owned = [row for row in rows if row["name"] in names]
        assert [row["name"] for row in owned] == names
        for row in owned:
            assert (row["agent"], row["relationship"], row["parent_span_id"]) == (
                worker,
                "observed",
                hand_offs[worker]["span_id"],
            )


def test_a_nul_byte_in_a_span_s_worker_name_or_payload_is_dropped(tmp_path: Path) -> None:
    _run(
        tmp_path,
        r"""
        from opentelemetry.trace import set_span_in_context
        root = tracer.start_span("claude_agent.conversation", attributes={"claude_agent.span_kind": "conversation"})
        task = tracer.start_span("tool.Task", context=set_span_in_context(root))
        task.set_attribute("claude_agent.span_kind", "subagent")
        task.set_attribute("claude_agent.subagent.type", "ref\x00und")
        call = tracer.start_span("tool.issue_refund", context=set_span_in_context(task))
        call.set_attribute("claude_agent.span_kind", "tool_execution")
        call.set_attribute("claude_agent.tool.name", "issue_refund")
        call.set_attribute("claude_agent.tool.input", '{"order_id": "10\x0043"}')
        call.end(); task.end(); root.end()
        """,
    )
    rows = _sealed(tmp_path)
    assert [row["delegated_to"] for row in rows if row.get("delegated_to")] == ["refund"]
    assert [row["agent"] for row in rows if row["name"] == "issue_refund"] == ["refund"]
    assert "\\u0000" not in json.dumps(rows)


def test_a_hand_off_the_observer_cannot_write_is_said_once_never_read_as_none(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from fi.alk.harness import call_runner as cr
    from fi.alk.harness import simulation_worker_events as bootstrap

    path = tmp_path / "calls.jsonl"
    observer = bootstrap._ToolSpans(str(path))

    def broken(*_args, **_kwargs):
        raise RuntimeError("could not write")

    monkeypatch.setattr(observer, "_returned_to", broken)
    for span_id in (1, 2):
        observer.started(
            SimpleNamespace(
                get_span_context=lambda span_id=span_id: SimpleNamespace(span_id=span_id),
                parent=None,
                attributes={},
                name="s",
                end=lambda: None,
                status=None,
                start_time=0,
                end_time=0,
            )
        )
    lines = [json.loads(one) for one in path.read_text().splitlines()]
    assert [one["kind"] for one in lines] == [bootstrap.HAND_OFF_UNRECORDED]

    runtime = SimpleNamespace(metadata={"tool_trace_path": str(path)})
    [notice] = cr.hand_off_notices(runtime)
    assert notice["code"] == cr.HAND_OFFS_INCOMPLETE
    # The marker is not a call, and not an unreadable line that would fail the capture.
    assert cr._read_file_tool_calls(runtime, strict=True) == ([], [])
    assert cr.HAND_OFF_UNRECORDED == bootstrap.HAND_OFF_UNRECORDED


def test_a_trace_that_cannot_be_read_marks_the_trial_s_hand_offs_incomplete(tmp_path, monkeypatch) -> None:
    from pathlib import Path
    from types import SimpleNamespace

    from fi.alk.harness import call_runner as cr

    path = tmp_path / "calls.jsonl"
    path.write_text("", encoding="utf-8")
    runtime = SimpleNamespace(metadata={"tool_trace_path": str(path)})
    assert cr.hand_off_notices(runtime) == []

    def unreadable(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", unreadable)
    [notice] = cr.hand_off_notices(runtime)
    assert notice["code"] == cr.HAND_OFFS_INCOMPLETE


@pytest.mark.parametrize("start", ["spawn", "forkserver", "fork"])
def test_a_voice_worker_s_job_process_records_the_hand_offs_its_agents_make(tmp_path: Path, start: str) -> None:
    """A LiveKit worker runs each call in a job process it starts. With the voice bundle's environment and the hook
    laid out as the sandbox lays it out for a LiveKit agent, the framework's instrumentor is turned on in the job
    process, and the hand-off it reports is in the trace."""
    from fi.alk.harness import livekit_tool_trace_bootstrap

    hook = tmp_path / "hook"
    hook.mkdir()
    _place_hook(hook)
    (hook / "sitecustomize.py").write_bytes(Path(livekit_tool_trace_bootstrap.__file__).read_bytes())
    site = tmp_path / "site"
    site.mkdir()
    # A framework whose agents hand off only through its instrumentor's spans, as the OpenAI Agents SDK's do.
    (site / "langchain_core.py").write_text("def hand_off():\n    pass\n")
    (site / "traceai_langchain.py").write_text(
        textwrap.dedent(
            """
            from opentelemetry import trace

            class LangChainInstrumentor:
                def instrument(self):
                    import langchain_core

                    def hand_off():
                        tracer = trace.get_tracer("traceai")
                        def span(name, **attributes):
                            return tracer.start_as_current_span(name, attributes=attributes)
                        with span("Returns desk", **{"gen_ai.span.kind": "AGENT", "graph.node.id": "Returns desk"}):
                            with span("ask_refund", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_refund"}):
                                with span("Refund worker", **{"gen_ai.span.kind": "AGENT",
                                                              "graph.node.id": "Refund worker"}):
                                    with span("issue_refund", **{"gen_ai.span.kind": "TOOL",
                                                                 "gen_ai.tool.name": "issue_refund",
                                                                 "input.value": '{"order": 7}',
                                                                 "output.value": "issued"}):
                                        pass

                    langchain_core.hand_off = hand_off
            """
        )
    )
    (site / "job.py").write_text(
        textwrap.dedent(
            """
            def run_call():
                from opentelemetry import trace
                from opentelemetry.sdk.trace import TracerProvider

                trace.set_tracer_provider(TracerProvider())
                import langchain_core

                langchain_core.hand_off()
                trace.get_tracer_provider().force_flush()
                # The LiveKit worker's own line for the same tool call, as its hook writes it.
                import json, os, time

                with open(os.environ["HARNESS_TOOL_TRACE"], "a") as trace_file:
                    trace_file.write(json.dumps({"name": "issue_refund", "arguments": {"order": 7}, "output": "issued",
                                                 "is_error": False, "at": time.time(), "source": "livekit"}) + "\\n")
            """
        )
    )
    (tmp_path / "worker.py").write_text(
        textwrap.dedent(
            f"""
            import multiprocessing, sys
            import job

            if __name__ == "__main__":
                process = multiprocessing.get_context({start!r}).Process(target=job.run_call)
                process.start()
                process.join(30)
                sys.exit(process.exitcode)
            """
        )
    )
    trace = tmp_path / "calls.jsonl"

    def hand_offs(observe: str) -> list[dict]:
        trace.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(tmp_path / "worker.py")],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join([str(hook), str(site)]),
                "HARNESS_TOOL_TRACE": str(trace),
                "ALK_OBSERVE_FRAMEWORKS": observe,
            },
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert result.returncode == 0, result.stderr
        records = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        return [one for one in records if one.get("delegated_to")]

    # The voice bundle's agent environment (ALK_OBSERVE_FRAMEWORKS) turns the instrumentor on in the job process.
    # (A hand-off is written when the worker starts and again when it returns.)
    assert {one["delegated_to"] for one in hand_offs("1")} == {"Refund worker"}
    # The tool call the LiveKit hook and the framework's span both report is counted once, under its worker.
    sealed = _sealed(tmp_path)
    assert [row["name"] for row in sealed if row["name"] == "issue_refund"] == ["issue_refund"]
    # Without it, as voice bundles were built before, the framework reports nothing: no hand-off.
    assert hand_offs("0") == []


_ADK_TRAVEL_APP = '''
import asyncio
from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.adk.tools.agent_tool import AgentTool
from google.genai import types


class Scripted(BaseLlm):
    script: list = []
    step: int = 0

    async def generate_content_async(self, llm_request, stream=False):
        kind, value = self.script[min(self.step, len(self.script) - 1)]
        self.step += 1
        part = (
            types.Part(function_call=types.FunctionCall(name=value[0], args=value[1]))
            if kind == "call"
            else types.Part(text=value)
        )
        yield LlmResponse(content=types.Content(role="model", parts=[part]))


def memorize(key: str, value: str) -> dict:
    return {"status": "stored"}


poi = LlmAgent(name="poi_agent", model=Scripted(model="poi", script=[("say", "Trams.")]), instruction="poi")
booking = LlmAgent(name="booking_agent", model=Scripted(model="book", script=[("say", "Booked.")]), instruction="b")
planning = LlmAgent(
    name="planning_agent",
    model=Scripted(model="plan", script=[
        ("call", ("memorize", {"key": "origin", "value": "San Francisco"})),
        ("call", ("transfer_to_agent", {"agent_name": "booking_agent"})),
    ]),
    instruction="plan",
    tools=[memorize],
)
root = LlmAgent(
    name="root_agent",
    model=Scripted(model="root", script=[
        ("call", ("poi_agent", {"request": "ideas"})),
        ("call", ("transfer_to_agent", {"agent_name": "planning_agent"})),
    ]),
    instruction="root",
    tools=[AgentTool(agent=poi)],
    sub_agents=[planning, booking],
)


async def main():
    from opentelemetry import trace

    runner = InMemoryRunner(agent=root, app_name="travel")
    session = await runner.session_service.create_session(app_name="travel", user_id="u")
    message = types.Content(role="user", parts=[types.Part(text="plan a trip")])
    async for _ in runner.run_async(user_id="u", session_id=session.id, new_message=message):
        pass
    provider = trace.get_tracer_provider()
    if hasattr(provider, "force_flush"):
        provider.force_flush()


asyncio.run(main())
'''


def test_adk_s_transfers_and_agent_tool_calls_are_read_as_hand_offs_beside_the_calls_the_chat_reports(
    tmp_path: Path,
) -> None:
    """A real ADK 2.7.1 app, as the travel concierge runs: a root agent asks an AgentTool, then transfers control, and
    the agent it handed to transfers again. ADK's own transfer spans keep no arguments, while the chat reports each
    `transfer_to_agent` with the agent it named: every transfer keeps the agent it handed control to."""
    pytest.importorskip("google.adk")
    hook = tmp_path / "hook"
    hook.mkdir()
    _place_hook(hook)
    (hook / "sitecustomize.py").write_text("import simulation_mark_bootstrap\nsimulation_mark_bootstrap.install()\n")
    (tmp_path / "app.py").write_text(_ADK_TRAVEL_APP)
    result = subprocess.run(
        [sys.executable, str(tmp_path / "app.py")],
        env={
            **os.environ,
            "PYTHONPATH": str(hook),
            "HARNESS_TOOL_TRACE": str(tmp_path / "calls.jsonl"),
            "ALK_OBSERVE_FRAMEWORKS": "1",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    runtime = EnvironmentRuntime(
        runtime_id="rt-1",
        world_index=0,
        bundle_digest="sha256:" + "0" * 64,
        state=RuntimeState.READY,
        endpoints={},
        metadata={"tool_trace_path": str(tmp_path / "calls.jsonl")},
    )
    # What the chat itself reported: each transfer with the agent it named, and no span of its own.
    reported = [
        Call(name="transfer_to_agent", arguments={"agent_name": name}, result={"result": None}, at=at)
        for at, name in ((1.0, "planning_agent"), (2.0, "booking_agent"))
    ]
    sealed = [trace_row(call) for call in call_runner.collect_seam_calls(runtime, None, observed=reported)]
    transfers = {row["arguments"]["agent_name"]: row for row in sealed if row["name"] == "transfer_to_agent"}
    assert set(transfers) == {"planning_agent", "booking_agent"}
    assert {name: (row.get("delegated_to"), row.get("agent")) for name, row in transfers.items()} == {
        "planning_agent": ("planning_agent", "root_agent"),
        "booking_agent": ("booking_agent", "planning_agent"),
    }
    # The AgentTool call is still a hand-off that returns, and a worker's own call keeps its worker.
    assert any(row["name"] == "poi_agent" for row in sealed)
    assert next(row for row in sealed if row["name"] == "memorize")["agent"] == "planning_agent"
