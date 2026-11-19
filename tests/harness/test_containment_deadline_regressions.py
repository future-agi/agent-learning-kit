"""A contained command has one timeout for startup and execution together."""

import os
import signal
import subprocess
from contextlib import suppress
from unittest.mock import Mock

import pytest

from fi.alk.harness import containment


def test_gate_startup_uses_the_command_timeout(monkeypatch):
    subtree = Mock()
    leaf = subtree.leaf.return_value

    def kill_leaf():
        with suppress(ProcessLookupError):
            os.kill(leaf.add.call_args.args[0], signal.SIGKILL)

    leaf.close.side_effect = kill_leaf
    monkeypatch.setattr(containment, "_containment", subtree)
    # Finite delay: an implementation with an unbounded handshake fails rather than hanging.
    monkeypatch.setattr(containment, "_GATE", "import time; time.sleep(1)\n" + containment._GATE)
    command = ["true"]

    with pytest.raises(subprocess.TimeoutExpired) as caught:
        containment.run(command, env={}, timeout=0.1)

    assert (caught.value.cmd, caught.value.timeout) == (command, 0.1)
    leaf.close.assert_called_once_with()
    with pytest.raises(ChildProcessError):
        os.waitpid(leaf.add.call_args.args[0], os.WNOHANG)
