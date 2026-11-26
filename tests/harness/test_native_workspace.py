import asyncio
import json
import os
import random
import socket
import subprocess
import sys
import threading

import pytest

from fi.alk.harness import isolated_process
from fi.alk.harness.world.handle import HostedWorld
from fi.alk.harness.world.errors import (
    WorldUsageError,
    WorldReadOnly,
    WorldStateTooLarge,
)
from fi.alk.harness.world.workspace_channel import WorkspaceClient
import test_observation_http_sessions as sessions

phase = sessions.phase


class Store:
    def dsn(self):
        return "postgresql://unused"

    def freeze(self):
        return "baseline"

    def restore(self, value):
        assert value == "baseline"


def test_workspace_is_created_before_real_source_build(tmp_path):
    from fi.alk.harness.bundle_v2 import SourceProcess
    from fi.alk.harness.process_runtime import build_process_tree

    source = tmp_path / "source"
    source.mkdir()
    process = SourceProcess(
        name="agent", working_directory=".", user="svc-agent",
        workspaces={"project": "workspace/project"},
        build_commands=[[sys.executable, "-c",
                         "from pathlib import Path; Path('workspace/project/built.txt').write_text('built')"]],
        run_command=[sys.executable, "-c", "pass"],
    )
    built = build_process_tree(process, source_root=source, build_root=tmp_path / "build")
    assert (built / "workspace/project/built.txt").read_text() == "built"
    assert not (source / "workspace").exists()


def world(root):
    root.mkdir(exist_ok=True)
    return HostedWorld(
        Store(),
        0,
        random.Random(0),
        {},
        workspace_roots={"project": root},
        _validate_baseline=False,
    )


def test_native_child_observes_fixture_and_checkpoint_restores_exact_files(tmp_path):
    w = world(tmp_path / "one")
    other = world(tmp_path / "two")
    w.write_file("project", "src/math.py", "return a - b\n")
    checkpoint = w.checkpoint()
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; p=Path('src/math.py'); assert p.read_text()=='return a - b\\n'; p.write_text('return a + b\\n'); Path('extra').write_text('new')",
        ],
        cwd=tmp_path / "one",
        check=True,
    )
    assert w.read_only().read_file("project", "src/math.py") == "return a + b\n"
    with pytest.raises(WorldUsageError):
        other.read_file("project", "src/math.py")
    w.revert(checkpoint)
    assert w.read_file("project", "src/math.py") == "return a - b\n"
    assert not (tmp_path / "one/extra").exists()
    with pytest.raises(WorldReadOnly):
        w.read_only().write_file("project", "ignored", "x")


@pytest.mark.parametrize("path", ["../outside", "/outside", ".", "a/../b", "a//b"])
def test_rejects_unsafe_paths(tmp_path, path):
    w = world(tmp_path / "workspace")
    with pytest.raises(WorldUsageError):
        w.write_file("project", path, "x")


def test_rejects_links_and_special_files(tmp_path):
    root = tmp_path / "workspace"
    w = world(root)
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    (root / "symlink").symlink_to(outside)
    os.link(outside, root / "hardlink")
    os.mkfifo(root / "fifo")
    for name in ("symlink", "hardlink", "fifo"):
        for operation in (
            lambda: w.read_file("project", name),
            lambda: w.write_file("project", name, "bad"),
        ):
            with pytest.raises(WorldUsageError):
                operation()
    assert outside.read_text() == "untouched"
    with pytest.raises(WorldUsageError):
        w.checkpoint()


def test_bounds_file_reads_and_writes(tmp_path):
    w = world(tmp_path / "workspace")
    with pytest.raises(WorldStateTooLarge):
        w.write_file("project", "large", "x" * (1024 * 1024 + 1))


def test_setup_ready_and_check_reach_the_workspace_through_the_harness(tmp_path, phase):
    w = world(tmp_path / "workspace")

    # The worker gets no root: under per-user isolation it could not open one.
    assert "workspace_roots" not in json.dumps(w._execution_state())
    setup = "def setup(world):\n    world.write_file('project', 'native.txt', 'from child')\n"
    assert phase(setup, w) is None
    assert (tmp_path / "workspace/native.txt").read_text() == "from child"
    ready = "def ready(world):\n    return world.read_file('project', 'native.txt') == 'from child'\n"
    assert phase(ready, w.read_only(), "ready") is True
    check = "def check(world):\n    return world.read_file('project', 'native.txt')\n"
    assert phase(check, w.read_only(), "check") == "from child"


# Scenario code that skips the handle and writes its own requests on the channel.
_RAW_REQUESTS = """def {entry}(world):
    import json, os, socket
    from fi.alk.harness.isolated_process import (
        CHANNEL_FD_ENV, encode_message, receive_message, send_message,
    )
    channel = socket.socket(fileno=os.dup(int(os.environ[CHANNEL_FD_ENV])))
    replies = []
    for request in {requests!r}:
        send_message(channel, encode_message(request))
        replies.append(receive_message(channel, limit=1 << 23))
    return json.dumps(replies)
"""


def test_a_worker_cannot_make_the_harness_reach_outside_its_world(tmp_path, phase):
    root = tmp_path / "workspace"
    w = world(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("held back")
    (root / "link").symlink_to(outside, target_is_directory=True)
    (root / "leaf").symlink_to(outside / "secret.txt")
    os.link(outside / "secret.txt", root / "hardlink")
    requests = [
        {"op": "read_file", "workspace": "project", "path": "../outside/secret.txt"},
        {"op": "read_file", "workspace": "project", "path": str(outside / "secret.txt")},
        {"op": "read_file", "workspace": "project", "path": "link/secret.txt"},
        {"op": "read_file", "workspace": "project", "path": "leaf"},
        {"op": "read_file", "workspace": "project", "path": "hardlink"},
        {"op": "write_file", "workspace": "project", "path": "link/planted.txt", "content": "x"},
        {"op": "write_file", "workspace": "project", "path": "leaf", "content": "x"},
        {"op": "write_file", "workspace": "project", "path": "hardlink", "content": "x"},
        {"op": "read_file", "workspace": "other", "path": "secret.txt", "root": str(outside)},
        {"op": "write_file", "workspace": "project", "path": "ok.txt", "content": 5},
        {"op": "unlink", "workspace": "project", "path": "link"},
    ]

    replies = json.loads(phase(_RAW_REQUESTS.format(entry="setup", requests=requests), w))

    assert [sorted(reply) for reply in replies] == [["error", "message"]] * len(requests)
    assert "held back" not in json.dumps(replies)
    assert sorted(path.name for path in outside.iterdir()) == ["secret.txt"]
    assert (outside / "secret.txt").read_text() == "held back"
    assert not (root / "ok.txt").exists()


def test_a_revert_handle_the_harness_never_issued_is_refused(tmp_path, phase):
    w = world(tmp_path / "workspace")
    w.write_file("project", "a.txt", "one")
    forged = [{"op": "revert", "digest": "0" * 64}, {"op": "revert", "digest": str(tmp_path)}]

    replies = json.loads(phase(_RAW_REQUESTS.format(entry="setup", requests=forged), w))

    assert replies == [
        {"error": "WorldUsageError", "message": "revert names no snapshot this phase took"}
    ] * 2
    assert (tmp_path / "workspace/a.txt").read_text() == "one"


def test_a_read_only_phase_cannot_write_through_the_channel(tmp_path, phase):
    w = world(tmp_path / "workspace")
    requests = [{"op": "write_file", "workspace": "project", "path": "x.txt", "content": "x"}]

    replies = json.loads(
        phase(_RAW_REQUESTS.format(entry="check", requests=requests), w.read_only(), "check")
    )

    assert replies == [{"error": "WorldReadOnly", "message": "write_file reached a read-only handle"}]
    assert not (tmp_path / "workspace/x.txt").exists()


@pytest.mark.parametrize(
    "call", ["write_file('project', 'x.txt', '\\ud800')", "read_file('project', '\\ud800')"],
    ids=["content", "path"],
)
def test_text_no_file_can_hold_is_the_scenario_s_mistake_not_the_world_s(tmp_path, phase, call):
    from fi.alk.harness.hosted_scheduler import _PhaseMisuse

    w = world(tmp_path / "workspace")

    # A misuse, not a world that went away (_PhaseWorldGone).
    with pytest.raises(_PhaseMisuse, match="WorldUsageError"):
        phase(f"def setup(world):\n    world.{call}\n", w)


# Scenario code that breaks the channel, then finishes its phase.
_BREAKS_THE_CHANNEL = """def setup(world):
    import os, socket
    from fi.alk.harness.isolated_process import CHANNEL_FD_ENV, encode_message, send_message
    channel = socket.socket(fileno=os.dup(int(os.environ[CHANNEL_FD_ENV])))
    send_message(channel, {body})
    return 'finished'
"""


@pytest.mark.parametrize(
    "body",
    [
        "b'[' * 200000",  # nested past the parser's recursion limit
        "b'not json'",
        "encode_message({'op': 'read_file', 'workspace': 'project', 'path': 'a.txt'})",
    ],
    ids=["deep-nesting", "not-json", "unread-reply"],
)
def test_a_worker_that_breaks_its_channel_keeps_its_phase_outcome(tmp_path, phase, body):
    # unread-reply: the worker exits before reading; on Linux the harness then reads ECONNRESET.
    w = world(tmp_path / "workspace")
    w.write_file("project", "a.txt", "one")

    assert phase(_BREAKS_THE_CHANNEL.format(body=body), w) == "finished"


class _Served:
    """A worker-side world whose workspace channel this test process serves, as a parent would."""

    def __init__(self, parent_world, *, read_only, **world_options):
        self._parent, child = socket.socketpair()
        self._serving = threading.Thread(
            target=asyncio.run,
            args=(
                isolated_process._serve(
                    self._parent, parent_world._workspace_service(read_only=read_only)
                ),
            ),
        )
        self._serving.start()
        self.world = HostedWorld(
            Store(), 0, random.Random(0), {},
            _validate_baseline=False,
            workspace_channel=WorkspaceClient(child.detach()),
            **world_options,
        )

    def close(self):
        self.world._workspace_channel._socket.close()
        self._serving.join(timeout=10)
        assert not self._serving.is_alive()


def test_a_worker_checkpoint_is_a_digest_and_revert_restores_the_harness_snapshot(tmp_path):
    w = world(tmp_path / "workspace")
    w.write_file("project", "a.txt", "one")
    served = _Served(w, read_only=True)
    try:
        remote = served.world
        checkpoint = remote.checkpoint()
        # Only a digest crossed; the snapshot stayed in the harness.
        assert checkpoint[0] == "baseline" and len(checkpoint[1]) == 64
        (tmp_path / "workspace/a.txt").write_text("two")  # what the agent might do
        assert remote.checkpoint() != checkpoint
        remote.revert(checkpoint)
        assert (tmp_path / "workspace/a.txt").read_text() == "one"
        assert remote.read_file("project", "a.txt") == "one"
        with pytest.raises(WorldReadOnly):
            remote.write_file("project", "a.txt", "three")
    finally:
        served.close()


def test_an_observation_that_changes_the_workspace_is_refused_and_undone(tmp_path):
    import test_named_observations as named
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    root = tmp_path / "workspace"
    w = world(root)
    w.write_file("project", "a.txt", "one")
    writes = {"on": False}

    class Agent(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if writes["on"]:
                (root / "a.txt").write_text("changed while observed")
            body = json.dumps({"events": ["completed"]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Agent)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    served = _Served(
        w, read_only=True,
        endpoints={"app": f"http://127.0.0.1:{server.server_port}"},
        observations=named.CATALOGUE,
    )
    try:
        readonly = served.world.read_only()
        assert readonly.observe("history", {"session": "abc", "limit": 3})["json"] == {
            "events": ["completed"]
        }
        writes["on"] = True
        with pytest.raises(WorldReadOnly):
            readonly.observe("history", {"session": "abc", "limit": 3})
        assert (root / "a.txt").read_text() == "one"
    finally:
        served.close()
        server.shutdown()
        thread.join()
        server.server_close()


def test_workspace_process_uses_private_copy_even_single_world_and_respawn_resets(
    tmp_path,
):
    from fi.alk.harness import process_runtime as pr
    from test_process_runtime import _source_process, _solo_port_plan, FakeHandle

    build = tmp_path / "build"
    (build / "workspace").mkdir(parents=True)
    (build / "workspace/original").write_text("baseline")
    process = _source_process().model_copy(
        update={"workspaces": {"project": "workspace"}}
    )
    locations = []

    def runner(argv, *, cwd, env, log_path, user=None, group=None):
        locations.append(cwd)
        return FakeHandle()

    def spawn(index):
        pr.spawn_source_process(
            process,
            build_dir=build,
            world_dir=tmp_path / f"w{index}",
            world_index=0,
            port_plan=_solo_port_plan("svc"),
            configuration_addresses={},
            secret_values={},
            secret_purposes={},
            runner=runner,
        )

    spawn(0)
    w = world(locations[-1] / "workspace")
    w.write_file("project", "original", "changed")
    w.write_file("project", "new", "extra")
    spawn(1)
    assert (locations[-1] / "workspace/original").read_text() == "baseline"
    spawn(0)
    assert (locations[-1] / "workspace/original").read_text() == "baseline"
    assert not (locations[-1] / "workspace/new").exists()
    assert (build / "workspace/original").read_text() == "baseline"
    assert not (build / "workspace/new").exists()


@pytest.mark.parametrize(
    "bindings",
    [
        {"a": "."},
        {"a": "../other"},
        {"a": "/absolute"},
        {"a": ".venv"},
        {"a": "one", "b": "one/sub"},
        {"a": "one", "b": "one"},
    ],
)
def test_workspace_declaration_rejects_unsafe_and_overlapping_roots(bindings):
    from fi.alk.harness.contract import Runtime

    with pytest.raises(ValueError):
        Runtime(workspaces=bindings)


def test_reset_restarts_workspace_process_even_when_keep_processes_requested(tmp_path):
    from fi.alk.harness import process_runtime as pr
    from test_process_runtime import _manifest, _frozen_template_database

    manifest = _manifest()
    for process in manifest.processes:
        if process.name == "agent":
            process.workspaces = {"project": "workspace"}
    freeze, context, _ = _frozen_template_database(manifest, tmp_path)
    spawned = pr._clone_or_reset_world(
        manifest,
        0,
        context=context,
        baseline=freeze.build_output,
        job_shared_handles=freeze.job_shared_handles,
        existing_handles={},
    )
    root = pr.world_scratch_dir(tmp_path, 0, "agent") / "runtime/workspace"
    w = world(root)
    w.write_file("project", "new.txt", "scenario one")
    old = spawned.handles["agent"].handle
    _, healthy = pr.reset_world(
        manifest,
        0,
        context=context,
        baseline=freeze.build_output,
        job_shared_handles=freeze.job_shared_handles,
        existing_handles=spawned.handles,
        keep_processes=True,
    )
    assert old.terminated
    assert healthy
    assert not (root / "new.txt").exists()


def test_workspace_declaration_survives_compilation_and_seal(tmp_path):
    import json
    from test_bundle_author_v2 import (
        _authoring,
        _write_voice_contract,
        _job,
        author_bundle_v2,
    )

    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("print('agent')\n")
    (source / "pyproject.toml").write_text("[project]\nname='agent'\nversion='1'\n")
    authoring = _authoring(tmp_path)
    _write_voice_contract(authoring)
    path = authoring / "contract.json"
    body = json.loads(path.read_text())
    body.setdefault("runtime", {})["workspaces"] = {"project": "workspace/project"}
    path.write_text(json.dumps(body))
    bundle = author_bundle_v2(
        source=source,
        job=_job(connector="auto", with_secrets=True),
        authoring=authoring,
        output=tmp_path / "bundle",
    )
    process = next(p for p in bundle.processes if p.name == "agent")
    assert process.workspaces == {"project": "workspace/project"}
    assert process.model_dump(mode="json")["workspaces"] == {
        "project": "workspace/project"
    }


def test_checkpoint_preserves_executable_mode_and_empty_directory(tmp_path):
    w = world(tmp_path / "workspace")
    w.write_file("project", "script", "#!/bin/sh\n")
    script = tmp_path / "workspace/script"
    script.chmod(0o751)
    (tmp_path / "workspace/empty").mkdir()
    checkpoint = w.checkpoint()
    script.unlink()
    (tmp_path / "workspace/empty").rmdir()
    w.revert(checkpoint)
    assert script.stat().st_mode & 0o777 == 0o751
    assert (tmp_path / "workspace/empty").is_dir()


def test_checkpoint_preserves_workspace_root_mode(tmp_path):
    from fi.alk.harness.world import files

    root = tmp_path / "workspace"
    root.mkdir(mode=0o750)
    baseline = files.snapshot(root)
    root.chmod(0o700)
    assert files.snapshot(root) != baseline
    files.restore(root, baseline)
    assert root.stat().st_mode & 0o777 == 0o750


def test_workspace_digest_changes_when_native_file_changes(tmp_path):
    from fi.alk.harness.authoring_runtime_validation import world_state_digest
    from test_world_handle import FakeStore

    root = tmp_path / "workspace"
    root.mkdir()
    w = HostedWorld(
        FakeStore({"rows": []}),
        0,
        random.Random(0),
        {"rows": 0},
        workspace_roots={"project": root},
    )
    w.write_file("project", "file", "first")
    before = world_state_digest(w)
    (root / "file").write_text("second")
    assert world_state_digest(w) != before


def test_snapshot_refuses_over_cap_instead_of_truncating(tmp_path, monkeypatch):
    from fi.alk.harness.world import files

    w = world(tmp_path / "workspace")
    w.write_file("project", "one", "one")
    w.write_file("project", "two", "two")
    monkeypatch.setattr(files, "SNAPSHOT_ENTRIES", 3)
    checkpoint = w.checkpoint()
    assert len(checkpoint[1]["project"]) == 3
    w.write_file("project", "three", "three")
    with pytest.raises(WorldStateTooLarge):
        w.checkpoint()


def test_symlinked_workspace_ancestor_is_rejected(tmp_path):
    from fi.alk.harness.world.files import prepare_root

    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "alias").symlink_to(real, target_is_directory=True)
    with pytest.raises(WorldUsageError):
        prepare_root(tmp_path / "alias/new")
    assert not (real / "new").exists()
