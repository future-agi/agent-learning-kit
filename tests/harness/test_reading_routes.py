"""The routes a reading's contract takes at bundling (TH-8800, TH-8810).

Two corrections, both only where the contract cannot run as written:
- an ``unreachable`` entry that names a module and a callable is traced on the callback route;
- a ``langgraph dev``/``up`` command with no ``langgraph-cli`` installed takes the graph route.
The 12 Nov readings (``fixtures/readings_12nov``) are the kit's own example agents.
"""

from __future__ import annotations

import json
import shlex
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
        **({f"{DEEP}/requirements.txt": requirements} if requirements is not None else {}),
        **(extra or {}),
    }
    return _repo(tmp_path, files)


def deep_reading() -> dict:
    return reading("deep_agents")


def control_environment(manifest) -> dict:
    return next(p for p in manifest.processes if p.name == manifest.runtime.control_service).environment


# --- An unreachable entry that says where it is, is traced -----------------------------------


def bound(entries, **options) -> list:
    return bundle_author_v2._source_tool_trace_bindings({"tool_entrypoints": entries}, **options)


def one(mode="unreachable", module="agent", callable="look_up_order"):
    return {"tool": "look_up_order", "mode": mode, "module": module, "callable": callable}


def test_an_unreachable_entry_with_module_and_callable_is_bound():
    assert bound([one()]) == [
        {"name": "look_up_order", "module": "agent", "callable": "look_up_order",
         "qualname": "look_up_order"}
    ]


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
        {"name": name, "module": "agent", "callable": name, "qualname": name} for name in TOOLS
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
    assert {
        "name": "look_up_order", "module": "broken", "callable": "look_up_order",
        "qualname": "look_up_order",
    } in bindings


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


# --- The LangGraph command that can't run takes the graph route -----------------------------

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


_PER_THREAD = "/threads/{{langgraph_thread_id}}/runs/wait"


def _set(interface: dict) -> dict:
    """The interface's set fields: a field left unset seals the same, written as null or left out."""
    return {key: value for key, value in (interface or {}).items() if value is not None}


def todays_route(manifest, output, body) -> None:
    """The graph route is not taken: the reading's command route stands, as the kit serves a framework's server
    (TH-8887). `langgraph dev` runs without its file watcher; served natively, it runs on the sandbox's port, with
    each conversation on its own thread. Execution, workdir and subfolder are the reading's."""
    from fi.alk.harness.framework_servers import _is_langgraph_server_command, server_command

    agent = _agent_of(manifest)
    assert not graph_route(manifest, output)
    assert "ALK_LANGGRAPH_ENTRYPOINT" not in agent.environment
    sealed = _sealed(output)["runtime"]
    for key in ("execution", "workdir", "source_subfolder"):
        assert sealed[key] == body["runtime"][key], key
    command, interface = body["runtime"]["command"], body["runtime"]["interface"]
    served = (sealed["interface"] or {}).get("path") == _PER_THREAD
    # The command: the reading's, given the sandbox's port when the kit serves `langgraph dev` natively.
    expected = command if not served or "--port" in command else [*command, "--port", "2024"]
    assert sealed["command"] == expected
    # The run: a served `langgraph dev` from its config folder on $PORT, else the command as sealed; neither
    # keeps the file watcher.
    if agent.run_command[:2] == ["sh", "-c"]:
        port = expected.index("--port") + 1
        argv = [*expected[:port], "PORT_VALUE", *expected[port + 1 :]]
        assert agent.run_command == ["sh", "-c", shlex.join(argv).replace("PORT_VALUE", "$PORT") + " --no-reload"]
    else:
        run = server_command(expected) if _is_langgraph_server_command(expected) else expected
        assert agent.run_command[-len(run) :] == run
    # The interface: the reading's, with the per-thread path and its thread setup when served.
    own = ("path", "request_template", "setup_requests", "headers_template", "setup_secrets")
    kept = {key: value for key, value in _set(interface).items() if key not in own}
    assert {key: value for key, value in _set(sealed["interface"]).items() if key not in own} == kept
    if not served:
        assert _set(sealed["interface"]) == _set(interface)
        return
    assert sealed["interface"]["request_template"]["input"] == {
        "messages": [{"content": "{{new_message_content}}", "role": "user"}]
    }
    assert sealed["interface"]["setup_requests"] == [
        {
            "accepted_statuses": [200],
            "body_template": {},
            "capture": {"langgraph_thread_id": "thread_id"},
            "method": "POST",
            "path": "/threads",
        }
    ]


def test_the_12_nov_deep_agents_reading_takes_the_graph_route(tmp_path):
    manifest, output = bundled(tmp_path, deep_source(tmp_path), DEEP, contract=deep_reading())
    assert graph_route(manifest, output)
    assert _agent_of(manifest).fixed_port is None
    assert "ThreadingHTTPServer" in _agent_of(manifest).run_command[-1]


def test_the_guidance_s_langgraph_shape_is_saved_by_a_reading_and_takes_the_graph_route(tmp_path):
    # SKILL.md item 12's shape: no command, execution auto, a callable fi.alk interface. A reading
    # on 17 Nov told to write no interface was refused nine times (missing-chat-interface).
    from fi.alk.harness.tools import accept_contract

    body = deep_reading()
    body["runtime"].update(
        command=[], execution="auto", interface={"kind": "callable", "protocol": "fi.alk"}
    )
    body["runtime"].pop("source_subfolder", None)
    for cited in ("system_prompt_from", "model"):  # lines of the real agent, not this stand-in
        body.pop(cited, None)
    source = deep_source(tmp_path)
    without = json.loads(json.dumps(body))
    without["runtime"].pop("interface")
    refused = accept_contract(
        without, tmp_path / "refused", source_root=source, subfolder=DEEP, require_setup_contract=True
    )
    assert refused.get("is_error") and "missing-chat-interface" in json.dumps(refused)
    result = accept_contract(
        body, tmp_path / "saved", source_root=source, subfolder=DEEP, require_setup_contract=True
    )
    assert not result.get("is_error"), result
    saved = json.loads((tmp_path / "saved" / "contract.json").read_text())

    manifest, output = bundled(tmp_path / "b", source, DEEP, contract=saved)

    # The kit's own no-command LangGraph route, which keeps the reading's callable interface.
    agent = _agent_of(manifest)
    assert agent.environment["ALK_LANGGRAPH_ENTRYPOINT"] == "agent.py:graph"
    runtime = _sealed(output)["runtime"]
    assert runtime["command"] == [] and runtime["execution"] == "auto"
    assert runtime["interface"]["kind"] == "callable"


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
    """A two-graph app with langgraph-cli installed bundles today, and must still."""
    source = deep_source(
        tmp_path,
        requirements=DEEP_REQUIREMENTS + "langgraph-cli\n",
        graph={"graphs": {"a": "./agent.py:graph", "b": "./agent.py:graph"}},
    )
    body = deep_reading()
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


def test_the_uv_run_langgraph_dev_shape_is_unchanged(tmp_path):
    """The command that carries its own CLI (`uv run --with langgraph-cli[inmem] langgraph dev`)
    is not one the graph route takes over: `_langgraph_command_cannot_run` says no, even for an
    app whose source is otherwise a single servable graph."""
    command = ["uv", "run", "--project", "graphs/python", "--with", "langgraph-cli[inmem]", "langgraph", "dev"]
    root, run_folder, runtime = _scoped(deep_source(tmp_path), command=command)
    assert runtime["command"] == command
    assert bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime) is False
    # The same source with the plain command is taken, so the answer above is the command's.
    plain = {**runtime, "command": ["langgraph", "dev"]}
    assert bundle_author_v2._langgraph_command_cannot_run(root, run_folder, plain) is True


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


def test_the_name_kept_is_the_one_the_tracer_needs(tmp_path):
    """Through the real tracer: a callback process with no subfolder starts at the
    repository root, where the frame carries the root-relative name. That name records the call;
    the name a rewrite would have written (`agent`) records nothing."""
    folder = tmp_path / "examples" / "harness" / "support"
    folder.mkdir(parents=True)
    (folder / "agent.py").write_text(DEFS)
    code = "from examples.harness.support import agent\nagent.look_up_order('x')\n"
    rooted = "examples.harness.support.agent"
    kept, records = _traced_run(
        tmp_path, [{"name": "look_up_order", "module": rooted, "callable": "look_up_order"}], code
    )
    assert kept.returncode == 0, kept.stderr
    assert [r["name"] for r in records] == ["look_up_order"]
    (tmp_path / "calls.jsonl").unlink()
    rewritten, records = _traced_run(
        tmp_path, [{"name": "look_up_order", "module": "agent", "callable": "look_up_order"}], code
    )
    assert rewritten.returncode == 0, rewritten.stderr
    assert records == []


# --- A Compose selection keeps its command and its HTTP interface ---------------------------

def test_a_compose_selection_bundles_as_it_does_on_the_old_kit(tmp_path, monkeypatch):
    """The service's Dockerfile installs langgraph-cli, which requirements.txt can't show. The
    graph route's `callable` interface is refused on Compose (compose_native_http_chat_required)."""
    monkeypatch.setenv("ALK_COMPOSE_BROKER_SOCKET", "/nonexistent/broker.sock")
    compose = (
        "services:\n  agent:\n    build: .\n"
        "    command: [langgraph, dev, --host, 0.0.0.0, --port, '2024']\n    ports: ['2024:2024']\n"
    )
    docker = "FROM python:3.12\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt 'langgraph-cli[inmem]'\n"
    source = deep_source(
        tmp_path, extra={f"{DEEP}/docker-compose.yml": compose, f"{DEEP}/Dockerfile": docker}
    )
    body = deep_reading()
    body["runtime"].update(
        execution="compose", compose_file=f"{DEEP}/docker-compose.yml", service="agent"
    )
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    sealed = _sealed(output)["runtime"]
    assert sealed["interface"]["kind"] == "http"
    assert sealed["interface"]["path"] == "/runs/wait"
    assert sealed["command"] == body["runtime"]["command"]


@pytest.mark.parametrize("execution", ["native", "auto"])
def test_an_auto_selected_compose_file_bundles_as_on_the_old_kit(
    tmp_path, monkeypatch, execution
):
    """The reading names no compose file, but `auto` (which `native` becomes on the
    graph route) selects the folder's docker-compose.yml, and Compose refuses `callable`."""
    monkeypatch.setenv("ALK_COMPOSE_BROKER_SOCKET", "/nonexistent/broker.sock")
    compose = (
        "services:\n  agent:\n    build: {context: ., dockerfile: docker/agent.Dockerfile}\n"
        "    command: [langgraph, dev, --host, 0.0.0.0, --port, '2024']\n    ports: ['2024:2024']\n"
    )
    docker = "FROM python:3.12\nWORKDIR /app\nCOPY . .\nRUN pip install -r requirements.txt 'langgraph-cli[inmem]'\n"
    source = deep_source(
        tmp_path, extra={f"{DEEP}/docker-compose.yml": compose, f"{DEEP}/docker/agent.Dockerfile": docker}
    )
    body = deep_reading()
    body["runtime"]["execution"] = execution
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    sealed = _sealed(output)["runtime"]
    assert sealed["interface"]["kind"] == "http"
    # A native `langgraph dev` is served by the kit, which runs each conversation on its own thread (TH-8887).
    assert sealed["interface"]["path"] == (
        "/threads/{{langgraph_thread_id}}/runs/wait" if execution == "native" else "/runs/wait"
    )
    assert sealed["command"] == body["runtime"]["command"]


def _scoped(source: Path, **selection):
    """What `_langgraph_command_cannot_run` is handed: the reading's runtime bound to its folder."""
    root = source.resolve()
    runtime, _, run_folder = bundle_author_v2._agent_scope(
        root, DEEP, {**deep_reading()["runtime"], **selection}
    )
    return root, run_folder, runtime


@pytest.mark.parametrize(
    "selection",
    [
        {"execution": "compose", "compose_file": "docker-compose.yml", "service": "agent"},
        {"execution": "auto", "compose_file": "docker-compose.yml", "service": "agent"},
    ],
    ids=["compose", "auto-with-a-compose-file"],
)
def test_a_compose_selection_keeps_the_command(tmp_path, selection):
    root, run_folder, runtime = _scoped(deep_source(tmp_path), **selection)
    assert not bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime)


def test_the_same_reading_with_no_compose_still_takes_the_graph_route(tmp_path):
    root, run_folder, runtime = _scoped(deep_source(tmp_path))
    assert bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime)


# --- A call is matched on the full callable, not its last name ------------------------------

TWO_TOOLS = (
    "class SearchRooms:\n"
    "    def _run(self, city):\n        return 'rooms in ' + city\n"
    "class BookRoom:\n"
    "    def _run(self, room):\n        return 'booked ' + room\n"
)


def _unreachable_binding(name, callable):
    (binding,) = bound([one(callable=callable) | {"tool": name}])
    return binding


def test_unreachable_bindings_carry_the_full_callable():
    assert _unreachable_binding("search_rooms", "SearchRooms._run")["qualname"] == "SearchRooms._run"


def test_an_import_binding_carries_no_qualname():
    (binding,) = bound([one("import", callable="SearchRooms._run")])
    assert "qualname" not in binding


def test_two_methods_with_one_last_name_are_each_their_own_tool(tmp_path):
    (tmp_path / "agent.py").write_text(TWO_TOOLS)
    bindings = [
        _unreachable_binding("search_rooms", "SearchRooms._run"),
        _unreachable_binding("book_room", "BookRoom._run"),
    ]
    result, records = _traced_run(
        tmp_path, bindings,
        "import agent\nagent.SearchRooms()._run('Oslo')\nagent.BookRoom()._run('r1')\n",
    )
    assert result.returncode == 0, result.stderr
    assert [(r["name"], r["output"]) for r in records] == [
        ("search_rooms", "rooms in Oslo"),
        ("book_room", "booked r1"),
    ]


def test_a_function_and_a_same_named_method_give_one_record(tmp_path):
    (tmp_path / "agent.py").write_text(
        "class Orders:\n"
        "    def look_up_order(self, order_id):\n        return {'id': order_id}\n"
        "orders = Orders()\n"
        "def look_up_order(order_id):\n    return orders.look_up_order(order_id)\n"
    )
    result, records = _traced_run(
        tmp_path, [_unreachable_binding("look_up_order", "look_up_order")],
        "import agent\nagent.look_up_order('A1')\n",
    )
    assert result.returncode == 0, result.stderr
    assert [(r["name"], r["arguments"]) for r in records] == [("look_up_order", {"order_id": "A1"})]


def test_a_tool_defined_inside_a_function_is_matched_by_its_own_name(tmp_path):
    """The frame's qualname is `build.<locals>.search`; the reading writes `search`."""
    (tmp_path / "agent.py").write_text(
        "def build():\n"
        "    async def search(q):\n        return 'hit ' + q\n"
        "    return search\n"
        "search_tool = build()\n"
        "def make_class():\n"
        "    class Finder:\n"
        "        def find(self, q):\n            return 'found ' + q\n"
        "    return Finder\n"
        "Finder = make_class()\n"
    )
    result, records = _traced_run(
        tmp_path, [_unreachable_binding("search", "search")],
        "import asyncio, agent\nasyncio.run(agent.search_tool('x'))\n",
    )
    assert result.returncode == 0, result.stderr
    assert [(r["name"], r["output"]) for r in records] == [("search", "hit x")]
    (tmp_path / "calls.jsonl").unlink()
    result, records = _traced_run(
        tmp_path, [_unreachable_binding("find", "Finder.find")],
        "import agent\nagent.Finder().find('y')\n",
    )
    assert result.returncode == 0, result.stderr
    assert [(r["name"], r["output"]) for r in records] == [("find", "found y")]


def test_a_tool_defined_inside_async_main_is_matched(tmp_path):
    (tmp_path / "agent.py").write_text(
        "import asyncio\n"
        "async def main():\n"
        "    def search(q):\n        return 'hit ' + q\n"
        "    return search('z')\n"
    )
    result, records = _traced_run(
        tmp_path, [_unreachable_binding("search", "search")],
        "import asyncio, agent\nasyncio.run(agent.main())\n",
    )
    assert result.returncode == 0, result.stderr
    assert [(r["name"], r["output"]) for r in records] == [("search", "hit z")]


def test_an_inherited_method_is_matched_under_the_subclass_name(tmp_path):
    (tmp_path / "agent.py").write_text(
        "class Base:\n"
        "    def _run(self, city):\n        return 'rooms in ' + city\n"
        "class SearchRooms(Base):\n    pass\n"
    )
    result, records = _traced_run(
        tmp_path, [_unreachable_binding("search_rooms", "SearchRooms._run")],
        "import agent\nagent.SearchRooms()._run('Oslo')\n",
    )
    assert result.returncode == 0, result.stderr
    assert [(r["name"], r["output"]) for r in records] == [("search_rooms", "rooms in Oslo")]


# --- An include or a uv group may install the CLI, so today's route is kept -----------------

@pytest.mark.parametrize(
    "line",
    ["-r base.txt", "--requirement base.txt", "--requirement=base.txt", "-c pins.txt",
     "--constraint pins.txt", "-rbase.txt",
     "-e ./lib", "--editable ./lib", "./lib", "lib @ file:./lib", "git+https://example.com/lib.git"],
)
def test_a_requirements_include_or_constraint_keeps_todays_route(tmp_path, line):
    body = deep_reading()
    source = deep_source(
        tmp_path,
        requirements=DEEP_REQUIREMENTS + line + "\n",
        extra={f"{DEEP}/base.txt": "langgraph-cli\n", f"{DEEP}/pins.txt": "langgraph-cli\n",
               f"{DEEP}/lib/pyproject.toml": "[project]\nname='lib'\nversion='1'\ndependencies=['langgraph-cli[inmem]']\n"},
    )
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


def test_a_dockerfile_keeps_todays_route(tmp_path):
    """The app's own Dockerfile may install the CLI, which the kit can't see."""
    body = deep_reading()
    source = deep_source(
        tmp_path,
        extra={f"{DEEP}/Dockerfile": "FROM python:3.12\nRUN pip install 'langgraph-cli[inmem]'\n"},
    )
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


def test_a_runtime_dockerfile_keeps_the_command(tmp_path):
    root, run_folder, runtime = _scoped(deep_source(tmp_path), dockerfile=f"{DEEP}/Dockerfile.agent")
    assert not bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime)


@pytest.mark.parametrize(
    "pyproject",
    [
        "[dependency-groups]\ndev = ['pytest']\ncli = ['langgraph-cli']\n",
        "[tool.uv]\ndefault-groups = ['cli']\n",
    ],
    ids=["dependency-groups", "uv-default-groups"],
)
def test_a_uv_group_keeps_todays_route(tmp_path, pyproject):
    body = deep_reading()
    source = deep_source(
        tmp_path,
        extra={f"{DEEP}/pyproject.toml": "[project]\nname='x'\nversion='1'\ndependencies=['langgraph']\n" + pyproject},
    )
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


PROJECT = "[project]\nname='x'\nversion='1'\ndependencies=['langgraph']\n"


@pytest.mark.parametrize(
    ("requirements", "extra"),
    [
        pytest.param(
            DEEP_REQUIREMENTS + "langgraph-cli[inmem]\n",
            {f"{DEEP}/pyproject.toml": PROJECT},
            id="requirements-txt-beside-a-pyproject",
        ),
        pytest.param(
            None,
            {
                f"{DEEP}/pyproject.toml": "[tool.poetry]\nname='x'\nversion='1'\n"
                "[tool.poetry.dependencies]\npython='^3.12'\nlanggraph='*'\nlanggraph-cli='*'\n",
                f"{DEEP}/poetry.lock": "",
            },
            id="poetry-lock",
        ),
        pytest.param(
            None,
            {
                f"{DEEP}/setup.py": "from setuptools import setup\n"
                "setup(name='x', install_requires=['langgraph', 'langgraph-cli[inmem]'])\n",
            },
            id="setup-py",
        ),
        pytest.param(
            None,
            {
                f"{DEEP}/pyproject.toml": PROJECT
                + "[tool.uv]\ndev-dependencies = ['langgraph-cli[inmem]']\n",
                f"{DEEP}/uv.lock": "",
            },
            id="uv-dev-dependencies",
        ),
    ],
)
def test_a_dependency_the_build_installs_beyond_the_pyproject_keeps_todays_route(
    tmp_path, requirements, extra
):
    source = deep_source(tmp_path, requirements=requirements, extra=extra)
    root, run_folder, runtime = _scoped(source)
    assert not bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime)
    body = deep_reading()
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(
            {
                f"{DEEP}/pyproject.toml": "[build-system]\nrequires=['poetry-core']\n"
                "build-backend='poetry.core.masonry.api'\n"
                "[tool.poetry]\nname='x'\nversion='1'\n"
                "[tool.poetry.dependencies]\npython='^3.12'\nlanggraph='*'\nlanggraph-cli='*'\n",
            },
            id="poetry-core-without-a-lock",
        ),
        pytest.param(
            {
                f"{DEEP}/pyproject.toml": "[build-system]\nrequires=['setuptools']\n"
                "build-backend='setuptools.build_meta'\n",
                f"{DEEP}/setup.cfg": "[metadata]\nname = x\n[options]\n"
                "install_requires =\n    langgraph\n    langgraph-cli[inmem]\n",
            },
            id="setuptools-setup-cfg",
        ),
        pytest.param(
            {
                f"{DEEP}/pyproject.toml": "[project]\nname='x'\nversion='1'\n"
                "dynamic=['dependencies']\n",
            },
            id="dynamic-dependencies",
        ),
    ],
)
def test_a_pip_project_install_the_pyproject_does_not_declare_keeps_todays_route(tmp_path, extra):
    source = deep_source(tmp_path, requirements=None, extra=extra)
    root, run_folder, runtime = _scoped(source)
    parsed = bundle_author_v2.Runtime.model_validate(runtime)
    component = bundle_author_v2._runtime_component(root, parsed.workdir)
    assert bundle_author_v2._install_strategy(component, parsed) == "pip-project"
    assert not bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime)
    body = deep_reading()
    manifest, output = bundled(tmp_path, source, DEEP, contract=body)
    todays_route(manifest, output, body)


def test_a_pip_project_install_with_static_dependencies_still_takes_the_graph_route(tmp_path):
    source = deep_source(tmp_path, requirements=None, extra={f"{DEEP}/pyproject.toml": PROJECT})
    root, run_folder, runtime = _scoped(source)
    parsed = bundle_author_v2.Runtime.model_validate(runtime)
    component = bundle_author_v2._runtime_component(root, parsed.workdir)
    assert bundle_author_v2._install_strategy(component, parsed) == "pip-project"
    assert bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime)


def test_a_requirements_txt_beside_a_pyproject_without_the_cli_still_takes_the_graph_route(tmp_path):
    source = deep_source(tmp_path, extra={f"{DEEP}/pyproject.toml": PROJECT})
    root, run_folder, runtime = _scoped(source)
    assert bundle_author_v2._langgraph_command_cannot_run(root, run_folder, runtime)


def test_a_process_with_the_livekit_tool_trace_hook_binds_no_unreachable_entry():
    """The hook already records the call; a second binding would grade it twice."""
    from types import SimpleNamespace as Process

    hooked = Process(
        environment={
            "LIVEKIT_AGENT_NAME": "x-{{JOB_ID}}",
            "FI_WORKER_HEALTH_PORT": "{{PORT_agent}}",
            "HARNESS_TOOL_TRACE": "{{WORLD_DIR}}/t",
        }
    )
    assert bundle_author_v2._observed_by_frameworks([hooked])
    assert bound([one()], watch_unreachable=not bundle_author_v2._observed_by_frameworks([hooked])) == []


def test_a_declared_livekit_agent_name_alone_is_not_the_hook():
    """A non-LiveKit reading may declare the name; only the worker knob marks the hook."""
    from types import SimpleNamespace as Process

    named = Process(environment={"LIVEKIT_AGENT_NAME": "x"})
    assert not bundle_author_v2._observed_by_frameworks([named])
    assert bound([one()], watch_unreachable=not bundle_author_v2._observed_by_frameworks([named]))
