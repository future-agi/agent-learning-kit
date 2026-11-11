"""The Pydantic AI reference agent, and how the kit's hosted path handles it.

The library is never installed here. Where the example has to be imported, a stand-in
`pydantic_ai` and `openai` written below take its place, shaped like the public names the
example uses.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from test_bundle_author_v2 import _authoring, _github_job, _write_callable_contract

from fi.alk.harness.bundle_author_v2 import author_bundle_v2
from fi.alk.harness.framework_detection import detect_framework, folder_findings
from fi.simulate import AgentInput

REPO = Path(__file__).resolve().parents[2]
SUBFOLDER = "examples/harness/pydantic_ai_support"
EXAMPLE = REPO / SUBFOLDER
BOOTSTRAP = REPO / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"

TOOLS = ("look_up_order", "start_return", "return_policy")

# `Tool.call` follows Pydantic AI 2.61.0 (_function_schema.py:81-89, _utils.py:182-199): a
# coroutine function is awaited, a plain function is run by `anyio.to_thread.run_sync`
# inside a closure, because `run_sync` takes no keyword arguments for the function.
STAND_IN_PYDANTIC_AI = '''
import inspect
from types import SimpleNamespace

import anyio.to_thread

STATE = {"agents": [], "runs": [], "output": "All done.", "raise": None}


async def run_in_executor(func, *args, **kwargs):
    def call_it():
        return func(*args, **kwargs)

    return await anyio.to_thread.run_sync(call_it)


class Tool:
    def __init__(self, function):
        self.name = function.__name__
        self.function = function
        self.is_async = inspect.iscoroutinefunction(function)

    async def call(self, args_dict):
        if self.is_async:
            return await self.function(**args_dict)
        return await run_in_executor(self.function, **args_dict)


class Agent:
    def __init__(self, model=None, *, instructions=None, tools=(), **options):
        self.model = model
        self.instructions = instructions
        self.tools = [Tool(function) for function in tools]
        self.options = options
        STATE["agents"].append(self)

    async def run(self, user_prompt, *, message_history=None, model=None, **options):
        client = getattr(getattr(model, "provider", None), "client", None)
        STATE["runs"].append(
            SimpleNamespace(
                agent=self,
                user_prompt=user_prompt,
                message_history=message_history,
                model=model,
                client_open=client is not None and not client.closed,
            )
        )
        if STATE["raise"] is not None:
            raise STATE["raise"]
        return SimpleNamespace(output=STATE["output"])
'''

STAND_IN_MESSAGES = '''
from dataclasses import dataclass, field


@dataclass
class UserPromptPart:
    content: str


@dataclass
class TextPart:
    content: str


@dataclass
class ModelRequest:
    parts: list = field(default_factory=list)


@dataclass
class ModelResponse:
    parts: list = field(default_factory=list)
'''

STAND_IN_MODELS_OPENAI = '''
class OpenAIChatModel:
    def __init__(self, model_name, *, provider="openai", **options):
        self.model_name = model_name
        self.provider = provider
'''

STAND_IN_PROVIDERS_OPENAI = '''
class OpenAIProvider:
    def __init__(self, *, openai_client=None, **options):
        self.client = openai_client
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

STAND_IN_FILES = {
    "pydantic_ai/__init__.py": STAND_IN_PYDANTIC_AI,
    "pydantic_ai/messages.py": STAND_IN_MESSAGES,
    "pydantic_ai/models/__init__.py": "",
    "pydantic_ai/models/openai.py": STAND_IN_MODELS_OPENAI,
    "pydantic_ai/providers/__init__.py": "",
    "pydantic_ai/providers/openai.py": STAND_IN_PROVIDERS_OPENAI,
    "openai/__init__.py": STAND_IN_OPENAI,
}


def _copy_example(destination: Path) -> Path:
    shutil.copytree(EXAMPLE, destination, ignore=shutil.ignore_patterns("__pycache__"))
    return destination


def _write_stand_in(root: Path) -> Path:
    for relative, source in STAND_IN_FILES.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source, encoding="utf-8")
    return root


@pytest.fixture
def example_module(tmp_path, monkeypatch):
    """A fresh import of the example's `agent.py` on the stand-in library, with no key set."""
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    stand_in = _write_stand_in(tmp_path / "stand-in")
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy2(EXAMPLE / "agent.py", run / "agent.py")
    saved = dict(sys.modules)
    for name in list(sys.modules):
        if name.split(".")[0] in {"pydantic_ai", "openai", "agent"}:
            del sys.modules[name]
    monkeypatch.syspath_prepend(str(run))
    monkeypatch.syspath_prepend(str(stand_in))

    def load():
        import agent

        return agent

    yield load
    for name in list(sys.modules):
        if name not in saved:
            del sys.modules[name]
    sys.modules.update(saved)


# T1: detection ------------------------------------------------------------------------------


def test_example_is_found_as_the_run_folder(tmp_path: Path) -> None:
    run = _copy_example(tmp_path / "run")

    assert detect_framework(run) == {"name": "pydantic_ai", "version": None}
    assert folder_findings(run).blockers == frozenset()


def test_example_is_found_as_a_subfolder_of_a_bigger_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _copy_example(repo / SUBFOLDER)
    # A package of the same name deep in the tree must not make the library import look
    # like the customer's own module.
    nested = repo / "src/pkg/pydantic_ai"
    nested.mkdir(parents=True)
    (nested / "__init__.py").write_text("", encoding="utf-8")
    (nested / "score.py").write_text("def score():\n    return 1\n", encoding="utf-8")
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")

    found = folder_findings(repo / SUBFOLDER, repo_root=repo)

    assert detect_framework(repo / SUBFOLDER, repo_root=repo) == {
        "name": "pydantic_ai",
        "version": None,
    }
    assert found.blockers == frozenset()


def test_example_in_place_is_not_shadowed_by_the_repository(tmp_path: Path) -> None:
    # Nothing named `pydantic_ai` may sit on the example's path or inside it, or it would
    # shadow the library and block detection.
    assert "pydantic_ai" not in EXAMPLE.relative_to(REPO).parts
    assert not [
        path
        for path in EXAMPLE.rglob("*")
        if path.name in {"pydantic_ai", "pydantic_ai.py"} and "__pycache__" not in path.parts
    ]
    assert detect_framework(EXAMPLE, repo_root=REPO) == {
        "name": "pydantic_ai",
        "version": None,
    }


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_the_callback_bridge_and_binds_the_tool_tracer(tmp_path: Path) -> None:
    source = tmp_path / "source"
    _copy_example(source / SUBFOLDER)
    (source / "README.md").write_text("many examples\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    _write_callable_contract(authoring)
    contract_path = authoring / "contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["tool_entrypoints"] = [
        {"tool": name, "mode": "import", "module": "agent", "callable": name}
        for name in TOOLS
    ]
    contract_path.write_text(json.dumps(contract), encoding="utf-8")

    bundle = author_bundle_v2(
        source=source,
        job=_github_job(
            SUBFOLDER,
            with_secrets=True,
            secret_aliases=("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        ),
        authoring=authoring,
        output=tmp_path / "bundle",
    )

    agent = next(p for p in bundle.processes if p.name == bundle.runtime.control_service)
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


# T3: the tracer and a library-shaped call ---------------------------------------------------

DRIVER = textwrap.dedent(
    """
    import asyncio
    import json
    import threading

    import agent
    import agent_extra
    from pydantic_ai import Tool

    # Same name as a declared tool, other module: only the module tells them apart.
    unlisted = Tool(agent_extra.look_up_order)
    tools = {tool.name: tool for tool in agent.agent.tools}
    CALLS = [
        (tools["look_up_order"], {"order_id": "A1001"}),
        (tools["start_return"], {"order_id": "A1001", "reason": "too small"}),
        (tools["return_policy"], {}),
        (unlisted, {"order_id": "X9"}),
    ]


    async def go():
        return [await tool.call(args) for tool, args in CALLS]


    # The bridge serves each request on a thread started after start-up.
    result = {}
    worker = threading.Thread(target=lambda: result.update(out=asyncio.run(go())))
    worker.start()
    worker.join()
    print(json.dumps(result["out"]))
    """
)


def test_tools_called_the_way_the_library_calls_them_are_traced(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy2(EXAMPLE / "agent.py", run / "agent.py")
    (run / "agent_extra.py").write_text(
        "def look_up_order(order_id):\n    return 'not declared'\n", encoding="utf-8"
    )
    shutil.copy2(BOOTSTRAP, run / "sitecustomize.py")
    stand_in = _write_stand_in(tmp_path / "stand-in")
    trace = tmp_path / "calls.jsonl"
    bindings = [{"name": name, "module": "agent", "callable": name} for name in TOOLS]

    completed = subprocess.run(
        [sys.executable, "-c", DRIVER],
        check=True,
        capture_output=True,
        text=True,
        cwd=run,
        env={
            **os.environ,
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


def test_callback_sends_each_turn_once_and_replies_with_content_only(
    example_module, monkeypatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://gateway.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()
    import openai
    import pydantic_ai
    from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    pydantic_ai.STATE["output"] = "Return started."
    # A system item and an extra key are the glue's to drop: only user and assistant turns
    # become history.
    messages = [
        {"role": "system", "content": "ignored"},
        {**CONVERSATION[0], "extra": "dropped"},
        *CONVERSATION[1:],
    ]

    reply = asyncio.run(agent.agent_callback(_agent_input(messages)))

    assert reply == {"content": "Return started."}
    (run,) = pydantic_ai.STATE["runs"]
    assert run.user_prompt == NEW_MESSAGE["content"]
    assert run.message_history == [
        ModelRequest(parts=[UserPromptPart(content=CONVERSATION[0]["content"])]),
        ModelResponse(parts=[TextPart(content=CONVERSATION[1]["content"])]),
    ]
    assert run.agent is agent.agent
    model = run.model
    assert isinstance(model, OpenAIChatModel)
    assert model.model_name == agent.MODEL == "vertex_ai/gemini-3.7-flash"
    assert isinstance(model.provider, OpenAIProvider)
    client = model.provider.client
    assert client.base_url == "http://gateway.test/v1"
    assert client.api_key == "test-key"
    assert run.client_open
    assert client.closed
    assert openai.CLIENTS == [client]


def test_first_turn_has_an_empty_history(example_module, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()
    import pydantic_ai

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION[:1])))

    (run,) = pydantic_ai.STATE["runs"]
    assert run.user_prompt == CONVERSATION[0]["content"]
    assert run.message_history == []


def test_each_call_builds_and_closes_its_own_client(example_module, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()
    import openai
    import pydantic_ai

    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION[:1])))
    asyncio.run(agent.agent_callback(_agent_input(CONVERSATION)))

    first, second = openai.CLIENTS
    assert first is not second
    assert first.closed and second.closed
    runs = pydantic_ai.STATE["runs"]
    assert [run.client_open for run in runs] == [True, True]
    assert [len(run.message_history) for run in runs] == [0, 2]
    assert len(pydantic_ai.STATE["agents"]) == 1


def test_import_sets_up_the_agent_without_a_key_or_a_client(example_module) -> None:
    agent = example_module()
    import openai
    import pydantic_ai

    assert openai.CLIENTS == []
    assert pydantic_ai.STATE["runs"] == []
    (built,) = pydantic_ai.STATE["agents"]
    assert built is agent.agent
    assert [tool.name for tool in built.tools] == list(TOOLS)
    # The model comes from the run, not the agent.
    assert built.model is None
    # With no key, a client built here would have failed the import above.
    with pytest.raises(RuntimeError, match="api_key"):
        openai.AsyncOpenAI(base_url="http://gateway.test/v1")


def test_persona_is_given_as_instructions_not_a_system_prompt(example_module) -> None:
    # A system prompt is added only when the message history is empty, so it would reach the
    # model on the first turn and never again.
    agent = example_module()

    assert agent.agent.instructions
    assert "system_prompt" not in agent.agent.options


def test_library_failure_is_not_hidden(example_module, monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    agent = example_module()
    import openai
    import pydantic_ai

    pydantic_ai.STATE["raise"] = RuntimeError("model unreachable")

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
    assert plain == set(TOOLS)


# T5: requirements ---------------------------------------------------------------------------


def test_requirements_and_python_version_are_pinned() -> None:
    lines = (EXAMPLE / "requirements.txt").read_text(encoding="utf-8").splitlines()
    assert lines == ["pydantic-ai-slim[openai]==2.61.0", "agent-simulate==0.1.3"]
    assert (EXAMPLE / ".python-version").read_text(encoding="utf-8").strip() == "3.12"
