"""Shared plumbing for the per-case test scripts.

Each case script fills in a small CONFIG block and calls run(). Everything
else — the FutureAGI simulator, the LiveKit room, trunks, caller numbers —
comes from .env.acceptance and needs no attention from the person testing.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # oss/simulation-acceptance/scripts
SUITE = HERE.parent                             # oss/simulation-acceptance

# Repo root, i.e. where you run the harness from and where .env.acceptance
# lives. Override with AGENT_LEARNING_KIT_DIR for an unusual checkout layout.
KIT = Path(os.environ.get("AGENT_LEARNING_KIT_DIR", SUITE.parent.parent))

# Your credentials. Defaults to .env.acceptance at the repo root, but set
# ACCEPTANCE_ENV_FILE to keep them outside the repo entirely — which is the
# safer habit, since this file holds keys that can spend money.
ENV_FILE = Path(os.environ.get("ACCEPTANCE_ENV_FILE", KIT / ".env.acceptance"))

PLACEHOLDER = re.compile(r"^\s*$|paste[- ]|your[- ]|<.*>|\+1XXXXXXXXXX", re.I)


def _load_env_file() -> dict[str, str]:
    if not ENV_FILE.exists():
        sys.exit(
            f"Missing {ENV_FILE}\n"
            f"Copy .env.acceptance.example to .env.acceptance and fill it in."
        )
    env: dict[str, str] = {}
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _python() -> str:
    for candidate in (KIT / ".venv/bin/python", KIT / "alk/bin/python"):
        if candidate.exists():
            return str(candidate)
    return shutil.which("python3") or sys.executable


def run(case_id: str, *, description: str, config: dict[str, str]) -> int:
    """Run one acceptance case with the caller's agent details.

    ``config`` maps environment variable names to the values the person
    testing filled in at the top of their script.
    """
    unfilled = [k for k, v in config.items() if PLACEHOLDER.match(str(v))]
    if unfilled:
        print(f"\n  Before running case {case_id}, fill these in at the top of this file:\n")
        for key in unfilled:
            print(f"    {key}")
        print()
        return 1

    harness = KIT / "oss/simulation-acceptance/run_voice_case.py"
    if not harness.exists():
        sys.exit(
            f"Could not find the test harness at {harness}\n"
            f"Set AGENT_LEARNING_KIT_DIR to your agent-learning-kit checkout."
        )

    env = {**os.environ, **_load_env_file(), **config}

    print(f"\n  Case {case_id} — {description}")
    print(f"  Testing: {', '.join(f'{k}={v}' for k, v in config.items() if 'PROMPT' not in k)}")

    # Configuration check first: no call is placed, nothing is spent.
    print("\n  [1/2] Checking configuration...", flush=True)
    check = subprocess.run(
        [_python(), str(harness), case_id, "--dry-run"],
        cwd=KIT, env=env, capture_output=True, text=True,
    )
    if check.returncode != 0:
        print("  Configuration problem:\n")
        print(check.stdout.strip() or check.stderr.strip()[-1500:])
        return check.returncode
    print("  Configuration OK.")

    phone = case_id.endswith(".1")
    print(
        f"\n  [2/2] Running the conversation"
        f"{' — this places a REAL phone call' if phone else ''}...",
        flush=True,
    )
    result = subprocess.run([_python(), str(harness), case_id], cwd=KIT, env=env)

    if result.returncode == 0:
        print("\n  PASSED — the conversation completed.")
        print(f"  Transcript and audio: {KIT}/artifacts/simulation-acceptance/")
    else:
        print("\n  FAILED. The output above explains why.")
        print("  Note: roughly 1 in 10 voice runs stalls for unrelated reasons —")
        print("  try again before investigating.")
    return result.returncode
