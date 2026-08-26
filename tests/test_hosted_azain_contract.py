from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from fi.alk.harness.hosted_entrypoint import _run
from fi.alk.harness.job import (
    FailureDomain,
    HarnessFailure,
    HarnessJob,
    HarnessJobStatus,
    HarnessStage,
)
from fi.simulate.runtime.spec import RuntimeRequirements


def _job(**runtime):
    return HarnessJob.model_validate(
        {
            "schema_version": "futureagi.harness-job.v1",
            "job_id": "job-1",
            "run_id": "run-1",
            "execution": "hosted",
            "source": {
                "kind": "github",
                "repository": "future-agi/ride-voice-agent",
                "ref": "main",
                "commit_sha": "a" * 40,
                "visibility": "public",
            },
            "agent": {"connector": "vapi", "config": {}, "secret_refs": {}},
            "scenario_count": 10,
            "seed": 1,
            "runtime": {
                "isolation": "dedicated_vm",
                "cpu_units": 4,
                "memory_mb": 8192,
                "parallelism": 3,
                "concurrency_weight": 1,
                "max_duration_seconds": 3600,
                "network_policy": "live",
                **runtime,
            },
            "security": {
                "untrusted_source": True,
                "read_only_source": True,
                "allow_privileged": False,
                "allow_host_runtime_control": False,
                "allowed_egress_domains": [],
            },
            "artifacts": {
                "level": "full",
                "retention_days": 30,
                "allow_bundle_download": True,
                "max_artifact_bytes": 1024,
            },
        }
    )


def test_runtime_requirements_preserve_hosted_parallelism():
    runtime = RuntimeRequirements.model_validate({"parallelism": 8})
    assert runtime.parallelism == 8


def test_hosted_job_rejects_parallelism_above_cpu():
    with pytest.raises(ValueError, match="hosted_parallelism_exceeds_cpu"):
        _job(cpu_units=2, parallelism=3)


def test_hosted_job_requires_resolved_commit():
    payload = _job().model_dump(mode="json")
    payload["source"]["commit_sha"] = None
    with pytest.raises(ValueError, match="github_commit_sha_required"):
        HarnessJob.model_validate(payload)


@pytest.mark.anyio
async def test_hosted_entrypoint_returns_zero_for_failed_terminal(
    monkeypatch, tmp_path
):
    job = _job()
    job_path = tmp_path / "job.json"
    job_path.write_text(job.model_dump_json(), encoding="utf-8")

    class Executor:
        async def run(self, job, *, source, output):
            return HarnessJobStatus(
                job_id=job.job_id,
                run_id=job.run_id,
                stage=HarnessStage.FAILED,
                updated_at=datetime.now(timezone.utc),
                failure=HarnessFailure(
                    domain=FailureDomain.AGENT,
                    stage=HarnessStage.RUNNING,
                    code="agent_failed",
                    message="agent failed",
                ),
            )

    monkeypatch.setattr("fi.alk.harness.hosted_entrypoint.HarnessExecutor", Executor)
    assert await _run(job_path, tmp_path, tmp_path / "artifacts") == 0


@pytest.mark.anyio
async def test_hosted_entrypoint_returns_three_when_fenced(monkeypatch, tmp_path):
    job = _job()
    job_path = tmp_path / "job.json"
    job_path.write_text(job.model_dump_json(), encoding="utf-8")

    class Executor:
        async def run(self, job, *, source, output):
            return HarnessJobStatus(
                job_id=job.job_id,
                run_id=job.run_id,
                stage=HarnessStage.FAILED,
                updated_at=datetime.now(timezone.utc),
                failure=HarnessFailure(
                    domain=FailureDomain.PLATFORM_SYNC,
                    stage=HarnessStage.UPLOADING_ARTIFACTS,
                    code="attempt_superseded",
                    message="superseded",
                ),
            )

    monkeypatch.setattr("fi.alk.harness.hosted_entrypoint.HarnessExecutor", Executor)
    assert await _run(job_path, tmp_path, tmp_path / "artifacts") == 3


def test_snapshot_catalog_matches_engine_contract():
    root = Path(__file__).parents[1]
    catalog = json.loads(
        (root / "hosted-snapshot" / "catalog.json").read_text(encoding="utf-8")
    )
    assert catalog["runtimes"] == {"python": ["3.11", "3.12"], "node": ["20", "22"]}
    assert {engine: data["version"] for engine, data in catalog["engines"].items()} == {
        "postgres": "16",
        "redis": "7",
        "rabbitmq": "3.13",
    }
