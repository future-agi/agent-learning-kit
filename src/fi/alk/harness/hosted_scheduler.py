"""World pool + scenario loop — `hosted-execution-seams.md` v1.12 §4/§5, `world-handle-interface.md`
v3.4, `outbound-channels.md` v1.3.

Owns: leasing/releasing the W worlds `process_runtime.ProcessRuntimeProvider.provision()` hands
back, resetting a world to pristine before each scenario (spine §4.2), running one scenario's
`setup`/`ready`/checks against the world handle (the return-convention + errored-receipt table in
`world-handle-interface.md`), the fixed one-retry-on-a-fresh-world rule (spine §5 step 4), and
synthesizing a complete receipt ledger (one per scenario, `skipped` for anything never attempted).

Decoupling, deliberate:
- `process_runtime.py` is the real, settled provisioner — imported directly (`EnvironmentRuntime`,
  `RuntimeState`). `WorldProvisioner` below is a structural `Protocol` matching
  `ProcessRuntimeProvider`'s actual async shape so tests can inject a fake without touching a real
  filesystem/subprocess tree.
- `outbound.py` is being written in parallel and its surface is not pinned yet, so nothing here
  imports it. `OutboundPort` is this module's own minimal sink for the events/receipts it
  produces, typed against `outbound-channels.md`'s closed vocabulary; whoever wires the real
  client adapts to it.
- The Scenario Generation Contract (Karthik, in review) is not available here either, so `Scenario`
  is this module's own minimal Protocol for what the loop needs: a key/id pair, `setup`/`ready`,
  and named sub-goal checks. Same for the simulated "call" itself (a different track's seam) —
  `CallRunner` is injected.
- Secrets and the cancel signal are entrypoint-owned (P10); `cancel_requested` is an injected
  zero-argument callable.
"""

from __future__ import annotations

import asyncio
import inspect
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, Sequence

from .job import FailureDomain, HarnessStage
from .process_runtime import EnvironmentRuntime, RuntimeState
from .world.errors import (
    WorldError,
    WorldQueryRejected,
    WorldReadOnly,
    WorldReservedName,
    WorldStateTooLarge,
    WorldUnavailable,
    WorldUsageError,
)
from .world.runtime import Call

# --- the World handle (world-handle-interface.md v3.4) --------------------------------------
#
# The frozen contract's code block gives six verbs plus `world_index`/`rng`. `read_only()` is not
# in that block, but the contract still requires `ready`/`check` to receive a handle whose writes
# raise `WorldReadOnly` (own section, "Read-only handles") without saying how a caller gets one —
# the shipped `HostedWorld.read_only()` (`world/handle.py`) already names this exact operation, so
# mirroring it here is the reversible choice: a real `HostedWorld` satisfies this Protocol as-is.
#
# m8: `World.read_only()` used to be typed `-> "World"`, but the real `ReadOnlyWorld` it returns
# has no `read_only()` of its own (mirroring `world/handle.py`'s own `ReadOnlyWorld`, which is
# deliberately not re-enterable) — so it fails a structural check against `World` itself.
# `ReadOnlyWorld` below names the narrower surface `ready`/`check` actually receive.


class ReadOnlyWorld(Protocol):
    world_index: int
    rng: random.Random

    def state(self, table: str | None = None) -> dict[str, list[dict[str, Any]]]: ...

    def put(self, collection: str, record: dict[str, Any], *, key: str = "") -> dict[str, Any]: ...

    def change(
        self, collection: str, key: str, changes: dict[str, Any], *, by: str = ""
    ) -> int: ...

    def drop(self, collection: str, key: str = "", *, by: str = "") -> int: ...

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Call: ...

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...


class World(Protocol):
    world_index: int
    rng: random.Random

    def state(self, table: str | None = None) -> dict[str, list[dict[str, Any]]]: ...

    def put(self, collection: str, record: dict[str, Any], *, key: str = "") -> dict[str, Any]: ...

    def change(
        self, collection: str, key: str, changes: dict[str, Any], *, by: str = ""
    ) -> int: ...

    def drop(self, collection: str, key: str = "", *, by: str = "") -> int: ...

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Call: ...

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]: ...

    def read_only(self) -> ReadOnlyWorld: ...


class WorldFactory(Protocol):
    """Builds the `World` handle for one already-reset `EnvironmentRuntime`.

    Deliberately not this module's job: `HostedWorld` needs a `PostgresStore` (parsed from the
    runtime's `database` endpoint) plus the baseline row counts the provisioner measured at
    freeze time — both live behind `ProcessRuntimeProvider`'s private state, which §4's
    `RuntimeProvider` Protocol never exposes. Injected instead of guessed.
    """

    async def create(self, runtime: EnvironmentRuntime, *, rng: random.Random) -> World: ...


# --- the provisioner surface this module actually drives -------------------------------------
#
# Matches `ProcessRuntimeProvider`'s real async shape (process_runtime.py) structurally, not the
# older single-runtime `runtime.RuntimeProvider`. `bundle` stays `Any` — this module never reads a
# bundle field itself, only threads it back into `provision()` reconcile calls, so it does not
# need `EnvironmentBundleV2`'s own in-flux-adjacent type.
#
# M1 (spine v1.12 §4): `bundle_dir` is a required keyword — §2c seed/migration paths resolve
# against the verified bundle root, never against `source`. `require_declared_user` is dropped
# entirely: it is not in §4's signature, and the real provider now defaults it `True` on its own
# (the local lane opts out at provider construction, not per call).
# M2 (spine §4 point 3): `healthy` — declared readiness probes, not "process is running" — is a
# port method, not an optionally-injected callable.


class WorldProvisioner(Protocol):
    async def provision(
        self,
        bundle: Any,
        *,
        source: Path,
        bundle_dir: Path,
        work_directory: Path,
        contract: Any | None = None,
        instances: int = 1,
    ) -> list[EnvironmentRuntime]: ...

    async def reset(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> None: ...

    async def healthy(self, runtime: EnvironmentRuntime, *, work_directory: Path) -> bool: ...

    async def close(self, *, work_directory: Path) -> None: ...


# --- scenarios (this module's own minimal surface; Karthik's contract is not wired yet) -------


class SubGoal(Protocol):
    name: str
    judged: str  # `sub_goals[].judged` per outbound-channels.md: boolean is `judged != ""`.

    def check(self, world: ReadOnlyWorld, calls: Sequence[Call]) -> object: ...


class Scenario(Protocol):
    scenario_key: str
    scenario_id: str  # platform id from pre-allocation (outbound-channels.md Channel 2 "Join").
    sub_goals: Sequence[SubGoal]

    def setup(self, world: World) -> object: ...

    def ready(self, world: ReadOnlyWorld) -> object: ...


# --- the simulated call (a different track's seam; injected, never built here) ---------------


@dataclass(frozen=True)
class CallOutcome:
    calls: tuple[Call, ...]
    turns: int
    started_at: str | None
    ended_at: str | None
    duration_ms: int
    transcript_artifact: str | None = None
    recording_artifacts: tuple[str, ...] = ()


class CallAborted(RuntimeError):
    """The call step started but did not finish. `partial`, when known, carries whatever timing
    the call runner already measured — the receipt's `call` field must not be null once the call
    has genuinely started (outbound-channels.md Channel 2, "errored receipt body")."""

    def __init__(self, message: str, *, partial: CallOutcome | None = None) -> None:
        super().__init__(message)
        self.partial = partial


class CallRunner(Protocol):
    async def run(self, scenario: Scenario, runtime: EnvironmentRuntime) -> CallOutcome: ...


# --- receipts (outbound-channels.md Channel 2; envelope fields — job_id/attempt_id/digest/etc —
# are the emitter's concern, not reproduced here) -----------------------------------------------


@dataclass(frozen=True)
class SubGoalResult:
    name: str
    held: bool | None
    reason: str | None
    judged: bool


@dataclass(frozen=True)
class Evaluation:
    name: str
    kind: str  # "metric" | "checkpoint"
    reason: str
    score: float | None = None
    passed: bool | None = None


@dataclass(frozen=True)
class CallSummary:
    started_at: str | None
    ended_at: str | None
    duration_ms: int
    turns: int
    transcript_artifact: str | None = None
    recording_artifacts: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReceiptFailure:
    domain: str
    stage: str
    code: str
    message: str


@dataclass(frozen=True)
class ResultReceipt:
    scenario_key: str
    scenario_id: str
    scenario_attempt: int
    world_index: int | None
    status: str  # "passed" | "failed" | "errored" | "skipped"
    sub_goals: tuple[SubGoalResult, ...]
    evaluations: tuple[Evaluation, ...]
    call: CallSummary | None
    failure: ReceiptFailure | None


# --- outbound (this module's own minimal sink; see the module docstring's decoupling note) ----


class OutboundPort(Protocol):
    async def scenario_started(
        self, *, scenario_key: str, world_index: int, scenario_attempt: int
    ) -> None: ...

    async def scenario_retried(
        self, *, scenario_key: str, from_world: int, to_world: int
    ) -> None: ...

    async def world_unhealthy(self, *, world_index: int, cause: str) -> None: ...

    async def log(self, *, level: str, message: str) -> None: ...

    async def receipt(self, receipt: ResultReceipt) -> None: ...


# --- failure-code -> FailureDomain, and which codes retry once on a fresh world ----------------
#
# `world_unavailable` is domain `environment` per world-handle-interface.md's own errored-receipt
# table (it overrides the table's default "domain: simulator"). `evidence_missing` is domain
# simulator but is explicitly carved out as retryable in that same document ("gets the same single
# retry-on-another-world as a world failure"). `call_failed` (v3.3) and `driver_crashed` (v3.4) are
# both rows in that same closed table now: `call_failed` is domain infrastructure, retried once
# like a world failure; `driver_crashed` is domain simulator, not retried — the scheduler's own
# machinery failing while driving a scenario, distinct from any agent/check/call outcome.
# `world_pool_exhausted` is NOT a per-scenario receipt code — it is `HostedScheduler.run()`'s own
# job-abort signal for spine v1.12 §5.4's closed job-level failure vocabulary for stage `running`
# (domain infrastructure).

_CODE_DOMAIN: dict[str, FailureDomain] = {
    "setup_crashed": FailureDomain.SIMULATOR,
    "setup_timeout": FailureDomain.SIMULATOR,
    "ready_timeout": FailureDomain.SIMULATOR,
    "check_timeout": FailureDomain.SIMULATOR,
    "ready_not_ready": FailureDomain.SIMULATOR,
    "ready_broken": FailureDomain.SIMULATOR,
    "check_broken": FailureDomain.SIMULATOR,
    "evidence_missing": FailureDomain.SIMULATOR,
    "world_usage": FailureDomain.SIMULATOR,
    "world_unavailable": FailureDomain.ENVIRONMENT,
    "state_too_large": FailureDomain.SIMULATOR,
    "call_failed": FailureDomain.INFRASTRUCTURE,
    "driver_crashed": FailureDomain.SIMULATOR,
    "world_pool_exhausted": FailureDomain.INFRASTRUCTURE,
}
_RETRYABLE_CODES = frozenset({"evidence_missing"})

# M13: an exception/overrun outcome leaves the world half-applied — world-handle-interface.md's
# return-conventions rule is "the world is discarded and re-provisioned (a half-applied world is
# never reused)." `ready_not_ready` is deliberately excluded: a precondition failing on the shared
# sealed baseline is a clean verdict, not an exception, so the world itself is still fine.
_DISCARD_ON_ERROR_CODES = frozenset(
    {
        "setup_crashed",
        "setup_timeout",
        "ready_timeout",
        "check_timeout",
        "ready_broken",
        "check_broken",
        "world_usage",
        "state_too_large",
    }
)

SETUP_TIMEOUT_SECONDS = 60.0
READY_TIMEOUT_SECONDS = 15.0
CHECK_TIMEOUT_SECONDS = 60.0

_MESSAGE_LIMIT = 2000  # matches the Call.result/error truncation convention (world-handle-interface.md).
_CAUSE_LIMIT = 200  # outbound-channels.md Channel 1: `world_unhealthy.cause` free text <=200.
_USERINFO_PATTERN = re.compile(r"://[^@/]+@")


def _is_retryable(code: str) -> bool:
    return _CODE_DOMAIN[code] in (FailureDomain.ENVIRONMENT, FailureDomain.INFRASTRUCTURE) or (
        code in _RETRYABLE_CODES
    )


def _truncate(text: str, limit: int = _MESSAGE_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - len("…[truncated]")] + "…[truncated]"


def _sanitize_cause(message: str) -> str:
    # M7: `cause` is capped at 200 chars and must never carry endpoint credentials — postgres
    # error strings routinely embed the DSN (`postgresql://user:pw@host/db`).
    return _truncate(_USERINFO_PATTERN.sub("://***@", message), _CAUSE_LIMIT)


def _failure(code: str, message: str) -> ReceiptFailure:
    return ReceiptFailure(
        domain=_CODE_DOMAIN[code].value,
        stage=HarnessStage.RUNNING.value,
        code=code,
        message=_truncate(message),
    )


# --- return-convention classification (world-handle-interface.md "Return conventions") --------


@dataclass(frozen=True)
class _Verdict:
    held: bool
    reason: str | None
    broken: bool


def _classify_ready(value: object) -> _Verdict:
    if value is None or value is True:
        return _Verdict(True, None, False)
    if isinstance(value, str):
        if value.strip() == "":
            return _Verdict(True, None, False)
        return _Verdict(False, value, False)
    # Bare False or any other value -> broken. checks.py's `run_world_check` treats a non-None,
    # non-string ready() answer the same way; a scenario hitting this cannot be told apart from a
    # buggy ready.py, which is why it is `ready_broken` rather than a clean not-ready verdict.
    return _Verdict(False, None, True)


def _classify_check(value: object) -> _Verdict:
    if value is None or value is True:
        return _Verdict(True, None, False)
    if isinstance(value, str):
        if value.strip() == "":
            return _Verdict(True, None, False)
        return _Verdict(False, value, False)
    if value is False:
        # An agent result ("the agent did something wrong"), not a broken check — matches
        # checks.py's `Outcome(name, False, "False")`.
        return _Verdict(False, "False", False)
    return _Verdict(False, None, True)


# --- phase execution: budget + exception classification ---------------------------------------


class _PhaseTimeout(Exception):
    def __init__(self, phase: str) -> None:
        super().__init__(phase)
        self.phase = phase


class _PhaseNeverStarted(Exception):
    """R1: the phase's own worker thread had not even started running when its budget elapsed —
    the dedicated executor was saturated, not the phase itself overrunning. Must not read as a
    genuine timeout (which discards the world); the world did nothing wrong here."""

    def __init__(self, phase: str) -> None:
        super().__init__(phase)
        self.phase = phase


class _PhaseWorldGone(Exception):
    def __init__(self, phase: str, cause: BaseException) -> None:
        super().__init__(f"{phase}: {cause}")
        self.cause = cause


class _PhaseMisuse(Exception):
    def __init__(self, phase: str, cause: BaseException) -> None:
        super().__init__(f"{phase}: {cause}")
        self.cause = cause


class _PhaseStateTooLarge(Exception):
    def __init__(self, phase: str, cause: BaseException) -> None:
        super().__init__(f"{phase}: {cause}")
        self.cause = cause


class _PhaseCrashed(Exception):
    def __init__(self, phase: str, cause: BaseException) -> None:
        super().__init__(f"{phase}: {cause}")
        self.cause = cause


async def _invoke(
    fn: Callable[..., object],
    *args: object,
    timeout: float,
    phase: str,
    executor: ThreadPoolExecutor,
) -> object:
    # R1: a `threading.Event` set as the thread body's first statement — the only way to tell
    # "the phase ran past its budget" (genuine overrun, world half-applied) apart from "the
    # phase's thread was still queued behind others when the budget elapsed" (the scheduler's own
    # executor was saturated; the world itself never touched anything).
    started_flag = threading.Event()

    def _run() -> object:
        started_flag.set()
        return fn(*args)

    async def _call() -> object:
        # B4: real scenario code (`setup`/`ready`/`check`) is synchronous, blocking psycopg calls
        # — it must never run directly on the event loop, or the timeout below is purely
        # decorative and every other world stalls with it. Dispatched to the scheduler's own
        # dedicated executor (world-handle-interface.md: "one worker thread per world"; R1 —
        # never the loop's default executor, which the provider's own `to_thread` calls also
        # use). If the thread's own return value is itself awaitable (scenario code that is
        # `async def`, reached indirectly through a sync wrapper), that coroutine is driven on
        # the event loop afterward, where real suspension/cancellation actually works — this is
        # the kept "awaitable" branch.
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(executor, _run)
        if inspect.isawaitable(result):
            result = await result
        return result

    loop = asyncio.get_running_loop()
    started = loop.time()
    try:
        return await asyncio.wait_for(_call(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        # m4: `asyncio.TimeoutError is TimeoutError` on 3.11 — a psycopg statement timeout raised
        # INSIDE `fn` looks identical to `wait_for`'s own deadline unless the elapsed time is
        # actually checked. If the budget did not genuinely elapse, this was `fn`'s own timeout
        # bubbling through — a broken phase, not a budget overrun.
        if loop.time() - started < timeout:
            raise _PhaseCrashed(phase, exc) from exc
        if not started_flag.is_set():
            raise _PhaseNeverStarted(phase) from exc
        # B4: `wait_for`'s cancellation stops US from waiting on the thread, not the thread
        # itself — psycopg in-flight cancellation is not wired here (P11 follow-up; recorded in
        # the fixer report). The thread is abandoned, bounded by scenario count per the contract's
        # own accepted tradeoff; its world is discarded rather than reused (M13).
        raise _PhaseTimeout(phase) from exc
    except WorldUnavailable as exc:
        raise _PhaseWorldGone(phase, exc) from exc
    except WorldStateTooLarge as exc:
        raise _PhaseStateTooLarge(phase, exc) from exc
    except (WorldReadOnly, WorldReservedName, WorldQueryRejected, WorldUsageError) as exc:
        raise _PhaseMisuse(phase, exc) from exc
    except WorldError as exc:
        # m5: catches any WorldError subclass not special-cased above (world/errors.py's own
        # base, kept exactly for "route 'scenario code misused the handle' to one outcome without
        # naming all six") — a future seventh subclass lands here instead of silently falling into
        # the generic crash classification below.
        raise _PhaseMisuse(phase, exc) from exc
    except Exception as exc:
        raise _PhaseCrashed(phase, exc) from exc


_CRASH_CODE_BY_PHASE = {"setup": "setup_crashed", "ready": "ready_broken", "check": "check_broken"}
_TIMEOUT_CODE_BY_PHASE = {"setup": "setup_timeout", "ready": "ready_timeout", "check": "check_timeout"}


@dataclass(frozen=True)
class _PhaseResult:
    value: object
    failure: ReceiptFailure | None


async def _run_phase(
    fn: Callable[..., object],
    *args: object,
    timeout: float,
    phase: str,
    executor: ThreadPoolExecutor,
) -> _PhaseResult:
    try:
        value = await _invoke(fn, *args, timeout=timeout, phase=phase, executor=executor)
        return _PhaseResult(value, None)
    except _PhaseNeverStarted:
        # R1: not the phase's fault and not the world's — the scheduler's own thread pool
        # couldn't service it in time. `driver_crashed` is not in `_DISCARD_ON_ERROR_CODES`, so
        # this releases the world rather than discarding a perfectly healthy one.
        return _PhaseResult(
            None,
            _failure(
                "driver_crashed", f"{phase} never started before its budget elapsed (thread pool saturated)"
            ),
        )
    except _PhaseTimeout:
        return _PhaseResult(None, _failure(_TIMEOUT_CODE_BY_PHASE[phase], f"{phase} exceeded its budget"))
    except _PhaseWorldGone as exc:
        return _PhaseResult(None, _failure("world_unavailable", str(exc.cause)))
    except _PhaseMisuse as exc:
        return _PhaseResult(None, _failure("world_usage", str(exc.cause)))
    except _PhaseStateTooLarge as exc:
        return _PhaseResult(None, _failure("state_too_large", str(exc.cause)))
    except _PhaseCrashed as exc:
        return _PhaseResult(
            None, _failure(_CRASH_CODE_BY_PHASE[phase], f"{type(exc.cause).__name__}: {exc.cause}")
        )


# --- the world pool -----------------------------------------------------------------------------


class NoWorldsAvailable(RuntimeError):
    """Every provisioned world is down and none is currently recoverable (spine v1.12 §5.4:
    "if ready worlds reach 0 the job FAILS in stage running, domain infrastructure" — declared
    only after in-flight re-provisioning completes without restoring a world), OR the pool has
    been closed (R5: `reason="closed"`)."""

    def __init__(self, message: str, *, reason: str = "exhausted") -> None:
        super().__init__(message)
        self.reason = reason


_RECONCILE_MAX_ATTEMPTS = 3
_RECONCILE_BACKOFF_SECONDS = (0.05, 0.1)
_LEASE_POLL_INTERVAL_SECONDS = 0.02
_CLOSE_RECONCILE_WAIT_SECONDS = 30.0  # R4: bounded wait for an in-flight reconcile before close()
# falls back to cancelling it (which cannot stop a thread-backed provider call already running).


class WorldPool:
    """Leases/releases the W worlds `provisioner.provision()` returns, resets one to pristine on
    every lease (spine §4.2), and reconciles an unhealthy world back in via `provision()` again
    (§4 rule 1: "a sick world mid-job is recovered by calling `provision` again") — in the
    background, so a lease elsewhere never blocks on someone else's recovery.

    B1/B2/M6 (spine v1.12 §4.5b): the provider port is NOT reentrant — at most one
    `provision`/`reset`/`healthy`/`close` call is ever in flight, serialized by `_provider_lock`
    (R13: `healthy` writes — it demotes state — so v1.12 folded it into the same serialized set
    that provision/reset/close were already in; it is no longer treated as a read-only probe
    exempt from the lock). A demotion that lands while a reconcile is already running is coalesced
    into a trailing pass rather than a second concurrent `provision()` call.
    """

    def __init__(
        self,
        provisioner: WorldProvisioner,
        *,
        bundle: Any,
        source: Path,
        bundle_dir: Path,
        work_directory: Path,
        instances: int,
        outbound: OutboundPort | None = None,
    ) -> None:
        self._provisioner = provisioner
        self._bundle = bundle
        self._source = source
        self._bundle_dir = bundle_dir
        self._work_directory = work_directory
        self._instances = instances
        self._outbound = outbound

        self._runtimes: dict[int, EnvironmentRuntime] = {}
        self._available: set[int] = set()
        self._leased: set[int] = set()
        self._down: set[int] = set()
        self._fresh: set[int] = set()  # m9: provisioned/recovered but never yet leased/reset
        self._effective_size = 0  # R2: the achieved world count `start()` settled on

        # m1: `asyncio.Condition` (not a manual `Event` + `clear()`) — waiting and notifying share
        # one lock, so there is no window between releasing a lock and clearing a flag for a
        # `set()` to land in and be silently lost.
        self._state_lock = asyncio.Condition()
        self._provider_lock = asyncio.Lock()
        self._reconcile_task: asyncio.Task[None] | None = None
        self._reconcile_pending = False
        self._started = False
        self._closing = False  # R4: set at the top of close() -- lets an in-flight reconcile bail
        # between attempts instead of burning close()'s wait budget on a pool being torn down.
        self._closed = False  # R5: set once close() has actually run -- latches provision()/lease()
        # out for good; close() itself becomes idempotent.

    @property
    def effective_size(self) -> int:
        """R2: the world count `start()` actually achieved — may be less than the requested
        `instances` on a legitimate degrade (conformance-gate failure, `fixed_port`). P10 sizes
        `parallelism_degraded` and anything else that needs "how many worlds do we really have"
        off this, never off the originally requested `instances`."""
        return self._effective_size

    @property
    def size(self) -> int:
        return len(self._runtimes)

    async def start(self) -> list[EnvironmentRuntime]:
        if self._started:
            # m10: a second call would re-provision behind every already-leased world's back.
            raise RuntimeError("WorldPool.start() called more than once")
        self._started = True

        async with self._provider_lock:
            runtimes = await self._provisioner.provision(
                self._bundle,
                source=self._source,
                bundle_dir=self._bundle_dir,
                work_directory=self._work_directory,
                instances=self._instances,
            )

        # R2 (spine v1.12 §4's conformance gate / `fixed_port`): `provision()` legitimately
        # returns FEWER than `instances` worlds — "Fail → effective parallelism 1 +
        # parallelism_degraded ... Loud, never silent," not a failure this pool should raise on.
        # Reject only a genuinely malformed result: zero worlds, duplicates, a non-contiguous
        # index set, or more worlds than were ever requested.
        indices = {runtime.world_index for runtime in runtimes}
        if (
            not runtimes
            or len(runtimes) != len(indices)
            or indices != set(range(len(runtimes)))
            or len(runtimes) > self._instances
        ):
            # m10/R2: spine §4 — "ordered by world_index" and contiguous from 0 (what
            # `range(effective_instances)` on the provider side guarantees).
            raise RuntimeError(
                f"provision() returned world_index set {sorted(indices)}, expected a contiguous "
                f"0..N-1 subset of 0..{self._instances - 1}"
            )
        self._effective_size = len(runtimes)

        async with self._state_lock:
            for runtime in runtimes:
                self._runtimes[runtime.world_index] = runtime
                if runtime.state in (RuntimeState.READY, RuntimeState.PREPARING):
                    # m10: never hand out a world provision() itself returned UNHEALTHY. A
                    # PREPARING world legitimately demotes straight to UNHEALTHY on a failed first
                    # reset/probe (spine v1.12 §3's preparing->unhealthy transition) -- lease()'s
                    # own health gate covers that case; nothing extra is needed here.
                    self._available.add(runtime.world_index)
                    if runtime.state is RuntimeState.READY:
                        self._fresh.add(runtime.world_index)
                else:
                    self._down.add(runtime.world_index)
            self._state_lock.notify_all()
        return runtimes

    def _reconcile_in_flight(self) -> bool:
        return self._reconcile_task is not None and not self._reconcile_task.done()

    async def _wait_bounded(self, *, poll: bool) -> None:
        if not poll:
            await self._state_lock.wait()
            return
        try:
            await asyncio.wait_for(self._state_lock.wait(), timeout=_LEASE_POLL_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass  # `Condition.wait()` reacquires the lock before propagating even on timeout.

    async def lease(
        self, *, exclude: frozenset[int] = frozenset(), abandon: Callable[[], bool] | None = None
    ) -> tuple[int, EnvironmentRuntime] | None:
        """Returns `None` if `abandon()` reports true while this call was queued (B5) — the caller
        never received a world, so there is nothing to release."""
        while True:
            # R5: latched once close() has run — a lease past that point must never spawn a
            # `reset()`/`healthy()` call against a provider that may already be hard-cleaned.
            if self._closed:
                raise NoWorldsAvailable("world pool is closed", reason="closed")
            if abandon is not None and abandon():
                return None

            async with self._state_lock:
                candidates = self._available - exclude
                if candidates:
                    world_index = min(candidates)
                    self._available.discard(world_index)
                    self._leased.add(world_index)
                    skip_reset = world_index in self._fresh  # m9
                    self._fresh.discard(world_index)
                else:
                    # Not just "every world is down" (the plain retry-exhausted case) — a world
                    # excluded for this lease (a same-scenario retry avoiding its failed world)
                    # can never satisfy `candidates` again no matter how long we wait, so it must
                    # count as unusable here too or a single-world pool's retry blocks forever.
                    usable = set(self._runtimes) - exclude
                    if not (usable - self._down):
                        # M9/R10 (spine v1.12 §5.4): declare exhaustion only once no reconcile is
                        # in flight or about to be — never on an instantaneous snapshot of world
                        # states. `_reconcile_pending` (set inside `mark_unhealthy`'s own critical
                        # section, R10) covers the gap between a demotion and its reconcile task
                        # actually existing.
                        if self._reconcile_in_flight() or self._reconcile_pending:
                            await self._wait_bounded(poll=abandon is not None)
                            continue
                        raise NoWorldsAvailable(
                            f"{len(self._down)}/{len(self._runtimes)} worlds unhealthy, "
                            f"none available outside {sorted(exclude)}"
                        )
                    await self._wait_bounded(poll=abandon is not None)
                    continue

            reset_exc: Exception | None = None
            probed_runtime: EnvironmentRuntime | None = None
            if not skip_reset:
                async with self._provider_lock:
                    runtime = self._runtimes.get(world_index)
                    probed_runtime = runtime
                    if runtime is not None:
                        try:
                            await self._provisioner.reset(runtime, work_directory=self._work_directory)
                        except Exception as exc:  # noqa: BLE001 - B3: must never leak out of lease()
                            reset_exc = exc

            is_healthy = False
            if reset_exc is None:
                # M2: `healthy()` is called unconditionally after reset — including the m9 fast
                # path, which skips only the (expensive) reset call, never the readiness check.
                # R13 (spine v1.12 §4.5b): `healthy` now rides the port's non-reentrancy rule too,
                # so it goes under `_provider_lock` like reset/provision/close.
                async with self._provider_lock:
                    runtime = self._runtimes.get(world_index)
                    probed_runtime = runtime
                    if runtime is not None:
                        try:
                            is_healthy = await self._provisioner.healthy(
                                runtime, work_directory=self._work_directory
                            )
                        except Exception as exc:  # noqa: BLE001
                            reset_exc = exc

            async with self._state_lock:
                # m2/R14: re-read after the awaited provider calls — a concurrent reconcile may
                # have replaced or dropped this index's `EnvironmentRuntime` while lease() awaited.
                # `is_healthy` was computed against `probed_runtime` specifically; if the object
                # at this index is no longer that same object, the verdict no longer describes it
                # — discard this attempt and let the outer loop re-evaluate the index fresh rather
                # than apply a stale verdict to a new object.
                runtime = self._runtimes.get(world_index)
                if runtime is None or runtime is not probed_runtime:
                    self._leased.discard(world_index)
                    self._state_lock.notify_all()
                    continue
                if is_healthy and runtime.state is RuntimeState.READY:
                    self._state_lock.notify_all()
                    return world_index, runtime
                cause = (
                    f"reset failed: {reset_exc}"
                    if reset_exc is not None
                    else f"reset left world in state {runtime.state.value}"
                )

            await self.mark_unhealthy(world_index, cause=cause)
            # loop again — this index is now excluded via `_down`, no explicit retry bookkeeping.

    async def release(self, world_index: int) -> None:
        async with self._state_lock:
            self._leased.discard(world_index)
            if world_index in self._runtimes and world_index not in self._down:
                self._available.add(world_index)
            self._state_lock.notify_all()

    async def mark_unhealthy(self, world_index: int, *, cause: str) -> None:
        async with self._state_lock:
            self._leased.discard(world_index)
            self._available.discard(world_index)
            self._fresh.discard(world_index)
            self._down.add(world_index)
            runtime = self._runtimes.get(world_index)
            if runtime is not None:
                # M12 (spine v1.12 §4.5b, normative): the scheduler demotes `state` on the
                # provider's own live `EnvironmentRuntime` object — that demotion is the signal
                # the NEXT `provision()` reconciles on.
                runtime.state = RuntimeState.UNHEALTHY
            # R10: set inside this same critical section (not left to `_schedule_reconcile`'s own,
            # later one) so a `lease()` observing state in the gap between the two never sees
            # "every world down, no reconcile in flight or pending" and raises spuriously.
            self._reconcile_pending = True
            self._state_lock.notify_all()

        # R6: this is the sole path every demotion (this method) goes through, so it is the one
        # place `world_unhealthy` needs to be emitted from for all four call sites to get it.
        if self._outbound is not None:
            try:
                await self._outbound.world_unhealthy(world_index=world_index, cause=_sanitize_cause(cause))
            except Exception as exc:  # noqa: BLE001 - B3: outbound failures are never fatal.
                await self._log(f"world_unhealthy emit failed: {exc}")

        await self._schedule_reconcile()

    async def _schedule_reconcile(self) -> None:
        async with self._state_lock:
            if self._closed:
                return  # R5: never spawn new provider work once the pool has been closed.
            if self._reconcile_in_flight():
                # B1/M6: a demotion landing mid-reconcile is coalesced into a trailing pass
                # (`_reconcile_loop`) rather than a second concurrent `provision()` call.
                self._reconcile_pending = True
                return
            self._reconcile_task = asyncio.create_task(self._reconcile_loop())

    async def _reconcile_loop(self) -> None:
        while True:
            async with self._state_lock:
                self._reconcile_pending = False
            await self._reconcile()
            async with self._state_lock:
                if not self._reconcile_pending:
                    return

    async def _reconcile(self) -> None:
        # M5: bounded retry with backoff — a single transient `provision()` failure (a momentary
        # ENOSPC, an engine hiccup) used to retire its world for the rest of the job with no
        # signal anywhere. Every failed attempt is logged through `OutboundPort` (when wired),
        # matching the contract's own "loud, never silent" standard for degradation.
        runtimes: list[EnvironmentRuntime] | None = None
        last_exc: Exception | None = None
        for attempt in range(1, _RECONCILE_MAX_ATTEMPTS + 1):
            if self._closing:
                # R4: close() is already bounded-waiting on this task — do not spend its wait
                # budget retrying a pool that is being torn down anyway.
                return
            try:
                async with self._provider_lock:
                    runtimes = await self._provisioner.provision(
                        self._bundle,
                        source=self._source,
                        bundle_dir=self._bundle_dir,
                        work_directory=self._work_directory,
                        instances=self._instances,
                    )
            except Exception as exc:  # noqa: BLE001 - a reconcile must never crash the pool
                last_exc = exc
                await self._log(
                    f"world pool reconcile attempt {attempt}/{_RECONCILE_MAX_ATTEMPTS} failed: {exc}"
                )
                if attempt < _RECONCILE_MAX_ATTEMPTS and not self._closing:
                    await asyncio.sleep(_RECONCILE_BACKOFF_SECONDS[attempt - 1])
                continue
            last_exc = None
            break

        if last_exc is not None or runtimes is None:
            # R8: every success path below ends in `notify_all()` — this give-up path must too,
            # or a `lease()` blocked in `_wait_bounded(poll=False)` (the `abandon is None` case)
            # waits forever for a reconcile that already gave up.
            async with self._state_lock:
                self._state_lock.notify_all()
            return  # stays `_down`; the next `mark_unhealthy` (or a lease-triggered wait) retries.

        # M12: recovery is judged by re-probing `healthy()` (M2's port), never by reading `state`
        # back — the scheduler is what wrote `state` when it demoted this world, so trusting it
        # here would be reading our own signal as independent proof. R13 (spine v1.12 §4.5b):
        # `healthy` now rides the port's non-reentrancy rule, so these probes go under
        # `_provider_lock` too.
        healthy_by_index: dict[int, bool] = {}
        async with self._provider_lock:
            for runtime in runtimes:
                try:
                    healthy_by_index[runtime.world_index] = await self._provisioner.healthy(
                        runtime, work_directory=self._work_directory
                    )
                except Exception:  # noqa: BLE001
                    healthy_by_index[runtime.world_index] = False

        achieved = {runtime.world_index for runtime in runtimes}
        async with self._state_lock:
            for runtime in runtimes:
                self._runtimes[runtime.world_index] = runtime
                if healthy_by_index.get(runtime.world_index, False):
                    was_down = runtime.world_index in self._down
                    self._down.discard(runtime.world_index)
                    if runtime.world_index not in self._leased:
                        self._available.add(runtime.world_index)
                        if was_down and runtime.state is RuntimeState.READY:
                            self._fresh.add(runtime.world_index)  # m9
            # `provision` reconciles to exactly `instances` worlds (a conformance-gate degrade can
            # shrink `achieved` below what this pool started with) — anything no longer returned
            # is gone, not merely unhealthy.
            for stale in [index for index in self._runtimes if index not in achieved]:
                self._runtimes.pop(stale, None)
                self._available.discard(stale)
                self._down.discard(stale)
                self._fresh.discard(stale)
                # m3: NOT `_leased.discard(stale)` — an in-flight scenario may still hold this
                # index's lease (e.g. a conformance degrade shrinking `achieved` mid-scenario);
                # dropping the lease record here would make its later `release()`/
                # `mark_unhealthy()` a silent no-op. Those methods already guard on
                # `world_index in self._runtimes`, so leaving `_leased` alone and letting them
                # reconcile it lazily is correct.
            self._state_lock.notify_all()

    async def close(self) -> None:
        async with self._state_lock:
            if self._closed:
                return  # R5: idempotent, matching spine §4 point 4 ("close is idempotent").
            self._closed = True
            self._closing = True
            task = self._reconcile_task
            # R5: wake anything blocked in `lease()`'s `_wait_bounded(poll=False)` so it re-checks
            # `_closed` instead of waiting for a recovery that will never come.
            self._state_lock.notify_all()

        if task is not None:
            # R4: `ProcessRuntimeProvider.provision`/`reset`/`healthy` are `asyncio.to_thread` —
            # cancelling the awaiting coroutine does NOT stop the underlying thread, so
            # cancelling immediately just races the hard-clean below against a `provision()`
            # still repopulating `self._runtimes`/the worlds directory. Wait for the real work to
            # finish on its own first; only cancel (accepting the thread may still leak, same
            # bounded tradeoff as an abandoned scenario phase) if it blows the bound.
            done, pending = await asyncio.wait({task}, timeout=_CLOSE_RECONCILE_WAIT_SECONDS)
            for pending_task in pending:
                pending_task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)

        async with self._provider_lock:
            await self._provisioner.close(work_directory=self._work_directory)

    async def _log(self, message: str, *, level: str = "error") -> None:
        if self._outbound is None:
            return
        try:
            # R9: reuses `world_unhealthy.cause`'s own sanitizer — a `provision()` failure
            # routinely carries a postgres error string with the DSN, and outbound-channels.md
            # requires redaction (no endpoint userinfo) before anything crosses the wire.
            await self._outbound.log(level=level, message=_sanitize_cause(message))
        except Exception:  # noqa: BLE001 - B3: outbound failures are never fatal.
            pass


# --- the scenario loop --------------------------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    receipts: tuple[ResultReceipt, ...]
    aborted: ReceiptFailure | None


def _skipped_receipt(scenario: Scenario) -> ResultReceipt:
    # Exact body per outbound-channels.md Channel 2, "skipped receipt body (exact)".
    return ResultReceipt(
        scenario_key=scenario.scenario_key,
        scenario_id=scenario.scenario_id,
        scenario_attempt=1,
        world_index=None,
        status="skipped",
        sub_goals=(),
        evaluations=(),
        call=None,
        failure=None,
    )


def _unjudged(sub_goals: Sequence[SubGoal]) -> tuple[SubGoalResult, ...]:
    return tuple(
        # R11: outbound-channels.md pins `judged` as `SubGoal.judged != ""`, not `bool(...)` —
        # they agree for every `str` but `bool` is not what the contract names.
        SubGoalResult(name=goal.name, held=None, reason=None, judged=goal.judged != "")
        for goal in sub_goals
    )


_LEAK_HEADROOM = 10  # R1: spine §1's hosted `scenario_count` admission range is 1..10 -- the most
# phase threads that can ever be simultaneously abandoned (leaked) in one job.


@dataclass
class _ScenarioContext:
    """R7: `_run_scenario` records the world/attempt it is currently working on here as it goes,
    so a crash that escapes every handled path still lets `worker()` report the REAL
    world_index/scenario_attempt on the `driver_crashed` receipt instead of always None/1."""

    world_index: int | None = None
    attempt: int = 1


@dataclass(frozen=True)
class _PendingRetryReceipt:
    """R3: carries attempt-1's already-built `_Retry` outcome across the retry-lease boundary, so
    a cancel/abort landing anywhere between "attempt 1 finished" and "attempt 2 actually starts"
    still reports what attempt 1 produced instead of losing it to skipped-synthesis (the same
    defect M8 fixed on the `NoWorldsAvailable` branch, on the other post-attempt-1 exit)."""

    world_index: int
    attempt: int
    outcome: "_Retry"


class HostedScheduler:
    """Drains a job's scenario list across a `WorldPool`, one asyncio task per scenario — lease()
    blocking when the pool is saturated is what caps concurrency at W, so nothing here re-derives
    a worker count. Retry is fixed at one extra attempt on a fresh world (spine §5 step 4), gated
    on `FailureDomain` per the P9 brief: retryable domains retry once, deterministic ones do not.
    """

    def __init__(
        self,
        *,
        pool: WorldPool,
        world_factory: WorldFactory,
        call_runner: CallRunner,
        outbound: OutboundPort,
        job_seed: int,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._pool = pool
        self._world_factory = world_factory
        self._call_runner = call_runner
        self._outbound = outbound
        self._job_seed = job_seed
        self._cancel_requested = cancel_requested or (lambda: False)
        self._executor: ThreadPoolExecutor | None = None

    async def run(self, scenarios: Sequence[Scenario]) -> RunResult:
        results: list[ResultReceipt | None] = [None] * len(scenarios)
        abort_holder: list[ReceiptFailure | None] = [None]

        # R1: a dedicated executor for scenario phase threads — never the loop's default
        # executor, which the provider's own `to_thread` calls (process_runtime.py) also use, and
        # whose capacity a leaked phase thread would starve globally. One worker per live world
        # plus headroom for the worst case of every admitted scenario leaking its own abandoned
        # thread at once (world-handle-interface.md: "its thread leaks, bounded by scenario
        # count").
        self._executor = ThreadPoolExecutor(
            max_workers=self._pool.effective_size + _LEAK_HEADROOM, thread_name_prefix="hosted-scenario"
        )
        try:

            async def worker(index: int, scenario: Scenario) -> None:
                if abort_holder[0] is not None or self._cancel_requested():
                    return
                context = _ScenarioContext()
                try:
                    results[index] = await self._run_scenario(
                        scenario, index, abort_holder=abort_holder, context=context
                    )
                except NoWorldsAvailable as exc:
                    abort_holder[0] = _failure("world_pool_exhausted", str(exc))
                except asyncio.CancelledError:
                    raise
                except BaseException as exc:  # noqa: BLE001
                    # B3: the scheduler's own machinery crashing must not suppress every other
                    # scenario's receipt — `gather(return_exceptions=True)` below is the second
                    # half of that guarantee.
                    results[index] = await self._driver_crashed_receipt(
                        scenario, exc, world_index=context.world_index, scenario_attempt=context.attempt
                    )

            tasks = [asyncio.create_task(worker(i, s)) for i, s in enumerate(scenarios)]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            receipts: list[ResultReceipt] = []
            for index, scenario in enumerate(scenarios):
                receipt = results[index]
                if receipt is None:
                    receipt = _skipped_receipt(scenario)
                    await self._emit(self._outbound.receipt(receipt), what="receipt")
                receipts.append(receipt)
            return RunResult(receipts=tuple(receipts), aborted=abort_holder[0])
        finally:
            # R1: never block `run()` on abandoned threads — `shutdown(wait=True)` would hang
            # this coroutine exactly like the bug this fixes. Queued-but-unstarted work is
            # cancelled; already-running (leaked) threads are the contract's own accepted,
            # bounded tradeoff (world-handle-interface.md's "the job TTL is the backstop").
            self._executor.shutdown(wait=False, cancel_futures=True)

    async def _emit(self, awaitable: Awaitable[None], *, what: str) -> None:
        # B3: `OutboundPort` exceptions are best-effort telemetry — never receipt-affecting and
        # never fatal to the run. Logged through the same port when logging itself doesn't also
        # fail; swallowed otherwise rather than let a transport hiccup kill the scenario loop.
        try:
            await awaitable
        except Exception as exc:  # noqa: BLE001
            try:
                await self._outbound.log(level="error", message=f"outbound.{what} failed: {exc}")
            except Exception:  # noqa: BLE001
                pass

    async def _driver_crashed_receipt(
        self, scenario: Scenario, exc: BaseException, *, world_index: int | None, scenario_attempt: int
    ) -> ResultReceipt:
        failure = _failure("driver_crashed", f"{type(exc).__name__}: {exc}")
        try:
            # R7: best-effort — every declared goal, `held: null`, matching the errored-receipt
            # body's rule. Falls back to `()` only when reading `sub_goals` itself is what crashed
            # (the one case with no goal list to report at all).
            sub_goals = _unjudged(scenario.sub_goals)
        except Exception:  # noqa: BLE001
            sub_goals = ()
        receipt = ResultReceipt(
            scenario_key=scenario.scenario_key,
            scenario_id=scenario.scenario_id,
            scenario_attempt=scenario_attempt,
            world_index=world_index,
            status="errored",
            sub_goals=sub_goals,
            evaluations=(),
            call=None,
            failure=failure,
        )
        await self._emit(self._outbound.receipt(receipt), what="receipt")
        return receipt

    async def _emit_pending_retry_receipt(
        self, scenario: Scenario, pending: "_PendingRetryReceipt"
    ) -> ResultReceipt:
        # R3: the single shape both post-attempt-1 "never got to run attempt 2" exits emit.
        receipt = ResultReceipt(
            scenario_key=scenario.scenario_key,
            scenario_id=scenario.scenario_id,
            scenario_attempt=pending.attempt,
            world_index=pending.world_index,
            status="errored",
            sub_goals=pending.outcome.sub_goals,
            evaluations=(),
            call=pending.outcome.call,
            failure=pending.outcome.failure,
        )
        await self._emit(self._outbound.receipt(receipt), what="receipt")
        return receipt

    async def _lease_or_abandon(
        self, *, exclude: frozenset[int], abort_holder: list[ReceiptFailure | None]
    ) -> tuple[int, EnvironmentRuntime] | None:
        def _abandon() -> bool:
            return abort_holder[0] is not None or self._cancel_requested()

        return await self._pool.lease(exclude=exclude, abandon=_abandon)

    async def _run_scenario(
        self,
        scenario: Scenario,
        scenario_index: int,
        *,
        attempt: int = 1,
        tried: frozenset[int] = frozenset(),
        pre_leased: tuple[int, EnvironmentRuntime] | None = None,
        pending_retry: "_PendingRetryReceipt | None" = None,
        abort_holder: list[ReceiptFailure | None],
        context: "_ScenarioContext",
    ) -> ResultReceipt | None:
        if pre_leased is not None:
            world_index, runtime = pre_leased
        else:
            leased = await self._lease_or_abandon(exclude=tried, abort_holder=abort_holder)
            if leased is None:
                return None  # B5: cancelled/aborted while queued — never got a world
            world_index, runtime = leased

        context.world_index = world_index  # R7: the real values for a driver_crashed receipt
        context.attempt = attempt

        # B5: re-check immediately after `lease()` returns — a cancel/abort landing while this
        # worker was queued must not let a freshly granted world start work it can never finish
        # inside the flush window.
        if abort_holder[0] is not None or self._cancel_requested():
            await self._pool.release(world_index)
            if pending_retry is not None:
                # R3: attempt 1 already ran on `pending_retry.world_index` and produced a real
                # outcome — this is the retry continuation (this world was never used for it).
                return await self._emit_pending_retry_receipt(scenario, pending_retry)
            return None

        world_resolved = False  # B3: the leased world must be released/discarded exactly once
        try:
            await self._emit(
                self._outbound.scenario_started(
                    scenario_key=scenario.scenario_key,
                    world_index=world_index,
                    scenario_attempt=attempt,
                ),
                what="scenario_started",
            )

            rng = random.Random(self._job_seed + scenario_index)
            outcome: ResultReceipt | _Retry
            try:
                world = await self._world_factory.create(runtime, rng=rng)
            except Exception as exc:  # noqa: BLE001
                # B3: `world_factory.create()` failing (e.g. a PostgresStore connect failure) is
                # the same shape as a mid-scenario `WorldUnavailable` — the world is unusable, not
                # the scenario code. Deliberately narrow to just this call: `_execute()` has its
                # own exhaustive internal exception handling (`_run_phase`/`_invoke`), so anything
                # that still escapes it is a genuine scheduler bug and belongs in `driver_crashed`
                # (via `worker()`'s `BaseException` catch), not swallowed into `world_unavailable`.
                outcome = _Retry(
                    _failure("world_unavailable", f"{type(exc).__name__}: {exc}"),
                    sub_goals=_unjudged(scenario.sub_goals),
                    call=None,
                    mark_unhealthy=True,
                )
            else:
                outcome = await self._execute(scenario, world, runtime, world_index, attempt=attempt)

            if isinstance(outcome, _Retry):
                # R6: `mark_unhealthy()` itself emits `world_unhealthy` now (every demotion path
                # goes through it) — no separate emit needed here.
                if outcome.mark_unhealthy:
                    await self._pool.mark_unhealthy(world_index, cause=outcome.failure.message)
                else:
                    await self._pool.release(world_index)
                world_resolved = True

                if attempt >= 2:
                    receipt = ResultReceipt(
                        scenario_key=scenario.scenario_key,
                        scenario_id=scenario.scenario_id,
                        scenario_attempt=attempt,
                        world_index=world_index,
                        status="errored",
                        sub_goals=outcome.sub_goals,
                        evaluations=(),
                        call=outcome.call,
                        failure=outcome.failure,
                    )
                    await self._emit(self._outbound.receipt(receipt), what="receipt")
                    return receipt

                # R3: attempt 1's outcome, carried forward so either exit below that never gets to
                # start attempt 2 can still report it instead of losing it to skipped-synthesis.
                pending = _PendingRetryReceipt(world_index=world_index, attempt=attempt, outcome=outcome)
                try:
                    next_leased = await self._lease_or_abandon(
                        exclude=tried | {world_index}, abort_holder=abort_holder
                    )
                except NoWorldsAvailable as exc:
                    # M8: this scenario already ran and produced a real attempt-1 failure — losing
                    # it to skipped-synthesis just because the retry lease found nothing would
                    # report "never ran" for a scenario that manifestly did.
                    abort_holder[0] = _failure("world_pool_exhausted", str(exc))
                    return await self._emit_pending_retry_receipt(scenario, pending)

                if next_leased is None:
                    # R3: same defect as the branch above, reached via cancel/abort instead of
                    # pool exhaustion.
                    return await self._emit_pending_retry_receipt(scenario, pending)
                next_index, next_runtime = next_leased
                await self._emit(
                    self._outbound.scenario_retried(
                        scenario_key=scenario.scenario_key, from_world=world_index, to_world=next_index
                    ),
                    what="scenario_retried",
                )
                return await self._run_scenario(
                    scenario,
                    scenario_index,
                    attempt=2,
                    tried=tried | {world_index},
                    pre_leased=(next_index, next_runtime),
                    pending_retry=pending,
                    abort_holder=abort_holder,
                    context=context,
                )

            # M13: a plain terminal receipt is either a real passed/failed verdict (release — the
            # world is fine) or a non-retryable fault from `_fault()`. For the latter, an
            # exception/overrun code means the world is half-applied and must be discarded rather
            # than handed to the next scenario; `ready_not_ready` is a clean verdict and keeps
            # `release()`.
            if outcome.failure is not None and outcome.failure.code in _DISCARD_ON_ERROR_CODES:
                await self._pool.mark_unhealthy(world_index, cause=outcome.failure.message)
            else:
                await self._pool.release(world_index)
            world_resolved = True
            await self._emit(self._outbound.receipt(outcome), what="receipt")
            return outcome
        finally:
            if not world_resolved:
                # B3: something blew past every handled path above (a bug in this module itself)
                # — the world must not be silently stranded outside the pool's bookkeeping.
                # Discarded rather than released: an exception here leaves its state unknown, and
                # world-handle-interface.md's own exception rule is "discarded and re-provisioned,
                # never reused."
                await self._pool.mark_unhealthy(
                    world_index, cause="scenario driver crashed while holding this world"
                )

    async def _execute(
        self, scenario: Scenario, world: World, runtime: EnvironmentRuntime, world_index: int, *, attempt: int
    ) -> "ResultReceipt | _Retry":
        setup = await _run_phase(
            scenario.setup, world, timeout=SETUP_TIMEOUT_SECONDS, phase="setup", executor=self._executor
        )
        if setup.failure is not None:
            return self._fault(scenario, world_index, attempt, setup.failure, sub_goals=_unjudged(scenario.sub_goals))

        read_only = world.read_only()
        ready = await _run_phase(
            scenario.ready, read_only, timeout=READY_TIMEOUT_SECONDS, phase="ready", executor=self._executor
        )
        if ready.failure is not None:
            return self._fault(scenario, world_index, attempt, ready.failure, sub_goals=_unjudged(scenario.sub_goals))
        verdict = _classify_ready(ready.value)
        if verdict.broken:
            return self._fault(
                scenario, world_index, attempt, _failure("ready_broken", f"ready() returned {ready.value!r}"),
                sub_goals=_unjudged(scenario.sub_goals),
            )
        if not verdict.held:
            return self._fault(
                scenario, world_index, attempt, _failure("ready_not_ready", verdict.reason or ""),
                sub_goals=_unjudged(scenario.sub_goals),
            )

        try:
            call_outcome = await self._call_runner.run(scenario, runtime)
        except WorldUnavailable as exc:
            return _Retry(_failure("world_unavailable", str(exc)), sub_goals=_unjudged(scenario.sub_goals), call=None, mark_unhealthy=True)
        except CallAborted as exc:
            call = self._call_summary(exc.partial)
            return self._fault(
                scenario, world_index, attempt, _failure("call_failed", str(exc)),
                sub_goals=_unjudged(scenario.sub_goals), call=call,
            )
        except Exception as exc:  # noqa: BLE001
            # B3: the call runner crashing outright (not a `CallAborted` it chose to raise) is the
            # same world-handle-interface.md v3.3 row — "the simulated-call machinery crashed" —
            # just with no partial evidence to report.
            return self._fault(
                scenario, world_index, attempt, _failure("call_failed", f"{type(exc).__name__}: {exc}"),
                sub_goals=_unjudged(scenario.sub_goals), call=None,
            )

        calls = list(call_outcome.calls)  # m12: `folder.py::_RUNNABLE` expects a list, not a tuple.
        if not calls:
            # M10: unconditioned on `turns` — an empty list must never reach checks regardless of
            # whether the simulator observed a turn (world-handle-interface.md "Coverage
            # guarantee": "An empty list is never handed to checks").
            failure = _failure("evidence_missing", "no tool calls were captured for this scenario's call")
            return _Retry(failure, sub_goals=_unjudged(scenario.sub_goals), call=self._call_summary(call_outcome), mark_unhealthy=False)

        if not scenario.sub_goals:
            # m7: `all(())` is vacuously True — a scenario declaring zero sub-goals must not read
            # as a silent pass.
            return self._fault(
                scenario, world_index, attempt,
                _failure("check_broken", "scenario declared zero sub_goals — a vacuous pass is forbidden"),
                sub_goals=(), call=self._call_summary(call_outcome),
            )

        sub_goal_results: list[SubGoalResult] = []
        check_handle = world.read_only()
        broken_failure: ReceiptFailure | None = None
        for goal in scenario.sub_goals:
            if broken_failure is not None:
                sub_goal_results.append(SubGoalResult(name=goal.name, held=None, reason=None, judged=goal.judged != ""))
                continue
            outcome = await _run_phase(
                goal.check, check_handle, calls, timeout=CHECK_TIMEOUT_SECONDS, phase="check", executor=self._executor
            )
            if outcome.failure is not None:
                if outcome.failure.code == "world_unavailable":
                    return _Retry(
                        outcome.failure, sub_goals=tuple(sub_goal_results) + _unjudged([goal]) + _unjudged(scenario.sub_goals[len(sub_goal_results) + 1 :]),
                        call=self._call_summary(call_outcome), mark_unhealthy=True,
                    )
                broken_failure = outcome.failure
                sub_goal_results.append(SubGoalResult(name=goal.name, held=None, reason=None, judged=goal.judged != ""))
                continue
            verdict = _classify_check(outcome.value)
            if verdict.broken:
                broken_failure = _failure("check_broken", f"{goal.name}: check() returned {outcome.value!r}")
                sub_goal_results.append(SubGoalResult(name=goal.name, held=None, reason=None, judged=goal.judged != ""))
                continue
            sub_goal_results.append(SubGoalResult(name=goal.name, held=verdict.held, reason=verdict.reason, judged=goal.judged != ""))

        if broken_failure is not None:
            return self._fault(
                scenario, world_index, attempt, broken_failure, sub_goals=tuple(sub_goal_results), call=self._call_summary(call_outcome),
            )

        status = "passed" if all(result.held for result in sub_goal_results) else "failed"
        return ResultReceipt(
            scenario_key=scenario.scenario_key,
            scenario_id=scenario.scenario_id,
            scenario_attempt=attempt,
            world_index=world_index,
            status=status,
            sub_goals=tuple(sub_goal_results),
            evaluations=(),
            call=self._call_summary(call_outcome),
            failure=None,
        )

    @staticmethod
    def _call_summary(outcome: CallOutcome | None) -> CallSummary | None:
        if outcome is None:
            return None
        return CallSummary(
            started_at=outcome.started_at,
            ended_at=outcome.ended_at,
            duration_ms=outcome.duration_ms,
            turns=outcome.turns,
            transcript_artifact=outcome.transcript_artifact,
            recording_artifacts=outcome.recording_artifacts,
        )

    def _fault(
        self,
        scenario: Scenario,
        world_index: int,
        attempt: int,
        failure: ReceiptFailure,
        *,
        sub_goals: tuple[SubGoalResult, ...],
        call: CallSummary | None = None,
        retry: bool | None = None,
    ) -> "ResultReceipt | _Retry":
        should_retry = _is_retryable(failure.code) if retry is None else retry
        if should_retry:
            return _Retry(failure, sub_goals=sub_goals, call=call, mark_unhealthy=failure.code == "world_unavailable")
        return ResultReceipt(
            scenario_key=scenario.scenario_key,
            scenario_id=scenario.scenario_id,
            scenario_attempt=attempt,
            world_index=world_index,
            status="errored",
            sub_goals=sub_goals,
            evaluations=(),
            call=call,
            failure=failure,
        )


@dataclass(frozen=True)
class _Retry:
    failure: ReceiptFailure
    sub_goals: tuple[SubGoalResult, ...]
    call: CallSummary | None
    mark_unhealthy: bool


__all__ = [
    "CHECK_TIMEOUT_SECONDS",
    "READY_TIMEOUT_SECONDS",
    "SETUP_TIMEOUT_SECONDS",
    "Call",
    "CallAborted",
    "CallOutcome",
    "CallRunner",
    "CallSummary",
    "Evaluation",
    "HostedScheduler",
    "NoWorldsAvailable",
    "OutboundPort",
    "ReadOnlyWorld",
    "ReceiptFailure",
    "ResultReceipt",
    "RunResult",
    "Scenario",
    "SubGoal",
    "SubGoalResult",
    "World",
    "WorldFactory",
    "WorldPool",
    "WorldProvisioner",
]
