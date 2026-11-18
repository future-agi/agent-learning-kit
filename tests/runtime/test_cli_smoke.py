from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from fi.alk import trinity
from fi.alk.cli import main


def test_doctor_returns_nonzero_when_boundary_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        trinity,
        "trinity_status",
        lambda: {
            "status": "failed",
            "exit_code": 1,
            "summary": {
                "missing_public_modules": ["fi.alk.simulate"],
                "missing_engine_modules": [],
            },
        },
    )

    assert main(["doctor", "--quiet"]) == 1


def test_documented_quickstart_stays_offline(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [sys.executable, str(root / "scripts/check_offline_quickstart.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (
        "artifact=agent-learning.run.v1 status=passed network_attempts=0"
        in result.stdout
    )
    assert "console=agent-learn --help status=passed" in result.stdout
    assert (
        "public_imports=fi.alk.capabilities,fi.alk.simulate,fi.alk.evals,"
        "fi.alk.redteam,fi.alk.optimize,fi.alk.suite" in result.stdout
    )
    assert str(root / "src" / "fi" / "alk") in result.stdout


def test_offline_guard_catches_startup_udp(tmp_path: Path) -> None:
    root = Path(__file__).parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            str(root / "scripts/check_offline_quickstart.py"),
            "--probe-startup-network",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "offline quickstart attempted a network operation" in result.stderr
    assert "127.0.0.1" in result.stderr
