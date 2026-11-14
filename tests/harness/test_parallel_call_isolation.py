import asyncio
import json
import os

from test_call_runner import (
    FakeAdapter,
    _context,
    _FakeScenario,
    _report,
    _runtime,
    _write_scenario_doc,
)

from fi.alk.harness import call_runner as cr
from fi.alk.harness.simulator_voice import SIMULATOR_MODEL_ENV


def test_supported_simulator_endpoint_overrides_reach_only_private_environment(
    tmp_path,
):
    _, context = _context(tmp_path=tmp_path)
    configured = {key: f"configured-{key}" for key in SIMULATOR_MODEL_ENV}
    ambient = {**configured, "HARNESS_PLATFORM_API_KEY": "platform-only"}
    original = dict(ambient)
    runner = cr.CallRunnerImpl(FakeAdapter(), context, environ=ambient)
    assert {key: runner._environ[key] for key in configured} == configured
    assert "HARNESS_PLATFORM_API_KEY" not in runner._environ
    assert ambient == original


def test_overlapping_calls_keep_scenario_settings_private(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_PLATFORM_API_KEY", "must-stay-in-parent")
    _, context = _context(tmp_path=tmp_path)
    for key, direction in (("k1", "outbound"), ("k2", "inbound")):
        _write_scenario_doc(context.bundle_dir, scenario_key=key)
        path = context.bundle_dir / "scenarios" / key / "scenario.json"
        doc = json.loads(path.read_text())
        doc.update(call_direction=direction, answered_by="voicemail")
        path.write_text(json.dumps(doc))
    original = dict(os.environ)
    snapshots = []

    async def run():
        both_started = asyncio.Event()

        async def worker(module, payload, *, environ, work_directory):
            before = dict(environ)
            assert "HARNESS_PLATFORM_API_KEY" not in environ
            snapshots.append(environ)
            if len(snapshots) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 3)
            assert environ == before
            return _report().model_dump(mode="json")

        monkeypatch.setattr(cr, "run_json_worker", worker)
        runner = cr.CallRunnerImpl(FakeAdapter(), context)
        await asyncio.gather(
            *(
                runner.run(
                    _FakeScenario(key),
                    _runtime(metadata={"livekit_agent_name": f"a-{key}"}),
                )
                for key in ("k1", "k2")
            )
        )

    asyncio.run(run())
    assert snapshots[0] is not snapshots[1]
    assert snapshots[0]["HARNESS_ANSWERED_BY"] == "voicemail"
    assert "HARNESS_ANSWERED_BY" not in snapshots[1]
    assert os.environ == original
