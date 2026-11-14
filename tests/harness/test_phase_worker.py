from __future__ import annotations

import asyncio
import os
import random
from concurrent.futures import ThreadPoolExecutor

import pytest

from fi.alk.bench._docker import docker_available
from fi.alk.harness.hosted_scheduler import _invoke, _PhaseMisuse, _PhaseTimeout
from fi.alk.harness.hosted_scheduler import _PhaseCrashed, _PhaseWorldGone
from fi.alk.harness.hosted_scheduler import _classify_ready, _classify_check
from fi.alk.harness.scenario_source import _compile_entry
from fi.alk.harness.world.handle import HostedWorld
from fi.alk.harness.world.stores.postgres import PostgresStore

pytestmark = pytest.mark.skipif(not docker_available(), reason="Docker required")


@pytest.fixture(scope="module")
def store():
    running = PostgresStore(version="16")
    running.start()
    try:
        running.apply("CREATE TABLE markers (id integer PRIMARY KEY, value text)")
        yield running
    finally:
        running.stop()


@pytest.fixture
def world(store):
    store.execute("TRUNCATE markers")
    return HostedWorld(store, 0, random.Random(42), {"markers": 0})


async def invoke(source, world, *, timeout=10, entry="setup"):
    fn = _compile_entry(source, label="phase-test", entry=entry)
    with ThreadPoolExecutor(max_workers=1) as executor:
        return await _invoke(fn, world, timeout=timeout, phase=entry, executor=executor)


def test_real_phase_writes_only_its_database_and_carries_rng(world, store):
    source = "def setup(world):\n world.put('markers', {'id': 1, 'value': str(world.rng.random())})\n"
    asyncio.run(invoke(source, world))
    expected = random.Random(42)
    assert store.table("markers") == [{"id": 1, "value": str(expected.random())}]
    assert world.rng.random() == expected.random()


def test_read_only_phase_keeps_misuse_classification(world, store):
    with pytest.raises(_PhaseMisuse):
        asyncio.run(
            invoke(
                "def ready(world):\n world.put('markers', {'id': 1, 'value': 'bad'})\n",
                world.read_only(),
                entry="ready",
            )
        )
    assert store.table("markers") == []


@pytest.mark.parametrize("custom", [False, True])
def test_world_error_family_keeps_misuse_classification(world, custom):
    source = "from fi.alk.harness.world.errors import WorldError\n"
    if custom:
        source += "class CustomError(WorldError): pass\n"
    error = "CustomError" if custom else "WorldError"
    source += f"def setup(world):\n raise {error}('invalid operation')\n"
    with pytest.raises(_PhaseMisuse):
        asyncio.run(invoke(source, world))


def test_world_unavailable_subclass_keeps_world_gone_classification(world):
    source = (
        "from fi.alk.harness.world.errors import WorldUnavailable\n"
        "class Disconnected(WorldUnavailable): pass\n"
        "def setup(world):\n raise Disconnected('connection lost')\n"
    )
    with pytest.raises(_PhaseWorldGone):
        asyncio.run(invoke(source, world))


def test_unrelated_exception_cannot_impersonate_world_error_by_name(world):
    source = (
        "class WorldUnavailable(ValueError): pass\n"
        "def setup(world):\n raise WorldUnavailable('bad argument')\n"
    )
    with pytest.raises(_PhaseCrashed):
        asyncio.run(invoke(source, world))


def test_timeout_cannot_write_after_world_is_released(world, store, tmp_path):
    started = tmp_path / "phase-started"
    source = (
        "def setup(world):\n"
        " import time\n from pathlib import Path\n"
        f" Path({str(started)!r}).touch()\n"
        " time.sleep(3)\n world.put('markers', {'id': 1, 'value': 'late'})\n"
    )

    async def run():
        with pytest.raises(_PhaseTimeout):
            await invoke(source, world, timeout=2)
        await asyncio.sleep(3.2)
        assert store.table("markers") == []

    asyncio.run(run())


def test_cancel_running_phase_cannot_write_after_world_is_released(
    world, store, tmp_path
):
    started = tmp_path / "phase-started"
    released = tmp_path / "world-released"
    source = (
        "def setup(world):\n"
        " import time\n from pathlib import Path\n"
        f" Path({str(started)!r}).touch()\n"
        f" while not Path({str(released)!r}).exists(): time.sleep(.01)\n"
        " world.put('markers', {'id': 1, 'value': 'late'})\n"
    )

    async def run():
        task = asyncio.create_task(invoke(source, world, timeout=60))
        try:

            async def wait_started():
                while not started.exists():
                    if task.done():
                        await task
                        pytest.fail("phase exited before starting")
                    await asyncio.sleep(0.01)

            await asyncio.wait_for(wait_started(), timeout=30)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            released.touch()
            await asyncio.sleep(0.2)
            assert store.table("markers") == []
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_phase_environment_and_cwd_do_not_leak_to_parent(world):
    original = dict(os.environ)
    cwd = os.getcwd()
    asyncio.run(
        invoke(
            "def setup(world):\n import os\n os.environ['PHASE_ONLY'] = 'yes'\n os.chdir('/')\n",
            world,
        )
    )
    assert dict(os.environ) == original
    assert os.getcwd() == cwd


@pytest.mark.parametrize(
    "entry,classify", [("ready", _classify_ready), ("check", _classify_check)]
)
@pytest.mark.parametrize(
    "source,expected",
    [
        ("False", False),
        ("True", True),
        ("None", None),
        ("''", ""),
        ("'  '", "  "),
        ("'failed'", "failed"),
        ("7", 7),
        ("[]", []),
        ("object()", object()),
    ],
)
def test_verdict_classification_is_preserved_across_worker_boundary(
    world, entry, classify, source, expected
):
    result = asyncio.run(
        invoke(
            f"def {entry}(world):\n return {source}\n",
            world.read_only(),
            entry=entry,
        )
    )
    assert classify(result) == classify(expected)


def test_agent_created_table_does_not_invalidate_existing_world(world, store):
    store.execute("CREATE TABLE agent_created (id integer PRIMARY KEY)")
    try:
        result = asyncio.run(
            invoke(
                "def ready(world):\n return world.state() == {'markers': []}\n",
                world.read_only(),
                entry="ready",
            )
        )
        assert result is True
        with pytest.raises(_PhaseMisuse):
            asyncio.run(
                invoke(
                    "def ready(world):\n world.put('markers', {'id': 1, 'value': 'bad'})\n",
                    world.read_only(),
                    entry="ready",
                )
            )
    finally:
        store.execute("DROP TABLE agent_created")
