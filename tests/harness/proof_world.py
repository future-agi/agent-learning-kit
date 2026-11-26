"""A small in-memory world for proof tests: an orders table, refunds, and optional unbound tools."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

from fi.alk.harness import prove as module
from fi.alk.harness.catalogue import Catalogue, SubGoal
from fi.alk.harness.folder import Outcome
from fi.alk.harness.scenario import Step
from fi.alk.harness.world.runtime import Call

BASE = {"orders": {"O-10": {"age_days": 10, "total": 42.5}, "O-20": {"age_days": 20, "total": 18.0}}, "refunds": []}


class FakeWorld:
    def __init__(self, data: dict[str, Any], runtime_tools=(), *, unreadable: bool = False) -> None:
        self.data = copy.deepcopy(data)
        self.runtime_tools = set(runtime_tools)
        self.endpoint_for: dict[str, str] = {}
        self.calls: list[Call] = []
        self.unreadable = unreadable

    def call(self, name: str, arguments: dict[str, Any]) -> Call:
        if name == "lookup_order":
            order = self.data["orders"].get(arguments.get("order_id"))
            call = Call(name, arguments, result=order, ok=order is not None, error="" if order else "no such order")
        elif name in {"refund_order", "place_order"}:
            order = self.data["orders"].get(arguments.get("order_id"))
            if order is None or order["age_days"] > 14:
                call = Call(name, arguments, ok=False, error="refused by policy", refused=True)
            else:
                self.data["refunds"].append(arguments.get("order_id"))
                call = Call(name, arguments, result={"refunded": arguments.get("order_id")})
        else:
            call = Call(name, arguments, ok=False, error="unknown tool")
        self.calls.append(call)
        return call

    def state(self) -> dict[str, Any]:
        if self.unreadable:
            raise RuntimeError("state unavailable")
        return copy.deepcopy(self.data)

    def reset(self) -> None:
        pass

    def close(self) -> None:
        pass


def install(monkeypatch, *, data=BASE, runtime_tools=(), unreadable=False, ready=True, ready_said="") -> None:
    """Make prove() use FakeWorld for every gate, without a world on disk."""

    def prepared(scenario, world_root):
        world = FakeWorld(data, runtime_tools, unreadable=unreadable)
        return world, Outcome(True), (Outcome(True) if ready else Outcome(False, ready_said))

    monkeypatch.setattr(module, "prepared", prepared)
    monkeypatch.setattr(module, "native_runtime", lambda world_root: False)
    monkeypatch.setattr(module, "restore", lambda world_root: FakeWorld(data, runtime_tools, unreadable=unreadable))


def check(body: str) -> str:
    return "def check(world, calls):\n" + "".join(f"    {line}\n" for line in body.splitlines())


def catalogue(**checks: str) -> Catalogue:
    return Catalogue(sub_goals=[SubGoal(name=name, what=f"The agent {name.replace('_', ' ')}.", check=source) for name, source in checks.items()])


def scenario(solution, *, sub_goals, name="refund_case", instruction="", **extra) -> SimpleNamespace:
    values = dict(
        name=name,
        scenario_key=name,
        sub_goals=list(sub_goals),
        solution=[Step(**one) if isinstance(one, dict) else one for one in solution],
        setup_code="",
        ready_code="",
        alternatives=[],
        instruction=instruction,
        tests="",
        variables={},
        persona=None,
        negative_controls=[],
        sources=[],
        use_case="",
    )
    values.update(extra)
    return SimpleNamespace(**values)


ONE_REFUND = check("n = world.data['refunds'].count('O-10')\nreturn None if n == 1 else f'{n} refunds'")


LOOKED_UP_O10 = check(
    "return None if any(c.name == 'lookup_order' and c.ok and c.arguments.get('order_id') == 'O-10' for c in calls) "
    "else 'O-10 never looked up'"
)
LOOKUP_O10 = {"tool": "lookup_order", "arguments": {"order_id": "O-10"}, "fixed": {"order_id": "the caller"}}
REFUND_O10 = {"tool": "refund_order", "arguments": {"order_id": "O-10"}, "fixed": {"order_id": "the caller"}}


def control(name, why, steps, must_fail=()):
    from fi.alk.harness.scenario import NegativeControl

    return NegativeControl(name=name, why=why, steps=steps, must_fail=list(must_fail))


def refund_case(**extra):
    """A 10-day order refunded once; its control refunds twice."""
    values = dict(
        sub_goals=["refund_once", "looked_up"],
        instruction="I want my money back for order O-10.",
        negative_controls=[control("two_refunds", "refunds the order twice", [LOOKUP_O10, REFUND_O10, REFUND_O10])],
    )
    values.update(extra)
    return scenario([LOOKUP_O10, REFUND_O10], **values)


REFUND_CATALOGUE = dict(refund_once=ONE_REFUND, looked_up=LOOKED_UP_O10)
