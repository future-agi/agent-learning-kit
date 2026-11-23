"""A design inside the hosted sandbox: the job's control process.

It reads the job's `metadata.design` (a design has no agent and no source, so not through
`HarnessJob`), unpacks the template the platform delivered under `--releases` into the workspace
when there is one, and waits while the conversation's builder designs, exactly as an adaptation's
control process waits. The builder's `finish_design`, `stop_design` or a parked question writes
`design-output/result.json` and the finished marker; this process turns that into the exit code.

A resumed design (`resume_from`) starts from the checkpointed workspace the platform uploaded, so
the design is already there and is kept.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..releases import (
    _DIGEST_PATTERN,
    _SLUG_PATTERN,
    RELEASES_SUITE,
    ReleaseError,
    environment_directory,
)
from .reviewed_sources import document_manifest
from .tools import FINISHED_MARKER, RESULT
from .workspace import DocumentRecord, marker_matches, open_design

OUTPUT = "design-output"


def ready_marker(workspace: Path) -> Path:
    return workspace.parent / f".{workspace.name}-design-ready"


async def wait_for_design(workspace: Path, attempt_id: str) -> None:
    for _ in range(150):
        if marker_matches(ready_marker(workspace), attempt_id):
            return
        if marker_matches(workspace / FINISHED_MARKER, attempt_id):
            raise RuntimeError("The design control process could not open the design.")
        await asyncio.sleep(2)
    raise RuntimeError("The design control process has not opened the design.")


class _Release(BaseModel):
    # The platform also names the release and its archive; the digest is what is unpacked.
    model_config = ConfigDict(extra="ignore")

    digest: str = Field(pattern=_DIGEST_PATTERN)


class DesignTemplateRef(BaseModel):
    """The template as the platform staged it: its environment chain (a root release, so one)
    and the suite proved against it, under `--releases`."""

    model_config = ConfigDict(extra="ignore")

    environment: list[_Release] = Field(min_length=1, max_length=1)
    suite: _Release


class DesignDocumentRef(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(min_length=1)
    name: str
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size: int = Field(ge=0)


class DesignJob(BaseModel):
    """`metadata.design` of a hosted design job, written by the platform."""

    model_config = ConfigDict(extra="ignore")

    attempt_id: str = Field(min_length=1)
    name: str = Field(pattern=_SLUG_PATTERN)
    requirements: str = ""
    modality: Literal["chat", "voice"] = "chat"
    template: DesignTemplateRef | None = None
    resume_from: str | None = None
    documents: list[DesignDocumentRef] = Field(default_factory=list)


def design_job(job: dict) -> DesignJob:
    return DesignJob.model_validate((job.get("metadata") or {}).get("design"))


def failed(
    output: Path,
    workspace: Path,
    code: str,
    message: str,
    *,
    attempt_id: str | None = None,
) -> int:
    workspace.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    (output / RESULT).write_text(
        json.dumps(
            {
                "status": "failed",
                "failure": {"code": code, "message": message},
                **({"attempt_id": attempt_id} if attempt_id is not None else {}),
            }
        ),
        encoding="utf-8",
    )
    (workspace / FINISHED_MARKER).write_text(
        json.dumps({"status": "failed", "attempt_id": attempt_id}), encoding="utf-8"
    )
    print(f"design failed: {code}: {message}")
    return 2


def clear_source_failure(output: Path, workspace: Path) -> None:
    try:
        result = json.loads((output / RESULT).read_bytes())
    except (OSError, ValueError):
        return
    if result.get("status") == "failed" and (result.get("failure") or {}).get(
        "code", ""
    ).startswith("source_"):
        (output / RESULT).unlink()
        (workspace / FINISHED_MARKER).unlink(missing_ok=True)


def run_hosted(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace)
    output = workspace / OUTPUT
    try:
        payload = json.loads(Path(args.job).read_text(encoding="utf-8"))
        job = design_job(payload)
    except (OSError, ValueError, ValidationError) as exc:
        return failed(output, workspace, "design_job_invalid", str(exc)[:600])
    releases = Path(args.releases)
    template = (
        (
            environment_directory(releases, job.template.environment[0].digest),
            releases / RELEASES_SUITE,
        )
        if job.template is not None
        else None
    )
    try:
        documents = [
            DocumentRecord(
                id=doc.id,
                name=doc.name,
                file=doc.name,
                sha256=doc.sha256,
                size=doc.size,
            )
            for doc in job.documents
        ]
        if not documents:
            documents = document_manifest(Path(args.documents), workspace=workspace)
        open_design(
            workspace,
            name=job.name,
            requirements=job.requirements,
            modality=job.modality,
            template=template,
            documents=documents,
            scenario_count=payload.get("scenario_count", 10),
            reviewed_snapshot=args.reviewed_snapshot,
            source_review_receipt=payload["metadata"].get("source_review_receipt"),
        )
    except ReleaseError as exc:
        return failed(
            output, workspace, exc.code, exc.detail[:600], attempt_id=job.attempt_id
        )
    ready_marker(workspace).write_text(
        json.dumps({"attempt_id": job.attempt_id}), encoding="utf-8"
    )
    deadline = time.monotonic() + int(
        os.environ.get("ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS") or 3300
    )
    while time.monotonic() < deadline:
        if marker_matches(
            workspace / FINISHED_MARKER, job.attempt_id
        ) and marker_matches(output / RESULT, job.attempt_id):
            result = json.loads((output / RESULT).read_text(encoding="utf-8"))
            print(
                json.dumps(
                    {"status": result.get("status"), "failure": result.get("failure")}
                )
            )
            return 0 if result.get("status") == "completed" else 2
        time.sleep(5)
    return failed(
        output,
        workspace,
        "design_unfinished",
        "the design did not finish in time",
        attempt_id=job.attempt_id,
    )
