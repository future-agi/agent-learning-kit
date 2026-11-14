from __future__ import annotations

import base64
import sqlite3

import pytest

from fi.alk.harness.compile.sqlite import (
    SQLiteCompileError,
    apply_sqlite,
    compile_sqlite,
)
from fi.alk.harness.source_model import (
    ForeignKey,
    LogicalType,
    SourceColumn,
    SourceModel,
    SourceTable,
)
from fi.alk.harness.world_ir import WorldIR, WorldRow, WorldTable, WorldValue


def _column(
    name: str,
    logical_type: LogicalType,
    native_type: str,
    *,
    nullable: bool = False,
    default: str | None = None,
) -> SourceColumn:
    return SourceColumn(
        name=name,
        logical_type=logical_type,
        native_type=native_type,
        nullable=nullable,
        has_default=default is not None,
        default_expression=default,
    )


def _fixture() -> tuple[SourceModel, WorldIR]:
    parent = SourceTable(
        name="parent",
        columns=(
            _column("id", LogicalType.INTEGER, "INTEGER"),
            _column("enabled", LogicalType.BOOLEAN, "BOOLEAN"),
            _column("payload", LogicalType.JSON, "JSON"),
            _column("created", LogicalType.STRING, "TEXT", default="'source-default'"),
        ),
        primary_key=("id",),
    )
    child = SourceTable(
        name="child",
        columns=(
            _column("id", LogicalType.INTEGER, "INTEGER"),
            _column("parent_id", LogicalType.INTEGER, "INTEGER"),
        ),
        primary_key=("id",),
        foreign_keys=(
            ForeignKey(
                columns=("parent_id",),
                referenced_table="parent",
                referenced_columns=("id",),
            ),
        ),
    )
    source = SourceModel.create(
        source_digest="sha256:" + "a" * 64, engine="sqlite", tables=(child, parent)
    )
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(
            WorldTable(
                source_name="child",
                rows=(
                    WorldRow(
                        identity="child-1",
                        values={
                            "id": WorldValue.present(LogicalType.INTEGER, 2),
                            "parent_id": WorldValue.present(LogicalType.INTEGER, 1),
                        },
                    ),
                ),
            ),
            WorldTable(
                source_name="parent",
                rows=(
                    WorldRow(
                        identity="parent-1",
                        values={
                            "id": WorldValue.present(LogicalType.INTEGER, 1),
                            "enabled": WorldValue.present(LogicalType.BOOLEAN, True),
                            "payload": WorldValue.present(
                                LogicalType.JSON, {"b": 2, "a": 1}
                            ),
                            "created": WorldValue.absent(),
                        },
                    ),
                ),
            ),
        ),
    )
    return source, world


def test_sqlite_compiler_is_deterministic_and_applies_bound_values() -> None:
    source, world = _fixture()
    first = compile_sqlite(source, world)
    assert first == compile_sqlite(source, world)
    assert [item.table for item in first.operations] == ["parent", "child"]
    assert first.operations[0].params == (1, 1, '{"a":1,"b":2}')
    assert first.decisions[0].code == "source_default_applied"

    database = sqlite3.connect(":memory:")
    database.executescript(
        "PRAGMA foreign_keys=ON; CREATE TABLE parent (id INTEGER PRIMARY KEY, enabled BOOLEAN NOT NULL, payload JSON NOT NULL, created TEXT NOT NULL DEFAULT 'source-default'); CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER NOT NULL REFERENCES parent(id));"
    )
    apply_sqlite(database, first)
    assert database.execute(
        "SELECT id, enabled, payload, created FROM parent"
    ).fetchone() == (1, 1, '{"a":1,"b":2}', "source-default")
    assert database.execute("SELECT id, parent_id FROM child").fetchone() == (2, 1)


def test_generic_pipeline_selects_sqlite_compiler(tmp_path) -> None:
    from fi.alk.harness.generic_pipeline import GenericHarnessPipeline
    from fi.alk.harness.repair_controller import RepairAction

    source, world = _fixture()
    result = GenericHarnessPipeline(tmp_path).evaluate(source, world)
    assert result.decision.action is RepairAction.CERTIFY
    assert result.compiled.compiler_version == "futureagi.sqlite-compiler.v1"


@pytest.mark.parametrize(
    ("logical_type", "native_type", "authored", "stored"),
    [
        (LogicalType.BOOLEAN, "BOOLEAN", True, 1),
        (LogicalType.INTEGER, "INTEGER", -(2**63) + 1, -(2**63) + 1),
        (LogicalType.NUMBER, "REAL", 1.25, 1.25),
        (LogicalType.STRING, "TEXT", "snowman ☃", "snowman ☃"),
        (
            LogicalType.UUID,
            "TEXT",
            "12345678-1234-5678-1234-567812345678",
            "12345678-1234-5678-1234-567812345678",
        ),
        (
            LogicalType.TIMESTAMP,
            "TEXT",
            "2026-09-14T12:34:56+05:30",
            "2026-09-14T12:34:56+05:30",
        ),
        (LogicalType.DATE, "TEXT", "2026-09-14", "2026-09-14"),
        (LogicalType.ENUM, "TEXT", "active", "active"),
        (LogicalType.ARRAY, "JSON", [[1, 2], [3, 4]], "[[1,2],[3,4]]"),
        (
            LogicalType.JSON,
            "JSON",
            {"nested": {"b": 2, "a": [True, None]}},
            '{"nested":{"a":[true,null],"b":2}}',
        ),
        (
            LogicalType.BINARY,
            "BLOB",
            base64.b64encode(b"binary\x00value").decode("ascii"),
            b"binary\x00value",
        ),
    ],
)
def test_generated_logical_type_matrix_round_trips_through_real_sqlite(
    logical_type: LogicalType,
    native_type: str,
    authored: object,
    stored: object,
) -> None:
    column = SourceColumn(
        name="value",
        logical_type=logical_type,
        native_type=native_type,
        nullable=False,
        has_default=False,
        enum_values=("active", "disabled") if logical_type is LogicalType.ENUM else (),
        element_type=LogicalType.INTEGER if logical_type is LogicalType.ARRAY else None,
    )
    source = SourceModel.create(
        source_digest="sha256:" + "b" * 64,
        engine="sqlite",
        tables=(
            SourceTable(
                name="generated matrix",
                columns=(
                    _column("id", LogicalType.INTEGER, "INTEGER"),
                    column,
                ),
                primary_key=("id",),
            ),
        ),
    )
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(
            WorldTable(
                source_name="generated matrix",
                rows=(
                    WorldRow(
                        identity="row-1",
                        values={
                            "id": WorldValue.present(LogicalType.INTEGER, 1),
                            "value": WorldValue.present(logical_type, authored),
                        },
                    ),
                ),
            ),
        ),
    )

    compiled = compile_sqlite(source, world)
    assert compiled == compile_sqlite(source, world)
    database = sqlite3.connect(":memory:")
    database.execute(
        f'CREATE TABLE "generated matrix" (id INTEGER PRIMARY KEY, value {native_type})'
    )
    apply_sqlite(database, compiled)
    assert database.execute(
        'SELECT value FROM "generated matrix" WHERE id = 1'
    ).fetchone() == (stored,)


def test_absent_null_and_present_remain_distinct_in_real_sqlite() -> None:
    source = SourceModel.create(
        source_digest="sha256:" + "c" * 64,
        engine="sqlite",
        tables=(
            SourceTable(
                name="states",
                columns=(
                    _column("id", LogicalType.INTEGER, "INTEGER"),
                    _column(
                        "value",
                        LogicalType.STRING,
                        "TEXT",
                        nullable=True,
                        default="'from-source'",
                    ),
                ),
                primary_key=("id",),
            ),
        ),
    )
    rows = (
        WorldRow(
            identity="absent",
            values={
                "id": WorldValue.present(LogicalType.INTEGER, 1),
                "value": WorldValue.absent(),
            },
        ),
        WorldRow(
            identity="null",
            values={
                "id": WorldValue.present(LogicalType.INTEGER, 2),
                "value": WorldValue.null(LogicalType.STRING),
            },
        ),
        WorldRow(
            identity="present",
            values={
                "id": WorldValue.present(LogicalType.INTEGER, 3),
                "value": WorldValue.present(LogicalType.STRING, "explicit"),
            },
        ),
    )
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=(WorldTable(source_name="states", rows=rows),),
    )
    database = sqlite3.connect(":memory:")
    database.execute(
        "CREATE TABLE states (id INTEGER PRIMARY KEY, value TEXT DEFAULT 'from-source')"
    )
    apply_sqlite(database, compile_sqlite(source, world))
    assert database.execute("SELECT id, value FROM states ORDER BY id").fetchall() == [
        (1, "from-source"),
        (2, None),
        (3, "explicit"),
    ]


def test_foreign_key_cycles_fail_with_a_stable_diagnostic() -> None:
    def table(name: str, target: str) -> SourceTable:
        return SourceTable(
            name=name,
            columns=(
                _column("id", LogicalType.INTEGER, "INTEGER"),
                _column("other_id", LogicalType.INTEGER, "INTEGER"),
            ),
            primary_key=("id",),
            foreign_keys=(
                ForeignKey(
                    columns=("other_id",),
                    referenced_table=target,
                    referenced_columns=("id",),
                ),
            ),
        )

    source = SourceModel.create(
        source_digest="sha256:" + "d" * 64,
        engine="sqlite",
        tables=(table("a", "b"), table("b", "a")),
    )
    world = WorldIR.create(
        source_model_fingerprint=source.fingerprint,
        tables=tuple(
            WorldTable(
                source_name=name,
                rows=(
                    WorldRow(
                        identity=f"{name}-1",
                        values={
                            "id": WorldValue.present(LogicalType.INTEGER, 1),
                            "other_id": WorldValue.present(LogicalType.INTEGER, 1),
                        },
                    ),
                ),
            )
            for name in ("a", "b")
        ),
    )

    with pytest.raises(SQLiteCompileError, match="seed_order_invalid") as error:
        compile_sqlite(source, world)
    assert error.value.code == "seed_order_invalid"
