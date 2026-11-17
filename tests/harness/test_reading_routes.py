"""The routes a reading's contract takes at bundling (TH-8800, TH-8810).

Two corrections, both only where the contract cannot run as written:
- an ``unreachable`` entry that names a module and a callable is traced on the callback route;
- a ``langgraph dev``/``up`` command with no ``langgraph-cli`` installed takes the graph route.
The 12 Nov readings (``fixtures/readings_12nov``) are the kit's own example agents.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_bundle_author_v2 import (
    _bundle,
    _github_job,
    _callback,
    _repo,
    _compile_source_tool_handlers,
    resolve_environment_plan,
    _job,
)
from fi.alk.harness import bundle_author_v2

FIXTURES = Path(__file__).parent / "fixtures" / "readings_12nov"
SDK = "examples/harness/openai_agents_support"
DEEP = "examples/harness/deep_agents_support"
TOOLS = ("look_up_order", "start_return", "return_policy")
DEFS = "".join(f"def {name}(order_id=None):\n    return order_id\n" for name in TOOLS)


ENVIRONMENT = ["OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"]


def bundled(tmp_path, source, subfolder, *, contract):
    """What the job supplies: the model endpoint the readings declare as required."""
    job = _github_job(
        subfolder,
        with_secrets=True,
        secret_aliases=tuple(ENVIRONMENT),
        metadata={"generic_harness_v1": True},
    )
    return _bundle(tmp_path, source, subfolder, contract=contract, job=job)


def reading(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def saved_sdk() -> dict:
    """The Agents SDK reading as the save writes it with the module correction."""
    body = reading("openai_agents")
    for entry in body["tool_entrypoints"]:
        entry["module"] = "agent"
    return body


def sdk_source(tmp_path: Path) -> Path:
    return _repo(tmp_path, {f"{SDK}/agent.py": DEFS + _callback(), f"{SDK}/requirements.txt": "openai-agents\n"})


GRAPH = {"graphs": {"support": "./agent.py:graph"}}
DEEP_REQUIREMENTS = "deepagents\nlangchain-openai\nlanggraph\n"


def deep_source(tmp_path: Path, *, requirements=DEEP_REQUIREMENTS, graph=GRAPH, extra=None) -> Path:
    files = {
        f"{DEEP}/agent.py": DEFS + "graph = object()\n",
        f"{DEEP}/langgraph.json": graph if isinstance(graph, str) else json.dumps(graph),
        f"{DEEP}/requirements.txt": requirements,
        **(extra or {}),
    }
    return _repo(tmp_path, files)


def deep_reading() -> dict:
    return reading("deep_agents")


def control_environment(manifest) -> dict:
    return next(p for p in manifest.processes if p.name == manifest.runtime.control_service).environment


# --- Task 2: an unreachable entry that says where it is, is traced --------------------------


def bound(entries) -> list:
    return bundle_author_v2._source_tool_trace_bindings({"tool_entrypoints": entries})


def one(mode="unreachable", module="agent", callable="look_up_order"):
    return {"tool": "look_up_order", "mode": mode, "module": module, "callable": callable}


def test_an_unreachable_entry_with_module_and_callable_is_bound():
    assert bound([one()]) == [{"name": "look_up_order", "module": "agent", "callable": "look_up_order"}]


@pytest.mark.parametrize("missing", ["module", "callable"])
def test_an_unreachable_entry_missing_either_is_not_bound(missing):
    assert bound([one(**{missing: ""})]) == []


@pytest.mark.parametrize("mode", ["service", "construct"])
def test_the_callback_route_binds_no_more_modes_than_before(mode):
    assert bound([one(mode)]) == []


def test_import_entries_are_bound_as_before():
    assert bound([one("import")]) == [
        {"name": "look_up_order", "module": "agent", "callable": "look_up_order"}
    ]


def test_no_handler_is_written_for_an_unreachable_entry(tmp_path):
    written = _compile_source_tool_handlers({"tool_entrypoints": [one()]}, tmp_path)
    assert written == []
    assert not (tmp_path / "handlers").exists()


def test_the_12_nov_agents_sdk_reading_binds_its_three_tools(tmp_path):
    manifest, output = bundled(tmp_path, sdk_source(tmp_path), SDK, contract=saved_sdk())
    environment = control_environment(manifest)
    assert json.loads(environment["ALK_TOOL_TRACE_BINDINGS"]) == [
        {"name": name, "module": "agent", "callable": name} for name in TOOLS
    ]
    assert environment["HARNESS_TOOL_TRACE"] == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
    assert not list((output / "handlers").glob("*")) if (output / "handlers").exists() else True


def test_an_unreachable_entry_without_a_callable_binds_nothing(tmp_path):
    body = saved_sdk()
    for entry in body["tool_entrypoints"]:
        entry["callable"] = ""
    manifest, _ = bundled(tmp_path, sdk_source(tmp_path), SDK, contract=body)
    assert "ALK_TOOL_TRACE_BINDINGS" not in control_environment(manifest)


def test_the_command_route_environment_is_unchanged(tmp_path):
    (tmp_path / "agent.py").write_text("print('ready')\n")
    runtime = {"language": "python", "execution": "native", "command": ["python", "agent.py"]}
    plan = resolve_environment_plan(
        tmp_path, _job(connector="auto"), contract_runtime=runtime,
        tool_entrypoints=[one(module="agent_tools")],
        contract_interface_kind="command", contract_modality="chat",
    )
    control = next(p for p in plan.processes if p.name == plan.control_service)
    assert "ALK_TOOL_TRACE_BINDINGS" not in control.environment
    assert control.environment["ALK_OBSERVE_FRAMEWORKS"] == "1"


def test_a_command_route_bundle_binds_no_unreachable_entry(tmp_path):
    """The command route already runs the framework observer; a second binding could record one
    call twice. Today's route, with `unreachable` entries naming where the tools are."""
    body = deep_reading()
    body["runtime"]["command"] = ["python", "agent.py"]
    for entry in body["tool_entrypoints"]:
        entry.update(mode="unreachable", module="agent")
    manifest, _ = bundled(tmp_path, deep_source(tmp_path), DEEP, contract=body)
    environment = control_environment(manifest)
    assert environment["ALK_OBSERVE_FRAMEWORKS"] == "1"
    assert "ALK_TOOL_TRACE_BINDINGS" not in environment


def test_the_framework_observer_marks_the_command_route():
    from types import SimpleNamespace as Process

    observed = Process(environment={"ALK_OBSERVE_FRAMEWORKS": "1"})
    assert bundle_author_v2._observed_by_frameworks([Process(environment={}), observed])
    assert not bundle_author_v2._observed_by_frameworks([Process(environment={"PORT": "1"})])


def test_a_module_that_raises_on_import_still_bundles(tmp_path):
    source = _repo(
        tmp_path,
        {f"{SDK}/agent.py": DEFS + _callback(), f"{SDK}/broken.py": "raise RuntimeError('boom')\n"},
    )
    body = saved_sdk()
    body["tool_entrypoints"][0]["module"] = "broken"
    manifest, _ = bundled(tmp_path, source, SDK, contract=body)
    bindings = json.loads(control_environment(manifest)["ALK_TOOL_TRACE_BINDINGS"])
    assert {"name": "look_up_order", "module": "broken", "callable": "look_up_order"} in bindings


def _traced_run(tmp_path: Path, bindings: list[dict], code: str):
    bootstrap = Path(bundle_author_v2.__file__).with_name("livekit_tool_trace_bootstrap.py")
    shutil.copy2(bootstrap, tmp_path / "sitecustomize.py")
    trace = tmp_path / "calls.jsonl"
    result = subprocess.run(
        [sys.executable, "-c", code],
        text=True, capture_output=True, cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(tmp_path), "HARNESS_TOOL_TRACE": str(trace),
             "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings)},
    )
    records = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
    return result, records


def test_the_tracer_never_imports_a_module_so_a_broken_one_changes_nothing(tmp_path):
    (tmp_path / "broken.py").write_text("raise RuntimeError('import side effect')\n")
    (tmp_path / "agent.py").write_text(DEFS)
    bindings = [
        {"name": "ghost", "module": "broken", "callable": "ghost"},
        {"name": "look_up_order", "module": "missing_module", "callable": "look_up_order"},
    ]
    result, records = _traced_run(
        tmp_path, bindings, "import agent\nprint(agent.look_up_order('A-1'))\n"
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "A-1"
    assert "import side effect" not in result.stderr
    assert records == []


def test_the_tracer_records_a_function_tool_by_its_def_name(tmp_path):
    (tmp_path / "agent.py").write_text(
        "class FunctionTool:\n"
        "    def __init__(self, fn):\n        self.fn = fn\n"
        "def function_tool(fn):\n    return FunctionTool(fn)\n"
        "@function_tool\n"
        "def look_up_order(order_id):\n    return {'id': order_id}\n"
    )
    result, records = _traced_run(
        tmp_path,
        [{"name": "look_up_order", "module": "agent", "callable": "look_up_order"}],
        "import agent\nprint(agent.look_up_order.fn('A-1'))\n",
    )
    assert result.returncode == 0, result.stderr
    assert [(r["name"], r["arguments"]) for r in records] == [("look_up_order", {"order_id": "A-1"})]
