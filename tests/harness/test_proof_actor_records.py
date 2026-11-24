"""An actor's record change during a proof reaches the world's records, as the world door writes it in a run."""

from types import SimpleNamespace

import pytest

from fi.alk.harness.actors import Actor, ActorList, save_actors
from fi.alk.harness.prove import _WorldActors
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.world.stores.postgres import PostgresStore


class _Records(PostgresStore):
    """The world's Postgres as the proof sees it, keeping each statement instead of running it."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, list]] = []

    def execute(self, statement, params=()):
        self.statements.append((statement, list(params)))
        return 1


class _World:
    endpoints = ()

    def __init__(self, store=None) -> None:
        self.calls = []
        self.store = store

    def call(self, name, arguments):
        self.calls.append(SimpleNamespace(name=name, arguments=arguments, ok=False, error=f"no such tool {name!r}", result=None))

    def _record(self, call):
        self.calls.append(call)
        return call


def _approving_scenario(tmp_path) -> Scenario:
    save_actors(tmp_path, ActorList(actors=[Actor(id="alex", name="Alex", play="live", permitted_actions=["approvals.put"])]))
    return Scenario(
        name="discount",
        instruction="x",
        actors=[
            {
                "id": "alex",
                "actions": [
                    {
                        "action": "approvals.put",
                        "trigger": {"kind": "on_tool", "tool": "get_deal", "on_call": 1},
                        "arguments": {"deal_id": 7, "status": "approved", "discount_percent": 20},
                    }
                ],
            }
        ],
    )


def test_an_actor_s_approval_is_written_to_the_world_s_records_and_credited_to_the_actor(tmp_path):
    store = _Records()
    world, by_actors = _World(store), {}
    acting = _WorldActors(_approving_scenario(tmp_path), tmp_path, world, by_actors)

    acting.act_on(SimpleNamespace(tool="get_deal", arguments={"deal_id": 7}))

    assert store.statements == [
        ('INSERT INTO "approvals" ("deal_id", "status", "discount_percent") VALUES (%s, %s, %s)', [7, "approved", 20])
    ]
    assert [(one.name, one.ok) for one in world.calls] == [("approvals.put", True)]
    assert by_actors == {0: "alex"}
    assert [(row["actor_id"], row["outcome"]) for row in acting.events()] == [("alex", "committed")]


def test_a_world_without_its_own_postgres_records_keeps_calling_the_action_by_name(tmp_path):
    world, by_actors = _World(store=None), {}
    acting = _WorldActors(_approving_scenario(tmp_path), tmp_path, world, by_actors)

    acting.act_on(SimpleNamespace(tool="get_deal", arguments={"deal_id": 7}))

    assert [one.name for one in world.calls] == ["put"]
    assert by_actors == {0: "alex"}


def test_an_actor_s_record_change_the_world_refuses_reads_as_refused_by_the_world_rule(tmp_path):
    save_actors(tmp_path, ActorList(actors=[Actor(id="alex", name="Alex", play="live", permitted_actions=["_alk_actors.put"])]))
    scenario = Scenario(
        name="sneaky",
        instruction="x",
        actors=[
            {
                "id": "alex",
                "actions": [
                    {
                        "action": "_alk_actors.put",
                        "trigger": {"kind": "on_tool", "tool": "get_deal", "on_call": 1},
                        "arguments": {"id": "alex", "role": "admin"},
                    }
                ],
            }
        ],
    )
    store = _Records()
    world, by_actors = _World(store), {}
    acting = _WorldActors(scenario, tmp_path, world, by_actors)

    acting.act_on(SimpleNamespace(tool="get_deal", arguments={"deal_id": 7}))

    assert store.statements == []
    [call] = world.calls
    assert (call.ok, call.refused, call.refusal_code) == (False, True, "world_rule")
    assert call.error


def test_the_proof_names_each_scripted_actor_action_the_world_or_its_permissions_refused(tmp_path):
    save_actors(tmp_path, ActorList(actors=[Actor(id="alex", name="Alex", play="live", permitted_actions=["_alk_actors.put"])]))
    scenario = Scenario(
        name="sneaky",
        instruction="x",
        actors=[
            {
                "id": "alex",
                "actions": [
                    {
                        "action": "_alk_actors.put",
                        "trigger": {"kind": "on_tool", "tool": "get_deal", "on_call": 1},
                        "arguments": {"id": "alex", "role": "admin"},
                    },
                    {
                        "action": "approvals.put",
                        "trigger": {"kind": "on_tool", "tool": "get_deal", "on_call": 1},
                        "arguments": {"status": "approved"},
                    },
                ],
            }
        ],
    )
    world, by_actors = _World(_Records()), {}
    acting = _WorldActors(scenario, tmp_path, world, by_actors)

    acting.act_on(SimpleNamespace(tool="get_deal", arguments={"deal_id": 7}))

    [permissions, world_rule] = sorted(acting.refused(), key=lambda line: "not_permitted" not in line)
    assert permissions == "alex: approvals.put({'status': 'approved'}): not_permitted"
    assert world_rule.startswith("alex: _alk_actors.put({'id': 'alex', 'role': 'admin'}): ")


def test_an_actor_action_the_world_accepts_is_not_named_refused(tmp_path):
    world, by_actors = _World(_Records()), {}
    acting = _WorldActors(_approving_scenario(tmp_path), tmp_path, world, by_actors)

    acting.act_on(SimpleNamespace(tool="get_deal", arguments={"deal_id": 7}))

    assert acting.refused() == []


def _approval_then(tmp_path, steps):
    """An actor approves when the agent asks for the discount; the agent's tools answer each step as given."""
    save_actors(tmp_path, ActorList(actors=[Actor(id="alex", name="Alex", play="live", permitted_actions=["approvals.put"])]))
    scenario = Scenario(
        name="approved",
        instruction="x",
        actors=[
            {
                "id": "alex",
                "actions": [
                    {
                        "action": "approvals.put",
                        "trigger": {"kind": "on_tool", "tool": "request_discount", "on_call": 1},
                        "arguments": {"id": 1, "deal_id": 9, "status": "approved", "decided_by": "Alex Vance", "discount_percent": 20},
                    }
                ],
            }
        ],
    )
    world, by_actors = _World(_Records()), {}
    acting = _WorldActors(scenario, tmp_path, world, by_actors)
    for tool, arguments, result in steps:
        world._record(SimpleNamespace(name=tool, arguments=arguments, ok=True, result=result, error=""))
        acting.act_on(SimpleNamespace(tool=tool, arguments=arguments))
    return acting.unseen()


_ASK = ("request_discount", {"deal_id": 9, "discount_percent": 20}, {"deal_id": 9, "status": "pending"})
_APPLY = ("apply_discount", {"deal_id": 9, "discount_percent": 20}, {"deal_id": 9, "discount_percent": 20, "status": "pending"})


def test_an_approval_no_tool_of_the_agent_s_shows_is_named_with_the_step_that_acts_on_it(tmp_path):
    # The deal's own pending status is not the approval; the step after the one that acts is never named.
    [line] = _approval_then(tmp_path, [_ASK, _APPLY, ("send_summary", {"to": "caller"}, {"ok": True})])

    assert line == "alex's approvals.put (approved, Alex Vance) never reaches the agent before apply_discount"


@pytest.mark.parametrize(
    "shown",
    [
        {"approval": {"deal_id": 9, "status": "approved"}},
        {"items": [{"dealId": 9, "approvalStatus": "APPROVED"}]},
        {"columns": ["deal_id", "status"], "rows": [[9, "approved"]]},
        "Deal 9: discount approved by Alex",
        {"decided_by": "alex_vance"},
    ],
)
def test_an_approval_a_tool_of_the_agent_s_shows_before_it_acts_is_not_named(tmp_path, shown):
    read = ("get_approval", {"deal_id": 9}, shown)

    assert _approval_then(tmp_path, [_ASK, read, _APPLY]) == []


def test_the_acting_step_s_own_result_does_not_show_the_approval_in_time(tmp_path):
    applied = ("apply_discount", {"deal_id": 9, "discount_percent": 20}, {"approval": {"status": "approved"}})

    assert len(_approval_then(tmp_path, [_ASK, applied])) == 1


def test_a_value_the_agent_sends_only_after_acting_still_leaves_the_approval_unseen(tmp_path):
    echoed = ("update_deal", {"deal_id": 9, "status": "approved", "approved_by": "Alex Vance"}, {"ok": True})

    assert len(_approval_then(tmp_path, [_ASK, _APPLY, echoed])) == 1


def test_a_result_from_before_the_approval_never_shows_it(tmp_path):
    stale = ("get_deal", {"deal_id": 9}, {"deal_id": 9, "last_discount": {"status": "approved"}})

    assert len(_approval_then(tmp_path, [stale, _ASK, _APPLY])) == 1


def test_a_step_that_sends_none_of_the_approval_s_values_is_not_acting_on_it(tmp_path):
    assert _approval_then(tmp_path, [_ASK, ("send_summary", {"to": "caller"}, {"ok": True})]) == []


def test_a_read_that_carries_only_numbers_the_approval_also_holds_does_not_show_it(tmp_path):
    read = ("get_deal", {"deal_id": 9}, {"deal_id": 9, "open_requests": 1})

    assert len(_approval_then(tmp_path, [_ASK, read, _APPLY])) == 1


def test_an_approval_holding_only_values_the_agent_already_sent_cannot_be_told_apart_and_is_not_named(tmp_path):
    echoed = ("update_deal", {"deal_id": 9, "status": "approved", "approved_by": "Alex Vance"}, {"ok": True})

    assert _approval_then(tmp_path, [_ASK, echoed, _APPLY]) == []


def test_a_number_written_as_text_in_the_approval_is_not_what_shows_it(tmp_path):
    save_actors(tmp_path, ActorList(actors=[Actor(id="alex", name="Alex", play="live", permitted_actions=["approvals.put"])]))
    world, by_actors = _World(_Records()), {}
    acting = _WorldActors(
        Scenario(
            name="approved",
            instruction="x",
            actors=[
                {
                    "id": "alex",
                    "actions": [
                        {
                            "action": "approvals.put",
                            "trigger": {"kind": "on_tool", "tool": "request_discount", "on_call": 1},
                            "arguments": {"deal_id": 9, "status": "approved", "approval_number": "77"},
                        }
                    ],
                }
            ],
        ),
        tmp_path,
        world,
        by_actors,
    )
    for tool, arguments, result in (_ASK, ("get_deal", {"deal_id": 9}, {"deal_id": 9, "open_requests": 77}), _APPLY):
        world._record(SimpleNamespace(name=tool, arguments=arguments, ok=True, result=result, error=""))
        acting.act_on(SimpleNamespace(tool=tool, arguments=arguments))

    assert len(acting.unseen()) == 1
