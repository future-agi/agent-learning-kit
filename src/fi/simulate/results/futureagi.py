"""FutureAGIResultSink — local write + real platform submission.

Composes ``LocalFilesystemResultSink`` and adds ``submit(...)`` that POSTs
report data to the Future AGI platform using the ALK ingestion endpoints:

  POST  /simulate/alk-simulate/run-tests/{run_test_id}/test-executions/
  POST  /simulate/alk-simulate/test-executions/{test_execution_id}/batch/
  PATCH /simulate/alk-simulate/call-executions/{call_execution_id}/result/

Configuration is env-driven so local runs stay unaffected when the platform
target is not set:

  FI_BASE_URL / FUTURE_AGI_API_URL / AGENT_LEARNING_API_URL — base URL
  FI_API_KEY / FUTURE_AGI_API_KEY / AGENT_LEARNING_API_KEY — x-api-key
  FI_SECRET_KEY / FUTURE_AGI_SECRET_KEY / AGENT_LEARNING_SECRET_KEY — x-secret-key
  FI_RUN_TEST_ID / FUTURE_AGI_RUN_TEST_ID / AGENT_LEARNING_RUN_TEST_ID — target run test

When any of those are absent the sink records ``status: "not_configured"``
in ``submission.json`` and returns cleanly — no HTTP is attempted.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from fi.simulate.runtime import (
    CanonicalEvent,
    SimulationPlan,
    SimulationReport,
    SimulationSpec,
)

from .filesystem import LocalFilesystemResultSink

_STATUS_MAP = {
    "completed": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "timed_out": "failed",
    "agent_unavailable": "failed",
}
_API_KEY_ENV = ("FI_API_KEY", "FUTURE_AGI_API_KEY", "AGENT_LEARNING_API_KEY")
_SECRET_KEY_ENV = ("FI_SECRET_KEY", "FUTURE_AGI_SECRET_KEY", "AGENT_LEARNING_SECRET_KEY")
_API_URL_ENV = ("FI_BASE_URL", "FUTURE_AGI_API_URL", "AGENT_LEARNING_API_URL")
_RUN_TEST_ID_ENV = (
    "FI_RUN_TEST_ID",
    "FUTURE_AGI_RUN_TEST_ID",
    "AGENT_LEARNING_RUN_TEST_ID",
)
_HTTP_TIMEOUT_SECONDS = 60.0


class FutureAGIResultSink:
    """Local sink + platform submission over HTTP."""

    def __init__(
        self,
        *,
        root: str | Path = ".fagi/runs",
        api_url: str | None = None,
        api_key_env: tuple[str, ...] = _API_KEY_ENV,
        secret_key_env: tuple[str, ...] = _SECRET_KEY_ENV,
        run_test_id: str | None = None,
    ) -> None:
        self._local = LocalFilesystemResultSink(root=root)
        self._api_url = api_url or _first_env(_API_URL_ENV)
        self._api_key_env = api_key_env
        self._secret_key_env = secret_key_env
        self._run_test_id = run_test_id or _first_env(_RUN_TEST_ID_ENV)
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
        self.submit(report)
        return report_path

    def submit(self, report: SimulationReport) -> dict[str, Any]:
        run_directory = self._local.run_directory
        if run_directory is None:
            raise RuntimeError("result_sink_not_prepared")

        api_key = _first_env(self._api_key_env)
        secret_key = _first_env(self._secret_key_env)

        submission: dict[str, Any] = {
            "schema_version": "futureagi.submission.v1",
            "run_id": report.run_id,
            "report_hash": report.report_hash,
            "test_cases": len(report.test_cases),
            "artifact_count": len(report.artifacts.entries),
            "events_recorded": self._event_count,
            "api_url": self._api_url,
            "run_test_id": self._run_test_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

        missing = _missing_config(
            api_url=self._api_url,
            api_key=api_key,
            secret_key=secret_key,
            run_test_id=self._run_test_id,
        )
        if missing:
            submission["status"] = "not_configured"
            submission["reason"] = "missing_config: " + ",".join(missing)
            _write_submission(run_directory, submission)
            return submission

        try:
            outcome = _submit_via_http(
                report=report,
                base_url=self._api_url,
                api_key=api_key,
                secret_key=secret_key,
                run_test_id=self._run_test_id,
            )
            submission.update(outcome)
            submission["status"] = "submitted"
        except Exception as exc:
            submission["status"] = "failed"
            submission["reason"] = f"submission_error: {exc.__class__.__name__}: {exc}"

        _write_submission(run_directory, submission)
        return submission


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _missing_config(
    *,
    api_url: str | None,
    api_key: str | None,
    secret_key: str | None,
    run_test_id: str | None,
) -> list[str]:
    missing: list[str] = []
    if not api_url:
        missing.append("api_url")
    if not api_key:
        missing.append("api_key")
    if not secret_key:
        missing.append("secret_key")
    if not run_test_id:
        missing.append("run_test_id")
    return missing


def _submit_via_http(
    *,
    report: SimulationReport,
    base_url: str,
    api_key: str,
    secret_key: str,
    run_test_id: str,
) -> dict[str, Any]:
    headers = {
        "x-api-key": api_key,
        "x-secret-key": secret_key,
        "Content-Type": "application/json",
    }
    with httpx.Client(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=_HTTP_TIMEOUT_SECONDS,
    ) as client:
        start = client.post(
            f"/simulate/api/alk-simulate/run-tests/{run_test_id}/test-executions/",
            json={},
        )
        start.raise_for_status()
        start_data = _unwrap(start.json())
        test_execution_id = start_data["test_execution_id"]

        call_execution_ids: list[str] = []
        for _ in range(64):  # hard cap to prevent runaway
            resp = client.post(
                f"/simulate/api/alk-simulate/test-executions/{test_execution_id}/batch/",
                json={},
            )
            resp.raise_for_status()
            body = _unwrap(resp.json())
            call_execution_ids.extend(body["call_execution_ids"])
            if not body.get("has_more"):
                break

        submitted_ids: list[str] = []
        failed: list[dict[str, Any]] = []
        for call_id, case in zip(call_execution_ids, report.test_cases):
            payload = _build_result_payload(case)
            resp = client.patch(
                f"/simulate/api/alk-simulate/call-executions/{call_id}/result/",
                json=payload,
            )
            if resp.is_error:
                failed.append(
                    {
                        "call_execution_id": call_id,
                        "status_code": resp.status_code,
                        "body": _safe_body(resp),
                    }
                )
            else:
                submitted_ids.append(call_id)

    return {
        "test_execution_id": test_execution_id,
        "allocated_call_executions": call_execution_ids,
        "submitted_call_executions": submitted_ids,
        "failed_call_executions": failed,
    }


def _unwrap(body: Any) -> dict[str, Any]:
    if isinstance(body, dict) and "result" in body and isinstance(body["result"], dict):
        return body["result"]
    if isinstance(body, dict):
        return body
    raise ValueError(f"unexpected_response_shape: {body!r}")


def _safe_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:500]


def _build_result_payload(case) -> dict[str, Any]:
    """Map a SimulationTestCaseResult into the ALK ingestion PATCH body.

    Backend derives conversation metrics and CSAT from the transcript, so
    the SDK only ships what it directly observed.
    """
    payload: dict[str, Any] = {
        "status": _STATUS_MAP.get(case.status.value, "failed"),
    }
    if case.started_at is not None:
        payload["started_at"] = case.started_at.isoformat()
    if case.ended_at is not None:
        payload["ended_at"] = case.ended_at.isoformat()
    if case.started_at is not None and case.ended_at is not None:
        payload["duration_seconds"] = max(
            int((case.ended_at - case.started_at).total_seconds()), 0
        )

    if case.failure is not None:
        payload["ended_reason"] = case.failure.code
        payload["error_message"] = case.failure.message or ""

    result = case.result
    transcript_segments: list[dict[str, Any]] = []
    if result is not None:
        transcript_segments = _extract_transcript_segments(result)
        if transcript_segments:
            payload["transcript"] = transcript_segments

        recording_uri = _extract_recording_uri(result)
        if recording_uri:
            payload["recording_url"] = recording_uri

        provider_call_data = result.metadata.get("provider_call_data")
        if isinstance(provider_call_data, dict) and provider_call_data:
            payload["provider_call_data"] = provider_call_data

        summary = result.metadata.get("call_summary") or result.metadata.get("summary")
        if isinstance(summary, str) and summary:
            payload["call_summary"] = summary

        call_metadata = {
            k: v
            for k, v in result.metadata.items()
            if k
            not in {
                "provider_call_data",
                "call_summary",
                "summary",
                "failure",
                "status",
                "test_case_id",
                "run_id",
            }
        }
        if call_metadata:
            payload["call_metadata"] = _json_safe(call_metadata)

    return payload


def _extract_transcript_segments(result) -> list[dict[str, Any]]:
    """Convert TestCaseResult.messages into ALK transcript segments.

    LiveKit engine emits each message with ``started_speaking_at`` and
    ``stopped_speaking_at`` (seconds since epoch, from ``ChatMessage.metrics``).
    We convert to millisecond offsets relative to the first speech timestamp
    so ``ConversationMetricsCalculator`` can compute overlap-based interruption
    counts, WPM and talk-ratio on the backend.
    """
    segments: list[dict[str, Any]] = []
    typed_messages = [msg for msg in result.messages if isinstance(msg, dict)]

    anchor = _first_speech_anchor(typed_messages)
    for msg in typed_messages:
        role = msg.get("role")
        content = msg.get("content")
        if not isinstance(content, str) or not content:
            continue
        if role == "assistant":
            speaker_role = "assistant"
        elif role in {"user", "customer"}:
            speaker_role = "user"
        elif role == "tool":
            speaker_role = "tool_call_result"
        elif role == "system":
            speaker_role = "system"
        else:
            speaker_role = "unknown"

        start_ms, end_ms = _resolve_message_timing_ms(msg, anchor)
        segments.append(
            {
                "speaker_role": speaker_role,
                "content": content,
                "start_time_ms": start_ms,
                "end_time_ms": end_ms,
            }
        )
    if segments:
        return segments

    if not result.transcript:
        return []
    for line in result.transcript.splitlines():
        if ":" not in line:
            continue
        role_label, content = line.split(":", 1)
        role_label = role_label.strip().lower()
        content = content.strip()
        if not content:
            continue
        if role_label in {"assistant", "agent", "bot"}:
            speaker_role = "assistant"
        elif role_label in {"customer", "user", "simulator", "caller"}:
            speaker_role = "user"
        else:
            speaker_role = "unknown"
        segments.append(
            {
                "speaker_role": speaker_role,
                "content": content,
                "start_time_ms": 0,
                "end_time_ms": 0,
            }
        )
    return segments


def _first_speech_anchor(messages: list[dict[str, Any]]) -> float | None:
    for msg in messages:
        for key in ("started_speaking_at", "created_at"):
            value = msg.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)
    return None


def _resolve_message_timing_ms(
    msg: dict[str, Any], anchor: float | None
) -> tuple[int, int]:
    """Return (start_ms, end_ms) relative to the first-speech anchor.

    Falls back to ``created_at`` when speech-timing metrics are missing (text
    turns, providers that don't report the metric). Zero is used as the last
    resort — the backend metrics calculator degrades gracefully when timings
    collapse to zero-duration.
    """
    if anchor is None:
        return 0, 0

    start_raw = msg.get("started_speaking_at") or msg.get("created_at") or 0.0
    stop_raw = (
        msg.get("stopped_speaking_at")
        or msg.get("created_at")
        or start_raw
        or 0.0
    )
    start_ms = max(int(round((float(start_raw) - anchor) * 1000)), 0) if start_raw else 0
    end_ms = max(int(round((float(stop_raw) - anchor) * 1000)), start_ms) if stop_raw else start_ms
    return start_ms, end_ms


def _extract_recording_uri(result) -> str | None:
    for artifact in result.artifacts:
        artifact_type = getattr(artifact, "type", None)
        if artifact_type == "audio" and getattr(artifact, "uri", None):
            return artifact.uri
    for candidate in (
        result.audio_combined_path,
        result.audio_output_path,
        result.audio_input_path,
    ):
        if candidate and str(candidate).startswith(("http://", "https://")):
            return str(candidate)
    return None


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return json.loads(json.dumps(value, default=str))


def _write_submission(run_directory: Path, payload: dict[str, Any]) -> None:
    submission_path = run_directory / "submission.json"
    submission_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


__all__ = ["FutureAGIResultSink"]
