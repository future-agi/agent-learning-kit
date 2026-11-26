from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from typing import Any, Literal

from fi.alk.studio._environment_input_snapshots import _assert_reviewed_snapshot
from fi.alk.studio._environment_input_types import (
    EnvironmentInputError,
    JsonScalar,
    PolicyRuleProposal,
    PolicyStatement,
    RecordRelationship,
    ReviewedEnvironmentInputs,
    ReviewedPolicySource,
    ReviewedRecord,
    ReviewedRecordSource,
    ReviewedReferenceSource,
    SourceSpan,
    _canonical_json,
    _canonical_value,
    _content_digest,
    _ImmutableValue,
)
from fi.simulate.manifest import ManifestError
from fi.simulate.simulation.models import Persona, Scenario, ScenarioConstraints
from fi.simulate.suite import evaluate_assertions, validate_deterministic_assertion

ScenarioCategory = Literal["success", "refusal", "negative_control"]
_CATEGORY_PURPOSES = {
    "success": "Test a compliant outcome.",
    "refusal": "Test refusal of a disallowed action.",
    "negative_control": "Test that an incorrect outcome is rejected.",
}


@dataclass(frozen=True)
class SourceRecordReference(_ImmutableValue):
    source_id: str
    record_id: str


@dataclass(frozen=True)
class StartingFact(_ImmutableValue):
    record: SourceRecordReference
    field: str
    value: JsonScalar


@dataclass(frozen=True)
class EnvironmentRequirements(_ImmutableValue):
    task: str
    declared_tools: tuple[str, ...] = ()
    required_record_ids: tuple[str, ...] = ()
    required_records: tuple[SourceRecordReference, ...] = ()
    required_tools: tuple[str, ...] = ()
    evidence_paths: tuple[str, ...] = ("response",)


@dataclass(frozen=True)
class ScenarioDiagnostic(_ImmutableValue):
    code: str
    severity: Literal["warning", "blocking"]
    message: str
    scenario_id: str | None = None


@dataclass(frozen=True)
class EvaluatorScenarioSpec(_ImmutableValue):
    scenario_id: str
    category: ScenarioCategory
    expected_result: Literal["pass", "fail", "unmeasured"]
    checks: tuple[PolicyRuleProposal, ...]
    source_spans: tuple[SourceSpan, ...]
    negative_control_output: str | None = None
    negative_control_calibrated: bool = False
    diagnostics: tuple[ScenarioDiagnostic, ...] = ()


@dataclass(frozen=True)
class SourceScenarioDraft(_ImmutableValue):
    scenario_id: str
    category: ScenarioCategory
    scenario_payload: str
    evaluator: EvaluatorScenarioSpec
    referenced_records: tuple[SourceRecordReference, ...]
    starting_facts: tuple[StartingFact, ...]
    relationships: tuple[RecordRelationship, ...]
    source_spans: tuple[SourceSpan, ...]
    diagnostics: tuple[ScenarioDiagnostic, ...]

    @property
    def scenario(self) -> Scenario:
        return Scenario.model_validate_json(self.scenario_payload)

    @property
    def status(self) -> Literal["reviewable", "blocked"]:
        return (
            "blocked"
            if any(
                item.severity == "blocking"
                for item in (*self.diagnostics, *self.evaluator.diagnostics)
            )
            else "reviewable"
        )


@dataclass(frozen=True)
class GeneratedScenarioDraftSet(_ImmutableValue):
    source_snapshot_id: str
    source_snapshot_digest: str
    drafts: tuple[SourceScenarioDraft, ...]
    revision: str
    evaluator_revision: str

    @property
    def public_scenarios(self) -> tuple[Scenario, ...]:
        return tuple(draft.scenario for draft in self.drafts)

    @property
    def scenario_payloads(self) -> tuple[str, ...]:
        return tuple(draft.scenario_payload for draft in self.drafts)

    @property
    def evaluator_specs(self) -> tuple[EvaluatorScenarioSpec, ...]:
        return tuple(draft.evaluator for draft in self.drafts)

    @property
    def diagnostics(self) -> tuple[ScenarioDiagnostic, ...]:
        return tuple(item for draft in self.drafts for item in draft.diagnostics)


def _reviewable_scenarios(
    reviewed: ReviewedEnvironmentInputs,
    *,
    environment_requirements: EnvironmentRequirements,
    requested_count: int,
) -> GeneratedScenarioDraftSet:
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=environment_requirements,
        requested_count=requested_count,
    )
    blocked = sum(draft.status == "blocked" for draft in generated.drafts)
    if blocked:
        raise EnvironmentInputError(
            "source_scenarios_blocked",
            f"{blocked} source scenarios are blocked by validation.",
        )
    return generated


def _public_source_case(draft: SourceScenarioDraft) -> dict[str, Any]:
    persona = draft.scenario.dataset[0].persona
    return {
        "id": draft.scenario_id,
        "category": draft.category,
        "purpose": _CATEGORY_PURPOSES[draft.category],
        "starting_facts": persona["starting_facts"],
        "policy_reference": persona["policy_reference"],
        "citations": persona["citations"],
    }


def _distinct_source_drafts(generated: GeneratedScenarioDraftSet) -> tuple[SourceScenarioDraft, ...]:
    unique = {}
    for draft in generated.drafts:
        case = _public_source_case(draft)
        case.pop("id")
        unique.setdefault(_content_digest(case), draft)
    return tuple(unique.values())


def _sealed_source_case(case: dict[str, Any]) -> dict[str, Any]:
    return {**_canonical_value(case), "proof_digest": _content_digest(case)}


_LIST_ITEM = re.compile(r"^\d+[.)]\s+")
_DOCUMENT_LABEL = re.compile(
    r"(?:(?:policy|document)\s+)?(?:version|edition|revision)\b.*"
    r"|(?:training|draft|reference|sample) copy[.]?"
    r"|(?:these (?:rules|policies|examples)|this (?:document|policy|data)) "
    r"(?:are|is) (?:fictional|synthetic)(?: and (?:exist[s]?|(?:are|is) intended) "
    r"(?:only )?for (?:scenario[- ]generation |simulation |training )?(?:testing|purposes))?[.]?"
    r"|(?:for (?:testing|training|illustration|demonstration) (?:purposes )?only)[.]?",
    re.IGNORECASE,
)


def _document_label(line: str) -> bool:
    line = _LIST_ITEM.sub("", line.strip()).strip()
    if line.endswith(":") or _DOCUMENT_LABEL.fullmatch(line):
        return True
    # A short noun-phrase title is a label even outside a Markdown heading.
    # Preserve factual sentences and instructions that happen to reference a policy.
    title = line.lstrip("# ").strip()
    return bool(
        re.fullmatch(r"[\w '&/–-]+ (?:policy|rubric|manual|guidelines)", title, re.I)
        and len(title.split()) <= 12
        and not re.search(r"\b(?:is|are|must|may|shall|should|use|follow|apply|read|refer|see)\b", title, re.I)
    ) or (line.startswith("#") and not re.search(r"[.!?]$", title))


def _reference_passages(statements: tuple[PolicyStatement, ...]) -> tuple[PolicyStatement, ...]:
    """Keep rule and narrative passages with the locations of their original statements."""
    passages = []
    for statement in statements:
        for part in re.split(r"(?m)^\s*(?=\d+[.)]\s+)", statement.text):
            # Keep original paragraph wrapping, including multiple lines in a PDF clause.
            lines = [line for line in part.splitlines() if not _document_label(line)]
            text = "\n".join(lines).strip()
            if text:
                passages.append(replace(statement, text=text))
    return tuple(passages)


def generate_source_scenarios(
    reviewed: ReviewedEnvironmentInputs,
    *,
    environment_requirements: EnvironmentRequirements,
    requested_count: int,
) -> GeneratedScenarioDraftSet:
    """Compose inactive, independently validated cases from approved public facts."""
    _assert_reviewed_snapshot(reviewed)
    if (
        isinstance(requested_count, bool)
        or not isinstance(requested_count, int)
        or requested_count < 1
    ):
        raise ValueError("requested_count must be a positive integer")
    if not environment_requirements.task.strip():
        raise ValueError("environment_requirements.task must be nonempty")
    records = {
        SourceRecordReference(source.pin.source_id, record.record_id): (source, record)
        for source in reviewed.sources
        if isinstance(source, ReviewedRecordSource) and source.audience.tested_agent
        for record in source.records
    }
    public_policies = tuple(
        source
        for source in reviewed.sources
        if isinstance(source, ReviewedPolicySource) and source.audience.tested_agent
    )
    public_rules = tuple(
        rule for source in public_policies for rule in source.active_rules
    )
    references = []
    for source in reviewed.sources:
        if not source.audience.tested_agent:
            continue
        if isinstance(source, ReviewedReferenceSource):
            passages = source.statements
        elif isinstance(source, ReviewedPolicySource):
            reference_ids = {
                item.proposal.statement_id
                for item in source.rule_reviews
                if item.decision == "reference_only"
            }
            passages = tuple(item for item in source.statements if item.statement_id in reference_ids)
        else:
            continue
        references.extend(_reference_passages(passages))
    private_rules = tuple(
        rule
        for source in reviewed.sources
        if isinstance(source, ReviewedPolicySource)
        and not source.audience.tested_agent
        and not source.audience.participant_ids
        for rule in source.active_rules
    )
    statements = tuple(
        statement for source in public_policies for statement in source.statements
    )
    rule_spans = {
        statement.statement_id: statement.span
        for source in reviewed.sources
        if isinstance(source, ReviewedPolicySource)
        for statement in source.statements
    }
    categories: list[ScenarioCategory] = ["success"]
    if any(
        rule.kind in {"forbidden_behavior", "refusal", "eligibility"}
        for rule in public_rules
    ):
        categories.append("refusal")
    if any(_negative_output(rule) is not None for rule in public_rules):
        categories.append("negative_control")
    # Preserve the full rule cycle (including refusal and negative controls) and then
    # cover a reference passage in its own success case, without enforcing it as a rule.
    cycle_size = len(categories) + int(bool(public_rules and references))
    drafts: list[SourceScenarioDraft] = []
    for index in range(requested_count):
        cycle, slot = divmod(index, cycle_size)
        reference_case = slot == len(categories)
        category = "success" if reference_case else categories[slot]
        ref = _case_record(index, environment_requirements, records)
        source_record = records.get(ref) if ref else None
        selected_records = [(ref, source_record)] if ref and source_record else []
        relationships: list[RecordRelationship] = []
        relationship_missing = False
        if source_record:
            source, record = source_record
            for relationship in reviewed.relationships:
                if relationship.from_source_id != source.pin.source_id:
                    continue
                targets = [
                    (key, pair)
                    for key, pair in records.items()
                    if key.source_id == relationship.to_source_id
                    and pair[1].as_dict().get(relationship.to_field)
                    == record.as_dict().get(relationship.from_field)
                ]
                if len(targets) != 1:
                    relationship_missing = True
                else:
                    relationships.append(relationship)
                    if targets[0] not in selected_records:
                        selected_records.append(targets[0])
        facts = tuple(
            StartingFact(key, field, value)
            for key, (_, record) in selected_records
            for field, value in record.values
        )
        applicable = () if reference_case else _rules_for_category(public_rules, category)
        if applicable:
            applicable = (applicable[cycle % len(applicable)],)
        candidates = statements if applicable else references
        passage_index = cycle if reference_case else index
        excerpt = candidates[passage_index % len(candidates)] if candidates else None
        if applicable:
            excerpt = next(
                (
                    statement
                    for statement in statements
                    if statement.statement_id == applicable[0].statement_id
                ),
                excerpt,
            )
        control = (
            next(
                (
                    _negative_output(rule)
                    for rule in applicable
                    if _negative_output(rule) is not None
                ),
                None,
            )
            if category == "negative_control"
            else None
        )
        scenario_id = (
            "source-case-"
            + _content_digest(
                (
                    reviewed.snapshot_digest,
                    environment_requirements,
                    category,
                    index,
                    facts,
                )
            )[7:31]
        )
        diagnostics: list[ScenarioDiagnostic] = []

        def block(code: str, message: str) -> None:
            diagnostics.append(
                ScenarioDiagnostic(code, "blocking", message, scenario_id)
            )

        if ref is not None and source_record is None:
            block(
                "source_relationship_missing",
                "The case references a record absent from the public snapshot",
            )
        if relationship_missing:
            block(
                "source_relationship_missing",
                "A case relationship does not resolve to a visible record",
            )
        available_tools = set(environment_requirements.declared_tools)
        if records:
            available_tools.update(
                ("list_structured_artifacts", "inspect_structured_artifact")
            )
        if public_policies or references:
            available_tools.update(("search_knowledge_base", "read_document"))
        if set(environment_requirements.required_tools) - available_tools:
            block(
                "scenario_evidence_missing",
                "A required action or read tool is unavailable",
            )
        if not applicable and category != "success":
            block(
                "scenario_evidence_missing",
                "No accepted public check supports this case",
            )
        if any(
            not _evidence_available(rule, environment_requirements)
            for rule in applicable
        ):
            block(
                "scenario_evidence_missing",
                "A case check lacks observable evaluator evidence",
            )
        if category == "negative_control" and control is None:
            block(
                "scenario_evidence_missing",
                "A concrete failing control could not be calibrated",
            )
        if not facts and not excerpt:
            block(
                "scenario_evidence_missing",
                "No approved public source facts support this case",
            )
        cited_spans = tuple(
            SourceSpan(key.source_id, row=record.source_row)
            for key, (_, record) in selected_records
        )
        if excerpt:
            cited_spans += (excerpt.span,)
        situation = {
            "success": (
                "Ask for a compliant resolution using the quoted policy and starting facts."
                if applicable
                else "Ask for a resolution using the approved source evidence and starting facts."
            ),
            "refusal": "Ask for the prohibited or ineligible action described by the quoted policy.",
            "negative_control": "Use this deliberately incorrect candidate response "
            + json.dumps(control),
        }[category]
        persona = {
            "starting_facts": [
                {
                    "source_id": fact.record.source_id,
                    "record_id": fact.record.record_id,
                    "field": fact.field,
                    "value": fact.value,
                }
                for fact in facts
            ],
            "policy_reference": excerpt.text if excerpt else "",
            "citations": [
                {"source_id": span.source_id, "page": span.page, "row": span.row}
                for span in cited_spans
            ],
        }
        scenario = Scenario(
            name=scenario_id,
            kind="task",
            description=environment_requirements.task.strip()
            + " ["
            + category
            + "] "
            + (excerpt.text if excerpt else _canonical_json(persona["starting_facts"])),
            dataset=[
                Persona(
                    persona=persona,
                    situation=situation,
                    outcome="Resolve the request using approved source evidence.",
                )
            ],
            constraints=ScenarioConstraints(
                declared_tools=sorted(available_tools),
                observable_state={"source_facts": persona["starting_facts"]},
            ),
        )
        checks = (*applicable, *private_rules)
        evaluator = EvaluatorScenarioSpec(
            scenario_id,
            category,
            "unmeasured",
            checks,
            tuple(rule_spans[rule.statement_id] for rule in checks),
            control,
            control is not None,
            tuple(
                ScenarioDiagnostic(
                    "scenario_evidence_missing",
                    "blocking",
                    "An evaluator-only check lacks observable evidence",
                    scenario_id,
                )
                for rule in private_rules
                if not _evidence_available(rule, environment_requirements)
            ),
        )
        drafts.append(
            SourceScenarioDraft(
                scenario_id,
                category,
                _canonical_json(scenario.model_dump()),
                evaluator,
                tuple(key for key, _ in selected_records) or ((ref,) if ref else ()),
                facts,
                tuple(relationships),
                cited_spans,
                tuple(diagnostics),
            )
        )
    return GeneratedScenarioDraftSet(
        reviewed.snapshot_id,
        reviewed.snapshot_digest,
        tuple(drafts),
        _content_digest(
            (
                reviewed.snapshot_digest,
                environment_requirements,
                requested_count,
                [(draft.scenario_payload, draft.diagnostics) for draft in drafts],
            )
        ),
        _content_digest(
            (reviewed.snapshot_digest, [draft.evaluator for draft in drafts])
        ),
    )


def _case_record(
    index: int,
    requirements: EnvironmentRequirements,
    records: dict[SourceRecordReference, tuple[ReviewedRecordSource, ReviewedRecord]],
) -> SourceRecordReference | None:
    if index < len(requirements.required_records):
        return requirements.required_records[index]
    if index < len(requirements.required_record_ids):
        record_id = requirements.required_record_ids[index]
        matches = [key for key in records if key.record_id == record_id]
        return matches[0] if len(matches) == 1 else SourceRecordReference("", record_id)
    return tuple(records)[index % len(records)] if records else None


def _rules_for_category(
    rules: tuple[PolicyRuleProposal, ...], category: ScenarioCategory
) -> tuple[PolicyRuleProposal, ...]:
    if category == "refusal":
        return tuple(
            rule
            for rule in rules
            if rule.kind in {"forbidden_behavior", "refusal", "eligibility"}
        )
    if category == "success":
        return tuple(rule for rule in rules if rule.kind == "required_behavior") or tuple(
            rule
            for rule in rules
            if rule.kind in {"required_behavior", "forbidden_behavior", "eligibility"}
        )
    return rules[:1]


def _evidence_available(
    rule: PolicyRuleProposal, requirements: EnvironmentRequirements
) -> bool:
    path = rule.check_arguments.get("path", "response")
    return path in requirements.evidence_paths


def _negative_output(rule: PolicyRuleProposal) -> str | None:
    try:
        assertion = validate_deterministic_assertion(
            {"type": rule.check_type, **rule.check_arguments}
        )
        value = assertion.get("value")
        if rule.check_type in {"not_contains", "json_path_not_contains"}:
            wrong = value
        elif rule.check_type in {"contains", "json_path_contains"}:
            wrong = next(
                (
                    candidate
                    for candidate in ("incorrect", "denied", "x", "")
                    if value not in candidate
                ),
                "",
            )
        elif rule.check_type == "json_path_gte":
            wrong = value - 1
        elif rule.check_type == "json_path_lte":
            wrong = value + 1
        else:
            wrong = (
                "deliberately_incorrect"
                if value != "deliberately_incorrect"
                else "different"
            )
        if rule.check_type == "json_path_exists":
            candidate = "{}"
        elif "path" in assertion:
            tokens = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\d+", assertion["path"])
            for token in reversed(tokens):
                if token.isdigit() and int(token) > 1024:
                    return None
                wrong = (
                    [None] * int(token) + [wrong] if token.isdigit() else {token: wrong}
                )
            candidate = json.dumps(wrong, allow_nan=False)
        else:
            candidate = str(wrong)
        result = evaluate_assertions(candidate, [assertion])
        return (
            candidate
            if not result["passed"] and not result["results"][0].get("error")
            else None
        )
    except (ManifestError, ValueError, TypeError, OverflowError):
        return None


__all__ = [
    "EnvironmentRequirements",
    "EvaluatorScenarioSpec",
    "GeneratedScenarioDraftSet",
    "ScenarioDiagnostic",
    "SourceRecordReference",
    "SourceScenarioDraft",
    "StartingFact",
    "generate_source_scenarios",
]
