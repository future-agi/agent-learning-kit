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

import hashlib
import json
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...world_door import WORLD_ROLE_SESSION
from ..errors import WorldQueryRejected
from . import Held, Snapshot, StoreError, WorldResetRefused
from .catalog_digest import catalog_digest
from .container import ContainerStore, docker

if TYPE_CHECKING:
    from psycopg import Connection

SCHEMA = "schema.sql"


# The harness's own sessions: nothing a source or seed defines in `public` stands in for the system's code
# (`world_door.PINNED_SEARCH_PATH`).
PINNED_SEARCH_PATH = "-c search_path=pg_catalog,pg_temp"
# A session that runs the world's own SQL (a handler's, a check's) where there is no world role: the system's own
# functions are found first, and the statement still finds the world's tables.
_SYSTEM_FIRST = "-c search_path=pg_catalog,public,pg_temp"
# Where a reset's put-back finds the world's own functions: after the system's.
_WORLD_CODE_PATH = "pg_catalog, public, pg_temp"


# A world trigger that fires even with triggers suspended (replica mode): a reset as the harness would run it.
_TRIGGERS_FIRING_ANYWAY = (
    "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND NOT t.tgisinternal AND t.tgenabled IN ('A', 'R') "
    "UNION ALL SELECT c.relname, r.rulename FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class "
    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = 'public' AND r.ev_enabled IN ('A', 'R')"
)
# Each foreign key between two of the world's tables: (child, parent).
_FOREIGN_KEYS = (
    "SELECT cn.nspname, c.relname, pn.nspname, p.relname FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid "
    "JOIN pg_class p ON p.oid = k.confrelid JOIN pg_namespace cn ON cn.oid = c.relnamespace "
    "JOIN pg_namespace pn ON pn.oid = p.relnamespace WHERE k.contype = 'f'"
)
# Every schema of the world's own: a reset puts back every one, not only `public`.
_CUSTOMER_SCHEMA = "schemaname NOT IN ('pg_catalog', 'information_schema') AND schemaname NOT LIKE 'pg\\_%'"


def _as_multiset(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Rows compared whatever order they come back in (a table may have no key to order by)."""
    return sorted(repr(sorted(row.items())) for row in rows)


def _key(schema: str, table: str) -> str:
    """A table's key in a snapshot: its bare name in `public`, `schema.table` elsewhere."""
    return table if schema == "public" else f"{schema}.{table}"


def _ident(schema: str, name: str) -> str:
    return '"' + schema.replace('"', '""') + '"."' + name.replace('"', '""') + '"'


def attached_world_store(endpoint: Any) -> AttachedPostgresStore:
    """The store over a leased world's database. With world roles, every statement runs as the world's own role, its
    reset included, so no SQL of the world's (a handler's, a check's, a customer trigger's) ever runs as the harness,
    and a world without a role is refused. With one fixed user (no roles) it runs as the harness, with the system's
    own functions first; a reset then suspends the world's triggers and refuses one set to fire anyway."""
    from ...privilege import IsolationMode, isolation_mode

    if isolation_mode() is IsolationMode.PER_USER:
        if not all(getattr(endpoint, name, None) for name in ("tool_address", "store_address", "log_address")):
            raise StoreError("no_world_role: a world's SQL runs only with its own roles, and none was given")
        return AttachedPostgresStore(
            endpoint.tool_address,
            restore_as_role=True,
            reader_dsn=endpoint.store_address,
            log_dsn=endpoint.log_address,
            roles_admin_dsn=endpoint.address,
            # The roles whose sessions run the agent's or the world's code: the agent's tools, the store role, and the
            # world's reader where the release's attached servers read the world (their code runs as it). The door and
            # the log reader run only the harness's own, and their long-lived sessions (the door's fault reads) are
            # left alone.
            role_dsns=tuple(
                one
                for one in (endpoint.tool_address, endpoint.store_address, getattr(endpoint, "reader_address", None))
                if one
            ),
        )
    return AttachedPostgresStore(system_first(endpoint.address), admin_dsn=endpoint.address)


def system_first(address: str) -> str:
    """``address`` with the system's own functions first on its search path, the world's tables still found."""
    from urllib.parse import quote

    joiner = "&" if "?" in address else "?"
    return f"{address}{joiner}options={quote(_SYSTEM_FIRST)}"


def _psycopg() -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise StoreError(
            "psycopg is not installed, so a Postgres store cannot be read. Install it with "
            "`uv sync --extra harness-stores`."
        ) from exc
    return psycopg


def _catalogue_text(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class PostgresStore(ContainerStore):
    """A Postgres container the agent under test is pointed at."""

    engine = "postgres"
    image = "postgres:16"
    container_port = 5432
    # With world roles only (`AttachedPostgresStore`): the harness's address for the world's roles, and theirs.
    _roles_admin_dsn: str | None = None
    _role_dsns: tuple[str, ...] = ()
    boot_env = {
        "POSTGRES_USER": "{user}",
        "POSTGRES_PASSWORD": "{password}",
        "POSTGRES_DB": "{database}",
    }

    # -- how to reach it -------------------------------------------------------------

    def dsn(self) -> str:
        external = os.environ.get("ALK_POSTGRES_DSN", "").strip()
        if external:
            return external
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
        # Remembered because the snapshot holds rows, not DDL. A restore into a fresh
        # container finds no tables, and restoring rows into a schema that is not there
        # quietly restores nothing.
        self.applied.append(script)

    def execute(self, statement: str, params: Sequence[Any] = ()) -> int:
        """Run one mutation and report the rows it actually changed."""
        if (held := getattr(self, "_held", None)) is not None:
            return max(0, int(held.execute(statement, tuple(params)).rowcount or 0))
        with self._connect() as connection:
            cursor = connection.execute(statement, tuple(params))
            return max(0, int(cursor.rowcount or 0))

    def hold(self) -> None:
        """Keep one tool call's statements in one transaction on one connection, until
        ``release``: its writes land together or not at all, and it reads what it wrote."""
        self._held = _psycopg().connect(self.dsn())

    def release(self, keep: bool) -> None:
        held, self._held = self._held, None
        try:
            if keep:
                held.commit()
            else:
                held.rollback()
        finally:
            held.close()

    def query(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Run one read statement, on a connection Postgres itself will not let write.

        Autocommit, with the session's own default flipped to read-only rather than one shared
        ``SET TRANSACTION READ ONLY`` transaction: every statement becomes its own implicit
        read-only transaction, so nothing here is ever held open, and ``reset``'s drop of the
        database can always proceed regardless of what a caller just read.

        An empty ``params`` tuple is passed through as ``None`` rather than as itself: psycopg
        scans for placeholders whenever it is handed anything other than ``None``, and an
        ordinary ``LIKE '%turkey%'`` with nothing to bind then reads its own ``%t`` as an
        unmatched one and raises before the statement ever reaches Postgres.
        """
        if (held := getattr(self, "_held", None)) is not None:
            return self._read_held(held, statement, params)
        # A check's read names the world's tables as the world's own SQL does: system functions first, read-only.
        return self._read(self._reader_address(), statement, params, _SYSTEM_FIRST)

    @staticmethod
    def _read_held(held: Any, statement: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        """Inside a held call: read on its own connection, so it sees what the call wrote."""
        cursor = held.execute(statement, tuple(params) if params else None)
        columns = [description[0] for description in cursor.description or []]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def read_log(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """A read of the harness's own tables (every name qualified) as the world's log reader, on the system's own
        path and read-only; the store's reader where there is none (one fixed user)."""
        log = getattr(self, "_log_dsn", None)
        if not log:
            return self.query(statement, params)
        return self._read(log, statement, params, PINNED_SEARCH_PATH)

    def query_as_world(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """``query`` as the agent's tools' own role, under the world's row security: a handler's read."""
        if (held := getattr(self, "_held", None)) is not None:
            return self._read_held(held, statement, params)
        return self._read(self.dsn(), statement, params, WORLD_ROLE_SESSION)

    def _reader_address(self) -> str:
        return getattr(self, "_reader_dsn", None) or self.dsn()

    def _reader_connect(self, writable: bool = False) -> Any:
        """The harness's own reads of the world: the store role where there is one, on the system's own search path
        (every statement it sends names its tables) and read-only unless it resets; else the store's connection."""
        reader = getattr(self, "_reader_dsn", None)
        if not reader:
            return self._connect()
        options = PINNED_SEARCH_PATH if writable else f"{PINNED_SEARCH_PATH} -c default_transaction_read_only=on"
        return _psycopg().connect(reader, autocommit=True, options=options)

    def _read(self, address: str, statement: str, params: Sequence[Any], path: str) -> list[dict[str, Any]]:
        with _psycopg().connect(
            address, autocommit=True, options=f"{path} -c default_transaction_read_only=on".strip()
        ) as connection:
            cursor = connection.execute(statement, tuple(params) if params else None)
            columns = [description[0] for description in cursor.description or []]
            seen: set[str] = set()
            for column in columns:
                if column in seen:
                    raise WorldQueryRejected(
                        f"query() returned more than one column named {column!r}; alias one "
                        "of them so a row does not silently lose one under the other."
                    )
                seen.add(column)
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def _tables(self, connection: Any) -> list[str]:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        ).fetchall()
        names = [_catalogue_text(row[0]) for row in rows]
        if getattr(self, "_restore_as_role", False):
            # The world's own tables: the harness's (its log, its marker) are no world role's to read or reset.
            names = [name for name in names if not name.startswith("_alk_") and name != "harness_seed_sentinel"]
        return names

    def _reset_tables(self, connection: Connection[Any]) -> list[tuple[str, str]]:
        """Every table of the world's own, in every customer schema: (schema, table). The harness's own are left out
        for a reset by the store role, which has no right on them."""
        rows = connection.execute(
            f"SELECT schemaname, tablename FROM pg_tables WHERE {_CUSTOMER_SCHEMA} ORDER BY schemaname, tablename"
        ).fetchall()
        tables = [(str(schema), str(table)) for schema, table in rows]
        if getattr(self, "_restore_as_role", False):
            tables = [
                (schema, table)
                for schema, table in tables
                if not (schema == "public" and (table.startswith("_alk_") or table == "harness_seed_sentinel"))
            ]
        return tables

    def _column_kinds(self, connection: Connection[Any], schema: str, table: str) -> dict[str, tuple[str, str, str]]:
        """Each column's (type, identity, generated): a stored generated column is never written back, and an
        identity column that is always generated takes its value back only with OVERRIDING SYSTEM VALUE."""
        rows = connection.execute(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attidentity::text, a.attgenerated::text "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped",
            (schema, table),
        ).fetchall()
        return {str(name): (str(kind), str(identity), str(generated)) for name, kind, identity, generated in rows}

    def _put_back(self, connection: Connection[Any], schema: str, table: str, rows: list[dict[str, Any]]) -> None:
        """A table's rows as the snapshot held them, generated columns left to the table and identity values kept."""
        if not rows:
            return
        kinds = self._column_kinds(connection, schema, table)
        types = self._column_types(connection, table, schema)
        columns = [column for column in rows[0] if kinds.get(column, ("", "", ""))[2] != "s"]
        overriding = any(kinds.get(column, ("", "", ""))[1] == "a" for column in columns)
        quoted = ", ".join('"' + column.replace('"', '""') + '"' for column in columns)
        placeholders = ", ".join(["%s"] * len(columns))
        statement = (
            f"INSERT INTO {_ident(schema, table)} ({quoted}) "  # noqa: S608 - listed names, quoted
            + ("OVERRIDING SYSTEM VALUE " if overriding else "")
            + f"VALUES ({placeholders})"
        )
        # The one statement of a reset that runs the world's own code (a check, an index expression): it finds the
        # world's functions by their bare names after the system's own . Every statement of the harness's own
        # runs on the system's path alone, so none of them reaches a function of the world's.
        connection.execute(f"SET LOCAL search_path = {_WORLD_CODE_PATH}")
        with connection.cursor() as cursor:
            cursor.executemany(
                statement,
                [tuple(_adapt(row.get(column), types.get(column, "")) for column in columns) for row in rows],
            )
        # Only on success: a failed statement ends the transaction, and its own error is the one to report.
        connection.execute("SET LOCAL search_path = pg_catalog, pg_temp")

    def _counters(self, connection: Connection[Any]) -> dict[str, Any]:
        rows = connection.execute(
            f"SELECT schemaname, sequencename, last_value FROM pg_sequences WHERE {_CUSTOMER_SCHEMA}"
        ).fetchall()
        return {
            _key(str(schema), str(sequence)): value
            for schema, sequence, value in rows
            if value is not None
            and not (getattr(self, "_restore_as_role", False) and schema == "public" and str(sequence).startswith("_alk_"))
        }

    def _set_counters(self, connection: Connection[Any], counters: dict[str, Any]) -> None:
        """Each of the world's sequences back where the snapshot found it; one the snapshot never saw used, to its
        start."""
        for schema, sequence, start in connection.execute(
            f"SELECT schemaname, sequencename, start_value FROM pg_sequences WHERE {_CUSTOMER_SCHEMA}"
        ).fetchall():
            if getattr(self, "_restore_as_role", False) and schema == "public" and str(sequence).startswith("_alk_"):
                continue
            value = counters.get(_key(str(schema), str(sequence)))
            connection.execute(
                "SELECT pg_catalog.setval(%s::regclass, %s, %s)",
                (_ident(str(schema), str(sequence)), value if value is not None else start, value is not None),
            )

    def _primary_key(self, connection: Connection[Any], table: str, schema: str = "public") -> list[str]:
        """The primary key columns, used only to read rows back in a stable order.

        Joined through ``pg_class``/``pg_namespace`` rather than a ``%s::regclass`` cast over an
        f-string, so a table name is only ever a bound value — an embedded ``"`` (a table
        created as ``CREATE TABLE "we""ird" (...)``) is just a character in that value instead
        of something a regclass cast has to parse.
        """
        rows = connection.execute(
            """
            SELECT a.attname
              FROM pg_index i
              JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
              JOIN pg_class c ON c.oid = i.indrelid
              JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = %s AND c.relname = %s AND i.indisprimary
             ORDER BY array_position(i.indkey, a.attnum)
            """,
            (schema, table),
        ).fetchall()
        return [_catalogue_text(row[0]) for row in rows]

    def _column_types(self, connection: Connection[Any], table: str, schema: str = "public") -> dict[str, str]:
        """Return declared column types so writes use the source schema's representation.

        New managed clusters are always UTF-8, but an externally attached or older SQL_ASCII
        database can return even information-schema text as ``bytes``. Decode the catalogue
        defensively: silently missing every lookup would disable all type adaptation and turn one
        environment detail into a sequence of misleading per-column database failures.
        """
        rows = connection.execute(
            """
            SELECT column_name, data_type
              FROM information_schema.columns
             WHERE table_schema = %s AND table_name = %s
            """,
            (schema, table),
        ).fetchall()

        return {_catalogue_text(row[0]): _catalogue_text(row[1]) for row in rows}

    def _select_ordered(self, connection: Connection[Any], table: str, schema: str = "public") -> list[dict[str, Any]]:
        """Every row of one table, ordered by its primary key where it has one.

        Without that order the same data comes back in whatever sequence the heap happens to
        hold it, and a check comparing the first row is reading a coin toss rather than the
        agent's behaviour. Built with ``sql.Identifier`` rather than an f-string because
        ``table`` is a name the harness only just read out of the catalogue, not a literal it
        wrote itself.
        """
        sql = _psycopg().sql
        key = self._primary_key(connection, table, schema)
        statement = sql.SQL("SELECT * FROM {}").format(sql.Identifier(schema, table))
        if key:
            statement += sql.SQL(" ORDER BY ") + sql.SQL(", ").join(
                sql.Identifier(column) for column in key
            )
        cursor = connection.execute(statement)
        columns = [description[0] for description in cursor.description or []]
        return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def state(
        self, only: Sequence[str] | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Every table and its rows, in the shape the checks already expect.

        ``only`` narrows the read to the named tables, still inside the one connection — a
        caller that already knows it wants a subset (``HostedWorld`` excluding over-cap tables)
        never pays to read and discard rows for the ones it does not. ``None`` reads every table
        in ``public``, exactly as before.
        """
        with self._reader_connect() as connection:
            tables = self._tables(connection) if only is None else list(only)
            return {table: self._select_ordered(connection, table) for table in tables}

    def columns(self, name: str) -> list[str]:
        """One table's columns, as the database's catalogue lists them."""
        with self._reader_connect() as connection:
            return list(self._column_types(connection, name))

    def table(self, name: str) -> list[dict[str, Any]]:
        """One table's rows, ordered by primary key where it has one.

        The read-side counterpart to ``state()``'s per-table loop, for a caller that wants only
        one of them: still a single connection, so reading one table never costs a second round
        trip just to learn how to order it.
        """
        with self._reader_connect() as connection:
            return self._select_ordered(connection, name)

    # -- how to put it back ----------------------------------------------------------

    def _admin_connect(self) -> Any:
        """The harness's own connection for what only it may do (a snapshot, a restore), on the system's own search
        path; the store's connection when it has no other."""
        admin = getattr(self, "_admin_dsn", None)
        if not admin:
            return self._reader_connect()
        return _psycopg().connect(admin, autocommit=True, options=PINNED_SEARCH_PATH)

    def freeze(self) -> Snapshot:
        """Rows and sequence counters, which together are the whole mutable state, and the digest of every
        definition a reset must leave as it found it."""
        with self._admin_connect() as connection:
            counters = self._counters(connection)
            rows = {
                _key(schema, table): self._select_ordered(connection, table, schema)
                for schema, table in self._reset_tables(connection)
            }
            with connection.transaction():
                catalog = catalog_digest(connection)
        roles = self._world_roles_state()
        if roles is not None:
            catalog["world roles"] = roles
        return Snapshot(rows=rows, counters=counters, catalog=catalog)


    def _verify_reset(self, connection: Connection[Any], snapshot: Snapshot, tables: list[tuple[str, str]]) -> None:
        # Deferred checks of the world's fire now, before what they did is read, never at the commit after it.
        connection.execute("SET CONSTRAINTS ALL IMMEDIATE")
        """Before a reset commits: the world as its snapshot held it, every table's rows (their count, for rows read
        from disk) and every definition, or nothing of the reset is kept and the world is refused. The world's own
        code a reset runs (a check, an index expression) is what could leave it otherwise."""
        for schema, table in tables:
            held = snapshot.rows.get(_key(schema, table)) or []
            now = self._select_ordered(connection, table, schema)
            same = _as_multiset(now) == _as_multiset(held) if snapshot.catalog else len(now) == len(held)
            if not same:
                raise WorldResetRefused(
                    f"the reset left {schema}.{table} other than its snapshot ({len(now)} rows, {len(held)} expected): "
                    "the world's own code changed it while the reset ran"
                )
        if snapshot.catalog:
            changed = sorted(
                kind for kind, digest in catalog_digest(connection).items() if snapshot.catalog.get(kind) != digest
            )
            if changed:
                raise WorldResetRefused(
                    "the world's definitions changed since its snapshot (" + ", ".join(changed) + "): the reset is "
                    "rolled back and the world is not reused"
                )

    def restore(self, snapshot: Snapshot) -> None:
        """Put the data back exactly as the snapshot found it.

        Foreign keys are suspended for the duration rather than the rows being sorted into
        dependency order: the snapshot was taken from a consistent database, so what goes back
        is consistent by construction, and ordering it would be solving a problem we do not
        have. Counters are set last, so the next scenario's first insert gets the id the first
        scenario's did.
        """
        try:
            if getattr(self, "_restore_as_role", False):
                self._restore_as_role_itself(snapshot)
            else:
                self._restore_as_admin(snapshot)
        except _psycopg().Error as error:
            # Whatever stopped it (a table no role of ours may empty, the world's own code failing), the world is
            # not as its snapshot held it: ours to replace, never graded.
            raise WorldResetRefused(f"the world could not be reset: {error}") from error

    def _restore_as_admin(self, snapshot: Snapshot) -> None:
        with self._admin_connect() as connection:
            tables = self._reset_tables(connection)
            if not tables:
                return
            firing = connection.execute(_TRIGGERS_FIRING_ANYWAY).fetchall()
            if firing:
                raise StoreError(
                    "a reset would run the world's own triggers as the harness: "
                    + ", ".join(sorted(f"{table}.{trigger}" for table, trigger in firing))
                    + " are set to fire in replica mode (ENABLE ALWAYS or ENABLE REPLICA)"
                )
            listed = ", ".join(_ident(schema, table) for schema, table in tables)
            with connection.transaction():
                # Suspended before the truncate too, so neither it nor a row put back fires a trigger of the world's.
                connection.execute("SET LOCAL session_replication_role = replica")
                # One statement, so Postgres resolves the dependency order between them itself.
                connection.execute(f"TRUNCATE TABLE {listed} RESTART IDENTITY CASCADE")
                for schema, table in tables:
                    self._put_back(connection, schema, table, snapshot.rows.get(_key(schema, table)) or [])
                self._set_counters(connection, snapshot.counters)
                self._verify_reset(connection, snapshot, tables)

    def _restore_as_role_itself(self, snapshot: Snapshot) -> None:
        """A reset by the world's store role: its rows bypass row security (the role's own BYPASSRLS), and the world's
        code a reset runs (a check, an index expression) has that role's rights only, which create and alter
        nothing. Every row deleted and put back in foreign-key order inside one transaction, with the world's
        triggers and foreign-key checks suspended and each sequence set back; checked against the snapshot before it
        commits (`_verify_reset`)."""
        with self._reader_connect(writable=True) as connection:
            tables = self._reset_tables(connection)
            if not tables:
                return
            firing = connection.execute(_TRIGGERS_FIRING_ANYWAY).fetchall()
            if firing:
                raise StoreError(
                    "a reset would run the world's own triggers: "
                    + ", ".join(sorted(f"{table}.{trigger}" for table, trigger in firing))
                    + " are set to fire in replica mode (ENABLE ALWAYS or ENABLE REPLICA)"
                )
            parents: dict[tuple[str, str], set[tuple[str, str]]] = {table: set() for table in tables}
            for child_schema, child, parent_schema, parent in connection.execute(_FOREIGN_KEYS).fetchall():
                one, other = (str(child_schema), str(child)), (str(parent_schema), str(parent))
                if one in parents and other in parents and one != other:
                    parents[one].add(other)
            order: list[tuple[str, str]] = []
            while len(order) < len(tables):
                ready = [t for t in tables if t not in order and parents[t] <= set(order)]
                order.extend(ready or [t for t in tables if t not in order])
            with connection.transaction():
                # The store role's every session starts with the world's triggers suspended, and it may not change
                # that: a role without the setting (made by an older harness) resets nothing.
                if getattr(self, "_reader_dsn", None) and connection.execute(
                    "SELECT current_setting('session_replication_role')"
                ).fetchone() != ("replica",):
                    raise WorldResetRefused("the store role does not start with the world's triggers suspended")
                for schema, table in reversed(order):
                    connection.execute(f"DELETE FROM {_ident(schema, table)}")  # noqa: S608 - a listed table
                for schema, table in order:
                    self._put_back(connection, schema, table, snapshot.rows.get(_key(schema, table)) or [])
                self._set_counters(connection, snapshot.counters)
                self._verify_reset(connection, snapshot, tables)
        self._settle_world_roles(snapshot)

    def _store_role_name(self) -> str | None:
        user = _psycopg().conninfo.conninfo_to_dict(getattr(self, "_reader_dsn", None) or "").get("user")
        return str(user) if user else None

    def _world_role_names(self) -> list[str]:
        conninfo = _psycopg().conninfo.conninfo_to_dict
        return sorted({str(conninfo(dsn).get("user")) for dsn in self._role_dsns if conninfo(dsn).get("user")})

    def _world_roles_state(self) -> str | None:
        """The world's own roles (the agent's and the store role) as the harness set them, each one's password
        verifier and settings, read as the harness: any role may change its own password and settings, so the world's
        code (a check the reset ran, the agent's own SQL) could have, and a reset that does not see it would carry it
        to the next trial. The verifiers are kept here, never in the snapshot."""
        if not self._roles_admin_dsn or not self._role_dsns:
            return None
        names = self._world_role_names()
        with _psycopg().connect(self._roles_admin_dsn, autocommit=True, options=PINNED_SEARCH_PATH) as connection:
            rows = connection.execute(
                "SELECT a.rolname, coalesce(a.rolpassword, ''), coalesce((SELECT string_agg(concat_ws(':', "
                "s.setdatabase, s.setconfig::text), ',' ORDER BY s.setdatabase) FROM pg_db_role_setting s "
                "WHERE s.setrole = a.oid), '') FROM pg_authid a WHERE a.rolname = ANY(%s) ORDER BY a.rolname",
                (names,),
            ).fetchall()
        for name, verifier, _settings in rows:
            self._verifiers.setdefault(str(name), str(verifier))
        return hashlib.sha256(repr(rows).encode()).hexdigest()

    def _settle_world_roles(self, snapshot: Snapshot) -> None:
        """After a reset commits: the world's own roles put back as the snapshot found them, as the harness. Their
        settings cleared, each password set back (its verifier, never the password itself), and every session of
        theirs ended, so none keeps what was set meanwhile. Nothing changed, nothing done."""
        held = snapshot.catalog.get("world roles")
        if held is None or self._world_roles_state() == held:
            return
        sql = _psycopg().sql
        names = self._world_role_names()
        with _psycopg().connect(self._roles_admin_dsn, autocommit=True, options=PINNED_SEARCH_PATH) as connection:
            for name in names:
                role = sql.Identifier(name)
                connection.execute(sql.SQL("ALTER ROLE {} RESET ALL").format(role))
                for (database,) in connection.execute(
                    "SELECT d.datname FROM pg_db_role_setting s JOIN pg_database d ON d.oid = s.setdatabase "
                    "WHERE s.setrole = %s::regrole",
                    (name,),
                ).fetchall():
                    connection.execute(
                        sql.SQL("ALTER ROLE {} IN DATABASE {} RESET ALL").format(role, sql.Identifier(str(database)))
                    )
                if name == self._store_role_name():
                    connection.execute(
                        sql.SQL("ALTER ROLE {} SET session_replication_role = replica").format(role)
                    )
                if self._verifiers.get(name):
                    connection.execute(
                        sql.SQL("ALTER ROLE {} PASSWORD {}").format(role, sql.Literal(self._verifiers[name]))
                    )
            connection.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename = ANY(%s) "
                "AND pid <> pg_backend_pid()",
                (names,),
            )
        if self._world_roles_state() != held:
            raise WorldResetRefused("the world's own roles could not be put back as the harness set them")

    def save_to(self, path: str | Path) -> None:
        """Save both the records and the DDL a fresh Postgres store needs."""
        Held.save_to(self, path)
        root = Path(path)
        schema = docker(
            "exec",
            self.container,
            "pg_dump",
            "--schema-only",
            "--no-owner",
            "--no-privileges",
            "--username",
            self.user,
            "--dbname",
            self.database,
        )
        # pg_dump can emit psql-only safety commands. The snapshot is replayed through psycopg,
        # so keep SQL and discard client meta-commands.
        schema = "\n".join(
            line for line in schema.splitlines() if not line.startswith("\\")
        )
        (root / SCHEMA).write_text(schema, encoding="utf-8")

    def load_from(self, path: str | Path) -> None:
        root = Path(path)
        schema = root / SCHEMA
        if not schema.exists():
            raise StoreError(f"no saved Postgres schema at {schema}")
        self.apply(schema.read_text(encoding="utf-8"))
        Held.load_from(self, root)

    # -- what a scenario changes -----------------------------------------------------

    def add(self, collection: str, record: Any) -> dict[str, Any]:
        """Insert one record and hand back exactly what Postgres stored.

        ``RETURNING *`` rather than a second read: a caller after the row's generated key (an
        identity column, a default, a trigger) would otherwise have to guess which column that
        is, and a table with no natural way to re-select the row it just inserted could not be
        read back at all.
        """
        columns = list(record)
        sql = _psycopg().sql
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
            sql.Identifier(collection),
            sql.SQL(", ").join(sql.Identifier(column) for column in columns),
            sql.SQL(", ").join(sql.SQL("%s") for _ in columns),
        )
        with self._connect() as connection:
            types = self._column_types(connection, collection)
            cursor = connection.execute(
                statement,
                tuple(
                    _adapt(record[column], types.get(column, "")) for column in columns
                ),
            )
            stored = cursor.fetchone()
            if stored is None:
                raise StoreError(
                    f"INSERT INTO {collection!r} ... RETURNING * came back with no row; a rule "
                    "or a BEFORE INSERT trigger the agent's own migrations declared can turn an "
                    "insert into a no-op, and put() cannot report a record that was never "
                    "written."
                )
            out = [description[0] for description in cursor.description or []]
            self._advance_sequences(connection, collection, columns)
            return dict(zip(out, stored, strict=True))

    @staticmethod
    def _advance_sequences(
        connection: Any, collection: str, columns: list[str]
    ) -> None:
        """Move each sequence behind a column the record gave a value for past the table's highest value, so the
        next record that takes its key from the sequence never draws one already stored. Never moves one back."""
        sql = _psycopg().sql
        table = sql.Identifier(collection).as_string(connection)
        for column, sequence in connection.execute(
            "SELECT name, pg_get_serial_sequence(%s, name) FROM unnest(%s::text[]) AS name",
            (table, columns),
        ).fetchall():
            if sequence is None:
                continue
            connection.execute(
                sql.SQL(
                    "SELECT setval(%s::regclass, "
                    "GREATEST(MAX({}), COALESCE(pg_sequence_last_value(%s::regclass), 1))) FROM {}"
                ).format(sql.Identifier(column), sql.Identifier(collection)),
                (sequence, sequence),
            )

    def amend(self, collection: str, key: str, changes: Any, *, by: str = "") -> int:
        if not by:
            raise StoreError(
                f"{collection} is a table, so changing a record needs the column it is keyed on"
            )
        sql = _psycopg().sql
        statement = sql.SQL("UPDATE {} SET {} WHERE {} = %s").format(
            sql.Identifier(collection),
            sql.SQL(", ").join(
                sql.SQL("{} = %s").format(sql.Identifier(column)) for column in changes
            ),
            sql.Identifier(by),
        )
        with self._connect() as connection:
            types = self._column_types(connection, collection)
            cursor = connection.execute(
                statement,
                (
                    *(
                        _adapt(value, types.get(column, ""))
                        for column, value in changes.items()
                    ),
                    key,
                ),
            )
            return cursor.rowcount

    def remove(self, collection: str, key: str = "", *, by: str = "") -> int:
        if key and not by:
            raise StoreError(
                f"{collection} is a table, so removing one record needs the column it is keyed on"
            )
        sql = _psycopg().sql
        statement = sql.SQL("DELETE FROM {}").format(sql.Identifier(collection))
        if key:
            statement += sql.SQL(" WHERE {} = %s").format(sql.Identifier(by))
        with self._connect() as connection:
            cursor = connection.execute(statement, (key,) if key else ())
            return cursor.rowcount


class AttachedPostgresStore(PostgresStore):
    """A Postgres store already started by the submitted repository's Compose project.

    The record and snapshot operations are exactly the same as ``PostgresStore``. Only ownership
    differs: the harness may inspect and reset this database, but the Compose provisioner owns
    its process and lifecycle, so closing one scenario must never remove the shared container.
    """

    def __init__(
        self,
        dsn: str,
        admin_dsn: str | None = None,
        restore_as_role: bool = False,
        reader_dsn: str | None = None,
        log_dsn: str | None = None,
        roles_admin_dsn: str | None = None,
        role_dsns: Sequence[str] = (),
    ) -> None:
        self._external_dsn = dsn
        # With world roles: the harness's own address, used only to read and put back the world's own roles (no
        # statement of the world's runs on it), and the address of each of those roles.
        self._roles_admin_dsn = roles_admin_dsn
        self._role_dsns = tuple(role_dsns)
        self._verifiers: dict[str, str] = {}
        # The world's store role (with world roles): the harness's reads, snapshot and reset of the world's rows,
        # past their row security; ``dsn`` stays the agent's tools' own role, under it.
        self._reader_dsn = reader_dsn
        # The world's log reader: the harness's own log and marker, read by a role that runs no world SQL.
        self._log_dsn = log_dsn
        # The harness's own address for a snapshot and a restore (one fixed user), or a reset run as ``dsn`` itself,
        # the world's role (`attached_world_store`).
        self._admin_dsn = admin_dsn
        self._restore_as_role = restore_as_role
        self.applied: list[str] = []
        self._started = True

    def dsn(self) -> str:
        return self._external_dsn

    def start(self) -> None:
        self.probe()

    def stop(self) -> None:
        return

    def save_to(self, path: str | Path) -> None:
        # Compose owns the schema and reruns the repository's migrations/initialisers whenever
        # the project is recreated. The harness snapshot therefore owns only mutable rows and
        # counters, avoiding a second generated schema that can drift from the submitted code.
        from . import Held

        Held.save_to(self, path)

    def load_from(self, path: str | Path) -> None:
        from . import Held

        Held.load_from(self, path)


def _adapt(value: Any, data_type: str = "") -> Any:
    """Hand back a value in the form psycopg will write.

    A list in a JSON column must be wrapped, while a list in an ARRAY column must remain a list
    so psycopg emits a native Postgres array. The authored setup crosses JSON boundaries before
    it reaches a store, and some producers consequently leave a structured array as the JSON
    string ``"[]"``. PostgreSQL interprets a bound string as its own array-literal syntax, where
    square brackets mean dimensions rather than values, and rejects it. Decode only an actual
    JSON list for an ARRAY column; arbitrary strings remain arbitrary strings and PostgreSQL can
    enforce the declared element type.

    Scenario setup may likewise represent a boolean as ``0``/``1`` even though PostgreSQL
    deliberately does not implicitly cast a bound smallint to boolean. Normalize only the
    finite, unambiguous boolean vocabulary; using ``bool(value)`` here would silently turn values
    such as ``2`` or ``"disabled"`` into true.
    """
    normalized_type = data_type.strip().lower()
    if value is not None and normalized_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        if isinstance(value, str):
            normalized_value = value.strip().lower()
            if normalized_value in {"true", "t", "1"}:
                return True
            if normalized_value in {"false", "f", "0"}:
                return False
        raise StoreError(
            "a PostgreSQL boolean column received "
            f"{value!r}; expected true/false or the equivalent 1/0"
        )
    if value is not None and normalized_type == "array":
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, str) and value.strip().startswith("["):
            try:
                decoded = json.loads(value)
            except json.JSONDecodeError as exc:
                raise StoreError(
                    "a PostgreSQL array column received malformed JSON array text "
                    f"{value!r}"
                ) from exc
            if not isinstance(decoded, list):  # pragma: no cover - guarded by '['
                raise StoreError(
                    f"a PostgreSQL array column received {value!r}; expected a JSON list"
                )
            return decoded
    if value is not None and normalized_type in ("json", "jsonb"):
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    return value
