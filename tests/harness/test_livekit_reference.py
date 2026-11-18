"""The LiveKit reference agent, and how the kit's hosted path handles it.

Written as LiveKit's documentation writes a voice agent (ADR-006, "what a reference agent is"):
an ``AgentServer`` worker whose session hears with OpenAI's speech-to-text, thinks with its
chat model and speaks with its text-to-speech, all through the gateway, and an ``Agent``
subclass whose tools are ``@function_tool`` methods.

Unlike the other references, the tests that import the example use the real livekit-agents
1.8.3: the kit's ``livekit`` extra installs it, so CI's kit-tests job has it. Where it isn't
installed, those tests skip; detection, the build and the files don't need it.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from reference_agents import (
    DETECTION_CASES,
    REPO,
    TOOLS,
    assert_detected,
    assert_pins,
    assert_refuses_key_without_base_url,
    copy_example,
    fresh_import,
)
from test_bundle_author_v2 import _authoring, _github_job

from fi.alk.harness.bundle_author_v2 import author_bundle_v2

SUBFOLDER = "examples/harness/livekit_support"
EXAMPLE = REPO / SUBFOLDER
ENVIRONMENT = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_MODEL")
DEFAULT_MODEL = "vertex_ai/gemini-3.7-flash"
LIVEKIT_SECRETS = ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET")


def _real_livekit() -> None:
    pytest.importorskip("livekit.agents")
    pytest.importorskip("livekit.plugins.openai")
    pytest.importorskip("livekit.plugins.silero")


@pytest.fixture
def example_module(tmp_path, monkeypatch):
    """A fresh import of the example's `agent.py` on the real libraries, no env set."""
    _real_livekit()
    with fresh_import(tmp_path, monkeypatch, EXAMPLE, clear_env=ENVIRONMENT) as load:
        yield load


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", DETECTION_CASES)
def test_example_is_detected_as_livekit(tmp_path: Path, where: str) -> None:
    # `livekit` is the library's top-level package, so a module of that name in the folder
    # would pass for the customer's own.
    assert_detected(
        EXAMPLE, "livekit", "livekit", where=where, tmp_path=tmp_path, subfolder=SUBFOLDER
    )


# T2: the build ------------------------------------------------------------------------------


def _built(tmp_path: Path):
    source = tmp_path / "source"
    copy_example(EXAMPLE, source / SUBFOLDER)
    (source / "README.md").write_text("many examples\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    # As the reading writes it: a voice agent whose tools are methods of the agent it builds.
    (authoring / "contract.json").write_text(
        json.dumps(
            {
                "modality": "voice",
                "tool_entrypoints": [
                    {
                        "tool": name,
                        "mode": "construct",
                        "module": "agent",
                        "callable": f"SupportAgent.{name}",
                        "factory": "SupportAgent()",
                    }
                    for name in TOOLS
                ],
            }
        ),
        encoding="utf-8",
    )
    bundle = author_bundle_v2(
        source=source,
        job=_github_job(
            SUBFOLDER,
            with_secrets=True,
            secret_aliases=("OPENAI_API_KEY", "OPENAI_BASE_URL", *LIVEKIT_SECRETS),
        ),
        authoring=authoring,
        output=tmp_path / "bundle",
    )
    return next(p for p in bundle.processes if p.name == bundle.runtime.control_service)


def test_build_starts_the_worker_and_traces_its_tools_from_the_session(tmp_path: Path) -> None:
    agent = _built(tmp_path)

    # The LiveKit CLI needs its subcommand, or it prints its usage and exits.
    assert agent.run_command[-3:] == ["-m", "agent", "start"]
    assert "requirements.txt" in json.dumps(agent.build_commands)
    assert agent.environment["LIVEKIT_AGENT_NAME"].endswith("-w{{WORLD_INDEX}}")
    # A voice agent's tool calls are recorded from its session's own events, whatever shape
    # its tools have, so the binding tracer is not used.
    assert agent.environment["HARNESS_TOOL_TRACE"] == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
    assert "ALK_TOOL_TRACE_BINDINGS" not in agent.environment
    # The methods themselves are what the kit binds the world to.
    assert json.loads(agent.environment["ALK_WORLD_DOOR_BINDINGS"]) == [
        {"callable": f"SupportAgent.{name}", "module": "agent", "name": name} for name in TOOLS
    ]


# T3: the example on the real library -------------------------------------------------------


def test_the_agent_has_the_three_function_tools(example_module) -> None:
    agent = example_module()
    from livekit.agents.llm import FunctionTool

    support = agent.SupportAgent()

    assert sorted(tool.info.name for tool in support.tools) == sorted(TOOLS)
    assert all(isinstance(tool, FunctionTool) for tool in support.tools)
    assert support.instructions == agent.INSTRUCTIONS


def test_each_call_has_its_own_orders(example_module) -> None:
    agent = example_module()
    # The worker builds one agent per call, as the entrypoint does.
    first, second = agent.SupportAgent(), agent.SupportAgent()

    async def calls():
        started = await first.start_return(None, order_id="A1001", reason="too small")
        return (
            started,
            await second.look_up_order(None, order_id="A1001"),
            await first.look_up_order(None, order_id="A1001"),
        )

    started, elsewhere, here = asyncio.run(calls())
    assert started == "Return started for order A1001: too small."
    assert elsewhere.endswith("return: none.")
    assert here.endswith("return: too small.")


def test_the_tools_answer_as_the_other_references_do(example_module) -> None:
    agent = example_module()
    support = agent.SupportAgent()

    async def calls():
        return [
            await support.look_up_order(None, order_id="A1002"),
            await support.start_return(None, order_id="A1002", reason="late"),
            await support.look_up_order(None, order_id="X9"),
            await support.return_policy(None),
        ]

    assert asyncio.run(calls()) == [
        "Order A1002: rain jacket, status shipped, return: none.",
        "Order A1002 is shipped and cannot be returned yet.",
        "No order X9 exists.",
        "Delivered items can be returned within 30 days for a full refund.",
    ]


def test_the_session_hears_thinks_and_speaks_through_the_gateway_on_plain_routes(
    example_module, monkeypatch
) -> None:
    agent = example_module(OPENAI_API_KEY="test-key", OPENAI_BASE_URL="http://gateway.test/v1")
    from livekit.plugins import openai

    sessions = []

    class Session:
        def __init__(self, **parts):
            self.parts = parts
            self.started = None
            self.greeting = None
            sessions.append(self)

        async def start(self, *, agent, room):
            self.started = (agent, room)

        async def generate_reply(self, *, instructions):
            self.greeting = instructions

    monkeypatch.setattr(agent, "AgentSession", Session)
    monkeypatch.setattr(agent.silero.VAD, "load", classmethod(lambda cls, **kw: "vad"))
    room = object()

    asyncio.run(agent.entrypoint(SimpleNamespace(room=room)))

    (session,) = sessions
    parts = session.parts
    assert isinstance(parts["stt"], openai.STT)
    # Not the realtime socket: a recognizer that doesn't stream, with the VAD cutting turns.
    assert parts["stt"].capabilities.streaming is False
    assert parts["vad"] == "vad"
    assert isinstance(parts["llm"], openai.LLM)
    assert parts["llm"].model == agent.MODEL == DEFAULT_MODEL
    assert isinstance(parts["tts"], openai.TTS)
    built, joined = session.started
    assert isinstance(built, agent.SupportAgent) and joined is room
    assert session.greeting


def test_import_needs_no_key_and_no_base_url(example_module) -> None:
    agent = example_module()

    assert agent.MODEL == DEFAULT_MODEL


def test_a_key_with_no_base_url_is_refused_at_import(example_module, monkeypatch) -> None:
    assert_refuses_key_without_base_url(example_module, None, monkeypatch)


def test_openai_api_base_alone_does_not_satisfy_the_check(example_module) -> None:
    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL"):
        example_module(OPENAI_API_KEY="secret-canary-key", OPENAI_API_BASE="http://other.test/v1")


def test_no_module_name_holds_the_key(example_module) -> None:
    agent = example_module(OPENAI_API_KEY="secret-canary-key", OPENAI_BASE_URL="http://g.test/v1")

    assert not [name for name, value in vars(agent).items() if value == "secret-canary-key"]


# T4: the files, read without the library ---------------------------------------------------


def _module() -> ast.Module:
    return ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))


def test_the_tools_are_documented_function_tool_methods_of_the_agent() -> None:
    (support,) = [
        node
        for node in _module().body
        if isinstance(node, ast.ClassDef) and node.name == "SupportAgent"
    ]
    assert [ast.unparse(base) for base in support.bases] == ["Agent"]
    methods = {
        node.name: node for node in support.body if isinstance(node, ast.AsyncFunctionDef)
    }
    assert sorted(methods) == sorted(TOOLS)
    for name, method in methods.items():
        assert [ast.unparse(d) for d in method.decorator_list] == ["function_tool"], name
        assert [a.arg for a in method.args.args[:2]] == ["self", "context"], name
        assert ast.get_docstring(method), name


def test_the_worker_is_an_agent_server_with_one_session_entrypoint() -> None:
    module = _module()
    (entrypoint,) = [
        node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "entrypoint"
    ]
    assert [ast.unparse(d) for d in entrypoint.decorator_list] == ["server.rtc_session()"]
    source = ast.unparse(module)
    assert "server = AgentServer()" in source
    assert "cli.run_app(server)" in source
    assert "openai.STT(use_realtime=False)" in source


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
        EXAMPLE,
        [
            "livekit-agents==1.8.3",
            "livekit-plugins-openai==1.8.3",
            "livekit-plugins-silero==1.8.3",
        ],
    )
