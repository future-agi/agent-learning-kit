"""Standalone entrypoint run inside one isolated hosted ALK sandbox.

The platform creates the typed job and prepares a source checkout through its repository
integration. It does not execute harness stages. This process consumes those inputs, resolves
target-provider secrets into the environment, runs the same ``HarnessExecutor`` used locally, and
then forwards the durable run artifacts to the platform over the v1.6 hosted-harness ingestion
API (see ``hosted_sink``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from . import hosted_sink
from .executor import HarnessExecutor
from .job import ExecutionMode, HarnessJob


async def _run(job_path: Path, source: Path, output: Path) -> int:
    # Resolved target-provider secrets are exported before the harness builds the
    # environment or runs the target/simulator, so every provider SDK picks them up.
    injected = hosted_sink.inject_secrets(hosted_sink.load_secrets())
    if injected:
        print(
            json.dumps({"hosted_secrets_injected": sorted(injected)}),
            flush=True,
        )

    job = HarnessJob.model_validate_json(job_path.read_text(encoding="utf-8"))
    if job.execution is not ExecutionMode.HOSTED:
        raise ValueError("hosted_entrypoint_requires_hosted_job")
    status = await HarnessExecutor().run(job, source=source, output=output)
    print(status.model_dump_json(exclude_none=True), flush=True)

    # Execution is authoritative; forwarding is best-effort and idempotent. A
    # forwarding failure must not change the run's exit code.
    capabilities = hosted_sink.load_capabilities()
    if capabilities is not None:
        terminal_stage = (
            "completed" if status.stage.terminal and not status.failure else "failed"
        )
        try:
            report = hosted_sink.forward_all(output, capabilities, terminal_stage)
        except Exception as exc:  # noqa: BLE001
            report = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps({"hosted_forward": report}, default=str), flush=True)

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
