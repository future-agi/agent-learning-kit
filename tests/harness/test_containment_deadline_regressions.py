"""A contained command has one timeout for startup and execution together."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fi.alk.harness import containment


class _FailingLeaf:
    def __init__(self):
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
    def __init__(self):
        self.current = _FailingLeaf()

    def leaf(self):
        return self.current


@pytest.mark.parametrize("backpressure", [False, True], ids=["done-read", "go-write"])
def test_stalled_gate_reaped_and_owned_pipes_closed(monkeypatch, tmp_path, caplog, backpressure):
    subtree = _FailingSubtree()
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_GATE", "import time; time.sleep(8)")
    # Larger than a Linux pipe. The gate never reads, so only the deadline interrupts the write.
    env = {"PAYLOAD": "a" * (8 << 20)} if backpressure else {}
    command = ["true"]
    fds = Path("/proc/self/fd")
    before = set(os.listdir(fds))
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired) as caught:
        containment.run(command, input=b"data", capture_output=True, env=env, timeout=0.5)

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
    subtree = _FailingSubtree()
    monkeypatch.setattr(containment, "_containment", subtree)
    monkeypatch.setattr(containment, "_GATE", "import time; time.sleep(1)\n" + containment._GATE)
    command = [sys.executable, "-c", "import time; print('started', flush=True); time.sleep(2)"]
    with (tmp_path / "output").open("w+") as output:
        with pytest.raises(subprocess.TimeoutExpired) as caught:
            containment.run(command, env={}, timeout=2.5, stdout=output)
        output.seek(0)  # A caller's file remains usable after cleanup.
        assert output.read() == "started\n"
    assert (caught.value.cmd, caught.value.timeout) == (command, 2.5)
    assert "simulated leaf removal failure" in caplog.text
