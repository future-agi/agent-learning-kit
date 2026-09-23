from __future__ import annotations

import asyncio
import base64
import os
from types import SimpleNamespace

from test_chat_call_runner import _Adapter, _context

from fi.alk.harness import chat_worker
from fi.alk.harness.process_runtime import EnvironmentRuntime, RuntimeState


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
                        scenario_key=f"scenario-{index}", scenario_id=f"id-{index}"
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
    assert sorted(adapter.uploads) == [b"scenario-0", b"scenario-1"]
    assert {payload["runtime"]["world_index"] for payload in payloads} == {0, 1}
    assert dict(os.environ) == original
