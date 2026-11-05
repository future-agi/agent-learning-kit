"""The one way into a reading of a repository: an agent read, or agents found, with no build.

A reading is billed from ``cost.json`` and judged from ``failure.json``, so this module owns the
order in which those files appear. Bad input is refused before any model is paid for. The cost
file is on disk, at zero and marked incomplete, before the work starts, and only the work itself
may mark it complete. A hard time limit ends the process even when its own writes fail, because a
reading that outlives its sandbox leaves no file at all and is retried as a crash.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import spend
from .failure_file import write_failure_file
from .job import FailureDomain, HarnessFailure, HarnessJob, HarnessStage
from .outbound import redact_outbound_text

READING_JOB_INVALID = "reading_job_invalid"
READING_OUTPUT_NOT_EMPTY = "reading_output_not_empty"
MESSAGE_LIMIT = 2000
COST_FILE = "cost.json"

_EXIT_RETRYABLE = 1
_EXIT_REFUSED = 78

# Neither file is deleted by the hosted authoring entrypoint, so a read here takes nothing away
# from the code that loads them.
_SECRET_FILES = (
    Path("/run/futureagi/secrets.json"),
    Path("/run/futureagi/simulator-secrets.json"),
)
_secret_values: tuple[str, ...] | None = None


@dataclass(frozen=True)
class ReadingMode:
    name: str
    check_job: Callable[[HarnessJob], HarnessFailure | None]
    work: Callable[[HarnessJob, Path, Path], Awaitable[int]]
    time_limit_seconds: float | None
    timed_out_code: str
    timed_out_retryable: bool
    crashed_code: str


def _read_secret_values() -> tuple[str, ...]:
    # Imported here: the module is large and the find-agents mode shares this helper. A broken
    # import must not end a reading before its failure can be written, so it masks nothing extra.
    try:
        from .hosted_entrypoint import peek_secret_values

        return tuple(value for path in _SECRET_FILES for value in peek_secret_values(path))
    except Exception:
        return ()


def write_reading_failure(failure: HarnessFailure, output: Path) -> Path | None:
    """Leave ``failure`` for the platform with its message made safe to show; the first one stands.

    Details are written as given, so a mode never puts what a provider said in them.
    """
    try:
        secrets = _secret_values if _secret_values is not None else _read_secret_values()
        # Masked before the cut, so a secret split by the cut can't survive half-matched.
        message = redact_outbound_text(failure.message, secrets)[:MESSAGE_LIMIT]
        return write_failure_file(failure.model_copy(update={"message": message}), Path(output))
    except Exception:
        return None


def write_reading_cost(output: Path, *, complete: bool) -> bool:
    """Write the spend so far to ``cost.json``; ``True`` when it was written.

    ``complete`` is kept only in this file, never in ``spend.snapshot()``, which builds share.
    """
    try:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        snapshot = spend.snapshot()
        # A turn with no price makes the total a floor, and a floor billed as the total is wrong.
        body = {**snapshot, "complete": bool(complete) and snapshot["unpriced_turns"] == 0}
        # Written whole, then moved: a poll that reads mid-write must never see half a total.
        handle = tempfile.NamedTemporaryFile(
            "w", dir=output, prefix=".cost-", suffix=".tmp", encoding="utf-8", delete=False
        )
        try:
            with handle as writing:
                json.dump(body, writing, indent=2, sort_keys=True)
            os.replace(handle.name, output / COST_FILE)
        except Exception:
            Path(handle.name).unlink(missing_ok=True)
            raise
    except Exception:
        # Accounting must never be the reason a reading fails.
        return False
    return True


def _failure(
    domain: FailureDomain, code: str, message: str, *, retryable: bool
) -> HarnessFailure:
    return HarnessFailure(
        domain=domain,
        stage=HarnessStage.UNDERSTANDING_AGENT,
        code=code,
        message=message,
        retryable=retryable,
    )


def _holds_anything(output: Path) -> bool:
    try:
        if not os.path.lexists(output):
            return False
        return not output.is_dir() or next(output.iterdir(), None) is not None
    except OSError:
        # A folder that can't be listed can't be shown to be empty.
        return True


def _start_hard_limit(mode: ReadingMode, output: Path) -> threading.Timer | None:
    if mode.time_limit_seconds is None:
        return None
    exit_code = _EXIT_RETRYABLE if mode.timed_out_retryable else _EXIT_REFUSED

    def end_the_process() -> None:
        try:
            write_reading_failure(
                _failure(
                    FailureDomain.INFRASTRUCTURE,
                    mode.timed_out_code,
                    f"The {mode.name} reading ran past its {mode.time_limit_seconds:g} s limit "
                    "and was stopped.",
                    retryable=mode.timed_out_retryable,
                ),
                output,
            )
            write_reading_cost(output, complete=False)
        finally:
            # Work running in a thread can't be cancelled, and a normal exit would wait for it.
            os._exit(exit_code)

    timer = threading.Timer(mode.time_limit_seconds, end_the_process)
    timer.daemon = True
    timer.start()
    return timer


def run_reading_mode(mode: ReadingMode, *, job_path: Path, source: Path, output: Path) -> int:
    """Refuse what can't be read, start the cost at zero, then run the mode's work."""
    global _secret_values
    _secret_values = _read_secret_values()
    output = Path(output)

    try:
        job = HarnessJob.model_validate(json.loads(Path(job_path).read_text(encoding="utf-8")))
    except Exception as exc:
        write_reading_failure(
            _failure(
                FailureDomain.PLATFORM_SYNC,
                READING_JOB_INVALID,
                f"The job file could not be read as a harness job ({type(exc).__name__}: {exc}).",
                retryable=False,
            ),
            output,
        )
        return _EXIT_REFUSED

    try:
        refused = mode.check_job(job)
    except Exception as exc:
        # Nothing has been spent yet, so no cost file: a broken check is a crash, not a refusal.
        write_reading_failure(
            _failure(
                FailureDomain.INFRASTRUCTURE,
                mode.crashed_code,
                f"{type(exc).__name__}: {exc}",
                retryable=True,
            ),
            output,
        )
        return _EXIT_RETRYABLE
    if refused is not None:
        write_reading_failure(refused, output)
        return _EXIT_REFUSED

    # After the job checks on purpose: a stale folder is refused either way, and the failure
    # they wrote into it is no less true.
    if _holds_anything(output):
        write_reading_failure(
            _failure(
                FailureDomain.PLATFORM_SYNC,
                READING_OUTPUT_NOT_EMPTY,
                "The reading's output folder already holds files; give it an empty folder.",
                retryable=False,
            ),
            output,
        )
        return _EXIT_REFUSED

    if not write_reading_cost(output, complete=False):
        # A model paid for while its cost can't be recorded would read as nothing spent.
        write_reading_failure(
            _failure(
                FailureDomain.INFRASTRUCTURE,
                mode.crashed_code,
                "The reading could not write its cost file, so it stopped before any model ran.",
                retryable=True,
            ),
            output,
        )
        return _EXIT_RETRYABLE

    timer = _start_hard_limit(mode, output)
    crash: Exception | None = None
    try:
        print(
            f"reading started at {datetime.now(timezone.utc).isoformat()}",
            file=sys.stderr,
            flush=True,
        )
        code = asyncio.run(mode.work(job, source, output))
    except Exception as exc:
        crash = exc
    finally:
        # Cancelled first, so the limit can't fire during a crash's writes or the caller's cleanup.
        if timer is not None:
            timer.cancel()

    if crash is not None:
        write_reading_failure(
            _failure(
                FailureDomain.INFRASTRUCTURE,
                mode.crashed_code,
                f"{type(crash).__name__}: {crash}",
                retryable=True,
            ),
            output,
        )
        write_reading_cost(output, complete=False)
        return _EXIT_RETRYABLE
    # The work wrote its own final cost; rewriting it here could mark a lost turn as paid.
    return code
