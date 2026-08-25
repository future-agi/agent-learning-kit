"""Standalone entrypoint run inside one isolated hosted ALK sandbox.

The platform creates the typed job and prepares a source checkout through its repository
integration. It does not execute harness stages. This process consumes those inputs and runs the
same ``HarnessExecutor`` used locally.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .executor import HarnessExecutor
from .job import ExecutionMode, HarnessJob


async def _run(job_path: Path, source: Path, output: Path) -> int:
    job = HarnessJob.model_validate_json(job_path.read_text(encoding="utf-8"))
    if job.execution is not ExecutionMode.HOSTED:
        raise ValueError("hosted_entrypoint_requires_hosted_job")
    status = await HarnessExecutor().run(job, source=source, output=output)
    print(status.model_dump_json(exclude_none=True), flush=True)
    if status.failure and status.failure.code in {
        "attempt_expired",
        "attempt_superseded",
        "attempt_fenced",
    }:
        return 3
    return 0 if status.stage.terminal else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alk-harness-worker")
    parser.add_argument("job", type=Path, help="typed HarnessJob JSON")
    parser.add_argument(
        "--source", required=True, type=Path, help="sandbox-owned source checkout"
    )
    parser.add_argument(
        "--output", required=True, type=Path, help="job artifact directory"
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.job, args.source, args.output))


if __name__ == "__main__":
    raise SystemExit(main())
