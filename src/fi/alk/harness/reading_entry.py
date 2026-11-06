"""The one way into a reading of a repository: an agent read, or agents found, with no build.

A reading is billed from ``cost.json`` and judged from ``failure.json``, so this module owns the
order in which those files appear. Bad input is refused before any model is paid for. The cost
file is on disk, at zero and marked incomplete, before the work starts, and only the work itself
may mark it complete. A hard time limit ends the process even when its own writes fail, because a
reading that outlives its sandbox leaves no file at all and is retried as a crash.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import math
import os
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from . import spend
from .agent_type_check import EXIT_REFUSED
from .failure_file import write_failure_file
from .job import FailureDomain, HarnessFailure, HarnessJob, HarnessStage
from .outbound import redact_outbound_text

READING_JOB_INVALID = "reading_job_invalid"
READING_OUTPUT_NOT_EMPTY = "reading_output_not_empty"
MESSAGE_LIMIT = 2000
COST_FILE = "cost.json"

EXIT_DONE = 0
EXIT_RETRYABLE = 1

# Neither file is deleted by the hosted authoring entrypoint, so a read here takes nothing away
# from the code that loads them.
_SECRET_FILES = (
    Path("/run/futureagi/secrets.json"),
    Path("/run/futureagi/simulator-secrets.json"),
)

logger = logging.getLogger(__name__)
# Unmasked on purpose: the mask reads the secrets, and the trace of failing to read them holds none.
_secrets_logger = logging.getLogger(f"{__name__}.secrets")


@dataclass(frozen=True)
class ReadingMode:
    name: str
    check_job: Callable[[HarnessJob], HarnessFailure | None]
    work: Callable[[HarnessJob, Path, Path], Awaitable[int]]
    time_limit_seconds: float | None
    timed_out_code: str
    timed_out_retryable: bool
    crashed_code: str
    # When set, the limit is read from this variable as the reading starts, with
    # ``time_limit_seconds`` as its default; the helper's own limit adds the margin to it.
    limit_env: str | None = None
    limit_margin_seconds: float = 0.0


def limit_seconds(env: str, default: float) -> float:
    """The seconds ``env`` names when they are a positive, finite number, else ``default``."""
    try:
        seconds = float(os.environ.get(env, ""))
    except ValueError:
        return default
    return seconds if math.isfinite(seconds) and seconds > 0 else default


def _hard_limit(mode: ReadingMode) -> float | None:
    if mode.time_limit_seconds is None:
        return None
    base = (
        limit_seconds(mode.limit_env, mode.time_limit_seconds)
        if mode.limit_env
        else mode.time_limit_seconds
    )
    return base + mode.limit_margin_seconds


@functools.cache
def _read_secret_values(paths: tuple[Path, ...]) -> tuple[str, ...]:
    # Imported here: the module is large and the find-agents mode shares this helper. A broken
    # import must not end a reading before its failure can be written, so it masks nothing extra.
    try:
        from .hosted_entrypoint import peek_secret_values

        return tuple(value for path in paths for value in peek_secret_values(path))
    except Exception:
        _secrets_logger.warning("secret values not read; only URL userinfo is masked", exc_info=True)
        return ()


def _masked(text: str) -> str:
    return redact_outbound_text(text, _read_secret_values(_SECRET_FILES))


class MaskSecrets(logging.Filter):
    """Masks a logger's lines, stack traces included, the way a reading's failure is masked."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg, record.args = _masked(record.getMessage()), None
        if record.exc_info:
            # Kept only as masked text, so no handler can format the raw exception itself.
            record.exc_text = _masked(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


logger.addFilter(MaskSecrets())


def write_reading_failure(failure: HarnessFailure, output: Path) -> Path | None:
    """Leave ``failure`` for the platform with its message made safe to show; the first one stands.

    Details are written as given, so a mode never puts what a provider said in them.
    """
    try:
        # Masked before the cut, so a secret split by the cut can't survive half-matched.
        message = _masked(failure.message)[:MESSAGE_LIMIT]
        return write_failure_file(failure.model_copy(update={"message": message}), Path(output))
    except Exception:
        logger.warning("failure file not written: code=%s", failure.code, exc_info=True)
        return None


def write_reading_cost(output: Path, *, complete: bool) -> bool:
    """Write the spend so far to ``cost.json``, and again after every turn; ``True`` when written.

    ``complete`` is kept only in this file, never in ``spend.snapshot()``, which builds share.
    """
    try:
        return spend.journal_to(Path(output) / COST_FILE, complete=complete)
    except Exception:
        # Accounting must never be the reason a reading fails.
        logger.warning("cost file not written", exc_info=True)
        return False


def reading_failure(
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


def _unreadable_job(exc: Exception) -> str:
    # Only where and how it failed: pydantic's own text repeats the job, and a job may hold a token.
    if not isinstance(exc, ValidationError):
        return type(exc).__name__
    found = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'job'}: {error['type']}"
        for error in exc.errors(include_input=False)
    )
    return f"ValidationError: {found}"


def _output_in_use(output: Path) -> bool:
    try:
        if not os.path.lexists(output):
            return False
        return not output.is_dir() or next(output.iterdir(), None) is not None
    except OSError:
        # A folder that can't be listed can't be shown to be empty.
        return True


def _start_hard_limit(
    mode: ReadingMode, output: Path, limit: float | None
) -> threading.Timer | None:
    if limit is None:
        return None
    exit_code = EXIT_RETRYABLE if mode.timed_out_retryable else EXIT_REFUSED

    def end_the_process() -> None:
        try:
            write_reading_failure(
                reading_failure(
                    FailureDomain.INFRASTRUCTURE,
                    mode.timed_out_code,
                    f"The {mode.name} reading ran past its {limit:g} s limit "
                    "and was stopped.",
                    retryable=mode.timed_out_retryable,
                ),
                output,
            )
            write_reading_cost(output, complete=False)
        finally:
            # Work running in a thread can't be cancelled, and a normal exit would wait for it.
            os._exit(exit_code)

    timer = threading.Timer(limit, end_the_process)
    timer.daemon = True
    timer.start()
    return timer


async def _work_then_stop_the_clock(
    mode: ReadingMode, job: HarnessJob, source: Path, output: Path, timer: threading.Timer | None
) -> int:
    try:
        return await mode.work(job, source, output)
    finally:
        # Inside the loop: asyncio.run waits for leftover worker threads before it returns, and
        # the limit must not fire during that wait over a reading that has already ended.
        if timer is not None:
            timer.cancel()


def run_reading_mode(mode: ReadingMode, *, job_path: Path, source: Path, output: Path) -> int:
    """Refuse what can't be read, start the cost at zero, then run the mode's work."""
    _read_secret_values(_SECRET_FILES)
    output = Path(output)

    try:
        job = HarnessJob.model_validate(json.loads(Path(job_path).read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError) as exc:
        write_reading_failure(
            reading_failure(
                FailureDomain.PLATFORM_SYNC,
                READING_JOB_INVALID,
                f"The job file could not be read as a harness job ({_unreadable_job(exc)}).",
                retryable=False,
            ),
            output,
        )
        return EXIT_REFUSED

    try:
        refused = mode.check_job(job)
    except Exception as exc:
        logger.warning("the %s job check crashed", mode.name, exc_info=True)
        # Nothing has been spent yet, so no cost file: a broken check is a crash, not a refusal.
        write_reading_failure(
            reading_failure(
                FailureDomain.INFRASTRUCTURE,
                mode.crashed_code,
                f"{type(exc).__name__}: {exc}",
                retryable=True,
            ),
            output,
        )
        return EXIT_RETRYABLE
    if refused is not None:
        write_reading_failure(refused, output)
        return EXIT_REFUSED

    # After the job checks on purpose: a stale folder is refused either way, and the failure
    # they wrote into it is no less true.
    if _output_in_use(output):
        write_reading_failure(
            reading_failure(
                FailureDomain.PLATFORM_SYNC,
                READING_OUTPUT_NOT_EMPTY,
                "The reading's output folder already holds files; give it an empty folder.",
                retryable=False,
            ),
            output,
        )
        return EXIT_REFUSED

    if not write_reading_cost(output, complete=False):
        # A model paid for while its cost can't be recorded would read as nothing spent.
        write_reading_failure(
            reading_failure(
                FailureDomain.INFRASTRUCTURE,
                mode.crashed_code,
                "The reading could not write its cost file, so it stopped before any model ran.",
                retryable=True,
            ),
            output,
        )
        return EXIT_RETRYABLE

    # Read now, not when the mode was made, so it is the same limit the work reads as it starts.
    timer = _start_hard_limit(mode, output, _hard_limit(mode))
    crash: Exception | None = None
    try:
        logger.info("reading started at %s", datetime.now(timezone.utc).isoformat())
        code = asyncio.run(_work_then_stop_the_clock(mode, job, source, output, timer))
    except Exception as exc:
        logger.error("the %s reading crashed", mode.name, exc_info=True)
        crash = exc
    finally:
        # Cancelled first, so the limit can't fire during a crash's writes or the caller's cleanup.
        if timer is not None:
            timer.cancel()

    if crash is not None:
        write_reading_failure(
            reading_failure(
                FailureDomain.INFRASTRUCTURE,
                mode.crashed_code,
                f"{type(crash).__name__}: {crash}",
                retryable=True,
            ),
            output,
        )
        write_reading_cost(output, complete=False)
        return EXIT_RETRYABLE
    # The work wrote its own final cost; rewriting it here could mark a lost turn as paid.
    return code
