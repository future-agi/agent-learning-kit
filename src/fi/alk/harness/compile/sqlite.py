"""Pure World IR to parameterized SQLite insert compilation."""

from __future__ import annotations

import base64
import json
import sqlite3
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..source_model import LogicalType, SourceColumn, SourceModel
from ..world_ir import ValueState, WorldIR, validate_world_ir
from .postgres import CompileDecision

SQLITE_COMPILER_VERSION = "futureagi.sqlite-compiler.v1"


class SQLiteCompileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class SQLiteInsert(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, arbitrary_types_allowed=True)

    table: str
    row_identity: str
    columns: tuple[str, ...]
    statement: str
    params: tuple[Any, ...] = Field(repr=False)


class SQLiteCompileResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_schema_hash: str
    world_ir_hash: str
    compiler_version: str = SQLITE_COMPILER_VERSION
    operations: tuple[SQLiteInsert, ...]
    decisions: tuple[CompileDecision, ...]
    warnings: tuple[str, ...] = ()


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _table_order(source: SourceModel, world: WorldIR) -> tuple[str, ...]:
    included = {table.source_name for table in world.tables if table.rows}
    dependencies = {name: set() for name in included}
    dependents: dict[str, set[str]] = defaultdict(set)
    for table in source.tables:
        if table.name not in included:
            continue
        for key in table.foreign_keys:
            if key.referenced_table in included:
                dependencies[table.name].add(key.referenced_table)
                dependents[key.referenced_table].add(table.name)
    ready = sorted(name for name, required in dependencies.items() if not required)
    ordered: list[str] = []
    while ready:
        name = ready.pop(0)
        ordered.append(name)
        for dependent in sorted(dependents.get(name, ())):
            dependencies[dependent].discard(name)
            if (
                not dependencies[dependent]
                and dependent not in ordered
                and dependent not in ready
            ):
                ready.append(dependent)
                ready.sort()
    if len(ordered) != len(included):
        raise SQLiteCompileError(
            "seed_order_invalid",
            "foreign-key cycle requires a source-supported deferred strategy: "
            + ", ".join(sorted(included - set(ordered))),
        )
    return tuple(ordered)


def _parameter(column: SourceColumn, value: Any) -> Any:
    if column.logical_type is LogicalType.BOOLEAN:
        return int(value)
    if column.logical_type in {LogicalType.ARRAY, LogicalType.JSON}:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    if value is None:
        return None
    if column.logical_type is LogicalType.BINARY:
        assert isinstance(value, str)
        return base64.b64decode(value, validate=True)
    return value


def compile_sqlite(source: SourceModel, world: WorldIR) -> SQLiteCompileResult:
    if source.engine != "sqlite":
        raise SQLiteCompileError(
            "schema_type_mismatch", f"expected sqlite source model, got {source.engine}"
        )
    validate_world_ir(world, source)
    source_tables = {table.name: table for table in source.tables}
    world_tables = {table.source_name: table for table in world.tables}
    operations: list[SQLiteInsert] = []
    decisions: list[CompileDecision] = []
    for table_name in _table_order(source, world):
        source_table = source_tables[table_name]
        for row in world_tables[table_name].rows:
            columns: list[str] = []
            params: list[Any] = []
            for column in source_table.columns:
                authored = row.values.get(column.name)
                if authored is None or authored.state is ValueState.ABSENT:
                    if column.has_default:
                        decisions.append(
                            CompileDecision(
                                code="source_default_applied",
                                table=table_name,
                                row_identity=row.identity,
                                column=column.name,
                            )
                        )
                    elif column.generated:
                        decisions.append(
                            CompileDecision(
                                code="generated_column_omitted",
                                table=table_name,
                                row_identity=row.identity,
                                column=column.name,
                            )
                        )
                    continue
                columns.append(column.name)
                params.append(
                    None
                    if authored.state is ValueState.NULL
                    else _parameter(column, authored.value)
                )
            if columns:
                names = ", ".join(_identifier(name) for name in columns)
                placeholders = ", ".join("?" for _ in columns)
                statement = f"INSERT INTO {_identifier(table_name)} ({names}) VALUES ({placeholders})"
            else:
                statement = f"INSERT INTO {_identifier(table_name)} DEFAULT VALUES"
            operations.append(
                SQLiteInsert(
                    table=table_name,
                    row_identity=row.identity,
                    columns=tuple(columns),
                    statement=statement,
                    params=tuple(params),
                )
            )
    return SQLiteCompileResult(
        source_schema_hash=source.fingerprint,
        world_ir_hash=world.fingerprint,
        operations=tuple(operations),
        decisions=tuple(decisions),
    )


def apply_sqlite(connection: sqlite3.Connection, compiled: SQLiteCompileResult) -> None:
    with connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for operation in compiled.operations:
            connection.execute(operation.statement, operation.params)


__all__ = [
    "SQLITE_COMPILER_VERSION",
    "SQLiteCompileError",
    "SQLiteCompileResult",
    "SQLiteInsert",
    "apply_sqlite",
    "compile_sqlite",
]
