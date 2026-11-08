"""The structured record of why a hosted check or build failed, written beside its work.

The platform used to tell a failure of ours from the agent's by reading the guest log's first
``runtime validation:`` line. This file says it instead, from the error itself: ``domain`` is
``harness`` (a bug or capacity limit of ours), ``world`` (a process the harness adds to the
generated world failed for a reason of ours) or ``agent`` (anything else, the agent's code and
services included). ``message`` is one redacted line of the harness's own text; the agent's
output, which follows it in the error, is never copied.

Each failed validation replaces the record and a passing one removes it, so after the run it
names the failure that ended it: a certify that does not repair re-raises a generic error that
is never recorded.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

FAILURE_RECORD = Path(os.environ.get("ALK_FAILURE_RECORD", "/work/failure.json"))
SCHEMA_VERSION = 1
_MESSAGE_CHARS = 300
# The processes the harness adds to a generated world (bundle_author_v2): the managed
# world-db, the generated tool-proxy and mcp-toolbox.
_OWNED_PROCESSES = frozenset({"world-db", "tool-proxy", "mcp-toolbox"})
# Codes raised with an infrastructure domain at every raise site, whatever the process.
_HARNESS_CODES = frozenset(
    {
        "store_statement_failed",
        "resource_capacity_unknown",
        "resource_capacity_unavailable",
    }
)
# A seed that failed only for the harness's own fingerprint check.
_OWNED_SEED_DIAGNOSTICS = frozenset({"source_model_fingerprint_mismatch"})
# Where the harness's text ends and the agent's output lines begin (process_runtime).
_AGENT_OUTPUT = "; last output:"
_TYPED = re.compile(
    r"(?:ProcessRuntimeError: )?([a-z_]+)/([a-z_]+)(?: \(([^)]+)\))?: (.*)", re.S
)


def _typed(exc: BaseException) -> tuple[str, str, str | None, str] | None:
    """``(stage, code, process, detail)`` of the process failure behind ``exc``."""
    from .process_runtime import ProcessRuntimeError

    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ProcessRuntimeError):
            detail = str(current).partition(": ")[2]
            return current.stage, current.code, current.process, detail
        current = current.__cause__ or current.__context__
    found = _TYPED.fullmatch(_own_text(exc))
    return found.groups() if found else None  # type: ignore[return-value]


def _diagnostic_codes(exc: BaseException) -> set[str]:
    """The codes of the structured diagnostics the failure carries, nearest first."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        diagnostics = getattr(current, "diagnostics", None) or ()
        if diagnostics:
            return {str(getattr(item, "code", "")) for item in diagnostics}
        current = current.__cause__ or current.__context__
    return set()


def _own_text(exc: BaseException) -> str:
    return str(exc).split(_AGENT_OUTPUT, 1)[0].strip()


def _domain(
    phase: str, typed: tuple[str, str, str | None, str] | None, codes: set[str]
) -> str:
    """The same rule the platform applied to the log, from the error itself."""
    if phase != "runtime" or typed is None:
        return "agent"
    _stage, code, process, _detail = typed
    if code.startswith("internal_") or code in _HARNESS_CODES:
        return "harness"
    if process not in _OWNED_PROCESSES:
        return "agent"
    if code != "seed_failed":
        return "world"
    return "world" if codes and codes <= _OWNED_SEED_DIAGNOSTICS else "agent"


def record(
    phase: str, exc: BaseException, path: Path | None = None
) -> dict[str, Any] | None:
    """Write this failure in place of any earlier one. Never raises."""
    from .outbound import redact_outbound_text

    target = path or FAILURE_RECORD
    try:
        typed = _typed(exc)
        line = " ".join(redact_outbound_text(_own_text(exc)).split())
        body = {
            "schema_version": SCHEMA_VERSION,
            "domain": _domain(phase, typed, _diagnostic_codes(exc)),
            "phase": phase,
            "stage": typed[0] if typed else None,
            "code": typed[1] if typed else None,
            "process": typed[2] if typed else None,
            "message": line[:_MESSAGE_CHARS],
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(body, sort_keys=True) + "\n", encoding="utf-8")
        return body
    except Exception:  # noqa: BLE001 - the record never changes how the run ends
        return None


def clear(path: Path | None = None) -> None:
    """A validation passed: no earlier failure ended the run. Never raises."""
    try:
        (path or FAILURE_RECORD).unlink(missing_ok=True)
    except OSError:
        pass
