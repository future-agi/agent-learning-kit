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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("job", type=Path)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
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
        adjustments_path=None,
        authoring_only=True,
    )
    return asyncio.run(_auto(namespace))


if __name__ == "__main__":
    raise SystemExit(main())
