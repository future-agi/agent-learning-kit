"""The CrewAI reference agent, and how the kit's hosted path handles it.

CrewAI is never installed here. Where the example has to be imported, a stand-in `crewai`
written below takes its place, shaped like the public names the example uses.
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
    assert_detected,
    assert_pins,
    build_control_service,
    fresh_import,
    run_tracer,
    write_callable_contract,
    write_stand_ins,
)

from fi.simulate import AgentInput

SUBFOLDER = "examples/harness/crewai_support"
EXAMPLE = REPO / SUBFOLDER

# Follows CrewAI 1.15.33:
# - `tool(fn)` needs a docstring and returns `Tool(name=fn.__name__, func=fn)`
#   (tools/base_tool.py:761-798); `Tool.run` calls `func(**kwargs)` on the caller's thread
#   and runs a coroutine the function returns (base_tool.py:576-580).
# - `LLM(custom_openai=True)` raises ValueError with no base URL in the call or in
#   `OPENAI_BASE_URL` / `OPENAI_API_BASE` (llm.py:481-486, 591-597), and builds no client, so
#   it works without a key.
# - `Agent.kickoff_async(messages)` returns a `LiteAgentOutput` whose `.raw` is the reply
#   (lite_agent_output.py:37). The sync `kickoff` inside a running loop returns a coroutine
#   (agent/core.py:1853-1859), so here it fails.
# - The executor runs each tool on a worker thread (`asyncio.to_thread`) that is started
#   once the agent is running, long after the process has started.
# - `import crewai` is where telemetry starts, so the environment is recorded at import.
STAND_IN_CREWAI = """
import asyncio
import inspect
import os
from types import SimpleNamespace

IMPORT_ENV = {
    name: os.environ.get(name)
    for name in (
        "CREWAI_DISABLE_TELEMETRY",
        "CREWAI_TRACING_ENABLED",
        "OTEL_SDK_DISABLED",
        "CREWAI_DISABLE_TRACKING",
    )
}
STATE = {
    "agents": [],
    "llms": [],
    "kickoffs": [],
    "script": [],
    "results": [],
    "output": "All done.",
    "raise": None,
}


class LLM:
    def __init__(self, model, *, custom_openai=False, base_url=None, api_key=None, **options):
        if custom_openai and not (
            base_url or os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
        ):
            raise ValueError(
                "custom_openai=True requires base_url, api_base, "
                "OPENAI_BASE_URL, or OPENAI_API_BASE"
            )
        self.model = model
        self.custom_openai = custom_openai
        self.base_url = base_url
        self.api_key = api_key
        STATE["llms"].append(self)


class Result:
    def __init__(self, raw):
        self.raw = raw

    # Unlike 1.15.33, where str() is the raw text (lite_agent_output.py:136-138): it differs
    # here so a test sees whether the agent reads `.raw`, the field the reply comes from.
    def __str__(self):
        return "LiteAgentOutput(" + self.raw + ")"


class Agent:
    def __init__(self, *, role, goal, backstory, tools=(), llm=None, **options):
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.tools = list(tools)
        self.llm = llm
        STATE["agents"].append(self)

    def kickoff(self, messages, **options):
        raise AssertionError("the sync kickoff is not usable inside a running loop")

    async def kickoff_async(self, messages, **options):
        assert isinstance(messages, list)
        STATE["kickoffs"].append(SimpleNamespace(agent=self, messages=messages))
        for entry, arguments in STATE["script"]:
            tool = next((t for t in self.tools if t.name == entry), entry)
            STATE["results"].append(await asyncio.to_thread(tool.run, **arguments))
        if STATE["raise"] is not None:
            raise STATE["raise"]
        return Result(STATE["output"])
"""

STAND_IN_TOOLS = """
import asyncio
import inspect


class Tool:
    def __init__(self, name, func):
        self.name = name
        self.func = func

    def run(self, *args, **kwargs):
        result = self.func(*args, **kwargs)
        if asyncio.iscoroutine(result):
            result = asyncio.run(result)
        return result


def tool(fn):
    if fn.__doc__ is None:
        raise ValueError("Function must have a docstring")
    return Tool(name=fn.__name__, func=fn)
"""

STAND_IN_FILES = {
    "crewai/__init__.py": STAND_IN_CREWAI,
    "crewai/tools/__init__.py": STAND_IN_TOOLS,
}

# These are cleared so the test sees only what the example sets.
CLEARED_ENV = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_API_BASE",
    "OPENAI_MODEL",
    "CREWAI_DISABLE_TELEMETRY",
    "CREWAI_TRACING_ENABLED",
    "OTEL_SDK_DISABLED",
    "CREWAI_DISABLE_TRACKING",
)


@pytest.fixture
def example_module(tmp_path, monkeypatch):
    """A fresh import of the example's `agent.py` on the stand-in library, with nothing set."""
    with fresh_import(
        tmp_path,
        monkeypatch,
        EXAMPLE,
        stand_in=STAND_IN_FILES,
        clear_env=CLEARED_ENV,
        purge=("crewai",),
    ) as load:
        yield load


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", DETECTION_CASES)
def test_example_is_detected_as_crewai(tmp_path: Path, where: str) -> None:
    # `crewai` is the library's top-level package, so a package or module of that name near
    # the example would pass for the customer's own.
    assert_detected(
        EXAMPLE, "crewai", "crewai", where=where, tmp_path=tmp_path, subfolder=SUBFOLDER
    )


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_the_callback_bridge_with_the_generic_tool_tracer(
    tmp_path: Path,
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
    assert sorted(bindings, key=lambda item: item["name"]) == sorted(
        ({"name": name, "module": "agent", "callable": name} for name in TOOLS),
        key=lambda item: item["name"],
    )
    # The kit's CrewAI observer is for command-run agents. A callback agent is traced by the
    # generic tracer alone.
    assert "ALK_CREWAI_TOOL_TRACE" not in agent.environment
    assert "ALK_CREWAI_TOOL_TRACE" not in json.dumps(agent.run_command)


# T3: the tracer and a library-shaped call ---------------------------------------------------

DRIVER = textwrap.dedent(
    """
    import asyncio
    import json
    import os
    from types import SimpleNamespace

    import agent
    import agent_extra
    import crewai
    from crewai.tools import tool

    names = json.loads(os.environ["TOOL_NAMES"])
    # Same name as a declared tool, other module: only the module tells them apart.
    unlisted = tool(getattr(agent_extra, names[0]))
    crewai.STATE["script"] = [
        (names[0], {"order_id": "A1001"}),
        (names[1], {"order_id": "A1001", "reason": "too small"}),
        (names[2], {}),
        (unlisted, {"order_id": "X9"}),
    ]
    messages = [{"role": "user", "content": "Return A1001, it is too small."}]
    asyncio.run(agent.agent_callback(SimpleNamespace(messages=messages)))
    print(json.dumps(crewai.STATE["results"]))
    """
)


def test_tools_called_the_way_crewai_calls_them_are_traced(tmp_path: Path) -> None:
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
            "OPENAI_BASE_URL": "http://gateway.test/v1",
            "PYTHONPATH": os.pathsep.join([str(stand_in), str(run)]),
            "TOOL_NAMES": json.dumps(TOOLS),
            "HARNESS_TOOL_TRACE": str(trace),
            "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
        },
    )

    outputs = json.loads(completed.stdout.splitlines()[-1])
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


def test_callback_sends_the_conversation_once_and_replies_with_content_only(
    example_module, monkeypatch
) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    import crewai

    crewai.STATE["output"] = "Return started."
    # A system item and an extra key are the glue's to drop: only user and assistant turns
    # are passed on, as role and content.
    messages = [
        {"role": "system", "content": "ignored"},
        {**CONVERSATION[0], "extra": "dropped"},
        *CONVERSATION[1:],
    ]

    reply = asyncio.run(agent.agent_callback(_agent_input(messages)))

    # `str(result)` of the stand-in differs from its `.raw`, so only `.raw` gives this.
    assert reply == {"content": "Return started."}
    (kickoff,) = crewai.STATE["kickoffs"]
    assert kickoff.messages == CONVERSATION
    assert [m["content"] for m in kickoff.messages].count(NEW_MESSAGE["content"]) == 1


def test_first_turn_sends_the_one_message(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    import crewai

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION[:1])))

    (kickoff,) = crewai.STATE["kickoffs"]
    assert kickoff.messages == CONVERSATION[:1]


def test_model_is_the_gateway_chat_completions_client(
    example_module, monkeypatch
) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    import crewai

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    (llm,) = crewai.STATE["llms"]
    assert isinstance(llm, crewai.LLM)
    assert llm.model == agent.MODEL == "vertex_ai/gemini-3.7-flash"
    assert llm.custom_openai is True
    assert llm.base_url == "http://gateway.test/v1"
    assert llm.api_key == "test-key"
    (built,) = crewai.STATE["agents"]
    assert built.llm is llm


def test_each_call_builds_its_own_agent_and_model(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    import crewai

    # Nothing is built by the import: CrewAI's clients belong to one event loop.
    assert crewai.STATE["agents"] == []
    assert crewai.STATE["llms"] == []

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION[:1])))
    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    first, second = crewai.STATE["agents"]
    assert first is not second
    assert first.llm is not second.llm
    assert len(crewai.STATE["llms"]) == 2
    assert [len(k.messages) for k in crewai.STATE["kickoffs"]] == [1, 3]


def test_agent_gets_the_three_tools_wrapped_from_the_plain_functions(
    example_module, monkeypatch
) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    import crewai

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    (built,) = crewai.STATE["agents"]
    assert [t.name for t in built.tools] == list(TOOLS)
    assert [t.func for t in built.tools] == [getattr(agent, name) for name in TOOLS]
    # The module keeps the plain functions, which is what the tool tracer is bound to.
    for name in TOOLS:
        assert callable(getattr(agent, name))
        assert not hasattr(getattr(agent, name), "func")
    assert built.role and built.goal and built.backstory


def test_import_needs_no_key_and_no_base_url(example_module) -> None:
    agent = example_module()
    import crewai

    assert crewai.STATE["llms"] == []
    assert crewai.STATE["agents"] == []
    assert agent.MODEL == "vertex_ai/gemini-3.7-flash"


def test_a_turn_with_no_base_url_fails_with_the_library_error(
    example_module, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()

    with pytest.raises(ValueError, match="custom_openai=True requires base_url"):
        asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))


def test_telemetry_is_off_before_crewai_is_imported(example_module) -> None:
    example_module()
    import crewai

    assert crewai.IMPORT_ENV["CREWAI_DISABLE_TELEMETRY"] == "true"
    assert crewai.IMPORT_ENV["CREWAI_TRACING_ENABLED"] == "false"
    # Turning off OpenTelemetry for the whole process is not the agent's to do.
    assert crewai.IMPORT_ENV["OTEL_SDK_DISABLED"] is None


def test_telemetry_values_the_operator_set_are_kept(
    example_module, monkeypatch
) -> None:
    monkeypatch.setenv("CREWAI_DISABLE_TELEMETRY", "false")
    monkeypatch.setenv("CREWAI_TRACING_ENABLED", "true")
    example_module()
    import crewai

    assert crewai.IMPORT_ENV["CREWAI_DISABLE_TELEMETRY"] == "false"
    assert crewai.IMPORT_ENV["CREWAI_TRACING_ENABLED"] == "true"


def test_library_failure_is_not_hidden(example_module, monkeypatch) -> None:
    _gateway(monkeypatch)
    agent = example_module()
    import crewai

    crewai.STATE["raise"] = RuntimeError("model unreachable")

    with pytest.raises(RuntimeError, match="model unreachable"):
        asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))


# T5: the example's shape and its requirements -----------------------------------------------


def _module() -> ast.Module:
    return ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))


def test_example_has_one_async_callback_and_three_plain_tools() -> None:
    module = _module()
    async_names = [
        node.name for node in module.body if isinstance(node, ast.AsyncFunctionDef)
    ]
    assert async_names == ["agent_callback"]
    plain = [node.name for node in module.body if isinstance(node, ast.FunctionDef)]
    assert plain == list(TOOLS)
    # CrewAI refuses a tool with no docstring.
    assert all(
        ast.get_docstring(node)
        for node in module.body
        if isinstance(node, ast.FunctionDef)
    )


def test_default_model_is_the_gateway_name() -> None:
    assignments = [
        node
        for node in _module().body
        if isinstance(node, ast.Assign)
        and [t.id for t in node.targets if isinstance(t, ast.Name)] == ["MODEL"]
    ]
    (assignment,) = assignments
    call = assignment.value
    assert isinstance(call, ast.Call)
    assert ast.unparse(call.func) == "os.getenv"
    assert [ast.literal_eval(arg) for arg in call.args] == [
        "OPENAI_MODEL",
        "vertex_ai/gemini-3.7-flash",
    ]


def test_example_does_not_turn_off_opentelemetry() -> None:
    constants = {
        node.value for node in ast.walk(_module()) if isinstance(node, ast.Constant)
    }
    assert "OTEL_SDK_DISABLED" not in constants


def test_folder_holds_exactly_the_example_files() -> None:
    names = sorted(
        path.name for path in EXAMPLE.iterdir() if path.name != "__pycache__"
    )
    assert names == [".python-version", "README.md", "agent.py", "requirements.txt"]


def test_requirements_and_python_version_are_pinned() -> None:
    assert_pins(EXAMPLE, ["crewai==1.15.33", "agent-simulate==0.1.3"])
