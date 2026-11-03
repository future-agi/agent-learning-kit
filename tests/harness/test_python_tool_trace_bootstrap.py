"""Framework-neutral observation of declared in-process Python tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def _trace_environment(tmp_path: Path, bindings: list[dict[str, str]]):
    bootstrap = Path(__file__).resolve().parents[2] / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    shutil.copy2(bootstrap, tmp_path / "sitecustomize.py")
    trace = tmp_path / "calls.jsonl"
    return trace, {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "HARNESS_TOOL_TRACE": str(trace),
        "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
    }


def test_declared_python_tool_is_traced_without_changing_result(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text(
        "import asyncio\n"
        "def get_weather(location):\n    return f'sunny in {location}'\n"
        "async def get_forecast(location):\n"
        "    await asyncio.sleep(0.2)\n"
        "    return f'rain in {location}'\n"
    )
    trace, env = _trace_environment(tmp_path, [
        {"name": "get_weather", "module": "agent", "callable": "get_weather"},
        {"name": "get_forecast", "module": "agent", "callable": "get_forecast"},
    ])

    started = time.time()
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio\n"
            "from agent import get_forecast, get_weather\n"
            "print(get_weather('Paris'))\n"
            "print(asyncio.run(get_forecast('Oslo')))\n",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )
    finished = time.time()

    assert result.stdout.split() == ["sunny", "in", "Paris", "rain", "in", "Oslo"]
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    # A suspended coroutine is also traced (its awaited value as output); the
    # completed call is the one that carries the tool's result.
    completed = {
        record["name"]: record
        for record in records
        if record["output"] in {"sunny in Paris", "rain in Oslo"}
    }
    weather, forecast = completed["get_weather"], completed["get_forecast"]
    assert weather == {
        "name": "get_weather",
        "arguments": {"location": "Paris"},
        "output": "sunny in Paris",
        "is_error": False,
        "at": weather["at"],
    }
    assert forecast["output"] == "rain in Oslo"
    assert started <= weather["at"] <= forecast["at"] <= finished
    # The tool began before its 200 ms await, not when it resumed after it.
    assert finished - forecast["at"] >= 0.2


def test_declared_python_tool_does_not_match_same_named_functions_in_other_modules(
    tmp_path: Path,
) -> None:
    (tmp_path / "agent.py").write_text("def lookup():\n    return 'declared'\n")
    (tmp_path / "my_agent.py").write_text("def lookup():\n    return 'look-alike'\n")
    package = tmp_path / "app"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (package / "agent.py").write_text("def lookup():\n    return 'nested'\n")
    trace, env = _trace_environment(
        tmp_path, [{"name": "lookup", "module": "agent", "callable": "lookup"}]
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from agent import lookup as declared\n"
            "from my_agent import lookup as look_alike\n"
            "from app.agent import lookup as nested\n"
            "print(look_alike(), nested(), declared())\n",
        ],
        check=True,
        env=env,
    )

    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [(record["name"], record["output"]) for record in records] == [
        ("lookup", "declared")
    ]


def test_declared_python_tool_is_traced_when_module_runs_as_script(
    tmp_path: Path,
) -> None:
    (tmp_path / "agent.py").write_text(
        "def lookup():\n    return 'declared'\n"
        "if __name__ == '__main__':\n    print(lookup())\n"
    )
    trace, env = _trace_environment(
        tmp_path, [{"name": "lookup", "module": "agent", "callable": "lookup"}]
    )

    result = subprocess.run(
        [sys.executable, "agent.py"],
        check=True,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.stdout.strip() == "declared"
    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [(record["name"], record["output"]) for record in records] == [
        ("lookup", "declared")
    ]
