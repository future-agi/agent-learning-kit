"""A contained command has one timeout for startup and execution together."""

import errno
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from fi.alk.harness import containment


class _FailingLeaf:
    def __init__(self, path):
        self.path = path
        self.pid = None
        self.closed = 0

    def add(self, pid):
        self.pid = pid

    def kill(self):
        raise OSError("simulated cgroup.kill failure")

    def close(self, timeout=5.0):
        self.closed += 1
        raise OSError("simulated leaf removal failure")


class _FailingSubtree:
    def __init__(self, path):
        self.current = _FailingLeaf(path)

    def leaf(self):
        return self.current


@pytest.mark.parametrize("backpressure", [False, True], ids=["done-read", "go-write"])
def test_stalled_gate_reaped_and_owned_pipes_closed(
    monkeypatch, tmp_path, caplog, backpressure
):
    subtree = _FailingSubtree(tmp_path / "missing-leaf")
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_GATE", "import time; time.sleep(8)")
    # Larger than a Linux pipe. The gate never reads, so only the deadline interrupts the write.
    env = {"PAYLOAD": "a" * (8 << 20)} if backpressure else {}
    command = ["true"]
    fds = Path("/dev/fd")
    before = set(os.listdir(fds))
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        containment.run(
            command, input=b"data", capture_output=True, env=env, timeout=0.5
        )

    assert time.monotonic() - start < 5
    assert (caught.value.cmd, caught.value.timeout) == (command, 0.5)
    assert "simulated leaf removal failure" in caplog.text
    pid = subtree.current.pid
    assert pid is not None
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    # The traceback still references the Popen frame; collection must not be needed.
    assert set(os.listdir(fds)) == before


def test_successful_handshake_spends_execution_budget(monkeypatch, tmp_path, caplog):
    subtree = _FailingSubtree(tmp_path / "missing-leaf")
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(
        containment, "_GATE", "import time; time.sleep(1)\n" + containment._GATE
    )
    command = [
        sys.executable,
        "-c",
        "import time; print('started', flush=True); time.sleep(2)",
    ]
    with (tmp_path / "output").open("w+") as output:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            containment.run(command, env={}, timeout=2.5, stdout=output)
        output.seek(0)  # A caller's file remains usable after cleanup.
        assert output.read() == "started\n"
    assert (caught.value.cmd, caught.value.timeout) == (command, 2.5)
    assert "simulated leaf removal failure" in caplog.text


@pytest.mark.parametrize("owns_group", [False, True])
def test_startup_abort_signals_only_owned_group(monkeypatch, tmp_path, owns_group):
    subtree = _FailingSubtree(tmp_path / "missing-leaf")
    monkeypatch.setattr(containment, "_containment", subtree)
    signals = []
    monkeypatch.setattr(os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    error = subprocess.TimeoutExpired(["true"], 1)

    def refuse_ack(fd):
        raise error

    monkeypatch.setattr(containment, "_read_all", refuse_ack)
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        containment.popen(["true"], start_new_session=owns_group)
    assert caught.value is error
    assert signals == ([(subtree.current.pid, signal.SIGKILL)] if owns_group else [])


@pytest.mark.parametrize("close_index", [1, 3], ids=["initial", "final"])
def test_pipe_close_interrupt_retains_ownership(monkeypatch, tmp_path, close_index):
    # An inert Popen stand-in: exercise real descriptors without ever stranding a gate.
    subtree = _FailingSubtree(tmp_path / "missing-leaf")
    monkeypatch.setattr(containment, "_containment", subtree)
    process = Mock(pid=123, stdin=None, stdout=None, stderr=None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: process)
    monkeypatch.setattr(containment, "_write_all", lambda *a: None)
    monkeypatch.setattr(containment, "_read_all", lambda *a: b"")
    close = containment._close
    calls = 0
    interrupt = KeyboardInterrupt()

    def interrupted(*streams):
        nonlocal calls
        calls += 1
        if calls == close_index:
            raise interrupt
        close(*streams)

    monkeypatch.setattr(containment, "_close", interrupted)
    pipe = os.pipe
    descriptors = []

    def record_pipe():
        pair = pipe()
        descriptors.extend(pair)
        return pair

    monkeypatch.setattr(os, "pipe", record_pipe)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            containment.popen(["true"])
        assert caught.value is interrupt
        for fd in descriptors:
            with pytest.raises(OSError) as closed:
                os.fstat(fd)
            assert closed.value.errno == errno.EBADF
        process.kill.assert_called_once_with()
        process.wait.assert_called_once()
    finally:
        for fd in descriptors:
            try:
                os.close(fd)
            except OSError:
                pass


def test_late_exit_reaped_and_leaf_removed_with_traceback_held(monkeypatch, tmp_path):
    path = tmp_path / "leaf"
    path.mkdir()
    blocker = path / "populated"
    blocker.touch()
    subtree = _FailingSubtree(path)
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_EMPTY_WAIT_SECONDS", 0.05)
    processes = []
    spawn = subprocess.Popen
    release, writer = os.pipe()

    def controlled(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        # Stand in for a pending signal that takes effect only after I/O completes.
        monkeypatch.setattr(process, "kill", lambda: None)
        return process

    monkeypatch.setattr(subprocess, "Popen", controlled)
    error = RuntimeError("caller interrupted")
    read = containment._read_all

    def after_exec(fd):
        assert read(fd) == b""
        raise error

    monkeypatch.setattr(containment, "_read_all", after_exec)
    try:
        with pytest.raises(RuntimeError) as caught:
            containment.popen(
                [sys.executable, "-c", f"import os; os.read({release}, 1)"],
                pass_fds=(release,),
            )
        process = processes[0]
        assert caught.value is error and caught.value.__traceback__ is not None
        assert process.returncode is None and path.is_dir()
        os.write(writer, b"x")
        blocker.unlink()
        deadline = time.monotonic() + 4
        while (
            process.returncode is None or path.exists()
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process.returncode == 0 and not path.exists()
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
    finally:
        os.close(release)
        os.close(writer)
        if processes:
            processes[0].wait(timeout=3)


def test_abort_stops_both_members_of_a_test_owned_group(tmp_path):
    # Check OS signal delivery separately from gate timing. Both members hold
    # the output pipe. EOF proves neither still holds it after the abort.
    worker = "import sys,time; print('ready',flush=True); time.sleep(10)"
    leader = "import subprocess,sys; subprocess.run([sys.executable,'-c',sys.argv[1]])"
    with subprocess.Popen(
        [sys.executable, "-c", leader, worker],
        stdout=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        # Keep a test-owned reader; the abort closes Popen's streams.
        with os.fdopen(os.dup(process.stdout.fileno()), "rb", buffering=0) as output:
            try:
                assert select.select([output], [], [], 3)[0]
                assert output.readline() == b"ready\n"
                containment._abort_child(
                    process, _FailingLeaf(tmp_path / "absent"), True
                )
                assert select.select([output], [], [], 3)[0]
                assert output.read(1) == b""
                assert process.returncode == -signal.SIGKILL
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
