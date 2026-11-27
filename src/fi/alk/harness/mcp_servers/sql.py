"""A read-only SQL MCP server over one world's database.

It connects as the world's reader role, which can SELECT only the tables the release lists, and
runs each query as one server-side cursor in a read-only transaction with a statement timeout and
caps on the rows and bytes read from it. The role is the boundary; the rest keeps one query small
and harmless.

    WORLD_READER_URL=postgresql://... python -m fi.alk.harness.mcp_servers.sql
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

ROW_CAP = 200
BYTE_CAP = 256 * 1024
STATEMENT_TIMEOUT_MS = 5_000

# Per-request INFO lines would land in every world's diagnostics.
server = FastMCP("sql", log_level="WARNING")


@server.tool(
    description=(
        "Run one read-only SQL query (PostgreSQL) and get the rows back, at most "
        f"{ROW_CAP}. Only SELECT on the tables you are allowed to read works."
    ),
    annotations=ToolAnnotations(readOnlyHint=True),
)
def query(sql: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    size = 0
    truncated = False
    with psycopg.connect(
        os.environ["WORLD_READER_URL"],
        options=f"-c statement_timeout={STATEMENT_TIMEOUT_MS}",
    ) as connection:
        connection.read_only = True
        # One cursor holds one statement: a second one after a `;` is refused instead of run. Rows
        # past the caps are never sent.
        with connection.cursor(name="query") as cursor:
            cursor.execute(sql)
            columns = _distinct([column.name for column in cursor.description or []])
            while (row := cursor.fetchone()) is not None:
                record = dict(zip(columns, row, strict=True))
                size += len(json.dumps(record, default=str))
                if len(rows) == ROW_CAP or size > BYTE_CAP:
                    truncated = True
                    break
                rows.append(record)
    return {"columns": columns, "rows": rows, "truncated": truncated}


def _distinct(names: list[str]) -> list[str]:
    """Each column under a name of its own: a join can return two called `id`."""
    taken: list[str] = []
    for name in names:
        unique, number = name, 2
        while unique in taken or (unique != name and unique in names):
            unique, number = f"{name}_{number}", number + 1
        taken.append(unique)
    return taken


if __name__ == "__main__":
    server.run("stdio")
