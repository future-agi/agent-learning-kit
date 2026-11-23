from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fi.alk.studio import environment_sources as transport


def _request(operation, **values):
    return json.dumps(
        {"version": transport.REQUEST_VERSION, "operation": operation, **values}
    ).encode()


def _prepare():
    return transport.execute_request(
        _request(
            "prepare",
            sources=[
                {
                    "source_id": "guide",
                    "filename": "guide.txt",
                    "media_type": "text/plain",
                    "role": "reference",
                    "content_base64": base64.b64encode(b"REFERENCE_SENTINEL").decode(),
                }
            ],
            destination_schema={
                "fields": [{"name": "id", "data_type": "string", "required": False}]
            },
        )
    )


def _csv(source_id, body):
    return {
        "source_id": source_id,
        "filename": source_id + ".csv",
        "media_type": "text/csv",
        "role": "seed_records",
        "content_base64": base64.b64encode(body.encode()).decode(),
    }


def _inferred(sources, **kwargs):
    response = transport.execute_request(_request("prepare", sources=sources, **kwargs))
    assert response["ok"], response
    return response["result"]


def _accept_proposed(draft):
    mappings = draft["proposals"]["field_mappings"]
    return transport.execute_request(
        _request(
            "accept",
            draft=draft,
            reviewer_id="author",
            review={
                "expected_draft_digest": draft["draft_digest"],
                "record_reviews": [
                    {
                        "source_id": s["pin"]["source_id"],
                        "visibility": "agent_visible",
                        "fields": [
                            m["mapping"]
                            for m in mappings
                            if m["source_id"] == s["pin"]["source_id"]
                        ],
                    }
                    for s in draft["sources"]
                    if s["pin"]["role"] == "seed_records"
                ],
                "policy_reviews": [
                    {
                        "source_id": s["pin"]["source_id"],
                        "visibility": "agent_visible",
                        "rules": [],
                    }
                    for s in draft["sources"]
                    if s["pin"]["role"] == "policy"
                ],
                "reference_reviews": [
                    {"source_id": s["pin"]["source_id"], "visibility": "agent_visible"}
                    for s in draft["sources"]
                    if s["pin"]["role"] == "reference"
                ],
            },
        )
    )


def test_inferred_reference_requires_review_without_record_fields():
    source = {
        "source_id": "terms",
        "filename": "terms.txt",
        "media_type": "text/plain",
        "role": "reference",
        "content_base64": base64.b64encode(b"Delivery takes four days.").decode(),
    }
    result = _inferred([source])
    draft = result["draft"]
    assert draft["destination_schema"]["fields"] == []
    assert not draft["proposals"]["field_mappings"]
    rejected = transport.execute_request(
        _request(
            "accept",
            draft=draft,
            reviewer_id="author",
            review={
                "expected_draft_digest": draft["draft_digest"],
                "record_reviews": [],
                "policy_reviews": [],
            },
        )
    )
    assert not rejected["ok"]
    approved = _accept_proposed(draft)
    assert approved["ok"], approved
    assert (
        approved["result"]["snapshot"]["data"]["simulation_schema_digest"]
        == result["schema_digest"]
    )


def test_inferred_csv_accept_round_trip_and_original_ids():
    sources = [
        _csv(
            "accounts",
            "id,amount,active,created\n001,1.5,true,2024-03-01\n002,2,false,2024-05-01\n",
        )
    ]
    result = _inferred(sources)
    assert result == _inferred(sources)
    draft = result["draft"]
    assert not draft["diagnostics"]
    assert {
        f["name"]: f["data_type"] for f in draft["destination_schema"]["fields"]
    } == {
        "accounts/id": "string",
        "accounts/amount": "number",
        "accounts/active": "boolean",
        "accounts/created": "date",
    }
    accepted = _accept_proposed(draft)
    assert accepted["ok"], accepted
    snap = accepted["result"]["snapshot"]
    assert snap["data"]["simulation_schema_digest"] == result["schema_digest"]
    assert [r["record_id"] for r in snap["data"]["sources"][0]["records"]] == [
        "001",
        "002",
    ]
    assert (
        transport.execute_request(_request("compile", snapshot=snap))["result"]
        == accepted["result"]
    )


def test_overlapping_entities_and_mixed_types_need_a_deterministic_repair():
    sources = [
        _csv("orders", "id,total\nA,1\nB,pending\n"),
        _csv("claims", "id,total\nC,3\nD,4\n"),
    ]
    first = _inferred(sources)
    draft = first["draft"]
    assert {i["code"] for i in draft["diagnostics"]} == {"source_schema_needs_repair"}
    assert {
        (i["source_id"], i["decision"])
        for i in draft["destination_schema"]["repair_choices"]
    } == {
        ("claims", "confirm_entity"),
        ("orders", "confirm_entity"),
        ("orders", "keep_text"),
    }
    assert _accept_proposed(draft)["error"]["code"] == "source_review_blocked"
    repairs = [
        {"source_id": "orders", "separate_entity": True, "text_fields": ["total"]},
        {"source_id": "claims", "separate_entity": True},
    ]
    repaired = _inferred(sources, schema_repairs=repairs)
    assert repaired == _inferred(sources, schema_repairs=repairs)
    assert repaired["draft"]["draft_digest"] != draft["draft_digest"]
    assert not repaired["draft"]["diagnostics"]
    approved = _accept_proposed(repaired["draft"])
    assert approved["ok"], approved
    rows = {
        s["pin"]["source_id"]: s["records"]
        for s in approved["result"]["snapshot"]["data"]["sources"]
    }
    assert dict(rows["orders"][0]["values"])["orders/total"] == "1"
    assert dict(rows["claims"][0]["values"])["claims/total"] == 3


def test_identity_repair_checks_the_complete_composite_key():
    sources = [_csv("seats", "row,column\nA,1\nA,2\nB,1\n")]

    def prepare(*identity):
        return _inferred(
            sources,
            schema_repairs=[{"source_id": "seats", "identity_fields": identity}],
        )["draft"]

    for identity in [(), ("row",)]:
        assert (
            prepare(*identity)["destination_schema"]["repair_choices"][0]["decision"]
            == "choose_identity"
        )
    assert _accept_proposed(prepare("row", "column"))["ok"]


def test_known_destination_keeps_authoritative_types_names_and_v2_format():
    schema = {"fields": [{"name": "id", "data_type": "string", "required": True}]}
    sources = [_csv("accounts", "id,value\n001,2\n")]
    draft = _inferred(sources, destination_schema=schema)["draft"]
    assert draft["destination_schema"] == schema
    assert [
        m["mapping"]["target_field"] for m in draft["proposals"]["field_mappings"]
    ] == ["id"]
    snapshot = _accept_proposed(draft)["result"]["snapshot"]
    assert snapshot["version"] == "environment-inputs.snapshot.v2"
    assert "simulation_schema_digest" not in snapshot["data"]
    bad = transport.execute_request(
        _request(
            "prepare",
            sources=sources,
            destination_schema=schema,
            schema_repairs=[{"source_id": "accounts", "identity_fields": ["id"]}],
        )
    )
    assert bad["error"]["code"] == "source_mapping_invalid"


def test_ambiguous_relationships_can_be_left_unlinked_by_explicit_choice():
    sources = [_csv(entity, "id\nA\n") for entity in ("tickets", "groups", "accounts")]
    repairs = [{"source_id": s["source_id"], "separate_entity": True} for s in sources]
    draft = _inferred(sources, schema_repairs=repairs)["draft"]
    assert {i["decision"] for i in draft["destination_schema"]["repair_choices"]} == {
        "review_relationships"
    }
    assert draft["destination_schema"]["repair_choices"][0]["candidate_targets"] == [
        "groups/id",
        "accounts/id",
    ]
    repaired = _inferred(
        sources, schema_repairs=[{**r, "manual_relationships": True} for r in repairs]
    )["draft"]
    assert not repaired["diagnostics"]
    assert not repaired["proposals"]["relationships"]
    assert _accept_proposed(repaired)["ok"]


def test_prepare_accept_compile_round_trip():
    prepared = _prepare()
    assert prepared["ok"]
    draft = prepared["result"]["draft"]
    assert draft["sources"][0]["pages"][0]["text"] == "REFERENCE_SENTINEL"
    assert not draft["proposals"]["policy_rules"]
    accepted = transport.execute_request(
        _request(
            "accept",
            draft=draft,
            review={
                "expected_draft_digest": draft["draft_digest"],
                "record_reviews": [],
                "policy_reviews": [],
                "reference_reviews": [
                    {
                        "source_id": "guide",
                        "audience": {
                            "tested_agent": False,
                            "participant_ids": ["buyer"],
                        },
                    }
                ],
            },
            reviewer_id="author",
        )
    )
    assert accepted["ok"], accepted
    result = accepted["result"]
    assert result["compiled"]["agent_environments"] == []
    assert "REFERENCE_SENTINEL" in json.dumps(
        result["compiled"]["participant_environments"]["buyer"]
    )
    assert result["compiled"]["evaluator_inputs"]["references"]
    compiled = transport.execute_request(
        _request("compile", snapshot=result["snapshot"])
    )
    assert compiled["ok"]
    assert compiled["result"] == result


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"{",
        b"[]",
        b'{"operation":"prepare","operation":"compile"}',
        _request("unknown"),
        b'{"version":"future","operation":"prepare"}',
        _request("prepare", sources=[], destination_schema={"fields": []}),
        _request("compile", snapshot={}),
    ],
)
def test_malformed_requests_return_stable_error_envelopes(payload):
    response = transport.execute_request(payload)
    assert response["version"] == transport.RESPONSE_VERSION
    assert response["ok"] is False
    assert response["error"]["code"] in {
        "source_request_invalid",
        "source_snapshot_invalid",
    }


@pytest.mark.parametrize("encoded", ["???", "YWJj\n", "YQ===", "é"])
def test_invalid_base64_is_rejected(encoded):
    response = transport.execute_request(
        _request(
            "prepare",
            sources=[
                {
                    "source_id": "bad",
                    "filename": "bad.txt",
                    "media_type": "text/plain",
                    "role": "reference",
                    "content_base64": encoded,
                }
            ],
            destination_schema={"fields": [{"name": "id", "data_type": "string"}]},
        )
    )
    assert response["ok"] is False
    assert response["operation"] == "prepare"
    assert response["error"]["code"] == "source_request_invalid"


def test_request_and_decoded_source_sizes_are_bounded(monkeypatch):
    monkeypatch.setattr(transport, "MAX_REQUEST_BYTES", 4)
    assert _prepare()["error"]["code"] == "source_too_large"
    monkeypatch.setattr(transport, "MAX_REQUEST_BYTES", 10000)
    monkeypatch.setattr(transport, "MAX_SOURCE_BYTES", 1)
    for content in (b"ab", b"abcdefgh"):
        response = transport.execute_request(
            _request(
                "prepare",
                sources=[
                    {
                        "source_id": "big",
                        "filename": "big.txt",
                        "media_type": "text/plain",
                        "role": "reference",
                        "content_base64": base64.b64encode(content).decode(),
                    }
                ],
                destination_schema={"fields": [{"name": "id", "data_type": "string"}]},
            )
        )
        assert response["error"]["code"] == "source_too_large"


def test_accept_rejects_modified_draft_and_invalid_audience():
    draft = _prepare()["result"]["draft"]
    request = dict(
        draft=draft,
        review={
            "expected_draft_digest": draft["draft_digest"],
            "record_reviews": [],
            "policy_reviews": [],
            "reference_reviews": [
                {
                    "source_id": "guide",
                    "audience": {
                        "tested_agent": False,
                        "participant_ids": ["../private"],
                    },
                }
            ],
        },
        reviewer_id="author",
    )
    response = transport.execute_request(_request("accept", **request))
    assert response["error"]["code"] == "source_audience_invalid"
    request["review"]["reference_reviews"][0]["audience"]["participant_ids"] = ["buyer"]
    draft["sources"][0]["pages"][0]["text"] = "changed"
    response = transport.execute_request(_request("accept", **request))
    assert response["error"]["code"] == "source_review_stale"


def test_module_reads_stdin_and_file_and_exits_nonzero_on_error(tmp_path: Path):
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    command = [sys.executable, "-m", "fi.alk.studio.environment_sources"]
    malformed = subprocess.run(command, input=b"{}", capture_output=True, env=env)
    assert malformed.returncode == 1
    assert json.loads(malformed.stdout)["error"]["code"] == "source_request_invalid"
    path = tmp_path / "request.json"
    path.write_bytes(
        _request(
            "prepare",
            sources=[],
            destination_schema={"fields": [{"name": "id", "data_type": "string"}]},
        )
    )
    completed = subprocess.run([*command, str(path)], capture_output=True, env=env)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["result"]["draft"]["sources"] == []
    assert sorted(item.name for item in tmp_path.iterdir()) == ["request.json"]
