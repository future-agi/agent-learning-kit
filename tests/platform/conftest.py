"""The platform lane: `agent-learn` and the SDK against a running FutureAGI platform.

Collected always, run only with AGENT_LEARNING_LIVE_PLATFORM=1 and a key pair for that
platform. Every test gets its own empty home, so nothing it signs in to or saves leaks
into the developer's own profiles or keychain.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

FLAG = "AGENT_LEARNING_LIVE_PLATFORM"
REQUIRED = ("FI_E2E_BASE_URL", "FI_E2E_API_KEY", "FI_E2E_SECRET_KEY")


def pytest_collection_modifyitems(config, items):
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if os.environ.get(FLAG) == "1" and not missing:
        return
    reason = (
        f"platform lane: set {FLAG}=1 and {', '.join(REQUIRED)}"
        if os.environ.get(FLAG) != "1"
        else f"platform lane: {', '.join(missing)} not set"
    )
    for item in items:
        if "live_platform" in item.keywords:
            item.add_marker(pytest.mark.skip(reason=reason))


@dataclass(frozen=True)
class Platform:
    base_url: str
    api_key: str
    secret_key: str


@pytest.fixture
def platform() -> Platform:
    return Platform(
        base_url=os.environ["FI_E2E_BASE_URL"].rstrip("/"),
        api_key=os.environ["FI_E2E_API_KEY"],
        secret_key=os.environ["FI_E2E_SECRET_KEY"],
    )


@dataclass(frozen=True)
class Result:
    code: int
    stdout: str
    stderr: str

    def json(self):
        return json.loads(self.stdout)


@pytest.fixture
def cli(tmp_path: Path):
    """Run `agent-learn` as a real process in a clean home with no keychain."""

    home = tmp_path / "home"
    home.mkdir()

    def run(*args: str, env: dict[str, str] | None = None) -> Result:
        environ = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "AGENT_LEARNING_HOME": str(home / ".agent-learning"),
            # No OS keychain in the lane: secrets take the file fallback.
            "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
            **(env or {}),
        }
        done = subprocess.run(
            [sys.executable, "-m", "fi.alk.cli", *args],
            env=environ,
            cwd=home,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return Result(done.returncode, done.stdout, done.stderr)

    run.home = home
    return run
