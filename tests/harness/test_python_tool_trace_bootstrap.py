"""Framework-neutral observation of declared in-process Python tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def _install_bootstrap(tmp_path: Path) -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    )
    shutil.copy2(source, tmp_path / "sitecustomize.py")


def _trace_environment(
    tmp_path: Path, trace: Path, bindings: list[dict[str, str]]
) -> dict[str, str]:
    return {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "HARNESS_TOOL_TRACE": str(trace),
        "ALK_TOOL_TRACE_BINDINGS": json.dumps(bindings),
    }


def test_declared_python_tool_is_traced_without_changing_result(tmp_path: Path) -> None:
    _install_bootstrap(tmp_path)
    (tmp_path / "agent.py").write_text(
        "import asyncio\n"
        "def get_weather(location):\n    return f'sunny in {location}'\n"
        "async def get_forecast(location):\n"
        "    await asyncio.sleep(0.2)\n"
        "    return f'rain in {location}'\n"
    )
    trace = tmp_path / "calls.jsonl"
    env = _trace_environment(
        tmp_path,
        trace,
        [
            {"name": "get_weather", "module": "agent", "callable": "get_weather"},
            {"name": "get_forecast", "module": "agent", "callable": "get_forecast"},
        ],
    )

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


def test_declared_method_trace_does_not_capture_other_methods_with_same_name(
    tmp_path: Path,
) -> None:
    _install_bootstrap(tmp_path)
    (tmp_path / "agent.py").write_text(
        "class BookingTool:\n"
        "    def invoke(self, date):\n"
        "        return f'free on {date}'\n"
        "class HotelAgent:\n"
        "    def invoke(self, message):\n"
        "        return {'message': message}\n",
        encoding="utf-8",
    )
    trace = tmp_path / "calls.jsonl"
    env = _trace_environment(
        tmp_path,
        trace,
        [
            {
                "name": "check_availability",
                "module": "agent",
                "callable": "BookingTool.invoke",
            }
        ],
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from agent import BookingTool, HotelAgent\n"
            "print(BookingTool().invoke('Friday'))\n"
            "print(HotelAgent().invoke('Friday'))\n",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [(record["arguments"], record["output"]) for record in records] == [
        ({"date": "Friday"}, "free on Friday")
    ]


def test_declared_package_tool_is_traced_from_init_module(tmp_path: Path) -> None:
    _install_bootstrap(tmp_path)
    package = tmp_path / "tools"
    package.mkdir()
    (package / "__init__.py").write_text(
        "def check_availability(date):\n    return f'free on {date}'\n",
        encoding="utf-8",
    )
    trace = tmp_path / "calls.jsonl"
    env = _trace_environment(
        tmp_path,
        trace,
        [
            {
                "name": "check_availability",
                "module": "tools.__init__",
                "callable": "check_availability",
            }
        ],
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from tools import check_availability\n"
            "print(check_availability('Friday'))\n",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [(record["arguments"], record["output"]) for record in records] == [
        ({"date": "Friday"}, "free on Friday")
    ]


def test_declared_module_does_not_match_longer_filename_suffix(tmp_path: Path) -> None:
    _install_bootstrap(tmp_path)
    implementation = (
        "def check_availability(date):\n"
        "    return f'free on {date}'\n"
    )
    (tmp_path / "agent.py").write_text(implementation, encoding="utf-8")
    (tmp_path / "other_agent.py").write_text(implementation, encoding="utf-8")
    trace = tmp_path / "calls.jsonl"
    env = _trace_environment(
        tmp_path,
        trace,
        [
            {
                "name": "check_availability",
                "module": "agent",
                "callable": "check_availability",
            }
        ],
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from agent import check_availability as expected\n"
            "from other_agent import check_availability as other\n"
            "print(other('Thursday'))\n"
            "print(expected('Friday'))\n",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    records = [json.loads(line) for line in trace.read_text().splitlines()]
    assert [(record["arguments"], record["output"]) for record in records] == [
        ({"date": "Friday"}, "free on Friday")
    ]
