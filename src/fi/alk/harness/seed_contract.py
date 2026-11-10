"""A reading handed to a build, so the build can skip reading the same agent again.

The reading records what it was made from beside its contract. The build command checks the
handed file's bytes before anything starts, and the builder installs it only when it was made
from the same source, folder, eval list and generic flag; otherwise the build reads as it does
without one.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

from .agent_type_check import EXIT_REFUSED
from .failure_file import write_failure_file
from .job import (
    FailureDomain,
    HarnessFailure,
    HarnessJob,
    HarnessStage,
    ProviderExecutionMode,
    SourceKind,
)
from .reading_result import _CONTRACT_LIMIT_BYTES

RECORD_KEY = "reading_inputs"
SEED_ACCEPTED = ".futureagi-seed-accepted"
SEED_CONTRACT_INVALID = "seed_contract_invalid"
SEED_CONTRACT_TOO_LARGE = "seed_contract_too_large"
# What a reading may save is what a build may be handed.
SEED_LIMIT_BYTES = _CONTRACT_LIMIT_BYTES

_CONTRACT = "contract.json"
_SOURCE_MODEL = Path("generic-harness") / "source-model.json"
_COMPARED = ("source", "subfolder", "evals_sha256", "generic_harness_v1")


class Problem(NamedTuple):
    code: str
    message: str
    details: dict[str, Any]


def seed_beside(output: str | Path) -> Path:
    return Path(output).parent / "seed" / _CONTRACT


def _canon(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()


def _write_whole(path: Path, body: bytes) -> None:
    # Written whole, then moved: a reader must never find half a file.
    handle = tempfile.NamedTemporaryFile(
        "wb", dir=path.parent, prefix=".seed-", suffix=".tmp", delete=False
    )
    try:
        with handle as writing:
            writing.write(body)
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def _log(line: str) -> None:
    # A closed stderr must not undo a seed that was already used.
    try:
        print(line, file=sys.stderr, flush=True)
    except (OSError, ValueError):
        pass


def record_for(job: HarnessJob) -> dict[str, Any] | None:
    """What a reading for ``job`` was made from, or None when nothing pins its source."""
    # A provider's own definition can change behind the same job, so no reading of it is reused.
    if job.agent.mode in {
        ProviderExecutionMode.PROVIDER_IMPORT,
        ProviderExecutionMode.CONNECT_ONLY,
    }:
        return None
    source = job.source
    if source.kind is SourceKind.GITHUB and source.repository and source.commit_sha:
        made_from: dict[str, Any] = {
            "kind": "github",
            "repository": source.repository,
            # The platform may send either case for the same commit.
            "commit_sha": source.commit_sha.lower(),
        }
    elif source.kind is SourceKind.ARCHIVE and source.archive_artifact_id:
        made_from = {
            "kind": "archive",
            "archive_artifact_id": source.archive_artifact_id,
        }
    else:
        return None
    evals = job.metadata.get("available_evals")
    return {
        "version": 1,
        "source": made_from,
        "subfolder": source.subfolder or "",
        "evals_sha256": (
            hashlib.sha256(_canon(evals)).hexdigest()
            if isinstance(evals, list)
            else None
        ),
        "generic_harness_v1": job.metadata.get("generic_harness_v1") is True,
    }


def _splice(raw: bytes, record: dict[str, Any], *, empty: bool) -> bytes:
    # Every byte the reading saved is kept, so the world is still built from its own rows.
    start = raw.index(b"{") + 1
    entry = (
        b'"'
        + RECORD_KEY.encode()
        + b'": '
        + json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode()
    )
    return raw[:start] + entry + (b"" if empty else b", ") + raw[start:]


def record_reading(job: HarnessJob, output: Path) -> None:
    """Put what the reading was made from into its saved contract."""
    path = Path(output) / _CONTRACT
    # Sized before it is read: the reading's own size check refuses what is over.
    if path.stat().st_size > SEED_LIMIT_BYTES:
        return
    record = record_for(job)
    if record is None:
        return
    raw = path.read_bytes()
    body = json.loads(raw.decode("utf-8"))
    if not isinstance(body, dict) or RECORD_KEY in body:
        return
    spliced = _splice(raw, record, empty=not body)
    # A reading at the edge stays as it was rather than crossing the platform's cap.
    if len(spliced) > SEED_LIMIT_BYTES:
        return
    _write_whole(path, spliced)


def differs(body: dict[str, Any], job: HarnessJob) -> list[str]:
    """The names that keep this reading from being reused for ``job``; empty when it matches."""
    record = body.get(RECORD_KEY)
    if not isinstance(record, dict):
        return [RECORD_KEY]
    version = record.get("version")
    if type(version) is not int or version != 1:
        return ["version"]
    expected = record_for(job)
    if expected is None:
        return ["source"]
    # Compared as JSON, so true and 1, or a missing name and null, never pass for each other.
    names = [
        name
        for name in _COMPARED
        if name not in record or _canon(record[name]) != _canon(expected[name])
    ]
    runtime = body.get("runtime")
    folder = (runtime if isinstance(runtime, dict) else {}).get("source_subfolder")
    if (folder or "") != (job.source.subfolder or ""):
        names.append("contract_subfolder")
    return names


def read_seed(
    path: str | Path,
) -> tuple[dict[str, Any] | None, Problem | None, bytes]:
    """The seed's body, or why it can't be used, and the exact bytes the body came from."""
    try:
        # Never follows a link and never waits on a pipe: the file is the platform's to place.
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None, _unreadable(path), b""
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            return (
                None,
                Problem(
                    SEED_CONTRACT_INVALID,
                    f"The seed contract at {path} is not a regular file.",
                    {"reason": "not_a_file"},
                ),
                b"",
            )
        if info.st_size > SEED_LIMIT_BYTES:
            return None, _too_large(info.st_size), b""
        chunks: list[bytes] = []
        held = 0
        while held <= SEED_LIMIT_BYTES:
            chunk = os.read(descriptor, SEED_LIMIT_BYTES + 1 - held)
            if not chunk:
                break
            chunks.append(chunk)
            held += len(chunk)
    except OSError:
        return None, _unreadable(path), b""
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    # It can grow after it was sized.
    if len(raw) > SEED_LIMIT_BYTES:
        return None, _too_large(len(raw)), b""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return (
            None,
            Problem(
                SEED_CONTRACT_INVALID,
                "The seed contract is not JSON.",
                {"reason": "not_json"},
            ),
            b"",
        )
    if not isinstance(body, dict):
        return (
            None,
            Problem(
                SEED_CONTRACT_INVALID,
                "The seed contract is not a JSON object.",
                {"reason": "not_an_object"},
            ),
            b"",
        )
    return body, None, raw


def _unreadable(path: str | Path) -> Problem:
    return Problem(
        SEED_CONTRACT_INVALID,
        f"The seed contract at {path} could not be opened.",
        {"reason": "unreadable"},
    )


def _too_large(size: int) -> Problem:
    return Problem(
        SEED_CONTRACT_TOO_LARGE,
        "The seed contract is larger than the 5 MiB a reading may save.",
        {"contract_bytes": size},
    )


def _refuse(output: str | Path, problem: Problem) -> int:
    write_failure_file(
        HarnessFailure(
            domain=FailureDomain.PLATFORM_SYNC,
            stage=HarnessStage.UNDERSTANDING_AGENT,
            code=problem.code,
            message=problem.message,
            retryable=False,
            details=problem.details,
        ),
        Path(output),
    )
    reason = problem.details.get("reason")
    _log(f"seed contract: refused; {problem.code}" + (f"; {reason}" if reason else ""))
    return EXIT_REFUSED


def accept_seed(path: str | Path, output: str | Path) -> int | None:
    """Refuse a seed the build could not use; ``None`` once it is accepted and marked."""
    expected = os.path.normpath(os.path.abspath(seed_beside(output)))
    # Refused loudly: a seed ignored in silence would cost a reading on every build.
    if os.path.normpath(os.path.abspath(path)) != expected:
        return _refuse(
            output,
            Problem(
                SEED_CONTRACT_INVALID,
                f"The seed contract must be at {expected}, where the build looks for it.",
                {"reason": "wrong_path"},
            ),
        )
    _, problem, _ = read_seed(expected)
    if problem is not None:
        return _refuse(output, problem)
    folder = Path(output)
    folder.mkdir(parents=True, exist_ok=True)
    # Only this mark lets the builder take the seed, so it comes after every check.
    _write_whole(folder / SEED_ACCEPTED, b"")
    return None


def use_seed(seed: str | Path, job: HarnessJob, source: Path, output: Path) -> str:
    """Install the seed as the build's contract when it matches ``job``, and say what happened.

    Never fails the build: whatever it can't use is read again, as a build without one does.
    """
    from pydantic import ValidationError

    from .contract import AgentContract, validate_contract

    output = Path(output)
    contract = output / _CONTRACT
    source_model = output / _SOURCE_MODEL
    names: list[str] = []
    problems = 0
    failed = ""
    try:
        had_model = source_model.is_file()
        body, problem, raw = read_seed(seed)
        if problem is not None or body is None:
            # It was checked when it was accepted, so it changed since.
            result = "unreadable"
        else:
            names = differs(body, job)
            if names:
                result = "mismatch"
            else:
                try:
                    # The checks a reading passes before it may save, so an older
                    # reading this harness would no longer save is read again.
                    problems = len(
                        validate_contract(AgentContract.model_validate(body))
                    )
                except ValidationError as exc:
                    # Only the count: the errors repeat the seed's values.
                    problems = exc.error_count()
                if problems:
                    result = "rejected"
                else:
                    from .cli import after_contract

                    output.mkdir(parents=True, exist_ok=True)
                    _write_whole(contract, raw)
                    try:
                        after_contract(output, source, job)
                        result = "used"
                    except Exception as exc:
                        _take_back(contract, source_model, had_model)
                        failed = type(exc).__name__
                        result = "setup_failed"
    except Exception as exc:
        # Nothing is installed by then: a failed write leaves no contract behind.
        failed = type(exc).__name__
        result = "error"
    _log(
        f"seed contract: {result}"
        + (f"; differs: {', '.join(names)}" if names else "")
        + (f"; problems: {problems}" if problems else "")
        + (f"; class: {failed}" if failed else "")
    )
    return result


def _take_back(contract: Path, source_model: Path, had_model: bool) -> None:
    # A half-installed seed would open the world stage on a contract nothing followed up.
    try:
        contract.unlink(missing_ok=True)
        if not had_model:
            source_model.unlink(missing_ok=True)
    except OSError:
        pass
