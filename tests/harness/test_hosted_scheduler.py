"""`hosted_scheduler.py` against in-memory fakes — no real provisioner, no real postgres.

`asyncio.run` drives every `async def` seam here, matching `test_process_runtime.py`'s own
convention (no pytest-asyncio dependency in this repo).
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fi.alk.harness import hosted_scheduler as hs
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState
from fi.alk.harness.world.errors import (
    WorldReadOnly,
    WorldStateTooLarge,
    WorldUnavailable,
    WorldUsageError,
)

# --- fakes ---------------------------------------------------------------------------------


def _runtime(index: int, state: RuntimeState = RuntimeState.READY) -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id=f"digest:w{index}", world_index=index, bundle_digest="digest", state=state
    )


class FakeProvisioner:
    """Mirrors `ProcessRuntimeProvider`'s real async shape, including the two properties the
    review named as fidelity gaps: `reset_scripts` lets a test script one world's next N reset
    outcomes (anything unscripted resets clean to READY), and every provider call — including
    `healthy()` (R13: spine v1.12 §4.5b folded it into the same non-reentrant set) — goes through
    `_serialized`, which both yields (`await asyncio.sleep(0)` — so a genuine overlap has a real
    chance to interleave) and asserts no second call is ever in flight at the same time, matching
    "not reentrant" (B1/B2's own regression test)."""

    def __init__(
        self, instances: int, *, reset_scripts: dict[int, list[RuntimeState]] | None = None
    ) -> None:
        self.instances = instances
        self.reset_scripts = reset_scripts or {}
        self.provision_calls = 0
        self.reset_calls = 0
        self.closed = False
        self._runtimes = {i: _runtime(i) for i in range(instances)}
        self._busy = False

    @contextlib.asynccontextmanager
    async def _serialized(self):
        # The `try/finally` wraps the yielding `sleep(0)` too — `close()` cancelling an in-flight
        # reconcile (M6) must still clear `_busy`, or a cancellation lands this assertion stuck
        # True forever and fails every later call in the same test for the wrong reason.
        assert not self._busy, "provider port called reentrantly (provision/reset/healthy/close overlap)"
        self._busy = True
        try:
            await asyncio.sleep(0)
            yield
        finally:
            self._busy = False

    async def provision(
        self, bundle: Any, *, source: Path, bundle_dir: Path, work_directory: Path,
        contract: Any | None = None, instances: int = 1,
    ) -> list[EnvironmentRuntime]:
        async with self._serialized():
            self.provision_calls += 1
            for index in range(instances):
                if index not in self._runtimes or self._runtimes[index].state in (
                    RuntimeState.STOPPED, RuntimeState.UNHEALTHY,
                ):
                    self._runtimes[index] = _runtime(index, RuntimeState.READY)
            return [self._runtimes[index] for index in range(instances) if index in self._runtimes]

    async def reset(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> None:
        async with self._serialized():
            self.reset_calls += 1
            script = self.reset_scripts.get(runtime.world_index)
            runtime.state = script.pop(0) if script else RuntimeState.READY

    async def healthy(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> bool:
        async with self._serialized():
            return runtime.state is RuntimeState.READY

    async def close(self, *, work_directory: Path) -> None:
        async with self._serialized():
            self.closed = True


class InMemoryWorld:
    """The six-verb surface, backed by a plain dict instead of postgres — enough to exercise the
    scheduler's own control flow without a `PostgresStore`."""

    def __init__(self, world_index: int, rng: random.Random, *, read_only: bool = False) -> None:
        self.world_index = world_index
        self.rng = rng
        self._read_only = read_only
        self.rows: dict[str, list[dict[str, Any]]] = {}

    def state(self, table: str | None = None) -> dict[str, list[dict[str, Any]]]:
        return dict(self.rows) if table is None else {table: list(self.rows.get(table, []))}

    def put(self, collection: str, record: dict[str, Any], *, key: str = "") -> dict[str, Any]:
        if self._read_only:
            raise WorldReadOnly("read-only world")
        self.rows.setdefault(collection, []).append(record)
        return record

    def change(self, collection: str, key: str, changes: dict[str, Any], *, by: str = "") -> int:
        if self._read_only:
            raise WorldReadOnly("read-only world")
        return 0

    def drop(self, collection: str, key: str = "", *, by: str = "") -> int:
        if self._read_only:
            raise WorldReadOnly("read-only world")
        return 0

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> hs.Call:
        if self._read_only:
            raise WorldReadOnly("read-only world")
        raise NotImplementedError

    def query(self, sql: str, params: Any = ()) -> list[dict[str, Any]]:
        return []

    def read_only(self) -> "InMemoryWorld":
        return InMemoryWorld(self.world_index, self.rng, read_only=True)


class FakeWorldFactory:
    async def create(self, runtime: EnvironmentRuntime, *, rng: random.Random) -> InMemoryWorld:
        return InMemoryWorld(runtime.world_index, rng)


@dataclass
class FakeSubGoal:
    name: str
    fn: Any
    judged: str = ""

    def check(self, world: Any, calls: Any) -> object:
        return self.fn(world, calls)


@dataclass
class FakeScenario:
    scenario_key: str
    scenario_id: str
    sub_goals: list[FakeSubGoal] = field(default_factory=list)
    setup_fn: Any = lambda world: None
    ready_fn: Any = lambda world: None

    def setup(self, world: Any) -> object:
        return self.setup_fn(world)

    def ready(self, world: Any) -> object:
        return self.ready_fn(world)


class FakeCallRunner:
    def __init__(self, outcomes: dict[str, Any]) -> None:
        self.outcomes = outcomes
        self.calls: list[tuple[str, int]] = []

    async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
        self.calls.append((scenario.scenario_key, runtime.world_index))
        outcome = self.outcomes[scenario.scenario_key]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeOutbound:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.receipts: list[hs.ResultReceipt] = []

    async def scenario_started(self, *, scenario_key: str, world_index: int, scenario_attempt: int) -> None:
        self.events.append(("scenario_started", {"scenario_key": scenario_key, "world_index": world_index, "scenario_attempt": scenario_attempt}))

    async def scenario_retried(self, *, scenario_key: str, from_world: int, to_world: int) -> None:
        self.events.append(("scenario_retried", {"scenario_key": scenario_key, "from_world": from_world, "to_world": to_world}))

    async def world_unhealthy(self, *, world_index: int, cause: str) -> None:
        self.events.append(("world_unhealthy", {"world_index": world_index, "cause": cause}))

    async def log(self, *, level: str, message: str) -> None:
        self.events.append(("log", {"level": level, "message": message}))

    async def receipt(self, receipt: hs.ResultReceipt) -> None:
        self.receipts.append(receipt)


class FailingOutbound(FakeOutbound):
    """B3 regression fixture: a subset of methods raise, standing in for a dead transport."""

    def __init__(self, *, fail_on: set[str]) -> None:
        super().__init__()
        self._fail_on = fail_on

    async def scenario_started(self, **kwargs: Any) -> None:
        if "scenario_started" in self._fail_on:
            raise ConnectionError("outbound transport down")
        await super().scenario_started(**kwargs)

    async def receipt(self, receipt: hs.ResultReceipt) -> None:
        if "receipt" in self._fail_on:
            raise ConnectionError("outbound transport down")
        await super().receipt(receipt)


def _call_outcome(turns: int = 1, calls: tuple[hs.Call, ...] = ()) -> hs.CallOutcome:
    return hs.CallOutcome(
        calls=calls, turns=turns, started_at="2026-08-25T00:00:00.000Z",
        ended_at="2026-08-25T00:00:05.000Z", duration_ms=5000,
    )


def _pool(
    instances: int, *, provisioner: Any | None = None,
    reset_scripts: dict[int, list[RuntimeState]] | None = None,
    outbound: Any | None = None,
) -> tuple[hs.WorldPool, FakeProvisioner]:
    fake = provisioner or FakeProvisioner(instances, reset_scripts=reset_scripts)
    pool = hs.WorldPool(
        fake, bundle=object(), source=Path("/work/source"), bundle_dir=Path("/work/bundle"),
        work_directory=Path("/work"), instances=instances, outbound=outbound,
    )
    return pool, fake


# --- WorldPool -------------------------------------------------------------------------------


def test_lease_skips_reset_for_a_freshly_provisioned_world_but_not_the_next_lease() -> None:
    # m9: a world just handed back by `provision()` is already at the sealed baseline — the
    # first lease must not pay for a redundant reset, but a world that has already been used
    # once resets normally on its next lease.
    async def scenario() -> None:
        pool, provisioner = _pool(1)
        await pool.start()
        world_index, runtime = await pool.lease()
        assert world_index == 0
        assert runtime.state is RuntimeState.READY
        assert provisioner.reset_calls == 0
        await pool.release(world_index)
        world_index, runtime = await pool.lease()
        assert provisioner.reset_calls == 1
        await pool.close()

    asyncio.run(scenario())


def test_a_world_left_unhealthy_by_reset_is_not_handed_out() -> None:
    async def scenario() -> None:
        pool, provisioner = _pool(2, reset_scripts={0: [RuntimeState.UNHEALTHY]})
        await pool.start()
        first, _ = await pool.lease()
        assert first == 0  # freshly provisioned -- m9 skips this lease's reset
        await pool.release(0)
        world_index, _ = await pool.lease()
        assert world_index == 1  # world 0's (now real) reset hit the scripted UNHEALTHY outcome
        await pool.close()

    asyncio.run(scenario())


def test_a_freshly_provisioned_world_failing_its_health_probe_is_not_handed_out() -> None:
    # M2: `healthy()` is called unconditionally after reset — including on the m9 fast path,
    # which only skips the (expensive) reset call, never the readiness check.
    async def scenario() -> None:
        class Provisioner(FakeProvisioner):
            async def healthy(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> bool:
                async with self._serialized():
                    return runtime.world_index != 0

        pool, _ = _pool(2, provisioner=Provisioner(2))
        await pool.start()
        world_index, _ = await pool.lease()
        assert world_index == 1
        await pool.close()

    asyncio.run(scenario())


def test_an_unhealthy_world_is_reconciled_back_in_the_background() -> None:
    async def scenario() -> None:
        pool, provisioner = _pool(2, reset_scripts={0: [RuntimeState.UNHEALTHY]})
        await pool.start()
        first, _ = await pool.lease()
        assert first == 0
        await pool.release(0)
        world_index, _ = await pool.lease()
        assert world_index == 1  # world 0's reset failed and it fell out of rotation
        await asyncio.sleep(0.2)
        assert provisioner.provision_calls >= 2  # start() + the background reconcile
        # T1: the world must be genuinely leasable again, not merely still counted in pool.size.
        recovered_index, recovered_runtime = await pool.lease()
        assert recovered_index == 0
        assert recovered_runtime.state is RuntimeState.READY
        await pool.close()

    asyncio.run(scenario())


def test_lease_excluding_the_only_world_raises_rather_than_hanging() -> None:
    async def scenario() -> None:
        pool, _ = _pool(1)
        await pool.start()
        world_index, _ = await pool.lease()
        await pool.release(world_index)
        try:
            await asyncio.wait_for(pool.lease(exclude=frozenset({world_index})), timeout=1.0)
        except hs.NoWorldsAvailable:
            pass
        else:
            raise AssertionError("expected NoWorldsAvailable")
        await pool.close()

    asyncio.run(scenario())


def test_reconcile_can_drop_a_world_that_never_recovers() -> None:
    async def scenario() -> None:
        calls = {"n": 0}

        class Provisioner:
            async def provision(self, bundle, *, source, bundle_dir, work_directory, contract=None, instances=1):
                calls["n"] += 1
                if calls["n"] == 1:
                    return [_runtime(0, RuntimeState.READY), _runtime(1, RuntimeState.READY)]
                return [_runtime(1, RuntimeState.READY)]  # world 0 degraded away, every time

            async def reset(self, runtime, *, work_directory):
                runtime.state = RuntimeState.UNHEALTHY if runtime.world_index == 0 else RuntimeState.READY

            async def healthy(self, runtime, *, work_directory):
                return runtime.state is RuntimeState.READY

            async def close(self, *, work_directory):
                pass

        pool, _ = _pool(2, provisioner=Provisioner())
        await pool.start()
        first, _ = await pool.lease()
        assert first == 0  # freshly provisioned -- m9 skips this lease's reset
        await pool.release(0)
        world_index, _ = await pool.lease()
        assert world_index == 1  # world 0's (now real) reset marked it unhealthy
        await asyncio.sleep(0.2)
        assert pool.size == 1  # the reconcile's own `provision()` never brings world 0 back
        await pool.close()

    asyncio.run(scenario())


def test_concurrent_mark_unhealthy_never_calls_provision_reentrantly() -> None:
    # T7/B1: two worlds going bad in the same tick must serialize onto one provider call at a
    # time — `FakeProvisioner._serialized`'s own assertion is what actually catches a regression.
    async def scenario() -> None:
        pool, provisioner = _pool(2)
        await pool.start()
        w0, _ = await pool.lease()
        w1, _ = await pool.lease()
        await asyncio.gather(
            pool.mark_unhealthy(w0, cause="boom0"), pool.mark_unhealthy(w1, cause="boom1")
        )
        await asyncio.sleep(0.1)
        assert pool.size == 2
        world_index, runtime = await pool.lease()
        assert runtime.state is RuntimeState.READY
        await pool.close()

    asyncio.run(scenario())


def test_lease_reset_and_a_background_reconcile_never_overlap_on_the_provider() -> None:
    # TH-4/B2: the overlap that actually matters is a lease()'s reset() running concurrently with
    # a DIFFERENT world's reconcile provision() -- the old coalescer test never drove two
    # DIFFERENT provider calls at once; `FakeProvisioner._serialized`'s reentrancy assertion is
    # what would catch a `_provider_lock` regression, so this drives it for real.
    async def scenario() -> None:
        pool, provisioner = _pool(2)
        await pool.start()

        # Use up world 1's "fresh" fast path so its NEXT lease pays for a real reset() call --
        # otherwise it would never touch the provider at all and the overlap wouldn't be real.
        w1, _ = await pool.lease(exclude=frozenset({0}))
        assert w1 == 1
        await pool.release(1)

        w0, _ = await pool.lease(exclude=frozenset({1}))
        assert w0 == 0

        results = await asyncio.gather(
            pool.mark_unhealthy(0, cause="boom"),  # schedules a background reconcile provision()
            pool.lease(exclude=frozenset({0})),  # world 1's reset()+healthy() run concurrently
        )
        leased = results[1]
        assert leased is not None and leased[0] == 1
        await asyncio.sleep(0.1)  # let the reconcile finish
        assert provisioner.provision_calls >= 2
        await pool.close()

    asyncio.run(scenario())


def test_close_waits_for_an_in_flight_reconcile_before_closing_the_provider() -> None:
    # TH-3/R4: `ProcessRuntimeProvider.provision` is `asyncio.to_thread` — cancelling the
    # awaiting coroutine does NOT stop the underlying thread. The fake here dispatches through
    # `asyncio.to_thread` too (a `threading.Event` gates when the thread-backed work actually
    # finishes), so this can only pass if `close()` genuinely waits for the real work instead of
    # racing a hard-clean against it.
    async def scenario() -> None:
        events: list[str] = []
        release_provision = threading.Event()

        def provision_sync(instances: int) -> list[EnvironmentRuntime]:
            events.append("provision-start")
            release_provision.wait(timeout=5.0)
            events.append("provision-end")
            return [_runtime(i, RuntimeState.READY) for i in range(instances)]

        class Provisioner:
            async def provision(self, bundle, *, source, bundle_dir, work_directory, contract=None, instances=1):
                return await asyncio.to_thread(provision_sync, instances)

            async def reset(self, runtime, *, work_directory):
                pass

            async def healthy(self, runtime, *, work_directory):
                return runtime.state is RuntimeState.READY

            async def close(self, *, work_directory):
                events.append("close")

        pool, _ = _pool(1, provisioner=Provisioner())
        release_provision.set()
        await pool.start()
        release_provision.clear()
        events.clear()  # drop start()'s own provision-start/-end

        await pool.mark_unhealthy(0, cause="boom")  # schedules a reconcile mid-flight
        await asyncio.sleep(0.05)  # let the reconcile's provision() actually begin on its thread
        assert events == ["provision-start"]

        close_task = asyncio.create_task(pool.close())
        await asyncio.sleep(0.05)
        assert events == ["provision-start"], "close() ran the provider's own close() too early"

        release_provision.set()  # let the thread-backed provision() finish on its own
        await asyncio.wait_for(close_task, timeout=5.0)
        assert events == ["provision-start", "provision-end", "close"]

    asyncio.run(scenario())


def test_mark_unhealthy_and_lease_after_close_are_blocked() -> None:
    # R5: `close()` latches -- neither a late `mark_unhealthy()` (e.g. a scenario's `finally`
    # racing a SIGTERM-triggered close()) nor a fresh `lease()` may touch the provider again once
    # the pool has been closed, and a second `close()` is a no-op.
    async def scenario() -> None:
        pool, provisioner = _pool(1)
        await pool.start()
        world_index, _ = await pool.lease()
        await pool.close()
        assert provisioner.provision_calls == 1  # start()'s call only
        assert provisioner.closed is True

        await pool.mark_unhealthy(world_index, cause="late failure")
        await asyncio.sleep(0.05)
        assert provisioner.provision_calls == 1  # no reconcile spawned post-close

        try:
            await asyncio.wait_for(pool.lease(), timeout=1.0)
        except hs.NoWorldsAvailable as exc:
            assert exc.reason == "closed"
        else:
            raise AssertionError("expected NoWorldsAvailable(reason='closed')")

        await pool.close()  # second close() is a no-op
        assert provisioner.provision_calls == 1

    asyncio.run(scenario())


def test_start_degrades_when_provision_returns_fewer_worlds_than_instances() -> None:
    # R2: `provision()` legitimately returns fewer worlds than requested (conformance-gate
    # failure, `fixed_port`) — spine v1.12 §4: "Fail -> effective parallelism 1 ...
    # Loud, never silent," not a `RuntimeError` from this pool.
    async def scenario() -> None:
        class Provisioner:
            async def provision(self, bundle, *, source, bundle_dir, work_directory, contract=None, instances=1):
                return [_runtime(0)]  # only 1 of the 3 requested

            async def reset(self, runtime, *, work_directory):
                pass

            async def healthy(self, runtime, *, work_directory):
                return True

            async def close(self, *, work_directory):
                pass

        pool, _ = _pool(3, provisioner=Provisioner())
        runtimes = await pool.start()
        assert len(runtimes) == 1
        assert pool.effective_size == 1
        world_index, _ = await pool.lease()
        assert world_index == 0
        await pool.close()

    asyncio.run(scenario())


def test_start_rejects_a_genuinely_malformed_provision_result() -> None:
    # R2: the degrade allowance is not a blanket exemption — zero worlds and a non-contiguous
    # index set are still rejected as malformed.
    class ZeroWorldsProvisioner:
        async def provision(self, bundle, *, source, bundle_dir, work_directory, contract=None, instances=1):
            return []

        async def reset(self, runtime, *, work_directory):
            pass

        async def healthy(self, runtime, *, work_directory):
            return True

        async def close(self, *, work_directory):
            pass

    class GapProvisioner:
        async def provision(self, bundle, *, source, bundle_dir, work_directory, contract=None, instances=1):
            return [_runtime(0), _runtime(2)]  # world_index 1 missing -- not contiguous

        async def reset(self, runtime, *, work_directory):
            pass

        async def healthy(self, runtime, *, work_directory):
            return True

        async def close(self, *, work_directory):
            pass

    async def zero_worlds() -> None:
        pool, _ = _pool(2, provisioner=ZeroWorldsProvisioner())
        try:
            await pool.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError for zero worlds")

    async def gap() -> None:
        pool, _ = _pool(3, provisioner=GapProvisioner())
        try:
            await pool.start()
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError for a non-contiguous index set")

    asyncio.run(zero_worlds())
    asyncio.run(gap())


def test_world_unhealthy_emitted_exactly_once_per_demotion_path() -> None:
    # R6: every demotion path now goes through `WorldPool.mark_unhealthy()`, the sole emitter —
    # parametrized over three distinct triggers (the fourth, the `_Retry(mark_unhealthy=True)`
    # path, is already covered by `test_world_unavailable_retries_once_on_a_fresh_world_and_recovers`
    # and `test_world_unhealthy_cause_is_truncated_and_redacted`).
    async def run_scheduler_case(setup_fn: Any, sub_goals: Any) -> list[dict[str, Any]]:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(
            pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1,
        )
        scenarios = [FakeScenario("s1", "id-1", setup_fn=setup_fn, sub_goals=sub_goals)]
        await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        await pool.close()
        return [kwargs for event, kwargs in outbound.events if event == "world_unhealthy"]

    async def m13_discard_branch() -> None:
        # the M13 discard branch: a plain `setup_crashed` fault.
        def boom(world: Any) -> None:
            raise ValueError("scenario code bug")

        events = await run_scheduler_case(boom, [FakeSubGoal("g", lambda w, c: None)])
        assert len(events) == 1
        assert events[0]["world_index"] == 0

    async def b3_finally_branch() -> None:
        # the B3 `finally` branch: something blows past every handled path in `_execute`.
        class BrokenSubGoals:
            def __bool__(self) -> bool:
                raise RuntimeError("sub_goals blew up")

        events = await run_scheduler_case(lambda world: None, BrokenSubGoals())
        assert len(events) == 1
        assert events[0]["world_index"] == 0

    async def lease_reset_failure_demotion() -> None:
        # `WorldPool.lease()`'s own reset/health-probe demotion — pool-level, no scheduler.
        outbound = FakeOutbound()
        pool, _ = _pool(2, reset_scripts={0: [RuntimeState.UNHEALTHY]}, outbound=outbound)
        await pool.start()
        first, _ = await pool.lease()
        assert first == 0
        await pool.release(0)
        await pool.lease()  # world 0's real reset hits the scripted UNHEALTHY outcome
        events = [kwargs for event, kwargs in outbound.events if event == "world_unhealthy"]
        assert len(events) == 1
        assert events[0]["world_index"] == 0
        await pool.close()

    asyncio.run(m13_discard_branch())
    asyncio.run(b3_finally_branch())
    asyncio.run(lease_reset_failure_demotion())


def test_bundle_dir_is_threaded_through_to_every_provision_call() -> None:
    # T7/M1: `bundle_dir` must reach both `start()`'s and `_reconcile()`'s `provision()` calls.
    async def scenario() -> None:
        seen: list[Path] = []

        class Provisioner(FakeProvisioner):
            async def provision(self, bundle, *, source, bundle_dir, work_directory, contract=None, instances=1):
                seen.append(bundle_dir)
                return await super().provision(
                    bundle, source=source, bundle_dir=bundle_dir, work_directory=work_directory,
                    contract=contract, instances=instances,
                )

        bundle_dir = Path("/work/bundle-xyz")
        pool = hs.WorldPool(
            Provisioner(1), bundle=object(), source=Path("/work/source"), bundle_dir=bundle_dir,
            work_directory=Path("/work"), instances=1,
        )
        await pool.start()
        world_index, _ = await pool.lease()
        await pool.mark_unhealthy(world_index, cause="force a reconcile")
        await asyncio.sleep(0.1)
        assert seen and all(path == bundle_dir for path in seen)
        await pool.close()

    asyncio.run(scenario())


# --- HostedScheduler ---------------------------------------------------------------------------


def test_two_scenarios_pass_over_two_worlds() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, provisioner = _pool(2, outbound=outbound)
        await pool.start()
        call = hs.Call(name="book", arguments={}, ok=True)
        runner = FakeCallRunner({"s1": _call_outcome(calls=(call,)), "s2": _call_outcome(calls=(call,))})
        scheduler = hs.HostedScheduler(
            pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=100,
        )
        scenarios = [
            FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", lambda w, c: None)]),
            FakeScenario("s2", "id-2", sub_goals=[FakeSubGoal("goal", lambda w, c: None)]),
        ]
        result = await scheduler.run(scenarios)
        assert result.aborted is None
        assert {r.status for r in result.receipts} == {"passed"}
        assert all(r.scenario_attempt == 1 for r in result.receipts)
        assert {e for e, _ in outbound.events} == {"scenario_started"}
        assert provisioner.provision_calls == 1
        await pool.close()

    asyncio.run(scenario())


def test_a_not_held_check_is_failed_not_errored() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", lambda w, c: "the combo was never ordered")])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "failed"
        assert receipt.sub_goals[0] == hs.SubGoalResult(name="goal", held=False, reason="the combo was never ordered", judged=False)
        assert receipt.failure is None
        await pool.close()

    asyncio.run(scenario())


def test_ready_not_ready_errors_without_retrying() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        runner = FakeCallRunner({})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", ready_fn=lambda w: "precondition missing", sub_goals=[FakeSubGoal("goal", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure == hs.ReceiptFailure(domain="simulator", stage="running", code="ready_not_ready", message="precondition missing")
        assert receipt.scenario_attempt == 1
        assert receipt.sub_goals[0].held is None
        assert runner.calls == []  # never reached the call step
        # M13: `ready_not_ready` is a clean verdict, not an exception -- the world is released,
        # not discarded, so it is immediately leasable again.
        world_index, _ = await pool.lease()
        assert world_index == 0
        await pool.close()

    asyncio.run(scenario())


def test_setup_crash_errors_without_retrying() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()

        def boom(world: Any) -> None:
            raise ValueError("scenario code bug")

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=FakeCallRunner({}), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", setup_fn=boom, sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure.code == "setup_crashed"
        assert receipt.scenario_attempt == 1
        # M13: an exception outcome discards the world (mark_unhealthy), never release() -- it is
        # down, not immediately available, until the background reconcile recovers it.
        assert 0 in pool._down
        await pool.close()

    asyncio.run(scenario())


def test_world_usage_misuse_in_ready_maps_to_world_usage() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()

        def misuse(world: Any) -> None:
            raise WorldUsageError("cannot invent a table")

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=FakeCallRunner({}), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", ready_fn=misuse, sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.failure.code == "world_usage"
        assert receipt.failure.domain == "simulator"
        assert 0 in pool._down  # M13: discarded, not released
        await pool.close()

    asyncio.run(scenario())


def test_state_too_large_from_check_errors_without_retrying() -> None:
    # T7: `state_too_large` was never exercised by the original suite.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})

        def blow_up(world: Any, calls: Any) -> None:
            raise WorldStateTooLarge("table 'events' exceeds the baseline cap")

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", blow_up)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure.code == "state_too_large"
        assert receipt.failure.domain == "simulator"
        assert receipt.scenario_attempt == 1
        await pool.close()

    asyncio.run(scenario())


def test_a_phase_over_its_budget_times_out() -> None:
    # T4: real scenario code is synchronous — a `time.sleep` body is what actually exercises
    # B4's dedicated-executor dispatch. The old `async def` + `asyncio.sleep` version would have
    # passed even with B4 unfixed (`wait_for` can always interrupt a coroutine's own suspension
    # point), so it never caught the bug.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        original = hs.SETUP_TIMEOUT_SECONDS
        hs.SETUP_TIMEOUT_SECONDS = 0.05
        try:
            def slow(world: Any) -> None:
                time.sleep(1.0)

            scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=FakeCallRunner({}), outbound=outbound, job_seed=1)
            scenarios = [FakeScenario("s1", "id-1", setup_fn=slow, sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
            result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
            assert result.receipts[0].failure.code == "setup_timeout"
        finally:
            hs.SETUP_TIMEOUT_SECONDS = original
        await pool.close()

    asyncio.run(scenario())


def test_an_async_phase_over_its_budget_still_times_out() -> None:
    # B4: "keep the awaitable branch" — async scenario code (reached via a sync wrapper, as
    # `FakeScenario.setup` always is) must still be interruptible on budget.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        original = hs.SETUP_TIMEOUT_SECONDS
        hs.SETUP_TIMEOUT_SECONDS = 0.05
        try:
            async def slow(world: Any) -> None:
                await asyncio.sleep(1.0)

            scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=FakeCallRunner({}), outbound=outbound, job_seed=1)
            scenarios = [FakeScenario("s1", "id-1", setup_fn=slow, sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
            result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
            assert result.receipts[0].failure.code == "setup_timeout"
        finally:
            hs.SETUP_TIMEOUT_SECONDS = original
        await pool.close()

    asyncio.run(scenario())


def test_concurrent_worlds_make_progress_while_one_is_blocked_in_sync_code() -> None:
    # B4/TH-1: BOTH worlds' setup sleeps -- serialized execution would take >=0.4s, concurrent
    # execution ~0.2s. The old version slept only world 0, so serialized and parallel wall times
    # were indistinguishable (both ~0.2s) and the elapsed-time assertion alone could not catch a
    # regression to serial dispatch; `started_order` now pins the actual interleaving too.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()
        started_order: list[str] = []

        def both_slow(world: Any) -> None:
            started_order.append(f"start-{world.world_index}")
            time.sleep(0.2)
            started_order.append(f"end-{world.world_index}")

        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),)), "s2": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [
            FakeScenario("s1", "id-1", setup_fn=both_slow, sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
            FakeScenario("s2", "id-2", setup_fn=both_slow, sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
        ]
        t0 = asyncio.get_running_loop().time()
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        elapsed = asyncio.get_running_loop().time() - t0
        assert all(r.status == "passed" for r in result.receipts)
        assert elapsed < 0.3  # serialized would be >=0.4s
        # Both worlds must have STARTED before either one ENDED -- genuine concurrency, not just
        # a fast total.
        assert set(started_order[:2]) == {"start-0", "start-1"}, started_order
        await pool.close()

    asyncio.run(scenario())


def test_a_leaked_phase_thread_does_not_starve_a_sibling_world_and_close_still_completes() -> None:
    # R1 (highest-ranked missing test): a phase whose thread never returns must not stop the
    # NEXT scenario's phase from running, and `pool.close()` must still complete promptly. With
    # the shared default executor this used to fail once enough threads leaked; with a dedicated
    # `ThreadPoolExecutor` (`shutdown(wait=False, cancel_futures=True)` on `run()` exit) both hold.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()
        original = hs.SETUP_TIMEOUT_SECONDS
        hs.SETUP_TIMEOUT_SECONDS = 0.1
        try:
            def maybe_runaway(world: Any) -> None:
                if world.world_index == 0:
                    time.sleep(5.0)  # abandoned -- this thread never returns

            runner = FakeCallRunner({"s2": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
            scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
            scenarios = [
                FakeScenario("s1", "id-1", setup_fn=maybe_runaway, sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
                FakeScenario("s2", "id-2", setup_fn=maybe_runaway, sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
            ]
            result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
            assert result.receipts[0].failure is not None and result.receipts[0].failure.code == "setup_timeout"
            assert result.receipts[1].status == "passed"  # world 1's phase ran despite world 0's leak
        finally:
            hs.SETUP_TIMEOUT_SECONDS = original
        await asyncio.wait_for(pool.close(), timeout=2.0)  # must not hang behind the leaked thread

    asyncio.run(scenario())


def test_check_broken_leaves_later_subgoals_unjudged() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[
            FakeSubGoal("goal1", lambda w, c: None),
            FakeSubGoal("goal2", lambda w, c: 42),  # wrong return type -> broken
            FakeSubGoal("goal3", lambda w, c: None),
        ])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure.code == "check_broken"
        assert [g.held for g in receipt.sub_goals] == [True, None, None]
        await pool.close()

    asyncio.run(scenario())


def test_zero_declared_sub_goals_is_check_broken_not_a_vacuous_pass() -> None:
    # m7: `all(())` is vacuously True — must not read as a silent pass.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure.code == "check_broken"
        assert receipt.sub_goals == ()
        await pool.close()

    asyncio.run(scenario())


def test_world_unavailable_retries_once_on_a_fresh_world_and_recovers() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, provisioner = _pool(2, outbound=outbound)
        await pool.start()
        attempts = {"n": 0}

        def check(world: Any, calls: Any) -> None:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise WorldUnavailable("world 0 lost its schema")

        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", check)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "passed"
        assert receipt.scenario_attempt == 2
        kinds = [event for event, _ in outbound.events]
        assert kinds.count("scenario_started") == 2
        assert kinds.count("scenario_retried") == 1
        assert kinds.count("world_unhealthy") == 1
        await pool.close()

    asyncio.run(scenario())


def test_world_unhealthy_cause_is_truncated_and_redacted() -> None:
    # T7/M7: `cause` is capped at 200 chars and must never carry endpoint credentials.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()
        leaky_message = "connection failed: postgresql://harness:s3cr3t@localhost:14000/w0 " + ("x" * 300)

        def blow_up(world: Any, calls: Any) -> None:
            raise WorldUnavailable(leaky_message)

        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", blow_up)])]
        await scheduler.run(scenarios)
        causes = [kwargs["cause"] for event, kwargs in outbound.events if event == "world_unhealthy"]
        assert causes
        cause = causes[0]
        assert len(cause) <= 200
        assert "s3cr3t" not in cause
        assert "://***@" in cause
        await pool.close()

    asyncio.run(scenario())


def test_world_unavailable_twice_gives_up_after_the_one_retry() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()

        def always_fails(world: Any, calls: Any) -> None:
            raise WorldUnavailable("always broken")

        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", always_fails)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.scenario_attempt == 2
        assert receipt.failure.code == "world_unavailable"
        await pool.close()

    asyncio.run(scenario())


def test_cancel_between_attempt_1_and_attempt_2_reports_errored_not_skipped() -> None:
    # R3: attempt 1 ran and produced a real errored outcome; a cancel/abort landing on the
    # retry-lease path — the "abandoned while queued for the retry world" site — must not lose
    # it to skipped-synthesis. outbound-channels.md defines `skipped` as "never ran," and this
    # scenario manifestly did.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()
        cancel_flag = {"v": False}

        def check(world: Any, calls: Any) -> None:
            raise WorldUnavailable("world 0 lost its schema")

        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(
            pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound,
            job_seed=1, cancel_requested=lambda: cancel_flag["v"],
        )

        real_lease = pool.lease
        lease_calls = {"n": 0}

        async def flaky_lease(*, exclude=frozenset(), abandon=None):
            lease_calls["n"] += 1
            if lease_calls["n"] == 2:  # the retry-lease call, right after attempt 1 failed
                cancel_flag["v"] = True
            return await real_lease(exclude=exclude, abandon=abandon)

        pool.lease = flaky_lease  # type: ignore[method-assign]

        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", check)])]
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure is not None and receipt.failure.code == "world_unavailable"
        assert receipt.scenario_attempt == 1
        assert receipt.world_index == 0
        await pool.close()

    asyncio.run(scenario())


def test_cancel_during_the_retry_leases_own_health_probe_reports_errored_not_skipped() -> None:
    # R3, the SECOND post-attempt-1 `return None` site: a cancel/abort can also land AFTER the
    # retry-lease has already granted a world (during ITS OWN reset/healthy await), rather than
    # while queued for one -- a distinct code path from the test above, reached at the top of the
    # attempt-2 recursive call instead of inside the retry-lease call itself.
    async def scenario() -> None:
        cancel_flag = {"v": False}

        class Provisioner(FakeProvisioner):
            async def healthy(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> bool:
                if runtime.world_index == 1:
                    cancel_flag["v"] = True
                async with self._serialized():
                    return runtime.state is RuntimeState.READY

        outbound = FakeOutbound()
        pool, _ = _pool(2, provisioner=Provisioner(2), outbound=outbound)
        await pool.start()

        def check(world: Any, calls: Any) -> None:
            raise WorldUnavailable("world 0 lost its schema")

        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(
            pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound,
            job_seed=1, cancel_requested=lambda: cancel_flag["v"],
        )
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", check)])]
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure is not None and receipt.failure.code == "world_unavailable"
        assert receipt.scenario_attempt == 1
        assert receipt.world_index == 0
        await pool.close()

    asyncio.run(scenario())


def test_evidence_missing_retries_without_marking_the_world_unhealthy() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()
        seen = {"n": 0}

        class Runner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                seen["n"] += 1
                if seen["n"] == 1:
                    return _call_outcome(turns=1, calls=())
                return _call_outcome(turns=1, calls=(hs.Call(name="x", arguments={}),))

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=Runner(), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "passed"
        assert receipt.scenario_attempt == 2
        kinds = [event for event, _ in outbound.events]
        assert kinds.count("world_unhealthy") == 0  # not a world-health problem
        assert kinds.count("scenario_retried") == 1
        await pool.close()

    asyncio.run(scenario())


def test_evidence_missing_twice_errors() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()

        class Runner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                return _call_outcome(turns=1, calls=())

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=Runner(), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure.code == "evidence_missing"
        assert receipt.scenario_attempt == 2
        assert receipt.call is not None  # the call step DID run; only evidence capture failed
        await pool.close()

    asyncio.run(scenario())


def test_zero_turns_and_zero_calls_is_still_evidence_missing() -> None:
    # M10: unconditioned on `turns` — a 0-turn call is an unobserved agent, same as any other
    # empty-calls outcome; `evidence_missing` must fire regardless.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()

        class Runner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                return _call_outcome(turns=0, calls=())

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=Runner(), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.failure.code == "evidence_missing"
        assert receipt.scenario_attempt == 2
        await pool.close()

    asyncio.run(scenario())


def test_call_aborted_with_partial_evidence_is_reported_not_null() -> None:
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)  # `call_failed` is infrastructure-domain (retryable) — needs a spare world
        await pool.start()

        class Runner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                raise hs.CallAborted(
                    "livekit room dropped",
                    partial=hs.CallOutcome(
                        calls=(), turns=0, started_at="2026-08-25T00:00:00.000Z",
                        ended_at="2026-08-25T00:00:01.000Z", duration_ms=1000,
                    ),
                )

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=Runner(), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure.code == "call_failed"
        assert receipt.call is not None and receipt.call.duration_ms == 1000
        assert receipt.scenario_attempt == 2
        await pool.close()

    asyncio.run(scenario())


def test_call_aborted_with_no_partial_evidence_still_retries() -> None:
    # T6/M3: `retry=call is not None` used to suppress the retry whenever `partial` was `None` —
    # world-handle-interface.md v3.3 pins `call_failed` as unconditionally retried once.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()

        class Runner:
            def __init__(self) -> None:
                self.attempts = 0

            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                self.attempts += 1
                if self.attempts == 1:
                    raise hs.CallAborted("livekit room never opened", partial=None)
                return _call_outcome(calls=(hs.Call(name="x", arguments={}),))

        runner = Runner()
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.status == "passed"
        assert receipt.scenario_attempt == 2
        assert runner.attempts == 2
        await pool.close()

    asyncio.run(scenario())


def test_call_runner_raising_a_bare_exception_maps_to_call_failed() -> None:
    # T7/B3: a crash from the call runner that is not a `CallAborted` it deliberately raised is
    # the same world-handle-interface.md v3.3 row — "the simulated-call machinery crashed".
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()
        runner = FakeCallRunner({"s1": ConnectionError("livekit socket reset")})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        receipt = result.receipts[0]
        assert receipt.failure.code == "call_failed"
        assert receipt.failure.domain == "infrastructure"
        assert receipt.scenario_attempt == 2
        assert receipt.call is None
        await pool.close()

    asyncio.run(scenario())


def test_a_bug_in_the_driver_itself_becomes_a_driver_crashed_receipt_without_killing_the_run() -> None:
    # T7/B3/R7: a crash that blows past every handled path in `_execute` (not the agent, not a
    # check, not the call) must land as `driver_crashed` and must not suppress the OTHER
    # scenario's receipt. R7: it must also report the REAL world_index the crash happened on
    # (world 0, genuinely leased) rather than always `None`.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()

        class BrokenSubGoals:
            def __bool__(self) -> bool:
                raise RuntimeError("sub_goals blew up")

        runner = FakeCallRunner({
            "s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),)),
            "s2": _call_outcome(calls=(hs.Call(name="x", arguments={}),)),
        })
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        broken = FakeScenario("s1", "id-1")
        broken.sub_goals = BrokenSubGoals()  # type: ignore[assignment]
        scenarios = [broken, FakeScenario("s2", "id-2", sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        assert result.receipts[0].status == "errored"
        assert result.receipts[0].failure.code == "driver_crashed"
        assert result.receipts[0].failure.domain == "simulator"
        assert result.receipts[0].world_index == 0  # R7: the real world, not always None
        assert result.receipts[0].scenario_attempt == 1
        assert result.receipts[1].status == "passed"  # the crash did not suppress this receipt
        assert len(outbound.receipts) == 2
        await pool.close()

    asyncio.run(scenario())


def test_driver_crashed_reports_unjudged_sub_goals_when_they_are_readable() -> None:
    # R7: `driver_crashed` must carry every declared sub-goal (`held: null`) when `sub_goals`
    # itself is perfectly readable — only a crash reading `sub_goals` falls back to `()`.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()

        class BrokenReadOnly:
            world_index = 0

            def read_only(self) -> Any:
                raise RuntimeError("world.read_only() blew up")

        class Factory:
            async def create(self, runtime: EnvironmentRuntime, *, rng: random.Random) -> Any:
                return BrokenReadOnly()

        scheduler = hs.HostedScheduler(pool=pool, world_factory=Factory(), call_runner=FakeCallRunner({}), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("g1", lambda w, c: None), FakeSubGoal("g2", lambda w, c: None)])]
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure is not None and receipt.failure.code == "driver_crashed"
        assert receipt.world_index == 0
        assert receipt.scenario_attempt == 1
        assert [g.name for g in receipt.sub_goals] == ["g1", "g2"]
        assert all(g.held is None and g.reason is None for g in receipt.sub_goals)
        await pool.close()

    asyncio.run(scenario())


def test_exactly_one_receipt_per_scenario_key_even_when_one_scenario_crashes() -> None:
    # T7/B3: `gather(return_exceptions=True)` + the try/finally around the leased region must
    # never produce zero or duplicate receipts for any scenario.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()

        def boom(world: Any) -> None:
            raise ValueError("scenario code bug")

        runner = FakeCallRunner({"s2": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [
            FakeScenario("s1", "id-1", setup_fn=boom, sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
            FakeScenario("s2", "id-2", sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
        ]
        result = await scheduler.run(scenarios)
        assert [r.status for r in result.receipts] == ["errored", "passed"]
        keys = [r.scenario_key for r in outbound.receipts]
        assert sorted(keys) == ["s1", "s2"]
        assert len(keys) == len(set(keys))
        await pool.close()

    asyncio.run(scenario())


def test_outbound_failures_never_kill_the_run_or_change_the_receipt() -> None:
    # B3: a completely broken OutboundPort is best-effort telemetry — never fatal, never
    # receipt-affecting.
    async def scenario() -> None:
        pool, _ = _pool(1)
        await pool.start()
        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        outbound = FailingOutbound(fail_on={"scenario_started", "receipt"})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=1)
        scenarios = [FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("goal", lambda w, c: None)])]
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        assert result.receipts[0].status == "passed"
        assert result.aborted is None
        await pool.close()

    asyncio.run(scenario())


def test_cancel_after_the_first_scenario_skips_the_rest() -> None:
    # T3/TH-2: the original test's `cancel_requested=lambda: True` was true before `run()` was
    # even called, so nothing ever launched and the "in-flight scenario finishes" behavior was
    # never exercised. Here the flag flips mid-run, from inside the first scenario's own call
    # step. TH-2: re-pins the exact six-field `skipped` receipt body outbound-channels.md calls
    # "exact" — the assertion that would have caught R3.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        cancel_flag = {"v": False}

        class Runner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                cancel_flag["v"] = True
                return _call_outcome(calls=(hs.Call(name="x", arguments={}),))

        scheduler = hs.HostedScheduler(
            pool=pool, world_factory=FakeWorldFactory(), call_runner=Runner(), outbound=outbound,
            job_seed=1, cancel_requested=lambda: cancel_flag["v"],
        )
        scenarios = [FakeScenario(f"s{i}", f"id-{i}", sub_goals=[FakeSubGoal("g", lambda w, c: None)]) for i in range(3)]
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        assert result.aborted is None  # a cancel is not a job-level failure
        assert result.receipts[0].status == "passed"  # the in-flight scenario finished
        assert result.receipts[1] == hs.ResultReceipt(
            scenario_key="s1", scenario_id="id-1", scenario_attempt=1, world_index=None,
            status="skipped", sub_goals=(), evaluations=(), call=None, failure=None,
        )
        assert result.receipts[2] == hs.ResultReceipt(
            scenario_key="s2", scenario_id="id-2", scenario_attempt=1, world_index=None,
            status="skipped", sub_goals=(), evaluations=(), call=None, failure=None,
        )
        await pool.close()

    asyncio.run(scenario())


def test_zero_ready_worlds_aborts_the_run_and_skips_the_rest() -> None:
    # T2: the original test raced two workers that both hit `NoWorldsAvailable` independently at
    # t~=0 (W=1, both past the pre-check before either leased) — "the rest gets skipped because
    # the job aborted" was never actually exercised. Here W=1, scenario 1 genuinely runs (and
    # fails on its only world), the provisioner then goes permanently down, and scenarios 2/3 are
    # asserted to have never reached the call step at all.
    async def scenario() -> None:
        provisioner = FakeProvisioner(1)
        calls = {"n": 0}
        real_provision = provisioner.provision

        async def flaky_provision(bundle, *, source, bundle_dir, work_directory, contract=None, instances=1):
            calls["n"] += 1
            if calls["n"] == 1:
                return await real_provision(
                    bundle, source=source, bundle_dir=bundle_dir, work_directory=work_directory,
                    contract=contract, instances=instances,
                )
            raise RuntimeError("provisioner is down")

        provisioner.provision = flaky_provision  # type: ignore[method-assign]
        outbound = FakeOutbound()
        pool, _ = _pool(1, provisioner=provisioner, outbound=outbound)
        await pool.start()

        class Runner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                if scenario.scenario_key == "s1":
                    raise WorldUnavailable("world 0's schema is gone")
                raise AssertionError(f"{scenario.scenario_key} should never have reached the call step")

        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=Runner(), outbound=outbound, job_seed=1)
        scenarios = [
            FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
            FakeScenario("s2", "id-2", sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
            FakeScenario("s3", "id-3", sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
        ]
        result = await asyncio.wait_for(scheduler.run(scenarios), timeout=5.0)
        assert result.aborted is not None
        assert result.aborted.domain == "infrastructure"
        assert result.aborted.code == "world_pool_exhausted"
        statuses = {r.scenario_key: r.status for r in result.receipts}
        assert statuses["s1"] == "errored"  # M8: ran and failed -- must not read as "never ran"
        assert statuses["s2"] == "skipped"
        assert statuses["s3"] == "skipped"
        started = [event for event, _ in outbound.events if event == "scenario_started"]
        assert len(started) == 1  # s2/s3 were genuinely never launched

    asyncio.run(scenario())


def test_pool_size_caps_concurrent_scenario_execution() -> None:
    async def scenario() -> None:
        pool, _ = _pool(1)
        await pool.start()
        concurrency = {"now": 0, "max": 0}

        class Runner:
            async def run(self, scenario: FakeScenario, runtime: EnvironmentRuntime) -> hs.CallOutcome:
                concurrency["now"] += 1
                concurrency["max"] = max(concurrency["max"], concurrency["now"])
                await asyncio.sleep(0.02)
                concurrency["now"] -= 1
                return _call_outcome(calls=(hs.Call(name="x", arguments={}),))

        outbound = FakeOutbound()
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=Runner(), outbound=outbound, job_seed=1)
        scenarios = [FakeScenario(f"s{i}", f"id-{i}", sub_goals=[FakeSubGoal("goal", lambda w, c: None)]) for i in range(4)]
        result = await scheduler.run(scenarios)
        assert concurrency["max"] == 1
        assert all(r.status == "passed" for r in result.receipts)
        await pool.close()

    asyncio.run(scenario())


def test_scenario_seed_is_job_seed_plus_index() -> None:
    # T5: one scenario at index 0 cannot distinguish `+index` from `+0` — two scenarios can.
    async def scenario() -> None:
        pool, _ = _pool(1)
        await pool.start()
        seen_first_draw: dict[str, int] = {}

        def make_setup(key: str):
            def setup(world: Any) -> None:
                seen_first_draw[key] = world.rng.randint(0, 10**9)
            return setup

        outbound = FakeOutbound()
        runner = FakeCallRunner({
            "s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),)),
            "s2": _call_outcome(calls=(hs.Call(name="x", arguments={}),)),
        })
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=777)
        scenarios = [
            FakeScenario("s1", "id-1", setup_fn=make_setup("s1"), sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
            FakeScenario("s2", "id-2", setup_fn=make_setup("s2"), sub_goals=[FakeSubGoal("g", lambda w, c: None)]),
        ]
        await scheduler.run(scenarios)
        assert seen_first_draw["s1"] == random.Random(777 + 0).randint(0, 10**9)
        assert seen_first_draw["s2"] == random.Random(777 + 1).randint(0, 10**9)
        await pool.close()

    asyncio.run(scenario())


def test_a_retry_reseeds_the_rng_identically() -> None:
    # world-handle-interface.md Determinism: "a retry re-seeds identically" — attempt 2 must draw
    # the SAME first value as attempt 1 did (both `Random(job.seed + scenario_index)`), not
    # continue attempt 1's stream and not use a different seed.
    async def scenario() -> None:
        outbound = FakeOutbound()
        pool, _ = _pool(2, outbound=outbound)
        await pool.start()
        draws: list[int] = []
        attempts = {"n": 0}

        def setup(world: Any) -> None:
            draws.append(world.rng.randint(0, 10**9))
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise WorldUnavailable("world 0 lost its schema")

        runner = FakeCallRunner({"s1": _call_outcome(calls=(hs.Call(name="x", arguments={}),))})
        scheduler = hs.HostedScheduler(pool=pool, world_factory=FakeWorldFactory(), call_runner=runner, outbound=outbound, job_seed=555)
        scenarios = [FakeScenario("s1", "id-1", setup_fn=setup, sub_goals=[FakeSubGoal("g", lambda w, c: None)])]
        result = await scheduler.run(scenarios)
        assert result.receipts[0].status == "passed"
        assert result.receipts[0].scenario_attempt == 2
        assert len(draws) == 2
        expected = random.Random(555 + 0).randint(0, 10**9)
        assert draws[0] == expected
        assert draws[1] == expected
        await pool.close()

    asyncio.run(scenario())
