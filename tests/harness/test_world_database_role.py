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
import time
import uuid
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
import test_process_runtime as runtime_tests

from fi.alk.harness import bundle_author_v2 as author
from fi.alk.harness import call_runner, world_door
from fi.alk.harness import process_runtime as pr
from fi.alk.harness import tool_trace_proxy as proxy
from fi.alk.harness.actors import ACTORS_DDL
from fi.alk.harness.bundle_v2 import BaselineStrategy
from fi.alk.harness.world_door import HARNESS_TABLES_DDL, PostgresLog
from fi.alk.harness.world_roles import seed_escalation_queries

_CREDENTIALS = pr.EngineCredentials("harness", "pw", world_secret="job-secret")


def _customer_sql(
    *, host: str, port: int, user: str, password: str, dbname: str, statement: str, read_only: bool = False
) -> list:
    """A source's or seed's own SQL, as `psql` runs it: on the default search path, unlike the harness's own runner."""
    with psycopg.connect(
        host=host, port=port, user=user, password=password, dbname=dbname, autocommit=True
    ) as connection:
        cursor = connection.execute(statement)
        try:
            return cursor.fetchall()
        except psycopg.ProgrammingError:
            return []


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
            chown=lambda path, uid, gid, dir_fd=None: None,
        ),
    )

    statements = [statement for _, statement in spy.calls]
    sealed = next(i for i, s in enumerate(statements) if "IS_TEMPLATE" in s)
    granted = next(i for i, s in enumerate(statements) if "GRANT SELECT, INSERT" in s)
    group = next(i for i, s in enumerate(statements) if "NOLOGIN" in s)
    assert group < granted < sealed
    # The harness's own tables are made before the grant, so it covers them in every world copy.
    made = [statements.index(statement) for statement in HARNESS_TABLES_DDL]
    assert max(made) < granted
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC" in statements[:sealed]
    assert "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC" in statements[:sealed]
    assert not [s for s in statements if "GRANT CREATE" in s or "GRANT ALL" in s]
    # Each trial's tables are made before the baseline's tables are measured, so a world counts them like its own.
    made = [i for i, s in enumerate(statements) if s.startswith("CREATE TABLE IF NOT EXISTS public._alk_trial_faults")]
    measured = next(i for i, s in enumerate(statements) if s.startswith("SELECT tablename FROM pg_tables"))
    assert made and made[0] < measured < granted


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

    parts = urlsplit(_REAL)
    superuser = pr.EngineCredentials(
        parts.username, parts.password or "", world_secret=uuid.uuid4().hex
    )
    host, port = parts.hostname or "localhost", parts.port or 5432
    baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"

    def run(dbname: str, statement: str) -> None:
        _customer_sql(
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
    # An older bundle's action log: it records actors, but not how a call was made or the fault applied on it.
    run(
        baseline,
        "CREATE TABLE _alk_tool_trace (id bigserial PRIMARY KEY, name text NOT NULL, arguments jsonb NOT NULL, "
        "result jsonb, ok boolean NOT NULL, error text, at double precision NOT NULL, "
        "actor_id text NOT NULL DEFAULT 'agent')",
    )
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

    connect, _reset, baseline = worlds
    for dbname in ("w1", "postgres", "template1", baseline):
        with pytest.raises(psycopg.OperationalError):
            connect(0, dbname).close()


@real_postgres
def test_a_world_s_role_cannot_run_programs_or_change_the_cluster(worlds):

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


# --- the world's door: its own role, and the harness's tables kept from the agent's tools ------------------------


def _door_manifest():
    """A bundle that runs its world's door as `svc-door`, the way the bundle author now seals one."""

    def mutate(body):
        body["capabilities"]["database"]["configuration_name"] = "WORLD_DATABASE_URL"
        for process in body["processes"]:
            if process.get("kind") == "source":
                process["environment"]["DATABASE_URL"] = "{{WORLD_DATABASE_URL}}"
        body["processes"].append(
            {
                "name": "tool-proxy",
                "kind": "source",
                "source_origin": "bundle",
                "working_directory": "generated/tool-proxy",
                "build_commands": [],
                "run_command": ["python3", "proxy.py"],
                "environment": {"DATABASE_URL": "{{WORLD_DOOR_DATABASE_URL}}"},
                "secret_purposes": [],
                "user": "svc-door",
                "depends_on": ["postgres"],
            }
        )
        return body

    return runtime_tests._manifest(mutate)


def test_only_the_door_gets_the_door_s_role_and_only_under_isolation(tmp_path: Path):
    manifest = _door_manifest()
    credentials = pr.generate_engine_credentials(manifest)
    plan = pr.plan_ports(manifest, instances=2)
    isolated = pr.build_endpoints(
        manifest, world_index=1, port_plan=plan, credentials=credentials, world_roles=True
    )
    database = isolated["database"]
    assert urlsplit(database.door_address).username == "alk_d1"
    assert urlsplit(database.door_address).path == "/w1"
    addresses = pr.configuration_addresses_from_endpoints(isolated)
    by_name = {process.name: process for process in manifest.processes}

    def rendered(process):
        return pr.render_environment(
            process, world_index=1, world_dir=tmp_path, port_plan=plan, configuration_addresses=addresses
        )

    assert urlsplit(rendered(by_name["tool-proxy"])["DATABASE_URL"]).username == "alk_d1"
    assert urlsplit(rendered(by_name["tools-api"])["DATABASE_URL"]).username == "alk_w1"
    # The agent's own process cannot name its way to the door's role.
    planted = by_name["tools-api"].model_copy(update={"environment": {"X": "{{WORLD_DOOR_DATABASE_URL}}"}})
    with pytest.raises(pr.ProcessRuntimeError, match="no resolution"):
        rendered(planted)
    # With one fixed user every process is the harness, as before: the door's address is the world's.
    fixed = pr.build_endpoints(manifest, world_index=1, port_plan=plan, credentials=credentials)
    assert fixed["database"].door_address is None
    assert pr.configuration_addresses_from_endpoints(fixed)["WORLD_DOOR_DATABASE_URL"] == fixed["database"].address
    # A bundle sealed before the door had a role of its own gets none.
    older = runtime_tests._manifest()
    assert (
        pr.build_endpoints(
            older, world_index=1, port_plan=pr.plan_ports(older, instances=2), credentials=credentials,
            world_roles=True,
        )["database"].door_address
        is None
    )


def test_a_door_role_s_password_never_reaches_a_kept_log():
    manifest = runtime_tests._manifest()
    credentials = pr.generate_engine_credentials(manifest)
    secrets_by_name = pr._engine_diagnostic_secrets(credentials, 2)
    for index in (0, 1):
        assert pr.door_role(credentials["postgres"], index).password in secrets_by_name.values()


def test_a_world_with_a_door_lets_in_its_own_door_role_only():
    statements, runner = _recording()
    role, door = pr.world_role(_CREDENTIALS, 3), pr.door_role(_CREDENTIALS, 3)
    assert door.username == "alk_d3" and door.password != role.password

    pr._reset_template_database(
        host="localhost", port=1, credentials=_CREDENTIALS, world_db="w3", template_db="baseline_t",
        sql_runner=runner, process_name="db", isolate=True, role=role, door=door,
    )

    opened = statements.index('ALTER DATABASE "w3" ALLOW_CONNECTIONS true')
    assert statements[opened - 3 : opened] == [
        "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'alk_d3') THEN "
        'CREATE ROLE "alk_d3" LOGIN IN ROLE "alk_door"; END IF; END $$',
        f"ALTER ROLE \"alk_d3\" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{door.password}'",
        'GRANT CONNECT ON DATABASE "w3" TO "alk_d3"',
    ]


@pytest.mark.parametrize("door", [True, False])
def test_the_baseline_takes_the_harness_tables_from_the_agent_only_with_a_door(tmp_path: Path, door: bool):
    manifest = _door_manifest() if door else runtime_tests._manifest()
    spy = runtime_tests.SqlSpy()
    context = runtime_tests._spawn_context(
        manifest, bundle_dir=tmp_path, sql_runner=spy, sync_run=runtime_tests._fake_sync_run,
        work_directory=tmp_path,
    )
    pr.freeze_baseline(
        manifest,
        bundle_digest=manifest.digest,
        context=replace(
            context,
            require_declared_user=True,
            user_resolver=runtime_tests._fake_user_resolver({"svc-data": (4444, 5555)}),
            chown=lambda path, uid, gid, dir_fd=None: None,
        ),
    )
    statements = [statement for _, statement in spy.calls]
    sealed = next(i for i, s in enumerate(statements) if "IS_TEMPLATE" in s)
    revoked = [i for i, s in enumerate(statements) if 'FROM PUBLIC, "alk_world_tools"' in s]
    door_grants = [s for s in statements if 'TO "alk_door"' in s]
    if door:
        assert revoked and revoked[0] < sealed
        assert any("CREATE ROLE \"alk_door\" NOLOGIN IN ROLE \"alk_world_tools\"" in s for s in statements)
        assert len(door_grants) == len(pr.DOOR_TABLE_RIGHTS) + 1
        assert all(statements.index(s) < sealed for s in door_grants)
    else:
        assert not revoked and not door_grants


def test_a_seed_that_left_a_way_to_more_rights_fails_the_build(tmp_path: Path):
    manifest = runtime_tests._manifest()
    spy = runtime_tests.SqlSpy(answers={seed_escalation_queries(pr.WORLD_ROLE_GROUP).our_roles: [("role alk_w0 is a member of pg_write_server_files", "")]})
    context = runtime_tests._spawn_context(
        manifest, bundle_dir=tmp_path, sql_runner=spy, sync_run=runtime_tests._fake_sync_run,
        work_directory=tmp_path,
    )
    with pytest.raises(pr.ProcessRuntimeError) as raised:
        pr.freeze_baseline(manifest, bundle_digest=manifest.digest, context=context)
    assert raised.value.code == "seed_failed"
    assert "role alk_w0 is a member of pg_write_server_files" in str(raised.value)
    assert not [s for _, s in spy.calls if "IS_TEMPLATE" in s]


# --- against a real Postgres: the door, the agent's tools and the harness's tables -----------------------------------


@pytest.fixture
def door_worlds(monkeypatch):
    """A baseline seeded as the bundle author seeds it, sealed with or without a door, and two worlds made from it.
    Yields `make(door)` returning (superuser url, world role url, door role url, reset)."""


    parts = urlsplit(_REAL)
    host, port = parts.hostname or "localhost", parts.port or 5432
    made: list[str] = []

    def make(door: bool, trial_tables_in_seed: bool = True):
        superuser = pr.EngineCredentials(parts.username, parts.password or "", world_secret=uuid.uuid4().hex)
        baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"
        made.append(baseline)

        def run(dbname: str, statement: str) -> None:
            _customer_sql(
                host=host, port=port, user=superuser.username, password=superuser.password,
                dbname=dbname, statement=statement,
            )

        run("postgres", f'CREATE DATABASE "{baseline}"')
        seed = author._TOOL_TRACE_DDL
        if not trial_tables_in_seed:
            # As a bundle sealed before its seed made each trial's tables.
            made_by_seed = "".join(f"{statement};\n" for statement in world_door.HARNESS_TABLES_DDL)
            assert made_by_seed in seed
            seed = seed.replace(made_by_seed, "")
        run(
            baseline,
            "CREATE TABLE IF NOT EXISTS harness_seed_sentinel (id text PRIMARY KEY);\n"
            + seed
            + "CREATE TABLE IF NOT EXISTS _alk_actors (id text PRIMARY KEY, kind text, side text, role text, "
            "permitted_actions jsonb, identities jsonb, on_behalf_of text);\n"
            "CREATE TABLE riders (id serial PRIMARY KEY, name text);",
        )
        pr._make_harness_tables(
            port=port, credentials=superuser, baseline_db=baseline, sql_runner=pr.default_sql_runner,
            process_name="db",
        )
        pr._grant_world_role_group(
            port=port, credentials=superuser, baseline_db=baseline, sql_runner=pr.default_sql_runner,
            process_name="db", door=door,
        )
        run("postgres", f'ALTER DATABASE "{baseline}" WITH IS_TEMPLATE true ALLOW_CONNECTIONS false')

        def reset(index: int) -> None:
            pr._reset_template_database(
                host=host, port=port, credentials=superuser, world_db=f"w{index}", template_db=baseline,
                sql_runner=pr.default_sql_runner, process_name="db", isolate=True,
                role=pr.world_role(superuser, index), door=pr.door_role(superuser, index) if door else None,
            )

        for index in (0, 1):
            reset(index)

        def url(credentials: pr.EngineCredentials, index: int = 0) -> str:
            return f"postgresql://{credentials.username}:{credentials.password}@{host}:{port}/w{index}"

        return (
            url(superuser),
            url(pr.world_role(superuser, 0)),
            url(pr.door_role(superuser, 0)),
            reset,
        )

    yield make
    world_door._TRIAL_SCHEMA_MADE.clear()
    for index in (0, 1):
        _customer_sql(
            host=host, port=port, user=parts.username, password=parts.password or "", dbname="postgres",
            statement=f'DROP DATABASE IF EXISTS "w{index}" WITH (FORCE)',
        )
    for baseline in made:
        for statement in (
            f'ALTER DATABASE "{baseline}" WITH IS_TEMPLATE false',
            f'DROP DATABASE IF EXISTS "{baseline}"',
        ):
            _customer_sql(
                host=host, port=port, user=parts.username, password=parts.password or "", dbname="postgres",
                statement=statement,
            )
    _ = psycopg


_HARNESS_TABLES = (*pr.DOOR_TABLE_RIGHTS, "harness_seed_sentinel")
_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE")


def _rights(address: str) -> dict[str, set[str]]:

    with psycopg.connect(address, autocommit=True) as connection:
        return {
            table: {
                privilege
                for privilege in _PRIVILEGES
                if connection.execute(
                    "SELECT has_table_privilege(current_user, %s, %s)", (table, privilege)
                ).fetchone()[0]
            }
            for table in _HARNESS_TABLES
        }


@real_postgres
def test_the_agent_s_role_has_no_right_on_any_harness_table_and_the_door_has_only_its_own(door_worlds):

    _superuser, world, door, reset = door_worlds(True)
    expected = {
        table: {one.strip() for one in rights.split(",")} for table, rights in pr.DOOR_TABLE_RIGHTS.items()
    } | {"harness_seed_sentinel": set()}
    for _ in range(2):
        assert _rights(world) == {table: set() for table in _HARNESS_TABLES}
        assert _rights(door) == expected
        with psycopg.connect(world, autocommit=True) as connection:
            for statement in (
                "SELECT count(*) FROM _alk_tool_trace",
                "DELETE FROM _alk_tool_trace",
                "UPDATE _alk_actors SET role = 'x'",
                "INSERT INTO _alk_trial_faults (id, faults) VALUES (2, '[]')",
                "SELECT nextval(pg_get_serial_sequence('_alk_tool_trace', 'id'))",
            ):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(statement)
            # The agent's tools still change the world itself.
            connection.execute("INSERT INTO riders (name) VALUES ('a')")
        with psycopg.connect(door, autocommit=True) as connection:
            connection.execute("INSERT INTO riders (name) VALUES ('b')")
            connection.execute(
                "INSERT INTO _alk_tool_trace (name, arguments, ok, at) VALUES ('x', '{}', true, 1)"
            )
            for statement in ("DELETE FROM _alk_tool_trace", "UPDATE _alk_actors SET role = 'x'"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    connection.execute(statement)
        # The same after the world is made again from its template.
        reset(0)
    with pytest.raises(psycopg.OperationalError):
        psycopg.connect(door.replace("/w0", "/w1")).close()


@real_postgres
def test_a_bundle_without_a_door_keeps_today_s_grants(door_worlds):
    _superuser, world, _door, _reset = door_worlds(False)
    rights = _rights(world)
    assert rights["_alk_tool_trace"] == set(_PRIVILEGES)
    assert rights["_alk_actors"] == set(_PRIVILEGES)


def _trial_state(monkeypatch, superuser: str, faults: list, **deadlines) -> None:
    from types import SimpleNamespace


    monkeypatch.setattr(call_runner, "_find_postgres_endpoint", lambda _runtime: SimpleNamespace(address=superuser))
    call_runner._write_trial_state(SimpleNamespace(), faults, **deadlines)


@real_postgres
def test_the_door_reads_this_trial_s_faults_so_a_faulted_and_a_fault_free_trial_both_write(door_worlds, monkeypatch):
    """G3: under per-user roles the door read none of the trial's faults, and every write answered 503."""


    superuser, _world, door, _reset = door_worlds(True)
    monkeypatch.setattr(proxy, "DATABASE_URL", door)
    monkeypatch.setattr(proxy, "_FAULTS_CONNECTION", None)

    class _World:
        def run(self, call):
            return world_door.Executed(200, {"ok": True})

    gate = world_door.WorldDoor(_World(), world_door.PostgresLog(door), world_door.DictRoster({}), faults=proxy._trial_faults)

    def write(key: str, action: str = "orders.cancel") -> world_door.Outcome:
        return gate.enter(world_door.ToolCall(action=action, name=action.rsplit(".", 1)[-1], effect="write", key=key))

    _trial_state(monkeypatch, superuser, [])
    assert (write("k1").outcome, write("k1").outcome) == ("committed", "duplicate")
    _trial_state(monkeypatch, superuser, [{"action": "orders.refund", "mode": "unavailable"}])
    faulted = write("k2", "orders.refund")
    assert (faulted.outcome, faulted.status, faulted.refusal_code) == ("faulted", 503, None)
    assert faulted.fault == {"action": "orders.refund", "mode": "unavailable"}
    with psycopg.connect(superuser, autocommit=True) as connection:
        logged = connection.execute("SELECT outcome, refusal_code FROM _alk_tool_trace ORDER BY at").fetchall()
        keys = connection.execute("SELECT idempotency_key FROM _alk_action_keys ORDER BY 1").fetchall()
    assert logged == [("committed", None), ("duplicate", None), ("faulted", None)]
    assert keys == [("k1",)]
    assert gate.unwritten == 0


@real_postgres
def test_a_bundle_sealed_before_its_seed_made_the_trial_tables_still_reads_faults_and_limits(door_worlds, monkeypatch):
    """G3 for a bundle without a door: its trial tables were made after the grants, so the world's role could read
    neither the faults nor a worker's limits, and every write and worker call was refused."""
    superuser, world, _door, _reset = door_worlds(False, trial_tables_in_seed=False)
    monkeypatch.setattr(proxy, "DATABASE_URL", world)
    monkeypatch.setattr(proxy, "_FAULTS_CONNECTION", None)

    class _World:
        def run(self, call):
            return world_door.Executed(200, {"ok": True})

    gate = world_door.WorldDoor(_World(), world_door.PostgresLog(world), world_door.DictRoster({}), faults=proxy._trial_faults)

    def write(key: str, action: str = "orders.cancel") -> world_door.Outcome:
        return gate.enter(world_door.ToolCall(action=action, name=action.rsplit(".", 1)[-1], effect="write", key=key))

    _trial_state(monkeypatch, superuser, [], worker_deadline_at=4102444800.0)
    assert proxy._worker_deadline() == 4102444800.0
    assert write("k1").outcome == "committed"
    _trial_state(monkeypatch, superuser, [{"action": "orders.refund", "mode": "unavailable"}])
    faulted = write("k2", "orders.refund")
    assert (faulted.outcome, faulted.status, faulted.refusal_code) == ("faulted", 503, None)


@real_postgres
def test_as_the_door_s_role_the_template_s_tables_take_a_key_claim_and_a_worker_s_run(door_worlds):
    """The door owns none of the tables, so it never makes or alters them: Postgres refuses that DDL to a non-owner
    even when the table is already there."""


    superuser, _world, door, _reset = door_worlds(True)
    log = world_door.PostgresLog(door)
    assert log.claim("k1", "agent") is None
    log.settle("k1", "agent", "committed", {"status": 200})
    world_door.mark_worker_run(door, "refund")
    with psycopg.connect(superuser, autocommit=True) as connection:
        assert connection.execute("SELECT state FROM _alk_action_keys").fetchall() == [("committed",)]
        assert connection.execute("SELECT worker FROM _alk_worker_runs").fetchall() == [("refund",)]


@real_postgres
def test_a_worker_stop_made_as_the_door_s_role_is_recorded_as_stopped(door_worlds, monkeypatch):
    import time
    from types import SimpleNamespace


    superuser, _world, door, _reset = door_worlds(True)
    _trial_state(monkeypatch, superuser, [], worker_deadline_at=time.time() - 1)
    assert world_door.claim_worker_stop(door, "refund", "", lambda: True) is True
    stops = call_runner.read_worker_stops(SimpleNamespace())
    assert [(row["worker"], row["reason"]) for row in stops] == [("refund", "deadline_passed")]


@real_postgres
@pytest.mark.parametrize(
    "planted, undo",
    [
        ('ALTER ROLE "alk_world_tools" SUPERUSER', 'ALTER ROLE "alk_world_tools" NOSUPERUSER'),
        ("CREATE ROLE planted_admin CREATEROLE", "DROP ROLE IF EXISTS planted_admin"),
        ('GRANT pg_execute_server_program TO "alk_world_tools"', 'REVOKE pg_execute_server_program FROM "alk_world_tools"'),
        (
            "CREATE FUNCTION planted() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",
            "DROP FUNCTION IF EXISTS planted()",
        ),
        (
            "CREATE FUNCTION planted_ddl() RETURNS event_trigger LANGUAGE plpgsql AS 'BEGIN END'; "
            "CREATE EVENT TRIGGER planted ON ddl_command_start EXECUTE FUNCTION planted_ddl()",
            "DROP EVENT TRIGGER IF EXISTS planted; DROP FUNCTION IF EXISTS planted_ddl()",
        ),
    ],
)
def test_a_seed_that_escalates_fails_the_build_against_a_real_server(planted, undo):
    parts = urlsplit(_REAL)
    host, port = parts.hostname or "localhost", parts.port or 5432
    superuser = pr.EngineCredentials(parts.username, parts.password or "")
    baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"

    def run(dbname: str, statement: str) -> None:
        _customer_sql(
            host=host, port=port, user=superuser.username, password=superuser.password, dbname=dbname,
            statement=statement,
        )

    def check() -> None:
        pr._refuse_seed_escalation(
            port=port, credentials=superuser, baseline_db=baseline, sql_runner=pr.default_sql_runner,
            process_name="db",
        )

    run("postgres", f'CREATE DATABASE "{baseline}"')
    run("postgres", "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'alk_world_tools') "
        "THEN CREATE ROLE alk_world_tools NOLOGIN; END IF; END $$")
    try:
        check()
        run(baseline, planted)
        with pytest.raises(pr.ProcessRuntimeError) as raised:
            check()
        assert raised.value.code == "seed_failed"
    finally:
        run(baseline, undo)
        run("postgres", f'DROP DATABASE IF EXISTS "{baseline}"')


def test_a_seed_refusal_says_what_to_change():
    def run(**kwargs):
        return [["role sneaky is a superuser or can create roles"]] if kwargs["dbname"] == "postgres" else []

    with pytest.raises(pr.ProcessRuntimeError) as raised:
        pr._refuse_seed_escalation(
            port=5432, credentials=pr.EngineCredentials("harness", "pw"), baseline_db="b", sql_runner=run,
            process_name="db",
        )

    said = str(raised.value)
    assert "role sneaky is a superuser" in said and "Change the seed so it gives no role SUPERUSER" in said


def test_the_door_s_rights_name_only_tables_the_harness_makes():
    made = world_door.ddl_columns(author._TOOL_TRACE_DDL, ACTORS_DDL, *world_door.HARNESS_TABLES_DDL)
    assert set(pr.DOOR_TABLE_RIGHTS) == set(made)


def test_migrations_run_as_the_migrations_login_and_the_seed_as_the_superuser(tmp_path: Path, monkeypatch):
    """Migrations connect as the non-superuser migrations login, the seed as the superuser, and the seed's event
    trigger is refused."""
    applied: list[tuple[str, str]] = []
    monkeypatch.setattr(
        pr,
        "apply_seed_file",
        lambda _engine, path, **kw: applied.append((Path(path).name, kw["credentials"].username)),
    )
    code = seed_escalation_queries(pr.WORLD_ROLE_GROUP).code
    from_seed = ("event trigger planted", "s1")

    def answer(dbname: str, statement: str) -> list[tuple[str, str]]:
        if statement != code or dbname == "postgres":
            return []
        return [from_seed] if any(name == "seed.sql" for name, _ in applied) else []

    spy = runtime_tests.SqlSpy()

    def run(**kwargs):
        found = answer(kwargs["dbname"], kwargs["statement"])
        return found if kwargs["statement"] == code else spy(**kwargs)

    def stores(body):
        body["seed"]["stores"][0].update(migrations=["migrate.sql"], seed_files=["seed.sql"])
        return body

    manifest = runtime_tests._manifest(stores)
    context = runtime_tests._spawn_context(
        manifest, bundle_dir=tmp_path, sql_runner=run, sync_run=runtime_tests._fake_sync_run, work_directory=tmp_path,
    )
    with pytest.raises(pr.ProcessRuntimeError) as raised:
        pr.freeze_baseline(manifest, bundle_digest=manifest.digest, context=context)
    assert applied[0] == ("migrate.sql", pr.MIGRATOR_ROLE)
    assert applied[1][0] == "seed.sql" and applied[1][1] != pr.MIGRATOR_ROLE
    assert "event trigger planted" in str(raised.value)


def test_a_migration_that_needs_a_superuser_says_where_the_statement_belongs(tmp_path: Path, monkeypatch):
    from types import SimpleNamespace

    def apply(_engine, path, **kw):
        if Path(path).name == "migrate.sql":
            raise pr.ProcessRuntimeError("seed", "seed_failed", "must be superuser to create an event trigger")

    monkeypatch.setattr(pr, "apply_seed_file", apply)
    store = SimpleNamespace(migrations=["migrate.sql"], seed_files=["seed.sql"])

    with pytest.raises(pr.ProcessRuntimeError) as raised:
        pr.apply_store_seed(
            store, engine=pr.ManagedEngine.POSTGRES, bundle_dir=tmp_path, port=5432, dbname="b",
            credentials=pr.EngineCredentials("harness", "pw"), process_name="db", sync_run=None,
            migration_credentials=pr.EngineCredentials(pr.MIGRATOR_ROLE, "pw"),
        )

    said = str(raised.value)
    assert "must be superuser" in said and "move that statement to a seed file" in said
    assert said.count("seed/seed_failed") == 1


def _migrated(migration: str, seed: str, *, alk_table: bool = False, adopt: bool = True):
    """A baseline whose migrations ran as the migrations login and whose seed ran as the superuser; the check, and a
    runner for each login."""
    parts = urlsplit(_REAL)
    host, port = parts.hostname or "localhost", parts.port or 5432
    superuser = pr.EngineCredentials(parts.username, parts.password or "")
    baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"
    checked = {"port": port, "credentials": superuser, "baseline_db": baseline, "sql_runner": pr.default_sql_runner,
               "process_name": "db"}

    def run(creds, dbname: str, statement: str):
        if statement:
            return _customer_sql(
                host=host, port=port, user=creds.username, password=creds.password, dbname=dbname, statement=statement,
            )
        return None

    run(superuser, "postgres", f'CREATE DATABASE "{baseline}"')
    run(superuser, "postgres", "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'alk_world_tools') "
        "THEN CREATE ROLE alk_world_tools NOLOGIN; END IF; END $$")
    migrator = pr.migrator_credentials(**{k: v for k, v in checked.items()})
    run(migrator, baseline, migration)
    if not adopt:
        return superuser, migrator, baseline, checked, set(), run
    pr._adopt_harness_tables(**checked)
    before = pr.seed_escalation_snapshot(**checked)
    run(superuser, baseline, seed)
    if alk_table:
        run(superuser, baseline, world_door._KEYS_TABLE_DDL)
    return superuser, migrator, baseline, checked, before, run


def build_authored_store(
    tmp_path: Path, generic: bool, schema: str, world: tuple[str, ...] = (
        "CREATE TABLE users (id TEXT, tags TEXT)", "INSERT INTO users VALUES ('user-1', 'priority')"
    ),
) -> tuple[list[str], list[str]]:
    """A bundle authored the way the platform authors one, on the generic path (`seed/source-schema.sql`) or the
    other (`seed/world.sql`), with ``schema`` as the source's own; its store then built into a fresh baseline as
    the build does: migrations as the migrations' login, the harness's tables taken over, the seed as the superuser,
    the check. Returns the store's migrations and the owner of each `_alk_*` table."""
    import sqlite3

    import test_bundle_author_v2 as author_tests

    from fi.alk.harness.bundle_author_v2 import author_bundle_v2

    source = tmp_path / "source"
    (source / "db").mkdir(parents=True)
    (source / "agent.py").write_text("print('ok')\n", encoding="utf-8")
    (source / "db" / "schema.sql").write_text(schema, encoding="utf-8")
    authoring = author_tests._authoring(tmp_path)
    (authoring / "runtime-validation.json").write_text('{"status":"certified"}\n', encoding="utf-8")
    with sqlite3.connect(authoring / "world.sqlite") as database:
        for statement in world:
            database.execute(statement)
    output = tmp_path / "bundle"
    manifest = author_bundle_v2(
        source=source,
        job=author_tests._job(connector="http", metadata={"generic_harness_v1": True} if generic else None),
        authoring=authoring,
        output=output,
    )
    store = manifest.seed.stores[0]
    parts = urlsplit(_REAL)
    superuser = pr.EngineCredentials(parts.username, parts.password or "")
    baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"
    checked = {"port": parts.port or 5432, "credentials": superuser, "baseline_db": baseline,
               "sql_runner": pr.default_sql_runner, "process_name": "db"}

    def run(dbname: str, statement: str):
        return _customer_sql(
            host=parts.hostname or "localhost", port=parts.port or 5432, user=superuser.username,
            password=superuser.password, dbname=dbname, statement=statement,
        )

    run("postgres", f'CREATE DATABASE "{baseline}"')
    try:
        before: set = set()

        def after_migrations() -> None:
            pr._adopt_harness_tables(**checked)
            before.update(pr.seed_escalation_snapshot(**checked))

        pr.apply_store_seed(
            store, engine=pr.ManagedEngine.POSTGRES, bundle_dir=output, port=checked["port"], dbname=baseline,
            credentials=superuser, process_name="db", sync_run=pr.subprocess.run,
            source_digest=manifest.provenance.source_digest, after_migrations=after_migrations,
            migration_credentials=pr.migrator_credentials(**checked),
        )
        pr._refuse_seed_escalation(**checked, before_seed=before)
        owners = run(baseline, "SELECT DISTINCT pg_get_userbyid(relowner) FROM pg_class WHERE relname LIKE '\\_alk\\_%'")
        return list(store.migrations), sorted(str(row[0]) for row in owners or [])
    finally:
        run("postgres", f'DROP DATABASE IF EXISTS "{baseline}" WITH (FORCE)')


_EVIL = "CREATE FUNCTION evil() RETURNS boolean LANGUAGE sql AS 'SELECT true'; "
_PLANTED = {
    "check": _EVIL + "CREATE TABLE _alk_trial_faults (trial_id text PRIMARY KEY, faults jsonb CHECK (evil()))",
    "default": _EVIL + "CREATE TABLE _alk_trial_faults (trial_id text PRIMARY KEY, seen boolean DEFAULT evil())",
    "domain": "CREATE FUNCTION evil(text) RETURNS boolean LANGUAGE sql AS 'SELECT true'; "
    "CREATE DOMAIN keyed AS text CHECK (evil(VALUE)); CREATE TABLE _alk_action_keys (key keyed PRIMARY KEY)",
    "index": "CREATE FUNCTION evil(text) RETURNS text LANGUAGE sql IMMUTABLE AS 'SELECT $1'; "
    "CREATE TABLE _alk_action_keys (key text PRIMARY KEY); CREATE INDEX ON _alk_action_keys (evil(key))",
    "grant": "CREATE TABLE _alk_tool_trace (id bigserial PRIMARY KEY, name text); "
    "GRANT INSERT ON _alk_tool_trace TO PUBLIC",
    "view": "CREATE TABLE riders_log (id int); CREATE VIEW _alk_tool_trace AS SELECT * FROM riders_log",
    # F2: `ANALYZE` evaluates a statistics object's expression as whoever runs it.
    "statistics": "CREATE FUNCTION evil(text) RETURNS text LANGUAGE sql IMMUTABLE AS 'SELECT $1'; "
    "CREATE TABLE _alk_action_keys (key text PRIMARY KEY); CREATE STATISTICS keyed ON (evil(key)) FROM _alk_action_keys",
    # F1: the log in an older shape with `ok` as text: a failed call is stored as 'false' and read as passed.
    "shape": "CREATE TABLE public._alk_tool_trace (id bigserial PRIMARY KEY, name text NOT NULL, arguments jsonb NOT NULL, "
    "result jsonb, ok text NOT NULL, error text, at double precision NOT NULL)",
}


@real_postgres
@pytest.mark.parametrize("planted", sorted(_PLANTED))
def test_a_harness_table_a_migration_made_that_runs_code_refuses_the_build(planted):
    """E1-a: the migrations' login makes the harness's tables (the bundle's prefix); one that would run anything but
    the system's own code when the harness or the door writes it refuses the build before it is taken over."""
    superuser, _migrator, baseline, checked, _before, run = _migrated(_PLANTED[planted], "", adopt=False)
    try:
        with pytest.raises(pr.ProcessRuntimeError) as refused:
            pr._adopt_harness_tables(**checked)
        assert "harness table" in str(refused.value)
        assert run(superuser, baseline, "SELECT count(*) FROM pg_class WHERE relname LIKE '\\_alk\\_%' "
                   "AND pg_get_userbyid(relowner) = 'alk_migrator' AND relkind IN ('r', 'v')") == [(1,)]
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
@pytest.mark.parametrize(
    "planted",
    (*sorted(_PLANTED), "catalog"),
)
def test_a_harness_table_the_seed_made_that_runs_code_refuses_the_build_whoever_owns_it(planted):
    """E1-a: a superuser-owned harness table with no trigger still refuses the build when a check, default, domain,
    index, grant or view would run code, even code planted in `pg_catalog`."""
    seed = _PLANTED.get(planted) or (
        "CREATE FUNCTION pg_catalog.evil() RETURNS boolean LANGUAGE sql AS 'SELECT true'; "
        "CREATE TABLE _alk_trial_faults (trial_id text PRIMARY KEY, seen boolean DEFAULT pg_catalog.evil())"
    )
    superuser, _migrator, baseline, checked, before, run = _migrated("", seed)
    try:
        with pytest.raises(pr.ProcessRuntimeError) as refused:
            pr._refuse_seed_escalation(**checked, before_seed=before)
        # A function planted in `pg_catalog` is refused first, before anything reads the harness tables.
        assert ("pg_catalog the system did not make" if planted == "catalog" else "harness table") in str(refused.value)
    finally:
        if planted == "catalog":
            run(superuser, baseline, "DROP FUNCTION IF EXISTS pg_catalog.evil() CASCADE")
        _dropped(superuser, baseline, run)


@real_postgres
@pytest.mark.parametrize(("generic", "migration"), ((True, "seed/source-schema.sql"), (False, "seed/world.sql")))
def test_both_authoring_paths_build_with_the_harness_tables_in_their_migration(tmp_path: Path, generic, migration):
    """The bundle's harness prefix rides in its migration file on both paths: the migrations' login makes the
    harness's tables, the harness takes them over, and the build passes with every one of them the superuser's."""
    migrations, owners = build_authored_store(
        tmp_path, generic, "CREATE TABLE users (id TEXT PRIMARY KEY, tags TEXT);\n"
    )

    assert migrations == [migration]
    assert owners == [urlsplit(_REAL).username]


def _dropped(superuser, baseline, run) -> None:
    run(superuser, "postgres", 'ALTER ROLE "alk_world_tools" NOCREATEDB')
    run(superuser, "postgres", f'DROP DATABASE IF EXISTS "{baseline}"')


@real_postgres
@pytest.mark.parametrize(
    "migration, seed, refused",
    [
        # Supabase-style: a migration's SECURITY DEFINER auth helper builds; it runs as the migrations login.
        (
            "CREATE SCHEMA auth; CREATE FUNCTION auth.uid() RETURNS text LANGUAGE sql SECURITY DEFINER "
            "AS $$ SELECT current_setting('request.jwt.claim.sub', true) $$",
            "",
            None,
        ),
        # RESET ROLE cannot escape the migrations login: what it makes after is still that login's.
        ("RESET ROLE; CREATE FUNCTION reset_then() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'", "", None),
        # One the seed adds, as the superuser, is refused.
        ("", "CREATE FUNCTION seed_added() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'", "seed_added()"),
        # A seed that hands a migration's function to the superuser and grants it to PUBLIC is refused.
        (
            "CREATE FUNCTION mig_revoked() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'; "
            "REVOKE EXECUTE ON FUNCTION mig_revoked() FROM PUBLIC",
            "ALTER FUNCTION mig_revoked() OWNER TO CURRENT_USER; GRANT EXECUTE ON FUNCTION mig_revoked() TO PUBLIC",
            "mig_revoked()",
        ),
        # A seed that alters the world group is refused.
        ("", 'ALTER ROLE "alk_world_tools" CREATEDB', "alk_world_tools"),
    ],
)
def test_the_seed_check_refuses_what_runs_with_more_rights_than_its_caller(migration, seed, refused):
    superuser, _migrator, baseline, checked, before, run = _migrated(migration, seed)
    try:
        if refused is None:
            pr._refuse_seed_escalation(**checked, before_seed=before)
        else:
            with pytest.raises(pr.ProcessRuntimeError) as raised:
                pr._refuse_seed_escalation(**checked, before_seed=before)
            assert raised.value.code == "seed_failed" and refused in str(raised.value)
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_a_migration_s_security_definer_function_cannot_reach_the_harness_tables():
    """A migration's SECURITY DEFINER function runs as the migrations login, which has no right on a harness table:
    called by anyone, it is refused there. SET ROLE to the superuser is refused to the migrations login too."""
    reads = (
        "CREATE FUNCTION peek() RETURNS bigint LANGUAGE plpgsql SECURITY DEFINER "
        "AS $$ BEGIN RETURN (SELECT count(*) FROM _alk_action_keys); END $$"
    )
    superuser, migrator, baseline, checked, before, run = _migrated(reads, "", alk_table=True)
    try:
        pr._refuse_seed_escalation(**checked, before_seed=before)
        with pytest.raises(Exception) as denied:
            run(superuser, baseline, "SET ROLE alk_world_tools; SELECT peek()")
        assert "permission denied" in str(denied.value)
        with pytest.raises(Exception) as escaped:
            run(migrator, baseline, f'SET ROLE "{superuser.username}"')
        assert "permission denied" in str(escaped.value)
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
@pytest.mark.parametrize(
    "migration, refused",
    [
        # E1: a migration makes a harness table first, with a trigger the superuser's write would run.
        (
            "CREATE TABLE _alk_trial_faults (id int PRIMARY KEY, faults jsonb); "
            "CREATE FUNCTION grab() RETURNS trigger LANGUAGE plpgsql AS "
            "$$ BEGIN EXECUTE 'ALTER ROLE alk_world_tools SUPERUSER'; RETURN NEW; END $$; "
            "CREATE TRIGGER grab BEFORE INSERT OR UPDATE ON _alk_trial_faults FOR EACH ROW EXECUTE FUNCTION grab()",
            "harness table",
        ),
        # E1 without a trigger: a harness table the migrations own is refused too.
        # The bundle's own prefix makes the harness's tables in its migration: a plain one is taken over.
        ("CREATE TABLE _alk_tool_trace (id bigserial PRIMARY KEY, name text)", None),
        # A source's own functions named like system ones build: every session of the harness's and the door's runs on
        # the system's own search path, where they are never found. Each raises if anything of the harness's runs it.
        (
            "CREATE FUNCTION public.unnest(text[]) RETURNS SETOF text LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'shadow ran'; END $$",
            None,
        ),
        (
            "CREATE FUNCTION public.format(text, text) RETURNS text LANGUAGE plpgsql AS "
            "$$ BEGIN RAISE EXCEPTION 'shadow ran'; END $$",
            None,
        ),
        # A customer's own `gen_random_uuid()` shim, as a source written for an older Postgres keeps one.
        ("CREATE FUNCTION public.gen_random_uuid() RETURNS uuid LANGUAGE sql AS 'SELECT pg_catalog.gen_random_uuid()'",
         None),
        # Trusted extensions whose functions share system names (pgcrypto's gen_random_uuid, citext's lower) build.
        ("CREATE EXTENSION IF NOT EXISTS pgcrypto; CREATE EXTENSION IF NOT EXISTS citext", None),
    ],
)
def test_harness_tables_and_system_names_are_the_harness_s_alone(migration, refused):
    # The source's statements, then the bundle's own prefix in the same migration file, as both authoring paths write.
    superuser, _migrator, baseline, checked, _before, run = _migrated(
        migration + ";\n" + author._TOOL_TRACE_DDL, "", adopt=False
    )

    def build() -> None:
        pr._adopt_harness_tables(**checked)
        pr._refuse_seed_escalation(**checked, before_seed=pr.seed_escalation_snapshot(**checked))
        pr._make_harness_tables(**checked)
        pr._grant_world_role_group(**checked, door=True)

    try:
        if refused is None:
            build()
            assert run(superuser, baseline, "SELECT count(*) FROM pg_class WHERE relname LIKE '\\_alk\\_%' "
                       "AND pg_get_userbyid(relowner) = 'alk_migrator'") == [(0,)]
        else:
            with pytest.raises(pr.ProcessRuntimeError) as raised:
                build()
            assert refused in str(raised.value)
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_a_migration_s_function_cannot_drop_a_harness_table_through_public():
    """E4: the migrations login may create in `public` but does not own it, so its SECURITY DEFINER function cannot
    drop and remake a harness table, called by anyone."""
    swap = (
        "CREATE FUNCTION swap() RETURNS void LANGUAGE plpgsql SECURITY DEFINER AS $$ BEGIN "
        "DROP TABLE public._alk_tool_trace; CREATE TABLE public._alk_tool_trace (id int); "
        "GRANT ALL ON public._alk_tool_trace TO PUBLIC; END $$"
    )
    superuser, _migrator, baseline, checked, before, run = _migrated(
        swap, "CREATE TABLE _alk_tool_trace (id bigserial PRIMARY KEY, name text)"
    )
    try:
        pr._refuse_seed_escalation(**checked, before_seed=before)
        with pytest.raises(Exception) as denied:
            run(superuser, baseline, "SET ROLE alk_world_tools; SELECT swap()")
        assert "must be owner" in str(denied.value)
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_a_customer_trigger_fired_by_the_world_s_rows_cannot_forge_the_call_log(door_worlds):
    """E2: the door's role may write the call log; a customer trigger on a world table, fired by a row the door
    writes, runs as whoever wrote the row. Written with the world's role (as the door's processes now do), the trigger
    is refused the log; written with the door's, it could forge it."""
    superuser, world, door, _reset = door_worlds(True)
    forge = (
        "CREATE FUNCTION forge() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
        "INSERT INTO _alk_tool_trace (name, arguments, ok, at) VALUES ('forged', '{}'::jsonb, true, 0); "
        "RETURN NEW; END $$; "
        "CREATE TRIGGER forge AFTER INSERT ON riders FOR EACH ROW EXECUTE FUNCTION forge()"
    )
    with psycopg.connect(superuser, autocommit=True) as connection:
        connection.execute(forge)

    with pytest.raises(Exception) as refused:
        world_door.PostgresRecords(world)("INSERT INTO riders (name) VALUES (%s)", ["ada"])
    assert "permission denied" in str(refused.value)
    with psycopg.connect(superuser, autocommit=True) as connection:
        assert connection.execute("SELECT count(*) FROM _alk_tool_trace WHERE name = 'forged'").fetchone() == (0,)
    # The door's own role still could: the reason its processes write the world's rows with the world's role.
    world_door.PostgresRecords(door)("INSERT INTO riders (name) VALUES (%s)", ["bo"])
    with psycopg.connect(superuser, autocommit=True) as connection:
        assert connection.execute("SELECT count(*) FROM _alk_tool_trace WHERE name = 'forged'").fetchone() == (1,)


def _runner_doors(superuser: str, world: str) -> dict[str, object]:
    from fi.alk.harness import chat_call_runner

    return {
        "record_door": call_runner.record_door(superuser, records_dsn=world),
        "runner_door": call_runner.runner_door("http://127.0.0.1:9", superuser, 1.0, records_dsn=world),
        "chat_door": chat_call_runner.chat_door(object(), superuser, {}, [], records_dsn=world),
    }


@real_postgres
def test_an_actor_s_record_change_in_the_runner_s_doors_runs_as_the_world_s_role(door_worlds):
    """E2-r: the runner's own doors change a world's rows with the world's role, so a customer trigger fired by an
    actor's row cannot write the harness tables."""
    superuser, world, _door, _reset = door_worlds(True)
    with psycopg.connect(superuser, autocommit=True) as connection:
        connection.execute(
            "CREATE FUNCTION forge() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
            "INSERT INTO _alk_tool_trace (name, arguments, ok, at) VALUES ('forged', '{}'::jsonb, true, 0); "
            "RETURN NEW; END $$; "
            "CREATE TRIGGER forge AFTER INSERT ON riders FOR EACH ROW EXECUTE FUNCTION forge()"
        )

    for name, door in _runner_doors(superuser, world).items():
        with pytest.raises(Exception) as refused:
            door.executor.records.execute("INSERT INTO riders (name) VALUES (%s)", ["ada"])
        assert "permission denied" in str(refused.value), name
    with psycopg.connect(superuser, autocommit=True) as connection:
        assert connection.execute("SELECT count(*) FROM _alk_tool_trace WHERE name = 'forged'").fetchone() == (0,)


def test_the_runner_hands_its_record_door_the_world_s_role_address(monkeypatch, tmp_path: Path):
    from types import SimpleNamespace

    made: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        call_runner, "record_door", lambda dsn, bundle_dir=None, records_dsn=None: made.append((dsn, records_dsn))
    )
    monkeypatch.setattr(call_runner, "_bundle_contract", lambda bundle_dir: {})
    monkeypatch.setattr(call_runner, "DoorActs", lambda door, contract: door)
    monkeypatch.setattr(call_runner, "build_dispatcher", lambda *args, **kwargs: "dispatcher")
    monkeypatch.setattr(call_runner, "_world_records", lambda runtime: None)
    monkeypatch.setattr(call_runner, "_world_columns", lambda runtime: None)
    database = SimpleNamespace(protocol="postgres", address="superuser-url", tool_address="world-url")
    runtime = SimpleNamespace(endpoints={"db": database, "world_door": SimpleNamespace(protocol="http")})

    assert call_runner._actor_dispatcher({"actors": [{"id": "a"}]}, tmp_path, runtime, "t1", 1.0) == "dispatcher"
    assert made == [("superuser-url", "world-url")]


def test_every_door_process_writes_the_world_s_rows_with_the_world_s_role(monkeypatch):
    import importlib

    from fi.alk.harness import bundle_author_v2 as author_module
    from fi.alk.harness import in_process_door, mcp_toolbox_adapter, tool_trace_proxy

    for process in (
        author_module._tool_proxy_process(),
        author_module._world_door_process([]),
        author_module._mcp_toolbox_process(),
    ):
        assert process.environment["WORLD_RECORDS_DATABASE_URL"] == "{{WORLD_DATABASE_URL}}"
        assert process.environment["DATABASE_URL"] == "{{WORLD_DOOR_DATABASE_URL}}"
    monkeypatch.setenv("DATABASE_URL", "door-url")
    monkeypatch.setenv("WORLD_RECORDS_DATABASE_URL", "world-url")
    try:
        assert importlib.reload(tool_trace_proxy).RECORDS_DATABASE_URL == "world-url"
        assert importlib.reload(in_process_door).RECORDS_DATABASE_URL == "world-url"
    finally:
        monkeypatch.undo()
        importlib.reload(tool_trace_proxy)
        importlib.reload(in_process_door)
    opened: list[str] = []

    class _Connection:
        def __init__(self, url):
            opened.append(url)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def cursor(self):
            return self

        def execute(self, *args, **kwargs):
            return None

        description = None
        rowcount = 1

    import psycopg as real_psycopg

    monkeypatch.setattr(real_psycopg, "connect", lambda url, **kwargs: _Connection(url))
    mcp_toolbox_adapter._execute(
        "add", {"statement": "INSERT INTO riders (name) VALUES (%s)", "parameters": [{"name": "name"}]},
        {"name": "ada"}, database_url="door-url", trace_path=Path("/dev/null"), records_url="world-url",
    )
    assert opened == ["world-url", "door-url"]


@real_postgres
def test_a_domain_typed_shadow_a_migration_planted_never_runs_in_the_harness_s_own_sessions():
    """E3: a migration's `public.format(public.dt, text)` takes only a domain over a system type, so no name check
    can call it a shadow, and on the default search path it wins `format('...', text)` in the harness's grants. The
    harness's sessions run on the system's own path, where it is never found."""
    shadow = (
        "CREATE DOMAIN public.dt AS text; CREATE FUNCTION public.format(public.dt, text) RETURNS text "
        "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'shadow ran'; END $$"
    )
    superuser, _migrator, baseline, checked, before, run = _migrated(author._TOOL_TRACE_DDL + shadow, "")
    try:
        pr._refuse_seed_escalation(**checked, before_seed=before)
        pr._make_harness_tables(**checked)
        pr._grant_world_role_group(**checked, door=True)
        assert run(superuser, baseline, f"SELECT has_table_privilege('{pr.DOOR_ROLE_GROUP}', "
                   "'public._alk_tool_trace', 'INSERT')") == [(True,)]
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_harness_tables_in_a_schema_named_after_the_superuser_never_stand_in_for_the_harness_s_own():
    """E1-b: a migration makes a schema named as the superuser (first on its default path, `$user`) and harness
    tables in it. The harness still makes and writes its own in `public`."""
    name = urlsplit(_REAL).username
    planted = (
        f'CREATE SCHEMA "{name}"; '
        f'CREATE TABLE "{name}"._alk_trial_faults (id INT PRIMARY KEY, faults JSONB NOT NULL); '
        f'CREATE TABLE "{name}"._alk_tool_trace (id bigserial PRIMARY KEY, name text)'
    )
    superuser, _migrator, baseline, checked, before, run = _migrated(planted + ";\n" + author._TOOL_TRACE_DDL, "")
    try:
        pr._refuse_seed_escalation(**checked, before_seed=before)
        pr._make_harness_tables(**checked)
        made = run(superuser, baseline, "SELECT to_regclass('public._alk_trial_faults') IS NOT NULL")
        assert made == [(True,)]
        url = urlunsplit(urlsplit(_REAL)._replace(path=f"/{baseline}"))
        world_door.PostgresLog(url).write(
            {"name": "x", "arguments": {}, "result": None, "ok": True, "error": "", "at": 1.0, "action_id": "a1",
             "outcome": "committed", "actor_id": "agent"}
        )
        counts = run(superuser, baseline, f'SELECT (SELECT count(*) FROM public._alk_tool_trace), '
                     f'(SELECT count(*) FROM "{name}"._alk_tool_trace)')
        assert counts == [(1, 0)]
    finally:
        run(superuser, baseline, f'DROP SCHEMA IF EXISTS "{name}" CASCADE')
        _dropped(superuser, baseline, run)


def test_a_logged_ok_counts_only_a_real_true():
    """F1: a log kept with `ok` as text holds 'false' for a failed call; it is never read as committed."""
    assert world_door.is_committed({"ok": "false"}) is False
    assert world_door.is_committed({"ok": "f"}) is False
    assert world_door.is_committed({"ok": False}) is False
    assert world_door.is_committed({"ok": "true"}) is True
    assert world_door.is_committed({"ok": True}) is True
    assert world_door.is_committed({}) is True


@real_postgres
def test_a_log_in_an_older_shape_with_a_text_ok_refuses_the_build_and_names_the_migrations():
    """F1: a migration makes the call log first with `ok` as text; the bundle's prefix keeps it (IF NOT EXISTS) and
    the door would store a failed call as 'false'. Its columns must be the harness's own, of the harness's types."""
    planted = (
        "CREATE TABLE public._alk_tool_trace (id bigserial PRIMARY KEY, name text NOT NULL, arguments jsonb NOT NULL, "
        "result jsonb, ok text NOT NULL, error text, at double precision NOT NULL);\n"
    )
    superuser, _migrator, baseline, checked, _before, run = _migrated(planted + author._TOOL_TRACE_DDL, "", adopt=False)
    try:
        with pytest.raises(pr.ProcessRuntimeError) as refused:
            pr._adopt_harness_tables(**checked)
        assert "column ok text, which the harness makes as boolean" in str(refused.value)
        assert "Change the migrations" in str(refused.value)
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_a_seed_that_retypes_a_harness_column_refuses_the_build():
    superuser, _migrator, baseline, checked, before, run = _migrated(
        author._TOOL_TRACE_DDL, "ALTER TABLE public._alk_tool_trace ALTER COLUMN ok TYPE text"
    )
    try:
        with pytest.raises(pr.ProcessRuntimeError) as refused:
            pr._refuse_seed_escalation(**checked, before_seed=before)
        assert "column ok text" in str(refused.value)
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_a_function_the_seed_put_in_pg_catalog_refuses_the_build_before_any_harness_session_finds_it():
    """F4: the seed runs as the superuser and may create in `pg_catalog`; a session on the system's own search path
    finds it first (`format(text, text)` wins the harness's grant)."""
    superuser, _migrator, baseline, checked, before, run = _migrated(
        author._TOOL_TRACE_DDL,
        "CREATE FUNCTION pg_catalog.format(text, text) RETURNS text LANGUAGE plpgsql AS "
        "$$ BEGIN RAISE EXCEPTION 'plant ran'; END $$",
    )
    try:
        with pytest.raises(pr.ProcessRuntimeError) as refused:
            pr._refuse_seed_escalation(**checked, before_seed=before)
        assert "function in pg_catalog the system did not make: format" in str(refused.value)
        assert "plant ran" not in str(refused.value)
    finally:
        run(superuser, baseline, "DROP FUNCTION IF EXISTS pg_catalog.format(text, text)")
        _dropped(superuser, baseline, run)


def test_with_world_roles_an_actor_s_record_change_never_runs_as_the_superuser(monkeypatch, tmp_path: Path):
    """With per-user isolation every world has a role of its own: a world without one is refused actors, and no door
    falls back to the superuser's address. Without world roles (one fixed user) nothing changes."""
    from types import SimpleNamespace

    from fi.alk.harness import chat_call_runner, privilege

    monkeypatch.setattr(privilege, "isolation_mode", lambda: privilege.IsolationMode.PER_USER)
    with pytest.raises(RuntimeError, match="no_world_role"):
        call_runner.record_door("superuser-url")
    with pytest.raises(RuntimeError, match="no_world_role"):
        call_runner.runner_door("http://127.0.0.1:9", "superuser-url", 1.0)
    assert chat_call_runner.chat_door(object(), "superuser-url", {}, []).executor.records is None
    database = SimpleNamespace(protocol="postgres", address="superuser-url", tool_address=None)
    runtime = SimpleNamespace(endpoints={"db": database, "world_door": SimpleNamespace(protocol="http")})
    why: list[tuple[str, str]] = []
    assert call_runner._actor_dispatcher({"actors": [{"id": "a"}]}, tmp_path, runtime, "t1", 1.0, why_not=why) is None
    assert why and why[0][0] == "no_world_role"

    monkeypatch.setattr(privilege, "isolation_mode", lambda: privilege.IsolationMode.FIXED_USER)
    assert call_runner.record_door("superuser-url").executor.records.execute.dsn == "superuser-url"


def test_actors_read_a_world_s_rows_with_its_own_role(monkeypatch):
    from types import SimpleNamespace

    from fi.alk.harness.world.stores import postgres as stores

    made: list[str] = []
    monkeypatch.setattr(stores.AttachedPostgresStore, "__init__", lambda self, dsn: made.append(dsn))
    runtime = SimpleNamespace(
        endpoints={"db": SimpleNamespace(protocol="postgres", address="superuser-url", tool_address="world-url")}
    )
    call_runner._world_records(runtime)
    call_runner._world_columns(runtime)
    assert made == ["world-url", "world-url"]


def _pg_dump_schema(owner: str) -> str:
    """A schema file as `pg_dump --schema-only` writes it, owners and the plpgsql comment included."""
    return (
        "SET statement_timeout = 0;\n"
        "SELECT pg_catalog.set_config('search_path', '', false);\n"
        "COMMENT ON EXTENSION plpgsql IS 'PL/pgSQL procedural language';\n"
        "CREATE TABLE public.items (id integer NOT NULL, name text);\n"
        f"ALTER TABLE public.items OWNER TO {owner};\n"
        "CREATE SEQUENCE public.items_id_seq AS integer START WITH 1 INCREMENT BY 1;\n"
        f"ALTER SEQUENCE public.items_id_seq OWNER TO {owner};\n"
        "CREATE FUNCTION public.note() RETURNS text LANGUAGE plpgsql AS $_$\n"
        "BEGIN\n"
        "-- ALTER TABLE public.items OWNER TO someone;\n"
        "RETURN 'ALTER TABLE public.items OWNER TO someone;';\n"
        "END\n"
        "$_$;\n"
        f"ALTER FUNCTION public.note() OWNER TO {owner};\n"
    )


def test_a_dump_s_ownership_statements_are_left_out_and_function_bodies_kept(tmp_path: Path):
    file = tmp_path / "schema.sql"
    file.write_text(_pg_dump_schema("postgres"), encoding="utf-8")

    filtered, skipped = pr._without_ownership(file)

    text = filtered.read_text(encoding="utf-8")
    assert skipped == 4
    assert "-- left out by the harness (ownership): ALTER TABLE public.items OWNER TO postgres;" in text
    assert "RETURN 'ALTER TABLE public.items OWNER TO someone;';" in text
    assert "\nALTER TABLE public.items OWNER TO postgres;" not in text
    assert pr._without_ownership(tmp_path / "schema.json") == (tmp_path / "schema.json", 0)


_NOT_OWNERSHIP = (
    "ALTER TABLE accounts RENAME COLUMN owner TO owner_id;\n",
    "ALTER TABLE public.a OWNER TO x; DROP TABLE public.b;\n",
    "ALTER TABLE public.a RENAME TO c; ALTER TABLE public.b OWNER TO x;\n",
    "COMMENT ON EXTENSION plpgsql IS 'a'; DROP TABLE public.b;\n",
    "alter table public.a owner to x;\n",
)


@pytest.mark.parametrize("line", _NOT_OWNERSHIP)
def test_a_line_that_is_not_one_of_pg_dump_s_ownership_shapes_is_kept(tmp_path: Path, line: str):
    file = tmp_path / "schema.sql"
    file.write_text(line, encoding="utf-8")

    assert pr._without_ownership(file) == (file, 0)


@pytest.mark.parametrize(
    "opening",
    (
        "-- a note about $b$ prices\n",
        "INSERT INTO prices (label) VALUES ('costs $$');\n",
        "/* $x$ in a /* nested */ comment */\n",
        'CREATE TABLE "odd$t$name" (id int);\n',
    ),
)
def test_a_dollar_sign_in_a_comment_string_or_name_does_not_hide_later_ownership_lines(tmp_path: Path, opening: str):
    file = tmp_path / "schema.sql"
    file.write_text(opening + "ALTER TABLE public.items OWNER TO postgres;\n", encoding="utf-8")

    filtered, skipped = pr._without_ownership(file)

    assert skipped == 1
    assert filtered.read_text(encoding="utf-8").startswith(opening)


def test_pg_dump_s_other_ownership_shapes_are_left_out(tmp_path: Path):
    file = tmp_path / "schema.sql"
    file.write_text(
        'ALTER FUNCTION public.f(integer, character varying, "Odd"[]) OWNER TO "the owner";\n'
        "ALTER MATERIALIZED VIEW public.m OWNER TO postgres;\n"
        "ALTER OPERATOR public.=== (integer, integer) OWNER TO postgres;\n"
        "ALTER OPERATOR CLASS public.c USING btree OWNER TO postgres;\n"
        "ALTER SCHEMA app OWNER TO postgres;\n",
        encoding="utf-8",
    )

    assert pr._without_ownership(file)[1] == 5


@real_postgres
def test_a_migration_renaming_an_owner_column_builds_and_keeps_its_rename(tmp_path: Path):
    superuser, migrator, baseline, checked, before, run = _migrated("", "")
    try:
        file = tmp_path / "schema.sql"
        file.write_text(
            "CREATE TABLE accounts (id integer, owner text);\n"
            "INSERT INTO accounts VALUES (1, 'costs $$');\n"
            "-- $b$\n"
            "ALTER TABLE accounts RENAME COLUMN owner TO owner_id;\n"
            f'ALTER TABLE public.accounts OWNER TO "{superuser.username}";\n',
            encoding="utf-8",
        )
        filtered, skipped = pr._without_ownership(file)
        assert skipped == 1
        run(migrator, baseline, filtered.read_text(encoding="utf-8"))
        pr._refuse_seed_escalation(**checked, before_seed=before)
        columns = run(
            superuser, baseline,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'accounts' ORDER BY ordinal_position",
        )
        assert columns == [("id",), ("owner_id",)]
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_a_pg_dump_schema_with_owners_builds_as_the_migrations_login(tmp_path: Path):
    superuser, migrator, baseline, checked, before, run = _migrated("", "")
    try:
        dumped = _pg_dump_schema(f'"{superuser.username}"')
        with pytest.raises(Exception):
            run(migrator, baseline, dumped)
        file = tmp_path / "schema.sql"
        file.write_text(dumped, encoding="utf-8")
        filtered, _ = pr._without_ownership(file)
        run(migrator, baseline, filtered.read_text(encoding="utf-8"))
        pr._refuse_seed_escalation(**checked, before_seed=before)
        owner = run(superuser, baseline, "SELECT tableowner FROM pg_tables WHERE tablename = 'items'")
        assert owner == [(pr.MIGRATOR_ROLE,)]
    finally:
        _dropped(superuser, baseline, run)


@real_postgres
def test_a_grant_to_public_changes_a_function_s_fingerprint():
    superuser, _migrator, baseline, checked, before, run = _migrated(
        "", "CREATE FUNCTION held() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'; "
        "REVOKE EXECUTE ON FUNCTION held() FROM PUBLIC"
    )
    try:
        ours, _ = pr._seed_escalations(**checked)
        run(superuser, baseline, "GRANT EXECUTE ON FUNCTION held() TO PUBLIC")
        after, _ = pr._seed_escalations(**checked)
        fingerprint = {what: mark for _db, what, mark in ours if "held()" in what}
        changed = {what: mark for _db, what, mark in after if "held()" in what}
        assert fingerprint and fingerprint != changed
    finally:
        _dropped(superuser, baseline, run)


def _as_superuser(dbname: str, statement: str) -> None:
    parts = urlsplit(_REAL)
    _customer_sql(
        host=parts.hostname or "localhost",
        port=parts.port or 5432,
        user=parts.username,
        password=parts.password or "",
        dbname=dbname,
        statement=statement,
    )


@real_postgres
def test_a_world_s_role_reads_the_trial_faults_the_runner_wrote(worlds, monkeypatch):
    """The runner writes a trial's faults as the superuser, and the tool proxy reads them with the world's role."""
    from fi.alk.harness import tool_trace_proxy
    from fi.alk.harness.world_door import TRIAL_FAULTS_DDL

    connect, _reset, _baseline = worlds
    _as_superuser("w0", TRIAL_FAULTS_DDL)
    _as_superuser(
        "w0",
        "INSERT INTO _alk_trial_faults (id, faults) VALUES "
        """(1, '[{"tool": "book", "kind": "timeout_before_commit"}]'::jsonb) """
        "ON CONFLICT (id) DO UPDATE SET faults = EXCLUDED.faults",
    )
    monkeypatch.setattr(tool_trace_proxy, "DATABASE_URL", "the world's role")
    monkeypatch.setattr(tool_trace_proxy, "_FAULTS_CONNECTION", connect(0, "w0"))

    assert tool_trace_proxy._trial_faults() == [{"tool": "book", "kind": "timeout_before_commit"}]


@real_postgres
def test_a_world_s_role_claims_an_actor_key_once(worlds, monkeypatch):
    """A door claims each keyed actor action with the world's role, which can neither create nor alter the table."""
    connect, _reset, _baseline = worlds
    log = PostgresLog("the world's role")
    monkeypatch.setattr(log, "_connect", lambda **_: connect(0, "w0"))

    assert log.claim("approve-1", "manager") is None
    assert log.claim("approve-1", "manager") == "pending"
    log.settle("approve-1", "manager", "committed", {"ok": True})
    assert log.claim("approve-1", "manager") == "committed"


@real_postgres
def test_an_older_bundle_s_action_log_records_how_each_call_was_made(worlds, monkeypatch):
    """A log made before the method, fault and endpoint columns gains them on the baseline; a world role could not add
    them."""
    connect, _reset, _baseline = worlds
    log = PostgresLog("the world's role")
    monkeypatch.setattr(log, "_connect", lambda **_: connect(0, "w0"))

    log._ensure_call_columns()

    assert {"method", "fault", "endpoint"} <= log._table_columns()


@real_postgres
def test_a_world_s_role_keeps_its_workers_runs_and_stops(worlds):
    """The in-process door marks a worker's run and claims its stop with the world's role, which may neither create
    nor alter a table: the baseline made them before the grant."""
    from fi.alk.harness import world_door

    connect, _reset, _baseline = worlds
    with connect(0, "w0") as connection:
        address = (
            f"postgresql://{connection.info.user}:{connection.info.password}"
            f"@{connection.info.host}:{connection.info.port}/w0"
        )
        connection.execute(
            "INSERT INTO _alk_trial_faults (id, faults, worker_deadline_at, started_at) VALUES (1, '[]', %s, %s)",
            (time.time() - 5, time.time() - 10),
        )
    world_door._TRIAL_SCHEMA_MADE.discard(address)
    world_door.mark_worker_run(address, "refunds")
    assert world_door.claim_worker_stop(address, "refunds", "", lambda: True) is True
    with connect(0, "w0") as connection:
        assert connection.execute("SELECT worker FROM _alk_worker_runs").fetchall() == [("refunds",)]
        assert connection.execute("SELECT worker, reason FROM _alk_worker_stops").fetchall() == [
            ("refunds", "deadline_passed")
        ]
