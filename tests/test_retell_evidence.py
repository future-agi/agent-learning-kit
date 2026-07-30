from __future__ import annotations

import asyncio
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
