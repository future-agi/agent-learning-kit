"""Local JSON transport for the pure source lifecycle.

Run ``python -m fi.alk.studio.environment_sources [request.json]``. Responses
contain private authoring data and must stay in caller-owned trusted storage.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, ValidationError

from fi.alk.harness.job import MAX_HOSTED_SCENARIO_COUNT
from fi.alk.studio._environment_input_parsers import (
    MAX_SOURCE_BYTES,
    _reject_json_constant,
    _unique_object,
)
from fi.alk.studio._environment_input_snapshots import (
    MAX_SNAPSHOT_BYTES,
    deserialize_environment_snapshot,
    serialize_environment_snapshot,
)
from fi.alk.studio._environment_input_types import (
    AgentEnvironmentSpec,
    DestinationSchema,
    EnvironmentInputDraft,
    EnvironmentInputError,
    EnvironmentInputReview,
    EnvironmentSource,
    SimulationSchemaRepair,
    SourceRole,
    _canonical_json,
    _canonical_value,
    _ImmutableValue,
)
from fi.alk.studio._environment_inputs import (
    compile_environment_inputs,
    prepare_environment_inputs,
    review_environment_inputs,
)
from fi.alk.studio._source_scenarios import (
    EnvironmentRequirements,
    _public_source_case,
    _reviewable_scenarios,
    _sealed_source_case,
)

REQUEST_VERSION = "environment-sources.request.v1"
RESPONSE_VERSION = "environment-sources.response.v1"
MAX_REQUEST_BYTES = 2 * MAX_SNAPSHOT_BYTES


@dataclass(frozen=True)
class EncodedEnvironmentSource(_ImmutableValue):
    source_id: str
    filename: str
    media_type: str
    role: SourceRole
    content_base64: str

    def decode(self) -> EnvironmentSource:
        if len(self.content_base64) > 4 * ((MAX_SOURCE_BYTES + 2) // 3):
            raise EnvironmentInputError(
                "source_too_large", "Encoded source exceeds the source limit"
            )
        try:
            content = base64.b64decode(self.content_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise EnvironmentInputError(
                "source_request_invalid", "Source content is not valid base64"
            ) from exc
        if len(content) > MAX_SOURCE_BYTES:
            raise EnvironmentInputError(
                "source_too_large", "Decoded source exceeds the source limit"
            )
        return EnvironmentSource(
            self.source_id, self.filename, self.media_type, content, self.role
        )


@dataclass(frozen=True)
class PrepareRequest(_ImmutableValue):
    version: Literal["environment-sources.request.v1"]
    operation: Literal["prepare"]
    sources: tuple[EncodedEnvironmentSource, ...]
    destination_schema: DestinationSchema | None = None
    schema_repairs: tuple[SimulationSchemaRepair, ...] = ()


@dataclass(frozen=True)
class AcceptRequest(_ImmutableValue):
    version: Literal["environment-sources.request.v1"]
    operation: Literal["accept"]
    draft: EnvironmentInputDraft
    review: EnvironmentInputReview
    reviewer_id: str
    existing_environments: tuple[AgentEnvironmentSpec, ...] = ()


@dataclass(frozen=True)
class CompileRequest(_ImmutableValue):
    version: Literal["environment-sources.request.v1"]
    operation: Literal["compile"]
    snapshot: dict[str, Any]
    existing_environments: tuple[AgentEnvironmentSpec, ...] = ()


@dataclass(frozen=True)
class GenerateScenariosRequest(_ImmutableValue):
    version: Literal["environment-sources.request.v1"]
    operation: Literal["generate_scenarios"]
    snapshot: dict[str, Any]
    environment_requirements: EnvironmentRequirements
    requested_count: Annotated[
        int, Field(strict=True, ge=1, le=MAX_HOSTED_SCENARIO_COUNT)
    ]
    bundle_manifest: dict[str, Any] | None = None


def _bundle_digest(manifest_body: dict[str, Any], pin: dict[str, str | None]) -> str:
    from fi.alk.harness.bundle_v2 import EnvironmentBundleV2, seal_bundle_v2

    try:
        manifest = EnvironmentBundleV2.model_validate_json(
            _canonical_json(manifest_body)
        )
        if (
            pin["schema_digest"] is not None
            and manifest.metadata.get("reviewed_source") == pin
            and manifest.digest == seal_bundle_v2(manifest)
        ):
            return manifest.digest
    except (ValidationError, ValueError):
        pass
    raise EnvironmentInputError(
        "source_bundle_invalid", "The bundle does not match the approved source pin."
    )


def _generate_scenarios(request: GenerateScenariosRequest) -> dict[str, Any]:
    from fi.alk.harness.folder import document_for
    from fi.alk.harness.scenario import SOURCE_FIXTURE, Scenario
    from fi.alk.harness.source_grounding import bind_source_case, safe_source_context

    try:
        reviewed = deserialize_environment_snapshot(
            _canonical_json(request.snapshot).encode()
        )
    except EnvironmentInputError as exc:
        raise EnvironmentInputError(
            exc.code, "The approved source snapshot is invalid."
        ) from None
    pin = {
        "id": reviewed.snapshot_id,
        "digest": reviewed.snapshot_digest,
        "schema_digest": reviewed.simulation_schema_digest,
    }
    result = {
        "snapshot_id": pin["id"],
        "snapshot_digest": pin["digest"],
        "simulation_schema_digest": pin["schema_digest"],
    }
    if request.bundle_manifest is not None:
        result["bundle_digest"] = _bundle_digest(request.bundle_manifest, pin)
    generated = _reviewable_scenarios(
        reviewed,
        environment_requirements=request.environment_requirements,
        requested_count=request.requested_count,
    )
    scenarios = []
    for draft in generated.drafts:
        case = _sealed_source_case(_public_source_case(draft))
        scenario = draft.scenario.model_dump(mode="json")
        persona = scenario["dataset"][0]
        if draft.category == "negative_control":
            # The calibrated incorrect response belongs only to the evaluator.
            persona["situation"] = (
                "Ask about the boundary described by the approved source."
            )
        context = safe_source_context(case)
        native_args = bind_source_case(
            {
                "name": draft.scenario_id,
                "scenario_key": draft.scenario_id,
                "use_case": request.environment_requirements.task.strip(),
                "branch": draft.category,
                "instruction": (
                    request.environment_requirements.task.strip() + f" for {context}."
                ),
                "tests": case["purpose"],
                "fixture": {"origin": "seed", SOURCE_FIXTURE: case},
            },
            case,
            require_relevance=False,
        )
        native = Scenario.model_validate(native_args)
        scenarios.append(
            {"scenario": scenario, "native": document_for(native), "source_case": case}
        )
    return {**result, "revision": generated.revision, "scenarios": scenarios}


def execute_request(payload: bytes) -> dict[str, Any]:
    """Return one versioned success/error envelope without filesystem writes."""
    operation: str | None = None
    try:
        if not isinstance(payload, bytes) or len(payload) > MAX_REQUEST_BYTES:
            raise EnvironmentInputError(
                "source_too_large", "Request exceeds the size limit"
            )
        data = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if isinstance(data, dict) and isinstance(data.get("operation"), str):
            operation = data["operation"]
        request = TypeAdapter(
            PrepareRequest | AcceptRequest | CompileRequest | GenerateScenariosRequest
        ).validate_json(_canonical_json(data))
        if isinstance(request, PrepareRequest):
            draft = prepare_environment_inputs(
                tuple(source.decode() for source in request.sources),
                destination_schema=request.destination_schema,
                schema_repairs=request.schema_repairs,
            )
            result = {"draft": draft, "schema_digest": draft.schema_digest}
        elif isinstance(request, GenerateScenariosRequest):
            result = _generate_scenarios(request)
        else:
            if isinstance(request, AcceptRequest):
                reviewed = review_environment_inputs(
                    request.draft,
                    review=request.review,
                    reviewer_id=request.reviewer_id,
                )
            else:
                reviewed = deserialize_environment_snapshot(
                    _canonical_json(request.snapshot).encode()
                )
            compiled = compile_environment_inputs(
                reviewed, existing_environments=request.existing_environments
            )
            result = {
                "snapshot": json.loads(serialize_environment_snapshot(reviewed)),
                "compiled": compiled,
            }
        return {
            "version": RESPONSE_VERSION,
            "ok": True,
            "operation": request.operation,
            "result": _canonical_value(result),
        }
    except EnvironmentInputError as exc:
        code, message, diagnostics = exc.code, str(exc), exc.diagnostics
    except ValidationError as exc:
        code = (
            "source_audience_invalid"
            if any("audience" in error["loc"] for error in exc.errors())
            else "source_request_invalid"
        )
        message, diagnostics = "The request contains invalid typed values", ()
    except (ValueError, TypeError, KeyError, RecursionError):
        code, message, diagnostics = (
            "source_request_invalid",
            "The source request is invalid",
            (),
        )
    return _error_response(code, message, diagnostics, operation=operation)


def request_too_large_response() -> dict[str, Any]:
    return _error_response(
        "source_too_large", "Request exceeds the size limit", (), operation=None
    )


def _error_response(
    code: str,
    message: str,
    diagnostics: tuple[Any, ...],
    *,
    operation: str | None,
) -> dict[str, Any]:
    return {
        "version": RESPONSE_VERSION,
        "ok": False,
        "operation": operation,
        "error": {
            "code": code,
            "message": message,
            "diagnostics": _canonical_value(diagnostics),
        },
    }


def main() -> int:
    if len(sys.argv) > 2:
        response = execute_request(b"")
    else:
        try:
            if len(sys.argv) == 2:
                with Path(sys.argv[1]).open("rb") as stream:
                    payload = stream.read(MAX_REQUEST_BYTES + 1)
            else:
                payload = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            response = execute_request(payload)
        except OSError:
            response = _error_response(
                "source_request_invalid",
                "The request file could not be read",
                (),
                operation=None,
            )
    sys.stdout.write(_canonical_json(response) + "\n")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
