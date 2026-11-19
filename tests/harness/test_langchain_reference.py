"""The LangChain reference agent, and how the kit's hosted path handles it.

The libraries are never installed here. Where the example has to be imported, a stand-in
`langchain` and `langchain_openai` written below take their place, shaped like the public
names the example uses.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.metadata
import json
import os
import shutil
import sys
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    assert_tool_calls_keep_each_conversations_orders,
    build_control_service,
    copy_example,
    fresh_import,
    run_tracer,
    write_chat_contract,
    write_stand_ins,
)

from fi.alk.harness.framework_detection import detect_framework

SUBFOLDER = "examples/harness/langchain_support"
EXAMPLE = REPO / SUBFOLDER
ENVIRONMENT = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL")
DEFAULT_MODEL = "vertex_ai/gemini-3.7-flash"

# Follows langchain 1.5.0 and langchain-core 1.8:
# - `create_agent(model, tools, *, system_prompt=...)` wraps each plain function as a tool
#   (it needs a docstring for the description) and returns a compiled graph with `ainvoke`.
# - The graph's tool node runs a plain function through `run_in_executor(None, ...)`, the
#   loop's default thread pool, with the context copied (runnables/config.py).
# - langgraph's `get_config()` (langgraph/config.py) returns the run's config, whose
#   `configurable` holds what the caller passed, and raises outside a run.
# The stand-in graph's run calls the tools `STATE["run"]` names and answers with the last
# tool's result.
STAND_IN_AGENTS = """
import asyncio
import inspect
import threading
from contextvars import copy_context
from functools import partial
from types import SimpleNamespace

from langgraph.config import CONFIG

STATE = {"calls": [], "executor_threads": [], "run": []}


async def run_in_executor(func, *args, **kwargs):
    def wrapper():
        STATE["executor_threads"].append(threading.get_ident())
        return func(*args, **kwargs)

    return await asyncio.get_running_loop().run_in_executor(
        None, partial(copy_context().run, wrapper)
    )


class Tool:
    def __init__(self, function):
        if function.__doc__ is None:
            raise ValueError("Function must have a docstring if description not provided.")
        self.name = function.__name__
        self.func = function
        self.is_async = inspect.iscoroutinefunction(function)

    async def ainvoke(self, args_dict):
        if self.is_async:
            return await self.func(**args_dict)
        return await run_in_executor(self.func, **args_dict)


class Graph:
    def __init__(self, tools):
        self.tools = [Tool(function) for function in tools]

    async def ainvoke(self, state, config=None):
        token = CONFIG.set({"configurable": dict((config or {}).get("configurable", {}))})
        try:
            content = "All done."
            tools = {tool.name: tool for tool in self.tools}
            for name, arguments in STATE["run"]:
                content = await tools[name].ainvoke(arguments)
        finally:
            CONFIG.reset(token)
        return {"messages": [{"role": "assistant", "content": content}]}


def create_agent(model=None, tools=None, *, system_prompt=None, **options):
    STATE["calls"].append(
        SimpleNamespace(
            model=model, tools=list(tools or []), system_prompt=system_prompt, options=options
        )
    )
    return Graph(list(tools or []))
"""

# The real client is built eagerly in the constructor, and the async one it builds refuses a
# missing key (langchain_openai/chat_models/base.py), which is what makes a model built at
# import fail an import in an environment that has none.
STAND_IN_LANGCHAIN_OPENAI = """
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
"""

STAND_IN_CONFIG = """
from contextvars import ContextVar

CONFIG = ContextVar("config", default=None)


def get_config():
    config = CONFIG.get()
    if config is None:
        raise RuntimeError("Called get_config outside of a runnable context")
    return config
"""

STAND_IN_FILES = {
    "langgraph/__init__.py": "",
    "langgraph/config/__init__.py": STAND_IN_CONFIG,
    "langchain/__init__.py": "",
    "langchain/agents/__init__.py": STAND_IN_AGENTS,
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
        purge=("langchain", "langchain_openai"),
    ) as load:
        yield load


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "where", [case for case in DETECTION_CASES if case != "shadowed"]
)
def test_example_is_detected_as_langchain(tmp_path: Path, where: str) -> None:
    # Nothing named `langchain` may sit on the example's path or inside it: the folder is first
    # on the path when it runs, so it would replace the library. langgraph.json doesn't make it
    # a LangGraph agent: detection reads the imports.
    assert_detected(
        EXAMPLE,
        "langchain",
        ("langchain", "langchain_openai"),
        where=where,
        tmp_path=tmp_path,
        subfolder=SUBFOLDER,
    )


def test_a_folder_that_shadows_both_imports_is_not_detected_as_langchain(
    tmp_path: Path,
) -> None:
    # The shared "shadowed" case plants one module. Both of this example's imports are
    # LangChain's (`langchain` and `langchain_openai`), so either one alone still finds it:
    # only both planted show the cases above prove something.
    run = copy_example(EXAMPLE, tmp_path / "run")
    assert detect_framework(run) == {"name": "langchain", "version": None}
    for name in ("langchain", "langchain_openai"):
        (run / f"{name}.py").write_text(
            "def helper():\n    return 1\n", encoding="utf-8"
        )

    assert detect_framework(run).get("name") != "langchain"


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_the_agent_with_the_langgraph_adapter_and_binds_the_tool_tracer(
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
    # The adapter's own loader, and not the callback bridge, whose loop per request would
    # strand a client built once.
    assert "_load_graph" in command
    assert "ALK_CALLBACK_ENTRYPOINT" not in agent.environment
    assert agent.environment["ALK_LANGGRAPH_ENTRYPOINT"] == "agent.py:agent"
    assert agent.environment["PORT"] == "{{PORT_agent}}"
    assert "requirements.txt" in json.dumps(agent.build_commands)
    assert (
        agent.environment["HARNESS_TOOL_TRACE"]
        == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
    )
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
    from langchain import agents
    from langchain.agents import Tool
    from langgraph.config import CONFIG

    # Same name as a declared tool, other module: only the module tells them apart.
    unlisted = Tool(agent_extra.look_up_order)
    tools = {tool.name: tool for tool in agent.agent.tools}
    CALLS = [
        (tools["look_up_order"], {"order_id": "A1001"}),
        (tools["start_return"], {"order_id": "A1001", "reason": "too small"}),
        (tools["return_policy"], {}),
        (unlisted, {"order_id": "X9"}),
    ]
    threads = {}


    async def go():
        threads["caller"] = threading.get_ident()
        # Inside a run of the agent, as its tool node calls them.
        CONFIG.set({"configurable": {"thread_id": "one"}})
        return [await tool.ainvoke(args) for tool, args in CALLS]


    # The adapter serves each request on a thread started after start-up.
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=asyncio.run(go())))
    worker.start()
    worker.join()
    ran_elsewhere = [
        ident != threads["caller"] and ident != threading.get_ident()
        for ident in agents.STATE["executor_threads"]
    ]
    print(json.dumps({"out": result["out"], "ran_elsewhere": ran_elsewhere}))
    """
)


def test_tools_called_the_way_the_library_calls_them_are_traced(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy2(EXAMPLE / "agent.py", run / "agent.py")
    (run / "agent_extra.py").write_text(
        "def look_up_order(order_id):\n    '''Not declared.'''\n    return 'not declared'\n",
        encoding="utf-8",
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
    records = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    assert [(record["name"], record["arguments"]) for record in records] == [
        ("look_up_order", {"order_id": "A1001"}),
        ("start_return", {"order_id": "A1001", "reason": "too small"}),
        ("return_policy", {}),
    ]
    assert [record["output"] for record in records] == outputs[:3]
    assert not any(record["is_error"] for record in records)


# T4: the wiring -----------------------------------------------------------------------------


def test_model_goes_to_the_gateway_on_chat_completions(
    example_module, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "custom/model-name")
    agent = example_module()
    import langchain_openai
    from langchain import agents

    (model,) = langchain_openai.INSTANCES
    assert model.model == agent.MODEL == "custom/model-name"
    assert model.base_url == "http://gateway.test/v1"
    assert model.api_key == "test-key"
    assert model.use_responses_api is False
    (call,) = agents.STATE["calls"]
    assert call.model is model


def test_the_agent_is_built_once_with_the_three_tools_in_order(example_module) -> None:
    agent = example_module()
    from langchain import agents

    (call,) = agents.STATE["calls"]
    assert call.tools == [getattr(agent, name) for name in TOOLS]
    assert [tool.name for tool in agent.agent.tools] == list(TOOLS)
    assert call.system_prompt == agent.SYSTEM_PROMPT
    assert agent.MODEL == DEFAULT_MODEL
    # Built at import, the way the documentation writes it and a graph named in
    # langgraph.json is loaded: once.
    assert len(agents.STATE["calls"]) == 1


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


def test_a_key_with_no_base_url_is_refused_at_import(
    example_module, monkeypatch
) -> None:
    def no_client() -> None:
        import langchain_openai

        assert langchain_openai.INSTANCES == []

    assert_refuses_key_without_base_url(
        example_module, None, monkeypatch, after_refusal=no_client
    )


def test_each_conversation_has_its_own_orders(example_module) -> None:
    agent = example_module()
    from langchain import agents

    def turn(thread_id: str, name: str, **arguments: str) -> str:
        agents.STATE["run"] = [(name, arguments)]
        state = asyncio.run(
            agent.agent.ainvoke(
                {"messages": [("user", "hi")]},
                config={"configurable": {"thread_id": thread_id}},
            )
        )
        return state["messages"][-1]["content"]

    assert_tool_calls_keep_each_conversations_orders(turn)


def test_openai_api_base_alone_does_not_satisfy_the_check(example_module) -> None:
    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL"):
        example_module(
            OPENAI_API_KEY="secret-canary-key", OPENAI_API_BASE="http://other.test/v1"
        )


def test_the_base_url_reaches_the_client_stripped(example_module) -> None:
    example_module(OPENAI_API_KEY="k", OPENAI_BASE_URL="  http://gateway.test/v1  ")
    import langchain_openai

    (model,) = langchain_openai.INSTANCES
    assert model.base_url == "http://gateway.test/v1"


def test_no_module_name_holds_the_key(example_module) -> None:
    agent = example_module(
        OPENAI_API_KEY="secret-canary-key", OPENAI_BASE_URL="http://g.test/v1"
    )

    # A traceback that prints module variables would show it.
    assert not [
        name for name, value in vars(agent).items() if value == "secret-canary-key"
    ]


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
    assert_pins(EXAMPLE, ["langchain==1.5.0", "langchain-openai==1.7.3"])


def test_langgraph_json_declares_exactly_the_agent() -> None:
    declared = json.loads((EXAMPLE / "langgraph.json").read_text(encoding="utf-8"))

    assert declared == {
        "dependencies": ["."],
        "graphs": {"support": "./agent.py:agent"},
    }
    assert (EXAMPLE / "agent.py").is_file()


def test_example_folder_holds_exactly_its_files() -> None:
    # Any other name could shadow a library when the folder is first on the path.
    names = sorted(
        path.name
        for path in EXAMPLE.rglob("*")
        if "__pycache__" not in path.parts and path.suffix != ".pyc"
    )
    assert names == sorted(
        [
            ".python-version",
            "README.md",
            "agent.py",
            "langgraph.json",
            "requirements.txt",
        ]
    )


def test_example_has_plain_tools_a_module_level_agent_and_the_literal_default_model() -> (
    None
):
    module = ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))
    assert not [
        node for node in ast.walk(module) if isinstance(node, ast.AsyncFunctionDef)
    ]
    plain = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
    assert plain == set(TOOLS) | {"_setting", "_orders"}
    assigned = {
        target.id
        for node in module.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    assert "agent" in assigned
    (assignment,) = [
        node
        for node in module.body
        if isinstance(node, ast.Assign)
        and [target.id for target in node.targets if isinstance(target, ast.Name)]
        == ["MODEL"]
    ]
    assert (
        ast.unparse(assignment.value) == f"os.getenv('OPENAI_MODEL', '{DEFAULT_MODEL}')"
    )
    imported = {
        (node.module, alias.name)
        for node in module.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imported == {
        ("langchain.agents", "create_agent"),
        ("langchain_openai", "ChatOpenAI"),
        ("langgraph.config", "get_config"),
    }


# The example on the libraries it pins ------------------------------------------------------

PINS = {"langchain": "1.5.0", "langchain-openai": "1.7.3"}


def _require_pins() -> None:
    for name, version in PINS.items():
        try:
            found = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            pytest.skip(f"{name} is not installed")
        if found != version:
            pytest.skip(f"{name} is {found}, not the pinned {version}")


class _ChatCompletions(BaseHTTPRequestHandler):
    """A chat-completions endpoint that asks for `start_return(A1001)`, then
    `look_up_order(A1001)`, then answers with what the tools returned."""

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size) or b"{}")
        results = [m["content"] for m in request["messages"] if m.get("role") == "tool"]
        steps = [
            ("start_return", {"order_id": "A1001", "reason": "too small"}),
            ("look_up_order", {"order_id": "A1001"}),
        ]
        message: dict = {"role": "assistant", "content": None}
        if len(results) < len(steps):
            name, arguments = steps[len(results)]
            message["tool_calls"] = [
                {
                    "id": f"call_{len(results)}",
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)},
                }
            ]
            finish = "tool_calls"
        else:
            message["content"] = " | ".join(results)
            finish = "stop"
        body = {
            "id": "chatcmpl-stub",
            "object": "chat.completion",
            "created": 0,
            "model": request.get("model", "stub"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        encoded = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args) -> None:
        return


PINNED_DRIVER = textwrap.dedent(
    """
    import json

    from fi.alk.harness import langgraph_http_adapter as adapter

    replies = [
        adapter._invoke(
            {"thread_id": thread, "new_message": {"content": "Return A1001, it's too small."}}
        )["content"]
        for thread in ("one", "two")
    ]
    adapter.close()
    print(json.dumps(replies))
    """
)


def test_on_the_pinned_libraries_each_thread_has_its_own_orders_and_every_call_is_traced(
    tmp_path: Path,
) -> None:
    # A turn through the kit's adapter on the real libraries, against a local endpoint.
    _require_pins()
    run = tmp_path / "run"
    shutil.copytree(EXAMPLE, run)
    hook = tmp_path / "hook"
    hook.mkdir()
    shutil.copy2(TRACE_HOOK, hook / "sitecustomize.py")
    trace = tmp_path / "calls.jsonl"
    bindings = [{"name": name, "module": "agent", "callable": name} for name in TOOLS]
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ChatCompletions)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        completed = run_tracer(
            [sys.executable, "-c", PINNED_DRIVER],
            hook,
            run,
            {
                "PYTHONPATH": os.pathsep.join([str(REPO / "src"), str(run)]),
                "ALK_LANGGRAPH_ENTRYPOINT": "agent.py:agent",
                "HARNESS_TOOL_TRACE": str(trace),
                "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}/v1",
                "OPENAI_API_BASE": None,
                "OPENAI_MODEL": "stub-model",
            },
        )
    finally:
        server.shutdown()
        server.server_close()

    one, two = json.loads(completed.stdout.splitlines()[-1])
    # Thread "two" starts its own return: the first thread's is not there.
    expected = "Return started for order A1001: too small. | Order A1001: trail running shoes, "
    assert one.startswith(expected), one
    assert two.startswith(expected), two
    records = [
        json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()
    ]
    assert [record["name"] for record in records] == [
        "start_return",
        "look_up_order",
    ] * 2
    assert not any(record["is_error"] for record in records)
