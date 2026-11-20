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

import psycopg
import pytest
import test_process_runtime as runtime_tests

from fi.alk.harness import bundle_author_v2 as author
from fi.alk.harness import call_runner, world_door
from fi.alk.harness import process_runtime as pr
from fi.alk.harness import tool_trace_proxy as proxy
from fi.alk.harness.actors import ACTORS_DDL
from fi.alk.harness.bundle_v2 import BaselineStrategy
from fi.alk.harness.world_roles import seed_escalation_queries

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
            chown=lambda path, uid, gid, dir_fd=None: None,
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
    # Each trial's tables are made before the baseline's tables are measured, so a world counts them like its own.
    made = [i for i, s in enumerate(statements) if s.startswith("CREATE TABLE IF NOT EXISTS _alk_trial_faults")]
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
            pr.default_sql_runner(
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
        pr.default_sql_runner(
            host=host, port=port, user=parts.username, password=parts.password or "", dbname="postgres",
            statement=f'DROP DATABASE IF EXISTS "w{index}" WITH (FORCE)',
        )
    for baseline in made:
        for statement in (
            f'ALTER DATABASE "{baseline}" WITH IS_TEMPLATE false',
            f'DROP DATABASE IF EXISTS "{baseline}"',
        ):
            pr.default_sql_runner(
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
        pr.default_sql_runner(
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


def test_the_seed_check_snapshots_after_the_migrations_and_before_the_seed(tmp_path: Path, monkeypatch):
    """The source's migrations made a SECURITY DEFINER function and the seed made an event trigger: only the seed's is
    refused, because the snapshot between the two lists holds the migration's."""
    applied: list[str] = []
    monkeypatch.setattr(pr, "apply_seed_file", lambda _engine, path, **_kw: applied.append(Path(path).name))
    code = seed_escalation_queries(pr.WORLD_ROLE_GROUP).code
    from_migration = ("security definer function auth.uid()", "m1")
    from_seed = ("event trigger planted", "s1")

    def answer(dbname: str, statement: str) -> list[tuple[str, str]]:
        if statement != code or dbname == "postgres":
            return []
        return [from_migration] + ([from_seed] if "seed.sql" in applied else [])

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
    assert applied == ["migrate.sql", "seed.sql"]
    assert "event trigger planted" in str(raised.value)
    assert "auth.uid()" not in str(raised.value)


@real_postgres
@pytest.mark.parametrize(
    "migration, seed, refused",
    [
        # A source whose own migrations define a SECURITY DEFINER function builds.
        ("CREATE FUNCTION mig_owned() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'", "", None),
        # One the seed adds is refused.
        ("", "CREATE FUNCTION seed_added() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'", "seed_added()"),
        # A seed that changes the migration's function is refused.
        (
            "CREATE FUNCTION mig_changed() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 1'",
            "CREATE OR REPLACE FUNCTION mig_changed() RETURNS int LANGUAGE sql SECURITY DEFINER AS 'SELECT 2'",
            "mig_changed()",
        ),
        # A seed that turns a migration's plain function into a SECURITY DEFINER one is refused.
        (
            "CREATE FUNCTION mig_plain() RETURNS int LANGUAGE sql AS 'SELECT 1'",
            "ALTER FUNCTION mig_plain() SECURITY DEFINER",
            "mig_plain()",
        ),
        # A seed that alters the world group is refused.
        ("", 'ALTER ROLE "alk_world_tools" CREATEDB', "alk_world_tools"),
    ],
)
def test_the_seed_check_refuses_only_what_the_seed_added_or_changed(migration, seed, refused):
    parts = urlsplit(_REAL)
    host, port = parts.hostname or "localhost", parts.port or 5432
    superuser = pr.EngineCredentials(parts.username, parts.password or "")
    baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"
    checked = {"port": port, "credentials": superuser, "baseline_db": baseline, "sql_runner": pr.default_sql_runner,
               "process_name": "db"}

    def run(dbname: str, statement: str) -> None:
        if statement:
            pr.default_sql_runner(
                host=host, port=port, user=superuser.username, password=superuser.password, dbname=dbname,
                statement=statement,
            )

    run("postgres", f'CREATE DATABASE "{baseline}"')
    run("postgres", "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'alk_world_tools') "
        "THEN CREATE ROLE alk_world_tools NOLOGIN; END IF; END $$")
    try:
        run(baseline, migration)
        before = pr.seed_escalation_snapshot(**checked)
        run(baseline, seed)
        if refused is None:
            pr._refuse_seed_escalation(**checked, before_seed=before)
        else:
            with pytest.raises(pr.ProcessRuntimeError) as raised:
                pr._refuse_seed_escalation(**checked, before_seed=before)
            assert raised.value.code == "seed_failed" and refused in str(raised.value)
    finally:
        run("postgres", 'ALTER ROLE "alk_world_tools" NOCREATEDB')
        run("postgres", f'DROP DATABASE IF EXISTS "{baseline}"')
