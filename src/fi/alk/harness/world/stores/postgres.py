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

import json
import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from ...world_door import WORLD_ROLE_SESSION
from ..errors import WorldQueryRejected
from . import Held, Snapshot, StoreError, WorldResetRefused
from .container import ContainerStore, docker

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
# Every definition of the world's own, a digest per kind, read on `pg_catalog` alone so each is spelled the same
# whichever session reads it: a reset compares it with its snapshot's before it commits.
_IN_WORLD = "n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg\\_%%'"
_CATALOG = " UNION ALL ".join(
    f"SELECT '{kind}', md5(coalesce(string_agg(x, E'\\n' ORDER BY x), '')) FROM ({rows}) d(x)"
    for kind, rows in (
        ("schemas", f"SELECT concat_ws('|', n.nspname, pg_get_userbyid(n.nspowner), n.nspacl::text) "
         f"FROM pg_namespace n WHERE {_IN_WORLD}"),
        ("relations", "SELECT concat_ws('|', n.nspname, c.relname, c.relkind, pg_get_userbyid(c.relowner), "
         "c.relacl::text, c.relrowsecurity, c.relforcerowsecurity, c.relpersistence, c.reloptions::text) "
         f"FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_IN_WORLD}"),
        ("columns", "SELECT concat_ws('|', n.nspname, c.relname, a.attname, format_type(a.atttypid, a.atttypmod), "
         "a.attnotnull, a.attidentity, a.attgenerated, a.attacl::text, pg_get_expr(d.adbin, d.adrelid)) "
         "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
         "LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum "
         f"WHERE a.attnum > 0 AND NOT a.attisdropped AND {_IN_WORLD}"),
        ("constraints", "SELECT concat_ws('|', n.nspname, k.conname, k.conrelid::oid::text <> '0', "
         "coalesce(c.relname, ''), pg_get_constraintdef(k.oid), k.convalidated) FROM pg_constraint k "
         "JOIN pg_namespace n ON n.oid = k.connamespace LEFT JOIN pg_class c ON c.oid = k.conrelid "
         f"WHERE {_IN_WORLD}"),
        ("indexes", "SELECT pg_get_indexdef(i.indexrelid) FROM pg_index i JOIN pg_class c ON c.oid = i.indrelid "
         f"JOIN pg_namespace n ON n.oid = c.relnamespace WHERE {_IN_WORLD}"),
        ("triggers", "SELECT concat_ws('|', n.nspname, c.relname, t.tgname, t.tgenabled, pg_get_triggerdef(t.oid)) "
         "FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
         f"WHERE NOT t.tgisinternal AND {_IN_WORLD}"),
        ("rules", "SELECT concat_ws('|', n.nspname, c.relname, r.rulename, r.ev_enabled, pg_get_ruledef(r.oid)) "
         "FROM pg_rewrite r JOIN pg_class c ON c.oid = r.ev_class JOIN pg_namespace n ON n.oid = c.relnamespace "
         f"WHERE {_IN_WORLD}"),
        ("policies", "SELECT concat_ws('|', n.nspname, c.relname, p.polname, p.polcmd, p.polpermissive, "
         "p.polroles::text, pg_get_expr(p.polqual, p.polrelid), pg_get_expr(p.polwithcheck, p.polrelid)) "
         "FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
         f"WHERE {_IN_WORLD}"),
        ("functions", "SELECT concat_ws('|', n.nspname, p.proname, pg_get_function_identity_arguments(p.oid), "
         "pg_get_function_result(p.oid), pg_get_userbyid(p.proowner), p.prosecdef, p.proacl::text, "
         "p.proconfig::text, p.provolatile, p.proleakproof, p.prolang, p.prosrc, p.probin) "
         f"FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE {_IN_WORLD}"),
        ("operators", "SELECT concat_ws('|', n.nspname, o.oprname, format_type(o.oprleft, NULL), "
         "format_type(o.oprright, NULL), o.oprcode::oid, pg_get_userbyid(o.oprowner)) "
         f"FROM pg_operator o JOIN pg_namespace n ON n.oid = o.oprnamespace WHERE {_IN_WORLD}"),
        ("types", "SELECT concat_ws('|', n.nspname, t.typname, t.typtype, pg_get_userbyid(t.typowner), "
         "t.typacl::text, (SELECT string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder) FROM pg_enum e "
         f"WHERE e.enumtypid = t.oid)) FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace WHERE {_IN_WORLD}"),
        ("casts", "SELECT concat_ws('|', c.castsource, c.casttarget, c.castfunc, c.castcontext) FROM pg_cast c "
         "WHERE c.oid >= 16384"),
        ("extensions", "SELECT concat_ws('|', e.extname, e.extversion, e.extnamespace) FROM pg_extension e"),
        # The reset's own role, here and everywhere: the role whose code a reset runs. Another world's roles, and the
        # agent's own role (whose settings each world's reset clears), are no part of this world's reset.
        ("role settings", "SELECT concat_ws('|', s.setdatabase, pg_get_userbyid(s.setrole), s.setconfig::text) "
         "FROM pg_db_role_setting s WHERE s.setrole = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
         "AND s.setdatabase IN (0, (SELECT oid FROM pg_database WHERE datname = current_database()))"),
        ("event triggers", "SELECT concat_ws('|', e.evtname, e.evtevent, e.evtenabled, e.evtfoid) "
         "FROM pg_event_trigger e"),
        # A large object outlives a reset of rows; one made since the snapshot would carry across trials.
        ("large objects", "SELECT concat_ws('|', l.oid, pg_get_userbyid(l.lomowner), l.lomacl::text) "
         "FROM pg_largeobject_metadata l"),
        # Default privileges carry a grant to whatever is made next.
        ("default privileges", "SELECT concat_ws('|', pg_get_userbyid(a.defaclrole), a.defaclnamespace, "
         "a.defaclobjtype, a.defaclacl::text) FROM pg_default_acl a"),
    )
)


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
        with self._connect() as connection:
            cursor = connection.execute(statement, tuple(params))
            return max(0, int(cursor.rowcount or 0))

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
        # A check's read names the world's tables as the world's own SQL does: system functions first, read-only.
        return self._read(self._reader_address(), statement, params, _SYSTEM_FIRST)

    def read_log(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """A read of the harness's own tables (every name qualified) as the world's log reader, on the system's own
        path and read-only; the store's reader where there is none (one fixed user)."""
        log = getattr(self, "_log_dsn", None)
        if not log:
            return self.query(statement, params)
        return self._read(log, statement, params, PINNED_SEARCH_PATH)

    def query_as_world(self, statement: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """``query`` as the agent's tools' own role, under the world's row security: a handler's read."""
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
        names = [row[0] for row in rows]
        if getattr(self, "_restore_as_role", False):
            # The world's own tables: the harness's (its log, its marker) are no world role's to read or reset.
            names = [name for name in names if not name.startswith("_alk_") and name != "harness_seed_sentinel"]
        return names

    def _reset_tables(self, connection: Any) -> list[tuple[str, str]]:
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

    def _column_kinds(self, connection: Any, schema: str, table: str) -> dict[str, tuple[str, str, str]]:
        """Each column's (type, identity, generated): a stored generated column is never written back, and an
        identity column that is always generated takes its value back only with OVERRIDING SYSTEM VALUE."""
        rows = connection.execute(
            "SELECT a.attname, format_type(a.atttypid, a.atttypmod), a.attidentity::text, a.attgenerated::text "
            "FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND c.relname = %s AND a.attnum > 0 AND NOT a.attisdropped",
            (schema, table),
        ).fetchall()
        return {str(name): (str(kind), str(identity), str(generated)) for name, kind, identity, generated in rows}

    def _put_back(self, connection: Any, schema: str, table: str, rows: list[dict[str, Any]]) -> None:
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
        try:
            with connection.cursor() as cursor:
                cursor.executemany(
                    statement,
                    [tuple(_adapt(row.get(column), types.get(column, "")) for column in columns) for row in rows],
                )
        finally:
            connection.execute("SET LOCAL search_path = pg_catalog, pg_temp")

    def _counters(self, connection: Any) -> dict[str, Any]:
        rows = connection.execute(
            f"SELECT schemaname, sequencename, last_value FROM pg_sequences WHERE {_CUSTOMER_SCHEMA}"
        ).fetchall()
        return {
            _key(str(schema), str(sequence)): value
            for schema, sequence, value in rows
            if value is not None
            and not (getattr(self, "_restore_as_role", False) and schema == "public" and str(sequence).startswith("_alk_"))
        }

    def _set_counters(self, connection: Any, counters: dict[str, Any]) -> None:
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

    def _primary_key(self, connection: Any, table: str, schema: str = "public") -> list[str]:
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
        return [row[0] for row in rows]

    def _column_types(self, connection: Any, table: str, schema: str = "public") -> dict[str, str]:
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

        def text(value: Any) -> str:
            return value.decode("utf-8") if isinstance(value, bytes) else str(value)

        return {text(row[0]): text(row[1]) for row in rows}

    def _select_ordered(self, connection: Any, table: str, schema: str = "public") -> list[dict[str, Any]]:
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
                catalog = self._catalog(connection)
        return Snapshot(rows=rows, counters=counters, catalog=catalog)

    @staticmethod
    def _catalog(connection: Any) -> dict[str, str]:
        """Every definition of the world's own, a digest per kind, spelled on `pg_catalog` alone (call inside a
        transaction: the search path is set for it only)."""
        connection.execute("SET LOCAL search_path = pg_catalog")
        return {str(kind): str(digest) for kind, digest in connection.execute(_CATALOG).fetchall()}

    def _verify_reset(self, connection: Any, snapshot: Snapshot, tables: list[tuple[str, str]]) -> None:
        """Before a reset commits: the world as its snapshot held it, every table's rows (their count, for rows read
        from disk) and every definition, or nothing of the reset is kept and the world is refused. The world's own
        code a reset runs (a check, an index expression) is what could leave it otherwise."""
        sorted_rows = lambda rows: sorted(repr(sorted(row.items())) for row in rows)  # noqa: E731
        for schema, table in tables:
            held = snapshot.rows.get(_key(schema, table)) or []
            now = self._select_ordered(connection, table, schema)
            same = sorted_rows(now) == sorted_rows(held) if snapshot.catalog else len(now) == len(held)
            if not same:
                raise WorldResetRefused(
                    f"the reset left {schema}.{table} other than its snapshot ({len(now)} rows, {len(held)} expected): "
                    "the world's own code changed it while the reset ran"
                )
        if snapshot.catalog:
            changed = sorted(
                kind for kind, digest in self._catalog(connection).items() if snapshot.catalog.get(kind) != digest
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
                if getattr(self, "_reader_dsn", None):
                    # The store role may suspend the world's triggers and foreign-key checks for its own reset.
                    connection.execute("SET LOCAL session_replication_role = replica")
                for schema, table in reversed(order):
                    connection.execute(f"DELETE FROM {_ident(schema, table)}")  # noqa: S608 - a listed table
                for schema, table in order:
                    self._put_back(connection, schema, table, snapshot.rows.get(_key(schema, table)) or [])
                self._set_counters(connection, snapshot.counters)
                self._verify_reset(connection, snapshot, tables)
                self._keep_own_password(connection)

    def _keep_own_password(self, connection: Any) -> None:
        """Last in the reset's transaction, after every statement that ran the world's code: the store role's password
        set back to the one it logs in with. Any role may change its own password, so a check the reset ran could
        have given it another, which would then let anyone holding it in, past the world's row security."""
        password = _psycopg().conninfo.conninfo_to_dict(getattr(self, "_reader_dsn", None) or "").get("password")
        if password:
            sql = _psycopg().sql
            connection.execute(sql.SQL("ALTER ROLE CURRENT_USER PASSWORD {}").format(sql.Literal(password)))

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
    ) -> None:
        self._external_dsn = dsn
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
