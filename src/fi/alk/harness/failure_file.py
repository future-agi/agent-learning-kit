"""The failure a build leaves for the platform before it exits non-zero."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .job import HarnessFailure

FAILURE_FILE = "failure.json"


def _write_in_place(path: Path, body: str) -> None:
    """For a disk with no hard links: still first-wins, but a reader could catch it part-written.

    A disk that refuses ``chmod`` keeps the umask's mode: a whole file beats none.
    """
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(body)
    except OSError:
        path.unlink(missing_ok=True)
        raise
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def write_failure_file(failure: HarnessFailure, output: Path) -> Path:
    """Write ``failure``. The first failure of a build stands.

    Whole, in one step, on a disk with hard links. On one without, it's written in place, where a
    reader could catch it part-written.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / FAILURE_FILE
    body = json.dumps(failure.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    temporary = tempfile.NamedTemporaryFile(
        "w", dir=output, prefix=".failure-", suffix=".tmp", encoding="utf-8", delete=False
    )
    try:
        with temporary:
            temporary.write(body)
        try:
            os.chmod(temporary.name, 0o644)
            os.link(temporary.name, path)
        except FileExistsError:
            pass
        except OSError:
            # No file reads as a crash, which pays for another full run to reach this same failure.
            _write_in_place(path, body)
    finally:
        os.unlink(temporary.name)
    return path
