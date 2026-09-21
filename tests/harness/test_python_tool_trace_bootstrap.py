"""Framework-neutral observation of declared in-process Python tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def test_declared_python_tool_is_traced_without_changing_result(tmp_path: Path) -> None:
    bootstrap = Path(__file__).resolve().parents[2] / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    shutil.copy2(bootstrap, tmp_path / "sitecustomize.py")
    (tmp_path / "agent.py").write_text(
        "def get_weather(location):\n    return f'sunny in {location}'\n"
    )
    trace = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "HARNESS_TOOL_TRACE": str(trace),
        "ALK_TOOL_TRACE_BINDINGS": json.dumps(
            [{"name": "get_weather", "module": "agent", "callable": "get_weather"}]
        ),
    }

    result = subprocess.run(
        [sys.executable, "-c", "from agent import get_weather; print(get_weather('Paris'))"],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.stdout.strip() == "sunny in Paris"
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert records == [
        {
            "name": "get_weather",
            "arguments": {"location": "Paris"},
            "output": "sunny in Paris",
            "is_error": False,
        }
    ]
