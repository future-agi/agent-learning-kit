"""Read one agent the way a build reads it, and leave its description, with no build after it.

The platform shows an agent's description before any environment exists, and bills the reading
from ``cost.json``. So the reading runs the build's own stage and gate, and every way it can end
leaves the files the platform judges it by: a saved contract is never thrown away and paid for
again, a timeout is never retried as a crash, and the cost on disk is never marked complete
while some of the spend may be missing from it.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import session
from .agent_type_check import EXIT_REFUSED
from .backends import StageDone
from .cli import guidance_text
from .failure_file import FAILURE_FILE
from .job import FailureDomain, HarnessFailure, HarnessJob, ProviderExecutionMode, SourceKind
from .reading_entry import (
    EXIT_RETRYABLE,
    READING_JOB_INVALID,
    MaskSecrets,
    ReadingMode,
    limit_seconds,
    reading_failure,
    write_reading_cost,
    write_reading_failure,
)
from .reading_result import (
    CONTRACT_FILE,
    classify,
    document,
    model_error,
    out_of_turns,
    saved_file,
)
from .session import Stage, StageIdleTimeout
from .source_folder import refuse_unusable_folder
from .sources import resolve as resolve_source
from .understand import SUBMIT_REMINDER, open_stage, opening, reading_settings

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

READING_SOURCE_EMPTY = "reading_source_empty"
AGENT_READING_TIMED_OUT = "agent_reading_timed_out"
AGENT_READING_CRASHED = "agent_reading_crashed"

_LIMIT_ENV = "ALK_READING_MAX_DURATION_SECONDS"
_DEFAULT_SOFT_LIMIT_SECONDS = 1800.0
# The hard limit outlasts the soft one by the longest stop (the interrupt, the wait after it and
# the close: 90 s) and room for the saved contract's document after it, whose framework scan can
# take well over a minute on a crafted repository, so a reading that stops itself is never cut
# off by the helper first.
_HARD_LIMIT_MARGIN_SECONDS = 300.0
_INTERRUPT_SECONDS = 10.0
_INTERRUPT_WAIT_SECONDS = 60.0
# Ours, not the SDK's: its close is shielded and has no bound of its own. With it the decision
# comes by soft + 90 s, which leaves the rest of the margin to the document.
_CLOSE_SECONDS = 20.0

_COUNTS = ("tokens_in", "tokens_out", "tokens_cached")


def _soft_limit() -> float:
    # The helper reads the same variable the same way for its own limit.
    return limit_seconds(_LIMIT_ENV, _DEFAULT_SOFT_LIMIT_SECONDS)


def _check_job(job: HarnessJob) -> HarnessFailure | None:
    if job.agent.mode in (ProviderExecutionMode.PROVIDER_IMPORT, ProviderExecutionMode.CONNECT_ONLY):
        # The build reads these agents from the provider's definition, which a reading never loads.
        return reading_failure(
            FailureDomain.PLATFORM_SYNC,
            READING_JOB_INVALID,
            "A reading without a build reads an agent from its repository only. A "
            f"{job.agent.mode.value} job's agent is defined by its provider, so it needs a build.",
            retryable=False,
        )
    if job.source.kind in (SourceKind.GITHUB, SourceKind.ARCHIVE):
        return None
    return reading_failure(
        FailureDomain.PLATFORM_SYNC,
        READING_JOB_INVALID,
        f"An agent is read from a GitHub repository or an upload, not from a "
        f"{job.source.kind.value} source.",
        retryable=False,
    )


class _ReadingStage(Stage):
    """The build's reading stage, with each turn's own spend kept, and no turn ever replayed."""

    def __init__(
        self,
        spec: Any,
        *,
        name: str,
        backend: Any,
        running_totals: bool,
    ) -> None:
        # Never replayed after a quiet stream: a replayed turn is paid for again, and a new
        # session would restart Claude's running totals under the baseline the difference is
        # taken from.
        super().__init__(spec, name=name, backend=backend, idle_retries=0)
        self.running_totals = running_totals
        self.done: list[StageDone] = []
        self.in_flight = False
        self.opened = False
        self._last_cost = 0.0
        self._last_counts = dict.fromkeys(_COUNTS, 0)

    async def __aenter__(self) -> "_ReadingStage":
        await super().__aenter__()
        self.opened = True
        return self

    def _own(self, reported: StageDone) -> StageDone:
        # Claude reports the session's running total on every result, so adding results as
        # reported would count every earlier turn again.
        if not self.running_totals:
            return reported
        cost = reported.cost_usd
        if cost is not None:
            cost, self._last_cost = max(0.0, cost - self._last_cost), cost
        counts: dict[str, int] = {}
        for key in _COUNTS:
            total = int(getattr(reported, key) or 0)
            counts[key] = max(0, total - self._last_counts[key])
            self._last_counts[key] = total
        return replace(reported, cost_usd=cost, **counts)

    def _events(self, received: Any, turn: Any) -> list[Any]:
        if not isinstance(received, StageDone):
            return super()._events(received, turn)
        done = self._own(received)
        events = super()._events(done, turn)
        self.done.append(done)
        self.in_flight = False
        return events

    async def say(self, message: str, **kwargs: Any) -> Any:
        self.in_flight = True
        return await super().say(message, **kwargs)


def _source_has_files(source: Path) -> bool:
    try:
        return source.is_dir() and next(source.iterdir(), None) is not None
    except OSError:
        # Read as empty, so retryable: a source the sandbox can't list yet may be listable next time.
        return False


def _reminder_due(stage: _ReadingStage, output: Path, stopping: asyncio.Event) -> bool:
    if saved_file(output) or stopping.is_set():
        return False
    opened = stage.done[-1] if stage.done else None
    # An error flag with no status may have passed; one nudge costs less than a whole retry.
    return opened is None or (
        opened.api_error_status is None and not out_of_turns(opened)
    )


async def _converse(
    stage: _ReadingStage, message: str, output: Path, stopping: asyncio.Event
) -> None:
    # The session lives inside this task, so cancelling the task closes it.
    async with stage:
        await stage.say(message)
        if _reminder_due(stage, output, stopping):
            await stage.say(SUBMIT_REMINDER)


async def _stop(
    stage: _ReadingStage, task: asyncio.Task[None], stopping: asyncio.Event, wait: float
) -> None:
    # Set first, so a turn ending now doesn't earn the reminder.
    stopping.set()
    # Its own task, waited on with a bound: wait_for would wait again for a cancel the interrupt
    # swallows, and catching the cancel around it would swallow a Ctrl-C too.
    interrupting = asyncio.ensure_future(stage.interrupt_response())
    bound = min(wait, _INTERRUPT_SECONDS)
    await asyncio.wait({interrupting}, timeout=bound)
    if not interrupting.done():
        interrupting.cancel()
        logger.warning("the stop at the time limit was not delivered within %g s", bound)
    elif interrupting.cancelled() or interrupting.exception() is not None:
        # A stop that can't be delivered is still a timeout, not a crash.
        logger.warning(
            "the stop at the time limit was not delivered",
            exc_info=None if interrupting.cancelled() else interrupting.exception(),
        )
    await asyncio.wait({task}, timeout=wait)
    # Closed before the files are read, so a late tool call is unlikely to save after the
    # decision. The close is bounded, though: a session that hangs closing must not keep the
    # reading from deciding, and one that outlives the bound could still act afterwards.
    task.cancel()
    closing = min(wait, _CLOSE_SECONDS)
    await asyncio.wait({task}, timeout=closing)
    if not task.done():
        logger.warning("the stopped reading's session did not close within %g s", closing)
    elif not task.cancelled() and task.exception() is not None:
        logger.warning("the stopped reading ended with an error", exc_info=task.exception())


def _timed_out(output: Path, soft_limit: float) -> int:
    write_reading_failure(
        reading_failure(
            FailureDomain.INFRASTRUCTURE,
            AGENT_READING_TIMED_OUT,
            f"The agent reading ran past its {soft_limit:g} s limit without saving the agent's "
            "description, and was stopped.",
            retryable=False,
        ),
        output,
    )
    return EXIT_REFUSED


async def _reading(
    job: HarnessJob, source: Path, output: Path, started: float, soft_limit: float, wait: float
) -> tuple[int, bool]:
    """The exit code, and whether every turn's spend is known to be in the cost file."""
    # Before the folder check, so an upload that hasn't arrived isn't blamed on the folder.
    if not _source_has_files(source):
        write_reading_failure(
            reading_failure(
                FailureDomain.PLATFORM_SYNC,
                READING_SOURCE_EMPTY,
                "The agent's source was missing or empty, so there was nothing to read. "
                "It may not have arrived yet.",
                retryable=True,
            ),
            output,
        )
        return EXIT_RETRYABLE, True
    if refuse_unusable_folder(source, job.source.subfolder, output):
        return EXIT_REFUSED, True

    backend = session.resolve()
    agent_source = resolve_source(
        "repo",
        name=str(job.metadata.get("agent_name") or source.name),
        root=source,
        subfolder=job.source.subfolder,
    )
    # Opened as the build opens its own reading, turns, thinking and setup rule included, so a
    # contract saved here is one the build's gate would have kept.
    opened, _ = open_stage(agent_source, out=output, **reading_settings(job))
    stage = _ReadingStage(
        opened.spec,
        name=opened.name,
        backend=backend,
        running_totals=getattr(backend, "name", "") == "claude",
    )
    stopping = asyncio.Event()
    # The build's opening, the job's guidance included: a generic job's runtime advice changes
    # what the contract records.
    message = opening(agent_source) + guidance_text(job.authoring_guidance())
    task = asyncio.create_task(_converse(stage, message, output, stopping))
    finished, _ = await asyncio.wait(
        {task}, timeout=max(0.0, started + soft_limit - time.monotonic())
    )

    if not finished:
        await _stop(stage, task, stopping, wait)
        # A request cut off by the stop may be billed without being in the last total.
        saved = saved_file(output)
        if saved == FAILURE_FILE:
            return EXIT_REFUSED, False
        if saved == CONTRACT_FILE:
            return document(job, source, output), False
        return _timed_out(output, soft_limit), False

    error = task.exception()
    if error is not None and not stage.opened:
        # Nothing reached the provider, so the fault is the reading's own: a crash, retried once.
        raise error
    if isinstance(error, StageIdleTimeout):
        # The quiet turn's spend never arrived; what was saved before it still stands.
        saved = saved_file(output)
        if saved == FAILURE_FILE:
            return EXIT_REFUSED, False
        if saved == CONTRACT_FILE:
            return document(job, source, output), False
        return (
            model_error(
                output,
                "The model stopped responding before the agent's description was saved "
                f"({error}).",
                retryable=True,
            ),
            False,
        )
    # A request cut with no status may still be billed, and its total never reached us.
    complete = (
        error is None
        and not stage.in_flight
        and not any(
            done.is_error and done.api_error_status is None and not out_of_turns(done)
            for done in stage.done
        )
    )
    return classify(job, source, output, stage, error), complete


async def _read(job: HarnessJob, source: Path, output: Path, *, interrupt_wait: float) -> int:
    # The same instant the helper's hard limit counts from, give or take a few lines.
    started = time.monotonic()
    output = Path(output)
    code, complete = EXIT_RETRYABLE, False
    try:
        try:
            code, complete = await _reading(
                job, Path(source), output, started, _soft_limit(), interrupt_wait
            )
        except (Exception, asyncio.CancelledError) as exc:
            # A contract the gate saved is paid for; a crash would have the platform pay again.
            # A cancel leaking out of the session is the same: the saved contract still stands.
            if saved_file(output) != CONTRACT_FILE:
                raise
            logger.warning(
                "reading fault after a saved contract: class=%s", type(exc).__name__, exc_info=True
            )
            code, complete = document(job, Path(source), output), False
    finally:
        # The work owns the final cost file, on every way out.
        write_reading_cost(output, complete=complete)
    return code


def _read_agent(*, interrupt_wait: float = _INTERRUPT_WAIT_SECONDS) -> ReadingMode:
    return ReadingMode(
        name="read_agent",
        check_job=_check_job,
        work=functools.partial(_read, interrupt_wait=interrupt_wait),
        # The helper reads the variable when the reading starts, as the work does, so its
        # limit is always the reading's own plus the margin.
        time_limit_seconds=_DEFAULT_SOFT_LIMIT_SECONDS,
        timed_out_code=AGENT_READING_TIMED_OUT,
        timed_out_retryable=False,
        crashed_code=AGENT_READING_CRASHED,
        limit_env=_LIMIT_ENV,
        limit_margin_seconds=_HARD_LIMIT_MARGIN_SECONDS,
    )


READ_AGENT = _read_agent()
