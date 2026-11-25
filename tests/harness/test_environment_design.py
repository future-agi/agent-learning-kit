"""Designing an environment: resuming, customizing a template, documents and the local command."""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness.design import hosted
from fi.alk.harness.design.hosted import OUTPUT
from fi.alk.harness.design.tools import (
    APPROVE,
    FINISHED_MARKER,
    PENDING_QUESTION,
    RESULT,
    settle_proposal,
)
from fi.alk.harness.design.workspace import (
    Proposal,
    load_design,
    open_design,
    save_design,
)
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
from fi.alk.harness.hosted_conversation import _workspace_archive

PROPOSAL = Proposal(one_liner="A desk", summary="It books.", use_cases=["booking"])


# --- a hosted design that parked and is continued ------------------------------------------------


def _parked_checkpoint(workspace: Path) -> bytes:
    """A design that asked for approval, parked, and whose ending turn checkpointed it."""
    design = open_design(workspace, name="desk")
    save_design(workspace, design.model_copy(update={"pending_proposal": PROPOSAL}))
    (workspace / PENDING_QUESTION).write_text(
        json.dumps({"message_id": "m1", "question": "Approve?", "options": []})
    )

    async def nothing() -> None:
        return None

    runtime = SimpleNamespace(
        workspace=workspace, backend_name="", _checkpoint=nothing
    )
    asyncio.run(HostedChatRuntime._park_design(runtime, []))
    return _workspace_archive(workspace)


def _hosted_args(tmp_path: Path, workspace: Path) -> argparse.Namespace:
    job = tmp_path / "job.json"
    job.write_text(
        json.dumps({"metadata": {"design": {"name": "desk", "resume_from": "job-1"}}})
    )
    return argparse.Namespace(
        job=str(job),
        releases=str(tmp_path / "releases"),
        workspace=str(workspace),
        documents=str(tmp_path / "documents"),
    )


def test_a_continued_design_waits_for_its_answer_and_finishes(tmp_path, monkeypatch):
    archive = _parked_checkpoint(tmp_path / "first")
    workspace = tmp_path / "second"
    with tarfile.open(fileobj=io.BytesIO(archive)) as packed:
        packed.extractall(workspace, filter="data")
    assert (workspace / FINISHED_MARKER).is_file()
    stages = []

    def answered_while_waiting(_seconds: float) -> None:
        # The person answers, and the builder finishes the design.
        settle_proposal(workspace, APPROVE)
        stages.append(load_design(workspace).stage)
        (workspace / PENDING_QUESTION).unlink()
        (workspace / OUTPUT / RESULT).write_text(json.dumps({"status": "completed"}))
        (workspace / FINISHED_MARKER).write_text(json.dumps({"status": "completed"}))

    monkeypatch.setattr(hosted.time, "sleep", answered_while_waiting)

    assert hosted.run_hosted(_hosted_args(tmp_path, workspace)) == 0
    assert stages == ["world"]


def test_a_design_this_attempt_completed_is_reported_again(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    open_design(workspace, name="desk")
    (workspace / OUTPUT).mkdir()
    (workspace / OUTPUT / RESULT).write_text(json.dumps({"status": "completed"}))
    (workspace / FINISHED_MARKER).write_text(json.dumps({"status": "completed"}))
    monkeypatch.setattr(
        hosted.time, "sleep", lambda _seconds: pytest.fail("it is already finished")
    )

    assert hosted.run_hosted(_hosted_args(tmp_path, workspace)) == 0
