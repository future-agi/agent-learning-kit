"""One deterministic admission decision shared by preflight and provisioning.

This is deliberately separate from :mod:`environment_plan`, which is the sealed description of
an already provisioned bundle.  A resolved plan is produced before Docker is touched and records
which adapter is allowed to create that bundle.
"""

from __future__ import annotations

import hashlib
import json
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from .generated_runtime import GeneratedRuntimeError, detect_generated_runtime
from .packaging import (
    PackagingCandidate,
    PackagingKind,
    PackagingManifest,
    inspect_packaging,
)


ENVIRONMENT_RESOLUTION_SCHEMA_VERSION = "futureagi.environment-resolution.v1"
ENVIRONMENT_RESOLUTION_FILE = "environment-resolution.json"


class DependencyDecision(BaseModel):
    name: str
    engine: str = ""
    ownership: Literal[
        "submitted_compose",
        "managed_service",
        "agent_runtime",
        "external_provider",
        "embedded",
        "unsupported",
    ]


class ResolvedEnvironmentPlan(BaseModel):
    schema_version: str = ENVIRONMENT_RESOLUTION_SCHEMA_VERSION
    digest: str
    source_fingerprint: str = ""
    contract_digest: str = ""
    harness_version: str
    packaging_type: Literal["compose", "dockerfile", "unpackaged"]
    runtime_adapter: Literal[
        "submitted_compose",
        "managed_compose_for_dockerfile",
        "generated_runtime",
        "unsupported",
    ]
    selected_runtime: str = ""
    component_root: str = "."
    build_context: str = "."
    dependencies: list[DependencyDecision] = Field(default_factory=list)
    required_credentials: list[str] = Field(default_factory=list)
    supported: bool
    execution_ready: bool
    code: str = ""
    message: str = ""
    action: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> "ResolvedEnvironmentPlan":
        if self.supported == (self.runtime_adapter == "unsupported"):
            raise ValueError("environment_resolution_support_adapter_mismatch")
        names = [item.name for item in self.dependencies]
        if len(names) != len(set(names)):
            raise ValueError("environment_resolution_dependency_ownership_not_unique")
        if self.supported and any(
            item.ownership == "unsupported" for item in self.dependencies
        ):
            raise ValueError("environment_resolution_unsupported_dependency")
        if self.required_credentials != sorted(set(self.required_credentials)):
            raise ValueError("environment_resolution_credentials_not_canonical")
        if _resolution_digest(self.model_dump(mode="json")) != self.digest:
            raise ValueError("environment_resolution_digest_mismatch")
        return self


def _canonical_digest(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    elif hasattr(value, "__dict__"):
        value = {
            key: _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dict__"):
        return {
            key: _jsonable(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    return value


def _resolution_digest(raw: dict[str, Any]) -> str:
    canonical = dict(raw)
    canonical.pop("digest", None)
    return _canonical_digest(canonical)


def _harness_version() -> str:
    try:
        return version("futureagi")
    except PackageNotFoundError:
        return "source-checkout"


def _candidate(
    packaging: PackagingManifest, kind: PackagingKind, path: str
) -> PackagingCandidate | None:
    normalized = Path(path).as_posix()
    return next(
        (
            item
            for item in packaging.candidates
            if item.kind is kind and item.path == normalized
        ),
        None,
    )


def _blocking(
    candidate: PackagingCandidate | None, *, contract: Any | None
) -> list[str]:
    if candidate is None:
        return []
    return [
        finding.message
        for finding in candidate.findings
        if finding.blocking
        and not (finding.code == "compose_env_file_missing" and contract is not None)
    ]


def _dependency_decisions(
    contract: Any | None, packaging_type: str
) -> list[DependencyDecision]:
    decisions: list[DependencyDecision] = []
    managed = {
        "clickhouse",
        "postgres",
        "mysql",
        "redis",
        "mongodb",
        "mongo",
        "qdrant",
        "rabbitmq",
        "nats",
        "minio",
    }
    providers = {
        "openai",
        "anthropic",
        "gemini",
        "vertex",
        "deepgram",
        "cartesia",
        "elevenlabs",
        "livekit",
        "retell",
        "vapi",
        "daily",
    }
    for index, dependency in enumerate(
        list(getattr(contract, "dependencies", None) or [])
    ):
        name = str(getattr(dependency, "name", "") or f"dependency-{index + 1}")
        engine = str(getattr(dependency, "engine", "") or "").lower()
        reached = getattr(dependency, "reached", None)
        external_configuration = bool(
            reached
            and any(
                str(getattr(reached, field, "") or "").strip()
                for field in ("dsn_env", "config_key", "password_from")
            )
            and not str(getattr(reached, "database", "") or "").strip()
        )
        description = (
            " ".join(
                str(getattr(dependency, field, "") or "")
                for field in ("name", "engine", "kind", "what")
            )
            .lower()
            .replace("-", "_")
        )
        if packaging_type == "compose":
            ownership = "submitted_compose"
        elif any(item in description for item in managed):
            ownership = "managed_service"
        elif any(item in description for item in providers):
            ownership = "external_provider"
        elif external_configuration:
            # A credential/configuration seam without a database identifies a customer-owned
            # remote API. ALK supplies the reference when configured; it must not try to create
            # that SaaS service or reject an agent that has its own in-process fallback.
            ownership = "external_provider"
        elif reached and (
            str(getattr(reached, "loader_module", "") or "")
            or str(getattr(reached, "loader_function", "") or "")
            or any(
                marker in description
                for marker in (
                    "sqlite",
                    "in_process",
                    "in_memory",
                    "filesystem",
                    "local_model",
                )
            )
        ):
            ownership = "embedded"
        elif packaging_type == "dockerfile" and engine:
            ownership = "agent_runtime"
        else:
            ownership = "unsupported"
        decisions.append(
            DependencyDecision(name=name, engine=engine, ownership=ownership)
        )
    return decisions


def _credential_names(contract: Any | None) -> list[str]:
    names: set[str] = set()
    store = getattr(contract, "data_store", None)
    values: list[Any] = [store]
    values.extend(
        getattr(item, "reached", None)
        for item in list(getattr(contract, "dependencies", None) or [])
    )
    for value in values:
        if value is None:
            continue
        for field in ("dsn_env", "config_key", "password_from"):
            candidate = str(getattr(value, field, "") or "").strip()
            if (
                candidate
                and candidate.replace("_", "").isalnum()
                and candidate.upper() == candidate
            ):
                names.add(candidate)
    return sorted(names)


def resolve_environment_plan(
    source: str | Path,
    packaging: PackagingManifest | None = None,
    contract: Any | None = None,
    *,
    source_fingerprint: str = "",
) -> ResolvedEnvironmentPlan:
    """Resolve exactly one provisioner without executing source or Docker."""
    root = Path(source).expanduser().resolve()
    packaging = packaging or inspect_packaging(root)
    runtime = getattr(contract, "runtime", None)
    explicit_compose = str(getattr(runtime, "compose_file", "") or "")
    explicit_dockerfile = str(getattr(runtime, "dockerfile", "") or "")
    selected = ""
    packaging_type = "unpackaged"
    adapter = "generated_runtime"
    component = str(getattr(runtime, "workdir", "") or ".")
    build_context = component
    supported = True
    execution_ready = True
    code = message = action = ""

    if explicit_compose:
        candidate = _candidate(packaging, PackagingKind.COMPOSE, explicit_compose)
        selected = Path(explicit_compose).as_posix()
        packaging_type, adapter = "compose", "submitted_compose"
        if candidate is None:
            supported = False
            code = "runtime_compose_not_admitted"
            message = f"Runtime Compose file is not an admitted repository candidate: {selected}"
            action = "Select a discovered Compose file within the submitted repository."
        elif problems := _blocking(candidate, contract=contract):
            supported = False
            code = "packaging_preflight_failed"
            message = "; ".join(problems)
            action = "Resolve the reported Compose admission findings and retry."
    elif explicit_dockerfile:
        candidate = _candidate(packaging, PackagingKind.DOCKERFILE, explicit_dockerfile)
        selected = Path(explicit_dockerfile).as_posix()
        packaging_type, adapter = "dockerfile", "managed_compose_for_dockerfile"
        if not (root / selected).is_file():
            supported = False
            code = "runtime_dockerfile_missing"
            message = f"Runtime Dockerfile does not exist: {selected}"
            action = "Provide the Dockerfile in the submitted repository or correct runtime.dockerfile."
        elif problems := _blocking(candidate, contract=contract):
            supported = False
            code = "packaging_preflight_failed"
            message = "; ".join(problems)
            action = "Resolve the reported Dockerfile admission findings and retry."
    elif packaging.selected_kind is PackagingKind.COMPOSE and packaging.selected_path:
        selected = packaging.selected_path
        packaging_type, adapter = "compose", "submitted_compose"
        if contract is None and not packaging.agent_runtime_packaged:
            execution_ready = False
            code = "compose_agent_runtime_missing"
            message = "Compose describes infrastructure but no packaged agent runtime."
            action = "Package the agent runtime or submit a source that can use the generated runtime adapter."
    elif (
        packaging.selected_kind is PackagingKind.DOCKERFILE and packaging.selected_path
    ):
        selected = packaging.selected_path
        packaging_type, adapter = "dockerfile", "managed_compose_for_dockerfile"
    elif packaging.candidates:
        supported = False
        adapter = "unsupported"
        code = "packaging_selection_required"
        details = list(packaging.notes)
        details.extend(
            finding.message
            for candidate in packaging.candidates
            for finding in candidate.findings
            if finding.blocking
        )
        message = (
            "; ".join(details) or "Repository packaging is ambiguous or unsupported."
        )
        action = "Select one runnable component/build context or fix the blocking packaging findings."
    else:
        try:
            generated = detect_generated_runtime(root, runtime)
            selected = generated.dependency_file
            component = generated.component or "."
            build_context = component
        except GeneratedRuntimeError as exc:
            if contract is None:
                # Understanding may still prove a remote/in-process agent. Submission is allowed,
                # but the same resolver is called again with that contract before provisioning.
                selected = "pending-contract"
            else:
                supported = False
                adapter = "unsupported"
                code = str(exc).split(":", 1)[0]
                managed_dependencies = [
                    item.engine
                    for item in _dependency_decisions(contract, "unpackaged")
                    if item.ownership == "managed_service" and item.engine
                ]
                message = (
                    "the agent requires "
                    + ", ".join(managed_dependencies)
                    + " but ships neither Compose nor a Dockerfile; "
                    + str(exc)
                    if managed_dependencies
                    else str(exc)
                )
                action = "Add supported packaging or declare an unambiguous Python/Node runtime."

    dependencies = _dependency_decisions(contract, packaging_type)
    unsupported = [
        item.name for item in dependencies if item.ownership == "unsupported"
    ]
    if supported and unsupported:
        supported = False
        adapter = "unsupported"
        code = "unsupported_dependency"
        message = "No environment owner could be determined for: " + ", ".join(
            unsupported
        )
        action = "Package the dependency in Compose or expose a supported configuration seam."
    if not supported:
        execution_ready = False

    raw: dict[str, Any] = {
        "schema_version": ENVIRONMENT_RESOLUTION_SCHEMA_VERSION,
        "digest": "sha256:" + "0" * 64,
        "source_fingerprint": source_fingerprint,
        "contract_digest": _canonical_digest(contract) if contract is not None else "",
        "harness_version": _harness_version(),
        "packaging_type": packaging_type,
        "runtime_adapter": adapter,
        "selected_runtime": selected or "automatic",
        "component_root": component or ".",
        "build_context": build_context or ".",
        "dependencies": [item.model_dump(mode="json") for item in dependencies],
        "required_credentials": _credential_names(contract),
        "supported": supported,
        "execution_ready": execution_ready,
        "code": code,
        "message": message,
        "action": action,
    }
    raw["digest"] = _resolution_digest(raw)
    return ResolvedEnvironmentPlan.model_validate(raw)


def write_environment_resolution(
    root: str | Path, plan: ResolvedEnvironmentPlan
) -> Path:
    target = Path(root) / ENVIRONMENT_RESOLUTION_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(plan.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def load_environment_resolution(root: str | Path) -> ResolvedEnvironmentPlan:
    target = Path(root) / ENVIRONMENT_RESOLUTION_FILE
    if not target.is_file():
        raise ValueError(f"environment_resolution_missing: {target}")
    try:
        return ResolvedEnvironmentPlan.model_validate_json(
            target.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise ValueError(f"environment_resolution_invalid: {exc}") from exc


__all__ = [
    "DependencyDecision",
    "ENVIRONMENT_RESOLUTION_FILE",
    "ResolvedEnvironmentPlan",
    "load_environment_resolution",
    "resolve_environment_plan",
    "write_environment_resolution",
]
