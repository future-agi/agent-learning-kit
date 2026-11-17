"""The Deep Agents reference agent, and how the kit's hosted path handles it.

The libraries are never installed here. Where the example has to be imported, a stand-in
`deepagents` and `langchain_openai` written below take their place, shaped like the public
names the example uses.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path

import pytest
from reference_agents import (
    DETECTION_CASES,
    REPO,
    TOOLS,
    TRACE_HOOK,
    assert_bindings_match,
    assert_detected,
    assert_pins,
    assert_refuses_key_without_base_url,
    build_control_service,
    fresh_import,
    run_tracer,
    write_chat_contract,
    write_stand_ins,
)

SUBFOLDER = "examples/harness/deep_agents_support"
EXAMPLE = REPO / SUBFOLDER
ENVIRONMENT = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL")
DEFAULT_MODEL = "vertex_ai/gemini-3.7-flash"

# `Tool.ainvoke` follows langchain-core 1.8.0 (tools/structured.py:174-185, tools/base.py:933,
# runnables/config.py:678-712): a coroutine function is awaited on the loop, and a plain
# function goes through `run_in_executor(None, ...)`, which hands it to the loop's default
# thread pool with the caller's context copied. `create_deep_agent` takes the tools and
# a model object (deepagents/graph.py:289-309) and returns a graph with `ainvoke`.
STAND_IN_DEEPAGENTS = '''
import asyncio
import inspect
import threading
from contextvars import copy_context
from functools import partial
from types import SimpleNamespace

STATE = {"calls": [], "executor_threads": []}


async def run_in_executor(func, *args, **kwargs):
    def wrapper():
        STATE["executor_threads"].append(threading.get_ident())
        return func(*args, **kwargs)

    return await asyncio.get_running_loop().run_in_executor(
        None, partial(copy_context().run, wrapper)
    )


class Tool:
    def __init__(self, function):
        self.name = function.__name__
        self.function = function
        self.is_async = inspect.iscoroutinefunction(function)

    async def ainvoke(self, args_dict):
        if self.is_async:
            return await self.function(**args_dict)
        return await run_in_executor(self.function, **args_dict)


class Graph:
    def __init__(self, tools):
        self.tools = [Tool(function) for function in tools]

    async def ainvoke(self, state, config=None):
        return {"messages": [{"role": "assistant", "content": "All done."}]}


def create_deep_agent(model=None, tools=None, *, system_prompt=None, **options):
    STATE["calls"].append(
        SimpleNamespace(
            model=model, tools=list(tools or []), system_prompt=system_prompt, options=options
        )
    )
    return Graph(list(tools or []))
'''

# The real client is built eagerly in the constructor, and the async one it builds refuses a
# missing key (langchain_openai/chat_models/base.py:1527-1545), which is what makes a model
# built at import fail an import in an environment that has none.
STAND_IN_LANGCHAIN_OPENAI = '''
INSTANCES = []


class ChatOpenAI:
    def __init__(
        self, *, model=None, base_url=None, api_key=None, use_responses_api=None, **options
    ):
        if api_key is None:
            raise RuntimeError("Missing credentials: the api_key must be set")
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.use_responses_api = use_responses_api
        self.options = options
        INSTANCES.append(self)
'''

STAND_IN_FILES = {
    "deepagents/__init__.py": STAND_IN_DEEPAGENTS,
    "langchain_openai/__init__.py": STAND_IN_LANGCHAIN_OPENAI,
}


@pytest.fixture
def example_module(tmp_path, monkeypatch):
    """A fresh import of the example's `agent.py` on the stand-in libraries, no env set."""
    with fresh_import(
        tmp_path,
        monkeypatch,
        EXAMPLE,
        stand_in=STAND_IN_FILES,
        clear_env=ENVIRONMENT,
        purge=("deepagents", "langchain_openai"),
    ) as load:
        yield load


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", DETECTION_CASES)
def test_example_is_detected_as_deep_agents(tmp_path: Path, where: str) -> None:
    # Nothing named `deepagents` or `langgraph` may sit on the example's path or inside it:
    # the folder is first on the path when it runs, so it would replace the library.
    assert_detected(
        EXAMPLE,
        "deepagents",
        ("deepagents", "langgraph"),
        where=where,
        tmp_path=tmp_path,
        subfolder=SUBFOLDER,
    )


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_the_graph_with_the_langgraph_adapter_and_binds_the_tool_tracer(
    tmp_path: Path, example_module
) -> None:
    agent = build_control_service(
        tmp_path,
        lambda authoring: write_chat_contract(authoring, command=[]),
        "agent",
        subfolder=SUBFOLDER,
        example=EXAMPLE,
    )

    command = json.dumps(agent.run_command)
    assert "ThreadingHTTPServer" in command
    # The adapter's own loader, and not the callback adapter's.
    assert "_load_graph" in command
    assert "ALK_CALLBACK_ENTRYPOINT" not in agent.environment
    assert agent.environment["ALK_LANGGRAPH_ENTRYPOINT"] == "agent.py:graph"
    assert agent.environment["PORT"] == "{{PORT_agent}}"
    assert "requirements.txt" in json.dumps(agent.build_commands)
    assert agent.environment["HARNESS_TOOL_TRACE"] == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
    bindings = json.loads(agent.environment["ALK_TOOL_TRACE_BINDINGS"])
    assert_bindings_match(bindings, example_module(), "agent")


# T3: the tracer and a library-shaped call ---------------------------------------------------

DRIVER = textwrap.dedent(
    """
    import asyncio
    import json
    import threading

    import agent
    import agent_extra
    import deepagents
    from deepagents import Tool

    # Same name as a declared tool, other module: only the module tells them apart.
    unlisted = Tool(agent_extra.look_up_order)
    tools = {tool.name: tool for tool in agent.graph.tools}
    CALLS = [
        (tools["look_up_order"], {"order_id": "A1001"}),
        (tools["start_return"], {"order_id": "A1001", "reason": "too small"}),
        (tools["return_policy"], {}),
        (unlisted, {"order_id": "X9"}),
    ]
    threads = {}


    async def go():
        threads["caller"] = threading.get_ident()
        return [await tool.ainvoke(args) for tool, args in CALLS]


    # The adapter serves each request on a thread started after start-up.
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=asyncio.run(go())))
    worker.start()
    worker.join()
    ran_elsewhere = [
        ident != threads["caller"] and ident != threading.get_ident()
        for ident in deepagents.STATE["executor_threads"]
    ]
    print(json.dumps({"out": result["out"], "ran_elsewhere": ran_elsewhere}))
    """
)


def test_tools_called_the_way_the_library_calls_them_are_traced(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy2(EXAMPLE / "agent.py", run / "agent.py")
    (run / "agent_extra.py").write_text(
        "def look_up_order(order_id):\n    return 'not declared'\n", encoding="utf-8"
    )
    hook = tmp_path / "hook"
    hook.mkdir()
    shutil.copy2(TRACE_HOOK, hook / "sitecustomize.py")
    stand_in = write_stand_ins(tmp_path / "stand-in", STAND_IN_FILES)
    trace = tmp_path / "calls.jsonl"
    bindings = [{"name": name, "module": "agent", "callable": name} for name in TOOLS]

    completed = run_tracer(
        [sys.executable, "-c", DRIVER],
        hook,
        run,
        {
            "PYTHONPATH": os.pathsep.join([str(stand_in), str(run)]),
            "HARNESS_TOOL_TRACE": str(trace),
            "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://gateway.test/v1",
        },
    )

    driven = json.loads(completed.stdout.splitlines()[-1])
    outputs = driven["out"]
    # All four calls, the undeclared one too, ran on a pool thread and not the loop's.
    assert driven["ran_elsewhere"] == [True, True, True, True]
    assert outputs[3] == "not declared"
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [(record["name"], record["arguments"]) for record in records] == [
        ("look_up_order", {"order_id": "A1001"}),
        ("start_return", {"order_id": "A1001", "reason": "too small"}),
        ("return_policy", {}),
    ]
    assert [record["output"] for record in records] == outputs[:3]
    assert not any(record["is_error"] for record in records)


# T4: the wiring -----------------------------------------------------------------------------


def test_model_goes_to_the_gateway_on_chat_completions(example_module, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "custom/model-name")
    agent = example_module()
    import deepagents
    import langchain_openai

    (model,) = langchain_openai.INSTANCES
    assert model.model == agent.MODEL == "custom/model-name"
    assert model.base_url == "http://gateway.test/v1"
    assert model.api_key == "test-key"
    assert model.use_responses_api is False
    (call,) = deepagents.STATE["calls"]
    assert call.model is model


def test_graph_is_built_once_with_the_three_tools_in_order(example_module) -> None:
    agent = example_module()
    import deepagents

    (call,) = deepagents.STATE["calls"]
    assert call.tools == [getattr(agent, name) for name in TOOLS]
    assert [tool.name for tool in agent.graph.tools] == list(TOOLS)
    assert isinstance(call.system_prompt, str) and call.system_prompt.strip()
    assert agent.MODEL == DEFAULT_MODEL
    # Built at import, the way a graph named in langgraph.json is loaded: once.
    assert len(deepagents.STATE["calls"]) == 1


def test_import_without_a_key_succeeds_with_a_placeholder(example_module) -> None:
    example_module()
    import langchain_openai

    # The stand-in refuses a missing key as the real client does, so an import that passed
    # None would have raised above.
    with pytest.raises(RuntimeError, match="api_key"):
        langchain_openai.ChatOpenAI(model="m", api_key=None)
    (model,) = langchain_openai.INSTANCES
    assert model.api_key == "unset"
    assert model.base_url is None
    assert model.model == DEFAULT_MODEL
    assert model.use_responses_api is False


@pytest.mark.parametrize("key", ["", "   "], ids=["empty", "blank"])
def test_an_empty_or_blank_key_also_gets_the_placeholder(example_module, key) -> None:
    example_module(OPENAI_API_KEY=key, OPENAI_BASE_URL=key)
    import langchain_openai

    (model,) = langchain_openai.INSTANCES
    assert model.api_key == "unset"
    assert model.base_url is None


def test_a_key_with_no_base_url_is_refused_at_import(example_module, monkeypatch) -> None:
    def no_client() -> None:
        import langchain_openai

        assert langchain_openai.INSTANCES == []

    assert_refuses_key_without_base_url(example_module, None, monkeypatch, after_refusal=no_client)


@pytest.mark.parametrize("base_url", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_the_refusal_leaves_the_key_out_of_the_examples_frame(example_module, base_url) -> None:
    environment = {"OPENAI_API_KEY": "secret-canary-key"}
    if base_url is not None:
        environment["OPENAI_BASE_URL"] = base_url

    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL") as refused:
        example_module(**environment)

    # Nor in the example's own frame, which a traceback that prints variables would show.
    frames = []
    trace = refused.tb
    while trace is not None:
        frames.append(trace.tb_frame)
        trace = trace.tb_next
    example_frames = [frame for frame in frames if frame.f_code.co_filename.endswith("agent.py")]
    assert example_frames
    assert not any("secret-canary-key" in repr(frame.f_locals) for frame in example_frames)


def test_openai_api_base_alone_does_not_satisfy_the_check(example_module) -> None:
    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL"):
        example_module(OPENAI_API_KEY="secret-canary-key", OPENAI_API_BASE="http://other.test/v1")


def test_the_base_url_reaches_the_client_stripped(example_module) -> None:
    example_module(OPENAI_API_KEY="k", OPENAI_BASE_URL="  http://gateway.test/v1  ")
    import langchain_openai

    (model,) = langchain_openai.INSTANCES
    assert model.base_url == "http://gateway.test/v1"


def test_no_module_name_holds_the_key(example_module) -> None:
    agent = example_module(OPENAI_API_KEY="secret-canary-key", OPENAI_BASE_URL="http://g.test/v1")

    # A traceback that prints module variables would show it.
    assert not [name for name, value in vars(agent).items() if value == "secret-canary-key"]


def test_importing_twice_under_different_environments(example_module) -> None:
    example_module()
    example_module(OPENAI_API_KEY="second-key", OPENAI_BASE_URL="http://two.test/v1")
    import langchain_openai

    # The second import is a fresh module on a fresh stand-in, so it sees only its own.
    (model,) = langchain_openai.INSTANCES
    assert model.api_key == "second-key"
    assert model.base_url == "http://two.test/v1"


# T5: the files ------------------------------------------------------------------------------


def test_requirements_and_python_version_are_pinned() -> None:
    assert_pins(EXAMPLE, ["deepagents==0.7.32", "langchain-openai==1.7.3"])


def test_langgraph_json_declares_exactly_one_graph() -> None:
    declared = json.loads((EXAMPLE / "langgraph.json").read_text(encoding="utf-8"))

    assert declared == {"dependencies": ["."], "graphs": {"support": "./agent.py:graph"}}
    assert (EXAMPLE / "agent.py").is_file()


def test_example_folder_holds_exactly_its_files() -> None:
    # Any other name could shadow a library when the folder is first on the path.
    names = sorted(
        path.name
        for path in EXAMPLE.rglob("*")
        if "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    assert names == sorted(
        [".python-version", "README.md", "agent.py", "langgraph.json", "requirements.txt"]
    )


def test_example_has_plain_tools_and_the_literal_default_model() -> None:
    module = ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))
    assert not [node for node in ast.walk(module) if isinstance(node, ast.AsyncFunctionDef)]
    plain = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
    assert plain == set(TOOLS) | {"_setting"}
    (assignment,) = [
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and [target.id for target in node.targets if isinstance(target, ast.Name)] == ["MODEL"]
    ]
    assert ast.unparse(assignment.value) == f"os.getenv('OPENAI_MODEL', '{DEFAULT_MODEL}')"
    imported = {
        (node.module, alias.name)
        for node in module.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imported == {("deepagents", "create_deep_agent"), ("langchain_openai", "ChatOpenAI")}
