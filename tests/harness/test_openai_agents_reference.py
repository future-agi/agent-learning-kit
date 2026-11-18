"""The OpenAI Agents SDK reference agent, and how the kit's hosted path handles it.

The SDK is never installed here. Where the example has to be imported, a stand-in `agents`
and `openai` written below take its place, shaped like the public names the example uses.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import os
import shutil
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from reference_agents import (
    DETECTION_CASES,
    REPO,
    TOOLS,
    TRACE_HOOK,
    assert_bindings_match,
    assert_a_failing_turn_keeps_the_key_out_of_its_frames,
    assert_detected,
    assert_each_conversation_has_its_own_orders,
    assert_pins,
    assert_refuses_key_without_base_url,
    build_control_service,
    fresh_import,
    run_tracer,
    write_callable_contract,
    write_stand_ins,
)

from fi.simulate import AgentInput

SUBFOLDER = "examples/harness/openai_agents_support"
EXAMPLE = REPO / SUBFOLDER
ENVIRONMENT = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")

# `function_tool` and `on_invoke_tool` follow SDK 0.24.0 (tool.py:2761, 2823-2832): a plain
# function is run with `asyncio.to_thread`, a coroutine function is awaited.
STAND_IN_AGENTS = '''
import asyncio
import inspect
import json
from types import SimpleNamespace

STATE = {
    "tracing_disabled": [],
    "agents": [],
    "runs": [],
    "final_output": "All done.",
    "raise": None,
}


def set_tracing_disabled(disabled):
    STATE["tracing_disabled"].append(disabled)


class FunctionTool:
    def __init__(self, func):
        self.name = func.__name__
        self.func = func

    async def on_invoke_tool(self, ctx, input_json):
        args = json.loads(input_json) if input_json else {}
        if inspect.iscoroutinefunction(self.func):
            return await self.func(**args)
        return await asyncio.to_thread(self.func, **args)


def function_tool(func=None, **options):
    if func is None:
        return FunctionTool
    return FunctionTool(func)


class Agent:
    def __init__(self, name, instructions=None, tools=(), **options):
        self.name = name
        self.tools = list(tools)
        self.options = options
        STATE["agents"].append(self)


class OpenAIChatCompletionsModel:
    def __init__(self, model, openai_client):
        self.model = model
        self.openai_client = openai_client


class RunConfig:
    def __init__(self, model=None, **options):
        self.model = model


class Runner:
    @classmethod
    async def run(cls, starting_agent, input, *, run_config=None, **options):
        client = getattr(getattr(run_config, "model", None), "openai_client", None)
        STATE["runs"].append(
            SimpleNamespace(
                agent=starting_agent,
                input=input,
                run_config=run_config,
                client_open=client is not None and not client.closed,
            )
        )
        if STATE["raise"] is not None:
            raise STATE["raise"]
        return SimpleNamespace(final_output=STATE["final_output"])
'''

# The real client refuses to be built without a key, which is what makes a client built at
# import fail an import in an environment that has none.
STAND_IN_OPENAI = '''
CLIENTS = []


class AsyncOpenAI:
    def __init__(self, base_url=None, api_key=None):
        if api_key is None:
            raise RuntimeError("The api_key client option must be set")
        self.base_url = base_url
        self.api_key = api_key
        self.closed = False
        CLIENTS.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        self.closed = True
'''


STAND_IN_FILES = {"agents/__init__.py": STAND_IN_AGENTS, "openai/__init__.py": STAND_IN_OPENAI}


@pytest.fixture
def example_module(tmp_path, monkeypatch):
    """A fresh import of the example's `agent.py` on the stand-in SDK, with no key set."""
    with fresh_import(
        tmp_path,
        monkeypatch,
        EXAMPLE,
        stand_in=STAND_IN_FILES,
        clear_env=ENVIRONMENT,
        purge=("agents", "openai"),
    ) as load:
        yield load


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", DETECTION_CASES)
def test_example_is_detected_as_the_sdk_agent(tmp_path: Path, where: str) -> None:
    # `agents` is the SDK's top-level package, so a package or module of that name anywhere
    # near the example would pass for the customer's own.
    assert_detected(
        EXAMPLE, "openai_agents", "agents", where=where, tmp_path=tmp_path, subfolder=SUBFOLDER
    )


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_the_callback_bridge_and_binds_the_tool_tracer(
    tmp_path: Path, example_module
) -> None:
    agent = build_control_service(
        tmp_path, write_callable_contract, "agent", subfolder=SUBFOLDER, example=EXAMPLE
    )

    assert "ThreadingHTTPServer" in json.dumps(agent.run_command)
    assert agent.environment["ALK_CALLBACK_ENTRYPOINT"] == "agent:agent_callback"
    assert agent.environment["PORT"] == "{{PORT_agent}}"
    assert "requirements.txt" in json.dumps(agent.build_commands)
    assert agent.environment["HARNESS_TOOL_TRACE"] == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
    bindings = json.loads(agent.environment["ALK_TOOL_TRACE_BINDINGS"])
    assert_bindings_match(bindings, example_module(), "agent")


BINDING_SHAPES = textwrap.dedent(
    """
    async def a(): ...


    def outer():
        def b(): ...


    c = lambda: 1


    def d(): ...
    """
)


def _shapes_module(tmp_path: Path):
    path = tmp_path / "shapes.py"
    path.write_text(BINDING_SHAPES, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("shapes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["a", "b", "c"])
def test_bindings_check_refuses_what_the_tracer_would_not_record(
    tmp_path: Path, name: str
) -> None:
    # An async def, a nested def and an assigned lambda can't be bound by code name and module.
    binding = [{"name": name, "module": "m", "callable": name}]
    with pytest.raises(AssertionError):
        assert_bindings_match(binding, _shapes_module(tmp_path), "m", tools=(name,))


def test_bindings_check_accepts_a_plain_top_level_def(tmp_path: Path) -> None:
    binding = [{"name": "d", "module": "m", "callable": "d"}]
    assert_bindings_match(binding, _shapes_module(tmp_path), "m", tools=("d",))


def test_bindings_check_refuses_a_tool_bound_to_another_tools_function(tmp_path: Path) -> None:
    module_source = tmp_path / "pair.py"
    module_source.write_text("def d(): ...\n\n\ndef e(): ...\n", encoding="utf-8")
    spec = importlib.util.spec_from_file_location("pair", module_source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    swapped = [
        {"name": "d", "module": "m", "callable": "e"},
        {"name": "e", "module": "m", "callable": "d"},
    ]
    with pytest.raises(AssertionError):
        assert_bindings_match(swapped, module, "m", tools=("d", "e"))


def test_shadowed_case_fails_when_the_framework_is_still_found_with_a_version(
    tmp_path: Path, monkeypatch
) -> None:
    import reference_agents

    # The first call is the unshadowed copy; the second, after the plant, still finds it.
    found = iter(
        [{"name": "openai_agents", "version": None}, {"name": "openai_agents", "version": "1"}]
    )
    monkeypatch.setattr(reference_agents, "detect_framework", lambda *a, **k: next(found))
    with pytest.raises(AssertionError):
        assert_detected(
            EXAMPLE,
            "openai_agents",
            "agents",
            where="shadowed",
            tmp_path=tmp_path,
            subfolder=SUBFOLDER,
        )


def _refusing_module(tmp_path: Path, monkeypatch, body: str):
    # A stand-in example: `agent.py`, so the check can find its frames.
    path = tmp_path / "refusing" / "agent.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("refusing_agent", path)

    def load(**environment):
        for name, value in environment.items():
            monkeypatch.setenv(name, value)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return load


def test_refusal_check_names_every_case_that_fails(tmp_path: Path, monkeypatch) -> None:
    import reference_agents

    # Refuses only an unset base URL, so the empty and blank cases both fail.
    load = _refusing_module(
        tmp_path,
        monkeypatch,
        """
        import os


        def call():
            if os.environ.get("OPENAI_BASE_URL") is None:
                raise RuntimeError("OPENAI_BASE_URL is empty")
        """,
    )
    with pytest.raises(AssertionError) as failed:
        reference_agents.assert_refuses_key_without_base_url(
            load, lambda module: module.call(), monkeypatch
        )
    assert "''" in str(failed.value)
    assert "'   '" in str(failed.value)


def test_refusal_check_fails_a_per_call_example_that_refuses_at_import(
    tmp_path: Path, monkeypatch
) -> None:
    import reference_agents

    load = _refusing_module(
        tmp_path,
        monkeypatch,
        """
        raise RuntimeError("OPENAI_BASE_URL is empty")


        def call():
            pass
        """,
    )
    with pytest.raises(AssertionError, match="import"):
        reference_agents.assert_refuses_key_without_base_url(
            load, lambda module: module.call(), monkeypatch
        )


def test_refusal_check_finds_the_key_in_the_examples_frame(
    tmp_path: Path, monkeypatch
) -> None:
    import reference_agents

    load = _refusing_module(
        tmp_path,
        monkeypatch,
        """
        import os


        def call():
            key = os.environ.get("OPENAI_API_KEY")
            if key:
                raise RuntimeError("OPENAI_BASE_URL is empty")
        """,
    )
    with pytest.raises(AssertionError, match="frame"):
        reference_agents.assert_refuses_key_without_base_url(
            load, lambda module: module.call(), monkeypatch
        )


# T3: the tracer and an SDK-shaped call ------------------------------------------------------

DRIVER = textwrap.dedent(
    """
    import asyncio
    import json
    import threading

    import agent
    import agent_extra
    from agents import function_tool

    # Same name as a declared tool, other module: only the module tells them apart.
    unlisted = function_tool(agent_extra.look_up_order)
    CALLS = [
        (agent.look_up_order, {"order_id": "A1001"}),
        (agent.start_return, {"order_id": "A1001", "reason": "too small"}),
        (agent.return_policy, {}),
        (unlisted, {"order_id": "X9"}),
    ]


    async def go():
        return [await tool.on_invoke_tool(None, json.dumps(args)) for tool, args in CALLS]


    # The bridge serves each request on a thread started after start-up.
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=asyncio.run(go())))
    worker.start()
    worker.join()
    print(json.dumps(result["out"]))
    """
)


def test_tools_called_the_way_the_sdk_calls_them_are_traced(tmp_path: Path) -> None:
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
        },
    )

    outputs = json.loads(completed.stdout.splitlines()[-1])
    assert outputs[3] == "not declared"
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [(record["name"], record["arguments"]) for record in records] == [
        ("look_up_order", {"order_id": "A1001"}),
        ("start_return", {"order_id": "A1001", "reason": "too small"}),
        ("return_policy", {}),
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


def test_callback_sends_the_conversation_once_and_replies_with_content_only(
    example_module, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()
    import agents
    import openai

    agents.STATE["final_output"] = "Return started."
    # An extra key on an item is the glue's to drop; the SDK takes role and content.
    messages = [{**CONVERSATION[0], "extra": "dropped"}, *CONVERSATION[1:]]

    reply = asyncio.run(agent.agent_callback(_agent_input(messages)))

    assert reply == {"content": "Return started."}
    (run,) = agents.STATE["runs"]
    assert run.input == CONVERSATION
    assert run.input.count(NEW_MESSAGE) == 1
    assert run.agent is agent.agent
    assert [tool.name for tool in agent.agent.tools] == list(TOOLS)
    model = run.run_config.model
    assert isinstance(model, agents.OpenAIChatCompletionsModel)
    assert model.model == agent.MODEL == "vertex_ai/gemini-3.7-flash"
    assert model.openai_client.base_url == "http://gateway.test/v1"
    assert model.openai_client.api_key == "test-key"
    assert run.client_open
    assert model.openai_client.closed
    assert openai.CLIENTS == [model.openai_client]


def test_each_call_builds_and_closes_its_own_client(example_module, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()
    import agents
    import openai

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION[:1])))
    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    first, second = openai.CLIENTS
    assert first is not second
    assert first.closed and second.closed
    assert [run.client_open for run in agents.STATE["runs"]] == [True, True]
    assert [len(run.input) for run in agents.STATE["runs"]] == [1, 3]
    assert len(agents.STATE["agents"]) == 1


def test_import_sets_up_the_agent_without_a_key_or_a_client(example_module) -> None:
    agent = example_module()
    import agents
    import openai

    assert agents.STATE["tracing_disabled"] == [True]
    assert openai.CLIENTS == []
    assert agents.STATE["runs"] == []
    (built,) = agents.STATE["agents"]
    assert built is agent.agent
    # The model comes from the run, not the agent.
    assert "model" not in built.options


def test_each_conversation_has_its_own_orders(example_module, monkeypatch) -> None:
    agent = example_module()

    def call_tool(name, **arguments):
        # Through the SDK's tool object, which runs the function on a worker thread.
        tool = getattr(agent, name)
        return asyncio.run(tool.on_invoke_tool(None, json.dumps(arguments)))

    assert_each_conversation_has_its_own_orders(agent, call_tool)

    # The callback names the conversation from the turn's thread before the run.
    seen = []

    async def run(*args, **kwargs):
        seen.append(agent._CONVERSATION.get())
        return SimpleNamespace(final_output="ok")

    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(agent.Runner, "run", run)
    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))
    assert seen == ["scenario-one"]


def test_a_turn_that_fails_later_keeps_the_key_out_of_its_frames(example_module) -> None:
    def make_it_fail() -> None:
        import agents

        agents.STATE["raise"] = ConnectionError("model unreachable")

    assert_a_failing_turn_keeps_the_key_out_of_its_frames(
        example_module,
        lambda agent: asyncio.run(agent.agent_callback(_agent_input(CONVERSATION))),
        make_it_fail,
    )


def test_a_key_with_no_base_url_is_refused_before_a_client_is_built(
    example_module, monkeypatch
) -> None:
    def nothing_built() -> None:
        import agents
        import openai

        assert openai.CLIENTS == []
        assert agents.STATE["runs"] == []

    assert_refuses_key_without_base_url(
        example_module,
        lambda agent: asyncio.run(agent.agent_callback(_agent_input(CONVERSATION))),
        monkeypatch,
        after_refusal=nothing_built,
    )


def test_sdk_failure_is_not_hidden(example_module, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()
    import agents
    import openai

    agents.STATE["raise"] = RuntimeError("model unreachable")

    with pytest.raises(RuntimeError, match="model unreachable"):
        asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))
    assert openai.CLIENTS[0].closed


def test_example_has_one_async_callback_and_plain_tools() -> None:
    module = ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))
    async_names = [
        node.name for node in module.body if isinstance(node, ast.AsyncFunctionDef)
    ]
    assert async_names == ["agent_callback"]
    plain = {node.name for node in module.body if isinstance(node, ast.FunctionDef)}
    assert plain == set(TOOLS) | {"_setting", "_orders"}


# T5: requirements ---------------------------------------------------------------------------


def test_requirements_and_python_version_are_pinned() -> None:
    assert_pins(EXAMPLE, ["openai-agents==0.24.0", "agent-simulate==0.1.3"])
