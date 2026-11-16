from __future__ import annotations

from fi.alk.studio._environment_input_records import (
    _EMAIL_RE,
    _PHONE_RE,
    _apply_synthetic_replacements,
    _profile_field,
    _review_record_sources,
    _validate_relationships,
)
from fi.alk.studio._environment_input_types import (
    AgentEnvironmentSpec,
    CompiledEnvironmentInputs,
    DestinationField,
    DestinationSchema,
    EnvironmentInputDraft,
    EnvironmentInputError,
    EnvironmentInputReview,
    EnvironmentSource,
    EvaluatorInputSet,
    FailedEnvironmentSource,
    FieldMapping,
    FieldProfile,
    JsonScalar,
    MappingProposal,
    ParsedPolicyPage,
    ParsedPolicySource,
    ParsedRecordRow,
    ParsedRecordSource,
    ParsedSource,
    PolicyStatement,
    PolicyReview,
    PolicyRuleProposal,
    PolicyRuleReview,
    PublicSourceProvenance,
    RecordRelationship,
    RecordReview,
    RejectedRecord,
    RelationshipProposal,
    ReviewedEnvironmentInputs,
    ReviewedPolicySource,
    ReviewedRecordSource,
    ReviewedSource,
    RuleDecision,
    SUPPORTED_CHECK_TYPES,
    SourceDiagnostic,
    SourcePin,
    SourceProposals,
    SourceSpan,
    _canonical_json,
    _canonical_value,
    _content_digest,
    _environment_entry_ids,
    _environment_tool_names,
)

import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from pydantic import TypeAdapter, ValidationError

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
from fi.simulate.suite import validate_deterministic_assertion


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
            owned.update(
                adapter_tools["retrieval_memory"] - {"retrieve_memory", "write_memory"}
            )
            owned.add(
                environment.data.get("tool_name")
                or environment.data.get("tool")
                or "retrieve_documents"
            )
        declared_names = _environment_tool_names(environment.data.get("tools", ()))
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
    "ParsedRecordRow",
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
