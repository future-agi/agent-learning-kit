"""Postgres, as the worked example of what an engine has to supply.

This is not "the database the harness supports". It is the reference: when the build stage
finds an agent on ClickHouse or MySQL or DuckDB, what it writes is a class this shape, and
what it has to work out is only what is in this file below ``boot_env`` -- how to reach the
engine, how to read what it holds, and how to put that back. Starting a container, finding a
free port, waiting for the thing to genuinely answer and not leaking it afterwards are all in
``ContainerStore`` and are never rewritten.

Nothing here knows what the agent's tools do. The agent keeps its own client, its own SQL and
its own migrations; the only thing that changed is the host on the far end of its DSN. The
schema is not invented either -- the build stage runs the agent's own migrations through
``apply``, so the tables are the agent's tables, spelled the way the agent spells them. A
schema we wrote ourselves would be a guess, and every check written against it would inherit
the guess.
"""

from __future__ import annotations

from typing import Any

from . import Snapshot, StoreError
from .container import ContainerStore


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise StoreError(
            "psycopg is not installed, so a Postgres store cannot be read. Install it with "
            "`uv sync --extra harness-stores`."
        ) from exc
    return psycopg


class PostgresStore(ContainerStore):
    """A Postgres container the agent under test is pointed at."""

    engine = "postgres"
    image = "postgres:16"
    container_port = 5432
    boot_env = {
        "POSTGRES_USER": "{user}",
        "POSTGRES_PASSWORD": "{password}",
        "POSTGRES_DB": "{database}",
    }

    # -- how to reach it -------------------------------------------------------------

    def dsn(self) -> str:
        host, port = self.address()
        return f"postgresql://{self.user}:{self.password}@{host}:{port}/{self.database}"

    def probe(self) -> None:
        """Really connect. A running container is not yet a database that listens."""
        with _psycopg().connect(self.dsn(), connect_timeout=3) as connection:
            connection.execute("SELECT 1")

    def _connect(self) -> Any:
        """A short-lived autocommit connection.

        Deliberately not pooled and never held open. An idle transaction of ours would block
        the ``TRUNCATE`` in ``restore``, and a reset that hangs on the harness's own connection
        is a very expensive thing to debug.
        """
        return _psycopg().connect(self.dsn(), autocommit=True)

    # -- how to read what it holds ---------------------------------------------------

    def apply(self, script: str) -> None:
        """Run whatever was handed in: the agent's migrations, or its seed."""
        if not script.strip():
            return
        with self._connect() as connection:
            connection.execute(script)

    def _tables(self, connection: Any) -> list[str]:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        ).fetchall()
        return [row[0] for row in rows]

    def _primary_key(self, connection: Any, table: str) -> list[str]:
        """The primary key columns, used only to read rows back in a stable order."""
        rows = connection.execute(
            """
            SELECT a.attname
              FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
             WHERE i.indrelid = %s::regclass AND i.indisprimary
             ORDER BY array_position(i.indkey, a.attnum)
            """,
            (f'public."{table}"',),
        ).fetchall()
        return [row[0] for row in rows]

    def state(self) -> dict[str, list[dict[str, Any]]]:
        """Every table and its rows, in the shape the checks already expect.

        Ordered by primary key where there is one. Without that the same data comes back in
        whatever order the heap happens to hold it, and a check comparing the first row is
        reading a coin toss rather than the agent's behaviour.
        """
        with self._connect() as connection:
            out: dict[str, list[dict[str, Any]]] = {}
            for table in self._tables(connection):
                key = self._primary_key(connection, table)
                order = (
                    " ORDER BY " + ", ".join(f'"{column}"' for column in key) if key else ""
                )
                cursor = connection.execute(f'SELECT * FROM "{table}"{order}')
                columns = [description[0] for description in cursor.description or []]
                out[table] = [dict(zip(columns, row)) for row in cursor.fetchall()]
            return out

    # -- how to put it back ----------------------------------------------------------

    def freeze(self) -> Snapshot:
        """Rows and sequence counters, which together are the whole mutable state."""
        with self._connect() as connection:
            counters = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT sequencename, last_value FROM pg_sequences "
                    "WHERE schemaname = 'public'"
                ).fetchall()
                if row[1] is not None
            }
        return Snapshot(rows=self.state(), counters=counters)

    def restore(self, snapshot: Snapshot) -> None:
        """Put the data back exactly as the snapshot found it.

        Foreign keys are suspended for the duration rather than the rows being sorted into
        dependency order: the snapshot was taken from a consistent database, so what goes back
        is consistent by construction, and ordering it would be solving a problem we do not
        have. Counters are set last, so the next scenario's first insert gets the id the first
        scenario's did.
        """
        with self._connect() as connection:
            tables = self._tables(connection)
            if not tables:
                return
            listed = ", ".join(f'"{table}"' for table in tables)
            # One statement, so Postgres resolves the dependency order between them itself.
            connection.execute(f"TRUNCATE TABLE {listed} RESTART IDENTITY CASCADE")

            connection.execute("SET session_replication_role = replica")
            try:
                for table, rows in snapshot.rows.items():
                    if not rows or table not in tables:
                        continue
                    columns = list(rows[0])
                    quoted = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join(["%s"] * len(columns))
                    statement = f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders})'
                    with connection.cursor() as cursor:
                        cursor.executemany(
                            statement,
                            [
                                tuple(_adapt(row.get(column)) for column in columns)
                                for row in rows
                            ],
                        )
            finally:
                connection.execute("SET session_replication_role = DEFAULT")

            for sequence, value in snapshot.counters.items():
                connection.execute(
                    "SELECT setval(%s, %s, true)", (f'public."{sequence}"', value)
                )


def _adapt(value: Any) -> Any:
    """Hand back a value in the form psycopg will write.

    Only json needs saying: a ``jsonb`` column reads back as a dict or a list, and handing
    either straight to an INSERT makes psycopg guess at a composite type instead.
    """
    if isinstance(value, (dict, list)):
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    return value
