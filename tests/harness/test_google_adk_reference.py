"""The Google ADK reference agent, and how the kit's hosted path handles it.

The real ADK of the test environment is used where the example has to run: it is a kit
dependency, and the model is the only part replaced, by a scripted stand-in, so nothing here
reaches a network.
"""

from __future__ import annotations

import ast
import inspect
import json
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
    copy_example,
    fresh_import,
    run_tracer,
    write_chat_contract,
)

SUBFOLDER = "examples/harness/google_adk_support"
EXAMPLE = REPO / SUBFOLDER

APP = "support_agent"
MODULE = f"{APP}.agent"
LOOK_UP_ORDER, START_RETURN, RETURN_POLICY = TOOLS
ENVIRONMENT = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", DETECTION_CASES)
def test_example_is_detected_as_an_adk_app(tmp_path: Path, where: str) -> None:
    # `google` is the library's top-level package, so a module of that name near the example
    # would pass for the customer's own.
    assert_detected(
        EXAMPLE,
        "google_adk",
        "google",
        where=where,
        tmp_path=tmp_path,
        subfolder=SUBFOLDER,
        ignore=(".adk",),
    )


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_adks_own_server_and_binds_the_tool_tracer(
    tmp_path: Path, load_example
) -> None:
    agent = build_control_service(
        tmp_path,
        lambda authoring: write_chat_contract(
            authoring, command=["adk", "web", "--port", "8080", "."], workdir=SUBFOLDER
        ),
        MODULE,
        subfolder=SUBFOLDER,
        example=EXAMPLE,
        ignore=(".adk",),
    )

    # ADK's server is started as it is, not nested in the kit's callback bridge.
    assert "ThreadingHTTPServer" not in json.dumps(agent.run_command)
    assert "ALK_SUBPROCESS_COMMAND" not in agent.environment
    assert "ALK_CALLBACK_ENTRYPOINT" not in agent.environment
    assert "adk web --port $PORT ." in agent.run_command[-1]
    assert "--port 8080" not in agent.run_command[-1]
    assert agent.fixed_port == 8080
    assert agent.fixed_port_consumable is True
    assert agent.environment["PORT"] == "{{PORT_agent}}"

    interface = json.loads((tmp_path / "bundle/contract.json").read_text(encoding="utf-8"))[
        "runtime"
    ]["interface"]
    assert interface["kind"] == "http"
    assert interface["protocol"] == "json_template"
    assert interface["path"] == "/run_sse"
    assert interface["request_template"]["app_name"] == APP
    assert interface["setup_requests"][0]["path"] == (
        f"/apps/{APP}/users/{{{{thread_id}}}}/sessions"
    )
    assert interface["response_path"] == "content.parts.0.text"

    assert agent.environment["HARNESS_TOOL_TRACE"] == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
    bindings = json.loads(agent.environment["ALK_TOOL_TRACE_BINDINGS"])
    # Each binding is checked against the example's real module, so renaming a tool there
    # breaks this test instead of leaving the build and its input in agreement.
    example = load_example()
    assert sorted(item["name"] for item in bindings) == sorted(
        tool.__name__ for tool in example.root_agent.tools
    )
    for item in bindings:
        assert item["module"] == MODULE
        function = getattr(example, item["callable"], None)
        assert inspect.isfunction(function), item
        assert not inspect.iscoroutinefunction(function), item
        assert function.__name__ == item["name"]


# T3: ADK's own tool path and the tracer -----------------------------------------------------

# The model answers by call index across both turns, so each turn ends on a text event.
SCRIPT = [
    ["call", LOOK_UP_ORDER, {"order_id": "A1001"}],
    ["text", "Order A1001 was delivered."],
    ["call", START_RETURN, {"order_id": "A1001", "reason": "too small"}],
    ["call", RETURN_POLICY, {}],
    ["text", "Return started. Items can be returned within 30 days."],
]
TURNS = ["Where is my order A1001?", "It is too small, please return it."]

DRIVER = textwrap.dedent(
    """
    import asyncio
    import json
    import sys

    from google.adk.cli.utils.agent_loader import AgentLoader
    from google.adk.models.base_llm import BaseLlm
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    spec = json.loads(sys.argv[1])
    seen = []


    class Scripted(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            seen.append(sorted(llm_request.tools_dict))
            kind, *rest = spec["script"][len(seen) - 1]
            if kind == "call":
                call = types.FunctionCall(name=rest[0], args=rest[1], id=f"call-{len(seen)}")
                part = types.Part(function_call=call)
            else:
                part = types.Part(text=rest[0])
            yield LlmResponse(content=types.Content(role="model", parts=[part]))


    async def main():
        # Loaded as `adk web` loads an app, so the tools carry the module the server gives them.
        root_agent = AgentLoader(spec["folder"]).load_agent("support_agent")
        root_agent.model = Scripted(model="scripted")
        sessions = InMemorySessionService()
        runner = Runner(
            app_name="support_agent", agent=root_agent, session_service=sessions
        )
        session = await sessions.create_session(app_name="support_agent", user_id="customer")
        replies = []
        for text in spec["turns"]:
            reply = None
            message = types.Content(role="user", parts=[types.Part(text=text)])
            async for event in runner.run_async(
                user_id="customer", session_id=session.id, new_message=message
            ):
                if event.is_final_response() and event.content and event.content.parts:
                    reply = "".join(part.text or "" for part in event.content.parts)
            replies.append(reply)
        await runner.close()
        return {
            "replies": replies,
            "tools_seen": seen,
            "tool_modules": sorted({tool.__module__ for tool in root_agent.tools}),
        }


    print(json.dumps(asyncio.run(main())))
    """
)


def test_tools_run_by_adk_are_traced_once_each(tmp_path: Path) -> None:
    run = copy_example(EXAMPLE, tmp_path / "run", (".adk",))
    hook = tmp_path / "hook"
    hook.mkdir()
    shutil.copy2(TRACE_HOOK, hook / "sitecustomize.py")
    trace = tmp_path / "calls.jsonl"
    bindings = [{"name": name, "module": MODULE, "callable": name} for name in TOOLS]

    completed = run_tracer(
        [
            sys.executable,
            "-c",
            DRIVER,
            json.dumps({"folder": str(run), "script": SCRIPT, "turns": TURNS}),
        ],
        hook,
        run,
        {
            **dict.fromkeys(ENVIRONMENT),
            "PYTHONPATH": str(run),
            "PYTHONDONTWRITEBYTECODE": "1",
            "HARNESS_TOOL_TRACE": str(trace),
            "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
        },
    )

    result = json.loads(completed.stdout.splitlines()[-1])
    assert result["replies"] == [SCRIPT[1][1], SCRIPT[4][1]]
    assert len(result["tools_seen"]) == len(SCRIPT)
    assert all(set(TOOLS) <= set(offered) for offered in result["tools_seen"])
    assert result["tool_modules"] == [MODULE]
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    # The whole list, not a membership check: a tool that suspends is also recorded at each
    # await, so a coroutine tool would add records here.
    assert [(r["name"], r["arguments"], r["output"]) for r in records] == [
        (
            LOOK_UP_ORDER,
            {"order_id": "A1001"},
            "Order A1001: trail running shoes, status delivered, return: none.",
        ),
        (
            START_RETURN,
            {"order_id": "A1001", "reason": "too small"},
            "Return started for order A1001: too small.",
        ),
        (
            RETURN_POLICY,
            {},
            "Delivered items can be returned within 30 days for a full refund.",
        ),
    ]
    assert not any(record["is_error"] for record in records)


# T4: the model wiring -----------------------------------------------------------------------


@pytest.fixture
def load_example(tmp_path, monkeypatch):
    """A fresh import of the example's `support_agent.agent` with only the given environment."""
    # The model is built when the module is imported, so each load needs its own copy and a
    # clean environment.
    with fresh_import(
        tmp_path,
        monkeypatch,
        EXAMPLE,
        module=MODULE,
        clear_env=ENVIRONMENT,
        copy_package=True,
        reset_each_load=True,
        ignore=(".adk",),
    ) as load:
        yield load


def test_model_goes_to_the_gateway_with_its_url_and_key(load_example) -> None:
    from google.adk.models.lite_llm import LiteLlm

    agent = load_example(
        OPENAI_BASE_URL="http://gateway.test/v1",
        OPENAI_API_KEY="test-key",
        OPENAI_MODEL="vertex_ai/test-model",
    )

    model = agent.root_agent.model
    assert isinstance(model, LiteLlm)
    # The prefix selects LiteLLM's OpenAI-compatible provider, which sends the rest unchanged.
    assert model.model == "openai/vertex_ai/test-model"
    # LiteLlm keeps these only in its private argument dict, and passes them on each call.
    assert model._additional_args["api_base"] == "http://gateway.test/v1"
    assert model._additional_args["api_key"] == "test-key"


def test_import_needs_no_environment_and_keeps_the_default_gateway_model(
    load_example,
) -> None:
    from google.adk.models.lite_llm import LiteLlm

    agent = load_example()

    assert agent.MODEL == "vertex_ai/gemini-3.7-flash"
    assert agent.root_agent.name == APP
    model = agent.root_agent.model
    assert isinstance(model, LiteLlm)
    assert model.model == "openai/vertex_ai/gemini-3.7-flash"
    assert model._additional_args["api_base"] is None
    assert model._additional_args["api_key"] is None
    assert [tool.__name__ for tool in agent.root_agent.tools] == list(TOOLS)


def test_a_second_import_sees_its_own_environment(load_example) -> None:
    first = load_example(OPENAI_BASE_URL="http://one.test/v1", OPENAI_API_KEY="one")
    assert first.root_agent.model._additional_args["api_base"] == "http://one.test/v1"

    second = load_example()

    assert second is not first
    assert second.root_agent.model._additional_args["api_base"] is None
    assert second.root_agent.model._additional_args["api_key"] is None


# T5: the pins and the app layout ------------------------------------------------------------


def test_requirements_and_python_version_are_pinned() -> None:
    # `litellm` is what the `openai/` model name needs, and its first call imports `mcp`, which
    # neither pin brings in; this agent runs on ADK's server, so the kit's simulate package is
    # not part of it.
    assert_pins(EXAMPLE, ["google-adk==2.13.0", "litellm==1.106.2", "mcp==2.3.0"])


def test_example_is_an_adk_app_package_with_plain_tools() -> None:
    package = EXAMPLE / APP
    init = ast.parse((package / "__init__.py").read_text(encoding="utf-8"))
    # Without the import ADK still finds `root_agent` through its fallback, so the kit's build
    # would not notice a package that leaves it out.
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module is None
        and node.level == 1
        and [alias.name for alias in node.names] == ["agent"]
        for node in init.body
    )
    source = (package / "agent.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    assert any(
        isinstance(node, ast.Assign)
        and [target.id for target in node.targets if isinstance(target, ast.Name)]
        == ["root_agent"]
        for node in module.body
    )
    # A coroutine tool is traced at every await as well as at its return, and ADK would run it
    # on the event loop differently.
    assert not [n.name for n in module.body if isinstance(n, ast.AsyncFunctionDef)]
    assert {n.name for n in module.body if isinstance(n, ast.FunctionDef)} == set(TOOLS)
    assert [p.name for p in EXAMPLE.iterdir() if p.is_dir() and (p / "agent.py").is_file()] == [
        APP
    ]


def test_example_ignores_adks_session_store_and_documents_the_start() -> None:
    assert ".adk/" in (EXAMPLE / ".gitignore").read_text(encoding="utf-8").splitlines()
    readme = (EXAMPLE / "README.md").read_text(encoding="utf-8")
    assert "adk web --port 8080 ." in readme
    for name in ENVIRONMENT:
        assert name in readme
