"""What a finished reading leaves: the agent's description from its saved contract, or why not.

A saved contract has been paid for, so once one exists nothing after it is retried. Without one,
the reading's own turns decide whether a retry could get further.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from .agent_type_check import EXIT_REFUSED
from .backends import StageDone
from .failure_file import FAILURE_FILE
from .framework_detection import detect_framework, named_framework_folder
from .job import FailureDomain, HarnessJob
from .reading_entry import (
    EXIT_DONE,
    EXIT_RETRYABLE,
    MaskSecrets,
    reading_failure,
    write_reading_failure,
)
from .scenario_source import authored_agent_document

if TYPE_CHECKING:
    from .agent_reading import _ReadingStage

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

AGENT_FILE = "agent.json"
CONTRACT_FILE = "contract.json"

AGENT_READING_MODEL_ERROR = "agent_reading_model_error"
AGENT_READING_INCOMPLETE = "agent_reading_incomplete"
AGENT_READING_TOO_LARGE = "agent_reading_too_large"
AGENT_DOCUMENT_INVALID = "agent_document_invalid"

# The platform's own caps, compared the way it compares them.
_DOCUMENT_LIMIT_BYTES = 1_048_576
_CONTRACT_LIMIT_BYTES = 5 * 1_048_576

# A model out of turns can't save, so a reminder after one only adds cost.
_TURN_LIMITS = frozenset({"max_turns", "error_max_turns"})
# Matched only, never repeated in a message: these are ADK's words, not ours.
_CALL_LIMIT = re.compile(r"Max number of llm calls limit of `\d+` exceeded")
# Statuses a retry may get past; any other 4xx would only be refused again.
_PASSING_STATUSES = frozenset({408, 409, 429})


def saved_file(output: Path) -> str:
    if (output / FAILURE_FILE).is_file():
        return FAILURE_FILE
    if (output / CONTRACT_FILE).is_file():
        return CONTRACT_FILE
    return ""


def out_of_turns(done: StageDone) -> bool:
    # ADK enforces its call budget by raising, which the Gemini backend reports as a failure with
    # no status, so ADK's own words are the signal. Matched whole, since the phrase inside other
    # text may be the model's own. The count is no signal: it catches no real limit, and it would
    # read a request cut on the last allowed call as one.
    return done.outcome in _TURN_LIMITS or (
        done.is_error
        and done.api_error_status is None
        and any(_CALL_LIMIT.fullmatch(str(said)) for said in done.errors)
    )


def _refusing(status: Any) -> bool:
    return (
        isinstance(status, int)
        and not isinstance(status, bool)
        and 400 <= status <= 499
        and status not in _PASSING_STATUSES
    )


def model_error(output: Path, message: str, status: Any = None, *, retryable: bool) -> int:
    write_reading_failure(
        reading_failure(
            FailureDomain.INFRASTRUCTURE,
            AGENT_READING_MODEL_ERROR,
            message,
            retryable=retryable,
            # Only our own reading of the status: what the provider said stays in the message.
            details={"status": status if isinstance(status, int) else None},
        ),
        output,
    )
    return EXIT_RETRYABLE if retryable else EXIT_REFUSED


def classify(
    job: HarnessJob,
    source: Path,
    output: Path,
    stage: _ReadingStage,
    error: BaseException | None,
) -> int:
    saved = saved_file(output)
    if saved == FAILURE_FILE:
        return EXIT_REFUSED
    # Before any error: the gate accepted the reading, and refusing it now would pay for it twice.
    if saved == CONTRACT_FILE:
        return document(job, source, output)
    forbidden = stage.forbidden_models()
    if forbidden:
        # A retry would bill the same model again.
        return model_error(
            output,
            "The provider billed a model this harness may not spend on ("
            + ", ".join(sorted(forbidden))
            + "), so the reading was stopped.",
            retryable=False,
        )
    # A last turn out of turns spent a whole budget, so a retry would likely spend another and
    # stop the same way, whatever failed before it.
    ran_out = bool(stage.done) and out_of_turns(stage.done[-1])
    failed = [
        done
        for done in stage.done
        if done.api_error_status is not None
        or (done.is_error and not out_of_turns(done))
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
        return model_error(
            output,
            f"The model provider failed while the agent was being read{shown}: "
            + ("; ".join(said) or "no detail was given."),
            status,
            retryable=not _refusing(status),
        )
    write_reading_failure(
        reading_failure(
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
    return EXIT_REFUSED


class _TooLarge(Exception):
    def __init__(self, sizes: dict[str, int]) -> None:
        super().__init__("too large")
        self.sizes = sizes


def _framework_folder(job: HarnessJob, source: Path, output: Path, contract_text: str) -> Path:
    # As a build chooses it when there is no build: the named folder, else the contract's own.
    return (
        named_framework_folder(
            source, output, job.source.subfolder, contract_text=contract_text
        )
        or source
    )


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


def document(job: HarnessJob, source: Path, output: Path) -> int:
    """The agent's description from the saved contract, as a build makes it."""
    # One catch over every step: a saved contract is paid for, so nothing after it is retryable.
    try:
        # Read once, here, so what is checked is what is used: the session may still save while
        # the framework scan below runs.
        with (output / CONTRACT_FILE).open("rb") as handle:
            raw = handle.read(_CONTRACT_LIMIT_BYTES + 1)
            contract_bytes = max(os.fstat(handle.fileno()).st_size, len(raw))
        if contract_bytes > _CONTRACT_LIMIT_BYTES:
            raise _TooLarge({"contract_bytes": contract_bytes})
        try:
            from .seed_contract import replace_if_unchanged, with_record

            # What the reading was made from, so a build can tell whether it may reuse it. Made
            # from these bytes and written only over them, so a later save is never lost.
            recorded = with_record(job, raw)
            if recorded is not None:
                replace_if_unchanged(output / CONTRACT_FILE, raw, recorded)
        except Exception:
            # A paid reading never fails for its record. The mask covers the trace, and
            # nothing here runs pydantic.
            logger.warning("reading record not written", exc_info=True)
        contract_text = raw.decode("utf-8")
        framework = detect_framework(
            _framework_folder(job, source, output, contract_text),
            repo_root=source,
            package_dir=None,
        )
        # The build's own document, made by the build's own code.
        made = authored_agent_document(
            contract_text, subfolder=job.source.subfolder, framework=framework
        )
        body = json.dumps(made, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        stored = json.dumps(made["agent"], ensure_ascii=False).encode("utf-8")
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
            reading_failure(
                FailureDomain.INFRASTRUCTURE,
                AGENT_READING_TOO_LARGE,
                "The agent's description is larger than the platform stores (1 MiB for the "
                "description, 5 MiB for the contract).",
                retryable=False,
                details=dict(too_large.sizes),
            ),
            output,
        )
        return EXIT_REFUSED
    except Exception as exc:
        code = str(getattr(exc, "code", "") or "")
        logger.warning(
            "agent document not built: class=%s code=%s",
            type(exc).__name__,
            code,
            # pydantic's trace repeats values from the contract, which the mask can't know.
            exc_info=not isinstance(exc, ValidationError),
        )
        details = {"class": type(exc).__name__, "code": code}
        if getattr(exc, "detail", "") == "subfolder_mismatch":
            # Named as the build names it: the one detail that is ours, not the contract's.
            details["detail"] = "subfolder_mismatch"
        write_reading_failure(
            reading_failure(
                FailureDomain.INFRASTRUCTURE,
                AGENT_DOCUMENT_INVALID,
                "The agent's description could not be built from its saved contract "
                f"({type(exc).__name__}{': ' + code if code else ''}).",
                retryable=False,
                details=details,
            ),
            output,
        )
        return EXIT_REFUSED
    return EXIT_DONE
