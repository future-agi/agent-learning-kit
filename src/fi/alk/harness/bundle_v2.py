"""`futureagi.environment-bundle.v2` — the hosted provisioner's manifest shape.

v1 (`bundle.py`) describes a `command`-per-service compose world and embeds the repository
source. v2 describes `/work/source` as already present and a job that starts plain processes on
localhost: `processes` (managed engines and copied-and-built source trees), `seed` (how each
store's baseline is built and proven), and the same `capabilities`/`readiness`/`files`/
`provenance` shape widened for both. v1 stays untouched — this module is additive, not a
replacement, and the two schema versions are never interchangeable: a hosted provisioner that
receives a `…bundle.v1` manifest rejects it rather than guessing.

What lives here is model-layer only: the shapes, the closed vocabularies, and the rules that need
nothing but the manifest's own fields to decide. Rules that need the bundle's actual files (secret
scanning, digest/file verification) or the job it will run under (`compose_not_hosted`,
`engine_unsupported`, `no_sql_store`, the `depends_on` graph, placeholder-vocabulary checking,
reserved-name scanning of migration content) are the §2e preflight checklist's job, not this
module's — see `hosted-execution-seams.md` §2e. Also deferred to that preflight: translating
pydantic's `extra="forbid"` rejection of an unknown process-entry key into §2b's `unknown_field`
code — that translation belongs where error surfacing is owned, not here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal, Sequence, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

from .bundle import CapabilityProtocol, _reject_secret_values, _safe_relative

BUNDLE_V2_SCHEMA_VERSION = "futureagi.environment-bundle.v2"
BUNDLE_V2_MANIFEST = "manifest.json"


class BundleV2Error(RuntimeError):
    """A v2 bundle manifest is wrong-versioned, malformed, or fails a model-layer rule."""


# --- §2a runtime -------------------------------------------------------------------------------


class RuntimeKindV2(str, Enum):
    PROCESS = "process"
    EXTERNAL = "external"
    COMPOSE = "compose"


class EvidenceSeam(str, Enum):
    HTTP_TOOL = "http_tool"
    TOOL_TRACE = "tool_trace"


class BundleRuntimeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: RuntimeKindV2
    control_service: str | None = None
    evidence_seam: EvidenceSeam | None = None
    # Carried over from v1 for `kind: compose` only (local SDK runs); a hosted `process` bundle
    # has no document to point at, since `/work/source` is already on disk.
    document: str | None = None

    @model_validator(mode="after")
    def _kind_specific_rules(self) -> "BundleRuntimeV2":
        if self.kind is RuntimeKindV2.PROCESS and self.evidence_seam is None:
            raise ValueError("evidence_seam_required: kind=process")
        if self.kind is RuntimeKindV2.COMPOSE and not self.document:
            raise ValueError("compose_runtime_requires_document")
        if self.kind is not RuntimeKindV2.COMPOSE and self.document is not None:
            raise ValueError("document_only_for_compose")
        if self.document:
            _safe_relative(self.document)
        return self


# --- §2b processes -------------------------------------------------------------------------


class ProcessKind(str, Enum):
    MANAGED = "managed"
    SOURCE = "source"


class ManagedEngine(str, Enum):
    POSTGRES = "postgres"
    REDIS = "redis"
    RABBITMQ = "rabbitmq"


class ProcessUser(str, Enum):
    """The snapshot's fixed, bundle-assignable users (§0). `svc-control` runs ALK itself and is
    never a process's own user."""

    SVC_AGENT = "svc-agent"
    SVC_TOOLS = "svc-tools"
    SVC_DATA = "svc-data"


class SecretPurpose(str, Enum):
    TARGET_PROVIDER = "target_provider"
    SOURCE_CHECKOUT = "source_checkout"


class StartedCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    port: int | None = Field(default=None, ge=1, le=65535)
    log_marker: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0)

    @model_validator(mode="after")
    def _exactly_one_probe(self) -> "StartedCheck":
        if (self.port is None) == (self.log_marker is None):
            raise ValueError("started_check_requires_exactly_one_of_port_or_log_marker")
        return self


class ManagedProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal[ProcessKind.MANAGED] = ProcessKind.MANAGED
    engine: ManagedEngine
    version: str = Field(min_length=1)
    user: ProcessUser
    depends_on: list[str] = Field(default_factory=list)


class SourceProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    kind: Literal[ProcessKind.SOURCE] = ProcessKind.SOURCE
    working_directory: str
    build_commands: list[list[str]] = Field(default_factory=list)
    run_command: list[str] = Field(min_length=1)
    environment: dict[str, str] = Field(default_factory=dict)
    build_environment: dict[str, str] | None = None
    fixed_port: int | None = Field(default=None, ge=1, le=65535)
    started_check: StartedCheck | None = None
    secret_purposes: list[SecretPurpose] = Field(default_factory=list)
    user: ProcessUser
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _shape(self) -> "SourceProcess":
        _safe_relative(self.working_directory)
        for step in self.build_commands:
            if not step:
                raise ValueError("build_command_step_empty")
        return self


ProcessEntry = Annotated[Union[ManagedProcess, SourceProcess], Field(discriminator="kind")]


# --- §2c seed ------------------------------------------------------------------------------


class BaselineStrategy(str, Enum):
    TEMPLATE_DATABASE = "template_database"
    DATADIR_COPY = "datadir_copy"
    EMPTY = "empty"


# The §2b catalog table. The engine a store answers to is the *capability's* protocol (resolved in
# the root validator, where `capabilities` is in scope) — a store entry carries no `engine` field
# of its own, and the sentinel's shape is a proof of that engine, not a second source for it.
_ENGINE_STRATEGIES: dict[ManagedEngine, frozenset[BaselineStrategy]] = {
    ManagedEngine.POSTGRES: frozenset(
        {BaselineStrategy.TEMPLATE_DATABASE, BaselineStrategy.DATADIR_COPY}
    ),
    ManagedEngine.REDIS: frozenset({BaselineStrategy.DATADIR_COPY, BaselineStrategy.EMPTY}),
    ManagedEngine.RABBITMQ: frozenset({BaselineStrategy.DATADIR_COPY}),
}


class StoreBaseline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: BaselineStrategy
    inputs_digest: str

    @model_validator(mode="after")
    def _digest_shape(self) -> "StoreBaseline":
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.inputs_digest):
            raise ValueError("inputs_digest_invalid")
        return self


class Sentinel(BaseModel):
    """A store's per-protocol read-only proof, per §2c: postgres `{query, expected}`, redis
    `{key, expected}`, rabbitmq `{queue, expected_depth}` — exactly one shape, never a mix."""

    model_config = ConfigDict(extra="forbid")

    query: str | None = None
    expected: str | None = None
    key: str | None = None
    queue: str | None = None
    expected_depth: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _one_protocol_shape(self) -> "Sentinel":
        if self.implied_engine is None:
            raise ValueError(
                "sentinel_shape_invalid: expected exactly one of "
                "postgres{query,expected}, redis{key,expected}, rabbitmq{queue,expected_depth}"
            )
        return self

    @property
    def implied_engine(self) -> ManagedEngine | None:
        postgres = self.query is not None and self.expected is not None
        redis = self.key is not None and self.expected is not None
        rabbitmq = self.queue is not None and self.expected_depth is not None
        shapes = [
            (postgres, ManagedEngine.POSTGRES, {self.key, self.queue, self.expected_depth}),
            (redis, ManagedEngine.REDIS, {self.query, self.queue, self.expected_depth}),
            (rabbitmq, ManagedEngine.RABBITMQ, {self.query, self.key, self.expected}),
        ]
        matched = [
            engine for present, engine, others in shapes if present and others == {None}
        ]
        return matched[0] if len(matched) == 1 else None


class StoreEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str = Field(min_length=1)
    migrations: list[str] = Field(default_factory=list)
    seed_files: list[str] = Field(default_factory=list)
    baseline: StoreBaseline
    sentinel: Sentinel

    @model_validator(mode="after")
    def _paths(self) -> "StoreEntry":
        for relative_path in (*self.migrations, *self.seed_files):
            _safe_relative(relative_path)
        return self


class Seed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stores: list[StoreEntry] = Field(default_factory=list)


# --- §2d capabilities, readiness, files, provenance -----------------------------------------


class CapabilityV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: CapabilityProtocol
    service: str = Field(min_length=1)
    container_port: int | None = Field(default=None, ge=1, le=65535)
    configuration_name: str | None = None


class ReadinessProbeV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability: str
    path: str | None = None
    timeout_seconds: float = Field(default=120.0, gt=0, le=1800)
    interval_seconds: float = Field(default=1.0, gt=0, le=60)


class BundleFileV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    sha256: str
    size: int = Field(ge=0)

    @model_validator(mode="after")
    def _valid_path(self) -> "BundleFileV2":
        _safe_relative(self.path)
        if not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError(f"file_sha256_invalid: {self.path}")
        return self


class BundleProvenanceV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_kind: str
    repository: str | None = None
    commit: str | None = None
    source_digest: str
    generator: str = "fi.alk.harness"
    generator_version: str = "1"
    adopted_files: list[str] = Field(default_factory=list)
    generated_files: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _valid_source_digest(self) -> "BundleProvenanceV2":
        # Bare 64-hex, matching what `source_fingerprint` (v1's producer) actually emits — no
        # `sha256:` prefix, unlike `digest`/`inputs_digest`.
        if not re.fullmatch(r"[0-9a-f]{64}", self.source_digest):
            raise ValueError(f"source_digest_invalid: {self.source_digest}")
        return self


# --- manifest root ---------------------------------------------------------------------------

_CAPABILITY_SLUG = re.compile(r"[a-z][a-z0-9_]*")

# §2c: a store's engine is the engine behind its capability's protocol, not a field the store
# carries itself. Only postgres/redis/amqp capabilities can host a store at all (§2c); any other
# protocol on a store's capability is a producer error §2e is left to catch.
_STORE_ENGINE_BY_PROTOCOL: dict[CapabilityProtocol, ManagedEngine] = {
    CapabilityProtocol.POSTGRES: ManagedEngine.POSTGRES,
    CapabilityProtocol.REDIS: ManagedEngine.REDIS,
    CapabilityProtocol.AMQP: ManagedEngine.RABBITMQ,
}


class EnvironmentBundleV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str
    digest: str
    name: str
    runtime: BundleRuntimeV2
    processes: list[ProcessEntry] = Field(default_factory=list)
    seed: Seed | None = None
    capabilities: dict[str, CapabilityV2] = Field(default_factory=dict)
    readiness: list[ReadinessProbeV2] = Field(default_factory=list)
    files: list[BundleFileV2] = Field(default_factory=list)
    provenance: BundleProvenanceV2
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_manifest(self) -> "EnvironmentBundleV2":
        if self.schema_version != BUNDLE_V2_SCHEMA_VERSION:
            raise ValueError(f"bundle_schema_unsupported: {self.schema_version}")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.digest):
            raise ValueError("bundle_digest_invalid")

        if self.runtime.kind is RuntimeKindV2.PROCESS and not self.processes:
            raise ValueError("processes_required: kind=process")
        if self.runtime.kind is RuntimeKindV2.EXTERNAL and (
            self.processes or self.seed is not None
        ):
            raise ValueError("processes_and_seed_forbidden: kind=external")

        for slug in self.capabilities:
            if not _CAPABILITY_SLUG.fullmatch(slug):
                raise ValueError(f"capability_slug_invalid: {slug}")

        process_names = Counter(process.name for process in self.processes)
        duplicated_names = sorted(name for name, count in process_names.items() if count > 1)
        if duplicated_names:
            raise ValueError("process_name_duplicate: " + ", ".join(duplicated_names))
        known_names = set(process_names)

        service_unresolved = {
            slug: capability.service
            for slug, capability in self.capabilities.items()
            if capability.service not in known_names
        }
        if service_unresolved:
            detail = ", ".join(
                f"{slug}: {service}" for slug, service in sorted(service_unresolved.items())
            )
            raise ValueError(f"service_unresolved: {detail}")

        control_service = self.runtime.control_service
        if control_service is not None and control_service not in known_names:
            raise ValueError(f"control_service_unresolved: {control_service}")

        names_to_slugs: dict[str, list[str]] = {}
        for slug, capability in self.capabilities.items():
            if capability.configuration_name:
                names_to_slugs.setdefault(capability.configuration_name, []).append(slug)
        duplicated = {name: slugs for name, slugs in names_to_slugs.items() if len(slugs) > 1}
        if duplicated:
            detail = ", ".join(
                f"{name} ({', '.join(sorted(slugs))})" for name, slugs in sorted(duplicated.items())
            )
            raise ValueError(f"configuration_name_duplicate: {detail}")

        unresolved = {
            probe.capability
            for probe in self.readiness
            if probe.capability not in self.capabilities
        }
        if self.seed is not None:
            for store in self.seed.stores:
                if store.capability not in self.capabilities:
                    unresolved.add(store.capability)
        if unresolved:
            raise ValueError("capability_unresolved: " + ", ".join(sorted(unresolved)))

        if self.seed is not None:
            # Every store's capability resolved above, so its protocol is known — that protocol,
            # not the sentinel's own shape, is the authoritative engine (§2c classifies stores by
            # capability protocol; the sentinel only proves that engine, it doesn't select it).
            for store in self.seed.stores:
                engine = _STORE_ENGINE_BY_PROTOCOL.get(self.capabilities[store.capability].protocol)
                if engine is None:
                    continue
                if store.sentinel.implied_engine is not engine:
                    raise ValueError(
                        f"sentinel_shape_mismatch: {store.capability}: sentinel implies "
                        f"{store.sentinel.implied_engine.value}, capability protocol resolves to "
                        f"{engine.value}"
                    )
                if store.baseline.strategy not in _ENGINE_STRATEGIES[engine]:
                    raise ValueError(
                        f"seed_strategy_unsupported: {store.capability}: {engine.value} does not "
                        f"support {store.baseline.strategy.value}"
                    )

        if self.seed is not None:
            # A store names its capability directly (no placeholder to resolve), so this half of
            # the configuration_name rule is decidable here; the process-`environment` half needs
            # placeholder scanning against the closed `{{...}}` vocabulary, which is §2e's job.
            missing_name = [
                store.capability
                for store in self.seed.stores
                if not self.capabilities[store.capability].configuration_name
            ]
            if missing_name:
                raise ValueError(
                    "configuration_name_required: " + ", ".join(sorted(set(missing_name)))
                )

        # v1's whole-manifest resolved-secret guard (`bundle.py`), reapplied here rather than
        # dropped: v2 newly carries free-form `environment`/`build_environment` dicts, exactly
        # where a resolved credential lands if an authoring stage ever inlines one instead of
        # routing it through `secret_purposes`. `secret_purposes` itself is excluded from the
        # dump — its key matches the secret-field pattern (`secret_...`) but it holds purpose
        # identifiers, never values, the same reasoning v1 exempts `secret_refs` under.
        _reject_secret_values(
            self.model_dump(exclude={"digest": True, "processes": {"__all__": {"secret_purposes"}}})
        )
        return self


# --- §2c inputs_digest ------------------------------------------------------------------------


def compute_inputs_digest(
    root: str | Path,
    migrations: Sequence[str],
    seed_files: Sequence[str],
    *,
    engine: ManagedEngine,
    version: str,
) -> str:
    """The byte-exact `seed.stores[].baseline.inputs_digest` construction from §2c.

    sha256 over, for each file in ``migrations`` then ``seed_files`` **in listed order** (never
    sorted — order is part of the identity, since migrations must apply in sequence),
    ``<relative_path>\\n<content_length>\\n<content_bytes>``, followed by ``<engine>:<version>\\n``.
    Runs at authoring time, against ``migrations``/``seed_files`` as bundle-relative paths under
    ``root`` (the bundle staging root) — the same paths the sealed manifest records, so the digest
    is reproducible from the bundle's own field values. ``engine``/``version`` must be the store's
    engine's own declared `ManagedProcess.engine`/`.version`, verbatim.
    """
    root = Path(root)
    digest = hashlib.sha256()
    for relative_path in (*migrations, *seed_files):
        _safe_relative(relative_path)
        content = (root / relative_path).read_bytes()
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\n")
        digest.update(str(len(content)).encode("utf-8"))
        digest.update(b"\n")
        digest.update(content)
    digest.update(f"{engine.value}:{version}\n".encode("utf-8"))
    return "sha256:" + digest.hexdigest()


def load_bundle_v2(path: str | Path) -> EnvironmentBundleV2:
    """Parse and validate one `futureagi.environment-bundle.v2` manifest.

    ``path`` is either the manifest file itself or the bundle directory containing it. Schema
    version is checked before full model validation runs, so a `…bundle.v1` manifest — or
    anything else — is named explicitly rather than failing on an unrelated field.
    """
    path = Path(path).expanduser()
    target = path if path.is_file() else path / BUNDLE_V2_MANIFEST
    if not target.is_file():
        raise BundleV2Error(f"bundle_manifest_missing: {target}")
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleV2Error(f"bundle_manifest_invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise BundleV2Error("bundle_manifest_invalid: not a JSON object")
    schema_version = raw.get("schema_version")
    if schema_version != BUNDLE_V2_SCHEMA_VERSION:
        raise BundleV2Error(f"bundle_schema_unsupported: {schema_version!r}")
    try:
        return EnvironmentBundleV2.model_validate(raw)
    except ValidationError as exc:
        raise BundleV2Error(f"bundle_manifest_invalid: {exc}") from exc


__all__ = [
    "BUNDLE_V2_MANIFEST",
    "BUNDLE_V2_SCHEMA_VERSION",
    "BaselineStrategy",
    "BundleFileV2",
    "BundleProvenanceV2",
    "BundleRuntimeV2",
    "BundleV2Error",
    "CapabilityV2",
    "EnvironmentBundleV2",
    "EvidenceSeam",
    "ManagedEngine",
    "ManagedProcess",
    "ProcessKind",
    "ProcessUser",
    "ReadinessProbeV2",
    "RuntimeKindV2",
    "Seed",
    "SecretPurpose",
    "Sentinel",
    "SourceProcess",
    "StartedCheck",
    "StoreBaseline",
    "StoreEntry",
    "compute_inputs_digest",
    "load_bundle_v2",
]
