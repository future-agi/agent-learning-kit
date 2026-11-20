"""A contained command has one timeout for startup and execution together."""

import errno
import fcntl
import os
import select
import signal
import subprocess
import sys
import threading
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


@pytest.mark.parametrize("interrupted_signal", ["group", "child"])
def test_signal_interrupt_preserves_startup_timeout(
    monkeypatch, tmp_path, interrupted_signal
):
    subtree = _FailingSubtree(tmp_path / "missing-leaf")
    monkeypatch.setattr(containment, "_containment", subtree)
    process = Mock(pid=123, returncode=None)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: process)
    group_kill = Mock()
    monkeypatch.setattr(os, "killpg", group_kill)
    (
        group_kill if interrupted_signal == "group" else process.kill
    ).side_effect = KeyboardInterrupt()
    timeout = subprocess.TimeoutExpired(["true"], 1)
    monkeypatch.setattr(containment, "_write_all", Mock())
    monkeypatch.setattr(containment, "_read_all", Mock(side_effect=timeout))
    handoff = Mock()
    monkeypatch.setattr(containment, "_defer_cleanup", handoff)

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        containment.popen(["true"], start_new_session=True)
    assert caught.value is timeout
    group_kill.assert_called_once_with(process.pid, signal.SIGKILL)
    if interrupted_signal == "group":
        process.kill.assert_not_called()  # Popen.kill can reap the leader.
        process.wait.assert_not_called()
        handoff.assert_called_once_with(process, subtree.current, True)
    else:
        process.kill.assert_called_once_with()
        process.wait.assert_called_once()
        handoff.assert_called_once_with(process, subtree.current)
    assert subtree.current.closed == 1
    for stream in (process.stdin, process.stdout, process.stderr):
        stream.close.assert_called_once_with()


@pytest.mark.parametrize("failure", ["false", "error"])
def test_second_pipe_emfile_releases_fds_before_unused_leaf_retry(
    monkeypatch, tmp_path, failure
):
    path = tmp_path / "unused"
    path.mkdir()
    blocker = path / "busy"
    blocker.touch()
    subtree = _FailingSubtree(path)
    monkeypatch.setattr(containment, "_containment", subtree)
    pair = []
    pipe = os.pipe
    emfile = OSError(errno.EMFILE, "controlled pipe limit")

    def limited_pipe():
        if pair:
            raise emfile
        pair.extend(pipe())
        return tuple(pair)

    closed_before_removal = []

    def close_unused():
        for fd in pair:
            try:
                os.fstat(fd)
            except OSError as exc:
                closed_before_removal.append(exc.errno == errno.EBADF)
            else:
                closed_before_removal.append(False)
        if failure == "error" or not all(closed_before_removal):
            raise emfile
        return False

    retry_attempted = threading.Event()
    monkeypatch.setattr(subtree.current, "kill", retry_attempted.set)
    monkeypatch.setattr(subtree.current, "close", close_unused)
    monkeypatch.setattr(os, "pipe", limited_pipe)
    try:
        with pytest.raises(OSError) as caught:
            containment.popen(["true"])
        assert caught.value is emfile and subtree.current.pid is None
        assert closed_before_removal == [True, True]
        assert retry_attempted.wait(4) and path.is_dir()
    finally:
        blocker.unlink()
        worker = containment._worker
        if worker is not None:
            worker.join(timeout=5)
    assert not path.exists()


@pytest.mark.parametrize("close_index", [1, 3], ids=["initial", "final"])
@pytest.mark.parametrize("after", [False, True], ids=["before-close", "after-reuse"])
def test_pipe_close_interrupt_retains_ownership(
    monkeypatch, tmp_path, close_index, after
):
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
    replacement = None
    target = None
    source = open(os.devnull, "rb", buffering=0)

    def interrupted(*streams):
        nonlocal calls, replacement, target
        calls += 1
        if calls == close_index:
            if after:
                target = streams[0].fileno()
                assert target == gate_pairs[0 if close_index == 1 else 1][0]
                close(*streams)
                # Reserve exactly the former reader without overwriting any live fd.
                reserved = fcntl.fcntl(source, fcntl.F_DUPFD, target)
                replacement = os.fdopen(reserved, "rb", buffering=0)
                assert replacement.fileno() == target
            raise interrupt
        close(*streams)

    monkeypatch.setattr(containment, "_close", interrupted)
    pipe = os.pipe
    descriptors = {}
    gate_pairs = []

    def record_pipe():
        pair = pipe()
        gate_pairs.append(pair)
        descriptors.update((fd, os.fstat(fd)) for fd in pair)
        return pair

    monkeypatch.setattr(os, "pipe", record_pipe)
    try:
        with pytest.raises(KeyboardInterrupt) as caught:
            containment.popen(["true"])
        assert caught.value is interrupt
        if replacement is not None:
            assert replacement.fileno() == target
            assert replacement.read(1) == b""  # A reused number was not closed again.
        for fd in descriptors:
            if replacement is not None and fd == replacement.fileno():
                continue
            with pytest.raises(OSError) as closed:
                os.fstat(fd)
            assert closed.value.errno == errno.EBADF
        process.kill.assert_called_once_with()
        process.wait.assert_called_once()
    finally:
        source.close()
        if replacement is not None:
            replacement.close()
        for fd, original in descriptors.items():
            try:
                if os.path.samestat(os.fstat(fd), original):
                    os.close(fd)
            except OSError:
                pass


@pytest.mark.parametrize(
    "fault",
    ["wait-timeout", "thread-start", "transient-start", "leaf-interrupt", "wait-interrupt"],
)
def test_late_exit_reaped_and_leaf_removed_with_traceback_held(
    monkeypatch, tmp_path, fault
):
    path = tmp_path / "leaf"
    path.mkdir()
    blocker = path / "populated"
    blocker.touch()
    subtree = _FailingSubtree(path)
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_EMPTY_WAIT_SECONDS", 0.05)
    processes = []
    spawn = subprocess.Popen
    permitted = threading.Event()
    retry_attempted = threading.Event()
    start = threading.Thread.start
    starts_refused = []

    def retry_kill():
        if not permitted.is_set():
            retry_attempted.set()
            raise OSError("leaf temporarily busy")
        blocker.unlink(missing_ok=True)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("second interrupt")

    def refuse_start(thread):
        if thread.name == "alk-reaper":
            if fault == "transient-start" and starts_refused:
                return start(thread)
            starts_refused.append(thread)
            raise RuntimeError("simulated thread capacity limit")
        return start(thread)

    # No pending work from an earlier test may service this one's handoff.
    previous = getattr(containment, "_worker", None)
    if previous is not None and previous.is_alive():
        previous.join(timeout=5)
        assert not previous.is_alive()
    monkeypatch.setattr(subtree.current, "kill", retry_kill)
    if fault in ("thread-start", "transient-start"):
        monkeypatch.setattr(threading.Thread, "start", refuse_start)
    elif fault == "leaf-interrupt":
        monkeypatch.setattr(subtree.current, "close", interrupted)

    def controlled(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        # Stand in for a pending signal that takes effect only after I/O completes.
        monkeypatch.setattr(process, "kill", lambda: None)
        if fault == "wait-interrupt":
            monkeypatch.setattr(process, "wait", interrupted)
        return process

    monkeypatch.setattr(subprocess, "Popen", controlled)
    error = RuntimeError("caller interrupted")
    read = containment._read_all

    def after_exec(fd):
        assert read(fd) == b""
        raise error

    monkeypatch.setattr(containment, "_read_all", after_exec)
    release, writer = os.pipe()
    try:
        with pytest.raises(RuntimeError) as caught:
            containment.popen(
                [sys.executable, "-c", f"import os; os.read({release}, 1)"],
                pass_fds=(release,),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        process = processes[0]
        assert caught.value is error and caught.value.__traceback__ is not None
        assert process.returncode is None and path.is_dir()
        assert all(
            stream.closed for stream in (process.stdin, process.stdout, process.stderr)
        )
        if fault == "thread-start":
            assert starts_refused
            monkeypatch.setattr(threading.Thread, "start", start)
            containment.retry_pending_cleanup()  # No later launch or GC.
        if fault == "transient-start":
            assert len(starts_refused) == 1
        assert retry_attempted.wait(4)
        permitted.set()  # Only the deferred kill can now remove the population blocker.
        os.write(writer, b"x")
        deadline = time.monotonic() + 4
        while (
            process.returncode is None or path.exists()
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert process.returncode == 0 and not path.exists()
        with pytest.raises(ChildProcessError):
            os.waitpid(process.pid, os.WNOHANG)
    finally:
        permitted.set()
        os.close(release)
        os.close(writer)
        for process in processes:
            spawn.kill(process)
            spawn.wait(process, timeout=3)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
        if fault == "thread-start":
            monkeypatch.setattr(threading.Thread, "start", start)
            containment.retry_pending_cleanup()
        worker = getattr(containment, "_worker", None)
        if worker is not None and worker.is_alive():
            worker.join(timeout=5)
            assert not worker.is_alive()


@pytest.mark.parametrize("wrap_index", range(1, 5))
@pytest.mark.parametrize("after", [False, True], ids=["before-adopt", "after-adopt"])
def test_interrupt_wrapping_each_gate_end_closes_raw_and_wrapped_fds(
    monkeypatch, tmp_path, wrap_index, after
):
    subtree = _FailingSubtree(tmp_path / "absent")
    monkeypatch.setattr(containment, "_containment", subtree)
    adopt = containment._GateEnd.adopt
    wraps = 0
    error = KeyboardInterrupt()

    def wrap(stream, fd, mode):
        nonlocal wraps
        wraps += 1
        if wraps == wrap_index and not after:
            raise error
        adopt(stream, fd, mode)
        if wraps == wrap_index:
            raise error

    monkeypatch.setattr(containment._GateEnd, "adopt", wrap)
    before = set(os.listdir("/dev/fd"))
    with pytest.raises(KeyboardInterrupt) as caught:
        containment.popen(["true"])
    assert caught.value is error and subtree.current.pid is None
    assert set(os.listdir("/dev/fd")) == before


def test_startup_timeout_stops_ready_descendant_when_leaf_fails(monkeypatch, tmp_path):
    subtree = _FailingSubtree(tmp_path / "absent")
    monkeypatch.setattr(containment, "_containment", subtree)
    read_ack = containment._read_all
    read_fd, write_fd = os.pipe()
    # Both processes retain the write end for their lifetime. Only the child
    # reports readiness, after fork. EOF checks both, even if init hasn't reaped it.
    command = [
        sys.executable,
        "-c",
        "import os,sys,time\n"
        "if os.fork() == 0: os.write(int(sys.argv[1]), b'R')\n"
        "time.sleep(30)",
        str(write_fd),
    ]
    with (
        os.fdopen(read_fd, "rb", buffering=0) as output,
        os.fdopen(write_fd, "wb") as parent_writer,
    ):
        startup_errors = []
        stopped = False

        def delayed_parent(fd, deadline):
            parent_writer.close()
            assert select.select([output], [], [], 5)[0] and output.read(1) == b"R"
            assert select.select([fd], [], [], 0)[0]  # Exec acknowledgement is ready.
            # Advance only the deadline clock at the parent's observation point.
            with monkeypatch.context() as m:
                m.setattr(containment.time, "monotonic", lambda: deadline.end + 1)
                try:
                    return read_ack(fd, deadline)
                except subprocess.TimeoutExpired as exc:
                    startup_errors.append(exc)
                    raise

        monkeypatch.setattr(containment, "_read_all", delayed_parent)
        try:
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                containment.run(command, pass_fds=(write_fd,), timeout=10)
            assert startup_errors == [caught.value]  # run never got the handle.
            assert (caught.value.cmd, caught.value.timeout) == (command, 10)
            assert subtree.current.closed and subtree.current.pid is not None
            assert select.select([output], [], [], 3)[0] and output.read(1) == b""
            stopped = True
            with pytest.raises(ChildProcessError):
                os.waitpid(subtree.current.pid, os.WNOHANG)
        finally:
            if not stopped and subtree.current.pid is not None:
                try:
                    os.killpg(subtree.current.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(subtree.current.pid, 0)
                except ChildProcessError:
                    pass


def test_timeout_handoff_during_interrupted_worker_start(monkeypatch, tmp_path):
    previous = containment._worker
    if previous is not None:
        previous.join(5)
        assert not previous.is_alive()
    starting, release = threading.Event(), threading.Event()
    interrupted = []
    lock = threading.Lock()
    start = threading.Thread.start

    class InterruptibleLock:
        def __enter__(self):
            if lock.acquire(blocking=False):
                return
            if (
                starting.is_set()
                and threading.current_thread() is threading.main_thread()
            ):
                interrupted.append("contended registration")
                raise KeyboardInterrupt()
            lock.acquire()

        def __exit__(self, *_):
            lock.release()

    def staged_start(thread):
        if thread.name == "alk-reaper":
            if threading.current_thread() is first:
                starting.set()
                assert release.wait(5)
            elif not interrupted:
                interrupted.append("worker start")
                raise KeyboardInterrupt()
        start(thread)

    first_leaf = _FailingLeaf(tmp_path / "first")
    subtree = _FailingSubtree(tmp_path / "second")
    first_leaf.path.mkdir()
    subtree.current.path.mkdir()
    first = threading.Thread(target=containment._defer_cleanup, args=(None, first_leaf))
    timeout = subprocess.TimeoutExpired(["true"], 1)

    def refuse_ack(fd):
        first.start()
        assert starting.wait(3)
        raise timeout

    process = Mock(pid=123, returncode=None)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_write_all", Mock())
    monkeypatch.setattr(containment, "_read_all", refuse_ack)
    monkeypatch.setattr(containment, "_cleanup_lock", InterruptibleLock())
    monkeypatch.setattr(threading.Thread, "start", staged_start)
    try:
        try:
            with pytest.raises(subprocess.TimeoutExpired) as caught:
                containment.popen(["true"])
        except KeyboardInterrupt:
            pytest.fail("secondary interrupt replaced the startup timeout")
        assert caught.value is timeout
        assert interrupted == ["worker start"]
    finally:
        release.set()
        if first.ident is not None:
            first.join(5)
    deadline = time.monotonic() + 3
    while (
        first_leaf.path.exists() or subtree.current.path.exists() or not process.poll.called
    ) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not first_leaf.path.exists() and not subtree.current.path.exists()
    process.poll.assert_called_once_with()


def test_group_retry_precedes_leader_reap(monkeypatch, tmp_path):
    # Model only the lifecycle ordering; never signal a host PID.
    subtree = _FailingSubtree(tmp_path / "leaf")
    subtree.current.path.mkdir()
    process = Mock(pid=123, returncode=None)
    calls = []
    reaped = threading.Event()

    def signal_group(pid, sig):
        calls.append((pid, sig, process.returncode))
        if len(calls) == 1:
            raise KeyboardInterrupt()

    def poll():
        process.returncode = -signal.SIGKILL
        reaped.set()
        return process.returncode

    timeout = subprocess.TimeoutExpired(["true"], 1)
    process.poll.side_effect = poll
    monkeypatch.setattr(os, "killpg", signal_group)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_write_all", Mock())
    monkeypatch.setattr(containment, "_read_all", Mock(side_effect=timeout))
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        containment.popen(["true"], start_new_session=True)
    assert caught.value is timeout and reaped.wait(3)
    assert calls == [(process.pid, signal.SIGKILL, None)] * 2
    process.kill.assert_not_called()
    process.wait.assert_not_called()
    assert not subtree.current.path.exists()
