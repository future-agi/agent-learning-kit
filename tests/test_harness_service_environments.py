from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from fi.alk.harness.contract import (
    AgentContract,
    DataStore,
    Dependency,
    Runtime,
    ToolSpec,
)
from fi.alk.harness import provision as provisioning
from fi.alk.harness.bundle import export_session_bundle
from fi.alk.harness.environment_plan import load_environment_plan
from fi.alk.harness.provision import (
    ProvisionError,
    ProvisionedEnvironment,
    _build_runtime,
    _internal_overrides,
    _endpoint_ready,
    _managed_compose,
    _runner_service_address,
    _start_managed_services,
    _overrides,
    _validate_compose_security,
    _write_port_override,
    healthy,
    provision,
    reachable_overrides,
    reset,
    runtime_logs,
    start_runtime,
    stop,
    stop_runtime,
)
from fi.alk.harness.service_catalog import profile_for
from fi.alk.harness.world.provisioned import ProvisionedWorld


def test_hosted_runner_uses_unique_container_dns_instead_of_shared_service_alias(
    monkeypatch,
):
    environment = ProvisionedEnvironment(
        source="/source",
        compose_file="/source/compose.yml",
        project="fagi-harness-run-123",
    )
    monkeypatch.setattr(
        "fi.alk.harness.provision._run",
        lambda *_args, **_kwargs: "container-id\n",
    )
    monkeypatch.setattr(
        "fi.alk.harness.provision._docker",
        lambda *_args, **_kwargs: "/fagi-harness-run-123-postgres-1\n",
    )

    address = _runner_service_address(
        environment,
        "postgresql://user:pass@postgres:5432/database",
        "postgres",
    )

    assert address == (
        "postgresql://user:pass@fagi-harness-run-123-postgres-1:5432/database"
    )


def test_stopping_worker_keeps_runner_network_for_post_call_grading(
    monkeypatch, tmp_path
):
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.yml"),
        project="fagi-harness-grading",
        runtime_container="runtime-id",
        runner_network="fagi-harness-grading_default",
        running=True,
    )
    environment.save(tmp_path)
    docker_calls = []
    monkeypatch.setattr(
        "fi.alk.harness.provision._docker",
        lambda *args, **_kwargs: docker_calls.append(args) or "",
    )
    monkeypatch.setattr(
        "fi.alk.harness.provision._detach_runner_network",
        lambda _environment: pytest.fail(
            "worker shutdown detached the runner before post-call grading"
        ),
    )

    assert stop_runtime(tmp_path)
    saved = ProvisionedEnvironment.load(tmp_path)
    assert saved is not None
    assert saved.runtime_container == ""
    assert saved.runner_network == "fagi-harness-grading_default"
    assert docker_calls == [("rm", "--force", "runtime-id")]


def test_runtime_logs_reads_only_the_active_bounded_runtime(monkeypatch, tmp_path):
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.yml"),
        project="fagi-harness-evidence",
        runtime_container="fagi-harness-evidence-runtime",
        running=True,
    )
    environment.save(tmp_path)
    calls = []
    monkeypatch.setattr(
        "fi.alk.harness.provision._docker",
        lambda *args, **kwargs: calls.append((args, kwargs)) or "structured logs",
    )

    assert runtime_logs(tmp_path, tail=50_000) == "structured logs"
    assert calls == [
        (
            ("logs", "--tail", "20000", "fagi-harness-evidence-runtime"),
            {"check": False, "timeout": 30},
        )
    ]


def test_runtime_build_retries_transient_registry_transport_failure(monkeypatch):
    environment = ProvisionedEnvironment(
        source="/source",
        compose_file="/source/compose.yml",
        project="fagi-harness-build-retry",
        runtime_services=["agent"],
    )
    calls = []

    def run(_environment, *arguments, **_kwargs):
        calls.append(arguments)
        if len(calls) == 1:
            raise ProvisionError(
                "failed to fetch anonymous token from ghcr.io: unexpected EOF"
            )
        return ""

    monkeypatch.setattr("fi.alk.harness.provision._run", run)
    monkeypatch.setattr("fi.alk.harness.provision.time.sleep", lambda _delay: None)

    _build_runtime(environment)

    assert calls == [("build", "agent"), ("build", "agent")]


def test_runtime_build_does_not_retry_submitted_dockerfile_failure(monkeypatch):
    environment = ProvisionedEnvironment(
        source="/source",
        compose_file="/source/compose.yml",
        project="fagi-harness-build-failure",
        runtime_services=["agent"],
    )
    calls = []

    def run(_environment, *arguments, **_kwargs):
        calls.append(arguments)
        raise ProvisionError("RUN uv sync failed: lockfile is inconsistent")

    monkeypatch.setattr("fi.alk.harness.provision._run", run)

    with pytest.raises(ProvisionError, match="lockfile is inconsistent"):
        _build_runtime(environment)

    assert calls == [("build", "agent")]


def test_hosted_world_uses_private_http_and_postgres_endpoints(monkeypatch, tmp_path):
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.yml"),
        project="fagi-harness-private-world",
        overrides={
            "DATABASE_URL": "postgresql://user:pass@host.docker.internal:41001/db",
            "TOOLS_API_URL": "http://host.docker.internal:41002",
        },
        internal_overrides={
            "DATABASE_URL": "postgresql://user:pass@postgres:5432/db",
            "TOOLS_API_URL": "http://tools-api:8080",
        },
        runner_network="fagi-harness-private-world_default",
        running=True,
    )
    environment.save(tmp_path)
    monkeypatch.setenv("ALK_RUNNER_CONTAINER", "harness-sandbox-runner")

    class Store:
        def start(self):
            return None

    seen = {}

    def store(destination):
        seen["overrides"] = reachable_overrides(
            ProvisionedEnvironment.load(destination)
        )
        return Store()

    monkeypatch.setattr(
        "fi.alk.harness.world.provisioned.attached_postgres_store", store
    )
    monkeypatch.setattr(ProvisionedWorld, "_discover_endpoints", lambda self: set())

    world = ProvisionedWorld(
        AgentContract(agent="ride", tools=[], real_use_cases=["book a ride"]),
        tmp_path,
        source_root=str(tmp_path),
    )

    assert world.base_url == "http://tools-api:8080"
    assert seen["overrides"]["DATABASE_URL"].endswith("@postgres:5432/db")


def test_provisioned_world_accepts_local_runtime_tool_telemetry(monkeypatch, tmp_path):
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.yml"),
        project="fagi-harness-local-tool",
        overrides={"TOOLS_API_URL": "http://127.0.0.1:41002"},
        running=True,
    )
    environment.save(tmp_path)

    class Store:
        def start(self):
            return None

    monkeypatch.setattr(
        "fi.alk.harness.world.provisioned.attached_postgres_store",
        lambda _destination: Store(),
    )
    monkeypatch.setattr(ProvisionedWorld, "_discover_endpoints", lambda self: set())
    world = ProvisionedWorld(
        AgentContract(
            agent="ride",
            tools=[{"name": "confirm_address", "args": ["place_id"]}],
            tool_entrypoints=[
                {
                    "tool": "confirm_address",
                    "mode": "construct",
                    "module": "agent",
                    "callable": "RideAgent.confirm_address",
                }
            ],
            real_use_cases=["book a ride"],
        ),
        tmp_path,
        source_root=str(tmp_path),
    )

    call = world.call("confirm_address", {"place_id": "place-1"})

    assert call.ok
    assert call.result == {
        "observed": True,
        "execution": "submitted_agent_runtime",
    }
    assert world.calls == [call]


def test_world_rejects_only_genuinely_distinct_http_endpoints(monkeypatch, tmp_path):
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.yml"),
        project="fagi-harness-ambiguous-world",
        overrides={
            "PRIMARY_API_URL": "http://127.0.0.1:41001",
            "PRIMARY_API_ALIAS": "http://127.0.0.1:41001/",
            "DATABASE_URL": "postgresql://user:pass@127.0.0.1:41002/db",
        },
        running=True,
    )
    environment.save(tmp_path)

    class Store:
        def start(self):
            return None

    monkeypatch.setattr(
        "fi.alk.harness.world.provisioned.attached_postgres_store",
        lambda _destination: Store(),
    )
    monkeypatch.setattr(ProvisionedWorld, "_discover_endpoints", lambda self: set())
    world = ProvisionedWorld(
        AgentContract(agent="agent", tools=[], real_use_cases=["help"]),
        tmp_path,
        source_root=str(tmp_path),
    )
    assert world.base_url == "http://127.0.0.1:41001"

    environment.overrides["SECONDARY_API_URL"] = "http://127.0.0.1:41003"
    environment.save(tmp_path)
    with pytest.raises(ProvisionError, match="PRIMARY_API_ALIAS.*SECONDARY_API_URL"):
        ProvisionedWorld(
            AgentContract(agent="agent", tools=[], real_use_cases=["help"]),
            tmp_path,
            source_root=str(tmp_path),
        )


def test_harness_owned_service_boot_gets_one_clean_retry(monkeypatch, tmp_path):
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.json"),
        project="fagi-harness-retry",
        managed=True,
    )
    calls = []

    def run(_environment, *arguments, **kwargs):
        calls.append((arguments, kwargs))
        if arguments[0] == "up" and sum(item[0][0] == "up" for item in calls) == 1:
            raise ProvisionError("container rabbitmq exited (1)")
        return ""

    monkeypatch.setattr("fi.alk.harness.provision._run", run)

    _start_managed_services(environment, ["rabbitmq", "nats"])

    assert [item[0][0] for item in calls] == ["up", "down", "up", "up"]
    assert calls[2][0][-1] == "rabbitmq"
    assert calls[3][0][-1] == "nats"
    assert calls[1][1]["check"] is False


def test_submitted_compose_boot_is_not_automatically_retried(monkeypatch, tmp_path):
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(tmp_path / "compose.yml"),
        project="customer-compose",
        managed=False,
    )
    calls = []

    def run(_environment, *arguments, **kwargs):
        calls.append((arguments, kwargs))
        raise ProvisionError("container customer-agent exited (1)")

    monkeypatch.setattr("fi.alk.harness.provision._run", run)

    with pytest.raises(ProvisionError, match="customer-agent"):
        _start_managed_services(environment, ["customer-agent"])

    assert [item[0][0] for item in calls] == ["up"]


def test_catalog_recognizes_voice_agent_dependency_families():
    expected = {
        ("clickhouse/clickhouse-server:24.8", 8123): ("clickhouse", "clickhouse"),
        ("redis:7", 6379): ("redis", "redis"),
        ("mysql:8.4", 3306): ("mysql", "mysql"),
        ("mongo:7", 27017): ("mongodb", "mongodb"),
        ("minio/minio", 9000): ("minio", "s3"),
        ("rabbitmq:3.13-management-alpine", 5672): ("rabbitmq", "amqp"),
        ("nats:2.10-alpine", 4222): ("nats", "nats"),
        ("private/code-executor", 8000): ("code-executor", "http"),
        ("qdrant/qdrant", 6333): ("qdrant", "http"),
        ("private/calculator", 9999): ("service", "tcp"),
    }
    for (image, port), result in expected.items():
        profile = profile_for("dependency", image, port)
        assert (profile.kind, profile.protocol) == result


def test_queue_readiness_requires_protocol_greeting(monkeypatch):
    class FakeSocket:
        def __init__(self, response):
            self.response = response
            self.sent = b""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendall(self, value):
            self.sent = value

        def recv(self, _size):
            return self.response

    amqp = FakeSocket(b"\x01\x00\x00\x00")
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: amqp)
    assert _endpoint_ready({"host_port": 5672, "protocol": "amqp"}, "127.0.0.1")
    assert amqp.sent == b"AMQP\x00\x00\x09\x01"

    nats = FakeSocket(b'INFO {"server_id":"one"}\r\n')
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: nats)
    assert _endpoint_ready({"host_port": 4222, "protocol": "nats"}, "127.0.0.1")

    silent = FakeSocket(b"")
    monkeypatch.setattr(socket, "create_connection", lambda *_args, **_kwargs: silent)
    assert not _endpoint_ready({"host_port": 5672, "protocol": "amqp"}, "127.0.0.1")


def test_clickhouse_connectors_are_injected_from_python_js_and_dotenv(
    tmp_path, monkeypatch
):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  analytics:\n    image: clickhouse/clickhouse-server:24.8\n"
    )
    (tmp_path / "agent.py").write_text(
        'url = os.getenv("CLICKHOUSE_URL", "http://localhost:8123/voice")\n'
        'host = os.getenv("CLICKHOUSE_HOST")\n'
    )
    (tmp_path / "worker.ts").write_text("const port = process.env.CLICKHOUSE_PORT\n")
    (tmp_path / ".env.example").write_text("CLICKHOUSE_HTTP_URL=\n")
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(compose),
        project="capability-test",
        services=["analytics"],
        service_endpoints=[
            {
                "service": "analytics",
                "kind": "clickhouse",
                "protocol": "clickhouse",
                "container_port": 8123,
                "host_port": 43123,
                "configured_host_port": 8123,
                "external_address": "http://127.0.0.1:43123",
                "internal_address": "http://analytics:8123",
                "configuration_names": ["CLICKHOUSE_URL", "CLICKHOUSE_HTTP_URL"],
                "readiness_path": "/ping",
            }
        ],
    )
    config = {"services": {"analytics": {}}}

    assert _overrides(environment, config) == {
        "CLICKHOUSE_HOST": "127.0.0.1",
        "CLICKHOUSE_HTTP_URL": "http://127.0.0.1:43123",
        "CLICKHOUSE_PORT": "43123",
        "CLICKHOUSE_URL": "http://127.0.0.1:43123/voice",
    }
    assert _internal_overrides(environment, config) == {
        "CLICKHOUSE_HOST": "analytics",
        "CLICKHOUSE_HTTP_URL": "http://analytics:8123",
        "CLICKHOUSE_PORT": "8123",
        "CLICKHOUSE_URL": "http://analytics:8123/voice",
    }


def test_multi_endpoint_dsn_does_not_abort_override_discovery(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text("services:\n  api:\n    image: example/api\n")
    (tmp_path / ".env.example").write_text(
        "REDIS_URI=redis://127.0.0.1:7001,redis://127.0.0.1:7002\n"
    )
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(compose),
        project="cluster-dsn",
        services=["api"],
        service_endpoints=[],
    )

    assert _overrides(environment, {"services": {"api": {}}}) == {}
    assert _internal_overrides(environment, {"services": {"api": {}}}) == {}


def test_unknown_customer_service_uses_its_existing_javascript_configuration_seam(
    tmp_path,
):
    compose = tmp_path / "compose.yml"
    compose.write_text('services:\n  calculation-runtime:\n    ports: ["9100:9100"]\n')
    (tmp_path / "tool.ts").write_text(
        'const endpoint = process.env.CALCULATION_RUNTIME_URL ?? "http://localhost:9100/v1"\n'
    )
    environment = ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(compose),
        project="custom-service",
        services=["calculation-runtime"],
        service_endpoints=[
            {
                "service": "calculation-runtime",
                "kind": "service",
                "protocol": "tcp",
                "container_port": 9100,
                "host_port": 49100,
                "configured_host_port": 9100,
                "external_address": "tcp://127.0.0.1:49100",
                "internal_address": "tcp://calculation-runtime:9100",
                "configuration_names": [],
                "readiness_path": "",
            }
        ],
    )

    assert _overrides(environment, {"services": {"calculation-runtime": {}}}) == {
        "CALCULATION_RUNTIME_URL": "http://127.0.0.1:49100/v1"
    }
    assert _internal_overrides(
        environment, {"services": {"calculation-runtime": {}}}
    ) == {"CALCULATION_RUNTIME_URL": "http://calculation-runtime:9100/v1"}


def test_static_ports_receive_a_job_scoped_compose_override(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yml"
    compose.write_text('services:\n  redis:\n    ports: ["6379:6379"]\n')
    environment = ProvisionedEnvironment(
        source=str(tmp_path), compose_file=str(compose), project="isolated"
    )
    _write_port_override(
        tmp_path / "session",
        environment,
        {"services": {"redis": {"ports": [{"target": 6379, "published": "6379"}]}}},
    )

    generated = Path(environment.compose_override_file).read_text()
    assert "ports: !override" in generated
    assert 'published: "0"' in generated
    assert 'host_ip: "127.0.0.1"' in generated


def test_a_fixed_port_on_all_interfaces_is_replaced_beside_a_variable_one(tmp_path):
    # b publishes 0.0.0.0:2375 on the same container port that a's ${A_PORT:-8080} takes: every
    # port goes through the override, on the kit's bind host, with Docker choosing the host port.
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  a:\n    ports: ['${A_PORT:-8080}:80']\n"
        "  b:\n    ports: ['0.0.0.0:2375:80']\n"
    )
    environment = ProvisionedEnvironment(
        source=str(tmp_path), compose_file=str(compose), project="isolated"
    )
    _write_port_override(
        tmp_path / "session",
        environment,
        {
            "services": {
                "a": {"ports": [{"target": 80, "published": "0"}]},
                "b": {"ports": [{"target": 80, "published": "2375", "host_ip": "0.0.0.0"}]},
            }
        },
    )
    generated = Path(environment.compose_override_file).read_text()
    assert '"a"' in generated and '"b"' in generated
    assert "2375" not in generated
    assert generated.count('published: "0"') == 2
    assert '"0.0.0.0"' not in generated


def test_fixed_container_name_is_reset_for_project_isolation(tmp_path):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  mongodb:\n    image: mongo:8\n    container_name: chat-mongodb\n"
    )
    environment = ProvisionedEnvironment(
        source=str(tmp_path), compose_file=str(compose), project="isolated"
    )

    _write_port_override(
        tmp_path / "session",
        environment,
        {"services": {"mongodb": {"container_name": "chat-mongodb"}}},
    )

    generated = Path(environment.compose_override_file).read_text()
    assert "container_name: !reset null" in generated


def test_profile_gated_service_ports_are_not_allocated(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        """services:
  api:
    image: example/api
    ports: ["8000:8000"]
  coturn:
    image: coturn/coturn
    profiles: [remote]
    ports: ["49152-49200:49152-49200/udp"]
"""
    )
    environment = ProvisionedEnvironment(
        source=str(tmp_path), compose_file=str(compose), project="isolated"
    )
    _write_port_override(
        tmp_path / "session",
        environment,
        {
            "services": {
                "api": {"ports": [{"target": 8000, "published": "8000"}]},
                "coturn": {
                    "profiles": ["remote"],
                    "ports": [
                        {"target": port, "published": str(port), "protocol": "udp"}
                        for port in range(49152, 49201)
                    ],
                },
            }
        },
    )

    generated = Path(environment.compose_override_file).read_text()
    assert '"api"' in generated
    assert 'published: "0"' in generated
    assert "coturn" not in generated
    assert "49152" not in generated


def test_empty_compose_profile_remains_active_by_default(tmp_path, monkeypatch):
    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  postgres:\n    image: postgres:16\n    profiles: ['', postgresql]\n"
        "    ports: ['5432:5432']\n"
    )
    environment = ProvisionedEnvironment(
        source=str(tmp_path), compose_file=str(compose), project="isolated"
    )
    config = {
        "services": {
            "postgres": {
                "profiles": ["", "postgresql"],
                "ports": [{"target": 5432, "published": "5432"}],
            }
        }
    }

    _write_port_override(tmp_path / "session", environment, config)

    assert 'published: "0"' in Path(environment.compose_override_file).read_text()


def test_authenticated_redis_response_counts_as_protocol_ready(monkeypatch):
    class RedisSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def sendall(self, _payload):
            return None

        def recv(self, _size):
            return b"-NOAUTH Authentication required.\r\n"

    monkeypatch.setattr(
        socket, "create_connection", lambda *_args, **_kwargs: RedisSocket()
    )

    from fi.alk.harness.provision import _endpoint_ready

    assert _endpoint_ready({"host_port": 6379, "protocol": "redis"}, "127.0.0.1")


@pytest.mark.parametrize(
    "service",
    [
        {"privileged": True},
        {"network_mode": "host"},
        {"devices": ["/dev/kvm:/dev/kvm"]},
        {"volumes": [{"source": "/var/run/docker.sock", "target": "/run/docker.sock"}]},
    ],
)
def test_host_escape_compose_features_are_rejected(service):
    with pytest.raises(ProvisionError, match="forbidden host access"):
        _validate_compose_security({"services": {"agent": service}})


_DOCKER_SOCKET_AND_PARENTS = [
    "/",
    "/run",
    "/var",
    "/var/run",
    "/var/run/docker.sock",
    "/run/docker.sock",
    "/var/run/",
    "/var/run/.",
    "/var/./run",
    "/var//run/docker.sock",
    "/run/./docker.sock/",
    "/var/run/../run",
    "//",
]


@pytest.mark.parametrize("source", _DOCKER_SOCKET_AND_PARENTS)
@pytest.mark.parametrize("form", ["long", "short"])
def test_a_bind_of_the_docker_socket_or_a_parent_is_rejected(source, form):
    volume = (
        {"type": "bind", "source": source, "target": "/h"}
        if form == "long"
        else f"{source}:/h:ro"
    )
    with pytest.raises(ProvisionError, match="agent: Docker socket mount"):
        _validate_compose_security({"services": {"agent": {"volumes": [volume]}}})


@pytest.mark.parametrize(
    "volume",
    [
        {"type": "bind", "source": "/var/run/other", "target": "/h"},
        {"type": "bind", "source": "/srv/data", "target": "/h"},
        {"type": "bind", "source": "./data", "target": "/h"},
        {"type": "volume", "source": "data", "target": "/h"},
        {"type": "bind", "source": "/run/userdata", "target": "/h"},
        "/srv/data:/h",
        "./data:/h",
        "data:/h",
    ],
)
def test_a_bind_elsewhere_is_not_a_docker_socket_mount(volume):
    _validate_compose_security({"services": {"agent": {"volumes": [volume]}}})


@pytest.mark.parametrize(
    "field",
    [
        "cap_add",
        "use_api_socket",
        "secrets",
        "configs",
        "sysctls",
        "runtime",
        "isolation",
        "cgroup_parent",
        "device_cgroup_rules",
        "oom_kill_disable",
        "gpus",
        "external_links",
        "some_future_field",
    ],
)
def test_a_service_field_not_on_the_allowlist_is_rejected(field):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(_svc(**{field: ["CANARY-7f3a"]}))
    assert f"agent: field {field} is not allowed" in str(error.value)
    assert "CANARY" not in str(error.value)
    assert "db: field" not in str(error.value)


def test_a_privileged_service_is_reported_once():
    with pytest.raises(ProvisionError) as error:
        _validate_compose_security(_svc(privileged=True, devices=["/dev/kvm"]))
    assert "agent: privileged" in str(error.value)
    assert "agent: host devices" in str(error.value)
    assert "field privileged" not in str(error.value)
    assert "field devices" not in str(error.value)


def test_service_fields_on_the_allowlist_are_accepted():
    _validate_compose_security(
        _svc(
            image="x",
            build={"context": "."},
            command=["run"],
            entrypoint=["run"],
            environment={"A": "b"},
            env_file=["a.env"],
            working_dir="/app",
            user="1000",
            depends_on={"db": {"condition": "service_started"}},
            healthcheck={"test": ["CMD", "true"]},
            volumes=[{"type": "volume", "source": "data", "target": "/d"}],
            ports=[{"target": 80}],
            expose=["80"],
            restart="no",
            profiles=["harness-runtime"],
            networks={"default": None},
            labels={"a": "b"},
            deploy={"resources": {"limits": {"cpus": "1"}}},
            cap_drop=["ALL"],
            tmpfs=["/tmp"],
            mem_limit="1g",
            develop={"watch": [{"path": "./src", "action": "sync", "target": "/app"}]},
            # The fields real jobs use, written out one by one (not looped over the allowlist),
            # so dropping any of them from the allowlist turns this red.
            container_name="job-agent",
            cpus=1.5,
            extra_hosts=["a:1.2.3.4"],
            hostname="agent",
            init=True,
            pids_limit=200,
            pull_policy="missing",
            read_only=True,
            shm_size="1g",
            stdin_open=True,
            stop_grace_period="10s",
            stop_signal="SIGINT",
            tty=True,
            ulimits={"nofile": {"soft": 1024, "hard": 2048}},
            # Resource limits only; Compose renders them and nothing here reaches the host.
            blkio_config={"weight": 300},
            cpu_count=2,
            cpu_percent=50,
            cpu_rt_runtime="400ms",
            cpu_rt_period="1400us",
            **{"x-anything": {"a": 1}},
        )
    )


def _svc(**fields):
    return {"services": {"agent": fields, "db": {}}}


def _net(spec, name="job"):
    return {"name": name, "services": {"agent": {}}, "networks": {"back": spec}}


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        # network_mode
        (
            _svc(network_mode="container:other"),
            "agent: network_mode outside the project",
        ),
        (_svc(network_mode="container:db"), "agent: network_mode outside the project"),
        (
            _svc(network_mode="platform_default"),
            "agent: network_mode outside the project",
        ),
        (_svc(network_mode="bridge"), "agent: network_mode outside the project"),
        (_svc(network_mode="host"), "agent: network_mode=host"),
        (
            _svc(network_mode="service:missing"),
            "agent: network_mode outside the project",
        ),
        (_svc(network_mode="NONE"), "agent: network_mode outside the project"),
        (_svc(network_mode="NoNe"), "agent: network_mode outside the project"),
        # pid and ipc
        (_svc(pid="host"), "agent: pid=host"),
        (_svc(pid="container:db"), "agent: pid outside the project"),
        (_svc(pid="service:missing"), "agent: pid outside the project"),
        (_svc(pid="private"), "agent: pid outside the project"),
        (_svc(ipc="host"), "agent: ipc=host"),
        (_svc(ipc="container:db"), "agent: ipc outside the project"),
        (_svc(ipc="service:missing"), "agent: ipc outside the project"),
        # uts, userns_mode and cgroup
        (_svc(uts="host"), "agent: uts outside the project"),
        (_svc(uts="HOST"), "agent: uts outside the project"),
        (_svc(userns_mode="host"), "agent: userns_mode outside the project"),
        (_svc(userns_mode="HOST"), "agent: userns_mode outside the project"),
        (_svc(cgroup="host"), "agent: cgroup outside the project"),
        (_svc(cgroup="HOST"), "agent: cgroup outside the project"),
        # volumes_from
        (
            _svc(volumes_from=["container:db"]),
            "agent: volumes_from outside the project",
        ),
        (
            _svc(volumes_from=["container:db:ro"]),
            "agent: volumes_from outside the project",
        ),
        (_svc(volumes_from=["missing"]), "agent: volumes_from outside the project"),
        (
            _svc(volumes_from=["db", "missing:ro"]),
            "agent: volumes_from outside the project",
        ),
        (_svc(volumes_from=["service:db"]), "agent: volumes_from outside the project"),
        # build
        (_svc(build={"context": ".", "network": "host"}), "agent: build.network"),
        (
            _svc(build={"context": ".", "network": "platform_default"}),
            "agent: build.network",
        ),
        (_svc(build={"context": ".", "privileged": True}), "agent: build.privileged"),
        (
            _svc(build={"context": ".", "entitlements": ["network.host"]}),
            "agent: build.entitlements",
        ),
        # top-level networks
        (_net({"name": "shared", "external": True}), "network back: external"),
        (
            _net({"name": "otherproject_back"}),
            "network back: named outside the project",
        ),
        (_net({"name": "job_default"}), "network back: named outside the project"),
        (_net({"name": "job_back", "driver": "macvlan"}), "network back: driver"),
        (_net({"name": "job_back", "driver": "host"}), "network back: driver"),
        (
            _net({"name": "job_back", "driver_opts": {"parent": "eth0"}}),
            "network back: driver_opts",
        ),
    ],
)
def test_compose_reach_outside_the_project_is_rejected(config, expected):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(config)
    assert expected in str(error.value)


@pytest.mark.parametrize(
    "config",
    [
        # network_mode
        _svc(network_mode="service:db"),
        _svc(network_mode="none"),
        _svc(network_mode=""),
        _svc(),
        # pid and ipc
        _svc(pid="service:db"),
        _svc(pid=""),
        _svc(ipc="service:db"),
        _svc(ipc="none"),
        _svc(ipc="private"),
        _svc(ipc="shareable"),
        _svc(ipc=""),
        # uts, userns_mode and cgroup
        _svc(uts="private"),
        _svc(uts=""),
        _svc(userns_mode="private"),
        _svc(userns_mode=""),
        _svc(cgroup="private"),
        _svc(cgroup=""),
        # volumes_from
        _svc(volumes_from=["db"]),
        _svc(volumes_from=["db:ro"]),
        _svc(volumes_from=["db:rw"]),
        # build
        _svc(build={"context": "."}),
        _svc(build={"context": ".", "network": "default"}),
        _svc(build={"context": ".", "network": "none"}),
        _svc(build={"context": ".", "privileged": False, "entitlements": []}),
        # top-level networks
        _net({"name": "job_back"}),
        _net({"name": "job_back", "driver": "bridge", "driver_opts": {}}),
        _net({"driver": "bridge", "internal": True}),
        {"name": "job", "services": {"agent": {}}, "networks": {"default": None}},
        {"name": "job", "services": {"agent": {}}},
    ],
)
def test_compose_settings_inside_the_project_are_accepted(config):
    _validate_compose_security(config)


@pytest.mark.parametrize(
    "config",
    [
        _svc(network_mode="CANARY-7f3a"),
        _svc(pid="CANARY-7f3a"),
        _svc(ipc="CANARY-7f3a"),
        _svc(uts="CANARY-7f3a"),
        _svc(userns_mode="CANARY-7f3a"),
        _svc(cgroup="CANARY-7f3a"),
        _svc(security_opt=["CANARY-7f3a"]),
        _svc(volumes_from=["CANARY-7f3a"]),
        _svc(build={"network": "CANARY-7f3a"}),
        _net({"name": "CANARY-7f3a"}),
        _net({"name": "job_back", "driver": "CANARY-7f3a"}),
        _net({"name": "job_back", "driver_opts": {"parent": "CANARY-7f3a"}}),
    ],
)
def test_compose_refusal_text_never_contains_a_value(config):
    with pytest.raises(ProvisionError) as error:
        _validate_compose_security(config)
    assert "CANARY" not in str(error.value)
    assert "7f3a" not in str(error.value)


def test_network_refusal_ends_with_the_hint_and_other_refusals_do_not():
    with pytest.raises(ProvisionError) as network:
        _validate_compose_security(_svc(network_mode="container:db"))
    assert str(network.value).endswith(
        "; declare networks in this file without external or name, and use network_mode "
        "only as none or service:<a service in this file>"
    )
    with pytest.raises(ProvisionError) as other:
        _validate_compose_security(_svc(privileged=True))
    assert "declare networks" not in str(other.value)


def test_clickhouse_is_generated_when_contract_declares_it_without_compose(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    contract = AgentContract(
        agent="analytics-voice-agent",
        tools=[ToolSpec(name="chart", args=["metric"])],
        real_use_cases=["answer a spoken analytics question"],
        dependencies=[
            Dependency(
                name="analytics",
                kind="datastore",
                engine="clickhouse",
            )
        ],
        data_store=DataStore(
            kind="clickhouse",
            configured_by="CLICKHOUSE_URL environment variable",
            database="voice_analytics",
            user="voice_agent",
        ),
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)
    assert target is not None
    document = json.loads(target.read_text())
    clickhouse = document["services"]["clickhouse"]
    assert clickhouse["image"].startswith("clickhouse/clickhouse-server:")
    assert "8123" in clickhouse["ports"][0]
    assert document["services"]["agent-runtime"]["environment"][
        "CLICKHOUSE_URL"
    ].startswith("http://voice_agent@")


def test_missing_custom_tool_service_is_never_generated(tmp_path):
    """Infrastructure can be supplied; absent customer behavior cannot be invented."""
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    contract = AgentContract(
        agent="voice-agent",
        tools=[ToolSpec(name="calculate_quote", args=["route"])],
        real_use_cases=["quote a route"],
        dependencies=[
            Dependency(
                name="proprietary-pricing-api",
                kind="service",
                engine="customer-pricing-engine",
                used_by=["calculate_quote"],
            )
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    assert _managed_compose(source, tmp_path / "session", contract) is None


def test_external_model_provider_is_not_mistaken_for_managed_infrastructure(
    tmp_path,
):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    contract = AgentContract(
        agent="voice-agent",
        tools=[ToolSpec(name="answer", args=["question"])],
        real_use_cases=["answer a caller"],
        dependencies=[
            Dependency(
                name="Google Vertex AI",
                kind="service",
                engine="Google Gemini 2.5 Flash",
                what="hosted model inference",
            )
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)

    assert target is not None
    document = json.loads(target.read_text())
    assert set(document["services"]) == {"agent-runtime"}


def test_embedded_memory_sqlite_is_not_mistaken_for_external_infrastructure(
    tmp_path,
):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    contract = AgentContract(
        agent="hotel-voice-agent",
        tools=[ToolSpec(name="book_room", args=["date"])],
        real_use_cases=["book a hotel room"],
        data_store=DataStore(kind="sqlite", database=":memory:"),
        dependencies=[
            Dependency(
                name="hotel_sqlite",
                kind="datastore",
                engine="sqlite (apsw in-process)",
                reached={
                    "host": "in-memory :memory:",
                    "loader_module": "fake_data.seed",
                    "loader_function": "populate",
                },
            )
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)

    assert target is not None
    document = json.loads(target.read_text())
    assert set(document["services"]) == {"agent-runtime"}


def test_dockerfile_owns_local_model_downloaded_at_build(tmp_path):
    """A submitted image owns its local model; ALK must not demand a Compose service."""
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    contract = AgentContract(
        agent="hotel-receptionist",
        modality="voice",
        tools=[ToolSpec(name="book_room", args=["room_type", "date"])],
        real_use_cases=["book a hotel room"],
        data_store=DataStore(kind="sqlite", database=":memory:"),
        dependencies=[
            Dependency(
                name="hotel_sqlite",
                kind="datastore",
                engine="SQLite",
                reached={"host": "in-process"},
            ),
            Dependency(
                name="LiveKit",
                kind="service",
                engine="LiveKit Cloud",
                what="hosted realtime transport",
            ),
            Dependency(
                name="Deepgram",
                kind="service",
                engine="Deepgram Nova",
                what="hosted speech transcription",
            ),
            Dependency(
                name="Gemini",
                kind="service",
                engine="Google Gemini",
                what="hosted model inference",
            ),
            Dependency(
                name="Silero VAD",
                kind="service",
                engine="Silero VAD ONNX local model",
                reached={
                    "host": (
                        "local model file downloaded at build via "
                        "livekit.agents download-files"
                    )
                },
            ),
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)

    assert target is not None
    document = json.loads(target.read_text())
    assert set(document["services"]) == {"agent-runtime"}


def test_local_runtime_marker_cannot_hide_an_unknown_network_service(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    contract = AgentContract(
        agent="voice-agent",
        tools=[ToolSpec(name="calculate_quote", args=["route"])],
        real_use_cases=["quote a route"],
        dependencies=[
            Dependency(
                name="customer-pricing-engine",
                kind="service",
                engine="customer-pricing-engine",
                reached={"host": "local model service", "port": 9911},
            )
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    assert _managed_compose(source, tmp_path / "session", contract) is None


def test_inline_schema_description_is_not_treated_as_a_source_path(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-slim\n")
    contract = AgentContract(
        agent="hotel-voice-agent",
        tools=[ToolSpec(name="book_room", args=["date"])],
        real_use_cases=["book a hotel room"],
        data_store=DataStore(
            kind="sqlite",
            database=":memory:",
            schema_from=(
                "hotel_db.py SCHEMA constant (CREATE TABLE IF NOT EXISTS DDL string). "
                "Tables: " + ", ".join(f"hotel_table_{index}" for index in range(80))
            ),
        ),
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)

    assert target is not None
    document = json.loads(target.read_text())
    assert set(document["services"]) == {"agent-runtime"}
    assert "volumes" not in document["services"]["agent-runtime"]


def test_declared_managed_dependency_without_dockerfile_fails_actionably(tmp_path):
    source = tmp_path / "unpackaged-agent"
    source.mkdir()
    contract = AgentContract(
        agent="unpackaged-voice-agent",
        tools=[ToolSpec(name="remember", args=["value"])],
        real_use_cases=["remember session context"],
        dependencies=[Dependency(name="cache", engine="redis", kind="cache")],
    )

    with pytest.raises(
        ProvisionError,
        match="requires redis but ships neither Compose nor a Dockerfile",
    ):
        provision(source, tmp_path / "session", contract)


def test_failed_compose_start_removes_only_the_failed_project(tmp_path, monkeypatch):
    source = tmp_path / "broken-agent"
    source.mkdir()
    (source / "compose.yml").write_text("services:\n  dependency:\n    image: broken\n")
    calls: list[tuple[str, ...]] = []

    def run(_environment, *arguments, **_kwargs):
        calls.append(arguments)
        if "config" in arguments:
            return json.dumps({"services": {"dependency": {"image": "broken"}}})
        if arguments and arguments[0] == "up":
            raise ProvisionError("image failed to start")
        return ""

    monkeypatch.setattr("fi.alk.harness.provision._run", run)
    with pytest.raises(ProvisionError, match="image failed to start"):
        provision(source, tmp_path / "session")
    assert calls[-1] == ("down", "--volumes", "--remove-orphans")


def test_dockerfile_only_agent_gets_every_declared_supported_dependency(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-alpine\n")
    contract = AgentContract(
        agent="voice-analytics",
        tools=[ToolSpec(name="answer_metric", args=["metric"])],
        real_use_cases=["answer analytics questions"],
        data_store=DataStore(
            kind="clickhouse",
            configured_by="CLICKHOUSE_URL",
            database="voice",
        ),
        dependencies=[
            Dependency(name="analytics", engine="clickhouse", kind="datastore"),
            Dependency(
                name="session-cache",
                engine="redis",
                kind="cache",
                reached={"dsn_env": "REDIS_URL"},
            ),
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)
    document = json.loads(target.read_text())
    assert set(document["services"]) == {"clickhouse", "redis", "agent-runtime"}
    assert document["services"]["agent-runtime"]["environment"] == {
        "CLICKHOUSE_URL": "http://harness@clickhouse:8123/voice",
        "REDIS_URL": "redis://redis:6379",
    }
    assert set(document["services"]["agent-runtime"]["depends_on"]) == {
        "clickhouse",
        "redis",
    }

    ProvisionedEnvironment(
        source=str(source),
        compose_file=str(target),
        project="bundle-test",
        services=["clickhouse", "redis"],
        managed=True,
    ).save(tmp_path / "session")
    _, bundle = export_session_bundle(
        source, tmp_path / "session", name="dockerfile-only-environment"
    )
    assert bundle.runtime.document == "compose.json"


def test_mongodb_and_qdrant_are_generated_from_declared_dependencies(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-alpine\n")
    contract = AgentContract(
        agent="voice-rag-agent",
        tools=[ToolSpec(name="answer_from_memory", args=["question"])],
        real_use_cases=["answer a caller using document and vector memory"],
        dependencies=[
            Dependency(
                name="conversation-memory",
                engine="mongodb",
                kind="datastore",
                reached={"dsn_env": "MONGODB_URL"},
            ),
            Dependency(
                name="knowledge-index",
                engine="qdrant",
                kind="vector_store",
                reached={"dsn_env": "QDRANT_URL"},
            ),
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)
    document = json.loads(target.read_text())

    assert set(document["services"]) == {"mongodb", "qdrant", "agent-runtime"}
    assert document["services"]["agent-runtime"]["environment"] == {
        "MONGODB_URL": "mongodb://mongodb:27017/harness",
        "QDRANT_URL": "http://qdrant:6333",
    }


def test_queue_and_object_services_are_generated_from_declared_dependencies(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-alpine\n")
    contract = AgentContract(
        agent="voice-workflow-agent",
        tools=[ToolSpec(name="process_call", args=["call_id"])],
        real_use_cases=["process a call using queued work and stored artifacts"],
        dependencies=[
            Dependency(
                name="durable-jobs",
                engine="rabbitmq",
                kind="queue",
                reached={"dsn_env": "AMQP_URL"},
            ),
            Dependency(
                name="live-events",
                engine="nats",
                kind="event_bus",
                reached={"dsn_env": "NATS_URL"},
            ),
            Dependency(
                name="call-artifacts",
                engine="minio",
                kind="object_store",
                reached={"dsn_env": "S3_ENDPOINT_URL"},
            ),
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)
    document = json.loads(target.read_text())

    assert set(document["services"]) == {
        "rabbitmq",
        "nats",
        "minio",
        "agent-runtime",
    }
    assert document["services"]["rabbitmq"]["image"] == "rabbitmq:3.13-alpine"
    assert (
        document["services"]["rabbitmq"]["environment"]["RABBITMQ_ERLANG_COOKIE"]
        == "harness-local-cookie"
    )
    assert (
        "chown rabbitmq:rabbitmq" in document["services"]["rabbitmq"]["entrypoint"][2]
    )
    assert document["services"]["rabbitmq"]["command"] == ["rabbitmq-server"]
    assert (
        document["services"]["rabbitmq"]["environment"][
            "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS"
        ]
        == "+S 2:2 +SDcpu 1 +SDio 1"
    )
    assert document["services"]["agent-runtime"]["environment"] == {
        "AMQP_URL": "amqp://harness:harness-local@rabbitmq:5672/%2F",
        "NATS_URL": "nats://nats:4222",
        "S3_ENDPOINT_URL": "http://minio:9000",
        "AWS_ACCESS_KEY_ID": "harness",
        "AWS_SECRET_ACCESS_KEY": "harness-local-secret",
        "AWS_DEFAULT_REGION": "us-east-1",
    }


def test_mysql_service_is_generated_from_declared_store(tmp_path):
    source = tmp_path / "agent"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM python:3.12-alpine\n")
    contract = AgentContract(
        agent="voice-crm-agent",
        tools=[ToolSpec(name="find_customer", args=["phone"])],
        real_use_cases=["retrieve a caller from a MySQL-backed CRM"],
        data_store=DataStore(
            kind="mysql",
            configured_by="DATABASE_URL",
            database="voice",
            user="harness",
        ),
        dependencies=[Dependency(name="crm", engine="mysql", kind="datastore")],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)
    document = json.loads(target.read_text())

    assert set(document["services"]) == {"mysql", "agent-runtime"}
    assert document["services"]["agent-runtime"]["environment"] == {
        "DATABASE_URL": "mysql://harness:harness-local@mysql:3306/voice"
    }


def _fixture_agent() -> Path:
    return (
        Path(__file__).parent / "fixtures" / "harness_agents" / "voice_analytics_agent"
    )


def _runtime_logs(environment) -> str:
    from fi.alk.harness.provision import _docker

    return _docker("logs", environment.runtime_container, timeout=30)


def _wait_runtime_log(environment, expected: str, timeout: float = 20) -> str:
    deadline = time.monotonic() + timeout
    logs = ""
    while time.monotonic() < deadline:
        logs = _runtime_logs(environment)
        if expected in logs:
            return logs
        time.sleep(0.2)
    return logs


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_case_one_compose_agent_runs_unchanged_with_isolated_dependencies(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    first = provision(_fixture_agent(), tmp_path / "first")
    second = provision(_fixture_agent(), tmp_path / "second")
    try:
        assert first.project != second.project
        assert {
            (endpoint["kind"], endpoint["host_port"])
            for endpoint in first.service_endpoints
        }.isdisjoint(
            {
                (endpoint["kind"], endpoint["host_port"])
                for endpoint in second.service_endpoints
            }
        )
        first = start_runtime(tmp_path / "first")
        second = start_runtime(tmp_path / "second")
        expected = "AGENT_READY clickhouse=Bengaluru redis=+PONG"
        assert expected in _wait_runtime_log(first, expected)
        assert expected in _wait_runtime_log(second, expected)
        reset(tmp_path / "first")
        assert healthy(tmp_path / "first")
        assert healthy(tmp_path / "second")
    finally:
        stop_runtime(tmp_path / "first")
        stop_runtime(tmp_path / "second")
        stop(tmp_path / "first")
        stop(tmp_path / "second")


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_case_two_dockerfile_agent_gets_managed_clickhouse_and_redis(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = tmp_path / "dockerfile-agent"
    shutil.copytree(_fixture_agent(), source)
    (source / "compose.yml").unlink()
    contract = AgentContract(
        agent="dockerfile-only-voice-analytics",
        tools=[ToolSpec(name="answer_metric", args=["metric"])],
        real_use_cases=["answer a spoken analytics question"],
        data_store=DataStore(
            kind="clickhouse",
            configured_by="CLICKHOUSE_URL",
            database="voice",
            user="harness",
            schema_from="init.sql",
        ),
        dependencies=[
            Dependency(name="analytics", engine="clickhouse", kind="datastore"),
            Dependency(
                name="session-cache",
                engine="redis",
                kind="cache",
                reached={"dsn_env": "REDIS_URL"},
            ),
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )
    session = tmp_path / "managed-session"
    environment = provision(source, session, contract)
    try:
        assert environment.managed
        assert set(environment.services) == {"clickhouse", "redis"}
        assert environment.internal_overrides["CLICKHOUSE_URL"] == (
            "http://harness@clickhouse:8123/voice"
        )
        assert "CLICKHOUSE_URL" in environment.runtime_configuration_names
        environment = start_runtime(session)
        expected = "AGENT_READY clickhouse=Bengaluru redis=+PONG"
        assert expected in _wait_runtime_log(environment, expected)
        assert source.joinpath("compose.yml").exists() is False
        assert session.joinpath("managed-compose.json").is_file()
    finally:
        stop_runtime(session)
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_case_two_dockerfile_agent_gets_managed_postgres(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = (
        Path(__file__).parent / "fixtures" / "harness_agents" / "voice_ledger_agent"
    )
    contract = AgentContract(
        agent="dockerfile-only-voice-ledger",
        tools=[ToolSpec(name="lookup_call", args=["call_id"])],
        real_use_cases=["look up a call ledger entry"],
        data_store=DataStore(
            kind="postgres",
            configured_by="DATABASE_URL",
            database="voice",
            user="harness",
            schema_from="init.sql",
        ),
        dependencies=[Dependency(name="ledger", engine="postgres", kind="datastore")],
        runtime=Runtime(dockerfile="Dockerfile"),
    )
    session = tmp_path / "postgres-session"
    environment = provision(source, session, contract)
    try:
        assert environment.managed and environment.services == ["postgres"]
        environment = start_runtime(session)
        expected = "AGENT_READY postgres=Mumbai"
        assert expected in _wait_runtime_log(environment, expected)
    finally:
        stop_runtime(session)
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_case_two_dockerfile_agent_gets_clean_managed_mysql(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = tmp_path / "voice-crm-agent"
    source.mkdir()
    (source / "Dockerfile").write_text(
        """FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir pymysql==1.1.1
COPY agent.py .
CMD ["python", "agent.py"]
"""
    )
    (source / "agent.py").write_text(
        """import os
import time
from urllib.parse import urlsplit

import pymysql

dsn = urlsplit(os.environ["DATABASE_URL"])
connection = pymysql.connect(
    host=dsn.hostname,
    port=dsn.port or 3306,
    user=dsn.username,
    password=dsn.password,
    database=dsn.path.lstrip("/"),
    autocommit=True,
)
with connection.cursor() as cursor:
    cursor.execute("CREATE TABLE IF NOT EXISTS callers (id INT PRIMARY KEY, name VARCHAR(80))")
    cursor.execute("SELECT COUNT(*) FROM callers")
    before = cursor.fetchone()[0]
    cursor.execute("INSERT INTO callers (id, name) VALUES (901, 'Meera Shah')")
    cursor.execute("SELECT name FROM callers WHERE id = 901")
    assert cursor.fetchone()[0] == "Meera Shah"
connection.close()
print(f"AGENT_READY mysql_before={before} caller=Meera Shah", flush=True)
while True:
    time.sleep(60)
"""
    )
    contract = AgentContract(
        agent="dockerfile-only-voice-crm",
        tools=[ToolSpec(name="find_customer", args=["phone"])],
        real_use_cases=["retrieve and update a caller in a MySQL-backed CRM"],
        data_store=DataStore(
            kind="mysql",
            configured_by="DATABASE_URL",
            database="voice",
            user="harness",
        ),
        dependencies=[Dependency(name="crm", engine="mysql", kind="datastore")],
        runtime=Runtime(dockerfile="Dockerfile"),
    )
    session = tmp_path / "mysql-session"
    expected = "AGENT_READY mysql_before=0 caller=Meera Shah"
    environment = provision(source, session, contract)
    try:
        assert environment.managed and environment.services == ["mysql"]
        assert environment.internal_overrides["DATABASE_URL"] == (
            "mysql://harness:harness-local@mysql:3306/voice"
        )
        assert "DATABASE_URL" in environment.runtime_configuration_names
        environment = start_runtime(session)
        assert expected in _wait_runtime_log(environment, expected, timeout=45)
        stop_runtime(session)
        reset(session)
        environment = start_runtime(session)
        assert expected in _wait_runtime_log(environment, expected, timeout=45)
    finally:
        stop_runtime(session)
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_case_two_dockerfile_agent_gets_clean_mongodb_and_qdrant(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = tmp_path / "voice-rag-agent"
    source.mkdir()
    (source / "Dockerfile").write_text(
        """FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir pymongo==4.10.1
COPY agent.py .
CMD ["python", "agent.py"]
"""
    )
    (source / "agent.py").write_text(
        """import json
import os
import time
import urllib.error
import urllib.request

from pymongo import MongoClient

mongo = MongoClient(os.environ["MONGODB_URL"], serverSelectionTimeoutMS=5000)
records = mongo.get_default_database()["calls"]
mongo_before = records.count_documents({})
records.insert_one({"caller": "Aarav", "intent": "retrieve policy"})

qdrant = os.environ["QDRANT_URL"].rstrip("/")
try:
    urllib.request.urlopen(qdrant + "/collections/calls", timeout=5)
    qdrant_before = 1
except urllib.error.HTTPError as exc:
    if exc.code != 404:
        raise
    qdrant_before = 0

def request(path, method, payload):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        qdrant + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.read()

if not qdrant_before:
    request(
        "/collections/calls",
        "PUT",
        {"vectors": {"size": 2, "distance": "Cosine"}},
    )
request(
    "/collections/calls/points?wait=true",
    "PUT",
    {"points": [{"id": 1, "vector": [0.2, 0.8], "payload": {"city": "Pune"}}]},
)
print(
    f"AGENT_READY mongo_before={mongo_before} qdrant_before={qdrant_before}",
    flush=True,
)
while True:
    time.sleep(60)
"""
    )
    contract = AgentContract(
        agent="dockerfile-only-voice-rag",
        tools=[ToolSpec(name="answer_from_memory", args=["question"])],
        real_use_cases=["answer a caller using document and vector memory"],
        dependencies=[
            Dependency(
                name="conversation-memory",
                engine="mongodb",
                kind="datastore",
                reached={"dsn_env": "MONGODB_URL"},
            ),
            Dependency(
                name="knowledge-index",
                engine="qdrant",
                kind="vector_store",
                reached={"dsn_env": "QDRANT_URL"},
            ),
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )
    session = tmp_path / "mongo-qdrant-session"
    environment = provision(source, session, contract)
    expected = "AGENT_READY mongo_before=0 qdrant_before=0"
    try:
        assert environment.managed
        assert set(environment.services) == {"mongodb", "qdrant"}
        environment = start_runtime(session)
        assert expected in _wait_runtime_log(environment, expected, timeout=30)
        stop_runtime(session)
        reset(session)
        environment = start_runtime(session)
        assert expected in _wait_runtime_log(environment, expected, timeout=30)
    finally:
        stop_runtime(session)
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_case_two_queue_and_object_environment_is_exercised_isolated_and_reset(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = tmp_path / "voice-workflow-agent"
    source.mkdir()
    (source / "Dockerfile").write_text(
        """FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir boto3==1.35.99 nats-py==2.9.0 pika==1.3.2
COPY agent.py .
CMD ["python", "agent.py"]
"""
    )
    (source / "agent.py").write_text(
        """import asyncio
import os
import time

import boto3
import nats
import pika
from botocore.exceptions import ClientError
from nats.js.errors import NotFoundError


async def exercise_nats():
    connection = await nats.connect(os.environ["NATS_URL"])
    jetstream = connection.jetstream()
    try:
        info = await jetstream.stream_info("CALLS")
        before = info.state.messages
    except NotFoundError:
        before = 0
        await jetstream.add_stream(name="CALLS", subjects=["calls.events"])
    await jetstream.publish("calls.events", b'{"call_id":"call-901"}')
    await connection.drain()
    return before


rabbit = pika.BlockingConnection(pika.URLParameters(os.environ["AMQP_URL"]))
channel = rabbit.channel()
queue = channel.queue_declare(queue="calls", durable=True)
rabbit_before = queue.method.message_count
channel.basic_publish(exchange="", routing_key="calls", body=b"call-901")
rabbit.close()

nats_before = asyncio.run(exercise_nats())

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.environ["AWS_DEFAULT_REGION"],
)
bucket = "call-artifacts"
try:
    s3.head_bucket(Bucket=bucket)
    object_before = s3.list_objects_v2(Bucket=bucket).get("KeyCount", 0)
except ClientError as error:
    if error.response["Error"]["Code"] not in {"404", "NoSuchBucket"}:
        raise
    object_before = 0
    s3.create_bucket(Bucket=bucket)
s3.put_object(Bucket=bucket, Key="call-901/transcript.txt", Body=b"hello caller")
assert s3.get_object(Bucket=bucket, Key="call-901/transcript.txt")["Body"].read() == b"hello caller"

print(
    f"AGENT_READY rabbit_before={rabbit_before} "
    f"nats_before={nats_before} object_before={object_before}",
    flush=True,
)
while True:
    time.sleep(60)
"""
    )
    contract = AgentContract(
        agent="dockerfile-only-voice-workflow",
        tools=[ToolSpec(name="process_call", args=["call_id"])],
        real_use_cases=["process a call using queued work and stored artifacts"],
        dependencies=[
            Dependency(
                name="durable-jobs",
                engine="rabbitmq",
                kind="queue",
                reached={"dsn_env": "AMQP_URL"},
            ),
            Dependency(
                name="live-events",
                engine="nats",
                kind="event_bus",
                reached={"dsn_env": "NATS_URL"},
            ),
            Dependency(
                name="call-artifacts",
                engine="minio",
                kind="object_store",
                reached={"dsn_env": "S3_ENDPOINT_URL"},
            ),
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )
    first_session = tmp_path / "queue-object-first"
    second_session = tmp_path / "queue-object-second"
    expected = "AGENT_READY rabbit_before=0 nats_before=0 object_before=0"
    first = None
    second = None
    try:
        # Enter the cleanup guard before the first external resource is created. If the second
        # environment fails admission/startup, the first one must not leak into the developer's
        # Docker daemon or the next test.
        first = provision(source, first_session, contract)
        second = provision(source, second_session, contract)
        assert set(first.services) == {"rabbitmq", "nats", "minio"}
        assert {
            (endpoint["kind"], endpoint["host_port"])
            for endpoint in first.service_endpoints
        }.isdisjoint(
            {
                (endpoint["kind"], endpoint["host_port"])
                for endpoint in second.service_endpoints
            }
        )
        first = start_runtime(first_session)
        second = start_runtime(second_session)
        assert expected in _wait_runtime_log(first, expected, timeout=60)
        assert expected in _wait_runtime_log(second, expected, timeout=60)

        stop_runtime(first_session)
        reset(first_session)
        first = start_runtime(first_session)
        assert expected in _wait_runtime_log(first, expected, timeout=60)
        assert healthy(second_session)
    finally:
        if first is not None:
            stop_runtime(first_session)
            stop(first_session)
        if second is not None:
            stop_runtime(second_session)
            stop(second_session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_compose_agent_uses_submitted_code_executor_and_resets_artifacts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HARNESS_RUNTIME_STABLE_SECONDS", "1")
    source = tmp_path / "voice-code-agent"
    source.mkdir()
    (source / "Dockerfile.executor").write_text(
        """FROM python:3.12-slim
WORKDIR /app
COPY executor.py .
CMD ["python", "executor.py"]
"""
    )
    (source / "Dockerfile.agent").write_text(
        """FROM python:3.12-slim
WORKDIR /app
COPY agent.py .
CMD ["python", "agent.py"]
"""
    )
    (source / "executor.py").write_text(
        """import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health":
            self.send_error(404)
            return
        self.send_response(204)
        self.end_headers()

    def do_POST(self):
        if self.path != "/execute":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        result = subprocess.run(
            [sys.executable, "-I", "-c", payload["code"]],
            cwd="/artifacts",
            env={"PATH": os.environ.get("PATH", "")},
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        body = json.dumps(
            {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


HTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
"""
    )
    (source / "agent.py").write_text(
        """import json
import os
import time
import urllib.request
from pathlib import Path

artifact = Path("/artifacts/call-volume.svg")
artifact_before = int(artifact.exists())
code = '''from pathlib import Path
values = [17, 29, 41, 53]
mean = sum(values) / len(values)
bars = "".join(f'<rect x="{10 + i * 25}" y="{70 - value}" width="15" height="{value}"/>' for i, value in enumerate(values))
Path("call-volume.svg").write_text(f'<svg xmlns="http://www.w3.org/2000/svg" width="120" height="80">{bars}</svg>')
print(mean)
'''
request = urllib.request.Request(
    os.environ["CODE_EXECUTOR_URL"].rstrip("/") + "/execute",
    data=json.dumps({"code": code}).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=10) as response:
    result = json.load(response)
assert result["returncode"] == 0, result
assert result["stdout"] == "35.0"
deadline = time.monotonic() + 5
while not artifact.exists() and time.monotonic() < deadline:
    time.sleep(0.05)
assert artifact.read_text().startswith("<svg")
print(f"AGENT_READY artifact_before={artifact_before} mean={result['stdout']} svg=1", flush=True)
while True:
    time.sleep(60)
"""
    )
    (source / "compose.yml").write_text(
        """services:
  code-executor:
    build:
      context: .
      dockerfile: Dockerfile.executor
    ports: ["8000:8000"]
    read_only: true
    cap_drop: ["ALL"]
    pids_limit: 64
    mem_limit: 256m
    cpus: 0.5
    tmpfs: ["/tmp"]
    volumes: ["artifacts:/artifacts"]
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"]
      interval: 1s
      timeout: 3s
      retries: 30
  agent-runtime:
    profiles: ["harness-runtime"]
    build:
      context: .
      dockerfile: Dockerfile.agent
    environment:
      CODE_EXECUTOR_URL: http://code-executor:8000
    depends_on:
      code-executor:
        condition: service_healthy
    volumes: ["artifacts:/artifacts:ro"]
volumes:
  artifacts: {}
"""
    )
    session = tmp_path / "code-session"
    expected = "AGENT_READY artifact_before=0 mean=35.0 svg=1"
    environment = provision(source, session)
    try:
        assert environment.services == ["code-executor"]
        assert environment.runtime_services == ["agent-runtime"]
        environment = start_runtime(session)
        assert expected in _wait_runtime_log(environment, expected, timeout=30)
        stop_runtime(session)
        reset(session)
        environment = start_runtime(session)
        assert expected in _wait_runtime_log(environment, expected, timeout=30)
    finally:
        stop_runtime(session)
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_real_clickhouse_and_redis_lifecycle_is_isolated_and_resettable(tmp_path):
    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    (source / "init.sql").write_text(
        "CREATE DATABASE IF NOT EXISTS voice;\n"
        "CREATE TABLE IF NOT EXISTS voice.calls (id UInt32, city String) ENGINE=MergeTree ORDER BY id;\n"
        "INSERT INTO voice.calls VALUES (1, 'Bengaluru');\n"
    )
    (source / "agent.py").write_text(
        'CLICKHOUSE_URL = os.getenv("CLICKHOUSE_URL", "http://localhost:8123")\n'
        'REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")\n'
    )
    (source / "compose.yml").write_text(
        """services:
  clickhouse:
    image: clickhouse/clickhouse-server:24.8-alpine
    environment:
      CLICKHOUSE_USER: default
      CLICKHOUSE_PASSWORD: ""
      CLICKHOUSE_DEFAULT_ACCESS_MANAGEMENT: "1"
    ports: ["8123:8123"]
    volumes:
      - ./init.sql:/docker-entrypoint-initdb.d/001-init.sql:ro
    healthcheck:
      test: ["CMD", "wget", "--spider", "-q", "http://127.0.0.1:8123/ping"]
      interval: 1s
      timeout: 3s
      retries: 60
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 1s
      timeout: 3s
      retries: 60
"""
    )
    environment = provision(source, session)
    try:
        assert {endpoint["kind"] for endpoint in environment.service_endpoints} == {
            "clickhouse",
            "redis",
        }
        clickhouse_url = environment.overrides["CLICKHOUSE_URL"]
        query = urllib.parse.urlencode({"query": "SELECT city FROM voice.calls"})
        direct_http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        assert (
            direct_http.open(f"{clickhouse_url}/?{query}", timeout=5).read()
            == b"Bengaluru\n"
        )

        redis_url = urllib.parse.urlsplit(environment.overrides["REDIS_URL"])
        with socket.create_connection(
            (redis_url.hostname, redis_url.port), timeout=5
        ) as client:
            client.sendall(b"*3\r\n$3\r\nSET\r\n$5\r\nstate\r\n$5\r\ndirty\r\n")
            assert client.recv(64).startswith(b"+OK")

        environment = reset(session)
        redis_url = urllib.parse.urlsplit(environment.overrides["REDIS_URL"])
        with socket.create_connection(
            (redis_url.hostname, redis_url.port), timeout=5
        ) as client:
            client.sendall(b"*2\r\n$3\r\nGET\r\n$5\r\nstate\r\n")
            assert client.recv(64) == b"$-1\r\n"
    finally:
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_sealed_bundle_restarts_the_admitted_source_not_the_mutated_checkout(tmp_path):
    """The saved-rerun boundary is proved with Docker, not only command mocks."""
    from types import SimpleNamespace

    from fi.alk.harness import cli

    source = tmp_path / "agent"
    session = tmp_path / "session"
    source.mkdir()
    compose = source / "compose.yml"
    compose.write_text(
        """services:
  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 1s
      timeout: 3s
      retries: 60
""",
        encoding="utf-8",
    )
    first = provision(source, session)
    bundle_root, bundle = export_session_bundle(source, session, name="redis-agent")
    plan = load_environment_plan(bundle_root, bundle=bundle)
    stop(session)
    # If rerun consults this mutable checkout, startup must fail. The admitted bundle remains
    # valid and contains the original redis:7-alpine Compose document.
    compose.write_text(
        "services: {redis: {image: definitely-invalid.invalid/mutated:latest}}\n",
        encoding="utf-8",
    )
    try:
        result = asyncio.run(
            cli._environment(
                SimpleNamespace(
                    action="up",
                    path=str(source),
                    bundle=str(bundle_root),
                    out=str(session),
                )
            )
        )
        restarted = ProvisionedEnvironment.load(session)
        assert result == 0
        assert restarted is not None
        assert restarted.running
        assert Path(restarted.source) == bundle_root / "services/source"
        assert plan.source.source_digest == first.source_fingerprint
        assert healthy(session)
        reset(session)
        assert healthy(session)
    finally:
        stop(session)


@pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requires Docker")
def test_repository_chat_runtime_runs_through_the_same_environment_lifecycle(tmp_path):
    """A default Compose API is isolated, started per case and driven as the real target."""
    import asyncio

    from fi.alk.harness.run.targets import RepositoryChatTarget
    from fi.alk.harness.world.runtime import GeneratedWorld

    source = tmp_path / "chat-agent"
    session = tmp_path / "session"
    source.mkdir()
    (source / "agent.py").write_text(
        """import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        self.send_response(200 if self.path == '/health' else 404)
        self.end_headers()
    def do_POST(self):
        size = int(self.headers.get('content-length') or 0)
        body = json.loads(self.rfile.read(size) or b'{}')
        messages = body.get('messages') or []
        if messages and messages[-1].get('role') == 'tool':
            message = {'role': 'assistant', 'content': 'The item is in your cart.'}
            payload = {'choices': [{'message': message, 'finish_reason': 'stop'}]}
        else:
            call = {
                'id': 'add-1', 'type': 'function',
                'function': {'name': 'add', 'arguments': json.dumps({'item_id': 'big_mac'})},
            }
            payload = {'choices': [{'message': {'role': 'assistant', 'content': '', 'tool_calls': [call]}, 'finish_reason': 'tool_calls'}]}
        encoded = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
""",
        encoding="utf-8",
    )
    (source / "Dockerfile").write_text(
        'FROM python:3.12-alpine\nWORKDIR /app\nCOPY agent.py .\nCMD ["python", "agent.py"]\n',
        encoding="utf-8",
    )
    (source / "compose.yml").write_text(
        """services:
  api:
    build: .
    ports: ["8080:8080"]
""",
        encoding="utf-8",
    )
    contract = AgentContract(
        agent="packaged-chat",
        modality="chat",
        implementation="present",
        tools=[ToolSpec(name="add", args=["item_id"])],
        real_use_cases=["add an item"],
        runtime=Runtime(
            compose_file="compose.yml",
            interface={
                "kind": "http",
                "protocol": "openai_chat",
                "port": 8080,
                "path": "/v1/chat/completions",
                "health_path": "/health",
            },
        ),
    )
    environment = provision(source, session, contract)
    assert environment.services == []
    assert environment.runtime_services == ["api"]

    world = GeneratedWorld(":memory:")
    world.tools = [{"name": "add"}]
    world.handlers = {
        "add": "def handle(args, db):\n    return {'added': args['item_id']}\n"
    }
    target = RepositoryChatTarget(
        contract,
        world,
        world_root=session,
        trace_path=session / "chat-tool-calls.jsonl",
        scenario_name="add-item",
    )

    async def exercise() -> str:
        await target.open()
        try:
            return await target.say("Please add a Big Mac")
        finally:
            await target.close()

    try:
        assert asyncio.run(exercise()) == "The item is in your cart."
        assert [call.name for call in world.calls] == ["add"]
        assert ProvisionedEnvironment.load(session).runtime_container == ""
    finally:
        world.close()
        stop(session)


def _vol(spec, key="data", **service):
    return {
        "name": "job",
        "services": {"agent": service},
        "volumes": {key: spec},
    }


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            _vol(
                {
                    "name": "job_data",
                    "driver": "local",
                    "driver_opts": {"type": "none", "o": "bind", "device": "/var/run"},
                }
            ),
            "volume data: driver_opts",
        ),
        (
            _vol(
                {
                    "name": "job_data",
                    "driver_opts": {"type": "none", "o": "bind", "device": "/"},
                },
                volumes=[
                    {
                        "type": "volume",
                        "source": "data",
                        "target": "/h",
                        "volume": {"subpath": "var/run"},
                    }
                ],
            ),
            "volume data: driver_opts",
        ),
        (
            _vol({"name": "job_data", "driver_opts": {"device": "/var/run"}}),
            "volume data: driver_opts",
        ),
        (
            _vol({"name": "job_data", "driver_opts": {"o": "bind"}}),
            "volume data: driver_opts",
        ),
        (
            _vol({"name": "job_data", "driver_opts": {"type": "bind"}}),
            "volume data: driver_opts",
        ),
        (
            _vol({"name": "job_data", "driver_opts": {"type": "nfs", "device": ":/x"}}),
            "volume data: driver_opts",
        ),
        (
            _vol(
                {
                    "name": "job_data",
                    "driver_opts": {"type": "tmpfs", "device": "/", "o": "bind"},
                }
            ),
            "volume data: driver_opts",
        ),
        (
            _vol({"name": "otherjob_data", "external": True}),
            "volume data: external",
        ),
        (_vol({"name": "job_data", "external": True}), "volume data: external"),
        (_vol({"name": "otherjob_data"}), "volume data: named outside the project"),
        (_vol({"name": "job_other"}), "volume data: named outside the project"),
        (_vol({"name": "job_data", "driver": "nfs"}), "volume data: driver"),
        (_vol({"name": "job_data", "driver": "rexray/s3fs"}), "volume data: driver"),
        (
            _vol(
                {"name": "job_data"},
                volumes=[{"type": "image", "source": "alpine", "target": "/h"}],
            ),
            "agent: volume type",
        ),
        (
            _vol(
                {"name": "job_data"},
                volumes=[{"type": "npipe", "source": "x", "target": "/h"}],
            ),
            "agent: volume type",
        ),
    ],
)
def test_a_named_volume_that_reaches_outside_the_project_is_rejected(config, expected):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(config)
    assert expected in str(error.value)


@pytest.mark.parametrize(
    "config",
    [
        _vol({}),
        _vol({"name": "job_data"}),
        _vol({"name": "job_data", "driver": "local"}),
        _vol({"name": "job_data", "driver": ""}),
        _vol({"name": "job_data", "driver_opts": {}}),
        _vol({"name": "job_data", "labels": {"a": "b"}}),
        _vol(
            {
                "name": "job_data",
                "driver_opts": {"type": "tmpfs", "device": "tmpfs", "o": "size=64m"},
            }
        ),
        _vol(None),
        _vol(
            {"name": "job_data"},
            volumes=[
                {"type": "volume", "source": "data", "target": "/h"},
                {"type": "tmpfs", "target": "/t"},
                {"type": "bind", "source": "/srv/data", "target": "/b"},
                {
                    "type": "volume",
                    "source": "data",
                    "target": "/s",
                    "volume": {"subpath": "sub"},
                },
            ],
        ),
    ],
)
def test_a_named_volume_inside_the_project_is_accepted(config):
    _validate_compose_security(config)


@pytest.mark.parametrize(
    "config",
    [
        _vol({"name": "CANARY-7f3a"}),
        _vol({"name": "job_data", "driver": "CANARY-7f3a"}),
        _vol({"name": "job_data", "driver_opts": {"device": "CANARY-7f3a"}}),
        _vol({"name": "job_data"}, volumes=[{"type": "CANARY-7f3a", "target": "/h"}]),
    ],
)
def test_a_volume_refusal_never_contains_a_value(config):
    with pytest.raises(ProvisionError) as error:
        _validate_compose_security(config)
    assert "CANARY" not in str(error.value)
    assert "7f3a" not in str(error.value)


@pytest.mark.parametrize("hook", ["post_start", "pre_stop"])
def test_a_privileged_lifecycle_hook_is_rejected(hook):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(
            _svc(**{hook: [{"command": ["id"], "privileged": True, "user": "root"}]})
        )
    assert f"agent: {hook} privileged" in str(error.value)
    assert "db:" not in str(error.value)


@pytest.mark.parametrize("hook", ["post_start", "pre_stop"])
def test_a_lifecycle_hook_field_outside_the_allowlist_is_rejected(hook):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(
            _svc(**{hook: [{"command": ["id"], "bogus": "CANARY-7f3a"}]})
        )
    assert f"agent: {hook} field bogus is not allowed" in str(error.value)
    assert "CANARY" not in str(error.value)


@pytest.mark.parametrize("hook", ["post_start", "pre_stop"])
def test_a_lifecycle_hook_without_privilege_is_accepted(hook):
    _validate_compose_security(
        _svc(
            **{
                hook: [
                    {"command": ["id"]},
                    {
                        "command": ["id"],
                        "user": "1000",
                        "working_dir": "/app",
                        "environment": {"A": "b"},
                        "privileged": False,
                    },
                ]
            }
        )
    )


def _link(tmp_path, target, name="sock"):
    link = tmp_path / name
    link.symlink_to(target)
    return str(link)


@pytest.mark.parametrize("target", ["/var/run", "/var/run/docker.sock", "/", "/run"])
@pytest.mark.parametrize("form", ["long", "short"])
def test_a_symlink_to_the_docker_socket_or_a_parent_is_rejected(tmp_path, target, form):
    source = _link(tmp_path, target)
    volume = (
        {"type": "bind", "source": source, "target": "/h"}
        if form == "long"
        else f"{source}:/h:ro"
    )
    with pytest.raises(ProvisionError, match="agent: Docker socket mount"):
        _validate_compose_security(
            {"services": {"agent": {"volumes": [volume]}}}, source=tmp_path
        )


def test_a_relative_symlink_and_a_chain_of_links_are_resolved(tmp_path):
    _link(tmp_path, "/var/run", name="first")
    _link(tmp_path, tmp_path / "first", name="second")
    for source in ("./first", "./second", str(tmp_path / "second")):
        with pytest.raises(ProvisionError, match="agent: Docker socket mount"):
            _validate_compose_security(
                {
                    "services": {
                        "agent": {
                            "volumes": [
                                {"type": "bind", "source": source, "target": "/h"}
                            ]
                        }
                    }
                },
                source=tmp_path,
            )


def test_a_symlink_to_a_folder_of_the_job_is_not_a_docker_socket_mount(tmp_path):
    (tmp_path / "data").mkdir()
    link = _link(tmp_path, tmp_path / "data", name="link")
    _validate_compose_security(
        {
            "services": {
                "agent": {
                    "volumes": [
                        {"type": "bind", "source": link, "target": "/h"},
                        f"{link}:/g",
                    ]
                }
            }
        },
        source=tmp_path,
    )


def _build(tmp_path, build, secrets=None):
    config = _svc(build=build)
    if secrets is not None:
        config["secrets"] = secrets
    return config


@pytest.mark.parametrize(
    ("build", "secrets", "expected"),
    [
        ({"context": "/"}, None, "agent: build.context"),
        ({"context": "/etc"}, None, "agent: build.context"),
        ({"context": ".."}, None, "agent: build.context"),
        ({"context": "../other"}, None, "agent: build.context"),
        ({"context": "{tmp}/../other"}, None, "agent: build.context"),
        ({"context": "{tmp}-sibling"}, None, "agent: build.context"),
        ({"context": "{tmp}/escape"}, None, "agent: build.context"),
        ({"context": "https://example.com/x.git"}, None, "agent: build.context"),
        ({"context": "git@example.com:x/y.git"}, None, "agent: build.context"),
        ({"context": "."}, None, None),
        (
            {"context": ".", "additional_contexts": {"h": "/"}},
            None,
            "agent: build.additional_contexts",
        ),
        (
            {"context": ".", "additional_contexts": {"h": "../x"}},
            None,
            "agent: build.additional_contexts",
        ),
        (
            {"context": ".", "additional_contexts": ["h=/etc"]},
            None,
            "agent: build.additional_contexts",
        ),
        (
            {"context": ".", "additional_contexts": {"h": "https://example.com/x"}},
            None,
            "agent: build.additional_contexts",
        ),
        (
            {"context": ".", "secrets": ["s"]},
            {"s": {"file": "/etc/hosts"}},
            "agent: build.secrets",
        ),
        (
            {"context": ".", "secrets": [{"source": "s", "target": "t"}]},
            {"s": {"file": "/etc/hosts"}},
            "agent: build.secrets",
        ),
        (
            {"context": ".", "secrets": ["s"]},
            {"s": {"file": "../hosts"}},
            "agent: build.secrets",
        ),
        (
            {"context": ".", "secrets": ["s"]},
            {"s": {"environment": "HOME"}},
            "agent: build.secrets",
        ),
        (
            {"context": ".", "secrets": ["missing"]},
            {},
            "agent: build.secrets",
        ),
        ({"context": ".", "ssh": ["default"]}, None, "agent: build.ssh"),
        ({"context": ".", "ssh": {"default": None}}, None, "agent: build.ssh"),
        (
            {"context": ".", "cache_to": ["type=local,dest=/tmp/cache"]},
            None,
            "agent: build.cache_to",
        ),
        (
            {"context": ".", "cache_to": ["type=local,dest=../cache"]},
            None,
            "agent: build.cache_to",
        ),
        (
            {"context": ".", "cache_from": ["type=local,src=/etc"]},
            None,
            "agent: build.cache_from",
        ),
        (
            {"context": ".", "cache_from": ["mode=max,type=local,src=/etc"]},
            None,
            "agent: build.cache_from",
        ),
        (
            {"context": ".", "isolation": "hyperv"},
            None,
            "agent: build field isolation is not allowed",
        ),
        (
            {"context": ".", "some_future_field": "x"},
            None,
            "agent: build field some_future_field is not allowed",
        ),
    ],
)
def test_build_reach_outside_the_source_is_rejected(tmp_path, build, secrets, expected):
    (tmp_path / "escape").symlink_to("/etc")
    build = {
        key: (
            value.replace("{tmp}", str(tmp_path)) if isinstance(value, str) else value
        )
        for key, value in build.items()
    }
    config = _build(tmp_path, build, secrets)
    if expected is None:
        _validate_compose_security(config, source=tmp_path)
        return
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(config, source=tmp_path)
    assert expected in str(error.value)


def test_build_with_no_source_given_refuses_an_absolute_context():
    with pytest.raises(ProvisionError, match="agent: build.context"):
        _validate_compose_security(_svc(build={"context": "/srv/app"}))


@pytest.mark.parametrize(
    ("build", "secrets"),
    [
        ("{tmp}", None),
        ({"context": "{tmp}", "dockerfile": "Dockerfile"}, None),
        ({"context": "{tmp}/sub", "dockerfile_inline": "FROM busybox\n"}, None),
        (
            {"context": ".", "target": "prod", "args": {"A": "b"}, "network": "none"},
            None,
        ),
        (
            {
                "context": ".",
                "labels": {"a": "b"},
                "platforms": ["linux/amd64"],
                "tags": ["x:1"],
                "pull": True,
                "no_cache": True,
                "shm_size": "1g",
                "ulimits": {"nofile": 1024},
                "extra_hosts": {"a": "1.2.3.4"},
                "provenance": False,
                "sbom": False,
                "x-anything": 1,
            },
            None,
        ),
        (
            {
                "context": ".",
                "additional_contexts": {
                    "a": "{tmp}/sub",
                    "b": "./sub",
                    "c": "docker-image://alpine:3",
                    "d": "service:db",
                },
            },
            None,
        ),
        (
            {"context": ".", "secrets": ["s", {"source": "t"}]},
            {"s": {"file": "{tmp}/s.txt"}, "t": {"file": "./t.txt"}},
        ),
        (
            {
                "context": ".",
                "cache_from": ["alpine:3", "type=registry,ref=x/y:cache"],
                "cache_to": ["type=inline", "type=local,dest={tmp}/cache"],
            },
            None,
        ),
        ({"context": ".", "ssh": [], "secrets": [], "additional_contexts": {}}, None),
    ],
)
def test_build_inside_the_source_is_accepted(tmp_path, build, secrets):
    def fill(value):
        if isinstance(value, str):
            return value.replace("{tmp}", str(tmp_path))
        if isinstance(value, list):
            return [fill(item) for item in value]
        if isinstance(value, dict):
            return {key: fill(item) for key, item in value.items()}
        return value

    _validate_compose_security(
        _build(tmp_path, fill(build), fill(secrets)), source=tmp_path
    )


@pytest.mark.parametrize(
    "build",
    [
        {"context": "/CANARY-7f3a"},
        {"context": ".", "additional_contexts": {"h": "/CANARY-7f3a"}},
        {"context": ".", "secrets": ["CANARY-7f3a"]},
        {"context": ".", "ssh": ["CANARY-7f3a"]},
        {"context": ".", "cache_to": ["type=local,dest=/CANARY-7f3a"]},
        {"context": ".", "cache_from": ["type=local,src=/CANARY-7f3a"]},
    ],
)
def test_a_build_refusal_never_contains_a_value(tmp_path, build):
    with pytest.raises(ProvisionError) as error:
        _validate_compose_security(_build(tmp_path, build, {}), source=tmp_path)
    assert "CANARY" not in str(error.value)
    assert "7f3a" not in str(error.value)


@pytest.mark.parametrize("dockerfile", ["/etc/passwd", "../../outside/Dockerfile"])
def test_a_dockerfile_outside_the_source_is_rejected(tmp_path, dockerfile):
    with pytest.raises(ProvisionError, match="agent: build.dockerfile"):
        _validate_compose_security(
            _svc(build={"context": ".", "dockerfile": dockerfile}), source=tmp_path
        )


def test_a_dockerfile_beside_the_context_inside_the_source_is_accepted(tmp_path):
    _validate_compose_security(
        _svc(build={"context": "sub", "dockerfile": "../Dockerfile.prod"}),
        source=tmp_path,
    )


def test_a_build_context_beside_the_compose_file_is_inside_the_job(tmp_path):
    source, session = tmp_path / "source", tmp_path / "session"
    source.mkdir()
    session.mkdir()
    config = _svc(build={"context": str(session / "generated-runtime-context")})
    _validate_compose_security(config, source=(source, session))
    with pytest.raises(ProvisionError, match="agent: build.context"):
        _validate_compose_security(config, source=source)


def _env_files(entries):
    config = _svc()
    config["x-alk-env-files"] = {"agent": entries}
    return config


@pytest.mark.parametrize(
    "entry",
    [
        {"path": "/etc/hosts", "required": True},
        {"path": "../outside.env", "required": False},
        {"path": "{tmp}/../outside.env", "required": True},
        {"path": "{tmp}/link.env", "required": True},
        "/etc/hosts",
        "../outside.env",
    ],
)
def test_an_env_file_outside_the_source_is_rejected(tmp_path, entry):
    (tmp_path / "link.env").symlink_to("/etc/hosts")
    if isinstance(entry, dict):
        entry = {**entry, "path": entry["path"].replace("{tmp}", str(tmp_path))}
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(_env_files([entry]), source=tmp_path)
    assert "agent: env_file outside the project" in str(error.value)
    assert "/etc" not in str(error.value)
    assert "outside.env" not in str(error.value)


@pytest.mark.parametrize(
    "path", ["{tmp}/${ENVF:-./b.env}", "${HOME}/x.env", "{tmp}/$ENVF", "{tmp}/a$$.env"]
)
def test_an_env_file_path_with_a_variable_is_rejected(tmp_path, path):
    # The check reads a render made without interpolation, so a variable could name any file.
    entry = {"path": path.replace("{tmp}", str(tmp_path)), "required": True}
    with pytest.raises(ProvisionError, match="agent: env_file path with a variable"):
        _validate_compose_security(_env_files([entry]), source=tmp_path)


def test_an_env_file_still_in_the_service_is_checked_too(tmp_path):
    with pytest.raises(ProvisionError, match="agent: env_file outside the project"):
        _validate_compose_security(
            _svc(env_file=[{"path": "/etc/hosts"}]), source=tmp_path
        )
    with pytest.raises(ProvisionError, match="agent: env_file outside the project"):
        _validate_compose_security(_svc(env_file="../x.env"), source=tmp_path)


@pytest.mark.parametrize(
    "entries",
    [
        [],
        ["a.env"],
        ["./a.env", "sub/b.env"],
        [{"path": "{tmp}/a.env", "required": True}],
        [{"path": "{tmp}/sub/missing.env", "required": False}],
    ],
)
def test_an_env_file_inside_the_source_is_accepted(tmp_path, entries):
    entries = [
        {**item, "path": item["path"].replace("{tmp}", str(tmp_path))}
        if isinstance(item, dict)
        else item
        for item in entries
    ]
    _validate_compose_security(_env_files(entries), source=tmp_path)
    _validate_compose_security(_svc(env_file=entries), source=tmp_path)


def _env_environment(tmp_path):
    return ProvisionedEnvironment(
        source=str(tmp_path), compose_file=str(tmp_path / "c.yml"), project="job"
    )


def test_the_config_render_keeps_resolved_files_and_checks_unresolved_ones(
    tmp_path, monkeypatch
):
    import json

    seen = []
    resolved = {"services": {"agent": {"environment": {"FROM_FILE": "kept"}}}}
    unresolved = {
        "services": {
            "agent": {
                "env_file": [{"path": str(tmp_path / "a.env"), "required": True}],
                "environment": {},
            },
            "db": {},
        }
    }

    def fake_run(_environment, *arguments, **kwargs):
        seen.append((arguments, kwargs.get("process_overrides")))
        return json.dumps(
            unresolved if "--no-env-resolution" in arguments else resolved
        )

    monkeypatch.setattr(provisioning, "_run", fake_run)
    config = provisioning._config(
        _env_environment(tmp_path), process_overrides={"A": "b"}
    )

    # The file that runs is the resolved one: the job's own env_file values still reach it.
    assert config["services"]["agent"]["environment"] == {"FROM_FILE": "kept"}
    assert "env_file" not in config["services"]["agent"]
    # The check sees the unresolved render's paths.
    assert config["x-alk-env-files"] == {"agent": [str(tmp_path / "a.env")]}
    plain = [a for a, _ in seen if "--no-env-resolution" not in a]
    flagged = [a for a, _ in seen if "--no-env-resolution" in a]
    assert len(seen) == len(plain) + len(flagged) == 2
    assert len(plain) == len(flagged) == 1
    for arguments in (plain[0], flagged[0]):
        assert arguments[:2] == ("--profile", "*")
        assert "config" in arguments
    # On Compose v2.40 the env files stay as paths only with both flags.
    assert {"--no-env-resolution", "--no-interpolate"} <= set(flagged[0])
    assert "--no-interpolate" not in plain[0]
    assert all(overrides == {"A": "b"} for _, overrides in seen)
    _validate_compose_security(config, source=tmp_path)


def test_an_env_file_outside_the_source_is_refused_after_the_config_render(
    tmp_path, monkeypatch
):
    import json

    unresolved = {"services": {"agent": {"env_file": [{"path": "/etc/hosts"}]}}}
    resolved = {
        "services": {"agent": {"environment": {"A": "b"}}},
        # A key from the submitted file never survives: the render's own map replaces it.
        "x-alk-env-files": {"agent": ["./safe.env"]},
    }
    monkeypatch.setattr(
        provisioning,
        "_run",
        lambda _e, *arguments, **_k: json.dumps(
            unresolved if "--no-env-resolution" in arguments else resolved
        ),
    )
    config = provisioning._config(_env_environment(tmp_path))
    assert config["x-alk-env-files"] == {"agent": ["/etc/hosts"]}
    with pytest.raises(ProvisionError, match="agent: env_file outside the project"):
        _validate_compose_security(config, source=tmp_path)


_HOME = str(Path.home())
_OTHER_DAEMON_SOCKETS = [
    "/run/user",
    "/run/user/1000",
    "/run/user/1000/docker.sock",
    "/run/user/1000/",
    "/var/run/user/1000/docker.sock",
    "/run/containerd",
    "/run/containerd/containerd.sock",
    "/var/run/containerd/containerd.sock",
    "/run/./containerd/",
    f"{_HOME}/.docker/run",
    f"{_HOME}/.docker/run/docker.sock",
]


@pytest.mark.parametrize("source", _OTHER_DAEMON_SOCKETS)
@pytest.mark.parametrize("form", ["long", "short"])
def test_a_bind_of_another_daemon_socket_or_its_folder_is_rejected(source, form):
    volume = (
        {"type": "bind", "source": source, "target": "/h"}
        if form == "long"
        else f"{source}:/h:ro"
    )
    with pytest.raises(ProvisionError, match="agent: Docker socket mount"):
        _validate_compose_security({"services": {"agent": {"volumes": [volume]}}})


@pytest.mark.parametrize(
    "source",
    ["/run/userdata", "/run/user/1000/app.sock", "/run/containerdata", "/run/user-x"],
)
def test_a_bind_beside_another_daemon_socket_is_accepted(source):
    _validate_compose_security(
        {
            "services": {
                "agent": {
                    "volumes": [{"type": "bind", "source": source, "target": "/h"}]
                }
            }
        }
    )


_GPU = {"driver": "nvidia", "capabilities": ["gpu"], "count": "all"}


@pytest.mark.parametrize(
    ("deploy", "expected"),
    [
        ({"resources": {"reservations": {"devices": [_GPU]}}}, "agent: host devices"),
        (
            {"resources": {"reservations": {"cpus": "1", "devices": [_GPU]}}},
            "agent: host devices",
        ),
        ({"bogus": 1}, "agent: deploy field bogus is not allowed"),
        (
            {"resources": {"bogus": 1}},
            "agent: deploy.resources field bogus is not allowed",
        ),
        (
            {"resources": {"limits": {"bogus": 1}}},
            "agent: deploy.resources.limits field bogus is not allowed",
        ),
        (
            {"resources": {"reservations": {"generic_resources": [{"x": 1}]}}},
            "agent: deploy.resources.reservations field generic_resources is not allowed",
        ),
    ],
)
def test_deploy_that_reaches_the_host_is_rejected(deploy, expected):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(_svc(deploy=deploy))
    assert expected in str(error.value)


@pytest.mark.parametrize(
    "deploy",
    [
        {"resources": {"limits": {"cpus": "1"}}},
        {"replicas": 1, "mode": "replicated"},
        {"resources": {"limits": {"cpus": "1", "memory": "1g", "pids": 100}}},
        {"resources": {"reservations": {"cpus": "1", "memory": "1g"}}},
        {"resources": {"reservations": {"devices": []}}},
        {
            "restart_policy": {"condition": "on-failure"},
            "placement": {"constraints": []},
            "update_config": {},
            "rollback_config": {},
            "labels": {"a": "b"},
            "endpoint_mode": "vip",
        },
    ],
)
def test_deploy_without_host_reach_is_accepted(deploy):
    _validate_compose_security(_svc(deploy=deploy))


@pytest.mark.parametrize(
    "logging",
    [
        {"driver": "syslog", "options": {"syslog-address": "tcp://127.0.0.1:5432"}},
        {"driver": "fluentd", "options": {"fluentd-address": "10.0.0.5:24224"}},
        {"driver": "awslogs", "options": {"awslogs-group": "g"}},
        {"driver": "gelf"},
        {"driver": "journald"},
        {"driver": "JSON-FILE"},
        {"driver": "CANARY-7f3a"},
    ],
)
def test_a_logging_driver_that_dials_out_is_rejected(logging):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(_svc(logging=logging))
    assert "agent: logging driver" in str(error.value)
    assert "CANARY" not in str(error.value)
    assert "127.0.0.1" not in str(error.value)


@pytest.mark.parametrize(
    "logging",
    [
        {"driver": "json-file", "options": {"max-size": "10m", "max-file": "3"}},
        {"driver": "local"},
        {"driver": "none"},
        {"options": {"max-size": "10m"}},
        {},
    ],
)
def test_a_local_logging_driver_is_accepted(logging):
    _validate_compose_security(_svc(logging=logging))


@pytest.mark.parametrize(
    "security_opt",
    [["no-new-privileges:true"], ["no-new-privileges=true"], []],
)
def test_the_no_new_privileges_hardening_flag_is_accepted(security_opt):
    _validate_compose_security(_svc(security_opt=security_opt))


@pytest.mark.parametrize(
    "security_opt",
    [
        ["seccomp=unconfined"],
        ["apparmor=unconfined"],
        ["label=disable"],
        ["no-new-privileges:true", "seccomp=unconfined"],
        ["no-new-privileges:false"],
        ["no-new-privileges"],
        ["CANARY-7f3a"],
        "no-new-privileges:true",
    ],
)
def test_any_other_security_opt_is_rejected_by_category(security_opt):
    with pytest.raises(ProvisionError, match="forbidden host access") as error:
        _validate_compose_security(_svc(security_opt=security_opt))
    assert "agent: security_opt" in str(error.value)
    assert "field security_opt" not in str(error.value)
    assert "CANARY" not in str(error.value)
    assert "unconfined" not in str(error.value)


def test_a_compose_warning_on_stderr_does_not_break_the_render(tmp_path, monkeypatch):
    import json
    import subprocess

    warning = (
        'time="2026-11-17T18:28:22+05:30" level=warning msg="the attribute `version` '
        'is obsolete, it will be ignored"'
    )

    def fake_run(command, **_kwargs):
        document = {"services": {"agent": {"environment": {"A": "b"}}}}
        return subprocess.CompletedProcess(
            command, 0, json.dumps(document, indent=2), warning
        )

    monkeypatch.setattr(provisioning.subprocess, "run", fake_run)
    config = provisioning._config(_env_environment(tmp_path))
    assert config["services"]["agent"]["environment"] == {"A": "b"}


def test_a_failed_compose_command_still_reports_its_stderr(tmp_path, monkeypatch):
    import subprocess

    monkeypatch.setattr(
        provisioning.subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 1, "", "invalid compose file"
        ),
    )
    with pytest.raises(ProvisionError, match="invalid compose file"):
        provisioning._config(_env_environment(tmp_path))


def test_a_service_with_a_missing_env_file_and_ports_gets_one_override_entry(tmp_path):
    import yaml

    compose = tmp_path / "compose.yml"
    compose.write_text(
        "services:\n  crawl:\n    image: x\n    env_file: .llm.env\n"
        "    ports: ['11235:11235']\n  other:\n    image: y\n    env_file: .gone.env\n"
    )
    environment = ProvisionedEnvironment(
        source=str(tmp_path), compose_file=str(compose), project="isolated"
    )
    _write_port_override(
        tmp_path / "session",
        environment,
        {
            "services": {
                "crawl": {"ports": [{"target": 11235, "published": "11235"}]},
                "other": {},
            }
        },
    )
    generated = Path(environment.compose_override_file).read_text()

    class Loader(yaml.SafeLoader):
        pass

    # Compose's own tags are not YAML core; keep each tagged node's value.
    Loader.add_multi_constructor(
        "!",
        lambda loader, _tag, node: (
            loader.construct_scalar(node)
            if isinstance(node, yaml.ScalarNode)
            else loader.construct_sequence(node, deep=True)
            if isinstance(node, yaml.SequenceNode)
            else loader.construct_mapping(node, deep=True)
        ),
    )
    document = yaml.load(
        generated, Loader=Loader
    )  # a duplicate key does not parse here
    assert generated.count('"crawl":') == 1
    assert "env_file: !reset []" in generated
    assert "ports: !override" in generated
    assert document["services"]["crawl"]["ports"][0]["target"] == 11235
    assert document["services"]["other"]["env_file"] == []
