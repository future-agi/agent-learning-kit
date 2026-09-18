from __future__ import annotations

import asyncio
import json
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
from fi.alk.harness.hosted_conversation import ConversationCapabilities
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
            },
            "endpoints": {
                "commands": f"{prefix}/commands/",
                "events": f"{prefix}/events/",
                "rerun": f"{prefix}/rerun/",
                "run_status": f"{prefix}/run-status/",
                "workspace": f"{prefix}/workspace/",
                "session_store": f"{prefix}/session-store/",
                "session_store_append": f"{prefix}/session-store/append/",
            },
        }
    )


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
            "payload": {"command_message_id": "message-1", "replayed": True},
            "acknowledge_through": 1,
        }
    ]

    journal_path = tmp_path / ".futureagi-conversation.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    journal["message-3"] = "started"
    journal_path.write_text(json.dumps(journal), encoding="utf-8")
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
