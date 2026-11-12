"""One tool call is one transaction, and calls to one world take turns.

A booking writes an appointment and its calendar event. Whatever fails between the two, and
however long the store keeps refusing writes, a failed call leaves nothing behind: the store
rolls it back, which needs no writes of its own. Two calls at once cannot undo each other,
because the second waits for the first. A refusal the world simulates on purpose
(`keep_writes`: a rival booking, a commit whose reply was lost) is the one failure that keeps
what it wrote, in process, in the isolated handler child and on PostgreSQL alike.

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
    world.isolated = bool(isolation)
    refused = world.call("rival", {"slot": "S1"})
    assert not refused.ok and _rows(world) == {"appointments": [], "events": []}
    kept = world.call("rival", {"slot": "S1", "kept": True})
    assert not kept.ok
    assert len(world.state()["appointments"]) == 1 and len(world.state()["events"]) == 1


# -- PostgreSQL: the path world_mcp and the release tool server take ---------------------------

PG = os.environ.get("ALK_TEST_POSTGRES_URL", "")


@pytest.fixture()
def pg_world():
    if not PG:
        pytest.skip("ALK_TEST_POSTGRES_URL is not set")
    import psycopg
    from psycopg import conninfo

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
