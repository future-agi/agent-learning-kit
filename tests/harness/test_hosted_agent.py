"""Our own agent (`hosted_agent`): what a stopped or cancelled call leaves behind."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from fi.alk.harness import hosted_agent
from fi.alk.harness.call_runner import CallRunnerContext
from fi.alk.harness.hosted_scheduler import CallCancelled
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.process_runtime import (
    EnvironmentRuntime,
    RuntimeEndpoint,
    RuntimeState,
)
from fi.alk.harness.run.conversation import Exchange, Transcript


class _Adapter:
    def __init__(self) -> None:
        self.uploads: list[bytes] = []

    async def upload_artifact(self, data: bytes, **_kwargs: Any) -> str:
        self.uploads.append(data)
        return "transcript-1"


class _Usage:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def record(self, **record: Any) -> None:
        self.records.append(record)


def _context(tmp_path, usage) -> CallRunnerContext:
    bundle = tmp_path / "bundle"
    scenario = bundle / "scenarios" / "one"
    scenario.mkdir(parents=True)
    (bundle / "contract.json").write_text(
        json.dumps({"agent": "a", "modality": "chat", "tools": []})
    )
    (scenario / "scenario.json").write_text(
        json.dumps({"scenario_key": "one", "name": "one", "instruction": "Refund it"})
    )
    job = HarnessJob.model_validate(
        {
            "job_id": "job-1",
            "run_id": "run-1",
            "execution": "local",
            "source": {"kind": "provider"},
            "agent": {
                "connector": "hosted_agent",
                "config": {"name": "a", "instructions": "help"},
            },
        }
    )
    return CallRunnerContext(
        job=job,
        bundle_dir=bundle,
        work_directory=tmp_path / "work",
        evidence_seam=None,
        target_provider_secret_values={},
        attempt_number=1,
        usage_reporter=usage,
    )


def _runtime() -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={
            name: RuntimeEndpoint(
                capability=name, protocol=protocol, address=address, configuration_name=name
            )
            for name, protocol, address in (
                (hosted_agent.WORLD_MCP_CAPABILITY, "http", "http://127.0.0.1:1"),
                ("world_db", "postgres", "postgresql://nowhere/w"),
            )
        },
    )


def _cancelled(tmp_path, monkeypatch, *, stop: bool):
    """One call cancelled while the agent answers its second turn."""
    adapter, usage, stopped = _Adapter(), _Usage(), {"value": False}
    monkeypatch.setattr(hosted_agent, "_client", lambda: None)
    monkeypatch.setattr(hosted_agent, "_clear_tool_trace_calls", lambda _dsn: None)
    monkeypatch.setattr(hosted_agent, "_read_tool_trace", lambda _runtime: ())

    async def scenario():
        reached = asyncio.Event()

        async def converse(*_args, **_kwargs):
            transcript = Transcript(
                exchanges=[
                    Exchange("customer", "Refund order 1043"),
                    Exchange("agent", "Looking it up"),
                ]
            )
            transcript.simulator_input_tokens = 40
            transcript.simulator_output_tokens = 2
            try:
                reached.set()
                await asyncio.sleep(60)
            except asyncio.CancelledError as exc:
                exc.partial_transcript = transcript
                raise

        monkeypatch.setattr(hosted_agent, "converse", converse)
        runner = hosted_agent.HostedAgentCallRunner(
            adapter,
            replace(_context(tmp_path, usage), stop_requested=lambda: stopped["value"]),
        )
        task = asyncio.create_task(
            runner.run(
                SimpleNamespace(scenario_key="one", scenario_id="scenario-1"), _runtime()
            )
        )
        await reached.wait()
        stopped["value"] = stop
        task.cancel()
        return await task

    with pytest.raises(asyncio.CancelledError if not stop else CallCancelled) as caught:
        asyncio.run(scenario())
    return adapter, usage, caught.value


def test_a_stopped_call_seals_its_partial_transcript_and_keeps_the_simulators_usage(
    tmp_path, monkeypatch
) -> None:
    adapter, usage, failure = _cancelled(tmp_path, monkeypatch, stop=True)

    assert failure.partial.stop_reason == "user_canceled"
    assert failure.partial.turns == 2
    (transcript,) = [json.loads(data) for data in adapter.uploads]
    assert [one["content"] for one in transcript["messages"]] == [
        "Refund order 1043",
        "Looking it up",
    ]
    (record,) = usage.records
    assert (record["outcome"], record["amount"]) == ("failed", 42)


def test_a_cancel_that_is_not_a_stop_is_never_billed_completed(
    tmp_path, monkeypatch
) -> None:
    adapter, usage, _failure = _cancelled(tmp_path, monkeypatch, stop=False)

    assert adapter.uploads == []
    assert [record["outcome"] for record in usage.records] == ["failed"]
