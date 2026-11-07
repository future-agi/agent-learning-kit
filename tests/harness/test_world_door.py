"""The world's door: one rule set for every surface, and no error of its own."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fi.alk.harness.world_door import (
    AGENT,
    DictRoster,
    Executed,
    HandlerExecutor,
    MemoryLog,
    Routed,
    TeeLog,
    ToolCall,
    WorldDoor,
)


class _Executor:
    def __init__(self, status=200, result=None, fail=False):
        self.status, self.result, self.fail = (
            status,
            result if result is not None else {"ok": True},
            fail,
        )
        self.ran: list[ToolCall] = []

    def run(self, call):
        if self.fail:
            raise RuntimeError("down")
        self.ran.append(call)
        return Executed(self.status, self.result)


def _door(executor=None, roster=None, faults=None, log=None):
    return WorldDoor(
        executor or _Executor(),
        log or MemoryLog(),
        DictRoster(
            roster
            or {"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}}
        ),
        faults=lambda: list(faults or []),
    )


def test_an_actor_action_it_may_not_take_is_refused_and_logged_and_never_reaches_the_world():
    executor = _Executor()
    door = _door(executor)
    outcome = door.enter(
        ToolCall(action="crm.delete", name="delete", actor="manager", key="t:manager:0")
    )
    assert (outcome.outcome, outcome.refusal_code) == ("refused", "not_permitted")
    assert executor.ran == []
    [row] = door.events()
    assert (row["actor_id"], row["outcome"]) == ("manager", "refused")


def test_an_unknown_or_unproven_actor_is_refused():
    door = _door()
    assert (
        door.enter(
            ToolCall(action="crm.approve", name="approve", actor="ghost")
        ).refusal_code
        == "unknown_actor"
    )
    assert door.enter(
        ToolCall(action="crm.approve", name="approve", actor="manager", proven=False)
    ).refusal_code == ("identity_conflict")


def test_an_actor_s_retry_is_answered_from_the_first_result_and_the_world_sees_it_once():
    executor = _Executor(result={"id": 7})
    door = _door(executor)
    first = door.enter(
        ToolCall(
            action="crm.approve",
            name="approve",
            actor="manager",
            key="k",
            arguments={"d": 1},
        )
    )
    again = door.enter(
        ToolCall(
            action="crm.approve",
            name="approve",
            actor="manager",
            key="k",
            arguments={"d": 1},
        )
    )
    other = door.enter(
        ToolCall(
            action="crm.approve",
            name="approve",
            actor="manager",
            key="k",
            arguments={"d": 2},
        )
    )
    assert (first.outcome, again.outcome, again.result) == (
        "committed",
        "duplicate",
        {"id": 7},
    )
    assert other.refusal_code == "idempotency_conflict"
    assert len(executor.ran) == 1


def test_the_agent_s_call_gets_no_permission_check_and_no_deduplication():
    executor = _Executor()
    door = _door(executor)
    for _ in range(2):
        assert (
            door.enter(ToolCall(action="crm.delete", name="delete", key="same")).outcome
            == "committed"
        )
    assert len(executor.ran) == 2


@pytest.mark.parametrize(
    "mode, ran", [("timeout_before_commit", 0), ("timeout_after_commit", 1)]
)
def test_a_scenario_s_fault_applies_to_the_agent_s_named_call_only(mode, ran):
    executor = _Executor()
    door = _door(
        executor,
        faults=[
            {"action": "crm.update", "mode": mode, "on_call": 1, "delay_seconds": 0}
        ],
    )
    outcome = door.enter(ToolCall(action="crm.update", name="update"))
    assert outcome.fault is not None and len(executor.ran) == ran
    # The second call of that action is not faulted.
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is None


def test_the_door_never_raises_into_its_caller_when_the_world_or_the_log_fails():
    class _Broken(MemoryLog):
        def write(self, row):
            raise RuntimeError("log down")

        def rows(self):
            raise RuntimeError("log down")

    door = _door(_Executor(fail=True), log=_Broken())
    outcome = door.enter(ToolCall(action="crm.update", name="update"))
    assert outcome.outcome == "faulted" and outcome.status == 502
    assert door.events() == []


def test_an_unexpected_failure_inside_the_door_comes_back_as_an_outcome():
    class _Unreadable(MemoryLog):
        def records_actors(self):
            raise RuntimeError("cannot say")

    door = _door(log=_Unreadable())
    outcome = door.enter(
        ToolCall(action="crm.approve", name="approve", actor="manager", key="k")
    )
    assert outcome.outcome == "faulted" and "door failed" in outcome.reason


def test_a_slow_log_loses_the_row_never_the_answer():
    class _Slow(MemoryLog):
        def write(self, row):
            import time

            time.sleep(1.0)

    door = WorldDoor(_Executor(), _Slow(), DictRoster({}), write_seconds=0.05)
    import time

    started = time.monotonic()
    assert (
        door.enter(ToolCall(action="crm.update", name="update")).outcome == "committed"
    )
    assert time.monotonic() - started < 0.5


def test_a_record_change_is_routed_only_for_an_actor_permitted_it():
    tools, records = _Executor(), _Executor(result={"changed": 1})
    door = WorldDoor(
        Routed(tools, records),
        MemoryLog(),
        DictRoster(
            {
                "manager": {
                    "permitted_actions": ["approvals.change"],
                    "on_behalf_of": None,
                }
            }
        ),
    )
    outcome = door.enter(
        ToolCall(
            action="approvals.change",
            name="change",
            actor="manager",
            key="k",
            form="record",
            arguments={"where": {"id": 1}, "set": {"status": "approved"}},
        )
    )
    assert outcome.outcome == "committed" and len(records.ran) == 1 and tools.ran == []


def test_the_chat_surface_keeps_an_actor_s_action_out_of_the_agent_s_own_calls():
    from fi.alk.harness.chat_call_runner import chat_door
    from fi.alk.harness.world.runtime import Call

    class _World:
        def __init__(self):
            self.calls = []

        def handle_tool_call(self, call):
            self.calls.append(
                Call(
                    name=call["name"],
                    arguments=dict(call["arguments"]),
                    result={"ok": True},
                )
            )
            return SimpleNamespace(
                content='{"ok": true}', success=True, result={"ok": True}
            )

    world = _World()
    door = chat_door(
        world,
        None,
        {"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}},
        [],
    )
    door.enter(ToolCall(action="crm.approve", name="approve", actor="manager", key="k"))
    door.enter(ToolCall(action="crm.update", name="update"))
    assert [call.name for call in world.calls] == ["update"]
    assert [row["actor_id"] for row in door.events()] == ["manager", AGENT]


def test_a_tee_log_reads_from_memory_even_when_the_mirror_fails():
    class _Down(MemoryLog):
        def write(self, row):
            raise RuntimeError("no table")

    log = TeeLog(MemoryLog(), _Down())
    log.write({"name": "x", "actor_id": AGENT})
    assert [row["name"] for row in log.rows()] == ["x"]


def test_the_in_process_handler_tells_a_missing_tool_as_the_world_does():
    executor = HandlerExecutor(lambda call: None)
    door = WorldDoor(executor, MemoryLog(), DictRoster({}))
    assert (
        door.enter(ToolCall(action="nope", name="nope")).answer()
        == b"no such tool nope"
    )


def test_the_dispatcher_acts_in_process_through_the_door():
    from fi.alk.harness.actor_dispatch import DoorActs

    executor = _Executor()
    door = _door(executor)
    sent = asyncio.run(
        DoorActs(door)(
            "manager", "crm.approve", {"d": 1}, key="t:manager:0", parent="a1"
        )
    )
    assert sent["outcome"] == "committed"
    [row] = door.events("manager")
    assert row["parent_event_id"] == "a1"


def test_a_record_change_to_the_harness_s_own_tables_is_refused_before_any_sql():
    from fi.alk.harness.world_door import RecordExecutor

    ran = []
    executor = RecordExecutor(lambda sql, params: ran.append(sql) or 1)
    for action in ("_alk_actors.change", "_ALK_tool_trace.put", "_alk_trial_faults.put"):
        args = {"where": {"id": "x"}, "set": {"permitted_actions": "[]"}} if action.endswith("change") else {"record": {"id": 1}}
        result = executor.run(ToolCall(action=action, name=action, arguments=args, actor="manager"))
        assert result.status == 403 and "harness's own table" in result.error
    assert ran == []


def test_access_is_a_world_record_the_world_s_rule_reads_so_a_grant_by_an_actor_takes_effect_at_once():
    # The developer may post only once they are a member of the channel. Membership is a world record; the CEO's
    # grant is a record change through the door, and the world's own rule reads it on the developer's next post.
    from fi.alk.harness.world_door import RecordExecutor

    members: set[str] = set()

    def handle(call):
        who = call.arguments.get("author")
        if call.name == "post" and who not in members:
            return False, {"detail": f"{who} is not in #deals"}, "not a member"
        return True, {"posted": True}, "posted"

    def execute(sql, params):
        assert sql.startswith('INSERT INTO "channel_members"')
        members.add(params[0])
        return 1

    roster = {
        "ceo": {"permitted_actions": ["channel_members.put"], "on_behalf_of": None},
        "dev": {"permitted_actions": ["slack.post"], "on_behalf_of": None},
    }
    door = WorldDoor(Routed(HandlerExecutor(handle), RecordExecutor(execute)), MemoryLog(), DictRoster(roster))
    post = ToolCall(action="slack.post", name="post", arguments={"author": "dev"}, actor="dev", key="t:dev:1")
    assert door.enter(post).outcome == "refused"
    grant = ToolCall(action="channel_members.put", name="put", arguments={"record": {"member": "dev"}}, actor="ceo",
                     key="t:ceo:1", form="record")
    assert door.enter(grant).outcome == "committed"
    assert door.enter(replace(post, key="t:dev:2")).outcome == "committed"
    assert [(row["actor_id"], row["outcome"]) for row in door.events()] == [
        ("dev", "refused"), ("ceo", "committed"), ("dev", "committed")
    ]


def test_one_actor_s_key_is_not_another_s_and_a_keyed_row_is_written_before_the_answer():
    roster = {
        "manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None},
        "lead": {"permitted_actions": ["crm.approve"], "on_behalf_of": None},
    }
    executor = _Executor()
    log = MemoryLog()
    door = WorldDoor(executor, log, DictRoster(roster), write_seconds=0.0)
    first = door.enter(ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k"))
    # The same key from another actor is its own action, not a duplicate of the manager's.
    other = door.enter(ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="lead", key="k"))
    again = door.enter(ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k"))
    assert (first.outcome, other.outcome, again.outcome) == ("committed", "committed", "duplicate")
    assert len(executor.ran) == 2
