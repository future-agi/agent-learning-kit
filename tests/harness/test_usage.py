from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from fi.alk.harness.outbound import (
    CAPABILITIES_SCHEMA_VERSION,
    HostedCapabilities,
    HostedEndpoints,
    TransportError,
)
from fi.alk.harness.usage import UsageJournal, UsageReporter, UsageUnavailable


def _capabilities() -> HostedCapabilities:
    root = "https://platform.example/simulate/api/harness-attempts/attempt-1"
    return HostedCapabilities(
        schema_version=CAPABILITIES_SCHEMA_VERSION,
        job_id="job-1",
        attempt_id="attempt-1",
        attempt_number=1,
        fence="fence-1",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        token="token-1",
        endpoints=HostedEndpoints(
            events=f"{root}/events/",
            results=f"{root}/results/",
            artifacts=f"{root}/artifacts/",
            scenarios=f"{root}/scenarios/",
        ),
    )


def test_usage_journal_recovers_and_deduplicates_stable_action(tmp_path) -> None:
    path = tmp_path / "usage.json"
    first = UsageJournal(path, attempt_id="attempt-1")
    original = first.append(
        action="voice_call",
        scenario_key="account-help",
        amount=1.25,
        funding="platform",
        record_key="1",
    )

    recovered = UsageJournal(path, attempt_id="attempt-1")
    replay = recovered.append(
        action="voice_call",
        scenario_key="account-help",
        amount=1.25,
        funding="platform",
        record_key="1",
    )

    assert replay.id == original.id
    assert len(recovered.records) == 1
    assert json.loads(path.read_text())["totals"]["voice_sim_minutes"] == 1.25


def test_customer_funded_text_tokens_remain_auditable_but_free(tmp_path) -> None:
    journal = UsageJournal(tmp_path / "usage.json", attempt_id="attempt-1")
    journal.append(
        action="text_call",
        scenario_key="account-help",
        amount=321,
        funding="customer",
        record_key="1",
    )

    payload = journal.payload()
    assert payload["records"][0]["amount"] == 321
    assert payload["records"][0]["funding"] == "customer"
    assert payload["totals"]["text_sim_tokens"] == 0


class _UnavailableTransport:
    def request(self, *args, **kwargs):
        raise TransportError("offline")


class _AllowedTransport:
    def __init__(self) -> None:
        self.body = None

    def request(self, *args, **kwargs):
        self.body = kwargs["json_body"]
        from fi.alk.harness.outbound import TransportResponse

        return TransportResponse(200, {"result": {"allowed": True}}, {})


def test_paid_usage_check_fails_closed_on_transport_error(tmp_path) -> None:
    reporter = UsageReporter(
        _capabilities(),
        _UnavailableTransport(),
        UsageJournal(tmp_path / "usage.json", attempt_id="attempt-1"),
    )

    with pytest.raises(UsageUnavailable, match="usage check unavailable"):
        reporter.check("voice_call")


def test_managed_evaluation_check_and_record_carry_real_model(tmp_path) -> None:
    transport = _AllowedTransport()
    reporter = UsageReporter(
        _capabilities(),
        transport,
        UsageJournal(tmp_path / "usage.json", attempt_id="attempt-1"),
    )

    reporter.check("managed_evaluation", model="gemini-2.5-pro")
    assert transport.body == {
        "operation": "check",
        "action": "managed_evaluation",
        "model": "gemini-2.5-pro",
    }

    reporter.record(
        action="managed_evaluation",
        scenario_key="account-help",
        amount=1,
        funding="platform",
        model="gemini-2.5-pro",
        record_key="goal-one",
    )
    record = reporter.journal.records[0]
    assert record.model == "gemini-2.5-pro"


def test_vertex_gemini_usage_metadata_reaches_the_metering_stage():
    import asyncio
    from types import SimpleNamespace

    from fi.alk.harness.backends.base import StageDone
    from fi.alk.harness.backends.vertex_gemini import VertexGeminiSession

    event = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=120,
            candidates_token_count=30,
            cached_content_token_count=80,
        ),
        content=None,
        is_final_response=lambda: True,
    )

    class Runner:
        async def run_async(self, **kwargs):
            yield event

    async def collect():
        session = VertexGeminiSession(SimpleNamespace(max_turns=2), "gemini-3.7-flash")
        session._runner = Runner()
        session._pending = "drive the scenario"
        return [reply async for reply in session.replies()]

    completed = next(
        reply for reply in asyncio.run(collect()) if isinstance(reply, StageDone)
    )
    assert completed.tokens_in == 120
    assert completed.tokens_out == 30
    assert completed.tokens_cached == 80
