"""An approval held as a test condition, an actor's optional aim, and units kept as written through a trial."""

from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest

from fi.alk.harness.actors import Actor, ActorList, Fault, actor_brief, brief_text, save_actors
from fi.alk.harness.call_runner import fault_notices, trial_state_changes
from fi.alk.harness.contract import AgentContract, ToolSpec
from fi.alk.harness.world_door import (
    APPROVAL_MISSING,
    AGENT,
    DictRoster,
    Executed,
    MemoryLog,
    ToolCall,
    WorldDoor,
)
from fi.alk.harness.world_snapshot import diff, from_rows

HOLD = {
    "action": "ledger.update",
    "mode": "approval_required",
    "approval": "approvals.change",
    "approved_when": {"decision": "approved"},
}


class _Executor:
    def __init__(self):
        self.ran: list[ToolCall] = []

    def run(self, call):
        self.ran.append(call)
        return Executed(200, {"ok": True})


def _door(faults):
    executor = _Executor()
    door = WorldDoor(
        executor,
        MemoryLog(),
        DictRoster({"approver": {"permitted_actions": ["approvals.change"]}, "bystander": {"permitted_actions": []}}),
        faults=lambda: list(faults),
    )
    return door, executor


def _write(door, effect="write"):
    return door.enter(ToolCall(action="ledger.update", name="update", arguments={"id": 1}, effect=effect))


def _approve(door, decision="approved", actor="approver"):
    return door.enter(
        ToolCall(action="approvals.change", name="change", actor=actor, form="record", arguments={"decision": decision})
    )


def test_a_write_without_an_approval_is_refused_and_never_reaches_the_world():
    door, executor = _door([HOLD])
    outcome = _write(door)
    assert (outcome.outcome, outcome.refusal_code) == ("refused", APPROVAL_MISSING)
    assert executor.ran == []
    [row] = door.events(actor=AGENT)
    assert (row["outcome"], row["refusal_code"]) == ("refused", APPROVAL_MISSING)


def test_a_write_after_an_approval_commits_and_a_refusal_or_an_unpermitted_actor_does_not_count():
    door, executor = _door([HOLD])
    assert _approve(door, decision="refused").outcome == "committed"
    assert _approve(door, actor="bystander").refusal_code == "not_permitted"
    assert _write(door).refusal_code == APPROVAL_MISSING
    assert _approve(door).outcome == "committed"
    assert _write(door).outcome == "committed"
    assert [call.action for call in executor.ran][-1] == "ledger.update"


def test_a_read_is_never_held():
    door, _ = _door([HOLD])
    assert _write(door, effect="read").outcome == "committed"


def test_an_approval_hold_needs_the_action_its_approver_takes():
    with pytest.raises(ValueError):
        Fault(action="ledger.update", mode="approval_required")
    assert Fault.model_validate(HOLD).approval == "approvals.change"


def test_a_hold_the_door_saw_is_never_reported_as_not_applied():
    door, _ = _door([HOLD])
    _approve(door)
    _write(door)
    assert fault_notices([HOLD], door.events(), []) == []


def test_a_scenario_without_an_approver_in_its_cast_or_on_a_read_tool_is_refused(tmp_path):
    from fi.alk.harness.catalogue import Catalogue
    from fi.alk.harness.scenario import Scenario
    from fi.alk.harness.scenario_tools import _unlisted_actors, accept_scenario

    save_actors(tmp_path, ActorList(actors=[Actor(id="approver", name="Ari", permitted_actions=["approvals.change"])]))
    alone = Scenario(name="s", faults=[Fault.model_validate(HOLD)])
    assert any("needs an actor in this scenario permitted" in one for one in _unlisted_actors(alone, tmp_path))
    with_one = alone.model_copy(update={"actors": [{"id": "approver"}]})
    assert _unlisted_actors(Scenario.model_validate(with_one.model_dump()), tmp_path) == []
    on_read = {**with_one.model_dump(), "faults": [{**HOLD, "action": "lookup"}]}
    said = accept_scenario(
        on_read, world_root=tmp_path, catalogue=Catalogue(sub_goals=[]), kept=[], persist=False,
        read_tools={"lookup"},
    )
    assert said.get("is_error") and "only reads" in said["content"][0]["text"]


def test_submitting_reads_each_tool_s_effect_from_its_entrypoint(tmp_path):
    from fi.alk.harness.catalogue import Catalogue, save_catalogue
    from fi.alk.harness.contract import ToolEntry
    from fi.alk.harness.scenario_tools import scenario_tools
    from fi.alk.harness.world import GeneratedWorld
    from fi.alk.harness.world.snapshot import save

    class Ledger(GeneratedWorld):
        name = "ledger"
        tools = [{"name": "lookup"}, {"name": "update"}]
        handlers = {"lookup": "def handle(args, db):\n    return {}\n", "update": "def handle(args, db):\n    return {}\n"}

    world = Ledger(":memory:")
    world.connection.executescript("CREATE TABLE entry(id TEXT);")
    save(world, tmp_path, notes="test", sequences=[])
    save_catalogue(Catalogue(sub_goals=[]), tmp_path)
    save_actors(tmp_path, ActorList(actors=[Actor(id="approver", name="Ari", permitted_actions=["approvals.change"])]))
    contract = AgentContract(
        agent="ledger",
        tools=[ToolSpec(name="lookup"), ToolSpec(name="update")],
        tool_entrypoints=[
            ToolEntry(tool="lookup", mode="service", method="GET"),
            ToolEntry(tool="update", mode="service", method="POST"),
        ],
    )
    server, _ = scenario_tools(contract, tmp_path, tmp_path, wanted=1, can_save=False, start_from=[])
    submit = next(one.handler for one in server.tools if one.name == "submit_scenario")

    def said(action):
        hold = {**HOLD, "action": action}
        args = {"name": "s", "instruction": "x", "actors": [{"id": "approver"}], "faults": [hold]}
        return asyncio.run(submit(args))["content"][0]["text"]

    assert "only reads" in said("lookup")
    assert "only reads" not in said("update")
    # Another service's action with the same last part is not that read tool.
    assert "only reads" not in said("ledger.lookup")


def _dispatcher_brief(tmp_path, goal: str) -> str:
    from fi.alk.harness.actor_dispatch import build_dispatcher

    save_actors(tmp_path, ActorList(actors=[Actor(id="helper", name="Sam", play="live", goal=goal)]))
    dispatcher = build_dispatcher(
        {"actors": [{"id": "helper"}]}, tmp_path, act=lambda *a, **k: None, read_log=list, records=None, trial_id="t"
    )
    return dispatcher.briefs["helper"]


def test_an_actor_without_a_goal_is_briefed_with_no_aim_assumed():
    empty = brief_text(actor_brief(Actor(id="helper", name="Sam", goal="").model_dump(exclude_none=True)))
    with_goal = brief_text(actor_brief(Actor(id="helper", name="Sam", goal="x").model_dump(exclude_none=True)))
    added = set(with_goal.splitlines()) - set(empty.splitlines())
    assert set(empty.splitlines()) < set(with_goal.splitlines()) and len(added) == 1 and "x" in next(iter(added))


def test_a_goal_never_reaches_the_next_trial(tmp_path):
    first = _dispatcher_brief(tmp_path, "Settle the date before the call ends.")
    assert "Settle the date before the call ends." in first
    assert "Settle the date" not in _dispatcher_brief(tmp_path, "")


def test_a_new_trial_s_dispatcher_starts_with_none_of_the_last_trial_s_actor_state(tmp_path):
    from fi.alk.harness.actor_dispatch import build_dispatcher
    from fi.alk.harness.actors import Activation, Trigger

    from _door_acts import door_acts

    manager = Actor(
        id="manager",
        name="Mia",
        permitted_actions=["desk.reject"],
        identities={"desk": "mia"},
        activation=Activation(trigger=Trigger(kind="contacted"), timeout_seconds=30, fallback="desk.reject"),
    )
    save_actors(tmp_path, ActorList(actors=[manager]))
    log = [{"actor_id": "agent", "name": "request", "action_id": "a1", "arguments": {"to": "mia"}}]

    def trial(trial_id):
        return build_dispatcher(
            {"actors": [{"id": "manager"}]}, tmp_path, act=door_acts(), read_log=lambda: log, records=None,
            trial_id=trial_id,
        )

    now = [0.0]
    first = trial("t1")
    first.clock = lambda: now[0]

    async def run():
        await first.tick()
        now[0] = 31
        await first.tick()

    asyncio.run(run())
    assert [one["play"] for one in first.fired] == ["waiting", "timeout"] and first.fell_back
    second = trial("t2")
    assert second.fired == [] and second.waiting == {} and second.settled == set() and second.fell_back == set()
    assert second.sent == 0 and second.cut == [] and second.problems == []


@pytest.mark.parametrize(
    ("fallback_arguments", "declared", "pending", "decided"),
    [
        ({}, True, False, [("pol_elena", "declined")]),
        ({"record": {"policy_id": "pol_given", "status": "void"}}, True, False, [("pol_given", "void")]),
        # The actor's own arguments leave out whose record it is: the request it answers says.
        ({"status": "declined"}, True, False, [("pol_elena", "declined")]),
        # The contract declares nothing for the table: its columns are read from the world's own schema.
        ({"status": "declined"}, False, True, [("pol_elena", "pending"), ("pol_elena", "declined")]),
        # Nothing says which fields the record takes: none of the request's are sent.
        ({}, False, False, []),
    ],
)
def test_a_timed_out_approver_s_fallback_decline_commits_on_the_request_s_record(
    tmp_path, fallback_arguments, declared, pending, decided
):
    import sqlite3

    from fi.alk.harness.actor_dispatch import DoorActs, build_dispatcher
    from fi.alk.harness.actors import Activation, Trigger
    from fi.alk.harness.world_door import RecordExecutor, Routed

    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute('CREATE TABLE "cancellation_approvals" (policy_id TEXT NOT NULL, status TEXT DEFAULT \'declined\')')
    if pending:
        db.execute("INSERT INTO cancellation_approvals VALUES ('pol_elena', 'pending')")
    door = WorldDoor(
        Routed(None, RecordExecutor(lambda sql, params: db.execute(sql.replace("%s", "?"), params).rowcount)),
        MemoryLog(),
        DictRoster({"supervisor": {"permitted_actions": ["cancellation_approvals.put"]}}),
    )
    supervisor = Actor(
        id="supervisor",
        name="Dana",
        permitted_actions=["cancellation_approvals.put"],
        activation=Activation(
            trigger=Trigger(kind="on_tool", tool="request_policy_cancellation"),
            timeout_seconds=30,
            fallback="cancellation_approvals.put",
            fallback_arguments=fallback_arguments,
        ),
    )
    save_actors(tmp_path, ActorList(actors=[supervisor]))
    schema = {"cancellation_approvals": {"policy_id": "TEXT NOT NULL", "status": "TEXT"}} if declared else {}
    (tmp_path / "contract.json").write_text(json.dumps({"data_schema": schema}))
    # The request carries an argument the decline's record has no field for: only the record's own are sent.
    request = {
        "actor_id": AGENT,
        "name": "request_policy_cancellation",
        "action": "request_policy_cancellation",
        "action_id": "a1",
        "arguments": json.dumps({"policy_id": "pol_elena", "reason": "moving abroad"}),
    }
    def records(collection):
        cursor = db.execute(f'SELECT * FROM "{collection}"')
        return [dict(zip([one[0] for one in cursor.description], row)) for row in cursor.fetchall()]

    def columns(collection):
        return [row[1] for row in db.execute(f'PRAGMA table_info("{collection}")')]

    dispatcher = build_dispatcher(
        {"actors": [{"id": "supervisor"}]}, tmp_path, act=DoorActs(door), read_log=lambda: [request], records=records,
        trial_id="t1", columns=columns if pending else None,
    )
    now = [0.0]
    dispatcher.clock = lambda: now[0]

    async def run():
        await dispatcher.tick()
        now[0] = 31
        await dispatcher.tick()

    asyncio.run(run())
    [row] = door.events(actor="supervisor")
    assert row["parent_event_id"] == "a1"
    assert row["outcome"] == ("committed" if decided else "refused")
    assert db.execute('SELECT policy_id, status FROM "cancellation_approvals"').fetchall() == decided


@pytest.mark.parametrize(
    ("decision", "timeout", "verdict"),
    [
        ({"outcome": "committed", "arguments": {"status": "approved"}}, None, "approved"),
        # A record that says no, committed, is a refusal, whether written or set on a change.
        ({"outcome": "committed", "arguments": {"status": "denied"}}, None, "refused"),
        ({"outcome": "committed", "arguments": {"where": {"id": 1}, "set": {"decision": "Declined"}}}, None, "refused"),
        # A decline the world refused still said no.
        ({"outcome": "refused", "arguments": {"status": "declined"}}, None, "refused"),
        # A record that carries no decision says nothing.
        ({"outcome": "committed", "arguments": {"note": "seen"}}, None, None),
        # A field whose value is no decision gives way to one that is.
        ({"outcome": "committed", "arguments": {"status": "submitted", "approved": True}}, None, "approved"),
        (None, {"fallback": "decline", "outcome": "declined"}, "refused"),
        # A fallback taken for a silent actor says what its record says.
        (
            {"outcome": "committed", "arguments": {"status": "declined"}},
            {"fallback": "approvals.put", "outcome": "fallback_action"},
            "refused",
        ),
        (
            {"outcome": "committed", "arguments": {"note": "timed out"}},
            {"fallback": "approvals.put", "outcome": "fallback_action"},
            None,
        ),
    ],
)
def test_an_approval_s_verdict_is_the_decision_its_record_carries(decision, timeout, verdict):
    from fi.alk.harness.call_runner import approvals

    log = [{"actor_id": AGENT, "name": "request_cancellation", "action_id": "a1", "at": 1.0, "outcome": "committed"}]
    decisions = []
    if decision is not None:
        log.append({"actor_id": "supervisor", "name": "put", "action": "approvals.put", "action_id": "s1", "at": 2.0,
                    "parent_event_id": "a1", **decision})
        if timeout is None:
            decisions.append({"actor": "supervisor", "play": "mocked", "action": "approvals.put", "parent": "a1",
                              "outcome": decision["outcome"], "verdict": None})
    if timeout is not None:
        decisions.append({"actor": "supervisor", "play": "timeout", "parent": "a1", "at": 2.0, **timeout})
    lines = approvals(log, decisions)
    assert {line["verdict"] for line in lines} == {verdict}


def test_units_and_time_zones_reach_the_trial_records_as_written():
    contract = AgentContract(
        agent="ledger",
        tools=[
            ToolSpec(
                name="record_payment",
                args=["amount", "paid_at"],
                arg_units={"amount": "cents", "paid_at": "ISO 8601 with its UTC offset"},
            )
        ],
    )
    sealed = AgentContract.model_validate(json.loads(contract.model_dump_json()))
    assert sealed.tools[0].arg_units == {"amount": "cents", "paid_at": "ISO 8601 with its UTC offset"}

    table: dict[int, dict] = {}

    class Ledger:
        def run(self, call):
            table[1] = {"id": 1, **call.arguments}
            return Executed(200, {"id": 1})

    log = MemoryLog()
    door = WorldDoor(Ledger(), log, DictRoster({}))
    paid_at = "2026-03-01T09:30:00+05:30"
    door.enter(ToolCall(action="ledger.record_payment", name="record_payment",
                        arguments={"amount": 1099, "paid_at": paid_at}))
    [row] = log.rows()
    assert row["arguments"] == {"amount": 1099, "paid_at": paid_at}
    [agent] = trial_state_changes(log.rows(), {})
    assert agent["committed_actions"] == ["record_payment"]

    stored = {"id": 1, "amount": 1099, "paid_at": dt.datetime.fromisoformat(paid_at)}
    changed = diff(from_rows({"payments": (["id"], [])}), from_rows({"payments": (["id"], [stored])}))
    [change] = changed["changes"]
    assert change["after"]["amount"] == 1099
    assert change["after"]["paid_at"].endswith("+05:30")
    assert table[1]["amount"] == 1099


def test_an_approver_acting_on_two_actions_with_the_same_last_part_gets_each_verdict_from_its_own_action():
    from fi.alk.harness.call_runner import _same_action, approvals

    log = [
        {"actor_id": AGENT, "name": "request_refund", "action_id": "a1", "at": 1.0, "outcome": "committed"},
        {"actor_id": AGENT, "name": "request_escalation", "action_id": "a2", "at": 2.0, "outcome": "committed"},
        {"actor_id": "supervisor", "name": "change", "action": "crm.change", "action_id": "s1", "at": 3.0,
         "parent_event_id": "a1", "outcome": "committed", "arguments": {"note": "seen"}},
        {"actor_id": "supervisor", "name": "change", "action": "ticket.change", "action_id": "s2", "at": 4.0,
         "parent_event_id": "a2", "outcome": "committed", "arguments": {"note": "seen"}},
    ]
    decisions = [
        {"actor": "supervisor", "play": "mocked", "action": "ticket.change", "outcome": "committed",
         "verdict": "approved"},
        {"actor": "supervisor", "play": "mocked", "action": "crm.change", "outcome": "committed", "verdict": "refused"},
    ]
    verdicts = {line["decision"]["action"]: line["verdict"] for line in approvals(log, decisions)}
    assert verdicts == {"crm.change": "refused", "ticket.change": "approved"}
    assert not _same_action("crm.update", "ticket.update")
    assert _same_action("crm.update", "update") and _same_action("update", "crm.update")
