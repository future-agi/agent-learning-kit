"""Versioned job and runtime contracts for the complete ALK harness workflow.

These contracts are control-plane neutral.  A local CLI creates the same ``HarnessJob`` as the
platform hosted-job API.  The platform may enqueue it, but only an ALK executor interprets its
stages.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Protocol

from pydantic import BaseModel, Field, JsonValue, model_validator

from fi.simulate.runtime.spec import RuntimeRequirements, SecretRef

HARNESS_JOB_SCHEMA_VERSION = "futureagi.harness-job.v1"


class ExecutionMode(str, Enum):
    LOCAL = "local"
    HOSTED = "hosted"


class SourceKind(str, Enum):
    LOCAL_REPOSITORY = "local_repository"
    GITHUB = "github"
    ARCHIVE = "archive"
    IMAGE = "image"
    REMOTE = "remote"


class RepositorySource(BaseModel):
    kind: SourceKind
    local_path: str | None = None
    installation_id: str | None = None
    repository: str | None = None
    ref: str | None = None
    archive_artifact_id: str | None = None
    image: str | None = None
    endpoint: str | None = None

    @model_validator(mode="after")
    def _required_locator(self) -> "RepositorySource":
        required = {
            SourceKind.LOCAL_REPOSITORY: self.local_path,
            SourceKind.GITHUB: self.installation_id and self.repository,
            SourceKind.ARCHIVE: self.archive_artifact_id,
            SourceKind.IMAGE: self.image,
            SourceKind.REMOTE: self.endpoint,
        }[self.kind]
        if not required:
            raise ValueError(f"source_locator_missing: {self.kind.value}")
        return self


class AgentConnection(BaseModel):
    connector: str
    config: dict[str, JsonValue] = Field(default_factory=dict)
    secret_refs: dict[str, SecretRef] = Field(default_factory=dict)


class ArtifactLevel(str, Enum):
    METADATA_ONLY = "metadata-only"
    TRACES = "traces"
    TRACES_AND_RECORDINGS = "traces-and-recordings"
    FULL = "full"
    LOCAL_ONLY = "local-only"


class HarnessArtifactPolicy(BaseModel):
    level: ArtifactLevel = ArtifactLevel.TRACES
    retention_days: int | None = Field(default=30, ge=1, le=3650)
    allow_bundle_download: bool = False
    max_artifact_bytes: int = Field(default=1_073_741_824, ge=0)


class HarnessJob(BaseModel):
    schema_version: str = HARNESS_JOB_SCHEMA_VERSION
    job_id: str
    run_id: str
    execution: ExecutionMode
    source: RepositorySource
    agent: AgentConnection
    scenario_count: int = Field(default=10, ge=1, le=1000)
    seed: int | None = None
    runtime: RuntimeRequirements = Field(default_factory=RuntimeRequirements)
    artifacts: HarnessArtifactPolicy = Field(default_factory=HarnessArtifactPolicy)
    platform_run_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_job(self) -> "HarnessJob":
        if self.schema_version != HARNESS_JOB_SCHEMA_VERSION:
            raise ValueError(f"harness_job_version_unsupported: {self.schema_version}")
        if (
            self.execution is ExecutionMode.LOCAL
            and self.source.kind is SourceKind.GITHUB
        ):
            # A local invocation may use a local clone or public URL through the repository
            # source adapter; it must not receive a platform GitHub installation credential.
            raise ValueError("local_job_cannot_use_platform_github_installation")
        if (
            self.execution is ExecutionMode.HOSTED
            and self.source.kind is SourceKind.LOCAL_REPOSITORY
        ):
            raise ValueError("hosted_job_cannot_use_local_path")
        _reject_secret_fields(self.model_dump(exclude={"agent": {"secret_refs"}}))
        return self


class HarnessStage(str, Enum):
    QUEUED = "queued"
    ACQUIRING_SOURCE = "acquiring_source"
    UNDERSTANDING_AGENT = "understanding_agent"
    GENERATING_ENVIRONMENT = "generating_environment"
    BUILDING_ENVIRONMENT = "building_environment"
    VALIDATING_ENVIRONMENT = "validating_environment"
    GENERATING_DATA = "generating_data"
    GENERATING_SCENARIOS = "generating_scenarios"
    VALIDATING_SCENARIOS = "validating_scenarios"
    CONNECTING_AGENT = "connecting_agent"
    RUNNING = "running"
    GRADING = "grading"
    UPLOADING_ARTIFACTS = "uploading_artifacts"
    CLEANING_UP = "cleaning_up"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"

    @property
    def terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELED}


class FailureDomain(str, Enum):
    AGENT = "agent"
    SIMULATOR = "simulator"
    ENVIRONMENT = "environment"
    CONNECTIVITY = "connectivity"
    INFRASTRUCTURE = "infrastructure"
    GRADING = "grading"
    PLATFORM_SYNC = "platform_sync"


class HarnessFailure(BaseModel):
    domain: FailureDomain
    stage: HarnessStage
    code: str
    message: str
    retryable: bool = False
    details: dict[str, JsonValue] = Field(default_factory=dict)


class HarnessJobStatus(BaseModel):
    job_id: str
    run_id: str
    stage: HarnessStage
    updated_at: datetime
    detail: str | None = None
    failure: HarnessFailure | None = None
    environment_digest: str | None = None
    scenario_set_digest: str | None = None
    completed_scenarios: int = Field(default=0, ge=0)
    total_scenarios: int = Field(default=0, ge=0)


class HostedHarnessPort(Protocol):
    """Scheduler-neutral interface implemented by the hosted ALK sandbox fleet."""

    async def submit(self, job: HarnessJob) -> HarnessJobStatus: ...

    async def status(self, job_id: str) -> HarnessJobStatus: ...

    async def cancel(self, job_id: str) -> None: ...


_SECRET_NAMES = {
    "api_key",
    "api_secret",
    "authorization",
    "credential",
    "credentials",
    "password",
    "private_key",
    "secret",
    "token",
}


def _reject_secret_fields(value: object, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            current = (*path, str(key))
            if normalized in _SECRET_NAMES and item not in (None, "", {}, []):
                raise ValueError("resolved_secret_forbidden: " + ".".join(current))
            _reject_secret_fields(item, current)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_fields(item, (*path, str(index)))


__all__ = [
    "HARNESS_JOB_SCHEMA_VERSION",
    "AgentConnection",
    "ArtifactLevel",
    "ExecutionMode",
    "FailureDomain",
    "HarnessArtifactPolicy",
    "HarnessFailure",
    "HarnessJob",
    "HarnessJobStatus",
    "HarnessStage",
    "HostedHarnessPort",
    "RepositorySource",
    "SourceKind",
]
