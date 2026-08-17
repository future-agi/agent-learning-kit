"""A real Postgres, stood up in a container, for the agent's own queries to run against.

Nothing here knows what the agent's tools do. It stands up the engine the agent already uses,
hands back a connection string, and puts the data back between scenarios. The agent's client,
its SQL and its migrations are untouched -- the only thing that changed is the host on the far
end of its DSN.

The schema is not invented here either. Whoever builds the environment runs the agent's own
migrations through ``apply_sql``, so the tables are the agent's tables, spelled exactly as the
agent spells them. A schema we wrote ourselves would be a guess, and every check written
against it would inherit the guess.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import time
from typing import Any

from . import Snapshot, StoreError

DEFAULT_IMAGE = "postgres:16"
DEFAULT_DATABASE = "alk"
DEFAULT_USER = "postgres"

# How long to wait for a fresh container to start answering. First run on a machine pulls the
# image, which dominates; afterwards this is a second or two.
READY_TIMEOUT_SECONDS = 120.0

# Marks every container this module starts, so strays from a killed run can be found and
# removed without guessing at names.
LABEL = "alk.harness.store"


def _docker(*args: str, check: bool = True) -> str:
    """Run a docker command, and turn its failure into something worth reading."""
    try:
        done = subprocess.run(
            ("docker", *args), capture_output=True, text=True, check=False
        )
    except FileNotFoundError as exc:  # pragma: no cover - depends on the machine
        raise StoreError(
            "docker is not on PATH, so no store can be stood up. Install Docker, or start "
            "Colima, and try again."
        ) from exc
    if check and done.returncode != 0:
        raise StoreError(
            f"docker {' '.join(args)} failed ({done.returncode}): "
            f"{(done.stderr or done.stdout).strip()}"
        )
    return done.stdout.strip()


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise StoreError(
            "psycopg is not installed, so a Postgres store cannot be read. Install it with "
            "`uv sync --extra harness-stores`."
        ) from exc
    return psycopg


class PostgresStore:
    """A Postgres container the agent under test is pointed at.

    Started once for a suite and reset between scenarios. Starting costs seconds; putting the
    data back costs milliseconds, which is why the container stays up for the whole run and
    only its contents move.
    """

    engine = "postgres"

    def __init__(
        self,
        version: str = "16",
        image: str | None = None,
        database: str = DEFAULT_DATABASE,
    ) -> None:
        self.image = image or f"postgres:{version}"
        self.database = database
        self.password = secrets.token_hex(16)
        self.container = f"alk-store-{secrets.token_hex(6)}"
        self.host = "127.0.0.1"
        self.port: int | None = None
        self._started = False

    # -- lifecycle -------------------------------------------------------------------

    def start(self) -> None:
        """Stand the container up and block until it accepts a connection."""
        if self._started:
            return
        _docker(
            "run",
            "--detach",
            "--name",
            self.container,
            "--label",
            f"{LABEL}=1",
            "--env",
            f"POSTGRES_PASSWORD={self.password}",
            "--env",
            f"POSTGRES_DB={self.database}",
            # Bound to loopback and given whatever port is free, so parallel runs on one
            # machine never collide on 5432.
            "--publish",
            "127.0.0.1::5432",
            self.image,
        )
        self._started = True
        self.port = self._published_port()
        self._await_ready()

    def stop(self) -> None:
        """Remove the container. Safe when it never started, so teardown needs no guard."""
        if not self._started:
            return
        _docker("rm", "--force", "--volumes", self.container, check=False)
        self._started = False
        self.port = None

    def _published_port(self) -> int:
        mapping = _docker("port", self.container, "5432/tcp")
        if not mapping:
            raise StoreError(f"{self.container} published no port for 5432/tcp")
        # "127.0.0.1:32768", or several lines when both stacks are bound.
        return int(mapping.splitlines()[0].rsplit(":", 1)[1])

    def _await_ready(self) -> None:
        """Poll until the server answers, and say what went wrong if it never does.

        A container that is running is not a database that is ready: Postgres starts, runs its
        own init, restarts once, and only then listens. Connecting is the only honest test.
        """
        psycopg = _psycopg()
        deadline = time.monotonic() + READY_TIMEOUT_SECONDS
        last: Exception | None = None
        while time.monotonic() < deadline:
            try:
                with psycopg.connect(self.dsn(), connect_timeout=3) as connection:
                    connection.execute("SELECT 1")
                return
            except Exception as exc:  # noqa: BLE001 - any failure means not ready yet
                last = exc
                time.sleep(0.25)
        logs = _docker("logs", "--tail", "20", self.container, check=False)
        raise StoreError(
            f"{self.container} did not accept a connection within "
            f"{READY_TIMEOUT_SECONDS:.0f}s: {last}\nlast lines of its log:\n{logs}"
        )

    # -- what the agent is pointed at ------------------------------------------------

    def dsn(self) -> str:
        """The connection string to hand the agent, in place of its own."""
        if not self._started or self.port is None:
            raise StoreError("the store has not been started, so it has no address yet")
        return (
            f"postgresql://{DEFAULT_USER}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    def env(self, variable: str = "DATABASE_URL") -> dict[str, str]:
        """The DSN under the name this agent reads it from.

        Redirecting an agent is usually one environment variable, and which one is a fact about
        the agent rather than about us -- so it is named by the caller, not assumed here.
        """
        return {variable: self.dsn()}

    def _connect(self) -> Any:
        """A short-lived autocommit connection.

        Deliberately not pooled and never held open. An idle transaction of ours would block
        the ``TRUNCATE`` in ``restore``, and a reset that hangs on the harness's own connection
        is a very expensive thing to debug.
        """
        return _psycopg().connect(self.dsn(), autocommit=True)

    # -- contents --------------------------------------------------------------------

    def apply_sql(self, sql: str) -> None:
        """Run whatever statements were handed in: the agent's migrations, or its seed."""
        if not sql.strip():
            return
        with self._connect() as connection:
            connection.execute(sql)

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

    # -- freeze and restore ----------------------------------------------------------

    def freeze(self) -> Snapshot:
        """Capture rows and sequence counters, which together are the whole mutable state."""
        with self._connect() as connection:
            sequences = {
                row[0]: row[1]
                for row in connection.execute(
                    "SELECT sequencename, last_value FROM pg_sequences "
                    "WHERE schemaname = 'public'"
                ).fetchall()
                if row[1] is not None
            }
        return Snapshot(rows=self.state(), sequences=sequences)

    def restore(self, snapshot: Snapshot) -> None:
        """Put the data back exactly as the snapshot found it.

        Foreign keys are suspended for the duration rather than the rows being sorted into
        dependency order: the snapshot was taken from a consistent database, so what goes back
        is consistent by construction, and ordering it would be solving a problem we do not
        have. Counters are set last, so the next scenario's first insert gets the id the first
        scenario's did.
        """
        psycopg = _psycopg()
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

            for sequence, value in snapshot.sequences.items():
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


def strays() -> list[str]:
    """Containers this module started that are still running.

    A killed run leaves its container behind, and the next one has no way to know it is not
    the owner. Naming them is enough; removing them is the caller's decision.
    """
    listed = _docker(
        "ps", "--filter", f"label={LABEL}=1", "--format", "{{.Names}}", check=False
    )
    return [name for name in listed.splitlines() if name.strip()]
