from __future__ import annotations

import json
import sqlite3
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


def _write_voice_contract(authoring: Path) -> None:
    (authoring / "contract.json").write_text(
        json.dumps({"modality": "voice"}), encoding="utf-8"
    )


def _write_callable_contract(authoring: Path) -> None:
    (authoring / "contract.json").write_text(
        json.dumps(
            {
                "modality": "chat",
                "runtime": {
                    "language": "python",
                    "interface": {
                        "kind": "callable",
                        "protocol": "fi.alk",
                        "include_tools": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_auto_voice_contract_compiles_livekit_process_runtime(tmp_path: Path) -> None:
    source = tmp_path / "voice-agent"
    source.mkdir()
    (source / "agent.py").write_text("print('agent')\n", encoding="utf-8")
    (source / "Dockerfile").write_text("FROM python:3.13\n", encoding="utf-8")
    (source / "pyproject.toml").write_text(
        "[project]\nname='agent'\nversion='1'\n", encoding="utf-8"
    )
    authoring = _authoring(tmp_path)
    _write_voice_contract(authoring)
    job = _job(connector="auto", with_secrets=True)

    bundle = author_bundle_v2(
        source=source, job=job, authoring=authoring, output=tmp_path / "bundle"
    )

    agent = next(process for process in bundle.processes if process.name == "agent")
    assert agent.environment["LIVEKIT_AGENT_NAME"].endswith("-w{{WORLD_INDEX}}")
    assert "target_provider" in agent.secret_purposes
    assert "target_http" not in bundle.capabilities


def test_callable_contract_compiles_repository_callback_adapter(tmp_path: Path) -> None:
    source = tmp_path / "ava"
    app = source / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "agent.py").write_text(
        "async def agent_callback(input):\n"
        "    return {'content': input.new_message['content']}\n\n"
        "if __name__ == '__main__':\n"
        "    print(input('you> '))\n",
        encoding="utf-8",
    )
    (source / "requirements.txt").write_text("agent-simulate\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    _write_callable_contract(authoring)

    bundle = author_bundle_v2(
        source=source,
        job=_job(connector="auto", with_secrets=True),
        authoring=authoring,
        output=tmp_path / "bundle",
    )

    agent = next(process for process in bundle.processes if process.name == "agent")
    assert agent.working_directory == "."
    assert agent.build_commands[1][-1] == "requirements.txt"
    assert agent.run_command[:2] == [".venv/bin/python", "-c"]
    assert "ThreadingHTTPServer" in agent.run_command[2]
    assert agent.environment["ALK_CALLBACK_ENTRYPOINT"] == "app.agent:agent_callback"
    assert agent.environment["PORT"] == "{{PORT_agent}}"
    assert bundle.capabilities["target_http"].service == "agent"
    assert any(probe.capability == "target_http" for probe in bundle.readiness)
    preflight_bundle(
        tmp_path / "bundle",
        bundle,
        parallelism=1,
        secret_refs={
            alias: ref.purpose
            for alias, ref in _job(
                connector="auto", with_secrets=True
            ).agent.secret_refs.items()
        },
    )


def test_repository_callback_is_discovered_when_contract_omits_interface(
    tmp_path: Path,
) -> None:
    source = tmp_path / "ava"
    app = source / "app"
    app.mkdir(parents=True)
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "agent.py").write_text(
        "async def agent_callback(input):\n"
        "    return {'content': input.new_message['content']}\n",
        encoding="utf-8",
    )
    (source / "requirements.txt").write_text("agent-simulate\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    (authoring / "contract.json").write_text(
        json.dumps(
            {
                "modality": "chat",
                "runtime": {
                    "language": "python",
                    "interface": None,
                    "command": ["python", "-m", "app.agent"],
                },
            }
        ),
        encoding="utf-8",
    )

    bundle = author_bundle_v2(
        source=source,
        job=_job(connector="auto", with_secrets=True),
        authoring=authoring,
        output=tmp_path / "bundle",
    )

    agent = next(process for process in bundle.processes if process.name == "agent")
    assert agent.working_directory == "."
    assert agent.run_command[:2] == [".venv/bin/python", "-c"]
    assert agent.environment["ALK_CALLBACK_ENTRYPOINT"] == "app.agent:agent_callback"
    assert bundle.capabilities["target_http"].service == "agent"


def test_callable_contract_rejects_missing_callback(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('cli only')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    _write_callable_contract(authoring)

    with pytest.raises(Exception, match="callback_entrypoint_missing"):
        author_bundle_v2(
            source=source,
            job=_job(connector="auto"),
            authoring=authoring,
            output=tmp_path / "bundle",
        )


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
    if connector == "livekit":
        control = next(
            process
            for process in loaded.processes
            if process.name == loaded.runtime.control_service
        )
        dispatch_name = control.environment["LIVEKIT_AGENT_NAME"]
        assert "{{JOB_ID}}" in dispatch_name
        assert "{{WORLD_INDEX}}" in dispatch_name
        assert (
            control.environment["HARNESS_TOOL_TRACE"]
            == "{{WORLD_DIR}}/agent-tool-calls.jsonl"
        )
        assert control.started_check is not None
        assert control.started_check.log_marker == "registered worker"
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
    (second / "setup.py").write_text(
        "def setup(world):\n    return None\n", encoding="utf-8"
    )
    (second / "ready.py").write_text(
        "def ready(world):\n    return None\n", encoding="utf-8"
    )
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


def test_bundle_preserves_sqlite_scalar_types_and_boolean_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    database = sqlite3.connect(authoring / "world.sqlite")
    try:
        database.execute(
            "CREATE TABLE payment_methods ("
            "id TEXT PRIMARY KEY, is_valid BOOLEAN, is_expired BOOLEAN, "
            "attempts INTEGER, score REAL)"
        )
        database.execute(
            "INSERT INTO payment_methods VALUES (?, ?, ?, ?, ?)",
            ("pm-1", True, False, 3, 0.75),
        )
        database.commit()
    finally:
        database.close()

    output = tmp_path / "bundle"
    author_bundle_v2(
        source=source,
        job=_job(connector="http"),
        authoring=authoring,
        output=output,
    )
    seed_sql = (output / "seed" / "world.sql").read_text(encoding="utf-8")
    assert (
        'CREATE TABLE IF NOT EXISTS "payment_methods" '
        '("id" text PRIMARY KEY, "is_valid" boolean, "is_expired" boolean, '
        '"attempts" bigint, "score" double precision);' in seed_sql
    )
    assert (
        'INSERT INTO "payment_methods" '
        '("id", "is_valid", "is_expired", "attempts", "score") '
        "VALUES ('pm-1', TRUE, FALSE, 3, 0.75);" in seed_sql
    )


def test_bundle_preserves_sqlite_column_defaults(tmp_path: Path) -> None:
    """A NOT NULL column with a default is filled implicitly by the authored world. Dropping the
    default while keeping NOT NULL makes every insert that relies on it fail against Postgres."""
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    database = sqlite3.connect(authoring / "world.sqlite")
    try:
        database.execute(
            "CREATE TABLE bookings ("
            "booking_ref TEXT PRIMARY KEY, "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        database.execute("INSERT INTO bookings (booking_ref) VALUES ('UB1')")
        database.commit()
    finally:
        database.close()

    output = tmp_path / "bundle"
    author_bundle_v2(
        source=source,
        job=_job(connector="http"),
        authoring=authoring,
        output=output,
    )
    seed_sql = (output / "seed" / "world.sql").read_text(encoding="utf-8")
    assert "DEFAULT CURRENT_TIMESTAMP" in seed_sql
    assert "DEFAULT 'pending'" in seed_sql
    # NOT NULL must keep its default, or an insert that omits the column fails where the
    # authored world accepted it.
    assert "NOT NULL DEFAULT CURRENT_TIMESTAMP" in seed_sql
    assert "NOT NULL DEFAULT 'pending'" in seed_sql


def test_bundle_preserves_sqlite_unique_constraints_for_upserts(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    database = sqlite3.connect(authoring / "world.sqlite")
    try:
        database.execute(
            "CREATE TABLE users (rider_id TEXT PRIMARY KEY, phone TEXT UNIQUE NOT NULL)"
        )
        database.execute(
            "INSERT INTO users (rider_id, phone) VALUES (?, ?)",
            ("rider-1", "+14155550101"),
        )
        database.commit()
    finally:
        database.close()

    output = tmp_path / "bundle"
    author_bundle_v2(
        source=source,
        job=_job(connector="http"),
        authoring=authoring,
        output=output,
    )

    seed_sql = (output / "seed" / "world.sql").read_text(encoding="utf-8")
    assert (
        'CREATE TABLE IF NOT EXISTS "users" '
        '("rider_id" text PRIMARY KEY, "phone" text NOT NULL, UNIQUE ("phone"));'
        in seed_sql
    )


def test_bundle_preserves_composite_sqlite_primary_key(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    database = sqlite3.connect(authoring / "world.sqlite")
    try:
        database.execute(
            "CREATE TABLE performance (client_id TEXT, period TEXT, value REAL, "
            "PRIMARY KEY (client_id, period))"
        )
        database.execute(
            "INSERT INTO performance (client_id, period, value) VALUES (?, ?, ?)",
            ("CLI-01", "YTD", 0.12),
        )
        database.commit()
    finally:
        database.close()

    output = tmp_path / "bundle"
    author_bundle_v2(
        source=source,
        job=_job(connector="http"),
        authoring=authoring,
        output=output,
    )

    seed_sql = (output / "seed" / "world.sql").read_text(encoding="utf-8")
    assert (
        'CREATE TABLE IF NOT EXISTS "performance" '
        '("client_id" text, "period" text, "value" double precision, '
        'PRIMARY KEY ("client_id", "period"));'
        in seed_sql
    )


def test_bundle_promotes_sqlite_json_text_to_postgres_jsonb(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    authoring = _authoring(tmp_path)
    database = sqlite3.connect(authoring / "world.sqlite")
    try:
        database.execute(
            "CREATE TABLE users (id TEXT PRIMARY KEY, accessibility_needs TEXT, note TEXT)"
        )
        database.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            ("rider-1", json.dumps(["wheelchair"]), "ordinary text"),
        )
        database.execute(
            "INSERT INTO users VALUES (?, ?, ?)",
            ("rider-2", json.dumps([]), "123"),
        )
        database.commit()
    finally:
        database.close()

    output = tmp_path / "bundle"
    author_bundle_v2(
        source=source,
        job=_job(connector="http"),
        authoring=authoring,
        output=output,
    )
    seed_sql = (output / "seed" / "world.sql").read_text(encoding="utf-8")
    assert (
        'CREATE TABLE IF NOT EXISTS "users" '
        '("id" text PRIMARY KEY, "accessibility_needs" jsonb, "note" text);' in seed_sql
    )
    assert "'[\"wheelchair\"]'" in seed_sql
    assert "'[]'" in seed_sql


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
    assert 'INSERT INTO public."users"' in seed_sql
    assert 'jsonb_populate_recordset(NULL::public."users"' in seed_sql
    assert '"priority"' in seed_sql
    assert "SET session_replication_role = replica;" in seed_sql
    assert "SET session_replication_role = origin;" in seed_sql
    assert "schema.sql" in manifest.provenance.adopted_files
    assert "store.json" in manifest.provenance.adopted_files
