"""Decode PostgreSQL failures from SQLSTATE and structured diagnostic fields."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..compile.postgres import PostgresApplyError
from ..diagnostics import DiagnosticLocation, HarnessDiagnostic
from ..job import HarnessStage

_SQLSTATE_CODES = {
    "23502": "explicit_null_not_allowed",
    "23503": "foreign_key_missing",
    "23505": "unique_key_duplicate",
    "23514": "constraint_value_invalid",
    # The column's type cannot parse the authored text (a uuid, inet or enum value, say):
    # the value has to be authored again, recompiling the same value cannot fix it.
    "22P02": "value_shape_mismatch",
    "42804": "schema_type_mismatch",
    "42P01": "generated_setup_invalid",
    "42703": "generated_setup_invalid",
    "42601": "generated_setup_invalid",
}


# A data exception's message quotes the authored value, which may hold quotes of its own:
# the type is read only from PostgreSQL's text around the value, and the diagnostic's
# message is a fixed sentence, never a copy of PostgreSQL's.
_INPUT_TYPE = re.compile(r"for type ([A-Za-z_][A-Za-z0-9_ ]*?)\s*(?::|$)")
_VALUE_REFUSED = "an authored value does not fit its column"
_ARRAY_REFUSED = "an authored value is not a valid array literal"


def _type_named(message: str) -> str | None:
    """The type PostgreSQL names before the quoted value or after it."""
    for part in (
        message.split('"', 1)[0],
        message.rsplit('"', 1)[1] if '"' in message else "",
    ):
        named = _INPUT_TYPE.search(part)
        if named:
            return named.group(1).strip()
    return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def diagnose_postgres_error(
    error: BaseException,
    *,
    stage: HarnessStage,
    component: str = "postgres",
    secret_values: Iterable[str] = (),
    evidence_refs: Iterable[str] = (),
) -> HarnessDiagnostic:
    """Produce a persistable diagnosis without retaining the raw exception."""

    written_table = None
    if isinstance(error, PostgresApplyError):
        written_table, error = error.table, error.error
    sqlstate = _text(getattr(error, "sqlstate", None))
    diag = getattr(error, "diag", None)
    datatype = _text(getattr(diag, "datatype_name", None)) if diag else None
    primary = _text(getattr(diag, "message_primary", None)) if diag else None
    message = primary or _text(error) or "PostgreSQL operation failed"
    code = _SQLSTATE_CODES.get(sqlstate or "", "unsupported_source_construct")
    if (sqlstate or "").startswith("22"):
        # PostgreSQL sends no datatype for 22P02; its message names the type.
        datatype = datatype or _type_named(message)
        array = message.startswith("malformed array literal") or bool(
            datatype and (datatype.startswith("_") or datatype.endswith("[]"))
        )
        if sqlstate == "22P02" and array:
            code = "array_shape_mismatch"
        message = _ARRAY_REFUSED if array else _VALUE_REFUSED
        if datatype:
            message += f" (type {datatype})"
    return HarnessDiagnostic.create(
        stage=stage,
        component=component,
        code=code,
        message=f"PostgreSQL {sqlstate or 'unknown SQLSTATE'}: {message}",
        location=DiagnosticLocation(
            table=(_text(getattr(diag, "table_name", None)) if diag else None)
            or _text(written_table),
            column=_text(getattr(diag, "column_name", None)) if diag else None,
            constraint=_text(getattr(diag, "constraint_name", None)) if diag else None,
            datatype=datatype,
        ),
        secret_values=secret_values,
        evidence_refs=evidence_refs,
    )


__all__ = ["diagnose_postgres_error"]
