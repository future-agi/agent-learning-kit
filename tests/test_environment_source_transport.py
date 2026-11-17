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
