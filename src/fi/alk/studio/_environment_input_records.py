from __future__ import annotations

import math
import re
from collections import defaultdict
from datetime import date, datetime

from fi.alk.studio._environment_input_types import (
    DataType,
    DestinationSchema,
    EnvironmentInputError,
    FieldMapping,
    FieldProfile,
    JsonScalar,
    ParsedRecordSource,
    ProposedSimulationSchema,
    RecordRelationship,
    RecordReview,
    RejectedRecord,
    ReplacementReceipt,
    ReviewedRecord,
    ReviewedRecordSource,
    SourceDiagnostic,
    SourceSpan,
    _canonical_json,
    _normalize_audience,
    simulation_target_field,
)

_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")

_PERSONAL_FIELD_RE = re.compile(
    r"(?:^|_)(?:customer|buyer|user|person|member|patient|name|email|phone|address)(?:_|$)",
    re.IGNORECASE,
)


def _profile_field(
    field: str,
    values: list[JsonScalar],
    destination_schema: DestinationSchema | None,
) -> FieldProfile:
    populated = [value for value in values if value not in (None, "")]
    inferred_type = _infer_type(populated)
    unique_values = {_canonical_json(value) for value in populated}
    destination = (
        destination_schema.field(field) if destination_schema is not None else None
    )
    return FieldProfile(
        source_field=field,
        inferred_type=inferred_type,
        sample_values=tuple(populated[:5]),
        null_count=len(values) - len(populated),
        unique_count=len(unique_values),
        suggested_target_field=(
            destination.name
            if destination
            and all(_can_convert(value, destination.data_type) for value in populated)
            else None
        ),
        suggested_stable_id=(
            bool(populated)
            and len(unique_values) == len(populated)
            and (field.lower().endswith("_id") or field.lower() == "id")
        ),
        personal_data_suspected=bool(_PERSONAL_FIELD_RE.search(field))
        or (
            inferred_type == "string"
            and any(
                isinstance(value, str)
                and (_EMAIL_RE.search(value) or _PHONE_RE.search(value))
                for value in populated
            )
        ),
    )


def _review_record_sources(
    parsed_sources: dict[str, ParsedRecordSource],
    reviews: dict[str, RecordReview],
    destination_schema: DestinationSchema,
) -> tuple[dict[str, ReviewedRecordSource], tuple[SourceDiagnostic, ...]]:
    reviewed: dict[str, ReviewedRecordSource] = {}
    diagnostics: list[SourceDiagnostic] = []
    for source_id, parsed in parsed_sources.items():
        review = reviews[source_id]
        _validate_field_mappings(parsed, review, destination_schema)
        accepted: list[ReviewedRecord] = []
        rejected: list[RejectedRecord] = list(parsed.rejected_records)
        stable_fields = tuple(field for field in review.fields if field.is_stable_id)
        for row in parsed.rows:
            values: list[tuple[str, JsonScalar]] = []
            reasons: list[str] = []
            source_values = row.as_dict()
            for mapping in review.fields:
                try:
                    converted = _convert_value(
                        source_values.get(mapping.source_field),
                        mapping.data_type,
                    )
                except ValueError as exc:
                    reasons.append(f"{mapping.source_field}: {exc}")
                    continue
                destination = destination_schema.field(mapping.target_field)
                if destination and destination.required and converted is None:
                    reasons.append(f"{mapping.source_field}: required value is missing")
                values.append((mapping.target_field, converted))
            if reasons:
                rejected.append(
                    RejectedRecord(
                        source_id=source_id,
                        source_row=row.source_row,
                        reasons=tuple(reasons),
                    )
                )
                diagnostics.append(
                    SourceDiagnostic(
                        code="source_mapping_invalid",
                        severity="warning",
                        message=f"Row {row.source_row} was rejected: {'; '.join(reasons)}",
                        span=SourceSpan(source_id=source_id, row=row.source_row),
                    )
                )
                continue
            value_map = dict(values)
            record_id = _record_id(
                tuple(value_map[field.target_field] for field in stable_fields)
            )
            accepted.append(
                ReviewedRecord(
                    record_id=record_id,
                    source_row=row.source_row,
                    values=tuple(values),
                )
            )
        record_ids = [record.record_id for record in accepted]
        if not accepted:
            raise EnvironmentInputError(
                "source_mapping_invalid",
                f"{source_id} has no valid records after mapping",
            )
        if any(not record_id or record_id == "None" for record_id in record_ids):
            raise EnvironmentInputError(
                "source_mapping_invalid",
                f"Stable identifier values must be nonempty for {source_id}",
            )
        if len(record_ids) != len(set(record_ids)):
            raise EnvironmentInputError(
                "source_mapping_invalid",
                f"Stable identifier values must be unique for {source_id}",
            )
        reviewed[source_id] = ReviewedRecordSource(
            pin=parsed.pin,
            filename=parsed.filename,
            visibility="evaluator_only",
            audience=_normalize_audience(review.visibility, review.audience),
            fields=review.fields,
            records=tuple(accepted),
            rejected_records=tuple(rejected),
            personal_data_mode=review.personal_data_mode,
        )
    required_targets = {
        field.name for field in destination_schema.fields if field.required
    }
    mapped_targets = {
        field.target_field for source in reviewed.values() for field in source.fields
    }
    missing_targets = sorted(required_targets - mapped_targets)
    if reviewed and missing_targets:
        raise EnvironmentInputError(
            "source_mapping_invalid",
            "Required destination fields are not mapped: " + ", ".join(missing_targets),
        )
    return reviewed, tuple(diagnostics)


def _record_id(values: tuple[JsonScalar, ...]) -> str:
    if any(
        value is None or (isinstance(value, str) and not value.strip())
        for value in values
    ):
        raise EnvironmentInputError(
            "source_mapping_invalid", "Stable identifier components must be nonempty"
        )
    return str(values[0]) if len(values) == 1 else _canonical_json(values)


def _validate_field_mappings(
    source: ParsedRecordSource,
    review: RecordReview,
    destination_schema: DestinationSchema,
) -> None:
    source_fields = {field.source_field for field in source.fields}
    mapping_sources = [field.source_field for field in review.fields]
    mapping_targets = [field.target_field for field in review.fields]
    if not review.fields:
        raise EnvironmentInputError(
            "source_mapping_invalid",
            f"{source.pin.source_id} requires at least one field mapping",
        )
    if len(mapping_sources) != len(set(mapping_sources)) or len(mapping_targets) != len(
        set(mapping_targets)
    ):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            f"{source.pin.source_id} mappings must use unique source and target fields",
        )
    if not set(mapping_sources).issubset(source_fields):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            f"{source.pin.source_id} maps a field that is not in the source",
        )
    suspected_personal_fields = {
        field.source_field for field in source.fields if field.personal_data_suspected
    }
    unmarked_personal_fields = sorted(
        mapping.source_field
        for mapping in review.fields
        if mapping.source_field in suspected_personal_fields
        and not mapping.personal_data
    )
    if unmarked_personal_fields:
        raise EnvironmentInputError(
            "source_privacy_unresolved",
            "Detected personal-data fields must be marked for replacement: "
            + ", ".join(unmarked_personal_fields),
        )
    for mapping in review.fields:
        if isinstance(destination_schema, ProposedSimulationSchema) and (
            mapping.target_field
            != simulation_target_field(source.pin.source_id, mapping.source_field)
        ):
            raise EnvironmentInputError(
                "source_mapping_invalid",
                "A simulation field must belong to its source entity",
            )
        destination = destination_schema.field(mapping.target_field)
        if destination is None or destination.data_type != mapping.data_type:
            raise EnvironmentInputError(
                "source_mapping_invalid",
                f"{mapping.target_field} does not match the approved destination schema",
            )
    if not any(field.is_stable_id for field in review.fields):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            f"{source.pin.source_id} requires a stable identifier",
        )
    if (
        any(field.personal_data for field in review.fields)
        and review.personal_data_mode != "synthetic_replace"
    ):
        raise EnvironmentInputError(
            "source_privacy_unresolved",
            f"{source.pin.source_id} contains personal data and requires synthetic replacement",
        )


def _validate_relationships(
    sources: dict[str, ReviewedRecordSource],
    relationships: tuple[RecordRelationship, ...],
) -> None:
    for relationship in relationships:
        from_source = sources.get(relationship.from_source_id)
        to_source = sources.get(relationship.to_source_id)
        if from_source is None or to_source is None:
            raise EnvironmentInputError(
                "source_relationship_missing",
                "A relationship refers to an unknown record source",
            )
        from_fields = {field.target_field for field in from_source.fields}
        to_fields = {field.target_field for field in to_source.fields}
        if (
            relationship.from_field not in from_fields
            or relationship.to_field not in to_fields
        ):
            raise EnvironmentInputError(
                "source_relationship_missing",
                "A relationship refers to an unmapped field",
            )
        target_values = {
            record.as_dict().get(relationship.to_field) for record in to_source.records
        }
        target_mapping = next(
            field
            for field in to_source.fields
            if field.target_field == relationship.to_field
        )
        from_mapping = next(
            field
            for field in from_source.fields
            if field.target_field == relationship.from_field
        )
        if from_mapping.data_type != target_mapping.data_type:
            raise EnvironmentInputError(
                "source_relationship_missing",
                "Relationship fields must use the same reviewed type",
            )
        if from_mapping.personal_data != target_mapping.personal_data:
            raise EnvironmentInputError(
                "source_privacy_unresolved",
                "Related fields must share the reviewed personal-data decision",
            )
        if not target_mapping.is_stable_id:
            raise EnvironmentInputError(
                "source_relationship_missing",
                "A relationship target must be a stable identifier",
            )
        missing = tuple(
            SourceDiagnostic(
                "source_relationship_missing",
                "blocking",
                f"Missing key {record.as_dict().get(relationship.from_field)!r} in "
                f"{relationship.to_source_id}.{relationship.to_field}",
                SourceSpan(
                    relationship.from_source_id,
                    row=record.source_row,
                    field=relationship.from_field,
                ),
            )
            for record in from_source.records
            if record.as_dict().get(relationship.from_field) not in target_values
        )
        if missing:
            raise EnvironmentInputError(
                "source_relationship_missing",
                (
                    f"{relationship.from_source_id}.{relationship.from_field} "
                    f"contains {len(missing)} value(s) absent from "
                    f"{relationship.to_source_id}.{relationship.to_field}"
                ),
                diagnostics=missing,
            )


def _apply_synthetic_replacements(
    sources: dict[str, ReviewedRecordSource],
    reviews: dict[str, RecordReview],
    relationships: tuple[RecordRelationship, ...],
) -> tuple[dict[str, ReviewedRecordSource], tuple[ReplacementReceipt, ...]]:
    parent: dict[tuple[str, str], tuple[str, str]] = {}

    def find(item: tuple[str, str]) -> tuple[str, str]:
        parent.setdefault(item, item)
        if parent[item] != item:
            parent[item] = find(parent[item])
        return parent[item]

    def union(left: tuple[str, str], right: tuple[str, str]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    mapping_by_key: dict[tuple[str, str], FieldMapping] = {}
    for source_id, review in reviews.items():
        for field in review.fields:
            key = (source_id, field.target_field)
            mapping_by_key[key] = field
            find(key)
    for relationship in relationships:
        union(
            (relationship.from_source_id, relationship.from_field),
            (relationship.to_source_id, relationship.to_field),
        )

    group_keys: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for key in mapping_by_key:
        group_keys[find(key)].append(key)
    replacements: dict[tuple[str, str, str], JsonScalar] = {}
    receipt: list[ReplacementReceipt] = []
    values_by_key: dict[tuple[str, str], set[JsonScalar]] = defaultdict(set)
    original_values: set[JsonScalar] = set()
    visible_original_values: set[JsonScalar] = set()
    for source_id, source in sources.items():
        for record in source.records:
            for field, value in record.values:
                if value is not None:
                    values_by_key[(source_id, field)].add(value)
                    original_values.add(value)
                    if source.audience.untrusted_recipients:
                        visible_original_values.add(value)
    for keys in group_keys.values():
        if not any(mapping_by_key[key].personal_data for key in keys):
            continue
        visible_keys = [
            key for key in keys if sources[key[0]].audience.untrusted_recipients
        ]
        representative = min(visible_keys or keys)
        mapping = mapping_by_key[representative]
        kind = _personal_kind(mapping.target_field)
        if mapping.data_type not in {"string", "integer", "number"} or (
            kind is None and mapping.data_type == "string"
        ):
            raise EnvironmentInputError(
                "source_privacy_unresolved",
                (
                    f"{representative[0]}.{representative[1]} has an unsupported type or free-text personal data; "
                    "exclude it or upload a sanitized source"
                ),
            )
        values = set().union(*(values_by_key[key] for key in keys))
        visible_values = set().union(*(values_by_key[key] for key in visible_keys))
        index = 1
        used: set[JsonScalar] = set()
        for originals, excluded in (
            (visible_values, visible_original_values),
            (values - visible_values, original_values),
        ):
            for value in sorted(originals, key=_canonical_json):
                while True:
                    synthetic = _synthetic_value(kind, mapping.data_type, index)
                    index += 1
                    if synthetic not in excluded and synthetic not in used:
                        break
                used.add(synthetic)
                for source_id, field in keys:
                    replacements[(source_id, field, _canonical_json(value))] = synthetic

    replaced_sources: dict[str, ReviewedRecordSource] = {}
    for source_id, source in sources.items():
        records: list[ReviewedRecord] = []
        stable_targets = tuple(
            field.target_field for field in source.fields if field.is_stable_id
        )
        mapping_by_target = {field.target_field: field for field in source.fields}
        for record in source.records:
            values: list[tuple[str, JsonScalar]] = []
            for field, value in record.values:
                synthetic = replacements.get(
                    (source_id, field, _canonical_json(value)), value
                )
                mapping = mapping_by_target[field]
                try:
                    converted = _convert_value(synthetic, mapping.data_type)
                    if type(converted) is not type(synthetic) or converted != synthetic:
                        raise ValueError(
                            "Replacement does not preserve the mapped type"
                        )
                except ValueError as exc:
                    raise EnvironmentInputError(
                        "source_privacy_unresolved",
                        "Replacement does not match the reviewed field type",
                    ) from exc
                values.append((field, synthetic))
                if synthetic != value:
                    receipt.append(
                        ReplacementReceipt(
                            source_id=source_id,
                            target_field=field,
                            original_value=value,
                            synthetic_value=synthetic,
                        )
                    )
            value_map = dict(values)
            records.append(
                ReviewedRecord(
                    record_id=_record_id(
                        tuple(value_map[field] for field in stable_targets)
                    ),
                    source_row=record.source_row,
                    values=tuple(values),
                )
            )
        replaced_sources[source_id] = ReviewedRecordSource(
            pin=source.pin,
            filename=source.filename,
            visibility=source.visibility,
            audience=source.audience,
            fields=source.fields,
            records=tuple(records),
            rejected_records=source.rejected_records,
            personal_data_mode=source.personal_data_mode,
        )
    _validate_relationships(replaced_sources, relationships)
    return replaced_sources, tuple(receipt)


def _can_convert(value: JsonScalar, data_type: DataType) -> bool:
    try:
        _convert_value(value, data_type)
        return True
    except ValueError:
        return False


def _infer_type(values: list[JsonScalar]) -> DataType:
    if not values:
        return "string"
    candidates: tuple[DataType, ...] = (
        "boolean",
        "integer",
        "number",
        "datetime",
        "date",
    )
    for candidate in candidates:
        try:
            for value in values:
                _convert_value(value, candidate)
        except ValueError:
            continue
        return candidate
    return "string"


def _convert_value(value: JsonScalar, data_type: DataType) -> JsonScalar:
    if value in (None, ""):
        return None
    if data_type == "string":
        if isinstance(value, (dict, list)):
            raise ValueError("nested values are not supported")
        return str(value)
    if data_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
        raise ValueError("expected true or false")
    if data_type == "integer":
        if isinstance(value, bool):
            raise ValueError("expected an integer")
        try:
            converted = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("expected an integer") from exc
        if isinstance(value, float) and not value.is_integer():
            raise ValueError("expected an integer")
        if isinstance(value, str) and str(converted) != value.strip():
            raise ValueError("expected an integer")
        return converted
    if data_type == "number":
        if isinstance(value, bool):
            raise ValueError("expected a number")
        try:
            converted = float(value)
            if not math.isfinite(converted):
                raise ValueError("expected a finite number")
            return converted
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("expected a number") from exc
    if data_type == "datetime":
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).isoformat()
        except ValueError as exc:
            raise ValueError("expected an ISO 8601 datetime") from exc
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError as exc:
        raise ValueError("expected an ISO 8601 date") from exc


def _personal_kind(field: str) -> str | None:
    lowered = field.lower()
    if "email" in lowered:
        return "email"
    if "phone" in lowered or "telephone" in lowered:
        return "phone"
    if "name" in lowered:
        return "name"
    if "address" in lowered:
        return "address"
    if lowered.endswith("_id") or lowered == "id":
        return "id"
    return None


def _synthetic_value(kind: str | None, data_type: DataType, index: int) -> JsonScalar:
    if data_type == "integer":
        return index
    if data_type == "number":
        return float(index)
    if kind == "email":
        return f"person{index:04d}@example.invalid"
    if kind == "phone":
        return f"+1-202-555-{100 + index:04d}"
    if kind == "name":
        return f"Person {index:04d}"
    if kind == "address":
        return f"Address {index:04d}"
    return f"synthetic_{index:04d}"
