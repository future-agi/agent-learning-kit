"""One ALK harness executor used by the local CLI and hosted sandbox workers."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
from typing import Callable, Protocol

from .job import HarnessJob, HarnessJobStatus, HarnessStage


class SourceAcquirer(Protocol):
    """Materialize job source inside an executor-owned ephemeral workspace."""

    async def acquire(self, job: HarnessJob, workspace: Path) -> Path: ...


class SourceAcquisitionError(RuntimeError):
    pass


class GitHubSourceAcquirer:
    """Clone one platform-authorized repository without exposing its token in argv or logs."""

    _REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

    def __init__(self, installation_token: Callable[[str], str]) -> None:
        self._installation_token = installation_token

    async def acquire(self, job: HarnessJob, workspace: Path) -> Path:
        source = job.source
        repository = str(source.repository or "")
        installation_id = str(source.installation_id or "")
        if not self._REPOSITORY.fullmatch(repository):
            raise SourceAcquisitionError("github_repository_invalid")
        if not installation_id:
            raise SourceAcquisitionError("github_installation_missing")
        token = self._installation_token(installation_id)
        if not token:
            raise SourceAcquisitionError("github_installation_token_missing")
        workspace = workspace.expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        destination = workspace / "repository"
        if destination.exists():
            raise SourceAcquisitionError(f"source_destination_not_empty: {destination}")

        command = ["git", "clone", "--depth", "1"]
        if source.ref:
            command.extend(["--branch", source.ref])
        command.extend([f"https://github.com/{repository}.git", str(destination)])
        # Git reads the authorization header from its child environment. It never appears in
        # the process command, exception, persisted job, or event stream.
        environment = {
            **os.environ,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Bearer {token}",
            "GIT_TERMINAL_PROMPT": "0",
        }

        def clone() -> None:
            try:
                completed = subprocess.run(
                    command,
                    env=environment,
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SourceAcquisitionError(
                    f"github_clone_unavailable: {type(exc).__name__}"
                ) from exc
            if completed.returncode:
                detail = (
                    completed.stderr or completed.stdout or "clone failed"
                ).strip()
                # Git errors should not contain an env-only header, but redact defensively.
                detail = detail.replace(token, "[REDACTED]")[:1000]
                raise SourceAcquisitionError(f"github_clone_failed: {detail}")

        await asyncio.to_thread(clone)
        if not destination.is_dir():
            raise SourceAcquisitionError("github_clone_missing_checkout")
        return destination


class HarnessExecutor:
    """Execute the autonomous pipeline without platform or scheduler dependencies.

    A hosted worker first resolves a GitHub installation/archive/image through its
    ``SourceAcquirer``. A local invocation already has a path. From that point onward both call
    exactly the same ``auto`` pipeline and produce the same job, bundle, event, scenario, trace,
    and result artifacts.
    """

    async def run(
        self,
        job: HarnessJob,
        *,
        source: Path,
        output: Path,
        model: str | None = None,
        run_model: str | None = None,
    ) -> HarnessJobStatus:
        from .cli import _auto

        output = output.expanduser().resolve()
        source = source.expanduser().resolve()
        args = argparse.Namespace(
            path=str(source),
            name=str(job.metadata.get("agent_name") or source.name),
            kind=str(job.metadata.get("source_kind") or "repo"),
            out=str(output),
            count=job.scenario_count,
            model=model,
            run_model=run_model,
            job=job,
        )
        status = await _auto(args)
        return HarnessJobStatus(
            job_id=job.job_id,
            run_id=job.run_id,
            stage=HarnessStage.COMPLETED if status in (0, 2) else HarnessStage.FAILED,
            updated_at=datetime.now(timezone.utc),
            detail=(
                "agent checks failed"
                if status == 2
                else None
                if status == 0
                else f"exit {status}"
            ),
            completed_scenarios=job.scenario_count if status in (0, 2) else 0,
            total_scenarios=job.scenario_count,
        )

    async def acquire_and_run(
        self,
        job: HarnessJob,
        *,
        acquirer: SourceAcquirer,
        workspace: Path,
        output: Path,
        model: str | None = None,
        run_model: str | None = None,
    ) -> HarnessJobStatus:
        source = await acquirer.acquire(job, workspace)
        return await self.run(
            job,
            source=source,
            output=output,
            model=model,
            run_model=run_model,
        )


def run_sync(job: HarnessJob, *, source: Path, output: Path) -> HarnessJobStatus:
    """Small synchronous adapter for job consumers that do not own an event loop."""
    return asyncio.run(HarnessExecutor().run(job, source=source, output=output))


__all__ = [
    "GitHubSourceAcquirer",
    "HarnessExecutor",
    "SourceAcquirer",
    "SourceAcquisitionError",
    "run_sync",
]
