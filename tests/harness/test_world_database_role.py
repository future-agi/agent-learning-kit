"""Under per-user isolation a world's processes reach its database through a role of their own.

The `harness` role is the cluster's superuser and stays with the harness. Customer processes and
the handler child get world N's own role: it connects to world N's database only, and may read
and change its rows, nothing more. The spy tests pin what the provisioner sends; the tests at the
end run it against a real Postgres when `ALK_TEST_POSTGRES_DSN` names a superuser connection.
"""

from __future__ import annotations

import asyncio
import os
import threading
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import test_process_runtime as runtime_tests

from fi.alk.harness import process_runtime as pr
from fi.alk.harness.bundle_v2 import BaselineStrategy

_CREDENTIALS = pr.EngineCredentials("harness", "pw", world_secret="job-secret")


def _recording() -> tuple[list[str], object]:
    statements: list[str] = []

    def runner(**kwargs):
        statements.append(kwargs["statement"])
        return []

    return statements, runner


def test_a_world_s_database_opens_only_after_its_own_role_is_the_only_one_let_in():
    statements, runner = _recording()
    role = pr.world_role(_CREDENTIALS, 3)

    pr._reset_template_database(
        host="localhost",
        port=1,
        credentials=_CREDENTIALS,
        world_db="w3",
        template_db="baseline_t",
        sql_runner=runner,
        process_name="db",
        isolate=True,
        role=role,
    )

    assert statements[1:] == [
        'DROP DATABASE IF EXISTS "w3"',
        'CREATE DATABASE "w3" TEMPLATE "baseline_t" ALLOW_CONNECTIONS false',
        'REVOKE ALL ON DATABASE "w3" FROM PUBLIC',
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'alk_w3') THEN "
        'CREATE ROLE "alk_w3" LOGIN IN ROLE "alk_world_tools"; END IF; END $$',
        'ALTER ROLE "alk_w3" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE '
        f"PASSWORD '{role.password}'",
        'GRANT CONNECT ON DATABASE "w3" TO "alk_w3"',
        'ALTER DATABASE "w3" ALLOW_CONNECTIONS true',
    ]


def test_with_one_fixed_user_a_world_s_database_resets_as_before():
    statements, runner = _recording()

    pr._reset_template_database(
        host="localhost",
        port=1,
        credentials=_CREDENTIALS,
        world_db="w0",
        template_db="baseline_t",
        sql_runner=runner,
        process_name="db",
    )

    assert statements[1:] == [
        'DROP DATABASE IF EXISTS "w0"',
        'CREATE DATABASE "w0" TEMPLATE "baseline_t"',
    ]


def test_an_authoring_lane_is_the_harness_s_alone():
    provider, runtime, statements = runtime_tests._clone_provider(
        [BaselineStrategy.TEMPLATE_DATABASE]
    )
    provider._context.require_declared_user = True
    runtime = runtime.model_copy(
        update={
            "endpoints": {
                **runtime.endpoints,
                "db": runtime.endpoints["db"].model_copy(
                    update={"tool_address": "postgresql://alk_w0:x@localhost:14000/w0"}
                ),
            }
        }
    )

    lane = asyncio.run(provider.clone_world_database(runtime, "lane3"))

    assert 'REVOKE ALL ON DATABASE "lane3" FROM PUBLIC' in statements
    assert not [s for s in statements if "GRANT" in s or "ROLE" in s]
    # The world role's address would still name w0: a handler child on a lane has none.
    assert lane.endpoints["db"].tool_address is None


def test_each_world_s_role_has_a_password_of_its_own():
    first, second = pr.world_role(_CREDENTIALS, 0), pr.world_role(_CREDENTIALS, 1)

    assert (first.username, second.username) == ("alk_w0", "alk_w1")
    assert first.password != second.password
    assert pr.world_role(_CREDENTIALS, 0) == first  # derived, never stored
    other_job = pr.EngineCredentials("harness", "pw", world_secret="another-job")
    assert pr.world_role(other_job, 0).password != first.password
    assert "job-secret" not in repr(_CREDENTIALS)
    with pytest.raises(pr.ProcessRuntimeError):
        pr.world_role(pr.EngineCredentials("harness", "pw"), 0)


def test_customer_processes_get_the_world_s_role_only_under_isolation():
    manifest = runtime_tests._manifest()
    credentials = pr.generate_engine_credentials(manifest)
    plan = pr.plan_ports(manifest, instances=2)

    isolated = pr.build_endpoints(
        manifest,
        world_index=1,
        port_plan=plan,
        credentials=credentials,
        world_roles=True,
    )
    fixed = pr.build_endpoints(
        manifest, world_index=1, port_plan=plan, credentials=credentials
    )

    database = isolated["database"]
    assert urlsplit(database.address).username == "harness"
    assert urlsplit(database.tool_address).username == "alk_w1"
    assert urlsplit(database.tool_address).path == "/w1"
    addresses = pr.configuration_addresses_from_endpoints(isolated)
    assert addresses["DATABASE_URL"] == database.tool_address
    assert fixed["database"].tool_address is None
    assert pr.configuration_addresses_from_endpoints(fixed)["DATABASE_URL"] == (
        fixed["database"].address
    )


def test_the_baseline_grants_its_rows_to_the_group_before_it_is_sealed(tmp_path: Path):
    manifest = runtime_tests._manifest(
        lambda body: {
            **body,
            "seed": {
                "stores": [
                    {
                        **body["seed"]["stores"][0],
                        "migrations": ["db/schema.sql"],
                        "seed_files": [],
                    }
                ]
            },
        }
    )
    spy = runtime_tests.SqlSpy(seeded_tables={"alk_baseline_postgres": {"riders"}})
    context = runtime_tests._spawn_context(
        manifest,
        bundle_dir=tmp_path,
        sql_runner=spy,
        sync_run=runtime_tests._fake_sync_run,
        work_directory=tmp_path,
    )

    pr.freeze_baseline(
        manifest,
        bundle_digest=manifest.digest,
        context=replace(
            context,
            require_declared_user=True,
            user_resolver=runtime_tests._fake_user_resolver({"svc-data": (4444, 5555)}),
            chown=lambda path, uid, gid: None,
        ),
    )

    statements = [statement for _, statement in spy.calls]
    sealed = next(i for i, s in enumerate(statements) if "IS_TEMPLATE" in s)
    granted = next(i for i, s in enumerate(statements) if "GRANT SELECT, INSERT" in s)
    group = next(i for i, s in enumerate(statements) if "NOLOGIN" in s)
    assert group < granted < sealed
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in statements[:sealed]
    assert "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC" in statements[:sealed]
    assert not [s for s in statements if "GRANT CREATE" in s or "GRANT ALL" in s]


def test_isolation_refuses_a_postgres_engine_its_worlds_cannot_be_scoped_on():
    datadir = runtime_tests._manifest(
        lambda body: {
            **body,
            "seed": {
                "stores": [
                    {
                        **body["seed"]["stores"][0],
                        "baseline": {
                            **body["seed"]["stores"][0]["baseline"],
                            "strategy": "datadir_copy",
                        },
                    }
                ]
            },
        }
    )

    with pytest.raises(pr.ProcessRuntimeError) as raised:
        pr._require_template_world_databases(datadir)

    assert raised.value.code == "internal_world_database_unscoped"
    pr._require_template_world_databases(runtime_tests._manifest())


# --- against a real Postgres (set ALK_TEST_POSTGRES_DSN to a superuser connection) ------------

_REAL = os.environ.get("ALK_TEST_POSTGRES_DSN")
real_postgres = pytest.mark.skipif(
    not _REAL, reason="set ALK_TEST_POSTGRES_DSN to a superuser DSN"
)


@pytest.fixture
def worlds():
    """A sealed baseline and two worlds cloned from it, the way per-user provisioning makes them."""
    import psycopg

    parts = urlsplit(_REAL)
    superuser = pr.EngineCredentials(
        parts.username, parts.password or "", world_secret=uuid.uuid4().hex
    )
    host, port = parts.hostname or "localhost", parts.port or 5432
    baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"

    def run(dbname: str, statement: str) -> None:
        pr.default_sql_runner(
            host=host,
            port=port,
            user=superuser.username,
            password=superuser.password,
            dbname=dbname,
            statement=statement,
        )

    run("postgres", f'CREATE DATABASE "{baseline}"')
    run(baseline, "CREATE TABLE riders (id serial PRIMARY KEY, name text)")
    run(baseline, "INSERT INTO riders (name) VALUES ('a'), ('b')")
    run(baseline, "CREATE SCHEMA billing")
    run(baseline, "CREATE TABLE billing.invoices (id serial PRIMARY KEY, amount int)")
    pr._grant_world_role_group(
        port=port,
        credentials=superuser,
        baseline_db=baseline,
        sql_runner=pr.default_sql_runner,
        process_name="db",
    )
    run(
        "postgres",
        f'ALTER DATABASE "{baseline}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false',
    )

    def reset(index: int) -> None:
        pr._reset_template_database(
            host=host,
            port=port,
            credentials=superuser,
            world_db=f"w{index}",
            template_db=baseline,
            sql_runner=pr.default_sql_runner,
            process_name="db",
            isolate=True,
            role=pr.world_role(superuser, index),
        )

    for index in (0, 1):
        reset(index)

    def connect(index: int, dbname: str):
        role = pr.world_role(superuser, index)
        return psycopg.connect(
            host=host,
            port=port,
            user=role.username,
            password=role.password,
            dbname=dbname,
            autocommit=True,
        )

    yield connect, reset, baseline
    for index in (0, 1):
        run("postgres", f'DROP DATABASE IF EXISTS "w{index}"')
    run("postgres", f'ALTER DATABASE "{baseline}" WITH IS_TEMPLATE false')
    run("postgres", f'DROP DATABASE IF EXISTS "{baseline}"')


@real_postgres
def test_a_world_s_role_changes_its_own_world_s_rows(worlds):
    connect, _reset, _baseline = worlds
    with connect(0, "w0") as connection:
        connection.execute("INSERT INTO riders (name) VALUES ('c')")
        connection.execute("UPDATE riders SET name = 'z' WHERE id = 1")
        connection.execute("DELETE FROM riders WHERE id = 2")
        connection.execute("INSERT INTO billing.invoices (amount) VALUES (5)")
        assert connection.execute("SELECT count(*) FROM riders").fetchone() == (2,)


@real_postgres
def test_a_world_s_role_cannot_reach_another_world_or_the_cluster(worlds):
    import psycopg

    connect, _reset, baseline = worlds
    for dbname in ("w1", "postgres", "template1", baseline):
        with pytest.raises(psycopg.OperationalError):
            connect(0, dbname).close()


@real_postgres
def test_a_world_s_role_cannot_run_programs_or_change_the_cluster(worlds):
    import psycopg

    connect, _reset, baseline = worlds
    with connect(0, "w0") as connection:
        for statement in (
            "COPY riders (name) FROM PROGRAM 'id'",
            "CREATE TABLE planted (x int)",
            "CREATE TABLE IF NOT EXISTS riders (id int)",
            "TRUNCATE riders",
            f'ALTER DATABASE "{baseline}" ALLOW_CONNECTIONS true',
            'DROP DATABASE "w1"',
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement)


@real_postgres
def test_no_moment_of_a_reset_lets_another_world_s_role_in(worlds):
    import psycopg

    connect, reset, _baseline = worlds
    stop = threading.Event()
    got_in: list[bool] = []

    def knock() -> None:
        while not stop.is_set():
            try:
                connect(0, "w1").close()
                got_in.append(True)
            except psycopg.OperationalError:
                pass

    knocker = threading.Thread(target=knock)
    knocker.start()
    try:
        for _ in range(5):
            reset(1)
    finally:
        stop.set()
        knocker.join()

    assert got_in == []
