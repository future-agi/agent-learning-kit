"""Generated tool handlers, and the agent code they import, run in a child of their own.

Under per-user isolation that child is a declared user, and neither the chat worker nor the
authoring probe ever imports the agent's code as the harness.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import pwd
import socket
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fi.alk.harness import chat_call_runner as chat
from fi.alk.harness import chat_worker, handler_child, privilege
from fi.alk.harness.call_runner import CallRunnerContext
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.handler_child import (
    ChildWorld,
    HandlerChannel,
    HandlerChild,
    start_handler_child,
)
from fi.alk.harness.hosted_scheduler import CallAborted
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.privilege import IsolationMode
from fi.alk.harness.process_runtime import (
    EnvironmentRuntime,
    RuntimeEndpoint,
    RuntimeState,
)
from fi.alk.harness.world.runtime import Call
from fi.simulate.runtime.spec import RuntimeIsolation

_DSN = "postgresql://harness:secret@localhost:15432/w0"
_WORLD_DSN = "postgresql://alk_w0:derived@localhost:15432/w0"
_AGENT_MODULE = "agent_tools_th_handler_child"


@pytest.fixture(autouse=True)
def _fixed_user_after_each_test(monkeypatch):
    monkeypatch.setattr(privilege, "_mode", IsolationMode.FIXED_USER)


@pytest.fixture
def per_user(monkeypatch):
    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)


def _bundle(tmp_path: Path) -> tuple[CallRunnerContext, Path]:
    bundle = tmp_path / "bundle"
    (bundle / "handlers").mkdir(parents=True)
    (bundle / "contract.json").write_text(
        json.dumps(
            {
                "agent": "chat-agent",
                "one_liner": "Looks up accounts",
                "modality": "chat",
                "tools": [{"name": "lookup_account", "args": ["account_id"]}],
            }
        )
    )
    (bundle / "handlers" / "lookup_account.py").write_text(
        f"from {_AGENT_MODULE} import lookup_account\n\n"
        "def handle(args, db):\n"
        "    return lookup_account(**args)\n"
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / f"{_AGENT_MODULE}.py").write_text(
        "def lookup_account(account_id):\n    return {'account_id': account_id}\n"
    )
    job = HarnessJob.model_validate(
        {
            "job_id": "job-1",
            "run_id": "run-1",
            "execution": "hosted",
            "source": {"kind": "archive", "archive_artifact_id": "source-1"},
            "agent": {"connector": "http"},
            "scenario_count": 1,
            "runtime": {"isolation": RuntimeIsolation.DEDICATED_VM.value},
        }
    )
    context = CallRunnerContext(
        job=job,
        bundle_dir=bundle,
        work_directory=tmp_path / "work",
        evidence_seam=None,
        target_provider_secret_values={"TARGET_API_KEY": "target-secret"},
        attempt_number=1,
        source_directory=source,
    )
    return context, source


def _runtime(tool_address: str | None = _WORLD_DSN) -> EnvironmentRuntime:
    return EnvironmentRuntime(
        runtime_id="runtime-1",
        world_index=0,
        bundle_digest="sha256:" + "a" * 64,
        state=RuntimeState.READY,
        endpoints={
            "world_db": RuntimeEndpoint(
                capability="world_db",
                protocol="postgres",
                address=_DSN,
                tool_address=tool_address,
            )
        },
    )


class _FakeProcess:
    pid = 424242

    def __init__(self) -> None:
        self.stdin = io.BytesIO()
        self.stdin.close = lambda: None  # keep what was written readable


@pytest.fixture
def spawned(monkeypatch) -> list[dict[str, Any]]:
    """Every handler child the code starts, as Popen saw it, with nothing real spawned."""
    calls: list[dict[str, Any]] = []

    def popen(argv, **kwargs):
        process = _FakeProcess()
        calls.append({"argv": argv, "kwargs": kwargs, "process": process})
        return process

    monkeypatch.setattr(handler_child.subprocess, "Popen", popen)
    monkeypatch.setattr(handler_child.os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(handler_child, "_stop", lambda process: None)
    monkeypatch.setattr(
        handler_child,
        "default_user_resolver",
        lambda name: SimpleNamespace(pw_uid=4444, pw_gid=5555) if name == "svc-agent" else None,
    )
    return calls


def _run_chat(context, monkeypatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    async def worker(module, payload, **kwargs):
        seen.update(module=module, payload=payload, kwargs=kwargs)
        return {"error": "stopped here"}

    monkeypatch.setattr(chat_worker, "run_json_worker", worker)
    monkeypatch.setattr(chat_worker, "_check_text_usage", _no_check)
    with pytest.raises(CallAborted):
        asyncio.run(
            chat_worker.IsolatedChatCallRunner(None, context).run(
                SimpleNamespace(scenario_key="one", scenario_id="s-1"), _runtime()
            )
        )
    return seen


async def _no_check(*_args, **_kwargs) -> None:
    return None


def test_under_isolation_the_handlers_run_as_their_user_and_the_worker_never_gets_the_source(
    per_user, tmp_path, monkeypatch, spawned
):
    monkeypatch.setenv("AGENTCC_API_KEY", "model-key")
    context, source = _bundle(tmp_path)

    seen = _run_chat(context, monkeypatch)

    assert len(spawned) == 1
    child = spawned[0]
    kwargs = child["kwargs"]
    # The declared user, with no groups and no capabilities to inherit.
    assert (kwargs["user"], kwargs["group"]) == (4444, 5555)
    assert kwargs["extra_groups"] == [] and kwargs["umask"] == 0o022
    assert kwargs["start_new_session"] is True
    # No model credentials and no target secrets reach the child; the DSN arrives on stdin.
    assert set(kwargs["env"]) <= {"PATH", "HOME", "TMPDIR", "LANG", "PYTHONPATH"}
    init = json.loads(child["process"].stdin.getvalue())
    # The world's own role, never the superuser, and only on stdin.
    assert init["dsn"] == _WORLD_DSN
    assert _WORLD_DSN not in json.dumps(kwargs["env"]) + str(child["argv"])
    assert set(init) == {"handlers", "tools", "refusal_signature", "dsn", "import_root"}
    assert "lookup_account" in init["handlers"] and init["import_root"] == str(source)
    # The worker gets the child's socket and the mode, and never the source.
    payload = seen["payload"]
    assert payload["process_isolation"] == "per_user"
    assert payload["source_directory"] is None
    assert seen["kwargs"]["pass_fds"] == (payload["handler_fd"],)


def test_with_one_fixed_user_the_worker_runs_the_handlers_as_before(
    tmp_path, monkeypatch, spawned
):
    context, source = _bundle(tmp_path)

    seen = _run_chat(context, monkeypatch)

    assert spawned == []
    assert seen["payload"]["source_directory"] == str(source)
    assert seen["payload"]["handler_fd"] is None
    assert "pass_fds" not in seen["kwargs"]


def test_a_worker_under_isolation_refuses_the_source(tmp_path, monkeypatch):
    result_path = tmp_path / "result.json"
    payload = {"process_isolation": "per_user", "source_directory": str(tmp_path)}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["chat_worker", str(result_path)])

    asyncio.run(chat_worker.main())

    result = json.loads(result_path.read_text())
    assert result["error"].startswith("chat_worker_refused:")


def test_a_sandbox_without_the_handlers_user_fails_closed(per_user, tmp_path, monkeypatch, spawned):
    monkeypatch.setattr(handler_child, "default_user_resolver", lambda name: None)
    context, source = _bundle(tmp_path)

    with pytest.raises(CallAborted, match="handler_user_unavailable") as raised:
        start_handler_child(
            handlers={},
            tools=[],
            refusal_signature="",
            dsn=_DSN,
            source=source,
            work_directory=context.work_directory,
            world_index=0,
        )

    assert raised.value.code == "call_failed"
    assert spawned == []


def test_a_world_without_its_own_role_starts_no_handler_child(
    per_user, tmp_path, monkeypatch, spawned
):
    context, _source = _bundle(tmp_path)

    with pytest.raises(CallAborted, match="handler_world_unscoped"):
        chat.handler_child_for(context, _runtime(tool_address=None))

    assert spawned == []


class _Store:
    key = "fake"

    def __init__(self, _dsn: str) -> None:
        pass

    def start(self) -> None:
        return None

    def state(self, only=None):
        return {}


def test_a_world_with_a_handler_channel_never_imports_the_agent_code(tmp_path, monkeypatch):
    monkeypatch.setattr(chat, "_HostedToolStore", _Store)
    context, source = _bundle(tmp_path)
    contract = AgentContract.model_validate_json(
        (context.bundle_dir / "contract.json").read_text()
    )

    class Channel:
        def call(self, name, arguments):
            return Call(name=name, arguments=dict(arguments), result={"from": "child"})

    world = chat._tool_world(context.bundle_dir, contract, _runtime(), source, Channel())
    call = world.call("lookup_account", {"account_id": "A-1"})

    assert isinstance(world, ChildWorld)
    assert call.result == {"from": "child"} and world.calls == [call]
    assert _AGENT_MODULE not in sys.modules
    assert str(source) not in sys.path
    with pytest.raises(RuntimeError):
        world.reach(str(source))


def test_the_authoring_probe_runs_the_handlers_in_the_child_under_isolation(
    per_user, tmp_path, monkeypatch
):
    from fi.alk.harness.chat_readiness import probe_chat_workflow

    context, source = _bundle(tmp_path)
    parent_end, child_end = socket.socketpair()
    closed: list[bool] = []
    child = HandlerChild(process=SimpleNamespace(pid=1), sock=parent_end)
    child.close = lambda: closed.append(True)  # type: ignore[method-assign]
    monkeypatch.setattr(chat, "handler_child_for", lambda ctx, runtime: child)
    seen: dict[str, Any] = {}

    class Runner:
        def __init__(self, artifacts, ctx):
            seen["context"] = ctx
            self._contract = AgentContract.model_validate_json(
                (context.bundle_dir / "contract.json").read_text()
            )

        async def run(self, scenario, runtime):
            raise CallAborted("stopped here")

    monkeypatch.setattr(chat, "HostedChatCallRunner", Runner)
    try:
        with pytest.raises(Exception):
            asyncio.run(
                probe_chat_workflow(
                    job=SimpleNamespace(metadata={}, require_tool_workflow=False),
                    source=source,
                    bundle=context.bundle_dir,
                    authoring=tmp_path,
                    runtime=_runtime(),
                    scenario=SimpleNamespace(scenario_key="one", scenario_id="s-1"),
                    candidate_hash="candidate",
                    bundle_digest="bundle",
                    secret_values={},
                    redaction_values={},
                    timeout=5,
                )
            )
    finally:
        child_end.close()
        parent_end.close()

    assert isinstance(seen["context"].handler_channel, HandlerChannel)
    assert closed == [True]
    assert _AGENT_MODULE not in sys.modules


# --- the real boundary (Linux, as root, with a Postgres to talk to: the hosted proof) -----------


@pytest.mark.skipif(
    sys.platform != "linux"
    or os.geteuid() != 0
    or not os.environ.get("ALK_TEST_POSTGRES_DSN"),
    reason="needs Linux, the capabilities to change users and a Postgres",
)
def test_the_handler_child_runs_as_its_user_with_nothing_of_the_harness(per_user):
    try:
        user = pwd.getpwnam(handler_child.HANDLER_USER.value)
    except KeyError:
        pytest.skip("the image's handler user is not present")
    # Not pytest's tmp_path: its parents are root's own, so the child could not reach its scratch.
    tmp_path = Path(tempfile.mkdtemp(dir="/tmp"))
    held = tmp_path / "held"
    held.mkdir(mode=0o700)
    (held / "case.json").write_text("CANARY")
    (held / "case.json").chmod(0o600)
    tmp_path.chmod(0o711)
    source = tmp_path / "source"
    source.mkdir()
    source.chmod(0o700)
    (source / f"{_AGENT_MODULE}.py").write_text("IMPORTED = True\n")
    handler = (
        f"import {_AGENT_MODULE}, os\n\n"
        "def handle(args, db):\n"
        "    status = open('/proc/self/status').read()\n"
        "    caps = [line for line in status.splitlines() if line.startswith(('CapEff', 'Groups'))]\n"
        "    try:\n"
        f"        held = open({str(held / 'case.json')!r}).read()\n"
        "    except OSError as exc:\n"
        "        held = type(exc).__name__\n"
        f"    return {{'uid': os.getuid(), 'caps': caps, 'held': held, 'imported': {_AGENT_MODULE}.IMPORTED}}\n"
    )
    child = start_handler_child(
        handlers={"probe": handler},
        tools=[{"name": "probe"}],
        refusal_signature="",
        dsn=os.environ["ALK_TEST_POSTGRES_DSN"],
        source=source,
        work_directory=tmp_path / "work",
        world_index=0,
    )
    try:
        call = HandlerChannel(child.sock).call("probe", {})
    finally:
        child.close()

    assert call.ok, call.error
    assert call.result["uid"] == user.pw_uid
    assert "CapEff:\t0000000000000000" in call.result["caps"]
    assert ["Groups:"] in [line.split() for line in call.result["caps"]]
    assert call.result["held"] == "PermissionError"
    assert call.result["imported"] is True
    assert _AGENT_MODULE not in sys.modules
