"""The world's records before and after a trial, compared by primary key on a read-only connection.

A table that cannot be compared is named with why, never dropped silently."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

MAX_ROWS = 5000
MAX_CHANGES = 100
# Longest text a change shows for one field; a document or blob column would otherwise ride in every trial's record.
MAX_VALUE_CHARS = 2000
INTERNAL_PREFIX = "_alk_"


@dataclass
class Snapshot:
    """Each compared table's rows by primary key (the key's values as JSON text), and the tables left out with why."""

    tables: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    skipped: list[dict[str, str]] = field(default_factory=list)


def _plain(row: dict[str, Any]) -> dict[str, Any]:
    """A row as JSON data: dates, decimals and the like as text."""
    return json.loads(json.dumps(row, default=str))


def _shown(row: dict[str, Any] | None) -> dict[str, Any] | None:
    """A record as a change shows it: text past MAX_VALUE_CHARS is cut, and says how much was cut."""
    if row is None:
        return None
    shown: dict[str, Any] = {}
    for name, value in row.items():
        text = value if isinstance(value, str) else json.dumps(value) if isinstance(value, (dict, list)) else None
        if text is not None and len(text) > MAX_VALUE_CHARS:
            value = f"{text[:MAX_VALUE_CHARS]}... ({len(text) - MAX_VALUE_CHARS} more characters)"
        shown[name] = value
    return shown


def from_rows(
    tables: dict[str, tuple[list[str], list[dict[str, Any]]]], *, max_rows: int = MAX_ROWS
) -> Snapshot:
    """A snapshot from each table's primary key columns and rows, by name; the reading is the caller's."""
    snap = Snapshot()
    for table in sorted(tables):
        if table.lower().startswith(INTERNAL_PREFIX):
            continue
        key, rows = tables[table]
        if not key:
            snap.skipped.append({"table": table, "why": "no primary key"})
            continue
        if len(rows) > max_rows:
            snap.skipped.append({"table": table, "why": f"over {max_rows} rows"})
            continue
        snap.tables[table] = {
            json.dumps([row.get(column) for column in key], default=str): _plain(row) for row in rows
        }
    return snap


_TABLES = (
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
)
_KEYS = (
    "SELECT kcu.table_name, kcu.column_name FROM information_schema.table_constraints tc "
    "JOIN information_schema.key_column_usage kcu ON tc.constraint_name = kcu.constraint_name "
    "AND tc.table_schema = kcu.table_schema AND tc.table_name = kcu.table_name "
    "WHERE tc.table_schema = 'public' AND tc.constraint_type = 'PRIMARY KEY' ORDER BY kcu.ordinal_position"
)


def snapshot(dsn: str, *, max_rows: int = MAX_ROWS, timeout_ms: int = 10000) -> Snapshot:
    """Every non-internal table of the world's Postgres database, read on a connection that cannot write. Raises when
    the database cannot be read; the caller records that, never failing the trial."""
    import psycopg

    # The system's own search path (`world_door.PINNED_SEARCH_PATH`): every table read is named with its schema.
    options = f"-c default_transaction_read_only=on -c statement_timeout={timeout_ms} -c search_path=pg_catalog,pg_temp"
    with psycopg.connect(dsn, autocommit=True, connect_timeout=5, options=options) as connection:
        names = [
            str(row[0])
            for row in connection.execute(_TABLES).fetchall()
            if not str(row[0]).lower().startswith(INTERNAL_PREFIX)
        ]
        keys: dict[str, list[str]] = {}
        for table, column in connection.execute(_KEYS).fetchall():
            keys.setdefault(str(table), []).append(str(column))
        read: dict[str, tuple[list[str], list[dict[str, Any]]]] = {}
        too_big: list[dict[str, str]] = []
        for table in names:
            quoted = '"public"."' + table.replace('"', '""') + '"'
            if not keys.get(table):
                read[table] = ([], [])
                continue
            count = connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()  # noqa: S608 - a listed table
            if count and int(count[0]) > max_rows:
                too_big.append({"table": table, "why": f"over {max_rows} rows"})
                continue
            cursor = connection.execute(f"SELECT * FROM {quoted}")  # noqa: S608 - a listed table
            columns = [one[0] for one in cursor.description or []]
            read[table] = (keys[table], [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()])
    snap = from_rows(read, max_rows=max_rows)
    snap.skipped = sorted([*snap.skipped, *too_big], key=lambda one: one["table"])
    return snap


def diff(before: Snapshot, after: Snapshot, *, max_changes: int = MAX_CHANGES) -> dict[str, Any]:
    """What the trial changed, table by table: each added, changed or removed record with its values before and
    after and, for a change, the fields that changed. Tables either side left out are named in `skipped`; changes
    past `max_changes` are counted in `truncated`."""
    changes: list[dict[str, Any]] = []
    total = 0
    skipped = {one["table"]: one for one in [*before.skipped, *after.skipped]}
    for table in sorted(set(before.tables) | set(after.tables)):
        if table in skipped:
            continue
        old = before.tables.get(table, {})
        new = after.tables.get(table, {})
        for key in sorted(set(old) | set(new)):
            was, now = old.get(key), new.get(key)
            if was == now:
                continue
            if was is None:
                entry = {"table": table, "key": json.loads(key), "kind": "added", "before": None, "after": now,
                         "fields": []}
            elif now is None:
                entry = {"table": table, "key": json.loads(key), "kind": "removed", "before": was, "after": None,
                         "fields": []}
            else:
                fields = sorted(name for name in set(was) | set(now) if was.get(name) != now.get(name))
                entry = {"table": table, "key": json.loads(key), "kind": "changed", "before": was, "after": now,
                         "fields": fields}
            total += 1
            if len(changes) < max_changes:
                entry["before"], entry["after"] = _shown(entry["before"]), _shown(entry["after"])
                changes.append(entry)
    return {"changes": changes, "skipped": list(skipped.values()), "truncated": max(total - len(changes), 0)}


def digest(snap: Snapshot) -> dict[str, str]:
    """Each compared table's content as one hash, to tell whether two starts held the same records."""
    return {
        table: hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
        for table, rows in snap.tables.items()
    }


def start_state(seeded: dict[str, str] | None, start: dict[str, str] | None) -> dict[str, Any]:
    """Whether a trial started from the seeded records: `seeded`, `differs` with the tables that differ, or `unknown`
    when either start could not be read."""
    if seeded is None or start is None:
        return {"state": "unknown", "tables": []}
    tables = sorted(name for name in set(seeded) | set(start) if seeded.get(name) != start.get(name))
    return {"state": "differs" if tables else "seeded", "tables": tables}


__all__ = ["MAX_CHANGES", "MAX_ROWS", "MAX_VALUE_CHARS", "Snapshot", "diff", "digest", "from_rows", "snapshot", "start_state"]
