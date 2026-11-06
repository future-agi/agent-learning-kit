import asyncio
import json
from pathlib import Path

import pytest

from fi.alk.harness.agent_type_check import agent_type_failure
from fi.alk.harness.executor import HarnessExecutor
from fi.alk.harness.failure_file import FAILURE_FILE, write_failure_file
from fi.alk.harness.job import HarnessJob, HarnessStage
from fi.alk.harness.local_certified_runtime import LocalCertifiedRunError
from fi.alk.harness.process_runtime import ProcessRuntimeError


@pytest.mark.parametrize("partial_run", [False, True])
def test_authored_scenarios_are_not_completed_calls(tmp_path, monkeypatch, partial_run):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"

    async def authored(args):
        Path(args.out).mkdir()
        (Path(args.out) / "scenarios.json").write_text(json.dumps([{"name": "authored"}]))
        return 0

    async def failed_runtime(*args, **kwargs):
        if partial_run:
            raise LocalCertifiedRunError(
                "infrastructure", "runtime_stopped", "stopped after one call",
                completed_scenarios=1,
            )
        raise ProcessRuntimeError(
            "admission", "resource_capacity_unknown", "resource bounds unavailable"
        )

    monkeypatch.setattr("fi.alk.harness.cli._auto", authored)
    monkeypatch.setattr(
        "fi.alk.harness.local_certified_runtime.run_local_certified", failed_runtime
    )
    job = HarnessJob(
        job_id="completed-count", run_id="completed-count-run", execution="local",
        source={"kind": "local_repository", "local_path": str(source)},
        agent={"connector": "auto"}, scenario_count=1,
        metadata={"generic_harness_v1": True},
    )
    status = asyncio.run(HarnessExecutor().run(job, source=source, output=output))
    assert status.stage is HarnessStage.FAILED
    assert status.total_scenarios == 1
    assert status.completed_scenarios == (1 if partial_run else 0)
    assert status.failure.code == ("runtime_stopped" if partial_run else "resource_capacity_unknown")


def _job(source):
    return HarnessJob(
        job_id="refused", run_id="refused-run", execution="local",
        source={"kind": "local_repository", "local_path": str(source)},
        agent={"connector": "auto"}, scenario_count=1,
    )


@pytest.mark.parametrize("exit_code", [1, 78])
def test_the_failure_a_build_left_is_the_jobs_failure(tmp_path, monkeypatch, exit_code):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"

    async def refused(args):
        write_failure_file(agent_type_failure(["playwright"]), Path(args.out))
        return exit_code

    monkeypatch.setattr("fi.alk.harness.cli._auto", refused)
    status = asyncio.run(HarnessExecutor().run(_job(source), source=source, output=output))
    assert status.stage is HarnessStage.FAILED
    assert status.failure == agent_type_failure(["playwright"])


@pytest.mark.parametrize("exit_code", [0, 2])
def test_a_build_that_ran_reports_no_failure_whatever_the_folder_holds(
    tmp_path, monkeypatch, exit_code
):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"

    async def ran(args):
        write_failure_file(agent_type_failure(["playwright"]), Path(args.out))
        return exit_code

    monkeypatch.setattr("fi.alk.harness.cli._auto", ran)
    status = asyncio.run(HarnessExecutor().run(_job(source), source=source, output=output))
    assert status.stage is HarnessStage.COMPLETED
    assert status.failure is None


@pytest.mark.parametrize("unreadable", ["malformed", "a folder"])
def test_an_unreadable_failure_file_falls_back_to_the_events(tmp_path, monkeypatch, unreadable):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"

    async def crashed(args):
        Path(args.out).mkdir()
        if unreadable == "malformed":
            (Path(args.out) / FAILURE_FILE).write_text("{")
        else:
            (Path(args.out) / FAILURE_FILE).mkdir()
        return 1

    monkeypatch.setattr("fi.alk.harness.cli._auto", crashed)
    status = asyncio.run(HarnessExecutor().run(_job(source), source=source, output=output))
    assert status.failure.code == "running_failed"
