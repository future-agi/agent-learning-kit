"""Deterministic producer for hosted ``EnvironmentBundleV2`` directories.

This module is deliberately a compiler, not a second execution engine.  It converts the
packaging already present in a submitted repository into the process vocabulary consumed by the
Daytona guest, adds the harness-owned world database, adopts frozen scenario artifacts, seals the
result, and runs the guest's exact preflight before publishing it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .bundle import CapabilityProtocol
from .bundle_v2 import (
    BUNDLE_V2_MANIFEST,
    BUNDLE_V2_SCHEMA_VERSION,
    BaselineStrategy,
    BundleFileV2,
    BundleProvenanceV2,
    BundleRuntimeV2,
    CapabilityV2,
    EnvironmentBundleV2,
    EvidenceSeam,
    ManagedEngine,
    ManagedProcess,
    ProcessUser,
    ReadinessProbeV2,
    RuntimeKindV2,
    SecretPurpose,
    Seed,
    Sentinel,
    SourceProcess,
    StartedCheck,
    StoreBaseline,
    StoreEntry,
    compute_inputs_digest,
    load_bundle_v2,
    seal_bundle_v2,
)
from .job import HarnessJob
from .job import ProviderExecutionMode
from .process_preflight import preflight_bundle
from .provision import source_fingerprint
from .provider_lifecycle import ProviderRepositoryManifest, load_provider_manifest


class BundleAuthorError(RuntimeError):
    """A source cannot be compiled into an honest hosted process bundle."""


@dataclass(frozen=True)
class EnvironmentPlanV2:
    packaging: str
    control_service: str
    processes: tuple[ManagedProcess | SourceProcess, ...]
    capabilities: dict[str, CapabilityV2]
    readiness: tuple[ReadinessProbeV2, ...]

    def __post_init__(self) -> None:
        names = [process.name for process in self.processes]
        if len(names) != len(set(names)):
            raise BundleAuthorError("environment_plan_process_names_not_unique")
        if self.control_service not in names:
            raise BundleAuthorError("environment_plan_control_service_missing")
        known = set(names)
        for process in self.processes:
            missing = sorted(set(process.depends_on) - known)
            if missing:
                raise BundleAuthorError(
                    f"environment_plan_dependency_missing: {process.name}: {', '.join(missing)}"
                )
        for slug, capability in self.capabilities.items():
            if capability.service not in known:
                raise BundleAuthorError(
                    f"environment_plan_capability_service_missing: {slug}: {capability.service}"
                )
        missing_probes = sorted(
            {
                probe.capability
                for probe in self.readiness
                if probe.capability not in self.capabilities
            }
        )
        if missing_probes:
            raise BundleAuthorError(
                "environment_plan_readiness_capability_missing: "
                + ", ".join(missing_probes)
            )


_COMPOSE_NAMES = (
    "compose.yml",
    "compose.yaml",
    "docker-compose.yml",
    "docker-compose.yaml",
)
_IGNORED_ARTIFACT_PARTS = {".git", ".venv", "__pycache__", "node_modules"}


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    return "'" + str(value).replace("'", "''") + "'"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_type(values: list[Any]) -> str:
    present = [value for value in values if value is not None]
    if present and all(isinstance(value, bool) for value in present):
        return "boolean"
    if present and all(
        isinstance(value, int) and not isinstance(value, bool) for value in present
    ):
        return "bigint"
    if present and all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in present
    ):
        return "double precision"
    if present and all(isinstance(value, (dict, list)) for value in present):
        return "jsonb"
    return "text"


def _collections_sql(path: Path) -> str:
    body = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(body, dict):
        raise BundleAuthorError("collections_invalid: expected an object")
    statements: list[str] = []
    for table, raw_rows in body.items():
        rows = raw_rows if isinstance(raw_rows, list) else []
        records = [row for row in rows if isinstance(row, dict)]
        columns = sorted({str(column) for row in records for column in row})
        if not columns:
            columns = ["id"]
        definitions = [
            f"{_identifier(column)} {_json_type([row.get(column) for row in records])}"
            for column in columns
        ]
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {_identifier(str(table))} ({', '.join(definitions)});"
        )
        for row in records:
            values = ", ".join(_sql_literal(row.get(column)) for column in columns)
            names = ", ".join(_identifier(column) for column in columns)
            statements.append(
                f"INSERT INTO {_identifier(str(table))} ({names}) VALUES ({values});"
            )
    return "\n".join(statements) + "\n"


def _sqlite_type(declared: str) -> str:
    normalized = declared.upper()
    if "BOOL" in normalized:
        return "boolean"
    if "INT" in normalized:
        return "bigint"
    if any(mark in normalized for mark in ("REAL", "FLOA", "DOUB")):
        return "double precision"
    if any(mark in normalized for mark in ("NUMERIC", "DECIMAL")):
        return "numeric"
    if "BLOB" in normalized:
        return "bytea"
    return "text"


def _sqlite_json_type(values: list[Any], sql_type: str) -> str:
    """Preserve structured SQLite TEXT values when moving a world to Postgres.

    SQLite has no native JSON/array storage class, so generated worlds store lists and
    objects as JSON text.  Treating those columns as Postgres ``text`` changes the tool
    contract (``[]`` becomes the string ``"[]"``).  Only promote a column when every
    non-null value is a JSON object or array; ordinary strings remain text.
    """
    if sql_type != "text":
        return sql_type
    present = [value for value in values if value is not None]
    if not present or not all(isinstance(value, str) for value in present):
        return sql_type
    try:
        decoded = [json.loads(value) for value in present]
    except (TypeError, ValueError, json.JSONDecodeError):
        return sql_type
    return (
        "jsonb"
        if all(isinstance(value, (dict, list)) for value in decoded)
        else sql_type
    )


def _sqlite_value(value: Any, sql_type: str) -> Any:
    if value is not None and sql_type == "boolean":
        return bool(value)
    if value is not None and sql_type == "jsonb" and isinstance(value, str):
        return json.loads(value)
    return value


def _sqlite_sql(path: Path) -> str:
    statements: list[str] = []
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for table in tables:
            info = list(connection.execute(f"PRAGMA table_info({_identifier(table)})"))
            selected = connection.execute(
                f"SELECT * FROM {_identifier(table)}"
            ).fetchall()
            definitions: list[str] = []
            columns: list[str] = []
            column_types: list[str] = []
            for row in info:
                name = str(row[1])
                sql_type = _sqlite_json_type(
                    [record[name] for record in selected],
                    _sqlite_type(str(row[2] or "")),
                )
                suffix = " PRIMARY KEY" if int(row[5] or 0) else ""
                definitions.append(f"{_identifier(name)} {sql_type}{suffix}")
                columns.append(name)
                column_types.append(sql_type)
            statements.append(
                f"CREATE TABLE IF NOT EXISTS {_identifier(table)} ({', '.join(definitions)});"
            )
            for record in selected:
                names = ", ".join(_identifier(column) for column in columns)
                values = ", ".join(
                    _sql_literal(_sqlite_value(record[column], sql_type))
                    for column, sql_type in zip(columns, column_types, strict=True)
                )
                statements.append(
                    f"INSERT INTO {_identifier(table)} ({names}) VALUES ({values});"
                )
    finally:
        connection.close()
    return "\n".join(statements) + "\n"


def _store_json_seed_sql(path: Path) -> str:
    """Restore rows exported by the existing ALK world store into adopted PostgreSQL tables.

    ``schema.sql`` is deliberately schema-only in several established authoring outputs.  The
    matching ``store.json`` carries the frozen rows under ``rows``.  PostgreSQL's
    ``jsonb_populate_recordset`` performs the type-aware conversion (including arrays, numerics,
    timestamps and JSON) against the adopted table definition instead of guessing SQL types.
    """
    body = json.loads(path.read_text(encoding="utf-8"))
    rows = body.get("rows") if isinstance(body, dict) else None
    if not isinstance(rows, dict):
        raise BundleAuthorError("store_invalid: expected an object with a rows object")
    statements: list[str] = []
    for table in sorted(rows):
        raw_rows = rows[table]
        if not isinstance(raw_rows, list):
            raise BundleAuthorError(f"store_invalid: rows.{table} must be an array")
        records = [row for row in raw_rows if isinstance(row, dict)]
        if len(records) != len(raw_rows):
            raise BundleAuthorError(
                f"store_invalid: rows.{table} contains a non-object row"
            )
        if not records:
            continue
        payload = json.dumps(records, sort_keys=True, separators=(",", ":"))
        statements.append(
            f"INSERT INTO public.{_identifier(str(table))} "
            f"SELECT * FROM jsonb_populate_recordset(NULL::public.{_identifier(str(table))}, "
            f"{_sql_literal(payload)}::jsonb);"
        )
    if not statements:
        return ""
    # A frozen snapshot is already internally consistent, but alphabetical table order is not
    # necessarily foreign-key order (for example payment_methods sorts before users).  Restore it
    # like pg_restore does: suppress constraint triggers for the bulk load, then re-enable them.
    return (
        "SET session_replication_role = replica;\n"
        + "\n".join(statements)
        + "\nSET session_replication_role = origin;\n"
    )


def _adopted_seed_sql(authoring: Path) -> tuple[str, list[str]]:
    schema = authoring / "schema.sql"
    if schema.is_file():
        sql = schema.read_text(encoding="utf-8")
        adopted = ["schema.sql"]
        store = authoring / "store.json"
        if store.is_file():
            sql += "\n" + _store_json_seed_sql(store)
            adopted.append("store.json")
        return sql, adopted
    sqlite = authoring / "world.sqlite"
    if sqlite.is_file():
        return _sqlite_sql(sqlite), ["world.sqlite"]
    collections = authoring / "collections.json"
    if collections.is_file():
        return _collections_sql(collections), ["collections.json"]
    return "", []


def _compose_path(source: Path) -> Path | None:
    matches = [source / name for name in _COMPOSE_NAMES if (source / name).is_file()]
    if len(matches) > 1:
        raise BundleAuthorError(
            "compose_ambiguous: " + ", ".join(path.name for path in matches)
        )
    return matches[0] if matches else None


def _load_compose(path: Path) -> dict[str, Any]:
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BundleAuthorError(f"compose_invalid: {exc}") from exc
    if not isinstance(body, dict) or not isinstance(body.get("services"), dict):
        raise BundleAuthorError("compose_invalid: services must be an object")
    return body


def _python_process(
    *,
    name: str,
    working_directory: str,
    entry: str,
    control: bool,
    needs_secrets: bool,
    port: int | None = None,
    environment: dict[str, str] | None = None,
    depends_on: list[str] | None = None,
) -> SourceProcess:
    # The build tree is writable; the submitted source remains read-only.  ``uv sync`` creates a
    # project-local venv for pyproject repositories, while requirements/stdlib sources get the
    # same explicit venv boundary.  No dependency is installed into the immutable snapshot.
    build: list[list[str]]
    run: list[str]
    relative_root = Path(working_directory)
    # Discovery happens at the caller's source root; these placeholders are resolved below by
    # `_plan_python`, which replaces this conservative default where necessary.
    build = [["python3.12", "-m", "venv", ".venv"]]
    run = [".venv/bin/python", entry]
    del relative_root
    return SourceProcess(
        name=name,
        working_directory=working_directory,
        build_commands=build,
        run_command=run,
        environment=environment or {},
        fixed_port=port,
        started_check=StartedCheck(port=True, timeout_seconds=180) if port else None,
        secret_purposes=[SecretPurpose.TARGET_PROVIDER] if needs_secrets else [],
        user=ProcessUser.SVC_AGENT if control else ProcessUser.SVC_TOOLS,
        depends_on=depends_on or [],
    )


def _plan_python(
    source: Path,
    *,
    name: str,
    root: Path,
    entry: str,
    control: bool,
    needs_secrets: bool,
    port: int | None = None,
    environment: dict[str, str] | None = None,
    depends_on: list[str] | None = None,
    livekit_download: bool = False,
    run_override: list[str] | None = None,
) -> SourceProcess:
    relative = root.relative_to(source).as_posix() or "."
    process = _python_process(
        name=name,
        working_directory=relative,
        entry=entry,
        control=control,
        needs_secrets=needs_secrets,
        port=port,
        environment=environment,
        depends_on=depends_on,
    )
    python = _docker_python(root)
    if (root / "pyproject.toml").is_file():
        commands = [["uv", "sync", "--no-cache", "--python", python]]
        if (root / "uv.lock").is_file():
            commands[0].append("--locked")
        if livekit_download:
            commands.append(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    "-m",
                    "livekit.agents",
                    "download-files",
                ]
            )
        run = ["uv", "run", "--no-sync", "python", entry]
    elif (root / "requirements.txt").is_file():
        commands = [
            [python, "-m", "venv", ".venv"],
            [
                ".venv/bin/python",
                "-m",
                "pip",
                "install",
                "--requirement",
                "requirements.txt",
            ],
        ]
        run = [".venv/bin/python", entry]
    else:
        commands = []
        run = [python, entry]
    return process.model_copy(
        update={"build_commands": commands, "run_command": run_override or run}
    )


def _docker_python(root: Path) -> str:
    dockerfile = root / "Dockerfile"
    if not dockerfile.is_file():
        return "python3.12"
    text = dockerfile.read_text(encoding="utf-8", errors="replace")
    argument = re.search(r"(?mi)^ARG\s+PYTHON_VERSION\s*=\s*([0-9]+\.[0-9]+)\s*$", text)
    if argument:
        return f"python{argument.group(1)}"
    direct = re.search(r"(?mi)^FROM\s+(?:[^/\s]+/)*python:([0-9]+\.[0-9]+)", text)
    return f"python{direct.group(1)}" if direct else "python3.12"


def _dockerfile_run(root: Path) -> list[str] | None:
    dockerfile = root / "Dockerfile"
    if not dockerfile.is_file():
        return None
    commands = []
    for line in dockerfile.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("CMD "):
            commands.append(stripped[4:].strip())
    if not commands:
        return None
    raw = commands[-1]
    if not raw.startswith("["):
        raise BundleAuthorError(
            f"dockerfile_command_unsupported: {dockerfile} uses shell-form CMD"
        )
    try:
        argv = json.loads(raw)
    except ValueError as exc:
        raise BundleAuthorError(f"dockerfile_command_invalid: {dockerfile}") from exc
    if (
        not isinstance(argv, list)
        or not argv
        or not all(isinstance(item, str) for item in argv)
    ):
        raise BundleAuthorError(f"dockerfile_command_invalid: {dockerfile}")
    if argv[0] == "python":
        argv[0] = ".venv/bin/python" if (root / "requirements.txt").is_file() else "uv"
        if argv[0] == "uv":
            argv[1:1] = ["run", "--no-sync", "python"]
    elif (
        argv[0] in {"uvicorn", "gunicorn", "flask"}
        and (root / "requirements.txt").is_file()
    ):
        argv[0] = f".venv/bin/{argv[0]}"
    return argv


def _managed_world_db() -> ManagedProcess:
    return ManagedProcess(
        name="world-db",
        engine=ManagedEngine.POSTGRES,
        version="16",
        user=ProcessUser.SVC_DATA,
    )


def _tool_proxy_process() -> SourceProcess:
    return SourceProcess(
        name="tool-proxy",
        working_directory="generated/tool-proxy",
        source_origin="bundle",
        run_command=["/opt/alk-venv/bin/python", "proxy.py"],
        environment={
            "PORT": "{{PORT_tool-proxy}}",
            "UPSTREAM_URL": "{{TOOLS_UPSTREAM_URL}}",
            "DATABASE_URL": "{{WORLD_DATABASE_URL}}",
        },
        started_check=StartedCheck(port=True, timeout_seconds=180),
        user=ProcessUser.SVC_TOOLS,
        depends_on=["tools-api", "world-db"],
    )


def resolve_environment_plan(
    source: str | Path,
    job: HarnessJob,
    *,
    contract_modality: str | None = None,
) -> EnvironmentPlanV2:
    """Resolve packaging once.  Authoring and provisioning consume this same immutable plan."""
    root = Path(source).resolve()
    if not root.is_dir():
        raise BundleAuthorError(f"source_unavailable: {root}")
    connector = job.agent.connector.lower()
    if job.agent.mode is ProviderExecutionMode.ENVIRONMENT_BACKED:
        declaration = load_provider_manifest(
            root, str(job.agent.config.get("lifecycle_manifest") or "alk.yaml")
        )
        if declaration.provider.type.value != connector:
            raise BundleAuthorError(
                "provider_lifecycle_connector_mismatch: "
                f"job={connector}, manifest={declaration.provider.type.value}"
            )
    # Hosted repository submissions normally arrive as ``connector=auto``.  In the unified
    # Daytona lane the contract is authored *after* dispatch, so the control plane cannot rewrite
    # that field before this compiler runs.  The frozen contract is therefore the authoritative
    # late-bound modality signal.  Voice is routed through LiveKit because that is the hosted
    # repository voice connector implemented by the guest; explicit vapi/retell values never
    # enter this path.
    is_livekit = connector == "livekit" or (
        connector == "auto" and (contract_modality or "").strip().lower() == "voice"
    )
    needs_target_secrets = any(
        reference.purpose == SecretPurpose.TARGET_PROVIDER.value
        for reference in job.agent.secret_refs.values()
    )
    compose = _compose_path(root)
    processes: list[ManagedProcess | SourceProcess] = [_managed_world_db()]
    capabilities: dict[str, CapabilityV2] = {
        "world_db": CapabilityV2(
            protocol=CapabilityProtocol.POSTGRES,
            service="world-db",
            container_port=5432,
            configuration_name="WORLD_DATABASE_URL",
        )
    }
    readiness = [ReadinessProbeV2(capability="world_db", timeout_seconds=180)]

    if compose is not None:
        body = _load_compose(compose)
        services = body["services"]
        # Compile the submitted topology.  Supported managed services become snapshot engines;
        # source services remain source processes.  Unknown image-only dependencies are rejected
        # explicitly instead of being silently emulated.
        managed_names: set[str] = set()
        for service_name, raw in services.items():
            service = raw if isinstance(raw, dict) else {}
            image = str(service.get("image") or "")
            if image.startswith("postgres:"):
                if service_name != "postgres":
                    raise BundleAuthorError(
                        f"managed_name_unsupported: postgres service must be named postgres, got {service_name}"
                    )
                managed_names.add(service_name)
                continue
            if image and "redis" in image:
                processes.append(
                    ManagedProcess(
                        name=service_name,
                        engine=ManagedEngine.REDIS,
                        version=image.split(":", 1)[1].split("-", 1)[0]
                        if ":" in image
                        else "7",
                        user=ProcessUser.SVC_DATA,
                    )
                )
                managed_names.add(service_name)
                capabilities[f"{service_name}_redis"] = CapabilityV2(
                    protocol=CapabilityProtocol.REDIS,
                    service=service_name,
                    container_port=6379,
                    configuration_name=f"{service_name.upper().replace('-', '_')}_URL",
                )
                readiness.append(ReadinessProbeV2(capability=f"{service_name}_redis"))
                continue
            if image and not service.get("build"):
                raise BundleAuthorError(
                    f"engine_unsupported: image-only service {service_name!r} ({image!r}) is not in the snapshot catalog"
                )

        source_services = [name for name in services if name not in managed_names]
        control_name = (
            "agent"
            if "agent" in source_services
            else (
                "api"
                if "api" in source_services
                else source_services[-1]
                if source_services
                else ""
            )
        )
        if not control_name:
            raise BundleAuthorError(
                "control_service_missing: compose has no source-built service"
            )
        for service_name in source_services:
            service = services[service_name]
            build = service.get("build", ".")
            if isinstance(build, dict):
                context = str(build.get("context") or ".")
            else:
                context = str(build)
            service_root = (root / context).resolve()
            if not service_root.is_relative_to(root):
                raise BundleAuthorError(f"build_context_escape: {service_name}")
            environment: dict[str, str] = {}
            raw_env = service.get("environment") or {}
            if isinstance(raw_env, dict):
                environment = {
                    str(k): str(v) for k, v in raw_env.items() if v is not None
                }
            depends = (
                list((service.get("depends_on") or {}).keys())
                if isinstance(service.get("depends_on"), dict)
                else list(service.get("depends_on") or [])
            )
            if service_name == "tools-api" and "postgres" in managed_names:
                # The target DB is intentionally a distinct per-world logical DB on the same
                # harness-owned Postgres engine. This preserves reset/isolation without another
                # daemon per call.
                environment["DATABASE_URL"] = "{{WORLD_DATABASE_URL}}"
                depends = [
                    "world-db" if item == "postgres" else item for item in depends
                ]
            if service_name == control_name and "tools-api" in source_services:
                environment["TOOLS_API_URL"] = "{{TOOLS_API_URL}}"
            if is_livekit and service_name == control_name:
                environment.setdefault(
                    "LIVEKIT_AGENT_NAME",
                    "uber-voice-booking-{{JOB_ID}}-w{{WORLD_INDEX}}",
                )
                environment.setdefault(
                    "HARNESS_TOOL_TRACE",
                    "{{WORLD_DIR}}/agent-tool-calls.jsonl",
                )
            entry = (
                "agent/agent.py"
                if (service_root / "agent" / "agent.py").is_file()
                else "agent.py"
            )
            port = 8080 if service_name in {"api", "tools-api"} else None
            process = _plan_python(
                root,
                name=service_name,
                root=service_root,
                entry=entry,
                control=service_name == control_name,
                needs_secrets=needs_target_secrets and service_name == control_name,
                port=port,
                environment=environment,
                depends_on=[item for item in depends if item != "postgres"],
                livekit_download=is_livekit and service_name == control_name,
                run_override=_dockerfile_run(service_root),
            )
            if is_livekit and service_name == control_name:
                # The LiveKit worker opens its HTTP health port before it has registered with
                # the dispatch service.  Treating the port as readiness creates a race where a
                # named dispatch is submitted in that gap; self-hosted LiveKit leaves that
                # dispatch unassigned even after the worker subsequently registers.  The worker
                # log is the first observable signal that it can actually accept the call.
                process = process.model_copy(
                    update={
                        "started_check": StartedCheck(
                            log_marker="registered worker", timeout_seconds=180
                        )
                    }
                )
            processes.append(process)
            if port:
                slug = "target_http" if service_name == control_name else "tools_api"
                config = (
                    "TARGET_HTTP_URL"
                    if service_name == control_name
                    else "TOOLS_UPSTREAM_URL"
                )
                capabilities[slug] = CapabilityV2(
                    protocol=CapabilityProtocol.HTTP,
                    service=service_name,
                    container_port=port,
                    configuration_name=config,
                )
                readiness.append(
                    ReadinessProbeV2(
                        capability=slug, path="/health", timeout_seconds=180
                    )
                )
        if "tools-api" in source_services:
            processes.append(_tool_proxy_process())
            # The target must not become eligible to start until the evidence proxy is ready.
            # Depending only on the upstream tools process leaves a race where the agent starts
            # with TOOLS_API_URL pointing at a port that has not been bound yet.
            rewritten: list[ManagedProcess | SourceProcess] = []
            for process in processes:
                if isinstance(process, SourceProcess) and process.name == control_name:
                    dependencies = [
                        "tool-proxy" if item == "tools-api" else item
                        for item in process.depends_on
                    ]
                    if "tool-proxy" not in dependencies:
                        dependencies.append("tool-proxy")
                    process = process.model_copy(update={"depends_on": dependencies})
                rewritten.append(process)
            processes = rewritten
            capabilities["tool_proxy"] = CapabilityV2(
                protocol=CapabilityProtocol.HTTP,
                service="tool-proxy",
                container_port=8080,
                configuration_name="TOOLS_API_URL",
            )
            readiness.append(
                ReadinessProbeV2(
                    capability="tool_proxy", path="/health", timeout_seconds=180
                )
            )
        packaging = "compose"
    else:
        entry = "agent.py"
        if not (root / entry).is_file():
            candidates = sorted(root.glob("**/agent.py"))
            if len(candidates) != 1:
                raise BundleAuthorError(
                    "component_ambiguous: expected exactly one agent.py"
                )
            component = candidates[0].parent
        else:
            component = root
        control_name = "agent"
        port = None if is_livekit else 8080
        environment = (
            {
                "LIVEKIT_AGENT_NAME": (
                    root.name.replace("_", "-") + "-{{JOB_ID}}-w{{WORLD_INDEX}}"
                ),
                "HARNESS_TOOL_TRACE": "{{WORLD_DIR}}/agent-tool-calls.jsonl",
            }
            if is_livekit
            else {}
        )
        process = _plan_python(
            root,
            name=control_name,
            root=component,
            entry=entry,
            control=True,
            needs_secrets=needs_target_secrets,
            port=port,
            environment=environment,
            livekit_download=is_livekit,
            run_override=_dockerfile_run(component),
        )
        if is_livekit:
            process = process.model_copy(
                update={
                    "started_check": StartedCheck(
                        log_marker="registered worker", timeout_seconds=180
                    )
                }
            )
        processes.append(process)
        if port:
            capabilities["target_http"] = CapabilityV2(
                protocol=CapabilityProtocol.HTTP,
                service=control_name,
                container_port=port,
                configuration_name="TARGET_HTTP_URL",
            )
            readiness.append(
                ReadinessProbeV2(
                    capability="target_http", path="/health", timeout_seconds=180
                )
            )
        packaging = (
            "dockerfile" if (root / "Dockerfile").is_file() else "generated_python"
        )

    return EnvironmentPlanV2(
        packaging=packaging,
        control_service=control_name,
        processes=tuple(processes),
        capabilities=capabilities,
        readiness=tuple(readiness),
    )


def _copy_scenarios(authoring: Path, staging: Path, *, count: int) -> None:
    source = authoring / "scenarios"
    if not source.is_dir():
        raise BundleAuthorError(f"scenario_artifacts_missing: {source}")
    target = staging / "scenarios"
    target.mkdir()
    folders = sorted(path for path in source.iterdir() if path.is_dir())
    if len(folders) < count:
        raise BundleAuthorError(
            f"scenario_artifacts_insufficient: requested {count}, found {len(folders)}"
        )
    for source_folder in folders[:count]:
        shutil.copytree(source_folder, target / source_folder.name)
    for folder in sorted(path for path in target.iterdir() if path.is_dir()):
        document = folder / "scenario.json"
        if not document.is_file():
            continue
        body = json.loads(document.read_text(encoding="utf-8"))
        body["scenario_key"] = str(
            body.get("scenario_key") or body.get("name") or folder.name
        )
        body["scenario_id"] = str(body.get("scenario_id") or "")
        document.write_text(
            json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


def _copy_chat_authoring(authoring: Path, staging: Path) -> list[str]:
    """Adopt the frozen target/tool contract needed by response-carried HTTP tools.

    These are authoring outputs, not repository inference performed by the hosted consumer. The
    producer validates and seals them exactly like scenario code. Voice bundles legitimately have
    none; HTTP chat bundles require a contract at pre-dial time and fail there with a typed error.
    """
    adopted: list[str] = []
    contract = authoring / "contract.json"
    if contract.is_file():
        shutil.copy2(contract, staging / "contract.json")
        adopted.append("contract.json")
    handlers = authoring / "handlers"
    if handlers.is_dir():
        shutil.copytree(handlers, staging / "handlers")
        adopted.append("handlers/")
    prompt = authoring / "simulator_prompt.md"
    if prompt.is_file():
        shutil.copy2(prompt, staging / "simulator_prompt.md")
        adopted.append("simulator_prompt.md")
    return adopted


def _files(root: Path) -> list[BundleFileV2]:
    records: list[BundleFileV2] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir() or path.name == BUNDLE_V2_MANIFEST:
            continue
        if path.is_symlink():
            raise BundleAuthorError(
                f"bundle_symlink_forbidden: {path.relative_to(root)}"
            )
        relative = path.relative_to(root)
        if any(part in _IGNORED_ARTIFACT_PARTS for part in relative.parts):
            continue
        content = path.read_bytes()
        records.append(
            BundleFileV2(
                path=relative.as_posix(),
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
            )
        )
    return records


def author_bundle_v2(
    *,
    source: str | Path,
    job: HarnessJob,
    authoring: str | Path,
    output: str | Path,
) -> EnvironmentBundleV2:
    source_root = Path(source).resolve()
    authoring_root = Path(authoring).resolve()
    output_root = Path(output).resolve()
    contract_modality: str | None = None
    contract_path = authoring_root / "contract.json"
    if contract_path.is_file():
        try:
            contract_body = json.loads(contract_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BundleAuthorError(
                f"contract_invalid: cannot read {contract_path}: {exc}"
            ) from exc
        if not isinstance(contract_body, dict):
            raise BundleAuthorError("contract_invalid: contract.json must be an object")
        contract_modality = str(contract_body.get("modality") or "").strip().lower()
    plan = resolve_environment_plan(
        source_root,
        job,
        contract_modality=contract_modality,
    )
    output_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(
        tempfile.mkdtemp(prefix=f".{output_root.name}.", dir=output_root.parent)
    )
    try:
        _copy_scenarios(authoring_root, temporary, count=job.scenario_count)
        adopted_chat_files = _copy_chat_authoring(authoring_root, temporary)
        if any(process.name == "tool-proxy" for process in plan.processes):
            generated = temporary / "generated" / "tool-proxy"
            generated.mkdir(parents=True)
            shutil.copy2(
                Path(__file__).with_name("tool_trace_proxy.py"),
                generated / "proxy.py",
            )
        seed_dir = temporary / "seed"
        seed_dir.mkdir()
        seed_path = seed_dir / "world.sql"
        prefix = (
            "CREATE TABLE IF NOT EXISTS harness_seed_sentinel (id text PRIMARY KEY);\n"
            "INSERT INTO harness_seed_sentinel(id) VALUES ('ready') ON CONFLICT DO NOTHING;\n"
            "CREATE TABLE IF NOT EXISTS _alk_tool_trace ("
            "id bigserial PRIMARY KEY, name text NOT NULL, arguments jsonb NOT NULL, "
            "result jsonb, ok boolean NOT NULL, error text, at double precision NOT NULL);\n"
        )
        schema, adopted_seed = _adopted_seed_sql(authoring_root)
        seed_path.write_text(prefix + schema, encoding="utf-8")
        migrations = ["seed/world.sql"]
        store = StoreEntry(
            capability="world_db",
            migrations=migrations,
            seed_files=[],
            baseline=StoreBaseline(
                strategy=BaselineStrategy.TEMPLATE_DATABASE,
                inputs_digest=compute_inputs_digest(
                    temporary,
                    migrations,
                    [],
                    engine=ManagedEngine.POSTGRES,
                    version="16",
                ),
            ),
            sentinel=Sentinel(
                query="SELECT id FROM harness_seed_sentinel WHERE id='ready'",
                expected="ready",
            ),
        )
        provider_manifest: ProviderRepositoryManifest | None = None
        if job.agent.mode is ProviderExecutionMode.ENVIRONMENT_BACKED:
            provider_manifest = load_provider_manifest(
                source_root,
                str(job.agent.config.get("lifecycle_manifest") or "alk.yaml"),
            )
            declared = set(provider_manifest.provider.required_secrets)
            supplied = set(job.agent.secret_refs)
            missing = sorted(declared - supplied)
            if missing:
                raise BundleAuthorError(
                    "provider_lifecycle_secrets_missing: " + ", ".join(missing)
                )

        manifest = EnvironmentBundleV2(
            schema_version=BUNDLE_V2_SCHEMA_VERSION,
            digest="sha256:" + "0" * 64,
            name=str(job.metadata.get("name") or source_root.name),
            runtime=BundleRuntimeV2(
                kind=RuntimeKindV2.PROCESS,
                control_service=plan.control_service,
                evidence_seam=EvidenceSeam.TOOL_TRACE,
            ),
            processes=list(plan.processes),
            seed=Seed(stores=[store]),
            capabilities=plan.capabilities,
            readiness=list(plan.readiness),
            files=_files(temporary),
            provenance=BundleProvenanceV2(
                source_kind=job.source.kind.value,
                repository=job.source.repository,
                commit=job.source.commit_sha,
                source_digest=source_fingerprint(source_root),
                generator="fi.alk.harness.bundle_author_v2",
                generator_version="2",
                adopted_files=["scenarios/"] + adopted_seed + adopted_chat_files,
                generated_files=["manifest.json", "seed/world.sql"],
            ),
            metadata={
                "packaging": plan.packaging,
                "environment_plan_version": "2",
                **(
                    {
                        "provider_lifecycle": provider_manifest.provider.model_dump(
                            mode="json"
                        )
                    }
                    if provider_manifest is not None
                    else {}
                ),
                "environment_plan_hash": hashlib.sha256(
                    json.dumps(
                        {
                            "packaging": plan.packaging,
                            "control_service": plan.control_service,
                            "processes": [
                                item.model_dump(mode="json") for item in plan.processes
                            ],
                            "capabilities": {
                                key: value.model_dump(mode="json")
                                for key, value in plan.capabilities.items()
                            },
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
            },
        )
        manifest = manifest.model_copy(update={"digest": seal_bundle_v2(manifest)})
        (temporary / BUNDLE_V2_MANIFEST).write_text(
            json.dumps(manifest.model_dump(mode="json"), indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        loaded = load_bundle_v2(temporary)
        preflight_bundle(
            temporary,
            loaded,
            parallelism=job.runtime.parallelism,
            secret_refs={
                alias: reference.purpose
                for alias, reference in job.agent.secret_refs.items()
            },
        )
        if output_root.exists():
            backup = output_root.with_name(output_root.name + ".previous")
            if backup.exists():
                shutil.rmtree(backup)
            output_root.rename(backup)
            temporary.rename(output_root)
            shutil.rmtree(backup)
        else:
            temporary.rename(output_root)
        return loaded
    except Exception:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def _load_job(path: Path) -> HarnessJob:
    return HarnessJob.model_validate_json(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alk-bundle-author-v2")
    parser.add_argument("--job", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--authoring", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    author_bundle_v2(
        source=args.source,
        job=_load_job(args.job),
        authoring=args.authoring,
        output=args.output,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
