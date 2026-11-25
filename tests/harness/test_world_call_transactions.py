"""One tool call is one transaction, and calls to one world take turns.

A booking writes an appointment and its calendar event. Whatever fails between the two, and
however long the store keeps refusing writes, a failed call leaves nothing behind: the store
rolls it back, which needs no writes of its own. Two calls at once cannot undo each other,
because the second waits for the first. A refusal the world simulates on purpose
(`keep_writes`: a rival booking, a commit whose reply was lost) is the one failure that keeps
what it wrote, in process, in the isolated handler child and on PostgreSQL alike.

On PostgreSQL a call's trace rows commit with its writes, so a write the world keeps is never
missing from the trace, and the release tool server runs each provider operation once.

The PostgreSQL half runs when ALK_TEST_POSTGRES_URL names a server it may create databases on.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

import pytest

from fi.alk.harness.world import GeneratedWorld

BOOK = (
    "def handle(args, db):\n"
    "    done = db.one('SELECT * FROM appointments WHERE request_key = ?', [args['request_key']])\n"
    "    if done:\n"
    "        return {'appointment_id': done['appointment_id'], 'idempotent': True}\n"
    "    if db.one('SELECT 1 FROM events WHERE slot = ?', [args['slot']]):\n"
    "        raise ToolError('slot taken', category='conflict')\n"
    "    aid = 'A' + args['slot']\n"
    "    db.execute('INSERT INTO appointments (appointment_id, slot, request_key) VALUES (?, ?, ?)', [aid, args['slot'], args['request_key']])\n"
    "    db.execute('INSERT INTO events (event_id, slot) VALUES (?, ?)', ['EV-' + aid, args['slot']])\n"
    "    return {'appointment_id': aid, 'idempotent': False}\n"
)
RIVAL = (
    "def handle(args, db):\n"
    "    db.execute('INSERT INTO appointments (appointment_id, slot, request_key) VALUES (?, ?, ?)', ['RIVAL', args['slot'], 'rival'])\n"
    "    db.execute('INSERT INTO events (event_id, slot) VALUES (?, ?)', ['EV-RIVAL', args['slot']])\n"
    "    error = ToolError('another caller just took this slot', category='conflict')\n"
    "    if args.get('kept'):\n"
    "        error.keep_writes = True\n"
    "    raise error\n"
)
SCHEMA = (
    "CREATE TABLE appointments (appointment_id TEXT PRIMARY KEY, slot TEXT NOT NULL, request_key TEXT NOT NULL);"
    "CREATE TABLE events (event_id TEXT PRIMARY KEY, slot TEXT NOT NULL);"
)


def _world() -> GeneratedWorld:
    class Clinic(GeneratedWorld):
        name = "clinic"
        tools = [{"name": "book"}, {"name": "rival"}]
        handlers = {"book": BOOK, "rival": RIVAL}

    world = Clinic(":memory:")
    # The environment's own world: one bound to an agent's code keeps that code's own behaviour.
    world.isolated = True
    world.connection.executescript(SCHEMA)
    world.connection.commit()
    return world


def _rows(world) -> dict:
    state = world.state()
    return {table: sorted(json.dumps(row, sort_keys=True) for row in state[table]) for table in ("appointments", "events")}


def test_a_store_that_keeps_refusing_writes_leaves_nothing_behind() -> None:
    world = _world()
    real = world.store.execute
    writes = []

    def refusing(sql, params=()):
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(sql)
            if len(writes) >= 2:  # every write from the event on, and anything after it
                raise RuntimeError("the database refuses writes")
        return real(sql, params)

    world.store.execute = refusing
    failed = world.call("book", {"slot": "S1", "request_key": "K1"})
    world.store.execute = real
    assert not failed.ok
    assert _rows(world) == {"appointments": [], "events": []}
    retried = world.call("book", {"slot": "S1", "request_key": "K1"})
    assert retried.ok and retried.result == {"appointment_id": "AS1", "idempotent": False}
    assert len(world.state()["events"]) == 1


def test_a_failing_call_cannot_undo_a_concurrent_success() -> None:
    world = _world()
    real = world.store.execute
    paused, resume = threading.Event(), threading.Event()

    def first_fails_at_its_event(sql, params=()):
        if threading.current_thread().name == "first" and sql.lstrip().upper().startswith("INSERT INTO EVENTS"):
            paused.set()
            resume.wait(5)
            raise RuntimeError("the event write failed")
        return real(sql, params)

    world.store.execute = first_fails_at_its_event
    outcomes = {}
    first = threading.Thread(name="first", target=lambda: outcomes.__setitem__("first", world.call("book", {"slot": "S1", "request_key": "K1"})))
    second = threading.Thread(name="second", target=lambda: outcomes.__setitem__("second", world.call("book", {"slot": "S2", "request_key": "K2"})))
    first.start()
    assert paused.wait(5)
    second.start()
    time.sleep(0.2)
    # The second call waits its turn rather than interleaving with the first.
    assert "second" not in outcomes
    resume.set()
    first.join(5)
    second.join(5)
    world.store.execute = real
    assert not outcomes["first"].ok and outcomes["second"].ok
    assert [json.loads(row)["appointment_id"] for row in _rows(world)["appointments"]] == ["AS2"]
    assert [json.loads(row)["event_id"] for row in _rows(world)["events"]] == ["EV-AS2"]


@pytest.mark.parametrize("isolation", ["", "process"])
def test_only_a_simulated_fault_keeps_what_it_wrote(isolation: str, monkeypatch) -> None:
    if isolation:
        monkeypatch.setenv("ALK_HANDLER_ISOLATION", isolation)
    world = _world()
    refused = world.call("rival", {"slot": "S1"})
    assert not refused.ok and _rows(world) == {"appointments": [], "events": []}
    kept = world.call("rival", {"slot": "S1", "kept": True})
    assert not kept.ok
    assert len(world.state()["appointments"]) == 1 and len(world.state()["events"]) == 1


def test_a_commit_that_fails_records_a_failed_call_and_keeps_nothing() -> None:
    """A constraint SQLite checks only at commit: the handler returns normally, the commit
    refuses, and the call must be recorded as failed, keep nothing, and leave the world usable."""
    from fi.alk.harness.world import GeneratedWorld

    class Ledger(GeneratedWorld):
        name = "ledger"
        tools = [{"name": "add_line"}]
        handlers = {
            "add_line": (
                "def handle(args, db):\n"
                "    db.execute('INSERT INTO lines (line_id, invoice_id) VALUES (?, ?)', [args['line_id'], args['invoice_id']])\n"
                "    return {'line_id': args['line_id']}\n"
            )
        }

    world = Ledger(":memory:")
    world.isolated = True
    world.connection.executescript(
        "CREATE TABLE invoices (invoice_id TEXT PRIMARY KEY);"
        "CREATE TABLE lines (line_id TEXT PRIMARY KEY, invoice_id TEXT REFERENCES invoices(invoice_id) DEFERRABLE INITIALLY DEFERRED);"
        "INSERT INTO invoices VALUES ('INV1');"
    )
    world.connection.commit()
    refused = world.call("add_line", {"line_id": "L1", "invoice_id": "NOPE"})
    assert not refused.ok and "FOREIGN KEY" in refused.error.upper()
    assert world.calls[-1] is refused and not any(c.ok for c in world.calls)
    assert world.state()["lines"] == []
    fine = world.call("add_line", {"line_id": "L2", "invoice_id": "INV1"})
    assert fine.ok and [row["line_id"] for row in world.state()["lines"]] == ["L2"]


def test_a_world_bound_to_an_agents_code_keeps_what_a_refused_call_wrote() -> None:
    world = GeneratedWorld(":memory:", kind="sqlite")
    world.store.apply("CREATE TABLE logins (failed INTEGER); INSERT INTO logins VALUES (0);")
    world.handlers = {
        "login": (
            "def handle(args, db):\n"
            "    db.execute('UPDATE logins SET failed = failed + 1')\n"
            "    raise ToolError('wrong pin')\n"
        )
    }
    frozen = []
    world.store.freeze = lambda: frozen.append(1)

    assert world.call("login", {}).refused
    assert world.state()["logins"] == [{"failed": 1}]
    assert not frozen


@pytest.mark.parametrize("copyable", [True, False])
def test_a_failed_call_leaves_the_agents_own_state_object_alone(copyable: bool) -> None:
    class Agent:
        def __init__(self) -> None:
            self.failed = 0
            self.lock = None if copyable else threading.Lock()

    world = GeneratedWorld(":memory:", kind="sqlite")
    agent = world.state_object = Agent()
    world.handlers = {
        "login": (
            "def handle(args, db):\n"
            "    db.state.failed += 1\n"
            "    raise ToolError('wrong pin')\n"
        )
    }

    assert world.call("login", {}).refused
    assert world.state_object is agent and agent.failed == 1


# -- PostgreSQL: the path world_mcp and the release tool server take ---------------------------

PG = os.environ.get("ALK_TEST_POSTGRES_URL", "")


@pytest.fixture()
def pg_world():
    if not PG:
        pytest.skip("ALK_TEST_POSTGRES_URL is not set")
    import psycopg
    from psycopg import conninfo

    from fi.alk.harness import request_binding
    from fi.alk.harness.releases import ToolDefinition
    from fi.alk.harness.world_tools import WorldTools

    name = f"alk_txn_{uuid.uuid4().hex[:10]}"
    with psycopg.connect(PG, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    url = conninfo.make_conninfo(PG, dbname=name)
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(SCHEMA)
        connection.execute(
            "CREATE TABLE _alk_tool_trace (id SERIAL PRIMARY KEY, name TEXT, arguments TEXT, result TEXT, ok BOOLEAN, "
            "error TEXT, at DOUBLE PRECISION, agent_tool TEXT, provider_call_id TEXT, tool_call_id TEXT, status TEXT)"
        )
        connection.execute(request_binding.REQUEST_BINDING_SQL)
    handlers = {tool: (source, compile(source, f"<handler:{tool}>", "exec")) for tool, source in (("book", BOOK), ("rival", RIVAL))}
    parameters = {"type": "object", "properties": {"slot": {"type": "string"}, "request_key": {"type": "string"}, "kept": {"type": "boolean"}}}
    tools = [ToolDefinition(name=tool, description=tool, parameters=parameters, mutates_state=True) for tool in handlers]
    yield WorldTools(handlers, tools, url), url
    with psycopg.connect(PG, autocommit=True) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _pg_rows(url):
    import psycopg

    with psycopg.connect(url) as connection:
        return {
            table: sorted(row[0] for row in connection.execute(f"SELECT {key} FROM {table}").fetchall())
            for table, key in (("appointments", "appointment_id"), ("events", "event_id"))
        }


def test_postgres_calls_take_turns_and_roll_back(pg_world) -> None:
    tools, url = pg_world
    assert tools.call("book", {"slot": "S1", "request_key": "K1"}).status == "ok"
    assert tools.call("rival", {"slot": "S2"}).status != "ok"
    assert _pg_rows(url) == {"appointments": ["AS1"], "events": ["EV-AS1"]}
    assert tools.call("rival", {"slot": "S2", "kept": True}).status != "ok"
    assert _pg_rows(url) == {"appointments": ["AS1", "RIVAL"], "events": ["EV-AS1", "EV-RIVAL"]}
    # Two calls for the same free slot at once: one books it, the other finds it taken.
    outcomes = []
    threads = [threading.Thread(target=lambda k=k: outcomes.append(tools.call("book", {"slot": "S9", "request_key": k}).status)) for k in ("K8", "K9")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert sorted(outcomes) == ["ok", "refused"]
    assert _pg_rows(url)["events"].count("EV-AS9") == 1


def _execute(url, statement, params=()):
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        return connection.execute(statement, params).fetchall() if params is not None else None


def _trace_names(url):
    import psycopg

    with psycopg.connect(url) as connection:
        return [row[0] for row in connection.execute("SELECT name FROM _alk_tool_trace ORDER BY id").fetchall()]


def _refuse_inserts(url, table):
    """The exact failure: every INSERT into ``table`` fails, every SELECT of it still succeeds."""
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(
            "CREATE OR REPLACE FUNCTION _refuse() RETURNS trigger AS $$ BEGIN "
            "RAISE EXCEPTION 'insert refused'; END $$ LANGUAGE plpgsql"
        )
        connection.execute(f"CREATE TRIGGER refuse BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION _refuse()")


def _allow_inserts(url, table):
    import psycopg

    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(f"DROP TRIGGER refuse ON {table}")


def _trace_runtime(url):
    from fi.alk.harness.process_runtime import (
        EnvironmentRuntime,
        RuntimeEndpoint,
        RuntimeState,
    )

    return EnvironmentRuntime(
        runtime_id="rt-1",
        world_index=0,
        bundle_digest="sha256:" + "0" * 64,
        state=RuntimeState.READY,
        endpoints={"database": RuntimeEndpoint(capability="database", protocol="postgres", address=url, configuration_name="DATABASE_URL")},
        metadata={},
    )


def test_postgres_a_write_whose_trace_cannot_be_written_is_undone_and_the_trial_unmeasured(pg_world) -> None:
    from fi.alk.harness import call_runner

    tools, url = pg_world
    runtime = _trace_runtime(url)
    _refuse_inserts(url, "_alk_tool_trace")
    outcome = tools.call("book", {"slot": "S1", "request_key": "K1"})
    # The booking and its record commit together: neither stands, and the agent hears a failure.
    assert outcome.status == "fault" and "insert refused" in outcome.error
    assert _pg_rows(url) == {"appointments": [], "events": []}
    # The trace reads fine and holds nothing, which alone would grade the run as one that made no
    # call; the durable mark makes the read a failed capture instead.
    assert _trace_names(url) == []
    assert call_runner._read_tool_trace(runtime) is None
    with pytest.raises(call_runner.ToolCaptureFailed, match="ran untraced"):
        call_runner.collect_tool_trace_calls(runtime, strict=True)
    assert call_runner.collect_tool_trace_calls(runtime) == ()
    _allow_inserts(url, "_alk_tool_trace")
    # The next scenario starts from a clean slate: clearing the trace clears the marks with it.
    call_runner._clear_tool_trace_calls(url)
    assert tools.call("book", {"slot": "S1", "request_key": "K1"}).status == "ok"
    assert _pg_rows(url) == {"appointments": ["AS1"], "events": ["EV-AS1"]}
    assert [call.name for call in call_runner._read_tool_trace(runtime)] == ["book"]


def test_postgres_a_trace_that_fails_only_in_the_call_s_transaction_records_the_failure(pg_world, monkeypatch) -> None:
    from fi.alk.harness import call_runner
    from fi.alk.harness.world_tools import WorldTools

    tools, url = pg_world
    real = WorldTools._trace
    failures = iter([RuntimeError("lost the connection")])

    def once(self, connection, *args):
        if not connection.autocommit and (failure := next(failures, None)):
            raise failure
        return real(self, connection, *args)

    monkeypatch.setattr(WorldTools, "_trace", once)
    outcome = tools.call("book", {"slot": "S1", "request_key": "K1"})
    assert outcome.status == "fault"
    assert _pg_rows(url) == {"appointments": [], "events": []}
    calls = call_runner._read_tool_trace(_trace_runtime(url))
    assert [(call.name, call.ok) for call in calls] == [("book", False)]


def test_postgres_a_refused_call_and_a_kept_fault_are_traced_with_their_writes(pg_world) -> None:
    tools, url = pg_world
    assert tools.call("rival", {"slot": "S1"}).status != "ok"
    assert tools.call("rival", {"slot": "S2", "kept": True}).status != "ok"
    assert _pg_rows(url) == {"appointments": ["RIVAL"], "events": ["EV-RIVAL"]}
    assert _trace_names(url) == ["rival", "rival"]


# -- The release tool server: one run per provider operation -----------------------------------

SECRET, TOKEN = "clone-secret", "bound-token"


@pytest.fixture()
def tool_server(pg_world):
    from http.server import ThreadingHTTPServer

    from fi.alk.harness import request_binding
    from fi.alk.harness.release_tool_server import make_handler

    tools, url = pg_world
    request_binding.register_active_call(url, token=TOKEN, secret=SECRET, scenario_key="s1")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tools, "/provider/tools", "/provider/events", request_binding_on=True))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/provider/tools", url
    server.shutdown()
    server.server_close()


def _post(address, body):
    import urllib.request

    request = urllib.request.Request(
        address,
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "x-alk-tool-secret": SECRET, "x-alk-binding": TOKEN},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _retell(name, args, transcript):
    return {"name": name, "args": args, "call": {"call_id": "retell-call-1", "transcript_with_tool_calls": transcript}}


def _invocation(tool_call_id, name, args):
    return {"role": "tool_call_invocation", "tool_call_id": tool_call_id, "name": name, "arguments": json.dumps(args)}


def test_a_retried_retell_delivery_of_a_write_runs_once(tool_server) -> None:
    address, url = tool_server
    first = {"slot": "S1", "request_key": "K1"}
    said = [{"role": "user", "content": "book me in"}, _invocation("tc-1", "book", first)]
    answer = _post(address, _retell("book", first, said))
    # Retell repeats the request after a lost answer: the stored answer comes back, nothing runs.
    assert _post(address, _retell("book", first, said)) == answer == {"appointment_id": "AS1", "idempotent": False}
    assert _pg_rows(url) == {"appointments": ["AS1"], "events": ["EV-AS1"]}
    assert _trace_names(url) == ["book"]
    # The agent asking again is a new invocation, even with the same arguments: it runs.
    said += [{"role": "tool_call_result", "tool_call_id": "tc-1", "content": json.dumps(answer)}, _invocation("tc-2", "book", first)]
    assert _post(address, _retell("book", first, said)) == {"appointment_id": "AS1", "idempotent": True}
    assert _trace_names(url) == ["book", "book"]


def test_a_retell_delivery_without_its_invocation_is_not_remembered(tool_server) -> None:
    # No transcript, or one whose last invocation is not this one: nothing names the operation, so
    # the call runs each time it arrives (the routed tools are never retried by Retell).
    address, url = tool_server
    rival = {"slot": "S1"}
    assert "error" in _post(address, _retell("rival", rival, []))
    assert "error" in _post(address, _retell("rival", rival, [_invocation("tc-9", "book", {"slot": "S1"})]))
    assert _trace_names(url) == ["rival", "rival"]
    responses = _execute(url, "SELECT count(*) FROM _alk_tool_responses", ())
    assert responses == [(0,)]


def test_a_repeated_vapi_tool_call_runs_once(tool_server) -> None:
    address, url = tool_server
    entry = {"id": "vapi-tc-1", "function": {"name": "book", "arguments": {"slot": "S2", "request_key": "K2"}}}
    body = {"message": {"type": "tool-calls", "call": {"id": "vapi-call-1"}, "toolCallList": [entry]}}
    first = _post(address, body)
    assert _post(address, body) == first
    assert first["results"][0]["toolCallId"] == "vapi-tc-1" and "result" in first["results"][0]
    assert _pg_rows(url) == {"appointments": ["AS2"], "events": ["EV-AS2"]}
    assert _trace_names(url) == ["book"]


def test_a_write_whose_answer_cannot_be_stored_does_not_stand(tool_server) -> None:
    # The effect and the stored answer commit together: a retry after a failed store runs the
    # call afresh rather than finding a write with no answer to replay.
    address, url = tool_server
    args = {"slot": "S3", "request_key": "K3"}
    said = [_invocation("tc-3", "book", args)]
    _refuse_inserts(url, "_alk_tool_responses")
    assert "insert refused" in _post(address, _retell("book", args, said))["error"]
    assert _pg_rows(url) == {"appointments": [], "events": []}
    _allow_inserts(url, "_alk_tool_responses")
    assert _post(address, _retell("book", args, said)) == {"appointment_id": "AS3", "idempotent": False}
    assert _pg_rows(url) == {"appointments": ["AS3"], "events": ["EV-AS3"]}


@pytest.mark.parametrize(
    ("transcript", "expected"),
    [
        ([_invocation("tc-1", "book", {"slot": "S1"})], "tc-1"),
        # The latest invocation is this one, an earlier identical one is not.
        ([_invocation("tc-1", "book", {"slot": "S1"}), {"role": "tool_call_result", "tool_call_id": "tc-1"}, _invocation("tc-2", "book", {"slot": "S1"})], "tc-2"),
        # Answered already: a request carrying it is not that invocation.
        ([_invocation("tc-1", "book", {"slot": "S1"}), {"role": "tool_call_result", "tool_call_id": "tc-1"}], None),
        ([_invocation("tc-1", "book", {"slot": "S2"})], None),
        ([_invocation("tc-1", "lookup", {"slot": "S1"})], None),
        ([{"role": "tool_call_invocation", "name": "book", "arguments": "{\"slot\": \"S1\"}"}], None),
        ([], None),
    ],
)
def test_the_retell_operation_id_is_only_this_invocation_s(transcript, expected) -> None:
    from fi.alk.harness.release_tool_server import retell_operation_id

    assert retell_operation_id(_retell("book", {"slot": "S1"}, transcript), "book", {"slot": "S1"}) == expected
    chat = {"name": "book", "args": {"slot": "S1"}, "chat": {"chat_id": "c1", "message_with_tool_calls": transcript}}
    assert retell_operation_id(chat, "book", {"slot": "S1"}) == expected
    assert retell_operation_id({"name": "book", "args": {"slot": "S1"}}, "book", {"slot": "S1"}) is None
