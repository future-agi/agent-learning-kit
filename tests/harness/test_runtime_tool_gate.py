"""The gate that earns the build stage its shell.

A world is allowed to grade an agent only once it has shown it answers that agent's own tools.
The case these tests exist for is the middle one: a world that could not be asked must not read
the same as a world that was asked and was fine.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from fi.alk.harness.world.probe import verify_runtime_tools
from fi.alk.harness.world.runtime import Call


def _contract(*names: str) -> SimpleNamespace:
    return SimpleNamespace(
        tools=[SimpleNamespace(name=n, args={}, arg_types={}) for n in names]
    )


class _Reachable:
    """A world whose tools can actually be called."""

    def __init__(self, outcomes: dict[str, Call]) -> None:
        self.runtime_tools = set(outcomes)
        self.endpoint_for = {name: name for name in outcomes}
        self._outcomes = outcomes

    def forward(self, endpoint, arguments, *, record=False):
        return self._outcomes[endpoint]


class _NoSeam:
    """A world that declares runtime tools and offers no way to call them."""

    def __init__(self, *names: str) -> None:
        self.runtime_tools = set(names)
        self.endpoint_for = {}


def test_a_world_that_answers_is_checked_and_ok():
    world = _Reachable({"book_ride": Call(name="book_ride", arguments={}, ok=True)})
    verdict = verify_runtime_tools(world, _contract("book_ride"))
    assert verdict.checked is True
    assert verdict.broken == []
    assert verdict.ok is True


def test_a_refusing_tool_is_working_not_broken():
    # A world that rejects a nonexistent id is doing its job; only a crash counts against it.
    world = _Reachable(
        {"book_ride": Call(name="book_ride", arguments={}, ok=False, refused=True)}
    )
    assert verify_runtime_tools(world, _contract("book_ride")).ok is True


def test_a_server_error_is_broken():
    world = _Reachable(
        {
            "book_ride": Call(
                name="book_ride", arguments={}, ok=False, error="500 Internal Server Error"
            )
        }
    )
    verdict = verify_runtime_tools(world, _contract("book_ride"))
    assert verdict.checked is True
    assert verdict.ok is False
    assert "book_ride" in verdict.broken[0]


def test_a_world_with_no_seam_is_not_checked_and_never_reads_as_ok():
    # The defect this whole gate replaces: recording an unexecuted tool as passing.
    world = _NoSeam("book_ride", "cancel_ride")
    verdict = verify_runtime_tools(world, _contract("book_ride", "cancel_ride"))
    assert verdict.checked is False
    assert verdict.ok is False
    assert sorted(verdict.tools) == ["book_ride", "cancel_ride"]
    assert "forward" in verdict.reason


def test_a_world_declaring_no_runtime_tools_is_trivially_fine():
    verdict = verify_runtime_tools(SimpleNamespace(), _contract())
    assert verdict.checked is True
    assert verdict.ok is True


# --- the scheduler's use of it ----------------------------------------------------------------


def _scheduler(contract):
    from fi.alk.harness.hosted_scheduler import HostedScheduler

    return HostedScheduler(
        pool=SimpleNamespace(),
        world_factory=SimpleNamespace(),
        call_runner=SimpleNamespace(),
        outbound=SimpleNamespace(),
        job_seed=1,
        contract=contract,
    )


def test_scheduler_refuses_a_world_whose_tools_are_broken():
    world = _Reachable(
        {"book_ride": Call(name="book_ride", arguments={}, ok=False, error="boom")}
    )
    cause = asyncio.run(_scheduler(_contract("book_ride"))._verify_world(world, 0))
    assert "runtime tools did not answer" in cause
    assert "book_ride" in cause


def test_scheduler_allows_an_unverifiable_world_but_names_what_goes_ungraded(caplog):
    # Hosted has no seam yet, so failing every run would be a worse lie than the silence. It is
    # allowed through, loudly, at a level the hosted guest actually emits.
    scheduler = _scheduler(_contract("book_ride"))
    with caplog.at_level(logging.WARNING, logger="fi.alk.harness.hosted_scheduler"):
        cause = asyncio.run(scheduler._verify_world(_NoSeam("book_ride"), 3))
    assert cause == ""
    assert "ungraded" in caplog.text
    assert "book_ride" in caplog.text


def test_a_world_is_verified_once_not_per_scenario():
    calls: list[str] = []

    class Counting(_Reachable):
        def forward(self, endpoint, arguments, *, record=False):
            calls.append(endpoint)
            return super().forward(endpoint, arguments, record=record)

    world = Counting({"book_ride": Call(name="book_ride", arguments={}, ok=True)})
    scheduler = _scheduler(_contract("book_ride"))
    for _ in range(3):
        assert asyncio.run(scheduler._verify_world(world, 0)) == ""
    assert calls == ["book_ride"]


def test_no_contract_means_no_gate():
    scheduler = _scheduler(None)
    assert asyncio.run(scheduler._verify_world(_NoSeam("book_ride"), 0)) == ""
