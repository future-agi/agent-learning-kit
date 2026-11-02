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


@pytest.mark.parametrize("native_worker", ["", "Writer worker"])
def test_a_span_line_lends_its_agent_to_another_tracer_s_line_for_the_same_call(native_worker: str) -> None:
    native = Call(name="publish_note", arguments={}, result=None, ok=True, worker_label=native_worker, at=2.0)
    span_line = Call(name="Publish note", arguments={}, result=None, ok=True, worker_id="w", worker_label="From span", at=2.1)
    other = Call(name="search_notes", arguments={}, result=None, ok=True, worker_id="w", worker_label="From span", at=1.0)
    merged = call_runner._merge_span_calls([native], [span_line, other])
    assert [(c.name, c.worker_label) for c in merged] == [
        ("search_notes", "From span"), ("publish_note", native_worker or "From span"),
    ]


def test_span_lines_alone_read_in_the_order_the_calls_started() -> None:
    late_end = Call(name="ask_research", arguments={}, result=None, ok=True, at=1.0)
    inner = Call(name="search_notes", arguments={}, result=None, ok=True, at=1.5)
    assert [c.name for c in call_runner._merge_span_calls([], [inner, late_end])] == ["ask_research", "search_notes"]


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
