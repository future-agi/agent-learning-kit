"""The platform's reader of a failure, as a check a kit test can run on a failure dict.

Mirrors, on the platform's feat/TH-8812-agent-layer branch:
  futureagi/simulate/services/hosted_harness_gateway.py  refusal_failure
  futureagi/simulate/serializers/hosted_harness.py       HarnessFailureSerializer
A wrong type anywhere invalidates the whole failure; a code that doesn't match the pattern is
accepted but never shown. DRF trims a string field's spaces before it checks it, so a value of
spaces alone is refused as blank.
"""

from __future__ import annotations

import re
from typing import Any

DOMAINS = frozenset(
    {
        "agent",
        "simulator",
        "environment",
        "connectivity",
        "infrastructure",
        "grading",
        "platform_sync",
        "artifact",
    }
)
STAGE_MAX = 64
CODE_MAX = 128
MESSAGE_MAX = 2000
SHOWN_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")


def platform_failure_problems(failure: Any) -> list[str]:
    """What the platform would refuse in ``failure``; empty when it reads whole."""
    if not isinstance(failure, dict):
        return ["failure is not a dict"]
    problems: list[str] = []
    if failure.get("domain") not in DOMAINS:
        problems.append(f"domain {failure.get('domain')!r} is not one of the platform's")
    stage = _trimmed(failure.get("stage"))
    if not isinstance(stage, str) or not stage or len(stage) > STAGE_MAX:
        problems.append("stage is not a string of 1..64 characters")
    code = _trimmed(failure.get("code"))
    if not isinstance(code, str) or not code or len(code) > CODE_MAX:
        problems.append("code is not a string of 1..128 characters")
    elif not SHOWN_CODE.fullmatch(code):
        problems.append(f"code {code!r} would not be shown (pattern [a-z][a-z0-9_]{{0,63}})")
    message = _trimmed(failure.get("message"))
    if not isinstance(message, str) or not message or len(message) > MESSAGE_MAX:
        problems.append("message is not a string of 1..2000 characters")
    if "retryable" in failure and not isinstance(failure["retryable"], bool):
        problems.append("retryable is not a bool")
    if "details" in failure and not isinstance(failure["details"], dict):
        problems.append("details is not a dict")
    return problems


def _trimmed(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


def assert_platform_reads(failure: Any) -> None:
    assert platform_failure_problems(failure) == [], failure
