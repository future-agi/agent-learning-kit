from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx

from fi.simulate.agent.definition import ProviderEvidenceConfig
from fi.simulate.evidence.providers.base import EvidenceContext
from fi.simulate.evidence.providers.retell import RetellEvidenceSource


def test_retell_originator_response_uses_exact_call_id(tmp_path) -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return httpx.Response(200, json={"call_id": "call_retell_123"})

    async def run() -> None:
        client = httpx.AsyncClient(
            base_url="https://api.retellai.com",
            transport=httpx.MockTransport(handler),
        )
        source = RetellEvidenceSource(
            ProviderEvidenceConfig(
                provider="retell",
                call_id_source="originator_response",
            ),
            api_key="test-key",
            client=client,
        )
        await source.connect(
            EvidenceContext(
                run_id="run_retell",
                test_case_id="case_retell",
                case_directory=tmp_path,
                started_at=datetime.now(timezone.utc),
                call_id_hint="call_retell_123",
            )
        )
        payload = await source._locate_and_fetch_call()
        await client.aclose()
        assert payload == {"call_id": "call_retell_123"}

    asyncio.run(run())

    assert requested_paths == ["/v2/get-call/call_retell_123"]


def test_retell_polling_uses_v3_typed_filters(tmp_path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/v3/list-calls":
            return httpx.Response(
                200,
                json={
                    "calls": [
                        {
                            "call_id": "call_retell_456",
                            "call_status": "ended",
                            "end_timestamp": 1_753_881_600_000,
                        }
                    ]
                },
            )
        if request.url.path == "/v2/get-call/call_retell_456":
            return httpx.Response(200, json={"call_id": "call_retell_456"})
        raise AssertionError(request.url.path)

    async def run() -> dict:
        client = httpx.AsyncClient(
            base_url="https://api.retellai.com",
            transport=httpx.MockTransport(handler),
        )
        source = RetellEvidenceSource(
            ProviderEvidenceConfig(
                provider="retell",
                call_id_source="polling_window",
                polling_window_seconds=60,
            ),
            api_key="test-key",
            client=client,
        )
        await source.connect(
            EvidenceContext(
                run_id="run_retell",
                test_case_id="case_retell",
                case_directory=tmp_path,
                started_at=datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc),
                caller_phone="+14155550100",
            )
        )
        payload = await source._locate_and_fetch_call()
        await client.aclose()
        assert payload is not None
        return payload

    assert asyncio.run(run()) == {"call_id": "call_retell_456"}
    request_payload = json.loads(requests[0].content)
    filters = request_payload["filter_criteria"]
    assert filters["start_timestamp"]["type"] == "range"
    assert filters["start_timestamp"]["op"] == "bt"
    assert filters["from_number"] == {
        "type": "string",
        "op": "eq",
        "value": "+14155550100",
    }
