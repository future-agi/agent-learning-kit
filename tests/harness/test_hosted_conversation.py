from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness.backends import SessionSpec, ToolServer, ToolSpec
from fi.alk.harness.chat_policy import (
    ChatPolicyError,
    apply_chat_policy,
    load_chat_policy,
)
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
from fi.alk.harness.poc_guest_booking import PIN_ENV, TARGET_PHONE_ENV
from fi.alk.harness.hosted_conversation import (
    ConversationCapabilities,
    ConversationClient,
)
from fi.alk.harness.session import Event as RuntimeEvent


def _capabilities() -> ConversationCapabilities:
    conversation_id = "4f1ddaa9-569e-4ddd-bef0-74926bb42b16"
    prefix = (
        f"https://platform.example/simulate/api/harness/conversations/{conversation_id}"
    )
    return ConversationCapabilities.model_validate(
        {
            "schema_version": "futureagi.harness-conversation.v1",
            "conversation_id": conversation_id,
            "job_id": "job-1",
            "token": "token",
            "fence": "fence",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
            "identity": {
                "app_name": "alk-harness",
                "user_id": "conversation-user",
            },
            "turn_context": {
                "conversation_id": conversation_id,
                "stage": "run",
                "event_watermark": 0,
                "command_watermark": 0,
            },
            "endpoints": {
                "commands": f"{prefix}/commands/",
                "events": f"{prefix}/events/",
                "rerun": f"{prefix}/rerun/",
                "adjust": f"{prefix}/adjust/",
                "run_status": f"{prefix}/run-status/",
                "workspace": f"{prefix}/workspace/",
                "session_store": f"{prefix}/session-store/",
                "session_store_append": f"{prefix}/session-store/append/",
            },
        }
    )


def test_conversation_turn_does_not_require_undeclared_history():
    from fi.alk.harness.chat import Conversation

    class StageStub:
        spent_usd = 0.25

        async def say(self, _message, on_event=None):
            del on_event

        async def __aexit__(self, *_args):
            return None

    conversation = Conversation(stage=StageStub(), stage_name="run")

    asyncio.run(conversation.say("what is happening?"))
    asyncio.run(conversation.close())
    assert conversation.spent_usd == 0.25


def test_hosted_chat_leaves_unrelated_invalid_jobs_on_generic_path(monkeypatch):
    from fi.alk.harness.poc_guest_booking import guest_booking_policy_job

    monkeypatch.setenv(TARGET_PHONE_ENV, "+15551234567")
    monkeypatch.setenv(PIN_ENV, "7682")
    job = {
        "metadata": {"agent_name": "ordinary phone agent"},
        "scenario_count": 10,
        "agent": {"connector": "phone", "config": {"phone_number": "+15557654321"}},
        # Deliberately not a complete SDK HarnessJob: generic chat accepted this before the POC.
        "runtime": {"parallelism": 20, "cpu_units": 1},
    }

    assert guest_booking_policy_job(job) is None


def test_conversation_client_starts_from_durable_command_watermark():
    capabilities = _capabilities()
    capabilities.turn_context["command_watermark"] = 7

    client = ConversationClient(capabilities)

    assert client.command_watermark == 7


def test_conversation_client_bounds_long_provider_call_ids():
    capabilities = _capabilities()

    class Transport:
        def __init__(self):
            self.body = None

        def request(self, _method, _url, *, headers, json_body=None, timeout):
            del headers, timeout
            self.body = json_body
            return type(
                "Response",
                (),
                {
                    "status_code": 200,
                    "body": {"acked_through_sequence": 1},
                },
            )()

    transport = Transport()
    client = ConversationClient(capabilities, transport=transport)

    asyncio.run(
        client.emit(
            "tool_started",
            function_call_id="provider-signature-" + ("x" * 500),
        )
    )

    call_id = transport.body["events"][0]["function_call_id"]
    assert len(call_id) <= 255
    assert call_id.startswith("call-")


def test_authoring_activity_pump_does_not_replay_existing_lines(tmp_path):
    events_path = tmp_path / "harness-events.jsonl"
    events_path.write_text(
        json.dumps(
            {
                "event_type": "harness.stage.done",
                "payload": {"stage": "scenarios", "status": 0},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    class ActivityClient:
        def __init__(self):
            self.events = []

        async def emit(self, kind, **kwargs):
            self.events.append((kind, kwargs))

    runtime = object.__new__(HostedChatRuntime)
    runtime.workspace = tmp_path
    runtime.client = ActivityClient()
    runtime.stopping = asyncio.Event()

    async def exercise():
        task = asyncio.create_task(runtime._pump_authoring_activity())
        await asyncio.sleep(0)
        assert runtime.client.events == []
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "event_type": "harness.stage.done",
                        "payload": {"stage": "run", "status": 0},
                    }
                )
                + "\n"
            )
        for _ in range(20):
            if runtime.client.events:
                break
            await asyncio.sleep(0.05)
        runtime.stopping.set()
        await task

    asyncio.run(exercise())
    assert len(runtime.client.events) == 1
    assert runtime.client.events[0][0] == "authoring_activity"


def test_chat_policy_gates_stages_without_redeclaring_stage_tools(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """schema_version: 1
harness_chat:
  enabled: true
  stages:
    run:
      enabled: true
  lifecycle:
    request_user_input: true
    interrupt_response: true
""",
        encoding="utf-8",
    )
    policy = load_chat_policy(policy_path)

    async def handler(_args):
        return {"content": []}

    spec = SessionSpec(
        system_prompt="run",
        servers={
            "run": ToolServer(
                name="run",
                tools=[
                    ToolSpec("read_results", "read", {}, handler),
                    ToolSpec("run_simulation", "run", {}, handler),
                ],
            ),
            "flow": ToolServer(
                name="flow",
                tools=[ToolSpec("route_to_stage", "route", {}, handler)],
            ),
        },
        builtins=("Read", "AskUserQuestion"),
        ask=handler,
    )
    filtered = apply_chat_policy(spec, stage="run", policy=policy)
    assert filtered.granted() == [
        "Read",
        "AskUserQuestion",
        "mcp__run__read_results",
        "mcp__run__run_simulation",
        "mcp__flow__route_to_stage",
    ]


def test_a_disabled_stage_gets_no_workers_either(tmp_path):
    from fi.alk.harness.backends import WorkerSpec

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """schema_version: 1
harness_chat:
  enabled: true
  stages:
    run:
      enabled: false
""",
        encoding="utf-8",
    )
    policy = load_chat_policy(policy_path)

    async def handler(_args):
        return {"content": []}

    tools = ToolServer(name="run", tools=[ToolSpec("run_simulation", "run", {}, handler)])
    spec = SessionSpec(
        system_prompt="run",
        servers={"run": tools},
        builtins=("Read",),
        workers={"runner": WorkerSpec(description="runs", instructions="run", servers={"run": tools})},
    )

    filtered = apply_chat_policy(spec, stage="run", policy=policy)

    # A worker brings its own tools, so keeping it would leave the stage able to act.
    assert filtered.granted_anywhere() == []
    assert filtered.workers == {}


def test_chat_policy_rejects_unknown_stages(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """schema_version: 1
harness_chat:
  enabled: true
  stages:
    invent:
      enabled: true
""",
        encoding="utf-8",
    )
    with pytest.raises(ChatPolicyError, match="unknown hosted chat stages"):
        load_chat_policy(policy_path)


class QuestionClient:
    def __init__(self) -> None:
        self.capabilities = _capabilities()
        self.turn_context = self.capabilities.turn_context
        self.command_watermark = 0
        self.events: list[dict[str, Any]] = []

    async def emit(self, kind: str, **kwargs):
        self.events.append({"kind": kind, **kwargs})
        return self.events[-1]

    async def commands(self):
        question = next(
            event for event in self.events if event["kind"] == "question_requested"
        )
        return [
            {
                "sequence": 2,
                "message_id": "answer-1",
                "kind": "user_response",
                "payload": {
                    "content": "Use app/api.py",
                    "reply_to": question["message_id"],
                },
            }
        ]

    async def checkpoint(self, workspace: Path):
        del workspace
        return {"digest": "sha256:" + "0" * 64, "size": 0}


def test_blocking_question_waits_for_matching_conversation_response(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """schema_version: 1
harness_chat:
  enabled: true
  stages:
    reception:
      enabled: true
  lifecycle:
    request_user_input: true
    interrupt_response: true
""",
        encoding="utf-8",
    )
    client = QuestionClient()
    runtime = HostedChatRuntime(
        client=client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=tmp_path,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    result = asyncio.run(
        runtime._ask(
            "AskUserQuestion",
            {"question": "Which entrypoint?", "options": ["app/api.py", "worker.py"]},
            None,
        )
    )
    assert result == {
        "content": [{"type": "text", "text": "Use app/api.py"}],
        "is_error": False,
    }
    question = client.events[0]
    assert question["kind"] == "question_requested"
    assert question["payload"]["options"] == ["app/api.py", "worker.py"]


def test_runtime_reuses_warm_state_and_does_not_reexecute_cold_replays(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ALK_HARNESS", "vertex-gemini")
    monkeypatch.setenv("ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER", "1")
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """schema_version: 1
harness_chat:
  enabled: true
  stages:
    reception:
      enabled: true
""",
        encoding="utf-8",
    )

    class TurnClient:
        def __init__(self):
            self.capabilities = _capabilities()
            self.turn_context = self.capabilities.turn_context
            self.command_watermark = 0
            self.events = []
            self.checkpoints = 0

        async def emit(self, kind, **kwargs):
            self.events.append({"kind": kind, **kwargs})
            acknowledged = kwargs.get("acknowledge_through")
            if acknowledged is not None:
                self.command_watermark = max(self.command_watermark, acknowledged)
            return self.events[-1]

        async def checkpoint(self, workspace):
            assert workspace == tmp_path
            self.checkpoints += 1
            return {
                "digest": f"sha256:{self.checkpoints:064x}",
                "size": self.checkpoints,
            }

    class TurnConversation:
        stage = None
        stage_name = "run"

        def __init__(self):
            self.messages = []
            self.resumed = []

        async def say(self, message, receive):
            self.messages.append(message)
            receive(
                RuntimeEvent(
                    kind="text",
                    text=f"Reply to {message}",
                    detail={"stage": "run", "invocation_id": f"reply-{message}"},
                )
            )

        async def resume(self, invocation_id, receive):
            self.resumed.append(invocation_id)
            receive(
                RuntimeEvent(
                    kind="text",
                    text="Resumed reply",
                    detail={"stage": "run", "invocation_id": invocation_id},
                )
            )

        async def close(self):
            return None

    def command(sequence, content):
        return {
            "sequence": sequence,
            "message_id": f"message-{sequence}",
            "kind": "user_message",
            "payload": {"content": content},
        }

    warm_client = TurnClient()
    warm_runtime = HostedChatRuntime(
        client=warm_client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=tmp_path,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    warm_conversation = TurnConversation()
    warm_runtime.conversation = warm_conversation  # type: ignore[assignment]

    async def exercise_warm_turns():
        await warm_runtime._handle(command(1, "first"))
        await warm_runtime._handle(command(2, "second"))

    asyncio.run(exercise_warm_turns())
    assert warm_conversation.messages == ["first", "second"]
    assert warm_client.command_watermark == 2
    assert warm_client.checkpoints == 4
    assert [event["kind"] for event in warm_client.events].count(
        "assistant_message"
    ) == 2

    cold_client = TurnClient()
    cold_runtime = HostedChatRuntime(
        client=cold_client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=tmp_path,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    cold_conversation = TurnConversation()
    cold_runtime.conversation = cold_conversation  # type: ignore[assignment]
    asyncio.run(cold_runtime._handle(command(1, "first")))

    assert cold_conversation.messages == []
    assert cold_client.command_watermark == 1
    assert cold_client.events == [
        {
            "kind": "turn_completed",
            "stage": "run",
            "payload": {
                "command_message_id": "message-1",
                "outcome": "success",
                "usage": {
                    "tokens_in": 0,
                    "tokens_out": 0,
                    "tokens_cached": 0,
                    "usd": 0.0,
                    "unpriced_turns": 0,
                    "models": [],
                },
                "replayed": True,
            },
            "acknowledge_through": 1,
        }
    ]

    journal_path = tmp_path / ".futureagi-conversation.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["message-3"] = "started"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
    # The checkpoint brought the session database back with the workspace.
    (tmp_path / ".futureagi-adk.db").write_bytes(b"")
    resumed_client = TurnClient()
    resumed_client.turn_context["active_invocation_id"] = "provider-invocation-3"
    resumed_runtime = HostedChatRuntime(
        client=resumed_client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=tmp_path,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    resumed_conversation = TurnConversation()
    resumed_runtime.conversation = resumed_conversation  # type: ignore[assignment]
    asyncio.run(resumed_runtime._handle(command(3, "third")))
    assert resumed_conversation.messages == []
    assert resumed_conversation.resumed == ["provider-invocation-3"]
    assert resumed_client.command_watermark == 3


def test_interrupt_stops_the_active_turn_and_acknowledges_control(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """schema_version: 1
harness_chat:
  enabled: true
  stages:
    reception:
      enabled: true
""",
        encoding="utf-8",
    )

    class InterruptClient:
        def __init__(self):
            self.capabilities = _capabilities()
            self.turn_context = self.capabilities.turn_context
            self.command_watermark = 0
            self.events = []

        async def commands(self):
            return [
                {
                    "sequence": 2,
                    "message_id": "interrupt-2",
                    "kind": "interrupt",
                    "payload": {},
                }
            ]

        async def emit(self, kind, **kwargs):
            self.events.append({"kind": kind, **kwargs})
            acknowledged = kwargs.get("acknowledge_through")
            if acknowledged is not None:
                self.command_watermark = max(self.command_watermark, acknowledged)
            return self.events[-1]

        async def checkpoint(self, workspace):
            assert workspace == tmp_path
            return {"digest": "sha256:" + "0" * 64, "size": 0}

    class InterruptibleConversation:
        stage = None
        stage_name = "run"

        def __init__(self):
            self.closed = False

        async def say(self, _message, receive):
            receive(
                RuntimeEvent(
                    kind="text",
                    text="Working",
                    detail={"stage": "run", "invocation_id": "active-turn"},
                )
            )
            await asyncio.Event().wait()

        async def close(self):
            self.closed = True

    client = InterruptClient()
    runtime = HostedChatRuntime(
        client=client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=tmp_path,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    conversation = InterruptibleConversation()
    runtime.conversation = conversation  # type: ignore[assignment]
    asyncio.run(
        runtime._handle(
            {
                "sequence": 1,
                "message_id": "message-1",
                "kind": "user_message",
                "payload": {"content": "Run the suite"},
            }
        )
    )

    assert conversation.closed
    assert client.command_watermark == 2
    assert any(event["kind"] == "turn_interrupted" for event in client.events)
    completed = client.events[-1]
    assert completed["kind"] == "turn_completed"
    assert completed["payload"]["outcome"] == "interrupted"


def test_answer_does_not_acknowledge_queued_followup(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "schema_version: 1\nharness_chat:\n  enabled: true\n"
        "  stages:\n    reception:\n      enabled: true\n"
    )

    class QueuedClient(QuestionClient):
        async def commands(self):
            answer = (await super().commands())[0]
            answer["sequence"] = 4
            return [
                {
                    "sequence": 2,
                    "message_id": "followup",
                    "kind": "user_message",
                    "payload": {"content": "Explain the scenario"},
                },
                answer,
            ]

    client = QueuedClient()
    runtime = HostedChatRuntime(
        client=client,
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=tmp_path,
        job={},
    )
    runtime._turn_acknowledged_through = 1
    result = asyncio.run(
        runtime._ask("AskUserQuestion", {"question": "Which file?"}, None)
    )
    assert result["content"][0]["text"] == "Use app/api.py"
    assert runtime._turn_acknowledged_through == 1
    asyncio.run(runtime._handle((asyncio.run(client.commands()))[-1]))
    assert client.events[-1]["acknowledge_through"] == 4


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 422])
def test_definite_conversation_errors_do_not_retry(status, monkeypatch):
    from types import SimpleNamespace
    from fi.alk.harness.hosted_conversation import ConversationTransportError

    class Transport:
        calls = 0

        def request(self, *args, **kwargs):
            self.calls += 1
            return SimpleNamespace(status_code=status, body={})

    async def unexpected_sleep(_delay):
        pytest.fail("A definite error must not be retried")

    monkeypatch.setattr(asyncio, "sleep", unexpected_sleep)
    transport = Transport()
    client = ConversationClient(_capabilities(), transport=transport)
    with pytest.raises(ConversationTransportError, match=f"HTTP {status}"):
        asyncio.run(client._request("GET", client.capabilities.endpoints.commands))
    assert transport.calls == 1


def test_unavailable_requests_stop_before_platform_stale_window(monkeypatch):
    from types import SimpleNamespace
    from fi.alk.harness.hosted_conversation import ConversationTransportError

    clock = [0.0]
    timeouts = []

    class Transport:
        def request(self, *args, timeout, **kwargs):
            timeouts.append(timeout)
            clock[0] += timeout
            return SimpleNamespace(status_code=503, body={})

    async def exercise():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, "time", lambda: clock[0])

        async def sleep(delay):
            clock[0] += delay

        monkeypatch.setattr(asyncio, "sleep", sleep)
        client = ConversationClient(_capabilities(), transport=Transport())
        with pytest.raises(ConversationTransportError, match="remained unavailable"):
            await client._request("GET", client.capabilities.endpoints.commands)

    asyncio.run(exercise())
    assert 35 < clock[0] <= 60
    assert timeouts[-1] < timeouts[0]


def test_carrying_on_a_build_restates_how_many_scenarios_it_was_asked_for(tmp_path):
    from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime

    runtime = object.__new__(HostedChatRuntime)
    runtime.workspace = tmp_path
    runtime.job = {"scenario_count": 60}
    runtime.client = type(
        "Client", (), {"turn_context": {"capabilities": {"control_only": True}}, "command_watermark": 3}
    )()
    runtime._last_progress, runtime._idle_turns, runtime._opened_with = None, 0, None
    runtime._what_exists = lambda: (True, False)
    runtime._after_saved = lambda: None
    handled = []

    async def handle(command):
        handled.append(command["payload"]["content"])

    runtime._handle = handle

    assert asyncio.run(runtime._carry_on())
    assert "with 60 scenarios" in handled[0]
    assert "call finish_build" in handled[0]


def test_open_for_building_passes_subfolder(tmp_path, monkeypatch):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "schema_version: 1\nharness_chat:\n  enabled: true\n", encoding="utf-8"
    )
    source = tmp_path / "source"
    (source / "agents" / "a").mkdir(parents=True)
    (source / "agents" / "a" / "agent.py").write_text("x = 1\n")
    (source / "agents" / "b").mkdir()
    (source / "agents" / "b" / "agent.py").write_text("x = 2\n")
    opened: dict[str, Any] = {}
    monkeypatch.setattr(
        "fi.alk.harness.builder.open_for_building",
        lambda contract, **kwargs: opened.update(kwargs),
    )
    monkeypatch.setattr(HostedChatRuntime, "_builder_platform_server", lambda self: None)
    monkeypatch.setattr(HostedChatRuntime, "_hosted_prompt", lambda self: "")
    runtime = HostedChatRuntime(
        client=SimpleNamespace(turn_context={}),  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=source,
        # A job that doesn't validate, as when the platform sends a field this build doesn't know.
        job={
            "metadata": {"agent_name": "demo"},
            "scenario_count": 1,
            "source": {"kind": "github", "subfolder": "agents/a"},
        },
    )

    runtime._open_for_building(None)

    assert opened["source"].subfolder == "agents/a"
    briefing = opened["source"].briefing()
    assert "agents/a/agent.py" in briefing and "agents/b/agent.py" not in briefing


def test_nudge_world_checks_cite_only_the_recorded_folder(tmp_path):
    from fi.alk.harness.backends import SessionSpec, tool, tool_server
    from fi.alk.harness.world.tools import WORLD_SERVER

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "schema_version: 1\nharness_chat:\n  enabled: true\n", encoding="utf-8"
    )
    source = tmp_path / "source"
    for name in ("a", "b"):
        (source / "agents" / name).mkdir(parents=True)
        (source / "agents" / name / "seed.sql").write_text(
            "INSERT INTO menu VALUES ('fries');\n"
        )
    workspace = tmp_path / "work"
    workspace.mkdir()
    (workspace / "contract.json").write_text(
        json.dumps(
            {"agent": "demo", "runtime": {"source_subfolder": "agents/a", "workdir": "agents/a"}}
        ),
        encoding="utf-8",
    )
    runtime = HostedChatRuntime(
        client=SimpleNamespace(turn_context={}),  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=workspace,
        source=source,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    runtime._nudge = {"nudge_id": "n1", "stage": "environment"}

    @tool("add_world_check", "Add one check.", {})
    async def add_world_check(args: dict) -> dict:
        return {"content": []}

    configured: list[SessionSpec] = []
    stage = SimpleNamespace(
        spec=SessionSpec(
            system_prompt="",
            servers={WORLD_SERVER: tool_server(name=WORLD_SERVER, tools=[add_world_check])},
        ),
        kept=None,
        configure=configured.append,
    )

    runtime._nudge_tools(stage, None)  # type: ignore[arg-type]

    check = next(one for one in configured[0].servers[WORLD_SERVER].tools)

    def cite(path: str) -> dict:
        return asyncio.run(
            check.handler(
                {
                    "name": "menu",
                    "code": "def check(world): return None",
                    "source_path": path,
                    "source_quote": "INSERT INTO menu VALUES ('fries');",
                }
            )
        )

    assert cite("agents/b/seed.sql").get("is_error")
    assert not cite("agents/a/seed.sql").get("is_error")


def test_a_file_that_shrinks_while_the_workspace_is_packed_does_not_fail_the_pack(
    tmp_path, monkeypatch
):
    import io
    import tarfile

    from fi.alk.harness.hosted_conversation import _workspace_archive

    (tmp_path / "scenarios").mkdir()
    scenario = tmp_path / "scenarios" / "one.json"
    scenario.write_text("x" * 4096)
    (tmp_path / "contract.json").write_text("{}")
    measured = tarfile.TarFile.gettarinfo
    shrunk = []

    def rewritten_after_its_size_is_read(self, name=None, *args, **kwargs):
        info = measured(self, name, *args, **kwargs)
        if name is not None and Path(name).name == "one.json" and not shrunk:
            scenario.write_text("{}")  # a scenario writer rewrites it mid-pack
            shrunk.append(name)
        return info

    monkeypatch.setattr(tarfile.TarFile, "gettarinfo", rewritten_after_its_size_is_read)

    body = _workspace_archive(tmp_path)

    assert shrunk
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        assert archive.extractfile("scenarios/one.json").read() == b"{}"
        assert archive.extractfile("contract.json").read() == b"{}"


def test_a_turn_cut_off_while_its_session_database_was_left_out_of_the_checkpoint_opens_fresh(
    tmp_path, monkeypatch
):
    """The platform still names the cut-off invocation, but the database holding it did not come
    back, so resuming it would fail the turn; the turn is answered again instead."""
    monkeypatch.setenv("ALK_HARNESS", "vertex-gemini")
    monkeypatch.setenv("ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER", "1")
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "schema_version: 1\nharness_chat:\n  enabled: true\n  stages:\n    reception:\n      enabled: true\n",
        encoding="utf-8",
    )
    (tmp_path / ".futureagi-conversation.json").write_text(json.dumps({"message-3": "started"}))

    class Client:
        def __init__(self):
            self.capabilities = _capabilities()
            self.turn_context = {**self.capabilities.turn_context, "active_invocation_id": "inv-3"}
            self.command_watermark = 0
            self.events = []

        async def emit(self, kind, **kwargs):
            self.events.append({"kind": kind, **kwargs})
            return self.events[-1]

        async def checkpoint(self, workspace):
            return {"digest": "sha256:" + "0" * 64, "size": 0}

    class Conversation:
        stage = None
        stage_name = "run"

        def __init__(self):
            self.messages, self.resumed = [], []

        async def say(self, message, receive):
            self.messages.append(message)

        async def resume(self, invocation_id, receive):
            self.resumed.append(invocation_id)

        async def close(self):
            return None

    runtime = HostedChatRuntime(
        client=Client(),  # type: ignore[arg-type]
        policy=load_chat_policy(policy_path),
        workspace=tmp_path,
        source=tmp_path,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    conversation = Conversation()
    runtime.conversation = conversation  # type: ignore[assignment]

    asyncio.run(
        runtime._handle(
            {"sequence": 3, "message_id": "message-3", "kind": "user_message", "payload": {"content": "third"}}
        )
    )

    assert conversation.resumed == []
    assert len(conversation.messages) == 1 and conversation.messages[0].endswith("third")


# -- The transcript mirror: what leaves the sandbox is cleaned, and a resumed session still works ---

import os  # noqa: E402
import subprocess  # noqa: E402

from transcript_fixtures import (  # noqa: E402
    PASSWORD,
    PLANTS,
    QUERY_KEY,
    SESSION_ID,
    StubAPI,
    UUIDS,
    build as build_transcript,
    api_thinking_blocks,
    pairing_problems,
    pointer_problems,
    thinking_blocks,
)

from fi.alk.harness.hosted_conversation import PlatformSessionStore  # noqa: E402


class StoringClient:
    """The platform's session store, in memory."""

    def __init__(self) -> None:
        self.posts: list[list[dict]] = []
        self.stored: list[dict] = []

    async def append_session(self, key, entries):
        self.posts.append(list(entries))
        self.stored.extend(entries)

    async def load_session(self, key):
        return {"entries": list(self.stored)}


def _mirror(raw: list[dict], *, split: int | None = None) -> list[dict]:
    """`raw` appended through one store, in one batch or two, and loaded back."""
    client = StoringClient()
    store = PlatformSessionStore(client)  # type: ignore[arg-type]
    cut = len(raw) if split is None else split
    for batch in (raw[:cut], raw[cut:]):
        asyncio.run(store.append({"project_key": "p", "session_id": SESSION_ID}, batch))
    return asyncio.run(store.load({"project_key": "p", "session_id": SESSION_ID})) or []


_CLI_VERSIONS = [(2, 1, 276), (2, 1, 318)]


@pytest.mark.parametrize("cli_version", _CLI_VERSIONS, ids=lambda v: ".".join(map(str, v)))
def test_a_mirrored_transcript_points_only_at_lines_it_holds(cli_version):
    raw = build_transcript("/work", cli_version)
    before = json.dumps(raw, sort_keys=True)

    for split in range(len(raw) + 1):
        loaded = _mirror(raw, split=split)

        assert pointer_problems(loaded) == [], f"split at {split}"
        assert thinking_blocks(loaded) == 0
        # Lines the CLI writes without a uuid for replay are not mirrored; a summary of a leaf is.
        assert not [one for one in loaded if str(one.get("type", "")).startswith("api-request")]
        assert [one for one in loaded if one.get("type") == "summary"] == [
            {"type": "summary", "summary": "Ran two commands", "leafUuid": UUIDS["a6"]}
        ]
        # The three thinking-only lines are gone and every other line is kept, in order.
        assert [one["uuid"] for one in loaded if "uuid" in one] == [
            UUIDS[name] for name in ["u1", "a2", "u2", "a4", "u3", "b", "s1", "a6"]
        ]
    assert json.dumps(raw, sort_keys=True) == before, "append changed the lines it was given"


def test_a_mirrored_transcript_closes_each_gap_a_dropped_line_leaves():
    loaded = {one.get("uuid"): one for one in _mirror(build_transcript("/work"), split=6)}

    assert loaded[UUIDS["a2"]]["parentUuid"] == UUIDS["u1"]
    assert loaded[UUIDS["a4"]]["parentUuid"] == UUIDS["u2"]
    assert loaded[UUIDS["a6"]]["parentUuid"] == UUIDS["s1"]
    boundary = loaded[UUIDS["b"]]["compactMetadata"]
    # A dropped line comes out of the kept tail rather than being replaced by its parent, which
    # would pull a line from before the boundary into it.
    assert boundary["preservedMessages"]["uuids"] == [UUIDS["a4"], UUIDS["u3"]]
    assert boundary["preservedMessages"]["allUuids"] == [UUIDS["a4"], UUIDS["u3"]]
    assert boundary["preservedSegment"]["headUuid"] == UUIDS["a4"]
    assert boundary["preservedSegment"]["tailUuid"] == UUIDS["u3"]
    assert loaded[UUIDS["b"]]["logicalParentUuid"] == UUIDS["u3"]


def test_a_mirrored_transcript_keeps_counts_and_loses_credentials():
    loaded = {one.get("uuid"): one for one in _mirror(build_transcript("/work", (2, 1, 318)), split=6)}

    assert not [plant for plant in PLANTS if plant in json.dumps(list(loaded.values()))]
    result = loaded[UUIDS["u3"]]["toolUseResult"]
    assert result["totalTokens"] == 51234 and result["totalToolUseCount"] == 3
    assert result["usage"] == {"input_tokens": 12, "output_tokens": 900}
    assert result["db_password"] == "[redacted]"
    use = loaded[UUIDS["a4"]]["message"]["content"][0]
    assert use["input"]["password"] == "[redacted]"
    assert use["input"]["description"] == "second"
    assert use["id"] == "toolu_01T2" and use["name"] == "Bash"
    assert loaded[UUIDS["u3"]]["message"]["content"][0]["tool_use_id"] == "toolu_01T2"
    # Ids, hashes and request references are not text to clean.
    assert loaded[UUIDS["a4"]]["requestId"] == "req_011B"
    assert loaded[UUIDS["a4"]]["requestRef"] == "req_B"


def _nested(depth: int) -> dict:
    value: dict = {"leaf": "x"}
    for _ in range(depth):
        value = {"d": value}
    return value


@pytest.mark.parametrize("depth", [600, 5000])
def test_a_deeply_nested_line_keeps_its_place_and_does_not_drop_the_batch(depth):
    raw = build_transcript("/work", (2, 1, 318))
    by_uuid = {one.get("uuid"): one for one in raw}
    by_uuid[UUIDS["a4"]]["message"]["content"][0]["input"] = _nested(depth)
    by_uuid[UUIDS["u3"]]["toolUseResult"] = _nested(depth)

    for split in (0, 6, len(raw)):
        loaded = _mirror(raw, split=split)

        # The batch is not dropped, and every pointer still resolves to a stored line.
        assert pointer_problems(loaded) == [], f"split at {split}"
        kept = {one.get("uuid"): one for one in loaded}
        assert UUIDS["a4"] in kept and UUIDS["u3"] in kept
        # Nested as deep as the cleaner can read it comes through cleaned; deeper is replaced.
        expected = _nested(depth) if depth < 1000 else "[redacted]"
        assert kept[UUIDS["a4"]]["message"]["content"][0]["input"] == expected
        assert kept[UUIDS["u3"]]["toolUseResult"] == expected
        assert kept[UUIDS["a4"]]["message"]["content"][0]["id"] == "toolu_01T2"
    assert by_uuid[UUIDS["a4"]]["message"]["content"][0]["input"] == _nested(depth)


def test_a_batch_with_nothing_left_to_mirror_is_not_posted():
    only_replay = [
        {"type": "api-request", "id": "r", "sessionId": SESSION_ID},
        {"type": "api-request-blob", "sessionId": SESSION_ID, "hash": "h", "message": {"role": "assistant", "content": []}},
    ]
    client = StoringClient()
    store = PlatformSessionStore(client)  # type: ignore[arg-type]

    asyncio.run(store.append({"project_key": "p", "session_id": SESSION_ID}, only_replay))

    assert client.posts == []


def test_an_empty_store_materializes_nothing_so_the_session_opens_fresh():
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.session_resume import materialize_resume_session

    store = PlatformSessionStore(StoringClient())  # type: ignore[arg-type]
    options = ClaudeAgentOptions(cwd="/work", resume=SESSION_ID, session_store=store)

    # With no entries nothing is written, the CLI is handed a session it doesn't have and refuses
    # it, and the builder opens a fresh one (see
    # test_a_stored_session_the_provider_will_not_resume_opens_fresh in test_builder_chat.py).
    assert asyncio.run(store.load({"project_key": "p", "session_id": SESSION_ID})) == []
    assert asyncio.run(materialize_resume_session(options)) is None


def _resume_cli() -> tuple[str, tuple[int, int, int]]:
    """The CLI that reads the stored transcript, and its version."""
    import claude_agent_sdk

    required = os.environ.get("ALK_RESUME_CLI_REQUIRED") == "1"
    path = os.environ.get("ALK_RESUME_CLI_PATH") or str(
        Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    )

    def unavailable(reason: str):
        # CI sets ALK_RESUME_CLI_REQUIRED, so a missing CLI there is a failure, not a skip.
        (pytest.fail if required else pytest.skip)(reason)

    if not (Path(path).is_file() and os.access(path, os.X_OK)):
        unavailable(f"no CLI to resume with at {path}")
    try:
        said = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=60).stdout
        version = tuple(int(part) for part in said.split()[0].split("."))
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        unavailable(f"{path} did not say its version")
    return path, version  # type: ignore[return-value]


def _resume_through_the_cli(cli: str, stub: StubAPI, store, cwd: Path, scratch: Path):
    """The SDK writes `store` out as it does on resume, and the CLI resumes it against the stub."""
    from claude_agent_sdk import ClaudeAgentOptions
    from claude_agent_sdk._internal.session_resume import materialize_resume_session

    home = scratch / "home"
    temp = scratch / "tmp"
    home.mkdir()
    temp.mkdir()
    options = ClaudeAgentOptions(
        cwd=str(cwd), resume=SESSION_ID, session_store=store, env={"CLAUDE_CONFIG_DIR": str(home / ".claude-source")}
    )
    materialized = asyncio.run(materialize_resume_session(options))
    assert materialized is not None
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(temp),
        "CLAUDE_CONFIG_DIR": str(materialized.config_dir),
        "ANTHROPIC_BASE_URL": stub.url,
        "ANTHROPIC_API_KEY": "sk-ant-test-dummy",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1",
        # Anything that is not the stub has nowhere to go.
        "HTTP_PROXY": "http://127.0.0.1:9",
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "NO_PROXY": "127.0.0.1,localhost",
    }
    try:
        done = subprocess.run(
            [cli, "-p", "RESUME_PROBE continue", "--resume", SESSION_ID, "--output-format", "stream-json",
             "--verbose", "--model", "claude-sonnet-4-5", "--max-turns", "1"],
            cwd=str(cwd), env=env, capture_output=True, text=True, timeout=240, stdin=subprocess.DEVNULL,
        )
    finally:
        asyncio.run(materialized.cleanup())
    return done


def test_a_real_cli_resumes_the_cleaned_transcript_and_sends_it_whole(tmp_path):
    cli, version = _resume_cli()
    cwd = tmp_path / "work"
    cwd.mkdir()
    raw = build_transcript(str(cwd.resolve()), version)
    stub = StubAPI()
    try:
        cleaned_client = StoringClient()
        mirror = PlatformSessionStore(cleaned_client)  # type: ignore[arg-type]
        for batch in (raw[:6], raw[6:]):
            asyncio.run(mirror.append({"project_key": "p", "session_id": SESSION_ID}, batch))
        raw_client = StoringClient()
        raw_client.stored = list(raw)

        class _RawReader(PlatformSessionStore):
            """Reads the stored lines as they are: load cleans, and the control must not."""

            async def load(self, key):
                return list((await self._client.load_session(dict(key)))["entries"])

        runs = {}
        for name, store in (
            ("raw", _RawReader(raw_client)),  # type: ignore[arg-type]
            ("cleaned", PlatformSessionStore(cleaned_client)),  # type: ignore[arg-type]
        ):
            scratch = tmp_path / name
            scratch.mkdir()
            done = _resume_through_the_cli(cli, stub, store, cwd.resolve(), scratch)
            runs[name] = (done, stub.probe_request("RESUME_PROBE"), list(stub.requests))
            stub.requests.clear()
    finally:
        stub.close()

    # The control: the same CLI, handed the raw store, does send what the cleaning removes, so
    # the checks below can fail.
    assert runs["raw"][1] is not None, runs["raw"][0].stderr[-800:]
    raw_body = runs["raw"][1]["body"]
    assert api_thinking_blocks(raw_body["messages"]) > 0
    # (The URL password sits in a line from before the compaction boundary, which the summary replaces.)
    assert all(plant in json.dumps(raw_body) for plant in (PASSWORD, QUERY_KEY))

    done, request, requests = runs["cleaned"]
    assert done.returncode == 0, done.stderr[-800:]
    results = [json.loads(one) for one in done.stdout.splitlines() if one.startswith("{") and '"type":"result"' in one.replace(" ", "")]
    assert results and not results[-1].get("is_error") and results[-1].get("result") == "STUB_OK"
    assert request is not None
    messages = request["body"]["messages"]
    sent = json.dumps(request["body"])
    assert api_thinking_blocks(messages) == 0
    assert [plant for plant in PLANTS if plant in sent] == []
    assert pairing_problems(messages) == []
    # The whole cleaned history reached the API: the summary, the kept tail's tool round, the reply.
    assert "Summary: ran echo one." in sent
    assert "toolu_01T2" in sent and "[redacted]" in sent
    assert "Both commands ran." in sent


# -- The workspace checkpoint leaves out what is raw, and cleans what it must carry ------------------


def _members(archive: bytes) -> dict[str, bytes]:
    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        return {one.name: tar.extractfile(one).read() for one in tar.getmembers() if one.isfile()}


def test_the_checkpoint_leaves_out_the_cli_state_and_the_files_a_database_writes_mid_transaction(tmp_path):
    from fi.alk.harness.hosted_conversation import _workspace_archive

    for name in (
        ".claude/projects/p/s.jsonl",
        ".claude-coordinator/projects/p/s.jsonl",
        ".futureagi-adk.db-journal",
        ".futureagi-adk.db-wal",
        ".futureagi-adk.db-shm",
        "contract.json",
        "scenarios/one/scenario.json",
        "notes/.claude-notes.md",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("DB_PASSWORD=hunter2" if ".claude" in name else "{}")

    assert sorted(_members(_workspace_archive(tmp_path))) == [
        "contract.json",
        "notes/.claude-notes.md",
        "scenarios/one/scenario.json",
    ]


def test_the_checkpoint_carries_the_pending_question_cleaned_and_leaves_the_file_as_written(tmp_path):
    from fi.alk.harness.hosted_conversation import _workspace_archive

    pending = {
        "message_id": "m-1",
        "question": "Keep postgres://svc:Zq9fakePw41@db.internal/app?",
        "options": ["yes (DB_PASSWORD=hunter2)", "no"],
    }
    path = tmp_path / ".futureagi-pending-question.json"
    path.write_text(json.dumps(pending, sort_keys=True, separators=(",", ":")))
    written = path.read_bytes()

    packed = _members(_workspace_archive(tmp_path))[".futureagi-pending-question.json"]

    assert b"Zq9fakePw41" not in packed and b"hunter2" not in packed
    kept = json.loads(packed)
    assert kept["message_id"] == "m-1" and kept["options"][1] == "no"
    assert path.read_bytes() == written


_ADK_KEY = "sk-ant-api03-" + "A" * 90
_ADK_PLANTS = {
    "shapeless value under a credential name": b"hunter2",
    "url password": b"Zq9fakePw41",
    "key shape": _ADK_KEY.encode(),
    "named value in a summary": b"Blue-Sky-42",
    "password in a text part": b"Pw-In-Text-77",
    "a deleted row": b"DELETED-ROW-SECRET-xyz",
}


def _adk_database(path: Path) -> None:
    """A session database as ADK writes it: a tool call and result, a text part, a compaction
    summary, and a row that was deleted (its page is free, but still holds the text)."""
    import sqlite3

    from google.adk.events import Event
    from google.adk.events.event_actions import EventActions, EventCompaction
    from google.adk.sessions import DatabaseSessionService
    from google.genai import types

    async def write():
        service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{path.as_posix()}")
        session = await service.create_session(app_name="app", user_id="u", session_id="conv-builder")
        call = types.Part(
            function_call=types.FunctionCall(
                name="query_db",
                args={"dsn": "postgres://svc:Zq9fakePw41@db.internal/app", "password": "hunter2", "limit": 5},
                id="c1",
            ),
            thought_signature=b"FAKESIGFAKESIG",
        )
        await service.append_event(session, Event(author="model", invocation_id="i1", content=types.Content(role="model", parts=[call])))
        reply = types.Part(
            function_response=types.FunctionResponse(
                name="query_db", id="c1", response={"result": {"db_password": "hunter2", "rows": ("row " * 300) + _ADK_KEY}}
            )
        )
        await service.append_event(session, Event(author="user", invocation_id="i1", content=types.Content(role="user", parts=[reply])))
        await service.append_event(
            session,
            Event(author="model", invocation_id="i1", content=types.Content(role="model", parts=[types.Part(text="Connected to postgres://svc:Pw-In-Text-77@db.internal/app")])),
        )
        compaction = EventActions(
            compaction=EventCompaction(
                start_timestamp=1.0,
                end_timestamp=2.0,
                compacted_content=types.Content(role="model", parts=[types.Part(text="Summary: new_password: Blue-Sky-42 was set")]),
            )
        )
        await service.append_event(session, Event(author="user", invocation_id="i2", actions=compaction))
        await service.append_event(
            session, Event(author="model", invocation_id="i3", content=types.Content(role="model", parts=[types.Part(text="DELETED-ROW-SECRET-xyz")]))
        )
        if hasattr(service, "close"):
            await service.close()

    asyncio.run(write())
    with sqlite3.connect(path) as database:
        # Some SQLite builds (Debian and Ubuntu's) zero a deleted row's page by default; turn that
        # off so the freed page keeps the text everywhere, which is what the copy has to drop.
        database.execute("PRAGMA secure_delete = OFF")
        database.execute("DELETE FROM events WHERE invocation_id = 'i3'")


def test_the_checkpoint_carries_the_session_database_with_no_credential_in_any_byte_of_it(tmp_path):
    import gzip
    import sqlite3

    from google.adk.sessions import DatabaseSessionService

    from fi.alk.harness.hosted_conversation import _workspace_archive

    database = tmp_path / ".futureagi-adk.db"
    _adk_database(database)
    live = database.read_bytes()
    assert all(plant in live for plant in _ADK_PLANTS.values()), "the plants are in the live file"
    with sqlite3.connect(database) as one:
        keys = one.execute("select id, app_name, user_id, session_id, invocation_id, timestamp from events order by rowid").fetchall()
        sessions = one.execute("select app_name, user_id, id from sessions").fetchall()

    before = sorted(tmp_path.rglob("*"))

    archive = _workspace_archive(tmp_path)

    # No copy of the database, raw or cleaned, is left in the workspace.
    assert sorted(tmp_path.rglob("*")) == before
    # Every byte of the archive, compressed or not, and every member of it.
    for what, plant in _ADK_PLANTS.items():
        assert plant not in gzip.decompress(archive), what
    assert database.read_bytes() == live, "the live database is untouched"
    members = _members(archive)
    assert sorted(members) == [".futureagi-adk.db"]
    packed = tmp_path / "packed" / ".futureagi-adk.db"
    packed.parent.mkdir()
    packed.write_bytes(members[".futureagi-adk.db"])
    with sqlite3.connect(packed) as one:
        assert one.execute("select id, app_name, user_id, session_id, invocation_id, timestamp from events order by rowid").fetchall() == keys
        assert one.execute("select app_name, user_id, id from sessions").fetchall() == sessions

    async def reload():
        service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{packed.as_posix()}")
        return await service.get_session(app_name="app", user_id="u", session_id="conv-builder")

    session = asyncio.run(reload())
    call = session.events[0].content.parts[0]
    assert call.function_call.args["limit"] == 5 and call.function_call.args["password"] == "[redacted]"
    assert call.function_call.name == "query_db" and call.thought_signature is None
    answer = session.events[1].content.parts[0].function_response
    assert answer.response["result"]["db_password"] == "[redacted]"
    assert len(session.events) == 4
    assert "Summary:" in session.events[3].actions.compaction.compacted_content.parts[0].text


def test_a_session_database_that_cannot_be_cleaned_is_left_out_loudly(tmp_path, caplog):
    import sqlite3

    from fi.alk.harness.hosted_conversation import _workspace_archive

    database = tmp_path / ".futureagi-adk.db"
    _adk_database(database)
    with sqlite3.connect(database) as one:
        one.execute("UPDATE events SET event_data = 'not json' WHERE rowid = 1")
    (tmp_path / "contract.json").write_text("{}")

    with caplog.at_level("WARNING"):
        archive = _workspace_archive(tmp_path)

    assert sorted(_members(archive)) == ["contract.json"]
    assert any("session database" in one.getMessage() for one in caplog.records)


def test_a_session_database_with_a_table_the_cleaner_does_not_know_is_left_out_loudly(
    tmp_path, caplog
):
    import sqlite3

    from fi.alk.harness.hosted_conversation import _workspace_archive

    database = tmp_path / ".futureagi-adk.db"
    _adk_database(database)
    with sqlite3.connect(database) as one:
        one.execute("CREATE TABLE extra_cache (v TEXT)")
        one.execute("INSERT INTO extra_cache VALUES ('token ghp_' || 'aB3dE5gH7jK9mN1pQ3sT5vX7zA9cE1gI3kM5')")
    (tmp_path / "contract.json").write_text("{}")

    with caplog.at_level("WARNING"):
        archive = _workspace_archive(tmp_path)

    assert sorted(_members(archive)) == ["contract.json"]
    assert any("session database" in one.getMessage() for one in caplog.records)


def test_a_session_database_with_exactly_the_known_tables_is_still_carried(tmp_path):
    import sqlite3

    from fi.alk.harness.hosted_conversation import _workspace_archive

    database = tmp_path / ".futureagi-adk.db"
    _adk_database(database)
    with sqlite3.connect(database) as one:
        tables = {row[0] for row in one.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert tables == {"adk_internal_metadata", "app_states", "events", "sessions", "user_states"}

    assert ".futureagi-adk.db" in _members(_workspace_archive(tmp_path))


def test_the_checkpoint_leaves_out_the_temporary_sibling_of_a_file_it_cleans(tmp_path):
    from fi.alk.harness.hosted_conversation import _workspace_archive

    for name in (".futureagi-pending-question.tmp", ".futureagi-adk.tmp", ".futureagi-adk.db.tmp"):
        (tmp_path / name).write_text("DB_PASSWORD=hunter2")
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "keep.tmp").write_text("{}")
    (tmp_path / "contract.json").write_text("{}")

    members = _members(_workspace_archive(tmp_path))

    assert b"hunter2" not in b"".join(members.values())
    assert sorted(members) == ["contract.json", "scenarios/keep.tmp"]


def test_the_checkpoint_cleans_the_state_the_session_database_keeps(tmp_path):
    import gzip
    import sqlite3

    from google.adk.events import Event
    from google.adk.events.event_actions import EventActions
    from google.adk.sessions import DatabaseSessionService
    from google.genai import types

    from fi.alk.harness.hosted_conversation import _workspace_archive

    database = tmp_path / ".futureagi-adk.db"

    async def write():
        service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{database.as_posix()}")
        session = await service.create_session(app_name="app", user_id="u", session_id="conv-builder")
        delta = EventActions(state_delta={"db": "postgres://svc:StateDeltaPw91@db.internal/app", "turns": 3})
        await service.append_event(
            session,
            Event(author="model", invocation_id="i1", actions=delta, content=types.Content(role="model", parts=[types.Part(text="ok")])),
        )
        if hasattr(service, "close"):
            await service.close()

    asyncio.run(write())
    with sqlite3.connect(database) as one:
        one.execute("INSERT OR REPLACE INTO app_states (app_name, state, update_time) VALUES ('app', ?, 0)", (json.dumps({"k": "DB_PASSWORD=AppStatePw55"}),))
        one.execute("INSERT OR REPLACE INTO user_states (app_name, user_id, state, update_time) VALUES ('app', 'u', ?, 0)", (json.dumps({"k": "DB_PASSWORD=UserStatePw66"}),))
        one.execute("UPDATE sessions SET state = ? ", (json.dumps({"db": "postgres://svc:StateDeltaPw91@db.internal/app", "own": "DB_PASSWORD=SessionStatePw77", "turns": 3}),))
        keys = one.execute("select app_name, user_id, id from sessions").fetchall(), one.execute("select app_name from app_states").fetchall(), one.execute("select app_name, user_id from user_states").fetchall()
    plants = [b"StateDeltaPw91", b"AppStatePw55", b"UserStatePw66", b"SessionStatePw77"]
    assert all(plant in database.read_bytes() for plant in plants)

    archive = _workspace_archive(tmp_path)

    for plant in plants:
        assert plant not in gzip.decompress(archive), plant
    packed = tmp_path / "packed.db"
    packed.write_bytes(_members(archive)[".futureagi-adk.db"])
    with sqlite3.connect(packed) as one:
        assert (one.execute("select app_name, user_id, id from sessions").fetchall(), one.execute("select app_name from app_states").fetchall(), one.execute("select app_name, user_id from user_states").fetchall()) == keys

    async def reload():
        service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{packed.as_posix()}")
        return await service.get_session(app_name="app", user_id="u", session_id="conv-builder")

    session = asyncio.run(reload())
    assert session.state["turns"] == 3 and "StateDeltaPw91" not in json.dumps(session.state)


def test_the_checkpoint_key_masks_the_state_the_session_database_keeps(tmp_path):
    import gzip
    import sqlite3

    from google.adk.events import Event
    from google.adk.events.event_actions import EventActions
    from google.adk.sessions import DatabaseSessionService
    from google.genai import types

    from fi.alk.harness.hosted_conversation import _workspace_archive

    database = tmp_path / ".futureagi-adk.db"

    async def write():
        service = DatabaseSessionService(db_url=f"sqlite+aiosqlite:///{database.as_posix()}")
        session = await service.create_session(app_name="app", user_id="u", session_id="conv-builder")
        delta = EventActions(
            state_delta={"api_key": "shapelessDeltaOne", "city": "Paris", "turns": 3}
        )
        await service.append_event(
            session,
            Event(author="model", invocation_id="i1", actions=delta, content=types.Content(role="model", parts=[types.Part(text="ok")])),
        )
        if hasattr(service, "close"):
            await service.close()

    asyncio.run(write())
    with sqlite3.connect(database) as one:
        one.execute("INSERT OR REPLACE INTO app_states (app_name, state, update_time) VALUES ('app', ?, 0)", (json.dumps({"api_key": "shapelessAppTwo", "city": "Paris"}),))
        one.execute("INSERT OR REPLACE INTO user_states (app_name, user_id, state, update_time) VALUES ('app', 'u', ?, 0)", (json.dumps({"api_key": "shapelessUserThree", "city": "Paris"}),))
        one.execute("UPDATE sessions SET state = ? ", (json.dumps({"api_key": "shapelessSessionFour", "city": "Paris", "turns": 3}),))
    plants = [b"shapelessDeltaOne", b"shapelessAppTwo", b"shapelessUserThree", b"shapelessSessionFour"]
    assert all(plant in database.read_bytes() for plant in plants)

    archive = _workspace_archive(tmp_path)

    for plant in plants:
        assert plant not in gzip.decompress(archive), plant
    packed = tmp_path / "packed.db"
    packed.write_bytes(_members(archive)[".futureagi-adk.db"])
    with sqlite3.connect(packed) as one:
        states = [
            json.loads(row[0])
            for table in ("sessions", "app_states", "user_states")
            for row in one.execute(f"select state from {table}")
        ]
        event = json.loads(one.execute("select event_data from events").fetchone()[0])
    assert states and all(state["api_key"] == "[redacted]" for state in states)
    assert all(state["city"] == "Paris" for state in states)
    assert [state["turns"] for state in states if "turns" in state] == [3]
    delta = event["actions"].get("state_delta") or event["actions"].get("stateDelta")
    assert delta == {"api_key": "[redacted]", "city": "Paris", "turns": 3}


def test_a_line_stored_raw_before_the_mirror_cleaned_comes_back_cleaned_on_load():
    raw = build_transcript("/work", (2, 1, 318))
    client = StoringClient()
    client.stored.extend(json.loads(json.dumps(raw)))
    assert any(plant in json.dumps(client.stored) for plant in PLANTS)
    assert thinking_blocks(client.stored) > 0
    store = PlatformSessionStore(client)  # type: ignore[arg-type]

    loaded = asyncio.run(store.load({"project_key": "p", "session_id": SESSION_ID})) or []

    assert not [plant for plant in PLANTS if plant in json.dumps(loaded)]
    assert thinking_blocks(loaded) == 0
    assert pointer_problems(loaded) == []
    assert [one["uuid"] for one in loaded if "uuid" in one] == [
        UUIDS[name] for name in ["u1", "a2", "u2", "a4", "u3", "b", "s1", "a6"]
    ]
    # The stored lines are left as they were, and a line already cleaned comes back as it is.
    assert any(plant in json.dumps(client.stored) for plant in PLANTS)
    second = StoringClient()
    second.stored.extend(loaded)
    again = asyncio.run(
        PlatformSessionStore(second).load({"project_key": "p", "session_id": SESSION_ID})  # type: ignore[arg-type]
    )
    assert again == loaded


def test_a_workspace_holding_the_actor_list_and_the_simulator_prompt_packs_no_credential(tmp_path):
    from types import SimpleNamespace

    from fi.alk.harness.hosted_conversation import _workspace_archive
    from fi.alk.harness.world.tools import world_actor_tools, world_tools
    from fi.alk.harness.contract import AgentContract

    key = "AKIA" + "IOSFODNN7EXAMPLE"
    add, _drop, _note = world_actor_tools(tmp_path, SimpleNamespace(modality="chat", model_dump=lambda: {}))
    contract = AgentContract(agent="support", modality="chat", tools=[{"name": "lookup"}])
    server, _world = world_tools(contract, tmp_path)
    write = {one.name: one.handler for one in server.tools}["write_simulator_prompt"]
    body = "You are a caller with persona {{ persona }}. Follow {{ instruction }} and ask natural follow-up questions."

    # What the builder sends first holds the key; each writer refuses it, then takes the placeholder.
    assert asyncio.run(
        add.handler({"id": "it_desk", "name": "IT desk", "prompt": f"You hold the shared key {key}", "identities": {"aws": key}})
    ).get("is_error")
    assert asyncio.run(write({"prompt": f"{body} The caller reads out {key}"})).get("is_error")
    assert not asyncio.run(add.handler({"id": "it_desk", "name": "IT desk", "prompt": "You hold the shared key $AWS_KEY_ID"})).get("is_error")
    assert not asyncio.run(write({"prompt": body + " The caller reads out $AWS_KEY_ID"})).get("is_error")

    members = _members(_workspace_archive(tmp_path))
    assert "actors.json" in members and "simulator_prompt.md" in members
    assert not [name for name, content in members.items() if key.encode() in content]


def test_an_unexpected_failure_cleaning_the_session_database_is_logged_with_its_traceback(
    tmp_path, caplog, monkeypatch
):
    from fi.alk.harness import hosted_conversation
    from fi.alk.harness.hosted_conversation import _workspace_archive

    _adk_database(tmp_path / ".futureagi-adk.db")
    (tmp_path / "contract.json").write_text("{}")

    def new_shape(*_args, **_kwargs):
        raise TypeError("an event shape the cleaner does not know")

    monkeypatch.setattr(hosted_conversation, "_clean_adk_event", new_shape)
    with caplog.at_level("WARNING"):
        archive = _workspace_archive(tmp_path)

    # Left out, so nothing raw leaves, and loud enough that a bug here is not mistaken for a damaged file.
    assert sorted(_members(archive)) == ["contract.json"]
    unexpected = [one for one in caplog.records if "session database" in one.getMessage()]
    assert len(unexpected) == 1
    assert unexpected[0].levelname == "ERROR"
    assert unexpected[0].exc_info and unexpected[0].exc_info[0] is TypeError


def test_a_damaged_session_database_is_a_warning_with_its_traceback(tmp_path, caplog):
    import sqlite3

    from fi.alk.harness.hosted_conversation import _workspace_archive

    database = tmp_path / ".futureagi-adk.db"
    _adk_database(database)
    with sqlite3.connect(database) as one:
        one.execute("UPDATE events SET event_data = 'not json' WHERE rowid = 1")

    with caplog.at_level("WARNING"):
        _workspace_archive(tmp_path)

    expected = [one for one in caplog.records if "session database" in one.getMessage()]
    assert len(expected) == 1 and expected[0].levelname == "WARNING"
    assert expected[0].exc_info and issubclass(expected[0].exc_info[0], ValueError)


def test_a_workspace_written_by_an_older_kit_packs_without_the_actor_file_or_prompt_that_hold_a_key(
    tmp_path, caplog
):
    from fi.alk.harness.hosted_conversation import _workspace_archive

    key = "AKIA" + "IOSFODNN7EXAMPLE"
    (tmp_path / "actors.json").write_text(json.dumps({"actors": [{"id": "it_desk", "prompt": f"You hold {key}"}]}))
    (tmp_path / "simulator_prompt.md").write_text(f"The caller reads out {key}")
    (tmp_path / "contract.json").write_text("{}")

    with caplog.at_level("WARNING"):
        members = _members(_workspace_archive(tmp_path))

    assert sorted(members) == ["contract.json"]
    assert not [name for name, content in members.items() if key.encode() in content]
    said = [one.getMessage() for one in caplog.records if one.levelname == "WARNING"]
    assert any("actors.json" in one for one in said) and any("simulator_prompt.md" in one for one in said)
    assert not any(key in one for one in said)


def test_an_honest_actor_file_and_simulator_prompt_pack_unchanged(tmp_path):
    from fi.alk.harness.hosted_conversation import _workspace_archive

    actors = json.dumps({"actors": [{"id": "it_desk", "prompt": "You hold the shared key $AWS_KEY_ID"}]})
    prompt = "You are a caller with persona {{ persona }}. Follow {{ instruction }}."
    (tmp_path / "actors.json").write_text(actors)
    (tmp_path / "simulator_prompt.md").write_text(prompt)

    members = _members(_workspace_archive(tmp_path))

    assert members["actors.json"] == actors.encode() and members["simulator_prompt.md"] == prompt.encode()
