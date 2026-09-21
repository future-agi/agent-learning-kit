"""Generative invariants shared by the deterministic state compilers."""

from __future__ import annotations

import base64
import sqlite3
from datetime import date, datetime, timezone

from hypothesis import given, settings, strategies as st

from fi.alk.harness.compile.postgres import compile_postgres
from fi.alk.harness.compile.sqlite import apply_sqlite, compile_sqlite
from fi.alk.harness.source_model import (
    LogicalType,
    SourceColumn,
    SourceModel,
    SourceTable,
)
from fi.alk.harness.world_ir import WorldIR, WorldRow, WorldTable, WorldValue
from fi.alk.harness.world_import.sqlite import import_sqlite_world


_SOURCE_DIGEST = "sha256:" + "9" * 64
_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=80,
)
_JSON_SCALARS = (
    st.none()
    | st.booleans()
    | st.integers(-(2**53), 2**53)
    | st.floats(
        allow_nan=False,
        allow_infinity=False,
        width=64,
    )
    | _TEXT
)
_JSON = st.recursive(
    _JSON_SCALARS,
    lambda children: (
        st.lists(children, max_size=5) | st.dictionaries(_TEXT, children, max_size=5)
    ),
    max_leaves=20,
)


@st.composite
def _portable_value(draw: st.DrawFn) -> tuple[LogicalType, str, str, object]:
    logical_type = draw(
        st.sampled_from(
            (
                LogicalType.BOOLEAN,
                LogicalType.INTEGER,
                LogicalType.NUMBER,
                LogicalType.STRING,
                LogicalType.UUID,
                LogicalType.TIMESTAMP,
                LogicalType.DATE,
                LogicalType.ENUM,
                LogicalType.ARRAY,
                LogicalType.JSON,
                LogicalType.BINARY,
            )
        )
    )
    if logical_type is LogicalType.BOOLEAN:
        return logical_type, "boolean", "BOOLEAN", draw(st.booleans())
    if logical_type is LogicalType.INTEGER:
        return (
            logical_type,
            "bigint",
            "INTEGER",
            draw(st.integers(-(2**63) + 1, 2**63 - 1)),
        )
    if logical_type is LogicalType.NUMBER:
        value = draw(st.floats(allow_nan=False, allow_infinity=False, width=64))
        return logical_type, "double precision", "REAL", value
    if logical_type is LogicalType.STRING:
        return logical_type, "text", "TEXT", draw(_TEXT)
    if logical_type is LogicalType.UUID:
        return logical_type, "uuid", "TEXT", str(draw(st.uuids()))
    if logical_type is LogicalType.TIMESTAMP:
        value = draw(
            st.datetimes(
                min_value=datetime(1970, 1, 1),
                max_value=datetime(2100, 1, 1),
                timezones=st.just(timezone.utc),
            )
        ).isoformat()
        return logical_type, "timestamptz", "TEXT", value
    if logical_type is LogicalType.DATE:
        value = draw(
            st.dates(min_value=date(1970, 1, 1), max_value=date(2100, 1, 1))
        ).isoformat()
        return logical_type, "date", "TEXT", value
    if logical_type is LogicalType.ENUM:
        return (
            logical_type,
            "status_enum",
            "TEXT",
            draw(st.sampled_from(("active", "disabled"))),
        )
    if logical_type is LogicalType.ARRAY:
        value = draw(st.lists(st.integers(-(2**31), 2**31 - 1), max_size=12))
        return logical_type, "integer[]", "JSON", value
    if logical_type is LogicalType.JSON:
        return logical_type, "jsonb", "JSON", draw(_JSON)
    raw = draw(st.binary(max_size=80))
    return logical_type, "bytea", "BLOB", base64.b64encode(raw).decode("ascii")


def _source_and_world(
    engine: str,
    logical_type: LogicalType,
    native_type: str,
    value: object,
) -> tuple[SourceModel, WorldIR]:
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
        source_digest=_SOURCE_DIGEST,
        engine=engine,
        tables=(
            SourceTable(
                name="generated values",
                columns=(
                    SourceColumn(
                        name="id",
                        logical_type=LogicalType.INTEGER,
                        native_type="bigint" if engine == "postgres" else "INTEGER",
                        nullable=False,
                        has_default=False,
                    ),
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
                source_name="generated values",
                rows=(
                    WorldRow(
                        identity="generated-row",
                        values={
                            "id": WorldValue.present(LogicalType.INTEGER, 1),
                            "value": WorldValue.present(logical_type, value),
                        },
                    ),
                ),
            ),
        ),
    )
    return source, world


@given(_portable_value())
@settings(max_examples=150, deadline=None)
def test_postgres_compilation_is_deterministic_for_generated_portable_values(
    generated: tuple[LogicalType, str, str, object],
) -> None:
    logical_type, postgres_type, _, value = generated
    source, world = _source_and_world("postgres", logical_type, postgres_type, value)

    first = compile_postgres(source, world)
    second = compile_postgres(source, world)

    assert first == second
    assert first.source_schema_hash == source.fingerprint
    assert first.world_ir_hash == world.fingerprint
    assert len(first.operations) == 1
    assert first.operations[0].columns == ("id", "value")


@given(_portable_value())
@settings(max_examples=150, deadline=None)
def test_sqlite_generated_portable_values_compile_apply_and_round_trip(
    generated: tuple[LogicalType, str, str, object],
) -> None:
    logical_type, _, sqlite_type, value = generated
    source, world = _source_and_world("sqlite", logical_type, sqlite_type, value)
    compiled = compile_sqlite(source, world)
    assert compiled == compile_sqlite(source, world)

    database = sqlite3.connect(":memory:")
    database.execute(
        f'CREATE TABLE "generated values" (id INTEGER PRIMARY KEY, value {sqlite_type})'
    )
    apply_sqlite(database, compiled)
    imported = import_sqlite_world(database, source).world
    imported_value = imported.tables[0].rows[0].values["value"]

    assert imported_value.logical_type is logical_type
    assert imported_value.value == value
