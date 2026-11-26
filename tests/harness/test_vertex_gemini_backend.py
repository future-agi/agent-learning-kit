import asyncio
from typing import Any

import pytest

from fi.alk.harness.backends.vertex_gemini import (
    _flattened,
    _forget_old_reads,
    _successful_terminal_save,
)


def test_session_replies_with_installed_adk(monkeypatch) -> None:
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from fi.alk.harness.backends import ModelReply, SessionSpec, StageDone
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiSession

    async def generate(self, llm_request, stream=False):
        assert llm_request.contents[-1].parts[0].text == "Hello"
        yield LlmResponse(
            content=types.Content(role="model", parts=[types.Part(text="Hello back")])
        )

    monkeypatch.setattr(Gemini, "generate_content_async", generate)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

    async def run():
        session = VertexGeminiSession(
            SessionSpec(system_prompt="Reply briefly", max_turns=1),
            model="gemini-3.7-flash",
        )
        await session.start()
        try:
            await session.send("Hello")
            return [event async for event in session.replies()]
        finally:
            await session.stop()

    events = asyncio.run(run())
    replies = [event for event in events if isinstance(event, ModelReply)]
    assert replies[0].parts[0].text == "Hello back"
    assert isinstance(events[-1], StageDone)
    assert events[-1].outcome == "success"


@pytest.mark.parametrize("ending", ["cancel", "complete", "cancel_before_usage"])
def test_stage_settles_reported_vertex_usage_once(monkeypatch, ending):
    from types import SimpleNamespace

    from google.adk.runners import Runner

    from fi.alk.harness import spend
    from fi.alk.harness.backends import SessionSpec
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiBackend, priced
    from fi.alk.harness.session import Stage

    model = "gemini-3.7-flash"
    monkeypatch.setattr(spend, "_stages", {})
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

    async def run():
        waiting = asyncio.Event()

        async def events(self, **kwargs):
            if ending != "cancel_before_usage":
                yield SimpleNamespace(
                    content=None,
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=1000, candidates_token_count=200,
                        cached_content_token_count=300,
                    ),
                    is_final_response=lambda: ending == "complete",
                )
            if ending != "complete":
                waiting.set()
                await asyncio.Event().wait()

        monkeypatch.setattr(Runner, "run_async", events)
        async with Stage(SessionSpec(system_prompt="Reply", model=model),
                         backend=VertexGeminiBackend(), name="builder") as stage:
            turn = asyncio.create_task(stage.say("Hello"))
            if ending == "complete":
                assert (await turn).outcome == "success"
            else:
                await asyncio.wait_for(waiting.wait(), timeout=5)
                turn.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await turn
            # The checkpoint reads the ledger immediately, before generator cleanup.
            ending_ledger = spend.snapshot()
        assert spend.snapshot() == ending_ledger
        return ending_ledger

    ledger = asyncio.run(run())
    if ending == "cancel_before_usage":
        assert ledger["stages"] == []
    else:
        entry, = ledger["stages"]
        assert entry["priced"] == 1
        assert (entry["tokens_in"], entry["tokens_out"], entry["tokens_cached"]) == (1000, 200, 300)
        assert ledger["total_usd"] == round(priced(model, 1000, 200, 300), 6)


@pytest.mark.parametrize("method", ["stream", "resume"])
def test_closing_vertex_stream_settles_usage_before_next_turn(monkeypatch, method):
    from types import SimpleNamespace

    from google.adk.runners import Runner

    from fi.alk.harness import spend
    from fi.alk.harness.backends import SessionSpec
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiBackend, priced
    from fi.alk.harness.session import Stage

    model = "gemini-3.7-flash"
    monkeypatch.setattr(spend, "_stages", {})
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

    async def events(self, **kwargs):
        yield SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="Hello back")]),
            usage_metadata=SimpleNamespace(
                prompt_token_count=1000, candidates_token_count=200,
                cached_content_token_count=300,
            ),
            is_final_response=lambda: True,
        )

    def assert_ledger(count):
        ledger = spend.snapshot()
        entry, = ledger["stages"]
        assert entry["priced"] == count
        assert entry["tokens_in"] == 1000 * count
        assert entry["tokens_out"] == 200 * count
        assert entry["tokens_cached"] == 300 * count
        assert ledger["total_usd"] == round(count * priced(model, 1000, 200, 300), 6)

    async def run():
        async with Stage(SessionSpec(system_prompt="Reply", model=model),
                         backend=VertexGeminiBackend(), name="builder") as stage:
            stream = stage.stream("Hello") if method == "stream" else stage.resume("inv-1")
            assert (await anext(stream)).text == "Hello back"
            await stream.aclose()
            assert_ledger(1)
            assert (await stage.say("Again")).outcome == "success"
            assert_ledger(2)
        assert_ledger(2)

    monkeypatch.setattr(Runner, "run_async", events)
    asyncio.run(run())


def test_successful_authoring_save_is_terminal() -> None:
    assert _successful_terminal_save(
        "mcp__world__save_world", {"content": "Saved to /work/authoring."}
    )
    assert _successful_terminal_save(
        "mcp__provision__save_environment", {"content": "Saved."}
    )
    assert _successful_terminal_save(
        "mcp__scenarios__save_scenarios", {"content": "Saved 10 scenarios."}
    )
    assert _successful_terminal_save(
        "mcp__source_data__finish_review", {"content": "Saved 12 invariants."}
    )
    assert _successful_terminal_save(
        "mcp__repair__submit_world_ir_patch", {"content": "Patch accepted."}
    )


def test_provider_429_exhausts_bounded_retries(monkeypatch) -> None:
    from google.genai import _api_client
    from google.genai.errors import ClientError

    from fi.alk.harness.backends import SessionSpec, StageDone
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiSession

    timeouts = []

    async def rate_limited(self, http_request, stream=False, **_options):
        # google-genai 2.34 also passes `implicit_retry`.
        timeouts.append(http_request.timeout)
        raise ClientError(
            429,
            {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}},
        )

    real_sleep = asyncio.sleep
    monkeypatch.setattr(asyncio, "sleep", lambda _: real_sleep(0))
    monkeypatch.setattr(_api_client.BaseApiClient, "_async_request_once", rate_limited)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    async def run():
        session = VertexGeminiSession(
            SessionSpec(system_prompt="Reply briefly", max_turns=2),
            model="gemini-3.7-flash",
        )
        await session.start()
        try:
            await session.send("Hello")
            return [event async for event in session.replies()]
        finally:
            await session.stop()

    events = asyncio.run(run())
    assert timeouts == [300.0] * 8
    assert isinstance(events[-1], StageDone)
    assert events[-1].outcome == "failed"
    assert events[-1].is_error
    assert "429" in events[-1].errors[0]


@pytest.mark.parametrize("server,tool", [("world", "save_world"), ("runtime_repair", "submit_runtime_plan_patch")])
def test_terminal_save_closes_adk_invocation_in_its_original_context(monkeypatch, caplog, server, tool):
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.adk.telemetry import _instrumentation, tracing
    from google.genai import types
    from opentelemetry.sdk.trace import TracerProvider

    from fi.alk.harness.backends import SessionSpec, ToolServer, ToolSpec
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiBackend
    from fi.alk.harness.session import Stage

    async def generate(self, llm_request, stream=False):
        if llm_request.contents[-1].parts[0].function_response is not None:
            await asyncio.Event().wait()
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part(function_call=types.FunctionCall(
                    id="save", name=f"mcp__{server}__{tool}", args={}
                ))],
            )
        )

    async def save(args):
        return {"content": "Saved."}

    provider = TracerProvider()
    monkeypatch.setattr(tracing, "tracer", provider.get_tracer("regression"))
    monkeypatch.setattr(_instrumentation, "resolve_schema_version", lambda: 1)
    monkeypatch.setattr(Gemini, "generate_content_async", generate)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

    async def run():
        async with Stage(
            SessionSpec(
                system_prompt="Save the world",
                servers={server: ToolServer(
                    name=server, tools=[ToolSpec(tool, "Save", {}, save)]
                )},
                max_turns=2,
                model="gemini-3.7-flash",
            ),
            backend=VertexGeminiBackend(),
            overheard=False,
        ) as stage:
            events = [event async for event in stage.stream("Save")]
            assert events[-1].detail["outcome"] == "success"
            assert stage.history[-1].outcome == "success"

    try:
        asyncio.run(run())
        assert "Failed to detach context" not in caplog.text
    finally:
        provider.shutdown()


def test_rejected_save_and_non_save_tools_are_not_terminal() -> None:
    assert not _successful_terminal_save(
        "mcp__world__save_world", {"is_error": True, "content": "Not saved."}
    )
    assert not _successful_terminal_save(
        "mcp__world__check_world", {"content": "All checks pass."}
    )
    assert not _successful_terminal_save("mcp__world__save_world", "Saved")


def _read(call_id: str, name: str, text: str) -> Any:
    from google.genai import types

    return types.Content(
        role="user",
        parts=[
            types.Part(
                function_response=types.FunctionResponse(
                    id=call_id, name=name, response={"content": text}
                )
            )
        ],
    )


def _said(part: Any) -> str:
    return _flattened(part.parts[0].function_response.response)


def test_small_sessions_keep_every_read() -> None:
    contents = [
        _read(f"c{n}", "mcp__scenarios__inspect_world", "x" * 100) for n in range(10)
    ]
    _forget_old_reads(contents)
    assert all(_said(one) == "x" * 100 for one in contents)


def test_a_long_session_keeps_recent_reads_within_the_character_budget() -> None:
    contents = [
        _read(f"c{n}", "mcp__scenarios__inspect_world", "x" * 30_000) for n in range(10)
    ]
    _forget_old_reads(contents)
    kept = [one for one in contents if _said(one) == "x" * 30_000]
    assert len(kept) == 6
    assert kept == contents[-6:]
    assert "inspect_world" in _said(contents[0])


def test_forgetting_is_per_tool_and_leaves_writes_alone() -> None:
    contents = [
        _read("c0", "mcp__scenarios__inspect_world", "w" * 90_000),
        _read("c1", "mcp__scenarios__inspect_world", "w" * 90_000),
        _read("c2", "mcp__scenarios__inspect_world", "w" * 90_000),
        _read("c3", "mcp__scenarios__inspect_scenario", "s" * 100),
        _read("c4", "mcp__scenarios__submit_scenario", "gate said no" * 500),
    ]
    _forget_old_reads(contents)
    assert _said(contents[0]).startswith("[dropped")
    assert _said(contents[1]) == "w" * 90_000
    assert _said(contents[3]) == "s" * 100
    assert _said(contents[4]) == "gate said no" * 500


def test_forgetting_twice_changes_nothing_more() -> None:
    contents = [
        _read(f"c{n}", "mcp__scenarios__inspect_world", "x" * 30_000) for n in range(10)
    ]
    _forget_old_reads(contents)
    once = [_said(one) for one in contents]
    _forget_old_reads(contents)
    assert [_said(one) for one in contents] == once


def test_forgetting_leaves_the_session_event_alone() -> None:
    from google.genai import types

    session = [
        _read(f"c{n}", "mcp__scenarios__inspect_world", "x" * 30_000) for n in range(10)
    ]
    request = [
        types.Content(role=one.role, parts=[part.model_copy() for part in one.parts])
        for one in session
    ]
    _forget_old_reads(request)
    assert _said(request[0]).startswith("[dropped")
    assert all(_said(one) == "x" * 30_000 for one in session)


def test_compaction_is_configured_and_can_be_switched_off(monkeypatch) -> None:
    from fi.alk.harness.backends import vertex_gemini

    monkeypatch.setattr(vertex_gemini, "COMPACT_ABOVE_TOKENS", 90_000)
    monkeypatch.setattr(vertex_gemini, "EVENTS_KEPT_RAW", 12)
    config = vertex_gemini._compaction()
    assert config.token_threshold == 90_000
    assert config.event_retention_size == 12

    monkeypatch.setattr(vertex_gemini, "COMPACT_ABOVE_TOKENS", 0)
    assert vertex_gemini._compaction() is None


def test_a_tool_call_carries_the_agent_that_made_it() -> None:
    from fi.alk.harness.backends import Call

    assert Call(id="c1", name="x").by == ""
    assert Call(id="c1", name="x", by="scenario_writer").by == "scenario_writer"


def test_a_cache_read_is_not_charged_at_the_full_input_rate(monkeypatch) -> None:
    from datetime import date

    from fi.alk.harness.backends import vertex_gemini
    from fi.alk.harness.backends.vertex_gemini import CACHE_READ_SHARE, priced

    # The 0.75 rate holds through 2026-12-31: a price is a schedule of dated rows.
    monkeypatch.setattr(vertex_gemini, "_today", lambda: date(2026, 12, 1))
    full = priced("gemini-3.7-flash", 1_000_000, 0, 0)
    all_cached = priced("gemini-3.7-flash", 1_000_000, 0, 1_000_000)
    assert full == 0.75
    assert all_cached == round(0.75 * CACHE_READ_SHARE, 6) or abs(all_cached - 0.075) < 1e-9
    assert priced("gemini-3.7-flash", 1_000, 0, 999_999) >= 0


def test_the_claude_sdk_drives_gemini_only_behind_the_gateway(monkeypatch):
    from fi.alk.harness.backends import resolve

    backend = resolve("claude-gemini")
    assert backend.name == "claude"

    monkeypatch.delenv("AGENTCC_API_KEY", raising=False)
    assert not backend.can_drive("gemini-3.7-flash")
    assert not backend.can_drive("vertex_ai/gemini-3.7-flash")

    monkeypatch.setenv("AGENTCC_API_KEY", "not-a-real-key")
    assert backend.can_drive("vertex_ai/gemini-3.7-flash")


def test_the_gateway_route_never_carries_the_vertex_claude_flag(monkeypatch):
    from fi.alk.harness.config import provider_env

    monkeypatch.setenv("AGENTCC_API_KEY", "not-a-real-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example.test/")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/creds.json")
    env = provider_env("vertex_ai/gemini-3.7-flash")
    assert env["CLAUDE_CODE_USE_VERTEX"] == "0"
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway.example.test"
    for pinned in ("ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
        assert env[pinned] == "vertex_ai/gemini-3.7-flash"
    # A model the SDK does not know has no window to look up, so the run declares one.
    assert env["CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT"] == "1"

    monkeypatch.setenv("AGENTCC_CLAUDE_MODEL_ALIAS", "claude-sonnet-4-6")
    aliased = provider_env("vertex_ai/gemini-3.7-flash")
    assert aliased["ANTHROPIC_MODEL"] == "claude-sonnet-4-6"
    assert "CLAUDE_CODE_DISABLE_UNKNOWN_MODEL_WINDOW_ENFORCEMENT" not in aliased
    monkeypatch.delenv("AGENTCC_CLAUDE_MODEL_ALIAS")

    monkeypatch.delenv("AGENTCC_API_KEY")
    straight = provider_env("gemini-3.7-flash")
    assert straight["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert "ANTHROPIC_BASE_URL" not in straight


def _declaration(schema):
    from fi.alk.harness.backends import ToolSpec
    from fi.alk.harness.backends.vertex_gemini import _spec_tool

    async def handler(args):
        return args

    return _spec_tool("submit", ToolSpec(
        name="submit", description="Submit", input_schema=schema, handler=handler,
    ))._get_declaration()


def _without_references(value):
    if isinstance(value, list):
        return all(_without_references(item) for item in value)
    if not isinstance(value, dict):
        return True
    return "$ref" not in value and "$defs" not in value and all(
        _without_references(item) for item in value.values()
    )


def _object_branch(schema):
    return next(
        item
        for item in schema.get("anyOf", [schema])
        if isinstance(item, dict) and item.get("type") == "object"
    )


def test_declaration_inlines_json_schema_on_vertex_wire():
    from copy import deepcopy
    from types import SimpleNamespace
    from google.genai import _common
    from google.genai.models import _GenerateContentParameters_to_vertex
    from google.genai.types import _GenerateContentParameters
    from fi.alk.harness.backends import tool_input_schema
    from fi.alk.harness.runtime_repair import _RuntimeSubmission
    from fi.alk.harness.repair_authoring import _PatchSubmission

    for model in (_RuntimeSubmission, _PatchSubmission):
        original = model.model_json_schema()
        expected = tool_input_schema(original)
        supplied = deepcopy(original)
        declaration = _declaration(supplied)
        assert declaration.parameters is None
        assert declaration.parameters_json_schema == expected
        params = _GenerateContentParameters(
            model="gemini-test", contents="test",
            config={"tools": [{"function_declarations": [declaration]}]},
        )
        wire = _GenerateContentParameters_to_vertex(SimpleNamespace(vertexai=True), _common.convert_to_dict(params))
        wire = _common.encode_unserializable_types(_common.convert_to_dict(wire))
        actual = wire["tools"][0]["functionDeclarations"][0]["parameters_json_schema"]
        assert actual == expected
        assert _without_references(actual)
        assert supplied == original
    assert original["$defs"]["RepairPatchOperation"]["properties"]["op"]["$ref"]


def test_runtime_repair_declaration_exposes_nested_observation_schema():
    from fi.alk.harness.runtime_repair import _RuntimeSubmission

    schema = _declaration(_RuntimeSubmission.model_json_schema()).parameters_json_schema
    runtime = schema["properties"]["runtime"]
    interface = _object_branch(runtime["properties"]["interface"])
    assert {"completed_tool_events", "completed_tools"} <= set(interface["properties"])
    events = _object_branch(interface["properties"]["completed_tool_events"])["properties"]
    assert {"path", "correlation_header", "action_id_path", "result_path"} <= set(events)
    assert events["source"]["enum"] == ["endpoint", "response"]
    assert events["max_pages"]["maximum"] == 20
    assert _without_references(schema)


def test_submit_contract_schema_preserves_scalar_and_map_constraints(tmp_path):
    from fi.alk.harness.tools import contract_tools

    submit = next(item for item in contract_tools(tmp_path).tools if item.name == "submit_contract")
    actual = _declaration(submit.input_schema).parameters_json_schema
    assert actual == submit.input_schema
    runtime = actual["properties"]["runtime"]["properties"]
    predicate = runtime["interface"]["properties"]["failure_rule"]["properties"]["success_values"]
    assert "[null] for JSON null" in predicate["description"]
    assert set(predicate["items"]["type"]) == {"string", "number", "boolean", "null"}
    config = runtime["config_files"]["additionalProperties"]
    assert config["additionalProperties"] is False
    assert set(config["properties"]) == {"format", "values", "secret_aliases"}


def test_declaration_expands_python_shorthand():
    schema = _declaration({"name": str, "count": int, "rows": list}).parameters_json_schema
    assert schema == {
        "type": "object", "properties": {
            "name": {"type": "string"}, "count": {"type": "integer"},
            "rows": {"type": "array", "items": {"type": "object"}},
        }, "required": ["name", "count", "rows"],
    }


def test_the_call_budget_holds_across_messages_and_sends_nothing_once_spent(monkeypatch) -> None:
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    from fi.alk.harness.backends import CALL_BUDGET_SPENT, SessionSpec, StageDone
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiSession

    sent: list[str] = []

    async def generate(self, llm_request, stream=False):
        sent.append(llm_request.contents[-1].parts[0].text)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))

    monkeypatch.setattr(Gemini, "generate_content_async", generate)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

    async def run():
        session = VertexGeminiSession(
            SessionSpec(system_prompt="Reply briefly", max_turns=10, call_budget=2),
            model="gemini-3.7-flash",
        )
        await session.start()
        ends = []
        try:
            for message in ("one", "two", "three", "four"):
                await session.send(message)
                ends.append([event async for event in session.replies()][-1])
            return ends
        finally:
            await session.stop()

    ends = asyncio.run(run())

    # The budget is the session's, not one message's: the third message sends nothing.
    assert sent == ["one", "two"]
    assert all(isinstance(end, StageDone) for end in ends)
    assert [(end.outcome, end.calls) for end in ends] == [
        ("success", 1),
        ("success", 1),
        (CALL_BUDGET_SPENT, 0),
        (CALL_BUDGET_SPENT, 0),
    ]
    assert ends[2].is_error


def test_a_streamed_tool_call_starts_once(monkeypatch) -> None:
    """ADK streams a call in a partial chunk, then again in the final message: one tool, one start."""
    from types import SimpleNamespace

    from google.adk.runners import Runner

    from fi.alk.harness import spend
    from fi.alk.harness.backends import SessionSpec
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiBackend
    from fi.alk.harness.session import TOOL, Stage

    monkeypatch.setattr(spend, "_stages", {})
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")

    def call(call_id):
        return SimpleNamespace(
            function_call=SimpleNamespace(id=call_id, name="run_proof", args={})
        )

    async def events(self, **kwargs):
        yield SimpleNamespace(
            content=SimpleNamespace(parts=[call(None)]),
            partial=True,
            usage_metadata=None,
            is_final_response=lambda: False,
        )
        yield SimpleNamespace(
            content=SimpleNamespace(parts=[call("fc-1")]),
            partial=False,
            usage_metadata=None,
            is_final_response=lambda: False,
        )
        yield SimpleNamespace(
            content=SimpleNamespace(parts=[SimpleNamespace(text="Proved.")]),
            usage_metadata=None,
            is_final_response=lambda: True,
        )

    async def run():
        async with Stage(SessionSpec(system_prompt="Reply", model="gemini-3.8-flash"),
                         backend=VertexGeminiBackend(), name="builder") as stage:
            return [event async for event in stage.stream("Prove it")]

    monkeypatch.setattr(Runner, "run_async", events)
    started = [event for event in asyncio.run(run()) if event.kind == TOOL]
    assert [event.detail.get("call_id") for event in started] == ["fc-1"]
