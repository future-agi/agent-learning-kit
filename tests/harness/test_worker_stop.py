"""A worker the app serves as its own process is stopped, not only refused, once its deadline in the trial passes."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import call_runner, hosted_scheduler, world_door
from fi.alk.harness.hosted_entrypoint import _worker_processes
from fi.alk.harness.hosted_scheduler import HostedScheduler, WorldPool
from fi.alk.harness.process_runtime import EnvironmentRuntime, ProcessRuntimeProvider, RuntimeState


def _runtime(world_index: int = 0) -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id=f"rt-{world_index}",
        world_index=world_index,
        bundle_digest="sha256:" + "0" * 64,
        state=RuntimeState.READY,
    )


class _Pool:
    def __init__(self, ends: bool = True) -> None:
        self.ends = ends
        self.stopped: list[str] = []

    async def stop_process(self, runtime, process_name: str) -> bool:
        self.stopped.append(process_name)
        return self.ends


def _scheduler(pool, processes) -> HostedScheduler:
    scheduler = object.__new__(HostedScheduler)
    scheduler._pool = pool
    scheduler._worker_processes = dict(processes)
    return scheduler


def _watch(monkeypatch, scheduler, deadlines, *, rounds: int = 5) -> list[tuple]:
    recorded: list[tuple] = []
    monkeypatch.setattr(hosted_scheduler, "_WORKER_STOP_POLL_SECONDS", 0)
    monkeypatch.setattr(call_runner, "_find_postgres_endpoint", lambda _runtime: SimpleNamespace(address="db"))
    monkeypatch.setattr(call_runner, "read_trial_deadlines", lambda _address: deadlines)

    def claim(_address, worker, process, stop):
        # The trial is not sealed: the stop is made, and kept when the provider ended the process.
        if stop():
            recorded.append((worker, process))
            return True
        return False

    monkeypatch.setattr(hosted_scheduler, "claim_worker_stop", claim)

    async def run() -> None:
        task = asyncio.create_task(scheduler._stop_workers_past_deadline(_runtime()))
        for _ in range(rounds):
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    return recorded


def test_a_worker_past_its_own_deadline_has_its_process_ended_once_and_kept(monkeypatch) -> None:
    pool = _Pool()
    scheduler = _scheduler(pool, {"refund": "refund-worker", "order_lookup": "lookup-worker"})
    past, future = time.time() - 1, time.time() + 3600
    recorded = _watch(monkeypatch, scheduler, (None, {"refund": past, "order_lookup": future}))
    assert pool.stopped == ["refund-worker"]
    assert recorded == [("refund", "refund-worker")]


def test_one_deadline_for_all_workers_stops_each_served_worker(monkeypatch) -> None:
    pool = _Pool()
    scheduler = _scheduler(pool, {"refund": "refund-worker", "order_lookup": "lookup-worker"})
    recorded = _watch(monkeypatch, scheduler, (time.time() - 1, {}))
    assert sorted(pool.stopped) == ["lookup-worker", "refund-worker"]
    assert sorted(recorded) == [("order_lookup", "lookup-worker"), ("refund", "refund-worker")]


def test_no_deadline_stops_no_one_and_a_stop_the_provider_cannot_make_is_not_kept(monkeypatch) -> None:
    pool = _Pool()
    assert _watch(monkeypatch, _scheduler(pool, {"refund": "refund-worker"}), (None, {})) == []
    assert pool.stopped == []
    unable = _Pool(ends=False)
    assert _watch(monkeypatch, _scheduler(unable, {"refund": "refund-worker"}), (time.time() - 1, {})) == []
    assert unable.stopped == ["refund-worker"]


def test_the_pool_asks_the_provider_and_a_provider_that_cannot_stop_one_process_says_no() -> None:
    class _Provider:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def stop_process(self, runtime, process_name):
            self.asked.append(process_name)
            return True

    pool = object.__new__(WorldPool)
    pool._provisioner = _Provider()
    assert asyncio.run(pool.stop_process(_runtime(), "refund-worker")) is True
    assert pool._provisioner.asked == ["refund-worker"]
    pool._provisioner = object()
    assert asyncio.run(pool.stop_process(_runtime(), "refund-worker")) is False


class _Handle:
    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True

    def interrupt(self) -> None:
        self.terminated = True

    def wait(self, timeout: float) -> bool:
        return True

    def kill(self) -> None:
        self.terminated = True

    def is_running(self) -> bool:
        return not self.terminated


def test_the_provider_ends_only_that_world_s_own_process_never_a_job_shared_engine() -> None:
    provider = object.__new__(ProcessRuntimeProvider)
    own, other_world, shared = _Handle(), _Handle(), _Handle()
    provider._job_shared_handles = {"world-db": SimpleNamespace(handle=shared)}
    provider._world_handles = {
        0: {"refund-worker": SimpleNamespace(handle=own), "world-db": SimpleNamespace(handle=shared)},
        1: {"refund-worker": SimpleNamespace(handle=other_world)},
    }
    assert asyncio.run(provider.stop_process(_runtime(0), "refund-worker")) is True
    assert (own.terminated, other_world.terminated) == (True, False)
    assert asyncio.run(provider.stop_process(_runtime(0), "world-db")) is False
    assert asyncio.run(provider.stop_process(_runtime(0), "no-such-process")) is False
    assert shared.terminated is False


def test_the_sealed_contract_names_each_worker_served_as_its_own_process(tmp_path: Path) -> None:
    (tmp_path / "contract.json").write_text(
        json.dumps(
            {
                "workers": [
                    {"id": "refund", "process": "refund-worker"},
                    {"id": "order_lookup", "process": ""},
                    {"id": "", "process": "nameless"},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert _worker_processes(tmp_path) == {"refund": "refund-worker"}
    assert _worker_processes(tmp_path / "missing") == {}


def test_a_contract_worker_may_name_the_process_that_runs_it_alone() -> None:
    from fi.alk.harness.contract import DeclaredWorker

    assert DeclaredWorker(id="refund").process == ""
    assert DeclaredWorker(id="refund", process="refund-worker").process == "refund-worker"


def test_a_stop_the_seal_refuses_is_never_made(monkeypatch) -> None:
    pool = _Pool()
    scheduler = _scheduler(pool, {"refund": "refund-worker"})
    monkeypatch.setattr(hosted_scheduler, "_WORKER_STOP_POLL_SECONDS", 0)
    monkeypatch.setattr(call_runner, "_find_postgres_endpoint", lambda _runtime: SimpleNamespace(address="db"))
    monkeypatch.setattr(call_runner, "read_trial_deadlines", lambda _address: (time.time() - 1, {}))
    monkeypatch.setattr(hosted_scheduler, "claim_worker_stop", lambda _address, worker, process, stop: False)

    async def run() -> None:
        task = asyncio.create_task(scheduler._stop_workers_past_deadline(_runtime()))
        await asyncio.sleep(0.05)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert pool.stopped == []


@pytest.fixture
def world_database(monkeypatch):
    """A real Postgres for the seal's lock: set ALK_TEST_POSTGRES_DSN (a database this test may create tables in)."""
    dsn = os.environ.get("ALK_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set ALK_TEST_POSTGRES_DSN to run the worker stop seal tests")
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("DROP TABLE IF EXISTS _alk_trial_faults, _alk_worker_stops")
    monkeypatch.setattr(call_runner, "_find_postgres_endpoint", lambda _runtime: SimpleNamespace(address=dsn))
    return dsn


def test_a_stop_after_the_seal_is_never_made_and_the_next_trial_may_stop_again(world_database) -> None:
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert call_runner.read_worker_stops(_runtime()) == []
    asked: list[str] = []
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: asked.append("x") or True) is False
    assert asked == []
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: True) is True
    assert [row["worker"] for row in call_runner.read_worker_stops(_runtime())] == ["refund"]


def test_the_seal_waits_for_a_stop_under_way_and_seals_it(world_database) -> None:
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    inside = threading.Event()

    def slow_stop() -> bool:
        inside.set()
        time.sleep(1.0)
        return True

    claimed: list[bool] = []
    thread = threading.Thread(
        target=lambda: claimed.append(world_door.claim_worker_stop(world_database, "refund", "refund-worker", slow_stop))
    )
    thread.start()
    assert inside.wait(5)
    # The process is being ended as the call seals: the seal waits and includes it.
    sealed = call_runner.read_worker_stops(_runtime())
    thread.join(5)
    assert claimed == [True]
    assert [row["worker"] for row in sealed] == ["refund"]


def test_a_deadline_cleared_with_the_call_stops_no_one(world_database) -> None:
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    call_runner._write_trial_state(_runtime(), [])
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: True) is False
    call_runner._write_trial_state(_runtime(), [], worker_deadlines={"refund": time.time() + 3600})
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: True) is False


def test_a_world_s_trial_tables_are_made_once_and_made_again_after_its_database_is_made_anew(world_database) -> None:
    import psycopg

    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    # A reset from the seed drops them; the next trial still writes its state, and a stop can still be claimed.
    with psycopg.connect(world_database, autocommit=True) as connection:
        connection.execute("DROP TABLE IF EXISTS _alk_trial_faults, _alk_worker_stops")
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: True) is True


def test_a_trial_s_start_changes_no_schema_after_the_world_s_first(monkeypatch) -> None:
    statements: list[str] = []

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def execute(self, sql, *_args):
            statements.append(sql)

    psycopg = pytest.importorskip("psycopg")
    monkeypatch.setattr(psycopg, "connect", lambda *_args, **_kwargs: _Connection())
    monkeypatch.setattr(call_runner, "_find_postgres_endpoint", lambda _runtime: SimpleNamespace(address="db-once"))
    world_door._TRIAL_SCHEMA_MADE.discard("db-once")
    call_runner._write_trial_state(_runtime(), [])
    first = [one for one in statements if one.lstrip().upper().startswith(("ALTER", "CREATE"))]
    statements.clear()
    call_runner._write_trial_state(_runtime(), [])
    # Schema changes take the table's lock a stop under way holds: never again at a later trial's start.
    assert first and not [one for one in statements if one.lstrip().upper().startswith(("ALTER", "CREATE"))]



def test_a_worker_that_ran_on_past_its_cancel_is_recorded_as_not_stopped(
    world_database,
) -> None:
    """Its run did not end at its cancel: the claim says so, and the seal keeps it as not held to its deadline."""
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert (
        world_door.claim_worker_stop(
            world_database, "refund", "refund-worker", lambda: False
        )
        is False
    )
    assert [
        (row["worker"], row["reason"])
        for row in call_runner.read_worker_stops(_runtime())
    ] == [("refund", world_door.NOT_STOPPED)]


def test_a_stop_left_by_an_earlier_trial_is_never_this_trial_s(world_database) -> None:
    """The clear before this trial failed and an earlier trial's stop is still in the table: the seal, reading from
    this trial's start, never publishes it."""
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert (
        world_door.claim_worker_stop(
            world_database, "refund", "refund-worker", lambda: True
        )
        is True
    )
    this_trial_started = time.time() + 1
    assert call_runner.read_worker_stops(_runtime(), this_trial_started) == []


def test_a_worker_that_runs_on_in_a_trial_after_a_failed_clear_is_never_read_as_held(world_database) -> None:
    """Trial 1 stopped refund; the clear before trial 2 failed, so its row stays. In trial 2 refund passes its deadline
    again and ignores the cancel: that is this trial's, read as not held, never dropped against the old row."""
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: True) is True
    world_door.mark_worker_run(world_database, "refund")
    trial_two = time.time() + 0.01
    time.sleep(0.02)
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: False) is False
    world_door.mark_worker_run(world_database, "refund")
    assert [
        (row["worker"], row["reason"]) for row in call_runner.read_worker_stops(_runtime(), trial_two)
    ] == [("refund", world_door.NOT_STOPPED)]
    assert call_runner.read_worker_runs(_runtime(), trial_two) == {"refund"}


def test_a_seal_that_times_out_on_a_stop_under_way_says_the_stops_are_not_known(
    world_database, monkeypatch
) -> None:
    """The seal waits on the trial's row while a stop is under way; when it times out, the stops are unknown (None),
    never an empty list a later commit could contradict."""
    monkeypatch.setattr(call_runner, "_SEAL_LOCK_TIMEOUT", "200ms")
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    inside = threading.Event()

    def slow_stop() -> bool:
        inside.set()
        time.sleep(1.5)
        return True

    thread = threading.Thread(
        target=lambda: world_door.claim_worker_stop(
            world_database, "refund", "refund-worker", slow_stop
        )
    )
    thread.start()
    assert inside.wait(5)
    sealed = call_runner.read_worker_stops(_runtime())
    thread.join(5)
    assert sealed is None


def test_what_the_seal_s_stops_mean_for_grading() -> None:
    document = {"worker_budget_seconds": {"refund": 5}}
    notices, ran_on = call_runner.worker_stop_outcomes(
        [
            {"worker": "refund", "reason": "deadline_passed"},
            {"worker": "lookup", "reason": world_door.NOT_STOPPED},
        ],
        document,
        None,
    )
    assert [(one["code"], one["workers"]) for one in notices] == [
        (call_runner.WORKER_STOPPED_AT_DEADLINE, ["refund"])
    ]
    assert [one["worker"] for one in ran_on] == ["lookup"]
    # Stops not known: every worker this trial gave a deadline is not shown held.
    notices, ran_on = call_runner.worker_stop_outcomes(None, document, None)
    assert notices == [] and [one["worker"] for one in ran_on] == ["refund"]


def test_a_run_marked_by_an_earlier_trial_is_never_this_trial_s(world_database) -> None:
    """A run of the worker marked in an earlier trial, left by a clear that failed: never read as this trial's, so
    its deadline is never taken as held because of it."""
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    world_door.mark_worker_run(world_database, "refund")
    assert call_runner.read_worker_runs(_runtime()) == {"refund"}
    assert call_runner.read_worker_runs(_runtime(), time.time() + 1) == set()


def test_a_trial_that_stops_a_worker_an_earlier_trial_left_running_on_reads_it_stopped(world_database) -> None:
    """Trial 1's refund ran on past its cancel and the clear before trial 2 failed, so its `not_stopped` row stays. In
    trial 2 refund is stopped at its deadline: that is this trial's stop, never the earlier trial's run on."""
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: False) is False
    time.sleep(0.02)
    trial_two = time.time()
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: True) is True
    assert [
        (row["worker"], row["reason"]) for row in call_runner.read_worker_stops(_runtime(), trial_two)
    ] == [("refund", "deadline_passed")]


def test_a_run_on_in_this_trial_is_never_turned_into_a_stop_by_a_later_claim(world_database) -> None:
    call_runner._write_trial_state(_runtime(), [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: False) is False
    assert world_door.claim_worker_stop(world_database, "refund", "refund-worker", lambda: True) is True
    assert [(row["worker"], row["reason"]) for row in call_runner.read_worker_stops(_runtime())] == [
        ("refund", world_door.NOT_STOPPED)
    ]


def test_unread_stops_hold_no_declared_worker_to_a_scalar_deadline_whatever_its_name(tmp_path) -> None:
    """Two workers named alike and one with no name: each is a worker the trial's one deadline fell on."""
    (tmp_path / "contract.json").write_text(
        json.dumps(
            {
                "workers": [
                    {"id": "sales", "name": "Assistant"},
                    {"id": "support", "name": "Assistant"},
                    {"id": "billing", "name": ""},
                    {"id": "refund"},
                ]
            }
        )
    )
    notices, unenforced = call_runner.worker_stop_outcomes(None, {"worker_budget_seconds": 30}, tmp_path)
    assert notices == []
    assert [one["worker"] for one in unenforced] == ["billing", "refund", "sales", "support"]
