from __future__ import annotations

import asyncio
import base64
import io
import json
import os
from types import SimpleNamespace

import pytest

from test_chat_call_runner import (
    _Adapter,
    _ToolWorld,
    _context,
    _single_exchange,
)

from fi.alk.harness import chat_call_runner, chat_worker
from fi.alk.harness.hosted_scheduler import CallAborted
from fi.alk.harness.process_runtime import (
    EnvironmentRuntime,
    RuntimeEndpoint,
    RuntimeState,
)
from fi.simulate.agent.wrapper import AgentResponse


def test_isolated_chat_worker_preserves_private_runtime_auth(tmp_path, monkeypatch):
    context = _context(tmp_path)
    adapter = _Adapter()
    contract_path = context.bundle_dir / "contract.json"
    contract = json.loads(contract_path.read_text())
    contract["runtime"]["interface"].update(
        local_auth={
            "server_environment": "API_SERVER_KEY", "template_variable": "api_key",
        },
        headers_template={"Authorization": "Bearer {{api_key}}"},
    )
    contract_path.write_text(json.dumps(contract))
    token = "private-runtime-auth-token"
    runtime = EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={
            "target_http": RuntimeEndpoint(
                capability="target_http", protocol="http", address="http://agent.invalid",
            )
        },
    )
    runtime._local_auth_values = {"API_SERVER_KEY": token}
    requests = []

    def request_json(_self, _endpoint, *, headers, **_kwargs):
        assert headers["Authorization"] == f"Bearer {token}"
        requests.append(True)
        return 200, {"choices": [{"message": {
            "role": "assistant", "content": f"Account active. {token}",
        }}]}

    async def worker(_module, payload, *, environ, work_directory, stop_requested=None, pass_fds=()):
        assert token not in json.dumps(payload["runtime"])
        assert token not in json.dumps(environ)
        result_path = tmp_path / "worker-result.json"
        monkeypatch.setattr(chat_worker.sys, "stdin", io.StringIO(json.dumps(payload)))
        monkeypatch.setattr(chat_worker.sys, "argv", ["chat_worker", str(result_path)])
        await chat_worker.main()
        assert result_path.stat().st_mode & 0o777 == 0o600
        return json.loads(result_path.read_text())

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    monkeypatch.setattr(chat_call_runner.HTTPAgentWrapper, "_request_json", request_json)
    monkeypatch.setattr(chat_call_runner, "tool_world", lambda *_args: _ToolWorld())
    monkeypatch.setattr(chat_call_runner, "_drive_conversation", _single_exchange)
    outcome = asyncio.run(
        chat_worker.IsolatedChatCallRunner(adapter, context).run(
            SimpleNamespace(
                scenario_key="trial-1", source_scenario_key="one", scenario_id="id-1",
            ),
            runtime,
        )
    )
    assert requests == [True]
    assert outcome.messages[-1] == {
        "role": "assistant", "content": "Account active. <redacted>",
    }
    assert token not in repr(outcome)
    assert all(token.encode() not in data for data in adapter.uploads)
    assert all(
        token.encode() not in path.read_bytes()
        for path in tmp_path.rglob("*") if path.is_file()
    )


def test_isolated_chat_worker_uses_authored_scenario_for_selected_trial(
    tmp_path, monkeypatch
):
    context = _context(tmp_path)

    class Wrapper:
        def __init__(self, **_kwargs):
            pass

        async def call(self, _input):
            return AgentResponse(content="Your account is active.")

    runtime = EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={
            "target_http": RuntimeEndpoint(
                capability="target_http",
                protocol="http",
                address="http://agent.invalid",
            )
        },
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
        "source_scenario_key": "one",
        "scenario_id": "trial-id",
    }
    result_path = tmp_path / "worker-result.json"
    monkeypatch.setattr(chat_call_runner, "HTTPAgentWrapper", Wrapper)
    monkeypatch.setattr(chat_call_runner, "tool_world", lambda *_args: _ToolWorld())
    monkeypatch.setattr(chat_call_runner, "_drive_conversation", _single_exchange)
    monkeypatch.setattr(chat_worker.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(chat_worker.sys, "argv", ["chat_worker", str(result_path)])

    asyncio.run(chat_worker.main())

    result = json.loads(result_path.read_text())
    assert "error" not in result
    transcript = json.loads(
        base64.b64decode(result["artifacts"][0]["data"], validate=True)
    )
    assert transcript["messages"] == [
        {"role": "user", "content": "Look up account ACC-2048"},
        {"role": "assistant", "content": "Your account is active."},
    ]


def test_parallel_chat_workers_keep_routing_private_and_upload_in_parent(
    tmp_path, monkeypatch
):
    context = _context(tmp_path)
    adapter = _Adapter()
    monkeypatch.setenv("HARNESS_PLATFORM_API_KEY", "parent-only")
    monkeypatch.setenv("AGENTCC_API_KEY", "simulator-model-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://gateway.example")
    monkeypatch.setenv("ALK_CLAUDE_GATEWAY_API_KEY", "simulator-claude-key")
    monkeypatch.setenv("ALK_CLAUDE_GATEWAY_URL", "https://claude.example")
    monkeypatch.setenv("ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER", "1")
    original = dict(os.environ)
    payloads = []

    async def run():
        both = asyncio.Event()

        async def worker(module, payload, *, environ, work_directory, stop_requested=None, pass_fds=()):
            assert "HARNESS_PLATFORM_API_KEY" not in environ
            assert environ["AGENTCC_API_KEY"] == "simulator-model-key"
            assert environ["AGENTCC_BASE_URL"] == "https://gateway.example"
            assert environ["ALK_CLAUDE_GATEWAY_API_KEY"] == "simulator-claude-key"
            assert environ["ALK_CLAUDE_GATEWAY_URL"] == "https://claude.example"
            assert environ["ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER"] == "1"
            index = payload["runtime"]["world_index"]
            assert payload["local_auth_values"] == {
                "API_SERVER_KEY": f"private-world-{index}"
            }
            assert "private-world-" not in json.dumps(payload["runtime"])
            assert "private-world-" not in json.dumps(environ)
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
        runtimes = [
            EnvironmentRuntime(
                runtime_id=f"runtime-{index}",
                world_index=index,
                bundle_digest="sha256:" + "a" * 64,
                state=RuntimeState.READY,
            )
            for index in range(2)
        ]
        for index, runtime in enumerate(runtimes):
            runtime._local_auth_values = {"API_SERVER_KEY": f"private-world-{index}"}
        outcomes = await asyncio.gather(
            *(
                runner.run(
                    SimpleNamespace(
                        scenario_key=f"trial-{index}",
                        source_scenario_key=f"authored-{index}",
                        scenario_id=f"id-{index}",
                    ),
                    runtimes[index],
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


def test_cancel_during_handler_startup_closes_child(tmp_path, monkeypatch):
    from threading import Event
    from unittest.mock import Mock

    child = Mock()
    release = Event()
    runner = chat_worker.IsolatedChatCallRunner(_Adapter(), _context(tmp_path))

    async def cancel_during_startup():
        started = asyncio.Event()
        loop = asyncio.get_running_loop()

        def acquire(*_args):
            loop.call_soon_threadsafe(started.set)
            assert release.wait(5)
            return child

        monkeypatch.setattr(chat_worker, "handler_child_for", acquire)
        call = asyncio.create_task(runner.run(SimpleNamespace(), _ready_runtime()))
        try:
            await asyncio.wait_for(started.wait(), 5)
            for _ in range(2):
                call.cancel()
                await asyncio.sleep(0)
                assert not call.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, 5)
        child.close.assert_called_once_with()
        child.sock.fileno.assert_not_called()

    asyncio.run(cancel_during_startup())


def test_isolated_chat_runner_preserves_structured_worker_failure(
    tmp_path, monkeypatch
):
    context = _context(tmp_path)

    async def worker(*_args, **_kwargs):
        return {
            "error": "chat_target_failed: upstream unavailable",
            "error_code": "target_agent_failed",
            "error_marker": "target_unavailable",
        }

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    runner = chat_worker.IsolatedChatCallRunner(_Adapter(), context)

    with pytest.raises(CallAborted) as error:
        asyncio.run(
            runner.run(
                SimpleNamespace(
                    scenario_key="trial-key",
                    source_scenario_key="one",
                    scenario_id="trial-id",
                ),
                EnvironmentRuntime(
                    runtime_id="runtime-1",
                    world_index=0,
                    bundle_digest="sha256:" + "a" * 64,
                    state=RuntimeState.READY,
                ),
            )
        )

    assert str(error.value) == (
        "chat_worker_failed: chat_target_failed: upstream unavailable"
    )
    assert error.value.code == "target_agent_failed"
    assert error.value.marker == "target_unavailable"


@pytest.mark.parametrize("with_partial", [False, True])
def test_chat_worker_returns_call_aborted_details_only(
    tmp_path, monkeypatch, with_partial
):
    context = _context(tmp_path)
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
        "source_scenario_key": "one",
        "scenario_id": "trial-id",
    }
    result_path = tmp_path / "worker-error.json"

    async def fail(runner, *_args, **_kwargs):
        from fi.alk.harness.hosted_scheduler import CallOutcome
        from fi.alk.harness.outbound import ArtifactKind

        partial = None
        if with_partial:
            identifier = await runner._adapter.upload_artifact(
                b"partial transcript", kind=ArtifactKind.TRANSCRIPT
            )
            partial = CallOutcome(
                calls=(),
                turns=1,
                started_at="start",
                ended_at="end",
                duration_ms=1,
                transcript_artifact=identifier,
            )
        raise CallAborted(
            "chat target did not start",
            code="target_agent_failed",
            marker="target_unavailable",
            partial=partial,
        )

    monkeypatch.setattr(chat_call_runner.HostedChatCallRunner, "run", fail)
    monkeypatch.setattr(chat_worker.sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(chat_worker.sys, "argv", ["chat_worker", str(result_path)])

    asyncio.run(chat_worker.main())

    result = json.loads(result_path.read_text())
    if with_partial:
        assert result.pop("partial")["transcript_artifact"] == "0"
        artifact = result.pop("artifacts")[0]
        assert artifact["id"] == "0"
        assert base64.b64decode(artifact["data"]) == b"partial transcript"
    assert result == {
        "error": "chat target did not start",
        "error_code": "target_agent_failed",
        "error_marker": "target_unavailable",
    }


def test_failed_worker_uploads_partial_evidence_before_raising(tmp_path, monkeypatch):
    context = _context(tmp_path)
    adapter = _Adapter()

    async def worker(*_args, **_kwargs):
        return {
            "error": "upstream refused",
            "error_code": "target_agent_failed",
            "partial": {
                "calls": [],
                "turns": 2,
                "started_at": "start",
                "ended_at": "end",
                "duration_ms": 10,
                "transcript_artifact": "0",
                "recording_artifacts": [],
                "messages": [{"role": "assistant", "content": "first answer"}],
            },
            "artifacts": [
                {
                    "id": "0",
                    "kind": "transcript",
                    "data": base64.b64encode(b"partial transcript").decode(),
                },
                {
                    "id": "1",
                    "kind": "trace",
                    "data": base64.b64encode(b"http failure trace").decode(),
                },
            ],
        }

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    with pytest.raises(CallAborted) as failure:
        asyncio.run(
            chat_worker.IsolatedChatCallRunner(adapter, context).run(
                SimpleNamespace(scenario_key="trial", scenario_id="id"),
                EnvironmentRuntime(
                    runtime_id="r",
                    world_index=0,
                    bundle_digest="sha256:" + "a" * 64,
                    state=RuntimeState.READY,
                ),
            )
        )
    assert adapter.uploads == [b"partial transcript", b"http failure trace"]
    assert failure.value.partial.transcript_artifact == "transcript-1"
    assert failure.value.partial.turns == 2


class _Reporter:
    def __init__(self, denied: bool = False) -> None:
        self.denied = denied
        self.checked: list[str] = []
        self.recorded: list[dict] = []

    def check(self, action):
        self.checked.append(action)
        if self.denied:
            from fi.alk.harness.usage import UsageDenied

            raise UsageDenied({"allowed": False, "reason": "plan limit"})

    def record(self, **facts):
        self.recorded.append(facts)


def _ready_runtime() -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={
            "target_http": RuntimeEndpoint(
                capability="target_http", protocol="http", address="http://agent.invalid",
            )
        },
    )


def test_a_text_call_run_in_the_worker_is_checked_and_recorded_with_its_tokens(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from datetime import datetime

    reporter = _Reporter()
    context = replace(_context(tmp_path), usage_reporter=reporter)

    class Wrapper:
        def __init__(self, **_kwargs):
            pass

        async def call(self, _input):
            return AgentResponse(content="Your account is active.")

    async def exchange_with_tokens(*args):
        transcript = await _single_exchange(*args)
        transcript.simulator_input_tokens, transcript.simulator_output_tokens = 11, 7
        return transcript

    async def worker(_module, payload, *, environ, work_directory, stop_requested=None, pass_fds=()):
        result_path = tmp_path / "worker-result.json"
        monkeypatch.setattr(chat_worker.sys, "stdin", io.StringIO(json.dumps(payload)))
        monkeypatch.setattr(chat_worker.sys, "argv", ["chat_worker", str(result_path)])
        await chat_worker.main()
        return json.loads(result_path.read_text())

    monkeypatch.delenv("ALK_SIMULATOR_FUNDING", raising=False)
    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    monkeypatch.setattr(chat_call_runner, "HTTPAgentWrapper", Wrapper)
    monkeypatch.setattr(chat_call_runner, "tool_world", lambda *_args: _ToolWorld())
    monkeypatch.setattr(chat_call_runner, "_drive_conversation", exchange_with_tokens)

    asyncio.run(
        chat_worker.IsolatedChatCallRunner(_Adapter(), context).run(
            SimpleNamespace(scenario_key="trial-key", source_scenario_key="one", scenario_id="id-1"),
            _ready_runtime(),
        )
    )

    assert reporter.checked == ["text_call"]
    [recorded] = reporter.recorded
    assert isinstance(recorded.pop("occurred_at"), datetime)
    assert recorded == {
        "action": "text_call",
        "scenario_key": "trial-key",
        "amount": 18,
        "funding": "platform",
        "outcome": "completed",
        "failure_domain": None,
    }


def test_a_text_call_the_plan_does_not_allow_never_starts_its_worker(tmp_path, monkeypatch):
    from dataclasses import replace

    reporter = _Reporter(denied=True)
    started = []

    async def worker(*_args, **_kwargs):
        started.append(True)
        return {"error": "should not run"}

    monkeypatch.delenv("ALK_SIMULATOR_FUNDING", raising=False)
    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    runner = chat_worker.IsolatedChatCallRunner(
        _Adapter(), replace(_context(tmp_path), usage_reporter=reporter)
    )

    with pytest.raises(CallAborted) as denied:
        asyncio.run(
            runner.run(
                SimpleNamespace(scenario_key="trial-key", source_scenario_key="one", scenario_id="id-1"),
                _ready_runtime(),
            )
        )

    assert reporter.checked == ["text_call"]
    assert denied.value.code == "usage_exhausted" and started == []


def test_an_isolated_chat_call_keeps_the_bundle_s_evidence_seam(tmp_path, monkeypatch):
    from dataclasses import replace

    from fi.alk.harness.bundle_v2 import EvidenceSeam

    class Stop(Exception):
        pass

    seen = {}

    class Runner:
        def __init__(self, _adapter, context):
            seen["worker"] = context.evidence_seam
            raise Stop

    async def worker(_module, payload, *, environ, work_directory, stop_requested=None, pass_fds=()):
        seen["payload"] = payload["evidence_seam"]
        monkeypatch.setattr(chat_worker.sys, "stdin", io.StringIO(json.dumps(payload)))
        monkeypatch.setattr(chat_worker.sys, "argv", ["chat_worker", str(tmp_path / "result.json")])
        await chat_worker.main()

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    monkeypatch.setattr(chat_call_runner, "HostedChatCallRunner", Runner)
    context = replace(_context(tmp_path), evidence_seam=EvidenceSeam.TOOL_TRACE)
    runtime = EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={"target_http": RuntimeEndpoint(capability="target_http", protocol="http", address="http://x")},
    )
    with pytest.raises(Exception):
        asyncio.run(
            chat_worker.IsolatedChatCallRunner(_Adapter(), context).run(
                SimpleNamespace(scenario_key="one", source_scenario_key="one", scenario_id="id-1"), runtime
            )
        )
    assert seen == {"payload": "tool_trace", "worker": EvidenceSeam.TOOL_TRACE}


def _stop_payload(context) -> dict:
    runtime = EnvironmentRuntime(
        runtime_id="runtime-1", world_index=0, bundle_digest="sha256:" + "a" * 64, state=RuntimeState.READY
    )
    return {
        "job": context.job.model_dump(mode="json"),
        "bundle_dir": str(context.bundle_dir),
        "work_directory": str(context.work_directory),
        "source_directory": None,
        "target_secrets": {},
        "attempt_number": 1,
        "runtime": runtime.model_dump(mode="json"),
        "scenario_key": "trial-key",
        "source_scenario_key": "one",
        "scenario_id": "trial-id",
    }


def test_a_stopped_chat_worker_hands_back_the_call_it_sealed(tmp_path, monkeypatch):
    """Inside the worker the person's stop reads true, so the chat runner seals its partial call as a stop."""
    from fi.alk.harness import isolated_process
    from fi.alk.harness.hosted_scheduler import CallCancelled, CallOutcome
    from fi.alk.harness.outbound import ArtifactKind

    context = _context(tmp_path)
    result_path = tmp_path / "stopped.json"

    async def run(runner, *_args, **_kwargs):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            if not runner._context.stop_requested():
                raise
            identifier = await runner._adapter.upload_artifact(b"first two turns", kind=ArtifactKind.TRANSCRIPT)
            partial = CallOutcome(
                calls=(), turns=2, started_at="s", ended_at="e", duration_ms=1, transcript_artifact=identifier,
                stop_reason="user_canceled",
            )
            raise CallCancelled(partial=partial) from None

    monkeypatch.setattr(chat_call_runner.HostedChatCallRunner, "run", run)
    monkeypatch.setattr(chat_worker.sys, "stdin", io.StringIO(json.dumps(_stop_payload(context))))
    monkeypatch.setattr(chat_worker.sys, "argv", ["chat_worker", str(result_path)])
    monkeypatch.setattr(isolated_process, "_stop_requested", False)

    async def stopped_mid_call():
        task = asyncio.create_task(chat_worker.main())
        await asyncio.sleep(0.2)
        isolated_process._stop_requested = True
        task.cancel()
        await task

    asyncio.run(stopped_mid_call())
    result = json.loads(result_path.read_text())
    assert result["error_code"] == "user_canceled"
    assert result["partial"]["stop_reason"] == "user_canceled"
    assert base64.b64decode(result["artifacts"][0]["data"]) == b"first two turns"


def test_the_parent_raises_the_stop_with_the_worker_s_sealed_call(tmp_path, monkeypatch):
    from fi.alk.harness.hosted_scheduler import CallCancelled

    context = _context(tmp_path)
    adapter = _Adapter()
    seen = {}

    async def worker(*_args, stop_requested=None, **_kwargs):
        seen["stop_requested"] = stop_requested
        return {
            "error": "user_canceled",
            "error_code": "user_canceled",
            "partial": {
                "calls": [], "turns": 2, "started_at": "s", "ended_at": "e", "duration_ms": 1,
                "transcript_artifact": "0", "recording_artifacts": [], "stop_reason": "user_canceled",
            },
            "artifacts": [{"id": "0", "kind": "transcript", "data": base64.b64encode(b"two turns").decode()}],
        }

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    runner = chat_worker.IsolatedChatCallRunner(adapter, context)
    # The scheduler gives this runner the seal bound before it cuts the call.
    assert runner.stops_at_turn_boundary is True
    with pytest.raises(CallCancelled) as stopped:
        asyncio.run(runner.run(SimpleNamespace(scenario_key="trial", scenario_id="id"), _ready_runtime()))
    assert seen["stop_requested"] is context.stop_requested
    assert adapter.uploads == [b"two turns"]
    assert stopped.value.partial.transcript_artifact == "transcript-1"
    assert stopped.value.partial.stop_reason == "user_canceled"


@pytest.mark.parametrize("stop", [True, False])
def test_a_worker_lost_on_the_stop_is_the_stop(tmp_path, monkeypatch, stop):
    from dataclasses import replace

    from fi.alk.harness.hosted_scheduler import CallCancelled
    from fi.alk.harness.isolated_process import IsolatedWorkerError

    context = replace(_context(tmp_path), stop_requested=lambda: stop)

    async def worker(*_args, **_kwargs):
        raise IsolatedWorkerError("isolated worker exited with status 1")

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    runner = chat_worker.IsolatedChatCallRunner(_Adapter(), context)
    expected = CallCancelled if stop else IsolatedWorkerError
    with pytest.raises(expected):
        asyncio.run(runner.run(SimpleNamespace(scenario_key="trial", scenario_id="id"), _ready_runtime()))


def test_a_stop_keeps_the_sealed_call_when_delivering_it_outlasts_the_seal_bound(tmp_path, monkeypatch):
    """The live path: the scheduler's stop reaches a real chat worker process as its stop signal, the worker seals the
    call it cut, and the parent delivers it through a platform slower than the seal bound. The call is kept."""
    from dataclasses import replace
    from pathlib import Path

    from test_hosted_scheduler import FakeOutbound, FakeScenario, FakeSubGoal, FakeWorldFactory, _pool

    from fi.alk.harness import hosted_scheduler as hs
    from fi.alk.harness import isolated_process

    ready = tmp_path / "in-the-call"
    (tmp_path / "stopped_chat_worker.py").write_text(
        "import asyncio\n"
        "from pathlib import Path\n"
        "from fi.alk.harness import chat_call_runner, chat_worker, isolated_process\n"
        "from fi.alk.harness.hosted_scheduler import CallCancelled, CallOutcome\n"
        "from fi.alk.harness.outbound import ArtifactKind\n"
        "async def run(runner, *_args, **_kwargs):\n"
        f" Path({str(ready)!r}).touch()\n"
        " try:\n"
        "  await asyncio.sleep(60)\n"
        " except asyncio.CancelledError:\n"
        "  if not runner._context.stop_requested():\n"
        "   raise\n"
        "  kept = await runner._adapter.upload_artifact(b'the customer asked', kind=ArtifactKind.TRANSCRIPT)\n"
        "  partial = CallOutcome(calls=(), turns=1, started_at='s', ended_at='e', duration_ms=1,\n"
        "   transcript_artifact=kept, stop_reason='user_canceled')\n"
        "  raise CallCancelled(partial=partial) from None\n"
        "chat_call_runner.HostedChatCallRunner.run = run\n"
        "isolated_process.run_worker(chat_worker.main, cancel_on_stop=True)\n"
    )
    src = str(Path(isolated_process.__file__).resolve().parents[3])
    real_worker = isolated_process.run_json_worker

    async def worker(_module, payload, *, environ, **kwargs):
        environ = {**environ, "PYTHONPATH": os.pathsep.join([str(tmp_path), src])}
        return await real_worker("stopped_chat_worker", payload, environ=environ, **kwargs)

    class _SlowPlatform(_Adapter):
        async def upload_artifact(self, data: bytes, **kwargs):
            # The platform is busy with the stop itself: delivering the sealed call outlasts the seal bound.
            await asyncio.sleep(1.5)
            return await super().upload_artifact(data, **kwargs)

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    monkeypatch.setenv(hs.CANCEL_SEAL_ENV, "0.5")
    stopped = ready.exists
    adapter = _SlowPlatform()
    runner = chat_worker.IsolatedChatCallRunner(adapter, replace(_context(tmp_path), stop_requested=stopped))

    async def scenario():
        outbound = FakeOutbound()
        pool, _ = _pool(1, outbound=outbound)
        await pool.start()
        scheduler = hs.HostedScheduler(
            pool=pool,
            world_factory=FakeWorldFactory(),
            call_runner=runner,
            outbound=outbound,
            job_seed=1,
            cancel_requested=stopped,
            judge=lambda *_args, **_kwargs: (True, ""),
        )
        result = await scheduler.run([FakeScenario("s1", "id-1", sub_goals=[FakeSubGoal("g", lambda w, c: None)])])
        await pool.close()
        return result

    (receipt,) = asyncio.run(scenario()).receipts
    assert receipt.failure.code == "user_canceled"
    assert receipt.call is not None
    assert receipt.call.transcript_artifact == "transcript-1"
    assert receipt.call.stop_reason == "user_canceled"
    assert adapter.uploads == [b"the customer asked"]


def test_the_transcript_names_the_end_state_by_the_id_it_was_stored_under():
    import json as _json

    from fi.alk.harness.chat_worker import _with_stored_end_state

    transcript = _json.dumps({"messages": [], "world_actors": {"end_state_artifact": "0", "records": {}}}).encode()
    stored = _json.loads(_with_stored_end_state(transcript, {"0": "sha256:" + "a" * 64}))
    assert stored["world_actors"]["end_state_artifact"] == "sha256:" + "a" * 64
    # An end state that was not stored is not named at all, and a transcript without one is left as it was.
    assert "end_state_artifact" not in _json.loads(_with_stored_end_state(transcript, {}))["world_actors"]
    plain = _json.dumps({"messages": []}).encode()
    assert _with_stored_end_state(plain, {}) == plain


@pytest.mark.parametrize("raises", [False, True])
def test_a_screenshot_the_platform_did_not_keep_is_said_and_never_named(tmp_path, monkeypatch, raises):
    context = _context(tmp_path)

    class Refuses:
        def __init__(self) -> None:
            self.kinds: list[str] = []

        async def upload_artifact(self, data, *, kind, **_kwargs):
            self.kinds.append(kind.value)
            if kind.value == "screenshot":
                if raises:
                    raise RuntimeError("upload failed")
                return None
            return f"stored-{len(self.kinds)}"

    async def worker(module, payload, **_kwargs):
        return {
            "outcome": {
                "calls": [], "turns": 1, "started_at": None, "ended_at": None, "duration_ms": 1,
                "transcript_artifact": "0", "recording_artifacts": [],
            },
            "artifacts": [
                {"id": "0", "kind": "transcript", "data": base64.b64encode(b"t").decode()},
                {"id": "1", "kind": "screenshot", "data": base64.b64encode(b"jpeg").decode()},
            ],
        }

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    adapter = Refuses()
    runtime = EnvironmentRuntime(
        runtime_id="runtime-0", world_index=0, bundle_digest="sha256:" + "a" * 64, state=RuntimeState.READY
    )
    outcome = asyncio.run(
        chat_worker.IsolatedChatCallRunner(adapter, context).run(
            SimpleNamespace(scenario_key="one", source_scenario_key="one", scenario_id="id"), runtime
        )
    )
    assert adapter.kinds == ["transcript", "screenshot"]
    assert outcome.recording_artifacts == ()
    assert [one["code"] for one in outcome.notices] == ["screenshots_not_kept"]


def test_the_worker_keeps_screenshots_within_what_its_result_can_carry(monkeypatch):
    # The worker's result is one file with a size limit: a screenshot that would push it over is not kept, and the
    # trial goes on.
    monkeypatch.setattr(chat_worker, "MAX_RESULT_BYTES", 400)
    collector = chat_worker._ArtifactCollector()
    kinds = chat_worker.ArtifactKind

    async def run():
        assert await collector.upload_artifact(b"t" * 100, kind=kinds.TRANSCRIPT) == "0"
        assert await collector.upload_artifact(b"j" * 100, kind=kinds.SCREENSHOT) == "1"
        assert await collector.upload_artifact(b"j" * 100, kind=kinds.SCREENSHOT) is None

    asyncio.run(run())
    assert [one["kind"] for one in collector.artifacts] == ["transcript", "screenshot"]
