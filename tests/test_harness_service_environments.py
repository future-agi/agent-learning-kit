from __future__ import annotations

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
from fi.alk.harness.bundle import export_session_bundle
from fi.alk.harness.provision import (
    ProvisionError,
    ProvisionedEnvironment,
    _internal_overrides,
    _endpoint_ready,
    _managed_compose,
    _start_managed_services,
    _overrides,
    _validate_compose_security,
    _write_port_override,
    healthy,
    provision,
    reset,
    start_runtime,
    stop,
    stop_runtime,
)
from fi.alk.harness.service_catalog import profile_for


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
    monkeypatch.setattr("fi.alk.harness.provision._free_port", lambda: 46379)

    _write_port_override(
        tmp_path / "session",
        environment,
        {"services": {"redis": {"ports": [{"target": 6379, "published": "6379"}]}}},
    )

    generated = Path(environment.compose_override_file).read_text()
    assert "ports: !override" in generated
    assert 'published: "46379"' in generated
    assert 'host_ip: "127.0.0.1"' in generated


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
    monkeypatch.setattr("fi.alk.harness.provision._free_port", lambda: 48000)

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
    assert "coturn" not in generated
    assert "49152" not in generated


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
            Dependency(name="OpenAI TTS", kind="service", what="speech synthesis")
        ],
        runtime=Runtime(dockerfile="Dockerfile"),
    )

    target = _managed_compose(source, tmp_path / "session", contract)

    assert target is not None
    document = json.loads(target.read_text())
    assert set(document["services"]) == {"agent-runtime"}


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
