"""Propose a typed, independent simulation entity for each uploaded record source."""

import re
from collections import Counter
from dataclasses import replace

from ._environment_input_records import (
    _EMAIL_RE,
    _PHONE_RE,
    _can_convert,
    _convert_value,
    _infer_type,
)
from ._environment_input_types import (
    DataType,
    DestinationField,
    EnvironmentInputError,
    FieldProfile,
    JsonScalar,
    ParsedRecordSource,
    ParsedSource,
    ProposedSimulationSchema,
    SchemaRepairChoice,
    SimulationSchemaRepair,
    _canonical_json,
    simulation_target_field,
)


def _column_type(values: list[JsonScalar], inferred: DataType) -> tuple[DataType, bool]:
    if not values:
        return "string", True
    # Numeric conversion loses significant spelling, particularly for IDs and postcodes.
    if any(
        isinstance(v, str) and re.fullmatch(r"[+-]?0\d+", v.strip()) for v in values
    ):
        return "string", any(not isinstance(v, str) for v in values)
    if inferred == "datetime" and all(_can_convert(v, "date") for v in values):
        return "date", False
    if inferred == "number" and any(
        _can_convert(v, "integer") and int(v) != float(v) for v in values
    ):
        return "string", True
    if inferred == "string":
        kinds = {_infer_type([v]) for v in values}
        return inferred, len(kinds) > 1 or any(not isinstance(v, str) for v in values)
    return inferred, False


def _unique_identity(
    source: ParsedRecordSource, names: tuple[str, ...], types: dict[str, DataType]
) -> bool:
    if not names or len(set(names)) != len(names) or not set(names) <= types.keys():
        return False
    keys = set()
    for row in source.rows:
        values = row.as_dict()
        key = tuple(_convert_value(values.get(name), types[name]) for name in names)
        if any(v is None or (isinstance(v, str) and not v.strip()) for v in key):
            return False
        keys.add(_canonical_json(key))
    return bool(keys) and len(keys) == len(source.rows)


def infer_simulation_schema(
    sources: list[ParsedSource], repairs: dict[str, SimulationSchemaRepair]
) -> ProposedSimulationSchema:
    records = [s for s in sources if isinstance(s, ParsedRecordSource)]
    if repairs.keys() - {s.pin.source_id for s in records}:
        raise EnvironmentInputError(
            "source_mapping_invalid", "Schema repair names an unknown record source"
        )
    field_counts = Counter(f.source_field for s in records for f in s.fields)
    fields: list[DestinationField] = []
    issues: list[SchemaRepairChoice] = []
    for index, source in enumerate(sources):
        if not isinstance(source, ParsedRecordSource):
            continue
        key = source.pin.source_id
        repair = repairs.get(key, SimulationSchemaRepair(key))
        names = tuple(f.source_field for f in source.fields)
        if (set(repair.text_fields) | set(repair.identity_fields)) - set(names):
            raise EnvironmentInputError(
                "source_mapping_invalid", "Schema repair names an unknown field"
            )
        duplicates = tuple(name for name in names if field_counts[name] > 1)
        if duplicates and not repair.separate_entity:
            issues.append(SchemaRepairChoice(key, "confirm_entity", duplicates))

        profiles: list[FieldProfile] = []
        types: dict[str, DataType] = {}
        for i, profile in enumerate(source.fields):
            name = profile.source_field
            column = (row.values[i][1] for row in source.rows)
            populated = [value for value in column if value not in (None, "")]
            kind, ambiguous = _column_type(populated, profile.inferred_type)
            if name in repair.text_fields:
                kind = "string"
            elif ambiguous:
                issues.append(SchemaRepairChoice(key, "keep_text", (name,)))
            target = simulation_target_field(key, name)
            types[name] = kind
            fields.append(DestinationField(target, kind))
            personal = profile.personal_data_suspected or (
                kind == "string"
                and any(
                    isinstance(v, str) and (_EMAIL_RE.search(v) or _PHONE_RE.search(v))
                    for v in populated
                )
            )
            profiles.append(
                replace(
                    profile,
                    inferred_type=kind,
                    suggested_target_field=target,
                    personal_data_suspected=personal,
                )
            )

        identity = repair.identity_fields or tuple(
            p.source_field
            for p in profiles
            if p.suggested_stable_id and not p.null_count
        )
        chosen = bool(repair.identity_fields) or len(identity) == 1
        if not chosen or not _unique_identity(source, identity, types):
            issues.append(SchemaRepairChoice(key, "choose_identity", names))
            identity = ()
        sources[index] = replace(
            source,
            fields=tuple(
                replace(p, suggested_stable_id=p.source_field in identity)
                for p in profiles
            ),
        )
    return ProposedSimulationSchema(tuple(fields), "simulation", tuple(issues))
