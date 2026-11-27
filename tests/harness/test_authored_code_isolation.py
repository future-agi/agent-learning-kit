from __future__ import annotations

import os
import random
import sys

import pytest

from fi.alk.harness.checks import run_check, run_world_check
from fi.alk.harness.folder import apply_setup, check_ready
from fi.alk.harness.scenario import Scenario
from fi.alk.harness.world import handler_host
from fi.alk.harness.world.handle import HostedWorld
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


@pytest.mark.skipif(
    os.environ.get("ALK_HANDLER_ISOLATION") != "namespace",
    reason="requires the hosted image's user, mount, and network namespaces",
)
def test_hosted_namespace_hides_files_environment_and_network(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BUILDER_REVIEW_CANARY", "visible-secret")
    secret = tmp_path / "builder-secret"
    secret.write_text("visible-secret", encoding="utf-8")
    source = (
        "import os\n"
        "import socket\n"
        "def check(world, calls):\n"
        "    if os.environ.get('BUILDER_REVIEW_CANARY'):\n"
        "        return 'environment leaked'\n"
        f"    if os.path.exists({str(secret)!r}):\n"
        "        return 'filesystem leaked'\n"
        "    probe = socket.socket()\n"
        "    probe.settimeout(0.2)\n"
        "    try:\n"
        "        probe.connect(('1.1.1.1', 53))\n"
        "    except OSError:\n"
        "        return None\n"
        "    return 'network leaked'\n"
    )

    outcome = run_check(source, _world(), [])

    assert outcome.held, outcome.said


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


def test_isolated_setup_preserves_query_index_and_rng(monkeypatch) -> None:
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _world()
    world.world_index = 3
    world.rng = random.Random(42)
    world.query = world.store.query
    scenario = Scenario(
        name="contract",
        setup_code=(
            "def setup(world):\n"
            "    rows = world.query('SELECT order_id FROM orders')\n"
            "    if rows != [{'order_id': 'O1'}] or world.world_index != 3:\n"
            "        return 'world contract changed'\n"
            "    world.rng.random()\n"
        ),
    )

    outcome = apply_setup(scenario, world)

    expected = random.Random(42)
    expected.random()
    assert outcome.ok, outcome.said
    assert world.rng.random() == expected.random()


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


def test_isolated_check_exposes_only_the_world_api_and_keeps_stdout_protocol_clean(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    source = (
        "def check(world, calls):\n"
        "    print('diagnostic from authored code' * 100000)\n"
        "    return 'world leaked its store' if hasattr(world, 'store') else None\n"
    )

    outcome = run_check(source, _world(), [])

    assert outcome.held, outcome.said


@pytest.mark.parametrize(
    "body",
    [
        "return None",
        "return False",
        "return 7",
        "raise ValueError('bad check')",
    ],
)
def test_isolated_check_preserves_in_process_verdicts(monkeypatch, body: str) -> None:
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    source = f"def check(world, calls):\n    {body}\n"
    world = _world()
    world.isolated = False
    local = run_check(source, world, [])
    world.isolated = True

    isolated = run_check(source, world, [])

    assert (isolated.held, isolated.said, isolated.broken) == (
        local.held,
        local.said,
        local.broken,
    )


class _Rows:
    """The little of a hosted world's store these checks read."""

    def query(self, *_args):
        return []

    def read_log(self, *_args):
        return []

    def state(self, only=None):
        return {}


def _hosted(**extra) -> HostedWorld:
    return HostedWorld(
        _Rows(), 0, random.Random(0), {}, _validate_baseline=False, **extra
    )


def test_a_submitted_agents_hosted_check_runs_in_process_with_every_helper(
    monkeypatch,
) -> None:
    """The hosted image turns handler isolation on for every job. Only a release's code is
    authored with its environment; a submitted agent's live proof reads the whole world handle
    and every call field, as it did before."""
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    world = _hosted()
    calls = [
        Call(name="ask", arguments={}, ok=None, refused=None, error=None, worker_id="w1")
    ]
    source = (
        "def check(world, calls, reference):\n"
        "    if repeated_commits(world, reference) or committed_writes(world):\n"
        "        return 'a write'\n"
        "    if world.events() != []:\n"
        "        return 'an event'\n"
        "    if calls[0].ok is not None or calls[0].worker_id != 'w1':\n"
        "        return f'{calls[0].ok!r} {calls[0].worker_id!r}'\n"
    )

    outcome = run_check(source, world, calls)
    applied = apply_setup(
        Scenario(
            name="s",
            setup_code="def setup(world):\n    return str(callable(world.write_file))\n",
        ),
        world,
    )

    assert outcome.held, outcome.said
    assert applied.said == "True"


def test_a_releases_hosted_code_is_isolated(monkeypatch) -> None:
    monkeypatch.setenv("BUILDER_REVIEW_CANARY", "visible-secret")
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    source = (
        "import os\n"
        "def check(world, calls):\n"
        "    return os.environ.get('BUILDER_REVIEW_CANARY')\n"
    )

    assert run_check(source, _hosted(isolated=True), []).held
    assert not run_check(source, _hosted(), []).held


def test_an_isolated_check_reads_a_step_that_did_not_run_as_neither_ok_nor_failed(
    monkeypatch,
) -> None:
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    source = (
        "def check(world, calls):\n"
        "    seen = [call.ok for call in calls]\n"
        "    return None if seen == [None, True, False] else str(seen)\n"
    )

    outcome = run_check(
        source,
        _world(),
        [Call(name="ask", arguments={}, ok=ok) for ok in (None, True, False)],
    )

    assert outcome.held, outcome.said


def test_a_child_that_cannot_run_is_not_the_authors_broken_check(monkeypatch) -> None:
    """A verdict from a child that never ran would also prove a check that has to fail."""
    monkeypatch.setenv("ALK_HANDLER_ISOLATION", "process")
    monkeypatch.setattr(
        handler_host,
        "_command",
        lambda _mode: [sys.executable, "-c", "raise SystemExit('no namespace here')"],
    )
    with pytest.raises(handler_host.HandlerHostError, match="no namespace here"):
        run_check("def check(world, calls):\n    return None\n", _world(), [])
    with pytest.raises(handler_host.HandlerHostError):
        apply_setup(
            Scenario(name="s", setup_code="def setup(world):\n    pass\n"), _world()
        )


def test_a_check_that_never_finishes_is_no_verdict() -> None:
    with pytest.raises(handler_host.HandlerTimeout):
        handler_host.run_authored(
            "def check(world, calls):\n    while True:\n        pass\n",
            _world(),
            kind="check",
            name="spin",
            mode="process",
            timeout=1.0,
        )
