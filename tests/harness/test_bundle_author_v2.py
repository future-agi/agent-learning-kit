from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk.harness.bundle_author_v2 import author_bundle_v2, resolve_environment_plan
from fi.alk.harness.bundle_v2 import load_bundle_v2
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.process_preflight import preflight_bundle


def _job(
    *, connector: str, with_secrets: bool = False, scenario_count: int = 1
) -> HarnessJob:
    secret_refs = {}
    if with_secrets:
        secret_refs = {
            "LIVEKIT_API_KEY": {
                "manager": "platform-vault",
                "key": "livekit-key",
                "purpose": "target_provider",
            }
        }
    return HarnessJob.model_validate(
        {
            "job_id": "job-v2",
            "run_id": "run-v2",
            "execution": "hosted",
            "source": {"kind": "archive", "archive_artifact_id": "source-1"},
            "agent": {"connector": connector, "secret_refs": secret_refs},
            "scenario_count": scenario_count,
            "runtime": {
                "isolation": "dedicated_vm",
                "cpu_units": 2,
                "memory_mb": 4096,
                "parallelism": 1,
            },
        }
    )


def _authoring(root: Path) -> Path:
    artifact = root / "authoring"
    scenario = artifact / "scenarios" / "one"
    (scenario / "checks").mkdir(parents=True)
    (scenario / "scenario.json").write_text(
        json.dumps({"name": "one", "instruction": "Test one", "sub_goals": ["works"]}),
        encoding="utf-8",
    )
    (scenario / "setup.py").write_text(
        "def setup(world):\n    return None\n", encoding="utf-8"
    )
    (scenario / "ready.py").write_text(
        "def ready(world):\n    return None\n", encoding="utf-8"
    )
    (scenario / "checks" / "works.py").write_text(
        "def check(world, calls):\n    return None\n", encoding="utf-8"
    )
    return artifact


@pytest.mark.parametrize(
    ("case", "connector", "packaging"),
    [
        ("uber-compose", "livekit", "compose"),
        ("packaged-chat", "http", "compose"),
        ("unpackaged-chat", "http", "generated_python"),
        ("frontdesk", "livekit", "dockerfile"),
        ("drive-thru", "livekit", "dockerfile"),
        ("hotel-receptionist", "livekit", "dockerfile"),
    ],
)
def test_six_supported_shapes_produce_preflight_clean_bundle(
    tmp_path: Path, case: str, connector: str, packaging: str
) -> None:
    source = tmp_path / case
    source.mkdir()
    if case == "uber-compose":
        (source / "agent" / "agent.py").parent.mkdir()
        (source / "agent" / "agent.py").write_text("print('agent')\n", encoding="utf-8")
        (source / "pyproject.toml").write_text(
            "[project]\nname='agent'\nversion='1'\n", encoding="utf-8"
        )
        tools = source / "tools-api"
        tools.mkdir()
        (tools / "agent.py").write_text("print('tools')\n", encoding="utf-8")
        (tools / "requirements.txt").write_text("\n", encoding="utf-8")
        (source / "compose.yml").write_text(
            """services:
  postgres:
    image: postgres:16
  tools-api:
    build: ./tools-api
    depends_on: {postgres: {condition: service_healthy}}
  agent:
    build: .
    depends_on: {tools-api: {condition: service_healthy}}
""",
            encoding="utf-8",
        )
    else:
        (source / "agent.py").write_text("print('agent')\n", encoding="utf-8")
        if case == "packaged-chat":
            (source / "Dockerfile").write_text("FROM python:3.12\n", encoding="utf-8")
            (source / "compose.yml").write_text(
                "services:\n  api:\n    build: .\n", encoding="utf-8"
            )
        elif packaging == "dockerfile":
            (source / "Dockerfile").write_text("FROM python:3.13\n", encoding="utf-8")
            (source / "pyproject.toml").write_text(
                "[project]\nname='agent'\nversion='1'\n", encoding="utf-8"
            )
        else:
            (source / "requirements.txt").write_text("\n", encoding="utf-8")

    job = _job(connector=connector, with_secrets=connector == "livekit")
    plan = resolve_environment_plan(source, job)
    assert plan.packaging == packaging
    output = tmp_path / "bundle"
    first = author_bundle_v2(
        source=source, job=job, authoring=_authoring(tmp_path), output=output
    )
    loaded = load_bundle_v2(output)
    assert loaded.digest == first.digest
    assert loaded.metadata["packaging"] == packaging
    assert loaded.provenance.source_digest
    preflight_bundle(
        output,
        loaded,
        parallelism=1,
        secret_refs={
            alias: ref.purpose for alias, ref in job.agent.secret_refs.items()
        },
    )

    # The compiler is deterministic for identical source, authoring artifacts and job contract.
    second = author_bundle_v2(
        source=source, job=job, authoring=tmp_path / "authoring", output=output
    )
    assert second.digest == first.digest


def test_bundle_never_persists_resolved_secret(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    job = _job(connector="livekit", with_secrets=True)
    output = tmp_path / "bundle"
    author_bundle_v2(
        source=source, job=job, authoring=_authoring(tmp_path), output=output
    )
    manifest = (output / "manifest.json").read_text(encoding="utf-8")
    assert "livekit-key" not in manifest
    assert "target_provider" in manifest


def test_bundle_limits_authoring_scenarios_to_requested_count(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    second = authoring / "scenarios" / "two"
    (second / "checks").mkdir(parents=True)
    (second / "scenario.json").write_text(
        json.dumps({"name": "two", "instruction": "Test two", "sub_goals": ["works"]}),
        encoding="utf-8",
    )
    (second / "setup.py").write_text("def setup(world):\n    return None\n", encoding="utf-8")
    (second / "ready.py").write_text("def ready(world):\n    return None\n", encoding="utf-8")
    (second / "checks" / "works.py").write_text(
        "def check(world, calls):\n    return None\n", encoding="utf-8"
    )

    output = tmp_path / "bundle"
    author_bundle_v2(
        source=source,
        job=_job(connector="http", scenario_count=1),
        authoring=authoring,
        output=output,
    )
    assert [path.name for path in (output / "scenarios").iterdir()] == ["one"]


def test_bundle_combines_schema_with_frozen_store_rows(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    (authoring / "schema.sql").write_text(
        "SET search_path = '';\nCREATE TABLE public.users "
        "(id text PRIMARY KEY, tags text[], active boolean);\n",
        encoding="utf-8",
    )
    (authoring / "store.json").write_text(
        json.dumps(
            {
                "rows": {
                    "users": [
                        {"id": "rider-1", "tags": ["priority", "voice"], "active": True}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "bundle"
    manifest = author_bundle_v2(
        source=source,
        job=_job(connector="http"),
        authoring=authoring,
        output=output,
    )
    seed_sql = (output / "seed" / "world.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE public.users" in seed_sql
    assert "INSERT INTO public.\"users\"" in seed_sql
    assert "jsonb_populate_recordset(NULL::public.\"users\"" in seed_sql
    assert '"priority"' in seed_sql
    assert "SET session_replication_role = replica;" in seed_sql
    assert "SET session_replication_role = origin;" in seed_sql
    assert "schema.sql" in manifest.provenance.adopted_files
    assert "store.json" in manifest.provenance.adopted_files
