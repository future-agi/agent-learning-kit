"""Local implementation of the hosted ALK sandbox boundary.

The platform talks to this HTTP contract in development. Production can replace the
implementation with a Kubernetes or microVM service without changing platform job APIs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import signal
import sys
from typing import Any
import uuid

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, SecretStr, model_validator

from fi.simulate.runtime.spec import SecretRef

from .credentials import (
    CredentialManifest,
    CredentialRequirement,
    RequirementKind,
    RequirementStatus,
    discover_credentials,
)
from .executor import GitHubSourceAcquirer, SourceAcquisitionError
from .github import parse_github_location
from .job import (
    AgentConnection,
    ExecutionMode,
    HarnessArtifactPolicy,
    HarnessJob,
    HarnessJobStatus,
    HarnessStage,
    RepositorySource,
    SourceKind,
    SourceVisibility,
)
from .packaging import PackagingManifest, inspect_packaging
from .provision import ProvisionError, source_fingerprint, stop
from .secrets import resolve_worker_secrets, worker_environment


class LocalSandboxRequest(BaseModel):
    source_path: str | None = None
    source_id: str | None = None
    github_repository: str | None = None
    github_ref: str | None = None
    github_commit_sha: str | None = None
    github_visibility: SourceVisibility = SourceVisibility.PUBLIC
    github_installation_id: str | None = None
    scenario_count: int = Field(default=10, ge=1, le=100)
    seed: int | None = None
    agent_name: str | None = None
    connector: str = "auto"
    connector_config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, SecretRef] = Field(default_factory=dict)
    environment_values: dict[str, SecretStr] = Field(default_factory=dict)
    platform_run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_source(self) -> "LocalSandboxRequest":
        if (
            sum(
                bool(value)
                for value in (self.source_path, self.source_id, self.github_repository)
            )
            != 1
        ):
            raise ValueError("exactly_one_source_required")
        _validate_environment_values(self.environment_values, self.secret_refs)
        return self


class SandboxPreflightRequest(BaseModel):
    source_path: str | None = None
    source_id: str | None = None
    github_repository: str | None = None
    github_visibility: SourceVisibility = SourceVisibility.PUBLIC
    github_installation_id: str | None = None
    connector_config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, SecretRef] = Field(default_factory=dict)
    environment_values: dict[str, SecretStr] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_source(self) -> "SandboxPreflightRequest":
        if (
            sum(
                bool(value)
                for value in (self.source_path, self.source_id, self.github_repository)
            )
            != 1
        ):
            raise ValueError("exactly_one_source_required")
        _validate_environment_values(self.environment_values, self.secret_refs)
        return self


class SandboxPreflightResponse(BaseModel):
    source_kind: SourceKind
    source_label: str
    ready_to_submit: bool
    checkout_required: bool = False
    credentials: CredentialManifest
    packaging: PackagingManifest | None = None
    notes: list[str] = Field(default_factory=list)


class SandboxJobResponse(BaseModel):
    job: HarnessJob
    status: HarnessJobStatus
    events: list[dict[str, Any]] = Field(default_factory=list)
    artifact_path: str | None = None
    credentials: CredentialManifest | None = None


class UploadedSourceResponse(BaseModel):
    source_id: str
    name: str
    file_count: int
    total_bytes: int


class LocalSandbox:
    def __init__(
        self,
        root: Path,
        *,
        max_concurrency: int = 2,
        upload_root: Path | None = None,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.jobs_root = self.root / "jobs"
        self.artifacts_root = self.root / "artifacts"
        self.uploads_root = (
            (
                upload_root
                or Path(
                    os.getenv("ALK_SANDBOX_UPLOAD_ROOT", str(self.root / "uploads"))
                )
            )
            .expanduser()
            .resolve()
        )
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self.uploads_root.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Values supplied through the platform's .env flow live only for the lifetime of the
        # job. Persisted job/state artifacts contain the opaque mounted references created below.
        self._ephemeral_secrets: dict[str, dict[str, str]] = {}
        self._recover_orphans()

    async def upload_source(
        self, files: list[UploadFile], paths: list[str], name: str
    ) -> UploadedSourceResponse:
        if not files or len(files) != len(paths):
            raise HTTPException(
                status_code=400, detail="one relative path is required per file"
            )
        if len(files) > 5_000:
            raise HTTPException(
                status_code=413, detail="source may contain at most 5000 files"
            )
        source_id = str(uuid.uuid4())
        staging = self.uploads_root / f".{source_id}.uploading"
        destination = self.uploads_root / source_id
        staging.mkdir(mode=0o700)
        total = 0
        seen: set[str] = set()
        try:
            for uploaded, raw_path in zip(files, paths, strict=True):
                relative = _safe_uploaded_path(raw_path)
                key = relative.as_posix()
                if key in seen:
                    raise HTTPException(
                        status_code=400, detail=f"duplicate source path: {key}"
                    )
                seen.add(key)
                target = staging.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                file_size = 0
                starts_with_shebang = False
                with target.open("xb") as handle:
                    while chunk := await uploaded.read(1024 * 1024):
                        if file_size == 0:
                            starts_with_shebang = chunk.startswith(b"#!")
                        file_size += len(chunk)
                        total += len(chunk)
                        if file_size > 50 * 1024 * 1024 or total > 200 * 1024 * 1024:
                            raise HTTPException(
                                status_code=413,
                                detail="source exceeds the 50 MiB file or 200 MiB bundle limit",
                            )
                        handle.write(chunk)
                target.chmod(0o755 if starts_with_shebang else 0o644)
            manifest = {
                "source_id": source_id,
                "name": (name or "uploaded-agent")[:255],
                "file_count": len(files),
                "total_bytes": total,
                "created_at": _now(),
            }
            staging.replace(destination)
            _write_json(
                self.uploads_root / f"{source_id}.json",
                manifest,
            )
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            shutil.rmtree(destination, ignore_errors=True)
            (self.uploads_root / f"{source_id}.json").unlink(missing_ok=True)
            raise
        return UploadedSourceResponse(
            source_id=source_id,
            name=(name or "uploaded-agent")[:255],
            file_count=len(files),
            total_bytes=total,
        )

    def uploaded_source(self, source_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f-]{36}", source_id):
            raise HTTPException(status_code=400, detail="source_id is invalid")
        source = (self.uploads_root / source_id).resolve()
        if source.parent != self.uploads_root or not source.is_dir():
            raise HTTPException(status_code=404, detail="uploaded source was not found")
        if not (self.uploads_root / f"{source_id}.json").is_file():
            raise HTTPException(status_code=400, detail="uploaded source is incomplete")
        return source

    def _recover_orphans(self) -> None:
        for path in self.jobs_root.glob("*/state.json"):
            state = _read_json(path)
            if state.get("stage") not in _TERMINAL_STAGES:
                state.update(
                    stage=HarnessStage.FAILED.value,
                    detail="sandbox service restarted while the job was running",
                    updated_at=_now(),
                )
                _write_json(path, state)

    def submit(self, request: LocalSandboxRequest) -> SandboxJobResponse:
        source = (
            _allowed_source(request.source_path)
            if request.source_path
            else self.uploaded_source(request.source_id)
            if request.source_id
            else None
        )
        identifier = str(uuid.uuid4())
        run_id = f"harness-{identifier}"
        github_location = (
            _github_location(request.github_repository)
            if request.github_repository
            else None
        )
        github_repository = github_location.repository if github_location else None
        mounted_refs: dict[str, SecretRef] = {}
        mounted_values: dict[str, str] = {}
        for index, (name, value) in enumerate(request.environment_values.items()):
            internal_key = f"ALK_JOB_{identifier.replace('-', '').upper()}_{index}"
            mounted_refs[name] = SecretRef(
                manager="mounted",
                key=internal_key,
                purpose=f"job-scoped environment value for {name}",
            )
            mounted_values[internal_key] = value.get_secret_value()
        source_spec = (
            RepositorySource(
                kind=SourceKind.ARCHIVE,
                archive_artifact_id=request.source_id,
            )
            if request.source_id
            else RepositorySource(
                kind=SourceKind.LOCAL_REPOSITORY, local_path=str(source)
            )
            if source
            else RepositorySource(
                kind=SourceKind.GITHUB,
                repository=github_repository,
                ref=(github_location.ref if github_location else None)
                or request.github_ref,
                commit_sha=request.github_commit_sha,
                visibility=request.github_visibility,
                installation_id=request.github_installation_id,
            )
        )
        job = HarnessJob(
            job_id=identifier,
            run_id=run_id,
            execution=(
                ExecutionMode.HOSTED
                if request.source_id or request.github_repository
                else ExecutionMode.LOCAL
            ),
            source=source_spec,
            agent=AgentConnection(
                connector=request.connector,
                config=request.connector_config,
                secret_refs={**request.secret_refs, **mounted_refs},
            ),
            scenario_count=request.scenario_count,
            seed=request.seed,
            artifacts=HarnessArtifactPolicy(level="full", allow_bundle_download=True),
            platform_run_id=request.platform_run_id,
            metadata={
                **request.metadata,
                "agent_name": request.agent_name
                or (
                    _uploaded_source_name(source)
                    if request.source_id and source
                    else source.name
                    if source
                    else str(github_repository).split("/")[-1]
                ),
                "source_kind": source_spec.kind.value,
                "environment_value_names": sorted(mounted_refs),
            },
        )
        directory = self.jobs_root / identifier
        directory.mkdir()
        _write_json(directory / "job.json", job.model_dump(mode="json"))
        _write_json(
            directory / "state.json",
            {
                "job_id": identifier,
                "run_id": run_id,
                "stage": HarnessStage.QUEUED.value,
                "updated_at": _now(),
                "detail": "waiting for a local sandbox slot",
                "completed_scenarios": 0,
                "total_scenarios": request.scenario_count,
                "attempt": 1,
            },
        )
        if mounted_values:
            self._ephemeral_secrets[identifier] = mounted_values
        self._tasks[identifier] = asyncio.create_task(self._execute(job, source))
        return self.get(identifier)

    def preflight(self, request: SandboxPreflightRequest) -> SandboxPreflightResponse:
        if request.source_path or request.source_id:
            source = (
                _allowed_source(request.source_path)
                if request.source_path
                else self.uploaded_source(request.source_id or "")
            )
            packaging = inspect_packaging(source)
            manifest = discover_credentials(
                source,
                secret_refs=request.secret_refs,
                provided_environment={
                    **request.connector_config,
                    **{
                        name: value.get_secret_value()
                        for name, value in request.environment_values.items()
                    },
                },
                scan_paths=_credential_scan_paths(packaging),
            )
            return SandboxPreflightResponse(
                source_kind=(
                    SourceKind.ARCHIVE
                    if request.source_id
                    else SourceKind.LOCAL_REPOSITORY
                ),
                source_label=(
                    _uploaded_source_name(source) if request.source_id else str(source)
                ),
                ready_to_submit=(
                    manifest.ready
                    and packaging.ready
                    and (packaging.agent_runtime_packaged or not packaging.candidates)
                ),
                credentials=manifest,
                packaging=packaging,
            )
        location = _github_location(request.github_repository or "")
        repository = location.repository
        requirement = CredentialRequirement(
            id="github_installation",
            environment_name="GITHUB_INSTALLATION",
            provider="github",
            purpose="read private repository source",
            kind=RequirementKind.SECRET,
            required=request.github_visibility is SourceVisibility.PRIVATE,
            status=(
                RequirementStatus.CONFIGURED
                if request.github_installation_id
                else RequirementStatus.MISSING
                if request.github_visibility is SourceVisibility.PRIVATE
                else RequirementStatus.OPTIONAL
            ),
            accepted_secret_types=["github_app_installation"],
        )
        manifest = CredentialManifest(
            source_digest="0" * 64,
            detected_connectors=[],
            requirements=[requirement],
            scanned_files=0,
        )
        return SandboxPreflightResponse(
            source_kind=SourceKind.GITHUB,
            source_label=repository,
            ready_to_submit=manifest.ready,
            checkout_required=True,
            credentials=manifest,
            notes=[
                "Agent credential discovery continues inside the sandbox after checkout."
            ],
        )

    async def _execute(self, job: HarnessJob, source: Path | None) -> None:
        directory = self.jobs_root / job.job_id
        state_path = directory / "state.json"
        output = self.artifacts_root / job.run_id
        async with self._semaphore:
            state = _read_json(state_path)
            if state.get("stage") == HarnessStage.CANCELED.value:
                return
            state.update(
                stage=HarnessStage.ACQUIRING_SOURCE.value,
                detail="acquiring source inside the ALK runner",
                updated_at=_now(),
            )
            _write_json(state_path, state)
            log_handle = (directory / "worker.log").open("ab")
            try:
                if source is None:
                    workspace = directory / "workspace"
                    for attempt in range(1, job.retry.max_infrastructure_attempts + 1):
                        state.update(
                            attempt=attempt,
                            detail=f"cloning public GitHub source (attempt {attempt})",
                            updated_at=_now(),
                        )
                        _write_json(state_path, state)
                        try:
                            source = await GitHubSourceAcquirer(
                                _github_installation_token
                            ).acquire(job, workspace)
                            break
                        except SourceAcquisitionError:
                            checkout = workspace / "repository"
                            if checkout.exists():
                                shutil.rmtree(checkout)
                            if attempt >= job.retry.max_infrastructure_attempts:
                                raise
                            delay = min(
                                job.retry.max_backoff_seconds,
                                job.retry.initial_backoff_seconds
                                * (2 ** (attempt - 1)),
                            )
                            await asyncio.sleep(delay)
                    if source is None:
                        raise SourceAcquisitionError("github_checkout_missing")
                packaging_manifest = inspect_packaging(source)
                credential_manifest = discover_credentials(
                    source,
                    secret_refs=job.agent.secret_refs,
                    provided_environment=job.agent.config,
                    scan_paths=_credential_scan_paths(packaging_manifest),
                )
                _write_json(
                    directory / "packaging.json",
                    packaging_manifest.model_dump(mode="json"),
                )
                if not packaging_manifest.ready:
                    detail = (
                        "; ".join(packaging_manifest.notes)
                        or "packaging is not runnable"
                    )
                    state.update(
                        stage=HarnessStage.FAILED.value,
                        detail=f"repository packaging preflight failed: {detail}",
                        failure={
                            "domain": "environment",
                            "stage": HarnessStage.ACQUIRING_SOURCE.value,
                            "code": "packaging_preflight_failed",
                            "message": detail,
                            "retryable": False,
                        },
                        updated_at=_now(),
                    )
                    _write_json(state_path, state)
                    return
                _write_json(
                    directory / "credentials.json",
                    credential_manifest.model_dump(mode="json"),
                )
                if not credential_manifest.ready:
                    missing = [
                        item.environment_name
                        for item in credential_manifest.missing_required
                    ]
                    missing.extend(
                        f"one of {choice.options}"
                        for choice in credential_manifest.credential_choices
                        if not choice.satisfied
                    )
                    names = ", ".join(missing)
                    state.update(
                        stage=HarnessStage.FAILED.value,
                        detail=f"missing required credentials: {names}",
                        failure={
                            "domain": "connectivity",
                            "stage": HarnessStage.ACQUIRING_SOURCE.value,
                            "code": "credentials_missing",
                            "message": f"Configure secret references for: {names}",
                            "retryable": False,
                        },
                        updated_at=_now(),
                    )
                    _write_json(state_path, state)
                    return
                resolved_secrets = resolve_worker_secrets(
                    job.agent.secret_refs,
                    environment={
                        **os.environ,
                        **self._ephemeral_secrets.get(job.job_id, {}),
                    },
                )
                submitted_source_digest = source_fingerprint(source)
                result: dict[str, Any] = {}
                return_code = 1
                for worker_attempt in range(
                    1, job.retry.max_infrastructure_attempts + 1
                ):
                    state.update(
                        attempt=worker_attempt,
                        detail=f"running isolated harness worker (attempt {worker_attempt})",
                        updated_at=_now(),
                    )
                    _write_json(state_path, state)
                    if worker_attempt > 1 and output.exists():
                        archived = directory / f"failed-attempt-{worker_attempt - 1}"
                        if archived.exists():
                            shutil.rmtree(archived)
                        output.replace(archived)
                    runtime_names = {
                        str(name)
                        for name in job.metadata.get("environment_value_names", [])
                    }
                    runtime_configuration = {
                        name: value
                        for name, value in resolved_secrets.items()
                        if name in runtime_names
                    }
                    controller_configuration = {
                        **_configuration_environment(job.agent.config),
                        **{
                            name: value
                            for name, value in resolved_secrets.items()
                            if name not in runtime_names
                        },
                    }
                    child_environment = worker_environment(
                        controller_configuration,
                        runtime_configuration=runtime_configuration,
                    )
                    # The provisioner must explicitly pass customer-provided values into
                    # Dockerfile/generated runtime containers.  Persist names only; values stay
                    # in this child process environment and never enter the job or bundle.
                    child_environment["ALK_RUNTIME_CONFIGURATION_NAMES"] = ",".join(
                        sorted(runtime_configuration)
                    )
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "fi.alk.harness.sandbox_worker",
                        str(directory / "job.json"),
                        "--source",
                        str(source),
                        "--output",
                        str(output),
                        "--status",
                        str(directory / "result.json"),
                        stdout=log_handle,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                        env=child_environment,
                    )
                    self._processes[job.job_id] = process
                    return_code = await process.wait()
                    result = _read_json(directory / "result.json")
                    if source_fingerprint(source) != submitted_source_digest:
                        return_code = 1
                        result = {
                            "stage": HarnessStage.FAILED.value,
                            "detail": "submitted source changed during isolated execution",
                            "failure": {
                                "domain": "infrastructure",
                                "stage": HarnessStage.CLEANING_UP.value,
                                "code": "source_mutation_detected",
                                "message": (
                                    "The runner detected writes to the read-only source tree"
                                ),
                                "retryable": False,
                            },
                        }
                    failure = result.get("failure") or {}
                    retryable = _worker_failure_retryable(
                        return_code,
                        failure,
                        job.retry.retryable_domains,
                    )
                    if (
                        not retryable
                        or worker_attempt >= job.retry.max_infrastructure_attempts
                    ):
                        break
                    delay = min(
                        job.retry.max_backoff_seconds,
                        job.retry.initial_backoff_seconds * (2 ** (worker_attempt - 1)),
                    )
                    state.update(
                        detail=(
                            f"retrying {failure.get('domain', 'infrastructure')} "
                            f"failure in {delay:g}s"
                        ),
                        updated_at=_now(),
                    )
                    _write_json(state_path, state)
                    await asyncio.sleep(delay)
                state = _read_json(state_path)
                if state.get("stage") != HarnessStage.CANCELED.value:
                    state.update(
                        stage=result.get(
                            "stage",
                            HarnessStage.COMPLETED.value
                            if return_code == 0
                            else HarnessStage.FAILED.value,
                        ),
                        detail=result.get("detail")
                        or (
                            None if return_code == 0 else f"worker exited {return_code}"
                        ),
                        completed_scenarios=result.get("completed_scenarios", 0),
                        failure=result.get("failure"),
                        attempt=state.get("attempt", 1),
                        updated_at=_now(),
                    )
                    _write_json(state_path, state)
            except Exception as exc:
                state = _read_json(state_path)
                domain = (
                    "connectivity"
                    if isinstance(exc, SourceAcquisitionError)
                    else "infrastructure"
                )
                state.update(
                    stage=HarnessStage.FAILED.value,
                    detail=f"{type(exc).__name__}: {exc}",
                    failure={
                        "domain": domain,
                        "stage": HarnessStage.ACQUIRING_SOURCE.value,
                        "code": type(exc).__name__,
                        "message": str(exc),
                        "retryable": isinstance(exc, SourceAcquisitionError),
                    },
                    updated_at=_now(),
                )
                _write_json(state_path, state)
            finally:
                log_handle.close()
                self._processes.pop(job.job_id, None)
                self._tasks.pop(job.job_id, None)
                self._ephemeral_secrets.pop(job.job_id, None)

    def get(self, job_id: str) -> SandboxJobResponse:
        directory = self.jobs_root / job_id
        if not directory.is_dir():
            raise KeyError(job_id)
        job = HarnessJob.model_validate(_read_json(directory / "job.json"))
        raw_state = _read_json(directory / "state.json")
        events = _events(self.artifacts_root / job.run_id / "harness-events.jsonl")
        event_stage = _stage_from_events(events)
        stage = event_stage or raw_state.get("stage", "queued")
        updated_at = raw_state.get("updated_at", _now())
        detail = raw_state.get("detail")
        if event_stage and events:
            # While the worker is alive, state.json is intentionally written only
            # at process boundaries.  The canonical event stream is the live
            # source of truth, so expose its timestamp and stage instead of making
            # a healthy long-running job look stale in the platform UI.
            updated_at = events[-1].get("wall_time") or updated_at
            detail = {
                HarnessStage.UNDERSTANDING_AGENT.value: "understanding agent source",
                HarnessStage.GENERATING_ENVIRONMENT.value: "provisioning and validating environment",
                HarnessStage.GENERATING_SCENARIOS.value: "generating and validating scenarios",
                HarnessStage.RUNNING.value: "running scenarios",
            }.get(event_stage, detail)
        if raw_state.get("stage") in _TERMINAL_STAGES:
            stage = raw_state["stage"]
            updated_at = raw_state.get("updated_at", updated_at)
            detail = raw_state.get("detail")
        status_value = HarnessJobStatus(
            job_id=job.job_id,
            run_id=job.run_id,
            stage=stage,
            updated_at=updated_at,
            detail=detail,
            failure=raw_state.get("failure"),
            completed_scenarios=raw_state.get("completed_scenarios", 0),
            total_scenarios=raw_state.get("total_scenarios", job.scenario_count),
            attempt=raw_state.get("attempt", 1),
        )
        return SandboxJobResponse(
            job=job,
            status=status_value,
            events=events,
            artifact_path=str(self.artifacts_root / job.run_id),
            credentials=(
                CredentialManifest.model_validate(
                    _read_json(directory / "credentials.json")
                )
                if (directory / "credentials.json").is_file()
                else None
            ),
        )

    def list(self) -> list[SandboxJobResponse]:
        responses = []
        for directory in sorted(
            self.jobs_root.iterdir(),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        ):
            if directory.is_dir():
                responses.append(self.get(directory.name))
        return responses

    async def cancel(self, job_id: str) -> SandboxJobResponse:
        response = self.get(job_id)
        if response.status.stage.terminal:
            return response
        state_path = self.jobs_root / job_id / "state.json"
        state = _read_json(state_path)
        state.update(
            stage=HarnessStage.CANCELED.value,
            detail="canceled by user",
            updated_at=_now(),
        )
        _write_json(state_path, state)
        process = self._processes.get(job_id)
        if process and process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        # SIGTERM stops the worker before its async ``finally`` can reliably run.  Cancellation
        # must therefore own the same environment boundary explicitly; otherwise every timed-out
        # hosted job leaves its database, network and volumes behind.
        output = self.artifacts_root / response.job.run_id
        try:
            await asyncio.to_thread(stop, output)
        except ProvisionError as exc:
            state.update(
                detail=f"canceled; isolated environment cleanup failed: {exc}",
                updated_at=_now(),
            )
            _write_json(state_path, state)
        self._ephemeral_secrets.pop(job_id, None)
        return self.get(job_id)


def _configuration_environment(config: dict[str, Any]) -> dict[str, str]:
    """Convert explicit, non-secret connector configuration into child env vars."""
    result: dict[str, str] = {}
    for name, value in config.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", str(name)):
            continue
        if isinstance(value, bool):
            result[str(name)] = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            result[str(name)] = str(value)
    return result


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RUNNER_RESERVED_ENVIRONMENT = {
    "DOCKER_HOST",
    "FI_API_KEY",
    "FI_BASE_URL",
    "FI_SECRET_KEY",
    "HARNESS_PLATFORM_API_KEY",
    "HARNESS_PLATFORM_SECRET_KEY",
    "HARNESS_PLATFORM_URL",
    "HARNESS_WEBHOOK_HOST",
    "HARNESS_WEBHOOK_PORT",
    "HOME",
    "PATH",
    "PYTHONPATH",
}


def _validate_environment_values(
    values: dict[str, SecretStr], references: dict[str, SecretRef]
) -> None:
    if len(values) > 256:
        raise ValueError("environment_values_limit_exceeded")
    if set(values) & set(references):
        raise ValueError("environment_value_conflicts_with_secret_reference")
    total = 0
    for name, secret in values.items():
        if not _ENVIRONMENT_NAME.fullmatch(name):
            raise ValueError(f"environment_name_invalid: {name}")
        if name in _RUNNER_RESERVED_ENVIRONMENT or name.startswith("ALK_"):
            raise ValueError(f"environment_name_reserved: {name}")
        value = secret.get_secret_value()
        if "\x00" in value:
            raise ValueError(f"environment_value_contains_nul: {name}")
        total += len(name.encode()) + len(value.encode())
    if total > 262_144:
        raise ValueError("environment_values_size_exceeded")


def _credential_scan_paths(packaging: PackagingManifest) -> list[str] | None:
    """Scope preflight credentials to the Compose runtime that will actually run."""
    if packaging.selected_kind is None or packaging.selected_kind.value != "compose":
        return None
    selected = next(
        (
            candidate
            for candidate in packaging.candidates
            if candidate.path == packaging.selected_path
        ),
        None,
    )
    if selected is None or packaging.selected_path is None:
        return None
    return [packaging.selected_path, *selected.runtime_source_roots]


def _worker_failure_retryable(
    return_code: int, failure: dict[str, Any], retryable_domains: list[str]
) -> bool:
    """Retry infrastructure transport, never agent behavior or grading outcomes."""
    return (
        return_code != 0
        and failure.get("retryable") is True
        and failure.get("domain") in retryable_domains
    )


def _allowed_source(raw: str) -> Path:
    source_text = raw
    for mapping in os.getenv("ALK_SANDBOX_PATH_MAP", "").split(os.pathsep):
        if "=" not in mapping:
            continue
        external, internal = mapping.split("=", 1)
        if source_text == external or source_text.startswith(
            external.rstrip("/") + "/"
        ):
            source_text = (
                internal.rstrip("/") + source_text[len(external.rstrip("/")) :]
            )
            break
    source = Path(source_text).expanduser().resolve()
    if not source.is_dir():
        raise HTTPException(
            status_code=400, detail="source_path must be an existing directory"
        )
    configured = os.getenv("ALK_SANDBOX_SOURCE_ROOTS")
    roots = [
        Path(item).expanduser().resolve()
        for item in (
            configured.split(os.pathsep) if configured else [str(Path.cwd().parent)]
        )
        if item
    ]
    if not any(source == root or root in source.parents for root in roots):
        raise HTTPException(
            status_code=403, detail="source_path is outside allowed roots"
        )
    return source


def _safe_uploaded_path(raw: str) -> PurePosixPath:
    normalized = str(raw).replace("\\", "/").strip("/")
    relative = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized) > 1024
        or relative.is_absolute()
        or any(part in {"", ".", ".."} for part in relative.parts)
        or len(relative.parts) > 64
    ):
        raise HTTPException(status_code=400, detail=f"unsafe source path: {raw[:200]}")
    leaf = relative.name.lower()
    safe_environment_templates = {".env.example", ".env.sample", ".env.template"}
    if (
        leaf == ".env" or leaf.startswith(".env.")
    ) and leaf not in safe_environment_templates:
        raise HTTPException(
            status_code=400,
            detail=f"{relative.as_posix()} may contain secrets; upload it through the .env control",
        )
    return relative


def _uploaded_source_name(source: Path) -> str:
    manifest = _read_json(source.parent / f"{source.name}.json")
    return str(manifest.get("name") or "uploaded-agent")


def _github_location(raw: str):
    try:
        return parse_github_location(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _github_installation_token(installation_id: str) -> str:
    """Local broker adapter; production exchanges the installation via its vault."""
    safe_id = re.sub(r"[^A-Za-z0-9_]", "_", installation_id)
    token = os.getenv(f"GITHUB_INSTALLATION_{safe_id}_TOKEN")
    if not token:
        raise SourceAcquisitionError(
            "github_installation_token_unavailable; authorize the GitHub App installation"
        )
    return token


def _events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            result.append(json.loads(line))
        except ValueError:
            continue
    return result


def _stage_from_events(events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        stage = event.get("payload", {}).get("stage")
        if stage:
            aliases = {
                "understand": HarnessStage.UNDERSTANDING_AGENT.value,
                "environment": HarnessStage.GENERATING_ENVIRONMENT.value,
                "scenarios": HarnessStage.GENERATING_SCENARIOS.value,
                "run": HarnessStage.RUNNING.value,
                "calls": HarnessStage.RUNNING.value,
            }
            return aliases.get(
                stage, stage if stage in HarnessStage._value2member_map_ else None
            )
    return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_TERMINAL_STAGES = {
    HarnessStage.COMPLETED.value,
    HarnessStage.FAILED.value,
    HarnessStage.CANCELED.value,
}


def _authorize(authorization: str | None = Header(default=None)) -> None:
    expected = os.getenv("ALK_SANDBOX_TOKEN")
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
        )


def create_app(root: Path | None = None) -> FastAPI:
    app = FastAPI(title="ALK local sandbox", version="1")
    sandbox = LocalSandbox(
        root or Path(os.getenv("ALK_SANDBOX_ROOT", "./artifacts/sandbox")),
        max_concurrency=int(os.getenv("ALK_SANDBOX_MAX_CONCURRENCY", "2")),
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "provider": "local-process"}

    @app.post(
        "/v1/sources",
        response_model=UploadedSourceResponse,
        dependencies=[Depends(_authorize)],
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_source(
        files: list[UploadFile] = File(...),
        paths: list[str] = Form(...),
        name: str = Form(default="uploaded-agent"),
    ) -> UploadedSourceResponse:
        return await sandbox.upload_source(files, paths, name)

    @app.post(
        "/v1/preflight",
        response_model=SandboxPreflightResponse,
        dependencies=[Depends(_authorize)],
    )
    async def preflight(
        request: SandboxPreflightRequest,
    ) -> SandboxPreflightResponse:
        return sandbox.preflight(request)

    @app.post(
        "/v1/jobs",
        response_model=SandboxJobResponse,
        dependencies=[Depends(_authorize)],
    )
    async def submit(request: LocalSandboxRequest) -> SandboxJobResponse:
        return sandbox.submit(request)

    @app.get(
        "/v1/jobs",
        response_model=list[SandboxJobResponse],
        dependencies=[Depends(_authorize)],
    )
    async def list_jobs() -> list[SandboxJobResponse]:
        return sandbox.list()

    @app.get(
        "/v1/jobs/{job_id}",
        response_model=SandboxJobResponse,
        dependencies=[Depends(_authorize)],
    )
    async def get_job(job_id: str) -> SandboxJobResponse:
        try:
            return sandbox.get(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=SandboxJobResponse,
        dependencies=[Depends(_authorize)],
    )
    async def cancel_job(job_id: str) -> SandboxJobResponse:
        try:
            return await sandbox.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "fi.alk.harness.sandbox_server:app",
        host=os.getenv("ALK_SANDBOX_HOST", "127.0.0.1"),
        port=int(os.getenv("ALK_SANDBOX_PORT", "8788")),
    )


if __name__ == "__main__":
    main()
