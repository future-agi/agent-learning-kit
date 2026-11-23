"""The live proof plays a scenario's actors around the reference steps, as the offline proof does."""

from types import SimpleNamespace

from fi.alk.harness import live_proof
from fi.alk.harness.actors import Actor, ActorList, save_actors
from fi.alk.harness.scenario import Scenario


class _Deals:
    """A live world where a discount goes through only once an approval is on record."""

    endpoints = ()

    def __init__(self) -> None:
        self.calls = []
        self.recorded = []
        self.approved = False

    def call(self, name, arguments):
        if name == "put":
            self.approved = True
        ok = self.approved if name == "apply_discount" else True
        error = "" if ok else "a discount above 15% needs an approved approval record"
        return self._record(SimpleNamespace(name=name, arguments=dict(arguments), ok=ok, error=error, result=None))

    def _record(self, call):
        self.calls.append(call)
        return call


STEPS = [
    SimpleNamespace(tool="get_deal", arguments={"deal_id": 7}),
    SimpleNamespace(tool="apply_discount", arguments={"deal_id": 7, "percent": 20}),
]


def _case(tmp_path, *, with_approver: bool) -> Scenario:
    if not with_approver:
        return Scenario(name="discount", instruction="x")
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


def _live(monkeypatch):
    ok = SimpleNamespace(ok=True, said="")
    monkeypatch.setattr(live_proof, "apply_setup", lambda scenario, world: ok)
    monkeypatch.setattr(live_proof, "check_ready", lambda scenario, world: ok)
    monkeypatch.setattr(live_proof, "assumed_tools", lambda world, steps: [])
    monkeypatch.setattr(live_proof, "play_reference_step", lambda world, step: world.call(step.tool, step.arguments))


def test_an_approval_an_actor_gives_reaches_the_live_world_before_the_step_that_needs_it(tmp_path, monkeypatch):
    _live(monkeypatch)
    world = _Deals()

    calls, _, refused = live_proof._play(_case(tmp_path, with_approver=True), world, STEPS, authoring=tmp_path)

    assert refused == []
    # The actor's approval is in the world, never among the agent's calls.
    assert [one.name for one in calls] == ["get_deal", "apply_discount"]
    assert [one.name for one in world.calls] == ["get_deal", "put", "apply_discount"]


def test_a_scenario_without_actors_plays_the_same_with_or_without_the_authoring_tree(tmp_path, monkeypatch):
    _live(monkeypatch)
    case = _case(tmp_path, with_approver=False)

    with_tree = live_proof._play(case, _Deals(), STEPS, authoring=tmp_path)
    without = live_proof._play(case, _Deals(), STEPS)

    assert [one.name for one in with_tree[0]] == [one.name for one in without[0]] == ["get_deal", "apply_discount"]
    assert with_tree[2] == without[2] == ["apply_discount({'deal_id': 7, 'percent': 20}): a discount above 15% needs an approved approval record"]
