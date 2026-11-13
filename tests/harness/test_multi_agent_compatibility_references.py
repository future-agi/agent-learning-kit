from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / "examples" / "harness" / "multi_agent_compatibility"


def run_verifier(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(REFERENCES / "verify_references.py"), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_multi_agent_reference_contracts_do_not_drift() -> None:
    result = run_verifier()
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "3 multi-agent references verified"


def test_google_reference_keeps_the_full_travel_concierge_topology() -> None:
    reference = json.loads(
        (REFERENCES / "google_adk" / "reference.json").read_text(encoding="utf-8")
    )
    topology = reference["topology"]
    workers = topology["workers"]
    top_level = {
        worker["id"]
        for worker in workers
        if worker["parent"] == topology["coordinator"]
    }
    assert top_level == {
        "inspiration_agent",
        "planning_agent",
        "booking_agent",
        "pre_trip_agent",
        "in_trip_agent",
        "post_trip_agent",
    }
    assert len(workers) == 20


def test_crewai_reference_keeps_the_native_self_evaluation_workers() -> None:
    reference = json.loads(
        (REFERENCES / "crewai" / "reference.json").read_text(encoding="utf-8")
    )
    assert reference["topology"] == {
        "coordinator": "ShakespeareXPostFlow",
        "workers": [
            {"id": "shakespearean_bard", "parent": "ShakespeareXPostFlow"},
            {"id": "x_post_verifier", "parent": "ShakespeareXPostFlow"},
        ],
    }


def test_langgraph_reference_keeps_both_async_workers() -> None:
    reference = json.loads(
        (REFERENCES / "langgraph" / "reference.json").read_text(encoding="utf-8")
    )
    assert reference["topology"] == {
        "coordinator": "agent",
        "workers": [
            {"id": "researcher", "parent": "agent"},
            {"id": "coder", "parent": "agent"},
        ],
    }


def test_verifier_checks_materialized_orchestration(tmp_path: Path) -> None:
    source = tmp_path / "travel-concierge"
    agent = source / "travel_concierge" / "agent.py"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "\n".join(
            [
                "sub_agents=[",
                "inspiration_agent,",
                "planning_agent,",
                "booking_agent,",
                "pre_trip_agent,",
                "in_trip_agent,",
                "post_trip_agent,",
            ]
        ),
        encoding="utf-8",
    )
    result = run_verifier(f"--source=google-adk-travel-concierge={source}")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "3 multi-agent references and 1 source trees verified"
    )


def test_returns_reference_declares_the_workers_and_tools_its_app_builds() -> None:
    import ast

    app = ROOT / "examples" / "harness" / "returns_reference"
    reference = json.loads((app / "reference.json").read_text(encoding="utf-8"))
    tree = ast.parse((app / "agent.py").read_text(encoding="utf-8"))
    # A worker's tools are built by a function of their own (each reaches the world through its own address).
    builders = {
        node.name: sorted(
            inner.name
            for inner in node.body
            if isinstance(inner, ast.FunctionDef)
            and any(getattr(one, "id", "") == "function_tool" for one in inner.decorator_list)
        )
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
    }
    built = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "Agent":
            fields = {one.arg: one.value for one in node.keywords}
            tools = fields["tools"]
            built[ast.literal_eval(fields["name"])] = (
                builders[tools.func.id]
                if isinstance(tools, ast.Call)
                else sorted(one.id for one in tools.elts if isinstance(one, ast.Name))
            )
    topology = reference["topology"]
    assert topology["coordinator"] in built
    assert {one["name"]: sorted(one["tools"]) for one in topology["workers"]} == {
        name: tools for name, tools in built.items() if name != topology["coordinator"]
    }


def test_the_returns_reference_answers_every_turn_of_a_conversation_on_one_event_loop(monkeypatch) -> None:
    """A model client keeps loop-bound connections between turns, so every turn must run on the same loop: a fresh
    loop per request answered the first turn and failed the next with "Event loop is closed"."""
    import asyncio
    import importlib.util
    import types

    held = {}

    class _Runner:
        @staticmethod
        async def run(_agent, conversation):
            # Like a cached HTTP client: its connections belong to the loop of the first turn, and using them from
            # another turn schedules work on that loop.
            loop = held.setdefault("loop", asyncio.get_running_loop())
            loop.call_soon(lambda: None)
            return types.SimpleNamespace(final_output=f"turn {len(conversation)}")

    class _Agent:
        def __init__(self, **fields):
            self.__dict__.update(fields)

        def as_tool(self, **_fields):
            return self

    stub = types.ModuleType("agents")
    stub.Agent, stub.Runner = _Agent, _Runner
    stub.function_tool = lambda function: function
    stub.set_trace_processors = lambda _processors: None
    monkeypatch.setitem(sys.modules, "agents", stub)
    spec = importlib.util.spec_from_file_location(
        "returns_reference_agent", ROOT / "examples" / "harness" / "returns_reference" / "agent.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    first = [{"role": "user", "content": "Refund order 1043"}]
    assert module.reply(first) == "turn 1"
    assert module.reply([*first, {"role": "assistant", "content": "Done"}, {"role": "user", "content": "Thanks"}]) == (
        "turn 3"
    )
