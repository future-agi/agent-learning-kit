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
        deadline = time.monotonic() + 4
        while path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
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
    ["wait-timeout", "no-new-threads", "leaf-interrupt", "wait-interrupt"],
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

    def retry_kill():
        if not permitted.is_set():
            retry_attempted.set()
            raise OSError("leaf temporarily busy")
        blocker.unlink(missing_ok=True)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("second interrupt")

    def refuse_start(thread):
        raise RuntimeError("simulated thread capacity limit")

    monkeypatch.setattr(subtree.current, "kill", retry_kill)
    if fault == "leaf-interrupt":
        monkeypatch.setattr(subtree.current, "close", interrupted)

    def controlled(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        if fault == "no-new-threads":
            monkeypatch.setattr(threading.Thread, "start", refuse_start)
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
    _assert_group_cleanup(monkeypatch, tmp_path, interrupt=False)


@pytest.mark.skipif(not hasattr(os, "waitid"), reason="needs non-reaping waitid")
def test_interrupted_group_signal_stops_ready_descendant(monkeypatch, tmp_path):
    _assert_group_cleanup(monkeypatch, tmp_path, interrupt=True)


def _assert_group_cleanup(monkeypatch, tmp_path, interrupt):
    subtree = _FailingSubtree(tmp_path / "absent")
    monkeypatch.setattr(containment, "_containment", subtree)
    read_ack = containment._read_all
    read_fd, write_fd = os.pipe()
    stop_fd, stop_writer = os.pipe()
    # Only the descendant reports readiness. Closing stop_writer in teardown releases
    # only our children even on assertion failure, without signalling a possibly stale PID.
    command = [
        sys.executable,
        "-c",
        "import os,select,sys\n"
        "if os.fork() == 0: os.write(int(sys.argv[1]), b'R')\n"
        "elif sys.argv[3] == 'retry': os._exit(0)\n"
        "select.select([int(sys.argv[2])], [], [], 30)",
        str(write_fd),
        str(stop_fd),
        "retry" if interrupt else "direct",
    ]
    spawn = subprocess.Popen
    processes = []

    def record_spawn(*args, **kwargs):
        process = spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record_spawn)
    killpg = os.killpg
    attempts = []

    wait_flags = (os.WEXITED | os.WNOWAIT | os.WNOHANG) if interrupt else 0

    def signal_group(pid, sig):
        assert pid == subtree.current.pid and sig == signal.SIGKILL
        if not attempts:
            attempts.append("interrupted")
            raise KeyboardInterrupt()
        # WNOWAIT observes the already-exited leader without freeing its PGID.
        assert os.waitid(os.P_PID, pid, wait_flags).si_pid == pid
        killpg(pid, sig)
        attempts.append("sent")

    if interrupt:
        monkeypatch.setattr(os, "killpg", signal_group)
    with (
        os.fdopen(read_fd, "rb", buffering=0) as output,
        os.fdopen(write_fd, "wb") as parent_writer,
        os.fdopen(stop_fd, "rb"),
        os.fdopen(stop_writer, "wb") as release,
    ):
        startup_errors = []

        def delayed_parent(fd, deadline):
            parent_writer.close()
            assert select.select([output], [], [], 5)[0] and output.read(1) == b"R"
            assert select.select([fd], [], [], 0)[0]  # Exec acknowledgement is ready.
            if interrupt:
                end = time.monotonic() + 3
                while not os.waitid(os.P_PID, subtree.current.pid, wait_flags):
                    assert time.monotonic() < end, "leader did not exit"
                    time.sleep(0.01)
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
                containment.run(command, pass_fds=(write_fd, stop_fd), timeout=10)
            assert startup_errors == [caught.value]
            assert (caught.value.cmd, caught.value.timeout) == (command, 10)
            assert subtree.current.closed and subtree.current.pid is not None
            assert select.select([output], [], [], 4)[0] and output.read(1) == b""
            end = time.monotonic() + 4
            while processes[0].returncode is None and time.monotonic() < end:
                time.sleep(0.01)
            assert processes[0].returncode == (0 if interrupt else -signal.SIGKILL)
            if interrupt:
                assert attempts == ["interrupted", "sent"]
            with pytest.raises(ChildProcessError):
                os.waitpid(subtree.current.pid, os.WNOHANG)
        finally:
            release.close()
            for process in processes:
                process.wait(timeout=4)


@pytest.mark.parametrize(
    ("launch", "interrupt_rollback"),
    [
        ("contained-popen", False),
        ("contained-run", False),
        ("plain-run", False),
        pytest.param("contained-run", True, id="interrupted-rollback"),
    ],
)
def test_worker_capacity_required_before_launch(
    monkeypatch, tmp_path, launch, interrupt_rollback
):
    state = containment._Cleanup()
    monkeypatch.setattr(containment, "_cleanup", state)
    subtree = _FailingSubtree(tmp_path / "leaf")
    leaf = Mock(wraps=subtree.leaf)
    monkeypatch.setattr(subtree, "leaf", leaf)
    monkeypatch.setattr(
        containment, "_containment", None if launch == "plain-run" else subtree
    )
    call = containment.popen if launch == "contained-popen" else containment.run
    queued = (None, subtree.current, False)
    state.pending.put(queued)
    failure = RuntimeError("thread capacity exhausted")
    with monkeypatch.context() as m:
        start = Mock(side_effect=failure)
        m.setattr(threading.Thread, "start", start)
        interrupted = False

        class InterruptRollback(threading.Condition):
            def __enter__(self):
                nonlocal interrupted
                if start.called and not interrupted:
                    interrupted = True
                    raise KeyboardInterrupt()
                return super().__enter__()

        if interrupt_rollback:
            state.election = InterruptRollback()
        notify = Mock(wraps=state.election.notify_all)
        m.setattr(state.election, "notify_all", notify)
        pipe = Mock(side_effect=AssertionError("pipe opened before admission"))
        spawn = Mock(side_effect=AssertionError("child started before admission"))
        m.setattr(os, "pipe", pipe)
        m.setattr(subprocess, "Popen", spawn)
        with pytest.raises(RuntimeError) as caught:
            call(["true"])
        assert caught.value is failure and state.pending.get_nowait() == queued
        assert interrupted == interrupt_rollback
        assert state.starting is None and state.worker is None
        notify.assert_called_once_with()
        leaf.assert_not_called()
        pipe.assert_not_called()
        spawn.assert_not_called()

    # The isolated worker lives through the next launch, not past this test.
    stop = threading.Event()
    consuming = threading.Event()
    consumers = []

    def consume():
        consumers.append(threading.current_thread())
        consuming.set()
        stop.wait(10)

    monkeypatch.setattr(state, "_consume", consume)
    monkeypatch.setattr(subtree.current, "close", lambda: True)
    try:
        result = call(["true"])
        if launch == "contained-popen":
            process, _ = result
            with process:
                assert process.wait(timeout=3) == 0
        else:
            assert result.returncode == 0
        assert consuming.wait(3) and consumers == [state.worker]
    finally:
        stop.set()
        if state.worker is not None:
            state.worker.join(3)


def test_interrupted_start_revokes_candidate(monkeypatch):
    state = containment._Cleanup()
    start = threading.Thread.start
    candidates = []
    consumers = []
    stop = threading.Event()

    # Hold the native thread before it can claim the ticket that start() revokes.
    entry = state._entry
    release = threading.Event()

    def delayed_entry():
        if release.wait(5):
            entry()

    monkeypatch.setattr(state, "_entry", delayed_entry)

    def interrupted(thread):
        candidates.append(thread)
        start(thread)
        raise KeyboardInterrupt()

    def consume():
        consumers.append(threading.current_thread())
        stop.wait(10)

    monkeypatch.setattr(state, "_consume", consume)
    try:
        with monkeypatch.context() as m:
            m.setattr(threading.Thread, "start", interrupted)
            with pytest.raises(KeyboardInterrupt):
                state.ensure_worker()
        release.set()
        assert state.worker is None
        state.ensure_worker()
    finally:
        release.set()
        stop.set()
        for thread in candidates:
            thread.join(3)
        if state.worker is not None:
            state.worker.join(3)
    assert consumers == [state.worker] and state.worker not in candidates


def test_interrupt_on_creator_reacquire_after_successful_start(monkeypatch):
    state = containment._Cleanup()
    monkeypatch.setattr(containment, "_cleanup", state)
    monkeypatch.setattr(containment, "_containment", None)
    creator = threading.current_thread()
    started, release, consuming, stop = (threading.Event() for _ in range(4))
    consumers = []
    candidates = []
    entry = state._entry
    start = threading.Thread.start

    class InterruptReacquire(threading.Condition):
        def __enter__(self):
            if threading.current_thread() is creator and started.is_set():
                started.clear()
                raise KeyboardInterrupt()
            return super().__enter__()

    def delayed_entry():
        if release.wait(5):
            entry()

    def consume():
        consumers.append(threading.current_thread())
        consuming.set()
        stop.wait(10)

    def successful_start(thread):
        start(thread)
        candidates.append(thread)
        started.set()  # Return normally; the next creator lock acquisition fails.

    state.election = InterruptReacquire()
    monkeypatch.setattr(state, "_entry", delayed_entry)
    monkeypatch.setattr(state, "_consume", consume)
    try:
        with monkeypatch.context() as m:
            m.setattr(threading.Thread, "start", successful_start)
            with pytest.raises(KeyboardInterrupt):
                containment.run(["true"])
        release.set()
        assert consuming.wait(3)
        assert containment.run(["true"]).returncode == 0
        assert consumers == candidates == [state.worker]
    finally:
        release.set()
        stop.set()
        for thread in candidates:
            thread.join(3)


def test_concurrent_admission_shares_one_worker(monkeypatch):
    state = containment._Cleanup()
    # A non-recursive lock lets the start hook check whether its caller holds it.
    state.election = threading.Condition(threading.Lock())
    start = threading.Thread.start
    starting, release, stop = threading.Event(), threading.Event(), threading.Event()
    starts = []
    admitted = []
    second_entered = threading.Event()
    monkeypatch.setattr(state, "_consume", lambda: stop.wait(10))

    def staged_start(thread):
        if thread.name == "alk-reaper":
            assert state.election.acquire(blocking=False)
            state.election.release()
            starts.append(thread)
            starting.set()
            assert release.wait(4)
        start(thread)

    def admit(second):
        if second:
            second_entered.set()
        state.ensure_worker()
        admitted.append(state.worker)

    monkeypatch.setattr(threading.Thread, "start", staged_start)
    callers = [
        threading.Thread(target=admit, args=(second,)) for second in (False, True)
    ]
    try:
        callers[0].start()
        assert starting.wait(3)
        callers[1].start()
        assert second_entered.wait(3) and not admitted
        release.set()
        for caller in callers:
            caller.join(3)
        assert starts == [state.worker] and admitted == starts * 2
        assert state.worker.is_alive()
    finally:
        release.set()
        stop.set()
        for caller in callers:
            if caller.ident is not None:
                caller.join(3)
        for worker in starts:
            if worker.ident is not None:
                worker.join(3)


@pytest.mark.parametrize("phase", ["waiting-for-starter", "after-admission"])
def test_admission_timeout_precedes_launch_resources(monkeypatch, phase):
    state = containment._Cleanup()
    subtree = Mock()
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_cleanup", state)
    starting, release, stop = threading.Event(), threading.Event(), threading.Event()
    starter_released = threading.Event()
    start = threading.Thread.start

    def delayed_start(thread):
        if thread.name == "alk-reaper":
            starting.set()
            release.wait(5)
            starter_released.set()
        start(thread)

    monkeypatch.setattr(state, "_consume", lambda: stop.wait(10))
    command = ["true"]
    timeout = 0.1
    first = threading.Thread(target=state.ensure_worker)
    pipe = Mock(side_effect=AssertionError("opened a pipe after expiry"))
    spawn = Mock(side_effect=AssertionError("spawned after expiry"))
    monkeypatch.setattr(os, "pipe", pipe)
    monkeypatch.setattr(subprocess, "Popen", spawn)
    try:
        if phase == "waiting-for-starter":
            monkeypatch.setattr(threading.Thread, "start", delayed_start)
            first.start()
            assert starting.wait(3)
        else:
            # Advance only containment's clock between admission and leaf creation.
            clock = Mock()
            clock.monotonic.return_value = 0.0
            monkeypatch.setattr(containment, "time", clock)
            message = containment._go_message

            def advance(env):
                clock.monotonic.return_value = timeout
                return message(env)

            monkeypatch.setattr(containment, "_go_message", advance)
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            containment.run(command, timeout=timeout)
        assert caught.value.cmd is command and caught.value.timeout == timeout
        if phase == "waiting-for-starter":
            assert not starter_released.is_set()
        subtree.leaf.assert_not_called()
        pipe.assert_not_called()
        spawn.assert_not_called()
    finally:
        release.set()
        if first.ident is not None:
            first.join(3)
        stop.set()
        if state.worker is not None:
            state.worker.join(3)


def test_handoff_ignores_contended_election(monkeypatch, tmp_path):
    subtree = _FailingSubtree(tmp_path / "leaf")
    subtree.current.path.mkdir()
    timeout = subprocess.TimeoutExpired(["true"], 1)
    process = Mock(pid=123, returncode=None)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_write_all", Mock())
    held, release = threading.Event(), threading.Event()
    expired = threading.Event()

    def contend():
        with containment._cleanup.election:
            held.set()
            if not release.wait(5):
                expired.set()

    contender = threading.Thread(target=contend)

    def refuse_ack(fd):
        contender.start()
        assert held.wait(3)
        raise timeout

    monkeypatch.setattr(containment, "_read_all", refuse_ack)
    try:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            containment.popen(["true"])
        assert caught.value is timeout
        assert not expired.is_set()
    finally:
        release.set()
        if contender.ident is not None:
            contender.join(3)
    deadline = time.monotonic() + 3
    while not process.poll.called and time.monotonic() < deadline:
        time.sleep(0.01)
    process.poll.assert_called_once_with()
    assert not subtree.current.path.exists()


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
