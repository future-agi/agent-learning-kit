"""FutureAGIResultSink — local write + Stage-6 submission seam.

Composes ``LocalFilesystemResultSink`` for the on-disk layout defined
in plan §8 and adds a ``submit(...)`` call that records the intended
Stage-6 ingestion routes (§11.2) into ``submission.json``. Real HTTP
submission is deferred; when ``FUTURE_AGI_API_URL`` and the API key
pair are absent the sink records ``status: "not_configured"`` and
returns cleanly, so local runs stay unaffected.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fi.simulate.runtime import (
    CanonicalEvent,
    SimulationPlan,
    SimulationReport,
    SimulationSpec,
)

from .filesystem import LocalFilesystemResultSink

FUTURE_AGI_INGESTION_ROUTES: dict[str, str] = {
    "test_case": "PUT /simulate/runs/{run_id}/test-cases/{test_case_id}/",
    "events_batch": "POST /simulate/runs/{run_id}/events/batch/",
    "artifact_presign": "POST /simulate/runs/{run_id}/artifacts/presign/",
    "artifact_put": "PUT /simulate/runs/{run_id}/artifacts/{artifact_id}/",
    "complete": "POST /simulate/runs/{run_id}/complete/",
}
_API_KEY_ENV = ("FI_API_KEY", "FUTURE_AGI_API_KEY", "AGENT_LEARNING_API_KEY")
_SECRET_KEY_ENV = ("FI_SECRET_KEY", "FUTURE_AGI_SECRET_KEY", "AGENT_LEARNING_SECRET_KEY")
_API_URL_ENV = ("FI_BASE_URL", "FUTURE_AGI_API_URL", "AGENT_LEARNING_API_URL")


class FutureAGIResultSink:
    """Local sink + deferred platform submission.

    Wraps a ``LocalFilesystemResultSink`` under the hood — every method
    that the ``ResultSink`` Protocol expects delegates to it. On top,
    ``submit`` writes a ``submission.json`` marker containing the
    intended ingestion route table and payload counts. When the
    platform HTTP client lands (Stage 6) that method becomes the actual
    upload path.
    """

    def __init__(
        self,
        *,
        root: str | Path = ".fagi/runs",
        api_url: str | None = None,
        api_key_env: tuple[str, ...] = _API_KEY_ENV,
        secret_key_env: tuple[str, ...] = _SECRET_KEY_ENV,
    ) -> None:
        self._local = LocalFilesystemResultSink(root=root)
        self._api_url = api_url or _first_env(_API_URL_ENV)
        self._api_key_env = api_key_env
        self._secret_key_env = secret_key_env
        self._event_count = 0
        self._spec: SimulationSpec | None = None
        self._plan: SimulationPlan | None = None

    @property
    def run_directory(self) -> Path | None:
        return self._local.run_directory

    def prepare(
        self,
        spec: SimulationSpec,
        plan: SimulationPlan | None = None,
    ) -> Path:
        self._spec = spec
        self._plan = plan
        self._event_count = 0
        return self._local.prepare(spec, plan)

    def write_event(self, event: CanonicalEvent) -> None:
        self._event_count += 1
        self._local.write_event(event)

    def write_report(self, report: SimulationReport) -> Path:
        report_path = self._local.write_report(report)
        # Auto-write a "not_configured" marker so consumers can tell
        # this sink was chosen even when submission is deferred.
        self.submit(report)
        return report_path

    def submit(self, report: SimulationReport) -> dict[str, Any]:
        run_directory = self._local.run_directory
        if run_directory is None:
            raise RuntimeError("result_sink_not_prepared")
        api_key = _first_env(self._api_key_env)
        secret_key = _first_env(self._secret_key_env)
        status = "not_configured"
        reason = None
        if self._api_url and api_key and secret_key:
            status = "deferred"
            reason = "http_submission_not_implemented"
        elif not self._api_url:
            reason = "future_agi_api_url_missing"
        elif not api_key or not secret_key:
            reason = "future_agi_credentials_missing"
        payload = {
            "schema_version": "futureagi.submission.v1",
            "run_id": report.run_id,
            "report_hash": report.report_hash,
            "test_cases": len(report.test_cases),
            "artifact_count": len(report.artifacts.entries),
            "events_recorded": self._event_count,
            "api_url": self._api_url,
            "status": status,
            "reason": reason,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ingestion_routes": _resolved_routes(report.run_id),
        }
        submission_path = run_directory / "submission.json"
        submission_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return payload


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _resolved_routes(run_id: str) -> dict[str, str]:
    return {
        key: template.format(run_id=run_id, test_case_id="{test_case_id}", artifact_id="{artifact_id}")
        for key, template in FUTURE_AGI_INGESTION_ROUTES.items()
    }


__all__ = ["FUTURE_AGI_INGESTION_ROUTES", "FutureAGIResultSink"]
