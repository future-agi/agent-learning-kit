"""Local implementation of the hosted ALK sandbox boundary.

The platform talks to this HTTP contract in development. Production can replace the
implementation with a Kubernetes or microVM service without changing platform job APIs.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import signal
import sys
import time
from typing import Any, Literal
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

from ..providers.agent import _redact
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
    ProviderExecutionMode,
    RepositorySource,
    SourceKind,
    SourceVisibility,
)
from .packaging import PackagingManifest, inspect_packaging
from .provision import ProvisionError, source_fingerprint, stop
from .secrets import (
    CONTROLLER_CONNECTION_NAMES,
    RUNNER_RESERVED_ENVIRONMENT as _RUNNER_RESERVED_ENVIRONMENT,
    SecretResolutionError,
    controller_name_reserved,
    resolve_worker_secrets,
    worker_environment,
)
from .source_folder import SubfolderNotFound, agent_folder, subfolder_not_found


_CONTROLLER_TOKEN = uuid.uuid4().hex
_SANDBOX_JOB_PREFIX = "ALK_JOB_"
_SANDBOX_FILE_PREFIX = "ALK_SECRET_FILE_"


class LocalSandboxRequest(BaseModel):
    source_path: str | None = None
    source_id: str | None = None
    provider_only: bool = False
    github_repository: str | None = None
    github_ref: str | None = None
    github_commit_sha: str | None = None
    github_visibility: SourceVisibility = SourceVisibility.PUBLIC
    github_installation_id: str | None = None
    github_subfolder: str | None = None
    scenario_count: int = Field(default=10, ge=1, le=200)
    seed: int | None = None
    agent_name: str | None = None
    connector: str = "auto"
    connector_mode: ProviderExecutionMode | None = None
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
                for value in (
                    self.source_path, self.source_id, self.github_repository,
                    self.provider_only,
                )
            )
            != 1
        ):
            raise ValueError("exactly_one_source_required")
        if self.provider_only and (
            self.connector not in {"retell", "retell_chat", "vapi", "livekit"}
            or self.connector_mode is not ProviderExecutionMode.CONNECT_ONLY
        ):
            raise ValueError("provider_only_requires_connect_only_provider")
        if self.github_subfolder:
            if not self.github_repository:
                raise ValueError("A subfolder needs a GitHub repository to read it from.")
            # The older authoring path would ignore the folder without saying so.
            if self.metadata.get("generic_harness_v1") is not True:
                raise ValueError(
                    "A subfolder needs the generic harness, which this job didn't ask for."
                )
        _validate_environment_values(self.environment_values, self.secret_refs)
        _validate_controller_names(self.connector_config, self.secret_refs)
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
    metadata: dict[str, Any] = Field(default_factory=dict)

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
        _validate_controller_names(self.connector_config, self.secret_refs)
        return self


class SandboxRerunRequest(BaseModel):
    """Fresh credentials for replaying an already-built harness session.

    The saved contract, sealed environment bundle and scenarios are reused. Secret values are
    deliberately supplied again (or resolved from fresh references); they are never recovered
    from the completed job's persisted payload.
    """

    secret_refs: dict[str, SecretRef] = Field(default_factory=dict)
    environment_values: dict[str, SecretStr] = Field(default_factory=dict)
    only: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_environment(self) -> "SandboxRerunRequest":
        _validate_environment_values(self.environment_values, self.secret_refs)
        _validate_secret_alias_shapes(self.secret_refs)
        return self


class SandboxPreflightResponse(BaseModel):
    source_kind: SourceKind
    source_label: str
    ready_to_submit: bool
    can_submit_for_authoring: bool = False
    configuration_status: str = "selection_pending"
    checkout_required: bool = False
    credentials: CredentialManifest
    packaging: PackagingManifest | None = None
    notes: list[str] = Field(default_factory=list)


class SandboxJobResponse(BaseModel):
    job: HarnessJob
    status: HarnessJobStatus
    events: list[dict[str, Any]] = Field(default_factory=list)
    stage_outputs: list[dict[str, Any]] = Field(default_factory=list)
    artifact_path: str | None = None
    credentials: CredentialManifest | None = None
    stage_outputs: list[dict[str, Any]] = Field(default_factory=list)
    adjustments: list[dict[str, Any]] = Field(default_factory=list)


class SandboxAdjustmentRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2_000)
    client_request_id: str | None = Field(default=None, max_length=128)
    target_stage: Literal["understand", "environment", "scenarios"] | None = None
    scenario_delta: int | None = Field(default=None, ge=1)


class UploadedSourceResponse(BaseModel):
    source_id: str
    name: str
    file_count: int
    total_bytes: int


class UploadedSecretFileResponse(BaseModel):
    """Opaque handle for a file that is materialized only at the worker boundary."""

    environment_name: str
    secret_ref: SecretRef
    size: int


class LocalSandbox:
    def __init__(
        self,
        root: Path,
        *,
        max_concurrency: int = 2,
        upload_root: Path | None = None,
        secret_file_root: Path | None = None,
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
        self.secret_files_root = (
            (
                secret_file_root
                or Path(
                    os.getenv(
                        "ALK_SANDBOX_SECRET_FILE_ROOT",
                        str(self.root / "secret-files"),
                    )
                )
            )
            .expanduser()
            .resolve()
        )
        # A sandbox-service restart orphans every in-flight worker. Secret files therefore have
        # no legitimate consumer after restart and must fail closed instead of lingering on disk.
        shutil.rmtree(self.secret_files_root, ignore_errors=True)
        (self.secret_files_root / "pending").mkdir(parents=True, mode=0o700)
        (self.secret_files_root / "jobs").mkdir(parents=True, mode=0o700)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # Values supplied through the platform's .env flow live only for the lifetime of the
        # job. Persisted job/state artifacts contain the opaque mounted references created below.
        self._ephemeral_secrets: dict[str, dict[str, str]] = {}
        self._ephemeral_secret_file_names: dict[str, set[str]] = {}
        self._recover_orphans()

    async def upload_secret_file(
        self, uploaded: UploadFile, environment_name: str
    ) -> UploadedSecretFileResponse:
        """Stage one credential file and return a bearer-style opaque reference.

        Contents never enter a request JSON, job, bundle, event, log or artifact. The upload is
        claimed by exactly one job and removed at that job's terminal boundary.
        """
        name = str(environment_name).strip()
        if not _ENVIRONMENT_NAME.fullmatch(name):
            raise HTTPException(status_code=400, detail="environment_name is invalid")
        if name in _RUNNER_RESERVED_ENVIRONMENT or name.startswith("ALK_"):
            raise HTTPException(status_code=400, detail="environment_name is reserved")
        self._purge_stale_secret_files()
        identifier = str(uuid.uuid4())
        name_digest = hashlib.sha256(name.encode()).hexdigest()[:12].upper()
        internal_key = (
            f"ALK_SECRET_FILE_{identifier.replace('-', '').upper()}_{name_digest}"
        )
        destination = self.secret_files_root / "pending" / identifier
        total = 0
        try:
            with destination.open("xb") as handle:
                while chunk := await uploaded.read(1024 * 1024):
                    total += len(chunk)
                    if total > 5 * 1024 * 1024:
                        raise HTTPException(
                            status_code=413,
                            detail="credential file may not exceed 5 MiB",
                        )
                    handle.write(chunk)
            destination.chmod(0o600)
            if total == 0:
                raise HTTPException(status_code=400, detail="credential file is empty")
            if name == "GOOGLE_APPLICATION_CREDENTIALS":
                try:
                    document = json.loads(destination.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise HTTPException(
                        status_code=400,
                        detail="Google application credentials must be a valid JSON object",
                    ) from exc
                if not isinstance(document, dict) or not document:
                    raise HTTPException(
                        status_code=400,
                        detail="Google application credentials must be a valid JSON object",
                    )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return UploadedSecretFileResponse(
            environment_name=name,
            secret_ref=SecretRef(
                manager="mounted",
                key=internal_key,
                # Credential files configure the submitted target process and therefore use
                # the same Bundle V2 admission purpose as text environment values.
                purpose="target_provider",
            ),
            size=total,
        )

    def _claim_secret_files(
        self, job_id: str, references: dict[str, SecretRef]
    ) -> tuple[dict[str, str], set[str]]:
        """Atomically move provider uploads into one job-private directory."""
        self._purge_stale_secret_files()
        candidates: list[tuple[str, str, Path]] = []
        for environment_name, reference in references.items():
            if reference.manager != "mounted" or not reference.key.startswith(
                "ALK_SECRET_FILE_"
            ):
                continue
            suffix = reference.key.removeprefix("ALK_SECRET_FILE_")
            match = re.fullmatch(r"([0-9A-F]{32})_([0-9A-F]{12})", suffix)
            expected_digest = (
                hashlib.sha256(environment_name.encode()).hexdigest()[:12].upper()
            )
            if match is None or match.group(2) != expected_digest:
                raise HTTPException(
                    status_code=400, detail="secret_file_reference_invalid"
                )
            identifier = str(uuid.UUID(hex=match.group(1).lower()))
            source = self.secret_files_root / "pending" / identifier
            if not source.is_file():
                raise HTTPException(
                    status_code=400,
                    detail="secret_file_reference_unavailable_or_consumed",
                )
            candidates.append((environment_name, reference.key, source))
        if not candidates:
            return {}, set()
        job_root = self.secret_files_root / "jobs" / job_id
        job_root.mkdir(mode=0o700)
        claimed: dict[str, str] = {}
        names: set[str] = set()
        try:
            for environment_name, internal_key, source in candidates:
                target = job_root / environment_name
                source.replace(target)
                target.chmod(0o400)
                claimed[internal_key] = str(target)
                names.add(environment_name)
        except Exception:
            shutil.rmtree(job_root, ignore_errors=True)
            raise
        return claimed, names

    def _delete_job_secret_files(self, job_id: str) -> None:
        shutil.rmtree(self.secret_files_root / "jobs" / job_id, ignore_errors=True)

    def _purge_stale_secret_files(self) -> None:
        ttl = max(60, int(os.getenv("ALK_SECRET_FILE_TTL_SECONDS", "900")))
        cutoff = time.time() - ttl
        for path in (self.secret_files_root / "pending").iterdir():
            try:
                if path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                continue

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
                controller_pid = int(state.get("controller_pid", 0) or 0)
                controller_identity = str(state.get("controller_identity") or "")
                controller_token = str(state.get("controller_token") or "")
                if (
                    controller_pid == os.getpid()
                    and controller_token == _CONTROLLER_TOKEN
                ) or (
                    controller_pid > 0
                    and controller_identity
                    and _process_identity(controller_pid) == controller_identity
                ):
                    # Another app object or health process may inspect the same provider root.
                    # It must not declare a job orphaned while the owning controller still exists.
                    continue
                state.update(
                    stage=HarnessStage.FAILED.value,
                    detail="sandbox service restarted while the job was running",
                    updated_at=_now(),
                )
                _write_json(path, state)

    def _job_secret_refs(
        self, identifier: str, request: LocalSandboxRequest, *, hosted: bool
    ) -> tuple[dict[str, SecretRef], dict[str, str], set[str]]:
        """Refs, values (keyed by internal key), and secret-file names for one new job."""
        # Job keys are public through GET /v1/jobs, so a sandbox prefix proves nothing about
        # who minted a key. Refusing every incoming ref that uses one, except an upload the
        # claim binds to its own alias, is what lets the resolver trust a prefixed vault key.
        for alias, reference in request.secret_refs.items():
            if reference.key.startswith(_SANDBOX_JOB_PREFIX) or (
                reference.key.startswith(_SANDBOX_FILE_PREFIX)
                and reference.manager != "mounted"
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"sandbox_secret_reference_refused: {alias}",
                )
            if hosted and reference.manager == "platform-vault":
                # The resolver answers only the vault keys this sandbox mints for the job, so an
                # incoming one could only fail later, after the job took a slot. Refuse it now,
                # with the code the resolver would give.
                raise HTTPException(
                    status_code=400,
                    detail=f"sandbox_vault_reference_unknown: {alias}",
                )
        claimed_files, secret_file_names = self._claim_secret_files(
            identifier, request.secret_refs
        )
        # Hosted jobs admit only vault refs. The sandbox stands in for the hosting side's
        # vault, and resolves these keys from this job's in-memory values only.
        manager = "platform-vault" if hosted else "mounted"
        refs = {
            alias: reference
            for alias, reference in request.secret_refs.items()
            if not reference.key.startswith((_SANDBOX_JOB_PREFIX, _SANDBOX_FILE_PREFIX))
        }
        values: dict[str, str] = {}
        job_prefix = f"{_SANDBOX_JOB_PREFIX}{identifier.replace('-', '').upper()}_"
        for index, (name, value) in enumerate(request.environment_values.items()):
            internal_key = f"{job_prefix}{index}"
            refs[name] = SecretRef(
                manager=manager,
                key=internal_key,
                # Values entered for a run configure the submitted target runtime. Bundle V2
                # admits secrets by purpose, so a descriptive free-form purpose causes the
                # certified process provider to drop an otherwise valid value.
                purpose="target_provider",
            )
            values[internal_key] = value.get_secret_value()
        # Only an upload the claim bound to this alias is minted. The claim's digest check already
        # binds every mounted upload key to its alias, so this is a second guard behind it, not
        # the only one: nothing should rely on it alone.
        for alias, reference in request.secret_refs.items():
            if (
                reference.manager == "mounted"
                and reference.key.startswith(_SANDBOX_FILE_PREFIX)
                and alias in secret_file_names
            ):
                refs[alias] = SecretRef(
                    manager=manager, key=reference.key, purpose="target_provider"
                )
        values.update(claimed_files)
        return refs, values, secret_file_names

    def submit(self, request: LocalSandboxRequest) -> SandboxJobResponse:
        source = (
            _allowed_source(request.source_path)
            if request.source_path
            else self.uploaded_source(request.source_id)
            if request.source_id
            else None
        )
        identifier = str(uuid.uuid4())
        if request.provider_only:
            source = self.jobs_root / identifier / "provider-source"
        run_id = f"harness-{identifier}"
        github_location = (
            _github_location(request.github_repository)
            if request.github_repository
            else None
        )
        github_repository = github_location.repository if github_location else None
        hosted = bool(request.source_id or request.github_repository)
        try:
            # Inside the try so a refusal or raise after the claim still deletes the claim.
            job_refs, mounted_values, secret_file_names = self._job_secret_refs(
                identifier, request, hosted=hosted
            )
            source_spec = (
                RepositorySource(kind=SourceKind.PROVIDER)
                if request.provider_only
                else RepositorySource(
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
                    subfolder=request.github_subfolder,
                )
            )
            job = HarnessJob(
                job_id=identifier,
                run_id=run_id,
                execution=ExecutionMode.HOSTED if hosted else ExecutionMode.LOCAL,
                source=source_spec,
                agent=AgentConnection(
                    connector=request.connector,
                    mode=request.connector_mode,
                    config=request.connector_config,
                    secret_refs=job_refs,
                ),
                scenario_count=request.scenario_count,
                seed=request.seed,
                artifacts=HarnessArtifactPolicy(
                    level="full", allow_bundle_download=True
                ),
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
                    # Both dotenv values and credential-file paths are runtime configuration. Only
                    # their names are persisted; values and provider-local paths remain ephemeral.
                    "environment_value_names": sorted(
                        {*request.environment_values, *secret_file_names}
                    ),
                    "secret_file_names": sorted(secret_file_names),
                },
            )
            directory = self.jobs_root / identifier
            directory.mkdir()
            if request.provider_only:
                assert source is not None
                source.mkdir()
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
                    "controller_pid": os.getpid(),
                    "controller_identity": _process_identity(os.getpid()),
                    "controller_token": _CONTROLLER_TOKEN,
                },
            )
        except Exception:
            self._delete_job_secret_files(identifier)
            shutil.rmtree(self.jobs_root / identifier, ignore_errors=True)
            raise
        if mounted_values:
            self._ephemeral_secrets[identifier] = mounted_values
        if secret_file_names:
            self._ephemeral_secret_file_names[identifier] = secret_file_names
        self._tasks[identifier] = asyncio.create_task(self._execute(job, source))
        return self.get(identifier)

    @staticmethod
    def _hosted_rerun_secrets(
        references: dict[str, SecretRef],
        claimed_files: dict[str, str],
        secret_file_names: set[str],
    ) -> dict[str, str]:
        """Resolve a hosted rerun's refs from the files it just claimed, and nothing else."""
        resolved: dict[str, str] = {}
        for alias, reference in references.items():
            # Any other ref would resolve from this server's environment, which holds the
            # runner's own credentials. Only an upload claimed under its own alias passes. The
            # last two conditions repeat what the claim's digest check guarantees: a second
            # guard behind it, not the only one.
            if (
                reference.manager != "mounted"
                or not reference.key.startswith(_SANDBOX_FILE_PREFIX)
                or reference.key not in claimed_files
                or alias not in secret_file_names
            ):
                raise HTTPException(
                    status_code=400,
                    detail=f"sandbox_rerun_secret_refused: {alias}",
                )
            resolved[str(alias)] = claimed_files[reference.key]
        return resolved

    def rerun(self, job_id: str, request: SandboxRerunRequest) -> SandboxJobResponse:
        response = self.get(job_id)
        if not response.status.stage.terminal:
            raise HTTPException(
                status_code=409, detail="harness job is already running"
            )
        output = self.artifacts_root / response.job.run_id
        required = (
            output / "contract.json",
            output / "scenarios.json",
            output / "environment-bundle" / "manifest.json",
            output / "platform.json",
        )
        missing = [path.name for path in required if not path.is_file()]
        if missing:
            raise HTTPException(
                status_code=409,
                detail=(
                    "saved harness session cannot be rerun; missing "
                    + ", ".join(missing)
                ),
            )

        runtime_configuration = {
            name: value.get_secret_value()
            for name, value in request.environment_values.items()
        }
        claimed_files, secret_file_names = self._claim_secret_files(
            job_id, request.secret_refs
        )
        try:
            if response.job.execution is ExecutionMode.HOSTED:
                resolved = self._hosted_rerun_secrets(
                    request.secret_refs, claimed_files, secret_file_names
                )
            else:
                try:
                    resolved = _resolve_local_references(
                        request.secret_refs, claimed_files
                    )
                except SecretResolutionError as exc:
                    if str(exc).startswith("secret_reference_reserved"):
                        raise HTTPException(status_code=400, detail=str(exc)) from exc
                    raise
        except Exception:
            self._delete_job_secret_files(job_id)
            raise
        runtime_configuration.update(resolved)
        self._ephemeral_secrets[job_id] = dict(runtime_configuration)
        self._ephemeral_secret_file_names[job_id] = secret_file_names

        state_path = self.jobs_root / job_id / "state.json"
        state = _read_json(state_path)
        operation_started_at = _now()
        state.update(
            stage=HarnessStage.QUEUED.value,
            detail="waiting to restart the saved environment",
            failure=None,
            completed_scenarios=0,
            total_scenarios=(len(request.only) or response.job.scenario_count),
            operation="rerun",
            operation_started_at=operation_started_at,
            attempt=int(state.get("attempt", 0) or 0) + 1,
            updated_at=operation_started_at,
        )
        _write_json(state_path, state)
        self._tasks[job_id] = asyncio.create_task(
            self._execute_rerun(response.job, request.only)
        )
        return self.get(job_id)

    async def _execute_rerun(self, job: HarnessJob, only: list[str]) -> None:
        """Run calls from immutable saved artifacts, with a fresh environment lifecycle."""

        directory = self.jobs_root / job.job_id
        state_path = directory / "state.json"
        output = self.artifacts_root / job.run_id
        # The local runner controls a host Docker daemon. Paths in Compose bind mounts are
        # therefore resolved by that daemon, not inside this service container. Artifacts live
        # on the runner's private volume, so replaying a bundle directly from ``output`` makes
        # repository seed/config files invisible to Docker. Materialize a disposable copy in
        # the provider's Docker-visible upload workspace. Hosted providers must offer the same
        # workspace guarantee even when their implementation is not a nested Docker daemon.
        replay_root = self.uploads_root / ".reruns" / job.job_id
        async with self._semaphore:
            state = _read_json(state_path)
            if state.get("stage") == HarnessStage.CANCELED.value:
                return
            state.update(
                stage=HarnessStage.RUNNING.value,
                detail="restarting the saved environment and running scenarios",
                updated_at=_now(),
            )
            _write_json(state_path, state)
            log_handle = (directory / "worker.log").open("ab")
            try:
                shutil.rmtree(replay_root, ignore_errors=True)
                replay_root.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(output / "environment-bundle", replay_root)
                runtime_configuration = self._ephemeral_secrets.get(job.job_id, {})
                file_names = self._ephemeral_secret_file_names.get(job.job_id, set())
                child_environment = worker_environment(
                    {
                        name: value
                        for name, value in runtime_configuration.items()
                        if name in CONTROLLER_CONNECTION_NAMES
                        and name not in file_names
                    },
                    runtime_configuration=runtime_configuration,
                )
                child_environment["ALK_RUNTIME_CONFIGURATION_NAMES"] = ",".join(
                    sorted(runtime_configuration)
                )
                child_environment["ALK_RUNTIME_SECRET_FILE_NAMES"] = ",".join(
                    sorted(self._ephemeral_secret_file_names.get(job.job_id, set()))
                )
                child_environment["ALK_HOSTED_EXECUTION"] = (
                    "1" if job.execution is ExecutionMode.HOSTED else "0"
                )
                child_environment["ALK_HARNESS_JOB_ID"] = job.job_id

                async def run_stage(command: list[str]) -> int:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=log_handle,
                        stderr=asyncio.subprocess.STDOUT,
                        start_new_session=True,
                        env=child_environment,
                    )
                    self._processes[job.job_id] = process
                    return await process.wait()

                environment_up = [
                    sys.executable,
                    "-m",
                    "fi.alk.harness.cli",
                    "environment",
                    "up",
                    "--bundle",
                    str(replay_root),
                    "--out",
                    str(output),
                ]
                simulation = [
                    sys.executable,
                    "-m",
                    "fi.alk.harness.cli",
                    "simulate",
                    "--name",
                    str(job.metadata.get("agent_name") or job.run_id),
                    "--out",
                    str(output),
                ]
                if only:
                    simulation.extend(["--only", *only])
                environment_down = [
                    sys.executable,
                    "-m",
                    "fi.alk.harness.cli",
                    "environment",
                    "down",
                    "--out",
                    str(output),
                ]

                up_code = await run_stage(environment_up)
                simulation_code = 1
                cleanup_code = 0
                if up_code == 0:
                    try:
                        simulation_code = await run_stage(simulation)
                    finally:
                        cleanup_code = await run_stage(environment_down)

                # Exit 2 is a completed suite whose submitted agent failed checks.
                successful_execution = (
                    up_code == 0 and simulation_code in (0, 2) and cleanup_code == 0
                )
                failed_stage = (
                    "environment_up"
                    if up_code != 0
                    else "environment_down"
                    if cleanup_code != 0
                    else "simulation"
                )
                failure_stage = (
                    HarnessStage.BUILDING_ENVIRONMENT.value
                    if up_code != 0
                    else HarnessStage.CLEANING_UP.value
                    if cleanup_code != 0
                    else HarnessStage.RUNNING.value
                )
                return_code = (
                    up_code
                    if up_code != 0
                    else cleanup_code
                    if cleanup_code != 0
                    else simulation_code
                )
                state = _read_json(state_path)
                if state.get("stage") != HarnessStage.CANCELED.value:
                    state.update(
                        stage=(
                            HarnessStage.COMPLETED.value
                            if successful_execution
                            else HarnessStage.FAILED.value
                        ),
                        detail=(
                            "saved harness session rerun completed"
                            if successful_execution
                            else f"saved harness session rerun exited {return_code}"
                        ),
                        completed_scenarios=(len(only) if only else job.scenario_count)
                        if successful_execution
                        else 0,
                        failure=None
                        if successful_execution
                        else {
                            "domain": "environment",
                            "stage": failure_stage,
                            "code": "saved_session_rerun_failed",
                            "message": f"{failed_stage} exited {return_code}",
                            "retryable": False,
                        },
                        updated_at=_now(),
                    )
                    _write_json(state_path, state)
            except Exception as exc:
                state = _read_json(state_path)
                state.update(
                    stage=HarnessStage.FAILED.value,
                    detail=f"{type(exc).__name__}: {exc}",
                    failure={
                        "domain": "infrastructure",
                        "stage": HarnessStage.RUNNING.value,
                        "code": type(exc).__name__,
                        "message": str(exc),
                        "retryable": False,
                    },
                    updated_at=_now(),
                )
                _write_json(state_path, state)
            finally:
                log_handle.close()
                self._processes.pop(job.job_id, None)
                self._tasks.pop(job.job_id, None)
                self._ephemeral_secrets.pop(job.job_id, None)
                self._ephemeral_secret_file_names.pop(job.job_id, None)
                self._delete_job_secret_files(job.job_id)
                shutil.rmtree(replay_root, ignore_errors=True)

    def preflight(self, request: SandboxPreflightRequest) -> SandboxPreflightResponse:
        if request.source_path or request.source_id:
            source = (
                _allowed_source(request.source_path)
                if request.source_path
                else self.uploaded_source(request.source_id or "")
            )
            packaging = inspect_packaging(
                source,
                external_environment=bool(request.environment_values),
            )
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
                template_secrets_required=(
                    request.metadata.get("generic_harness_v1") is not True
                ),
            )
            from .credentials import assess_configuration

            admission = assess_configuration()
            return SandboxPreflightResponse(
                configuration_status=admission.status,
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
                can_submit_for_authoring=True,
                notes=[
                    "Runtime selection and configuration validation continue after authoring. Packaging and credential findings are inventory."
                ],
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
            can_submit_for_authoring=manifest.ready,
            checkout_required=True,
            credentials=manifest,
            notes=[
                "Agent credential discovery continues inside the sandbox after checkout."
            ],
        )

    def _resolve_job_secrets(self, job: HarnessJob) -> dict[str, str]:
        """Resolve a job's refs; vault refs only from this job's own map."""
        # Read here rather than passed in, so no caller can hand the resolver a wider store.
        values = self._ephemeral_secrets.get(job.job_id, {})
        own_job_prefix = f"{_SANDBOX_JOB_PREFIX}{job.job_id.replace('-', '').upper()}_"
        resolved: dict[str, str] = {}
        others: dict[str, SecretRef] = {}
        for alias, reference in job.agent.secret_refs.items():
            if reference.manager != "platform-vault":
                others[alias] = reference
                continue
            # A vault ref never reads the process environment, which holds this server's own
            # credentials, and never another job's values.
            if reference.key.startswith(_SANDBOX_JOB_PREFIX):
                if not reference.key.startswith(own_job_prefix):
                    raise SecretResolutionError(
                        f"sandbox_vault_reference_unknown: {alias}"
                    )
            elif not reference.key.startswith(_SANDBOX_FILE_PREFIX):
                raise SecretResolutionError(f"sandbox_vault_reference_unknown: {alias}")
            value = values.get(reference.key)
            # Empty counts as missing, as it does for the other managers.
            if not value:
                raise SecretResolutionError(f"secret_reference_unavailable: {alias}")
            resolved[str(alias)] = value
        if others:
            resolved.update(_resolve_local_references(others, values))
        return resolved

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
                agent_folder(source, job.source.subfolder)
                packaging_manifest = inspect_packaging(
                    source,
                    external_environment=bool(
                        job.metadata.get("environment_value_names", [])
                    ),
                )
                # The import walk reads the agent's files at bundling's limits, seconds on a
                # crafted repository; off the loop, the server keeps answering meanwhile.
                credential_manifest = await asyncio.to_thread(
                    discover_credentials,
                    source,
                    secret_refs=job.agent.secret_refs,
                    provided_environment=job.agent.config,
                    agent_subfolder=job.source.subfolder,
                    template_secrets_required=(
                        job.metadata.get("generic_harness_v1") is not True
                    ),
                )
                _write_json(
                    directory / "packaging.json",
                    packaging_manifest.model_dump(mode="json"),
                )
                _write_json(
                    directory / "credentials.json",
                    credential_manifest.model_dump(mode="json"),
                )
                from .credentials import assess_configuration

                admission = assess_configuration()
                _write_json(
                    directory / "configuration-admission.json",
                    admission.model_dump(mode="json"),
                )
                resolved_secrets = self._resolve_job_secrets(job)
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
                    } | {
                        # The customer agent's own credentials: the agent's, never the controller's.
                        str(alias)
                        for alias, reference in job.agent.secret_refs.items()
                        if _target_alias(reference)
                    }
                    config_controller, config_runtime = _configuration_environment(
                        job.agent.config
                    )
                    # The job's own values win over its connector configuration.
                    runtime_configuration = {
                        **config_runtime,
                        **{
                            name: value
                            for name, value in resolved_secrets.items()
                            if name in runtime_names
                        },
                    }
                    file_names = {
                        str(name)
                        for name in job.metadata.get("secret_file_names", [])
                    }
                    controller_configuration = {
                        **config_controller,
                        # The job's own values for connection names are the controller's too.
                        **{
                            name: value
                            for name, value in resolved_secrets.items()
                            if name in runtime_names
                            and name in CONTROLLER_CONNECTION_NAMES
                            and name not in file_names
                        },
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
                    child_environment["ALK_RUNTIME_SECRET_FILE_NAMES"] = ",".join(
                        sorted(self._ephemeral_secret_file_names.get(job.job_id, set()))
                    )
                    child_environment["ALK_HOSTED_EXECUTION"] = (
                        "1" if job.execution is ExecutionMode.HOSTED else "0"
                    )
                    child_environment["ALK_HARNESS_JOB_ID"] = job.job_id
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
                        "--adjustments",
                        str(directory / "adjustments.jsonl"),
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
            except SubfolderNotFound as exc:
                state = _read_json(state_path)
                if state.get("stage") != HarnessStage.CANCELED.value:
                    failure = subfolder_not_found(exc.subfolder)
                    state.update(
                        stage=HarnessStage.FAILED.value,
                        detail=failure.message,
                        failure=failure.model_dump(mode="json"),
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
                self._ephemeral_secret_file_names.pop(job.job_id, None)
                self._delete_job_secret_files(job.job_id)

    def get(self, job_id: str) -> SandboxJobResponse:
        directory = self.jobs_root / job_id
        if not directory.is_dir():
            raise KeyError(job_id)
        job = HarnessJob.model_validate(_read_json(directory / "job.json"))
        raw_state = _read_json(directory / "state.json")
        events = _events(self.artifacts_root / job.run_id / "harness-events.jsonl")
        event_stage = _stage_from_events(events)
        if (
            raw_state.get("operation") == "rerun"
            and raw_state.get("stage") not in _TERMINAL_STAGES
        ):
            # Historic event streams end in ``completed``. During a rerun the current state is
            # authoritative until the new invocation emits/commits its terminal result.
            event_stage = None
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
        scenario_index = _json_artifact(
            self.artifacts_root / job.run_id / "scenarios.json"
        )
        discovered_scenarios = (
            len(scenario_index) if isinstance(scenario_index, list) else 0
        )
        completed_scenarios = int(raw_state.get("completed_scenarios", 0) or 0)
        if stage == HarnessStage.RUNNING.value:
            # The worker writes state.json only at process boundaries, while each scenario result
            # is committed as soon as that scenario finishes. Derive live progress from the newest
            # campaign directory so a multi-call run does not remain at 0/N until finalization.
            # Only immediate scenario result files count; nested SDK/debug artifacts are ignored.
            runs_root = self.artifacts_root / job.run_id / "runs"
            campaigns = [path for path in runs_root.glob("run-*") if path.is_dir()]
            operation_started_at = str(
                raw_state.get("operation_started_at") or ""
            ).strip()
            if raw_state.get("operation") == "rerun" and operation_started_at:
                try:
                    started_ns = int(
                        datetime.fromisoformat(operation_started_at).timestamp()
                        * 1_000_000_000
                    )
                    campaigns = [
                        path
                        for path in campaigns
                        if path.stat().st_mtime_ns >= started_ns
                    ]
                except ValueError:
                    campaigns = []
            if campaigns:
                latest = max(campaigns, key=lambda path: path.stat().st_mtime_ns)
                committed = sum(
                    1
                    for scenario in latest.iterdir()
                    if scenario.is_dir() and (scenario / "result.json").is_file()
                )
                completed_scenarios = min(
                    raw_state.get("total_scenarios", job.scenario_count),
                    max(completed_scenarios, committed),
                )
        failure = raw_state.get("failure")
        if isinstance(failure, dict):
            legacy_stage = str(failure.get("stage") or "")
            normalized_stage = {
                "environment_up": HarnessStage.BUILDING_ENVIRONMENT.value,
                "environment_down": HarnessStage.CLEANING_UP.value,
                "simulation": HarnessStage.RUNNING.value,
            }.get(legacy_stage)
            if normalized_stage:
                failure = {**failure, "stage": normalized_stage}
        status_value = HarnessJobStatus(
            job_id=job.job_id,
            run_id=job.run_id,
            stage=stage,
            updated_at=updated_at,
            detail=detail,
            failure=failure,
            completed_scenarios=completed_scenarios,
            total_scenarios=max(
                raw_state.get("total_scenarios", job.scenario_count),
                discovered_scenarios,
            ),
            attempt=raw_state.get("attempt", 1),
        )
        return SandboxJobResponse(
            job=job,
            status=status_value,
            events=events,
            stage_outputs=_stage_outputs(self.artifacts_root / job.run_id),
            artifact_path=str(self.artifacts_root / job.run_id),
            credentials=(
                CredentialManifest.model_validate(
                    _read_json(directory / "credentials.json")
                )
                if (directory / "credentials.json").is_file()
                else None
            ),
            adjustments=_adjustments(directory),
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
        self._ephemeral_secret_file_names.pop(job_id, None)
        self._delete_job_secret_files(job_id)
        return self.get(job_id)

    def adjust(
        self, job_id: str, request: SandboxAdjustmentRequest
    ) -> SandboxJobResponse:
        response = self.get(job_id)
        if response.status.stage.terminal:
            raise HTTPException(
                status_code=409, detail="a completed run cannot be adjusted"
            )
        if response.status.stage in {
            HarnessStage.CLEANING_UP,
            HarnessStage.UPLOADING_ARTIFACTS,
        }:
            raise HTTPException(
                status_code=409,
                detail="the run is already finalizing; start a follow-up run to change it",
            )
        instruction = request.instruction.strip()
        record = {
            "adjustment_id": str(uuid.uuid4()),
            "client_request_id": request.client_request_id,
            "instruction": instruction,
            "target_stage": request.target_stage or _adjustment_stage(response.status.stage.value),
            "scenario_delta": request.scenario_delta,
            "status": "pending",
            "created_at": _now(),
        }
        path = self.jobs_root / job_id / "adjustments.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
            stream.flush()
        return self.get(job_id)


def _configuration_environment(
    config: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Split explicit, non-secret connector configuration into (controller, runtime) values.

    Only the originators' connection names become controller variables. Every other upper-case
    key is the submitted agent's own configuration and travels in the runtime namespace.
    """
    controller: dict[str, str] = {}
    runtime: dict[str, str] = {}
    for name, value in config.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", str(name)):
            continue
        if isinstance(value, bool):
            text = "true" if value else "false"
        elif isinstance(value, (str, int, float)):
            text = str(value)
        else:
            continue
        target = controller if str(name) in CONTROLLER_CONNECTION_NAMES else runtime
        target[str(name)] = text
    return controller, runtime


_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_secret_alias_shapes(references: dict[str, SecretRef]) -> None:
    for alias in references:
        if not _ENVIRONMENT_NAME.fullmatch(str(alias)):
            raise ValueError(f"secret_reference_name_invalid: {alias}")


def _validate_controller_names(
    config: dict[str, Any], references: dict[str, SecretRef]
) -> None:
    """Refuse the names of a job that would reach the controller's own environment."""
    for name in config:
        if str(name) in _RUNNER_RESERVED_ENVIRONMENT or str(name).startswith("ALK_"):
            raise ValueError(f"connector_config_name_reserved: {name}")
    _validate_secret_alias_shapes(references)
    for alias, reference in references.items():
        # An upload alias becomes a runtime value; every other alias is a controller variable.
        if reference.manager == "mounted" and reference.key.startswith(
            _SANDBOX_FILE_PREFIX
        ):
            continue
        if _target_alias(reference):
            # The customer agent's own credential (ANTHROPIC_API_KEY, GOOGLE_API_KEY): it reaches
            # only the agent's runtime namespace, so it takes the rule environment values take.
            if alias in _RUNNER_RESERVED_ENVIRONMENT or alias.startswith("ALK_"):
                raise ValueError(f"secret_alias_reserved: {alias}")
            continue
        if controller_name_reserved(alias):
            raise ValueError(f"secret_alias_reserved: {alias}")


def _target_alias(reference: SecretRef) -> bool:
    """A ref the platform sends for the customer's agent, never for the controller."""
    return reference.purpose == "target_provider"


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


def _json_artifact(path: Path, *, max_bytes: int = 1_000_000) -> Any | None:
    """Read a bounded, generated JSON artifact for control-plane presentation."""
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _stage_outputs(root: Path) -> list[dict[str, Any]]:
    outputs: list[dict[str, Any]] = []
    contract = _json_artifact(root / "contract.json")
    if isinstance(contract, dict):
        outputs.append(
            {
                "id": "contract",
                "stage": HarnessStage.UNDERSTANDING_AGENT.value,
                "title": "Agent contract",
                "summary": (
                    f"{len(contract.get('tools') or [])} tools, "
                    f"{len(contract.get('hard_constraints') or [])} constraints and "
                    f"{len(contract.get('real_use_cases') or [])} use cases"
                ),
                "kind": "contract",
                "data": _presentation_value(contract),
                "updated_at": datetime.fromtimestamp(
                    (root / "contract.json").stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    environment = _json_artifact(root / "environment.json")
    if isinstance(environment, dict):
        visible = _presentation_value(
            {
                key: value
                for key, value in environment.items()
                if key
                not in {
                    "source",
                    "compose_file",
                    "compose_override_file",
                    "internal_overrides",
                    "runtime_trace_path",
                    "source_fingerprint",
                }
            }
        )
        outputs.append(
            {
                "id": "environment",
                "stage": HarnessStage.GENERATING_ENVIRONMENT.value,
                "title": "Execution environment",
                "summary": (
                    f"{len(environment.get('services') or [])} services ready"
                    + (
                        f" in {environment.get('provision_seconds')}s"
                        if environment.get("provision_seconds") is not None
                        else ""
                    )
                ),
                "kind": "environment",
                "data": visible,
                "updated_at": datetime.fromtimestamp(
                    (root / "environment.json").stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    scenarios = _json_artifact(root / "scenarios.json")
    if isinstance(scenarios, list):
        outputs.append(
            {
                "id": "scenarios",
                "stage": HarnessStage.GENERATING_SCENARIOS.value,
                "title": "Generated scenarios",
                "summary": f"{len(scenarios)} grounded scenarios",
                "kind": "scenarios",
                "data": scenarios,
                "updated_at": datetime.fromtimestamp(
                    (root / "scenarios.json").stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    results = _json_artifact(root / "results.json")
    if isinstance(results, dict):
        outputs.append(
            {
                "id": "results",
                "stage": HarnessStage.GRADING.value,
                "title": "Run results",
                "summary": "Scenario execution and grading evidence",
                "kind": "results",
                "data": results,
                "updated_at": datetime.fromtimestamp(
                    (root / "results.json").stat().st_mtime, timezone.utc
                ).isoformat(),
            }
        )
    return outputs


def _presentation_value(value: Any, key: str = "") -> Any:
    lowered = key.lower()
    if any(marker in lowered for marker in ("password", "secret", "token", "api_key")):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _presentation_value(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_presentation_value(item, key) for item in value]
    if isinstance(value, str) and "://" in value and "@" in value:
        scheme, remainder = value.split("://", 1)
        # Prose can contain an email before a later URL. Only redact when the URI
        # remainder itself contains userinfo.
        if "@" in remainder:
            return f"{scheme}://[redacted]@{remainder.split('@', 1)[1]}"
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            records.append(value)
    return records


def _adjustments(directory: Path) -> list[dict[str, Any]]:
    requested = _read_jsonl(directory / "adjustments.jsonl")
    acknowledgements = {
        item.get("adjustment_id"): item
        for item in _read_jsonl(directory / "adjustment-status.jsonl")
    }
    return [
        {**item, **acknowledgements.get(item.get("adjustment_id"), {})}
        for item in requested
    ]


def _adjustment_stage(current_stage: str) -> str:
    """The stage running now, for a change that does not name the stage it belongs to."""
    return {
        HarnessStage.UNDERSTANDING_AGENT.value: "understand",
        HarnessStage.GENERATING_ENVIRONMENT.value: "environment",
        HarnessStage.BUILDING_ENVIRONMENT.value: "environment",
        HarnessStage.VALIDATING_ENVIRONMENT.value: "environment",
        HarnessStage.GENERATING_SCENARIOS.value: "scenarios",
        HarnessStage.VALIDATING_SCENARIOS.value: "scenarios",
    }.get(current_stage, "scenarios")


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


def _process_identity(pid: int) -> str:
    """Return a PID-reuse-safe Linux process identity for orphan ownership."""
    try:
        # Field 22 is process start time in clock ticks since boot. ``comm`` may contain spaces,
        # so split only after the final closing parenthesis instead of indexing the whole line.
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        fields = stat.rsplit(")", 1)[1].strip().split()
        start_ticks = fields[19]
        boot_id = (
            Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
        )
        return f"{boot_id}:{pid}:{start_ticks}"
    except (OSError, IndexError, ValueError):
        return ""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read_json_value(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _stage_outputs(run_root: Path) -> list[dict[str, Any]]:
    """Return bounded, secret-safe snapshots for the platform's live run UI."""
    outputs: list[dict[str, Any]] = []
    contract = _read_json_value(run_root / "contract.json")
    if isinstance(contract, dict):
        tools = [
            {"name": str(tool.get("name") or "")}
            for tool in contract.get("tools", [])
            if isinstance(tool, dict) and tool.get("name")
        ]
        # One call: a contract written before write_contract cleaned its prose is cleaned here.
        one_liner, *rules = _redact(
            [
                str(contract.get("one_liner") or ""),
                *(str(item) for item in contract.get("hard_constraints", [])),
            ]
        )
        data = {
            "one_liner": one_liner,
            "modality": str(contract.get("modality") or "unknown"),
            "runtime": contract.get("runtime") or {},
            "tools": tools,
            "hard_constraints": rules,
        }
        outputs.append(
            {
                "id": "contract",
                "kind": "contract",
                "title": "Agent contract",
                "summary": f"{len(tools)} tools · {data['modality']} modality",
                "data": data,
            }
        )

    environment = _read_json_value(run_root / "environment.json")
    if isinstance(environment, dict):
        services = [str(item) for item in environment.get("services", [])]
        # Endpoint values are useful operational feedback, but credentials embedded in a URI
        # must never reach the platform response.
        overrides = {
            str(name): re.sub(r"(://)[^/@]+@", r"\1***@", str(value))
            for name, value in dict(environment.get("overrides") or {}).items()
        }
        outputs.append(
            {
                "id": "environment",
                "kind": "environment",
                "title": "Execution environment",
                "summary": f"{len(services)} services ready",
                "data": {
                    "services": services,
                    "project": str(environment.get("project") or ""),
                    "managed": bool(environment.get("managed")),
                    "overrides": overrides,
                },
            }
        )

    scenarios = _read_json_value(run_root / "scenarios.json")
    if isinstance(scenarios, list):
        data = [
            {
                "name": str(item.get("name") or "scenario"),
                "instruction": str(item.get("instruction") or ""),
                "use_case": str(item.get("use_case") or ""),
            }
            for item in scenarios
            if isinstance(item, dict)
        ]
        outputs.append(
            {
                "id": "scenarios",
                "kind": "scenarios",
                "title": "Generated scenarios",
                "summary": f"{len(data)} grounded scenarios",
                "data": data,
            }
        )

    platform = _read_json_value(run_root / "platform.json")
    if isinstance(platform, dict):
        run_test_id = str(platform.get("run_test_id") or "").strip()
        execution_id = str(platform.get("test_execution_id") or "").strip()
        if run_test_id:
            url = (
                f"/dashboard/simulate/test/{run_test_id}/{execution_id}"
                if execution_id
                else f"/dashboard/simulate/test/{run_test_id}/runs"
            )
            outputs.append(
                {
                    "id": "simulation",
                    "kind": "simulation",
                    "title": "Simulation results",
                    "summary": "Open this run in the simulation view",
                    "data": {
                        "url": url,
                        "run_test_id": run_test_id,
                        "test_execution_id": execution_id,
                    },
                }
            )
    return outputs


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


logger = logging.getLogger(__name__)

_TOKEN_ENV = "ALK_SANDBOX_TOKEN"
_ALLOW_ENV = "ALK_SANDBOX_ALLOW_UNAUTHENTICATED"


def _sandbox_token() -> str | None:
    # Returned unstripped so the server compares exactly the bytes the platform sends.
    token = os.getenv(_TOKEN_ENV)
    if not token or not token.strip():
        return None
    try:
        # A value that isn't valid UTF-8 can never match a header; treating it as unset
        # keeps every request a clean refusal instead of a 500.
        token.encode()
    except UnicodeEncodeError:
        return None
    return token


def _unauthenticated_allowed() -> bool:
    # Only an exact "1": "0", "false" or an empty value must never open the server.
    return os.getenv(_ALLOW_ENV) == "1"


def _is_loopback(host: str) -> bool:
    # A name such as "localhost" can resolve anywhere, so only an IP literal counts.
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _dev_mode() -> bool:
    """True only on a developer's own laptop: no token, the flag, and a loopback address."""
    return (
        _sandbox_token() is None
        and _unauthenticated_allowed()
        and _is_loopback(os.getenv("ALK_SANDBOX_HOST", "127.0.0.1"))
    )


def _resolve_local_references(
    references: dict[str, SecretRef], own_values: dict[str, str]
) -> dict[str, str]:
    """Resolve environment, mounted and futureagi refs.

    A key that is one of this job's own values (or a claimed upload) resolves from those values
    only. Any other key would read the server's own environment, which holds the runner's
    credentials, so that is allowed only in local development mode and never for a name the
    controller reserves.
    """
    resolved: dict[str, str] = {}
    own: dict[str, SecretRef] = {}
    for alias, reference in references.items():
        if reference.key in own_values:
            own[alias] = reference
            continue
        if reference.manager in {"environment", "futureagi", "mounted"} and (
            not _dev_mode()
            or controller_name_reserved(reference.key)
            or reference.key.upper().startswith("GITHUB_INSTALLATION_")
        ):
            raise SecretResolutionError(f"secret_reference_reserved: {alias}")
        resolved.update(resolve_worker_secrets({alias: reference}, environment=os.environ))
    resolved.update(resolve_worker_secrets(own, environment=own_values))
    return resolved


def _startup_refusal(host: str, token: str | None, allow: bool) -> str | None:
    # A padded token would never match what the platform sends, so refuse it loudly
    # rather than start a server that rejects every job. These messages end up in
    # container logs, so they name variables, never values.
    if token:
        try:
            token.encode()
        except UnicodeEncodeError:
            return f"{_TOKEN_ENV} is not valid UTF-8. Set it to a plain text value."
    if token and token != token.strip():
        return (
            f"{_TOKEN_ENV} is blank or has leading or trailing whitespace. Set it "
            "without any, and the same value as ALK_HARNESS_SANDBOX_TOKEN for the "
            "platform."
        )
    if token:
        return None
    if not allow:
        return (
            f"{_TOKEN_ENV} is not set. Set {_TOKEN_ENV} on the sandbox, and the "
            "same value as ALK_HARNESS_SANDBOX_TOKEN for the platform. For a "
            f"development server on your own laptop only, set {_ALLOW_ENV}=1 with "
            "a loopback ALK_SANDBOX_HOST."
        )
    if not _is_loopback(host):
        return (
            f"{_ALLOW_ENV}=1 works only on a loopback address, and ALK_SANDBOX_HOST "
            f"is {host!r}. Use 127.0.0.1 or ::1 (not localhost), or set {_TOKEN_ENV}."
        )
    return None


def _authorize(authorization: str | None = Header(default=None)) -> None:
    token = _sandbox_token()
    if token is not None:
        # Bytes, so a missing or non-ASCII header is a 401 rather than a TypeError.
        if not hmac.compare_digest(
            (authorization or "").encode(), f"Bearer {token}".encode()
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token"
            )
        return
    if _unauthenticated_allowed():
        return
    # The operator, not the caller, has to fix this, so it is not a 401.
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="sandbox_token_not_configured",
    )


def create_app(root: Path | None = None) -> FastAPI:
    # No schema or docs pages: they skip the token check, and a closed server shows nothing to
    # a caller without one.
    app = FastAPI(
        title="ALK local sandbox",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
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
        "/v1/secret-files",
        response_model=UploadedSecretFileResponse,
        dependencies=[Depends(_authorize)],
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_secret_file(
        file: UploadFile = File(...),
        environment_name: str = Form(...),
    ) -> UploadedSecretFileResponse:
        return await sandbox.upload_secret_file(file, environment_name)

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
        "/v1/jobs/{job_id}/rerun",
        response_model=SandboxJobResponse,
        dependencies=[Depends(_authorize)],
    )
    async def rerun_job(
        job_id: str, request: SandboxRerunRequest
    ) -> SandboxJobResponse:
        try:
            return sandbox.rerun(job_id, request)
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

    @app.post(
        "/v1/jobs/{job_id}/adjust",
        response_model=SandboxJobResponse,
        dependencies=[Depends(_authorize)],
    )
    async def adjust_job(
        job_id: str, request: SandboxAdjustmentRequest
    ) -> SandboxJobResponse:
        try:
            return sandbox.adjust(job_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    return app


app = create_app()


def main() -> None:
    # One value for both the check and the bind, so the check sees the real host.
    host = os.getenv("ALK_SANDBOX_HOST", "127.0.0.1")
    refusal = _startup_refusal(host, os.getenv(_TOKEN_ENV), _unauthenticated_allowed())
    if refusal is not None:
        print(refusal, file=sys.stderr)
        sys.exit(2)
    if _sandbox_token() is None:
        logger.warning(
            "unauthenticated sandbox: for a developer's own laptop only; any container "
            "on this machine, including the agent under test, can submit jobs and read "
            "this server's values"
        )

    import uvicorn

    uvicorn.run(
        "fi.alk.harness.sandbox_server:app",
        host=host,
        port=int(os.getenv("ALK_SANDBOX_PORT", "8788")),
    )


if __name__ == "__main__":
    main()
