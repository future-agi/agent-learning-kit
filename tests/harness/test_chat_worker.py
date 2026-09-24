from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from types import SimpleNamespace

from test_chat_call_runner import _Adapter, _context

from fi.alk.harness import chat_call_runner, chat_worker
from fi.alk.harness.hosted_scheduler import CallOutcome
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState


def test_isolated_chat_worker_preserves_source_scenario_identity(
    tmp_path, monkeypatch
):
    context = _context(tmp_path)
    observed = {}

    class Runner:
        def __init__(self, *_args):
            pass

        async def run(self, scenario, _runtime):
            observed["scenario_key"] = scenario.scenario_key
            observed["source_scenario_key"] = scenario.source_scenario_key
            observed["scenario_id"] = scenario.scenario_id
            return CallOutcome((), 1, None, None, 1)

    runtime = EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
    )
    payload = {
        "job": context.job.model_dump(mode="json"),
        "bundle_dir": str(context.bundle_dir),
        "work_directory": str(context.work_directory),
        "source_directory": None,
        "target_secrets": {},
        "attempt_number": 1,
        "runtime": runtime.model_dump(mode="json"),
        "scenario_key": "trial-key",
        "source_scenario_key": "authored-key",
        "scenario_id": "scenario-id",
    }
    result_path = tmp_path / "worker-result.json"
    monkeypatch.setattr(chat_call_runner, "HostedChatCallRunner", Runner)
    monkeypatch.setattr(chat_worker.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(chat_worker.sys, "argv", ["chat_worker", str(result_path)])

    asyncio.run(chat_worker.main())

    assert observed == {
        "scenario_key": "trial-key",
        "source_scenario_key": "authored-key",
        "scenario_id": "scenario-id",
    }
    assert json.loads(result_path.read_text())["outcome"]["turns"] == 1


def test_parallel_chat_workers_keep_routing_private_and_upload_in_parent(
    tmp_path, monkeypatch
):
    context = _context(tmp_path)
    adapter = _Adapter()
    monkeypatch.setenv("HARNESS_PLATFORM_API_KEY", "parent-only")
    original = dict(os.environ)
    payloads = []

    async def run():
        both = asyncio.Event()

        async def worker(module, payload, *, environ, work_directory):
            assert "HARNESS_PLATFORM_API_KEY" not in environ
            payloads.append(payload)
            if len(payloads) == 2:
                both.set()
            await asyncio.wait_for(both.wait(), 3)
            return {
                "outcome": {
                    "calls": [],
                    "turns": 1,
                    "started_at": None,
                    "ended_at": None,
                    "duration_ms": 1,
                    "transcript_artifact": "0",
                    "recording_artifacts": [],
                },
                "artifacts": [
                    {
                        "id": "0",
                        "kind": "transcript",
                        "data": base64.b64encode(
                            payload["scenario_key"].encode()
                        ).decode(),
                    }
                ],
            }

        monkeypatch.setattr(chat_worker, "run_json_worker", worker)
        runner = chat_worker.IsolatedChatCallRunner(adapter, context)
        outcomes = await asyncio.gather(
            *(
                runner.run(
                    SimpleNamespace(
                        scenario_key=f"trial-{index}",
                        source_scenario_key=f"authored-{index}",
                        scenario_id=f"id-{index}",
                    ),
                    EnvironmentRuntime(
                        runtime_id=f"runtime-{index}",
                        world_index=index,
                        bundle_digest="sha256:" + "a" * 64,
                        state=RuntimeState.READY,
                    ),
                )
                for index in range(2)
            )
        )
        assert all(
            outcome.transcript_artifact == "transcript-1" for outcome in outcomes
        )

    asyncio.run(run())
    assert sorted(adapter.uploads) == [b"trial-0", b"trial-1"]
    assert {payload["runtime"]["world_index"] for payload in payloads} == {0, 1}
    assert {
        (payload["scenario_key"], payload["source_scenario_key"])
        for payload in payloads
    } == {("trial-0", "authored-0"), ("trial-1", "authored-1")}
    assert dict(os.environ) == original
