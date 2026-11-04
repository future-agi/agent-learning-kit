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

from fi.alk.harness import call_runner, simulation_mark_bootstrap
from fi.alk.harness.world.runtime import Call

_PRELUDE = """
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Status, StatusCode
{provider}
tracer = trace.get_tracer("probe")

def span(name, **attributes):
    return tracer.start_as_current_span(name, attributes=attributes)
"""


def _run(tmp_path: Path, body: str, *, provider: str = "trace.set_tracer_provider(TracerProvider())") -> list[dict]:
    hook = tmp_path / "hook"
    hook.mkdir()
    (hook / "simulation_mark_bootstrap.py").write_bytes(Path(simulation_mark_bootstrap.__file__).read_bytes())
    (hook / "sitecustomize.py").write_text("import simulation_mark_bootstrap\nsimulation_mark_bootstrap.install()\n")
    trace = tmp_path / "calls.jsonl"
    script = _PRELUDE.format(provider=provider) + textwrap.dedent(body)
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": str(hook), "HARNESS_TOOL_TRACE": str(trace), "ALK_OBSERVE_FRAMEWORKS": "1"},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "hook: failed" not in result.stderr
    if not trace.exists():
        return []
    return [json.loads(line) for line in trace.read_text().splitlines()]


def _by_tool(records: list[dict]) -> dict[str, tuple]:
    return {r["name"]: (r.get("agent_key"), r.get("delegated_to")) for r in records}


def test_a_tool_call_names_the_agent_above_it_and_a_hand_off_names_the_worker(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        with span("Lead agent", **{"gen_ai.span.kind": "AGENT", "graph.node.id": "Lead agent"}):
            with span("ask_research", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_research"}):
                with span("Research worker", **{"gen_ai.span.kind": "AGENT", "graph.node.id": "Research worker"}):
                    with span("search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes",
                                              "input.value": '{"query": "q"}', "output.value": "found"}):
                        pass
    """)
    assert _by_tool(records) == {"search_notes": ("Research worker", None), "ask_research": ("Lead agent", "Research worker")}
    lookup = next(r for r in records if r["name"] == "search_notes")
    assert (lookup["arguments"], lookup["output"], lookup["is_error"]) == ('{"query": "q"}', "found", False)


def test_a_langgraph_tool_is_the_subgraph_above_the_node_that_ran_it(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        with span("tools", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "publish_note",
                              "gen_ai.agent.name": "tools", "langgraph_node": "tools",
                              "langgraph_checkpoint_ns": "writer_worker:1|tools:2"}):
            pass
        with span("tools", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup",
                              "langgraph_node": "tools", "langgraph_checkpoint_ns": "tools:3"}):
            pass
    """)
    assert _by_tool(records) == {"publish_note": ("writer_worker", None), "lookup": (None, None)}


def test_a_background_job_is_a_hand_off_from_the_tool_call_that_launched_it(tmp_path: Path) -> None:
    # A supervisor starts two jobs; each runs later as its own run (no parent here), named by the id its launch
    # returned, with its tools attributed to the job's agent. A status check that echoes the id is not the launch.
    records = _run(tmp_path, """
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
    """)
    launches = {r["output"].rsplit(" ", 1)[-1]: r for r in records if r["name"] == "start_async_task" and not r.get("delegated_to")}
    hand_offs = {r["delegated_to"]: r for r in records if r.get("delegated_to")}
    assert {r["name"]: r.get("agent_key") for r in records if r["name"] in ("analyze_topic", "run_code_check")} == {
        "analyze_topic": "researcher", "run_code_check": "coder",
    }
    assert sorted(hand_offs) == ["coder", "researcher"]
    for worker, job in (("researcher", "job-r"), ("coder", "job-c")):
        hand_off = hand_offs[worker]
        assert (hand_off["name"], hand_off["agent_key"], hand_off["output"]) == ("start_async_task", "agent", worker + " done")
        assert hand_off["parent_event_id"] == launches[job]["event_id"]
        assert hand_off["arguments"] == worker
        assert 0 < hand_off["at"] <= hand_off["ended_at"]


def test_a_job_s_answer_is_what_its_model_last_said_not_its_whole_conversation(tmp_path: Path) -> None:
    records = _run(tmp_path, """
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
    """)
    assert [r["output"] for r in records if r.get("delegated_to") and "ended_at" in r] == ["<the answer>"]


def test_a_job_never_joins_a_launch_from_an_earlier_call(tmp_path: Path) -> None:
    records = _run(tmp_path, """
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
    """)
    assert [(r["name"], r.get("delegated_to")) for r in records] == [("run_code_check", None)]


def test_a_job_still_running_when_the_call_ends_keeps_its_hand_off(tmp_path: Path) -> None:
    # The job starts and makes a tool call, but has not finished when the process stops.
    records = _run(tmp_path, """
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
    """)
    hand_offs = [r for r in records if r.get("delegated_to")]
    assert [(r["delegated_to"], r["arguments"], "ended_at" in r) for r in hand_offs] == [("coder", "coder", False)]
    assert {r["name"]: r.get("agent_key") for r in records if r["name"] == "run_code_check"} == {"run_code_check": "coder"}


def test_a_sub_agent_run_through_a_tool_is_its_hand_off_and_owns_its_tool_calls(tmp_path: Path) -> None:
    # Deep Agents' `task` tool runs a sub-agent inside the supervisor's `tools` node. The tool span is marked a tool
    # only when it ends; the sub-agent's nodes sit under the host node in the namespace and name their agent. A tool
    # inside a named agent whose own inner run names the same agent is not a hand-off.
    records = _run(tmp_path, """
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
    """)
    assert _by_tool(records) == {"ls": ("researcher", None), "task": (None, "researcher"), "summarise": ("writer", None)}


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
    records = _run(tmp_path, """
sup = {"gen_ai.conversation.id": "thread-sup"}
own = {"lc_agent_name": "coder", "gen_ai.conversation.id": "job-c"}
agent = tracer.start_span("agent", attributes={"gen_ai.span.kind": "AGENT", **sup})
launch = tracer.start_span("start_async_task", context=trace.set_span_in_context(agent), attributes={
    "gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "start_async_task", "input.value": "coder", **sup})
job = tracer.start_span("coder", context=trace.set_span_in_context(trace.INVALID_SPAN))
""" + (inner + launched if inner_first else launched + inner) + "agent.end()\n")
    hand_offs = [r for r in records if r.get("delegated_to")]
    assert [(r["delegated_to"], r["agent_key"], "ended_at" in r) for r in hand_offs] == [("coder", "agent", False)]
    assert {r["name"]: r.get("agent_key") for r in records if r["name"] == "run_code_check"} == {"run_code_check": "coder"}


def test_a_run_whose_conversation_no_tool_returned_is_not_a_hand_off(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        with span("agent", **{"gen_ai.span.kind": "AGENT", "gen_ai.conversation.id": "thread-a"}):
            with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup", "output.value": "x",
                                   "gen_ai.conversation.id": "thread-a"}):
                pass
        with span("agent", **{"gen_ai.span.kind": "AGENT", "gen_ai.conversation.id": "thread-a"}):
            pass
    """)
    assert [r.get("delegated_to") for r in records] == [None]


def test_an_agent_named_on_the_tool_span_or_by_operation_name_is_read(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        with span("execute_tool search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes",
                                              "gen_ai.agent.name": "research_worker"}):
            pass
        with span("invoke_agent", **{"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "writer_worker"}):
            with span("execute_tool", **{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "publish_note"}):
                pass
        with span("Task", **{"claude_agent.span_kind": "subagent", "claude_agent.subagent_type": "researcher"}):
            with span("WebSearch", **{"claude_agent.span_kind": "tool", "tool.name": "WebSearch"}):
                pass
    """)
    assert _by_tool(records) == {
        "search_notes": ("research_worker", None),
        "publish_note": ("writer_worker", None),
        "WebSearch": ("researcher", None),
    }


def test_an_unnamed_agent_takes_the_tool_that_handed_it_work_and_otherwise_stays_unnamed(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        with span("Agent.run", **{"gen_ai.span.kind": "AGENT"}):
            with span("ask_researcher", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "ask_researcher"}):
                with span("Agent.run", **{"gen_ai.span.kind": "AGENT"}):
                    with span("search", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search"}):
                        pass
    """)
    assert _by_tool(records) == {"search": ("ask_researcher", None), "ask_researcher": (None, "ask_researcher")}


def test_one_call_wrapped_by_two_instrumentations_is_written_once_and_a_failure_is_an_error(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        with span("Worker", **{"gen_ai.span.kind": "AGENT", "agent.name": "Worker"}):
            with span("ToolUsage._use", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "Publish note"}):
                with span("publish_note", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "publish_note"}) as inner:
                    inner.set_status(Status(StatusCode.ERROR))
            with span("Delegate work", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "Delegate work"}):
                with span("search_notes", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "search_notes"}):
                    pass
    """)
    assert [(r["name"], r["is_error"]) for r in records] == [
        ("publish_note", True), ("search_notes", False), ("Delegate work", False),
    ]


def test_an_autogen_core_tool_is_the_agent_the_runtime_handed_the_message_to(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        process = {"messaging.operation": "process", "recipient_agent_type": "Writer", "sender_agent_type": "Triage"}
        with span("autogen process Writer.(s1)-A", **process):
            with span("execute_tool look_up_tide", **{"gen_ai.operation.name": "execute_tool",
                                                      "gen_ai.tool.name": "look_up_tide"}):
                pass
        with span("autogen publish Writer.(s1)-T", **{"messaging.operation": "publish"}):
            with span("execute_tool draft_note", **{"gen_ai.operation.name": "execute_tool",
                                                    "gen_ai.tool.name": "draft_note"}):
                pass
    """)
    assert _by_tool(records) == {"look_up_tide": ("Writer", None), "draft_note": (None, None)}


def test_a_process_that_sets_no_provider_is_still_observed(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        with span("Worker", **{"gen_ai.span.kind": "AGENT", "agent.name": "Worker"}):
            with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
                pass
    """, provider="")
    assert _by_tool(records) == {"lookup": ("Worker", None)}


def test_a_process_that_sets_its_own_provider_later_keeps_it(tmp_path: Path) -> None:
    records = _run(tmp_path, """
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        own = TracerProvider(); memory = InMemorySpanExporter(); own.add_span_processor(SimpleSpanProcessor(memory))
        trace.set_tracer_provider(own)
        with span("lookup", **{"gen_ai.span.kind": "TOOL", "gen_ai.tool.name": "lookup"}):
            pass
        assert [s.name for s in memory.get_finished_spans()] == ["lookup"]
    """, provider="")
    assert [r["name"] for r in records] == ["lookup"]


def test_a_span_made_before_the_process_sets_its_provider_leaves_it_free_to_set_one(tmp_path: Path) -> None:
    records = _run(tmp_path, """
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
    """, provider="")
    assert [r["name"] for r in records] == ["startup", "lookup"]


@pytest.mark.parametrize("native_worker", ["", "Writer worker"])
def test_a_span_line_lends_its_agent_to_another_tracer_s_line_for_the_same_call(native_worker: str) -> None:
    native = Call(name="publish_note", arguments={}, result=None, ok=True, worker_label=native_worker, at=2.0)
    span_line = Call(name="Publish note", arguments={}, result=None, ok=True, worker_id="w", worker_label="From span", at=2.1)
    other = Call(name="search_notes", arguments={}, result=None, ok=True, worker_id="w", worker_label="From span", at=1.0)
    merged = call_runner._merge_span_calls([native], [span_line, other])
    assert [(c.name, c.worker_label) for c in merged] == [
        ("search_notes", "From span"), ("publish_note", native_worker or "From span"),
    ]


def test_a_refusal_read_differently_by_the_two_tracers_is_still_one_call() -> None:
    native = Call(name="issue_refund", arguments={}, result="refused", ok=False, at=2.0)
    span_line = Call(name="issue_refund", arguments={}, result="refused", ok=True, worker_label="Refunds", at=2.0)
    merged = call_runner._merge_span_calls([native], [span_line])
    assert [(c.name, c.worker_label) for c in merged] == [("issue_refund", "Refunds")]


def test_a_span_pairs_with_the_call_of_its_own_outcome_when_one_tool_ran_twice() -> None:
    passed = Call(name="issue_refund", arguments={"order": "1"}, result="issued", ok=True, at=2.0)
    failed = Call(name="issue_refund", arguments={"order": "2"}, result="refused", ok=False, at=3.0)
    span_line = Call(name="issue_refund", arguments={}, result=None, ok=False, worker_label="Refunds", at=2.9)
    merged = call_runner._merge_span_calls([passed, failed], [span_line])
    assert [(c.arguments, c.worker_label) for c in merged] == [({"order": "1"}, ""), ({"order": "2"}, "Refunds")]


def test_a_running_job_without_an_event_id_is_kept_beside_a_finished_one() -> None:
    def job(name, **fields):
        return Call(name="start_async_task", arguments={}, result=None, ok=True, delegated_to=name, **fields)

    merged = call_runner._merge_span_calls([], [job("coder", at=1.0), job("researcher", at=2.0, ended_at=5.0)])
    assert [c.delegated_to for c in merged] == ["coder", "researcher"]


def test_span_lines_alone_read_in_the_order_the_calls_started() -> None:
    late_end = Call(name="ask_research", arguments={}, result=None, ok=True, at=1.0)
    inner = Call(name="search_notes", arguments={}, result=None, ok=True, at=1.5)
    assert [c.name for c in call_runner._merge_span_calls([], [inner, late_end])] == ["ask_research", "search_notes"]


def test_a_job_hand_off_stays_its_own_event_and_is_kept_once() -> None:
    # The sandbox's tracer and the span both report the launch; the job's hand-off is written when it starts and again
    # with its return, under the launch, and is never paired with the launch itself.
    def line(at, **fields):
        return Call(name="start_async_task", arguments={}, result=None, ok=True, at=at, **fields)

    natives = [line(1.0), line(1.1)]
    first = line(1.0, runtime_ids={"event_id": "l1"})
    started = line(1.5, delegated_to="coder", runtime_ids={"event_id": "j1", "parent_event_id": "l1"})
    returned = line(1.5, delegated_to="coder", ended_at=9.0, runtime_ids={"event_id": "j1", "parent_event_id": "l1"})
    second = line(1.1, runtime_ids={"event_id": "l2"})
    merged = call_runner._merge_span_calls(natives, [first, started, returned, second])
    assert [(c.delegated_to, c.ended_at, c.runtime_ids.get("event_id")) for c in merged] == [
        ("", 0.0, None), ("", 0.0, None), ("coder", 9.0, "j1"),
    ]


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


@pytest.mark.parametrize(("app", "kept"), [
    ("set_trace_processors([])", ["FiTracingProcessor"]),
    ("set_trace_processors([Own()])", ["Own", "FiTracingProcessor"]),
    ("from traceai_openai_agents import FiTracingProcessor\nset_trace_processors([FiTracingProcessor()])", ["FiTracingProcessor"]),
])
def test_an_openai_agents_app_that_sets_its_own_trace_processors_is_still_observed_once(
    tmp_path: Path, app: str, kept: list[str]
) -> None:
    for path, source in {**_AGENTS_SDK, "hook/sitecustomize.py": "import simulation_mark_bootstrap\n"
                         "simulation_mark_bootstrap.install()\n"}.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)
    (tmp_path / "hook/simulation_mark_bootstrap.py").write_bytes(Path(simulation_mark_bootstrap.__file__).read_bytes())
    script = f"from agents import get_trace_provider, set_trace_processors\nclass Own: pass\n{app}\n" \
        "print([type(p).__name__ for p in get_trace_provider()._multi_processor._processors])"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": f"{tmp_path / 'hook'}:{tmp_path}", "ALK_OBSERVE_FRAMEWORKS": "1",
             "HARNESS_TOOL_TRACE": str(tmp_path / "calls.jsonl")},
        capture_output=True, text=True, timeout=60,
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


def test_an_autogen_tool_call_is_written_with_its_arguments_and_result_beside_its_span(tmp_path: Path) -> None:
    for path, source in {**_AUTOGEN_CORE, "hook/sitecustomize.py": "import simulation_mark_bootstrap\n"
                         "simulation_mark_bootstrap.install()\n"}.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(source)
    (tmp_path / "hook/simulation_mark_bootstrap.py").write_bytes(Path(simulation_mark_bootstrap.__file__).read_bytes())
    script = textwrap.dedent("""
        import asyncio
        from autogen_core.tools import BaseTool
        def fail(note):
            raise ValueError("no such note")
        async def main():
            await BaseTool("look_up_tide", lambda place: "high at 06:10").run_json({"place": "Kochi"}, None)
            try:
                await BaseTool("read_note", fail).run_json({"note": "n1"}, None)
            except ValueError:
                pass
        asyncio.run(main())
    """)
    trace = tmp_path / "calls.jsonl"
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONPATH": f"{tmp_path / 'hook'}:{tmp_path}", "ALK_OBSERVE_FRAMEWORKS": "1",
             "HARNESS_TOOL_TRACE": str(trace)},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "hook: failed" not in result.stderr
    lines = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [(one["name"], one["arguments"], one["output"], one["is_error"]) for one in lines] == [
        ("look_up_tide", {"place": "Kochi"}, "high at 06:10", False),
        ("read_note", {"note": "n1"}, "no such note", True),
    ]


def test_checks_grade_a_launch_once_and_a_hand_off_made_by_a_tool_call() -> None:
    from fi.alk.harness.world.runtime import graded_calls

    launch = Call(name="start_async_task", arguments={}, result=None, ok=True, at=1.0)
    job = Call(name="start_async_task", arguments={}, result=None, ok=True, at=1.5, delegated_to="coder",
               runtime_ids={"event_id": "j1", "parent_event_id": "l1"})
    task = Call(name="task", arguments={}, result=None, ok=True, at=2.0, delegated_to="researcher",
                runtime_ids={"event_id": "t1", "parent_event_id": "a1"})
    assert graded_calls([launch, job, task]) == [launch, task]


def test_a_job_joins_the_launch_that_named_its_whole_id() -> None:
    from fi.alk.harness.simulation_mark_bootstrap import _names

    assert _names('{"task_id": "1"}', "1")
    assert _names("job a1b2-c3 started", "a1b2-c3")
    assert not _names('{"task_id": "17"}', "1")
    assert not _names("thread_1x", "1")


def test_spans_that_never_end_do_not_stop_observation(monkeypatch) -> None:
    from types import SimpleNamespace

    from fi.alk.harness import simulation_mark_bootstrap as bootstrap

    monkeypatch.setattr(bootstrap, "_LIVE_SPANS_MAX", 2)
    observer = bootstrap._ToolSpans("/dev/null")

    def span(span_id):
        return SimpleNamespace(get_span_context=lambda: SimpleNamespace(span_id=span_id), parent=SimpleNamespace(span_id=0),
                               attributes={}, name="s", end=lambda: None)

    for span_id in (1, 2, 3):
        observer.started(span(span_id))
    assert list(observer._live) == [2, 3]


def test_a_span_starting_or_ending_waits_for_another_thread_s_bookkeeping(tmp_path: Path) -> None:
    import threading
    from types import SimpleNamespace

    from fi.alk.harness import simulation_mark_bootstrap as bootstrap

    observer = bootstrap._ToolSpans(str(tmp_path / "calls.jsonl"))
    one = SimpleNamespace(get_span_context=lambda: SimpleNamespace(span_id=1), parent=None, attributes={}, name="s",
                          end=lambda: None, status=None, start_time=0, end_time=0)
    for step in (lambda: observer.started(one), lambda: one.end()):
        with observer._lock:
            thread = threading.Thread(target=step)
            thread.start()
            thread.join(0.2)
            assert thread.is_alive()
        thread.join(5)
        assert not thread.is_alive()


def test_the_process_instrumenting_after_the_hook_with_its_own_provider_gets_it() -> None:
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
