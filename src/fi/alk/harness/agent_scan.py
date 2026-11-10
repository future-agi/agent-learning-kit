"""Finds the agents in a repository at one commit, or in an upload, and lists them for the platform.

The platform shows this list before it creates an agent, and bills the scan from ``cost.json``.
A plain code check runs first and costs nothing. The model is asked only when that check finds
nothing outside test and example folders, or only files that call a model directly beside
TypeScript or JavaScript it can't read, because the customer pays for every turn of it. So
once the model has answered, nothing it was paid for is thrown away: a crash after it still
writes what was kept, and never ends in a retryable failure that would pay for the same answer
again. Every return leaves the final ``cost.json``, marked incomplete whenever the model's turn
may be missing from it.
"""

from __future__ import annotations

import logging
import os
import stat
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from .job import FailureDomain, HarnessFailure, HarnessJob, SourceKind
from .agent_type_check import EXIT_REFUSED
from .reading_entry import (
    EXIT_DONE,
    EXIT_RETRYABLE,
    MaskSecrets,
    ReadingMode,
    limit_seconds,
    reading_failure,
    write_reading_cost,
    write_reading_failure,
)
from .scan_ai import AiOutcome, run_ai_step
from .scan_document import Row, ScanResultInvalid, finish, root_name, write_agents_json
from .scan_folders import ClockRanOut, FolderChecker, FolderCheckerBroken
from .scan_walk import _WEAKEST, Candidate, Hazard, WalkResult, describe_entry, walk_repo

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

# Failures, written to failure.json.
SCAN_JOB_INVALID = "scan_job_invalid"
SCAN_SOURCE_MISSING = "scan_source_missing"
SCAN_CRASHED = "scan_crashed"
SCAN_HARD_LIMIT_REACHED = "scan_hard_limit_reached"
SCAN_MODEL_ERROR = "scan_model_error"
SCAN_MODEL_FORBIDDEN = "scan_model_forbidden"
SCAN_RESULT_INVALID = "scan_result_invalid"

# Results, written to agents.json as its error.
SCAN_LIMIT_REACHED = "scan_limit_reached"
SCAN_TIMED_OUT = "scan_timed_out"
SCAN_OUT_OF_MEMORY = "scan_out_of_memory"
SCAN_AI_NO_ANSWER = "scan_ai_no_answer"
SCAN_FAILED_AFTER_AI = "scan_failed_after_ai"
AGENT_PATH_UNSUPPORTED = "agent_path_unsupported"

CLOCK_ENV = "ALK_SCAN_MAX_SECONDS"
# The scan's clock when CLOCK_ENV doesn't name one. The helper reads the same variable for its
# hard stop, both when the reading starts.
CLOCK = 1200.0
AI_SECONDS = limit_seconds("ALK_SCAN_AI_MAX_SECONDS", 600.0)
# The helper's hard stop comes this long after the scan's own clock, so a scan that stops itself
# always writes its own file first.
HARD_LIMIT_MARGIN = 600.0
# The model is asked only with this much left: its own time, then what follows it.
AI_LEAST_LEFT = 360.0
# Kept back from the model for the stop sequence, the AI rows' descriptions and their checks.
AI_RESERVE = 180.0

_ERROR_MESSAGES = {
    SCAN_LIMIT_REACHED: "The repository is larger than the scan reads, and no agent was found "
    "before the limit.",
    SCAN_TIMED_OUT: "The scan ran out of time before it found an agent.",
    SCAN_OUT_OF_MEMORY: "The scan ran out of memory before it found an agent.",
    SCAN_AI_NO_ANSWER: "The model looked for agents and gave no answer.",
    SCAN_FAILED_AFTER_AI: "The scan failed after the model looked for agents, and nothing it "
    "found could be kept.",
    AGENT_PATH_UNSUPPORTED: "Every agent found sits at a path the platform cannot use.",
}
_STOPPED = {"limit": SCAN_LIMIT_REACHED, "clock": SCAN_TIMED_OUT, "memory": SCAN_OUT_OF_MEMORY}

# A walk that ran out of memory is dropped whole, so nothing it decided is half kept.
_NO_WALK = WalkResult(
    agents=(),
    more_than_fifty=False,
    stopped="memory",
    unread_files=0,
    source_outside_side=False,
    candidate_outside_side=False,
    script_outside_side=False,
    only_weak_outside_side=False,
    paths_left_out=0,
    upload_others=0,
    upload_others_side=0,
)


@dataclass
class _Scan:
    """What one scan has so far, kept where the after-model catch can still reach it."""

    upload: bool
    walk_rows: list[Row] = field(default_factory=list)
    ai_found: list[Candidate] = field(default_factory=list)
    ai_rows: list[Row] = field(default_factory=list)
    truncated: bool = False
    cut_by_clock: bool = False
    complete: bool = True
    # One look per folder: many agents can share one, and the look walks skipped folders.
    hazards: dict[str, Hazard] = field(default_factory=dict)
    # The model was asked past walk rows that only call a model, so the cap must not drop
    # what it found in their favour.
    models_first: bool = False


def _fail(
    output: Path, domain: FailureDomain, code: str, message: str, *, retryable: bool
) -> int:
    write_reading_failure(reading_failure(domain, code, message, retryable=retryable), output)
    logger.warning("agent scan: failed, code=%s retryable=%s", code, retryable)
    return EXIT_RETRYABLE if retryable else EXIT_REFUSED


def _check_job(job: HarnessJob) -> HarnessFailure | None:
    source = job.source
    if source.kind in (SourceKind.REMOTE, SourceKind.PROVIDER) or source.subfolder:
        return reading_failure(
            FailureDomain.PLATFORM_SYNC,
            SCAN_JOB_INVALID,
            "The agent scan reads a whole GitHub repository or an upload; this job names a "
            "remote or provider agent, or a folder.",
            retryable=False,
        )
    return None


def _holds_a_file(source: Path) -> bool:
    """Whether ``source`` is a folder with at least one regular file, stopping at the first."""
    try:
        if not source.is_dir():
            return False
    except OSError:
        return False
    for directory, _, files in os.walk(source, followlinks=False):
        for name in files:
            try:
                if stat.S_ISREG(os.lstat(os.path.join(directory, name)).st_mode):
                    return True
            except OSError:
                continue
    return False


def _hints(walk: WalkResult) -> str:
    """What the walk counted, for the model's opening. Counts only, never a path: a path invited
    the model to submit again what the walk already listed."""
    side = sum(candidate.side for candidate in walk.agents) + walk.upload_others_side
    # The model is asked past candidates outside side folders only when every one of them
    # only calls a model, so each unlisted one outside them is one of those.
    weak = sum(
        not candidate.side and candidate.sign in _WEAKEST for candidate in walk.agents
    ) + (walk.upload_others - walk.upload_others_side)
    return (
        "source files outside those folders: yes; "
        f"agent-like files inside them: {side}; "
        f"files outside them that only call a model: {weak}; "
        f"files the check could not read: {walk.unread_files}."
    )


def _unchecked(candidate: Candidate, *, found_by_ai: bool = True) -> Row:
    return Row(
        candidate=candidate,
        framework={"name": "unknown", "version": None, "label": None},
        finished=False,
        found_by_ai=found_by_ai,
    )


def _check_into(
    rows: list[Row],
    checker: FolderChecker,
    source: Path,
    candidates: Sequence[Candidate],
    scan: _Scan,
    *,
    found_by_ai: bool,
    deadline: float,
    clock: Callable[[], float],
) -> None:
    """Checks each candidate's folder in order, appending each row as soon as it is known."""
    for index, candidate in enumerate(candidates):
        try:
            word = checker.check(source, candidate.subfolder, deadline=deadline, clock=clock)
        except ClockRanOut:
            scan.truncated = scan.cut_by_clock = True
            # Found, so listed without its folder's word rather than lost: the person can still
            # pick it, and a build of it names the framework.
            rows.extend(
                _unchecked(left, found_by_ai=found_by_ai) for left in candidates[index:]
            )
            return
        rows.append(Row(candidate, word.framework, word.finished, found_by_ai))


def _one_row(walk_rows: Sequence[Row], ai_rows: Sequence[Row]) -> tuple[list[Row], bool]:
    """An upload's one row, at the root, and whether any other was left out."""
    every = [*walk_rows, *ai_rows]
    if not every:
        return [], False
    outside = next((row for row in walk_rows if not row.candidate.side), None)
    if outside is not None and outside.candidate.sign in _WEAKEST:
        # The model was asked past it, so what the model found outside side folders is
        # likelier the agent than a file that only calls a model.
        outside = next((row for row in ai_rows if not row.candidate.side), outside)
    chosen = (
        outside
        or next((row for row in ai_rows if not row.candidate.side), None)
        or every[0]
    )
    if chosen.candidate.subfolder:
        chosen = replace(chosen, candidate=replace(chosen.candidate, subfolder=""))
    return [chosen], len(every) > 1


def _write(
    job: HarnessJob,
    output: Path,
    rows: Sequence[Row],
    *,
    upload: bool,
    truncated: bool,
    error: str | None,
    models_first: bool,
) -> int:
    try:
        body, _ = finish(
            rows=rows,
            root_name=root_name(job),
            commit_sha=None if upload else job.source.commit_sha,
            truncated=truncated,
            error=None if error is None else (error, _ERROR_MESSAGES[error]),
            models_first=models_first,
        )
    except ScanResultInvalid as invalid:
        logger.error("agent scan: result invalid, problem=%s", invalid.problem)
        return _fail(
            output,
            FailureDomain.INFRASTRUCTURE,
            SCAN_RESULT_INVALID,
            "The agent list broke the platform's rules and was not written; this is a fault "
            "in the scan, not in the repository.",
            retryable=False,
        )
    write_agents_json(output, body)
    logger.info(
        "agent scan: written, rows=%d truncated=%s error=%s",
        len(rows),
        truncated,
        error or "none",
    )
    return EXIT_DONE


def _result(
    job: HarnessJob,
    output: Path,
    scan: _Scan,
    walk: WalkResult,
    outcome: AiOutcome | None,
) -> int:
    """Turn the rows and the model's outcome into the file: an upload keeps one row, the
    outcome decides the status and ``truncated``, and the walk's flags add to ``truncated``."""
    if scan.upload:
        rows, others = _one_row(scan.walk_rows, scan.ai_rows)
    else:
        rows, others = [*scan.walk_rows, *scan.ai_rows], False
    truncated = scan.truncated or others
    error: str | None = None
    if outcome is not None:
        if outcome.kind == "model_error":
            logger.warning("agent scan: model step failed, %s", outcome.message)
            if not rows:
                return _fail(
                    output,
                    FailureDomain.INFRASTRUCTURE,
                    SCAN_MODEL_ERROR,
                    "The model failed while looking for agents; a retry may get through."
                    if outcome.retryable
                    else "The model refused the request to look for agents; a retry would be "
                    "refused the same way.",
                    retryable=outcome.retryable,
                )
            # The walk's rows are already checked and cost nothing. A retry would pay for the
            # model again to add at most what it missed, so the rows are kept, marked partial.
            truncated = True
        elif outcome.kind == "forbidden":
            return _fail(
                output,
                FailureDomain.INFRASTRUCTURE,
                SCAN_MODEL_FORBIDDEN,
                "The provider billed a model this kit may not spend on, so the scan stopped.",
                retryable=False,
            )
        elif outcome.kind in ("no_answer", "timed_out"):
            if rows:
                truncated = True
            else:
                error = SCAN_AI_NO_ANSWER if outcome.kind == "no_answer" else SCAN_TIMED_OUT
        truncated = truncated or outcome.refused > 0 or outcome.uncarried > 0
    truncated = (
        truncated
        or walk.more_than_fifty
        or bool(walk.stopped and rows)
        or walk.paths_left_out > 0
        or walk.unread_files > 0
        or scan.cut_by_clock
        or walk.upload_others > 0
        or any(row.candidate.partial or not row.finished for row in rows)
    )
    if not rows and error is None:
        if walk.stopped:
            error = _STOPPED[walk.stopped]
        elif walk.paths_left_out or (outcome is not None and outcome.uncarried):
            error = AGENT_PATH_UNSUPPORTED
    return _write(
        job,
        output,
        rows,
        upload=scan.upload,
        truncated=truncated,
        error=error,
        models_first=scan.models_first,
    )


def _after_ai(
    job: HarnessJob,
    source: Path,
    output: Path,
    scan: _Scan,
    walk: WalkResult,
    outcome: AiOutcome,
    checker: FolderChecker,
    *,
    deadline: float,
    clock: Callable[[], float],
) -> int:
    for folder, entry in outcome.pairs:
        candidate = describe_entry(
            source,
            entry,
            folder,
            deadline=deadline,
            upload=scan.upload,
            clock=clock,
            hazards=scan.hazards,
        )
        if candidate is None:
            scan.truncated = True
            continue
        scan.ai_found.append(candidate)
    # Walk rows first, so a walk row always beats a model row for the same entry, and a
    # duplicate is never checked twice.
    taken = {row.candidate.entry for row in scan.walk_rows}
    fresh: list[Candidate] = []
    for candidate in scan.ai_found:
        if candidate.entry not in taken:
            taken.add(candidate.entry)
            fresh.append(candidate)
    scan.ai_found = fresh
    _check_into(
        scan.ai_rows,
        checker,
        source,
        fresh,
        scan,
        found_by_ai=True,
        deadline=deadline,
        clock=clock,
    )
    return _result(job, output, scan, walk, outcome)


def _keep_what_was_paid_for(
    job: HarnessJob, output: Path, scan: _Scan, crash: Exception
) -> int:
    """After the model was paid for, a crash still ends in a file, never in a paid retry."""
    # Logged with its trace, as the reading helper logs a crash. Only the platform's secret
    # values are masked; a repo path in the message is not.
    logger.error("agent scan: failed after the model step", exc_info=crash)
    checked = {row.candidate.entry for row in scan.ai_rows}
    ai_rows = [
        *scan.ai_rows,
        *(_unchecked(candidate) for candidate in scan.ai_found if candidate.entry not in checked),
    ]
    if scan.upload:
        rows, _ = _one_row(scan.walk_rows, ai_rows)
    else:
        rows = [*scan.walk_rows, *ai_rows]
    try:
        body, _ = finish(
            rows=rows,
            root_name=root_name(job),
            commit_sha=None if scan.upload else job.source.commit_sha,
            truncated=True,
            error=None
            if rows
            else (SCAN_FAILED_AFTER_AI, _ERROR_MESSAGES[SCAN_FAILED_AFTER_AI]),
            models_first=scan.models_first,
        )
        write_agents_json(output, body)
        return EXIT_DONE
    except Exception:
        logger.error("agent scan: the kept rows could not be written", exc_info=True)
    try:
        body, _ = finish(
            rows=[],
            root_name=root_name(job),
            commit_sha=None if scan.upload else job.source.commit_sha,
            truncated=True,
            error=(SCAN_FAILED_AFTER_AI, _ERROR_MESSAGES[SCAN_FAILED_AFTER_AI]),
        )
        write_agents_json(output, body)
        return EXIT_DONE
    except Exception:
        logger.error("agent scan: the fixed error could not be written", exc_info=True)
    raise crash


async def _scan(job: HarnessJob, source: Path, output: Path, scan: _Scan) -> int:
    clock = time.monotonic
    # Taken first, so the source check counts against the clock too.
    deadline = clock() + limit_seconds(CLOCK_ENV, CLOCK)
    if not _holds_a_file(source):
        return _fail(
            output,
            FailureDomain.PLATFORM_SYNC,
            SCAN_SOURCE_MISSING,
            "The source to scan is missing, is not a folder, or holds no file.",
            retryable=True,
        )
    try:
        walk = walk_repo(
            source,
            deadline=deadline,
            upload=scan.upload,
            clock=clock,
            hazards=scan.hazards,
        )
    except MemoryError:
        walk = _NO_WALK
        logger.warning("agent scan: the walk ran out of memory and was dropped")
    with FolderChecker() as checker:
        try:
            _check_into(
                scan.walk_rows,
                checker,
                source,
                walk.agents,
                scan,
                found_by_ai=False,
                deadline=deadline,
                clock=clock,
            )
        except FolderCheckerBroken:
            # No model has run yet, so a retry costs nothing that was paid for.
            return _fail(
                output,
                FailureDomain.INFRASTRUCTURE,
                SCAN_CRASHED,
                "The folder check could not run, before any model was asked.",
                retryable=True,
            )
        # A Python file that only calls a model says nothing of an agent written in TypeScript
        # or JavaScript beside it, which the walk can't list.
        asks_the_model = (
            (
                not walk.candidate_outside_side
                or (walk.only_weak_outside_side and walk.script_outside_side)
            )
            and walk.source_outside_side
            and walk.stopped == ""
            and deadline - clock() >= AI_LEAST_LEFT
        )
        if not asks_the_model:
            return _result(job, output, scan, walk, None)
        scan.models_first = walk.candidate_outside_side
        outcome = await run_ai_step(
            source,
            hints=_hints(walk),
            seconds=min(AI_SECONDS, deadline - clock() - AI_RESERVE),
            on_turn_end=lambda: write_reading_cost(output, complete=False),
        )
        if not outcome.complete:
            scan.complete = False
        # `_write` has already turned a ScanResultInvalid into its 78, so it never reaches here.
        try:
            return _after_ai(
                job,
                source,
                output,
                scan,
                walk,
                outcome,
                checker,
                deadline=deadline,
                clock=clock,
            )
        except Exception as crash:
            return _keep_what_was_paid_for(job, output, scan, crash)


async def _work(job: HarnessJob, source: Path, output: Path) -> int:
    scan = _Scan(upload=job.source.kind is SourceKind.ARCHIVE)
    try:
        return await _scan(job, Path(source), Path(output), scan)
    finally:
        # The one place the final cost is written, so no return can leave the helper's zero.
        write_reading_cost(output, complete=scan.complete)


FIND_AGENTS = ReadingMode(
    name="find_agents",
    check_job=_check_job,
    work=_work,
    time_limit_seconds=CLOCK,
    limit_env=CLOCK_ENV,
    limit_margin_seconds=HARD_LIMIT_MARGIN,
    timed_out_code=SCAN_HARD_LIMIT_REACHED,
    timed_out_retryable=False,
    crashed_code=SCAN_CRASHED,
)
