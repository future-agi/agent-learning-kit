"""Containment: every customer execution in a cgroup of its own, so stopping it stops all it started.

The unit tests stand a fake leaf in for the cgroup. The tests marked ``needs a writable cgroup``
use the real one; they run in the hosted sandbox as root, and CI, which has no writable cgroup,
skips them.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fi.alk.harness import containment
from fi.alk.harness import process_runtime as pr


class _Leaf:
    def __init__(self, on_add=None) -> None:
        self.pids: list[int] = []
        self.killed = 0
        self.closed = 0
        self._on_add = on_add

    def add(self, pid: int) -> None:
        if self._on_add is not None:
            self._on_add(pid)
        self.pids.append(pid)

    def kill(self) -> None:
        self.killed += 1

    def close(self, timeout: float = 5.0) -> None:
        self.closed += 1


class _Subtree:
    def __init__(self, leaf: _Leaf) -> None:
        self.made = 0
        self._leaf = leaf

    def leaf(self) -> _Leaf:
        self.made += 1
        return self._leaf


@pytest.fixture
def contained(monkeypatch):
    def install(leaf: _Leaf) -> _Subtree:
        subtree = _Subtree(leaf)
        monkeypatch.setattr(containment, "_containment", subtree)
        return subtree

    return install


# --- the gate ----------------------------------------------------------------------------------


def test_the_command_starts_only_once_it_is_in_its_leaf(tmp_path, contained):
    ran = tmp_path / "ran"

    def join(pid: int) -> None:
        time.sleep(0.3)
        assert not ran.exists()  # still held at the gate

    leaf = _Leaf(on_add=join)
    contained(leaf)

    process, got = containment.popen(["touch", str(ran)])

    assert process.wait(5) == 0
    assert got is leaf and leaf.pids == [process.pid]
    assert ran.exists()


def test_the_gate_holds_its_pipe_above_descriptor_nine(tmp_path, contained):
    # A harness with files open: the pipe lands above 9, where dash cannot redirect from.
    held = [os.open(os.devnull, os.O_RDONLY) for _ in range(12)]
    try:
        contained(_Leaf())
        result = containment.run(["sh", "-c", "echo ran"], capture_output=True, text=True)
    finally:
        for descriptor in held:
            os.close(descriptor)

    assert (result.returncode, result.stdout) == (0, "ran\n")


def test_a_process_that_cannot_join_its_leaf_runs_nothing(tmp_path, contained):
    ran = tmp_path / "ran"

    def refuse(pid: int) -> None:
        raise PermissionError("cgroup.procs")

    leaf = _Leaf(on_add=refuse)
    contained(leaf)

    with pytest.raises(PermissionError):
        containment.popen(["touch", str(ran)])

    time.sleep(0.2)
    assert not ran.exists()
    assert leaf.closed == 1


def test_a_command_that_cannot_run_fails_like_exec_and_leaves_no_leaf(tmp_path, contained):
    leaf = _Leaf()
    subtree = contained(leaf)
    script = tmp_path / "script"
    script.write_text("#!/bin/sh\n")  # no execute bit

    with pytest.raises(FileNotFoundError):
        containment.popen(["alk-no-such-command"])
    with pytest.raises(FileNotFoundError):
        containment.popen(["./missing"], cwd=tmp_path)
    with pytest.raises(PermissionError):
        containment.popen([str(script)])
    assert subtree.made == leaf.closed == 3


def test_the_gate_starts_with_no_caller_variable_and_the_command_gets_them_all(
    contained, monkeypatch
):
    # A variable the customer declares, a loader one above all, must not act on the gate: the
    # gate runs before the process is in its leaf.
    gate_environments = []
    spawn = subprocess.Popen

    def recording(args, **kwargs):
        gate_environments.append(kwargs.get("env"))
        return spawn(args, **kwargs)

    monkeypatch.setattr(containment.subprocess, "Popen", recording)
    contained(_Leaf())
    declared = {
        "PATH": os.environ["PATH"],
        "LD_PRELOAD": "/work/build/agent/preload.so",
        "ALK_DECLARED": "a=b and ü",
    }

    result = containment.run(
        [sys.executable, "-c", "import json, os; print(json.dumps(dict(os.environ)))"],
        env=declared,
        capture_output=True,
        text=True,
    )

    assert gate_environments == [{}]
    seen = json.loads(result.stdout)
    assert {name: seen.get(name) for name in declared} == declared


@pytest.mark.skipif(
    not Path("/proc/self/environ").exists(), reason="reads the held gate's environment in /proc"
)
def test_the_held_gate_carries_no_caller_variable(contained):
    held = []
    contained(_Leaf(on_add=lambda pid: held.append(Path(f"/proc/{pid}/environ").read_bytes())))

    process, _ = containment.popen(
        ["true"], env={"PATH": os.environ["PATH"], "LD_PRELOAD": "/nowhere/preload.so"}
    )

    assert process.wait(5) == 0
    assert held == [b""]


def test_without_an_env_the_command_gets_the_harness_s_own(contained, monkeypatch):
    monkeypatch.setenv("ALK_INHERITED", "yes")
    contained(_Leaf())

    result = containment.run(
        [sys.executable, "-c", "import os; print(os.environ['ALK_INHERITED'])"],
        capture_output=True,
        text=True,
    )

    assert result.stdout == "yes\n"


def test_an_illegal_variable_name_is_refused_before_anything_starts(contained):
    subtree = contained(_Leaf())

    with pytest.raises(ValueError):
        containment.popen(["true"], env={"A=B": "1"})
    assert subtree.made == 0


def test_the_harness_never_judges_a_command_by_its_own_access(contained, monkeypatch):
    # The harness's access(2) answers for svc-control with no capabilities. A command only the
    # declared user can reach (its home, a 0700 repo folder) would look missing to it.
    monkeypatch.setattr(os, "access", lambda *args, **kwargs: False)
    monkeypatch.setattr(shutil, "which", lambda *args, **kwargs: None)
    contained(_Leaf())

    result = containment.run(["sh", "-c", "echo ran"], capture_output=True, text=True)

    assert result.stdout == "ran\n"


def _gate(command: list[str]) -> tuple[subprocess.Popen, int]:
    """The gate on its own, holding ``command``, and the write end of its go pipe."""
    go_read, go_write = os.pipe()
    done_read, done_write = os.pipe()
    gate = subprocess.Popen(
        [sys.executable, "-I", "-S", "-c", containment._GATE, str(go_read), str(done_write), *command],
        env={},
        pass_fds=(go_read, done_write),
    )
    os.close(go_read)
    os.close(done_write)
    os.close(done_read)
    return gate, go_write


@pytest.mark.parametrize(
    "cut", [lambda m: m[:-1], lambda m: m.replace(b"go ", b"go 9", 1)], ids=["cut off", "forged"]
)
def test_a_go_message_that_is_not_whole_ends_the_gate_unrun(tmp_path, cut):
    ran = tmp_path / "ran"
    message = containment._go_message({"PATH": os.environ["PATH"], "A": "1"})
    gate, go = _gate(["touch", str(ran)])

    os.write(go, cut(message))
    os.close(go)

    assert gate.wait(5) == 125
    assert not ran.exists()


def test_a_whole_go_message_runs_the_command(tmp_path):
    ran = tmp_path / "ran"
    gate, go = _gate(["touch", str(ran)])

    os.write(go, containment._go_message({"PATH": os.environ["PATH"]}))
    os.close(go)

    assert gate.wait(5) == 0
    assert ran.exists()


class _KillingLeaf(_Leaf):
    """A leaf whose close kills what it holds, as ``cgroup.kill`` does."""

    def close(self, timeout: float = 5.0) -> None:
        super().close(timeout)
        for pid in self.pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_a_forged_exec_failure_never_keeps_the_harness_waiting_on_a_live_command(
    contained, monkeypatch
):
    # A process of the same user can reach the held gate's done pipe through /proc/<pid>/fd and
    # write an errno there while the real command runs.
    contained(_KillingLeaf())
    monkeypatch.setattr(containment, "_read_all", lambda fd: b"2")
    outcome = {}

    def start() -> None:
        try:
            containment.popen(["sleep", "30"])
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            outcome["error"] = exc

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    thread.join(10)

    assert not thread.is_alive(), "the harness waited on a live command"
    assert isinstance(outcome.get("error"), FileNotFoundError)


def test_a_leaf_that_cannot_be_made_leaves_no_descriptor_open(monkeypatch):
    class Refusing:
        def leaf(self):
            raise OSError(errno.EBUSY, "cgroup busy")

    monkeypatch.setattr(containment, "_containment", Refusing())
    before = set(os.listdir("/dev/fd"))

    with pytest.raises(OSError, match="cgroup busy"):
        containment.popen(["true"])

    assert set(os.listdir("/dev/fd")) == before


def test_without_containment_a_process_starts_as_it_always_did():
    process, leaf = containment.popen(["true"])

    assert process.wait(5) == 0
    assert leaf is None and process.args == ["true"]


# --- a synchronous run -------------------------------------------------------------------------


def test_a_run_returns_its_output_and_closes_its_leaf(contained):
    leaf = _Leaf()
    contained(leaf)

    result = containment.run(
        ["sh", "-c", "echo out; echo err >&2; exit 3"], capture_output=True, text=True
    )
    with pytest.raises(subprocess.CalledProcessError):
        containment.run(["false"], check=True)

    assert (result.returncode, result.stdout, result.stderr) == (3, "out\n", "err\n")
    assert leaf.closed == 2


def test_a_run_past_its_timeout_is_stopped_and_closes_its_leaf(contained):
    leaf = _Leaf()
    contained(leaf)
    started = time.monotonic()

    with pytest.raises(subprocess.TimeoutExpired):
        containment.run(["sleep", "30"], timeout=0.3)

    assert time.monotonic() - started < 5
    assert leaf.closed == 1


# --- a long-running process --------------------------------------------------------------------


def test_a_finished_process_takes_its_leaf_with_it(tmp_path):
    leaf = _Leaf()
    process = pr.PopenProcess(
        popen=subprocess.Popen(["true"]), log_path=tmp_path / "log", leaf=leaf
    )

    assert process.wait(5)
    assert leaf.closed == 1 and process.leaf is None


def test_killing_a_process_kills_its_leaf(tmp_path):
    leaf = _Leaf()
    process = pr.PopenProcess(
        popen=subprocess.Popen(["sleep", "30"], start_new_session=True),
        log_path=tmp_path / "log",
        leaf=leaf,
    )

    process.kill()

    assert leaf.killed == 1
    assert process.wait(5)


# --- the cgroup itself -------------------------------------------------------------------------


def test_a_host_without_cgroup_v2_has_no_containment(tmp_path):
    own = tmp_path / "cgroup"
    (own / "user").mkdir(parents=True)
    v1_only = tmp_path / "v1"
    v1_only.write_text("1:name=systemd:/user\n")
    plain = tmp_path / "plain"
    plain.write_text("0::/user\n")  # a unified line, but a folder that is no cgroup

    assert containment.probe(proc_self_cgroup=v1_only, cgroup_root=own) is None
    assert containment.probe(proc_self_cgroup=plain, cgroup_root=own) is None
    assert list((own / "user").iterdir()) == []  # the trial subtree is gone again


def test_a_leaf_that_will_not_empty_is_reported_not_raised(tmp_path, caplog):
    leaf = containment.Leaf(tmp_path / "leaf")
    leaf.path.mkdir()
    (leaf.path / "stuck").write_text("")  # rmdir then fails, as for a process the kernel kept

    leaf.close(timeout=0.1)

    assert "did not empty" in caplog.text


def test_a_leaf_already_removed_is_left_alone(tmp_path):
    leaf = containment.Leaf(tmp_path / "gone")

    leaf.kill()
    leaf.close(timeout=0.1)


# --- with a writable cgroup ------------------------------------------------------------------


@pytest.fixture
def real_subtree(monkeypatch):
    subtree = containment.probe()
    if subtree is None:
        pytest.skip("needs a writable cgroup v2 subtree: the hosted sandbox has one, CI does not")
    monkeypatch.setattr(containment, "_containment", subtree)
    yield subtree
    subtree.sweep()


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return True  # no /proc here: alive as far as kill() can tell
    return state != "Z"


def _gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    return not _alive(pid)


def _pid_from(path: Path, seconds: float = 5.0) -> int:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        text = path.read_text() if path.exists() else ""
        if text.strip():
            return int(text)
        time.sleep(0.05)
    raise AssertionError(f"{path} never got a pid")


def _members(leaf: containment.Leaf) -> list[str]:
    try:
        return (leaf.path / "cgroup.procs").read_text().split()
    except FileNotFoundError:
        return []


def test_an_interrupted_run_leaves_no_process_behind(real_subtree, monkeypatch):
    # The command starts a child that would outlive it, then waits. A SIGINT reaches the harness
    # while it waits on the run, as Ctrl-C does.
    leaves: list[containment.Leaf] = []
    make = real_subtree.leaf
    monkeypatch.setattr(real_subtree, "leaf", lambda: leaves.append(make()) or leaves[-1])

    def interrupt_once_both_are_in_the_leaf() -> None:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if leaves and len(_members(leaves[0])) >= 2:
                os.kill(os.getpid(), signal.SIGINT)
                return
            time.sleep(0.02)

    threading.Thread(target=interrupt_once_both_are_in_the_leaf, daemon=True).start()

    with pytest.raises(KeyboardInterrupt):
        containment.run(["sh", "-c", "sleep 300 & sleep 10"])

    assert not leaves[0].path.exists()  # emptied, so the harness could remove it


# A process that leaves its session and its process group, as a daemon does.
_DAEMON = "setsid sh -c 'echo $$ > {pidfile}; exec sleep 300' </dev/null >/dev/null 2>&1 &"


def test_the_command_runs_inside_its_own_leaf(real_subtree):
    result = containment.run(["cat", "/proc/self/cgroup"], capture_output=True, text=True)

    own = result.stdout.strip().split("::", 1)[1]
    assert Path("/sys/fs/cgroup", own.lstrip("/")).parent == real_subtree.root


def test_killing_a_process_kills_what_it_started_in_another_session(tmp_path, real_subtree):
    pidfile = tmp_path / "daemon.pid"
    leader = f"trap '' TERM; {_DAEMON.format(pidfile=pidfile)} wait"
    process = pr.default_process_runner(
        ["sh", "-c", leader], cwd=tmp_path, env=dict(os.environ), log_path=tmp_path / "log"
    )
    daemon = _pid_from(pidfile)

    process.terminate()  # the leader ignores it, and the daemon never gets it
    assert not process.wait(0.5) and _alive(daemon)
    process.kill()

    assert process.wait(5)
    assert _gone(daemon)
    assert [leaf for leaf in real_subtree.root.iterdir() if leaf.is_dir()] == []


def test_a_process_that_exits_takes_what_it_started_in_another_session(tmp_path, real_subtree):
    pidfile = tmp_path / "daemon.pid"
    leader = f"{_DAEMON.format(pidfile=pidfile)} wait"
    process = pr.default_process_runner(
        ["sh", "-c", leader], cwd=tmp_path, env=dict(os.environ), log_path=tmp_path / "log"
    )
    daemon = _pid_from(pidfile)

    process.terminate()  # the leader exits; its daemon is in another session

    assert process.wait(5)
    assert _gone(daemon)


def test_a_build_step_that_leaves_a_daemon_behind_ends_with_it(tmp_path, real_subtree):
    pidfile = tmp_path / "daemon.pid"

    # The step waits until its daemon is up, then exits and leaves it running.
    step = f"{_DAEMON.format(pidfile=pidfile)} until [ -s {pidfile} ]; do sleep 0.05; done"

    result = pr.declared_user_run(["sh", "-c", step], cwd=tmp_path, timeout=10)

    assert result.returncode == 0
    assert _gone(_pid_from(pidfile))
