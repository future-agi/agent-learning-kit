"""The Node mark hook in a real Node process: its own suite, and the option the sandbox sets."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from fi.alk.harness import process_runtime as pr

_NODE = shutil.which("node")
_SUITE = (
    Path(__file__).parent
    / "fixtures"
    / "node_simulation_mark"
    / "simulation_mark_node.test.cjs"
)
_HOOK = Path(pr.__file__).with_name("simulation_mark_node.cjs")

pytestmark = pytest.mark.skipif(_NODE is None, reason="needs node")


def test_node_suite_passes() -> None:
    result = subprocess.run(
        [_NODE, "--test", str(_SUITE)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_node_loads_the_hook_from_the_option_the_sandbox_sets(tmp_path: Path) -> None:
    world = tmp_path / 'w0 "agent"'
    world.mkdir()
    hook = world / _HOOK.name
    shutil.copyfile(_HOOK, hook)
    for program in (["-e", "0"], ["--input-type=module", "-e", "await 0"]):
        result = subprocess.run(
            [_NODE, *program],
            env={
                "NODE_OPTIONS": pr._node_require_option(hook),
                "FI_SIMULATION_INGEST_API_KEY": "ingest-key",
                "FI_SIMULATION_INGEST_SECRET_KEY": "ingest-secret",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://127.0.0.1:9/v1/traces",
            },
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stderr
        assert result.stderr == "simulation mark hook: applied\n"
