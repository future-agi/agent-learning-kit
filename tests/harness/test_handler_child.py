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
import subprocess
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

    def __init__(self, directory: Path) -> None:
        self.init_file = directory / "init.json"
        self.stdin = self.init_file.open("wb")


@pytest.fixture
def spawned(monkeypatch, tmp_path) -> list[dict[str, Any]]:
    """Every handler child the code starts, as Popen saw it, with nothing real spawned."""
    calls: list[dict[str, Any]] = []

    def popen(argv, **kwargs):
        process = _FakeProcess(tmp_path)
        calls.append({"argv": argv, "kwargs": kwargs, "process": process})
        return process

    monkeypatch.setattr(handler_child.subprocess, "Popen", popen)
    monkeypatch.setattr(handler_child.os, "chown", lambda *args, **kwargs: None)
    monkeypatch.setattr(handler_child, "_stop", lambda process: None)
    monkeypatch.setattr(
        handler_child,
        "default_user_resolver",
        lambda name: (
            SimpleNamespace(pw_uid=4444, pw_gid=5555) if name == "svc-agent" else None
        ),
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
    init = json.loads(child["process"].init_file.read_bytes())
    # The world's own role, never the superuser, and only on stdin.
    assert init["dsn"] == _WORLD_DSN
    assert _WORLD_DSN not in json.dumps(kwargs["env"]) + str(child["argv"])
    # A build without a folder sends the child what it always did.
    assert set(init) == {"handlers", "tools", "refusal_signature", "dsn", "import_root"}
    assert "lookup_account" in init["handlers"] and init["import_root"] == str(source)
    # The worker gets the child's socket and the mode, and never the source.
    payload = seen["payload"]
    assert payload["process_isolation"] == "per_user"
    assert payload["source_directory"] is None
    assert seen["kwargs"]["pass_fds"] == (payload["handler_fd"],)
    # What the child prints goes to a log beside its scratch, where only the harness writes;
    # never into the harness's own stream.
    scratch = Path(kwargs["cwd"])
    assert scratch.name == "_handlers"
    assert isinstance(kwargs["stderr"], int) and kwargs["stdout"] == kwargs["stderr"]
    assert (scratch.parent / "_handlers.log").is_file()


def test_with_one_fixed_user_the_worker_runs_the_handlers_as_before(
    tmp_path, monkeypatch, spawned
):
    context, source = _bundle(tmp_path)

    seen = _run_chat(context, monkeypatch)

    assert spawned == []
    assert seen["payload"]["source_directory"] == str(source)
    assert seen["payload"]["handler_fd"] is None
    assert seen["kwargs"]["pass_fds"] == ()


@pytest.mark.parametrize(
    ("given", "refused"),
    [
        ({"source_directory": "/work/source", "handler_fd": 7}, True),
        ({"source_directory": None, "handler_fd": None}, True),
        (
            {
                "source_directory": None,
                "handler_fd": None,
                "runtime": {"metadata": {"observation_mode": "native"}},
            },
            False,
        ),
    ],
    ids=["a source", "no handler child", "a native world runs no handlers"],
)
def test_a_worker_under_isolation_never_runs_the_handlers_itself(
    tmp_path, monkeypatch, given, refused
):
    result_path = tmp_path / "result.json"
    payload = {"process_isolation": "per_user", "runtime": {"metadata": {}}, **given}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.setattr(sys, "argv", ["chat_worker", str(result_path)])

    asyncio.run(chat_worker.main())

    result = json.loads(result_path.read_text())
    assert result["error"].startswith("chat_worker_refused:") is refused


def test_arguments_too_large_to_send_fail_the_call_and_not_the_world():
    parent_end, child_end = socket.socketpair()
    try:
        call = HandlerChannel(parent_end).call(
            "lookup_account", {"blob": "x" * handler_child.MAX_MESSAGE_BYTES}
        )

        assert (call.ok, call.error) == (
            False,
            "tool arguments exceed the handler message limit",
        )
        child_end.setblocking(False)
        with pytest.raises(BlockingIOError):
            child_end.recv(1)  # nothing was sent
    finally:
        parent_end.close()
        child_end.close()


@pytest.mark.parametrize(
    "reply", [b"[" * 200000, b"not json", b"[1]"], ids=["deep-nesting", "not-json", "not-an-object"]
)
def test_a_reply_the_child_breaks_aborts_the_call_as_the_world_s_failure(reply):
    # The child runs the agent's code, which can write anything on its end of the socket.
    import threading

    from fi.alk.harness.isolated_process import send_message

    parent_end, child_end = socket.socketpair()
    child_end.settimeout(10)
    writer = threading.Thread(target=send_message, args=(child_end, reply), daemon=True)
    writer.start()
    try:
        with pytest.raises(CallAborted, match="handler_child_failed") as aborted:
            HandlerChannel(parent_end).call("lookup_account", {})

        assert aborted.value.code == "call_failed"
    finally:
        parent_end.close()
        writer.join(timeout=10)
        child_end.close()


def test_a_sandbox_without_the_handlers_user_fails_closed(
    per_user, tmp_path, monkeypatch, spawned
):
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


def test_the_handler_child_starts_in_a_leaf_of_its_own_and_the_leaf_goes_with_it(
    per_user, tmp_path, monkeypatch, spawned
):
    # The child imports the agent's code: what that code starts must stop with the child.
    leaf = SimpleNamespace(closed=0)
    leaf.close = lambda: setattr(leaf, "closed", leaf.closed + 1)
    contained = []

    def contained_popen(argv, **kwargs):
        contained.append(kwargs)
        return _FakeProcess(tmp_path), leaf

    monkeypatch.setattr(handler_child.containment, "popen", contained_popen)
    context, source = _bundle(tmp_path)

    child = start_handler_child(
        handlers={},
        tools=[],
        refusal_signature="",
        dsn=_DSN,
        source=source,
        work_directory=context.work_directory,
        world_index=0,
    )
    child.close()

    assert len(contained) == 1 and spawned == []  # no spawn around the containment
    assert child.leaf is leaf and leaf.closed == 1


def test_initialization_preserves_partial_writes(tmp_path, monkeypatch, spawned):
    write = os.write
    monkeypatch.setattr(handler_child.os, "write", lambda fd, data: write(fd, data[:7]))
    child = start_handler_child(
        handlers={"probe": "a" * 256}, tools=[], refusal_signature="", dsn=_DSN,
        source=None, work_directory=tmp_path, world_index=0,
    )
    try:
        assert json.loads(spawned[0]["process"].init_file.read_bytes())["handlers"] == {
            "probe": "a" * 256
        }
    finally:
        child.close()


@pytest.mark.parametrize("reads_nothing", [True, False], ids=["deadline", "pipe-close"])
def test_initialization_failure_releases_resources(tmp_path, monkeypatch, reads_nothing):
    monkeypatch.setattr(handler_child, "default_user_resolver", lambda _: pwd.getpwuid(os.getuid()))
    monkeypatch.setattr(handler_child, "INIT_WRITE_TIMEOUT_SECONDS", 0.25, raising=False)
    ends = socket.socketpair()
    monkeypatch.setattr(handler_child.socket, "socketpair", lambda: ends)
    leaf_closed = []
    leaf = SimpleNamespace(close=lambda: leaf_closed.append(True))
    # Even on the old code, the reader exits on its own rather than hanging the suite.
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(2)" if reads_nothing else "pass"],
        stdin=subprocess.PIPE, start_new_session=True,
    )
    if not reads_nothing:
        child.wait(timeout=5)
    monkeypatch.setattr(handler_child.containment, "popen", lambda *a, **kw: (child, leaf))
    try:
        with pytest.raises(TimeoutError if reads_nothing else BrokenPipeError):
            start_handler_child(
                handlers={"probe": "a" * (2 * 1024 * 1024)}, tools=[], refusal_signature="",
                dsn=_DSN, source=None, work_directory=tmp_path, world_index=0,
            )
        assert child.returncode is not None
        assert child.stdin.closed
        assert all(end.fileno() == -1 for end in ends)
        assert leaf_closed == [True]
    finally:
        child.kill()
        child.wait()
        child.stdin.close()
        for end in ends:
            end.close()


def test_a_world_without_its_own_role_starts_no_handler_child(
    per_user, tmp_path, monkeypatch, spawned
):
    context, _source = _bundle(tmp_path)

    with pytest.raises(CallAborted, match="handler_world_unscoped"):
        chat.handler_child_for(context, _runtime(tool_address=None))

    assert spawned == []


class _Store:
    key = "fake"
    dsns: list[str] = []

    def __init__(self, dsn: str) -> None:
        _Store.dsns.append(dsn)

    def start(self) -> None:
        return None

    def state(self, only=None):
        return {}


def test_a_world_with_a_handler_channel_never_imports_the_agent_code(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(chat, "_HostedToolStore", _Store)
    from fi.alk.harness import privilege

    monkeypatch.setattr(privilege, "isolation_mode", lambda: privilege.IsolationMode.PER_USER)
    context, source = _bundle(tmp_path)
    contract = AgentContract.model_validate_json(
        (context.bundle_dir / "contract.json").read_text()
    )

    class Channel:
        def call(self, name, arguments):
            return Call(name=name, arguments=dict(arguments), result={"from": "child"})

    world = chat.tool_world(
        context.bundle_dir, contract, _runtime(), source, Channel()
    )
    call = world.call("lookup_account", {"account_id": "A-1"})

    assert isinstance(world, ChildWorld)
    assert call.result == {"from": "child"} and world.calls == [call]
    # Checks read the rows as the world's own role: nothing of the world's runs with the harness's rights.
    assert _Store.dsns[-1].dsn() == _WORLD_DSN
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
    seen: dict[str, Any] = {}

    def handler_child_for(ctx, runtime):
        seen["child_work"] = ctx.work_directory
        return child

    monkeypatch.setattr(chat, "handler_child_for", handler_child_for)

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
                    work_directory=tmp_path / "work",
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
    # The child's scratch goes with the provisioned worlds, which its user can reach; the
    # probe's own folder is the harness's alone.
    assert seen["child_work"] == tmp_path / "work"
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


def test_the_child_puts_the_agents_folder_on_the_path_as_the_worker_does(monkeypatch):
    # A folder build imports its own modules by flat name; the child reaches the folder too.
    reached: list[tuple[str, str]] = []
    monkeypatch.setattr(
        handler_child.GeneratedWorld,
        "reach",
        lambda self, root, subfolder="": reached.append((root, subfolder)),
    )
    monkeypatch.setattr(handler_child, "serve", lambda sock, world: None)
    monkeypatch.setattr(
        chat, "_HostedToolStore", lambda dsn: SimpleNamespace(key="store", start=lambda: None)
    )
    parent, child = socket.socketpair()
    try:
        monkeypatch.setattr(sys, "argv", ["handler_child", str(child.fileno())])
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps({"dsn": "x", "import_root": "/src", "import_subfolder": "agents/a"})
            ),
        )
        handler_child.main()
    finally:
        parent.close()

    assert reached == [("/src", "agents/a")]


def test_under_isolation_a_folder_build_s_handler_imports_its_sibling_by_flat_name(
    per_user, tmp_path, monkeypatch, spawned
):
    # Both halves at once: the conversation sends the child the contract's folder, and the
    # child, fed what was sent, runs a handler whose module imports a sibling by flat name.
    import threading

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "modules", dict(sys.modules))
    context, source = _bundle(tmp_path)
    folder = source / "agents" / "a"
    folder.mkdir(parents=True)
    (folder / "flat_tools_th_handler_child.py").write_text(
        "import flat_sibling_th_handler_child\n\n"
        "def lookup_account(account_id):\n"
        "    return {'account_id': account_id, 'from': flat_sibling_th_handler_child.MARK}\n"
    )
    (folder / "flat_sibling_th_handler_child.py").write_text("MARK = 'sibling'\n")
    (context.bundle_dir / "contract.json").write_text(
        json.dumps(
            {
                "agent": "chat-agent",
                "one_liner": "Looks up accounts",
                "modality": "chat",
                "tools": [{"name": "lookup_account", "args": ["account_id"]}],
                "runtime": {
                    "language": "python",
                    "workdir": "agents/a",
                    "source_subfolder": "agents/a",
                },
            }
        )
    )
    (context.bundle_dir / "handlers" / "lookup_account.py").write_text(
        "from flat_tools_th_handler_child import lookup_account\n\n"
        "def handle(args, db):\n"
        "    return lookup_account(**args)\n"
    )
    monkeypatch.setenv("AGENTCC_API_KEY", "model-key")

    _run_chat(context, monkeypatch)

    init = json.loads(spawned[0]["process"].init_file.read_bytes())
    assert init["import_subfolder"] == "agents/a"
    # The child, as the spawned process runs it (the switch to its user aside), fed that init.
    monkeypatch.setattr(
        chat,
        "_HostedToolStore",
        lambda dsn: SimpleNamespace(key="store", start=lambda: None, stop=lambda: None),
    )
    parent, child = socket.socketpair()
    monkeypatch.setattr(sys, "argv", ["handler_child", str(child.fileno())])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(init)))
    thread = threading.Thread(target=handler_child.main, daemon=True)
    thread.start()
    try:
        call = HandlerChannel(parent).call("lookup_account", {"account_id": "7"})
    finally:
        parent.close()
        thread.join(10)

    assert call.ok, call.error
    assert call.result == {"account_id": "7", "from": "sibling"}

