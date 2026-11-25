"""The world's door: one rule set for every surface, and no error of its own."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_egress_proxy

from fi.alk.harness.world_door import (
    LIMITS_UNREADABLE,
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

# The Postgres fixture: a fresh action log in its own schema of the test database.
world_log = test_egress_proxy.world_log



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


def test_the_agent_s_call_gets_no_permission_check_and_an_unkeyed_retry_runs_again():
    executor = _Executor()
    door = _door(executor)
    for _ in range(2):
        assert door.enter(ToolCall(action="crm.delete", name="delete")).outcome == "committed"
    assert len(executor.ran) == 2


def test_the_agent_s_keyed_retry_is_answered_from_the_first_result_and_the_world_sees_it_once():
    executor = _Executor(result={"refund": 1})
    door = _door(executor)
    first = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    again = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    assert (first.outcome, again.outcome, again.result) == ("committed", "duplicate", {"refund": 1})
    assert len(executor.ran) == 1
    assert [row["outcome"] for row in door.events(actor=AGENT)] == ["committed", "duplicate"]
    other = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 8}, key="k-1"))
    assert other.refusal_code == "idempotency_conflict" and len(executor.ran) == 1


@pytest.mark.parametrize("recovered", [False, True], ids=["same door", "door restarted on the log"])
def test_the_agent_s_keyed_retry_after_a_lost_answer_is_answered_not_committed_twice(recovered):
    class _Created(_Executor):
        def run(self, call):
            self.ran.append(call)
            return Executed(201, {"refund": 1}, body=b"refund RF-1 created", content_type="text/plain")

    executor = _Created()
    log = MemoryLog()
    faults = [{"action": "orders.refund", "mode": "timeout_after_commit", "on_call": 1, "delay_seconds": 0}]
    door = _door(executor, faults=faults, log=log)
    lost = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    again = _door(executor, faults=faults, log=log) if recovered else door
    retry = again.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    assert lost.fault is not None and lost.outcome == "committed" and lost.status == 504
    assert retry.outcome == "duplicate" and len(executor.ran) == 1
    # The retry hears what the world first answered, never the timeout the fault put in its place.
    assert (retry.status, retry.answer(), retry.content_type) == (201, b"refund RF-1 created", "text/plain")


def test_a_keyed_retry_after_a_write_that_ran_but_whose_answer_was_lost_to_a_reset_never_runs_it_again():
    """The refund commits, then the connection resets before its answer comes back, under a timeout_after_commit
    fault: whether it took effect is unknown, so a retry with the same key is never run again."""

    class _CommitsThenResets:
        def __init__(self):
            self.ran: list[ToolCall] = []

        def run(self, call):
            self.ran.append(call)
            if len(self.ran) == 1:
                raise ConnectionResetError("reset after commit")
            return Executed(200, {"refund": len(self.ran)})

    executor = _CommitsThenResets()
    door = _door(
        executor,
        faults=[{"action": "orders.refund", "mode": "timeout_after_commit", "on_call": 1, "delay_seconds": 0}],
    )

    first = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    retry = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))

    assert first.outcome == "faulted"
    assert retry.outcome == "faulted" and "unknown" in retry.reason
    assert len(executor.ran) == 1


def test_the_agent_s_keyed_retry_after_a_timeout_before_commit_runs_since_nothing_took_effect():
    executor = _Executor()
    door = _door(
        executor,
        faults=[{"action": "orders.refund", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}],
    )
    held = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    retry = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    assert held.outcome == "faulted" and retry.outcome == "committed" and len(executor.ran) == 1


def test_a_door_that_outlives_a_trial_forgets_the_agent_s_keys_from_the_trial_before():
    trial = {"id": "a"}
    executor = _Executor()

    class _NoActors(MemoryLog):
        def records_actors(self):
            return False

    door = WorldDoor(executor, _NoActors(), DictRoster({}), trial=lambda: trial["id"])
    door.enter(ToolCall(action="orders.refund", name="refund", key="k-1"))
    assert door.enter(ToolCall(action="orders.refund", name="refund", key="k-1")).outcome == "duplicate"
    trial["id"] = "b"
    assert door.enter(ToolCall(action="orders.refund", name="refund", key="k-1")).outcome == "committed"
    assert len(executor.ran) == 2


def test_an_unavailable_fault_never_reaches_the_world():
    executor = _Executor()
    door = _door(executor, faults=[{"action": "orders.refund", "mode": "unavailable", "on_call": 1}])
    outcome = door.enter(ToolCall(action="orders.refund", name="refund"))
    assert (outcome.outcome, outcome.status, outcome.reason) == ("faulted", 503, "unavailable")
    assert outcome.delay_seconds == 0.0 and executor.ran == []
    [row] = door.events()
    assert (row["outcome"], row["error"]) == ("faulted", "unavailable")


def test_an_error_fault_answers_the_scenario_s_status_and_message_and_never_reaches_the_world():
    executor = _Executor()
    door = _door(
        executor,
        faults=[{"action": "orders.refund", "mode": "error", "status": 429, "message": "slow down", "on_call": 1}],
    )
    outcome = door.enter(ToolCall(action="orders.refund", name="refund"))
    assert (outcome.outcome, outcome.status, outcome.reason) == ("faulted", 429, "slow down")
    assert outcome.answer() == b'{"detail": "slow down"}' and executor.ran == []
    unset = _door(executor, faults=[{"action": "orders.refund", "mode": "error", "status": 200}])
    assert unset.enter(ToolCall(action="orders.refund", name="refund")).status == 500


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


def test_a_fault_naming_the_tool_holds_its_call_through_the_proxy_and_a_worker_s_channel():
    executor = _Executor()
    door = _door(
        executor,
        faults=[{"action": "get_order", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}],
    )
    # Over HTTP the call is named by its route; its sealed tool is `get_order`.
    first = door.enter(ToolCall(action="orders.get", name="get", tool="get_order", worker="order_lookup"))
    second = door.enter(ToolCall(action="orders.get", name="get", tool="get_order", worker="order_lookup"))
    assert first.fault is not None and second.fault is None
    assert len(executor.ran) == 1


def test_a_fault_is_never_guessed_onto_every_call_when_the_log_cannot_count_the_calls():
    class _Uncountable(MemoryLog):
        def agent_calls(self, action, name):
            raise RuntimeError("log down")

    executor = _Executor()
    door = _door(
        executor,
        faults=[
            {"action": "crm.update", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}
        ],
        log=_Uncountable(),
    )
    outcomes = [door.enter(ToolCall(action="crm.update", name="update")) for _ in range(3)]
    assert [one.fault for one in outcomes] == [None, None, None]
    assert len(executor.ran) == 3


def test_a_one_shot_fault_is_not_taken_again_when_the_faulted_call_s_row_was_not_kept():
    class _LosesFirstRow(MemoryLog):
        lost = False

        def write(self, row):
            if not self.lost:
                self.lost = True
                raise RuntimeError("log down")
            return super().write(row)

    executor = _Executor()
    door = _door(
        executor,
        faults=[
            {"action": "crm.update", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}
        ],
        log=_LosesFirstRow(),
    )
    first = door.enter(ToolCall(action="crm.update", name="update"))
    second = door.enter(ToolCall(action="crm.update", name="update"))
    assert first.fault is not None
    assert second.fault is None and len(executor.ran) == 1


def test_a_door_that_outlives_a_trial_numbers_the_next_trial_s_calls_from_one():
    trial = {"id": "a"}

    class _LosesRows(MemoryLog):
        def write(self, row):
            raise RuntimeError("log down")

    executor = _Executor()
    door = WorldDoor(
        executor,
        _LosesRows(),
        DictRoster({}),
        faults=lambda: [
            {"action": "crm.update", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}
        ],
        trial=lambda: trial["id"],
    )
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is not None
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is None
    trial["id"] = "b"
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is not None


def test_the_door_never_raises_into_its_caller_when_the_world_or_the_log_fails():
    class _Broken(MemoryLog):
        def write(self, row):
            raise RuntimeError("log down")

        def rows(self):
            raise RuntimeError("log down")

    door = _door(_Executor(fail=True), log=_Broken())
    outcome = door.enter(ToolCall(action="crm.update", name="update"))
    assert outcome.outcome == "faulted" and outcome.status == 502
    from fi.alk.harness.world_door import EvidenceUnavailable

    # The read side says the evidence is missing rather than reading as an empty log.
    with pytest.raises(EvidenceUnavailable):
        door.events()


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

        def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
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


@pytest.mark.parametrize("broken", ["write", "read", "both"])
def test_a_keyed_action_runs_once_even_when_its_log_cannot_be_written_or_read(broken):
    class _Failing(MemoryLog):
        def write(self, row):
            if broken in ("write", "both"):
                raise RuntimeError("log down")
            super().write(row)

        def committed(self, key, actor):
            if broken in ("read", "both"):
                raise RuntimeError("log down")
            return super().committed(key, actor)

    roster = {"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}}
    executor = _Executor()
    door = WorldDoor(executor, _Failing(), DictRoster(roster), write_seconds=0.0)
    call = ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k")
    first = door.enter(call)
    again = door.enter(call)
    assert len(executor.ran) <= 1
    if broken == "write":
        # The door remembers the answer it gave even though the log lost the row.
        assert (first.outcome, again.outcome) == ("committed", "duplicate")
    else:
        # The log cannot say whether the key ran: never run, never run twice.
        assert executor.ran == [] and {first.outcome, again.outcome} == {"faulted"}


def test_a_keyed_action_whose_row_was_lost_is_not_run_again_by_the_next_door():
    """The world applied the action but the log refused its row: a later door over the same world finds the key
    claimed and never runs it again."""

    class _WriteFails(MemoryLog):
        refuse = True

        def write(self, row):
            if self.refuse:
                raise RuntimeError("log write refused")
            super().write(row)

    roster = {"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}}
    executor = _Executor()
    log = _WriteFails()
    call = ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k")
    first = WorldDoor(executor, log, DictRoster(roster), write_seconds=0.0).enter(call)
    log.refuse = False
    retry = WorldDoor(executor, log, DictRoster(roster), write_seconds=0.0).enter(call)
    assert first.outcome == "committed"
    assert retry.outcome == "faulted" and "unknown" in retry.reason
    assert len(executor.ran) == 1


def test_a_refused_keyed_action_releases_its_key_for_a_retry_by_the_next_door():
    class _RefusesFirst(_Executor):
        def run(self, call):
            self.ran.append(call)
            return Executed(403, {"detail": "not yet"}) if len(self.ran) == 1 else Executed(200, {"ok": True})

    roster = {"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}}
    log = MemoryLog()
    executor = _RefusesFirst()
    call = ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k")
    refused = WorldDoor(executor, log, DictRoster(roster), write_seconds=0.0).enter(call)
    retry = WorldDoor(executor, log, DictRoster(roster), write_seconds=0.0).enter(call)
    assert (refused.outcome, retry.outcome) == ("refused", "committed")
    assert len(executor.ran) == 2


def test_a_keyed_action_the_world_failed_mid_way_is_never_run_again():
    class _Flaky(_Executor):
        def run(self, call):
            self.ran.append(call)
            if len(self.ran) == 1:
                raise RuntimeError("connection reset after the write")
            return Executed(200, {"ok": True})

    roster = {"manager": {"permitted_actions": ["crm.approve"], "on_behalf_of": None}}
    executor = _Flaky()
    door = WorldDoor(executor, MemoryLog(), DictRoster(roster), write_seconds=0.0)
    call = ToolCall(action="crm.approve", name="approve", arguments={"deal": 7}, actor="manager", key="k")
    assert door.enter(call).outcome == "faulted"
    retry = door.enter(call)
    assert retry.outcome == "faulted" and "unknown" in retry.reason
    assert len(executor.ran) == 1


def test_two_calls_at_once_never_both_take_a_one_shot_fault():
    import threading
    import time as clock

    class _SlowCount(MemoryLog):
        def agent_calls(self, action, name, method=None):
            made = super().agent_calls(action, name, method)
            # Both calls read the count before either row lands, unless the door holds them apart.
            clock.sleep(0.05)
            return made

    executor = _Executor()
    door = WorldDoor(
        executor,
        _SlowCount(),
        DictRoster({}),
        faults=lambda: [{"action": "crm.approve", "on_call": 1, "mode": "timeout_before_commit"}],
        write_seconds=0.0,
    )
    door_outcomes = []
    calls = [
        threading.Thread(
            target=lambda: door_outcomes.append(
                door.enter(ToolCall(action="crm.approve", name="approve", arguments={"deal": 7})).outcome
            )
        )
        for _ in range(2)
    ]
    for one in calls:
        one.start()
    for one in calls:
        one.join()
    assert sorted(door_outcomes) == ["committed", "faulted"]
    assert len(executor.ran) == 1


@pytest.mark.parametrize("identity", ["U-priya", ""])
def test_an_actor_never_acts_under_another_s_actor_id_on_any_channel(identity):
    from fi.alk.harness.actor_dispatch import DoorActs

    contract = {
        "tools": [{"name": "approve", "args": ["deal", "actor_id"]}],
        "tool_entrypoints": [{"tool": "approve", "endpoint": "/crm/approve"}],
    }
    executor = _Executor()
    door = _door(executor)
    asyncio.run(
        DoorActs(door, contract)(
            "manager",
            "crm.approve",
            {"deal": 7, "actor_id": "U-admin"},
            key="t:manager:0",
            parent=None,
            **({"identity": identity} if identity else {}),
        )
    )
    # The model wrote someone else's id; the world sees the acting actor's own.
    assert executor.ran[0].arguments == {"deal": 7, "actor_id": identity or "manager"}


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


def test_worker_limits_that_cannot_be_read_refuse_as_the_world_s_failure_never_the_worker_s():
    def broken():
        raise OSError("unreadable")

    executor = _Executor()
    door = WorldDoor(executor, MemoryLog(), DictRoster({}), workers=broken)
    refused = door.enter(ToolCall(action="refunds.issue", name="issue", worker="refund"))
    # Never widened, and never read as the worker breaking its allow-list or deadline.
    assert refused.outcome == "refused" and executor.ran == []
    assert refused.refusal_code not in ("not_permitted", "deadline_passed")


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


def test_a_templated_allowance_never_reaches_another_tool_s_own_route_or_another_method():
    executor = _Executor()
    limits = {
        "permissions": {"lookup": ["GET orders.{order_id}"]},
        "routes": ["GET orders.{order_id}", "POST orders.update_status", "POST orders.cancel"],
    }
    door = _worker_door(executor, limits)

    def enter(method, action):
        return door.enter(ToolCall(action=action, name=action.rsplit(".", 1)[-1], method=method, worker="lookup"))

    assert enter("GET", "orders.1043").outcome == "committed"
    # A path that is another tool's own route is that tool, never a value of the template.
    assert enter("POST", "orders.update_status").refusal_code == "not_permitted"
    assert enter("GET", "orders.cancel").refusal_code == "not_permitted"
    # The same path with another method is another action.
    assert enter("DELETE", "orders.1043").refusal_code == "not_permitted"
    assert len(executor.ran) == 1


def test_a_templated_allowance_never_reaches_a_route_hidden_behind_an_encoded_separator_or_dots():
    executor = _Executor()
    limits = {"permissions": {"lookup": ["GET orders.{order_id}"]}, "routes": ["GET orders.{order_id}"]}
    door = _worker_door(executor, limits)

    def enter(action):
        return door.enter(ToolCall(action=action, name="x", method="GET", worker="lookup"))

    # The service behind the proxy decodes these into another route than the one the door checked.
    for action in ("orders.1043%2Fpayment", "orders.1043%2fpayment", "orders.%2E%2E", "orders....admin"):
        assert enter(action).refusal_code == "not_permitted", action
    assert enter("orders.1043").outcome == "committed"
    assert len(executor.ran) == 1


def test_an_actor_s_templated_permission_never_reaches_another_tool_s_own_route():
    executor = _Executor()
    door = WorldDoor(
        executor,
        MemoryLog(),
        DictRoster({"manager": {"permitted_actions": ["crm.{deal_id}"], "on_behalf_of": None}}),
        workers=lambda: {"routes": ["GET crm.{deal_id}", "POST crm.delete_all"]},
    )

    def enter(action, method="GET"):
        return door.enter(ToolCall(action=action, name="x", actor="manager", method=method, key=f"t:m:{action}"))

    assert enter("crm.8").outcome == "committed"
    # `crm.delete_all` is the world's own route, never a deal id.
    assert enter("crm.delete_all", "POST").refusal_code == "not_permitted"


def test_an_actor_s_templated_permission_is_refused_when_the_world_s_routes_cannot_be_read():
    executor = _Executor()

    def unreadable():
        raise OSError("actions.json unreadable")

    roster = DictRoster(
        {
            "manager": {"permitted_actions": ["crm.{deal_id}"], "on_behalf_of": None},
            "clerk": {"permitted_actions": ["crm.approve"], "on_behalf_of": None},
        }
    )
    door = WorldDoor(executor, MemoryLog(), roster, workers=unreadable)

    def enter(actor, action, method="GET"):
        return door.enter(ToolCall(action=action, name="x", actor=actor, method=method, key=f"t:{actor}:{action}"))

    # Without the routes, `crm.{deal_id}` could stand for `crm.delete_all`: refused as the world's failure.
    refused = enter("manager", "crm.delete_all", "POST")
    assert (refused.outcome, refused.refusal_code) == ("refused", LIMITS_UNREADABLE)
    # A literal permission needs no routes to be read.
    assert enter("clerk", "crm.approve").outcome == "committed"
    assert len(executor.ran) == 1


def test_an_actor_s_action_with_an_encoded_separator_or_dots_is_refused():
    executor = _Executor()
    door = WorldDoor(
        executor,
        MemoryLog(),
        DictRoster({"manager": {"permitted_actions": ["crm.{deal_id}"], "on_behalf_of": None}}),
        workers=lambda: {"routes": ["GET crm.{deal_id}"]},
    )
    for action in ("crm.%2e%2e", "crm.a%2fb", "crm.."):
        outcome = door.enter(ToolCall(action=action, name="x", actor="manager", method="GET", key=f"t:{action}"))
        assert outcome.refusal_code == "not_permitted"
    assert executor.ran == []


def test_two_actions_with_one_last_part_are_numbered_apart_for_a_fault():
    executor = _Executor()
    door = _door(
        executor,
        faults=[{"action": "crm.update", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}],
    )
    # `ticket.update` is not a call of `crm.update`, though the two share their last part.
    assert door.enter(ToolCall(action="ticket.update", name="update")).fault is None
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is not None
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is None
    assert [row["action"] for row in door.events()] == ["ticket.update", "crm.update", "crm.update"]


def test_a_door_whose_log_lost_its_rows_still_numbers_each_action_apart():
    class _LosesRows(MemoryLog):
        def write(self, row):
            raise RuntimeError("log down")

    door = _door(
        faults=[{"action": "crm.update", "mode": "timeout_before_commit", "on_call": 2, "delay_seconds": 0}],
        log=_LosesRows(),
    )
    door.faults = lambda: [
        {"action": "crm.update", "mode": "timeout_before_commit", "on_call": 2, "delay_seconds": 0},
        {"action": "ticket.update", "mode": "timeout_before_commit", "on_call": 9, "delay_seconds": 0},
    ]
    assert door.enter(ToolCall(action="ticket.update", name="update")).fault is None
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is None
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is not None


def test_a_row_written_before_actions_were_recorded_is_counted_by_its_name():
    log = MemoryLog()
    log.write({"name": "update", "actor_id": AGENT, "outcome": "committed"})
    door = _door(
        faults=[{"action": "crm.update", "mode": "timeout_before_commit", "on_call": 2, "delay_seconds": 0}],
        log=log,
    )
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is not None


def test_one_action_s_faulted_call_never_holds_up_another_action_with_the_same_last_part():
    import threading

    release = threading.Event()

    class _Holds(_Executor):
        def run(self, call):
            if call.action == "ticket.update":
                release.wait(5)
            return super().run(call)

    door = _door(
        _Holds(),
        faults=[
            {"action": "ticket.update", "mode": "timeout_before_commit", "on_call": 9, "delay_seconds": 0},
            {"action": "crm.update", "mode": "timeout_before_commit", "on_call": 9, "delay_seconds": 0},
        ],
    )
    held = threading.Thread(target=lambda: door.enter(ToolCall(action="ticket.update", name="update")))
    held.start()
    try:
        done = threading.Event()
        other = threading.Thread(
            target=lambda: (door.enter(ToolCall(action="crm.update", name="update")), done.set())
        )
        other.start()
        assert done.wait(2), "crm.update waited on ticket.update's lock"
    finally:
        release.set()
        held.join()
        other.join()


def test_a_key_reused_for_another_action_is_a_conflict_never_the_first_action_s_answer():
    executor = _Executor(result={"approved": 7})
    roster = {"manager": {"permitted_actions": ["crm.approve", "crm.reject"], "on_behalf_of": None}}
    log = MemoryLog()
    door = _door(executor, roster=roster, log=log)
    assert door.enter(ToolCall(action="crm.approve", name="approve", actor="manager", key="k")).outcome == "committed"
    other = door.enter(ToolCall(action="crm.reject", name="reject", actor="manager", key="k"))
    assert (other.outcome, other.refusal_code, other.result) == ("refused", "idempotency_conflict", None)
    # A later door reads the first action from the log and refuses the same way.
    later = _door(executor, roster=roster, log=log)
    again = later.enter(ToolCall(action="crm.reject", name="reject", actor="manager", key="k"))
    assert again.refusal_code == "idempotency_conflict"
    assert len(executor.ran) == 1
    assert later.enter(ToolCall(action="crm.approve", name="approve", actor="manager", key="k")).outcome == "duplicate"


def test_a_new_door_on_the_same_log_counts_only_the_fault_s_own_action():
    log = MemoryLog()
    _door(log=log).enter(ToolCall(action="ticket.update", name="update"))
    door = _door(
        faults=[{"action": "crm.update", "mode": "timeout_before_commit", "on_call": 1, "delay_seconds": 0}], log=log
    )
    assert door.enter(ToolCall(action="crm.update", name="update")).fault is not None


@pytest.mark.parametrize(
    ("call", "effect"),
    [
        (ToolCall(action="orders.get", name="get", method="GET"), "read"),
        (ToolCall(action="orders.get", name="get", method="head"), "read"),
        (ToolCall(action="orders.search", name="search", method="POST", effect="read"), "read"),
        (ToolCall(action="orders.refund", name="refund", method="POST"), "write"),
        (ToolCall(action="orders.refund", name="refund", method="GET", effect="write"), "write"),
        (ToolCall(action="approvals.change", name="change", actor="manager", form="record", method="GET"), "write"),
    ],
)
def test_each_call_is_logged_as_a_read_or_a_write_from_its_declared_effect_else_its_method(call, effect):
    door = _door(roster={"manager": {"permitted_actions": ["approvals.change"], "on_behalf_of": None}})
    assert door.enter(call).outcome == "committed"
    [row] = door.events()
    assert row["effect"] == effect


def test_events_raise_when_the_log_cannot_be_read():
    from fi.alk.harness.world_door import EvidenceUnavailable

    class _Unreadable(MemoryLog):
        def rows(self):
            raise RuntimeError("no read grant")

    door = _door(log=_Unreadable())
    with pytest.raises(EvidenceUnavailable):
        door.events()


def test_events_hold_only_actions_attempted_in_the_world_never_stopped_egress_or_unrecorded_marks():
    from fi.alk.harness.egress_proxy import REFUSAL_CODE as EGRESS_BLOCKED
    from fi.alk.harness.world_door import EVIDENCE_INCOMPLETE

    log = MemoryLog()
    for row in (
        {"name": "telemetry.example.net", "actor_id": "agent", "outcome": "refused", "refusal_code": EGRESS_BLOCKED},
        {"name": "update", "actor_id": "agent", "outcome": "unrecorded", "refusal_code": EVIDENCE_INCOMPLETE},
        {"name": "update", "actor_id": "agent", "outcome": "committed", "refusal_code": None},
    ):
        log.write(row)
    door = _door(log=log)
    assert [(row["name"], row["outcome"]) for row in door.events(actor="agent")] == [("update", "committed")]
    assert door.events(outcome="refused") == []


def test_a_fault_on_one_action_is_never_used_up_by_another_with_the_same_last_name_part():
    """An on_call 1 fault on crm.update fires on crm.update's first call, though ticket.update (also `update`) ran
    first; and with only ticket.update called, the notice says crm.update was never called, never that it was."""
    from fi.alk.harness.call_runner import fault_notices
    from fi.alk.harness.world.runtime import Call

    faults = [{"action": "crm.update", "mode": "unavailable", "on_call": 1}]
    door = _door(_Executor(), faults=faults)

    ticket = door.enter(ToolCall(action="ticket.update", name="update", arguments={"id": 1}))
    crm = door.enter(ToolCall(action="crm.update", name="update", arguments={"id": 1}))

    assert (ticket.outcome, crm.outcome) == ("committed", "faulted")
    assert [(row["action"], row["outcome"]) for row in door.events()] == [
        ("ticket.update", "committed"),
        ("crm.update", "faulted"),
    ]
    assert fault_notices(faults, door.events(), [Call(name="update", arguments={}), Call(name="update", arguments={})]) == []

    only_ticket = _door(_Executor(), faults=faults)
    only_ticket.enter(ToolCall(action="ticket.update", name="update", arguments={"id": 1}))
    [notice] = fault_notices(faults, only_ticket.events(), [Call(name="update", arguments={})])
    assert notice["code"] == "fault_not_triggered" and "made 0 calls" in notice["message"]


def test_a_keyed_retry_after_an_approval_that_could_not_be_read_runs_since_nothing_took_effect(monkeypatch):
    executor = _Executor()
    door = _door(
        executor,
        faults=[{"action": "orders.refund", "mode": "approval_required", "approval": "crm.approve"}],
    )
    answers = iter([RuntimeError("approvals table down"), True])

    def approved(fault):
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(door, "_approved", approved)
    held = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    retry = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))

    assert (held.outcome, held.refusal_code) == ("faulted", "approval_unknown")
    assert retry.outcome == "committed" and len(executor.ran) == 1


def test_a_write_is_held_while_the_trial_s_faults_cannot_be_read_and_a_read_still_runs():
    """A hold may be among faults the door cannot read, so the agent's write never commits past it; a read changes
    nothing and runs. Nothing ran, so the key stays free for a retry once the faults read again."""
    executor = _Executor()
    readable = iter([False, False, True])

    def faults():
        if not next(readable):
            raise RuntimeError("world database down")
        return []

    door = WorldDoor(executor, MemoryLog(), DictRoster({}), faults=faults)
    held = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))
    read = door.enter(ToolCall(action="orders.find", name="find", arguments={"order": 7}, effect="read"))
    retry = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}, key="k-1"))

    assert (held.outcome, held.refusal_code) == ("faulted", "faults_unknown")
    assert read.outcome == "committed"
    assert retry.outcome == "committed"
    assert [call.action for call in executor.ran] == ["orders.find", "orders.refund"]


@pytest.mark.parametrize(
    ("tool", "endpoint", "action", "method"),
    [
        ("issue_refund", "/orders/refund/", "orders.refund", "POST"),
        ("find_order", "/orders/{order_id}", "orders.1043", "GET"),
    ],
    ids=["route", "parameterized GET route"],
)
def test_a_fault_the_platform_names_by_the_tool_holds_the_call_the_http_door_sees_on_its_route(
    tool, endpoint, action, method
):
    from fi.alk.harness.world_door import routed_faults

    contract = {"tool_entrypoints": [{"tool": tool, "endpoint": endpoint}]}
    faults = routed_faults([{"action": tool, "mode": "unavailable"}], contract)
    executor = _Executor()
    door = _door(executor, faults=faults)

    outcome = door.enter(ToolCall(action=action, name=action.rsplit(".", 1)[-1], arguments={}, method=method))

    assert outcome.outcome == "faulted" and executor.ran == []
    # Another service's route with the same last part is never held by it.
    other = door.enter(ToolCall(action="tickets.refund", name="refund", arguments={}, method=method))
    assert other.outcome == "committed"


def test_a_fault_written_as_the_route_s_path_holds_the_call_and_is_never_an_unknown_action():
    from fi.alk.harness.call_runner import fault_notices
    from fi.alk.harness.world_door import routed_faults

    contract = {"tool_entrypoints": [{"tool": "apply_discount", "endpoint": "/crm/apply_discount", "mode": "import"}]}
    faults = routed_faults([{"action": "crm/apply_discount", "mode": "error", "status": 403}], contract)
    executor = _Executor()
    door = _door(executor, faults=faults)

    outcome = door.enter(ToolCall(action="crm.apply_discount", name="apply_discount", arguments={}, method="POST"))

    assert outcome.outcome == "faulted" and executor.ran == []
    notices = fault_notices(faults, [], [], contract=contract)
    assert not [one for one in notices if one["code"] == "fault_unknown_action"]
    # A method or a host around the path is not a route's path: the fault names no action, so the trial is unmeasured.
    for written in ("POST /crm/apply_discount", "https://host/crm/apply_discount"):
        kept = routed_faults([{"action": written, "mode": "error", "status": 403}], contract)
        assert kept[0]["action"] == written
        notices = fault_notices(kept, [], [], contract=contract)
        assert [one["code"] for one in notices] == ["fault_unknown_action"]


def test_a_keyed_retry_with_true_where_the_first_sent_1_is_another_write_never_its_duplicate():
    executor = _Executor()
    door = _door(executor)
    first = door.enter(ToolCall(action="settings.update", name="update", arguments={"enabled": 1}, key="k-1"))
    again = door.enter(ToolCall(action="settings.update", name="update", arguments={"enabled": True}, key="k-1"))

    assert first.outcome == "committed"
    assert (again.outcome, again.refusal_code) == ("refused", "idempotency_conflict")


def test_an_approval_recorded_with_true_does_not_meet_an_approved_when_of_1():
    door = _door(
        faults=[{"action": "orders.refund", "mode": "approval_required", "approval": "crm.approve",
                 "approved_when": {"order_id": 1}}]
    )
    door.enter(ToolCall(action="crm.approve", name="approve", arguments={"order_id": True}, actor="manager"))

    held = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order_id": 1}))

    assert (held.outcome, held.refusal_code) == ("refused", "approval_missing")


@pytest.mark.parametrize(
    ("status", "body", "content_type"),
    [(201, b"created order 7", "text/plain"), (204, b"", None)],
    ids=["201 text/plain", "204 no content"],
)
@pytest.mark.parametrize("recovered", [False, True], ids=["same door", "door restarted on the log"])
def test_a_keyed_retry_is_answered_with_the_first_call_s_status_body_and_type(status, body, content_type, recovered):
    class _Answering(_Executor):
        def run(self, call):
            self.ran.append(call)
            return Executed(status, None, body=body, content_type=content_type)

    executor = _Answering()
    log = MemoryLog()
    first_door = _door(executor, log=log)
    call = ToolCall(action="orders.create", name="create", arguments={"order": 7}, key="k-1")
    first = first_door.enter(call)
    retry = (_door(executor, log=log) if recovered else first_door).enter(call)

    assert first.outcome == "committed" and retry.outcome == "duplicate" and len(executor.ran) == 1
    assert (retry.status, retry.answer(), retry.content_type) == (status, body, content_type)


def test_a_fault_on_call_2_is_past_once_a_door_mark_counts_two_calls_made_while_it_was_out_of_reach():
    from fi.alk.harness.world_door import DOOR_OUT_OF_REACH, EVIDENCE_INCOMPLETE

    log = MemoryLog()
    log.write({"name": "refund", "action": "orders.refund", "actor_id": "agent", "outcome": "unrecorded",
               "error": DOOR_OUT_OF_REACH, "refusal_code": EVIDENCE_INCOMPLETE, "arguments": {"count": 2}})
    executor = _Executor()
    door = _door(executor, log=log, faults=[{"action": "orders.refund", "mode": "unavailable", "on_call": 2}])

    third = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order": 7}))

    assert third.outcome == "committed" and len(executor.ran) == 1


def test_an_actor_s_change_whose_where_names_a_null_field_reaches_the_record_holding_null():
    import sqlite3

    from fi.alk.harness.world_door import RecordExecutor, Routed

    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute('CREATE TABLE "tickets" (id TEXT, assigned_to TEXT, status TEXT)')
    db.execute("INSERT INTO tickets VALUES ('t1', NULL, 'open')")
    db.execute("INSERT INTO tickets VALUES ('t2', 'sam', 'open')")
    door = WorldDoor(
        Routed(None, RecordExecutor(lambda sql, params: db.execute(sql.replace("%s", "?"), params).rowcount)),
        MemoryLog(),
        DictRoster({"lead": {"permitted_actions": ["tickets.change"]}}),
    )

    outcome = door.enter(
        ToolCall(
            action="tickets.change",
            name="change",
            arguments={"where": {"assigned_to": None}, "set": {"status": "triaged"}},
            actor="lead",
            form="record",
        )
    )

    assert outcome.outcome == "committed", outcome
    assert db.execute("SELECT id, status FROM tickets ORDER BY id").fetchall() == [("t1", "triaged"), ("t2", "open")]


def test_the_durable_log_counts_a_mark_s_calls_and_replays_a_keyed_answer(world_log):
    """On the world's own table: a mark for two calls counts two, and a door started fresh on the log answers a keyed
    retry with the first call's status, type and body."""
    from fi.alk.harness.world_door import (
        DOOR_OUT_OF_REACH,
        EVIDENCE_INCOMPLETE,
        PostgresLog,
    )

    dsn, _rows = world_log
    log = PostgresLog(dsn)
    # Calls counted by the method they were made with, on a table that gains the column when first written.
    log.write({"name": "1043", "action": "orders.1043", "arguments": {}, "result": None, "ok": True, "error": "",
               "at": 0.5, "action_id": "g1", "outcome": "committed", "actor_id": "agent", "method": "GET"})
    assert (log.agent_calls("orders.1043", "1043", "DELETE"), log.agent_calls("orders.1043", "1043", "GET")) == (0, 1)
    # A route with a part counts each call of it, whichever part the call named.
    assert log.agent_calls("orders.{order_id}", "x", "GET") == 1
    log.write({"name": "refund", "action": "orders.refund", "arguments": {"count": 2}, "result": None, "ok": False,
               "error": DOOR_OUT_OF_REACH, "at": 1.0, "action_id": "m1", "outcome": "unrecorded",
               "refusal_code": EVIDENCE_INCOMPLETE, "actor_id": "agent"})
    assert log.agent_calls("orders.refund", "refund") == 2

    class _Answering(_Executor):
        def run(self, call):
            self.ran.append(call)
            return Executed(201, None, body=b"created", content_type="text/plain")

    executor = _Answering()
    call = ToolCall(action="orders.create", name="create", arguments={"order": 7}, key="k-1")
    WorldDoor(executor, PostgresLog(dsn), DictRoster({})).enter(call)
    retry = WorldDoor(executor, PostgresLog(dsn), DictRoster({})).enter(call)

    assert len(executor.ran) == 1 and retry.outcome == "duplicate"
    assert (retry.status, retry.answer(), retry.content_type) == (201, b"created", "text/plain")


def test_a_fault_on_the_delete_of_a_route_never_holds_the_get_of_the_same_route():
    from fi.alk.harness.world_door import routed_faults

    contract = {
        "tool_entrypoints": [
            {"tool": "get_order", "endpoint": "/orders/{order_id}", "method": "GET"},
            {"tool": "cancel_order", "endpoint": "/orders/{order_id}", "method": "DELETE"},
        ]
    }
    executor = _Executor()
    door = _door(executor, faults=routed_faults([{"action": "cancel_order", "mode": "unavailable"}], contract))

    read = door.enter(ToolCall(action="orders.1043", name="1043", arguments={}, method="GET", effect="read"))
    cancel = door.enter(ToolCall(action="orders.1043", name="1043", arguments={}, method="DELETE"))

    assert (read.outcome, cancel.outcome) == ("committed", "faulted")


def test_a_keyed_retry_sending_89_0_for_89_is_the_same_write_but_true_for_1_is_not():
    executor = _Executor()
    door = _door(executor)
    door.enter(ToolCall(action="orders.refund", name="refund", arguments={"amount": 89, "notify": 1}, key="k-1"))

    same = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"amount": 89.0, "notify": 1}, key="k-1"))
    other = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"amount": 89, "notify": True}, key="k-1"))

    assert same.outcome == "duplicate" and len(executor.ran) == 1
    assert (other.outcome, other.refusal_code) == ("refused", "idempotency_conflict")


def test_a_mark_s_count_reads_the_same_in_memory_as_in_the_durable_log():
    from fi.alk.harness.world_door import DOOR_OUT_OF_REACH, calls_in

    def mark(count):
        return {"error": DOOR_OUT_OF_REACH, "arguments": {"count": count}}

    assert [calls_in(mark(one)) for one in (2, 2.0, 2.5, "2", True, None)] == [2, 2, 3, 1, 1, 1]


def _get_and_delete():
    from fi.alk.harness.world_door import routed_faults

    contract = {
        "tool_entrypoints": [
            {"tool": "get_order", "endpoint": "/orders/{order_id}", "method": "GET"},
            {"tool": "cancel_order", "endpoint": "/orders/{order_id}", "method": "DELETE"},
        ]
    }
    return routed_faults([{"action": "cancel_order", "mode": "unavailable", "on_call": 1}], contract)


def test_the_trial_reads_a_delete_s_fault_as_applied_though_a_get_of_the_route_came_first():
    from fi.alk.harness.call_runner import fault_notices, unmeasured_reasons
    from fi.alk.harness.world.runtime import Call

    faults = _get_and_delete()
    log = MemoryLog()
    door = _door(_Executor(), faults=faults, log=log)
    door.enter(ToolCall(action="orders.1043", name="1043", arguments={}, method="GET", effect="read"))
    door.enter(ToolCall(action="orders.1043", name="1043", arguments={}, method="DELETE"))

    calls = [Call(name="get_order", arguments={}, ok=True), Call(name="cancel_order", arguments={}, ok=False)]
    notices = fault_notices(faults, log.rows(), calls)

    assert notices == [] and not unmeasured_reasons(notices)


def test_a_door_restarted_mid_trial_never_applies_a_method_s_first_call_fault_again():
    faults = _get_and_delete()
    log = MemoryLog()
    executor = _Executor()
    first = _door(executor, faults=faults, log=log).enter(
        ToolCall(action="orders.1043", name="1043", arguments={}, method="DELETE")
    )

    again = _door(executor, faults=faults, log=log).enter(
        ToolCall(action="orders.1044", name="1044", arguments={}, method="DELETE")
    )

    assert (first.outcome, again.outcome) == ("faulted", "committed")


def test_a_log_table_from_before_workers_were_recorded_still_keeps_every_row():
    from fi.alk.harness.world_door import PostgresLog

    written = []

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def execute(self, sql, values=None):
            written.append((sql, values))

    class _OldTable(PostgresLog):
        """An action log that records actors, made before worker ids and the agent's tool were columns."""

        def _table_columns(self):
            return frozenset(
                "name arguments result ok error at action_id outcome refusal refusal_code idempotency_key actor_id "
                "parent_event_id on_behalf_of".split()
            )

        def _ensure_call_columns(self):
            return None

        def _connect(self, **_options):
            return _Connection()

    _OldTable("postgresql://unused").write(
        {"name": "refund", "actor_id": "agent", "worker_id": "refund", "agent_tool": "issue_refund", "at": 1.0}
    )
    [(sql, values)] = written
    # Columns the table lacks are never named: the insert would fail and every row would be lost.
    assert "worker_id" not in sql and "agent_tool" not in sql
    assert "refund" in values


def test_a_row_the_log_cannot_write_is_counted_never_silently_dropped():
    class _Failing(MemoryLog):
        def write(self, row):
            raise RuntimeError("relation is read-only")

    door = WorldDoor(_Executor(), _Failing(), DictRoster({}))
    outcome = door.enter(ToolCall(action="orders.refund", name="refund", arguments={"order_id": "1048"}))
    # The agent's call still gets its answer; the lost row is counted for the trial's evidence.
    assert outcome.outcome == "committed"
    assert door.unwritten == 1


def test_a_worker_is_due_at_the_earlier_of_its_own_deadline_and_the_trial_s():
    from fi.alk.harness.world_door import worker_due

    # The door refuses at whichever comes first, so the stop comes then too.
    assert worker_due(200.0, 150.0) == 150.0
    assert worker_due(100.0, 150.0) == 100.0
    assert worker_due(None, 150.0) == 150.0
    assert worker_due(100.0, None) == 100.0
    assert worker_due(None, None) is None
    assert worker_due(True, None) is None


@pytest.mark.parametrize("builder", ["runner", "record", "chat"])
def test_the_runner_s_doors_check_an_actor_s_template_against_the_sealed_routes(tmp_path, builder):
    """Through the doors the runners build for actors: a sealed route is never a template's value, and a sealed file
    that cannot be read refuses the template as the world's failure."""
    from fi.alk.harness.call_runner import record_door, runner_door
    from fi.alk.harness.chat_call_runner import chat_door

    sealed = tmp_path / "generated" / "tool-proxy"
    sealed.mkdir(parents=True)
    (sealed / "actions.json").write_text(json.dumps({"GET orders.{order_id}": "get_order", "GET orders.export": "export"}))
    roster = {"manager": {"permitted_actions": ["GET orders.{order_id}"], "on_behalf_of": None}}

    def door():
        if builder == "runner":
            built = runner_door("http://tools", "postgresql://unused", 5.0, tmp_path)
        elif builder == "record":
            built = record_door("postgresql://unused", tmp_path)
        else:
            built = chat_door(object(), None, roster, [], bundle_dir=tmp_path)
        built.executor, built.log, built.roster = _Executor(), MemoryLog(), DictRoster(roster)
        return built

    def enter(one, action):
        return one.enter(ToolCall(action=action, name="x", actor="manager", method="GET", key=f"k:{action}"))

    open_door = door()
    assert enter(open_door, "orders.1043").outcome == "committed"
    # `orders.export` is the world's own route, never an order id.
    assert enter(open_door, "orders.export").refusal_code == "not_permitted"
    (sealed / "actions.json").write_text("{not json")
    assert enter(door(), "orders.1043").refusal_code == LIMITS_UNREADABLE


def test_a_key_the_log_cannot_check_is_our_failure_and_one_a_test_condition_lost_is_the_agent_s():
    from fi.alk.harness.world_door import KEY_UNKNOWN, LOG_UNKNOWN, WORLD_UNREACHABLE, world_failed

    class _LogDown(MemoryLog):
        def committed(self, key, actor):
            raise RuntimeError("log down")

    door = _door(log=_LogDown())
    held = door.enter(ToolCall(action="orders.refund", name="refund", key="k-1"))
    assert (held.outcome, held.refusal_code) == ("faulted", LOG_UNKNOWN)
    assert world_failed(door.events()[-1])

    # The world unreachable mid-action: ours, and the keyed retry it leaves unknown is ours too.
    down = _door(_Executor(fail=True))
    first = down.enter(ToolCall(action="orders.refund", name="refund", key="k-2"))
    again = down.enter(ToolCall(action="orders.refund", name="refund", key="k-2"))
    assert (first.refusal_code, again.refusal_code) == (WORLD_UNREACHABLE, LOG_UNKNOWN)
    assert all(world_failed(row) for row in down.events())

    # A test condition lost the first answer after the world applied it: the retry is the agent's to handle.
    lost = _door(faults=[{"action": "orders.refund", "mode": "timeout_after_commit", "on_call": 1}])
    lost.enter(ToolCall(action="orders.refund", name="refund", key="k-3"))
    retry = lost.enter(ToolCall(action="orders.refund", name="refund", key="k-3"))
    assert retry.refusal_code != LOG_UNKNOWN and not any(world_failed(row) for row in lost.events())
    assert KEY_UNKNOWN  # the code a lost retry carries when one is held


def test_a_lost_key_is_ours_only_when_our_door_lost_it_across_doors_on_one_keys_table():
    from fi.alk.harness.world_door import KEY_UNKNOWN, LOG_UNKNOWN, world_failed

    def keyed(door, key):
        return door.enter(ToolCall(action="orders.refund", name="refund", key=key))

    log = MemoryLog()
    # The agent's own tools server answered 5xx on the first try: its world. A retry, by this door or another, is the
    # agent's to handle, graded.
    keyed(_door(_Executor(status=500), log=log), "k-upstream")
    assert keyed(_door(log=log), "k-upstream").refusal_code == KEY_UNKNOWN
    # Our own executor failed on the first try: the retry is ours, by any door.
    keyed(_door(_Executor(fail=True), log=log), "k-ours")
    assert keyed(_door(log=log), "k-ours").refusal_code == LOG_UNKNOWN
    # A door that claimed a key and never recorded its answer: ours.
    log.claim("k-pending", "agent")
    held = keyed(_door(log=log), "k-pending")
    assert held.refusal_code == LOG_UNKNOWN and world_failed({**held.__dict__, "actor_id": "agent", "outcome": "faulted"})


def test_the_agent_s_tools_server_not_answering_is_its_world_graded_never_our_failure():
    from fi.alk.harness.world_door import UPSTREAM_UNREACHABLE, HttpExecutor, world_failed

    door = _door(HttpExecutor("http://127.0.0.1:9", timeout=1))
    outcome = door.enter(ToolCall(action="orders.refund", name="refund"))

    assert (outcome.outcome, outcome.refusal_code) == ("faulted", UPSTREAM_UNREACHABLE)
    assert not world_failed(door.events()[-1])
