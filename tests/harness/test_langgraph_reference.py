"""The LangGraph reference agent, and how the kit's hosted path handles it.

The libraries are never installed here. Where the example has to be imported, a stand-in
`langgraph` and `langchain_openai` written below take their place, shaped like the public
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
    assert_a_failing_turn_keeps_the_key_out_of_its_frames,
    assert_bindings_match,
    assert_detected,
    assert_pins,
    assert_refuses_key_without_base_url,
    assert_tool_calls_keep_each_conversations_orders,
    build_control_service,
    fresh_import,
    run_tracer,
    write_chat_contract,
    write_stand_ins,
)

SUBFOLDER = "examples/harness/langgraph_support"
EXAMPLE = REPO / SUBFOLDER
ENVIRONMENT = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL")
DEFAULT_MODEL = "vertex_ai/gemini-3.7-flash"

# Follows langgraph 1.2.22 and langgraph-prebuilt 1.1:
# - `StateGraph(MessagesState)` takes nodes and edges and `compile()` returns the graph with
#   `ainvoke`; `START` is "__start__" and `END` is "__end__".
# - `tools_condition(state)` returns "tools" when the last message asks for tool calls, else
#   `END`.
# - `ToolNode(tools)` wraps each function as a tool in `tools_by_name`; `Tool.ainvoke` follows
#   langchain-core (tools/structured.py, runnables/config.py): a plain function goes through
#   `run_in_executor(None, ...)`, the loop's default thread pool, with the context copied.
# - `get_config()` (langgraph/config.py) returns the run's config, whose `configurable` holds
#   what the caller passed, and raises outside a run. A tool the graph runs sees its run's.
# The stand-in graph's run calls the tools `STATE["calls"]` names, through the tool node, and
# answers with the last tool's result.
STAND_IN_GRAPH = """
from langgraph.config import CONFIG

START = "__start__"
END = "__end__"
STATE = {"graphs": [], "calls": []}


class MessagesState(dict):
    pass


class Compiled:
    def __init__(self, builder):
        self.builder = builder

    async def ainvoke(self, state, config=None):
        token = CONFIG.set({"configurable": dict((config or {}).get("configurable", {}))})
        try:
            content = "All done."
            for name, arguments in STATE["calls"]:
                tool = self.builder.nodes["tools"].tools_by_name[name]
                content = await tool.ainvoke(arguments)
        finally:
            CONFIG.reset(token)
        return {"messages": [{"role": "assistant", "content": content}]}


class StateGraph:
    def __init__(self, schema):
        self.schema = schema
        self.nodes = {}
        self.edges = []
        self.conditional = []
        self.compiled = 0
        STATE["graphs"].append(self)

    def add_node(self, name, action):
        assert name not in self.nodes, name
        self.nodes[name] = action

    def add_edge(self, start, end):
        self.edges.append((start, end))

    def add_conditional_edges(self, source, path, path_map=None):
        self.conditional.append((source, path, path_map))

    def compile(self, checkpointer=None, **options):
        self.compiled += 1
        self.checkpointer = checkpointer
        return Compiled(self)
"""

STAND_IN_PREBUILT = """
import asyncio
import inspect
import threading
from contextvars import copy_context
from functools import partial

STATE = {"executor_threads": [], "tool_nodes": []}


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
        self.function = function
        self.is_async = inspect.iscoroutinefunction(function)

    async def ainvoke(self, args_dict):
        if self.is_async:
            return await self.function(**args_dict)
        return await run_in_executor(self.function, **args_dict)


class ToolNode:
    def __init__(self, tools, **options):
        self.functions = list(tools)
        self.tools_by_name = {tool.name: tool for tool in map(Tool, tools)}
        STATE["tool_nodes"].append(self)


def tools_condition(state):
    last = state["messages"][-1]
    return "tools" if getattr(last, "tool_calls", None) else "__end__"
"""

# The real client is built eagerly in the constructor, and the async one it builds refuses a
# missing key (langchain_openai/chat_models/base.py), which is what makes a model built at
# import fail an import in an environment that has none. `bind_tools` returns a new runnable
# over the same client.
STAND_IN_LANGCHAIN_OPENAI = """
from types import SimpleNamespace

INSTANCES = []


class Bound:
    def __init__(self, model, tools):
        self.model = model
        self.tools = tools
        self.calls = []

    async def ainvoke(self, messages, config=None):
        self.calls.append(list(messages))
        return SimpleNamespace(content="All done.", tool_calls=[])


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
        self.bound = []
        INSTANCES.append(self)

    def bind_tools(self, tools, **options):
        bound = Bound(self, list(tools))
        self.bound.append(bound)
        return bound
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
    "langgraph/graph/__init__.py": STAND_IN_GRAPH,
    "langgraph/prebuilt/__init__.py": STAND_IN_PREBUILT,
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
        purge=("langgraph", "langchain_openai"),
    ) as load:
        yield load


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", DETECTION_CASES)
def test_example_is_detected_as_langgraph(tmp_path: Path, where: str) -> None:
    # Nothing named `langgraph` may sit on the example's path or inside it: the folder is first
    # on the path when it runs, so it would replace the library.
    assert_detected(
        EXAMPLE,
        "langgraph",
        ("langgraph", "langchain_openai"),
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
    from langgraph import prebuilt
    from langgraph.config import CONFIG
    from langgraph.prebuilt import Tool

    # Same name as a declared tool, other module: only the module tells them apart.
    unlisted = Tool(agent_extra.look_up_order)
    tools = agent._tool_node.tools_by_name
    CALLS = [
        (tools["look_up_order"], {"order_id": "A1001"}),
        (tools["start_return"], {"order_id": "A1001", "reason": "too small"}),
        (tools["return_policy"], {}),
        (unlisted, {"order_id": "X9"}),
    ]
    threads = {}


    async def go():
        threads["caller"] = threading.get_ident()
        # Inside a run of the graph, as the tool node calls them.
        CONFIG.set({"configurable": {"thread_id": "one"}})
        return [await tool.ainvoke(args) for tool, args in CALLS]


    # The adapter serves each request on a thread started after start-up.
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=asyncio.run(go())))
    worker.start()
    worker.join()
    ran_elsewhere = [
        ident != threads["caller"] and ident != threading.get_ident()
        for ident in prebuilt.STATE["executor_threads"]
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

    (model,) = langchain_openai.INSTANCES
    assert model.model == agent.MODEL == "custom/model-name"
    assert model.base_url == "http://gateway.test/v1"
    assert model.api_key == "test-key"
    assert model.use_responses_api is False
    (bound,) = model.bound
    assert bound is agent._model


def test_the_graph_is_the_tool_loop_built_once(example_module) -> None:
    agent = example_module()
    from langgraph import graph as langgraph_graph
    from langgraph import prebuilt

    (builder,) = langgraph_graph.STATE["graphs"]
    assert builder.schema is langgraph_graph.MessagesState
    assert builder.nodes == {"agent": agent._respond, "tools": agent._tool_node}
    assert builder.edges == [("__start__", "agent"), ("tools", "agent")]
    assert builder.conditional == [("agent", prebuilt.tools_condition, None)]
    # Compiled once at import, the way a graph named in langgraph.json is loaded, and with no
    # checkpointer: the kit sends the whole history each turn.
    assert builder.compiled == 1
    assert builder.checkpointer is None
    assert agent.graph.builder is builder


def test_the_model_and_the_tool_node_get_the_three_tools_in_order(
    example_module,
) -> None:
    agent = example_module()
    import langchain_openai
    from langgraph import prebuilt

    (model,) = langchain_openai.INSTANCES
    (bound,) = model.bound
    (tool_node,) = prebuilt.STATE["tool_nodes"]
    functions = [getattr(agent, name) for name in TOOLS]
    assert bound.tools == functions
    assert tool_node.functions == functions
    assert list(tool_node.tools_by_name) == list(TOOLS)
    assert agent.MODEL == DEFAULT_MODEL


def test_the_model_node_puts_the_prompt_in_front_of_the_conversation(
    example_module,
) -> None:
    agent = example_module()
    history = [{"role": "user", "content": "Where is A1002?"}]

    update = asyncio.run(agent._respond({"messages": history}))

    (sent,) = agent._model.calls
    assert sent == [("system", agent.SYSTEM_PROMPT), *history]
    assert [message.content for message in update["messages"]] == ["All done."]
    # The prompt is not written into the state: the next turn's history won't hold it twice.
    assert history == [{"role": "user", "content": "Where is A1002?"}]


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


def test_a_turn_that_fails_later_keeps_the_key_out_of_its_frames(
    example_module,
) -> None:
    def make_it_fail() -> None:
        import langchain_openai

        async def unreachable(self, messages, config=None):
            raise ConnectionError("model unreachable")

        langchain_openai.Bound.ainvoke = unreachable

    assert_a_failing_turn_keeps_the_key_out_of_its_frames(
        example_module,
        lambda agent: asyncio.run(
            agent._respond({"messages": [("user", "Where is A1001?")]})
        ),
        make_it_fail,
    )


def test_each_conversation_has_its_own_orders(example_module) -> None:
    agent = example_module()
    import langgraph.graph

    def turn(thread_id: str, name: str, **arguments: str) -> str:
        langgraph.graph.STATE["calls"] = [(name, arguments)]
        state = asyncio.run(
            agent.graph.ainvoke(
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
    assert_pins(EXAMPLE, ["langgraph==1.2.22", "langchain-openai==1.7.3"])


def test_langgraph_json_declares_exactly_one_graph() -> None:
    declared = json.loads((EXAMPLE / "langgraph.json").read_text(encoding="utf-8"))

    assert declared == {
        "dependencies": ["."],
        "graphs": {"support": "./agent.py:graph"},
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


def test_example_has_plain_tools_one_async_node_and_the_literal_default_model() -> None:
    module = ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))
    asynchronous = {
        node.name for node in module.body if isinstance(node, ast.AsyncFunctionDef)
    }
    assert asynchronous == {"_respond"}
    plain = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
    assert plain == set(TOOLS) | {"_setting", "_orders"}
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
        ("langchain_openai", "ChatOpenAI"),
        ("langgraph.config", "get_config"),
        ("langgraph.graph", "START"),
        ("langgraph.graph", "MessagesState"),
        ("langgraph.graph", "StateGraph"),
        ("langgraph.prebuilt", "ToolNode"),
        ("langgraph.prebuilt", "tools_condition"),
    }


# The example on the libraries it pins ------------------------------------------------------

PINS = {"langgraph": "1.2.22", "langchain-openai": "1.7.3"}


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
                "ALK_LANGGRAPH_ENTRYPOINT": "agent.py:graph",
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
