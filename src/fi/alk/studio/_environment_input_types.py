from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime
from types import MappingProxyType
from typing import Any, Literal, TypeAlias

from pydantic import ConfigDict

from fi.simulate.suite import DETERMINISTIC_ASSERTION_TYPES

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
        if "tools" in self.data:
            _environment_tool_names(self.data["tools"])
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


def _environment_tool_names(entries: object) -> list[str]:
    if not isinstance(entries, Sequence) or isinstance(entries, (str, bytes)):
        raise EnvironmentInputError(
            "source_mapping_invalid", "Environment tools must be a sequence"
        )
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            raise EnvironmentInputError(
                "source_mapping_invalid", "Environment tools must be objects"
            )
        function = entry.get("function")
        if function is not None and not isinstance(function, Mapping):
            raise EnvironmentInputError(
                "source_mapping_invalid", "Environment tool functions must be objects"
            )
        name = entry.get("name") or (
            function.get("name") if isinstance(function, Mapping) else None
        )
        if not isinstance(name, str) or not name.strip():
            raise EnvironmentInputError(
                "source_mapping_invalid",
                "Environment tool names must be nonempty strings",
            )
        names.append(name)
    if len(set(names)) != len(names):
        raise EnvironmentInputError(
            "source_mapping_invalid", "Environment tool names must be unique"
        )
    return names


def _content_digest(value: Any) -> str:
    return (
        "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
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


ParsedSource: TypeAlias = (
    ParsedRecordSource | ParsedPolicySource | FailedEnvironmentSource
)
ReviewedSource: TypeAlias = ReviewedRecordSource | ReviewedPolicySource
