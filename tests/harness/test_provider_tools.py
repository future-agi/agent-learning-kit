from __future__ import annotations

import json
from pathlib import Path

from fi.alk.harness import authoring_entrypoint
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.provider_tools import provider_tools, with_provider_tools
from fi.alk.harness.sources import ProviderSource

TRANSFER = {
    "type": "transferCall",
    "destinations": [
        {
            "type": "number",
            "number": "+14155550199",
            "message": "Transferring you to the front desk now.",
        }
    ],
}
VAPI = {
    "provider": "vapi",
    "source_target_id": "asst-1",
    "name": "Harbor Hotels",
    "model": {
        "messages": [{"role": "system", "content": "Transfer a manager request."}],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "book_room",
                    "description": "Book a room for a guest",
                    "parameters": {
                        "type": "object",
                        "properties": {"guest_name": {"type": "string"}},
                    },
                },
            },
            TRANSFER,
            {"type": "endCall"},
        ],
    },
    "reusable_tools": [],
}
RETELL = {
    "provider": "retell",
    "modality": "voice",
    "response_engine_type": "retell-llm",
    "general_tools": [
        {
            "name": "transfer_to_hygienist",
            "type": "transfer_call",
            "description": "Transfer the call to the hygienist line",
            "transfer_destination": {"type": "predefined", "number": "+14155550123"},
        },
        {"name": "end_call", "type": "end_call"},
    ],
}


def _vapi_job() -> HarnessJob:
    return HarnessJob(
        job_id="job-vapi",
        run_id="run-vapi",
        execution="hosted",
        source={"kind": "provider"},
        agent={
            "connector": "vapi",
            "mode": "connect_only",
            "config": {"assistant_id": "asst-1"},
        },
        scenario_count=1,
        runtime={"isolation": "dedicated_vm"},
    )


def _by_name(tools: list[dict]) -> dict[str, dict]:
    return {tool["name"]: tool for tool in tools}


def test_a_vapi_transfer_is_a_tool_with_its_destinations_and_hand_off():
    tools = _by_name(provider_tools(VAPI))

    assert list(tools) == ["book_room", "transferCall", "endCall"]
    transfer = tools["transferCall"]
    assert transfer["type"] == "transferCall"
    assert transfer["destinations"] == TRANSFER["destinations"]
    assert "+14155550199" in transfer["description"]
    assert "+14155550199" in transfer["effects"]
    assert "leaves the call" in transfer["effects"]
    assert tools["endCall"]["effects"] == "Ends the call."
    assert tools["book_room"]["parameters"]["properties"] == {
        "guest_name": {"type": "string"}
    }
    assert "effects" not in tools["book_room"]


def test_a_retell_transfer_is_listed_the_same_way():
    tools = _by_name(provider_tools(RETELL))

    transfer = tools["transfer_to_hygienist"]
    assert transfer["description"] == "Transfer the call to the hygienist line"
    assert transfer["destinations"] == [{"type": "predefined", "number": "+14155550123"}]
    assert "+14155550123" in transfer["effects"]
    assert "leaves the call" in transfer["effects"]
    assert tools["end_call"]["effects"] == "Ends the call."


def test_a_retell_state_tool_and_a_vapi_reusable_tool_are_listed():
    retell = {**RETELL, "general_tools": [], "states": [{"name": "s", "tools": [RETELL["general_tools"][0]]}]}
    vapi = {**VAPI, "model": {"tools": []}, "reusable_tools": [TRANSFER]}

    assert [tool["name"] for tool in provider_tools(retell)] == ["transfer_to_hygienist"]
    assert [tool["name"] for tool in provider_tools(vapi)] == ["transferCall"]


def test_a_profile_that_lists_its_tools_or_has_none_is_kept():
    listed = {"provider": "phone", "tools": []}
    bare = {"provider": "retell", "general_prompt": "Cached."}

    assert with_provider_tools(listed) is listed
    assert with_provider_tools(bare) is bare


def test_a_cached_vapi_profile_reaches_the_reading_with_its_transfer(tmp_path: Path):
    cache = tmp_path / "provider-profile.json"
    cache.write_text(json.dumps(VAPI), encoding="utf-8")

    profile = authoring_entrypoint._load_provider_import_profile(_vapi_job(), None, cache)

    assert profile is not None
    assert "transferCall" in [tool["name"] for tool in profile["tools"]]
    assert json.loads(cache.read_text(encoding="utf-8")) == VAPI


def test_a_fetched_vapi_profile_reaches_the_reading_with_its_transfer(
    monkeypatch, tmp_path: Path
):
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"VAPI_API_KEY": "key"}), encoding="utf-8")
    monkeypatch.setattr(
        authoring_entrypoint, "inspect_provider_target", lambda *_a, **_k: dict(VAPI)
    )

    profile = authoring_entrypoint._load_provider_import_profile(_vapi_job(), secrets)

    assert profile is not None
    assert "transferCall" in [tool["name"] for tool in profile["tools"]]


def test_the_reading_is_told_to_record_every_listed_tool():
    briefing = ProviderSource(name="harbor", profile=with_provider_tools(VAPI)).briefing()

    assert "Record each one in the contract's tools under the same name" in briefing
    assert '"name": "transferCall"' in briefing
    assert '"number": "+14155550199"' in briefing
