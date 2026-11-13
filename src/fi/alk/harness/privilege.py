"""Whether customer processes run as their own OS users, decided once per harness process.

A hosted platform that can grant it starts the harness as its own user holding the capabilities
below, and sets ``ALK_PROCESS_ISOLATION=required`` in the same step. The harness then clears its
ambient capability set, so nothing it executes inherits a capability, and every customer process
and build step runs as its declared user. A process that only holds the harness's files then
cannot read them, held-back cases included.

Per-user isolation also needs containment: every customer execution in a cgroup of its own, so
stopping it stops whatever it started (`containment.py`). A host where that cannot work gets no
per-user isolation.

Without the grant, every process runs as the harness's own user, as before. When the platform
asked for isolation and the capabilities or the containment did not arrive, the harness refuses
to start rather than run customer code next to the cases it must not see.
"""

from __future__ import annotations

import ctypes
import logging
import os
from collections.abc import Callable, Mapping
from enum import Enum
from pathlib import Path

from . import containment

logger = logging.getLogger(__name__)

ISOLATION_ENV = "ALK_PROCESS_ISOLATION"

# chown, dac_override, fowner, kill, setgid, setuid: to start a process as its declared user,
# and to stop, re-mode and remove what that process owns afterwards.
_CAPABILITIES = (1 << 0) | (1 << 1) | (1 << 3) | (1 << 5) | (1 << 6) | (1 << 7)
_PR_CAP_AMBIENT = 47
_PR_CAP_AMBIENT_CLEAR_ALL = 4


class IsolationMode(str, Enum):
    FIXED_USER = "fixed_user"
    PER_USER = "per_user"


class IsolationUnavailable(RuntimeError):
    """The platform asked for per-user isolation, and this process cannot provide it."""


_mode = IsolationMode.FIXED_USER


def isolation_mode() -> IsolationMode:
    """The mode ``establish_isolation`` chose; fixed-user until it runs."""
    return _mode


def establish_isolation(
    environ: Mapping[str, str] | None = None,
    *,
    status_path: Path = Path("/proc/self/status"),
    contain: Callable[[], containment.Containment | None] = containment.probe,
) -> IsolationMode:
    """Choose this process's mode. Call once, before the harness starts any child."""
    global _mode
    values: Mapping[str, str] = os.environ if environ is None else environ
    required = values.get(ISOLATION_ENV) == "required"
    if _effective_capabilities(status_path) & _CAPABILITIES != _CAPABILITIES:
        if required:
            raise IsolationUnavailable(
                "process_isolation_unavailable: the platform asked for per-user isolation, but "
                "this process does not hold the capabilities it needs"
            )
        _mode = IsolationMode.FIXED_USER
        return _mode
    subtree = contain()
    if subtree is None:
        if required:
            raise IsolationUnavailable(
                "process_isolation_unavailable: the platform asked for per-user isolation, but "
                "this host gives the harness no cgroup to contain each process in"
            )
        # A container that holds the capabilities but a read-only cgroupfs (a local Docker
        # harness): run as before, and say so, rather than isolate what it cannot stop.
        logger.warning(
            "per-user isolation is off: no writable cgroup to contain customer processes; "
            "every process runs as the harness's own user"
        )
        _mode = IsolationMode.FIXED_USER
        return _mode
    _clear_ambient_capabilities()
    # The harness's own files stay its own; a declared-user child gets the usual umask back.
    os.umask(0o077)
    containment.enable(subtree)
    _mode = IsolationMode.PER_USER
    return _mode


def _effective_capabilities(status_path: Path) -> int:
    try:
        text = status_path.read_text(encoding="utf-8")
    except OSError:
        return 0  # no /proc (macOS): no capabilities to use
    for line in text.splitlines():
        if line.startswith("CapEff:"):
            return int(line.split()[1], 16)
    return 0


def _clear_ambient_capabilities() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_CAP_AMBIENT, _PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) != 0:
        raise IsolationUnavailable(
            "process_isolation_unavailable: could not clear the ambient capability set "
            f"(errno {ctypes.get_errno()})"
        )
