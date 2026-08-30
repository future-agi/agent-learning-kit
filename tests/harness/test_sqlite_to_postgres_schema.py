"""Translating the authored SQLite world into the Postgres the hosted lane seeds.

Everything here is about the same thing: the seeded world has to be the schema the agent's own
code runs against. A constraint that survives authoring and is dropped in translation gives a
world that accepts rows the real one rejects, and a scenario then grades behaviour the agent could
not actually produce.

The failure that prompted this is `PRAGMA table_info`'s `pk` column, which is not a flag. It is
the column's 1-based POSITION in the key, so a two-column key has pk=1 and pk=2, both truthy, and
reading it as a boolean emitted one column-level PRIMARY KEY per column. Postgres rejects the
table outright. Single-column keys were fine, which is why it took a second agent shape to find:
the first agent tested had no composite key anywhere.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from fi.alk.harness.bundle_author_v2 import _sqlite_sql


def _world(tmp_path: Path, *statements: str, rows: tuple[str, ...] = ()) -> Path:
    path = tmp_path / "world.sqlite"
    connection = sqlite3.connect(path)
    for statement in statements:
        connection.execute(statement)
    for row in rows:
        connection.execute(row)
    connection.commit()
    connection.close()
    return path


def _create(sql: str, table: str) -> str:
    prefix = f'CREATE TABLE IF NOT EXISTS "{table}"'
    return next(line for line in sql.splitlines() if line.startswith(prefix))


COMPOSITE = """CREATE TABLE shares (
    note_id TEXT NOT NULL,
    with_user TEXT NOT NULL,
    can_edit INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (note_id, with_user)
)"""
SINGLE = "CREATE TABLE notes (id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT DEFAULT 'untitled')"


def test_a_composite_key_becomes_one_table_level_constraint(tmp_path):
    """`multiple primary keys for table "shares" are not allowed` -- verified against a real
    Postgres 16 as well as asserted here."""
    line = _create(_sqlite_sql(_world(tmp_path, COMPOSITE)), "shares")
    assert line.count("PRIMARY KEY") == 1, line
    assert 'PRIMARY KEY ("note_id", "with_user")' in line
    # Not attached to a column: that is the form Postgres refuses to have twice.
    assert '"note_id" text PRIMARY KEY' not in line


def test_the_key_keeps_the_order_it_was_declared_in(tmp_path):
    """`pk` is a position, so it has to be sorted by. A key is ordered, and reversing it changes
    which lookups the index serves."""
    reversed_key = """CREATE TABLE pairs (
        second TEXT NOT NULL, first TEXT NOT NULL, PRIMARY KEY (first, second)
    )"""
    line = _create(_sqlite_sql(_world(tmp_path, reversed_key)), "pairs")
    # Declared (first, second) though the columns appear in the other order in the table.
    assert 'PRIMARY KEY ("first", "second")' in line


def test_a_single_column_key_still_works(tmp_path):
    """The common case, and the one already in production. Same table-level form for both, because
    one form is one thing to get right."""
    line = _create(_sqlite_sql(_world(tmp_path, SINGLE)), "notes")
    assert line.count("PRIMARY KEY") == 1
    assert 'PRIMARY KEY ("id")' in line


def test_not_null_survives_translation(tmp_path):
    """A world that accepts a NULL the real schema rejects is not the agent's world."""
    line = _create(_sqlite_sql(_world(tmp_path, SINGLE)), "notes")
    assert '"owner" text NOT NULL' in line
    assert '"title" text DEFAULT' in line  # and nullable, as declared
    assert '"title" text NOT NULL' not in line


def test_a_default_survives_translation(tmp_path):
    line = _create(_sqlite_sql(_world(tmp_path, SINGLE)), "notes")
    assert "DEFAULT 'untitled'" in line
    numeric = _create(_sqlite_sql(_world(tmp_path, COMPOSITE)), "shares")
    assert '"can_edit" bigint NOT NULL DEFAULT 0' in numeric


def test_a_default_that_does_not_translate_is_dropped_out_loud(tmp_path, caplog):
    """A SQLite expression has no Postgres meaning, and guessing at one would put values in the
    world the real schema never produces. Dropping it is right; dropping it silently is how the
    world quietly stops matching."""
    import fi.alk.harness.bundle_author_v2 as module

    odd = "CREATE TABLE t (id TEXT PRIMARY KEY, blob TEXT DEFAULT (hex(randomblob(4))))"
    with caplog.at_level(logging.WARNING, logger=module.__name__):
        line = _create(_sqlite_sql(_world(tmp_path, odd)), "t")
    assert "DEFAULT" not in line.split('"blob" text')[1].split(",")[0]
    assert "does not translate to Postgres" in caplog.text
    assert "t.blob" in caplog.text


def test_portable_defaults_are_kept(tmp_path):
    stamped = "CREATE TABLE t (id TEXT PRIMARY KEY, made_at TEXT DEFAULT CURRENT_TIMESTAMP)"
    assert "DEFAULT CURRENT_TIMESTAMP" in _create(_sqlite_sql(_world(tmp_path, stamped)), "t")


def test_the_rows_still_come_with_the_schema(tmp_path):
    """The seed is the schema and its data; a fix to one must not drop the other."""
    sql = _sqlite_sql(
        _world(tmp_path, COMPOSITE, rows=("INSERT INTO shares VALUES ('n1','dave_k',0)",))
    )
    assert 'INSERT INTO "shares" ("note_id", "with_user", "can_edit") VALUES' in sql


def test_every_table_declares_at_most_one_primary_key(tmp_path):
    """The invariant, over the whole emitted file rather than one table, so a table added to the
    fixture later is covered without anyone remembering to assert on it."""
    sql = _sqlite_sql(_world(tmp_path, COMPOSITE, SINGLE))
    creates = [line for line in sql.splitlines() if line.startswith("CREATE TABLE")]
    assert len(creates) == 2
    for line in creates:
        assert line.count("PRIMARY KEY") == 1, line
        assert re.search(r'PRIMARY KEY \("', line), line
