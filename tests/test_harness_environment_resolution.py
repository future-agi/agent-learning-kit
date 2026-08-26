from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness.environment_resolution import resolve_environment_plan
from fi.alk.harness.packaging import inspect_packaging


def _runtime(**values):
    defaults = {
        "compose_file": "",
        "dockerfile": "",
        "workdir": "",
        "language": "",
        "version": "",
        "command": [],
    }
    return SimpleNamespace(**{**defaults, **values})


@pytest.mark.parametrize(
    ("case", "expected_type", "expected_adapter"),
    [
        ("uber-compose", "compose", "submitted_compose"),
        ("hotel-dockerfile", "dockerfile", "managed_compose_for_dockerfile"),
        ("livekit-dockerfile", "dockerfile", "managed_compose_for_dockerfile"),
        ("packaged-chat", "dockerfile", "managed_compose_for_dockerfile"),
        ("unpackaged-chat", "unpackaged", "generated_runtime"),
        ("complex-compose", "compose", "submitted_compose"),
    ],
)
def test_environment_certification_static_matrix(
    tmp_path: Path, case: str, expected_type: str, expected_adapter: str
) -> None:
    source = tmp_path / case
    source.mkdir()
    runtime = _runtime()
    dependencies = []
    if case in {"uber-compose", "complex-compose"}:
        (source / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
        services = "  agent:\n    build: .\n    command: [python, agent.py]\n"
        if case == "complex-compose":
            services += (
                "  postgres:\n    image: postgres:17\n"
                "  redis:\n    image: redis:7\n"
                "  minio:\n    image: minio/minio:latest\n"
            )
        (source / "compose.yml").write_text("services:\n" + services, encoding="utf-8")
        runtime = _runtime(compose_file="compose.yml")
    elif case == "unpackaged-chat":
        (source / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
        (source / "main.py").write_text("print('ready')\n", encoding="utf-8")
        runtime = _runtime(language="python", command=["python", "main.py"])
    else:
        (source / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
        runtime = _runtime(dockerfile="Dockerfile")
        if case == "hotel-dockerfile":
            dependencies = [
                SimpleNamespace(
                    name="local SQLite state",
                    engine="sqlite",
                    kind="datastore",
                    what="in-process booking state",
                    reached=SimpleNamespace(
                        loader_module="hotel.db", loader_function="load"
                    ),
                )
            ]
    contract = SimpleNamespace(
        runtime=runtime, dependencies=dependencies, data_store=None
    )
    packaging = inspect_packaging(source)

    first = resolve_environment_plan(
        source, packaging, contract, source_fingerprint="a" * 64
    )
    second = resolve_environment_plan(
        source, packaging, contract, source_fingerprint="a" * 64
    )

    assert first.supported is True
    assert first.packaging_type == expected_type
    assert first.runtime_adapter == expected_adapter
    assert first.digest == second.digest
    assert first.source_fingerprint == "a" * 64


def test_environment_resolution_rejects_unowned_dependency_before_docker(
    tmp_path: Path,
) -> None:
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    (tmp_path / "main.py").write_text("print('ready')\n", encoding="utf-8")
    contract = SimpleNamespace(
        runtime=_runtime(language="python", command=["python", "main.py"]),
        data_store=None,
        dependencies=[
            SimpleNamespace(
                name="private mystery service",
                engine="mysteryd",
                kind="service",
                what="required remote state",
                reached=SimpleNamespace(),
            )
        ],
    )

    plan = resolve_environment_plan(tmp_path, contract=contract)

    assert plan.supported is False
    assert plan.code == "unsupported_dependency"
    assert plan.dependencies[0].ownership == "unsupported"
    assert plan.action


def test_environment_resolution_accepts_optional_external_api_with_embedded_fallback(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "frontdesk"\nversion = "0.1.0"\ndependencies = ["livekit-agents"]\n',
        encoding="utf-8",
    )
    (tmp_path / "agent.py").write_text("print('ready')\n", encoding="utf-8")
    contract = SimpleNamespace(
        runtime=_runtime(language="python", command=["python", "agent.py"]),
        data_store=None,
        dependencies=[
            SimpleNamespace(
                name="FakeCalendar",
                engine="in-process Python",
                kind="datastore",
                what="fallback used when CAL_API_KEY is unset",
                reached=SimpleNamespace(
                    loader_module="calendar_api",
                    loader_function="FakeCalendar",
                    dsn_env="",
                    config_key="",
                    password_from="",
                    database="",
                ),
            ),
            SimpleNamespace(
                name="cal.com API",
                engine="HTTP REST",
                kind="service",
                what="optional production calendar backend",
                reached=SimpleNamespace(
                    loader_module="",
                    loader_function="",
                    dsn_env="CAL_API_KEY",
                    config_key="",
                    password_from="",
                    database="",
                ),
            ),
        ],
    )

    plan = resolve_environment_plan(tmp_path, contract=contract)

    assert plan.supported is True
    assert plan.runtime_adapter == "generated_runtime"
    assert [item.ownership for item in plan.dependencies] == [
        "embedded",
        "external_provider",
    ]
