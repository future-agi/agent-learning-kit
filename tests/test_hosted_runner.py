"""Hosted-runner (plan §9) SDK tests: job contract, offline child run, and the
sink's pre-created-execution routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from fi.simulate.hosted.child_entrypoint import main as child_main
from fi.simulate.hosted.job import RunnerMode, StartRunnerJob
from fi.simulate.results.futureagi import FutureAGIResultSink
from fi.simulate.runtime import new_run_id
from fi.simulate.runtime.runner import SimulationRunner
from fi.simulate.runtime.spec import (
    AgentEndpointSpec,
    EnvironmentSpec,
    EvidencePolicy,
    SimulationSpec,
    SimulatorPolicySpec,
)
from fi.simulate.simulation.models import Persona, Scenario

_ECHO_SOURCE = (
    "def reply(input):\n"
    "    msgs = getattr(input, 'messages', None) or []\n"
    "    last = msgs[-1]['content'] if msgs else 'hello'\n"
    "    return f'Thanks for reaching out about: {last}. I can help with that.'\n"
)


def _echo_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    module_path = tmp_path / "hosted_echo_agent.py"
    module_path.write_text(_ECHO_SOURCE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return "hosted_echo_agent:reply"


def _chat_spec(target_adapter: str, target_config: dict) -> SimulationSpec:
    scenario = Scenario(
        name="refund-help",
        dataset=[
            Persona(
                persona={"name": "Sam"},
                situation="I was double charged on my invoice.",
                outcome="Get a refund confirmation.",
            )
        ],
    )
    return SimulationSpec(
        run_id=new_run_id(),
        environment=EnvironmentSpec(
            adapter="chat",
            world_kind="conversation",
            config={"max_turns": 4, "min_turns": 2, "modality": "text"},
        ),
        target=AgentEndpointSpec(adapter=target_adapter, config=target_config),
        simulator=SimulatorPolicySpec(adapter="synthetic_user"),
        scenario=scenario,
        evidence=EvidencePolicy(),
    )


def test_chat_runner_job_roundtrips():
    spec = _chat_spec("callable", {"target": "mod:fn"})
    job = StartRunnerJob(job_id="job-1", mode=RunnerMode.CHAT, spec=spec)
    restored = StartRunnerJob.model_validate_json(job.model_dump_json())
    assert restored.job_id == "job-1"
    assert restored.mode is RunnerMode.CHAT
    assert restored.mode.needs_phone is False
    assert restored.spec.environment.adapter == "chat"


def test_voice_runner_job_roundtrips_and_requires_voice_config():
    from fi.simulate.hosted.job import VoiceRunConfig

    voice = VoiceRunConfig(
        agent_definition={
            "name": "vx",
            "system_prompt": "p",
            "transport": {"kind": "sip_outbound"},
        },
        scenario={
            "name": "s",
            "dataset": [{"persona": {"name": "x"}, "situation": "y", "outcome": "z"}],
        },
    )
    job = StartRunnerJob(job_id="v1", mode=RunnerMode.VOICE_SIP, voice=voice)
    restored = StartRunnerJob.model_validate_json(job.model_dump_json())
    assert restored.mode.is_voice is True
    assert restored.mode.needs_phone is True
    assert restored.voice.agent_definition["transport"]["kind"] == "sip_outbound"

    with pytest.raises(Exception):
        StartRunnerJob(job_id="bad", mode=RunnerMode.VOICE_WEBRTC)  # no voice cfg


def test_child_entrypoint_chat_completes_offline(tmp_path, monkeypatch):
    # A callable target runs caller code in-process — allowed here only via the
    # explicit trusted-default escape (an operator-configured local target).
    monkeypatch.setenv("ALK_UNSAFE_INPROCESS_CODE_ACTORS", "true")
    target = _echo_module(tmp_path, monkeypatch)
    run_root = tmp_path / "runs"
    spec = _chat_spec("callable", {"target": target})
    job = StartRunnerJob(
        job_id="job-offline",
        mode=RunnerMode.CHAT,
        spec=spec,
        sink={"root_directory": str(run_root)},
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(job.model_dump_json(), encoding="utf-8")

    # No FI_* creds -> sink records not_configured, run still completes.
    for var in ("FI_API_KEY", "FI_SECRET_KEY", "FI_BASE_URL", "FI_TEST_EXECUTION_ID"):
        monkeypatch.delenv(var, raising=False)

    rc = child_main([str(job_path), "--status-file", str(tmp_path / "status.jsonl")])
    assert rc == 0

    report = json.loads((run_root / spec.run_id / "report.json").read_text())
    assert report["status"] == "completed"
    submission = json.loads((run_root / spec.run_id / "submission.json").read_text())
    assert submission["status"] == "not_configured"


def test_sink_submits_into_pre_created_execution(tmp_path, monkeypatch):
    target = _echo_module(tmp_path, monkeypatch)
    spec = _chat_spec("callable", {"target": target})

    sink = FutureAGIResultSink(
        root=str(tmp_path / "runs"),
        api_url="http://localhost:8000",
        run_test_id="rt-1",
        test_execution_id="te-1",
    )
    report = asyncio.run(
        SimulationRunner().run(spec, target=_import(target), result_sink=sink)
    )

    seen = {"create": [], "batch": [], "result": []}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/test-executions/"):
            seen["create"].append(path)
            return httpx.Response(200, json={"result": {"test_execution_id": "BAD"}})
        if path.endswith("/batch/"):
            seen["batch"].append(path)
            return httpx.Response(
                200,
                json={"result": {"call_execution_ids": ["ce-1"], "has_more": False}},
            )
        if path.endswith("/result/"):
            seen["result"].append(path)
            return httpx.Response(
                200,
                json={
                    "result": {
                        "call_execution_id": "ce-1",
                        "status": "ingested",
                        "eval_dispatched": True,
                    }
                },
            )
        return httpx.Response(404)

    original_client = httpx.Client

    def client_factory(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(handler))
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "Client", client_factory)
    monkeypatch.setenv("FI_API_KEY", "k")
    monkeypatch.setenv("FI_SECRET_KEY", "s")

    outcome = sink.submit(report)

    assert outcome["status"] == "submitted"
    # Pre-created execution -> create endpoint never hit; batch targets te-1.
    assert seen["create"] == []
    assert any("te-1" in path for path in seen["batch"])
    assert seen["result"], "expected a result PATCH per test case"


def _import(ref: str):
    import importlib

    module_name, _, attr = ref.partition(":")
    return getattr(importlib.import_module(module_name), attr)
