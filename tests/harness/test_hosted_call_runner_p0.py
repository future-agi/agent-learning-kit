"""P0 focused tests for hosted_call_runner.py:

1. ADC materialization + cleanup (GOOGLE_APPLICATION_CREDENTIALS_JSON -> mode-0600 file)
2. Voice-case shared precedence (scenario -> bundle metadata -> env -> fallback)
3. Named-agent dispatch (LIVEKIT_TARGET_AGENT_NAME required)
4. Tool-trace capture preserved
5. P=1 failure preservation (call_failed not masked by world_pool_exhausted)
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from fi.alk.harness.hosted_call_runner import (
    VoiceCallRunner,
    _cleanup_adc,
    _materialize_adc,
    _resolve_voice_case,
)
from fi.alk.harness.hosted_scheduler import (
    CallAborted,
    CallOutcome,
    CallSummary,
    HostedScheduler,
    NoWorldsAvailable,
    ReceiptFailure,
    ResultReceipt,
    RunResult,
    WorldPool,
)
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _runtime(world_index: int = 0, agent_name: str = "agent-w0", trace_path: str = "/tmp/trace.jsonl") -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id=f"test:w{world_index}:abcd",
        world_index=world_index,
        bundle_digest="sha256:" + "0" * 64,
        state=RuntimeState.READY,
        metadata={"livekit_agent_name": agent_name, "tool_trace_path": trace_path},
    )


@dataclass
class _FakeScenario:
    scenario_key: str = "cancel_ride"
    scenario_id: str = "sc-1"
    sub_goals: tuple = ()

    def setup(self, world):
        pass

    def ready(self, world):
        pass


# ---------------------------------------------------------------------------
# 1. ADC materialization + cleanup
# ---------------------------------------------------------------------------


class TestADCMaterialization:
    def test_materialize_creates_mode_0600_file(self, tmp_path):
        creds = '{"type":"service_account","project_id":"test"}'
        path = _materialize_adc(creds, tmp_path)
        try:
            assert path.exists()
            assert path.read_text() == creds
            mode = stat.S_IMODE(path.stat().st_mode)
            assert mode == (stat.S_IRUSR | stat.S_IWUSR), f"expected 0600, got {oct(mode)}"
        finally:
            _cleanup_adc(path)

    def test_materialize_empty_json_raises_call_aborted(self, tmp_path):
        with pytest.raises(CallAborted, match="empty"):
            _materialize_adc("   ", tmp_path)

    def test_materialize_invalid_json_raises_call_aborted(self, tmp_path):
        with pytest.raises(CallAborted, match="not valid JSON"):
            _materialize_adc("{not-json", tmp_path)

    def test_vertex_without_adc_raises_before_call(self, tmp_path, monkeypatch):
        bundle_dir = tmp_path / "bundle"
        scenario_dir = bundle_dir / "scenarios" / "test_sc"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "scenario.json").write_text(
            json.dumps({"scenario_key": "test_sc", "voice_case": "2.1.2"}),
            encoding="utf-8",
        )
        monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
        runner = VoiceCallRunner(bundle_dir, bundle_metadata={"voice_case": "2.1.2"})
        with pytest.raises(CallAborted, match="Vertex caller requires"):
            asyncio.run(runner.run(_FakeScenario(scenario_key="test_sc"), _runtime()))

    def test_cleanup_removes_file_and_env(self, tmp_path):
        creds = '{"type":"test"}'
        path = _materialize_adc(creds, tmp_path)
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
        _cleanup_adc(path)
        assert not path.exists()
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ

    def test_cleanup_none_is_noop(self):
        _cleanup_adc(None)  # must not raise

    def test_cleanup_missing_file_is_noop(self, tmp_path):
        path = tmp_path / "gone.json"
        _cleanup_adc(path)  # must not raise

    def test_adc_cleaned_on_call_failure(self, tmp_path, monkeypatch):
        """Verify ADC file is cleaned even when place_the_call raises."""
        bundle_dir = tmp_path / "bundle"
        scenarios_dir = bundle_dir / "scenarios" / "test_sc"
        scenarios_dir.mkdir(parents=True)
        (scenarios_dir / "scenario.json").write_text(
            json.dumps({"scenario_key": "test_sc", "voice_case": "2.1.2"}),
            encoding="utf-8",
        )
        monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", '{"type":"test_sa"}')

        runner = VoiceCallRunner(bundle_dir, bundle_metadata={"voice_case": "2.1.2"})

        def _failing_call(*_args, **_kwargs):
            raise RuntimeError("simulated call failure")

        with mock.patch("fi.alk.harness.hosted_call_runner.place_the_call", side_effect=_failing_call):
            with pytest.raises(RuntimeError, match="simulated call failure"):
                asyncio.run(runner.run(_FakeScenario(scenario_key="test_sc"), _runtime()))

        # ADC file must be gone
        adc_dir = bundle_dir.parent / ".adc"
        if adc_dir.is_dir():
            remaining = list(adc_dir.iterdir())
            assert remaining == [], f"ADC file not cleaned: {remaining}"
        assert "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ


# ---------------------------------------------------------------------------
# 2. Voice-case shared precedence
# ---------------------------------------------------------------------------


class TestVoiceCasePrecedence:
    def test_scenario_doc_wins(self):
        assert _resolve_voice_case({"voice_case": "3.0.0"}, {"voice_case": "2.1.2"}) == "3.0.0"

    def test_bundle_metadata_fallback(self):
        assert _resolve_voice_case({}, {"voice_case": "2.1.2"}) == "2.1.2"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("HARNESS_VOICE_CASE", "1.0.0")
        assert _resolve_voice_case({}, {}) == "1.0.0"

    def test_hardcoded_fallback(self, monkeypatch):
        monkeypatch.delenv("HARNESS_VOICE_CASE", raising=False)
        assert _resolve_voice_case({}, {}) == "2.1.2"

    def test_empty_scenario_doc_falls_through(self):
        assert _resolve_voice_case({"voice_case": ""}, {"voice_case": "2.1.2"}) == "2.1.2"

    def test_whitespace_scenario_doc_falls_through(self):
        assert _resolve_voice_case({"voice_case": "  "}, {"voice_case": "2.1.2"}) == "2.1.2"

    def test_non_string_scenario_doc_falls_through(self):
        assert _resolve_voice_case({"voice_case": 123}, {"voice_case": "2.1.2"}) == "2.1.2"


# ---------------------------------------------------------------------------
# 3. Named-agent dispatch (missing agent_name -> CallAborted)
# ---------------------------------------------------------------------------


class TestNamedAgentDispatch:
    def test_missing_agent_name_raises_call_aborted(self, tmp_path):
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        runner = VoiceCallRunner(bundle_dir)
        runtime = EnvironmentRuntime(
            runtime_id="test:w0:abcd",
            world_index=0,
            bundle_digest="sha256:" + "0" * 64,
            state=RuntimeState.READY,
            metadata={},  # no livekit_agent_name
        )
        with pytest.raises(CallAborted, match="livekit_agent_name"):
            asyncio.run(runner.run(_FakeScenario(), runtime))

    def test_empty_agent_name_raises_call_aborted(self, tmp_path):
        bundle_dir = tmp_path / "bundle"
        bundle_dir.mkdir()
        runner = VoiceCallRunner(bundle_dir)
        runtime = EnvironmentRuntime(
            runtime_id="test:w0:abcd",
            world_index=0,
            bundle_digest="sha256:" + "0" * 64,
            state=RuntimeState.READY,
            metadata={"livekit_agent_name": "  "},
        )
        with pytest.raises(CallAborted, match="livekit_agent_name"):
            asyncio.run(runner.run(_FakeScenario(), runtime))

    def test_agent_name_set_in_env(self, tmp_path, monkeypatch):
        """Verify LIVEKIT_TARGET_AGENT_NAME is set for the subprocess."""
        bundle_dir = tmp_path / "bundle"
        scenarios_dir = bundle_dir / "scenarios" / "cancel_ride"
        scenarios_dir.mkdir(parents=True)
        (scenarios_dir / "scenario.json").write_text(
            json.dumps({"scenario_key": "cancel_ride", "voice_case": "2.1.2"}),
            encoding="utf-8",
        )
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)

        captured_env: dict[str, str] = {}

        def _capture_call(case, dry_run, on_exchange):
            captured_env["LIVEKIT_TARGET_AGENT_NAME"] = os.environ.get("LIVEKIT_TARGET_AGENT_NAME", "")
            return 0  # success exit code

        runner = VoiceCallRunner(bundle_dir, bundle_metadata={"voice_case": "2.1.2"})
        with mock.patch("fi.alk.harness.hosted_call_runner.place_the_call", side_effect=_capture_call):
            with mock.patch("fi.alk.harness.hosted_call_runner._semantic_calls", return_value=[]):
                # exit code 0 with no calls → CallAborted, but that's fine for this test
                try:
                    asyncio.run(runner.run(_FakeScenario(), _runtime(agent_name="my-agent-w0")))
                except CallAborted:
                    pass  # expected: code 0 but no evidence
        assert captured_env.get("LIVEKIT_TARGET_AGENT_NAME") == "my-agent-w0"


# ---------------------------------------------------------------------------
# 4. Trace outcome: CallOutcome timestamps/artifacts obey outbound contract
# ---------------------------------------------------------------------------


class TestTraceOutcome:
    def test_call_outcome_has_rfc3339_timestamps(self, tmp_path, monkeypatch):
        bundle_dir = tmp_path / "bundle"
        scenarios_dir = bundle_dir / "scenarios" / "cancel_ride"
        scenarios_dir.mkdir(parents=True)
        (scenarios_dir / "scenario.json").write_text(
            json.dumps({"scenario_key": "cancel_ride", "voice_case": "2.1.2"}),
            encoding="utf-8",
        )
        monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS_JSON", raising=False)

        # Fake a successful call with tool evidence
        from fi.alk.harness.world.runtime import Call

        fake_call = Call(name="cancel_booking", arguments={"ref": "BK-001"}, result="ok")

        def _ok_call(case, dry_run, on_exchange):
            if on_exchange:
                on_exchange({"turn": 1})
            return 0

        runner = VoiceCallRunner(bundle_dir, bundle_metadata={})
        with mock.patch("fi.alk.harness.hosted_call_runner.place_the_call", side_effect=_ok_call):
            with mock.patch("fi.alk.harness.hosted_call_runner._semantic_calls", return_value=[fake_call]):
                outcome = asyncio.run(runner.run(_FakeScenario(), _runtime()))

        assert outcome.started_at is not None
        assert outcome.ended_at is not None
        # RFC 3339 with Z suffix
        assert outcome.started_at.endswith("Z")
        assert outcome.ended_at.endswith("Z")
        assert outcome.duration_ms > 0 or outcome.duration_ms == 0  # non-negative
        assert outcome.turns == 1
        assert len(outcome.calls) == 1
        assert outcome.calls[0].name == "cancel_booking"


# ---------------------------------------------------------------------------
# 5. P=1 failure preservation
# ---------------------------------------------------------------------------


class TestP1FailurePreservation:
    """When parallelism=1, a single call_failed must be returned as its original
    typed failure, not masked by world_pool_exhausted."""

    def test_single_world_call_failed_preserves_original_failure(self):
        """Simulate: P=1, call_failed on the only world → retry excludes it →
        NoWorldsAvailable. The abort should carry the original call_failed, not
        world_pool_exhausted."""

        # Build a fake pool/factory/runner/outbound to exercise the scheduler path.
        @dataclass
        class FakeWorld:
            world_index: int = 0
            rng: Any = None

            def state(self, table=None):
                return {}

            def put(self, *a, **kw):
                return {}

            def change(self, *a, **kw):
                return 0

            def drop(self, *a, **kw):
                return 0

            def call(self, *a, **kw):
                from fi.alk.harness.world.runtime import Call
                return Call(name="x", arguments={})

            def query(self, *a, **kw):
                return []

            def read_only(self):
                return self

        class FakeWorldFactory:
            async def create(self, runtime, *, rng):
                return FakeWorld(world_index=runtime.world_index, rng=rng)

        class FailingCallRunner:
            async def run(self, scenario, runtime):
                raise CallAborted(
                    "LiveKit agent unreachable",
                    partial=CallOutcome(
                        calls=(), turns=0,
                        started_at="2026-01-01T00:00:00.000Z",
                        ended_at="2026-01-01T00:00:01.000Z",
                        duration_ms=1000,
                    ),
                )

        class FakeOutbound:
            async def scenario_started(self, **kw):
                pass

            async def scenario_retried(self, **kw):
                pass

            async def world_unhealthy(self, **kw):
                pass

            async def log(self, **kw):
                pass

            async def receipt(self, receipt):
                pass

        class FakeProvisioner:
            async def provision(self, bundle, *, source, bundle_dir, work_directory, instances=1):
                return [_runtime(world_index=i) for i in range(instances)]

            async def reset(self, runtime, *, work_directory):
                pass

            async def healthy(self, runtime, *, work_directory):
                return True

            async def close(self, *, work_directory):
                pass

        async def _run_p1():
            provisioner = FakeProvisioner()
            pool = WorldPool(
                provisioner,
                bundle=None,
                source=Path("/work/source"),
                bundle_dir=Path("/work/bundle"),
                work_directory=Path("/work"),
                instances=1,
            )
            await pool.start()
            scheduler = HostedScheduler(
                pool=pool,
                world_factory=FakeWorldFactory(),
                call_runner=FailingCallRunner(),
                outbound=FakeOutbound(),
                job_seed=42,
            )
            scenario = _FakeScenario(scenario_key="cancel_ride", scenario_id="sc-1")
            scenario.sub_goals = (
                type("SG", (), {"name": "booking_cancelled", "judged": "", "check": lambda w, c: True})(),
            )
            result = await scheduler.run([scenario])
            await pool.close()
            return result

        result: RunResult = asyncio.run(_run_p1())

        # The abort should carry the ORIGINAL call_failed failure, not world_pool_exhausted.
        assert result.aborted is not None, "expected an abort for a P=1 deterministic call failure"
        assert result.aborted.code == "call_failed", (
            f"expected call_failed, got {result.aborted.code} — "
            "the original failure was masked by world_pool_exhausted"
        )
        assert "LiveKit agent unreachable" in result.aborted.message

        # The scenario's receipt should also carry the original failure.
        assert len(result.receipts) == 1
        receipt = result.receipts[0]
        assert receipt.status == "errored"
        assert receipt.failure is not None
        assert receipt.failure.code == "call_failed"
