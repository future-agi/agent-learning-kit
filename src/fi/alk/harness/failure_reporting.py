"""Safe, structured failure handoff between CLI stages and harness controllers."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any


FAILURE_PATH_ENVIRONMENT = "ALK_HARNESS_FAILURE_PATH"
_last_failure: dict[str, Any] | None = None
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|authorization|credential)"
    r"\s*[:=]\s*([^\s,;]+)"
)


def sanitize_failure_message(
    message: object, *, source: str | Path | None = None
) -> str:
    """Return a bounded explanation suitable for a customer-visible job result."""
    value = str(message).replace("\r", " ").replace("\n", " ").strip()
    if source:
        source_value = str(Path(source).expanduser().resolve())
        value = value.replace(source_value, "the submitted repository")
    for name, secret in os.environ.items():
        lowered = name.lower()
        if (
            secret
            and len(secret) >= 6
            and any(
                marker in lowered
                for marker in ("secret", "token", "password", "api_key", "credential")
            )
        ):
            value = value.replace(secret, "[REDACTED]")
    value = _SENSITIVE_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=[REDACTED]", value
    )
    return value[:1000] or "The harness stage failed without an explanation"


def clear_stage_failure() -> None:
    global _last_failure
    _last_failure = None
    configured = os.getenv(FAILURE_PATH_ENVIRONMENT, "").strip()
    if configured:
        Path(configured).unlink(missing_ok=True)


def record_stage_failure(
    code: str,
    message: object,
    *,
    source: str | Path | None = None,
    details: dict[str, Any] | None = None,
    retryable: bool = False,
    action: str = "",
) -> dict[str, Any]:
    """Record one sanitized failure in memory and, when requested, a private sidecar."""
    global _last_failure
    safe_details = {
        str(key): value
        for key, value in (details or {}).items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    _last_failure = {
        "code": re.sub(r"[^a-z0-9_]+", "_", code.lower()).strip("_") or "stage_failed",
        "detail": sanitize_failure_message(message, source=source),
        "details": safe_details,
        "retryable": bool(retryable),
        "action": sanitize_failure_message(action, source=source) if action else "",
    }
    configured = os.getenv(FAILURE_PATH_ENVIRONMENT, "").strip()
    if configured:
        target = Path(configured)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.tmp")
        temporary.write_text(json.dumps(_last_failure), encoding="utf-8")
        temporary.replace(target)
    return dict(_last_failure)


def take_stage_failure() -> dict[str, Any] | None:
    global _last_failure
    value = _last_failure
    _last_failure = None
    return dict(value) if value else None


def load_stage_failure(path: str | Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


__all__ = [
    "FAILURE_PATH_ENVIRONMENT",
    "clear_stage_failure",
    "load_stage_failure",
    "record_stage_failure",
    "sanitize_failure_message",
    "take_stage_failure",
]
