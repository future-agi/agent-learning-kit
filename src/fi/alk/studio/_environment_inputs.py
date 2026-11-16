from __future__ import annotations

import hashlib
import json
import math
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

from pydantic import ConfigDict, TypeAdapter, ValidationError

from fi.alk.studio._environment_input_parsers import (
    MAX_DESIGN_BYTES,
    MAX_SOURCE_FILES,
    PARSER_REVISION,
    RawPolicyDocument,
    RawRecordDocument,
    SourceParseError,
    parse_environment_source,
)
from fi.alk.studio._scan import scan_content
from fi.simulate.manifest import ManifestError
from fi.simulate.suite import (
    DETERMINISTIC_ASSERTION_TYPES,
    validate_deterministic_assertion,
)

SourceRole: TypeAlias = Literal["policy", "seed_records"]
SourceVisibility: TypeAlias = Literal["agent_visible", "evaluator_only"]
PersonalDataMode: TypeAlias = Literal["none", "synthetic_replace"]
DataType: TypeAlias = Literal[
    "string", "integer", "number", "boolean", "date", "datetime"
]
RuleDecision: TypeAlias = Literal["accepted", "rejected", "reference_only"]
JsonScalar: TypeAlias = str | int | float | bool | None

SUPPORTED_CHECK_TYPES = DETERMINISTIC_ASSERTION_TYPES


class _ImmutableValue:
    __pydantic_config__ = ConfigDict(extra="forbid", strict=True)

    def __post_init__(self) -> None:
        for field in fields(self):
            object.__setattr__(
                self, field.name, _deep_freeze(getattr(self, field.name))
            )


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\d)(?:\+?\d[\d .()-]{7,}\d)(?!\d)")
_PERSONAL_FIELD_RE = re.compile(
    r"(?:^|_)(?:customer|buyer|user|person|member|patient|name|email|phone|address)(?:_|$)",
    re.IGNORECASE,
)


class EnvironmentInputError(ValueError):
    """A stable source-lifecycle error for API callers."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        diagnostics: tuple[SourceDiagnostic, ...] = (),
    ):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics


@dataclass(frozen=True)
class EnvironmentSource(_ImmutableValue):
    source_id: str
    filename: str
    media_type: str
    content: bytes
    role: SourceRole

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("source_id must be nonempty")
        if not self.filename.strip():
            raise ValueError("filename must be nonempty")
        if self.role not in {"policy", "seed_records"}:
            raise ValueError("role must be policy or seed_records")


@dataclass(frozen=True)
class SourcePin(_ImmutableValue):
    source_id: str
    source_sha256: str
    parser_revision: str
    role: SourceRole


@dataclass(frozen=True)
class SourceSpan(_ImmutableValue):
    source_id: str
    page: int | None = None
    row: int | None = None
    field: str | None = None
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class SourceDiagnostic(_ImmutableValue):
    code: str
    severity: Literal["warning", "blocking"]
    message: str
    span: SourceSpan | None = None


@dataclass(frozen=True)
class DestinationField(_ImmutableValue):
    name: str
    data_type: DataType
    required: bool = False


@dataclass(frozen=True)
class DestinationSchema(_ImmutableValue):
    fields: tuple[DestinationField, ...]

    def __post_init__(self) -> None:
        super().__post_init__()
        names = [field.name for field in self.fields]
        if not names or any(not name.strip() for name in names):
            raise ValueError("destination schema fields must be nonempty")
        if len(set(names)) != len(names):
            raise ValueError("destination schema fields must be unique")

    def field(self, name: str) -> DestinationField | None:
        return next((field for field in self.fields if field.name == name), None)


@dataclass(frozen=True)
class FieldProfile(_ImmutableValue):
    source_field: str
    inferred_type: DataType
    sample_values: tuple[JsonScalar, ...]
    null_count: int
    unique_count: int
    suggested_target_field: str | None
    suggested_stable_id: bool
    personal_data_suspected: bool


@dataclass(frozen=True)
class ParsedRecordRow(_ImmutableValue):
    source_row: int
    values: tuple[tuple[str, JsonScalar], ...]

    def as_dict(self) -> dict[str, JsonScalar]:
        return dict(self.values)


@dataclass(frozen=True)
class ParsedRecordSource(_ImmutableValue):
    pin: SourcePin
    filename: str
    rows: tuple[ParsedRecordRow, ...]
    fields: tuple[FieldProfile, ...]
    rejected_records: tuple[RejectedRecord, ...] = ()
    parse_state: Literal["needs_review"] = "needs_review"


@dataclass(frozen=True)
class ParsedPolicyPage(_ImmutableValue):
    page: int
    text: str


@dataclass(frozen=True)
class PolicyStatement(_ImmutableValue):
    statement_id: str
    text: str
    span: SourceSpan


@dataclass(frozen=True)
class ParsedPolicySource(_ImmutableValue):
    pin: SourcePin
    filename: str
    pages: tuple[ParsedPolicyPage, ...]
    statements: tuple[PolicyStatement, ...]
    parse_state: Literal["needs_review"] = "needs_review"


@dataclass(frozen=True)
class FailedEnvironmentSource(_ImmutableValue):
    pin: SourcePin
    filename: str
    parse_state: Literal["parse_blocked"] = "parse_blocked"


ParsedSource: TypeAlias = (
    ParsedRecordSource | ParsedPolicySource | FailedEnvironmentSource
)


@dataclass(frozen=True)
class EnvironmentInputDraft(_ImmutableValue):
    sources: tuple[ParsedSource, ...]
    destination_schema: DestinationSchema
    diagnostics: tuple[SourceDiagnostic, ...]
    draft_digest: str
    proposals: SourceProposals = dataclass_field(
        default_factory=lambda: SourceProposals()
    )


@dataclass(frozen=True)
class FieldMapping(_ImmutableValue):
    source_field: str
    target_field: str
    data_type: DataType
    is_stable_id: bool = False
    personal_data: bool = False


@dataclass(frozen=True)
class RecordRelationship(_ImmutableValue):
    from_source_id: str
    from_field: str
    to_source_id: str
    to_field: str


@dataclass(frozen=True)
class RecordReview(_ImmutableValue):
    source_id: str
    visibility: SourceVisibility
    personal_data_mode: PersonalDataMode
    fields: tuple[FieldMapping, ...]


@dataclass(frozen=True)
class PolicyRuleProposal(_ImmutableValue):
    rule_id: str
    statement_id: str
    kind: Literal["required_behavior", "forbidden_behavior", "refusal", "eligibility"]
    check_type: str | None
    check_arguments: Mapping[str, JsonScalar]
    conflict_group: str | None = None
    status: Literal["supported", "unsupported", "conflict"] = "supported"

    def __post_init__(self) -> None:
        object.__setattr__(self, "check_arguments", _deep_freeze(self.check_arguments))


@dataclass(frozen=True)
class PolicyRuleReview(_ImmutableValue):
    proposal: PolicyRuleProposal
    decision: RuleDecision


@dataclass(frozen=True)
class PolicyReview(_ImmutableValue):
    source_id: str
    visibility: SourceVisibility
    rules: tuple[PolicyRuleReview, ...] = ()


@dataclass(frozen=True)
class EnvironmentInputReview(_ImmutableValue):
    expected_draft_digest: str
    record_reviews: tuple[RecordReview, ...]
    policy_reviews: tuple[PolicyReview, ...]
    relationships: tuple[RecordRelationship, ...] = ()


@dataclass(frozen=True)
class RejectedRecord(_ImmutableValue):
    source_id: str
    source_row: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ReviewedRecord(_ImmutableValue):
    record_id: str
    source_row: int
    values: tuple[tuple[str, JsonScalar], ...]

    def as_dict(self) -> dict[str, JsonScalar]:
        return dict(self.values)


@dataclass(frozen=True)
class ReviewedRecordSource(_ImmutableValue):
    pin: SourcePin
    filename: str
    visibility: SourceVisibility
    fields: tuple[FieldMapping, ...]
    records: tuple[ReviewedRecord, ...]
    rejected_records: tuple[RejectedRecord, ...]
    personal_data_mode: PersonalDataMode = "none"


@dataclass(frozen=True)
class ReviewedPolicySource(_ImmutableValue):
    pin: SourcePin
    filename: str
    visibility: SourceVisibility
    pages: tuple[ParsedPolicyPage, ...]
    statements: tuple[PolicyStatement, ...]
    rule_reviews: tuple[PolicyRuleReview, ...]

    @property
    def active_rules(self) -> tuple[PolicyRuleProposal, ...]:
        return tuple(
            review.proposal
            for review in self.rule_reviews
            if review.decision == "accepted"
        )


ReviewedSource: TypeAlias = ReviewedRecordSource | ReviewedPolicySource


@dataclass(frozen=True)
class ReplacementReceipt(_ImmutableValue):
    source_id: str
    target_field: str
    original_value: JsonScalar
    synthetic_value: JsonScalar


@dataclass(frozen=True)
class ReviewedEnvironmentInputs(_ImmutableValue):
    snapshot_id: str
    snapshot_digest: str
    reviewer_id: str
    reviewed_at: datetime
    sources: tuple[ReviewedSource, ...]
    relationships: tuple[RecordRelationship, ...]
    private_replacement_receipt: tuple[ReplacementReceipt, ...]
    diagnostics: tuple[SourceDiagnostic, ...] = ()


@dataclass(frozen=True)
class EvaluatorInputSet(_ImmutableValue):
    policies: tuple[ReviewedPolicySource, ...]
    records: tuple[ReviewedRecordSource, ...]
    rules: tuple[PolicyRuleProposal, ...]


@dataclass(frozen=True)
class AgentEnvironmentSpec:
    type: str
    data: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field in ("documents", "artifacts"):
            if field in self.data:
                _environment_entry_ids(self.data[field], field)
        object.__setattr__(self, "data", _deep_freeze(self.data))

    def as_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": _deep_thaw(self.data)}


@dataclass(frozen=True)
class PublicSourceProvenance(_ImmutableValue):
    source_id: str
    source_sha256: str
    parser_revision: str
    role: SourceRole


@dataclass(frozen=True)
class CompiledEnvironmentInputs(_ImmutableValue):
    agent_environments: tuple[AgentEnvironmentSpec, ...]
    evaluator_inputs: EvaluatorInputSet
    snapshot_digest: str
    public_provenance: tuple[PublicSourceProvenance, ...]


@dataclass(frozen=True)
class MappingProposal(_ImmutableValue):
    source_id: str
    mapping: FieldMapping


@dataclass(frozen=True)
class RelationshipProposal(_ImmutableValue):
    relationship: RecordRelationship
    matched_rows: tuple[tuple[int, int], ...]
    confidence: Literal["observed_exact_match"] = "observed_exact_match"


@dataclass(frozen=True)
class SourceProposals(_ImmutableValue):
    field_mappings: tuple[MappingProposal, ...] = ()
    relationships: tuple[RelationshipProposal, ...] = ()
    policy_rules: tuple[PolicyRuleProposal, ...] = ()


def prepare_environment_inputs(
    sources: tuple[EnvironmentSource, ...] | list[EnvironmentSource],
    *,
    destination_schema: DestinationSchema,
) -> EnvironmentInputDraft:
    if not isinstance(destination_schema, DestinationSchema):
        raise EnvironmentInputError(
            "source_mapping_invalid", "An approved destination schema is required"
        )
    source_items = tuple(sources)
    if len(source_items) > MAX_SOURCE_FILES:
        raise EnvironmentInputError(
            "source_too_large",
            f"An environment design accepts at most {MAX_SOURCE_FILES} files",
        )
    if sum(len(source.content) for source in source_items) > MAX_DESIGN_BYTES:
        raise EnvironmentInputError(
            "source_too_large",
            "The combined source size exceeds 200 MiB",
        )
    source_ids = [source.source_id for source in source_items]
    if len(source_ids) != len(set(source_ids)):
        raise EnvironmentInputError(
            "source_mapping_invalid", "source_id values must be unique"
        )

    parsed_sources: list[ParsedSource] = []
    diagnostics: list[SourceDiagnostic] = []
    for source in source_items:
        pin = SourcePin(
            source_id=source.source_id,
            source_sha256="sha256:" + hashlib.sha256(source.content).hexdigest(),
            parser_revision=PARSER_REVISION,
            role=source.role,
        )
        try:
            parsed = parse_environment_source(
                content=source.content,
                filename=source.filename,
                media_type=source.media_type,
                role=source.role,
            )
        except SourceParseError as exc:
            parsed_sources.append(
                FailedEnvironmentSource(pin=pin, filename=source.filename)
            )
            diagnostics.append(
                SourceDiagnostic(
                    code=exc.code,
                    severity="blocking",
                    message=str(exc),
                    span=SourceSpan(source.source_id),
                )
            )
            continue
        if isinstance(parsed, RawPolicyDocument):
            policy_source, policy_diagnostics = _prepare_policy_source(
                source, pin, parsed
            )
            parsed_sources.append(policy_source)
            diagnostics.extend(policy_diagnostics)
        else:
            record_source, record_diagnostics = _prepare_record_source(
                source,
                pin,
                parsed,
                destination_schema,
            )
            parsed_sources.append(record_source)
            diagnostics.extend(record_diagnostics)

    proposals = _propose_inputs(tuple(parsed_sources), destination_schema)
    draft_payload = {
        "sources": parsed_sources,
        "destination_schema": destination_schema,
        "diagnostics": diagnostics,
        "proposals": proposals,
    }
    return EnvironmentInputDraft(
        sources=tuple(parsed_sources),
        destination_schema=destination_schema,
        diagnostics=tuple(diagnostics),
        draft_digest=_content_digest(draft_payload),
        proposals=proposals,
    )


def review_environment_inputs(
    draft: EnvironmentInputDraft,
    *,
    review: EnvironmentInputReview,
    reviewer_id: str,
) -> ReviewedEnvironmentInputs:
    try:
        review = TypeAdapter(EnvironmentInputReview).validate_json(
            _canonical_json(review)
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise EnvironmentInputError(
            "source_mapping_invalid",
            "The source review contains invalid typed decisions",
        ) from exc
    if review.expected_draft_digest != draft.draft_digest:
        raise EnvironmentInputError(
            "source_review_stale",
            "The reviewed source preview has changed; review the latest draft",
        )
    if draft.draft_digest != _content_digest(
        {
            "sources": draft.sources,
            "destination_schema": draft.destination_schema,
            "diagnostics": draft.diagnostics,
            "proposals": draft.proposals,
        }
    ):
        raise EnvironmentInputError(
            "source_review_stale", "The source preview has changed"
        )
    if not reviewer_id.strip():
        raise ValueError("reviewer_id must be nonempty")
    blockers = tuple(item for item in draft.diagnostics if item.severity == "blocking")
    if blockers:
        raise EnvironmentInputError(
            "source_review_blocked",
            "Blocking source diagnostics must be resolved before approval",
            diagnostics=blockers,
        )

    parsed_records = {
        source.pin.source_id: source
        for source in draft.sources
        if isinstance(source, ParsedRecordSource)
    }
    parsed_policies = {
        source.pin.source_id: source
        for source in draft.sources
        if isinstance(source, ParsedPolicySource)
    }
    record_reviews = _index_reviews(review.record_reviews, "record")
    policy_reviews = _index_reviews(review.policy_reviews, "policy")
    if set(record_reviews) != set(parsed_records):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            "Every record source requires exactly one review",
        )
    if set(policy_reviews) != set(parsed_policies):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            "Every policy source requires exactly one review",
        )

    reviewed_records, conversion_diagnostics = _review_record_sources(
        parsed_records,
        record_reviews,
        draft.destination_schema,
    )
    _validate_relationships(reviewed_records, review.relationships)
    reviewed_records, receipt = _apply_synthetic_replacements(
        reviewed_records,
        record_reviews,
        review.relationships,
    )
    reviewed_policies = tuple(
        _review_policy_source(parsed_policies[source_id], policy_reviews[source_id])
        for source_id in sorted(parsed_policies)
    )
    _validate_policy_conflicts(reviewed_policies)
    reviewed_sources: tuple[ReviewedSource, ...] = (
        *reviewed_policies,
        *(reviewed_records[source_id] for source_id in sorted(reviewed_records)),
    )
    snapshot_diagnostics = (*draft.diagnostics, *conversion_diagnostics)
    snapshot_digest = _reviewed_snapshot_digest(
        reviewed_sources, review.relationships, snapshot_diagnostics
    )
    return ReviewedEnvironmentInputs(
        snapshot_id=f"source_snapshot_{snapshot_digest.removeprefix('sha256:')[:16]}",
        snapshot_digest=snapshot_digest,
        reviewer_id=reviewer_id,
        reviewed_at=datetime.now(timezone.utc),
        sources=reviewed_sources,
        relationships=review.relationships,
        private_replacement_receipt=receipt,
        diagnostics=snapshot_diagnostics,
    )


def compile_environment_inputs(
    reviewed: ReviewedEnvironmentInputs,
    *,
    existing_environments: tuple[AgentEnvironmentSpec, ...] = (),
) -> CompiledEnvironmentInputs:
    _assert_reviewed_snapshot(reviewed)
    visible_policies = tuple(
        source
        for source in reviewed.sources
        if isinstance(source, ReviewedPolicySource)
        and source.visibility == "agent_visible"
    )
    visible_records = tuple(
        source
        for source in reviewed.sources
        if isinstance(source, ReviewedRecordSource)
        and source.visibility == "agent_visible"
    )
    hidden_policies = tuple(
        source
        for source in reviewed.sources
        if isinstance(source, ReviewedPolicySource)
        and source.visibility == "evaluator_only"
    )
    hidden_records = tuple(
        source
        for source in reviewed.sources
        if isinstance(source, ReviewedRecordSource)
        and source.visibility == "evaluator_only"
    )

    environments: list[AgentEnvironmentSpec] = []
    if visible_policies:
        documents = []
        for source in visible_policies:
            for page in source.pages:
                documents.append(
                    {
                        "id": _public_source_id(reviewed, source.pin.source_id)
                        + f":page:{page.page}",
                        "title": f"Approved policy, page {page.page}",
                        "content": page.text,
                        "source": source.pin.source_id,
                        "version": reviewed.snapshot_digest,
                        "metadata": {"page": page.page, "role": "policy"},
                    }
                )
        environments.append(
            AgentEnvironmentSpec(
                type="retrieval_memory",
                data={"documents": documents, "memory": {}, "require_current": True},
            )
        )
    if visible_records:
        artifacts: dict[str, Any] = {}
        for source in visible_records:
            for record in source.records:
                artifact_id = (
                    _public_source_id(reviewed, source.pin.source_id)
                    + ":"
                    + _content_digest(record.record_id)[7:23]
                )
                if artifact_id in artifacts:
                    raise EnvironmentInputError(
                        "source_mapping_invalid",
                        f"Duplicate compiled record identifier: {artifact_id}",
                    )
                artifacts[artifact_id] = {
                    "id": artifact_id,
                    "domain": "source_records",
                    "schema": source.pin.source_id,
                    "data": record.as_dict(),
                    "metadata": {
                        "source_id": source.pin.source_id,
                        "source_row": record.source_row,
                    },
                }
        environments.append(
            AgentEnvironmentSpec(
                type="structured_artifacts",
                data={"artifacts": artifacts, "default_domain": "source_records"},
            )
        )

    _validate_environment_collisions((*existing_environments, *environments))
    rules = tuple(
        rule
        for source in reviewed.sources
        if isinstance(source, ReviewedPolicySource)
        for rule in source.active_rules
    )
    public_sources = tuple(
        source for source in reviewed.sources if source.visibility == "agent_visible"
    )
    return CompiledEnvironmentInputs(
        agent_environments=(*existing_environments, *environments),
        evaluator_inputs=EvaluatorInputSet(
            policies=hidden_policies,
            records=hidden_records,
            rules=rules,
        ),
        snapshot_digest=reviewed.snapshot_digest,
        public_provenance=tuple(
            PublicSourceProvenance(
                source_id=source.pin.source_id,
                source_sha256=source.pin.source_sha256,
                parser_revision=source.pin.parser_revision,
                role=source.pin.role,
            )
            for source in public_sources
        ),
    )


def _prepare_policy_source(
    source: EnvironmentSource,
    pin: SourcePin,
    parsed: RawPolicyDocument,
) -> tuple[ParsedPolicySource, tuple[SourceDiagnostic, ...]]:
    pages = tuple(
        ParsedPolicyPage(page=item.page, text=item.text) for item in parsed.pages
    )
    diagnostics: list[SourceDiagnostic] = []
    statements: list[PolicyStatement] = []
    for page in pages:
        for index, match in enumerate(
            re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", page.text, re.DOTALL), start=1
        ):
            statements.append(
                PolicyStatement(
                    statement_id=f"{source.source_id}:page:{page.page}:statement:{index}",
                    text=match.group(0),
                    span=SourceSpan(
                        source_id=source.source_id,
                        page=page.page,
                        start=match.start(),
                        end=match.end(),
                    ),
                )
            )
        scan = scan_content(page.text)
        if scan["status"] == "flagged":
            diagnostics.append(
                SourceDiagnostic(
                    code="source_content_flagged",
                    severity="blocking",
                    message=f"Page {page.page} contains content-scan findings",
                    span=SourceSpan(source_id=source.source_id, page=page.page),
                )
            )
        if _EMAIL_RE.search(page.text) or _PHONE_RE.search(page.text):
            diagnostics.append(
                SourceDiagnostic(
                    code="source_privacy_unresolved",
                    severity="blocking",
                    message=(
                        f"Page {page.page} may contain personal data; "
                        "upload a sanitized policy file"
                    ),
                    span=SourceSpan(source_id=source.source_id, page=page.page),
                )
            )
    return (
        ParsedPolicySource(
            pin=pin,
            filename=source.filename,
            pages=pages,
            statements=tuple(statements),
        ),
        tuple(diagnostics),
    )


def _prepare_record_source(
    source: EnvironmentSource,
    pin: SourcePin,
    parsed: RawRecordDocument,
    destination_schema: DestinationSchema,
) -> tuple[ParsedRecordSource, tuple[SourceDiagnostic, ...]]:
    rows = tuple(
        ParsedRecordRow(
            source_row=source_row, values=tuple(zip(parsed.headers, values))
        )
        for source_row, values in zip(parsed.source_rows, parsed.rows)
    )
    diagnostics = tuple(
        SourceDiagnostic(
            code=issue.code,
            severity="warning"
            if source.filename.lower().endswith(".csv") and parsed.rows
            else "blocking",
            message=issue.message,
            span=SourceSpan(
                source_id=source.source_id,
                row=issue.row,
                field=issue.field,
            ),
        )
        for issue in parsed.issues
    )
    diagnostics += tuple(
        SourceDiagnostic(
            code="source_content_flagged",
            severity="blocking",
            message="Record contains content-scan findings",
            span=SourceSpan(
                source_id=source.source_id, row=row.source_row, field=field
            ),
        )
        for row in rows
        for field, value in row.values
        if isinstance(value, str) and scan_content(value)["status"] == "flagged"
    )
    if not rows and not diagnostics:
        diagnostics = (
            SourceDiagnostic(
                "source_parse_failed",
                "blocking",
                "The source contains no records",
                SourceSpan(source.source_id),
            ),
        )
    columns = zip(*parsed.rows) if parsed.rows else (() for _ in parsed.headers)
    field_profiles = tuple(
        _profile_field(header, list(values), destination_schema)
        for header, values in zip(parsed.headers, columns)
    )
    return (
        ParsedRecordSource(
            pin=pin,
            filename=source.filename,
            rows=rows,
            fields=field_profiles,
            rejected_records=tuple(
                RejectedRecord(source.source_id, issue.row, (issue.message,))
                for issue in parsed.issues
                if issue.row is not None
            ),
        ),
        diagnostics,
    )


def _profile_field(
    field: str,
    values: list[JsonScalar],
    destination_schema: DestinationSchema,
) -> FieldProfile:
    populated = [value for value in values if value not in (None, "")]
    inferred_type = _infer_type(populated)
    unique_values = {_canonical_json(value) for value in populated}
    destination = destination_schema.field(field)
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
            visibility=review.visibility,
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
        missing = [
            record.as_dict().get(relationship.from_field)
            for record in from_source.records
            if record.as_dict().get(relationship.from_field) not in target_values
        ]
        if missing:
            raise EnvironmentInputError(
                "source_relationship_missing",
                (
                    f"{relationship.from_source_id}.{relationship.from_field} "
                    f"contains {len(missing)} value(s) absent from "
                    f"{relationship.to_source_id}.{relationship.to_field}"
                ),
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
                    if source.visibility == "agent_visible":
                        visible_original_values.add(value)
    for keys in group_keys.values():
        if not any(mapping_by_key[key].personal_data for key in keys):
            continue
        visible_keys = [
            key for key in keys if sources[key[0]].visibility == "agent_visible"
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
        # Public allocation must not depend on evaluator-only values.
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
            fields=source.fields,
            records=tuple(records),
            rejected_records=source.rejected_records,
            personal_data_mode=source.personal_data_mode,
        )
    _validate_relationships(replaced_sources, relationships)
    return replaced_sources, tuple(receipt)


def _review_policy_source(
    source: ParsedPolicySource,
    review: PolicyReview,
) -> ReviewedPolicySource:
    statements = {statement.statement_id: statement for statement in source.statements}
    rule_ids: set[str] = set()
    accepted_conflicts: dict[str, int] = defaultdict(int)
    for rule_review in review.rules:
        proposal = rule_review.proposal
        if proposal.rule_id in rule_ids:
            raise EnvironmentInputError(
                "source_mapping_invalid", "Policy rule ids must be unique"
            )
        rule_ids.add(proposal.rule_id)
        if proposal.statement_id not in statements:
            raise EnvironmentInputError(
                "source_mapping_invalid",
                f"Rule {proposal.rule_id} refers to an unknown policy statement",
            )
        _validate_rule(proposal, rule_review.decision)
        if rule_review.decision == "accepted":
            recognized = _propose_policy_rule(statements[proposal.statement_id])
            if (
                recognized.check_type is None
                or proposal.check_type != recognized.check_type
                or proposal.check_arguments != recognized.check_arguments
                or proposal.kind != recognized.kind
            ):
                raise EnvironmentInputError(
                    "source_rule_unsupported",
                    "Unsupported policy prose must remain reference-only",
                )
            if proposal.conflict_group:
                accepted_conflicts[proposal.conflict_group] += 1
    if {item.proposal.statement_id for item in review.rules} != set(statements):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            "Every policy proposal requires an explicit review decision",
        )
    if any(count > 1 for count in accepted_conflicts.values()):
        raise EnvironmentInputError(
            "source_policy_conflict",
            "Only one rule in a conflict group can be accepted",
        )
    return ReviewedPolicySource(
        pin=source.pin,
        filename=source.filename,
        visibility=review.visibility,
        pages=source.pages,
        statements=source.statements,
        rule_reviews=review.rules,
    )


def _validate_policy_conflicts(
    sources: tuple[ReviewedPolicySource, ...],
) -> None:
    accepted_conflicts: dict[str, int] = defaultdict(int)
    for source in sources:
        for rule in source.active_rules:
            if rule.conflict_group:
                accepted_conflicts[rule.conflict_group] += 1
    if any(count > 1 for count in accepted_conflicts.values()):
        raise EnvironmentInputError(
            "source_policy_conflict",
            "Only one rule in a conflict group can be accepted across all policy sources",
        )
    active = tuple(rule for source in sources for rule in source.active_rules)
    signatures = {
        (rule.check_type, _canonical_json(rule.check_arguments)) for rule in active
    }
    for rule in active:
        opposite = {"contains": "not_contains", "not_contains": "contains"}.get(
            rule.check_type
        )
        if opposite and (opposite, _canonical_json(rule.check_arguments)) in signatures:
            raise EnvironmentInputError(
                "source_policy_conflict",
                "Contradictory accepted checks across all policy sources",
            )


def _validate_rule(proposal: PolicyRuleProposal, decision: RuleDecision) -> None:
    if decision not in {"accepted", "rejected", "reference_only"}:
        raise EnvironmentInputError(
            "source_mapping_invalid", "Unknown policy rule decision"
        )
    if decision != "accepted":
        return
    try:
        validate_deterministic_assertion(
            {"type": proposal.check_type, **proposal.check_arguments}
        )
    except (ManifestError, TypeError) as exc:
        raise EnvironmentInputError(
            "source_rule_unsupported",
            f"Rule {proposal.rule_id} is not a registered check with deterministic arguments: {exc}",
        ) from exc


def _can_convert(value: JsonScalar, data_type: DataType) -> bool:
    try:
        _convert_value(value, data_type)
        return True
    except ValueError:
        return False


def _propose_inputs(
    sources: tuple[ParsedSource, ...], schema: DestinationSchema
) -> SourceProposals:
    records = tuple(
        source for source in sources if isinstance(source, ParsedRecordSource)
    )
    row_values = {
        source.pin.source_id: tuple(
            (row.source_row, row.as_dict()) for row in source.rows
        )
        for source in records
    }
    mappings = tuple(
        MappingProposal(
            source.pin.source_id,
            FieldMapping(
                profile.source_field,
                profile.suggested_target_field,
                schema.field(profile.suggested_target_field).data_type,
                profile.suggested_stable_id,
                profile.personal_data_suspected,
            ),
        )
        for source in records
        for profile in source.fields
        if profile.suggested_target_field is not None
    )
    relationships: list[RelationshipProposal] = []
    target_indexes: dict[tuple[str, str], dict[JsonScalar, int]] = {}
    for source in records:
        for profile in source.fields:
            if not (
                profile.source_field.lower().endswith("_id")
                or profile.source_field.lower() == "id"
            ):
                continue
            values = {
                values[profile.source_field]
                for _, values in row_values[source.pin.source_id]
            }
            if not values or None in values or "" in values:
                continue
            candidate: (
                tuple[ParsedRecordSource, FieldProfile, dict[JsonScalar, int]] | None
            ) = None
            ambiguous = False
            for target in records:
                if target is source:
                    continue
                for target_profile in target.fields:
                    if (
                        not target_profile.suggested_stable_id
                        or target_profile.inferred_type != profile.inferred_type
                    ):
                        continue
                    target_key = (
                        target.pin.source_id,
                        target_profile.source_field,
                    )
                    target_rows = target_indexes.get(target_key)
                    if target_rows is None:
                        target_rows = {
                            values[target_profile.source_field]: source_row
                            for source_row, values in row_values[target.pin.source_id]
                        }
                        target_indexes[target_key] = target_rows
                    if values <= target_rows.keys():
                        if candidate is not None:
                            ambiguous = True
                            break
                        candidate = (target, target_profile, target_rows)
                if ambiguous:
                    break
            if candidate is not None and not ambiguous:
                target, target_profile, target_rows = candidate
                relationships.append(
                    RelationshipProposal(
                        RecordRelationship(
                            source.pin.source_id,
                            profile.source_field,
                            target.pin.source_id,
                            target_profile.source_field,
                        ),
                        tuple(
                            (
                                source_row,
                                target_rows[values[profile.source_field]],
                            )
                            for source_row, values in row_values[source.pin.source_id]
                        ),
                    )
                )
    rules = [
        _propose_policy_rule(statement)
        for source in sources
        if isinstance(source, ParsedPolicySource)
        for statement in source.statements
    ]
    opposing = {(rule.check_type, rule.check_arguments.get("value")) for rule in rules}
    from dataclasses import replace

    rules = [
        replace(
            rule,
            status="conflict",
            conflict_group=_content_digest(rule.check_arguments)[7:23],
        )
        if (
            ("not_contains" if rule.check_type == "contains" else "contains"),
            rule.check_arguments.get("value"),
        )
        in opposing
        else rule
        for rule in rules
    ]
    return SourceProposals(mappings, tuple(relationships), tuple(rules))


def _propose_policy_rule(statement: PolicyStatement) -> PolicyRuleProposal:
    match = re.fullmatch(
        r"""(?:the )?(?:response|[A-Za-z]+ responses) must (not )?contain (?:(['"])(.+?)\2|([A-Za-z][\w-]*))\.?""",
        statement.text.strip(),
        re.IGNORECASE,
    )
    return PolicyRuleProposal(
        rule_id=statement.statement_id + ":rule",
        statement_id=statement.statement_id,
        kind="forbidden_behavior" if match and match[1] else "required_behavior",
        check_type=("not_contains" if match[1] else "contains") if match else None,
        check_arguments={"value": match[3] or match[4]} if match else {},
        status="supported" if match else "unsupported",
    )


def _public_source_id(reviewed: ReviewedEnvironmentInputs, source_id: str) -> str:
    return "source_" + _content_digest((reviewed.snapshot_digest, source_id))[7:31]


def _environment_entry_ids(entries: object, field: str) -> list[str]:
    if isinstance(entries, Mapping):
        identifiers = list(entries)
    elif isinstance(entries, Sequence) and not isinstance(entries, (str, bytes)):
        if any(not isinstance(entry, Mapping) for entry in entries):
            raise EnvironmentInputError(
                "source_mapping_invalid", f"Environment {field} entries must be objects"
            )
        identifiers = [entry.get("id") for entry in entries]
    else:
        raise EnvironmentInputError(
            "source_mapping_invalid",
            f"Environment {field} must be a mapping or sequence",
        )
    if any(
        not isinstance(identifier, str) or not identifier.strip()
        for identifier in identifiers
    ):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            f"Environment {field} identifiers must be nonempty strings",
        )
    return identifiers


def _validate_environment_collisions(
    environments: tuple[AgentEnvironmentSpec, ...],
) -> None:
    adapters: set[str] = set()
    identifiers: set[str] = set()
    tools: set[str] = set()
    adapter_tools = {
        "retrieval_memory": {
            "search_knowledge_base",
            "query_knowledge",
            "retrieve_documents",
            "read_document",
            "retrieve_memory",
            "write_memory",
            "cite_sources",
            "record_attribution",
            "retrieval_memory_status",
        },
        "structured_artifacts": {
            "list_structured_artifacts",
            "inspect_structured_artifact",
        },
    }
    for environment in environments:
        adapter = environment.type.lower().replace("-", "_")
        if adapter == "structured_artifact":
            adapter = "structured_artifacts"
        owned = set(adapter_tools.get(adapter, ()))
        if adapter in {
            "retrieval_hook",
            "retrieval_hooks",
            "http_retrieval_hook",
            "http_rag_hook",
        }:
            owned.add(
                environment.data.get("tool_name")
                or environment.data.get("tool")
                or "retrieve_documents"
            )
        declarations = environment.data.get("tools", ())
        declared_names = [
            tool.get("name") or tool.get("function", {}).get("name")
            for tool in declarations
        ]
        if None in declared_names or len(set(declared_names)) != len(declared_names):
            raise EnvironmentInputError(
                "source_mapping_invalid",
                "Environment tool declarations must have unique names",
            )
        owned.update(declared_names)
        if environment.data.get("tool_name"):
            owned.add(environment.data["tool_name"])
        document_ids = _environment_entry_ids(
            environment.data.get("documents", ()), "documents"
        )
        artifact_ids = _environment_entry_ids(
            environment.data.get("artifacts", {}), "artifacts"
        )
        new_ids = document_ids + artifact_ids
        if (
            adapter in adapters
            or tools & owned
            or len(set(new_ids)) != len(new_ids)
            or identifiers.intersection(new_ids)
        ):
            raise EnvironmentInputError(
                "source_mapping_invalid",
                "Environment adapter, tool or identifier collision",
            )
        adapters.add(adapter)
        identifiers.update(new_ids)
        tools.update(owned)


SNAPSHOT_SCHEMA_VERSION = "environment-inputs.snapshot.v1"
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024


def serialize_environment_snapshot(reviewed: ReviewedEnvironmentInputs) -> bytes:
    """Return a canonical private snapshot for caller-owned trusted storage."""
    _assert_reviewed_snapshot(reviewed)
    data = _canonical_value(reviewed)
    payload = _canonical_json(
        {
            "version": SNAPSHOT_SCHEMA_VERSION,
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
    from fi.alk.studio._environment_input_parsers import (
        _reject_json_constant,
        _unique_object,
    )

    try:
        if not isinstance(payload, bytes) or len(payload) > MAX_SNAPSHOT_BYTES:
            raise ValueError("Snapshot exceeds the size limit or is not bytes")
        envelope = json.loads(
            payload,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
        if (
            set(envelope) != {"version", "data", "digest"}
            or envelope["version"] != SNAPSHOT_SCHEMA_VERSION
        ):
            raise ValueError("Unknown snapshot schema")
        if _content_digest(envelope["data"]) != envelope["digest"]:
            raise ValueError("Snapshot envelope digest mismatch")
        if any(
            "personal_data_mode" not in source
            for source in envelope["data"]["sources"]
            if "records" in source
        ):
            raise ValueError("Record snapshot is missing its privacy decision")
        reviewed = TypeAdapter(ReviewedEnvironmentInputs).validate_json(
            _canonical_json(envelope["data"])
        )
        _assert_reviewed_snapshot(reviewed)
        return reviewed
    except (ValueError, TypeError, KeyError, ValidationError, RecursionError) as exc:
        raise EnvironmentInputError(
            "source_snapshot_invalid", "The source snapshot is invalid or changed"
        ) from exc


def _index_reviews(items: tuple[Any, ...], label: str) -> dict[str, Any]:
    indexed = {item.source_id: item for item in items}
    if len(indexed) != len(items):
        raise EnvironmentInputError(
            "source_mapping_invalid",
            f"Each {label} source may have only one review",
        )
    return indexed


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
        except (TypeError, ValueError) as exc:
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


def _content_digest(value: Any) -> str:
    return (
        "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    )


def _reviewed_snapshot_digest(
    sources: tuple[ReviewedSource, ...],
    relationships: tuple[RecordRelationship, ...],
    diagnostics: tuple[SourceDiagnostic, ...] = (),
) -> str:
    return _content_digest(
        {"sources": sources, "relationships": relationships, "diagnostics": diagnostics}
    )


def _assert_reviewed_snapshot(reviewed: ReviewedEnvironmentInputs) -> None:
    digest = _reviewed_snapshot_digest(
        reviewed.sources, reviewed.relationships, reviewed.diagnostics
    )
    expected_id = f"source_snapshot_{digest.removeprefix('sha256:')[:16]}"
    if reviewed.snapshot_digest != digest or reviewed.snapshot_id != expected_id:
        raise EnvironmentInputError(
            "source_snapshot_invalid",
            "The approved source snapshot has changed and must be reviewed again",
        )


def _canonical_json(value: Any) -> str:
    return json.dumps(
        _canonical_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _canonical_value(value: Any) -> Any:
    if is_dataclass(value):
        return {
            field.name: _canonical_value(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(key): _canonical_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return "sha256:" + hashlib.sha256(value).hexdigest()
    return value


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


__all__ = [
    "AgentEnvironmentSpec",
    "CompiledEnvironmentInputs",
    "DestinationField",
    "DestinationSchema",
    "EnvironmentInputDraft",
    "EnvironmentInputError",
    "EnvironmentInputReview",
    "EnvironmentSource",
    "EvaluatorInputSet",
    "FieldMapping",
    "FieldProfile",
    "MappingProposal",
    "RelationshipProposal",
    "SourceProposals",
    "ParsedPolicySource",
    "ParsedRecordSource",
    "PolicyReview",
    "PolicyRuleProposal",
    "PolicyRuleReview",
    "PublicSourceProvenance",
    "RecordRelationship",
    "RecordReview",
    "ReviewedEnvironmentInputs",
    "SourceDiagnostic",
    "SourcePin",
    "SourceSpan",
    "SUPPORTED_CHECK_TYPES",
    "compile_environment_inputs",
    "prepare_environment_inputs",
    "review_environment_inputs",
    "serialize_environment_snapshot",
    "deserialize_environment_snapshot",
]
