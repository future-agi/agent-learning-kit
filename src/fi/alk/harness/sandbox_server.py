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
import re
import shutil
import signal
import sys
from typing import Any
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field, model_validator

from fi.simulate.runtime.spec import SecretRef

from .credentials import (
    CredentialManifest,
    CredentialRequirement,
    RequirementKind,
    RequirementStatus,
    discover_credentials,
)
from .executor import GitHubSourceAcquirer, SourceAcquisitionError
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
from .provision import source_fingerprint
from .secrets import resolve_worker_secrets, worker_environment


class LocalSandboxRequest(BaseModel):
    source_path: str | None = None
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
    platform_run_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_source(self) -> "LocalSandboxRequest":
        if bool(self.source_path) == bool(self.github_repository):
            raise ValueError("exactly_one_source_required")
        return self


class SandboxPreflightRequest(BaseModel):
    source_path: str | None = None
    github_repository: str | None = None
    github_visibility: SourceVisibility = SourceVisibility.PUBLIC
    github_installation_id: str | None = None
    connector_config: dict[str, Any] = Field(default_factory=dict)
    secret_refs: dict[str, SecretRef] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _one_source(self) -> "SandboxPreflightRequest":
        if bool(self.source_path) == bool(self.github_repository):
            raise ValueError("exactly_one_source_required")
        return self


class SandboxPreflightResponse(BaseModel):
    source_kind: SourceKind
    source_label: str
    ready_to_submit: bool
    checkout_required: bool = False
    credentials: CredentialManifest
    notes: list[str] = Field(default_factory=list)


class SandboxJobResponse(BaseModel):
    job: HarnessJob
    status: HarnessJobStatus
    events: list[dict[str, Any]] = Field(default_factory=list)
    artifact_path: str | None = None
    credentials: CredentialManifest | None = None


class LocalSandbox:
    def __init__(self, root: Path, *, max_concurrency: int = 2) -> None:
        self.root = root.expanduser().resolve()
        self.jobs_root = self.root / "jobs"
        self.artifacts_root = self.root / "artifacts"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._recover_orphans()

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
        source = _allowed_source(request.source_path) if request.source_path else None
        identifier = str(uuid.uuid4())
        run_id = f"harness-{identifier}"
        github_repository = (
            _github_repository(request.github_repository)
            if request.github_repository
            else None
        )
        source_spec = (
            RepositorySource(kind=SourceKind.LOCAL_REPOSITORY, local_path=str(source))
            if source
            else RepositorySource(
                kind=SourceKind.GITHUB,
                repository=github_repository,
                ref=request.github_ref,
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
                if request.github_repository
                and request.github_visibility is SourceVisibility.PRIVATE
                else ExecutionMode.LOCAL
            ),
            source=source_spec,
            agent=AgentConnection(
                connector=request.connector,
                config=request.connector_config,
                secret_refs=request.secret_refs,
            ),
            scenario_count=request.scenario_count,
            seed=request.seed,
            artifacts=HarnessArtifactPolicy(level="full", allow_bundle_download=True),
            platform_run_id=request.platform_run_id,
            metadata={
                **request.metadata,
                "agent_name": request.agent_name
                or (source.name if source else str(github_repository).split("/")[-1]),
                "source_kind": source_spec.kind.value,
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
        self._tasks[identifier] = asyncio.create_task(self._execute(job, source))
        return self.get(identifier)

    def preflight(self, request: SandboxPreflightRequest) -> SandboxPreflightResponse:
        if request.source_path:
            source = _allowed_source(request.source_path)
            manifest = discover_credentials(
                source,
                secret_refs=request.secret_refs,
                provided_environment=request.connector_config,
            )
            return SandboxPreflightResponse(
                source_kind=SourceKind.LOCAL_REPOSITORY,
                source_label=str(source),
                ready_to_submit=manifest.ready,
                credentials=manifest,
            )
        repository = _github_repository(request.github_repository or "")
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
                credential_manifest = discover_credentials(
                    source,
                    secret_refs=job.agent.secret_refs,
                    provided_environment=job.agent.config,
                )
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
                resolved_secrets = resolve_worker_secrets(job.agent.secret_refs)
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
                        env=worker_environment(
                            {
                                **_configuration_environment(job.agent.config),
                                **resolved_secrets,
                            }
                        ),
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


def _github_repository(raw: str) -> str:
    repository = raw.strip()
    prefix = "https://github.com/"
    if repository.startswith(prefix):
        repository = repository[len(prefix) :]
    repository = repository.removesuffix(".git").strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise HTTPException(status_code=400, detail="github_repository is invalid")
    return repository


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
