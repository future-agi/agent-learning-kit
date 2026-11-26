from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk.studio import (
    DestinationField,
    DestinationSchema,
    EnvironmentInputReview,
    EnvironmentRequirements,
    EnvironmentSource,
    FieldMapping,
    PolicyReview,
    PolicyRuleProposal,
    PolicyRuleReview,
    RecordRelationship,
    RecordReview,
    SimulationSchemaRepair,
    SourceRecordReference,
    compile_environment_inputs,
    generate_source_scenarios,
    prepare_environment_inputs,
    review_environment_inputs,
)
from fi.alk.studio._environment_inputs import ParsedPolicySource

from test_environment_inputs import _pdf


AGENTS = (
    ("OpenAI Agents SDK", "openai_agents_agent.py", "from agents import Agent"),
    ("Pydantic AI", "pydantic_ai_agent.py", "from pydantic_ai import Agent"),
    ("Google ADK", "google_adk_agent.py", "from google.adk.agents import Agent"),
)


def _reviewed_order_knowledge(eta: str = "Tuesday", *, infer_schema: bool = False):
    sources = (
        EnvironmentSource(
            source_id="public-policy",
            filename="order-status-policy.pdf",
            media_type="application/pdf",
            content=_pdf(
                'Order responses must contain shipped.\n\nThe response must not contain "cancelled".'
            ),
            role="policy",
        ),
        EnvironmentSource(
            source_id="hidden-policy",
            filename="order-status-grader.pdf",
            media_type="application/pdf",
            content=_pdf("ORDER_GRADER_SECRET_9X is evaluator only."),
            role="policy",
        ),
        EnvironmentSource(
            source_id="customers",
            filename="customers.csv",
            media_type="text/csv",
            content=b"customer_id,name\nC17,Alice\n",
            role="seed_records",
        ),
        EnvironmentSource(
            source_id="orders",
            filename="orders.json",
            media_type="application/json",
            content=json.dumps(
                [{"order_id": "4821", "buyer_id": "C17", "status": "shipped", "eta": eta}]
            ).encode(),
            role="seed_records",
        ),
    )
    schema = DestinationSchema(
        fields=(
            DestinationField("order_id", "string"),
            DestinationField("customer_id", "string"),
            DestinationField("name", "string"),
            DestinationField("status", "string"),
            DestinationField("eta", "string"),
        )
    )
    if infer_schema:
        sources = (sources[0], sources[1], sources[3])
    draft = prepare_environment_inputs(
        sources, destination_schema=None if infer_schema else schema,
        schema_repairs=(SimulationSchemaRepair("orders", identity_fields=("order_id",)),)
        if infer_schema else (),
    )
    public_policy = next(
        source
        for source in draft.sources
        if isinstance(source, ParsedPolicySource)
        and source.pin.source_id == "public-policy"
    )
    success = PolicyRuleProposal(
        "status-shipped",
        public_policy.statements[0].statement_id,
        "required_behavior",
        "contains",
        {"value": "shipped"},
    )
    refusal = PolicyRuleProposal(
        "status-not-cancelled",
        public_policy.statements[1].statement_id,
        "forbidden_behavior",
        "not_contains",
        {"value": "cancelled"},
    )
    review = EnvironmentInputReview(
        expected_draft_digest=draft.draft_digest,
        record_reviews=(
            RecordReview(
                "orders", "agent_visible", "synthetic_replace",
                tuple(item.mapping for item in draft.proposals.field_mappings),
            ),
        ) if infer_schema else (
            RecordReview(
                "customers",
                "agent_visible",
                "synthetic_replace",
                (
                    FieldMapping(
                        "customer_id",
                        "customer_id",
                        "string",
                        is_stable_id=True,
                        personal_data=True,
                    ),
                    FieldMapping("name", "name", "string", personal_data=True),
                ),
            ),
            RecordReview(
                "orders",
                "agent_visible",
                "synthetic_replace",
                (
                    FieldMapping("order_id", "order_id", "string", is_stable_id=True),
                    FieldMapping(
                        "buyer_id", "customer_id", "string", personal_data=True
                    ),
                    FieldMapping("status", "status", "string"),
                    FieldMapping("eta", "eta", "string"),
                ),
            ),
        ),
        policy_reviews=(
            PolicyReview(
                "public-policy",
                "agent_visible",
                (
                    PolicyRuleReview(success, "accepted"),
                    PolicyRuleReview(refusal, "accepted"),
                ),
            ),
            PolicyReview(
                "hidden-policy",
                "evaluator_only",
                tuple(
                    PolicyRuleReview(proposal, "reference_only")
                    for proposal in draft.proposals.policy_rules
                    if proposal.statement_id.startswith("hidden-policy:")
                ),
            ),
        ),
        relationships=() if infer_schema else (
            RecordRelationship("orders", "customer_id", "customers", "customer_id"),
        ),
    )
    return review_environment_inputs(draft, review=review, reviewer_id="test-reviewer")


@pytest.mark.parametrize(("framework", "fixture", "sdk_import"), AGENTS)
def test_genuine_order_support_agents_receive_grounded_source_scenarios(
    framework: str,
    fixture: str,
    sdk_import: str,
) -> None:
    agent_source = (
        Path(__file__).parents[1] / "examples" / "frameworks" / fixture
    ).read_text()
    assert sdk_import in agent_source
    assert "def run_agent(" in agent_source
    assert "def order_status(" in agent_source
    assert "Order 4821: shipped, arriving Tuesday." in agent_source

    reviewed = _reviewed_order_knowledge()
    compiled = compile_environment_inputs(reviewed)
    order = SourceRecordReference("orders", "4821")
    generated = generate_source_scenarios(
        reviewed,
        environment_requirements=EnvironmentRequirements(
            task=f"Test the {framework} order-support agent using approved order knowledge",
            declared_tools=("order_status",),
            required_records=(order, order, order),
        ),
        requested_count=3,
    )

    assert [draft.category for draft in generated.drafts] == [
        "success",
        "refusal",
        "negative_control",
    ]
    assert all(draft.status == "reviewable" for draft in generated.drafts)
    assert all(framework in draft.scenario.description for draft in generated.drafts)
    assert all(
        "order_status" in draft.scenario.constraints.declared_tools
        for draft in generated.drafts
    )
    assert all(
        {reference.source_id for reference in draft.referenced_records}
        == {"customers", "orders"}
        for draft in generated.drafts
    )
    assert all(
        {span.source_id for span in draft.source_spans}
        == {"customers", "orders", "public-policy"}
        for draft in generated.drafts
    )
    assert all(
        draft.relationships == reviewed.relationships for draft in generated.drafts
    )
    assert all(
        draft.scenario.dataset[0].persona["citations"] for draft in generated.drafts
    )

    success = generated.drafts[0]
    assert {fact.value for fact in success.starting_facts} >= {
        "4821",
        "synthetic_0001",
        "shipped",
        "Tuesday",
        "Person 0001",
    }
    negative_control = generated.drafts[2].evaluator
    assert negative_control.negative_control_calibrated
    assert negative_control.negative_control_output == "incorrect"
    public_payload = "".join(generated.scenario_payloads)
    assert "ORDER_GRADER_SECRET_9X" not in public_payload
    assert (
        "ORDER_GRADER_SECRET_9X" in compiled.evaluator_inputs.policies[0].pages[0].text
    )
