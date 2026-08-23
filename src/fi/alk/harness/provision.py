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
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import quote, urlsplit, urlunsplit

from .packaging import PackagingKind, inspect_packaging
from .generated_runtime import (
    GENERATED_DOCKERFILE,
    GeneratedRuntimeError,
    GeneratedRuntimePlan,
    can_generate_runtime,
    prepare_generated_runtime,
)
from .service_catalog import address, profile_for

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
    r"(?:\s*,\s*[\"'](?P<url>[a-zA-Z][a-zA-Z0-9+.-]*://[^\"']+)[\"'])?"
)
_JS_URL_SETTING = re.compile(
    r"process\.env\.(?P<name>[A-Z][A-Z0-9_]{2,})\s*(?:\?\?|\|\|)\s*"
    r"[\"'](?P<url>[a-zA-Z][a-zA-Z0-9+.-]*://[^\"']+)[\"']"
)
_DOTENV_URL_SETTING = re.compile(
    r"(?m)^\s*(?P<name>[A-Z][A-Z0-9_]{2,})\s*=\s*"
    r"(?P<url>[a-zA-Z][a-zA-Z0-9+.-]*://\S+)\s*$"
)
_ENV_NAME = re.compile(
    r"(?:os\.(?:environ\.get|getenv)\(\s*|os\.environ\[\s*|process\.env\.)"
    r"[\"']?(?P<name>[A-Z][A-Z0-9_]{2,})"
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
    compose_override_file: str = ""
    services: list[str] = field(default_factory=list)
    port_variables: dict[str, str] = field(default_factory=dict)
    overrides: dict[str, str] = field(default_factory=dict)
    internal_overrides: dict[str, str] = field(default_factory=dict)
    service_endpoints: list[dict[str, Any]] = field(default_factory=list)
    runtime_services: list[str] = field(default_factory=list)
    runtime_container: str = ""
    runtime_trace_volume: str = ""
    runtime_trace_path: str = ""
    runner_network: str = ""
    running: bool = False
    source_fingerprint: str = ""
    provision_seconds: float = 0.0
    managed: bool = False
    generated_runtime_plan: str = ""
    runtime_fingerprint: str = ""
    # Names only. Values are resolved from the job secret environment immediately before the
    # ephemeral worker starts and are never serialized into environment.json or a bundle.
    runtime_configuration_names: list[str] = field(default_factory=list)

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
    packaging = inspect_packaging(root)
    if packaging.ready and packaging.selected_kind is PackagingKind.COMPOSE:
        assert packaging.selected_path is not None
        return root / packaging.selected_path
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
        if path.is_symlink():
            # Never follow a submitted link while fingerprinting. A malicious
            # repository could otherwise make preflight read an arbitrary host file.
            relative = path.relative_to(root).as_posix().encode()
            target = os.readlink(path).encode(errors="surrogateescape")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(b"\0symlink\0")
            digest.update(target)
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
    process_overrides: dict[str, str] | None = None,
) -> str:
    files = ["--file", environment.compose_file]
    if environment.compose_override_file:
        files.extend(("--file", environment.compose_override_file))
    command = [
        "docker",
        "compose",
        *files,
        "--project-name",
        environment.project,
        *arguments,
    ]
    # Runtime credentials are inherited by Compose and selected with ``--env NAME``. They must
    # never be serialized into argv, where error messages and host process listings expose them.
    process_env = {
        **os.environ,
        **environment.port_variables,
        **(process_overrides or {}),
    }
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
        shown = _command_failure_output(output)
        raise ProvisionError(
            f"{' '.join(command)} failed ({completed.returncode}): {shown}"
        )
    return output


def _start_managed_services(
    environment: ProvisionedEnvironment, services: list[str], *, build: bool = True
) -> None:
    """Start dependencies, retrying one clean boot for harness-owned stacks only."""
    attempts = 2 if environment.managed else 1
    for attempt in range(attempts):
        try:
            # Managed dependencies are independent. Starting heavyweight brokers and object
            # stores in one burst can exhaust a local/hosted sandbox's process budget even when
            # its steady-state capacity is sufficient. Admit each service to readiness before
            # starting the next; submitted Compose retains its own native dependency graph.
            groups = (
                ([service] for service in services)
                if environment.managed
                else [services]
            )
            for group in groups:
                _run(
                    environment,
                    "up",
                    "--detach",
                    "--build" if build else "--no-build",
                    "--wait",
                    *group,
                )
            return
        except ProvisionError as exc:
            detail = str(exc).lower()
            transient_boot = " exited (" in detail or "unhealthy" in detail
            if attempt + 1 >= attempts or not transient_boot:
                raise
            # A retry starts from a genuinely clean state. Reusing a partially initialized
            # broker/database volume makes the second attempt neither isolated nor diagnostic.
            _run(
                environment,
                "down",
                "--volumes",
                "--remove-orphans",
                check=False,
                timeout=120,
            )


def _command_failure_output(output: str, *, limit: int = 16_000) -> str:
    """Keep actionable build context without putting megabytes of logs into job state/UI."""
    if not output:
        return "no output"
    if len(output) <= limit:
        return output
    head_size = min(2000, max(1, limit // 4))
    head = output[:head_size]
    tail = output[-(limit - len(head)) :]
    omitted = len(output) - len(head) - len(tail)
    return f"{head}\n... {omitted} characters omitted ...\n{tail}"


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
    """Opt-in services that identify the submitted agent/worker runtime.

    Compose profiles are also commonly used for pgAdmin, dashboards and debugging tools. Treating
    every profiled service as an agent causes the harness to launch infrastructure UIs as if they
    were voice workers. A generated harness runtime is explicit; repository runtimes must carry a
    recognizable agent/worker/bot name.
    """
    profiled = [
        name
        for name, service in config["services"].items()
        if service.get("profiles") or []
    ]
    explicit = [
        name
        for name in profiled
        if "harness-runtime" in (config["services"][name].get("profiles") or [])
    ]
    if explicit:
        return sorted(explicit)
    preferred = [
        name
        for name in profiled
        if any(word in name.lower() for word in ("agent", "worker", "bot"))
    ]
    return sorted(preferred)


def _validate_compose_security(config: dict[str, Any]) -> None:
    """Reject host-escape primitives before any submitted container is created."""
    violations: list[str] = []
    for name, service in config["services"].items():
        if service.get("privileged"):
            violations.append(f"{name}: privileged")
        for key in ("network_mode", "pid", "ipc"):
            if str(service.get(key) or "").lower() == "host":
                violations.append(f"{name}: {key}=host")
        if service.get("devices"):
            violations.append(f"{name}: host devices")
        for volume in service.get("volumes") or []:
            source = (
                str(volume.get("source") or "")
                if isinstance(volume, dict)
                else str(volume)
            )
            if (
                source in {"/var/run/docker.sock", "/run/docker.sock"}
                or "docker.sock:" in source
            ):
                violations.append(f"{name}: Docker socket mount")
    if violations:
        raise ProvisionError(
            "submitted Compose requests forbidden host access: " + ", ".join(violations)
        )


def _write_port_override(
    destination: Path,
    environment: ProvisionedEnvironment,
    config: dict[str, Any],
) -> None:
    """Replace every fixed published port with a job-owned port.

    Environment variables cover only repositories that deliberately parameterise their Compose
    ports.  Most repositories publish constants, which collide as soon as two jobs overlap.  A
    generated Compose override makes isolation universal while leaving container ports intact.
    """
    services: list[tuple[str, list[dict[str, Any]]]] = []
    used: set[int] = set()
    dynamic_targets = set(
        _declared_port_targets(Path(environment.compose_file)).values()
    )
    bind_host = os.environ.get("ALK_DOCKER_BIND_HOST", "").strip() or (
        "127.0.0.1" if _published_host() == "127.0.0.1" else "0.0.0.0"
    )
    for name, service in config["services"].items():
        # Compose excludes profiled services unless that profile is explicitly enabled. Do not
        # allocate ports for dormant TURN/admin/debug services; doing so can exhaust a runner's
        # ephemeral port range before the selected environment even starts.
        if service.get("profiles"):
            continue
        ports: list[dict[str, Any]] = []
        for item in service.get("ports") or []:
            target = int(item.get("target") or 0)
            if not target or target in dynamic_targets:
                continue
            published = _free_port()
            while published in used:
                published = _free_port()
            used.add(published)
            ports.append(
                {
                    "target": target,
                    "published": str(published),
                    "host_ip": bind_host,
                    "protocol": str(item.get("protocol") or "tcp"),
                }
            )
        if ports:
            services.append((name, ports))
    if not services:
        return
    lines = ["services:"]
    for name, ports in services:
        lines.extend((f"  {json.dumps(name)}:", "    ports: !override"))
        for item in ports:
            lines.extend(
                (
                    f"      - target: {item['target']}",
                    f"        published: {json.dumps(item['published'])}",
                    f"        host_ip: {json.dumps(item['host_ip'])}",
                    f"        protocol: {item['protocol']}",
                )
            )
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / "compose.harness.override.yaml"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    environment.compose_override_file = str(target.resolve())


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
    for path in source.rglob("*"):
        if (
            path.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}
            and path.name != ".env.example"
        ):
            continue
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
        for pattern in (_JS_URL_SETTING, _DOTENV_URL_SETTING):
            found.extend(
                (match.group("name"), match.group("url"))
                for match in pattern.finditer(text)
            )
    return found


def _declared_configuration_names(source: Path) -> set[str]:
    """Configuration variables the submitted source actually reads or documents."""
    found = {name for name, _ in _url_settings(source)}
    ignored = {".git", ".venv", "node_modules", "dist", "build", "__pycache__"}
    for path in source.rglob("*"):
        if (
            path.suffix.lower() not in {".py", ".js", ".jsx", ".ts", ".tsx"}
            and path.name != ".env.example"
        ):
            continue
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found.update(match.group("name") for match in _ENV_NAME.finditer(text))
        if path.name == ".env.example":
            found.update(
                match.group(1)
                for match in re.finditer(r"(?m)^\s*([A-Z][A-Z0-9_]{2,})\s*=", text)
            )
    return found


def _replace_endpoint(value: str, host: str, port: int) -> str:
    parsed = urlsplit(value)
    userinfo = ""
    if parsed.username is not None:
        userinfo = quote(parsed.username, safe="")
        if parsed.password is not None:
            userinfo += ":" + quote(parsed.password, safe="")
        userinfo += "@"
    return urlunsplit(
        (
            parsed.scheme,
            f"{userinfo}{host}:{port}",
            parsed.path,
            parsed.query,
            parsed.fragment,
        )
    )


def _service_endpoints(
    environment: ProvisionedEnvironment, config: dict[str, Any]
) -> list[dict[str, Any]]:
    """Describe every published dependency; unknown images remain usable TCP services."""
    records: list[dict[str, Any]] = []
    for service_name in environment.services:
        service = config["services"][service_name]
        image = str(service.get("image") or "")
        for port in service.get("ports") or []:
            target = int(port.get("target") or 0)
            if not target:
                continue
            configured_host_port = int(port.get("published") or 0)
            try:
                host_port = _published_port(environment, service_name, target)
            except ProvisionError:
                # Primarily useful for provider adapters and dry-run validation. A real started
                # Compose project normally resolves through ``compose port``.
                if not configured_host_port:
                    raise
                host_port = configured_host_port
            profile = profile_for(service_name, image, target)
            records.append(
                {
                    "service": service_name,
                    "kind": profile.kind,
                    "protocol": profile.protocol,
                    "container_port": target,
                    "host_port": host_port,
                    "configured_host_port": configured_host_port,
                    "external_address": address(
                        profile.protocol, _published_host(), host_port
                    ),
                    "internal_address": address(profile.protocol, service_name, target),
                    "configuration_names": list(profile.configuration_names),
                    "readiness_path": profile.readiness_path,
                }
            )
    return records


def _endpoint_ready(endpoint: dict[str, Any], host: str) -> bool:
    """Probe the service protocol when known, falling back to a TCP connection."""
    port = int(endpoint["host_port"])
    protocol = str(endpoint.get("protocol") or "tcp")
    try:
        with socket.create_connection((host, port), timeout=0.75) as connection:
            if protocol == "redis":
                connection.sendall(b"*1\r\n$4\r\nPING\r\n")
                response = connection.recv(128)
                # An authentication challenge is a semantic Redis response and proves the
                # server is ready. Credentials are validated by the submitted agent itself.
                return response.startswith((b"+PONG", b"-NOAUTH"))
            if protocol == "amqp":
                # A listening RabbitMQ socket can appear before the AMQP application is ready.
                # Require the server to answer the protocol header, not merely accept TCP.
                connection.sendall(b"AMQP\x00\x00\x09\x01")
                return bool(connection.recv(128))
            if protocol == "nats":
                # NATS begins every client session with an INFO line once the server can route
                # traffic. This also distinguishes it from an unrelated process on the port.
                return connection.recv(256).startswith(b"INFO ")
    except OSError:
        return False
    readiness_path = str(endpoint.get("readiness_path") or "")
    if readiness_path and protocol in {"clickhouse", "http", "mcp", "s3"}:
        url = f"http://{host}:{port}/{readiness_path.lstrip('/')}"
        try:
            with urllib_request.build_opener(urllib_request.ProxyHandler({})).open(
                url, timeout=1.0
            ) as response:
                response.read(256)
                return 200 <= response.status < 400
        except (OSError, urllib_error.URLError, urllib_error.HTTPError):
            return False
    return True


def _wait_for_endpoints(
    endpoints: list[dict[str, Any]],
    timeout: float = 60.0,
    stability_seconds: float = 2.0,
) -> None:
    """Require stable protocol readiness, not merely a briefly-open container port."""
    indexed = {
        (str(item["service"]), int(item["host_port"])): item for item in endpoints
    }
    pending = set(indexed)
    ready_since: dict[tuple[str, int], float] = {}
    deadline = time.monotonic() + timeout
    host = _published_host()
    while pending and time.monotonic() < deadline:
        for endpoint in list(pending):
            if _endpoint_ready(indexed[endpoint], host):
                first_ready = ready_since.setdefault(endpoint, time.monotonic())
                # Protocol services can briefly answer while an init script is about to restart
                # them. A short stability window prevents the first real tool call racing that
                # transition. Unknown TCP services retain the fast generic behavior.
                needs_stability = bool(indexed[endpoint].get("readiness_path")) or str(
                    indexed[endpoint].get("protocol") or ""
                ) in {"amqp", "nats", "redis"}
                if (
                    not needs_stability
                    or time.monotonic() - first_ready >= stability_seconds
                ):
                    pending.remove(endpoint)
            else:
                ready_since.pop(endpoint, None)
        if pending:
            time.sleep(0.2)
    if pending:
        rendered = ", ".join(f"{service}:{port}" for service, port in sorted(pending))
        raise ProvisionError(f"environment endpoints did not become ready: {rendered}")


def _overrides(
    environment: ProvisionedEnvironment, config: dict[str, Any]
) -> dict[str, str]:
    """Map declared settings to the equivalent endpoint in this isolated project."""
    endpoints = environment.service_endpoints or _service_endpoints(environment, config)
    source = Path(environment.source)
    names = _declared_configuration_names(source)
    answers: dict[str, str] = {}
    declared = _declared_port_targets(Path(environment.compose_file))
    for variable, default in _url_settings(Path(environment.source)):
        parsed = urlsplit(default)
        default_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        expected_target = declared.get(default_port)
        match = next(
            (
                item
                for item in endpoints
                if item["host_port"] == default_port
                or item["configured_host_port"] == default_port
                or item["container_port"] == default_port
                or (
                    expected_target is not None
                    and item["container_port"] == expected_target
                )
            ),
            None,
        )
        if match is None:
            continue
        answers[variable] = _replace_endpoint(
            default, _published_host(), int(match["host_port"])
        )
    for endpoint in endpoints:
        kind = str(endpoint["kind"]).upper().replace("-", "_")
        candidates = set(endpoint["configuration_names"]) | {
            f"{kind}_HOST",
            f"{kind}_PORT",
        }
        for variable in sorted(names & candidates):
            if variable in answers:
                continue
            if variable.endswith("_HOST"):
                value = _published_host()
            elif variable.endswith("_PORT"):
                value = str(endpoint["host_port"])
            elif variable.endswith("BOOTSTRAP_SERVERS"):
                value = f"{_published_host()}:{endpoint['host_port']}"
            else:
                value = str(endpoint["external_address"])
            answers[variable] = value
    return answers


def _internal_overrides(
    environment: ProvisionedEnvironment, config: dict[str, Any]
) -> dict[str, str]:
    """The same inferred endpoints, addressed from another Compose container."""
    endpoints = environment.service_endpoints or _service_endpoints(environment, config)
    declared = _declared_port_targets(Path(environment.compose_file))
    answers: dict[str, str] = {}
    for variable, default in _url_settings(Path(environment.source)):
        parsed = urlsplit(default)
        default_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        expected_target = declared.get(default_port)
        match = next(
            (
                item
                for item in endpoints
                if item["host_port"] == default_port
                or item["configured_host_port"] == default_port
                or item["container_port"] == default_port
                or (
                    expected_target is not None
                    and item["container_port"] == expected_target
                )
            ),
            None,
        )
        if match is not None:
            answers[variable] = _replace_endpoint(
                default, str(match["service"]), int(match["container_port"])
            )
    external = _overrides(environment, config)
    by_name: dict[str, dict[str, Any]] = {}
    for endpoint in endpoints:
        for name in endpoint["configuration_names"]:
            by_name[name] = endpoint
        kind = str(endpoint["kind"]).upper().replace("-", "_")
        by_name[f"{kind}_HOST"] = endpoint
        by_name[f"{kind}_PORT"] = endpoint
    for variable in external:
        if variable in answers or variable not in by_name:
            continue
        endpoint = by_name[variable]
        if variable.endswith("_HOST"):
            value = str(endpoint["service"])
        elif variable.endswith("_PORT"):
            value = str(endpoint["container_port"])
        elif variable.endswith("BOOTSTRAP_SERVERS"):
            value = f"{endpoint['service']}:{endpoint['container_port']}"
        else:
            value = str(endpoint["internal_address"])
        answers[variable] = value
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
        f"@{_published_host()}:{port}/{quote(database, safe='')}"
    )


def _published_host() -> str:
    """Host through which this process reaches ports published by Docker.

    A host CLI reaches published ports on loopback. A sandbox talking to a
    remote daemon reaches the same ports through that daemon's gateway.
    Deployment owns the address; provisioning only applies it consistently to
    HTTP services and attached stores.
    """
    return os.environ.get("ALK_DOCKER_PUBLISHED_HOST", "").strip() or "127.0.0.1"


def attached_postgres_store(destination: str | Path):
    """The standard world-store interface over the repository's running Postgres."""
    from .world.stores.postgres import AttachedPostgresStore

    return AttachedPostgresStore(postgres_dsn(destination))


def _configuration_name(value: str) -> str:
    """Extract an environment variable name from a contract's prose or config key."""
    candidates = _configuration_names(value)
    return candidates[0] if candidates else ""


def _configuration_names(value: str) -> list[str]:
    """Extract every env name from fields that may contain comma/slash-separated prose."""
    raw = (value or "").strip()
    candidates = re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", raw)
    names = [candidate for candidate in candidates if "_" in candidate]
    if not names and re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", raw):
        names = [raw]
    return list(dict.fromkeys(names))


def _contract_runtime_configuration_names(contract: Any | None) -> list[str]:
    names: set[str] = set()
    for dependency in list(getattr(contract, "dependencies", None) or []):
        reached = getattr(dependency, "reached", None)
        if reached is None:
            continue
        for field_name in ("dsn_env", "config_key", "user", "password_from"):
            names.update(
                _configuration_names(str(getattr(reached, field_name, "") or ""))
            )
    forbidden = {
        "FI_API_KEY",
        "FI_SECRET_KEY",
        "HARNESS_PLATFORM_API_KEY",
        "HARNESS_PLATFORM_SECRET_KEY",
    }
    return sorted(name for name in names if name not in forbidden)


def _managed_service(
    engine: str,
    *,
    version: str,
    database: str,
    user: str,
    init_mounts: list[str],
) -> tuple[dict[str, Any], str, str]:
    """A real infrastructure service, its internal connector and default config name."""
    if engine == "clickhouse":
        return (
            {
                "image": f"clickhouse/clickhouse-server:{version or '24.8'}",
                "environment": {
                    "CLICKHOUSE_DB": database,
                    "CLICKHOUSE_USER": user,
                    "CLICKHOUSE_PASSWORD": "",
                    "CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT": "1",
                },
                "ports": [
                    "${HARNESS_CLICKHOUSE_HTTP_PORT:-58123}:8123",
                    "${HARNESS_CLICKHOUSE_NATIVE_PORT:-59000}:9000",
                ],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "wget",
                        "--spider",
                        "-q",
                        "http://127.0.0.1:8123/ping",
                    ],
                    "interval": "1s",
                    "timeout": "3s",
                    "retries": 60,
                },
                "volumes": init_mounts,
            },
            f"http://{quote(user, safe='')}@clickhouse:8123/{quote(database, safe='')}",
            "CLICKHOUSE_URL",
        )
    if engine == "postgres":
        return (
            {
                "image": f"postgres:{version or '16'}",
                "environment": {
                    "POSTGRES_DB": database,
                    "POSTGRES_USER": user,
                    "POSTGRES_HOST_AUTH_METHOD": "trust",
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
            f"postgresql://{user}@postgres:5432/{database}",
            "DATABASE_URL",
        )
    if engine == "mysql":
        return (
            {
                "image": f"mysql:{version or '8.4'}",
                "environment": {
                    "MYSQL_DATABASE": database,
                    "MYSQL_USER": user,
                    "MYSQL_PASSWORD": "harness-local",
                    "MYSQL_ROOT_PASSWORD": "harness-root-local",
                },
                "ports": ["${HARNESS_MYSQL_PORT:-53306}:3306"],
                "healthcheck": {
                    "test": [
                        "CMD-SHELL",
                        "mysqladmin ping -h 127.0.0.1 -u$$MYSQL_USER "
                        "-p$$MYSQL_PASSWORD --silent",
                    ],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 60,
                },
                "volumes": init_mounts,
            },
            f"mysql://{quote(user, safe='')}:harness-local@mysql:3306/"
            f"{quote(database, safe='')}",
            "DATABASE_URL",
        )
    if engine == "redis":
        return (
            {
                "image": f"redis:{version or '7-alpine'}",
                "ports": ["${HARNESS_REDIS_PORT:-56379}:6379"],
                "healthcheck": {
                    "test": ["CMD", "redis-cli", "ping"],
                    "interval": "1s",
                    "timeout": "3s",
                    "retries": 30,
                },
            },
            "redis://redis:6379",
            "REDIS_URL",
        )
    if engine == "mongodb":
        return (
            {
                "image": f"mongo:{version or '7'}",
                "ports": ["${HARNESS_MONGODB_PORT:-57017}:27017"],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "mongosh",
                        "--quiet",
                        "--eval",
                        "db.adminCommand('ping').ok",
                    ],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 60,
                },
            },
            f"mongodb://mongodb:27017/{quote(database, safe='')}",
            "MONGODB_URL",
        )
    if engine == "qdrant":
        return (
            {
                "image": f"qdrant/qdrant:{version or 'v1.13.6'}",
                "ports": ["${HARNESS_QDRANT_HTTP_PORT:-56333}:6333"],
            },
            "http://qdrant:6333",
            "QDRANT_URL",
        )
    if engine == "rabbitmq":
        return (
            {
                # The management plugin is not needed for AMQP workloads and materially raises
                # memory/boot pressure when several isolated jobs start together. Customers can
                # still request a management tag explicitly in their contract.
                "image": f"rabbitmq:{version or '3.13-alpine'}",
                "environment": {
                    "RABBITMQ_DEFAULT_USER": "harness",
                    "RABBITMQ_DEFAULT_PASS": "harness-local",
                    # Erlang otherwise sizes scheduler/dirty-scheduler pools from the host CPU
                    # count, not the job's practical sandbox share. Concurrent environments can
                    # then exhaust Docker's thread budget during boot even though each broker is
                    # small. Keep the harness-owned broker deterministic and resource-bounded.
                    "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS": "+S 2:2 +SDcpu 1 +SDio 1",
                },
                "ports": ["${HARNESS_RABBITMQ_PORT:-55672}:5672"],
                "healthcheck": {
                    "test": ["CMD", "rabbitmq-diagnostics", "-q", "ping"],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 90,
                },
            },
            "amqp://harness:harness-local@rabbitmq:5672/%2F",
            "AMQP_URL",
        )
    if engine == "nats":
        return (
            {
                "image": f"nats:{version or '2.10-alpine'}",
                "command": ["-js", "-m", "8222"],
                "ports": [
                    "${HARNESS_NATS_PORT:-54222}:4222",
                    "${HARNESS_NATS_MONITORING_PORT:-58222}:8222",
                ],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "wget",
                        "--spider",
                        "-q",
                        "http://127.0.0.1:8222/healthz",
                    ],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 60,
                },
            },
            "nats://nats:4222",
            "NATS_URL",
        )
    if engine == "minio":
        return (
            {
                "image": f"minio/minio:{version or 'RELEASE.2025-04-22T22-12-26Z'}",
                "command": ["server", "/data", "--console-address", ":9001"],
                "environment": {
                    "MINIO_ROOT_USER": "harness",
                    "MINIO_ROOT_PASSWORD": "harness-local-secret",
                },
                "ports": [
                    "${HARNESS_MINIO_PORT:-59010}:9000",
                    "${HARNESS_MINIO_CONSOLE_PORT:-59011}:9001",
                ],
                "healthcheck": {
                    "test": [
                        "CMD",
                        "curl",
                        "-f",
                        "http://127.0.0.1:9000/minio/health/ready",
                    ],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 60,
                },
            },
            "http://minio:9000",
            "S3_ENDPOINT_URL",
        )
    raise ProvisionError(f"managed_dependency_unsupported: {engine}")


def _managed_compose(
    source: Path,
    destination: Path,
    contract: Any,
    generated_runtime: GeneratedRuntimePlan | None = None,
) -> Path | None:
    """Write a harness-owned adapter for a supported source-declared data store.

    This is deliberately registry-like and explicit.  A recognized engine gets its real service;
    an unknown engine returns no adapter and provisioning fails clearly rather than substituting
    Postgres or an in-memory fake with different behavior.
    """
    store = getattr(contract, "data_store", None)
    kind = str(getattr(store, "kind", "") or "").lower()
    dependencies = list(getattr(contract, "dependencies", None) or [])
    supported = (
        "clickhouse",
        "postgres",
        "mysql",
        "redis",
        "mongodb",
        "qdrant",
        "rabbitmq",
        "nats",
        "minio",
    )
    primary_engine = next((one for one in supported if one in kind), "")
    requested: list[tuple[str, Any | None]] = []
    embedded_store = any(
        marker in kind.replace("-", "_").replace(" ", "_")
        for marker in (
            "in_process",
            "in_memory",
            "memory",
            "sqlite",
            "filesystem",
            "file_store",
            "local_state",
        )
    )
    unsupported_declared = bool(kind) and not primary_engine and not embedded_store
    dependency_manifest = ""
    if generated_runtime is not None:
        manifest = (
            source / generated_runtime.component / generated_runtime.dependency_file
        )
        if manifest.is_file():
            dependency_manifest = (
                manifest.read_text(encoding="utf-8", errors="replace")
                .lower()
                .replace("_", "-")
            )
    if primary_engine:
        requested.append((primary_engine, None))
    for dependency in dependencies:
        description = (
            f"{getattr(dependency, 'name', '')} {getattr(dependency, 'engine', '')} "
            f"{getattr(dependency, 'kind', '')} {getattr(dependency, 'what', '')}"
        ).lower()
        engine = next((one for one in supported if one in description), "")
        declared_engine = str(getattr(dependency, "engine", "") or "").strip()
        reached = getattr(dependency, "reached", None)
        dsn_env = str(getattr(reached, "dsn_env", "") or "").strip()
        config_key = str(getattr(reached, "config_key", "") or "").strip()
        password_from = str(getattr(reached, "password_from", "") or "").strip()
        embedded_dependency = bool(
            reached
            and (
                str(getattr(reached, "loader_module", "") or "").strip()
                or str(getattr(reached, "loader_function", "") or "").strip()
            )
            and not dsn_env
            and not str(getattr(reached, "config_key", "") or "").strip()
            and not str(getattr(reached, "password_from", "") or "").strip()
            and not str(getattr(reached, "host", "") or "").strip()
            and not getattr(reached, "port", None)
            and not str(getattr(reached, "database", "") or "").strip()
        )
        package_name = re.split(r"[<>=!~\s\[]", declared_engine, maxsplit=1)[0]
        packaged_dependency = bool(
            generated_runtime is not None
            and package_name
            and package_name.lower().replace("_", "-") in dependency_manifest
            and not dsn_env
            and not config_key
            and not password_from
            and not str(getattr(reached, "database", "") or "").strip()
            and not (
                str(getattr(reached, "host", "") or "").strip() not in {"", ":memory:"}
            )
            and not getattr(reached, "port", None)
        )
        external_provider = bool(
            reached
            and (password_from or dsn_env or config_key)
            and not str(getattr(reached, "database", "") or "").strip()
        )
        if (
            not engine
            and declared_engine
            and not external_provider
            and not embedded_dependency
            and not packaged_dependency
        ):
            unsupported_declared = True
        if engine and engine not in {name for name, _ in requested}:
            requested.append((engine, dependency))
    # A standalone Dockerfile needs no service adapter. A Dockerfile whose contract names a
    # service we cannot supply is different: do not silently omit that dependency and pretend
    # the environment is complete.
    if unsupported_declared:
        return None
    runtime = getattr(contract, "runtime", None)
    dockerfile_value = str(getattr(runtime, "dockerfile", "") or "Dockerfile")
    dockerfile = source / dockerfile_value
    if generated_runtime is None and not dockerfile.is_file():
        if not requested:
            return None
        raise ProvisionError(
            "the agent requires "
            + ", ".join(engine for engine, _ in requested)
            + " but ships neither Compose nor a Dockerfile; "
            "the harness can provision dependencies only when it can run the submitted code"
        )

    init_mounts: list[str] = []
    schema_value = str(getattr(store, "schema_from", "") or "")
    if schema_value:
        schema = (source / schema_value).resolve()
        if schema.exists() and (schema.is_dir() or schema.suffix.lower() == ".sql"):
            target = (
                "/docker-entrypoint-initdb.d/source"
                if schema.is_dir()
                else "/docker-entrypoint-initdb.d/001-source.sql"
            )
            init_mounts.append(f"{schema}:{target}:ro")

    database = str(getattr(store, "database", "") or "harness")
    user = str(getattr(store, "user", "") or "harness")
    services: dict[str, Any] = {}
    runtime_environment: dict[str, str] = {}
    depends_on: dict[str, Any] = {}
    for engine, declared_dependency in requested:
        version = str(
            getattr(declared_dependency, "version", "")
            or (getattr(store, "version", "") if engine == primary_engine else "")
            or ""
        )
        service, internal_dsn, default_variable = _managed_service(
            engine,
            version=version,
            database=database,
            user=user,
            init_mounts=init_mounts if engine == primary_engine else [],
        )
        variable = ""
        if engine == primary_engine:
            variable = _configuration_name(
                str(getattr(store, "config_key", "") or "")
                or str(getattr(store, "configured_by", "") or "")
            )
        if declared_dependency is not None:
            reached = getattr(declared_dependency, "reached", None)
            variable = variable or _configuration_name(
                str(getattr(reached, "dsn_env", "") or "")
                or str(getattr(reached, "config_key", "") or "")
            )
        runtime_environment[variable or default_variable] = internal_dsn
        if engine == "minio":
            # These are harness-owned, run-local credentials for the isolated MinIO service,
            # not customer credentials. They never enter HarnessJob or SecretRef payloads.
            runtime_environment.update(
                {
                    "AWS_ACCESS_KEY_ID": "harness",
                    "AWS_SECRET_ACCESS_KEY": "harness-local-secret",
                    "AWS_DEFAULT_REGION": "us-east-1",
                }
            )
        services[engine] = service
        depends_on[engine] = {"condition": "service_healthy"}
    platform_value = str(getattr(runtime, "platform", "") or "")
    if not platform_value:
        declared_platforms: set[str] = set()
        for name in COMPOSE_FILES:
            compose = source / name
            if not compose.is_file():
                continue
            declared_platforms.update(
                match.group(1)
                for match in re.finditer(
                    r"(?m)^\s*platform\s*:\s*['\"]?([^'\"\s#]+)",
                    compose.read_text(encoding="utf-8", errors="replace"),
                )
            )
        if len(declared_platforms) == 1:
            platform_value = declared_platforms.pop()
    build_context = str(source)
    if generated_runtime is not None:
        build_context = generated_runtime.context_directory
        dockerfile_value = GENERATED_DOCKERFILE
    runtime_service: dict[str, Any] = {
        "build": {"context": build_context, "dockerfile": dockerfile_value},
        "profiles": ["harness-runtime"],
        "environment": runtime_environment,
        "depends_on": depends_on,
    }
    if generated_runtime is not None:
        runtime_service["command"] = list(generated_runtime.command)
    if platform_value:
        runtime_service["platform"] = platform_value
    services["agent-runtime"] = runtime_service
    document = {"services": services}
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
    packaging = inspect_packaging(source_root)
    generated_runtime: GeneratedRuntimePlan | None = None
    explicit_dockerfile = str(
        getattr(getattr(contract, "runtime", None), "dockerfile", "") or ""
    )
    if explicit_dockerfile:
        selected_dockerfile = (source_root / explicit_dockerfile).resolve()
        try:
            selected_dockerfile.relative_to(source_root)
        except ValueError as exc:
            raise ProvisionError(
                "runtime Dockerfile escapes the submitted repository"
            ) from exc
        if not selected_dockerfile.is_file():
            raise ProvisionError(
                f"runtime Dockerfile does not exist: {explicit_dockerfile}"
            )
        explicit_candidate = next(
            (
                item
                for item in packaging.candidates
                if item.kind is PackagingKind.DOCKERFILE
                and item.path == Path(explicit_dockerfile).as_posix()
            ),
            None,
        )
        blocking = [
            finding.message
            for finding in (explicit_candidate.findings if explicit_candidate else [])
            if finding.blocking
        ]
        if blocking:
            raise ProvisionError("packaging preflight failed: " + "; ".join(blocking))
        compose = None
    elif packaging.ready and packaging.selected_kind is PackagingKind.COMPOSE:
        assert packaging.selected_path is not None
        compose = source_root / packaging.selected_path
    elif packaging.ready and packaging.selected_kind is PackagingKind.DOCKERFILE:
        compose = None
    elif packaging.candidates:
        details = list(packaging.notes)
        details.extend(
            finding.message
            for candidate in packaging.candidates
            for finding in candidate.findings
            if finding.blocking
        )
        raise ProvisionError("packaging preflight failed: " + "; ".join(details))
    else:
        compose = None
    managed = False
    if compose is None and contract is not None:
        if not packaging.candidates and not (source_root / "Dockerfile").is_file():
            try:
                generated_runtime = prepare_generated_runtime(
                    source_root,
                    destination,
                    getattr(contract, "runtime", None),
                )
            except GeneratedRuntimeError as exc:
                dependencies = list(getattr(contract, "dependencies", None) or [])
                required = [
                    str(getattr(item, "engine", "") or getattr(item, "name", ""))
                    for item in dependencies
                ]
                prefix = (
                    "the agent requires "
                    + ", ".join(item for item in required if item)
                    + " but ships neither Compose nor a Dockerfile; "
                    if required
                    else ""
                )
                raise ProvisionError(prefix + str(exc)) from exc
        compose = _managed_compose(
            source_root, destination, contract, generated_runtime=generated_runtime
        )
        managed = compose is not None
    if compose is None:
        raise ProvisionError(
            f"{source_root} does not ship a Compose file; a non-Compose runtime adapter is required"
        )
    fingerprint = source_fingerprint(source_root)
    runtime_fingerprint = generated_runtime.fingerprint if generated_runtime else ""
    runtime_configuration_names = (
        _contract_runtime_configuration_names(contract) if generated_runtime else []
    )
    existing = ProvisionedEnvironment.load(destination)
    if (
        existing
        and Path(existing.source) == source_root
        and existing.source_fingerprint == fingerprint
        and existing.runtime_fingerprint == runtime_fingerprint
        and existing.running
    ):
        # Verify rather than trusting a stale file left by a killed process.
        if not existing.services or _run(
            existing, "ps", "--status", "running", "--quiet", check=False, timeout=30
        ):
            config = _config(existing)
            _validate_compose_security(config)
            existing.services = _started_services(config)
            existing.runtime_services = _runtime_services(config)
            existing.service_endpoints = _service_endpoints(existing, config)
            _wait_for_endpoints(existing.service_endpoints)
            existing.overrides = _overrides(existing, config)
            existing.internal_overrides = _internal_overrides(existing, config)
            existing.runtime_configuration_names = runtime_configuration_names
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
        generated_runtime_plan=(
            str(destination / "generated-runtime.json") if generated_runtime else ""
        ),
        runtime_fingerprint=runtime_fingerprint,
        runtime_configuration_names=runtime_configuration_names,
    )
    config = _config(environment)
    _validate_compose_security(config)
    _write_port_override(destination, environment, config)
    if environment.compose_override_file:
        config = _config(environment)
        _validate_compose_security(config)
    environment.services = _started_services(config)
    environment.runtime_services = _runtime_services(config)
    if not environment.services and len(environment.runtime_services) != 1:
        raise ProvisionError(
            "the Compose file has neither default infrastructure services nor exactly one "
            "opt-in agent runtime"
        )
    try:
        started = time.monotonic()
        # Build the submitted runtime during provisioning so packaging failures surface before a
        # simulation is accepted. A Dockerfile-only agent may legitimately have no infrastructure
        # containers to start; its environment is the isolated, validated, built runtime itself.
        if environment.runtime_services:
            _run(environment, "build", *environment.runtime_services)
        if environment.services:
            _start_managed_services(environment, environment.services)
        environment.service_endpoints = _service_endpoints(environment, config)
        _wait_for_endpoints(environment.service_endpoints)
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
        runtime = getattr(contract, "runtime", None)
        dockerfile_value = str(getattr(runtime, "dockerfile", "") or "Dockerfile")
        has_runtime = (Path(source).expanduser().resolve() / dockerfile_value).is_file()
        generated = can_generate_runtime(source, runtime)
        if store is None and not dependencies and not has_runtime and not generated:
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
    if environment.runtime_container:
        stop_runtime(destination)
        environment = ProvisionedEnvironment.load(destination) or environment
    _run(
        environment,
        "down",
        "--volumes",
        "--remove-orphans",
        check=False,
        timeout=120,
    )
    started = time.monotonic()
    if environment.services:
        _start_managed_services(environment, environment.services, build=False)
    config = _config(environment)
    _validate_compose_security(config)
    environment.services = _started_services(config)
    environment.runtime_services = _runtime_services(config)
    environment.service_endpoints = _service_endpoints(environment, config)
    _wait_for_endpoints(environment.service_endpoints)
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
    if environment is None or not environment.running:
        return False
    if not environment.services:
        if not environment.runtime_services:
            return False
        if not environment.runtime_container:
            return True
        return bool(
            _docker(
                "inspect",
                "--format",
                "{{.State.Running}}",
                environment.runtime_container,
                check=False,
                timeout=30,
            ).strip()
            == "true"
        )
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
    if not set(environment.services).issubset(found):
        return False
    try:
        # Provision/reset require a stability window; this is a point-in-time liveness probe.
        _wait_for_endpoints(environment.service_endpoints, 1.0, 0.0)
    except ProvisionError:
        return False
    return True


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
    config = _config(environment)
    service_config = config["services"][service]
    service_environment = _environment_values(service_config)
    endpoint_overrides = environment.internal_overrides
    if environment.managed:
        # Managed Compose already contains the complete internal DSN, including its ephemeral
        # database credentials. The generic non-secret endpoint view must not replace it.
        endpoint_overrides = {
            name: value
            for name, value in endpoint_overrides.items()
            if name not in service_environment
        }
    injected = {
        **endpoint_overrides,
        "HARNESS_MODE": "1",
        # The SDK recorder is also a remote LiveKit participant. Agents that
        # wait for an arbitrary participant can otherwise bind their audio
        # input to the silent recorder and stall after one turn. This stable
        # prefix identifies the actual simulated caller across scenarios.
        "HARNESS_CALLER_IDENTITY_PREFIX": "fagi-simulator",
        **(overrides or {}),
    }
    for name in environment.runtime_configuration_names:
        value = os.environ.get(name, "").strip()
        if value:
            if name == "LIVEKIT_URL":
                parsed = urlsplit(value)
                if (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}:
                    port = f":{parsed.port}" if parsed.port else ""
                    value = urlunsplit(
                        (
                            parsed.scheme,
                            f"host.docker.internal{port}",
                            parsed.path,
                            parsed.query,
                            parsed.fragment,
                        )
                    )
            injected.setdefault(name, value)
    arguments = [
        "run",
        "--detach",
        "--no-deps",
        "--name",
        container,
    ]
    trace_volume = ""
    trace_destination = ""
    if trace_path is not None:
        trace = Path(trace_path).expanduser().resolve()
        trace.parent.mkdir(parents=True, exist_ok=True)
        # The submitted runtime commonly runs as a non-root UID that is unrelated to the
        # sandbox runner's UID. Mounting a root-owned result directory and asking that worker to
        # create the trace file makes otherwise-successful tools fail with PermissionError in
        # agents that trace synchronously. Pre-create only the job-owned file and make that file
        # writable across the container boundary; the surrounding artifact tree stays private.
        trace.touch(exist_ok=True)
        trace.chmod(0o666)
        # Mount only this scenario's result folder. Agents that support the generic harness trace
        # seam write semantic/model-facing tool events here; agents that do not simply ignore the
        # variable and the backend proxy remains the fallback evidence source.
        trace_arguments, trace_volume = _runtime_trace_mount(trace)
        arguments.extend(trace_arguments)
        trace_destination = str(trace)
        container_trace = f"/run/harness-trace/{trace.name}"
        # HARNESS_AGENT_TOOL_TRACE is the runtime-level contract. Keep the shorter historical
        # name as a compatibility alias for submitted agents that already adopted it; both point
        # at the same mounted file, so evidence is still collected exactly once.
        injected["HARNESS_AGENT_TOOL_TRACE"] = container_trace
        injected["HARNESS_TOOL_TRACE"] = container_trace
    # Credential paths in a repository env file name host files. Mount them into a stable,
    # read-only container location and replace only the path value; never copy or persist the
    # credential contents in harness artifacts.
    for name, source, target in _runtime_credential_mounts(
        service_environment, service_config
    ):
        arguments.extend(("--volume", f"{source}:{target}:ro"))
        injected[name] = target
    google_path = injected.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if google_path:
        google_source = Path(google_path).expanduser()
        if not _valid_google_credentials(google_source):
            raise ProvisionError(
                "GOOGLE_APPLICATION_CREDENTIALS for the generated runtime is not a valid "
                "readable JSON credential file"
            )
        google_target = f"/run/harness-secrets/{google_source.name}"
        arguments.extend(("--volume", f"{google_source.resolve()}:{google_target}:ro"))
        injected["GOOGLE_APPLICATION_CREDENTIALS"] = google_target
    for name in sorted(injected):
        arguments.extend(("--env", name))
    arguments.append(service)
    _run(environment, *arguments, timeout=900, process_overrides=injected)
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
    environment.runtime_trace_volume = trace_volume
    environment.runtime_trace_path = trace_destination
    environment.save(destination)
    return environment


def _runtime_trace_mount(trace: Path) -> tuple[list[str], str]:
    """Give a sibling runtime one writable trace target without exposing other jobs.

    Docker resolves bind sources on the daemon host, not inside the runner container. A path
    such as ``/var/lib/alk-sandbox`` can therefore name a Docker volume in the runner while
    accidentally naming an unrelated host directory for ``docker run --volume``. Resolve bind
    mounts to their host source; for named-volume runners, use a one-call exchange volume that is
    copied back on cleanup so a submitted runtime cannot inspect artifacts from another job.
    """
    configured = os.environ.get("ALK_RUNNER_CONTAINER", "").strip()
    if not configured:
        return ["--volume", f"{trace.parent}:/run/harness-trace"], ""
    runner = socket.gethostname() if configured == "self" else configured
    mounts = json.loads(
        _docker(
            "inspect",
            "--format",
            "{{json .Mounts}}",
            runner,
            timeout=30,
        )
        or "[]"
    )
    for mount in mounts:
        destination = Path(str(mount.get("Destination") or ""))
        if not destination.is_absolute():
            continue
        try:
            relative = trace.parent.relative_to(destination)
        except ValueError:
            continue
        kind = str(mount.get("Type") or "")
        if kind == "volume" and mount.get("Name"):
            # ``docker compose run`` does not accept ``--mount`` and ``--volume`` cannot select
            # a subdirectory of another volume. Use a one-call exchange volume instead: the
            # runtime writes there, then stop_runtime copies the one file back into the runner's
            # job volume before deleting the exchange volume.
            exchange = f"alk-trace-{uuid.uuid4().hex[:12]}"
            _docker("volume", "create", exchange, timeout=30)
            _docker(
                "run",
                "--rm",
                "--volume",
                f"{exchange}:/trace",
                "--entrypoint",
                "touch",
                "docker:27-cli",
                f"/trace/{trace.name}",
                timeout=30,
            )
            _docker(
                "run",
                "--rm",
                "--volume",
                f"{exchange}:/trace",
                "--entrypoint",
                "chmod",
                "docker:27-cli",
                "0666",
                f"/trace/{trace.name}",
                timeout=30,
            )
            return ["--volume", f"{exchange}:/run/harness-trace"], exchange
        if kind == "bind" and mount.get("Source"):
            source = Path(str(mount["Source"])) / relative
            return ["--volume", f"{source}:/run/harness-trace"], ""
    # Local Docker execution has no outer runner mount to translate.
    return ["--volume", f"{trace.parent}:/run/harness-trace"], ""


def connect_runner_network(
    destination: str | Path, *, alias: str = "alk-harness-runner"
) -> str:
    """Join this runner to the submitted environment's private network.

    A per-call webhook lives in the runner process. The submitted agent runs in
    the repository's Compose network, so a hosted/containerized runner must
    join that network explicitly; host loopback and host-published ports are
    neither private nor reliably reachable from sibling containers.
    """
    destination = Path(destination)
    environment = ProvisionedEnvironment.load(destination)
    if environment is None or not environment.running:
        raise ProvisionError(f"no running environment recorded at {destination}")
    configured = os.environ.get("ALK_RUNNER_CONTAINER", "").strip()
    if not configured:
        return ""
    runner = socket.gethostname() if configured == "self" else configured
    service_container = ""
    if environment.services:
        service_container = _run(
            environment,
            "ps",
            "--quiet",
            environment.services[0],
            timeout=30,
        ).strip()
    elif environment.runtime_container:
        service_container = environment.runtime_container
    else:
        # A runtime-only Compose project creates its network when ``compose run`` starts the
        # worker. Return the stable alias now; the caller invokes this again immediately after
        # startup to make that alias resolvable from the submitted container.
        return alias
    if not service_container:
        raise ProvisionError("could not identify a container in the source environment")
    networks = json.loads(
        _docker(
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            service_container,
            timeout=30,
        )
        or "{}"
    )
    if len(networks) != 1:
        raise ProvisionError(
            "expected the source environment on one private network, found "
            + (", ".join(networks) or "none")
        )
    network = next(iter(networks))
    runner_networks = json.loads(
        _docker(
            "inspect",
            "--format",
            "{{json .NetworkSettings.Networks}}",
            runner,
            timeout=30,
        )
        or "{}"
    )
    if network not in runner_networks:
        _docker("network", "connect", "--alias", alias, network, runner, timeout=30)
    environment.runner_network = network
    environment.save(destination)
    return alias


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
    if environment.runtime_trace_volume and environment.runtime_trace_path:
        trace = Path(environment.runtime_trace_path)
        trace.parent.mkdir(parents=True, exist_ok=True)
        _docker(
            "cp",
            f"{environment.runtime_container}:/run/harness-trace/{trace.name}",
            str(trace),
            check=False,
        )
    _docker("rm", "--force", environment.runtime_container, check=False)
    if environment.runtime_trace_volume:
        _docker(
            "volume", "rm", environment.runtime_trace_volume, check=False, timeout=30
        )
    environment.runtime_container = ""
    environment.runtime_trace_volume = ""
    environment.runtime_trace_path = ""
    configured = os.environ.get("ALK_RUNNER_CONTAINER", "").strip()
    if configured and environment.runner_network:
        runner = socket.gethostname() if configured == "self" else configured
        _docker(
            "network",
            "disconnect",
            environment.runner_network,
            runner,
            check=False,
        )
        environment.runner_network = ""
    environment.save(destination)
    return True


def stop(destination: str | Path) -> bool:
    """Tear down the exact project recorded for a session, including its test data."""
    destination = Path(destination)
    environment = ProvisionedEnvironment.load(destination)
    if environment is None:
        return False
    if environment.runtime_container:
        stop_runtime(destination)
        environment = ProvisionedEnvironment.load(destination) or environment
    _run(environment, "down", "--volumes", "--remove-orphans", check=False, timeout=120)
    environment.running = False
    environment.save(destination)
    return True
