"""Portable, immutable environment bundles shared by local and hosted ALK.

The harness may discover an environment in a repository or generate missing pieces, but every
runtime receives the same sealed representation.  A bundle contains no resolved customer
secrets.  Runtime credentials are injected from ``SecretRef`` values when a job starts.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field, JsonValue, model_validator

BUNDLE_SCHEMA_VERSION = "futureagi.environment-bundle.v1"
BUNDLE_MANIFEST = "manifest.json"


class BundleError(RuntimeError):
    """A bundle is unsafe, incomplete, or no longer matches its digest."""


class RuntimeKind(str, Enum):
    COMPOSE = "compose"
    PROCESS = "process"
    EXTERNAL = "external"


class CapabilityProtocol(str, Enum):
    HTTP = "http"
    MCP = "mcp"
    POSTGRES = "postgres"
    SQLITE = "sqlite"
    LIVEKIT = "livekit"
    TCP = "tcp"


class BundleRuntime(BaseModel):
    kind: RuntimeKind
    document: str | None = None
    control_service: str | None = None
    command: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_entrypoint(self) -> "BundleRuntime":
        if self.kind is RuntimeKind.COMPOSE and not self.document:
            raise ValueError("compose runtime requires a document")
        if self.kind is RuntimeKind.PROCESS and not self.command:
            raise ValueError("process runtime requires a command")
        if self.document:
            _safe_relative(self.document)
        return self


class Capability(BaseModel):
    protocol: CapabilityProtocol
    service: str | None = None
    container_port: int | None = Field(default=None, ge=1, le=65535)
    path: str | None = None
    configuration_name: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class ReadinessProbe(BaseModel):
    capability: str
    path: str | None = None
    timeout_seconds: float = Field(default=120.0, gt=0, le=1800)
    interval_seconds: float = Field(default=1.0, gt=0, le=60)


class BundleProvenance(BaseModel):
    source_kind: str
    repository: str | None = None
    commit: str | None = None
    source_digest: str
    generator: str = "fi.alk.harness"
    generator_version: str = "1"
    adopted_files: list[str] = Field(default_factory=list)
    generated_files: list[str] = Field(default_factory=list)


class BundleFile(BaseModel):
    path: str
    sha256: str
    size: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid_path(self) -> "BundleFile":
        _safe_relative(self.path)
        return self


class EnvironmentBundle(BaseModel):
    schema_version: str = BUNDLE_SCHEMA_VERSION
    name: str
    digest: str
    runtime: BundleRuntime
    services: list[str] = Field(default_factory=list)
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    readiness: list[ReadinessProbe] = Field(default_factory=list)
    provenance: BundleProvenance
    files: list[BundleFile] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_manifest(self) -> "EnvironmentBundle":
        if self.schema_version != BUNDLE_SCHEMA_VERSION:
            raise ValueError(f"bundle_schema_unsupported: {self.schema_version}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.digest):
            raise ValueError("bundle_digest_invalid")
        missing = [
            p.capability
            for p in self.readiness
            if p.capability not in self.capabilities
        ]
        if missing:
            raise ValueError(
                "readiness_capability_missing: " + ", ".join(sorted(set(missing)))
            )
        _reject_secret_values(self.model_dump(exclude={"digest"}))
        return self


_SECRET_FIELD = re.compile(
    r"(^|_)(api_?key|api_?secret|authorization|credential|password|private_?key|secret|token)(_|$)",
    re.IGNORECASE,
)
_SECRET_FILES = {".env", ".env.local", ".npmrc", ".pypirc", "credentials", "id_rsa"}
_SECRET_CONTENT = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\b(?:ghp|github_pat)_[A-Za-z0-9_]{20,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}\b"),
)


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not value or ".." in path.parts:
        raise ValueError(f"bundle_path_unsafe: {value}")
    return path


def _reject_secret_values(value: Any, path: tuple[str, ...] = ()) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            current = (*path, str(key))
            # References and configuration variable names are identifiers, never values.
            if str(key) in {"secret_refs", "configuration_name"}:
                continue
            if _SECRET_FIELD.search(str(key)) and item not in (None, "", {}, []):
                raise ValueError("resolved_secret_forbidden: " + ".".join(current))
            _reject_secret_values(item, current)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_secret_values(item, (*path, str(index)))


def _iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.name == BUNDLE_MANIFEST or path.is_dir():
            continue
        if path.is_symlink():
            raise BundleError(f"bundle_symlink_forbidden: {path.relative_to(root)}")
        if not path.is_file():
            raise BundleError(f"bundle_entry_unsupported: {path.relative_to(root)}")
        files.append(path)
    return files


def _inspect_file(root: Path, path: Path) -> BundleFile:
    relative = path.relative_to(root).as_posix()
    _safe_relative(relative)
    if path.name in _SECRET_FILES or path.suffix.lower() in {
        ".pem",
        ".key",
        ".p12",
        ".pfx",
    }:
        raise BundleError(f"bundle_secret_file_forbidden: {relative}")
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
            if any(pattern.search(chunk) for pattern in _SECRET_CONTENT):
                raise BundleError(f"bundle_secret_material_detected: {relative}")
    return BundleFile(path=relative, sha256=digest.hexdigest(), size=size)


def _bundle_digest(manifest: dict[str, Any], files: list[BundleFile]) -> str:
    core = dict(manifest)
    core.pop("digest", None)
    core.pop("files", None)
    digest = hashlib.sha256(
        json.dumps(
            core, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    )
    for record in files:
        encoded = record.model_dump_json().encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return "sha256:" + digest.hexdigest()


def seal_bundle(
    root: str | Path, manifest: EnvironmentBundle | dict[str, Any]
) -> EnvironmentBundle:
    """Validate and seal a prepared bundle directory with a content digest.

    The operation is idempotent: sealing unchanged contents produces the same manifest and
    digest.  ``manifest.json`` is replaced atomically only after all validation succeeds.
    """
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise BundleError(f"bundle_root_missing: {root}")
    raw = (
        manifest.model_dump(mode="json")
        if isinstance(manifest, EnvironmentBundle)
        else dict(manifest)
    )
    raw.setdefault("schema_version", BUNDLE_SCHEMA_VERSION)
    raw["digest"] = "sha256:" + "0" * 64
    raw["files"] = []
    files = [_inspect_file(root, path) for path in _iter_files(root)]
    raw["files"] = [record.model_dump(mode="json") for record in files]
    # Hash the fully normalized representation. Optional/default fields must participate in the
    # same way both before and after a JSON round trip or verification will disagree with seal.
    normalized = EnvironmentBundle.model_validate(raw).model_dump(mode="json")
    normalized["digest"] = _bundle_digest(normalized, files)
    sealed = EnvironmentBundle.model_validate(normalized)
    target = root / BUNDLE_MANIFEST
    temporary = root / f".{BUNDLE_MANIFEST}.tmp-{os.getpid()}"
    temporary.write_text(sealed.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)
    return sealed


def load_bundle(root: str | Path, *, verify: bool = True) -> EnvironmentBundle:
    root = Path(root).expanduser().resolve()
    target = root / BUNDLE_MANIFEST
    if not target.is_file():
        raise BundleError(f"bundle_manifest_missing: {target}")
    try:
        bundle = EnvironmentBundle.model_validate_json(
            target.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise BundleError(f"bundle_manifest_invalid: {exc}") from exc
    if verify:
        actual = [_inspect_file(root, path) for path in _iter_files(root)]
        expected = [item.model_dump() for item in bundle.files]
        if [item.model_dump() for item in actual] != expected:
            raise BundleError("bundle_files_changed")
        digest = _bundle_digest(bundle.model_dump(mode="json"), actual)
        if digest != bundle.digest:
            raise BundleError("bundle_digest_mismatch")
    return bundle


_COPY_IGNORED = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "node_modules",
}


def export_session_bundle(
    source: str | Path,
    session: str | Path,
    *,
    name: str,
) -> tuple[Path, EnvironmentBundle]:
    """Export the environment discovered/generated for a harness session.

    The source is copied into the internal bundle because Compose build contexts must survive
    deletion of a hosted executor's repository clone. Customer dotenv files, VCS metadata,
    caches, and prior artifacts are excluded. The secret scanner then validates every retained
    byte before the staging directory is atomically promoted.
    """
    from .provision import ProvisionedEnvironment, compose_file, source_fingerprint

    source = Path(source).expanduser().resolve()
    session = Path(session).expanduser().resolve()
    if not source.is_dir():
        raise BundleError(f"bundle_source_missing: {source}")
    session.mkdir(parents=True, exist_ok=True)
    final = session / "environment-bundle"
    staging_parent = Path(tempfile.mkdtemp(prefix=".bundle-", dir=session))
    staging = staging_parent / "environment-bundle"
    staged_source = staging / "services" / "source"
    adopted: list[str] = []
    generated: list[str] = []
    try:
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if any(part in _COPY_IGNORED for part in relative.parts):
                continue
            if (
                path.name in _SECRET_FILES
                or path.name.startswith(".env.")
                and path.name != ".env.example"
            ):
                continue
            target = staged_source / relative
            if path.is_symlink():
                raise BundleError(f"bundle_source_symlink_forbidden: {relative}")
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                adopted.append((Path("services/source") / relative).as_posix())

        for artifact in (
            "contract.json",
            "sub_goals.json",
            "simulator_prompt.md",
            "world.json",
            "world.py",
        ):
            origin = session / artifact
            if not origin.is_file():
                continue
            target = staging / artifact
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
            generated.append(artifact)

        provisioned = ProvisionedEnvironment.load(session)
        services = list(provisioned.services) if provisioned else []
        configuration_names = sorted(
            (provisioned.overrides if provisioned else {}).keys()
        )
        capabilities = {
            "agent_tools": Capability(
                protocol=CapabilityProtocol.HTTP,
                service=(
                    provisioned.services[-1]
                    if provisioned and provisioned.services
                    else None
                ),
                configuration_name=(
                    configuration_names[0] if len(configuration_names) == 1 else None
                ),
            )
        }
        compose = compose_file(source)
        if compose is None and provisioned and provisioned.compose_file:
            compose = Path(provisioned.compose_file)
        if compose is None:
            runtime = BundleRuntime(kind=RuntimeKind.EXTERNAL)
        else:
            try:
                document = (
                    Path("services/source") / compose.relative_to(source)
                ).as_posix()
            except ValueError:
                # Managed Compose is generated into the session, not the repository.
                document = "compose.json"
                shutil.copy2(compose, staging / document)
                generated.append(document)
            runtime = BundleRuntime(kind=RuntimeKind.COMPOSE, document=document)

        raw = {
            "schema_version": BUNDLE_SCHEMA_VERSION,
            "name": name,
            "digest": "sha256:" + "0" * 64,
            "runtime": runtime.model_dump(mode="json"),
            "services": services,
            "capabilities": {
                key: value.model_dump(mode="json", exclude_none=True)
                for key, value in capabilities.items()
            },
            "readiness": (
                [{"capability": "agent_tools", "path": "/health"}]
                if provisioned and provisioned.overrides
                else []
            ),
            "provenance": {
                "source_kind": "repository",
                "repository": source.name,
                "source_digest": source_fingerprint(source),
                "adopted_files": adopted,
                "generated_files": generated,
            },
        }
        seal_bundle(staging, raw)
        if final.exists():
            shutil.rmtree(final)
        staging.replace(final)
        return final, load_bundle(final)
    except Exception:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent, ignore_errors=True)


__all__ = [
    "BUNDLE_MANIFEST",
    "BUNDLE_SCHEMA_VERSION",
    "BundleError",
    "BundleFile",
    "BundleProvenance",
    "BundleRuntime",
    "Capability",
    "CapabilityProtocol",
    "EnvironmentBundle",
    "ReadinessProbe",
    "RuntimeKind",
    "load_bundle",
    "export_session_bundle",
    "seal_bundle",
]
