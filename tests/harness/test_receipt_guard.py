"""A graded result never leaves the kit carrying a failure: the platform refuses that shape and the trial reads skipped."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from fi.alk.harness.outbound import ScenarioStatus, build_result_receipt, format_rfc3339_millis

_STARTED = datetime(2026, 8, 25, 10, 14, 3, 412000, tzinfo=timezone.utc)
_ENDED = datetime(2026, 8, 25, 10, 17, 7, 623000, tzinfo=timezone.utc)


def _receipt(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        job_id="j1",
        attempt_id="a1",
        attempt_number=1,
        scenario_key="refund-once",
        scenario_id="sid-1",
        scenario_attempt=1,
        world_index=0,
        status=ScenarioStatus.FAILED,
        sub_goals=[{"name": "refunded", "held": True, "reason": None, "judged": False}],
        evaluations=[],
        call={
            "started_at": format_rfc3339_millis(_STARTED),
            "ended_at": format_rfc3339_millis(_ENDED),
            "duration_ms": 184211,
            "turns": 2,
            "transcript_artifact": "sha256:" + "a" * 64,
            "recording_artifacts": [],
        },
        failure=None,
    )
    base.update(overrides)
    return build_result_receipt(**base)


@pytest.mark.parametrize("status", [ScenarioStatus.PASSED, ScenarioStatus.FAILED])
def test_a_graded_receipt_carrying_a_failure_is_refused_before_it_leaves(status) -> None:
    failure = {"domain": "agent", "stage": "running", "code": "duplicate_commit", "message": "committed twice"}
    with pytest.raises(ValueError, match="graded_receipt_must_not_include_failure"):
        _receipt(status=status, failure=failure)
    assert _receipt(status=status, reason="committed twice")["failure"] is None
