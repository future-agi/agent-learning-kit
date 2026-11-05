from __future__ import annotations

import pytest

from fi.alk.harness.checks import run_check, run_world_check
from fi.alk.harness.folder import apply_setup, check_ready
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.world.runtime import Call, GeneratedWorld


def _world() -> GeneratedWorld:
    world = GeneratedWorld(":memory:", kind="sqlite")
    world.store.apply(
        "CREATE TABLE orders (order_id TEXT PRIMARY KEY, status TEXT);"
        "INSERT INTO orders VALUES ('O1', 'open');"
    )
    world.handlers = {
        "close_order": (
            "def handle(args, db):\n"
            "    db.execute(\"UPDATE orders SET status = 'closed' WHERE order_id = ?\", "
            "(args['order_id'],))\n"
            "    return {'closed': args['order_id']}\n"
        )
    }
    world.isolated = True
    return world


@pytest.mark.parametrize("entry", ["setup", "ready"])
def test_isolated_scenario_code_cannot_read_the_callers_environment(
    monkeypatch, entry: str
) -> None:
    monkeypatch.setenv("BUILDER_REVIEW_CANARY", "visible-secret")
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    scenario = Scenario(
        name="private",
        setup_code=(
            "import os\n"
            "def setup(world):\n"
            "    return os.environ.get('BUILDER_REVIEW_CANARY')\n"
        ),
        ready_code=(
            "import os\n"
            "def ready(world):\n"
            "    return os.environ.get('BUILDER_REVIEW_CANARY')\n"
        ),
    )

    outcome = (
        apply_setup(scenario, _world())
        if entry == "setup"
        else check_ready(scenario, _world())
    )

    assert outcome.ok, outcome.said


def test_isolated_checks_cannot_read_the_callers_environment(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_REVIEW_CANARY", "visible-secret")
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    source = (
        "import os\n"
        "def check(world, calls=None):\n"
        "    return os.environ.get('BUILDER_REVIEW_CANARY')\n"
    )
    world = _world()

    scenario = run_check(source, world, [])
    world_check = run_world_check(source, world)

    assert scenario.held, scenario.said
    assert world_check.held, world_check.said


def test_isolated_setup_mutates_the_real_world_and_can_call_a_tool(monkeypatch) -> None:
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _world()
    scenario = Scenario(
        name="close",
        setup_code=(
            "def setup(world):\n"
            "    world.put('orders', {'order_id': 'O2', 'status': 'open'})\n"
            "    call = world.call('close_order', {'order_id': 'O1'})\n"
            "    return None if call.ok else call.error\n"
        ),
    )

    outcome = apply_setup(scenario, world)

    assert outcome.ok, outcome.said
    assert world.state()["orders"] == [
        {"order_id": "O1", "status": "closed"},
        {"order_id": "O2", "status": "open"},
    ]


def test_isolated_check_receives_calls_and_reference_with_attribute_access(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _world()
    calls = [Call(name="close_order", arguments={"order_id": "O1"})]
    reference = [{"tool": "close_order", "arguments": {"order_id": "O1"}}]
    source = (
        "def check(world, calls, reference):\n"
        "    if calls[0].name != reference[0]['tool']:\n"
        "        return 'call did not match the reference'\n"
        "    if calls[0].arguments != reference[0]['arguments']:\n"
        "        return 'arguments did not match the reference'\n"
        "    return None\n"
    )

    outcome = run_check(source, world, calls, reference=reference)

    assert outcome.held, outcome.said
