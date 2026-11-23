from __future__ import annotations

import json
import asyncio
from dataclasses import replace

import pytest

from fi.alk.studio import (
    EnvironmentInputError,
    EnvironmentRequirements,
    ScenarioDiagnostic,
    SourceRecordReference,
    coverage_report,
    generate_source_scenarios,
)

from test_environment_inputs import (
    _approve_proposals,
    _policy_source,
    _record_source,
    _reviewed,
)


def test_generation_creates_reviewable_success_refusal_and_negative_drafts() -> None:
    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(
            task="Handle an eligible refund",
            declared_tools=("issue_refund",),
        ),
        requested_count=3,
    )

    assert [spec.category for spec in generated.evaluator_specs] == [
        "success",
        "refusal",
        "negative_control",
    ]
    assert [spec.expected_result for spec in generated.evaluator_specs] == [
        "unmeasured",
        "unmeasured",
        "unmeasured",
    ]
    assert not [item for item in generated.diagnostics if item.severity == "blocking"]
    assert all(scenario.verification is None for scenario in generated.public_scenarios)
    assert all(scenario.version for scenario in generated.public_scenarios)


def test_generation_does_not_leak_hidden_policy() -> None:
    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a refund"),
        requested_count=3,
    )

    assert "HIDDEN_SENTINEL_74Q" not in json.dumps(generated.scenario_payloads)
    assert generated.evaluator_specs[0].source_spans


def test_generation_is_deterministic_and_does_not_mutate_prior_drafts() -> None:
    _, reviewed = _reviewed()
    requirements = EnvironmentRequirements(task="Handle a refund")

    first = generate_source_scenarios(
        reviewed,
        environment_requirements=requirements,
        requested_count=3,
    )
    second = generate_source_scenarios(
        reviewed,
        environment_requirements=requirements,
        requested_count=3,
    )

    assert first.scenario_payloads == second.scenario_payloads
    assert first is not second


def test_missing_required_record_blocks_generation_without_losing_snapshot() -> None:
    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(
            task="Handle a refund",
            required_record_ids=("missing-customer",),
        ),
        requested_count=3,
    )

    assert generated.source_snapshot_id == reviewed.snapshot_id
    assert len(generated.drafts) == 3
    assert [draft.status for draft in generated.drafts] == [
        "blocked",
        "reviewable",
        "reviewable",
    ]
    assert [(item.code, item.severity) for item in generated.diagnostics] == [
        ("source_relationship_missing", "blocking")
    ]


def test_generation_rejects_a_modified_reviewed_snapshot() -> None:
    _, reviewed = _reviewed()
    record_source = reviewed.sources[-1]
    record = record_source.records[0]
    tampered_source = replace(
        record_source,
        records=(replace(record, values=(("status", "tampered"),)),),
    )
    tampered = replace(reviewed, sources=(*reviewed.sources[:-1], tampered_source))

    with pytest.raises(EnvironmentInputError, match="changed") as exc:
        generate_source_scenarios(
            tampered,
            environment_requirements=EnvironmentRequirements(task="Handle a refund"),
            requested_count=3,
        )
    assert exc.value.code == "source_snapshot_invalid"


def test_drafts_include_source_specific_facts_relationships_and_citations() -> None:
    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(
            task="Handle a refund",
            required_records=(SourceRecordReference("orders", "synthetic_0001"),),
        ),
        requested_count=3,
    )
    draft = generated.drafts[0]
    assert draft.relationships == reviewed.relationships
    assert {fact.record.source_id for fact in draft.starting_facts} == {
        "customers",
        "orders",
    }
    assert {fact.value for fact in draft.starting_facts} >= {
        "synthetic_0001",
        "eligible",
        "Person 0001",
    }
    assert {span.source_id for span in draft.source_spans} == {
        "orders",
        "customers",
        "public-policy",
    }
    assert "eligible" in draft.scenario_payload
    assert "Refund responses" in draft.scenario.description
    assert draft.scenario.dataset[0].persona["citations"]
    assert draft.scenario.goal is None
    edited = draft.scenario
    edited.dataset[0].persona["starting_facts"].clear()
    assert draft.scenario.dataset[0].persona["starting_facts"]


def test_negative_control_is_concrete_and_calibrated_by_real_evaluator() -> None:
    from fi.simulate.suite import evaluate_assertions

    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a refund"),
        requested_count=3,
    )
    draft = generated.drafts[2]
    spec = draft.evaluator
    assert spec.negative_control_calibrated
    assert spec.negative_control_output
    check = spec.checks[0]
    result = evaluate_assertions(
        spec.negative_control_output,
        [{"type": check.check_type, **check.check_arguments}],
    )
    assert not result["passed"] and not result["results"][0].get("error")
    assert spec.expected_result == "unmeasured"
    assert spec.negative_control_output in draft.scenario.dataset[0].situation


def test_local_runner_executes_generated_negative_control_and_measures_failure(
    tmp_path,
) -> None:
    from fi.alk import simulate
    from fi.simulate.suite import evaluate_assertions

    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a refund"),
        requested_count=3,
    )
    draft = generated.drafts[2]
    private_spec = draft.evaluator
    assert private_spec.negative_control_output
    assert private_spec.expected_result == "unmeasured"

    def target(agent_input):
        return simulate.AgentResponse(content=private_spec.negative_control_output)

    report = asyncio.run(
        simulate.SimulationRunner().run(
            simulate.SimulationSpec(
                run_id="source-negative-control",
                environment=simulate.EnvironmentSpec(
                    adapter="chat", world_kind="conversation", config={"max_turns": 1}
                ),
                target=simulate.AgentEndpointSpec(adapter="callable"),
                simulator=simulate.SimulatorPolicySpec(adapter="synthetic_user"),
                scenario=draft.scenario,
            ),
            target=target,
            result_sink=simulate.LocalFilesystemResultSink(tmp_path),
        )
    )
    assert report.status == simulate.RunStatus.COMPLETED
    assert report.failure is None
    measured = {}
    for case in report.test_cases:
        assert case.status == simulate.TestCaseStatus.COMPLETED
        assert case.result is not None
        observed = next(
            message["content"]
            for message in reversed(case.result.messages)
            if message["role"] == "assistant"
        )
        assert observed == private_spec.negative_control_output
        measured[case.test_case_id] = evaluate_assertions(
            observed,
            [
                {"type": check.check_type, **check.check_arguments}
                for check in private_spec.checks
            ],
        )
    assert measured
    assert set(measured) == {case.test_case_id for case in report.test_cases}
    assert all(result["passed"] is False for result in measured.values())
    assert all(
        result["results"][0]["passed"] is False
        and not result["results"][0].get("error")
        for result in measured.values()
    )
    assert private_spec.expected_result == "unmeasured"


def test_categories_depend_on_approved_public_material() -> None:
    _, reviewed = _approve_proposals(
        (_policy_source('The response must contain "approved".'),)
    )
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a request"),
        requested_count=3,
    )
    assert [draft.category for draft in generated.drafts] == [
        "success",
        "negative_control",
        "success",
    ]
    _, reference = _approve_proposals(
        (_policy_source("Explain the policy courteously."),)
    )
    generated = generate_source_scenarios(
        reference,
        environment_requirements=EnvironmentRequirements(task="Handle a request"),
        requested_count=3,
    )
    assert {draft.category for draft in generated.drafts} == {"success"}
    assert all(draft.status == "blocked" for draft in generated.drafts)


def test_hidden_sources_cannot_shape_public_content_or_diagnostics() -> None:
    sources = (
        _policy_source('The response must contain "approved".'),
        _record_source(b"id,status\na,ready\n"),
    )
    hidden = _policy_source(
        'The response must not contain "HIDDEN_SENTINEL_42".',
        source_id="hidden-source",
        filename="hidden-file.pdf",
    )
    _, public = _approve_proposals(sources)
    _, private = _approve_proposals((*sources, hidden), hidden=("hidden-source",))
    requirements = EnvironmentRequirements(task="Handle a request")
    first = generate_source_scenarios(
        public, environment_requirements=requirements, requested_count=3
    )
    second = generate_source_scenarios(
        private, environment_requirements=requirements, requested_count=3
    )
    assert [draft.category for draft in first.drafts] == [
        draft.category for draft in second.drafts
    ]
    for before, after in zip(first.drafts, second.drafts):
        assert before.scenario.description == after.scenario.description
        assert before.scenario.dataset == after.scenario.dataset
        assert before.starting_facts == after.starting_facts
        assert [(item.code, item.message) for item in before.diagnostics] == [
            (item.code, item.message) for item in after.diagnostics
        ]
        assert all(
            secret not in after.scenario_payload
            for secret in ("HIDDEN_SENTINEL_42", "hidden-source", "hidden-file.pdf")
        )
    assert any(
        rule.check_arguments.get("value") == "HIDDEN_SENTINEL_42"
        for rule in second.evaluator_specs[0].checks
    )


def test_missing_record_preserves_other_cases_and_typed_requested_reference() -> None:
    _, reviewed = _reviewed()
    missing = SourceRecordReference("customers", "not-approved")
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(
            task="Handle a refund",
            required_records=(
                missing,
                SourceRecordReference("customers", "synthetic_0001"),
            ),
        ),
        requested_count=3,
    )
    assert generated.drafts[0].referenced_records == (missing,)
    assert [draft.status for draft in generated.drafts] == [
        "blocked",
        "reviewable",
        "reviewable",
    ]
    assert generated.drafts[0].diagnostics[0].code == "source_relationship_missing"
    assert len(generated.public_scenarios) == len(generated.evaluator_specs) == 3
    report = coverage_report(generated)
    assert report["obligation_coverage"]["per_axis"]["source_cases"] == {
        "declared": 3,
        "covered": 2,
        "uncovered": [generated.drafts[0].scenario_id],
    }
    assert report["obligation_coverage"]["rate"] < 1.0
    assert report["residual_uncovered"]["rate"] > 0.0
    assert (
        coverage_report(generated.public_scenarios)["obligation_coverage"]["rate"]
        == 1.0
    )


def test_all_blocked_drafts_retain_uncovered_cases_without_tool_obligations() -> None:
    _, reviewed = _approve_proposals(
        (_policy_source("Explain the policy courteously."),)
    )
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a request"),
        requested_count=3,
    )
    assert all(draft.status == "blocked" for draft in generated.drafts)
    assert len(generated.public_scenarios) == len(generated.scenario_payloads) == 3
    report = coverage_report(generated, axes=())
    assert report["obligation_coverage"]["declared"] == 3
    assert report["obligation_coverage"]["covered"] == 0
    assert report["obligation_coverage"]["rate"] == 0.0
    assert report["residual_uncovered"]["rate"] == 1.0


def test_reviewable_draft_coverage_measures_readiness_and_preserves_projections() -> (
    None
):
    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a refund"),
        requested_count=3,
    )
    assert all(draft.status == "reviewable" for draft in generated.drafts)
    before = generated.scenario_payloads
    report = coverage_report(generated)
    assert report["obligation_coverage"]["per_axis"]["source_cases"]["covered"] == 3
    assert report["obligation_coverage"]["rate"] == 1.0
    assert generated.scenario_payloads == before
    assert [item.name for item in generated.public_scenarios] == [
        draft.scenario.name for draft in generated.drafts
    ]
    assert {item.expected_result for item in generated.evaluator_specs} == {
        "unmeasured"
    }


def test_private_blocking_changes_trusted_coverage_without_changing_public_payloads() -> (
    None
):
    _, reviewed = _reviewed()
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(
            task="Handle a refund", declared_tools=("issue_refund",)
        ),
        requested_count=3,
    )
    blocked = replace(
        generated,
        drafts=tuple(
            replace(
                draft,
                evaluator=replace(
                    draft.evaluator,
                    diagnostics=(
                        ScenarioDiagnostic(
                            "private_evidence_missing", "blocking", "Missing evidence"
                        ),
                    ),
                ),
            )
            for draft in generated.drafts
        ),
    )
    assert blocked.scenario_payloads == generated.scenario_payloads
    assert blocked.public_scenarios == generated.public_scenarios
    report = coverage_report(blocked)
    tools = report["obligation_coverage"]["per_axis"]["tool_obligations"]
    assert (
        tools["declared"]
        == coverage_report(generated)["obligation_coverage"]["per_axis"][
            "tool_obligations"
        ]["declared"]
    )
    assert tools["covered"] == 0
    assert "allow:issue_refund" in tools["uncovered"]
    assert report["obligation_coverage"]["rate"] == 0.0


def test_tools_and_evidence_are_validated_and_revisions_are_deterministic() -> None:
    _, reviewed = _reviewed()
    requirements = EnvironmentRequirements(
        task="Handle a refund", required_tools=("issue_refund",), evidence_paths=()
    )
    first = generate_source_scenarios(
        reviewed, environment_requirements=requirements, requested_count=3
    )
    assert all(draft.status == "blocked" for draft in first.drafts)
    assert any("tool" in item.message for item in first.diagnostics)
    assert any("evidence" in item.message for item in first.diagnostics)
    identical = generate_source_scenarios(
        reviewed, environment_requirements=requirements, requested_count=3
    )
    assert first.revision == identical.revision
    assert first.evaluator_revision == identical.evaluator_revision
    accepted_payload = first.drafts[0].scenario_payload
    changed = generate_source_scenarios(
        reviewed,
        environment_requirements=replace(
            requirements, declared_tools=("issue_refund",), evidence_paths=("response",)
        ),
        requested_count=3,
    )
    assert changed.revision != first.revision
    assert all(draft.status == "reviewable" for draft in changed.drafts)
    assert first.drafts[0].scenario_payload == accepted_payload


def test_hidden_record_does_not_satisfy_a_public_case_reference() -> None:
    _, reviewed = _approve_proposals(
        (
            _policy_source('The response must contain "approved".'),
            _record_source(b"id,status\na,ready\n", source_id="private-records"),
        ),
        hidden=("private-records",),
    )
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(
            task="Handle a request",
            required_records=(SourceRecordReference("private-records", "a"),),
        ),
        requested_count=2,
    )
    assert generated.drafts[0].status == "blocked"
    assert not generated.drafts[0].starting_facts
    assert "private-records" not in generated.scenario_payloads[0]
    assert generated.drafts[1].status == "reviewable"


def test_forbidden_only_material_supports_a_compliant_case() -> None:
    _, reviewed = _approve_proposals(
        (_policy_source('The response must not contain "forbidden".'),)
    )
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a request"),
        requested_count=3,
    )
    assert [draft.category for draft in generated.drafts] == [
        "success",
        "refusal",
        "negative_control",
    ]
    assert all(draft.status == "reviewable" for draft in generated.drafts)


def test_participant_only_policy_does_not_shape_tested_agent_scenarios() -> None:
    from fi.alk.studio import SourceAudience
    from test_environment_source_audiences import _approve

    reviewed = _approve(
        (
            _policy_source('The response must contain "approved".', source_id="public"),
            _policy_source(
                'The response must contain "PARTICIPANT_ONLY_SENTINEL".',
                source_id="participant",
            ),
        ),
        {
            "public": SourceAudience(True),
            "participant": SourceAudience(False, ("buyer",)),
        },
    )
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(task="Handle a request"),
        requested_count=2,
    )
    assert all(
        "PARTICIPANT_ONLY_SENTINEL" not in payload
        for payload in generated.scenario_payloads
    )
    assert not any(
        check.check_arguments.get("value") == "PARTICIPANT_ONLY_SENTINEL"
        for check in generated.evaluator_specs[0].checks
    )
