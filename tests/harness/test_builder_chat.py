from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fi.alk.harness import builder
from fi.alk.harness.chat_policy import load_chat_policy
from fi.alk.harness.hosted_chat_entrypoint import BuilderConversation, HostedChatRuntime
from fi.alk.harness.hosted_conversation import (
    ConversationCapabilities,
    ConversationClient,
    ConversationTransportError,
)
from fi.alk.harness.session import Event as RuntimeEvent


def _capabilities() -> ConversationCapabilities:
    conversation_id = "4f1ddaa9-569e-4ddd-bef0-74926bb42b16"
    prefix = f"https://platform.example/simulate/api/harness/conversations/{conversation_id}"
    names = ("commands", "events", "rerun", "adjust", "workspace", "session_store")
    endpoints = {name: f"{prefix}/{name.replace('_', '-')}/" for name in names}
    endpoints["run_status"] = f"{prefix}/run-status/"
    endpoints["session_store_append"] = f"{prefix}/session-store/append/"
    return ConversationCapabilities.model_validate(
        {
            "schema_version": "futureagi.harness-conversation.v1",
            "conversation_id": conversation_id,
            "job_id": "job-1",
            "token": "token",
            "fence": "fence",
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "identity": {"app_name": "alk-harness", "user_id": "conversation-user"},
            "turn_context": {"conversation_id": conversation_id, "stage": "builder"},
            "endpoints": endpoints,
        }
    )


class BuilderClient:
    def __init__(self, *, applied: bool = True):
        self.capabilities = _capabilities()
        self.turn_context = self.capabilities.turn_context
        self.command_watermark = 0
        self.events: list[dict] = []
        self.checkpoints = 0
        self.published: list[str] = []
        self.queries: list[dict] = []
        self.applied = applied

    async def emit(self, kind, **kwargs):
        self.events.append({"kind": kind, **kwargs})
        acknowledged = kwargs.get("acknowledge_through")
        if acknowledged is not None:
            self.command_watermark = max(self.command_watermark, acknowledged)
        return self.events[-1]

    async def checkpoint(self, workspace):
        self.checkpoints += 1
        return {"digest": f"sha256:{self.checkpoints:064x}", "size": 1}

    async def publish(self, digest):
        self.published.append(digest)
        if not self.applied:
            return {"applied": False, "code": "conversation_checkpoint_invalid", "message": "bad"}
        return {
            "applied": True,
            "revision": "sha256:new",
            "scenario_count": 41,
            "added": [{"number": 41, "name": "late_driver"}],
            "removed": [],
            "changed": [],
        }

    async def scenarios(self, query):
        self.queries.append(query)
        return {"rows": [{"number": 7, "name": "seven"}, {"number": 12, "name": "twelve"}]}

    async def append_session(self, key, entries):
        return None

    async def load_session(self, key):
        return {"entries": []}


class EditingConversation(BuilderConversation):
    """A builder whose model writes one scenario file and never calls save_changes."""

    def __init__(self, workspace: Path, *, writes: bool):
        super().__init__(lambda: None)
        self.workspace = workspace
        self.writes = writes
        self.messages: list[str] = []

    async def say(self, message, on_event=None):
        self.messages.append(message)
        if self.writes:
            (self.workspace / "scenarios" / "late_driver").mkdir(parents=True)
            (self.workspace / "scenarios" / "late_driver" / "scenario.json").write_text("{}")
        on_event(RuntimeEvent(kind="text", text="Added it.", detail={"stage": "builder"}))


def _runtime(tmp_path: Path, client: BuilderClient, monkeypatch) -> HostedChatRuntime:
    monkeypatch.setenv("ALK_HARNESS", "vertex-gemini")
    monkeypatch.setenv("ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER", "1")
    (tmp_path / "scenarios" / "one").mkdir(parents=True)
    (tmp_path / "scenarios" / "one" / "scenario.json").write_text("{}")
    policy = tmp_path / "policy.yaml"
    policy.write_text("schema_version: 1\nharness_chat:\n  enabled: true\n", encoding="utf-8")
    runtime = HostedChatRuntime(
        client=client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy),
        workspace=tmp_path,
        source=tmp_path,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 1},
    )
    return runtime


def _turn(runtime: HostedChatRuntime, payload: dict) -> None:
    command = {"sequence": 1, "message_id": "m-1", "kind": "user_message", "payload": payload}
    asyncio.run(runtime._handle(command))


def _reply(client: BuilderClient) -> str:
    return next(e for e in client.events if e["kind"] == "assistant_message")["payload"]["text"]


def test_selected_rows_reach_the_builder_by_tab_number(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    conversation = EditingConversation(tmp_path, writes=False)
    runtime.conversation = conversation
    runtime._published_fingerprint()

    _turn(runtime, {"content": "make these harder", "scenario_ids": ["a", "b"]})

    assert client.queries == [{"ids": ["a", "b"], "page_size": 100}]
    assert conversation.messages == [
        "[Selected on the Scenarios tab (2): #7 seven, #12 twelve]\n\nmake these harder"
    ]
    assert client.published == []


def test_unsaved_changes_are_published_and_reported_by_number(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime.conversation = EditingConversation(tmp_path, writes=True)
    runtime._published_fingerprint()
    monkeypatch.setattr("fi.alk.harness.bundle_author_v2.reseal_scenarios", lambda root: None)

    _turn(runtime, {"content": "add one"})

    assert len(client.published) == 1
    assert "Saved to the environment: added #41 late_driver" in _reply(client)
    basis = json.loads((tmp_path / ".futureagi-authoring-basis.json").read_text())
    assert basis == {"authoring_revision": "sha256:new"}
    # Saved once; nothing is left for the next turn to publish again.
    assert asyncio.run(runtime._save_changes())["unchanged"] is True


def test_a_change_to_the_world_data_alone_is_saved(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    (tmp_path / "world.sqlite").write_bytes(b"before")
    runtime._published_fingerprint()
    monkeypatch.setattr("fi.alk.harness.bundle_author_v2.reseal_scenarios", lambda root: None)

    (tmp_path / "world.sqlite").write_bytes(b"after")
    result = asyncio.run(runtime._save_changes())

    assert result.get("unchanged") is not True
    assert len(client.published) == 1


def test_a_rejected_publish_is_never_reported_as_done(tmp_path, monkeypatch):
    client = BuilderClient(applied=False)
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime.conversation = EditingConversation(tmp_path, writes=True)
    runtime._published_fingerprint()
    monkeypatch.setattr("fi.alk.harness.bundle_author_v2.reseal_scenarios", lambda root: None)

    _turn(runtime, {"content": "add one"})

    assert "These changes were NOT saved to the environment: bad." in _reply(client)
    assert not (tmp_path / ".futureagi-authoring-basis.json").exists()


def test_a_failed_reseal_ends_the_turn_failed_with_nothing_to_land(
    tmp_path, monkeypatch
):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime.conversation = EditingConversation(tmp_path, writes=True)
    runtime._published_fingerprint()

    def fails(root):
        raise RuntimeError("seed import failed")

    monkeypatch.setattr("fi.alk.harness.bundle_author_v2.reseal_scenarios", fails)

    _turn(runtime, {"content": "edit the world"})

    assert client.published == []
    reply = next(e for e in client.events if e["kind"] == "assistant_message")
    assert "These changes were NOT saved" in reply["payload"]["text"]
    assert reply["payload"]["error"] == "reseal_failed"
    kinds = [event["kind"] for event in client.events]
    assert "checkpoint_committed" not in kinds
    assert client.events[-1]["kind"] == "turn_completed"
    assert client.events[-1]["payload"]["outcome"] == "failed"


def test_read_only_tools_skip_the_workspace_checkpoint(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()

    class ReadingConversation(EditingConversation):
        async def say(self, message, on_event=None):
            for name in ("find_scenarios", "submit_scenario"):
                on_event(
                    RuntimeEvent(
                        kind="tool", tool=f"mcp__x__{name}", detail={"call_id": name}
                    )
                )
            on_event(RuntimeEvent(kind="text", text="ok", detail={}))

    runtime.conversation = ReadingConversation(tmp_path, writes=False)
    _turn(runtime, {"content": "look"})

    # One before the turn, one for the write, one at the end; none for the read.
    assert client.checkpoints == 3


class PackFailingClient(BuilderClient):
    """A client whose workspace pack fails on the checkpoints named, as when a file shrinks mid-pack."""

    def __init__(self, failing: set[int]):
        super().__init__()
        self.failing = failing

    async def checkpoint(self, workspace):
        self.checkpoints += 1
        if self.checkpoints in self.failing:
            raise OSError("unexpected end of data")
        return {"digest": f"sha256:{self.checkpoints:064x}", "size": 1}


def test_a_checkpoint_whose_pack_fails_leaves_the_pump_running_for_the_next_one(
    tmp_path, monkeypatch
):
    client = PackFailingClient(failing={1})
    runtime = _runtime(tmp_path, client, monkeypatch)
    queue: asyncio.Queue = asyncio.Queue()
    for name in ("submit_scenario", "edit_scenario"):
        queue.put_nowait(RuntimeEvent(kind="tool", tool=f"mcp__x__{name}", detail={"call_id": name}))
    queue.put_nowait(RuntimeEvent(kind="text", text="Both kept.", detail={}))
    queue.put_nowait(None)
    said: list[str] = []

    asyncio.run(
        runtime._pump_events(
            queue,
            assistant_message_id="a-1",
            fallback_invocation_id="turn-1",
            emitted_text=said,
            turn_failures=[],
        )
    )

    assert client.checkpoints == 2
    committed = [e for e in client.events if e["kind"] == "checkpoint_committed"]
    assert [e["payload"]["digest"] for e in committed] == [f"sha256:{2:064x}"]
    assert said == ["Both kept."]


def test_a_turn_whose_pump_failed_still_sends_its_reply_and_completes(tmp_path, monkeypatch):
    # The turn's opening checkpoint is the first; the write's, packed by the pump, fails.
    client = PackFailingClient(failing={2})
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()

    class WritingConversation(EditingConversation):
        async def say(self, message, on_event=None):
            on_event(RuntimeEvent(kind="tool", tool="mcp__x__edit_scenario", detail={"call_id": "e"}))
            on_event(RuntimeEvent(kind="text", text="Edited it.", detail={}))

    runtime.conversation = WritingConversation(tmp_path, writes=False)
    _turn(runtime, {"content": "make it harder"})

    assert _reply(client) == "Edited it."
    completed = [e for e in client.events if e["kind"] == "turn_completed"]
    assert [e["payload"]["outcome"] for e in completed] == ["success"]
    assert client.checkpoints == 3


def test_older_platforms_without_builder_endpoints_still_load(monkeypatch):
    body = _capabilities().model_dump(mode="json")
    capabilities = ConversationCapabilities.model_validate(body)
    client = ConversationClient(capabilities)
    with pytest.raises(ConversationTransportError, match="does not offer"):
        asyncio.run(client.scenarios({}))


def test_every_skill_is_loadable_by_the_builder_except_its_own():
    index = builder.skill_index()
    assert "write-scenarios" in index and "platform" in index
    assert builder.BUILDER not in index
    assert all(index.values())


def test_the_secret_guard_refuses_env_reads_and_scrubs_the_shell():
    from fi.alk.harness.config import scrubbed_command, secret_refusal

    for tool, arguments in (
        ("Bash", {"command": "printenv"}),
        ("Bash", {"command": "echo $SOME_API_KEY"}),
        ("Bash", {"command": "cat /proc/1/environ"}),
        ("Read", {"file_path": "/work/authoring/.env"}),
        ("Read", {"file_path": "/run/futureagi/conversation.json"}),
        ("Write", {"file_path": "/work/authoring/scenarios/a/scenario.json"}),
        ("Read", {"file_path": "/work/authoring/../../run/futureagi/simulator-secrets.json"}),
        ("Read", {"file_path": "/work/.authoring-credentials/google.json"}),
        ("Grep", {"pattern": "KEY", "path": "/run"}),
        ("Grep", {"pattern": "KEY", "path": "/"}),
    ):
        assert secret_refusal(tool, arguments), (tool, arguments)
    for tool, arguments in (
        ("Bash", {"command": "ls scenarios | wc -l"}),
        ("Read", {"file_path": "/work/authoring/contract.json"}),
        ("Write", {"file_path": "/tmp/analysis.py"}),
        ("Grep", {"pattern": "refund", "path": "/work/authoring/scenarios"}),
    ):
        assert not secret_refusal(tool, arguments), (tool, arguments)
    assert scrubbed_command("ls").startswith("env -i ")


def test_world_change_is_all_or_nothing_and_reports_counts(tmp_path):
    import sqlite3

    from fi.alk.harness.builder import world_change

    db = sqlite3.connect(tmp_path / "world.sqlite")
    db.executescript(
        "PRAGMA foreign_keys=ON; CREATE TABLE a(id TEXT PRIMARY KEY);"
        "CREATE TABLE b(id TEXT, a_id TEXT REFERENCES a(id));"
        "INSERT INTO a VALUES ('x'); INSERT INTO b VALUES ('1', 'x');"
    )
    db.close()

    assert "1 rows changed" in world_change(tmp_path, "INSERT INTO a VALUES ('y')")
    assert "refused" in world_change(tmp_path, "DELETE FROM a WHERE id = 'x'")
    assert "exactly one" in world_change(tmp_path, "DROP TABLE a")
    assert "a: 2, b: 1" in world_change(tmp_path, "UPDATE b SET id = '2'")


def test_meaning_changes_wait_for_a_yes_to_that_call_but_surface_edits_do_not(tmp_path, monkeypatch):
    from fi.alk.harness.backends import ToolSpec
    from fi.alk.harness.hosted_chat_entrypoint import _CONFIRM

    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    (tmp_path / "scenarios" / "one" / "scenario.json").write_text(
        json.dumps(
            {
                "name": "one",
                "scenario_key": "one",
                "instruction": "ask for a refund",
                "persona": {"name": "Ana", "accent": "American"},
                "solution": [
                    {
                        "tool": "refund",
                        "arguments": {"id": "7"},
                        "environment_arguments": {},
                    }
                ],
            }
        )
    )
    (tmp_path / "sub_goals.json").write_text(
        json.dumps({"sub_goals": [{"name": "refunded", "description": "x"}]})
    )
    calls, asked, answers = [], [], []

    async def handler(args):
        calls.append(args)
        return {"content": [{"type": "text", "text": "kept"}]}

    async def ask(tool, payload, context):
        asked.append(payload["question"])
        return {"content": [{"type": "text", "text": answers.pop(0)}]}

    runtime._ask = ask
    submit = runtime._confirmed(ToolSpec("submit_scenario", "", {}, handler))
    drop = runtime._confirmed(ToolSpec("drop_scenarios", "", {}, handler))
    goal = runtime._confirmed(ToolSpec("add_sub_goal", "", {}, handler))

    surface = {"name": "one", "instruction": "ask for a refund", "persona": {"name": "Ana", "accent": "Indian"}}
    meaning = {"name": "one", "instruction": "ask for a cancellation", "persona": {"name": "Ana"}}
    assert asyncio.run(submit.handler(surface)).get("is_error") is None
    assert asyncio.run(submit.handler({"name": "two", "instruction": "anything"})).get("is_error") is None
    assert asyncio.run(goal.handler({"name": "new-goal"})).get("is_error") is None
    assert (
        asyncio.run(
            submit.handler({**surface, "name": "uno", "scenario_key": "one"})
        ).get("is_error")
        is None
    )
    (tmp_path / "scenarios" / "old").mkdir()
    (tmp_path / "scenarios" / "old" / "scenario.json").write_text(
        json.dumps({"name": "old"})
    )
    answers.append("No")
    unreadable = {"name": "old", "instruction": "anything"}
    assert asyncio.run(submit.handler(unreadable))["is_error"] is True
    asked.pop()
    resubmitted = {
        **surface,
        "name": "uno",
        "scenario_key": "one",
        "solution": [{"tool": "refund", "arguments": {"id": "7"}}],
    }
    assert asyncio.run(submit.handler(resubmitted)).get("is_error") is None
    assert asked == []
    answers.append("No")
    assert (
        asyncio.run(submit.handler({**meaning, "name": "uno", "scenario_key": "one"}))[
            "is_error"
        ]
        is True
    )
    assert "ask for a cancellation" in asked.pop()
    assert not runtime._changes_situation(
        {"scenario_key": "one", "persona": {"name": "Ana"}}
    )
    assert runtime._changes_situation({"scenario_key": "one", "persona": {"accent": "Indian"}})

    answers.extend(["No, keep it", "Which scenarios use it?"])
    assert asyncio.run(drop.handler({"names": ["one"]}))["is_error"] is True
    assert asyncio.run(submit.handler(meaning))["is_error"] is True
    answers.append(_CONFIRM)
    assert asyncio.run(goal.handler({"name": "refunded"})).get("is_error") is None
    answers.append("No")
    assert asyncio.run(drop.handler({"names": ["one"]}))["is_error"] is True
    assert "drop_scenarios" in asked[0] and "add_sub_goal" in asked[2]
    long_meaning = {**meaning, "setup_code": "def setup(world):\n" + "    pass\n" * 200 + "    world.done()"}
    answers.append("No")
    assert asyncio.run(submit.handler(long_meaning))["is_error"] is True
    assert "world.done()" in asked[-1]

    runtime._note_health("one is broken")
    answers.append("No")
    assert asyncio.run(submit.handler(meaning))["is_error"] is True
    assert asyncio.run(submit.handler(surface)).get("is_error") is None
    assert len(calls) == 7


def test_an_edit_and_a_clear_go_through_the_same_confirmation_as_a_submission(
    tmp_path, monkeypatch
):
    from fi.alk.harness.backends import ToolSpec

    runtime = _runtime(tmp_path, BuilderClient(), monkeypatch)
    (tmp_path / "scenarios" / "one" / "scenario.json").write_text(
        json.dumps(
            {
                "name": "one",
                "scenario_key": "one",
                "instruction": "ask for a refund",
                "persona": {"name": "Ana", "accent": "American"},
                "fixture": {"origin": "seed", "order": "7"},
                "solution": [
                    {
                        "tool": "refund",
                        "arguments": {"id": "7"},
                        "environment_arguments": {},
                    }
                ],
            }
        )
    )
    calls, asked, answers = [], [], []

    async def handler(args):
        calls.append(args)
        return {"content": [{"type": "text", "text": "kept"}]}

    async def ask(tool, payload, context):
        asked.append(payload["question"])
        return {"content": [{"type": "text", "text": answers.pop(0)}]}

    runtime._ask = ask
    edit = runtime._confirmed(ToolSpec("edit_scenario", "", {}, handler))
    assert (
        asyncio.run(
            edit.handler({"scenario_key": "one", "persona": {"accent": "Indian"}})
        ).get("is_error")
        is None
    )
    assert asked == []
    answers.append("No")
    assert (
        asyncio.run(edit.handler({"scenario_key": "one", "clear": ["solution"]}))[
            "is_error"
        ]
        is True
    )
    assert len(asked) == 1
    assert runtime._changes_situation({"scenario_key": "one", "clear": ["fixture"]})
    assert runtime._changes_situation({"scenario_key": "one", "clear": ["persona"]})
    assert runtime._changes_situation(
        {"scenario_key": "one", "clear": ["persona.accent"]}
    )
    runtime._from_platform = True
    assert (
        asyncio.run(edit.handler({"scenario_key": "one", "clear": ["fixture"]}))[
            "is_error"
        ]
        is True
    )
    assert len(calls) == 1


_EVERY_CHANGE = "Yes, make every change for this request"


def _guarded_refund_changes(tmp_path, monkeypatch, answer):
    """A saved scenario and sub-goal, and guarded tools whose changes to them each need a yes."""
    from fi.alk.harness.backends import ToolSpec

    runtime = _runtime(tmp_path, BuilderClient(), monkeypatch)
    (tmp_path / "scenarios" / "one" / "scenario.json").write_text(
        json.dumps({"name": "one", "scenario_key": "one", "instruction": "ask for a refund"})
    )
    (tmp_path / "sub_goals.json").write_text(
        json.dumps({"sub_goals": [{"name": "refunded", "description": "x"}]})
    )
    ran, offered = [], []

    async def handler(args):
        ran.append(args)
        return {"content": [{"type": "text", "text": "kept"}]}

    async def ask(tool, payload, context):
        offered.append(payload["options"])
        await asyncio.sleep(0.01)  # the person takes a moment to answer
        return {"content": [{"type": "text", "text": answer(len(offered))}]}

    runtime._ask = ask

    def guarded(name):
        return runtime._confirmed(ToolSpec(name, "", {}, handler)).handler

    return runtime, guarded, ran, offered


def test_one_yes_to_every_change_runs_the_rest_of_its_message_and_the_next_message_asks_again(
    tmp_path, monkeypatch
):
    from fi.alk.harness.hosted_chat_entrypoint import _CONFIRM

    answers = [_EVERY_CHANGE, "No"]
    runtime, guarded, ran, offered = _guarded_refund_changes(
        tmp_path, monkeypatch, lambda asked: answers[asked - 1]
    )
    edit, goal = guarded("edit_scenario"), guarded("add_sub_goal")
    turns = [
        [
            lambda: edit({"scenario_key": "one", "instruction": "ask for a cancellation"}),
            lambda: goal({"name": "refunded", "description": "y"}),
            lambda: edit({"scenario_key": "one", "instruction": "ask for store credit"}),
        ],
        [lambda: edit({"scenario_key": "one", "instruction": "ask for an exchange"})],
    ]
    results: list[list[dict]] = []

    class Changing(BuilderConversation):
        def __init__(self):
            super().__init__(lambda: None)

        async def say(self, message, on_event=None):
            results.append([await change() for change in turns[len(results)]])
            on_event(RuntimeEvent(kind="text", text="Done.", detail={"stage": "builder"}))

    runtime.conversation = Changing()
    runtime._published_fingerprint()
    for sequence in (1, 2):
        command = {
            "sequence": sequence,
            "message_id": f"m-{sequence}",
            "kind": "user_message",
            "payload": {"content": "Add 30 more scenarios so it has 60 in total"},
        }
        asyncio.run(runtime._handle(command))

    # One question for the whole first message, then one again for the next.
    assert offered == [[_CONFIRM, _EVERY_CHANGE, "No"]] * 2
    assert [one.get("is_error") for one in results[0]] == [None, None, None]
    assert results[1][0]["is_error"] is True and "they said: No" in results[1][0]["content"][0]["text"]
    assert [args.get("instruction") or args["name"] for args in ran] == [
        "ask for a cancellation",
        "refunded",
        "ask for store credit",
    ]


def test_a_yes_to_one_change_runs_only_that_change_and_a_no_still_refuses(tmp_path, monkeypatch):
    from fi.alk.harness.hosted_chat_entrypoint import _CONFIRM

    answers = [_CONFIRM, "No", _CONFIRM]
    _, guarded, ran, offered = _guarded_refund_changes(
        tmp_path, monkeypatch, lambda asked: answers[asked - 1]
    )
    edit, goal = guarded("edit_scenario"), guarded("add_sub_goal")

    assert asyncio.run(edit({"scenario_key": "one", "instruction": "ask for a cancellation"})).get("is_error") is None
    refused = asyncio.run(goal({"name": "refunded", "description": "y"}))
    assert refused["is_error"] is True and "did not confirm" in refused["content"][0]["text"]
    assert asyncio.run(goal({"name": "refunded", "description": "z"})).get("is_error") is None
    assert len(offered) == 3
    assert [args.get("instruction") or args["description"] for args in ran] == ["ask for a cancellation", "z"]


def test_changes_made_at_once_ask_one_at_a_time_so_one_yes_to_every_change_runs_them_all(
    tmp_path, monkeypatch
):
    _, guarded, ran, offered = _guarded_refund_changes(
        tmp_path, monkeypatch, lambda asked: _EVERY_CHANGE
    )
    edit = guarded("edit_scenario")

    async def together():
        return await asyncio.gather(
            *(
                edit({"scenario_key": "one", "instruction": f"ask for refund number {n}"})
                for n in range(3)
            )
        )

    assert [one.get("is_error") for one in asyncio.run(together())] == [None, None, None]
    # A second open question would have no answer: the platform takes one to the latest only.
    assert len(offered) == 1 and len(ran) == 3


def test_a_drop_after_a_yes_to_every_change_still_asks_on_its_own(tmp_path, monkeypatch):
    from fi.alk.harness.hosted_chat_entrypoint import _CONFIRM

    answers = [_EVERY_CHANGE, _EVERY_CHANGE, _CONFIRM]
    _, guarded, ran, offered = _guarded_refund_changes(
        tmp_path, monkeypatch, lambda asked: answers[asked - 1]
    )
    edit, drop = guarded("edit_scenario"), guarded("drop_scenario")

    assert asyncio.run(edit({"scenario_key": "one", "instruction": "ask for a cancellation"})).get("is_error") is None
    # Not offered on a drop, so the batch answer typed in does not run it.
    assert asyncio.run(drop({"name": "one"})).get("is_error") is True
    assert asyncio.run(drop({"name": "one"})).get("is_error") is None
    assert asyncio.run(edit({"scenario_key": "one", "instruction": "ask for store credit"})).get("is_error") is None
    assert offered == [[_CONFIRM, _EVERY_CHANGE, "No"], [_CONFIRM, "No"], [_CONFIRM, "No"]]
    assert [args.get("instruction") or args["name"] for args in ran] == [
        "ask for a cancellation",
        "one",
        "ask for store credit",
    ]


def test_only_scenario_and_sub_goal_changes_are_covered_by_a_yes_to_every_change():
    from fi.alk.harness.hosted_chat_entrypoint import _ASKS_ALONE, _NEEDS_CONFIRMATION

    # A newly guarded tool fails here until it is put on one side or the other.
    assert _ASKS_ALONE < _NEEDS_CONFIRMATION
    assert _NEEDS_CONFIRMATION - _ASKS_ALONE == {"submit_scenario", "edit_scenario", "add_sub_goal"}


def test_the_keys_the_platform_has_used_are_never_given_to_a_new_scenario(
    tmp_path, monkeypatch
):
    from fi.alk.harness.scenario import used_keys

    monkeypatch.setenv("ALK_HARNESS", "vertex-gemini")
    monkeypatch.setenv("ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER", "1")
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "schema_version: 1\nharness_chat:\n  enabled: true\n", encoding="utf-8"
    )
    HostedChatRuntime(
        client=BuilderClient(),  # type: ignore[arg-type]
        policy=load_chat_policy(policy),
        workspace=tmp_path,
        source=tmp_path,
        job={
            "metadata": {"agent_name": "demo", "used_scenario_keys": ["two", "one"]},
            "scenario_count": 1,
        },
    )
    assert {"one", "two"} <= used_keys(tmp_path)


def test_the_harness_builds_from_an_empty_workspace_with_the_tools_for_what_exists(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ALK_HARNESS", "claude")
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "schema_version: 1\nharness_chat:\n  enabled: true\n  stages:\n    builder:\n      enabled: true\n"
        "  lifecycle:\n    request_user_input: true\n",
        encoding="utf-8",
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("def answer():\n    return 'hi'\n", encoding="utf-8")
    workspace = tmp_path / "authoring"
    workspace.mkdir()

    class LiveBuildClient(BuilderClient):
        def __init__(self):
            super().__init__()
            self.turn_context = {**self.capabilities.turn_context, "capabilities": {"control_only": True}}

        async def overview(self):
            return {}

    client = LiveBuildClient()
    runtime = HostedChatRuntime(
        client=client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy),
        workspace=workspace,
        source=source,
        job={"metadata": {"agent_name": "demo"}, "scenario_count": 3},
    )
    assert isinstance(runtime.conversation, BuilderConversation)
    stage = asyncio.run(runtime._open_builder())
    granted = set(stage.spec.granted())
    assert "mcp__platform__finish_build" in granted and "mcp__knowledge__read_skill" in granted
    assert any(name.endswith("submit_contract") for name in granted)
    assert "Sub-goals are shared" in stage.spec.system_prompt or "shared sub-goals" in stage.spec.system_prompt

    finish = next(one for one in stage.spec.servers["platform"].tools if one.name == "finish_build")
    refused = asyncio.run(finish.handler({}))
    assert refused["is_error"] and "no contract is saved" in refused["content"][0]["text"]

    from fi.alk.harness import builder, scenario_tools

    monkeypatch.setattr(runtime, "_what_exists", lambda: (True, True))
    monkeypatch.setattr(scenario_tools, "load_scenarios", lambda path: ["one", "two"])
    monkeypatch.setattr(builder, "suite_health", lambda *args, **kwargs: [])
    short = asyncio.run(finish.handler({}))
    assert short["is_error"] and "2 of the 3 scenarios" in short["content"][0]["text"]
    assert not (workspace / ".futureagi-build-finished.json").exists()
    monkeypatch.setattr(scenario_tools, "load_scenarios", lambda path: ["one", "two", "three", "four"])
    over = asyncio.run(finish.handler({}))
    assert over["is_error"] and "the build asked for 3" in over["content"][0]["text"]
    assert not (workspace / ".futureagi-build-finished.json").exists()

    async def commands():
        return [{"sequence": 7, "message_id": "m7", "kind": "user_message", "payload": {"content": "add a lapsed policy"}}]

    client.commands = commands
    stopped = []

    class Working:
        async def interrupt_response(self):
            stopped.append(True)
            return True

        async def close(self):
            pass

    runtime.conversation = Working()

    async def building():
        while not stopped:
            await asyncio.sleep(0.05)

    yielded = asyncio.run(runtime._turn_until_interrupted(building(), command_sequence=1))
    assert yielded["message_id"] == "m7" and stopped
    # Answered next as its own turn, so it is not acknowledged here.
    assert runtime._turn_acknowledged_through == 0



def test_opening_a_chat_on_a_built_environment_does_not_reprove_the_whole_suite(tmp_path, monkeypatch):
    import pytest

    from fi.alk.harness import builder
    from fi.alk.harness.backends import ToolServer
    from fi.alk.harness.contract import AgentContract

    class Opened(Exception):
        pass

    def health(*args, **kwargs):
        raise Opened(kwargs)

    empty = ToolServer(name="stub", version="1", tools=[])
    monkeypatch.setattr(builder, "load_scenarios", lambda out: ["saved"])
    monkeypatch.setattr(builder, "scenario_tools", lambda *args, **kwargs: (empty, ["saved"]))
    monkeypatch.setattr(builder, "knowledge_server", lambda *args, **kwargs: empty)
    monkeypatch.setattr(builder, "suite_server", lambda *args, **kwargs: empty)
    monkeypatch.setattr(builder, "suite_health", health)
    with pytest.raises(Opened) as opened:
        builder.open_builder(AgentContract(agent_name="demo"), out=tmp_path, source_root=None)
    assert not opened.value.args[0].get("prove_all")


def _job(source_kind: str, connector: str, mode: str, config: dict) -> dict:
    return {
        "job_id": "job-1",
        "run_id": "run-1",
        "execution": "hosted",
        "source": {"kind": source_kind, "visibility": "public"}
        if source_kind == "provider"
        else {"kind": "archive", "archive_artifact_id": "source-1"},
        "agent": {
            "connector": connector,
            "mode": mode,
            "config": config,
            "secret_refs": {
                "VAPI_API_KEY": {"manager": "platform-vault", "key": "vapi-key", "purpose": "target_provider"}
            }
            if mode == "provider_import"
            else {},
        },
        "scenario_count": 1,
        "runtime": {"isolation": "dedicated_vm", "cpu_units": 2, "memory_mb": 4096, "parallelism": 1},
        "metadata": {"agent_name": "demo"},
    }


def _opened_source(tmp_path: Path, monkeypatch, job: dict):
    opened = {}
    monkeypatch.setattr(
        "fi.alk.harness.builder.open_for_building",
        lambda contract, **kwargs: opened.update(kwargs),
    )
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    runtime = _runtime(tmp_path / "authoring", BuilderClient(), monkeypatch)
    runtime.source = source
    runtime.job = job
    runtime._open_for_building(None)
    return opened["source"]


def test_a_phone_target_opens_the_builder_on_its_supplied_prompt(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_PROVIDER_IMPORT_PROFILE_PATH", "")
    monkeypatch.delenv("ALK_PROVIDER_IMPORT_PROFILE_PATH")
    job = _job(
        "provider",
        "phone",
        "connect_only",
        {"phone_number": "+14155551234", "target_system_prompt": "Answer ride questions."},
    )

    source = _opened_source(tmp_path, monkeypatch, job)

    assert source.kind == "provider"
    assert "Answer ride questions." in source.briefing()


def test_an_imported_provider_reaches_the_builder_from_the_build_process_cache(
    tmp_path, monkeypatch
):
    import os

    from fi.alk.harness.understand import _provider_import_briefing

    monkeypatch.setenv("ALK_PROVIDER_IMPORT_PROFILE_PATH", "")
    monkeypatch.delenv("ALK_PROVIDER_IMPORT_PROFILE_PATH")
    (tmp_path / "provider-import-profile.json").write_text(
        json.dumps({"provider": "vapi", "system_prompt": "Imported assistant prompt."})
    )
    job = _job("archive", "vapi", "provider_import", {"assistant_id": "asst-1"})

    source = _opened_source(tmp_path, monkeypatch, job)

    assert source.kind == "repo"
    assert os.environ["ALK_PROVIDER_IMPORT_PROFILE_PATH"] == str(tmp_path / "provider-import-profile.json")
    assert "Imported assistant prompt." in _provider_import_briefing()


def test_a_repository_opens_the_builder_on_its_source(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_PROVIDER_IMPORT_PROFILE_PATH", "")
    monkeypatch.delenv("ALK_PROVIDER_IMPORT_PROFILE_PATH")
    job = _job("archive", "livekit", "connect_only", {})

    source = _opened_source(tmp_path, monkeypatch, job)

    assert source.kind == "repo"
    assert "ALK_PROVIDER_IMPORT_PROFILE_PATH" not in __import__("os").environ


def test_a_retried_run_start_sends_the_same_operation_id(monkeypatch):
    from types import SimpleNamespace

    body = _capabilities().model_dump(mode="json")
    body["endpoints"]["run_start"] = body["endpoints"]["run_status"].replace("run-status", "run-start")
    sent = []

    class Transport:
        def request(self, method, url, **kwargs):
            sent.append(kwargs.get("json_body"))
            return SimpleNamespace(status_code=503 if len(sent) == 1 else 200, body={"run_id": "r-1"})

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    client = ConversationClient(ConversationCapabilities.model_validate(body), transport=Transport())

    asyncio.run(client.start_run([1], [], 1))

    assert len(sent) == 2 and sent[0]["operation_id"] == sent[1]["operation_id"]


def test_a_sub_agent_replacing_a_shared_sub_goal_waits_for_a_yes(tmp_path, monkeypatch):
    from fi.alk.harness.backends import SessionSpec, ToolSpec, WorkerSpec, tool_server

    runtime = _runtime(tmp_path, BuilderClient(), monkeypatch)
    (tmp_path / "sub_goals.json").write_text(json.dumps({"sub_goals": [{"name": "refunded", "description": "x"}]}))
    ran, asked = [], []

    async def handler(args):
        ran.append(args)
        return {"content": [{"type": "text", "text": "kept"}]}

    async def ask(tool, payload, context):
        asked.append(tool)
        return {"content": [{"type": "text", "text": "No"}]}

    runtime._ask = ask
    server = tool_server(name="scenarios", version="1", tools=[ToolSpec("add_sub_goal", "", {}, handler)])
    spec = SessionSpec(
        system_prompt="", servers={"scenarios": server},
        workers={"scenario_writer": WorkerSpec(description="", instructions="", servers={"scenarios": server})},
    )
    runtime._guard(spec)
    replace_goal = spec.workers["scenario_writer"].servers["scenarios"].tools[0].handler

    assert asyncio.run(replace_goal({"name": "refunded"}))["is_error"] is True
    assert asyncio.run(replace_goal({"name": "a-new-goal"})).get("is_error") is None
    assert asked == ["add_sub_goal"] and ran == [{"name": "a-new-goal"}]


def test_file_tools_on_any_backend_never_return_credentials(tmp_path):
    from fi.alk.harness.backends.files import file_tools
    from fi.alk.harness.backends.vertex_gemini import _spec_tool

    work = tmp_path / "work"
    (work / "scenarios").mkdir(parents=True)
    (work / "scenarios" / "a.json").write_text("SENTINEL-free")
    (work / ".env").write_text("SENTINEL=1")
    (work / "service_account.json").write_text('{"SENTINEL": 1}')
    tools = {spec.name: _spec_tool(spec.name, spec) for spec in file_tools(str(work / "scenarios"))}

    def run(name, **args):
        result = asyncio.run(tools[name].run_async(args=args, tool_context=None))
        return result["content"][0]["text"]

    assert "SENTINEL=1" not in run("Read", file_path="../.env")
    assert "SENTINEL" not in run("Read", file_path=str(work / "service_account.json")).replace("SENTINEL-free", "")
    found = run("Grep", pattern="SENTINEL", path="..")
    assert "SENTINEL-free" in found and "SENTINEL=1" not in found and '{"SENTINEL' not in found
    assert ".env" not in run("Glob", pattern="*", path="..")
    assert "a.json" in run("Glob", pattern="*.json")
    assert "kept out of this session" in run("Grep", pattern="x", path="/")


class RebuildClient(BuilderClient):
    def __init__(self, workspace: Path):
        super().__init__()
        self.workspace = workspace
        self.results: list[dict | None] = []

    async def overview(self):
        return {}

    async def checkpoint(self, workspace):
        from fi.alk.harness.nudge import NUDGE_RESULT

        path = self.workspace / NUDGE_RESULT
        self.results.append(json.loads(path.read_text()) if path.is_file() else None)
        return await super().checkpoint(workspace)


class RebuildConversation(BuilderConversation):
    """A builder whose model turn is ``act``, run against the stage the runtime opens."""

    def __init__(self, runtime: HostedChatRuntime, act):
        super().__init__(runtime._open_builder)
        self.act = act
        self.messages: list[str] = []

    async def say(self, message, on_event=None):
        self.messages.append(message)
        self.stage = await self._open_stage(resume=True)
        self.stage.emit_event = on_event
        await self.act(self.stage)
        on_event(
            RuntimeEvent(kind="text", text="Updated.", detail={"stage": "builder"})
        )

    async def resume(self, invocation_id, on_event=None):
        await self.say("", on_event)


def _rebuild(
    tmp_path: Path,
    monkeypatch,
    stage: str,
    act,
    *,
    stale_result: bool = False,
    draft_keys: tuple[str, ...] = (),
    journal: dict | None = None,
    redeliver: bool = False,
    resumed: bool = False,
):
    from test_nudge_turn import _suite

    from fi.alk.harness import spend

    monkeypatch.setenv("ALK_HARNESS", "vertex-gemini" if resumed else "claude")
    monkeypatch.setenv("ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER", "1")
    monkeypatch.setattr(spend, "_stages", {})
    workspace = tmp_path / "work"
    workspace.mkdir()
    _suite(workspace, "adds-a-big-mac")
    if stale_result:
        from fi.alk.harness.nudge import NUDGE_RESULT

        (workspace / NUDGE_RESULT).write_text('{"nudge_id": "an-older-turn"}')
    if journal is not None:
        (workspace / ".futureagi-conversation.json").write_text(json.dumps(journal))
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        "schema_version: 1\nharness_chat:\n  enabled: true\n  stages:\n    builder:\n"
        "      enabled: true\n",
        encoding="utf-8",
    )
    client = RebuildClient(workspace)
    if resumed:
        client.turn_context = {**client.turn_context, "active_invocation_id": "inv-1"}
    runtime = HostedChatRuntime(
        client=client,  # type: ignore[arg-type]
        policy=load_chat_policy(policy),
        workspace=workspace,
        source=tmp_path / "source",
        job={"metadata": {"agent_name": "cart"}, "scenario_count": 1},
    )
    asked: list[str] = []

    async def ask(tool, payload, context):
        asked.append(tool)
        return {"content": [{"type": "text", "text": "No"}]}

    runtime._ask = ask
    conversation = RebuildConversation(runtime, act)
    runtime.conversation = conversation
    command = {
        "sequence": 3,
        "message_id": "m-3",
        "kind": "nudge",
        "payload": {
            "content": "The agent moved to v2: add was renamed.",
            "nudge_id": "n-1",
            "stage": stage,
            "user_draft_keys": list(draft_keys),
        },
    }
    asyncio.run(runtime._handle(command))
    if redeliver:
        # The turn_completed was lost: the same command reaches the restored workspace.
        client.redelivered_at = len(client.events)
        asyncio.run(runtime._handle(command))
    return runtime, client, conversation, asked


def _tool(stage, server: str, name: str):
    return next(one for one in stage.spec.servers[server].tools if one.name == name)


def _called(stage, name: str) -> None:
    stage.emit_event(
        RuntimeEvent(kind="tool", tool=f"mcp__x__{name}", detail={"call_id": name})
    )


def test_an_environment_rebuild_turn_runs_its_own_tools_and_ends_the_sandbox(
    tmp_path, monkeypatch
):
    from fi.alk.harness import spend
    from fi.alk.harness.catalogue import load_catalogue
    from fi.alk.harness.nudge import NUDGE_RESULT
    from fi.alk.harness.source_data_invariants import ARTIFACT

    seen: dict = {}

    async def act(stage):
        seen["result_at_start"] = (tmp_path / "work" / NUDGE_RESULT).exists()
        seen["granted"] = set(stage.spec.granted())
        seen["workers"] = dict(stage.spec.workers)
        seen["rule"] = await _tool(stage, "scenarios", "add_rule").handler(
            {"rule": "Always read the item back.", "why": "v2 says so in agent.py"}
        )
        await _tool(stage, "nudge", "retire_sub_goal").handler(
            {"name": "right-item", "why": "gone in v2"}
        )
        seen["added"] = await _tool(stage, "world", "add_sub_goal").handler(
            {
                "name": "cart-exists",
                "what": "the cart table is still there",
                "check": "def check(world, calls):\n    return None if 'cart' in world.state() else 'no cart'\n",
            }
        )
        seen["added_too"] = await _tool(stage, "scenarios", "add_sub_goal").handler(
            {
                "name": "one-row",
                "what": "the cart holds at most one row",
                "check": "def check(world, calls):\n    return None if len(world.state()['cart']) <= 1 else 'more'\n",
            }
        )
        spend.record(
            "builder", 0.25, turns=1, models={"m2"}, tokens_in=40, tokens_out=6
        )
        (tmp_path / "source").mkdir()
        (tmp_path / "source" / "agent.py").write_text("def add_item(item_id):\n")
        review = {
            "name": "items-exist",
            "evidence": [{"path": "agent.py", "quote": "def add("}],
        }
        (tmp_path / "work" / ARTIFACT).write_text(json.dumps({"checks": [review]}))
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "rule added"}
        )

    runtime, client, conversation, asked = _rebuild(
        tmp_path, monkeypatch, "environment", act, stale_result=True
    )

    granted = seen["granted"]
    assert seen["result_at_start"] is False
    assert {
        "mcp__nudge__report_update",
        "mcp__nudge__retire_sub_goal",
        "mcp__world__save_world",
    } <= granted
    assert (
        not {
            "mcp__scenarios__submit_scenario",
            "mcp__platform__save_changes",
            "Delegate",
        }
        & granted
    )
    # One world writer: change_world would bypass the world save_world keeps.
    assert not any(name.endswith("__change_world") for name in granted)
    assert seen["workers"] == {}
    # No confirmation in a rebuild turn: nobody is there to answer.
    assert asked == [] and not seen["rule"].get("is_error"), seen["rule"]
    assert not seen["added"].get("is_error"), seen["added"]
    assert not seen["added_too"].get("is_error"), seen["added_too"]
    assert load_catalogue(tmp_path / "work").names() == {
        "item-added",
        "cart-exists",
        "one-row",
    }
    assert "add was renamed" in conversation.messages[0]
    assert "builder/agent-update" in conversation.messages[0]

    kinds = [one["kind"] for one in client.events]
    committed = kinds.index("checkpoint_committed")
    completed = client.events[-1]
    assert kinds[-1] == "turn_completed" and committed < len(kinds) - 1
    assert client.events[committed]["invocation_id"] == completed["invocation_id"]
    result = client.results[-1]
    assert result["nudge_id"] == "n-1" and result["checks"]["passed"]
    # The agent's source at /work/source is what the stale-review warning reads.
    assert "items-exist" in result["checks"]["warnings"][0]
    assert client.published, "the turn's changes are published before turn_completed"
    assert completed["payload"]["outcome"] == "success"
    assert completed["payload"]["usage"] == {
        "tokens_in": 40,
        "tokens_out": 6,
        "tokens_cached": 0,
        "usd": 0.25,
        "unpriced_turns": 0,
        "models": ["m2"],
    }
    assert runtime.stopping.is_set()


def test_a_suite_rebuild_turn_cannot_amend_the_contract_or_rename(
    tmp_path, monkeypatch
):
    from test_nudge_turn import _scenario

    from fi.alk.harness.proofs import PROOFS, load_proofs

    seen: dict = {}

    async def act(stage):
        seen["granted"] = set(stage.spec.granted())
        (tmp_path / "work" / PROOFS).unlink(missing_ok=True)
        seen["renamed"] = await _tool(stage, "scenarios", "submit_scenario").handler(
            _scenario("orders-a-big-mac", scenario_key="adds-a-big-mac")
        )
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": False, "notes": "fits"}
        )

    runtime, client, _conversation, asked = _rebuild(
        tmp_path, monkeypatch, "suite", act
    )

    granted = seen["granted"]
    assert {"mcp__scenarios__submit_scenario", "mcp__suite__prove_scenarios"} <= granted
    assert not {"mcp__scenarios__amend_contract", "mcp__world__save_world"} & granted
    assert (
        seen["renamed"]["is_error"]
        and "rename" in seen["renamed"]["content"][0]["text"]
    )
    assert load_proofs(tmp_path / "work")["adds-a-big-mac"]["holds"] is True
    assert client.results[-1]["stage"] == "suite" and asked == []
    assert client.events[-1]["payload"]["usage"]["tokens_in"] == 0
    assert runtime.stopping.is_set()


def test_a_failed_rebuild_turn_still_reports_usage_and_ends_the_sandbox(
    tmp_path, monkeypatch
):
    from fi.alk.harness import spend

    async def act(stage):
        spend.record("builder", 0.1, turns=1, models={"m2"}, tokens_in=5)
        raise RuntimeError("model unavailable")

    runtime, client, _conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "environment", act
    )

    completed = client.events[-1]
    assert (
        completed["kind"] == "turn_completed"
        and completed["payload"]["outcome"] == "failed"
    )
    assert completed["payload"]["usage"]["tokens_in"] == 5
    assert runtime.stopping.is_set()


def test_a_chat_turn_carries_no_usage_and_keeps_the_sandbox(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime.conversation = EditingConversation(tmp_path, writes=False)

    _turn(runtime, {"content": "hello"})

    assert "usage" not in client.events[-1]["payload"]
    assert not runtime.stopping.is_set()


def test_prove_scenarios_records_each_proof(tmp_path):
    from test_nudge_turn import _suite

    from fi.alk.harness.proofs import PROOFS, load_proofs
    from fi.alk.harness.scenario_tools import load_scenarios

    root = _suite(tmp_path, "adds-a-big-mac", "adds-one-big-mac")
    (root / PROOFS).unlink(missing_ok=True)
    server = builder.suite_server(root, load_scenarios(root))
    prove = next(one for one in server.tools if one.name == "prove_scenarios")

    said = asyncio.run(prove.handler({"names": ["adds-a-big-mac", "not-there"]}))

    proofs = load_proofs(root)
    assert "adds-a-big-mac: holds" in said["content"][0]["text"]
    assert set(proofs) == {"adds-a-big-mac"}
    assert (
        proofs["adds-a-big-mac"]["holds"] is True
        and proofs["adds-a-big-mac"]["proved_at"]
    )


def test_a_write_after_the_last_report_runs_the_checks_again(tmp_path, monkeypatch):
    from test_nudge_turn import _break_scenario

    async def act(stage):
        _called(stage, "report_update")
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": False, "notes": "fits"}
        )
        _called(stage, "submit_scenario")
        _break_scenario(tmp_path / "work", "adds-a-big-mac")
        _called(stage, "find_scenarios")

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "suite", act
    )

    result = client.results[-1]
    assert result["checks"]["passed"] is False
    assert [one["item"] for one in result["checks"]["problems"]] == ["adds-a-big-mac"]
    assert result["changed"] is True and result["notes"] == (
        "fits (changed after this report; checks re-run at the end of the turn)"
    )


def test_a_tool_added_without_saving_the_world_fails_the_report(
    tmp_path, monkeypatch
):
    from test_nudge_turn import _add_tool

    async def act(stage):
        _add_tool(tmp_path / "work", "send_otp")
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "added send_otp"}
        )

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "environment", act
    )

    result = client.results[-1]
    assert result["checks"]["passed"] is False
    assert [(one["item"], one["kind"]) for one in result["checks"]["problems"]] == [
        ("send_otp", "world")
    ]
    # The turn added it and no sub-goal names it: a prompt to look, not a gate.
    assert any("tool send_otp was added" in one for one in result["checks"]["warnings"])


def test_world_changes_fail_the_report_until_the_world_is_saved(tmp_path, monkeypatch):
    import test_nudge_turn

    from fi.alk.harness.simulator import save_simulator_prompt

    suite = test_nudge_turn._suite

    def _checked_suite(root: Path, *names: str) -> Path:
        suite(root, *names)
        test_nudge_turn._set_manifest(
            root,
            world_checks={
                "menu-has-items": "def check(world):\n"
                "    return None if world.state().get('menu') else 'no menu'\n"
            },
        )
        save_simulator_prompt(
            "You are a customer described by {{ persona }}. Follow {{ instruction }} in a "
            "natural multi-turn conversation. Never invent the assistant's responses or claim "
            "a tool result you have not heard. Ask a concise follow-up when needed and end "
            "once the task resolves.",
            root,
        )
        return root

    monkeypatch.setattr(test_nudge_turn, "_suite", _checked_suite)
    seen: dict = {}

    async def act(stage):
        seeded = await _tool(stage, "world", "seed").handler(
            {"table": "menu", "rows": [{"id": "fries"}]}
        )
        assert not seeded.get("is_error"), seeded
        seen["unsaved"] = await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "seeded fries"}
        )
        seen["saved"] = await _tool(stage, "world", "save_world").handler({})
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "seeded fries"}
        )

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "environment", act
    )

    assert seen["unsaved"].get("is_error")
    assert '"kind": "world"' in seen["unsaved"]["content"][0]["text"]
    assert not seen["saved"].get("is_error"), seen["saved"]
    assert client.results[-1]["checks"]["passed"] is True


_OLD_SEED_CHECKS = {
    "seed_matches_source": "def check(world):\n"
    "    ids = sorted(row['id'] for row in world.state().get('menu') or [])\n"
    "    return None if ids == ['big_mac'] else f'menu is {ids}'\n",
    "key_balances": "def check(world):\n"
    "    rows = world.state().get('wallet') or []\n"
    "    dana = [row['balance'] for row in rows if row['rider'] == 'dana']\n"
    "    return None if dana == [60.0] else f'dana holds {dana}'\n",
}
_NEW_SEED = (
    "INSERT INTO menu (id) VALUES ('big_mac');\n"
    "INSERT INTO menu (id) VALUES ('fries');\n"
    "INSERT INTO wallet (rider, balance) VALUES ('dana', 5.00);\n"
)


def _seed_rebuild(tmp_path, monkeypatch, act, checks=_OLD_SEED_CHECKS):
    """An environment rebuild whose world checks pin the old seed: big_mac only, dana 60."""
    import test_nudge_turn

    from fi.alk.harness.simulator import save_simulator_prompt
    from fi.alk.harness.world.snapshot import read_manifest, restore, save

    suite = test_nudge_turn._suite

    def _old_seed_suite(root: Path, *names: str) -> Path:
        suite(root, *names)
        world = restore(root)
        world.connection.executescript(
            "CREATE TABLE wallet(rider TEXT PRIMARY KEY, balance REAL);"
            "INSERT INTO wallet VALUES ('dana', 60.0);"
        )
        world.connection.commit()
        save(
            world,
            root,
            notes="old seed",
            sequences=list(read_manifest(root).get("sequences") or []),
            world_checks=checks,
        )
        save_simulator_prompt(
            "You are a customer described by {{ persona }}. Follow {{ instruction }} in a "
            "natural multi-turn conversation. Never invent the assistant's responses or claim "
            "a tool result you have not heard. Ask a concise follow-up when needed and end "
            "once the task resolves.",
            root,
        )
        return root

    monkeypatch.setattr(test_nudge_turn, "_suite", _old_seed_suite)
    return _rebuild(tmp_path, monkeypatch, "environment", act)


def _reseed(stage):
    async def run():
        seeded = await _tool(stage, "world", "seed").handler(
            {"table": "menu", "rows": [{"id": "fries"}]}
        )
        changed = await _tool(stage, "world", "change_data").handler(
            {"sql": "UPDATE wallet SET balance = 5.0 WHERE rider = 'dana'"}
        )
        assert not seeded.get("is_error") and not changed.get("is_error"), changed

    return run()


def test_a_seed_change_rewrites_the_world_checks_that_pin_the_old_seed(
    tmp_path, monkeypatch
):
    from fi.alk.harness.world.snapshot import read_manifest

    seen: dict = {}
    menu = (
        "def check(world):\n"
        "    ids = sorted(row['id'] for row in world.state().get('menu') or [])\n"
        "    return None if ids == ['big_mac', 'fries'] else f'menu is {ids}'\n"
    )
    balances = _OLD_SEED_CHECKS["key_balances"].replace("[60.0]", "[5.0]")

    async def act(stage):
        (tmp_path / "source" / "db").mkdir(parents=True)
        (tmp_path / "source" / "db" / "seed.sql").write_text(_NEW_SEED)
        seen["granted"] = set(stage.spec.granted())
        await _reseed(stage)
        seen["refused"] = await _tool(stage, "world", "save_world").handler({})
        for name, code, quote in (
            ("seed_matches_source", menu, "INSERT INTO menu (id) VALUES ('fries');"),
            ("key_balances", balances, "VALUES ('dana', 5.00);"),
        ):
            edited = await _tool(stage, "world", "add_world_check").handler(
                {
                    "name": name,
                    "code": code,
                    "source_path": "db/seed.sql",
                    "source_quote": quote,
                }
            )
            assert not edited.get("is_error"), edited
        seen["saved"] = await _tool(stage, "world", "save_world").handler({})
        seen["report"] = await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "new seed rows"}
        )

    _runtime, client, _conversation, _asked = _seed_rebuild(tmp_path, monkeypatch, act)

    assert "mcp__world__add_world_check" in seen["granted"]
    refused = seen["refused"]["content"][0]["text"]
    assert seen["refused"].get("is_error")
    assert "key_balances" in refused and "seed_matches_source" in refused
    assert not seen["saved"].get("is_error"), seen["saved"]
    assert not seen["report"].get("is_error"), seen["report"]
    checks = read_manifest(tmp_path / "work")["world_checks"]
    assert checks == {"seed_matches_source": menu, "key_balances": balances}
    result = client.results[-1]
    assert result["checks"]["passed"] is True
    assert result["checks"]["warnings"] == [
        "world checks changed: key_balances (cites db/seed.sql), "
        "seed_matches_source (cites db/seed.sql)"
    ]


def test_a_world_check_edit_that_cites_no_seed_change_is_refused(tmp_path, monkeypatch):
    from fi.alk.harness.world.snapshot import read_manifest

    seen: dict = {}
    weakened = (
        "def check(world):\n    return None if world.state().get('menu') else 'no'\n"
    )

    async def act(stage):
        (tmp_path / "source" / "db").mkdir(parents=True)
        (tmp_path / "source" / "db" / "seed.sql").write_text(
            "INSERT INTO menu (id) VALUES ('big_mac');\n"
        )
        await _reseed(stage)
        tool = _tool(stage, "world", "add_world_check")
        seen["uncited"] = await tool.handler(
            {"name": "seed_matches_source", "code": weakened}
        )
        seen["unchanged"] = await tool.handler(
            {
                "name": "seed_matches_source",
                "code": weakened,
                "source_path": "db/seed.sql",
                "source_quote": "INSERT INTO menu (id) VALUES ('fries');",
            }
        )
        seen["saved"] = await _tool(stage, "world", "save_world").handler({})
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "fries"}
        )

    _runtime, client, _conversation, _asked = _seed_rebuild(tmp_path, monkeypatch, act)

    assert seen["uncited"].get("is_error") and seen["unchanged"].get("is_error")
    assert "does not occur in db/seed.sql" in seen["unchanged"]["content"][0]["text"]
    assert seen["saved"].get("is_error")
    assert read_manifest(tmp_path / "work")["world_checks"] == _OLD_SEED_CHECKS
    result = client.results[-1]
    assert result["checks"]["passed"] is False
    assert not result["checks"]["warnings"]


def test_a_rebuild_that_leaves_out_a_seeded_row_fails_until_it_is_seeded(
    tmp_path, monkeypatch
):
    seen: dict = {}

    async def act(stage):
        (tmp_path / "source" / "db").mkdir(parents=True)
        (tmp_path / "source" / "db" / "seed.sql").write_text(
            "INSERT INTO menu (id) VALUES ('big_mac');\n"
            "INSERT INTO bookings (ref, status) VALUES ('UB7F3A2C91', 'matched');\n"
        )
        schema = await _tool(stage, "world", "create_schema").handler(
            {"sql": "CREATE TABLE bookings(ref TEXT PRIMARY KEY, status TEXT);"}
        )
        assert not schema.get("is_error"), schema
        for key in ("left_out", "seeded"):
            if key == "seeded":
                await _tool(stage, "world", "seed").handler(
                    {
                        "table": "bookings",
                        "rows": [{"ref": "UB7F3A2C91", "status": "matched"}],
                    }
                )
            saved = await _tool(stage, "world", "save_world").handler({})
            assert not saved.get("is_error"), saved
            seen[key] = await _tool(stage, "nudge", "report_update").handler(
                {"changed": True, "notes": key}
            )

    _runtime, client, conversation, _asked = _seed_rebuild(tmp_path, monkeypatch, act)

    assert "every row the agent's current seed inserts" in conversation.messages[0]
    assert seen["left_out"].get("is_error")
    left_out = seen["left_out"]["content"][0]["text"]
    assert "UB7F3A2C91" in left_out and "db/seed.sql" in left_out
    assert not seen["seeded"].get("is_error"), seen["seeded"]
    assert client.results[-1]["checks"]["passed"] is True


def test_a_seed_row_in_a_table_the_old_world_kept_empty_is_reproduced(
    tmp_path, monkeypatch
):
    from fi.alk.harness.world.snapshot import read_manifest

    seen: dict = {}
    empty_cart = {
        "seed_matches_source": "def check(world):\n"
        "    st = world.state()\n"
        "    if sorted(row['id'] for row in st.get('menu') or []) != ['big_mac']:\n"
        "        return 'menu is not the seed menu'\n"
        "    return 'cart is not empty at the start' if st.get('cart') else None\n"
    }
    seeded_cart = (
        "def check(world):\n"
        "    st = world.state()\n"
        "    if sorted(row['id'] for row in st.get('menu') or []) != ['big_mac']:\n"
        "        return 'menu is not the seed menu'\n"
        "    cart = [row['item_id'] for row in st.get('cart') or []]\n"
        "    return None if cart == ['big_mac'] else f'cart is {cart}'\n"
    )

    async def act(stage):
        (tmp_path / "source" / "db").mkdir(parents=True)
        (tmp_path / "source" / "db" / "seed.sql").write_text(
            "INSERT INTO menu (id) VALUES ('big_mac');\n"
            "INSERT INTO cart (item_id) VALUES ('big_mac');\n"
        )
        seeded = await _tool(stage, "world", "seed").handler(
            {"table": "cart", "rows": [{"item_id": "big_mac"}]}
        )
        assert not seeded.get("is_error"), seeded
        seen["refused"] = await _tool(stage, "world", "save_world").handler({})
        edited = await _tool(stage, "world", "add_world_check").handler(
            {
                "name": "seed_matches_source",
                "code": seeded_cart,
                "source_path": "db/seed.sql",
                "source_quote": "INSERT INTO cart (item_id) VALUES ('big_mac');",
            }
        )
        assert not edited.get("is_error"), edited
        seen["saved"] = await _tool(stage, "world", "save_world").handler({})
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "seeded the open cart"}
        )

    _runtime, client, _conversation, _asked = _seed_rebuild(
        tmp_path, monkeypatch, act, checks=empty_cart
    )

    assert seen["refused"].get("is_error")
    assert "seed_matches_source" in seen["refused"]["content"][0]["text"]
    assert not seen["saved"].get("is_error"), seen["saved"]
    manifest = read_manifest(tmp_path / "work")
    assert manifest["tables"]["cart"] == 1
    assert manifest["world_checks"] == {"seed_matches_source": seeded_cart}
    assert client.results[-1]["checks"]["passed"] is True


def test_a_report_after_the_last_write_stands(tmp_path, monkeypatch):
    async def act(stage):
        _called(stage, "submit_scenario")
        _called(stage, "report_update")
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": False, "notes": "fits"}
        )
        _called(stage, "check_health")

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "suite", act
    )

    result = client.results[-1]
    assert result["checks"]["passed"] is True and result["changed"] is False


def test_a_late_write_whose_checks_cannot_run_lands_no_result(tmp_path, monkeypatch):
    from fi.alk.harness import nudge

    async def act(stage):
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": True, "notes": "fits"}
        )
        _called(stage, "submit_scenario")

        def broken(*_args, **_kwargs):
            raise RuntimeError("gate failed")

        monkeypatch.setattr(nudge, "report_update", broken)

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "suite", act
    )

    assert client.results[-1] is None
    assert client.events[-1]["payload"]["outcome"] == "success"


def test_a_suite_rebuild_is_told_which_scenarios_are_the_persons(tmp_path, monkeypatch):
    async def act(stage):
        return None

    _runtime, _client, conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "suite", act, draft_keys=("adds-a-big-mac",)
    )

    assert conversation.messages[0].endswith(
        "Scenarios the person is still editing (keep what they wrote; only carry over a "
        "renamed tool or argument): adds-a-big-mac"
    )
    assert "No scenario is being edited" not in conversation.messages[0]


def test_a_suite_rebuild_with_no_draft_says_earlier_edits_have_landed(
    tmp_path, monkeypatch
):
    async def act(stage):
        return None

    _runtime, _client, conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "suite", act
    )

    assert conversation.messages[0].endswith(
        "No scenario is being edited by the person now: no list of edits in an earlier "
        "message applies any more, so repair every scenario this update breaks."
    )


def test_an_environment_rebuild_says_nothing_about_edits(tmp_path, monkeypatch):
    async def act(stage):
        return None

    _runtime, _client, conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "environment", act
    )

    assert "being edited" not in conversation.messages[0]
    assert "still editing" not in conversation.messages[0]


def test_a_suite_rebuild_can_add_a_scenario(tmp_path, monkeypatch):
    from test_nudge_turn import _scenario

    from fi.alk.harness.scenario_tools import load_scenarios

    seen: dict = {}

    async def act(stage):
        await _tool(stage, "scenarios", "aim_for").handler({"count": 2})
        seen["added"] = await _tool(stage, "scenarios", "submit_scenario").handler(
            _scenario("adds-one-big-mac")
        )

    _rebuild(tmp_path, monkeypatch, "suite", act)

    assert not seen["added"].get("is_error"), seen["added"]
    assert {one.name for one in load_scenarios(tmp_path / "work")} == {
        "adds-a-big-mac",
        "adds-one-big-mac",
    }


def test_a_replayed_rebuild_commits_the_workspace_holding_its_result(
    tmp_path, monkeypatch
):
    from fi.alk.harness.nudge import NUDGE_RESULT

    async def act(stage):
        raise AssertionError("a replayed turn does not run again")

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path,
        monkeypatch,
        "suite",
        act,
        stale_result=True,
        journal={"m-3": "completed"},
    )

    kinds = [one["kind"] for one in client.events]
    assert kinds[-2:] == ["checkpoint_committed", "turn_completed"]
    assert client.events[-1]["payload"]["replayed"] is True
    assert client.events[-2]["payload"]["digest"]
    assert client.results[-1] == {"nudge_id": "an-older-turn"}
    assert (tmp_path / "work" / NUDGE_RESULT).is_file()


@pytest.mark.parametrize("ending", ["raised", "turn_failures", "interrupted"])
def test_a_rebuild_turn_that_did_not_succeed_replays_as_failed(
    tmp_path, monkeypatch, ending
):
    async def act(stage):
        await _tool(stage, "nudge", "report_update").handler(
            {"changed": False, "notes": "fits"}
        )
        _called(stage, "submit_scenario")
        if ending == "raised":
            raise RuntimeError("model unavailable")
        if ending == "turn_failures":
            stage.emit_event(
                RuntimeEvent(kind="done", detail={"outcome": "failed", "error": "x"})
            )

    async def interrupted(self, turn, *, command_sequence):
        await turn
        return {"kind": "interrupt", "message_id": "m-4", "sequence": 4}

    if ending == "interrupted":
        monkeypatch.setattr(HostedChatRuntime, "_turn_until_interrupted", interrupted)

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path, monkeypatch, "suite", act, redeliver=True
    )

    journal = json.loads(
        (tmp_path / "work" / ".futureagi-conversation.json").read_text()
    )
    assert journal == {"m-3": "ended"}
    # The turn's last workspace holds no result, so nothing of it can land.
    assert client.results[-1] is None
    replay = client.events[client.redelivered_at :]
    assert [one["kind"] for one in replay] == ["turn_completed"]
    assert replay[0]["payload"] == {"command_message_id": "m-3", "outcome": "failed"}


def test_a_cut_off_rebuild_turn_keeps_no_result(tmp_path, monkeypatch):
    async def act(stage):
        raise AssertionError("a retried rebuild turn does not run again")

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path,
        monkeypatch,
        "suite",
        act,
        stale_result=True,
        journal={"m-3": "retried"},
    )

    journal = json.loads(
        (tmp_path / "work" / ".futureagi-conversation.json").read_text()
    )
    assert journal == {"m-3": "ended"} and client.results[-1] is None
    assert client.events[-1]["payload"]["interrupted"] is True


def test_a_resumed_rebuild_turn_starts_without_its_earlier_result(
    tmp_path, monkeypatch
):
    from fi.alk.harness.nudge import NUDGE_RESULT

    seen: dict = {}

    async def act(stage):
        seen["result_at_start"] = (tmp_path / "work" / NUDGE_RESULT).exists()
        _called(stage, "submit_scenario")

    _runtime, client, _conversation, _asked = _rebuild(
        tmp_path,
        monkeypatch,
        "suite",
        act,
        stale_result=True,
        journal={"m-3": "started"},
        resumed=True,
    )

    assert client.events[0]["payload"]["resumed"] is True
    # The report lived in the lost process: without a new one nothing lands.
    assert seen["result_at_start"] is False
    assert client.results[-1] is None


def test_aim_for_in_a_suite_rebuild_only_raises_the_count(tmp_path, monkeypatch):
    from fi.alk.harness.nudge import REBUILD_ADDS_AT_MOST, aim_refusal

    said: dict = {}

    async def act(stage):
        aim = _tool(stage, "scenarios", "aim_for").handler
        said["alike"] = await aim({"count": 2, "people": "alike"})
        said["asked"] = await aim({"count": 2, "asked_for": {"task": ["add"]}})
        said["far"] = await aim({"count": 2 + REBUILD_ADDS_AT_MOST})
        said["near"] = await aim({"count": 1 + REBUILD_ADDS_AT_MOST})

    _rebuild(tmp_path, monkeypatch, "suite", act)

    for refused in ("alike", "asked", "far"):
        assert said[refused].get("is_error"), said[refused]
    assert not said["near"].get("is_error"), said["near"]
    assert aim_refusal({"count": 2}, 3, 3)


@pytest.mark.parametrize("kind", ["builder", "coordinator"])
def test_a_stored_session_the_provider_will_not_resume_opens_fresh(kind):
    from fi.alk.harness.backends.base import ConversationSession, SessionSpec
    from fi.alk.harness.hosted_chat_entrypoint import CoordinatorConversation
    from fi.alk.harness.session import Stage

    starts: list = []

    class Session:
        def __init__(self, spec):
            self.resuming = spec.conversation.resume_session_id

        async def start(self):
            starts.append(self.resuming)
            if self.resuming:
                raise RuntimeError(f"No conversation found with session ID {self.resuming}")

        async def stop(self):
            pass

    class Backend:
        def create(self, spec):
            return Session(spec)

    conversation = ConversationSession(
        app_name="alk", user_id="u", session_id="s", resume_session_id="sdk-1"
    )
    stage = Stage(
        SessionSpec(system_prompt="", conversation=conversation),
        name=kind,
        backend=Backend(),
    )

    async def say(message, on_event=None):
        return f"heard {message}"

    stage.say = say  # type: ignore[method-assign]

    async def open_stage(*, resume=True):
        return stage

    chat = (
        BuilderConversation(open_stage)
        if kind == "builder"
        else CoordinatorConversation(stage)
    )
    assert asyncio.run(chat.say("hi")) == "heard hi"
    assert starts == ["sdk-1", None]
    assert conversation.resume_session_id is None


def test_reading_a_run_can_wait_until_it_has_finished(tmp_path, monkeypatch):
    client = BuilderClient()
    states = iter(["running", "running", "completed"])

    async def runs(run_id, limit):
        if run_id == "unknown":
            return {"error": "no Run with that id belongs to this environment"}
        return {"run_id": run_id, "state": next(states), "calls": []}

    async def no_sleep(_delay):
        return None

    client.runs = runs
    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    runtime = _runtime(tmp_path, client, monkeypatch)
    read = next(spec for spec in runtime._builder_platform_server().tools if spec.name == "read_runs").handler

    result = asyncio.run(read({"run_id": "r-1", "wait": True}))

    assert '"completed"' in result["content"][0]["text"]
    assert "no Run" in asyncio.run(read({"run_id": "unknown", "wait": True}))["content"][0]["text"]


def test_waiting_on_a_run_never_outlasts_the_turn_it_waits_in():
    from fi.alk.harness.hosted_chat_entrypoint import _RUN_POLL_SECONDS, _RUN_WAIT_SECONDS
    from fi.alk.harness.hosted_conversation import _UNAVAILABLE_BUDGET_SECONDS
    from fi.alk.harness.session import STAGE_IDLE_TIMEOUT_SECONDS

    assert _RUN_WAIT_SECONDS + _RUN_POLL_SECONDS + _UNAVAILABLE_BUDGET_SECONDS < STAGE_IDLE_TIMEOUT_SECONDS


def test_the_build_and_its_check_change_without_asking_and_a_question_gets_no_one(tmp_path, monkeypatch):
    from fi.alk.harness.backends import ToolSpec

    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    calls = []

    async def handler(args):
        calls.append(args)
        return {"content": [{"type": "text", "text": "kept"}]}

    (tmp_path / "scenarios" / "one").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scenarios" / "one" / "scenario.json").write_text(
        json.dumps(
            {
                "name": "one",
                "branch": "the account is on hold",
                "instruction": "Order one Big Mac. Ask whether it is safe for a peanut allergy first.",
                "fixture": {"allergy": "peanut"},
                "persona": {"name": "Asha", "gender": "female", "languages": ["French"]},
                "solution": [{"tool": "a"}],
            }
        )
    )
    drop = runtime._confirmed(ToolSpec("drop_scenarios", "", {}, handler))
    submit = runtime._confirmed(ToolSpec("submit_scenario", "", {}, handler))
    runtime._from_platform = True
    person = {"name": "Asha", "gender": "female", "languages": ["French"]}
    corrected = {"name": "one", "branch": "the account is on hold", "persona": person, "solution": [{"tool": "b"}]}
    assert asyncio.run(submit.handler(corrected)).get("is_error") is None
    assert asyncio.run(submit.handler({**corrected, "branch": "a lost item"}))["is_error"] is True
    for situation in (
        {"instruction": "Order one Big Mac. Do not ask about allergies."},
        {"fixture": {"allergy": "none"}},
        {"persona": {"name": "Ravi", "gender": "male"}},
        {"persona": {**person, "languages": ["English"]}},
        {"persona": None},
    ):
        assert asyncio.run(submit.handler({**corrected, **situation}))["is_error"] is True
    assert asyncio.run(drop.handler({"names": ["one"]}))["is_error"] is True
    answer = asyncio.run(runtime._ask("AskUserQuestion", {"question": "Which?"}, None))
    assert "Nobody answers" in answer["content"][0]["text"]
    assert calls == [corrected]


def _two_agent_world(tmp_path: Path, folder: str):
    """A saved environment whose contract records ``folder``, over a repository of two agents."""
    from test_nudge_turn import _suite

    from fi.alk.harness.contract import Runtime
    from fi.alk.harness.scenario_tools import load_scenarios
    from fi.alk.harness.understand import load

    root = _suite(tmp_path / "ws", "adds-a-big-mac")
    source = tmp_path / "source"
    for name in ("a", "b"):
        (source / "agents" / name).mkdir(parents=True)
        (source / "agents" / name / "agent.py").write_text(
            f"def cancel_{name}():\n    return 'ok'\n", encoding="utf-8"
        )
    contract = load(root)
    if folder:
        contract.runtime = Runtime(source_subfolder=folder, workdir=folder)
    return root, source, contract, load_scenarios(root)


def test_the_builder_is_told_which_folder_is_the_agent(tmp_path):
    root, source, contract, _ = _two_agent_world(tmp_path, "agents/a")

    named = builder.open_builder(contract, out=root, source_root=source)
    contract.runtime = None
    whole = builder.open_builder(contract, out=root, source_root=source)

    assert "The agent lives in its folder agents/a" in named.spec.system_prompt
    assert "The agent lives in its folder" not in whole.spec.system_prompt


def test_declared_tool_evidence_must_be_in_the_agents_folder(tmp_path):
    root, source, contract, kept = _two_agent_world(tmp_path, "agents/a")
    server = builder.suite_server(root, kept, contract, source)
    declare = next(one for one in server.tools if one.name == "declare_tool")

    def declared(path: str, name: str) -> dict:
        return asyncio.run(
            declare.handler(
                {
                    "name": name,
                    "description": "cancel",
                    "evidence_file": path,
                    "evidence_quote": f"def {name}():",
                    "why": "the agent has it",
                }
            )
        )

    sibling = declared("agents/b/agent.py", "cancel_b")
    own = declared("agents/a/agent.py", "cancel_a")

    assert sibling["is_error"] and "agents/a" in sibling["content"][0]["text"]
    assert not own.get("is_error"), own


# -- Change review r2, A-H1: what the chat sends the platform loses URL credentials ------------------

_SECRET_URL = "https://bot:hunter2@hooks.example/v1?key=sk-1#frag"
_SEEN = "https://hooks.example/v1"
_LEAKS = ("hunter2", "bot:", "sk-1", "key=", "frag", "hun", "ter2")


def _leaks(events: list[dict]) -> list[dict]:
    return [
        one
        for one in events
        if any(leak in json.dumps(one.get("payload"), ensure_ascii=False) for leak in _LEAKS)
    ]


def _deltas(client: BuilderClient) -> list[str]:
    return [one["payload"]["text"] for one in client.events if one["kind"] == "assistant_delta"]


def test_every_event_the_pump_sends_loses_url_credentials(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    queue: asyncio.Queue = asyncio.Queue()
    pieces = [
        # One URL split three ways, inside its "://" and inside its password.
        "Your agent posts to https:",
        "//bot:hun",
        "ter2@hooks.example/v1?key=sk-1#frag. It also ",
        "reads https://bot:hunter2@hooks.example/v2",
    ]
    for piece in pieces[:3]:
        queue.put_nowait(RuntimeEvent(kind="text", text=piece, detail={"partial": True}))
    queue.put_nowait(RuntimeEvent(kind="tool", tool="mcp__x__Read", detail={"call_id": "r"}))
    queue.put_nowait(
        RuntimeEvent(kind="result", text=f"agent.py:3: HOOK = '{_SECRET_URL}'", detail={"call_id": "r"})
    )
    queue.put_nowait(
        RuntimeEvent(kind="artifact", detail={"path": "https://bot:hunter2@hooks.example/out/c.json"})
    )
    queue.put_nowait(RuntimeEvent(kind="text", text=pieces[3], detail={"partial": True}))
    queue.put_nowait(None)
    said: list[str] = []

    asyncio.run(
        runtime._pump_events(
            queue,
            assistant_message_id="a-1",
            fallback_invocation_id="turn-1",
            emitted_text=said,
            turn_failures=[],
        )
    )

    assert _leaks(client.events) == []
    assert "".join(_deltas(client)) == (
        f"Your agent posts to {_SEEN} It also reads https://hooks.example/v2"
    )
    # The text after the last space is held until the message ends, then sent.
    assert client.events[-1]["kind"] == "assistant_delta"
    assert client.events[-1]["payload"]["text"] == "https://hooks.example/v2"
    results = [one["payload"] for one in client.events if one["kind"] == "tool_result"]
    assert results[0]["text"] == f"agent.py:3: HOOK = '{_SEEN}"
    assert results[1] == {"artifact": "https://hooks.example/out/c.json"}
    # The turn's own record of what was said stays as said; only what is sent is cleaned.
    assert said == pieces


class _Saying(EditingConversation):
    def __init__(self, workspace: Path, events: list[RuntimeEvent]):
        super().__init__(workspace, writes=False)
        self.events = events

    async def say(self, message, on_event=None):
        for event in self.events:
            on_event(event)


@pytest.mark.parametrize("ending", ["success", "interrupted", "failed"])
def test_the_turns_assistant_message_loses_url_credentials(tmp_path, monkeypatch, ending):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()
    events = [
        RuntimeEvent(kind="text", text="Saved https://bot:hun", detail={"stage": "builder"}),
        RuntimeEvent(kind="text", text="ter2@hooks.example/v1?key=sk-1#frag for you.", detail={}),
    ]
    if ending == "failed":
        events.append(
            RuntimeEvent(kind="done", detail={"outcome": "failed", "error": f"provider said {_SECRET_URL}"})
        )
    if ending == "interrupted":

        async def interrupted(self, turn, *, command_sequence):
            await turn
            return {"kind": "interrupt", "message_id": "m-4", "sequence": 4}

        monkeypatch.setattr(HostedChatRuntime, "_turn_until_interrupted", interrupted)
    runtime.conversation = _Saying(tmp_path, events)

    _turn(runtime, {"content": "save it"})

    assert _leaks(client.events) == []
    assert "".join(_deltas(client)) == f"Saved {_SEEN} for you."
    expected = {
        "success": f"Saved {_SEEN} for you.",
        "interrupted": f"Saved {_SEEN} for you.",
        "failed": f"I couldn't complete that turn: provider said {_SEEN}",
    }[ending]
    assert _reply(client) == expected


def test_a_question_the_model_asks_loses_url_credentials(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime.stopping.set()
    question = f"Should the hook stay {_SECRET_URL} ?"
    options = [{"label": "keep", "description": f"post to {_SECRET_URL}"}, "drop it"]

    asyncio.run(runtime._ask("AskUserQuestion", {"question": question, "options": options}, None))

    asked = next(one for one in client.events if one["kind"] == "question_requested")
    assert asked["payload"] == {
        "prompt": f"Should the hook stay {_SEEN} ?",
        "options": [f"keep: post to {_SEEN}", "drop it"],
    }
    # The file a resumed turn matches against keeps the question as asked.
    pending = json.loads((tmp_path / ".futureagi-pending-question.json").read_text())
    assert pending["question"] == question


def test_the_authoring_drain_sends_its_reply_cleaned(monkeypatch):
    from fi.alk.harness import cli

    class DrainClient:
        command_watermark = 0

        def __init__(self):
            self.events: list[dict] = []

        async def commands(self):
            return [{"sequence": 1, "message_id": "c-1", "payload": {"content": "where?"}}]

        async def emit(self, kind, **kwargs):
            self.events.append({"kind": kind, **kwargs})

    class Stage:
        async def say(self, content, on_event):
            on_event(RuntimeEvent(kind="text", text=f"It posts to {_SECRET_URL} today."))

    client = DrainClient()
    monkeypatch.delenv("ALK_FOREGROUND_COORDINATOR", raising=False)
    monkeypatch.setattr(cli, "_AUTHORING_CLIENT", client)

    asyncio.run(cli._drain_authoring_commands(Stage()))

    reply = next(one for one in client.events if one["kind"] == "assistant_message")
    assert reply["payload"]["text"] == f"It posts to {_SEEN} today."
    assert _leaks(client.events) == []


# -- The holdback on its own: bounded, and nothing of a URL's credentials let out --------------------


def _stream(text: str, size: int):
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    cleaner = _DeltaCleaner()
    out: list[str] = []
    most = 0
    for start in range(0, len(text), size):
        out.append(cleaner.feed(text[start : start + size]))
        most = max(most, len(cleaner.held))
    out.append(cleaner.flush())
    return "".join(out), most


def test_a_long_run_with_no_space_is_shed_without_its_credentials():
    from fi.alk.harness.hosted_chat_entrypoint import _HOLD_HARD
    from fi.alk.providers.agent import _redact

    text = "x" * 50_000 + _SECRET_URL + "y" * 50_000 + " and after."
    assert len(text) > 100_000

    out, most = _stream(text, 7)

    assert most <= _HOLD_HARD
    assert "hunter2" not in out and "sk-1" not in out and "bot:" not in out
    # The query owns the rest of its token, so the ys go with it, as cleaning the whole drops them.
    assert out == _redact(text) == "x" * 50_000 + _SEEN + " and after."


def test_a_url_longer_than_the_hard_cap_is_sent_as_redacted():
    from fi.alk.harness.hosted_chat_entrypoint import _HOLD_HARD

    text = "Before https://h.example/" + "p" * _HOLD_HARD + "?key=sk-1&pw=hunter2 after."

    out, most = _stream(text, 100)

    assert most <= _HOLD_HARD
    assert out == "Before [redacted] after."


def test_prose_around_a_streamed_url_is_kept():
    from fi.alk.providers.agent import _redact

    text = "Read the docs at https://u:hunter2@docs.example/a?x=sk-1 then\tcall us.\n"

    out, _most = _stream(text, 3)

    assert out == _redact(text) == "Read the docs at https://docs.example/a then\tcall us.\n"


def test_a_message_that_is_one_url_broken_by_a_line_break_is_cleaned_whole():
    # The cleaner joins a value that is one URL broken by a line break, so no cut is made there.
    out, _most = _stream("https://bot:hun\nter2@hooks.example/v1?key=sk-1", 5)

    assert out == _SEEN


def test_a_scheme_split_at_its_colon_is_held_over_a_shed():
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    cleaner = _DeltaCleaner()
    # Past the soft bound with no URL start, so it is shed, all but the ":".
    out = cleaner.feed("z" * 5_000 + "https:")
    out += cleaner.feed("//bot:hunter2@hooks.example/v1 done") + cleaner.flush()

    assert out == "z" * 5_000 + "https://hooks.example/v1 done"


def test_a_shed_inside_a_query_drops_the_rest_of_the_token():
    text = "see https://h.example/cb?a=1&next=https://x.example/y&token=sk-1" + "z" * 5_000 + " ok"

    out, _most = _stream(text, 9)

    assert out == "see https://h.example/cb ok"


# -- Change review r2, coordinator items: cuts at any whitespace, and nothing lost past the bound ----


@pytest.mark.parametrize("gap", ["\t", "\n"], ids=["tab", "line_break"])
def test_a_tab_or_a_line_break_releases_the_held_text(gap):
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    cleaner = _DeltaCleaner()

    out = cleaner.feed(f"See https://bot:hunter2@hooks.example/v1?key=sk-1{gap}next")

    assert out == f"See {_SEEN}{gap}"
    assert cleaner.held == "next"


def test_a_message_with_no_space_is_cut_at_a_line_break_once_its_opening_is_prose():
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    cleaner = _DeltaCleaner()

    out = cleaner.feed(f"Steps:\n1.{_SECRET_URL}\nnext")

    assert out == f"Steps:\n1.{_SEEN}\n"
    assert cleaner.held == "next"


def test_a_line_break_after_a_streamed_url_is_kept():
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner
    from fi.alk.providers.agent import _redact

    pieces = ["Read\n", "https://u:hunter2@docs.example/a\n", "ok"]

    cleaner = _DeltaCleaner()
    out = "".join(cleaner.feed(piece) for piece in pieces) + cleaner.flush()

    assert out == _redact("".join(pieces)) == "Read\nhttps://docs.example/a\nok"


@pytest.mark.parametrize(
    "text",
    [
        "Start https://u:hunter2@h.example/" + "p" * 5_000 + "?key=sk-1" + "q" * 100 + " and the rest.",
        "Start " + "x" * 5_000 + "https://u:hunter2@h.example/path and the rest.",
    ],
    ids=["with_a_query", "after_a_long_word"],
)
def test_past_the_soft_bound_a_held_url_is_sent_cleaned_and_nothing_else_is_lost(text):
    """What is shed is the text cleaned, as cleaning the whole would give: the host and path go
    out, only the password and query are lost, and the words after the URL are kept."""
    from fi.alk.providers.agent import _redact

    out, _most = _stream(text, 50)

    assert out == _redact(text)
    assert "hunter2" not in out and "sk-1" not in out
    assert "h.example/" in out and out.endswith(" and the rest.")


# -- Change review r3, B-H1 and A-M1/M2: a message that may be one URL broken by a line break ---
#
# The cleaner joins a value that begins with a scheme and :// and has no whitespace but tabs and
# line breaks. The stored reply is the model's text stripped, so the stream must never send what
# cleaning that stripped whole drops, however it is split.

_PAD = "https://docs.example/" + "a" * 5_000
_ONE_URL_SHAPES = {
    # B-H1: past the soft bound, the kept text starts at a URL start that is not a letter.
    "digit_dot_start_tab_in_password": _PAD + "/1.https://bot:hun\tter2@hooks.example/v1?key=sk-1",
    "digit_dot_start_break_before_query": (
        _PAD + "/1.https://bot:hunter2@hooks.example/v1\n?key=sk-1"
    ),
    "schemeless_start_break_in_password": _PAD + "/://bot:hun\nter2@hooks.example/v1?key=sk-1",
    "dash_start_break_before_userinfo": _PAD + "/-https://\nbot:hunter2@hooks.example/v1",
    # A-M1, shape 2: whitespace before the first character and after the last.
    "leading_line_breaks": "\n\nhttps://bot:hun\nter2@hooks.example/v1\n?key=sk-1",
    "leading_space": " \thttps://bot:hun\nter2@hooks.example/v1?key=sk-1",
    "trailing_spaces": "x://bot:PW1@localho\tst:443/#FS1  ",
    "trailing_space_after_a_query": "https://bot:hun\nter2@hooks.example/v1?key=sk-1 \n",
    # A-M1, shape 3: an opening run of scheme letters longer than the soft bound.
    "long_scheme_run": (
        "a" * 4_097 + "https://U:PW2@hooks.exampl\te/x.json?key=QS1/获取数据x://y"
    ),
    "long_digit_led_run": "x." * 3_000 + "x://10.\t0.0.1/a/b?key=sk-1",
    # A held text that starts at its :// after a shed, and grows past the bound before its query.
    "schemeless_held_past_the_bound": (
        "a" * 4_097 + "://bot:hun\tter2@hooks.example/" + "p" * 4_200 + "?key=sk-1"
    ),
    # Round 2's W1 case, for the record.
    "one_url_broken_by_a_line_break": "https://bot:hun\nter2@hooks.example/v1?key=sk-1",
    # Fix check, B-M2: the spaced shed of a held lone URL must clean what it lets out.
    "lone_url_shed_at_a_space": "https://bot:hun\nter2@h.example/" + "p" * 4_200 + " ",
    # Fix check, B-M3: dropping a held query ends only at a space while prose isn't known.
    "dropped_query_runs_past_a_line_break": "https://h.example/" + "p" * 4_200 + "?k=v\nQS9tail",
}
_ONE_URL_SECRETS = (
    "hunter2", "hun\t", "hun\n", "ter2", "sk-1", "key=", "bot:", "PW1", "PW2", "FS1", "QS1",
    "QS9",
)


@pytest.mark.parametrize(
    "size", [None, 1, 2, 3, 7, 50], ids=lambda size: f"size_{size or 'whole'}"
)
@pytest.mark.parametrize("name", list(_ONE_URL_SHAPES))
def test_a_message_that_may_be_one_url_streams_as_its_stripped_whole_is_cleaned(name, size):
    from fi.alk.providers.agent import _redact

    text = _ONE_URL_SHAPES[name]

    out, _most = _stream(text, size or len(text))

    reply = _redact(text.strip())
    assert [secret for secret in _ONE_URL_SECRETS if secret in out] == []
    assert out.strip() == reply


@pytest.mark.parametrize(
    "text",
    [
        # A1: a shed of a run holding two URL starts, the first with userinfo and no query.
        "Mirror at https://bot:hunter2@hooks.example/"
        + "a" * 5_000
        + "https://cdn.example/x then more",
        # A4: the shed with no URL start keeps a trailing ":/".
        "Blob " + "b" * 4_100 + "https://bot:hunter2@hooks.example/v1 then more",
    ],
    ids=["two_starts_in_one_shed", "scheme_tail_held_over_a_shed"],
)
@pytest.mark.parametrize("split", ["size_7", "after_the_scheme_tail"])
def test_each_shed_sends_what_cleaning_the_whole_sends(text, split):
    """Change review r3, A-M2: each must equal the whole, so a one-token plant in _shed fails."""
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner
    from fi.alk.providers.agent import _redact

    if split == "size_7":
        out, _most = _stream(text, 7)
    else:
        cut = text.rindex("https:/") + len("https:/")
        cleaner = _DeltaCleaner()
        out = cleaner.feed(text[:cut]) + cleaner.feed(text[cut:]) + cleaner.flush()

    assert "hunter2" not in out and "bot:" not in out
    assert out == _redact(text)


def test_a_shed_splits_a_run_where_cleaning_the_whole_splits_it():
    """Each URL starts at its scheme's first letter, as _redact_token starts it, so a digit or
    dot before the letter stays with the URL before (here, as part of its port)."""
    from fi.alk.providers.agent import _redact

    text = "See https://h.example:80" + "1.ftp://files.example/" + "p" * 4_200 + " then"

    out, _most = _stream(text, 7)

    assert out == _redact(text) == "See https://h.example" + "ftp://files.example/" + "p" * 4_200 + " then"


def test_a_shed_finds_a_query_only_after_the_first_url_start():
    """A ? before the URL is not its query, so the shed keeps the URL and the rest of the token
    (reviewer A's plant A2 searched from the token's start, and dropped them)."""
    from fi.alk.providers.agent import _redact

    text = "Ask q?" + "/" * 100 + "https://h.example/" + "p" * 4_200 + " then"

    out, _most = _stream(text, 7)

    assert out == _redact(text) == text


def test_a_space_after_a_long_url_ends_its_token_before_prose_is_known():
    """A space with nothing after it yet doesn't make the reply prose, but nothing after it can
    join the token before it, so a shed there must not drop the next word."""
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner
    from fi.alk.providers.agent import _redact

    pieces = ["https://bot:hunter2@h.example/" + "p" * 4_200 + "?key=sk-1 ", "then more"]

    cleaner = _DeltaCleaner()
    out = "".join(cleaner.feed(piece) for piece in pieces) + cleaner.flush()

    assert out == _redact("".join(pieces)) == "https://h.example/" + "p" * 4_200 + " then more"


def test_text_held_before_the_reply_is_known_to_be_prose_is_cut_with_it():
    """What was held while the reply might be one URL keeps its line breaks once it is prose."""
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner
    from fi.alk.providers.agent import _redact

    # The long run comes in a delta of its own, so it is held past the soft bound with the text
    # before it, and shed.
    pieces = ["https://h.example/a\n1.https://h.example/b\n ", "中" * 5_000, " end"]

    cleaner = _DeltaCleaner()
    out = "".join(cleaner.feed(piece) for piece in pieces) + cleaner.flush()

    assert out == _redact("".join(pieces)) == "".join(pieces)


class _SayingAndWriting(_Saying):
    """Says its pieces and leaves one scenario unsaved, so the reply gains the save note."""

    async def say(self, message, on_event=None):
        (self.workspace / "scenarios" / "late_driver").mkdir(parents=True)
        (self.workspace / "scenarios" / "late_driver" / "scenario.json").write_text("{}")
        await super().say(message, on_event)


_BROKEN_URL_PIECES = ["https://bot:hun", "\nter2@hooks.example/v1", "?key=sk-1"]


def _texts(pieces: list[str], stage: str = "builder") -> list[RuntimeEvent]:
    return [RuntimeEvent(kind="text", text=piece, detail={"stage": stage}) for piece in pieces]


def test_the_reply_is_cleaned_before_the_save_note_and_the_health_lines_are_added(
    tmp_path, monkeypatch
):
    """Change review r3, A-M1 shape 1: notes added first made a one-URL reply prose."""
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()
    monkeypatch.setattr("fi.alk.harness.bundle_author_v2.reseal_scenarios", lambda root: None)
    runtime.conversation = _SayingAndWriting(tmp_path, _texts(_BROKEN_URL_PIECES))
    runtime._last_health = f"The hook check failed for {_SECRET_URL}"

    _turn(runtime, {"content": "save it"})

    assert _leaks(client.events) == []
    assert "".join(_deltas(client)) == _SEEN
    assert _reply(client) == (
        f"{_SEEN}\n\nSaved to the environment: added #41 late_driver. It now has 41 scenarios."
        f"\n\nStill open from the last health check (1): The hook check failed for {_SEEN}"
    )


def test_the_save_note_is_cleaned_on_its_own(tmp_path, monkeypatch):
    class NamingClient(BuilderClient):
        async def publish(self, digest):
            result = await super().publish(digest)
            result["added"] = [{"number": 41, "name": f"posts to {_SECRET_URL}"}]
            return result

    client = NamingClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()
    monkeypatch.setattr("fi.alk.harness.bundle_author_v2.reseal_scenarios", lambda root: None)
    runtime.conversation = _SayingAndWriting(tmp_path, _texts(["Added it."]))

    _turn(runtime, {"content": "save it"})

    assert _leaks(client.events) == []
    # The full stop after #frag is part of the fragment, so it goes too.
    assert _reply(client) == (
        f"Added it.\n\nSaved to the environment: added #41 posts to {_SEEN} It now has 41 scenarios."
    )


def test_a_failed_reseal_sends_the_reply_cleaned_before_its_note(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()

    def fails(root):
        raise RuntimeError("seed import failed")

    monkeypatch.setattr("fi.alk.harness.bundle_author_v2.reseal_scenarios", fails)
    runtime.conversation = _SayingAndWriting(tmp_path, _texts(_BROKEN_URL_PIECES))

    _turn(runtime, {"content": "save it"})

    reply = next(e for e in client.events if e["kind"] == "assistant_message")
    assert _leaks(client.events) == []
    assert reply["payload"]["error"] == "reseal_failed"
    assert reply["payload"]["text"].startswith(f"{_SEEN}\n\nThese changes were NOT saved")


@pytest.mark.parametrize(
    ("error", "shown"),
    [
        ("https://bot:hun\nter2@hooks.example/v1?key=sk-1", _SEEN),
        # Cut first at 400, the password 4242 would be kept as the address's port.
        ("x" * 383 + " https://bot:4242@hooks.example/v1", ("x" * 383 + " " + _SEEN)[:400]),
        # Fix check B-M1: whitespace around a lone line-broken URL is stripped before cleaning.
        ("\n\nhttps://bot:hun\nter2@hooks.example/v1\n?key=sk-1 \n", _SEEN),
    ],
    ids=[
        "one_url_broken_by_a_line_break",
        "cut_right_after_a_numeric_password",
        "whitespace_around_one_line_broken_url",
    ],
)
def test_a_failed_stage_cleans_its_detail_whole_before_cutting_it(
    tmp_path, monkeypatch, error, shown
):
    """Change review r3, A-M1 and round 2's L2: the detail is cleaned before the prefix and cut."""
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()
    events = [RuntimeEvent(kind="done", detail={"outcome": "failed", "error": error})]
    runtime.conversation = _Saying(tmp_path, events)

    _turn(runtime, {"content": "go"})

    reply = next(e for e in client.events if e["kind"] == "assistant_message")
    assert reply["payload"]["text"] == f"I couldn't complete that turn: {shown}"
    assert "4242" not in reply["payload"]["text"] and _leaks(client.events) == []


@pytest.mark.parametrize("ending", ["success", "interrupted"])
def test_whitespace_around_a_one_url_reply_leaves_the_stream_as_clean_as_the_reply(
    tmp_path, monkeypatch, ending
):
    """Change review r3, A-M1 shape 2: the reply is stripped, so the stream is read as it is.
    Fix check B-M4: the interrupted ending strips its reply too."""
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()
    pieces = ["\n\nhttps://bot:hun", "\nter2@hooks.example/v1", "\n?key=sk-1", " \n"]
    if ending == "interrupted":

        async def interrupted(self, turn, *, command_sequence):
            await turn
            return {"kind": "interrupt", "message_id": "m-4", "sequence": 4}

        monkeypatch.setattr(HostedChatRuntime, "_turn_until_interrupted", interrupted)
    runtime.conversation = _Saying(tmp_path, _texts(pieces))

    _turn(runtime, {"content": "go"})

    assert _leaks(client.events) == []
    assert "".join(_deltas(client)).strip() == _reply(client) == _SEEN


def _fuzzed_message(rng) -> tuple[str, list[str]]:
    secrets: list[str] = []

    def secret(kind: str) -> str:
        value = f"{kind}{rng.randrange(10**6, 10**7)}Z"
        secrets.append(value)
        return value

    def url() -> str:
        scheme = rng.choice(["https", "x", "postgres", "1.https", "-https", "", "${S}", "a.b-c"])
        parts = [scheme, "://"]
        if rng.random() < 0.7:
            parts.append(f"{rng.choice(['bot', secret('UU')])}:{secret('PW')}@")
        parts.append(rng.choice(["hooks.example", "h", "[::1]", "10.0.0.1"]))
        parts.append(rng.choice(["", "/", "/v1", "/a/b"]))
        if rng.random() < 0.5:
            parts.append(f"?key={secret('QS')}")
        if rng.random() < 0.2:
            parts.append(f"#{secret('FS')}")
        text = "".join(parts)
        if rng.random() < 0.5:
            at = rng.randrange(1, len(text))
            text = text[:at] + rng.choice(["\n", "\t", "\r\n"]) + text[at:]
        return text

    pieces = [rng.choice(["", "", "\n", " ", "\t\n"])]
    if rng.random() < 0.15:
        # An opening past the soft bound with no space, to reach every shed.
        pieces.append(rng.choice(["a", "x.", "/a", "a\n"]) * rng.randrange(2_100, 2_600))
    for _ in range(rng.randrange(1, 6)):
        word = rng.choice(["see", "/", "-", "1.", "://", ","])
        pieces.append(url() if rng.random() < 0.6 else word)
        if rng.random() < 0.3:
            pieces.append(rng.choice([" ", "\n", "\t", "\xa0"]))
    pieces.append(rng.choice(["", "", " ", "\n", "  \n"]))
    return "".join(pieces), secrets


def _random_pieces(text: str, rng) -> list[str]:
    cuts = set(rng.sample(range(1, len(text)), min(len(text) - 1, rng.randrange(0, 20))))
    cuts |= {
        at + rng.randrange(2)
        for at, char in enumerate(text)
        if char in ":@?#\n\t" and rng.random() < 0.5
    }
    cuts = sorted(at for at in cuts if 0 < at < len(text))
    return [text[start:end] for start, end in zip([0, *cuts], [*cuts, len(text)]) if start < end]


def test_random_splits_never_send_a_credential_the_stripped_whole_drops():
    """Change review r3: a bounded random-split fuzz. Whatever the split, no secret leaves the
    stream unless cleaning the stripped whole keeps it too (case aside: the whole lowers a
    scheme that a shed already let out)."""
    import random

    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner
    from fi.alk.providers.agent import _redact

    rng = random.Random(9491)
    found: list[tuple[str, list[str]]] = []
    for _ in range(600):
        text, secrets = _fuzzed_message(rng)
        if not text.strip():
            continue
        cleaner = _DeltaCleaner()
        out = "".join(cleaner.feed(piece) for piece in _random_pieces(text, rng)) + cleaner.flush()
        whole = _redact(text.strip()).lower()
        shown = out.lower()
        leaked = [one for one in secrets if one.lower() in shown and one.lower() not in whole]
        if leaked:
            found.append((text, leaked))
    assert found == []


# -- TH-9500: the hold rule for secrets matched across whitespace (plan v1.1, C1-C4) --------------
# Every key-shaped value is fake. The cases are the design reviewers' lists, copied as data.

_GH = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
_SKP = "sk-proj-" + "FAKEfake0123456789abcdefFAKEfake0123456789ab"
_AK = "AKIA" + "FAKEFAKE12345678"
_XB = "xoxb-12345678901-12345678901-" + "FAKEfakeFAKEfakeFAKEfake"
_SLACK = "https://hooks.slack.com/services/" + "T00FAKE00/B00FAKE00/" + "FAKEfakeFAKEfakeFAKEfake"
_TG = "https://api.telegram.org/bot123456789:AAFAKEfakeFAKEfakeFAKEfakeFAKEfake_x/sendMessage"
_JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKEsigFAKEsig"
_PEM = "-----BEGIN RSA PRIVATE KEY-----\nMIIEFAKEFAKEFAKE\nABCDEFFAKE==\n-----END RSA PRIVATE KEY-----"
# Round 1's cases (r1cases.py) and the addendum's three: round 2's 1,919 stream splits.
_ROUND1 = {
    "page: ws in password": "https://bot:hun ter2@h/v1",
    "page: lb in password": "see https://bot:hun\n    ter2@h/v1 is the hook",
    "page: tab in password": "see https://bot:hun\tter2@h/v1 is the hook",
    "page: query next line": "see https://u:pw@h/x\n  ?key=sk-1 and more",
    "page: ?# in password": "https://bot:8812#x@db/",
    "page: / in password": "https://bot:pa/ss@db",
    "page: :// in password": "https://bot:pa://ss@db",
    "bare ghp prose": f"Use {_GH} to push.",
    "bare sk-proj prose": f"key is {_SKP}.",
    "bare AKIA quoted": f'aws="{_AK}"',
    "bare xoxb json": f'{{"t": "{_XB}"}}',
    "slack hook": f"Post to {_SLACK} daily",
    "telegram": f"Call {_TG} now",
    "jwt prose": f"Bearer {_JWT}",
    "pem": f"key:\n{_PEM}\nend",
    "pem header only": "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAAFAKE\n(truncated)",
    "assign pw": "password=hunter22",
    "assign spaced": "token: s3cr3tvalue99",
    "tab query": "see https://h/x\t?key=sk-1 more",
    "mid-query break": "see https://h/x?a=1&\n    key=sk-1 more",
    "break before ? no indent": "see https://h/x\n?key=sk-1 more",
    "nbsp query": "see https://h/x\xa0?key=sk-1 more",
    "nbsp password": "https://bot:hun\xa0ter2@h/v1",
    "H port+email": "Run at http://localhost:3000 and email support@acme.com",
    "H query email": "https://host:8080/path?email=a@b.com",
    "H npm scoped": "Install from https://registry.npmjs.org:443/@babel/core today",
    "H medium": "Read https://medium.com/@user for more",
    "H port then mention": "Open http://localhost:8080 and ping @oncall in Slack",
    "H md amp": "See https://docs.x.com/a\n&copy=2024 Acme",
    "H password required": "password: required",
    "H token expires": "the token: expires hourly",
    "H secret never": "secret: never tell anyone",
    "H env": 'api_key=os.environ["OPENAI_API_KEY"]',
    "H env placeholder": "Send the token: ${SLACK_BOT_TOKEN} in the header",
    "H commit": "Fixed in 1ac329e16b0f2c9a8e7d6c5b4a3f2e1d0c9b8a7f",
    "H uuid": "order 123e4567-e89b-12d3-a456-426614174000",
    "H b64img": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
    "H order": "Order ID ORD-2024-000123 and SKU AKIAMODEL",
    "H token count": "max_tokens: 4096 and token=streaming",
    "a1": 'config "password": "Zq8wYv3Lp9" ok',
    "a3": "use https://bot:hun ter2@h/v1 now",
    "a4": f"key:\n{_PEM}\nend",
}
# The design page's chat-test list, each with trailing text.
_RUN = "/".join(["a" * 50] * 90)
_CHAT_CASES = {
    "ghp in a long run": '{"x":"'
    + _RUN[:3000]
    + '","t":"'
    + _GH
    + '","y":"'
    + _RUN[:2000]
    + '"} done now',
    "named value at its ': '": 'config "password": "Zq8wYv3Lp9" ok then more',
    "rule 1 at its space": "use https://bot:hun ter2@h/v1 now and later",
    "rule 1 at a break + 4": "see https://bot:hun\n    ter2@h/v1 is the hook",
    "block": f"key:\n{_PEM}\nend of message here",
    "block in YAML": f"tls:\n  key: |\n    {_PEM.replace(chr(10), chr(10) + '    ')}\n  ok: true",
    "block CRLF": f"Here:\r\n{_PEM.replace(chr(10), chr(13) + chr(10))}\r\nKeep it safe.",
    "block encrypted": "Here:\n-----BEGIN RSA PRIVATE KEY-----\nProc-Type: 4,ENCRYPTED\nDEK-Info: "
    "AES-128-CBC,3F17F5316E2BAC89\n\nMIIEFAKEQUJDREVG\n-----END RSA PRIVATE KEY-----\nKeep it.",
    "block one line": "Use this: -----BEGIN RSA PRIVATE KEY----- MIIEFAKEQUJDREVG "
    "-----END RSA PRIVATE KEY----- then restart",
    "login query": "Login at https://h.example/login?user=bob&password=Sunshine then stop",
    "oauth code": "POST https://oauth2.googleapis.com/token?code=4/0AXfakeFAKEcode123&grant_type=x and wait",
    "telegram path": f"Call {_TG}?chat_id=1 now please",
    "a URL holding a name and a prefix": "Open https://h.example/cb/token?ref=sk-not-a-key&session=Qm9vZmFrZQ then go",
    "rule 2 on the next line": "see https://h/x?a=1\n&token=Sunshine and more",
    "named md label": "- **Password:** Zq8wYv3Lp9 then go on",
    "named camel json": '{"clientSecret": "Zq8wYv3Lp9Kd2xT", "x": 1} done now',
    "named env pass": "DB_PASS=Zq8wYv3Lp9\nSMTP_PASS=hunter22x9\nok",
    "named 16 spaces": 'admin = "x"\npassword                = "Zq8wYv3Lp9" ok',
    "named kebab": "cloud.aws.credentials.secret-key: Zq8wYv3Lp9Kd2xT ok",
    "named quoted with spaces": 'cfg "password": "correct horse battery 9" ok more',
    "named break + 4": "password:\n   Zq8wYv3Lp9 ok",
    "conn string": "Use DefaultEndpointsProtocol=https;AccountName=acct;AccountKey=FAKEazureFAKEkey0123"
    "456789abcdef==;EndpointSuffix=core.windows.net then go",
    "azure key, spaced": "set AZURE_KEY = "
    + ("FAKEazureFAKEkey0123456789abcdefFAKE+/" * 3)[:86]
    + "== ok",
}
_PLANTED = [
    "hun ter2",
    "ter2@",
    "hunter22",
    "Zq8wYv3Lp9",
    "s3cr3tvalue99",
    "MIIEFAKE",
    "ABCDEFFAKE",
    _GH,
    _SKP,
    "battery 9",
    "FAKEazureFAKEkey0123",
    "Sunshine",
    "4/0AXfake",
    "Qm9vZmFrZQ",
    "AAFAKEfake",
    "key=sk-1",
    "FAKEfakeFAKEfakeFAKEfake",
    "FAKEsigFAKEsig",
    "QUJDREVG",
]


def _events_of(deltas: list[str]) -> list[str]:
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    cleaner = _DeltaCleaner()
    events = [cleaner.feed(delta) for delta in deltas]
    events.append(cleaner.flush())
    return [event for event in events if event]


def _removed_fragments(text: str, whole: str) -> set[str]:
    """k1_stream's frags(): 6-character fragments of what the whole clean removes, less any
    that also occur in what it keeps (r4 L8)."""
    import difflib

    matcher = difflib.SequenceMatcher(None, text, whole, autojunk=False)
    removed = [
        text[start:end]
        for op, start, end, _, _ in matcher.get_opcodes()
        if op in ("delete", "replace") and len(text[start:end].strip()) >= 6
    ]
    fragments = {
        piece[at : at + 6] for piece in removed for at in range(len(piece) - 5)
    }
    return {fragment for fragment in fragments if fragment not in whole}


def _split_problems(text: str, splits: list[list[str]]) -> list[str]:
    """C1's three checks, for each way of splitting `text` into deltas: the joined events equal
    one whole clean; no 6-character fragment of a removed string is in any event; no planted
    secret the whole clean drops is in the joined events."""
    from fi.alk.providers.agent import _redact

    whole = _redact(text)
    fragments = _removed_fragments(text, whole)
    problems = []
    for deltas in splits:
        events = _events_of(deltas)
        joined = "".join(events)
        if joined != whole:
            problems.append(f"unequal at {[len(d) for d in deltas][:3]}")
        elif any(
            event[at : at + 6] in fragments
            for event in events
            for at in range(len(event) - 5)
        ):
            problems.append(f"a fragment at {[len(d) for d in deltas][:3]}")
        elif any(
            secret in text and secret not in whole and secret in joined
            for secret in _PLANTED
        ):
            problems.append(f"a planted secret at {[len(d) for d in deltas][:3]}")
    return problems


def _every_split(text: str) -> list[list[str]]:
    return [[text[:at], text[at:]] for at in range(1, len(text))] + [list(text)]


def test_round_twos_1919_stream_splits_each_equal_one_whole_clean() -> None:
    """C2 (W3): round 1's cases and the addendum's, every two-way split and char by char."""
    splits = {name: _every_split(text) for name, text in _ROUND1.items()}
    assert sum(len(each) for each in splits.values()) == 1919

    problems = {
        name: _split_problems(_ROUND1[name], each) for name, each in splits.items()
    }

    assert {name: found[:3] for name, found in problems.items() if found} == {}


@pytest.mark.parametrize("name", list(_CHAT_CASES))
def test_each_chat_case_split_at_every_offset_equals_one_whole_clean(name: str) -> None:
    """C1: every offset (sampled in the long run), and 1-, 3- and 7-character deltas."""
    text = _CHAT_CASES[name]
    offsets = range(1, len(text)) if len(text) < 2000 else range(1, len(text), 7)
    splits = [[text[:at], text[at:]] for at in offsets]
    splits += [
        [text[at : at + size] for at in range(0, len(text), size)] for size in (1, 3, 7)
    ]

    assert _split_problems(text, splits)[:3] == []


def test_every_event_the_pump_sends_masks_a_secret_split_across_its_whitespace(
    tmp_path, monkeypatch
):
    """C1 through _pump_events: assistant_delta, tool_result and the turn's assistant_message
    (the question is below). A named value split at its ': ' and a password at its space."""
    from fi.alk.providers.agent import _redact

    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    queue: asyncio.Queue = asyncio.Queue()
    said = 'Set "password": "Zq8wYv3Lp9" and use https://bot:hun ter2@h.example/v1 then go.'
    colon, space = said.index(": ") + 1, said.index("hun ") + 4
    pieces = [
        said[:colon],
        said[colon : colon + 1],
        said[colon + 1 : space],
        said[space:],
    ]
    for piece in pieces:
        queue.put_nowait(
            RuntimeEvent(kind="text", text=piece, detail={"partial": True})
        )
    queue.put_nowait(
        RuntimeEvent(kind="tool", tool="mcp__x__Read", detail={"call_id": "r"})
    )
    queue.put_nowait(
        RuntimeEvent(
            kind="result", text=f".env:1: GITHUB_TOKEN={_GH}", detail={"call_id": "r"}
        )
    )
    queue.put_nowait(None)

    asyncio.run(
        runtime._pump_events(
            queue,
            assistant_message_id="a-1",
            fallback_invocation_id="turn-1",
            emitted_text=[],
            turn_failures=[],
        )
    )

    shown = json.dumps(client.events)
    assert "Zq8wYv3Lp9" not in shown and "ter2" not in shown and _GH[4:12] not in shown
    assert "".join(_deltas(client)) == _redact(said)
    [result] = [one["payload"] for one in client.events if one["kind"] == "tool_result"]
    assert result["text"] == ".env:1: GITHUB_TOKEN=[redacted]"


def test_the_turns_assistant_message_masks_a_key_split_between_its_pieces(
    tmp_path, monkeypatch
):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime._published_fingerprint()
    runtime.conversation = _Saying(
        tmp_path, _texts(["Saved password: ", "Zq8wYv3Lp9 ", "for you."])
    )

    _turn(runtime, {"content": "save it"})

    assert "Zq8wYv3Lp9" not in json.dumps(client.events)
    assert "".join(_deltas(client)) == "Saved password: [redacted] for you."
    assert _reply(client) == "Saved password: [redacted] for you."


def test_a_question_the_model_asks_masks_a_named_value(tmp_path, monkeypatch):
    client = BuilderClient()
    runtime = _runtime(tmp_path, client, monkeypatch)
    runtime.stopping.set()

    asyncio.run(
        runtime._ask(
            "AskUserQuestion",
            {
                "question": "Keep DB_PASSWORD=Zq8wYv3Lp9 ?",
                "options": [f"use {_GH}", "drop it"],
            },
            None,
        )
    )

    asked = next(one for one in client.events if one["kind"] == "question_requested")
    assert asked["payload"] == {
        "prompt": "Keep DB_PASSWORD=[redacted] ?",
        "options": ["use [redacted]", "drop it"],
    }


def _seconds(deltas: list[str]) -> float:
    import time

    best = 9.0
    for _ in range(3):
        start = time.perf_counter()
        _events_of(deltas)
        best = min(best, time.perf_counter() - start)
    return best


def test_the_seams_work_per_delta_is_bounded_by_the_delta() -> None:
    """C3 (r4 H2, r1 M7): each under 0.5 s, best of 3."""
    words = "The quick brown fox jumps over the lazy dog while the build runs. "
    refuse = (
        "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse. "
        + (words * 700)[: 40 * 1024]
    )
    # 10,000 pairs, so a build that looks over the whole held text on every delta shows.
    no_space = "Here: " + "a/" * 10_000 + _GH + " done"
    saml = (
        "Sign in at https://idp.example.com/sso?SAMLRequest="
        + ("fZJNT8MwDIbvSPyHKPeuWwdbDEFAKE" * 160)[:4200]
        + "&tok="
        + _GH
        + "&RelayState=Sunshine then come back"
    )
    quoted = 'cfg "password": "' + "a b c " * 500 + '" ok'
    named_lines = ("token: Zq8wYv3Lp9 and password = abc\n" * 1200)[: 40 * 1024]

    times = {
        "honest BEGIN + 40 KiB, 4-char deltas": _seconds(
            [refuse[i : i + 4] for i in range(0, len(refuse), 4)]
        ),
        "20,000 with no space + ghp_, 1-char deltas": _seconds(list(no_space)),
        "4 KB SAML query past N, 1-char deltas": _seconds(list(saml)),
        "3 KB quoted value, 4-char deltas": _seconds(
            [quoted[i : i + 4] for i in range(0, len(quoted), 4)]
        ),
        "a name on every line, 40 KiB, 4-char deltas": _seconds(
            [named_lines[i : i + 4] for i in range(0, len(named_lines), 4)]
        ),
    }

    assert {name: seconds for name, seconds in times.items() if seconds >= 0.5} == {}


def test_a_lone_webhook_broken_by_a_line_break_before_prose_is_cleaned_as_one_url() -> (
    None
):
    """C4 (r4 M2): before the text is known to be prose, the seam never cuts at \\n."""
    from fi.alk.providers.agent import _redact

    text = "https://hooks.slack.com/services/" + "T00FAKE00/B00FAKE00/\nFAKEfakeFAKEfakeFAKEfake"
    for size in (1, 3, 7):
        joined = "".join(
            _events_of([text[i : i + size] for i in range(0, len(text), size)])
        )
        assert joined == _redact(text) == "https://hooks.slack.com/services/[redacted]"


@pytest.mark.parametrize("at", [4058, 4070, 4080, 4090, 4095])
def test_a_key_straddling_the_soft_bound_in_a_held_url_query_never_streams(
    at: int,
) -> None:
    """C4 (r4 M1, r1 L6), at 1-character deltas: no head of the key in any event, and the honest
    word after the URL ("then") is kept."""
    from fi.alk.providers.agent import _redact

    url = "https://idp.example.com/sso?SAMLRequest="
    filler = ("fZJNT8MwDIbvSPyHKPeuWwdbDEFAKE" * 160)[: at - len(url) - len("&tok=")]
    text = (
        "Sign in at "
        + url
        + filler
        + "&tok="
        + _GH
        + "&RelayState=Sunshine then come back"
    )

    events = _events_of(list(text))

    assert "".join(events) == _redact(text)
    assert "".join(events).endswith(" then come back")
    assert not any(_GH[:6] in event for event in events)


def test_a_key_block_longer_than_the_hard_bound_is_held_never_streamed() -> None:
    """C4 (r4 L7): an 18 KB block is held whole, whatever M is."""
    from fi.alk.providers.agent import _redact

    lines = [f"L{i:03d}FAKEQUJDREVGR0hJSktM" + "A" * 36 for i in range(300)]
    text = (
        "Key:\n-----BEGIN RSA PRIVATE KEY-----\n"
        + "\n".join(lines)
        + "\n-----END RSA PRIVATE KEY-----\nKeep it safe."
    )
    assert len(text) > 16_384

    events = _events_of([text[i : i + 7] for i in range(0, len(text), 7)])

    assert "".join(events) == _redact(text) == "Key:\n[redacted]\nKeep it safe."
    assert not any(line[4:30] in event for line in lines for event in events)


# -- TH-9500 change review r1 (cr1): what streams past the soft bound ---------------------------

# cr1 M3: a named value longer than N, plain, quoted with spaces, AWS's guard name, and a URL as
# the value. Past N the shed sent the value cleaned only as far as it was held, then the rest
# with no name in front of it, or the URL whole.
_LONG_NAMED = {
    "plain": "Note token: " + "Zq8w" * 1100 + " ok then",
    "quoted with spaces": 'cfg "password": "' + "a1 b2 c3 " * 540 + '" ok then',
    "aws guard": "aws_secret_access_key = "
    + "wJalrXUtnFEMIFAKEK7MD" * 210
    + " ok then",
    "a URL as the value": "Note token: https://h.example/" + "a" * 4200 + " ok then",
}


@pytest.mark.parametrize("name", list(_LONG_NAMED))
def test_a_named_value_longer_than_the_soft_bound_streams_as_one_whole_clean(
    name: str,
) -> None:
    """cr1 M3: held as a key is, up to the hard bound, at 1-, 5- and 50-character deltas."""
    text = _LONG_NAMED[name]
    splits = [
        [text[at : at + size] for at in range(0, len(text), size)]
        for size in (1, 5, 50)
    ]

    assert _split_problems(text, splits) == []


@pytest.mark.parametrize("at", [4070, 4090])
@pytest.mark.parametrize("size", [1, 5])
def test_a_key_straddling_the_soft_bound_in_a_run_with_no_space_never_streams(
    at: int, size: int
) -> None:
    """cr1 M5 (round 3's w3_flush shape): a ghp_ key in a whitespace-free run, across the
    4,096th held character. The key is held whole, up to the hard bound."""
    from fi.alk.providers.agent import _redact

    text = "Here: " + ("a/" * 3000)[: at - 6] + _GH + "/" + "b/" * 300 + " done now"
    assert text.find(_GH) == at

    events = _events_of([text[i : i + size] for i in range(0, len(text), size)])

    assert "".join(events) == _redact(text)
    assert not any(_GH[:12] in event or _GH[-20:] in event for event in events)


def test_the_seam_stays_linear_on_an_honest_begin_then_256_kib_of_prose() -> None:
    """cr1 M6: the per-delta END search is bounded by the delta (r4 H2), at 256 KiB, best of
    3, under 1.5 s. Searched from the block's header on every delta, it took 3.5 s."""
    words = "The quick brown fox jumps over the lazy dog while the build runs. "
    refuse = (
        "If a user pastes -----BEGIN PRIVATE KEY----- text, refuse. "
        + (words * 4100)[: 256 * 1024]
    )

    seconds = _seconds([refuse[i : i + 4] for i in range(0, len(refuse), 4)])

    assert seconds < 1.5, f"{seconds:.3f} s"


def test_text_after_an_end_split_across_two_deltas_streams_before_the_flush() -> None:
    """cr1 L3: an END line split between two deltas closes the block when its second half
    arrives, so what follows streams then, not only at the message's end."""
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    text = f"key:\n{_PEM}\nThen restart the service and check the logs."
    split = text.index("-----END") + len("-----END RSA PRI")
    after = text.index("Then")
    cleaner = _DeltaCleaner()

    streamed = "".join(
        cleaner.feed(delta) for delta in (text[:split], text[split:after], text[after:])
    )

    assert streamed.startswith("key:\n[redacted]\nThen restart the service and")
    assert (
        streamed + cleaner.flush()
        == "key:\n[redacted]\nThen restart the service and check the logs."
    )


# -- TH-9500 rule 1b: an @ before the password's / (design addendum v2.5.2) ----------------------
# The rows test_secret_shapes.py's R1B_CLEANED and R1B_KEPT check on the whole text, copied as
# data. 1b's span and its host lookahead cross no whitespace and no ://, so ChatHold is
# unchanged (L5); a one-URL value runs step 1 on its joined token, as `_clean` does (M3, M-A).
_R1B_ROWS = [
    "postgres://admin:Xk9/Qp+Zr8@db.example.com:5432/app",
    "https://user:pa#ss@host.example/x",
    "postgres://admin:P@ss/Xk9@db.example.com:5432/app",
    "smtp://me@gmail.com:app/pass@smtp.gmail.com:587",
    "redis://:p@w/x9@cache.internal:6379/0",
    "postgres://admin:P@ss:99/Xk9@db.example.com/app",
    "postgres://admin:P@ss/Xk9@localhost/app",
    "postgres://admin:P@ss/Xk9@db/app",
    "postgres://admin:P@ss/Xk9@[::1]:5432/app",
    "postgres://admin:P@ss/Xk9@postgres_db:5432/app",
    "postgres://admin:P@ss/Xk9@my_db/app",
    "`redis://:p@w/x9@redis`",
    "postgres://admin:P@ss/Xk9@db:${DB_PORT}/app",
    "postgres://admin:P@ss/Xk9@db:$DB_PORT/app",
    "postgres://admin:P@ss/Xk9@db?sslmode=disable",
    "redis://:p@w/x9@redis#0",
    "(amqp://u:p@w/x9@rabbitmq)",
    "amqp://u:p@w/x9@rabbitmq, then",
    "amqp://u:p@w/x9@rabbitmq; then",
    "<amqp://u:p@w/x9@rabbitmq>",
    "[amqp://u:p@w/x9@rabbitmq]",
    "postgres://admin:P@ss/Xk9@[fe80::1%25en0]:5432/app",
    "a://u:x://v:p@h/x@host.example",
    "http://admin@localhost:8080/bob@example.com",
    "ssh://git@gitlab.example.com:2222/ops@infra.example/repo.git",
    "https://ci@registry.example.com:8443/lodash@4.17.21",
    "ssh://git@host.example:22/org@team/repo",
    "http://admin@[::1]/bob@example.com",
    "ssh://git@[fd00::5]/org@team/repo",
    "https://ci@registry.example.com/lodash@4.17.21",
    "ssh://git@github.com/org@team/repo",
    "Mail https://u:p@h.example/to@~team now",
]
# M3 and M-A: equal and clean when the URL stands alone (in prose, a break inside the span is a
# named limit).
_R1B_BROKEN = {
    "postgres://admin:P@ss/Xk\n9@db.example.com:5432/app": "postgres://db.example.com:5432/app",
    "postgres://admin:Pa\nss@w/x9@db.example.com/app": "postgres://db.example.com/app",
    "redis://:pa\nss@w/x9@cache.internal:6379/0": "redis://cache.internal:6379/0",
    "a://u:x://v:p@h/\nx@host.example": "a://host.example",
}


@pytest.mark.parametrize("text", _R1B_ROWS, ids=range(len(_R1B_ROWS)))
def test_rule_1b_streams_as_the_whole_text_cleans_at_every_split(text: str) -> None:
    """Alone and in prose, every two-way split and char by char, with C1's three checks."""
    for each in (text, f"Use {text} now, then {text} again."):
        assert _split_problems(each, _every_split(each))[:3] == [], each


@pytest.mark.parametrize("text", list(_R1B_BROKEN), ids=range(len(_R1B_BROKEN)))
def test_a_one_url_value_broken_inside_1bs_span_streams_equal_and_clean(
    text: str,
) -> None:
    from fi.alk.providers.agent import _redact

    assert _redact(text) == _R1B_BROKEN[text]
    assert _split_problems(text, _every_split(text)) == []


# -- TH-9500 change review r2 (cr2) -----------------------------------------------------------------

# cr2 H1: a reply that is one URL, with a key or a named value at the start of a later line.
# Cleaning the whole text masks it before the join; before prose, so does the stream. Each row
# is (reply, the secret that must not stream). Owl's ten shapes, then rows longer than N, which
# go through `_shed`'s branch before prose.
_AKIA = "AKIA" + "FAKEFAKEFAKE1234"
_JWT_LONG = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.FAKEsigFAKEsigFAKEsig"
_LONG_URL = "https://h.example/" + "a" * 4100
_LINE_STARTS = {
    "url \\n ghp": ("https://github.com/settings/tokens\n" + _GH, _GH),
    "url \\t ghp": ("https://github.com/settings/tokens\t" + _GH, _GH),
    "url \\r\\n ghp": ("https://h.example/p\r\n" + _GH, _GH),
    "url \\n AKIA": ("https://console.aws.amazon.com/iam\n" + _AKIA, _AKIA),
    "url \\n jwt": ("https://jwt.io\n" + _JWT_LONG, "FAKEsigFAKEsigFAKEsig"),
    "url \\n sk-proj": ("https://platform.openai.com/api-keys\n" + _SKP, _SKP),
    "url \\n token=": ("https://h.example/p\ntoken=Zq8wYv3Lp9Kd2xT", "Zq8wYv3Lp9Kd2xT"),
    "url \\n DB_PASSWORD=": (
        "https://h.example/p\nDB_PASSWORD=Zq8wYv3Lp9Kd2xT",
        "Zq8wYv3Lp9Kd2xT",
    ),
    "url \\n url \\n ghp": ("https://a.example/x\nhttps://b.example/y\n" + _GH, _GH),
    "1b url \\n ghp": (
        "postgres://admin:P@ss/Xk9@db.example.com:5432/app\n" + _GH,
        _GH,
    ),
    "past N \\n ghp": (_LONG_URL + "\n" + _GH, _GH),
    "past N \\n token: \\n value": (
        _LONG_URL + "\ntoken:\nZq8wYv3Lp9Kd2xT",
        "Zq8wYv3Lp9Kd2xT",
    ),
    "past N \\n ghp \\n url": (_LONG_URL + "\n" + _GH + "\nhttps://c.example/z", _GH),
    # A URL on the key's own line: the cut there sends the key's line, masked at its start.
    "past N \\n ghp/url": (_LONG_URL + "\n" + _GH + "/https://c.example/z", _GH),
    "past N, many lines": ("https://h.example/x" + "\nabc" * 1100 + "\n" + _GH, _GH),
    # cr3 M5: a key on the line after a slash, glued to a URL.
    "past N /\\n AKIA glued to url": (
        _LONG_URL + "/\n" + _AKIA + "https://c.example/z",
        _AKIA,
    ),
}


@pytest.mark.parametrize("name", list(_LINE_STARTS))
def test_a_key_at_a_line_start_in_a_one_url_reply_never_streams(name: str) -> None:
    """cr2 H1: at 1- and 7-character deltas and as one delta, the stream equals cleaning the
    whole text, and no event holds the secret's tail, in any case: a value cleaned as part of
    a scheme comes out lower-cased (cr3 H1)."""
    from fi.alk.providers.agent import _redact

    text, secret = _LINE_STARTS[name]
    tail = secret[-12:].lower()
    whole = _redact(text)
    assert tail not in whole.lower()
    for size in (1, 7, len(text)):
        events = _events_of([text[i : i + size] for i in range(0, len(text), size)])
        assert "".join(events) == whole, size
        assert not any(tail in event.lower() for event in events), size


# cr3 H1: past N before prose, a named value or a key on the line before a URL line. The
# branches find URL starts on the joined text, where that line is glued onto the URL's scheme;
# a cut there sent the name alone and cleaned the value as a scheme, lower-cased. Hawk's rows
# 1, 3 and 5.
_V = "Zq8wYv3Lp9Kd2xT"
_GRAFANA = "\n".join(
    f"https://grafana.example.com/d/service-{i}/latency" for i in range(90)
)
_LINE_BEFORE_A_URL = {
    "a long URL, DB_PASSWORD=V, a URL": (
        _LONG_URL + "\nDB_PASSWORD=" + _V + "\nhttps://b.example/y",
        _V,
    ),
    "a long URL, token=V glued to a URL": (
        _LONG_URL + "\ntoken=" + _V + "https://c.example/z",
        _V,
    ),
    "90 grafana URLs, GITHUB_TOKEN=ghp_, a URL": (
        _GRAFANA + "\nGITHUB_TOKEN=" + _GH + "\nhttps://github.com/org/repo",
        _GH,
    ),
    # A name and its value on the next line go together.
    "a long URL, token:, V, a URL": (
        _LONG_URL + "\ntoken:\n" + _V + "\nhttps://c.example/z",
        _V,
    ),
    # A cut never splits a URL's userinfo from its host.
    "a long user, :V@host on the next line, a URL": (
        "https://" + "u" * 4100 + "\n:" + _V + "@host.example/x\nhttps://c.example/z",
        _V,
    ),
}


@pytest.mark.parametrize("name", list(_LINE_BEFORE_A_URL))
@pytest.mark.parametrize("size", [1, 4, 512, 0])
def test_a_value_on_the_line_before_a_url_never_streams(name: str, size: int) -> None:
    """cr3 H1: before prose, a shed cuts only at a line boundary, at 1-, 4- and 512-character
    deltas and as one delta."""
    from fi.alk.providers.agent import _redact

    text, secret = _LINE_BEFORE_A_URL[name]
    size = size or len(text)
    tail = secret[-12:].lower()
    whole = _redact(text)
    assert tail not in whole.lower()

    events = _events_of([text[i : i + size] for i in range(0, len(text), size)])

    assert "".join(events) == whole
    assert not any(tail in event.lower() for event in events)


@pytest.mark.parametrize("size", [1, 7, 0])
def test_a_line_start_masked_in_the_stream_is_logged_once(
    size: int, caplog: pytest.LogCaptureFixture
) -> None:
    """cr2 H1: the line-start mask's kinds go on the cleaner's one log line."""
    text = "https://github.com/settings/tokens\n" + _GH
    size = size or len(text)
    with caplog.at_level("WARNING"):
        _events_of([text[i : i + size] for i in range(0, len(text), size)])

    assert [
        r.getMessage() for r in caplog.records if r.name == "fi.alk.providers.agent"
    ] == ["secrets redacted: kinds=github"]


@pytest.mark.parametrize("size", [1, 7, 0])
def test_a_line_start_masked_by_a_shed_is_logged_once(
    size: int, caplog: pytest.LogCaptureFixture
) -> None:
    """cr3 L3: past N, the shed sends the key's line, masked, and logs its kind once."""
    text = _LONG_URL + "\n" + _GH + "/\nhttps://c.example/z"
    size = size or len(text)
    with caplog.at_level("WARNING"):
        events = _events_of([text[i : i + size] for i in range(0, len(text), size)])

    assert "[redacted]/" in events[0]
    assert [
        r.getMessage() for r in caplog.records if r.name == "fi.alk.providers.agent"
    ] == ["secrets redacted: kinds=github"]


# cr3 M6 and L1: an opening that fills N with scheme characters and no :// is prose, so the
# URL lines after it aren't glued onto it. L1's named limit ("an opening of 4,096 or more scheme
# characters with no ://") is closed by it, and its row now passes.
_LONG_OPENINGS = {
    "M6: a query value on a later URL line": (
        "Zq8w" * 1100 + "\nhttps://registry.example.com/x\n&tok=" + _V,
        _V,
    ),
    "L1: a key on the next line": ("a" * 4100 + "\n" + _GH, _GH),
}


@pytest.mark.parametrize("name", list(_LONG_OPENINGS))
@pytest.mark.parametrize("size", [1, 7, 64, 0])
def test_a_long_opening_with_no_scheme_streams_as_prose(name: str, size: int) -> None:
    from fi.alk.providers.agent import _redact

    text, secret = _LONG_OPENINGS[name]
    size = size or len(text)
    tail = secret[-12:].lower()
    whole = _redact(text)
    assert tail not in whole.lower()

    events = _events_of([text[i : i + size] for i in range(0, len(text), size)])

    assert "".join(events) == whole
    assert not any(tail in event.lower() for event in events)


# cr2 M2: rule 1 across whitespace, then rule 2's next piece (S's r1r2.py).
_RULE1_THEN_RULE2 = [
    "See redis://:pa ss@h:6379\n?code=s3cr3tvalue99 then",
    "See redis://u:pa ss@h.example:6379 &tok=s3cr3tvalue99 then",
    "Try postgres://admin:Xk9/Qp+Zr\n  8@db.example.com:5432/app\n&tok=s3cr3tvalue99 ok",
    # cr3 M2: the address piece's query ends in &, so rule 2 reaches the next piece.
    "See redis://:pa ss@h:6379/?a=1& tok=s3cr3tvalue99 then",
]


@pytest.mark.parametrize("text", _RULE1_THEN_RULE2, ids=range(len(_RULE1_THEN_RULE2)))
def test_rule_2s_next_piece_holds_after_a_password_broken_by_whitespace(
    text: str,
) -> None:
    """cr2 M2: the piece a rule 1 match ends in is an address piece, at 1- and 5-character
    deltas, and at every two-way split, where the @ arrives in the delta its piece ends in
    (cr3 M2)."""
    splits = [
        [text[i : i + size] for i in range(0, len(text), size)] for size in (1, 5)
    ] + _every_split(text)

    assert _split_problems(text, splits) == []
    assert not any("s3cr3tvalue99" in event for event in _events_of(list(text)))


def test_a_password_broken_by_a_crlf_split_between_deltas_never_streams() -> None:
    """cr2 M3: `\\r` and `\\n` in separate deltas, at 1-, 2- and 3-character deltas; and with
    spaces and a tab before the `\\r` (cr3 M3)."""
    for text in (
        "Try postgres://admin:Xk9/Qp+Zr\r\n8@db.example.com:5432/app now",
        "Try postgres://admin:Xk9/Qp+Zr \t\r\n8@db.example.com:5432/app now",
    ):
        splits = [
            [text[i : i + size] for i in range(0, len(text), size)]
            for size in (1, 2, 3)
        ]

        assert _split_problems(text, splits) == [], text


def test_an_open_quoted_value_longer_than_the_hard_bound_still_streams() -> None:
    """cr2 M4: a named value is held up to M (`_LONGEST_HELD_KEY`), not past it: text streams
    before the message ends."""
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    words = "The quick brown fox jumps over the lazy dog while the build runs. "
    text = 'Set "password": "' + (words * 320)[: 20 * 1024]
    cleaner = _DeltaCleaner()

    streamed = "".join(cleaner.feed(text[i : i + 4]) for i in range(0, len(text), 4))

    assert len(streamed) > 1024


def test_one_large_delta_of_passwords_broken_by_spaces_stays_linear() -> None:
    """cr3 M1: `_address` finds its piece by bisection. One 256 KiB delta of rule 1 matches
    that cross a space takes at most twice the same text with no broken password, best of 3,
    interleaved (cr4 L1: an absolute time failed on a loaded machine)."""
    import time

    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    def text(unit: str) -> str:
        return (unit * (256 * 1024 // len(unit) + 1))[: 256 * 1024]

    broken, control = (
        text("See redis://:pa ss@h x y z "),
        text("See redis://h:6379 x y z now "),
    )
    best = {broken: 99.0, control: 99.0}
    for _ in range(3):
        for each in (broken, control):
            cleaner = _DeltaCleaner()
            start = time.perf_counter()
            cleaner.feed(each)
            best[each] = min(best[each], time.perf_counter() - start)

    assert best[broken] <= 2 * best[control], (
        f"{best[broken]:.3f} s, {best[control]:.3f} s"
    )


# -- TH-9500 change review r4 (cr4) -----------------------------------------------------------------

# cr4 H1: Dileep's rule 1b input with its password broken by a line break, after the / and
# before it, past N before prose. A cut between the password's lines sent its first half; the
# cut holds while the text after it, up to the next ://, may still be that URL's userinfo.
_RULE1B_BROKEN_PAST_N = {
    "after the /": _LONG_URL
    + "\npostgres://admin:Xk9/Qp+Zr\n8@db.example.com:5432/app\nhttps://c.example/z",
    "before the /": _LONG_URL
    + "\npostgres://admin:Xk\n9/Qp+Zr8@db.example.com:5432/app\nhttps://c.example/z",
}


@pytest.mark.parametrize("name", list(_RULE1B_BROKEN_PAST_N))
@pytest.mark.parametrize("size", [1, 7, 512, 0])
def test_a_rule_1b_password_broken_by_a_line_break_past_n_never_streams(
    name: str, size: int
) -> None:
    from fi.alk.providers.agent import _redact

    text = _RULE1B_BROKEN_PAST_N[name]
    size = size or len(text)
    whole = _redact(text)
    assert "qp+zr" not in whole.lower()

    events = _events_of([text[i : i + size] for i in range(0, len(text), size)])

    assert "".join(events) == whole
    assert not any("qp+zr" in event.lower() for event in events)


# cr4 M1: honest replies that are only URLs, one per line. Each line ends in scheme characters,
# so joined it is glued onto the next line's scheme; the cut must still fall between them.
_URL_LISTS = {
    "400 grafana dashboards": "\n".join(
        f"https://grafana.example.com/d/service-{i}/latency" for i in range(400)
    ),
    "800 bare hosts": "\n".join(f"https://host{i}.example.com" for i in range(800)),
    "400 GitHub repositories": "\n".join(
        f"https://github.com/org-{i}/repo-{i}" for i in range(400)
    ),
}


@pytest.mark.parametrize("name", list(_URL_LISTS))
@pytest.mark.parametrize("size", [7, 512, 0])
def test_an_honest_url_list_streams_before_its_end(name: str, size: int) -> None:
    """cr4 M1: the stream equals the whole text, and most of it goes before the flush."""
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner
    from fi.alk.providers.agent import _redact

    text = _URL_LISTS[name]
    size = size or len(text)
    cleaner = _DeltaCleaner()

    streamed = "".join(
        cleaner.feed(text[i : i + size]) for i in range(0, len(text), size)
    )

    assert streamed + cleaner.flush() == _redact(text)
    assert len(streamed) > len(text) // 2


def _best_interleaved(texts: dict[str, str], size: int = 4) -> dict[str, float]:
    """Each text fed in `size`-character deltas, best of 3, the texts taking turns so that a
    loaded machine slows them alike."""
    import time

    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    best = dict.fromkeys(texts, 99.0)
    for _ in range(3):
        for name, text in texts.items():
            cleaner = _DeltaCleaner()
            start = time.perf_counter()
            for i in range(0, len(text), size):
                cleaner.feed(text[i : i + size])
            cleaner.flush()
            best[name] = min(best[name], time.perf_counter() - start)
    return best


# 16 KiB held in 4-character deltas costs the same for any text it holds, a cut that is walked
# once or one that is walked again on every delta aside; one URL with no breaks is the control.
_HELD_16K = 16 * 1024
_HELD_CONTROL = ("https://a.example/" + "x" * _HELD_16K)[:_HELD_16K]


def test_a_held_cut_is_not_walked_again_on_every_delta() -> None:
    """cr4 M2: while a cut holds, the held text grows by lines it can't cut at; they are walked
    once, not on every delta. R's shape at 16 KiB in 4-character deltas takes at most twice a
    control (walked on every delta, about a minute)."""
    held = ("https://hostA\nhttps://hostB" + "\nx" * _HELD_16K)[:_HELD_16K]
    # The cut held at a line that is only a URL's own `://` keeps its place too.
    at_scheme = ("https://hostA/a\nhttps\n://hostB/p\nhttps://c/" + "\nx" * _HELD_16K)[:_HELD_16K]

    best = _best_interleaved({"held": held, "at_scheme": at_scheme, "control": _HELD_CONTROL})

    assert best["held"] <= 2 * best["control"], best
    assert best["at_scheme"] <= 2 * best["control"], best


def test_a_cut_the_userinfo_guard_holds_is_not_walked_again_on_every_delta() -> None:
    """cr4b M2: the other way a cut is held: an @ line before the next URL. The guard path
    keeps the held cut too."""
    held = (
        "https://a.example/\nalice@example.com\nhttps://b.example/" + "\nx" * _HELD_16K
    )[:_HELD_16K]

    best = _best_interleaved({"held": held, "control": _HELD_CONTROL})

    assert best["held"] <= 2 * best["control"], best


@pytest.mark.parametrize("size", [7, 512, 0])
def test_a_cut_held_by_an_at_line_is_released_by_the_url_lines_after_it(
    size: int,
) -> None:
    """cr4b M2: a long URL, an email line, then 800 URL lines (24,844 joined). The cut the
    @ line holds moves on with the URL starts after it: the list streams as stored, not as
    `[redacted]`, at every size (a held cut that stays put past a later start fails at 7)."""
    from fi.alk.providers.agent import _redact

    text = (
        _LONG_URL
        + "\nalice@example.com\nhttps://c.example/z"
        + "".join(f"\nhttps://h{i}.example.com/p" for i in range(800))
    )
    step = size or len(text)

    events = _events_of([text[i : i + step] for i in range(0, len(text), step)])

    assert "".join(events).strip() == _redact(text.strip())
    assert sum(len(event) for event in events[:-1]) > len(text) // 2


def test_tab_split_lines_are_cut_without_one_call_blocking() -> None:
    """cr4b H3: a URL whose lines are split by tabs, 1 MiB of them, then a :// that moves the
    cut. No call takes a second (a search back to the held text's start for every line took
    2 s at 1 MiB, 9 s at 2), and the held text, breaks counted, stays within the hard bound."""
    import time

    from fi.alk.harness.hosted_chat_entrypoint import _HOLD_HARD, _DeltaCleaner

    run = 128
    text = (
        "https://a.example/@x"
        + ("\t" * run + "x") * (1024 * 1024 // (run + 1))
        + "://b"
    )
    cleaner = _DeltaCleaner()
    slowest = most = 0
    for i in range(0, len(text), 4096):
        start = time.perf_counter()
        cleaner.feed(text[i : i + 4096])
        slowest = max(slowest, time.perf_counter() - start)
        most = max(most, len(cleaner.held))
    cleaner.flush()

    assert slowest < 1.0, f"{slowest:.2f} s"
    assert most <= _HOLD_HARD + 4096


def test_the_walk_over_tab_split_lines_costs_each_line_its_own_length() -> None:
    """cr4b H3: `_line_cut` on 1 MiB of tab-split lines, with the cut on the first, takes at
    most twice the same on newline-split lines (searching back for every line took 2 s)."""
    import time

    from fi.alk.harness.hosted_chat_entrypoint import _URL_BREAKS, _DeltaCleaner

    run = 128
    unit = ("\t" * run + "x", "\n" * run + "x")
    best = [99.0, 99.0]
    for _ in range(3):
        for n, each in enumerate(unit):
            raw = "https://a.example/@x" + each * (1024 * 1024 // (run + 1)) + "://b"
            start = time.perf_counter()
            _DeltaCleaner()._line_cut(raw, raw.translate(_URL_BREAKS), 0)
            best[n] = min(best[n], time.perf_counter() - start)

    assert best[0] <= 2 * best[1] + 0.05, best


def test_a_name_its_equals_and_its_value_on_three_lines_past_n_is_a_named_limit() -> (
    None
):
    """cr4 M3, a named limit: before prose and past N, a name, its `=` and its value each on a
    line of its own, then a URL line. No cut keeps all three lines with the name: the value
    streams joined onto the URL's scheme, lower-cased, where the whole text masks it. The
    stream is pinned as it is, at every size."""
    text = _LONG_URL + "\ntoken\t=\tZq8wYv3Lp9Kd2xT\nhttps://c.example/z"
    for size in (1, 7, 512, len(text)):
        events = _events_of([text[i : i + size] for i in range(0, len(text), size)])
        assert "".join(events) == (
            _LONG_URL + "token=zq8wyv3lp9kd2xthttps://c.example/z"
        ), size


# -- TH-9500 change review r5 (cr5): the offset sweeps as tests -------------------------------------

# Three rounds of hand-picked rows each passed and then failed the next offset class a sweep
# found. So each class is swept here: a break at every offset after the ://, or at every place
# the input has one, and the rule is the one chat has: the stream, joined, is the stored message
# (`_redact(reply.strip())`), and no event holds the secret, compared lower-cased. A sweep that
# ran fewer cases than it should have, or whose check was taken out, fails on its count.
_SWEEP_BREAKS = {"n": "\n", "t": "\t", "rn": "\r\n"}
_SWEEP_GRAFANA = "\n".join(
    f"https://grafana.example.com/d/service-{i}/latency" for i in range(90)
)
# (url, the tail of the password that must never stream)
_SWEEP_URLS = {
    "1b": ("postgres://admin:Xk9/Qp+Zr8@db.example.com:5432/app", "qp+zr"),
    "r1": ("postgres://admin:S3cretPw9x@db.example.com/app", "cretp"),
    "r1-nouser": ("redis://:S3cretPw9x@cache.internal:6379/0", "cretp"),
    "1b-at": ("postgres://admin:P@ss/Xk9@db.example.com:5432/app", "ss/xk"),
    "1b-ash": ("redis://:p@w/x9@cache.internal:6379/0", "w/x9"),
    "1b-pass": ("postgres://admin:Pass@w/x9@db.example.com/app", "w/x9"),
}
_SWEEP_TAILS = ("\nhttps://c.example/z", "https://c.example/z")


def _swept(cases, size: int) -> tuple[int, list[str], int]:
    """(cases held, the ones that were not, cases skipped) over `cases` of (label, text,
    secret): each streamed in deltas of `size`. A case whose stored message keeps the secret is
    skipped: that is the base cleaner's, not the stream's."""
    from fi.alk.providers.agent import _redact

    held, bad, skipped = 0, [], 0
    for label, text, secret in cases:
        whole = _redact(text.strip())
        if secret in whole.lower():
            skipped += 1
            continue
        events = _events_of([text[i : i + size] for i in range(0, len(text), size)])
        joined = "".join(events)
        leak = any(secret in event.lower() for event in events)
        if leak or joined.strip() != whole:
            bad.append(f"{label}: {'leak' if leak else 'unequal'}")
        else:
            held += 1
    return held, bad, skipped


def _assert_swept(cases, size: int, at_least: int) -> None:
    held, bad, skipped = _swept(cases, size)
    assert not bad, f"{len(bad)} of {held + len(bad)}: {bad[:6]}"
    # Every case is held or bad, so an emptied sweep, or a check taken out, changes this.
    assert held >= at_least, f"swept {held}, skipped {skipped}"


def test_a_sweep_reports_a_leak_and_an_unequal_stream(monkeypatch) -> None:
    """The sweeps' own check, on streams made to fail it: a sweep that cannot see a leak or a
    difference would pass on any tree."""
    import sys

    here = sys.modules[__name__]
    case = [("c", "See https://h.example/p now", "zzzzz")]

    monkeypatch.setattr(
        here, "_events_of", lambda deltas: ["See https://h.example/p now"]
    )
    assert _swept(case, 5) == (1, [], 0)
    monkeypatch.setattr(
        here, "_events_of", lambda deltas: ["See https://h.example/p ZZZZZ"]
    )
    assert _swept(case, 5) == (0, ["c: leak"], 0)
    monkeypatch.setattr(
        here, "_events_of", lambda deltas: ["See https://h.example/q now"]
    )
    assert _swept(case, 5) == (0, ["c: unequal"], 0)
    # The base cleaner's own miss is no stream's: skipped, not held.
    assert _swept([("k", "See zzzzz now", "zzzzz")], 5) == (0, [], 1)


def _url_sweep(size: int, brk: str):
    """A credentialed URL after a long URL, with the break at every offset after its ://."""
    # One delta per character over the long URL alone keeps the 1-character run short.
    prefixes = (_LONG_URL,) if size == 1 else (_LONG_URL, _SWEEP_GRAFANA)
    tails = _SWEEP_TAILS[:1] if size == 1 else _SWEEP_TAILS
    for pn, prefix in enumerate(prefixes):
        for name, (url, secret) in _SWEEP_URLS.items():
            for k in range(url.index("://") + 3, len(url) + 1):
                for tn, tail in enumerate(tails):
                    text = prefix + "\n" + url[:k] + brk + url[k:] + tail
                    yield f"{name}/{pn}/{k}/{tn}", text, secret


@pytest.mark.parametrize("brk", list(_SWEEP_BREAKS))
@pytest.mark.parametrize("size", [1, 7, 64, 512])
def test_a_credentialed_url_broken_at_any_offset_past_n_streams_as_stored(
    size: int, brk: str
) -> None:
    """cr4b H1, H2: U's sweep. After a long URL (or a URL list), a credentialed URL with a
    break at every offset from just after its :// to its end, before and after its @, inside its
    host, and before its port."""
    _assert_swept(_url_sweep(size, _SWEEP_BREAKS[brk]), size, 215 if size == 1 else 860)


def _rule_1b_sweep(brk: str):
    """R's: a rule 1b password (it holds a /) broken after each of its characters."""
    for pn, prefix in enumerate((_LONG_URL, _SWEEP_GRAFANA)):
        for password in ("Xk9/Qp+Zr8", "Xk9/Qp+Zr8Wv5Ty", "ab/cdEFgh12"):
            for k in range(1, len(password) + 1):
                for tn, tail in enumerate(
                    ("\nhttps://c.example/z", "https://c.example/z")
                ):
                    text = (
                        prefix
                        + "\npostgres://admin:"
                        + password[:k]
                        + brk
                        + password[k:]
                        + "@db.example.com:5432/app"
                        + tail
                    )
                    yield (
                        f"{pn}/{password}/{k}/{tn}",
                        text,
                        password.split("/")[1][:5].lower(),
                    )


@pytest.mark.parametrize("brk", list(_SWEEP_BREAKS))
@pytest.mark.parametrize("size", [1, 7, 64, 512])
def test_a_rule_1b_password_broken_at_any_offset_past_n_streams_as_stored(
    size: int, brk: str
) -> None:
    """cr4 H1: R's sweep, a break after each character of a password that holds a /."""
    _assert_swept(_rule_1b_sweep(_SWEEP_BREAKS[brk]), size, 144)


def _dodge_cases(brk: str):
    """Every :// held is broken by a line break, so the guard that looks for one finds none
    (cr4b H2), and rule 1b passwords broken after the @ or inside the host (cr4b H1)."""
    long = _LONG_URL + "/"
    z = "https://c.example/" + "z" * 4100
    cases = {
        "a :/ break /": (
            long + "\npostgres:/\n/admin:Xk9/Qp+Zr\n8@db.example.com:5432/app\n" + z,
            "qp+zr",
        ),
        "b : break //": (
            long + "\npostgres:\n//admin:Xk9/Qp+Zr\n8@db.example.com:5432/app\n" + z,
            "qp+zr",
        ),
        "c :/ break /, P@ss/Xk9@": (
            long + "\npostgres:/\n/admin:P@ss/Xk9@\ndb.example.com:5432/app\n" + z,
            "ss/xk9",
        ),
        "d pw broken twice": (
            long + "\npostgres:/\n/admin:Xk9/Qp\n+Zr\n8@db.example.com:5432/app\n" + z,
            "qp+zr",
        ),
        "e control, :// whole": (
            long + "\npostgres://admin:Xk9/Qp+Zr\n8@db.example.com:5432/app\n" + z,
            "qp+zr",
        ),
    }
    long_url = _LONG_URL
    other = "https://c.example/z"
    h123 = {
        "H1 broken after the path's @": (
            long_url
            + "\npostgres://admin:P@ss/Xk9@\ndb.example.com:5432/app\n"
            + other,
            "ss/xk9",
        ),
        "H1 addendum shape, port in password": (
            long_url + "\npostgres://admin:P@ss:99/Xk9@\ndb.example.com/app\n" + other,
            "ss:99/xk9",
        ),
        "H1 redis 1b, glued URL after": (
            long_url + "\nredis://:p@w/x9@\ncache.internal:6379/0" + other,
            "w/x9",
        ),
        "H1 broken inside the host": (
            long_url
            + "\npostgres://admin:P@ss/Xk9@db.\nexample.com:5432/app\n"
            + other,
            "ss/xk9",
        ),
        "H1 control, under N": (
            "https://h.example/p\npostgres://admin:P@ss/Xk9@\ndb.example.com:5432/app\n"
            + other,
            "ss/xk9",
        ),
        "H2 :/ break /, password broken": (
            long_url
            + "/\npostgres:/\n/admin:Xk9/Qp+Zr\n8@db.example.com:5432/app\nhttps://c.example/"
            + "z" * 4100,
            "qp+zr",
        ),
        "H2 control, :// whole": (
            long_url
            + "/\npostgres://admin:Xk9/Qp+Zr\n8@db.example.com:5432/app\nhttps://c.example/"
            + "z" * 4100,
            "qp+zr",
        ),
    }
    for name, (text, secret) in {**cases, **h123}.items():
        yield name, text.replace("\n", brk), secret


@pytest.mark.parametrize("brk", list(_SWEEP_BREAKS))
@pytest.mark.parametrize("size", [1, 7, 64, 512])
def test_a_url_whose_own_scheme_or_host_is_broken_past_n_streams_as_stored(
    size: int, brk: str
) -> None:
    """cr4b H1, H2: the guard may not depend on a :// being whole in the text held, nor on the
    break falling in a place that leaves the password's host on the next line."""
    _assert_swept(_dodge_cases(_SWEEP_BREAKS[brk]), size, 12)


def _honest_lists() -> dict[str, str]:
    def alt(n: int, a: str, b: str) -> str:
        return "\n".join(a.format(i=i) + "\n" + b.format(i=i) for i in range(n))

    return {
        "github + email x300": alt(
            300, "https://github.com/user{i}", "user{i}@example.com"
        ),
        "email + github x300": "https://example.com/team\n"
        + "\n".join(
            f"user{i}@example.com\nhttps://github.com/user{i}" for i in range(300)
        ),
        "url + git@ x300": alt(
            300,
            "https://gitlab.example.com/org/repo{i}",
            "git@gitlab.example.com:org/repo{i}.git",
        ),
        "url + mailto x300": alt(
            300, "https://example.com/people/p{i}", "mailto:p{i}@example.com"
        ),
        "url + @mention x400": alt(
            400, "https://github.com/org/repo{i}/pull/{i}", "@reviewer{i}"
        ),
        "url + img@sha256 x150": alt(
            150,
            "https://hub.docker.com/r/org/img{i}",
            "org/img{i}@sha256:" + "ab12" * 16,
        ),
        "medium /@user x500": "\n".join(
            f"https://medium.com/@writer{i}" for i in range(500)
        ),
        "npm /@scope/pkg x500": "\n".join(
            f"https://www.npmjs.com/package/@scope{i}/pkg" for i in range(500)
        ),
        "control: github only x600": "\n".join(
            f"https://github.com/user{i}" for i in range(600)
        ),
    }


# The lists that stream: an @ in a URL's own path or a /@ segment is no userinfo.
_STREAMING_LISTS = (
    "medium /@user x500",
    "npm /@scope/pkg x500",
    "control: github only x600",
)
# cr4b M1, a named limit: an @ on the line before a URL could be the end of a userinfo, and the
# cut after it can't tell, so the held text isn't cut while a later URL follows an @ line. Past
# _HOLD_HARD it is sent as `[redacted]` and the rest of the token is dropped; short of it,
# nothing goes before the flush. (Holding only while rule 1 finds a userinfo that would cross
# the cut was tried: rule 1 is the cross-whitespace rule, and the plain userinfo of a URL on one
# line isn't its span, so it let passwords through.) The raw text is counted toward the bound,
# breaks too, so a list of about 16,000 joined characters with a break per URL is past it.
_AT_LINE_LISTS_REDACTED = (
    "url + git@ x300",
    "url + @mention x400",
    "url + img@sha256 x150",
    "url + mailto x300",
)
_AT_LINE_LISTS_HELD = ("github + email x300", "email + github x300")


def _fed(text: str, size: int) -> tuple[str, str]:
    """What the stream sends before the flush, and what the flush sends."""
    from fi.alk.harness.hosted_chat_entrypoint import _DeltaCleaner

    cleaner = _DeltaCleaner()
    sent = "".join(cleaner.feed(text[i : i + size]) for i in range(0, len(text), size))
    return sent, cleaner.flush()


@pytest.mark.parametrize("size", [7, 512, 0])
def test_an_honest_url_list_streams_as_stored_and_before_its_end(size: int) -> None:
    """URL-only lists past _HOLD_SOFT joined with an @ in a path: the stream is the stored
    message, and most of it goes before the flush."""
    from fi.alk.providers.agent import _redact

    lists = _honest_lists()
    for name in _STREAMING_LISTS:
        text = lists[name]
        sent, flushed = _fed(text, size or len(text))
        assert (sent + flushed).strip() == _redact(text.strip()), name
        assert len(sent) > len(text) // 2, name
    assert (
        len(_STREAMING_LISTS) + len(_AT_LINE_LISTS_REDACTED) + len(_AT_LINE_LISTS_HELD)
        == 9
    )
    assert len(lists) == 9


@pytest.mark.parametrize("size", [7, 512, 0])
def test_an_honest_url_list_with_an_at_line_before_each_url_is_a_named_limit(
    size: int,
) -> None:
    """cr4b M1, a named limit: pinned as it is, so that a change to it shows."""
    from fi.alk.providers.agent import _redact

    lists = _honest_lists()
    for name in _AT_LINE_LISTS_REDACTED + _AT_LINE_LISTS_HELD:
        text = lists[name]
        sent, flushed = _fed(text, size or len(text))
        if name in _AT_LINE_LISTS_REDACTED:
            assert sent + flushed == "[redacted]", name
        else:
            assert sent == "", name
            assert flushed.strip() == _redact(text.strip()), name


# -- TH-9500 change review r5, H1: a break just before a credentialed URL's own :// -----------------
_PRE_SCHEME_URLS = {
    "1b": ("postgres://admin:Xk9/Qp+Zr8@db.example.com:5432/app", "qp+zr"),
    "1b-at": ("postgres://admin:P@ss/Xk9@db.example.com:5432/app", "ss/xk"),
    "1b-port": ("postgres://admin:P@ss:99/Xk9@db.example.com/app", "ss:99"),
    "1b-ash": ("redis://:p@w/x9@cache.internal:6379/0", "w/x9"),
    "r1": ("postgres://admin:S3cretPw9x@db.example.com/app", "cretp"),
}


def _pre_scheme_cases():
    for name, (url, secret) in _PRE_SCHEME_URLS.items():
        at = url.index("://")
        for kind, brk in _SWEEP_BREAKS.items():
            text = _LONG_URL + "\n" + url[:at] + brk + url[at:] + "\nhttps://c.example/z"
            yield f"{name}/{kind}", text, secret


@pytest.mark.parametrize("size", [1, 7, 64, 512, 0], ids=lambda size: f"size_{size or 'whole'}")
def test_a_credentialed_url_broken_just_before_its_scheme_past_n_streams_as_stored(size) -> None:
    """Past N before prose, the line cut can fall between a URL's scheme and its own ://, where
    the text after the cut still holds the userinfo. Each of 5 URLs × 3 breaks streams equal to
    the stored message, with no part of its password in any event."""
    cases = list(_pre_scheme_cases())
    assert len(cases) == 15
    if size == 0:
        held = sum(_swept([case], len(case[1]))[0] for case in cases)
        bad = [b for case in cases for b in _swept([case], len(case[1]))[1]]
        assert not bad, bad
        assert held == len(cases)
        return
    _assert_swept(cases, size, at_least=len(cases))


@pytest.mark.parametrize("size", [1, 7, 64])
def test_a_scheme_line_then_a_lone_colon_line_past_n_streams_as_stored(size) -> None:
    """The scheme, its `:` and its `//` on three lines, with a password holding `://` and an @
    on a line of its own: the cut must not fall between the scheme and its `://`."""
    text = (
        _LONG_URL
        + "\npostgres\n:\n//admin:S3cretPw9x/a://b\n@\ndb.example.com/app\nhttps://c.example/z"
    )
    _assert_swept([("scheme, :, //", text, "s3cretpw9x")], size, at_least=1)
