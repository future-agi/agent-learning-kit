from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from fi.alk.studio import (
    DestinationField,
    DestinationSchema,
    EnvironmentInputError,
    EnvironmentInputReview,
    EnvironmentSource,
    FieldMapping,
    PolicyReview,
    PolicyRuleReview,
    RecordRelationship,
    RecordReview,
    ReferenceReview,
    ReviewedReferenceSource,
    SourceAudience,
    compile_environment_inputs,
    deserialize_environment_snapshot,
    prepare_environment_inputs,
    review_environment_inputs,
    serialize_environment_snapshot,
)
from fi.alk.studio._environment_input_types import _canonical_value, _content_digest
from test_environment_inputs import _pdf

SCHEMA = DestinationSchema(
    (DestinationField("id", "string"), DestinationField("customer_id", "string"))
)


def _approve(sources, audiences, *, relationships=()):
    draft = prepare_environment_inputs(sources, destination_schema=SCHEMA)
    records, policies, references = [], [], []
    for source in draft.sources:
        source_id = source.pin.source_id
        audience = audiences[source_id]
        if source.pin.role == "reference":
            references.append(ReferenceReview(source_id, audience=audience))
        elif source.pin.role == "policy":
            policies.append(
                PolicyReview(
                    source_id,
                    rules=tuple(
                        PolicyRuleReview(
                            rule, "accepted" if rule.check_type else "reference_only"
                        )
                        for rule in draft.proposals.policy_rules
                        if rule.statement_id.startswith(source_id + ":")
                    ),
                    audience=audience,
                )
            )
        else:
            mappings = tuple(
                FieldMapping(
                    field.source_field,
                    field.source_field,
                    "string",
                    True,
                    field.personal_data_suspected,
                )
                for field in source.fields
            )
            records.append(
                RecordReview(
                    source_id,
                    personal_data_mode="synthetic_replace",
                    fields=mappings,
                    audience=audience,
                )
            )
    return review_environment_inputs(
        draft,
        review=EnvironmentInputReview(
            draft.draft_digest,
            tuple(records),
            tuple(policies),
            relationships,
            reference_reviews=tuple(references),
        ),
        reviewer_id="author",
    )


def _payload(environments):
    return json.dumps([environment.as_dict() for environment in environments])


@pytest.mark.parametrize(
    "filename,media_type,content",
    [
        (
            "reference.pdf",
            "application/pdf",
            _pdf('The response must contain "REFERENCE_SENTINEL".'),
        ),
        (
            "reference.txt",
            "text/plain; charset=utf-8",
            b'\xef\xbb\xbfThe response must contain "REFERENCE_SENTINEL".\r\n',
        ),
    ],
)
def test_reference_is_retrieval_only_with_spans(filename, media_type, content):
    source = EnvironmentSource("reference", filename, media_type, content, "reference")
    draft = prepare_environment_inputs((source,), destination_schema=SCHEMA)
    assert not draft.proposals.policy_rules
    reviewed = _approve((source,), {"reference": SourceAudience(True, ("buyer",))})
    assert isinstance(reviewed.sources[0], ReviewedReferenceSource)
    assert reviewed.sources[0].statements[0].span.page == 1
    compiled = compile_environment_inputs(reviewed)
    for projection in (
        compiled.agent_environments,
        compiled.participant_environments["buyer"],
    ):
        assert [environment.type for environment in projection] == ["retrieval_memory"]
        assert "REFERENCE_SENTINEL" in _payload(projection)
    assert not compiled.evaluator_inputs.rules
    assert not compiled.evaluator_inputs.records
    assert compiled.evaluator_inputs.references == reviewed.sources
    restored = deserialize_environment_snapshot(
        serialize_environment_snapshot(reviewed)
    )
    assert restored == reviewed


def test_purposes_and_audiences_project_independently_without_evaluator_leaks():
    sources = (
        EnvironmentSource(
            "policy",
            "policy.pdf",
            "application/pdf",
            _pdf('The response must contain "POLICY_AGENT".'),
            "policy",
        ),
        EnvironmentSource(
            "buyer",
            "buyer.pdf",
            "application/pdf",
            _pdf('The response must contain "BUYER_ONLY".'),
            "policy",
        ),
        EnvironmentSource(
            "seller", "seller.csv", "text/csv", b"id\nSELLER_ONLY\n", "seed_records"
        ),
        EnvironmentSource(
            "private", "private.txt", "text/plain", b"EVALUATOR_SENTINEL", "reference"
        ),
    )
    reviewed = _approve(
        sources,
        {
            "policy": SourceAudience(True),
            "buyer": SourceAudience(False, ("buyer",)),
            "seller": SourceAudience(False, ("seller",)),
            "private": SourceAudience(),
        },
    )
    compiled = compile_environment_inputs(reviewed)
    agent = _payload(compiled.agent_environments)
    buyer = _payload(compiled.participant_environments["buyer"])
    seller = _payload(compiled.participant_environments["seller"])
    assert (
        "POLICY_AGENT" in agent
        and "BUYER_ONLY" not in agent
        and "SELLER_ONLY" not in agent
    )
    assert (
        "BUYER_ONLY" in buyer
        and "POLICY_AGENT" not in buyer
        and "SELLER_ONLY" not in buyer
    )
    assert (
        "SELLER_ONLY" in seller
        and "POLICY_AGENT" not in seller
        and "BUYER_ONLY" not in seller
    )
    assert all("EVALUATOR_SENTINEL" not in value for value in (agent, buyer, seller))
    assert "EVALUATOR_SENTINEL" in json.dumps(
        _canonical_value(compiled.evaluator_inputs)
    )
    assert len(compiled.evaluator_inputs.policies) == 2
    assert len(compiled.evaluator_inputs.records) == 1
    assert [source.source_id for source in compiled.public_provenance] == ["policy"]
    with pytest.raises(TypeError):
        compiled.participant_environments["other"] = ()


@pytest.mark.parametrize(
    "participants",
    [("",), ("bad id",), ("../private",), ("a", "a"), "buyer", None, (1,)],
)
def test_invalid_participant_identifiers_fail_with_typed_error(participants):
    with pytest.raises(EnvironmentInputError) as exc:
        SourceAudience(False, participants)
    assert exc.value.code == "source_audience_invalid"


@pytest.mark.parametrize(
    "visibility,audience",
    [
        ("agent_visible", SourceAudience()),
        ("evaluator_only", SourceAudience(True)),
        ("evaluator_only", SourceAudience(False, ("buyer",))),
    ],
)
def test_conflicting_legacy_visibility_and_audience_fail(visibility, audience):
    source = EnvironmentSource(
        "reference", "reference.txt", "text/plain", b"Reference", "reference"
    )
    draft = prepare_environment_inputs((source,), destination_schema=SCHEMA)
    with pytest.raises(EnvironmentInputError) as exc:
        review_environment_inputs(
            draft,
            review=EnvironmentInputReview(
                draft.draft_digest,
                (),
                (),
                reference_reviews=(
                    ReferenceReview("reference", visibility, audience=audience),
                ),
            ),
            reviewer_id="author",
        )
    assert exc.value.code == "source_audience_invalid"


def test_synthetic_joins_are_identical_in_every_allowed_projection():
    sources = (
        EnvironmentSource(
            "customers",
            "customers.csv",
            "text/csv",
            b"customer_id\nC17\n",
            "seed_records",
        ),
        EnvironmentSource(
            "orders", "orders.csv", "text/csv", b"customer_id\nC17\n", "seed_records"
        ),
    )
    audience = SourceAudience(True, ("seller", "buyer"))
    reviewed = _approve(
        sources,
        {"customers": audience, "orders": audience},
        relationships=(
            RecordRelationship("orders", "customer_id", "customers", "customer_id"),
        ),
    )
    compiled = compile_environment_inputs(reviewed)
    for projection in (
        compiled.agent_environments,
        *compiled.participant_environments.values(),
    ):
        artifacts = projection[0].data["artifacts"].values()
        assert {artifact["data"]["customer_id"] for artifact in artifacts} == {
            "synthetic_0001"
        }
        assert "C17" not in _payload(projection)
    assert reviewed.private_replacement_receipt[0].original_value == "C17"


@pytest.mark.parametrize(
    "audience", [SourceAudience(True), SourceAudience(False, ("buyer",))]
)
def test_hidden_join_target_is_never_pulled_into_projection(audience):
    sources = tuple(
        EnvironmentSource(
            identifier, identifier + ".csv", "text/csv", b"id\njoined\n", "seed_records"
        )
        for identifier in ("origin", "target")
    )
    reviewed = _approve(
        sources,
        {"origin": audience, "target": SourceAudience()},
        relationships=(RecordRelationship("origin", "id", "target", "id"),),
    )
    with pytest.raises(EnvironmentInputError) as exc:
        compile_environment_inputs(reviewed)
    assert exc.value.code == "source_audience_invalid"
    assert exc.value.diagnostics[0].severity == "blocking"
    assert exc.value.diagnostics[0].span.source_id == "origin"


def test_snapshot_digest_binds_purpose_and_normalized_audience():
    pdf = _pdf('The response must contain "approved".')
    policy = EnvironmentSource(
        "document", "document.pdf", "application/pdf", pdf, "policy"
    )
    reference = replace(policy, role="reference")
    first = _approve((policy,), {"document": SourceAudience(True)})
    second = _approve((reference,), {"document": SourceAudience(True)})
    third = _approve((reference,), {"document": SourceAudience(False, ("buyer",))})
    assert (
        len({first.snapshot_digest, second.snapshot_digest, third.snapshot_digest}) == 3
    )
    assert SourceAudience(False, ("seller", "buyer")) == SourceAudience(
        False, ("buyer", "seller")
    )


def test_original_v1_snapshot_retains_digest_bytes_and_legacy_projection():
    payload = (
        Path(__file__).parent / "fixtures/environment_inputs/snapshot_v1.json"
    ).read_bytes()
    original = json.loads(payload)
    reviewed = deserialize_environment_snapshot(payload)
    assert reviewed.snapshot_digest == original["data"]["snapshot_digest"]
    assert serialize_environment_snapshot(reviewed) == payload
    compiled = compile_environment_inputs(reviewed)
    assert not compiled.participant_environments
    assert all(not source.audience.participant_ids for source in reviewed.sources)
    assert [source.pin.source_id for source in compiled.evaluator_inputs.policies] == [
        "hidden-policy"
    ]
    assert not compiled.evaluator_inputs.records
    assert "HIDDEN_SENTINEL_74Q" not in _payload(compiled.agent_environments)


@pytest.mark.parametrize("mutation", ["future", "legacy_audience", "missing_audience"])
def test_snapshot_schema_cannot_invent_or_drop_audience(mutation):
    path = Path(__file__).parent / "fixtures/environment_inputs/snapshot_v1.json"
    envelope = json.loads(path.read_bytes())
    if mutation == "future":
        envelope["version"] = "environment-inputs.snapshot.v99"
    elif mutation == "legacy_audience":
        envelope["data"]["sources"][0]["audience"] = {
            "tested_agent": False,
            "participant_ids": ["buyer"],
        }
    else:
        reviewed = _approve(
            (
                EnvironmentSource(
                    "ref", "ref.txt", "text/plain", b"Reference", "reference"
                ),
            ),
            {"ref": SourceAudience()},
        )
        envelope = json.loads(serialize_environment_snapshot(reviewed))
        envelope["data"]["sources"][0].pop("audience")
    envelope["digest"] = _content_digest(envelope["data"])
    with pytest.raises(EnvironmentInputError) as exc:
        deserialize_environment_snapshot(json.dumps(envelope).encode())
    assert exc.value.code == "source_snapshot_invalid"


def test_broken_relationship_reports_source_row_field_target_and_missing_key():
    sources = (
        EnvironmentSource(
            "origin", "origin.csv", "text/csv", b"id\nmissing-key\n", "seed_records"
        ),
        EnvironmentSource(
            "target", "target.csv", "text/csv", b"id\nexisting-key\n", "seed_records"
        ),
    )
    with pytest.raises(EnvironmentInputError) as exc:
        _approve(
            sources,
            {"origin": SourceAudience(True), "target": SourceAudience(True)},
            relationships=(RecordRelationship("origin", "id", "target", "id"),),
        )
    assert exc.value.code == "source_relationship_missing"
    diagnostic = exc.value.diagnostics[0]
    assert diagnostic.span.source_id == "origin"
    assert diagnostic.span.row == 2
    assert diagnostic.span.field == "id"
    assert "missing-key" in diagnostic.message
    assert "target.id" in diagnostic.message


@pytest.mark.parametrize(
    "role,filename,media_type,content",
    [
        ("reference", "reference.csv", "text/csv", b"id\na\n"),
        ("policy", "policy.txt", "text/plain", b"Policy"),
        ("seed_records", "records.txt", "text/plain", b"Record"),
        ("reference", "reference.txt", "text/plain", b"\xff"),
        ("reference", "reference.txt", "text/plain", b"\r\n "),
    ],
)
def test_purpose_and_format_mismatch_or_invalid_text_is_blocked(
    role, filename, media_type, content
):
    draft = prepare_environment_inputs(
        (EnvironmentSource("invalid", filename, media_type, content, role),),
        destination_schema=SCHEMA,
    )
    assert draft.diagnostics[0].severity == "blocking"
    assert draft.diagnostics[0].code in {
        "source_unsupported_type",
        "source_parse_failed",
        "source_no_extractable_text",
    }
