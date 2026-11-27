"""What a release world's tool server leaves in the trace, read back as a run reads it."""

from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest

from fi.alk.harness import call_runner as cr
from fi.alk.harness.bindings import (
    AgentTool,
    MappedToolBinding,
    UnsupportedToolBinding,
)
from fi.alk.harness.bundle_author_v2 import WORLD_SEED_PREFIX
from fi.alk.harness.releases import ToolDefinition
from fi.alk.harness.request_binding import REQUEST_BINDING_SQL
from fi.alk.harness.world_tools import WorldTools

pytestmark = pytest.mark.skipif(
    not os.environ.get("ALK_TEST_POSTGRES_DSN"), reason="needs ALK_TEST_POSTGRES_DSN"
)

_LOOKUP = "def handle(args, db):\n    return {'id': args['id']}\n"
_CHANGE = (
    "def handle(args, db):\n"
    "    if args['id'] == 'locked':\n"
    "        raise ToolError('the booking is locked')\n"
    "    if args['id'] == 'kept':\n"
    "        refusal = ToolError('the line dropped')\n"
    "        refusal.keep_writes = True\n"
    "        raise refusal\n"
    "    if args['id'] == 'broken':\n"
    "        raise RuntimeError('no such shelf')\n"
    "    return {'changed': args['id']}\n"
)
_PARAMETERS = {
    "type": "object",
    "properties": {"id": {"type": "string"}},
    "required": ["id"],
}
_ENVIRONMENT = [
    ToolDefinition(
        name=name, description=name, parameters=_PARAMETERS, mutates_state=mutates
    )
    for name, mutates in (("find_booking", False), ("change_booking", True))
]


def _step(tool: str) -> dict:
    return {"id": tool, "tool": tool, "arguments": {"id": {"arg": "ref"}}}


# The customer's `find_booking` is the environment's `change_booking`: a name in one vocabulary is never read in the
# other.
_BINDING = SimpleNamespace(
    mode="tool_routed",
    agent_tools=[
        AgentTool(
            name=name,
            description=name,
            parameters={"type": "object", "properties": {"ref": {"type": "string"}}},
            admission="routed",
        )
        for name in ("reschedule", "find_booking", "fax_receipt")
    ],
    tools=[
        MappedToolBinding.model_validate(
            {
                "agent_tool": "reschedule",
                "steps": [_step("find_booking"), _step("change_booking")],
            }
        ),
        MappedToolBinding.model_validate(
            {"agent_tool": "find_booking", "steps": [_step("change_booking")]}
        ),
        UnsupportedToolBinding(
            agent_tool="fax_receipt", unsupported="no environment tool sends a fax"
        ),
    ],
    fixtures={},
    aliases={},
)


@pytest.fixture
def dsn():
    import psycopg
    from psycopg.conninfo import make_conninfo

    admin = os.environ["ALK_TEST_POSTGRES_DSN"]
    world = f"world_tools_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{world}"')
    try:
        address = make_conninfo(admin, dbname=world)
        with psycopg.connect(address, autocommit=True) as connection:
            connection.execute(WORLD_SEED_PREFIX + REQUEST_BINDING_SQL)
        yield address
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{world}" WITH (FORCE)')


def _world(dsn: str, binding=None) -> WorldTools:
    handlers = {
        name: (source, compile(source, name, "exec"))
        for name, source in (("find_booking", _LOOKUP), ("change_booking", _CHANGE))
    }
    return WorldTools(handlers, _ENVIRONMENT, dsn, binding=binding)


def _runtime(dsn: str):
    return SimpleNamespace(
        endpoints={"database": SimpleNamespace(protocol="postgres", address=dsn)}
    )


def _read(dsn: str):
    return cr.collect_tool_trace_calls(_runtime(dsn), strict=True)


def test_a_routed_call_is_graded_by_the_environment_s_operations_not_the_customer_s_tool(
    dsn,
) -> None:
    world = _world(dsn, _BINDING)
    world.call("reschedule", {"ref": "B1"}, provider_call_id="c1", tool_call_id="t1")
    world.call("find_booking", {"ref": "B2"}, provider_call_id="c1", tool_call_id="t2")

    calls = _read(dsn)
    assert [(one.name, one.arguments) for one in calls] == [
        ("find_booking", {"id": "B1"}),
        ("change_booking", {"id": "B1"}),
        ("change_booking", {"id": "B2"}),
    ]
    log = cr._read_action_log(dsn)
    assert cr.trial_state_changes(log, {})[0]["committed_actions"] == [
        "find_booking",
        "change_booking",
        "change_booking",
    ]
    other = {
        "actor_id": "clerk",
        "name": "change_booking",
        "arguments": {"id": "B2"},
        "outcome": "committed",
    }
    (shared,) = cr.trial_shared_records([*log, other], {}, {"id"})
    assert [one["tool"] for one in shared["actions"]] == [
        "change_booking",
        "change_booking",
    ]
    # One row answers one call of its own tool, never a call of the other vocabulary's tool of the same name.
    assert not cr.evidence_notices(log[2:], calls[2:], {"change_booking"})
    assert cr.evidence_notices(log[2:], calls[:1], {"find_booking"})
    fault = {"action": "find_booking", "on_call": 1, "mode": "unavailable"}
    assert cr.fault_notices([fault], log[2:], []) == cr.fault_notices([fault], [], [])


def test_a_call_through_the_tool_proxy_is_still_graded_by_the_agent_s_own_tool(
    dsn,
) -> None:
    from fi.alk.harness.world_door import PostgresLog

    row = {
        "name": "list",
        "action": "refunds.list",
        "arguments": {},
        "result": {},
        "ok": True,
        "error": "",
        "at": 1.0,
        "action_id": "a1",
        "outcome": "committed",
        "refusal": None,
        "refusal_code": None,
        "idempotency_key": None,
        "actor_id": "agent",
        "worker_id": None,
        "attribution": None,
        "agent_tool": "list_refunds",
        "parent_event_id": None,
        "on_behalf_of": None,
        "effect": "write",
        "method": "GET",
        "fault": None,
    }
    PostgresLog(dsn).write(row)

    assert [one.name for one in _read(dsn)] == ["list_refunds"]
    changes = cr.trial_state_changes(cr._read_action_log(dsn), {})
    assert changes[0]["committed_actions"] == ["list_refunds"]


def test_what_a_call_came_to_is_read_back_from_the_world_s_own_log(dsn) -> None:
    world = _world(dsn, _BINDING)
    world.call("find_booking", {"ref": "B1"})
    # All of a routed call's operations share its outcome: the first was rolled back with the refused second.
    world.call("reschedule", {"ref": "locked"})
    world.call("find_booking", {"ref": "broken"})
    world.call("find_booking", {})
    world.call("fax_receipt", {})

    calls = _read(dsn)
    assert [(one.name, one.ok, one.refused) for one in calls] == [
        ("change_booking", True, False),
        ("find_booking", False, True),
        ("change_booking", False, True),
        ("change_booking", False, False),
        ("find_booking", False, False),
        ("fax_receipt", False, False),
    ]
    log = cr._read_action_log(dsn)
    assert [row["outcome"] for row in log] == [
        "committed",
        "refused",
        "refused",
        "faulted",
        "faulted",
        "faulted",
    ]
    agent = cr.trial_state_changes(log, {})[0]
    assert (agent["committed"], agent["refused"], agent["faulted"]) == (1, 2, 3)
    assert agent["committed_actions"] == ["change_booking"]


def test_a_call_is_still_traced_in_a_world_seeded_before_outcomes_were_logged(
    dsn,
) -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute("ALTER TABLE _alk_tool_trace DROP COLUMN outcome")
    _world(dsn).call("change_booking", {"id": "locked"})

    (call,) = _read(dsn)
    assert (call.name, call.ok, call.refused) == ("change_booking", False, True)


def test_a_refusal_that_keeps_its_writes_still_reads_refused(dsn) -> None:
    _world(dsn).call("change_booking", {"id": "kept"})

    assert [row["outcome"] for row in cr._read_action_log(dsn)] == ["refused"]
    (call,) = _read(dsn)
    assert (call.ok, call.refused) == (False, True)


def test_attaching_servers_makes_no_login_and_passes_the_readers_address(tmp_path):
    from fi.alk.harness.releases import AttachedServer
    from fi.alk.harness.world_tools import attach_servers

    server = AttachedServer.model_validate(
        {
            "id": "sql",
            "server": "sql",
            "tools": ["query"],
            "attach": {"database_reader": {"tables": ["bookings"]}},
        }
    )
    # Nothing listens there: attaching connects to no database.
    reader = "postgresql://alk_r0:secret@127.0.0.1:1/w0"

    attached = attach_servers([server], reader, cwd=tmp_path)

    assert attached._parameters(server).env["WORLD_READER_URL"] == reader
    assert attach_servers([], None) is None


def test_two_first_calls_start_the_attached_servers_once(monkeypatch):
    import threading
    import time

    from fi.alk.harness.world_tools import AttachedServers

    attached = AttachedServers([], reader_url=None, cwd=None)
    starts = []

    async def serve(self, ready):
        import asyncio

        starts.append(1)
        self._stop = asyncio.Event()
        await asyncio.sleep(0.2)
        self.definitions.append("ready")
        ready.set_result(None)
        await self._stop.wait()

    monkeypatch.setattr(AttachedServers, "_serve", serve)
    seen = []

    def first():
        attached.start()
        seen.append(list(attached.definitions))

    threads = [threading.Thread(target=first) for _ in range(4)]
    for thread in threads:
        thread.start()
        time.sleep(0.01)
    for thread in threads:
        thread.join(10)
    attached.close()

    assert starts == [1] and seen == [["ready"]] * 4


def test_a_release_s_world_is_seeded_and_served_without_the_superuser(
    tmp_path, monkeypatch
):
    """As hosted runs have it: the seed runs as the restricted login, which can make no role, and
    the tool server connects as the world's own role, which can make no table."""
    import subprocess
    from urllib.parse import urlsplit, urlunsplit

    import psycopg

    from fi.alk.harness import process_runtime as pr
    from fi.alk.harness import releases
    from fi.alk.harness.bundle_v2 import StoreEntry
    from fi.alk.harness.mcp_servers import READER_ROLE
    from fi.alk.harness.world_tools import TRACE_FAILURES_SQL, TRACE_FAILURES_TABLE

    reader = SimpleNamespace(tables=["bookings"])
    monkeypatch.setattr(
        releases,
        "load_servers",
        lambda _root: [SimpleNamespace(attach=SimpleNamespace(database_reader=reader))],
    )
    (tmp_path / "world.sql").write_text(
        WORLD_SEED_PREFIX
        + REQUEST_BINDING_SQL
        + f"{TRACE_FAILURES_SQL};\n"
        + 'CREATE TABLE "bookings" (id text PRIMARY KEY);\n'
        + releases.reader_grants_sql(tmp_path),
        encoding="utf-8",
    )
    parts = urlsplit(os.environ["ALK_TEST_POSTGRES_DSN"])
    host, port = parts.hostname or "localhost", parts.port or 5432
    superuser = pr.EngineCredentials(
        parts.username, parts.password or "", world_secret=uuid.uuid4().hex
    )
    baseline = f"alk_baseline_{uuid.uuid4().hex[:8]}"
    world = f"alk_world_{uuid.uuid4().hex[:8]}"
    index = uuid.uuid4().int % 100_000 + 1_000
    engine = dict(
        port=port,
        credentials=superuser,
        sql_runner=pr.default_sql_runner,
        process_name="world-db",
    )
    admin = os.environ["ALK_TEST_POSTGRES_DSN"]
    with psycopg.connect(admin, autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{baseline}"')
    try:
        login = pr.seed_store_safely(
            StoreEntry.model_construct(
                capability="world_db", migrations=["world.sql"], seed_files=[]
            ),
            engine=pr.ManagedEngine.POSTGRES,
            bundle_dir=tmp_path,
            dbname=baseline,
            sync_run=subprocess.run,
            **engine,
        )
        pr.retire_login(
            login,
            port=port,
            credentials=superuser,
            sql_runner=pr.default_sql_runner,
            process_name="world-db",
        )
        pr._make_harness_tables(baseline_db=baseline, **engine)
        # A release's bundle has no process of the door's, so its tool server writes the log.
        pr._grant_world_role_group(baseline_db=baseline, door=False, **engine)
        role = pr.world_role(superuser, index)
        pr._reset_template_database(
            host=host,
            port=port,
            credentials=superuser,
            world_db=world,
            template_db=baseline,
            sql_runner=pr.default_sql_runner,
            process_name="world-db",
            isolate=True,
            role=role,
            reader=pr.reader_role(superuser, index),
        )

        def address(user: str, password: str) -> str:
            at = f"{user}:{password}@" if password else f"{user}@"
            return urlunsplit(
                (parts.scheme, f"{at}{host}:{port}", f"/{world}", parts.query, "")
            )

        # The attached servers read the listed tables with a login the harness made: the tool
        # server's own can make none.
        reader_login = pr.reader_role(superuser, index)
        with psycopg.connect(
            address(reader_login.username, reader_login.password)
        ) as connection:
            assert connection.execute("SELECT count(*) FROM bookings").fetchone() == (0,)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute("SELECT * FROM _alk_tool_trace")
        with (
            psycopg.connect(address(role.username, role.password)) as connection,
            pytest.raises(psycopg.errors.InsufficientPrivilege),
        ):
            connection.execute("CREATE ROLE planted LOGIN")
        as_harness = address(superuser.username, superuser.password)
        tools = _world(address(role.username, role.password))
        tools.call("change_booking", {"id": "B1"})
        tools.call("change_booking", {"id": "locked"})
        done, refused = _read(as_harness)
        assert (done.ok, done.refused) == (True, False)
        # The handler's own refusal is an answer, as it is in a proof.
        assert (refused.ok, refused.refused, refused.refusal_code) == (
            False,
            True,
            "world_rule",
        )
        with psycopg.connect(as_harness, autocommit=True) as connection:
            assert connection.execute(
                "SELECT has_table_privilege(%s, 'public.bookings', 'SELECT')",
                (READER_ROLE,),
            ).fetchone() == (True,)
            # A call that ran and could not be logged is marked, so its trial is never read as complete.
            connection.execute("ALTER TABLE _alk_tool_trace ADD CHECK (name <> 'find_booking')")
            assert tools.call("find_booking", {"id": "B1"}) is not None
            assert connection.execute(
                f"SELECT count(*) FROM {TRACE_FAILURES_TABLE}"
            ).fetchone() == (1,)
        with pytest.raises(cr.ToolCaptureFailed):
            _read(as_harness)
    finally:
        with psycopg.connect(admin, autocommit=True) as connection:
            connection.execute(f'DROP DATABASE IF EXISTS "{world}" WITH (FORCE)')
            connection.execute(f'ALTER DATABASE "{baseline}" WITH IS_TEMPLATE false')
            connection.execute(f'DROP DATABASE IF EXISTS "{baseline}" WITH (FORCE)')
