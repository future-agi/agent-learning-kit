"""An actor's record change during a proof reaches the world's records, as the world door writes it in a run."""

from types import SimpleNamespace

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
