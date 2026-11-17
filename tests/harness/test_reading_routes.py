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
    _agent_of,
    _bundle,
    _github_job,
    _callback,
    _repo,
    _sealed,
    BundleAuthorError,
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


# --- Task 3: the LangGraph command that can't run takes the graph route ---------------------

def graph_route(manifest, output) -> bool:
    agent = _agent_of(manifest)
    taken = "ALK_LANGGRAPH_ENTRYPOINT" in agent.environment
    runtime = _sealed(output)["runtime"]
    if taken:
        assert agent.environment["ALK_LANGGRAPH_ENTRYPOINT"] == "agent.py:graph"
        assert runtime["command"] == []
        assert runtime["execution"] == "auto"
        assert runtime["interface"]["kind"] == "callable"
        assert runtime["interface"]["include_tools"] is False
    return taken


def todays_route(manifest, output, body) -> None:
    """The command is run as written, the interface and execution as the reading wrote them."""
    agent = _agent_of(manifest)
    assert "ALK_LANGGRAPH_ENTRYPOINT" not in agent.environment
    assert agent.run_command[-len(body["runtime"]["command"]) :] == body["runtime"]["command"]
    sealed = _sealed(output)["runtime"]
    for key in ("command", "execution", "interface", "workdir", "source_subfolder"):
        assert sealed[key] == body["runtime"][key], key


def test_the_12_nov_deep_agents_reading_takes_the_graph_route(tmp_path):
    manifest, output = bundled(tmp_path, deep_source(tmp_path), DEEP, contract=deep_reading())
    assert graph_route(manifest, output)
    assert _agent_of(manifest).fixed_port is None
    assert "ThreadingHTTPServer" in _agent_of(manifest).run_command[-1]


@pytest.mark.parametrize("verb", ["dev", "up"])
def test_langgraph_dev_and_up_both_take_it(tmp_path, verb):
    body = deep_reading()
    body["runtime"]["command"] = ["langgraph", verb, "--port", "2024"]
    manifest, output = bundled(tmp_path, deep_source(tmp_path), DEEP, contract=body)
    assert graph_route(manifest, output)


def _callback_found(tmp_path):
    return deep_source(
        tmp_path, extra={f"{DEEP}/agent.py": DEFS + "graph = object()\n" + _callback()}
    )


def _not_chat(tmp_path):
    return deep_source(tmp_path)


def _cli_installed(tmp_path):
    return deep_source(tmp_path, requirements=DEEP_REQUIREMENTS + "langgraph-cli[inmem]>=0.1\n")


def _cli_in_pyproject(tmp_path):
    return deep_source(
        tmp_path,
        extra={f"{DEEP}/pyproject.toml": "[project]\nname='x'\nversion='1'\ndependencies=['langgraph_cli']\n"},
    )


def _too_large(tmp_path):
    return deep_source(tmp_path, graph={**GRAPH, "env": "x" * 70_000})


def _has_http(tmp_path):
    return deep_source(tmp_path, graph={**GRAPH, "http": {"app": "./app.py:app"}})


def _has_auth(tmp_path):
    return deep_source(tmp_path, graph={**GRAPH, "auth": {"path": "./auth.py:auth"}})


def _two_graphs(tmp_path):
    return deep_source(tmp_path, graph={"graphs": {"a": "./agent.py:graph", "b": "./agent.py:graph"}})


def _invalid_graph(tmp_path):
    return deep_source(tmp_path, graph={"graphs": {"a": "no-colon"}})


def _unreadable_json(tmp_path):
    return deep_source(tmp_path, graph="{not json")


def _no_langgraph_json(tmp_path):
    source = deep_source(tmp_path)
    (source / DEEP / "langgraph.json").unlink()
    return source


@pytest.mark.parametrize(
    ("make", "change"),
    [
        pytest.param(_callback_found, {}, id="callback-discovered"),
        pytest.param(_not_chat, {"modality": "voice"}, id="not-chat"),
        pytest.param(deep_source, {"command": ["uv", "run", "--with", "langgraph-cli[inmem]", "langgraph", "dev"]}, id="uv-run-wrapper"),
        pytest.param(deep_source, {"command": ["python", "-m", "langgraph", "dev"]}, id="python-m"),
        pytest.param(deep_source, {"command": ["langgraph", "build"]}, id="other-verb"),
        pytest.param(deep_source, {"command": ["langgraph"]}, id="no-verb"),
        pytest.param(deep_source, {"command": ["./langgraph", "dev"]}, id="path-to-binary"),
        pytest.param(_cli_installed, {}, id="cli-in-requirements"),
        pytest.param(_cli_in_pyproject, {}, id="cli-in-pyproject"),
        pytest.param(_too_large, {}, id="langgraph-json-over-64kb"),
        pytest.param(_has_http, {}, id="http-key"),
        pytest.param(_has_auth, {}, id="auth-key"),
        pytest.param(_two_graphs, {}, id="two-graphs"),
        pytest.param(_invalid_graph, {}, id="invalid-graph"),
        pytest.param(_unreadable_json, {}, id="unreadable-json"),
        pytest.param(_no_langgraph_json, {}, id="no-langgraph-json"),
    ],
)
def test_each_condition_failing_alone_keeps_todays_route(tmp_path, make, change):
    body = deep_reading()
    if "command" in change:
        body["runtime"]["command"] = change["command"]
    if "modality" in change:
        body["modality"] = change["modality"]
    try:
        manifest, output = bundled(tmp_path, make(tmp_path), DEEP, contract=body)
    except BundleAuthorError:
        # Today's route, refusal included: the same input before this change fails the same way.
        assert body["modality"] == "voice"
        return
    todays_route(manifest, output, body)


def test_the_h2_probe_two_graphs_with_the_cli_installed_still_bundles(tmp_path):
    """Round 1, H2: a two-graph app with langgraph-cli installed bundles today, and must still."""
    source = deep_source(
        tmp_path,
        requirements=DEEP_REQUIREMENTS + "langgraph-cli\n",
        graph={"graphs": {"a": "./agent.py:graph", "b": "./agent.py:graph"}},
    )
    body = deep_reading()
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


def test_the_uv_run_langgraph_dev_shape_is_unchanged(tmp_path):
    command = ["uv", "run", "--project", "graphs/python", "--with", "langgraph-cli[inmem]", "langgraph", "dev"]
    runtime = {"language": "python", "execution": "native", "command": command}
    (tmp_path / "agent.py").write_text("print('ready')\n")
    plan = resolve_environment_plan(
        tmp_path, _job(connector="auto"), contract_runtime=runtime, tool_entrypoints=[],
        contract_interface_kind="http", contract_modality="chat",
    )
    control = next(p for p in plan.processes if p.name == plan.control_service)
    assert "ALK_LANGGRAPH_ENTRYPOINT" not in control.environment


def test_a_langgraph_json_is_not_read_past_64kb(tmp_path, monkeypatch):
    read = []
    real = bundle_author_v2._langgraph_entrypoint
    monkeypatch.setattr(
        bundle_author_v2, "_langgraph_entrypoint", lambda root: read.append(root) or real(root)
    )
    body = deep_reading()
    bundled(tmp_path, _too_large(tmp_path), DEEP, contract=body)
    assert read == []


def test_a_graph_route_reading_the_requirements_uses_the_install_plans_files(tmp_path):
    """Where the build installs from: a requirements file the runtime names counts too."""
    body = deep_reading()
    body["runtime"]["requirements_files"] = ["extra.txt"]
    source = deep_source(tmp_path, extra={f"{DEEP}/extra.txt": "langgraph-cli\n"})
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


def test_the_other_12_nov_readings_take_no_graph_route(tmp_path):
    body = reading("google_adk")
    source = _repo(
        tmp_path,
        {"examples/harness/google_adk_support/support_agent/agent.py": DEFS + "root_agent = object()\n",
         "examples/harness/google_adk_support/support_agent/__init__.py": "",
         "examples/harness/google_adk_support/requirements.txt": "google-adk\nmcp\n"},
    )
    manifest, output = bundled(tmp_path, source, "examples/harness/google_adk_support", contract=body)
    assert "ALK_LANGGRAPH_ENTRYPOINT" not in _agent_of(manifest).environment
    assert _sealed(output)["runtime"]["interface"]["path"] == "/run_sse"
