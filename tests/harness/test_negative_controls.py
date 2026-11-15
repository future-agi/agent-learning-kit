"""Task-specific negative controls must fail the checks, and a refusal case is proved by them."""

from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

from fi.alk.harness import catalogue as catalogue_module
from fi.alk.harness import live_proof
from fi.alk.harness import prove as module
from fi.alk.harness import scenario_tools
from fi.alk.harness.contract import ToolEntry
from fi.alk.harness.prove import Proof
from fi.alk.harness.world.runtime import Call

from proof_world import (
    BASE,
    LOOKUP_O10,
    REFUND_CATALOGUE,
    REFUND_O10,
    catalogue,
    check,
    control,
    install,
    refund_case,
    scenario,
)

LOOKUP_O20 = {"tool": "lookup_order", "arguments": {"order_id": "O-20"}, "fixed": {"order_id": "the caller"}}
REFUND_O20 = {"tool": "refund_order", "arguments": {"order_id": "O-20"}, "fixed": {"order_id": "the caller"}}
NO_REFUND = check(
    "if world.data['refunds'] or any(c.name == 'refund_order' for c in calls):\n    return 'a refund was attempted'\nreturn None"
)
LOOKED_UP_O20 = check(
    "return None if any(c.name == 'lookup_order' and c.ok and c.arguments.get('order_id') == 'O-20' for c in calls) "
    "else 'O-20 never looked up'"
)


def _refusal(sub_goals=("no_refund", "looked_up_o20")):
    return scenario(
        [LOOKUP_O20],
        name="refuse_20_day",
        sub_goals=list(sub_goals),
        instruction="I bought this 20 days ago and want my money back.",
        negative_controls=[control("refund_anyway", "refunds the 20-day order", [LOOKUP_O20, REFUND_O20])],
    )


def test_two_refunds_control_fails(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(), catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.controls[0]["name"] == "two_refunds"
    assert proof.controls[0]["failed"] is True
    assert proof.proved is True and proof.blocked_reason() is None


def test_control_that_passes_blocks(monkeypatch, tmp_path):
    install(monkeypatch)
    loose = check("return None if len(world.data['refunds']) >= 1 else 'no refund'")
    proof = module.prove(refund_case(), catalogue(refund_once=loose, looked_up=REFUND_CATALOGUE["looked_up"]), tmp_path)
    assert proof.blocked_reason() == "negative_control_passed"
    assert "refund_once" in proof.controls[0]["held"]
    assert proof.proved is False


def test_no_control_blocks(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(refund_case(negative_controls=[]), catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.blocked_reason() == "no_negative_control"
    assert proof.proved is False and proof.kept_as_written is False


def test_refusal_case_outcome_no_change(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.outcome == "no_change"
    assert proof.controls[0]["name"] == "refund_anyway" and proof.controls[0]["failed"] is True


def test_no_change_case_holds_despite_weak_checks(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.weak == ["no_refund"]
    assert proof.holds is True and proof.proved is True


def test_control_with_unbound_tool_is_not_run(monkeypatch, tmp_path):
    install(monkeypatch, runtime_tools={"place_order"})
    place = {"tool": "place_order", "arguments": {"order_id": "O-10"}}
    case = refund_case(negative_controls=[control("places_instead", "places a new order", [LOOKUP_O10, REFUND_O10, place])])
    proof = module.prove(case, catalogue(**REFUND_CATALOGUE), tmp_path)
    assert proof.controls[0]["assumed"] == ["place_order"]
    assert proof.blocked_reason() == "control_not_run"
    assert proof.proved is False and proof.kept_as_written is True


def test_refusal_lookup_without_check_is_unchecked(monkeypatch, tmp_path):
    install(monkeypatch)
    proof = module.prove(_refusal(sub_goals=("no_refund",)), catalogue(no_refund=NO_REFUND), tmp_path)
    assert proof.unchecked == ["lookup_order.order_id"]
    assert proof.blocked_reason() == "unchecked"


def test_unreadable_state_reads_change(monkeypatch, tmp_path):
    install(monkeypatch, unreadable=True)
    proof = module.prove(_refusal(), catalogue(no_refund=NO_REFUND, looked_up_o20=LOOKED_UP_O20), tmp_path)
    assert proof.outcome == "change"


def test_assumed_write_never_reads_no_change(monkeypatch, tmp_path):
    # The refund step never ran, so the unchanged world says nothing: the case is no refusal case.
    install(monkeypatch, runtime_tools={"refund_order"})
    proof = module.prove(refund_case(), catalogue(**REFUND_CATALOGUE), tmp_path, contract=None, actors=[])
    assert proof.assumed == ["refund_order"]
    assert proof.outcome == "change"


class ServiceWorld:
    """The live world over one shared datastore: a bound tool is a POST to the tools service."""

    def __init__(self, store: dict) -> None:
        self.store = store
        self.calls: list[Call] = []
        self.runtime_tools: set[str] = set()
        self.endpoint_for: dict[str, str] = {}
        self.forwarded: list[str] = []

    @property
    def data(self) -> dict:
        return self.store

    def bind_tools(self, proxy, endpoint_for, runtime_tools) -> None:
        assert proxy == "http://proxy"
        self.endpoint_for = dict(endpoint_for)
        self.runtime_tools = set(runtime_tools) | set(endpoint_for)

    def forward(self, endpoint, arguments=None, *, record_as="", record=True) -> Call:
        self.forwarded.append(endpoint)
        args = dict(arguments or {})
        order = self.store["orders"].get(args.get("order_id"))
        if endpoint == "lookup":
            return Call(endpoint, args, result=order, ok=order is not None)
        if order is None or order["age_days"] > 14:
            return Call(endpoint, args, ok=False, refused=True, error="refused by policy")
        self.store["refunds"].append(args["order_id"])
        return Call(endpoint, args, result={"refunded": args["order_id"]})

    def call(self, name, arguments=None) -> Call:
        call = self.forward(self.endpoint_for[name], arguments)
        self.calls.append(call)
        return call

    def state(self) -> dict:
        return copy.deepcopy(self.store)


class Stack:
    """A runtime whose reset restores the seeded datastore, and a factory over it."""

    def __init__(self) -> None:
        self.store = copy.deepcopy(BASE)
        self.resets = 0
        self.endpoints = {"tool_proxy": SimpleNamespace(address="http://proxy"), "target_http": "x"}
        self.worlds: list[ServiceWorld] = []

    async def reset(self, runtime, *, work_directory) -> None:
        self.resets += 1
        self.store.clear()
        self.store.update(copy.deepcopy(BASE))

    async def create(self, runtime, *, rng) -> ServiceWorld:
        world = ServiceWorld(self.store)
        self.worlds.append(world)
        return world


def _contract(modes: dict[str, str]):
    entries = {name: ToolEntry(tool=name, mode=mode, endpoint={"lookup_order": "lookup", "refund_order": "refund"}[name]) for name, mode in modes.items()}
    return SimpleNamespace(tool_names=lambda: list(modes), entry_for=entries.get)


def _prove(monkeypatch, tmp_path, modes, goals=REFUND_CATALOGUE):
    monkeypatch.setattr(scenario_tools, "load_scenarios", lambda path: [refund_case()])
    monkeypatch.setattr(catalogue_module, "load_catalogue", lambda path: catalogue(**goals))
    monkeypatch.setattr(live_proof, "_digest", lambda world: repr(sorted(world.state()["refunds"])))
    stack = Stack()
    results = asyncio.run(
        live_proof.prove_live(
            authoring=tmp_path, contract=_contract(modes), runtime=stack, provider=stack, factory=stack, work=tmp_path
        )
    )
    return results, stack


def test_service_tool_control_runs_and_fails_on_the_runtime(monkeypatch, tmp_path):
    results, stack = _prove(monkeypatch, tmp_path, {"lookup_order": "service", "refund_order": "service"})

    live = results["refund_case"]
    assert live["assumed"] == []
    assert [(one["name"], one["held"]) for one in live["with_solution"]] == [("refund_once", True), ("looked_up", True)]
    control = live["controls"][0]
    assert control["name"] == "two_refunds"
    assert control["failed"] is True and control["assumed"] == []
    assert "refund" in stack.worlds[0].forwarded
    # A reset before the reference, the empty run and the control, and one after.
    assert stack.resets == 4
    assert live_proof.load_live(tmp_path)["refund_case"] == live

    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is True and proof.blocked_reason() is None


CALLED_ONCE = check(
    "n = sum(1 for c in calls if c.ok and c.name == 'refund_order' and c.arguments.get('order_id') == 'O-10')\n"
    "return None if n == 1 else f'{n} refund calls'"
)


def test_in_process_tool_stays_assumed(monkeypatch, tmp_path):
    # The refund runs inside the agent's own process: it has no endpoint, so its step is recorded
    # unrun and only a check that reads the calls can hold.
    goals = dict(refund_once=CALLED_ONCE, looked_up=REFUND_CATALOGUE["looked_up"])
    results, stack = _prove(monkeypatch, tmp_path, {"lookup_order": "service", "refund_order": "import"}, goals)
    assert "refund" not in stack.worlds[0].forwarded

    live = results["refund_case"]
    assert live["assumed"] == ["refund_order"]
    assert live["controls"][0]["assumed"] == ["refund_order"]
    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is False and proof.kept_as_written is True


def test_no_tool_proxy_plays_nothing(monkeypatch, tmp_path):
    contract = _contract({"lookup_order": "service", "refund_order": "service"})
    assert live_proof.service_endpoints(contract, {"target_http": "x"}) == {}
    assert live_proof.service_endpoints(contract, {"tool_proxy": "y"}) == {"lookup_order": "lookup", "refund_order": "refund"}


RAISES_ON_TWO = check(
    "if len(world.data['refunds']) > 1:\n    raise KeyError('second refund')\n"
    "return None if len(world.data['refunds']) == 1 else 'no refund'"
)


def test_a_check_that_raises_under_a_control_is_broken_not_failing(monkeypatch, tmp_path):
    install(monkeypatch)
    goals = dict(refund_once=RAISES_ON_TWO, looked_up=REFUND_CATALOGUE["looked_up"])
    proof = module.prove(refund_case(), catalogue(**goals), tmp_path)

    control = proof.controls[0]
    assert control["failed"] is False and control["broken"] == ["refund_once"]
    assert proof.proved is False
    assert proof.blocked_reason() == "broken_check"
    assert proof.broken == ["refund_once (under negative control two_refunds)"]


def test_a_control_that_could_not_be_played_is_no_evidence():
    proof = Proof(ready=True, solvable=True, vacuous=False, outcome="change")
    proof.controls = [{"name": "two_refunds", "why": "", "failed": None, "held": [], "assumed": []}]

    assert proof.ran_controls == []
    assert proof.proved is False
    assert proof.blocked_reason() == "control_not_run"


def test_a_control_that_crashes_on_the_runtime_keeps_the_case_live_proof(monkeypatch, tmp_path):
    real = live_proof._play
    played = []

    def play(scenario, world, steps):
        played.append(len(steps))
        if len(played) == 3:  # reference, empty run, then the control
            raise ConnectionError("tools service went away")
        return real(scenario, world, steps)

    monkeypatch.setattr(live_proof, "_play", play)
    results, _stack = _prove(monkeypatch, tmp_path, {"lookup_order": "service", "refund_order": "service"})

    live = results["refund_case"]
    assert [one["held"] for one in live["with_solution"]] == [True, True]
    control = live["controls"][0]
    assert control["failed"] is None and "tools service went away" in control["error"]
    proof = live_proof.apply_live(Proof(deferred=True), live)
    assert proof.proved is False and proof.blocked_reason() == "control_not_run"
