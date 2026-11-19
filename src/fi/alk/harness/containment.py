"""Every customer execution in a cgroup of its own, so stopping it stops all it started.

Under per-user isolation a customer process runs as a declared user that every world shares. A
process that forks and calls ``setsid()`` leaves its process group, so signalling the group does
not reach it, and it would outlive its world. Each execution therefore gets one cgroup-v2 leaf
under the harness's own cgroup, and its stop writes the leaf's ``cgroup.kill``: the kernel
kills every process in it, wherever its session went.

A process joins its leaf before any customer code runs: it starts as a small gate, the harness's
own interpreter, that waits on a pipe until the harness has moved it, then executes the real
command. The gate starts with an empty environment, so nothing the customer declares (an
``LD_PRELOAD``, say) acts before the move; the command's own environment arrives over the pipe
as the signal to go. A process cannot leave its leaf, because a declared user cannot write any
``cgroup.procs``. Leaves sit under the harness's own cgroup, so the limits the harness reads for
admission still apply.
"""

from __future__ import annotations

import atexit
import errno
import itertools
import logging
import os
import selectors
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CGROUP_ROOT = Path("/sys/fs/cgroup")
_PROC_SELF_CGROUP = Path("/proc/self/cgroup")
_EMPTY_WAIT_SECONDS = 5.0
# The gate reads the command's environment from one inherited pipe, up to EOF, and executes the
# command with it. The message is ``go <length>\0`` and the environment: one that is not whole
# (the harness gone before it moved the process, or while it wrote) ends the gate unrun. The
# second pipe closes on exec: EOF tells the harness the command started, and an errno written
# there tells it why not, so the exec is judged as the declared user, not the harness. Not a
# shell: dash cannot redirect from a descriptor above 9, and the pipes are never that low in a
# harness with open files.
_GATE = (
    "import os, sys\n"
    "go, done = int(sys.argv[1]), int(sys.argv[2])\n"
    "os.set_inheritable(done, False)\n"
    "chunks = []\n"
    "while chunk := os.read(go, 65536):\n"
    "    chunks.append(chunk)\n"
    "os.close(go)\n"
    "head, _, body = b''.join(chunks).partition(b'\\0')\n"
    "if head != b'go ' + str(len(body)).encode():\n"
    "    os._exit(125)\n"
    "env = dict(e.split(b'=', 1) for e in body.split(b'\\0') if e)\n"
    "try:\n"
    "    os.execvpe(sys.argv[3], sys.argv[3:], env)\n"
    "except OSError as exc:\n"
    "    os.write(done, str(exc.errno or 0).encode())\n"
    "os._exit(127)\n"
)
# Nothing of the harness's or the customer's: the gate needs no variable to run.
_GATE_ENVIRONMENT: dict[str, str] = {}


def _self_cgroup(proc_self_cgroup: Path, cgroup_root: Path) -> Path | None:
    """This process's own cgroup-v2 directory, or None without a unified hierarchy."""
    try:
        for line in proc_self_cgroup.read_text(encoding="utf-8").splitlines():
            if line.startswith("0::"):
                return cgroup_root / line[3:].strip().lstrip("/")
    except OSError:
        pass
    return None


@dataclass
class Leaf:
    """One execution's cgroup. Stopping it kills every process it holds."""

    path: Path

    def add(self, pid: int) -> None:
        (self.path / "cgroup.procs").write_text(str(pid), encoding="utf-8")

    def kill(self) -> None:
        try:
            (self.path / "cgroup.kill").write_text("1", encoding="utf-8")
        except FileNotFoundError:
            pass  # already removed

    def close(self, timeout: float = _EMPTY_WAIT_SECONDS) -> None:
        """Kill what is left, wait until the leaf is empty, and remove it. Never raises."""
        try:
            self.kill()
            deadline = time.monotonic() + timeout
            while not self._empty() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.path.rmdir()
        except FileNotFoundError:
            pass
        except OSError as exc:
            # A leaf that will not empty is a process the kernel cannot kill; say so.
            logger.warning("containment: %s did not empty (%s)", self.path, exc)

    def _empty(self) -> bool:
        try:
            events = (self.path / "cgroup.events").read_text(encoding="utf-8")
        except FileNotFoundError:
            return True
        return "populated 0" in events


class Containment:
    """The harness's own cgroup subtree, ``<own cgroup>/alk-<pid>``, and its leaves."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self._numbers = itertools.count()
        self._lock = threading.Lock()

    def leaf(self) -> Leaf:
        with self._lock:
            number = next(self._numbers)
        path = self.root / str(number)
        path.mkdir()
        return Leaf(path)

    def sweep(self) -> None:
        """Kill every leaf still standing; for the harness's own exit."""
        if not self.root.exists():
            return
        for child in sorted(self.root.iterdir()):
            if child.is_dir():
                Leaf(child).close()
        try:
            self.root.rmdir()
        except OSError:
            pass


def probe(
    proc_self_cgroup: Path = _PROC_SELF_CGROUP, cgroup_root: Path = _CGROUP_ROOT
) -> Containment | None:
    """A working subtree, proven by making and removing a leaf; None when this host has none."""
    own = _self_cgroup(proc_self_cgroup, cgroup_root)
    if own is None:
        return None
    root = own / f"alk-{os.getpid()}"
    usable = False
    try:
        root.mkdir(exist_ok=True)
        trial = root / "probe"
        trial.mkdir()
        try:
            # `cgroup.kill` (Linux 5.14+) is what stops a whole leaf at once.
            usable = (trial / "cgroup.kill").exists() and (
                trial / "cgroup.procs"
            ).exists()
        finally:
            trial.rmdir()
    except OSError:
        usable = False
    if not usable:
        try:
            root.rmdir()
        except OSError:
            pass
        return None
    return Containment(root)


_containment: Containment | None = None


def enable(containment: Containment) -> None:
    """Contain every customer execution from now on; the leaves go with the harness."""
    global _containment
    _containment = containment
    atexit.register(containment.sweep)


def active() -> Containment | None:
    return _containment


def _go_message(env: Mapping[Any, Any] | None) -> bytes:
    """The command's environment as the gate's go message, refused where ``subprocess`` would."""
    entries = []
    for key, value in (os.environb if env is None else env).items():
        name, text = os.fsencode(key), os.fsencode(value)
        if not name or b"=" in name or b"\0" in name or b"\0" in text:
            raise ValueError(f"illegal environment variable name or value: {key!r}")
        entries.append(name + b"=" + text)
    body = b"\0".join(entries)
    return b"go " + str(len(body)).encode() + b"\0" + body


@dataclass(frozen=True)
class _Deadline:
    args: Sequence[str]
    timeout: float
    end: float

    def remaining(self) -> float:
        return max(0.0, self.end - time.monotonic())

    def wait(self, fd: int, event: int) -> None:
        with selectors.DefaultSelector() as selector:
            selector.register(fd, event)
            left = self.remaining()
            if not left or not selector.select(left):
                raise subprocess.TimeoutExpired(self.args, self.timeout)


def _write_all(fd: int, data: bytes, deadline: _Deadline | None = None) -> None:
    view = memoryview(data)
    if deadline is not None:
        os.set_blocking(fd, False)
    while view:
        if deadline is not None:
            deadline.wait(fd, selectors.EVENT_WRITE)
        view = view[os.write(fd, view) :]


def _read_all(fd: int, deadline: _Deadline | None = None) -> bytes:
    chunks = []
    while True:
        if deadline is not None:
            deadline.wait(fd, selectors.EVENT_READ)
        chunk = os.read(fd, 4096)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _close(*fds: int) -> None:
    for fd in fds:
        if fd >= 0:
            os.close(fd)


def _abort_child(process: subprocess.Popen, leaf: Leaf | None) -> None:
    # A failed leaf kill must not leave the direct child alive or its parent blocked in wait.
    try:
        process.kill()
    except OSError as exc:
        logger.warning("containment: could not kill child %s (%s)", process.pid, exc)
    if leaf is not None:
        try:
            leaf.close()  # Includes descendants; reports a leaf that could not be removed.
        except OSError as exc:
            logger.warning(
                "containment: could not close leaf for child %s (%s)", process.pid, exc
            )
    try:
        process.wait(timeout=_EMPTY_WAIT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("containment: could not reap child %s (%s)", process.pid, exc)
    # These attributes are populated for PIPE only, never for caller-owned file objects.
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError as exc:
                logger.warning("containment: could not close child stream (%s)", exc)


def popen(
    args: Sequence[str], *, _deadline: _Deadline | None = None, **kwargs: Any
) -> tuple[subprocess.Popen, Leaf | None]:
    """``subprocess.Popen`` for a customer execution, inside a leaf of its own when contained.

    Without containment this is a plain Popen. With it, the process starts as the gate, joins
    a fresh leaf, and only then runs ``args`` with ``env``; any process it starts is born inside
    the leaf. A command that cannot be executed raises here, as ``subprocess.Popen`` does.
    """
    containment = _containment
    if containment is None:
        return subprocess.Popen(list(args), **kwargs), None
    argv = [str(arg) for arg in args]
    message = _go_message(kwargs.pop("env", None))
    pass_fds = tuple(kwargs.pop("pass_fds", ()))
    leaf = containment.leaf()
    go_read = go_write = done_read = done_write = -1
    try:
        go_read, go_write = os.pipe()
        done_read, done_write = os.pipe()
        process = subprocess.Popen(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                _GATE,
                str(go_read),
                str(done_write),
                *argv,
            ],
            env=_GATE_ENVIRONMENT,
            pass_fds=(*pass_fds, go_read, done_write),
            **kwargs,
        )
    except BaseException:
        _close(go_read, go_write, done_read, done_write)
        leaf.close()
        raise
    _close(go_read, done_write)
    try:
        leaf.add(process.pid)
        _write_all(go_write, message, _deadline)
        _close(go_write)
        go_write = -1
        # EOF once the command is executing.
        failure = (
            _read_all(done_read)
            if _deadline is None
            else _read_all(done_read, _deadline)
        )
        if failure:
            code = int(failure) or errno.ENOEXEC
            raise OSError(code, os.strerror(code), argv[0])
    except BaseException:
        _abort_child(process, leaf)
        raise
    finally:
        _close(go_write, done_read)
    return process, leaf


def run(
    args: Sequence[str],
    *,
    input: str | bytes | None = None,
    timeout: float | None = None,
    check: bool = False,
    capture_output: bool = False,
    **kwargs: Any,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` for a customer execution, in a session of its own: what it started never
    outlives it. On a timeout or any error its process group is killed, and its leaf with
    containment; with containment the leaf is also closed when it exits."""
    if capture_output:
        kwargs["stdout"] = subprocess.PIPE
        kwargs["stderr"] = subprocess.PIPE
    if input is not None:
        kwargs["stdin"] = subprocess.PIPE
    deadline = (
        _Deadline(args, timeout, time.monotonic() + timeout)
        if timeout is not None and active() is not None
        else None
    )
    process, leaf = popen(args, _deadline=deadline, start_new_session=True, **kwargs)
    try:
        stdout, stderr = process.communicate(
            input=input, timeout=deadline.remaining() if deadline else timeout
        )
    except BaseException as exc:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError as kill_error:
            logger.warning(
                "containment: could not kill group %s (%s)", process.pid, kill_error
            )
        _abort_child(process, leaf)
        if deadline is not None and isinstance(exc, subprocess.TimeoutExpired):
            raise subprocess.TimeoutExpired(
                args, deadline.timeout, output=exc.output, stderr=exc.stderr
            ) from exc
        raise
    else:
        if leaf is not None:
            leaf.close()
    if check and process.returncode:
        raise subprocess.CalledProcessError(
            process.returncode, list(args), stdout, stderr
        )
    return subprocess.CompletedProcess(list(args), process.returncode, stdout, stderr)
