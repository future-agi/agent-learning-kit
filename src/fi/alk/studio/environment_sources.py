"""Local JSON transport for the pure source lifecycle.

Run ``python -m fi.alk.studio.environment_sources [request.json]``. Responses
contain private authoring data and must stay in caller-owned trusted storage.
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import shutil
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
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
    _distinct_source_drafts,
    EnvironmentRequirements,
    _public_source_case,
    _reviewable_scenarios,
    _sealed_source_case,
)

REQUEST_VERSION = "environment-sources.request.v1"
RESPONSE_VERSION = "environment-sources.response.v1"
MAX_REQUEST_BYTES = 2 * MAX_SNAPSHOT_BYTES
MAX_AUTHORING_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_EXPANDED_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100_000


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
    prior_source_projection: tuple[AgentEnvironmentSpec, ...] = ()


@dataclass(frozen=True)
class CompileRequest(_ImmutableValue):
    version: Literal["environment-sources.request.v1"]
    operation: Literal["compile"]
    snapshot: dict[str, Any]
    existing_environments: tuple[AgentEnvironmentSpec, ...] = ()
    prior_source_projection: tuple[AgentEnvironmentSpec, ...] = ()


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
    distinct_source_cases: bool = False


@dataclass(frozen=True)
class ResealScenariosRequest(_ImmutableValue):
    version: Literal["environment-sources.request.v1"]
    operation: Literal["reseal_scenarios"]
    snapshot: dict[str, Any]
    authoring_archive_base64: str
    mode: Literal["reviewed_build", "existing_environment"]
    generated_scenario_keys: Annotated[tuple[str, ...], Field(min_length=1)]
    schema_digest: str | None = None
    distinct_case_budget: Annotated[int, Field(strict=True, ge=1, le=MAX_HOSTED_SCENARIO_COUNT)] | None = None


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


def _native_source_scenario(draft: Any, requirements: EnvironmentRequirements):
    from fi.alk.harness.folder import document_for
    from fi.alk.harness.scenario import SOURCE_FIXTURE, Scenario
    from fi.alk.harness.source_grounding import bind_source_case, safe_source_context

    case = _sealed_source_case(_public_source_case(draft))
    context = safe_source_context(case)
    # Existing environments receive native scenarios directly, without a writer.
    # Put a checkable expectation in the test itself, before binding citations.
    facts = case["starting_facts"]
    rule = case["policy_reference"]
    checks = []
    if rule:
        checks.append(f"The response must apply this source passage accurately: {rule}")
    for source_id, record_id in dict.fromkeys((f["source_id"], f["record_id"]) for f in facts):
        values = [
            f"{fact['field'].rsplit('/', 1)[-1]} = {fact['value']}"
            for fact in facts if (fact["source_id"], fact["record_id"]) == (source_id, record_id)
        ]
        checks.append(f"Check the response against record {record_id}: " + "; ".join(values))
    instruction = f"{requirements.task.strip()} for {context}."
    if rule:
        instruction += f" Ask how the following passage applies: {rule}"
    native_args = bind_source_case(
        {
            "name": draft.scenario_id,
            "scenario_key": draft.scenario_id,
            "use_case": requirements.task.strip(),
            "branch": draft.category,
            "instruction": instruction,
            "tests": " ".join([case["purpose"], *checks]),
            "fixture": {"origin": "seed", SOURCE_FIXTURE: case},
        },
        case,
        require_relevance=False,
    )
    native = Scenario.model_validate(native_args)
    return native, document_for(native), case


def _generate_scenarios(request: GenerateScenariosRequest) -> dict[str, Any]:

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
    drafts = _distinct_source_drafts(generated) if request.distinct_source_cases else generated.drafts
    for draft in drafts:
        scenario = draft.scenario.model_dump(mode="json")
        persona = scenario["dataset"][0]
        if draft.category == "negative_control":
            # The calibrated incorrect response belongs only to the evaluator.
            persona["situation"] = (
                "Ask about the boundary described by the approved source."
            )
        _, native, case = _native_source_scenario(
            draft, request.environment_requirements
        )
        scenarios.append({"scenario": scenario, "native": native, "source_case": case})
    return {**result, "revision": generated.revision, "scenarios": scenarios}


def _extract_authoring_archive(encoded: str, destination: Path) -> None:
    if len(encoded) > 4 * ((MAX_AUTHORING_ARCHIVE_BYTES + 2) // 3):
        raise EnvironmentInputError(
            "source_too_large", "Authoring archive exceeds the size limit"
        )
    try:
        body = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise EnvironmentInputError(
            "source_request_invalid", "Authoring archive is not valid base64"
        ) from None
    if len(body) > MAX_AUTHORING_ARCHIVE_BYTES:
        raise EnvironmentInputError(
            "source_too_large", "Authoring archive exceeds the size limit"
        )
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
            total = 0
            count = 0
            seen: set[str] = set()
            for member in archive:
                count += 1
                if count > MAX_ARCHIVE_MEMBERS:
                    raise EnvironmentInputError(
                        "source_too_large", "Authoring archive has too many files"
                    )
                relative = PurePosixPath(member.name)
                if (
                    not member.name
                    or member.name in seen
                    or member.name.startswith("/")
                    or "\\" in member.name
                    or any(part in {"", ".", ".."} for part in relative.parts)
                    or member.issym()
                    or member.islnk()
                    or not (member.isdir() or member.isfile())
                ):
                    raise EnvironmentInputError(
                        "source_archive_invalid",
                        "Authoring archive contains an unsafe entry",
                    )
                seen.add(member.name)
                total += member.size
                if total > MAX_EXPANDED_ARCHIVE_BYTES:
                    raise EnvironmentInputError(
                        "source_too_large",
                        "Expanded authoring archive exceeds the size limit",
                    )
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise EnvironmentInputError(
                        "source_archive_invalid",
                        "Authoring archive contains an unreadable file",
                    )
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                target.chmod(member.mode & 0o777)
    except (tarfile.TarError, OSError):
        raise EnvironmentInputError(
            "source_archive_invalid", "Authoring archive is invalid"
        ) from None


def _bundle_files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and path.name != "manifest.json"
        and "scenarios" not in path.relative_to(root).parts
        and path.name != "reviewed-source.json"
    }


def _scenario_files(root: Path, name: str) -> dict[str, bytes]:
    folder = root / "scenarios" / name
    return {
        path.relative_to(folder).as_posix(): path.read_bytes()
        for path in sorted(folder.rglob("*"))
        if path.is_file()
    }


def _same_inherited_scenario(
    current: dict[str, bytes], sealed: dict[str, bytes]
) -> bool:
    if current.keys() != sealed.keys():
        return False
    for path, body in current.items():
        if path == "scenario.json":
            try:
                if json.loads(body) != json.loads(sealed[path]):
                    return False
            except ValueError:
                return False
        elif body != sealed[path]:
            return False
    return True


def _reseal_scenarios(request: ResealScenariosRequest) -> dict[str, Any]:
    from fi.alk.harness.bundle_author_v2 import reseal_scenarios
    from fi.alk.harness.bundle_v2 import load_bundle_v2, seal_bundle_v2
    from fi.alk.harness.folder import (
        document_for,
        read_all,
        ready_document,
        setup_document,
    )
    from fi.alk.harness.hosted_conversation import _workspace_archive
    from fi.alk.harness.reviewed_build import load_review_pin
    from fi.alk.harness.source_grounding import semantic_source_digest

    reviewed = deserialize_environment_snapshot(
        _canonical_json(request.snapshot).encode()
    )
    snapshot_schema = reviewed.simulation_schema_digest
    supplied_schema = request.schema_digest
    if snapshot_schema is not None and supplied_schema not in (None, snapshot_schema):
        raise EnvironmentInputError(
            "source_schema_changed",
            "The supplied schema digest does not match the snapshot",
        )
    schema_digest = snapshot_schema or supplied_schema
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", schema_digest or ""):
        raise EnvironmentInputError(
            "source_schema_required", "Supply the accepted simulation schema digest"
        )
    pin = {
        "id": reviewed.snapshot_id,
        "digest": reviewed.snapshot_digest,
        "schema_digest": schema_digest,
    }
    with tempfile.TemporaryDirectory(prefix="alk-source-reseal-") as temporary:
        authoring = Path(temporary)
        _extract_authoring_archive(request.authoring_archive_base64, authoring)
        try:
            bundle = authoring / "environment-bundle"
            previous = load_bundle_v2(bundle)
            if previous.digest != seal_bundle_v2(previous):
                raise EnvironmentInputError(
                    "source_bundle_invalid", "The sealed bundle is not canonical"
                )
            sealed_pin = previous.metadata.get("reviewed_source")
            if request.mode == "reviewed_build":
                archived_pin = load_review_pin(authoring)
                if archived_pin != pin or sealed_pin != pin:
                    raise EnvironmentInputError(
                        "source_snapshot_changed",
                        "The reviewed build pin does not match the snapshot",
                    )
            else:
                (authoring / "reviewed-source.json").write_text(
                    _canonical_json(pin), encoding="utf-8"
                )

            scenarios = read_all(authoring)
            by_key = {scenario.scenario_key: scenario for scenario in scenarios}
            if len(by_key) != len(scenarios):
                raise EnvironmentInputError(
                    "source_suite_invalid",
                    "The archive contains duplicate scenario keys",
                )
            generated_keys = set(request.generated_scenario_keys)
            if len(generated_keys) != len(request.generated_scenario_keys):
                raise EnvironmentInputError(
                    "source_suite_invalid", "Generated scenario keys must be unique"
                )
            if request.mode == "existing_environment":
                inherited = read_all(bundle)
                inherited_by_key = {
                    scenario.scenario_key: scenario for scenario in inherited
                }
                if len(inherited_by_key) != len(inherited):
                    raise EnvironmentInputError(
                        "source_bundle_invalid",
                        "The sealed bundle has duplicate scenario keys",
                    )
                if generated_keys & inherited_by_key.keys() or set(by_key) != (
                    set(inherited_by_key) | generated_keys
                ):
                    raise EnvironmentInputError(
                        "source_suite_invalid",
                        "Generated keys do not identify the archive's exact added scenario set",
                    )
                for key, inherited_scenario in inherited_by_key.items():
                    current = by_key[key]
                    if not _same_inherited_scenario(
                        _scenario_files(authoring, current.name),
                        _scenario_files(bundle, inherited_scenario.name),
                    ):
                        raise EnvironmentInputError(
                            "source_suite_invalid", "An inherited scenario was changed"
                        )
            elif generated_keys != set(by_key):
                raise EnvironmentInputError(
                    "source_suite_invalid",
                    "Generated keys do not identify the reviewed build's exact suite",
                )

            generated_scenarios = [
                by_key[key] for key in request.generated_scenario_keys
            ]
            tasks = {
                scenario.use_case.strip()
                for scenario in generated_scenarios
                if scenario.use_case.strip()
            }
            if len(tasks) != 1:
                raise EnvironmentInputError(
                    "source_suite_invalid",
                    "Generated scenarios must share one source task",
                )
            requirements = EnvironmentRequirements(task=tasks.pop())
            generated = _reviewable_scenarios(
                reviewed,
                environment_requirements=requirements,
                requested_count=request.distinct_case_budget or len(generated_scenarios),
            )
            drafts = _distinct_source_drafts(generated) if request.distinct_case_budget else generated.drafts
            expected = {
                native.scenario_key: (native, document, case)
                for draft in drafts
                for native, document, case in [
                    _native_source_scenario(draft, requirements)
                ]
            }
            if generated_keys != expected.keys():
                raise EnvironmentInputError(
                    "source_suite_invalid",
                    "The archive scenario set does not match the approved cases",
                )
            for scenario in generated_scenarios:
                native, document, case = expected[scenario.scenario_key]
                folder = authoring / "scenarios" / scenario.name
                files = {
                    path.relative_to(folder).as_posix()
                    for path in folder.rglob("*")
                    if path.is_file()
                }
                complete_files = {"scenario.json", "setup.py", "ready.py"}
                if request.mode == "reviewed_build":
                    valid_files = files == complete_files
                else:
                    valid_files = files in ({"scenario.json"}, complete_files)
                if not valid_files:
                    raise EnvironmentInputError(
                        "source_suite_invalid",
                        "Generated source scenarios contain unexpected files",
                    )
                expected_scripts = {
                    "setup.py": setup_document(native).encode(),
                    "ready.py": ready_document(native).encode(),
                }
                if files == complete_files and any(
                    (folder / name).read_bytes() != body
                    for name, body in expected_scripts.items()
                ):
                    raise EnvironmentInputError(
                        "source_suite_invalid",
                        "A generated source scenario script was changed",
                    )
                if document_for(scenario) != document or semantic_source_digest(
                    case, scenario
                ) != semantic_source_digest(case, native):
                    raise EnvironmentInputError(
                        "source_suite_invalid",
                        "A generated source scenario was changed",
                    )
                if files == {"scenario.json"}:
                    for name, body in expected_scripts.items():
                        (folder / name).write_bytes(body)
            held = _bundle_files(bundle)
            inherited_files = (
                {
                    key: _scenario_files(bundle, scenario.name)
                    for key, scenario in inherited_by_key.items()
                }
                if request.mode == "existing_environment"
                else {}
            )
            digest = reseal_scenarios(
                authoring,
                reviewed_source_pin=(
                    pin if request.mode == "existing_environment" else None
                ),
            )
            if digest is None:
                raise EnvironmentInputError(
                    "source_bundle_invalid",
                    "The archive has no sealed environment bundle",
                )
            resealed = load_bundle_v2(bundle)
            if (
                resealed.digest != digest
                or resealed.digest != seal_bundle_v2(resealed)
                or resealed.metadata.get("reviewed_source") != pin
            ):
                raise EnvironmentInputError(
                    "source_bundle_invalid", "The resealed bundle is invalid"
                )
            if _bundle_files(bundle) != held:
                raise EnvironmentInputError(
                    "source_bundle_invalid",
                    "Resealing changed non-scenario bundle files",
                )
            resealed_by_key = {
                scenario.scenario_key: scenario for scenario in read_all(bundle)
            }
            if any(
                key not in resealed_by_key
                or _scenario_files(bundle, resealed_by_key[key].name) != files
                for key, files in inherited_files.items()
            ):
                raise EnvironmentInputError(
                    "source_bundle_invalid", "Resealing changed an inherited scenario"
                )
            sealed = _workspace_archive(authoring)
        except EnvironmentInputError:
            raise
        except Exception:
            raise EnvironmentInputError(
                "source_bundle_invalid", "The authoring archive could not be resealed"
            ) from None
    return {
        "authoring_archive_base64": base64.b64encode(sealed).decode(),
        "bundle_digest": digest,
        "reviewed_source": pin,
    }


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
            PrepareRequest
            | AcceptRequest
            | CompileRequest
            | GenerateScenariosRequest
            | ResealScenariosRequest
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
        elif isinstance(request, ResealScenariosRequest):
            result = _reseal_scenarios(request)
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
                reviewed, existing_environments=request.existing_environments,
                prior_source_projection=request.prior_source_projection,
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
