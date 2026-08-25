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

from fi.simulate.runtime.spec import RuntimeIsolation, RuntimeRequirements, SecretRef

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


class SourceVisibility(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"


class RepositorySource(BaseModel):
    kind: SourceKind
    local_path: str | None = None
    installation_id: str | None = None
    repository: str | None = None
    ref: str | None = None
    commit_sha: str | None = None
    visibility: SourceVisibility = SourceVisibility.PRIVATE
    archive_artifact_id: str | None = None
    image: str | None = None
    endpoint: str | None = None

    @model_validator(mode="after")
    def _required_locator(self) -> RepositorySource:
        required = {
            SourceKind.LOCAL_REPOSITORY: self.local_path,
            SourceKind.GITHUB: self.repository
            and (self.visibility is SourceVisibility.PUBLIC or self.installation_id),
            SourceKind.ARCHIVE: self.archive_artifact_id,
            SourceKind.IMAGE: self.image,
            SourceKind.REMOTE: self.endpoint,
        }[self.kind]
        if not required:
            raise ValueError(f"source_locator_missing: {self.kind.value}")
        if self.kind is SourceKind.GITHUB and self.commit_sha:
            normalized = self.commit_sha.lower()
            if len(normalized) != 40 or any(
                char not in "0123456789abcdef" for char in normalized
            ):
                raise ValueError("github_commit_sha_invalid")
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


class SandboxSecurityPolicy(BaseModel):
    """Security invariants a provider must satisfy before executing customer code."""

    untrusted_source: bool = True
    read_only_source: bool = True
    allow_privileged: bool = False
    allow_host_runtime_control: bool = False
    allowed_egress_domains: list[str] = Field(default_factory=list)


class HarnessRetryPolicy(BaseModel):
    """Conservative job retry policy; agent behavior is intentionally absent."""

    max_infrastructure_attempts: int = Field(default=2, ge=1, le=5)
    initial_backoff_seconds: float = Field(default=1.0, ge=0, le=60)
    max_backoff_seconds: float = Field(default=15.0, ge=0, le=300)
    retryable_domains: list[str] = Field(
        default_factory=lambda: ["infrastructure", "connectivity"]
    )

    @model_validator(mode="after")
    def _valid_retry_policy(self) -> HarnessRetryPolicy:
        allowed = {"infrastructure", "connectivity", "platform_sync"}
        unsupported = set(self.retryable_domains) - allowed
        if unsupported:
            raise ValueError("retry_domain_unsafe: " + ", ".join(sorted(unsupported)))
        if self.max_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("retry_backoff_invalid")
        return self


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
    security: SandboxSecurityPolicy = Field(default_factory=SandboxSecurityPolicy)
    retry: HarnessRetryPolicy = Field(default_factory=HarnessRetryPolicy)
    artifacts: HarnessArtifactPolicy = Field(default_factory=HarnessArtifactPolicy)
    platform_run_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_job(self) -> HarnessJob:
        if self.schema_version != HARNESS_JOB_SCHEMA_VERSION:
            raise ValueError(f"harness_job_version_unsupported: {self.schema_version}")
        if (
            self.execution is ExecutionMode.LOCAL
            and self.source.kind is SourceKind.GITHUB
            and (
                self.source.visibility is not SourceVisibility.PUBLIC
                or self.source.installation_id
            )
        ):
            # A local runner may clone public source, but it must never receive a platform
            # GitHub installation credential. Private source is acquired by a hosted provider.
            raise ValueError("local_job_cannot_use_private_platform_github_source")
        if (
            self.execution is ExecutionMode.HOSTED
            and self.source.kind is SourceKind.LOCAL_REPOSITORY
        ):
            raise ValueError("hosted_job_cannot_use_local_path")
        if self.execution is ExecutionMode.HOSTED:
            if self.source.kind is SourceKind.IMAGE:
                raise ValueError("image_source_not_hosted")
            if self.source.kind is SourceKind.GITHUB and not self.source.commit_sha:
                raise ValueError("github_commit_sha_required")
            if self.scenario_count > 10:
                raise ValueError("hosted_scenario_count_out_of_range")
            if self.runtime.isolation is not RuntimeIsolation.DEDICATED_VM:
                raise ValueError("hosted_isolation_must_be_dedicated_vm")
            if self.runtime.parallelism > self.runtime.cpu_units:
                raise ValueError("hosted_parallelism_exceeds_cpu")
            if self.artifacts.level is ArtifactLevel.LOCAL_ONLY:
                raise ValueError("local_only_not_hosted")
            for alias, reference in self.agent.secret_refs.items():
                if reference.manager != "platform-vault":
                    raise ValueError(
                        f"hosted_secret_manager_unsupported: {alias}"
                    )
                if reference.purpose != "target_provider":
                    raise ValueError(
                        f"hosted_secret_purpose_invalid: {alias}"
                    )
            if self.security.allow_privileged:
                raise ValueError("hosted_privileged_execution_forbidden")
            if self.security.allow_host_runtime_control:
                raise ValueError("hosted_runtime_control_forbidden")
            if not self.security.read_only_source:
                raise ValueError("hosted_source_must_be_read_only")
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
    attempt: int = Field(default=1, ge=1)


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
    "HarnessRetryPolicy",
    "HarnessStage",
    "HostedHarnessPort",
    "RepositorySource",
    "SandboxSecurityPolicy",
    "SourceKind",
    "SourceVisibility",
]
