"""A chat conversation starts several fresh interpreters, so their imports set its speed."""

from __future__ import annotations

import subprocess
import sys

import pytest

VOICE_AND_DATA_SDKS = ("livekit.agents", "retell", "openai", "pandas")


@pytest.mark.parametrize(
    "worker", ["fi.alk.harness.phase_worker", "fi.alk.harness.chat_worker"]
)
def test_a_chat_worker_does_not_load_the_voice_sdks(worker: str) -> None:
    code = (
        f"import sys, {worker}\n"
        f"print(sorted(name for name in {VOICE_AND_DATA_SDKS!r} if name in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "[]"
