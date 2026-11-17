"""The Claude Agent SDK reference agent, and how the kit's hosted path handles it.

The SDK is the one installed here, and T3 runs its real bundled CLI against a local stub of the
Messages API. Nothing leaves 127.0.0.1.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import sys
import tempfile
import textwrap
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

# Imported once, before any fixture snapshots `sys.modules`, so a fixture's teardown never
# drops half of the SDK and leaves the next import mixing two copies of its classes.
import claude_agent_sdk
import pytest
from reference_agents import (
    DETECTION_CASES,
    REPO,
    TOOLS,
    TRACE_HOOK,
    assert_bindings_match,
    assert_detected,
    assert_pins,
    build_control_service,
    fresh_import,
    run_tracer,
    write_callable_contract,
)

from fi.simulate import AgentInput

SUBFOLDER = "examples/harness/claude_agent_sdk_support"
EXAMPLE = REPO / SUBFOLDER

MODEL = "vertex_ai/gemini-3.7-flash"

# Cleared so a test sees only what it sets.
CLEARED_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_MODEL",
    "CLAUDE_CONFIG_DIR",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_FOUNDRY",
)


# T1: detection ------------------------------------------------------------------------------


@pytest.mark.parametrize("where", DETECTION_CASES)
def test_example_is_detected_as_the_claude_agent_sdk(tmp_path: Path, where: str) -> None:
    # `claude_code_sdk` is the library's earlier name, which detection also reads.
    assert_detected(
        EXAMPLE,
        "claude_agent_sdk",
        ("claude_agent_sdk", "claude_code_sdk"),
        where=where,
        tmp_path=tmp_path,
        subfolder=SUBFOLDER,
    )


# T2: the build ------------------------------------------------------------------------------


def test_build_runs_the_callback_bridge_with_the_generic_tool_tracer(
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
    # The plain functions, not the `@tool` handlers the SDK calls.
    assert_bindings_match(bindings, example_module(), "agent")


# T3: the real SDK and its bundled CLI, against a local stub of the Messages API --------------


def _bundled_cli() -> Path:
    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if not cli.is_file():
        pytest.fail(f"the Claude CLI bundled with claude-agent-sdk was not found at {cli}")
    return cli


def _blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return [b for b in content or [] if isinstance(b, dict)]


class _MessagesStub(BaseHTTPRequestHandler):
    """The Messages API on 127.0.0.1: asks for one tool per turn, then answers in text.

    Turn 1 asks for `look_up_order` and turn 2 (whose prompt carries a transcript) for
    `start_return`. A request holding a `tool_result` gets the text, and one with no tools
    (the CLI's own housekeeping) just gets text.
    """

    requests: list[dict] = []

    def log_message(self, *args) -> None:
        pass

    def _json(self, status: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:
        self._json(200, {"data": [], "has_more": False})

    def _plan(self, request: dict) -> tuple[str | None, str]:
        """(tool to ask for, text to answer with) for this request."""
        messages = request.get("messages") or []
        last = _blocks(messages[-1]["content"]) if messages else []
        first = "".join(
            b.get("text", "") for b in _blocks(messages[0]["content"]) if b.get("type") == "text"
        ) if messages else ""
        turn = 2 if "Conversation so far" in first else 1
        text = f"stub reply for turn {turn}"
        if any(b.get("type") == "tool_result" for b in last) or not request.get("tools"):
            return None, text
        if turn == 1:
            return "mcp__support__look_up_order", text
        return "mcp__support__start_return", text

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            request = json.loads(raw)
        except ValueError:
            request = {}
        self.requests.append(
            {"path": self.path, "headers": dict(self.headers.items()), "body": request}
        )
        if "count_tokens" in self.path:
            # Answered as the gateway does for a non-Anthropic provider, if the CLI asks.
            self._json(501, {"type": "error", "error": {"type": "api_error", "message": "no"}})
            return
        model_name = request.get("model", MODEL)
        tool, text = self._plan(request)
        arguments = (
            {"order_id": "A1001"}
            if tool == "mcp__support__look_up_order"
            else {"order_id": "A1001", "reason": "too small"}
        )
        if not request.get("stream"):
            content = (
                [{"type": "text", "text": text}]
                if tool is None
                else [{"type": "tool_use", "id": "toolu_1", "name": tool, "input": arguments}]
            )
            self._json(
                200,
                {
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "model": model_name,
                    "content": content,
                    "stop_reason": "end_turn" if tool is None else "tool_use",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 20, "output_tokens": 5},
                },
            )
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()

        def event(name: str, data: dict) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": model_name,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 20,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        )
        if tool is None:
            event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
            )
            event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": text},
                },
            )
        else:
            event(
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": tool,
                        "input": {},
                    },
                },
            )
            event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)},
                },
            )
        event("content_block_stop", {"type": "content_block_stop", "index": 0})
        event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "end_turn" if tool is None else "tool_use",
                    "stop_sequence": None,
                },
                "usage": {"output_tokens": 10},
            },
        )
        event("message_stop", {"type": "message_stop"})


DRIVER = textwrap.dedent(
    """
    import asyncio
    import json
    from types import SimpleNamespace

    import agent

    first = {"role": "user", "content": "Where is order A1001?"}
    second = {"role": "user", "content": "It is too small, please start a return."}
    one = asyncio.run(agent.agent_callback(SimpleNamespace(messages=[first])))
    two = asyncio.run(
        agent.agent_callback(
            SimpleNamespace(
                messages=[first, {"role": "assistant", "content": one["content"]}, second]
            )
        )
    )
    print(json.dumps([one, two]))
    """
)


def test_the_real_cli_runs_two_turns_and_only_the_plain_tools_are_traced(
    tmp_path: Path,
) -> None:
    _bundled_cli()
    _MessagesStub.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MessagesStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    run = tmp_path / "run"
    run.mkdir()
    shutil.copy2(EXAMPLE / "agent.py", run / "agent.py")
    hook = tmp_path / "hook"
    hook.mkdir()
    shutil.copy2(TRACE_HOOK, hook / "sitecustomize.py")
    trace = tmp_path / "calls.jsonl"
    bindings = [{"name": name, "module": "agent", "callable": name} for name in TOOLS]
    try:
        completed = run_tracer(
            [sys.executable, "-c", DRIVER],
            hook,
            run,
            {
                **{name: None for name in CLEARED_ENV},
                **{name: None for name in os.environ if name.startswith("OTEL_")},
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
                "ANTHROPIC_AUTH_TOKEN": "placeholder-not-a-token",
                "PYTHONPATH": str(run),
                "HARNESS_TOOL_TRACE": str(trace),
                "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
            },
            timeout=240,
        )
    finally:
        server.shutdown()
        server.server_close()

    replies = json.loads(completed.stdout.splitlines()[-1])
    assert replies == [
        {"content": "stub reply for turn 1"},
        {"content": "stub reply for turn 2"},
    ]
    # Only the plain tools: the async handlers the SDK calls are not traced.
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    assert [(r["name"], r["arguments"]) for r in records] == [
        ("look_up_order", {"order_id": "A1001"}),
        ("start_return", {"order_id": "A1001", "reason": "too small"}),
    ]
    assert [r["output"] for r in records] == [
        "Order A1001: trail running shoes, status delivered, return: none.",
        "Return started for order A1001: too small.",
    ]
    assert not any(r["is_error"] for r in records)

    sent = [r for r in _MessagesStub.requests if "count_tokens" not in r["path"]]
    assert sent, "the CLI never reached the stub"
    assert {r["body"].get("model") for r in sent if r["body"].get("tools")} == {MODEL}
    assert all(r["headers"].get("Authorization") == "Bearer placeholder-not-a-token" for r in sent)
    with_tools = [r for r in sent if r["body"].get("tools")]
    # Two turns, each asking for a tool and then answering after its result.
    assert len(with_tools) == 4
    for request in with_tools:
        assert sorted(t["name"] for t in request["body"]["tools"]) == sorted(
            f"mcp__support__{name}" for name in TOOLS
        )

    def user_text(request: dict) -> str:
        first = request["body"]["messages"][0]
        return "".join(b.get("text", "") for b in _blocks(first["content"]))

    # The results the CLI handed back to the model are the plain tools' outputs, as text.
    results = []
    for request in with_tools:
        for block in _blocks(request["body"]["messages"][-1]["content"]):
            if block.get("type") == "tool_result":
                assert not block.get("is_error")
                results.append(
                    "".join(b.get("text", "") for b in _blocks(block.get("content")))
                )
    assert results == [
        "Order A1001: trail running shoes, status delivered, return: none.",
        "Return started for order A1001: too small.",
    ]

    turn_two = [r for r in with_tools if "Conversation so far" in user_text(r)]
    assert len(turn_two) == 2
    prompt = user_text(turn_two[0])
    assert "User: Where is order A1001?" in prompt
    assert "Assistant: stub reply for turn 1" in prompt
    assert prompt.endswith("User: It is too small, please start a return.")


# T4: the callback's glue --------------------------------------------------------------------

NEW_MESSAGE = {"role": "user", "content": "Please start a return for A1001."}
CONVERSATION = [
    {"role": "user", "content": "Where is my order A1001?"},
    {"role": "assistant", "content": "It was delivered on Monday."},
    NEW_MESSAGE,
]


def _agent_input(messages: list[dict]) -> AgentInput:
    # Built as the hosted chat target builds it: the whole conversation, ending with the new
    # message, and `new_message` a copy of that last item.
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


@pytest.fixture
def example_module(tmp_path, monkeypatch):
    """A fresh import of the example's `agent.py` on the real SDK, with no `ANTHROPIC_*` set."""
    with fresh_import(tmp_path, monkeypatch, EXAMPLE, clear_env=CLEARED_ENV) as load:
        yield load


class _Seen:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.config_dir_existed: list[bool] = []


def _result(**fields):
    from claude_agent_sdk import ResultMessage

    base = dict(
        subtype="success",
        duration_ms=1,
        duration_api_ms=1,
        is_error=False,
        num_turns=1,
        session_id="s",
        result="Return started.",
    )
    return ResultMessage(**{**base, **fields})


def _assistant(text: str):
    from claude_agent_sdk import AssistantMessage, TextBlock

    return AssistantMessage(content=[TextBlock(text=text)], model=MODEL)


def _fake_query(agent, monkeypatch, *messages, error: Exception | None = None) -> _Seen:
    seen = _Seen()
    script = list(messages) if messages else [_assistant("Return started."), _result()]

    async def query(*, prompt, options):
        seen.calls.append({"prompt": prompt, "options": options})
        seen.config_dir_existed.append(Path(options.env["CLAUDE_CONFIG_DIR"]).is_dir())
        if error is not None:
            raise error
        for message in script:
            yield message

    monkeypatch.setattr(agent, "query", query)
    return seen


def _run(agent, messages):
    return asyncio.run(agent.agent_callback(_agent_input(messages)))


def test_callback_replies_with_the_result_text_only(example_module, monkeypatch) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch, _assistant("draft"), _result(result="Done."))

    assert _run(agent, CONVERSATION) == {"content": "Done."}
    assert len(seen.calls) == 1


def test_reply_falls_back_to_the_last_assistant_text(example_module, monkeypatch) -> None:
    agent = example_module()
    from claude_agent_sdk import AssistantMessage, TextBlock

    _fake_query(
        agent,
        monkeypatch,
        _assistant("first"),
        AssistantMessage(content=[TextBlock(text="last "), TextBlock(text="words")], model=MODEL),
        _result(result=None),
    )

    assert _run(agent, CONVERSATION) == {"content": "last words"}


@pytest.mark.parametrize(
    ("script", "match"),
    [
        ([_assistant("x"), _result(is_error=True, result="boom")], "boom"),
        ([_assistant("x"), _result(subtype="error_max_turns", result=None)], "error_max_turns"),
        ([_assistant("x")], "no result"),
    ],
)
def test_callback_raises_on_an_error_a_wrong_subtype_or_no_result(
    example_module, monkeypatch, script, match
) -> None:
    agent = example_module()
    _fake_query(agent, monkeypatch, *script)

    with pytest.raises(RuntimeError, match=match):
        _run(agent, CONVERSATION)


def test_prompt_with_no_earlier_turn_is_the_last_message(example_module, monkeypatch) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)

    _run(agent, CONVERSATION[:1])

    assert seen.calls[0]["prompt"] == CONVERSATION[0]["content"]


def test_prompt_has_each_earlier_turn_once_in_order_then_the_new_message_once(
    example_module, monkeypatch
) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)
    messages = [
        {"role": "system", "content": "ignored"},
        *CONVERSATION,
    ]

    _run(agent, messages)

    prompt = seen.calls[0]["prompt"]
    assert prompt == (
        "Conversation so far:\n"
        "User: Where is my order A1001?\n"
        "Assistant: It was delivered on Monday.\n"
        "\n"
        "User: Please start a return for A1001."
    )
    for item in CONVERSATION:
        assert prompt.count(item["content"]) == 1
    assert "ignored" not in prompt


def test_empty_messages_or_a_last_message_that_is_not_the_user_raise(
    example_module, monkeypatch
) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)

    with pytest.raises(ValueError):
        asyncio.run(agent.agent_callback(SimpleNamespace(messages=[])))
    with pytest.raises(ValueError):
        _run(agent, CONVERSATION[:2])
    assert seen.calls == []


def test_content_that_is_not_a_string_is_rendered_as_text(example_module, monkeypatch) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "Hi, "}, {"type": "text", "text": "there"}]},
        {"role": "assistant", "content": {"unexpected": 1}},
        {"role": "user", "content": [{"type": "image", "source": "x"}]},
    ]

    _run(agent, messages)

    assert seen.calls[0]["prompt"] == (
        "Conversation so far:\n"
        "User: Hi, there\n"
        "Assistant: {'unexpected': 1}\n"
        "\n"
        "User: [{'type': 'image', 'source': 'x'}]"
    )


def test_prompt_with_exactly_one_earlier_turn(example_module, monkeypatch) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)

    _run(agent, [{"role": "assistant", "content": "Hello."}, NEW_MESSAGE])
    _run(agent, [CONVERSATION[0], NEW_MESSAGE])

    assert [c["prompt"] for c in seen.calls] == [
        "Conversation so far:\nAssistant: Hello.\n\nUser: " + NEW_MESSAGE["content"],
        "Conversation so far:\nUser: " + CONVERSATION[0]["content"]
        + "\n\nUser: " + NEW_MESSAGE["content"],
    ]


def test_handlers_return_the_plain_tool_output_as_text_content(example_module) -> None:
    agent = example_module()
    cases = [
        (agent._look_up_order_tool, {"order_id": "A1002"}, agent.look_up_order("A1002")),
        (
            agent._start_return_tool,
            {"order_id": "A1003", "reason": "too big"},
            "Return started for order A1003: too big.",
        ),
        (agent._return_policy_tool, {}, agent.return_policy()),
    ]
    for handler, args, expected in cases:
        assert asyncio.run(handler.handler(args)) == {
            "content": [{"type": "text", "text": expected}]
        }
    assert "30 days" in cases[2][2]


def test_a_query_that_raises_propagates_and_cleans_up(example_module, monkeypatch) -> None:
    agent = example_module()
    _fake_query(agent, monkeypatch, error=ValueError("error result"))

    with pytest.raises(ValueError, match="error result"):
        _run(agent, CONVERSATION)


def test_options_close_off_everything_but_the_three_support_tools(
    example_module, monkeypatch
) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)

    _run(agent, CONVERSATION)

    options = seen.calls[0]["options"]
    assert options.allowed_tools == [
        "mcp__support__look_up_order",
        "mcp__support__start_return",
        "mcp__support__return_policy",
    ]
    assert options.tools == []
    assert options.strict_mcp_config is True
    assert options.setting_sources == []
    assert options.max_turns == 8
    assert options.model == agent.MODEL == MODEL
    assert options.mcp_servers == {"support": agent.SERVER}
    assert options.system_prompt


def _expected_env(config_dir: str, model: str, *, window: bool, gateway: bool) -> dict:
    # Keys and values as in `config.py` (provider_env), `backends/claude.py` and
    # `test_agent_reading.py::test_the_claude_cli_reports_running_totals`.
    env = {
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "CLAUDE_CODE_SUBAGENT_MODEL": model,
        "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "CLAUDE_CONFIG_DIR": config_dir,
    }
    if window:
        env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] = "1"
        env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"] = "1000000"
    if gateway:
        env["CLAUDE_CODE_USE_VERTEX"] = ""
        env["CLAUDE_CODE_USE_BEDROCK"] = ""
        env["CLAUDE_CODE_USE_FOUNDRY"] = ""
        env["ANTHROPIC_API_KEY"] = ""
        env["CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS"] = "1"
        env["CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING"] = "1"
        env["CLAUDE_CODE_ATTRIBUTION_HEADER"] = "0"
    return env


@pytest.mark.parametrize(
    ("model", "token", "window", "gateway"),
    [
        (MODEL, "placeholder-not-a-token", True, True),
        (MODEL, None, True, False),
        ("claude-sonnet-4-5", None, False, False),
        ("Claude-Sonnet-4-5", None, False, False),
    ],
)
def test_the_env_passed_to_the_cli_is_exactly_this(
    example_module, monkeypatch, model, token, window, gateway
) -> None:
    monkeypatch.setenv("ANTHROPIC_MODEL", model)
    agent = example_module()
    # Set after the import: the env is built per call, not at import.
    if token:
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", token)
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://gateway.test")
    seen = _fake_query(agent, monkeypatch)

    _run(agent, CONVERSATION)

    env = seen.calls[0]["options"].env
    assert env == _expected_env(env["CLAUDE_CONFIG_DIR"], model, window=window, gateway=gateway)
    # Inherited from the process, so it is never copied (and so never counted as protection).
    assert "ANTHROPIC_BASE_URL" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env


@pytest.mark.parametrize("base_url", [None, "", "   "], ids=["unset", "empty", "blank"])
def test_a_token_with_no_base_url_is_refused_before_the_cli_starts(
    example_module, monkeypatch, base_url
) -> None:
    agent = example_module()
    # Set after the import: the check runs on every call.
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "secret-canary-token")
    if base_url is not None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    seen = _fake_query(agent, monkeypatch)

    with pytest.raises(RuntimeError, match="ANTHROPIC_BASE_URL") as refused:
        _run(agent, CONVERSATION)

    assert "secret-canary-token" not in str(refused.value)
    # The CLI never started, so the token went nowhere.
    assert seen.calls == []


def test_a_blank_token_is_not_a_token(example_module, monkeypatch) -> None:
    agent = example_module()
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "   ")
    seen = _fake_query(agent, monkeypatch)

    _run(agent, CONVERSATION)

    assert seen.calls[0]["options"].env == _expected_env(
        seen.calls[0]["options"].env["CLAUDE_CONFIG_DIR"], MODEL, window=True, gateway=False
    )


def test_without_a_token_the_customers_own_key_is_not_blanked(
    example_module, monkeypatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "customer-key-placeholder")
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)

    _run(agent, CONVERSATION)

    assert "ANTHROPIC_API_KEY" not in seen.calls[0]["options"].env


def test_each_turn_gets_a_fresh_temporary_config_folder_and_removes_it(
    example_module, monkeypatch
) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)

    _run(agent, CONVERSATION)
    _run(agent, CONVERSATION)

    folders = [Path(c["options"].env["CLAUDE_CONFIG_DIR"]) for c in seen.calls]
    assert folders[0] != folders[1]
    assert seen.config_dir_existed == [True, True]
    temp = Path(tempfile.gettempdir()).resolve()
    for folder in folders:
        assert folder.resolve().parent == temp
        assert not folder.exists()


def test_the_config_folder_is_removed_when_the_turn_fails(example_module, monkeypatch) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch, error=RuntimeError("cli died"))

    with pytest.raises(RuntimeError, match="cli died"):
        _run(agent, CONVERSATION)

    assert not Path(seen.calls[0]["options"].env["CLAUDE_CONFIG_DIR"]).exists()


def test_a_cleanup_error_does_not_turn_a_good_reply_into_an_exception(
    example_module, monkeypatch
) -> None:
    agent = example_module()
    seen = _fake_query(agent, monkeypatch)
    real = shutil.rmtree

    def failing(path, ignore_errors=False, **kwargs):
        if not ignore_errors:
            raise OSError("cannot remove")
        real(path, ignore_errors=True)

    monkeypatch.setattr(agent.shutil, "rmtree", failing)

    assert _run(agent, CONVERSATION) == {"content": "Return started."}
    assert len(seen.calls) == 1


def test_import_with_nothing_set_succeeds(example_module) -> None:
    agent = example_module()

    assert agent.MODEL == MODEL
    assert agent.SERVER["name"] == "support"


# T5: the example's shape and its requirements -----------------------------------------------


def _module() -> ast.Module:
    return ast.parse((EXAMPLE / "agent.py").read_text(encoding="utf-8"))


def test_example_has_three_plain_tools_and_three_differently_named_handlers() -> None:
    module = _module()
    plain = [
        node.name
        for node in module.body
        if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
    ]
    assert plain == list(TOOLS)
    handlers = {
        node.name: node
        for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name != "agent_callback"
    }
    assert sorted(handlers) == sorted(f"_{name}_tool" for name in TOOLS)
    for name in TOOLS:
        decorators = handlers[f"_{name}_tool"].decorator_list
        (decorator,) = decorators
        assert isinstance(decorator, ast.Call)
        assert ast.unparse(decorator.func) == "tool"
        assert ast.literal_eval(decorator.args[0]) == name
    callbacks = [
        node for node in module.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "agent_callback"
    ]
    assert len(callbacks) == 1


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
        "ANTHROPIC_MODEL",
        "vertex_ai/gemini-3.7-flash",
    ]


def test_folder_holds_exactly_the_example_files() -> None:
    names = sorted(path.name for path in EXAMPLE.iterdir() if path.name != "__pycache__")
    assert names == [".python-version", "README.md", "agent.py", "requirements.txt"]


def test_requirements_and_python_version_are_pinned() -> None:
    assert_pins(EXAMPLE, ["claude-agent-sdk==0.2.174", "agent-simulate==0.1.3"])
