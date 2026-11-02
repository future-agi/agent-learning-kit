"""The failure a build leaves for the platform before it exits non-zero."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .job import HarnessFailure

FAILURE_FILE = "failure.json"


def write_failure_file(failure: HarnessFailure, output: Path) -> Path:
    """Write ``failure`` whole, in one step. The first failure of a build stands."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / FAILURE_FILE
    with tempfile.NamedTemporaryFile(
        "w", dir=output, prefix=".failure-", suffix=".tmp", encoding="utf-8", delete=False
    ) as temporary:
        json.dump(failure.model_dump(mode="json"), temporary, indent=2, sort_keys=True)
        temporary.write("\n")
    try:
        os.chmod(temporary.name, 0o644)
        os.link(temporary.name, path)
    except FileExistsError:
        pass
    finally:
        os.unlink(temporary.name)
    return path
