"""Read one agent the way a build reads it, and leave its description, with no build after it.

The platform shows an agent's description before any environment exists, and bills the reading
from ``cost.json``. So the reading runs the build's own stage and gate, and every way it can end
leaves the files the platform judges it by: a saved contract is never thrown away and paid for
again, a timeout is never retried as a crash, and the cost on disk is never marked complete
while some of the spend may be missing from it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .. import __version__
from ..providers.agent import AgentBuildError, build_authored_agent
from . import session
from .backends import StageDone
from .builder import BUILDER_TURNS
from .config import chosen_model
from .contract import AgentContract
from .failure_file import FAILURE_FILE
from .framework_detection import _contract_workdir, detect_framework
from .job import FailureDomain, HarnessFailure, HarnessJob, HarnessStage, SourceKind
from .reading_entry import (
    READING_JOB_INVALID,
    ReadingMode,
    write_reading_cost,
    write_reading_failure,
)
from .session import Stage, StageIdleTimeout
from .source_folder import agent_folder, refuse_unusable_folder
from .sources import resolve as resolve_source
from .understand import open_stage, opening

logger = logging.getLogger(__name__)

AGENT_FILE = "agent.json"
CONTRACT_FILE = "contract.json"

READING_SOURCE_EMPTY = "reading_source_empty"
AGENT_READING_TIMED_OUT = "agent_reading_timed_out"
AGENT_READING_CRASHED = "agent_reading_crashed"
AGENT_READING_MODEL_ERROR = "agent_reading_model_error"
AGENT_READING_INCOMPLETE = "agent_reading_incomplete"
AGENT_READING_TOO_LARGE = "agent_reading_too_large"
AGENT_DOCUMENT_INVALID = "agent_document_invalid"

_LIMIT_ENV = "ALK_READING_MAX_DURATION_SECONDS"
_DEFAULT_SOFT_LIMIT_SECONDS = 1800.0
# The hard limit outlasts the soft one by the interrupt, the wait after it, and a margin, so a
# reading that stops itself is never cut off by the helper first.
_HARD_LIMIT_MARGIN_SECONDS = 180.0
_INTERRUPT_SECONDS = 10.0
_INTERRUPT_WAIT_SECONDS = 60.0

# The platform's own caps, compared the way it compares them.
_DOCUMENT_LIMIT_BYTES = 1_048_576
_CONTRACT_LIMIT_BYTES = 5 * 1_048_576

# A model out of turns can't save, so a reminder after one only adds cost.
_TURN_LIMITS = frozenset({"max_turns", "error_max_turns"})
# Matched only, never repeated in a message: these are ADK's words, not ours.
_CALL_LIMIT = re.compile(r"Max number of llm calls limit of `\d+` exceeded")
# Statuses a retry may get past; any other 4xx would only be refused again.
_PASSING_STATUSES = frozenset({408, 409, 429})
_COUNTS = ("tokens_in", "tokens_out", "tokens_cached")

_EXIT_DONE = 0
_EXIT_RETRYABLE = 1
_EXIT_REFUSED = 78

# The same words the build's unattended reading uses, so the two readings can't drift apart.
_REMINDER = (
    "Nothing was saved: you finished without calling submit_contract. Call it now "
    "with the contract you worked out."
)


def _soft_limit() -> float:
    try:
        seconds = float(os.environ.get(_LIMIT_ENV, ""))
    except ValueError:
        return _DEFAULT_SOFT_LIMIT_SECONDS
    return seconds if math.isfinite(seconds) and seconds > 0 else _DEFAULT_SOFT_LIMIT_SECONDS


def _failure(
    domain: FailureDomain,
    code: str,
    message: str,
    *,
    retryable: bool,
    details: dict[str, Any] | None = None,
) -> HarnessFailure:
    return HarnessFailure(
        domain=domain,
        stage=HarnessStage.UNDERSTANDING_AGENT,
        code=code,
        message=message,
        retryable=retryable,
        details=details or {},
    )


def _check_job(job: HarnessJob) -> HarnessFailure | None:
    if job.source.kind in (SourceKind.GITHUB, SourceKind.ARCHIVE):
        return None
    return _failure(
        FailureDomain.PLATFORM_SYNC,
        READING_JOB_INVALID,
        f"An agent is read from a GitHub repository or an upload, not from a "
        f"{job.source.kind.value} source.",
        retryable=False,
    )


class _ReadingStage(Stage):
    """The build's reading stage, with each turn's own spend kept and written as it lands."""

    def __init__(
        self,
        spec: Any,
        *,
        name: str,
        backend: Any,
        output: Path,
        running_totals: bool,
    ) -> None:
        super().__init__(spec, name=name, backend=backend)
        self.output = output
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
        # On disk after every turn, so a sandbox that dies mid-reading still leaves what it spent.
        write_reading_cost(self.output, complete=False)
        return events

    async def say(self, message: str, **kwargs: Any) -> Any:
        self.in_flight = True
        return await super().say(message, **kwargs)


def _holds_anything(source: Path) -> bool:
    try:
        return source.is_dir() and next(source.iterdir(), None) is not None
    except OSError:
        # Read as empty, so retryable: a source the sandbox can't list yet may be listable next time.
        return False


def _saved(output: Path) -> str:
    if (output / FAILURE_FILE).is_file():
        return FAILURE_FILE
    if (output / CONTRACT_FILE).is_file():
        return CONTRACT_FILE
    return ""


def _out_of_turns(done: StageDone) -> bool:
    # ADK enforces its call budget by raising, which the Gemini backend reports as a failure with
    # no status, so ADK's own words are the signal. Matched whole, since the phrase inside other
    # text may be the model's own. The count is no signal: it catches no real limit, and it would
    # read a request cut on the last allowed call as one.
    return done.outcome in _TURN_LIMITS or (
        done.is_error
        and done.api_error_status is None
        and any(_CALL_LIMIT.fullmatch(str(said)) for said in done.errors)
    )


def _reminder_due(stage: _ReadingStage, output: Path, stopping: asyncio.Event) -> bool:
    if _saved(output) or stopping.is_set():
        return False
    opened = stage.done[-1] if stage.done else None
    # An error flag with no status may have passed; one nudge costs less than a whole retry.
    return opened is None or (
        opened.api_error_status is None and not _out_of_turns(opened)
    )


async def _converse(
    stage: _ReadingStage, agent_source: Any, output: Path, stopping: asyncio.Event
) -> None:
    # The session lives inside this task, so cancelling the task closes it.
    async with stage:
        await stage.say(opening(agent_source))
        if _reminder_due(stage, output, stopping):
            await stage.say(_REMINDER)


async def _stop(stage: _ReadingStage, task: asyncio.Task[None], stopping: asyncio.Event) -> None:
    # Set first, so a turn ending now doesn't earn the reminder.
    stopping.set()
    try:
        await asyncio.wait_for(stage.interrupt_response(), _INTERRUPT_SECONDS)
    except Exception:
        # A stop that can't be delivered is still a timeout, not a crash.
        pass
    await asyncio.wait({task}, timeout=_INTERRUPT_WAIT_SECONDS)
    # Closed before the files are read, so no late tool call can save after the decision.
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


def _refusing(status: Any) -> bool:
    return (
        isinstance(status, int)
        and not isinstance(status, bool)
        and 400 <= status <= 499
        and status not in _PASSING_STATUSES
    )


def _model_error(output: Path, message: str, status: Any = None, *, retryable: bool) -> int:
    write_reading_failure(
        _failure(
            FailureDomain.INFRASTRUCTURE,
            AGENT_READING_MODEL_ERROR,
            message,
            retryable=retryable,
            # Only our own reading of the status: what the provider said stays in the message.
            details={"status": status if isinstance(status, int) else None},
        ),
        output,
    )
    return _EXIT_RETRYABLE if retryable else _EXIT_REFUSED


def _timed_out(output: Path, soft_limit: float) -> int:
    write_reading_failure(
        _failure(
            FailureDomain.INFRASTRUCTURE,
            AGENT_READING_TIMED_OUT,
            f"The agent reading ran past its {soft_limit:g} s limit without saving the agent's "
            "description, and was stopped.",
            retryable=False,
        ),
        output,
    )
    return _EXIT_REFUSED


def _classify(
    job: HarnessJob,
    source: Path,
    output: Path,
    stage: _ReadingStage,
    error: BaseException | None,
) -> int:
    saved = _saved(output)
    if saved == FAILURE_FILE:
        return _EXIT_REFUSED
    # Before any error: the gate accepted the reading, and refusing it now would pay for it twice.
    if saved == CONTRACT_FILE:
        return _document(job, source, output)
    forbidden = stage.forbidden_models()
    if forbidden:
        # A retry would bill the same model again.
        return _model_error(
            output,
            "The provider billed a model this harness may not spend on ("
            + ", ".join(sorted(forbidden))
            + "), so the reading was stopped.",
            retryable=False,
        )
    # A last turn out of turns spent a whole budget, so a retry would likely spend another and
    # stop the same way, whatever failed before it.
    ran_out = bool(stage.done) and _out_of_turns(stage.done[-1])
    failed = [
        done
        for done in stage.done
        if done.api_error_status is not None
        or (done.is_error and not _out_of_turns(done))
    ]
    if not ran_out and (failed or error is not None):
        status = next(
            (done.api_error_status for done in reversed(failed) if done.api_error_status is not None),
            None,
        )
        said = [str(one) for done in failed for one in done.errors]
        if error is not None:
            said.append(f"{type(error).__name__}: {error}")
        shown = f" (status {status})" if isinstance(status, int) else ""
        return _model_error(
            output,
            f"The model provider failed while the agent was being read{shown}: "
            + ("; ".join(said) or "no detail was given."),
            status,
            retryable=not _refusing(status),
        )
    write_reading_failure(
        _failure(
            FailureDomain.INFRASTRUCTURE,
            AGENT_READING_INCOMPLETE,
            "The model finished without saving the agent's description.",
            retryable=False,
            details={
                "outcome": stage.done[-1].outcome if stage.done else "",
                "turns": sum(int(done.turns or 0) for done in stage.done),
            },
        ),
        output,
    )
    return _EXIT_REFUSED


class _TooLarge(Exception):
    def __init__(self, sizes: dict[str, int]) -> None:
        super().__init__("too large")
        self.sizes = sizes


def _framework_folder(job: HarnessJob, source: Path, output: Path) -> Path:
    # As a build chooses it when there is no build: the named folder, else the contract's own.
    if job.source.subfolder:
        return agent_folder(source, job.source.subfolder)
    return _contract_workdir(source, output) or source


def _write_whole(path: Path, body: bytes) -> None:
    # Written whole, then moved: the platform must never read half a document.
    handle = tempfile.NamedTemporaryFile(
        "wb", dir=path.parent, prefix=".agent-", suffix=".tmp", delete=False
    )
    try:
        with handle as writing:
            writing.write(body)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _document(job: HarnessJob, source: Path, output: Path) -> int:
    """The agent's description from the saved contract, as a build makes it."""
    # One catch over every step: a saved contract is paid for, so nothing after it is retryable.
    try:
        contract_bytes = (output / CONTRACT_FILE).stat().st_size
        if contract_bytes > _CONTRACT_LIMIT_BYTES:
            raise _TooLarge({"contract_bytes": contract_bytes})
        contract = AgentContract.model_validate(
            json.loads((output / CONTRACT_FILE).read_text(encoding="utf-8"))
        ).model_dump(mode="json")
        # The echo proves which folder was read, so it comes from the contract.
        built_from = str((contract.get("runtime") or {}).get("source_subfolder") or "")
        if built_from != (job.source.subfolder or ""):
            raise AgentBuildError("subfolder_mismatch")
        framework = detect_framework(
            _framework_folder(job, source, output), repo_root=source, package_dir=None
        )
        inner = build_authored_agent(
            contract, subfolder=built_from or None, framework=framework
        )
        body = json.dumps(
            {"sdk_version": __version__, "agent": inner},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        stored = json.dumps(inner, ensure_ascii=False).encode("utf-8")
        if len(body) > _DOCUMENT_LIMIT_BYTES or len(stored) > _DOCUMENT_LIMIT_BYTES:
            raise _TooLarge(
                {
                    "agent_bytes": len(body),
                    "stored_bytes": len(stored),
                    "contract_bytes": contract_bytes,
                }
            )
        _write_whole(output / AGENT_FILE, body)
    except _TooLarge as too_large:
        write_reading_failure(
            _failure(
                FailureDomain.INFRASTRUCTURE,
                AGENT_READING_TOO_LARGE,
                "The agent's description is larger than the platform stores (1 MiB for the "
                "description, 5 MiB for the contract).",
                retryable=False,
                details=dict(too_large.sizes),
            ),
            output,
        )
        return _EXIT_REFUSED
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "")
        logger.warning(
            "agent document not built: class=%s code=%s", type(exc).__name__, code
        )
        write_reading_failure(
            _failure(
                FailureDomain.INFRASTRUCTURE,
                AGENT_DOCUMENT_INVALID,
                "The agent's description could not be built from its saved contract "
                f"({type(exc).__name__}{': ' + code if code else ''}).",
                retryable=False,
                details={"class": type(exc).__name__, "code": code},
            ),
            output,
        )
        return _EXIT_REFUSED
    return _EXIT_DONE


async def _reading(
    job: HarnessJob, source: Path, output: Path, started: float, soft_limit: float
) -> tuple[int, bool]:
    """The exit code, and whether every turn's spend is known to be in the cost file."""
    # Before the folder check, so an upload that hasn't arrived isn't blamed on the folder.
    if not _holds_anything(source):
        write_reading_failure(
            _failure(
                FailureDomain.PLATFORM_SYNC,
                READING_SOURCE_EMPTY,
                "The agent's source was missing or empty, so there was nothing to read. "
                "It may not have arrived yet.",
                retryable=True,
            ),
            output,
        )
        return _EXIT_RETRYABLE, True
    if refuse_unusable_folder(source, job.source.subfolder, output):
        return _EXIT_REFUSED, True

    # A replayed turn is paid for again, and a new session would restart Claude's running
    # totals under the baseline the difference is taken from.
    session.STAGE_IDLE_RETRIES = 0
    backend = session.resolve()
    agent_source = resolve_source(
        "repo",
        name=str(job.metadata.get("agent_name") or source.name),
        root=source,
        subfolder=job.source.subfolder,
    )
    offered = job.metadata.get("available_evals")
    opened, _ = open_stage(
        agent_source,
        out=output,
        available_evals=offered if isinstance(offered, list) else None,
    )
    # The build's settings, not the stage's own: the reading is meant to seed a build later.
    stage = _ReadingStage(
        replace(opened.spec, max_turns=BUILDER_TURNS, model=chosen_model(), thinking=False),
        name=opened.name,
        backend=backend,
        output=output,
        running_totals=getattr(backend, "name", "") == "claude",
    )
    stopping = asyncio.Event()
    task = asyncio.create_task(_converse(stage, agent_source, output, stopping))
    finished, _ = await asyncio.wait(
        {task}, timeout=max(0.0, started + soft_limit - time.monotonic())
    )

    if not finished:
        await _stop(stage, task, stopping)
        # A request cut off by the stop may be billed without being in the last total.
        saved = _saved(output)
        if saved == FAILURE_FILE:
            return _EXIT_REFUSED, False
        if saved == CONTRACT_FILE:
            return _document(job, source, output), False
        return _timed_out(output, soft_limit), False

    error = task.exception()
    if error is not None and not stage.opened:
        # Nothing reached the provider, so the fault is the reading's own: a crash, retried once.
        raise error
    if isinstance(error, StageIdleTimeout):
        # The quiet turn's spend never arrived; what was saved before it still stands.
        saved = _saved(output)
        if saved == FAILURE_FILE:
            return _EXIT_REFUSED, False
        if saved == CONTRACT_FILE:
            return _document(job, source, output), False
        return (
            _model_error(
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
            done.is_error and done.api_error_status is None and not _out_of_turns(done)
            for done in stage.done
        )
    )
    return _classify(job, source, output, stage, error), complete


async def _read(job: HarnessJob, source: Path, output: Path) -> int:
    # The same instant the helper's hard limit counts from, give or take a few lines.
    started = time.monotonic()
    output = Path(output)
    code, complete = _EXIT_RETRYABLE, False
    try:
        try:
            code, complete = await _reading(job, Path(source), output, started, _soft_limit())
        except Exception as exc:
            # A contract the gate saved is paid for; a crash would have the platform pay again.
            if _saved(output) != CONTRACT_FILE:
                raise
            logger.warning("reading fault after a saved contract: class=%s", type(exc).__name__)
            code, complete = _document(job, Path(source), output), False
    finally:
        # The work owns the final cost file, on every way out.
        write_reading_cost(output, complete=complete)
    return code


READ_AGENT = ReadingMode(
    name="read_agent",
    check_job=_check_job,
    work=_read,
    time_limit_seconds=_soft_limit() + _HARD_LIMIT_MARGIN_SECONDS,
    timed_out_code=AGENT_READING_TIMED_OUT,
    timed_out_retryable=False,
    crashed_code=AGENT_READING_CRASHED,
)
