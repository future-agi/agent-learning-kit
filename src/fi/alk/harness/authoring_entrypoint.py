"""Run the established ALK authoring stages for one hosted job.

This is intentionally a thin process boundary over :func:`fi.alk.harness.cli._auto`.
Contract creation, logical environment creation, and scenario generation therefore remain the
same implementation used by the local SDK and sandbox flows.  Daytona consumes the frozen output
afterward; this command never executes scenarios itself.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .cli import _auto
from .job import HarnessJob
from .scenarios import load as load_written


def _persist_authored_scenario_count(
    job_path: Path, job: HarnessJob, output: Path
) -> None:
    """Keep the frozen job in sync with adjustments applied during authoring.

    The control plane may increase ``scenario_count`` while this process is already
    running.  ``_auto`` sees that adjustment and writes the larger validated suite,
    but the following Bundle V2 process reloads this on-disk job document.  Without
    reconciling it here the bundler copies the original number of scenarios and the
    platform correctly rejects preallocation because its expected count is newer.
    """
    authored_count = len(load_written(output))
    if authored_count <= 0 or authored_count == job.scenario_count:
        return
    updated = job.model_copy(update={"scenario_count": authored_count})
    temporary = job_path.with_suffix(f"{job_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(updated.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(job_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--adjustments",
        type=Path,
        help="JSONL inbox for user corrections applied at safe stage boundaries",
    )
    args = parser.parse_args(argv)

    job = HarnessJob.model_validate(json.loads(args.job.read_text(encoding="utf-8")))
    # Transport kinds such as ``archive`` and ``github`` describe how the platform acquired the
    # source.  Once extracted, the established ALK authoring pipeline must inspect it as a repo.
    source_kind = str(job.metadata.get("source_kind") or "repo")
    if source_kind not in {"repo", "spec"}:
        source_kind = "repo"
    namespace = argparse.Namespace(
        path=str(args.source.resolve()),
        name=str(job.metadata.get("agent_name") or args.source.name),
        kind=source_kind,
        out=str(args.output.resolve()),
        count=job.scenario_count,
        model=None,
        run_model=None,
        job=job,
        adjustments_path=str(args.adjustments) if args.adjustments else None,
        authoring_only=True,
    )
    status = asyncio.run(_auto(namespace))
    if status == 0:
        _persist_authored_scenario_count(args.job, job, args.output.resolve())
    return status


if __name__ == "__main__":
    raise SystemExit(main())
