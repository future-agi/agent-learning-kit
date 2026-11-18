"""The LangChain reference agent, and how the kit's hosted path handles it.

LangChain is never installed here. Where the example has to be imported, a stand-in
`langchain` and `langchain_openai` written below take their place, shaped like the public
names the example uses.
"""

from __future__ import annotations

import ast
import asyncio
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
    assert_a_failing_turn_keeps_the_key_out_of_its_frames,
    assert_bindings_match,
    assert_detected,
    assert_each_conversation_has_its_own_orders,
    assert_pins,
    assert_refuses_key_without_base_url,
    assert_tool_calls_keep_each_conversations_orders,
    build_control_service,
    fresh_import,
    run_tracer,
    write_callable_contract,
    write_stand_ins,
)

from fi.simulate import AgentInput

SUBFOLDER = "examples/harness/langchain_support"
EXAMPLE = REPO / SUBFOLDER
DEFAULT_MODEL = "vertex_ai/gemini-3.7-flash"

# Follows langchain 1.5.0 and langchain-core 1.8:
# - `create_agent(model, tools, *, system_prompt=...)` wraps each plain function as a tool
#   (it needs a docstring for the description) and returns a compiled graph with `ainvoke`,
#   taking and returning `{"messages": [...]}`.
# - The graph's tool node runs a plain function through `run_in_executor(None, ...)`, the
#   loop's default thread pool, with the context copied (runnables/config.py).
# - A message's `.text` is its text (a property since langchain-core 1.0).
STAND_IN_AGENTS = '''
import asyncio
import inspect
import threading
from contextvars import copy_context
from functools import partial
from types import SimpleNamespace

STATE = {
    "agents": [],
    "invocations": [],
    "script": [],
    "results": [],
    "executor_threads": [],
    "output": "All done.",
    "raise": None,
}


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


class Message:
    def __init__(self, text):
        self.text = text

    # Unlike langchain-core, where str() is the content: it differs here so a test sees
    # whether the agent reads `.text`.
    def __str__(self):
        return "AIMessage(" + self.text + ")"


class Agent:
    def __init__(self, model, tools, system_prompt):
        self.model = model
        self.tools = [tool if isinstance(tool, Tool) else Tool(tool) for tool in tools]
        self.system_prompt = system_prompt

    def invoke(self, state, config=None):
        raise AssertionError("the sync invoke blocks the callback's event loop")

    async def ainvoke(self, state, config=None):
        STATE["invocations"].append(SimpleNamespace(agent=self, state=state))
        for entry, arguments in STATE["script"]:
            tool = next((t for t in self.tools if t.name == entry), entry)
            STATE["results"].append(await tool.ainvoke(arguments))
        if STATE["raise"] is not None:
            raise STATE["raise"]
        return {"messages": [*state["messages"], Message(STATE["output"])]}


def create_agent(model, tools=None, *, system_prompt=None, **options):
    agent = Agent(model, list(tools or []), system_prompt)
    STATE["agents"].append(agent)
    return agent
'''

# The real client is built eagerly in the constructor and refuses a missing key, reading
# OPENAI_API_KEY when none is passed (langchain_openai/chat_models/base.py).
STAND_IN_LANGCHAIN_OPENAI = '''
import os

INSTANCES = []


class ChatOpenAI:
    def __init__(
        self, *, model=None, base_url=None, api_key=None, use_responses_api=None, **options
    ):
        if api_key is None and not os.environ.get("OPENAI_API_KEY"):
            raise ValueError("The api_key client option must be set")
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.use_responses_api = use_responses_api
        self.options = options
        INSTANCES.append(self)
'''

STAND_IN_FILES = {
    "langchain/__init__.py": "",
    "langchain/agents/__init__.py": STAND_IN_AGENTS,
    "langchain_openai/__init__.py": STAND_IN_LANGCHAIN_OPENAI,
}

CLEARED_ENV = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL")


@pytest.fixture
def example_module(tmp_path, monkeypatch):
    """A fresh import of the example's `agent.py` on the stand-in library, with nothing set."""
    with fresh_import(
        tmp_path,
        monkeypatch,
        EXAMPLE,
        stand_in=STAND_IN_FILES,
        clear_env=CLEARED_ENV,
        purge=("langchain", "langchain_openai"),
    ) as load:
        yield load


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", [case for case in DETECTION_CASES if case != "shadowed"])
def test_example_is_detected_as_langchain(tmp_path: Path, where: str) -> None:
    # `langchain` is the library's top-level package, so a package or module of that name near
    # the example would pass for the customer's own.
    assert_detected(
        EXAMPLE,
        "langchain",
        ("langchain", "langchain_openai"),
        where=where,
        tmp_path=tmp_path,
        subfolder=SUBFOLDER,
    )


def test_a_folder_that_shadows_both_imports_is_not_detected_as_langchain(tmp_path: Path) -> None:
    # The shared "shadowed" case plants one module. Both of this example's imports are
    # LangChain's (`langchain` and `langchain_openai`), so either one alone still finds it:
    # only both planted show the cases above prove something.
    from reference_agents import copy_example

    from fi.alk.harness.framework_detection import detect_framework

    run = copy_example(EXAMPLE, tmp_path / "run")
    assert detect_framework(run) == {"name": "langchain", "version": None}
    for name in ("langchain", "langchain_openai"):
        (run / f"{name}.py").write_text("def helper():\n    return 1\n", encoding="utf-8")

    assert detect_framework(run).get("name") != "langchain"


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_the_callback_bridge_with_the_generic_tool_tracer(
    tmp_path: Path, example_module
) -> None:
    agent = build_control_service(
        tmp_path, write_callable_contract, "agent", subfolder=SUBFOLDER, example=EXAMPLE
    )

    assert "ThreadingHTTPServer" in json.dumps(agent.run_command)
    assert agent.environment["ALK_CALLBACK_ENTRYPOINT"] == "agent:agent_callback"
    # No langgraph.json, so not the LangGraph adapter, though create_agent returns a graph.
    assert "ALK_LANGGRAPH_ENTRYPOINT" not in agent.environment
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
    import os
    import threading
    from types import SimpleNamespace

    import agent
    import agent_extra
    from langchain import agents
    from langchain.agents import Tool

    names = json.loads(os.environ["TOOL_NAMES"])
    # Same name as a declared tool, other module: only the module tells them apart.
    unlisted = Tool(getattr(agent_extra, names[0]))
    agents.STATE["script"] = [
        (names[0], {"order_id": "A1001"}),
        (names[1], {"order_id": "A1001", "reason": "too small"}),
        (names[2], {}),
        (unlisted, {"order_id": "X9"}),
    ]
    messages = [{"role": "user", "content": "Return A1001, it is too small."}]
    threads = {}


    async def go():
        threads["caller"] = threading.get_ident()
        return await agent.agent_callback(SimpleNamespace(thread_id="t", messages=messages))


    # The bridge serves each request on a thread started after start-up.
    worker = threading.Thread(target=lambda: asyncio.run(go()))
    worker.start()
    worker.join()
    ran_elsewhere = [
        ident != threads["caller"] and ident != threading.get_ident()
        for ident in agents.STATE["executor_threads"]
    ]
    print(json.dumps({"out": agents.STATE["results"], "ran_elsewhere": ran_elsewhere}))
    """
)


def test_tools_called_the_way_langchain_calls_them_are_traced(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy2(EXAMPLE / "agent.py", run / "agent.py")
    (run / "agent_extra.py").write_text(
        f'def {TOOLS[0]}(order_id: str) -> str:\n    """Not a declared tool."""\n'
        "    return 'not declared'\n",
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
            **dict.fromkeys(CLEARED_ENV),
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "http://gateway.test/v1",
            "PYTHONPATH": os.pathsep.join([str(stand_in), str(run)]),
            "TOOL_NAMES": json.dumps(TOOLS),
            "HARNESS_TOOL_TRACE": str(trace),
            "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
        },
    )

    driven = json.loads(completed.stdout.splitlines()[-1])
    outputs = driven["out"]
    # All four calls, the undeclared one too, ran on a pool thread and not the loop's.
    assert driven["ran_elsewhere"] == [True, True, True, True]
    assert outputs[3] == "not declared"
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [(record["name"], record["arguments"]) for record in records] == [
        (TOOLS[0], {"order_id": "A1001"}),
        (TOOLS[1], {"order_id": "A1001", "reason": "too small"}),
        (TOOLS[2], {}),
    ]
    assert [record["output"] for record in records] == outputs[:3]
    assert not any(record["is_error"] for record in records)


# T4: the callback's glue --------------------------------------------------------------------

NEW_MESSAGE = {"role": "user", "content": "Please start a return for A1001."}
CONVERSATION = [
    {"role": "user", "content": "Where is my order A1001?"},
    {"role": "assistant", "content": "It was delivered on Monday."},
    NEW_MESSAGE,
]


def _agent_input(messages: list[dict]) -> AgentInput:
    # Built as the hosted chat target builds it for each turn: the whole conversation, ending
    # with the new message, and `new_message` a copy of that last item.
    return AgentInput(
        thread_id="scenario-one",
        execution_id="run-one",
        turn_index=1,
        scenario_name="scenario-one",
        modality="text",
        messages=list(messages),
        new_message=dict(messages[-1]),
        tools=[],
    )


def _gateway(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_callback_sends_the_conversation_once_and_replies_with_text_only(
    example_module, monkeypatch
) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    from langchain import agents

    agents.STATE["output"] = "Return started."
    # A system item and an extra key are the glue's to drop: only user and assistant turns
    # are passed on, as role and content.
    messages = [
        {"role": "system", "content": "ignored"},
        {**CONVERSATION[0], "extra": "dropped"},
        *CONVERSATION[1:],
    ]

    reply = asyncio.run(agent.agent_callback(_agent_input(messages)))

    # `str()` of the stand-in's message differs from its `.text`, so only `.text` gives this.
    assert reply == {"content": "Return started."}
    (invocation,) = agents.STATE["invocations"]
    assert invocation.state == {"messages": CONVERSATION}
    sent = [m["content"] for m in invocation.state["messages"]]
    assert sent.count(NEW_MESSAGE["content"]) == 1


def test_first_turn_sends_the_one_message(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    from langchain import agents

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION[:1])))

    (invocation,) = agents.STATE["invocations"]
    assert invocation.state == {"messages": CONVERSATION[:1]}


def test_model_goes_to_the_gateway_on_chat_completions(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    monkeypatch.setenv("OPENAI_MODEL", "custom/model-name")
    agent = example_module()
    import langchain_openai
    from langchain import agents

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    (model,) = langchain_openai.INSTANCES
    assert model.model == agent.MODEL == "custom/model-name"
    assert model.base_url == "http://gateway.test/v1"
    assert model.api_key == "test-key"
    assert model.use_responses_api is False
    (built,) = agents.STATE["agents"]
    assert built.model is model
    assert built.system_prompt == agent.SYSTEM_PROMPT


def test_each_call_builds_its_own_agent_and_model(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    import langchain_openai
    from langchain import agents

    # Nothing is built by the import: the client's connection belongs to one event loop.
    assert agents.STATE["agents"] == []
    assert langchain_openai.INSTANCES == []

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION[:1])))
    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    first, second = agents.STATE["agents"]
    assert first is not second
    assert first.model is not second.model
    assert [len(i.state["messages"]) for i in agents.STATE["invocations"]] == [1, 3]


def test_agent_gets_the_three_plain_functions_in_order(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    from langchain import agents

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    (built,) = agents.STATE["agents"]
    assert [t.name for t in built.tools] == list(TOOLS)
    assert [t.func for t in built.tools] == [getattr(agent, name) for name in TOOLS]
    # The module keeps the plain functions, which is what the tool tracer is bound to.
    for name in TOOLS:
        assert not hasattr(getattr(agent, name), "func")


def test_import_needs_no_key_and_no_base_url(example_module) -> None:
    agent = example_module()
    import langchain_openai
    from langchain import agents

    assert langchain_openai.INSTANCES == []
    assert agents.STATE["agents"] == []
    assert agent.MODEL == DEFAULT_MODEL


def test_each_conversation_has_its_own_orders(example_module, monkeypatch) -> None:
    agent = example_module()
    assert_each_conversation_has_its_own_orders(agent)

    # The same through the callback, with LangChain running each tool on a pool thread.
    from langchain import agents

    _gateway(monkeypatch)

    def turn(thread_id: str, name: str, **arguments: str) -> str:
        agents.STATE["script"] = [(name, arguments)]
        agents.STATE["results"] = []
        turn_input = _agent_input(CONVERSATION).model_copy(update={"thread_id": thread_id})
        asyncio.run(agent.agent_callback(turn_input))
        (result,) = agents.STATE["results"]
        return result

    assert_tool_calls_keep_each_conversations_orders(turn)


def test_a_turn_that_fails_later_keeps_the_key_out_of_its_frames(example_module) -> None:
    def make_it_fail() -> None:
        from langchain import agents

        agents.STATE["raise"] = ConnectionError("model unreachable")

    assert_a_failing_turn_keeps_the_key_out_of_its_frames(
        example_module,
        lambda agent: asyncio.run(agent.agent_callback(_agent_input(CONVERSATION))),
        make_it_fail,
    )


def test_a_key_with_no_base_url_is_refused_before_a_model_is_built(
    example_module, monkeypatch
) -> None:
    def nothing_built() -> None:
        import langchain_openai

        assert langchain_openai.INSTANCES == []

    assert_refuses_key_without_base_url(
        example_module,
        lambda agent: asyncio.run(agent.agent_callback(_agent_input(CONVERSATION))),
        monkeypatch,
        after_refusal=nothing_built,
    )


def test_openai_api_base_alone_does_not_satisfy_the_check(example_module, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_BASE", "http://other.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-canary-key")
    agent = example_module()
    import langchain_openai

    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL"):
        asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))
    assert langchain_openai.INSTANCES == []


def test_a_turn_with_neither_key_nor_base_url_fails_with_the_library_error(
    example_module,
) -> None:
    agent = example_module()

    with pytest.raises(ValueError, match="api_key client option must be set"):
        asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))


def test_library_failure_is_not_hidden(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    from langchain import agents

    agents.STATE["raise"] = RuntimeError("model unreachable")

    with pytest.raises(RuntimeError, match="model unreachable"):
        asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))


# T5: the example's shape and its requirements -----------------------------------------------


def _module() -> ast.Module:
    return ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))


def test_example_has_one_async_callback_and_three_plain_tools() -> None:
    module = _module()
    async_names = [node.name for node in module.body if isinstance(node, ast.AsyncFunctionDef)]
    assert async_names == ["agent_callback"]
    plain = [node.name for node in module.body if isinstance(node, ast.FunctionDef)]
    assert plain == ["_orders", *TOOLS, "_setting"]
    # create_agent takes a plain function's docstring as its description.
    assert all(
        ast.get_docstring(node)
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name in TOOLS
    )
    imported = {
        (node.module, alias.name)
        for node in module.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert imported == {("langchain.agents", "create_agent"), ("langchain_openai", "ChatOpenAI")}


def test_default_model_is_the_gateway_name() -> None:
    (assignment,) = [
        node
        for node in _module().body
        if isinstance(node, ast.Assign)
        and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["MODEL"]
    ]
    assert ast.unparse(assignment.value) == f"os.getenv('OPENAI_MODEL', '{DEFAULT_MODEL}')"


def test_folder_holds_exactly_the_example_files() -> None:
    # Any other name could shadow a library when the folder is first on the path.
    names = sorted(path.name for path in EXAMPLE.iterdir() if path.name != "__pycache__")
    assert names == [".python-version", "README.md", "agent.py", "requirements.txt"]


def test_requirements_and_python_version_are_pinned() -> None:
    assert_pins(
        EXAMPLE, ["langchain==1.5.0", "langchain-openai==1.7.3", "agent-simulate==0.1.3"]
    )
