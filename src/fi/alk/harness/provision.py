"""Provision the runtime an agent repository already describes.

The source repository is the authority for infrastructure.  If it ships a Compose file, the
harness starts that file under an isolated project name, substitutes free host ports, waits for
its health checks, and records the URLs that the agent must receive.  It does not ask a model to
rewrite Dockerfiles, migrations, seed data, or service implementations.

This module deliberately has no Docker SDK dependency.  Compose itself is the parser and the
lifecycle manager, so every Compose feature the user's project supports keeps working here too.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import socket
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

MANIFEST = "environment.json"
COMPOSE_FILES = (
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)

# ${TOOLS_PORT:-18090}:8080 is the common way a repository makes a published port movable.
_PORT_VARIABLE = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-?(?P<default>\d+))?\}"
    r"\s*:\s*(?P<target>\d+)"
)
# Covers os.environ.get/getenv and the equivalent bracket lookup.  The default URL is useful:
# it identifies which published service this setting points to without guessing from its name.
_URL_SETTING = re.compile(
    r"(?:os\.(?:environ\.get|getenv)\(\s*|os\.environ\[\s*)"
    r"[\"'](?P<name>[A-Za-z_][A-Za-z0-9_]*)[\"']"
    r"(?:\s*,\s*[\"'](?P<url>https?://[^\"']+)[\"'])?"
)
_AGENT_NAME_SETTING = re.compile(
    r"agent_name\s*=\s*os\.(?:environ\.get|getenv)\(\s*"
    r"[\"']LIVEKIT_AGENT_NAME[\"']\s*,\s*[\"'](?P<default>[^\"']+)[\"']"
)


class ProvisionError(RuntimeError):
    """The source environment could not be discovered, started, or inspected."""


@dataclass
class ProvisionedEnvironment:
    source: str
    compose_file: str
    project: str
    services: list[str] = field(default_factory=list)
    port_variables: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)
    internal_overrides: dict[str, str] = field(default_factory=dict)
    runtime_services: list[str] = field(default_factory=list)
    runtime_container: str = ""
    running: bool = False
    source_fingerprint: str = ""
    provision_seconds: float = 0.0
    managed: bool = False

    def save(self, destination: Path) -> Path:
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / MANIFEST
        target.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, destination: Path) -> ProvisionedEnvironment | None:
        target = Path(destination) / MANIFEST
        if not target.exists():
            return None
        return cls(**json.loads(target.read_text(encoding="utf-8")))


def compose_file(source: str | Path) -> Path | None:
    root = Path(source).expanduser().resolve()
    for name in COMPOSE_FILES:
        candidate = root / name
        if candidate.is_file():
            return candidate
    return None


_FINGERPRINT_IGNORED = {
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


def source_fingerprint(source: str | Path) -> str:
    """Identify the exact submitted source used to build an environment.

    A path is not a version. Reusing a healthy Compose project after files at that path changed
    silently runs yesterday's image against today's contract and scenarios. Hash the build
    context while excluding local caches and generated artifacts so reuse is both safe and fast.
    """
    root = Path(source).expanduser().resolve()
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if any(part in _FINGERPRINT_IGNORED for part in path.relative_to(root).parts):
            continue
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix().encode()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        try:
            with path.open("rb") as source_file:
                while chunk := source_file.read(1024 * 1024):
                    digest.update(chunk)
        except OSError as exc:
            raise ProvisionError(f"could not fingerprint {path}: {exc}") from exc
    return digest.hexdigest()


def _free_port() -> int:
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        return int(held.getsockname()[1])


def port_variables(path: Path) -> dict[str, str]:
    """Give each interpolated published port its own currently-free host port."""
    text = path.read_text(encoding="utf-8")
    assigned: dict[str, str] = {}
    used: set[int] = set()
    for match in _PORT_VARIABLE.finditer(text):
        name = match.group("name")
        if name in assigned:
            continue
        port = _free_port()
        while port in used:
            port = _free_port()
        used.add(port)
        assigned[name] = str(port)
    return assigned


def _declared_port_targets(path: Path) -> dict[int, int]:
    """Map the repository's default host ports to their container ports."""
    targets: dict[int, int] = {}
    for match in _PORT_VARIABLE.finditer(path.read_text(encoding="utf-8")):
        if match.group("default"):
            targets[int(match.group("default"))] = int(match.group("target"))
    return targets


def _run(
    environment: ProvisionedEnvironment,
    *arguments: str,
    check: bool = True,
    timeout: int = 900,
) -> str:
    command = [
        "docker",
        "compose",
        "--file",
        environment.compose_file,
        "--project-name",
        environment.project,
        *arguments,
    ]
    process_env = {**os.environ, **environment.port_variables}
    try:
        completed = subprocess.run(
            command,
            cwd=environment.source,
            env=process_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise ProvisionError("Docker is not installed or is not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProvisionError(f"environment command timed out after {timeout}s") from exc
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if check and completed.returncode:
        raise ProvisionError(
            f"{' '.join(command)} failed ({completed.returncode}): {output or 'no output'}"
        )
    return output


def _config(environment: ProvisionedEnvironment) -> dict[str, Any]:
    # Include opt-in profiles while inspecting. They are not started as infrastructure, but the
    # harness must still discover which service is the submitted agent runtime.
    rendered = _run(
        environment,
        "--profile",
        "*",
        "config",
        "--format",
        "json",
        timeout=60,
    )
    try:
        value = json.loads(rendered)
    except json.JSONDecodeError as exc:
        raise ProvisionError(
            f"Docker Compose returned invalid configuration: {exc}"
        ) from exc
    if not isinstance(value, dict) or not isinstance(value.get("services"), dict):
        raise ProvisionError("Docker Compose configuration has no services")
    return value


def _started_services(config: dict[str, Any]) -> list[str]:
    """Services Compose starts by default; opt-in profile services stay opt-in."""
    return sorted(
        name
        for name, service in config["services"].items()
        if not (service.get("profiles") or [])
    )


def _runtime_services(config: dict[str, Any]) -> list[str]:
    """Opt-in services that look like the submitted agent/worker runtime."""
    profiled = [
        name
        for name, service in config["services"].items()
        if service.get("profiles") or []
    ]
    preferred = [
        name
        for name in profiled
        if any(word in name.lower() for word in ("agent", "worker", "bot"))
    ]
    return sorted(preferred or profiled)


def _published_port(
    environment: ProvisionedEnvironment, service: str, target: int
) -> int:
    shown = _run(environment, "port", service, str(target), timeout=30)
    line = next((line for line in shown.splitlines() if line.strip()), "")
    try:
        return int(line.rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ProvisionError(
            f"could not determine the host port for {service}:{target}: {shown or 'no mapping'}"
        ) from exc


def _url_settings(source: Path) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    ignored = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}
    for path in source.rglob("*.py"):
        if any(part in ignored for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for match in _URL_SETTING.finditer(text):
            url = match.group("url") or ""
            if url:
                found.append((match.group("name"), url))
    return found


def _overrides(
    environment: ProvisionedEnvironment, config: dict[str, Any]
) -> dict[str, str]:
    """Map source URL settings to the equivalent endpoint in this isolated Compose project."""
    published: list[tuple[str, int, int]] = []
    for service_name in environment.services:
        service = config["services"][service_name]
        for port in service.get("ports") or []:
            target = int(port.get("target") or 0)
            published_port = int(port.get("published") or 0)
            if target and published_port:
                published.append((service_name, target, published_port))

    answers: dict[str, str] = {}
    declared = _declared_port_targets(Path(environment.compose_file))
    for variable, default in _url_settings(Path(environment.source)):
        parsed = urlsplit(default)
        default_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        expected_target = declared.get(default_port)
        match = next(
            (
                item
                for item in published
                if item[2] == default_port
                or (expected_target is not None and item[1] == expected_target)
            ),
            None,
        )
        if match is None:
            continue
        service, target, _ = match
        live_port = _published_port(environment, service, target)
        answers[variable] = f"{parsed.scheme}://127.0.0.1:{live_port}"
    return answers


def _internal_overrides(
    environment: ProvisionedEnvironment, config: dict[str, Any]
) -> dict[str, str]:
    """The same inferred endpoints, addressed from another Compose container."""
    published: list[tuple[str, int, int]] = []
    for service_name in environment.services:
        service = config["services"][service_name]
        for port in service.get("ports") or []:
            target = int(port.get("target") or 0)
            host_port = int(port.get("published") or 0)
            if target and host_port:
                published.append((service_name, target, host_port))
    declared = _declared_port_targets(Path(environment.compose_file))
    answers: dict[str, str] = {}
    for variable, default in _url_settings(Path(environment.source)):
        parsed = urlsplit(default)
        default_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        expected_target = declared.get(default_port)
        match = next(
            (
                item
                for item in published
                if item[2] == default_port
                or (expected_target is not None and item[1] == expected_target)
            ),
            None,
        )
        if match is not None:
            service, target, _ = match
            answers[variable] = f"{parsed.scheme}://{service}:{target}"
    return answers


def _environment_values(service: dict[str, Any]) -> dict[str, str]:
    raw = service.get("environment") or {}
    if isinstance(raw, list):
        return dict(
            entry.split("=", 1) if "=" in entry else (entry, "") for entry in raw
        )
    return {str(key): str(value) for key, value in raw.items()}


def postgres_dsn(destination: str | Path) -> str:
    """Resolve the submitted Compose project's Postgres endpoint without persisting its secret."""
    environment = ProvisionedEnvironment.load(Path(destination))
    if environment is None or not environment.running:
        raise ProvisionError(f"no running environment recorded at {destination}")
    config = _config(environment)
    candidates: list[tuple[str, dict[str, Any]]] = []
    for name in environment.services:
        service = config["services"].get(name) or {}
        image = str(service.get("image") or "").lower()
        targets = {int(port.get("target") or 0) for port in service.get("ports") or []}
        if image.startswith("postgres:") or 5432 in targets:
            candidates.append((name, service))
    if len(candidates) != 1:
        raise ProvisionError(
            "expected exactly one Postgres service in the submitted environment, found "
            + (", ".join(name for name, _ in candidates) or "none")
        )
    name, service = candidates[0]
    values = _environment_values(service)
    user = values.get("POSTGRES_USER") or "postgres"
    password = values.get("POSTGRES_PASSWORD") or ""
    database = values.get("POSTGRES_DB") or user
    port = _published_port(environment, name, 5432)
    return (
        f"postgresql://{quote(user, safe='')}:{quote(password, safe='')}"
        f"@127.0.0.1:{port}/{quote(database, safe='')}"
    )


def attached_postgres_store(destination: str | Path):
    """The standard world-store interface over the repository's running Postgres."""
    from .world.stores.postgres import AttachedPostgresStore

    return AttachedPostgresStore(postgres_dsn(destination))


def _configuration_name(value: str) -> str:
    """Extract an environment variable name from a contract's prose or config key."""
    candidates = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", value or "")
    return (
        candidates[0]
        if candidates
        else (value.strip() if value.strip().isupper() else "")
    )


def _managed_compose(source: Path, destination: Path, contract: Any) -> Path | None:
    """Write a minimal harness-owned Compose adapter for a source-declared Postgres store.

    This is intentionally narrow and truthful: it supplies infrastructure and the repository's
    own runtime, never an invented tool service. Repositories with their own Compose file remain
    authoritative. Repositories with only in-process tools need no container environment.
    """
    store = getattr(contract, "data_store", None)
    kind = str(getattr(store, "kind", "") or "").lower()
    dependencies = list(getattr(contract, "dependencies", None) or [])
    needs_postgres = "postgres" in kind or any(
        "postgres"
        in f"{getattr(one, 'name', '')} {getattr(one, 'kind', '')} {getattr(one, 'what', '')}".lower()
        for one in dependencies
    )
    if not needs_postgres:
        return None
    runtime = getattr(contract, "runtime", None)
    dockerfile_value = str(getattr(runtime, "dockerfile", "") or "Dockerfile")
    dockerfile = source / dockerfile_value
    if not dockerfile.is_file():
        raise ProvisionError(
            "the agent requires Postgres but ships neither Compose nor a Dockerfile; "
            "the harness can provision dependencies only when it can run the submitted code"
        )

    init_mounts: list[str] = []
    schema_value = str(getattr(store, "schema_from", "") or "")
    if schema_value:
        schema = (source / schema_value).resolve()
        if schema.exists() and (schema.is_dir() or schema.suffix.lower() == ".sql"):
            init_mounts.append(f"{schema}:/docker-entrypoint-initdb.d/source:ro")

    variable = (
        _configuration_name(
            str(getattr(store, "config_key", "") or "")
            or str(getattr(store, "configured_by", "") or "")
        )
        or "DATABASE_URL"
    )
    database = str(getattr(store, "database", "") or "harness")
    user = str(getattr(store, "user", "") or "harness")
    internal_dsn = f"postgresql://{user}:harness-only@postgres:5432/{database}"
    document = {
        "services": {
            "postgres": {
                "image": f"postgres:{getattr(store, 'version', '') or '16'}",
                "environment": {
                    "POSTGRES_DB": database,
                    "POSTGRES_USER": user,
                    "POSTGRES_PASSWORD": "harness-only",
                },
                "ports": ["${HARNESS_POSTGRES_PORT:-55432}:5432"],
                "healthcheck": {
                    "test": ["CMD-SHELL", f"pg_isready -U {user} -d {database}"],
                    "interval": "1s",
                    "timeout": "3s",
                    "retries": 30,
                },
                "volumes": init_mounts,
            },
            "agent-runtime": {
                "build": {"context": str(source), "dockerfile": dockerfile_value},
                "profiles": ["harness-runtime"],
                "environment": {variable: internal_dsn},
                "depends_on": {"postgres": {"condition": "service_healthy"}},
            },
        }
    }
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "managed-compose.json"
    target.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return target


def provision(
    source: str | Path,
    destination: str | Path,
    contract: Any | None = None,
) -> ProvisionedEnvironment:
    """Start and record the environment described by one source repository."""
    source_root = Path(source).expanduser().resolve()
    destination = Path(destination)
    compose = compose_file(source_root)
    managed = False
    if compose is None and contract is not None:
        compose = _managed_compose(source_root, destination, contract)
        managed = compose is not None
    if compose is None:
        raise ProvisionError(
            f"{source_root} does not ship a Compose file; a non-Compose runtime adapter is required"
        )
    fingerprint = source_fingerprint(source_root)
    existing = ProvisionedEnvironment.load(destination)
    if (
        existing
        and Path(existing.source) == source_root
        and existing.source_fingerprint == fingerprint
        and existing.running
    ):
        # Verify rather than trusting a stale file left by a killed process.
        if _run(
            existing, "ps", "--status", "running", "--quiet", check=False, timeout=30
        ):
            config = _config(existing)
            existing.services = _started_services(config)
            existing.runtime_services = _runtime_services(config)
            existing.overrides = _overrides(existing, config)
            existing.internal_overrides = _internal_overrides(existing, config)
            existing.save(destination)
            return existing

    # A recorded project belongs only to this session. If its source changed or its process died,
    # remove its test volumes before replacing it so stale rows and orphan containers cannot be
    # mistaken for the newly submitted environment.
    if existing is not None and existing.running:
        _run(
            existing,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
            timeout=120,
        )

    project = "fagi-harness-" + secrets.token_hex(4)
    environment = ProvisionedEnvironment(
        source=str(source_root),
        compose_file=str(compose),
        project=project,
        port_variables=port_variables(compose),
        source_fingerprint=fingerprint,
        managed=managed,
    )
    config = _config(environment)
    environment.services = _started_services(config)
    environment.runtime_services = _runtime_services(config)
    if not environment.services:
        raise ProvisionError("the Compose file has no services enabled by default")
    try:
        started = time.monotonic()
        _run(environment, "up", "--detach", "--build", "--wait", *environment.services)
        environment.overrides = _overrides(environment, config)
        environment.internal_overrides = _internal_overrides(environment, config)
        environment.running = True
        environment.provision_seconds = round(time.monotonic() - started, 3)
        environment.save(destination)
        return environment
    except Exception:
        _run(
            environment,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
            timeout=120,
        )
        raise


def provision_if_present(
    source: str | Path, destination: str | Path, contract: Any | None = None
) -> ProvisionedEnvironment | None:
    """Provision a repository's Compose environment, or do nothing for in-process agents."""
    if compose_file(source) is None and contract is None:
        return None
    if compose_file(source) is None:
        store = getattr(contract, "data_store", None)
        dependencies = list(getattr(contract, "dependencies", None) or [])
        if store is None and not dependencies:
            return None
    return provision(source, destination, contract)


def reset(destination: str | Path) -> ProvisionedEnvironment:
    """Return a provisioned environment to the repository's declared seed state.

    This generic reset is deliberately lifecycle-based: removing only this Compose project's
    volumes and recreating its already-built default services works for databases, queues and
    filesystems without guessing their internal reset protocol. Engine-specific snapshot
    adapters may optimise this later, but they must preserve the same observable result.
    """
    destination = Path(destination)
    environment = ProvisionedEnvironment.load(destination)
    if environment is None:
        raise ProvisionError(f"no environment recorded at {destination}")
    _run(
        environment,
        "down",
        "--volumes",
        "--remove-orphans",
        check=False,
        timeout=120,
    )
    started = time.monotonic()
    _run(
        environment,
        "up",
        "--detach",
        "--no-build",
        "--wait",
        *environment.services,
    )
    config = _config(environment)
    environment.services = _started_services(config)
    environment.runtime_services = _runtime_services(config)
    environment.overrides = _overrides(environment, config)
    environment.internal_overrides = _internal_overrides(environment, config)
    environment.running = True
    environment.provision_seconds = round(time.monotonic() - started, 3)
    environment.save(destination)
    return environment


def healthy(destination: str | Path) -> bool:
    """Whether the recorded project still has all of its expected services running.

    Runtime providers use this public probe rather than reaching into Compose mechanics.  A
    stale manifest therefore becomes an unhealthy runtime instead of being trusted as ready.
    """
    environment = ProvisionedEnvironment.load(Path(destination))
    if environment is None or not environment.running or not environment.services:
        return False
    running = _run(
        environment,
        "ps",
        "--status",
        "running",
        "--services",
        check=False,
        timeout=30,
    )
    found = {line.strip() for line in running.splitlines() if line.strip()}
    return set(environment.services).issubset(found)


def _docker(*arguments: str, check: bool = True, timeout: int = 120) -> str:
    try:
        completed = subprocess.run(
            ["docker", *arguments],
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ProvisionError(
            f"docker {' '.join(arguments)} could not run: {exc}"
        ) from exc
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if check and completed.returncode:
        raise ProvisionError(
            f"docker {' '.join(arguments)} failed ({completed.returncode}): "
            f"{output or 'no output'}"
        )
    return output


def _valid_google_credentials(path: Path) -> bool:
    """Google's SDK accepts a path long before it discovers malformed JSON.

    A Compose placeholder such as ``/dev/null`` therefore looks configured during discovery but
    fails only after a paid voice call has started.  Validate the actual host file before mounting
    it into a submitted worker.
    """
    if not path.is_file():
        return False
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and bool(value)


def _runtime_credential_mounts(
    service_environment: dict[str, str], service_config: dict[str, Any]
) -> list[tuple[str, Path, str]]:
    """Resolve credential variables to real host files and their container targets.

    Repository Compose files commonly expose a stable in-container credential path backed by a
    harmless placeholder when run without platform secrets.  The harness replaces that placeholder
    with its platform-owned credential; repository users should not have to copy secrets into the
    submitted source tree.
    """
    declared_volumes = {
        str(volume.get("target", "")): Path(str(volume.get("source", ""))).expanduser()
        for volume in service_config.get("volumes", [])
        if isinstance(volume, dict) and volume.get("target") and volume.get("source")
    }
    mounts: list[tuple[str, Path, str]] = []
    for name, value in service_environment.items():
        if not any(marker in name.upper() for marker in ("CREDENTIAL", "KEY_FILE")):
            continue
        configured = Path(value).expanduser()
        target = f"/run/harness-secrets/{configured.name}"
        source = (
            configured if configured.is_absolute() and configured.is_file() else None
        )
        if configured.is_absolute() and value in declared_volumes:
            target = value
            declared = declared_volumes[value]
            source = declared if declared.is_file() else None
        if name.upper() == "GOOGLE_APPLICATION_CREDENTIALS":
            if source is None or not _valid_google_credentials(source):
                platform_value = os.environ.get(name, "").strip()
                platform = Path(platform_value).expanduser() if platform_value else None
                if platform is not None and _valid_google_credentials(platform):
                    source = platform
            if source is None or not _valid_google_credentials(source):
                raise ProvisionError(
                    "the submitted runtime needs GOOGLE_APPLICATION_CREDENTIALS, but neither its "
                    "Compose mount nor the harness platform points to a valid JSON credential file"
                )
        if source is not None:
            mounts.append((name, source.resolve(), target))
    return mounts


def start_runtime(
    destination: str | Path,
    *,
    overrides: dict[str, str] | None = None,
    trace_path: str | Path | None = None,
) -> ProvisionedEnvironment:
    """Start the submitted agent/worker service with only test endpoint substitutions."""
    destination = Path(destination)
    environment = ProvisionedEnvironment.load(destination)
    if environment is None or not environment.running:
        raise ProvisionError(f"no running environment recorded at {destination}")
    if len(environment.runtime_services) != 1:
        raise ProvisionError(
            "expected exactly one opt-in agent runtime service, found "
            + (", ".join(environment.runtime_services) or "none")
        )
    service = environment.runtime_services[0]
    container = f"{environment.project}-runtime"
    # The manifest write happens after readiness. A killed harness can therefore leave the
    # session-owned container behind without recording it; always reconcile the deterministic
    # name before starting instead of trusting bookkeeping from a process that may have died.
    _docker("rm", "--force", container, check=False)
    injected = {
        **environment.internal_overrides,
        "HARNESS_MODE": "1",
        # The SDK recorder is also a remote LiveKit participant. Agents that
        # wait for an arbitrary participant can otherwise bind their audio
        # input to the silent recorder and stall after one turn. This stable
        # prefix identifies the actual simulated caller across scenarios.
        "HARNESS_CALLER_IDENTITY_PREFIX": "fagi-simulator",
        **(overrides or {}),
    }
    arguments = [
        "run",
        "--detach",
        "--no-deps",
        "--name",
        container,
    ]
    if trace_path is not None:
        trace = Path(trace_path).expanduser().resolve()
        trace.parent.mkdir(parents=True, exist_ok=True)
        # Mount only this scenario's result folder. Agents that support the generic harness trace
        # seam write semantic/model-facing tool events here; agents that do not simply ignore the
        # variable and the backend proxy remains the fallback evidence source.
        arguments.extend(("--volume", f"{trace.parent}:/run/harness-trace"))
        container_trace = f"/run/harness-trace/{trace.name}"
        # HARNESS_AGENT_TOOL_TRACE is the runtime-level contract. Keep the shorter historical
        # name as a compatibility alias for submitted agents that already adopted it; both point
        # at the same mounted file, so evidence is still collected exactly once.
        injected["HARNESS_AGENT_TOOL_TRACE"] = container_trace
        injected["HARNESS_TOOL_TRACE"] = container_trace
    config = _config(environment)
    service_config = config["services"][service]
    service_environment = _environment_values(service_config)
    # Credential paths in a repository env file name host files. Mount them into a stable,
    # read-only container location and replace only the path value; never copy or persist the
    # credential contents in harness artifacts.
    for name, source, target in _runtime_credential_mounts(
        service_environment, service_config
    ):
        arguments.extend(("--volume", f"{source}:{target}:ro"))
        injected[name] = target
    for name, value in sorted(injected.items()):
        arguments.extend(("--env", f"{name}={value}"))
    arguments.append(service)
    _run(environment, *arguments, timeout=900)
    deadline = time.monotonic() + 60
    # LiveKit workers commonly stay alive while warming VAD/STT/TTS processes and only register
    # for dispatch afterwards.  Starting a room after five seconds races that registration: the
    # container looks healthy, but LiveKit has nobody to dispatch to.  Fifteen seconds covers the
    # observed plugin warm-up while remaining configurable for unusually small or large workers.
    stable_seconds = max(
        1.0, float(os.environ.get("HARNESS_RUNTIME_STABLE_SECONDS", "15"))
    )
    stable_since: float | None = None
    while time.monotonic() < deadline:
        status = _docker(
            "inspect",
            "--format",
            "{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}",
            container,
            check=False,
            timeout=10,
        ).strip()
        if status.startswith("running healthy"):
            break
        if status.startswith("running"):
            stable_since = stable_since or time.monotonic()
            # A worker often has no healthcheck. Remaining alive through startup is the strongest
            # generic signal available; an immediate import/configuration crash is still caught.
            # This window must include worker registration, not only process creation.
            if time.monotonic() - stable_since >= stable_seconds:
                break
        else:
            stable_since = None
            if status.startswith(("exited", "dead")):
                logs = _docker(
                    "logs", "--tail", "40", container, check=False, timeout=10
                )
                raise ProvisionError(
                    f"submitted runtime {service!r} exited during startup:\n{logs}"
                )
        time.sleep(0.25)
    else:
        raise ProvisionError(
            f"submitted runtime {service!r} did not become ready within 60s"
        )
    environment.runtime_container = container
    environment.save(destination)
    return environment


def runtime_environment(destination: str | Path) -> dict[str, str]:
    """Return the submitted worker's rendered environment without writing it to artifacts."""
    environment = ProvisionedEnvironment.load(Path(destination))
    if environment is None or not environment.running:
        raise ProvisionError(f"no running environment recorded at {destination}")
    if len(environment.runtime_services) != 1:
        raise ProvisionError(
            "expected exactly one opt-in agent runtime service, found "
            + (", ".join(environment.runtime_services) or "none")
        )
    config = _config(environment)
    return _environment_values(config["services"][environment.runtime_services[0]])


def infer_livekit_agent_name(destination: str | Path) -> str:
    """Read the worker's configured or source-default LiveKit dispatch name."""
    configured = runtime_environment(destination).get("LIVEKIT_AGENT_NAME", "").strip()
    if configured:
        return configured
    environment = ProvisionedEnvironment.load(Path(destination))
    if environment is None:
        return ""
    source = Path(environment.source)
    for path in source.rglob("*.py"):
        if any(part in _FINGERPRINT_IGNORED for part in path.relative_to(source).parts):
            continue
        try:
            match = _AGENT_NAME_SETTING.search(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
        if match:
            return match.group("default").strip()
    return ""


def activate_voice_environment(
    destination: str | Path, *, system_prompt: str = ""
) -> dict[str, str]:
    """Make source-owned voice credentials available to the in-process simulator.

    Compose has already resolved the repository's env files. Values are copied only into this
    process for the duration of the harness process and are never added to environment.json or
    run artifacts. Existing platform-wide simulator settings always win.
    """
    values = runtime_environment(destination)
    allowed = {
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "DEEPGRAM_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_LOCATION",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "CARTESIA_API_KEY",
        "ELEVEN_API_KEY",
        "ELEVENLABS_API_KEY",
    }
    activated: dict[str, str] = {}
    for name in allowed:
        value = values.get(name, "").strip()
        if value and not os.environ.get(name, "").strip():
            os.environ[name] = value
            activated[name] = value
    agent_name = infer_livekit_agent_name(destination)
    if agent_name and not os.environ.get("LIVEKIT_TARGET_AGENT_NAME", "").strip():
        os.environ["LIVEKIT_TARGET_AGENT_NAME"] = agent_name
        activated["LIVEKIT_TARGET_AGENT_NAME"] = agent_name
    if system_prompt and not os.environ.get("LIVEKIT_TARGET_SYSTEM_PROMPT", "").strip():
        os.environ["LIVEKIT_TARGET_SYSTEM_PROMPT"] = system_prompt
        activated["LIVEKIT_TARGET_SYSTEM_PROMPT"] = system_prompt
    return activated


def stop_runtime(destination: str | Path) -> bool:
    """Stop only the submitted worker, leaving its scenario infrastructure running."""
    destination = Path(destination)
    environment = ProvisionedEnvironment.load(destination)
    if environment is None or not environment.runtime_container:
        return False
    _docker("rm", "--force", environment.runtime_container, check=False)
    environment.runtime_container = ""
    environment.save(destination)
    return True


def stop(destination: str | Path) -> bool:
    """Tear down the exact project recorded for a session, including its test data."""
    destination = Path(destination)
    environment = ProvisionedEnvironment.load(destination)
    if environment is None:
        return False
    _run(environment, "down", "--volumes", "--remove-orphans", check=False, timeout=120)
    environment.running = False
    environment.save(destination)
    return True
