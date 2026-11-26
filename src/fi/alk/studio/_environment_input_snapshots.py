from __future__ import annotations

import json
import re
from typing import Any

from pydantic import TypeAdapter, ValidationError

from fi.alk.studio._environment_input_parsers import (
    _reject_json_constant,
    _unique_object,
)
from fi.alk.studio._environment_input_types import (
    EnvironmentInputError,
    RecordRelationship,
    ReviewedEnvironmentInputs,
    ReviewedSource,
    SourceDiagnostic,
    _canonical_json,
    _canonical_value,
    _content_digest,
    _normalize_audience,
)

LEGACY_SNAPSHOT_SCHEMA_VERSION = "environment-inputs.snapshot.v1"
SNAPSHOT_SCHEMA_VERSION = "environment-inputs.snapshot.v2"
SIMULATION_SNAPSHOT_SCHEMA_VERSION = "environment-inputs.snapshot.v3"
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


def serialize_environment_snapshot(reviewed: ReviewedEnvironmentInputs) -> bytes:
    """Return a canonical private snapshot for caller-owned trusted storage."""
    _assert_reviewed_snapshot(reviewed)
    data = _snapshot_data(reviewed)
    payload = _canonical_json(
        {
            "version": reviewed.schema_version,
            "data": data,
            "digest": _content_digest(data),
        }
    ).encode("utf-8")
    if len(payload) > MAX_SNAPSHOT_BYTES:
        raise EnvironmentInputError(
            "source_snapshot_invalid", "Snapshot exceeds the size limit"
        )
    return payload


def deserialize_environment_snapshot(payload: bytes) -> ReviewedEnvironmentInputs:
    """Validate snapshot integrity and reconstruct deeply immutable domain values."""
    try:
        if not isinstance(payload, bytes) or len(payload) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Snapshot exceeds the size limit or is not bytes")
        envelope = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if set(envelope) != {"version", "data", "digest"} or envelope[
            "version"
        ] not in {
            LEGACY_SNAPSHOT_SCHEMA_VERSION,
            SNAPSHOT_SCHEMA_VERSION,
            SIMULATION_SNAPSHOT_SCHEMA_VERSION,
        }:
            raise ValueError("Unknown snapshot schema")
        if _content_digest(envelope["data"]) != envelope["digest"]:
            raise ValueError("Snapshot envelope digest mismatch")
        if any(
            "personal_data_mode" not in source
            for source in envelope["data"]["sources"]
            if "records" in source
        ):
            raise ValueError("Record snapshot is missing its privacy decision")
        data = envelope["data"]
        if "schema_version" in data:
            raise ValueError("Snapshot schema belongs in its envelope")
        if envelope["version"] == LEGACY_SNAPSHOT_SCHEMA_VERSION:
            if any(
                "audience" in source or source["pin"]["role"] == "reference"
                for source in data["sources"]
            ):
                raise ValueError(
                    "Legacy snapshots cannot grant new audiences or purposes"
                )
        elif any("audience" not in source for source in data["sources"]):
            raise ValueError("Snapshot is missing its audience decision")
        reviewed = TypeAdapter(ReviewedEnvironmentInputs).validate_json(
            _canonical_json({**data, "schema_version": envelope["version"]})
        )
        _assert_reviewed_snapshot(reviewed)
        return reviewed
    except (ValueError, TypeError, KeyError, ValidationError, RecursionError) as exc:
        raise EnvironmentInputError(
            "source_snapshot_invalid", "The source snapshot is invalid or changed"
        ) from exc


def _reviewed_snapshot_digest(
    sources: tuple[ReviewedSource, ...],
    relationships: tuple[RecordRelationship, ...],
    diagnostics: tuple[SourceDiagnostic, ...] = (),
    schema_version: str = SNAPSHOT_SCHEMA_VERSION,
    simulation_schema_digest: str | None = None,
) -> str:
    source_data = _canonical_value(sources)
    if schema_version == LEGACY_SNAPSHOT_SCHEMA_VERSION:
        for source in source_data:
            source.pop("audience")
    return _content_digest(
        {
            "sources": source_data,
            "relationships": relationships,
            "diagnostics": diagnostics,
            **(
                {"simulation_schema_digest": simulation_schema_digest}
                if schema_version == SIMULATION_SNAPSHOT_SCHEMA_VERSION
                else {}
            ),
        }
    )


def _snapshot_data(reviewed: ReviewedEnvironmentInputs) -> dict[str, Any]:
    data = _canonical_value(reviewed)
    data.pop("schema_version")
    if reviewed.schema_version == SIMULATION_SNAPSHOT_SCHEMA_VERSION:
        if not re.fullmatch(
            r"sha256:[0-9a-f]{64}", reviewed.simulation_schema_digest or ""
        ):
            raise EnvironmentInputError(
                "source_snapshot_invalid", "Missing simulation schema digest"
            )
    else:
        if data.pop("simulation_schema_digest") is not None:
            raise EnvironmentInputError(
                "source_snapshot_invalid", "Unexpected simulation schema digest"
            )
    if reviewed.schema_version == LEGACY_SNAPSHOT_SCHEMA_VERSION:
        for source in data["sources"]:
            if (
                source["audience"]
                != _canonical_value(_normalize_audience(source["visibility"], None))
                or source["pin"]["role"] == "reference"
            ):
                raise EnvironmentInputError(
                    "source_snapshot_invalid",
                    "Legacy snapshots cannot grant new audiences or purposes",
                )
            source.pop("audience")
    return data


def _assert_reviewed_snapshot(reviewed: ReviewedEnvironmentInputs) -> None:
    _snapshot_data(reviewed)
    digest = _reviewed_snapshot_digest(
        reviewed.sources,
        reviewed.relationships,
        reviewed.diagnostics,
        reviewed.schema_version,
        reviewed.simulation_schema_digest,
    )
    expected_id = f"source_snapshot_{digest.removeprefix('sha256:')[:16]}"
    if reviewed.snapshot_digest != digest or reviewed.snapshot_id != expected_id:
        raise EnvironmentInputError(
            "source_snapshot_invalid",
            "The approved source snapshot has changed and must be reviewed again",
        )
