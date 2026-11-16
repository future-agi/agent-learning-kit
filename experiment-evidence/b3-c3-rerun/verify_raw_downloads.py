"""Verify every committed evidence file using unauthenticated raw GitHub curl."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--repo-dir", type=Path, required=True)
    p.add_argument("--commit", required=True)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    prefix = "experiment-evidence/b3-c3-rerun/"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", args.commit, "--", prefix], cwd=args.repo_dir, text=True).splitlines()
    assert paths, "No evidence paths found"

    def verify(path):
        url = f"https://raw.githubusercontent.com/future-agi/agent-learning-kit/{args.commit}/{path}"
        local = args.repo_dir / path
        downloaded = args.output_dir / "downloaded" / path
        downloaded.parent.mkdir(parents=True, exist_ok=True)
        command = ["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3", "--retry-delay", "1", "--output", str(downloaded), "--write-out", "%{http_code} %{size_download}", url]
        result = subprocess.run(command, text=True, capture_output=True)
        local_digest = hashlib.sha256(local.read_bytes()).hexdigest()
        remote_digest = hashlib.sha256(downloaded.read_bytes()).hexdigest() if downloaded.exists() else None
        return {"path": path, "raw_url": url, "command": command, "curl_exit_code": result.returncode, "http_and_bytes": result.stdout, "stderr": result.stderr, "local_sha256": local_digest, "download_sha256": remote_digest, "bytes": local.stat().st_size, "status": "pass" if result.returncode == 0 and result.stdout.startswith("200 ") and local_digest == remote_digest else "fail"}

    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(verify, paths))
    report = {"status": "pass" if all(row["status"] == "pass" for row in records) else "inconclusive", "evidence_commit_sha": args.commit, "branch": "codex/alk-w1-rerun-evidence", "authentication": "none; public raw.githubusercontent.com URLs", "files_verified": len(records), "results": records}
    (args.output_dir / "raw-download-verification.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.output_dir / "curl-command.log").write_text("\n\n".join("COMMAND: " + " ".join(r["command"]) + "\nEXIT: " + str(r["curl_exit_code"]) + "\nHTTP_BYTES: " + r["http_and_bytes"] + "\nSTDERR: " + r["stderr"] + "\nSHA256_MATCH: " + str(r["local_sha256"] == r["download_sha256"]) for r in records) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "results"}, indent=2))
    raise SystemExit(0 if report["status"] == "pass" else 1)


if __name__ == "__main__":
    main()
