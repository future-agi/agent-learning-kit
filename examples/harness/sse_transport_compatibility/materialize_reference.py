#!/usr/bin/env python3
"""Materialize the pinned upstream AG-UI CrewAI integration without modifying it."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT / "ag_ui_crewai" / "reference.json"


def run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def materialize(output: Path) -> Path:
    reference = json.loads(REFERENCE.read_text(encoding="utf-8"))
    source = reference["source"]
    destination = output / reference["id"]
    if destination.exists():
        raise FileExistsError(destination)
    with tempfile.TemporaryDirectory(prefix="sse-reference-") as temporary:
        checkout = Path(temporary) / "checkout"
        run(
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            source["repository"],
            str(checkout),
        )
        run("git", "checkout", "--detach", source["commit"], cwd=checkout)
        if run("git", "rev-parse", "HEAD", cwd=checkout) != source["commit"]:
            raise RuntimeError("source revision mismatch")
        shutil.copytree(checkout / source["subdirectory"], destination)
    (destination / ".futureagi-source.json").write_text(
        json.dumps(
            {
                "reference_id": reference["id"],
                "repository": source["repository"],
                "commit": source["commit"],
                "subdirectory": source["subdirectory"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    print(materialize(args.output))


if __name__ == "__main__":
    main()
