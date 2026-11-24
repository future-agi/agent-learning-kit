"""Checking and applying JSON Schemas somebody else wrote.

An agent's tool schemas are the customer's, declared or inspected at their provider, and
`jsonschema` by default fetches what a `$ref` names, over http(s) or from a file. Every schema
that is not the harness's own is applied through here, with nothing to fetch from.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.jsonschema import DRAFT202012


def validator(schema: Any, **options: Any) -> Draft202012Validator:
    return Draft202012Validator(schema, registry=Registry(), **options)


def value_problems(schema: Any, value: Any, *, formats: bool = False) -> list[str]:
    """Why `schema` does not accept `value`; empty when it does. A schema that cannot be applied
    (a reference outside it, or one that never ends) accepts nothing. `formats` holds the value
    to the schema's formats too, which JSON Schema leaves as annotations."""
    checker = Draft202012Validator.FORMAT_CHECKER if formats else None
    try:
        return sorted(
            f"{'.'.join(map(str, error.absolute_path)) or 'arguments'}: {error.message}"
            for error in validator(schema, format_checker=checker).iter_errors(value)
        )
    except Exception as exc:  # noqa: BLE001 - whatever the schema's author wrote
        return [f"the schema cannot be applied: {type(exc).__name__}: {exc}"]


def schema_problems(schema: Any) -> list[str]:
    """Why `schema` is not a JSON Schema of its own: it is not valid, or a reference in it leads
    outside it or nowhere. Empty when it is one."""
    try:
        Draft202012Validator.check_schema(schema)
        resource = DRAFT202012.create_resource(schema)
        lookup = Registry().with_resource("", resource).resolver().lookup
        return [
            f"$ref {reference!r} is not part of the schema"
            for reference in _references(schema)
            if not reference.startswith("#") or not _leads(lookup, reference)
        ]
    except SchemaError as exc:
        where = "/".join(str(part) for part in exc.path) or "the root"
        return [f"{where}: {exc.message}"]
    except Exception as exc:  # noqa: BLE001 - whatever the schema's author wrote
        return [f"{type(exc).__name__}: {exc}"]


def _leads(lookup: Any, reference: str) -> bool:
    try:
        lookup(reference)
    except Exception:  # noqa: BLE001 - however it fails, it leads nowhere
        return False
    return True


def _references(schema: Any) -> Iterator[str]:
    if isinstance(schema, Mapping):
        for keyword, value in schema.items():
            if keyword in {"$ref", "$dynamicRef"} and isinstance(value, str):
                yield value
            yield from _references(value)
    elif isinstance(schema, list):
        for value in schema:
            yield from _references(value)
