from __future__ import annotations

import json
import stat
from pathlib import Path

from fi.alk.harness.failure_file import FAILURE_FILE, write_failure_file
from fi.alk.harness.job import FailureDomain, HarnessFailure, HarnessStage


def _failure(code: str = "subfolder_not_found") -> HarnessFailure:
    return HarnessFailure(
        domain=FailureDomain.AGENT,
        stage=HarnessStage.ACQUIRING_SOURCE,
        code=code,
        message="The folder is not there.",
        retryable=False,
        details={"subfolder": "agents/x"},
    )


def test_writer_creates_folder(tmp_path: Path) -> None:
    output = tmp_path / "work" / "authoring"
    path = write_failure_file(_failure(), output)
    assert path == output / FAILURE_FILE
    assert path.is_file()


def test_writer_writes_full_shape(tmp_path: Path) -> None:
    path = write_failure_file(_failure(), tmp_path)
    assert json.loads(path.read_text()) == {
        "domain": "agent",
        "stage": "acquiring_source",
        "code": "subfolder_not_found",
        "message": "The folder is not there.",
        "retryable": False,
        "details": {"subfolder": "agents/x"},
    }
    assert not list(tmp_path.glob(".failure-*"))


def test_first_failure_stands(tmp_path: Path) -> None:
    write_failure_file(_failure("first"), tmp_path)
    write_failure_file(_failure("second"), tmp_path)
    assert json.loads((tmp_path / FAILURE_FILE).read_text())["code"] == "first"
    assert not list(tmp_path.glob(".failure-*"))


def test_file_is_world_readable(tmp_path: Path) -> None:
    path = write_failure_file(_failure(), tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
