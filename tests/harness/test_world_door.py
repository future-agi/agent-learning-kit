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


def test_a_chat_agent_s_service_tools_run_in_its_tools_service_for_it_and_for_actors():
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from fi.alk.harness.chat_call_runner import chat_door

    seen = []

    class _Tools(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def do_POST(self):  # noqa: N802
            body = json.loads(self.rfile.read(int(self.headers["content-length"])) or b"{}")
            seen.append((self.path, body, self.headers.get("X-ALK-Actor-Identity")))
            status, answer = (404, {"detail": "no deal"}) if body.get("deal_id") == 0 else (200, {"deal_id": 7})
            content = json.dumps(answer).encode()
            self.send_response(status)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Tools)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    handled = []
    world = SimpleNamespace(
        calls=[], handle_tool_call=lambda call: handled.append(call["name"]) or SimpleNamespace(content="ok", success=True, result="ok")
    )
    door = chat_door(
        world,
        None,
        {"manager": {"permitted_actions": ["chat.post_message"], "on_behalf_of": None}},
        [],
        tools_url=f"http://127.0.0.1:{server.server_address[1]}",
        service_actions={"crm.get_deal"},
    )
    try:
        assert door.enter(ToolCall(action="crm.get_deal", name="get_deal", arguments={"deal_id": 7})).outcome == "committed"
        assert door.enter(ToolCall(action="crm.get_deal", name="get_deal", arguments={"deal_id": 0})).outcome == "refused"
        door.enter(
            ToolCall(action="chat.post_message", name="post_message", actor="manager", key="k", arguments={"t": 1}, identity="mgr")
        )
        # A tool the service does not run keeps its own handler.
        assert door.enter(ToolCall(action="quote", name="quote")).outcome == "committed"
    finally:
        server.shutdown()
    assert handled == ["quote"]
    assert [(path, who) for path, _, who in seen] == [("/crm/get_deal", None), ("/crm/get_deal", None), ("/chat/post_message", "mgr")]
    assert [(call.name, call.ok, call.result) for call in world.calls] == [
        ("get_deal", True, {"deal_id": 7}),
        ("get_deal", False, None),
    ]
    assert world.calls[1].refused and "no deal" in world.calls[1].error
    assert [row["actor_id"] for row in door.events()] == [AGENT, AGENT, "manager", AGENT]


def test_a_chat_agent_s_call_the_tools_service_never_answered_is_still_its_attempt():
    from fi.alk.harness.chat_call_runner import chat_door

    world = SimpleNamespace(calls=[], handle_tool_call=lambda call: None)
    door = chat_door(world, None, {}, [], tools_url="http://127.0.0.1:9", service_actions={"crm.get_deal"})
    with pytest.raises(Exception):
        door.executor.run(ToolCall(action="crm.get_deal", name="get_deal", arguments={"deal_id": 7}))
    assert [(call.name, call.ok) for call in world.calls] == [("get_deal", False)]


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


def test_a_put_takes_the_record_s_fields_as_they_come_or_under_record():
    from fi.alk.harness.world_door import RecordExecutor

    ran = []
    executor = RecordExecutor(lambda sql, params: ran.append((sql, params)) or 1)
    for arguments in ({"deal_id": 7, "status": "approved"}, {"record": {"deal_id": 7, "status": "approved"}}):
        done = executor.run(ToolCall(action="approvals.put", name="put", actor="manager", form="record", arguments=arguments))
        assert done.status == 200
    assert ran == [('INSERT INTO "approvals" ("deal_id", "status") VALUES (%s, %s)', [7, "approved"])] * 2
    flat = executor.run(
        ToolCall(action="approvals.change", name="change", actor="manager", form="record", arguments={"status": "x"})
    )
    assert flat.status == 400 and "`where`" in flat.error


def test_a_record_that_breaks_a_world_constraint_is_refused_and_an_unreachable_world_is_still_a_fault():
    import sqlite3

    from fi.alk.harness.world_door import MemoryLog, RecordExecutor, Routed

    db = sqlite3.connect(":memory:")
    db.execute('CREATE TABLE "channel_members" (member TEXT, channel TEXT, UNIQUE (member, channel))')

    def execute(sql, params):
        return db.execute(sql.replace("%s", "?"), params).rowcount

    door = WorldDoor(Routed(HandlerExecutor(lambda call: None), RecordExecutor(execute)), MemoryLog(),
                     DictRoster({"ceo": {"permitted_actions": ["channel_members.put"]}}))
    grant = {"member": "coworker", "channel": "#deals"}
    first = door.enter(ToolCall(action="channel_members.put", name="put", actor="ceo", form="record", arguments=grant))
    again = door.enter(ToolCall(action="channel_members.put", name="put", actor="ceo", form="record", arguments=grant,
                                key="second"))
    assert first.outcome == "committed"
    assert again.outcome == "refused" and again.status == 409 and "UNIQUE" in again.reason

    class Down(Exception):
        sqlstate = "08006"

    def down(sql, params):
        raise Down("connection lost")

    lost = WorldDoor(Routed(HandlerExecutor(lambda call: None), RecordExecutor(down)), MemoryLog(),
                     DictRoster({"ceo": {"permitted_actions": ["channel_members.put"]}}))
    out = lost.enter(ToolCall(action="channel_members.put", name="put", actor="ceo", form="record", arguments=grant))
    assert out.outcome == "faulted"


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


def test_a_keyed_action_s_row_is_written_before_its_answer_even_on_a_slow_log_so_a_retry_finds_it():
    import time as clock

    class _Slow(MemoryLog):
        def write(self, row):
            clock.sleep(0.3)
            super().write(row)

    roster = {"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}}
    executor = _Executor()
    door = WorldDoor(executor, _Slow(), DictRoster(roster), write_seconds=0.01)
    first = door.enter(ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k"))
    # The retry comes at once: the first row must already be in the log, or the world would be asked twice.
    again = door.enter(ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k"))
    assert (first.outcome, again.outcome) == ("committed", "duplicate")
    assert len(executor.ran) == 1


def _worker_door(executor, limits):
    return WorldDoor(executor, MemoryLog(), DictRoster({}), workers=lambda: limits)


@pytest.mark.parametrize(
    ("limits", "action", "code"),
    [
        ({"permissions": {"refund": ["refunds.issue"]}}, "refunds.issue", None),
        ({"permissions": {"refund": ["refunds.issue"]}}, "orders.cancel", "not_permitted"),
        ({"permissions": {"lookup": ["orders.get"]}}, "refunds.issue", "not_permitted"),
        ({"permissions": {"refund": None}}, "refunds.issue", "not_permitted"),
        ({"deadline_at": 1.0}, "refunds.issue", "deadline_passed"),
        ({"deadline_at": 4_102_444_800.0}, "refunds.issue", None),
        ({}, "refunds.issue", None),
    ],
    ids=["allowed", "not-in-list", "other-worker-only", "unreadable-list", "past-deadline", "before-deadline", "no-limits"],
)
def test_a_worker_s_call_reaches_the_world_only_within_its_allow_list_and_deadline(limits, action, code):
    executor = _Executor()
    door = _worker_door(executor, limits)
    outcome = door.enter(ToolCall(action=action, name=action.rsplit(".", 1)[-1], worker="refund"))
    assert outcome.refusal_code == code
    assert len(executor.ran) == (0 if code else 1)
    [row] = door.events()
    assert (row["actor_id"], row["worker_id"], row["outcome"]) == (
        AGENT,
        "refund",
        "refused" if code else "committed",
    )


def test_a_channel_no_worker_owns_is_refused_and_the_agent_s_own_calls_are_not_limited():
    executor = _Executor()
    door = _worker_door(executor, {"permissions": {}, "deadline_at": 1.0})
    assert door.enter(ToolCall(action="refunds.issue", name="issue", worker="")).refusal_code == "identity_conflict"
    # No channel: the agent's own call, never held to a worker's limits.
    assert door.enter(ToolCall(action="refunds.issue", name="issue")).outcome == "committed"
    assert [row.get("worker_id") for row in door.events()] == [None, None]


def test_worker_limits_that_cannot_be_read_refuse_and_never_widen():
    def broken():
        raise OSError("unreadable")

    executor = _Executor()
    door = WorldDoor(executor, MemoryLog(), DictRoster({}), workers=broken)
    assert door.enter(ToolCall(action="refunds.issue", name="issue", worker="refund")).refusal_code == "not_permitted"
    assert executor.ran == []


def test_a_worker_naming_an_actor_never_borrows_that_actor_s_permissions():
    executor = _Executor()
    door = WorldDoor(
        executor,
        MemoryLog(),
        DictRoster({"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}}),
        workers=lambda: {"permissions": {"lookup": ["orders.get"]}},
    )
    # Over the proxy a request that names an actor is never proven; from a worker's channel it stays the worker's.
    outcome = door.enter(ToolCall(action="crm.approve", name="approve", actor="manager", proven=False, worker="lookup"))
    assert (outcome.outcome, outcome.refusal_code) == ("refused", "identity_conflict")
    assert executor.ran == []
    [row] = door.events()
    assert (row["actor_id"], row["worker_id"]) == (AGENT, "lookup")


def test_a_limited_worker_s_request_for_anything_that_is_not_a_world_action_never_reaches_the_world():
    executor = _Executor()
    door = _worker_door(executor, {"permissions": {"lookup": ["orders.get"]}})
    # The proxy names a route that is no world action `unknown`: a limited worker reaches only its listed actions.
    outcome = door.enter(ToolCall(action="unknown", name="scenarios", worker="lookup"))
    assert (outcome.outcome, outcome.refusal_code) == ("refused", "not_permitted")
    assert executor.ran == []
