"""A PostgreSQL proof runs authored code as a login that reads and changes its world's rows and
nothing else: a superuser's session reads the server's files and runs its programs."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from fi.alk.harness.releases import PolicyProfile, PostgresProof
from fi.alk.harness.scenario import Scenario, Step

DSN = os.environ.get("ALK_RELEASE_POSTGRES_DSN", "")
pytestmark = pytest.mark.skipif(not DSN, reason="set ALK_RELEASE_POSTGRES_DSN")

HANDLERS = {
    "add_order": (
        "def handle(args, db):\n"
        "    db.execute('INSERT INTO orders (order_id, status) VALUES (?, ?)', [args['id'], 'open'])\n"
        "    db.execute('UPDATE orders SET status = ? WHERE order_id = ?', ['closed', args['id']])\n"
        "    db.execute('DELETE FROM orders WHERE order_id = ?', ['O1'])\n"
        "    return db.query('SELECT * FROM orders')\n"
    ),
    "read_file": (
        "def handle(args, db):\n"
        "    return db.one('SELECT pg_read_file(?) AS f', [args['id']])\n"
    ),
    "run_program": (
        "def handle(args, db):\n"
        "    db.execute(\"COPY orders TO PROGRAM 'cat'\")\n"
    ),
    "make_role": (
        "def handle(args, db):\n    db.execute('CREATE ROLE planted')\n"
    ),
}


@pytest.fixture(scope="module")
def proof(tmp_path_factory):
    root = tmp_path_factory.mktemp("release")
    environment, suite = root / "environment", root / "suite"
    (environment / "world" / "handlers").mkdir(parents=True)
    suite.mkdir()
    with sqlite3.connect(environment / "world" / "world.sqlite") as connection:
        connection.executescript(
            "CREATE TABLE environment_policy (name TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE orders (order_id TEXT PRIMARY KEY, status TEXT);"
            "INSERT INTO orders VALUES ('O1', 'open');"
        )
    (environment / "policy.json").write_text('{"rules": []}')
    (suite / "sub_goals.json").write_text('{"sub_goals": []}')
    (environment / "tools.json").write_text(
        json.dumps(
            [
                {
                    "name": name,
                    "description": name,
                    "mutates_state": True,
                    "parameters": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}},
                    },
                }
                for name in HANDLERS
            ]
        )
    )
    for name, source in HANDLERS.items():
        (environment / "world" / "handlers" / f"{name}.py").write_text(source)
    with PostgresProof(suite, environment, PolicyProfile(), DSN) as opened:
        yield opened


def test_a_handler_reads_and_changes_its_worlds_rows(proof):
    played = proof.attempt(Scenario(name="s"), [("add_order", {"id": "O2"})])

    assert [outcome.status for outcome in played.outcomes] == ["ok"], played.faults
    assert played.outcomes[0].result == [{"order_id": "O2", "status": "closed"}]


@pytest.mark.parametrize("tool", ["read_file", "run_program", "make_role"])
def test_a_handler_reaches_nothing_beyond_its_world(proof, tmp_path: Path, tool):
    canary = tmp_path / "canary"
    canary.write_text("host-file")

    played = proof.attempt(Scenario(name="s"), [(tool, {"id": str(canary)})])

    (outcome,) = played.outcomes
    assert outcome.status == "fault" and "permission denied" in outcome.error
    assert "host-file" not in str(outcome.result)


def test_setup_reads_and_changes_rows_and_cannot_read_a_file(proof, tmp_path: Path):
    canary = tmp_path / "canary"
    canary.write_text("host-file")
    changes = Scenario(
        name="s",
        setup_code=(
            "def setup(world):\n"
            "    world.put('orders', {'order_id': 'O3', 'status': 'open'})\n"
            "    world.change('orders', 'O1', {'status': 'held'}, by='order_id')\n"
        ),
        solution=[Step(tool="add_order", arguments={"id": "O2"})],
    )
    reads = Scenario(
        name="s",
        setup_code=f"def setup(world):\n    world.query(\"SELECT pg_read_file('{canary}')\")\n",
    )

    assert proof.attempt(changes, []).ready.ok
    refused = proof.attempt(reads, []).ready
    assert not refused.ok and "pg_read_file" in refused.said


def test_the_login_goes_with_the_proof(proof):
    import psycopg

    def logins() -> list[tuple]:
        with psycopg.connect(DSN) as connection:
            return connection.execute(
                "SELECT rolsuper, rolcreaterole, rolcreatedb FROM pg_roles WHERE rolname = %s",
                (other._login,),
            ).fetchall()

    other = PostgresProof(
        proof._environment_root.parent / "suite",
        proof._environment_root,
        PolicyProfile(),
        DSN,
    )
    with other:
        assert logins() == [(False, False, False)]
    assert logins() == []
