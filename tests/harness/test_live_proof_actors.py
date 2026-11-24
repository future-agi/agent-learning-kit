"""The live proof plays a scenario's actors around the reference steps, as the offline proof does."""

import time
from types import SimpleNamespace

from fi.alk.harness import live_proof
from fi.alk.harness.actors import Actor, ActorList, save_actors
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.world.stores.postgres import PostgresStore


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


class _Log(PostgresStore):
    """A live world's records: an approval written here is what lets the discount through."""

    def __init__(self, world) -> None:
        self.world = world

    def execute(self, statement, params=()):
        if statement.startswith('INSERT INTO "approvals"'):
            self.world.approved = True
        return 1


class _LiveDeals:
    """A live world as the hosted runtime gives it: its own action log (`events`), calls through the door, and its
    records behind `record_store`. No `_record`, no `store`."""

    endpoints = ()

    def __init__(self) -> None:
        self.calls, self.recorded, self.approved, self.log = [], [], False, []
        self.record_store = _Log(self)

    def call(self, name, arguments):
        at = time.time()
        if name == "bad_request":
            status, ok, code = 422, False, "request_not_accepted"
        elif name == "apply_discount" and not self.approved:
            status, ok, code = 409, False, "world_rule"
        else:
            status, ok, code = 200, True, None
        self.log.append({"name": name, "arguments": dict(arguments), "at": at, "ok": ok, "actor_id": None, "outcome": "committed" if ok else "refused", "refusal_code": code, "status": status})
        call = SimpleNamespace(name=name, arguments=dict(arguments), ok=ok, error="" if ok else f"refused {status}", result=None, at=at)
        self.calls.append(call)
        return call

    def events(self, actor_id=None, outcome=None):
        return [dict(row) for row in self.log]


LIVE_STEPS = [
    SimpleNamespace(tool="get_deal", arguments={"deal_id": 7}),
    SimpleNamespace(tool="bad_request", arguments={}),
    SimpleNamespace(tool="apply_discount", arguments={"deal_id": 7, "percent": 20}),
]


def test_a_live_world_without_actors_keeps_its_own_action_log(tmp_path, monkeypatch):
    _live(monkeypatch)
    world = _LiveDeals()
    own = world.events

    live_proof._play(_case(tmp_path, with_approver=False), world, LIVE_STEPS, authoring=tmp_path)

    assert world.events == own


def test_with_an_approver_the_live_log_keeps_its_own_refusals_and_credits_the_actor_s_write(tmp_path, monkeypatch):
    _live(monkeypatch)
    world = _LiveDeals()

    calls, _, refused = live_proof._play(_case(tmp_path, with_approver=True), world, LIVE_STEPS, authoring=tmp_path)

    # Alex's approval went to the world's records, so the discount went through; the 422 stays the agent's own.
    assert refused == ["bad_request({}): refused 422"]
    assert [one.name for one in calls] == ["get_deal", "bad_request", "apply_discount"]
    rows = world.events()
    assert [(row["name"], row.get("refusal_code")) for row in rows if row["name"] == "bad_request"] == [("bad_request", "request_not_accepted")]
    assert [(row["name"], row["actor_id"], row["outcome"]) for row in world.events(actor_id="alex")] == [("approvals.put", "alex", "committed")]
    assert [row["name"] for row in world.events(actor_id="agent")] == ["get_deal", "bad_request", "apply_discount"]


class _RoutedDeals(_LiveDeals):
    """A live world whose service publishes the approval as a route, so the actor's write goes through the door, and
    the door logs it under the route's last segment, as anyone's."""

    endpoints = ("approvals/put",)

    def call(self, name, arguments):
        if name == "approvals/put":
            self.approved = True
        call = super().call(name, arguments)
        self.log[-1]["name"] = name.rsplit("/", 1)[-1]
        return call


def test_an_actor_s_call_through_the_door_is_credited_to_the_actor_in_the_live_log(tmp_path, monkeypatch):
    _live(monkeypatch)
    world = _RoutedDeals()

    live_proof._play(_case(tmp_path, with_approver=True), world, LIVE_STEPS, authoring=tmp_path)

    assert [(row["name"], row["actor_id"]) for row in world.events(actor_id="alex")] == [("put", "alex")]
    assert len(world.events()) == len(world.log)


def _over(calls, by_actors, log, refusals=()):
    from fi.alk.harness.prove import _WorldActors

    acting = SimpleNamespace(
        by_actors=by_actors, world=SimpleNamespace(calls=calls), refusals=list(refusals), _row_for=_WorldActors._row_for
    )
    return _WorldActors.events_over(acting, lambda *args: [dict(row) for row in log])


def _call(name, arguments, at):
    return SimpleNamespace(name=name, arguments=arguments, at=at, ok=True, result=None, error="", refusal_code="")


def test_an_actor_and_the_agent_calling_the_same_tool_close_together_are_each_credited_their_own_call():
    calls = [_call("apply_discount", {"percent": 10}, 100.0), _call("apply_discount", {"percent": 20}, 101.0)]
    log = [
        {"name": "apply_discount", "arguments": {"percent": 10}, "at": 100.0, "actor_id": None, "ok": True},
        {"name": "apply_discount", "arguments": {"percent": 20}, "at": 101.0, "actor_id": None, "ok": True},
    ]

    rows = _over(calls, {1: "alex"}, log)()

    assert [(row["arguments"]["percent"], row.get("actor_id") or "agent") for row in rows] == [(10, "agent"), (20, "alex")]


def test_two_rows_equally_close_to_an_actor_s_call_are_not_guessed_the_actor_s_row_is_added():
    calls = [_call("apply_discount", {"percent": 20}, 100.0)]
    log = [
        {"name": "apply_discount", "arguments": {"percent": 20}, "at": 99.0, "actor_id": None, "ok": True},
        {"name": "apply_discount", "arguments": {"percent": 20}, "at": 101.0, "actor_id": None, "ok": True},
    ]

    rows = _over(calls, {0: "alex"}, log)()

    assert [row.get("actor_id") or "agent" for row in rows] == ["agent", "alex", "agent"]


def test_rows_read_in_the_order_they_happened_an_actor_s_refusal_at_its_own_time():
    calls = [_call("get_deal", {"deal_id": 7}, 100.0)]
    log = [{"name": "get_deal", "arguments": {"deal_id": 7}, "at": 100.0, "actor_id": None, "ok": True},
           {"name": "apply_discount", "arguments": {}, "at": 103.0, "actor_id": None, "ok": True}]
    refusal = {"name": "put", "arguments": {}, "ok": False, "actor_id": "jordan", "outcome": "refused", "refusal_code": "not_permitted", "at": 101.0}

    rows = _over(calls, {}, log, [(1, refusal)])()

    assert [row["name"] for row in rows] == ["get_deal", "put", "apply_discount"]


def test_the_actor_s_row_is_found_by_its_arguments_even_when_another_call_is_logged_closer_in_time():
    calls = [_call("apply_discount", {"percent": 10}, 100.5), _call("apply_discount", {"percent": 20}, 100.4)]
    log = [
        {"name": "apply_discount", "arguments": {"percent": 10}, "at": 100.5, "actor_id": None, "ok": True},
        {"name": "apply_discount", "arguments": {"percent": 20}, "at": 101.0, "actor_id": None, "ok": True},
    ]

    rows = _over(calls, {1: "alex"}, log)()

    assert sorted((row["arguments"]["percent"], row.get("actor_id") or "agent") for row in rows) == [(10, "agent"), (20, "alex")]


def test_of_two_identical_calls_the_actor_is_credited_the_one_closest_to_its_own():
    calls = [_call("apply_discount", {"percent": 20}, 98.0), _call("apply_discount", {"percent": 20}, 101.0)]
    log = [
        {"name": "apply_discount", "arguments": {"percent": 20}, "at": 98.0, "actor_id": None, "ok": True},
        {"name": "apply_discount", "arguments": {"percent": 20}, "at": 101.1, "actor_id": None, "ok": True},
    ]

    rows = _over(calls, {1: "alex"}, log)()

    assert [(row["at"], row.get("actor_id") or "agent") for row in rows] == [(98.0, "agent"), (101.1, "alex")]


class _Desk:
    """A world of requests and approvals: `request` files one, `approve` and `notify` record the actors' answers."""

    def __init__(self):
        self.records = {"requests": [], "approvals": [], "notices": []}
        self.calls = []

    def call(self, name, arguments):
        from fi.alk.harness.world.runtime import Call

        table = {"request": "requests", "approve": "approvals", "notify": "notices"}[name]
        self.records[table].append(dict(arguments))
        self.calls.append(Call(name, dict(arguments), result={"ok": True}))
        return self.calls[-1]

    def state(self):
        return {name: list(rows) for name, rows in self.records.items()}


def test_an_actor_waiting_on_a_record_acts_in_the_proof_once_the_record_is_there_and_in_turn_brings_in_the_next(
    tmp_path,
):
    from fi.alk.harness.prove import _unplayed, _WorldActors

    save_actors(
        tmp_path,
        ActorList(
            actors=[
                Actor(id="approver", name="Approver", play="mocked", permitted_actions=["approve"]),
                Actor(id="clerk", name="Clerk", play="mocked", permitted_actions=["notify"]),
            ]
        ),
    )

    def waits_on(collection, action):
        return {"action": action, "arguments": {"id": 7}, "trigger": {"kind": "conditional", "collection": collection, "where": {"id": 7}}}

    scenario = Scenario(
        name="request",
        instruction="x",
        # The clerk is listed first: it is brought in only by what the approver does after it was passed over.
        actors=[
            {"id": "clerk", "actions": [waits_on("approvals", "notify")]},
            {"id": "approver", "actions": [waits_on("requests", "approve")]},
        ],
    )
    world, by_actors = _Desk(), {}
    acting = _WorldActors(scenario, tmp_path, world, by_actors)

    acting.act_on(None)
    assert world.calls == []
    world.call("request", {"id": 7})
    acting.act_on(SimpleNamespace(tool="request", arguments={"id": 7}))

    assert [call.name for call in world.calls] == ["request", "approve", "notify"]
    assert by_actors == {1: "approver", 2: "clerk"}
    assert _unplayed(scenario) == []
