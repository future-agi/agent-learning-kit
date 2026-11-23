#!/usr/bin/env python3
"""Materialize pinned upstream reference applications without changing their source."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def references() -> list[dict[str, object]]:
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(ROOT.glob("*/reference.json"))
    ]


def run(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def materialize(reference: dict[str, object], output: Path) -> Path:
    destination = output / reference["id"]
    if destination.exists():
        raise FileExistsError(destination)

    source = reference["source"]
    with tempfile.TemporaryDirectory(prefix="multi-agent-reference-") as raw_temp:
        checkout = Path(raw_temp) / "checkout"
        run(
            "git",
            "clone",
            "--filter=blob:none",
            "--no-checkout",
            source["repository"],
            str(checkout),
        )
        run("git", "checkout", "--detach", source["commit"], cwd=checkout)
        actual_commit = run("git", "rev-parse", "HEAD", cwd=checkout)
        if actual_commit != source["commit"]:
            raise RuntimeError(f"source revision mismatch for {reference['id']}")

        source_root = checkout / source["subdirectory"]
        if not source_root.is_dir():
            raise FileNotFoundError(source_root)
        shutil.copytree(source_root, destination, ignore=shutil.ignore_patterns(".git"))

    adaptation = reference.get("adaptation")
    if adaptation:
        patch = ROOT / str(reference["framework"]["name"]) / adaptation["patch"]
        digest = hashlib.sha256(patch.read_bytes()).hexdigest()
        if digest != adaptation["sha256"]:
            raise RuntimeError(f"adaptation checksum mismatch for {reference['id']}")
        run("git", "apply", "--check", str(patch), cwd=destination)
        run("git", "apply", str(patch), cwd=destination)

    provenance = {
        "reference_id": reference["id"],
        "repository": source["repository"],
        "commit": source["commit"],
        "subdirectory": source["subdirectory"],
        "adaptation": adaptation,
    }
    (destination / ".futureagi-source.json").write_text(
        json.dumps(provenance, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for reference in references():
        print(materialize(reference, args.output))


if __name__ == "__main__":
    main()
