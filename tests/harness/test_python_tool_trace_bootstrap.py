"""Framework-neutral observation of declared in-process Python tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


def test_declared_python_tool_is_traced_without_changing_result(tmp_path: Path) -> None:
    bootstrap = Path(__file__).resolve().parents[2] / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    shutil.copy2(bootstrap, tmp_path / "sitecustomize.py")
    (tmp_path / "agent.py").write_text(
        "import asyncio\n"
        "def get_weather(location):\n    return f'sunny in {location}'\n"
        "async def get_forecast(location):\n"
        "    await asyncio.sleep(0.2)\n"
        "    return f'rain in {location}'\n"
    )
    trace = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "HARNESS_TOOL_TRACE": str(trace),
        "ALK_TOOL_TRACE_BINDINGS": json.dumps(
            [
                {"name": "get_weather", "module": "agent", "callable": "get_weather"},
                {"name": "get_forecast", "module": "agent", "callable": "get_forecast"},
            ]
        ),
    }

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


def test_declared_langchain_tool_is_traced_by_instance_name(tmp_path: Path) -> None:
    bootstrap = (
        Path(__file__).resolve().parents[2]
        / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    )
    shutil.copy2(bootstrap, tmp_path / "sitecustomize.py")
    package = tmp_path / "langchain_community" / "tools" / "sql_database"
    package.mkdir(parents=True)
    for directory in (package.parents[1], package.parent, package):
        (directory / "__init__.py").write_text("")
    (package / "tool.py").write_text(
        "class QueryTool:\n"
        "    name = 'sql_db_query'\n"
        "    def _run(self, query):\n"
        "        return [('Alice', 3)]\n"
        "class OtherTool:\n"
        "    name = 'not_declared'\n"
        "    def _run(self, query):\n"
        "        return 'ignored'\n"
    )
    trace = tmp_path / "calls.jsonl"
    env = {
        **os.environ,
        "PYTHONPATH": str(tmp_path),
        "HARNESS_TOOL_TRACE": str(trace),
        "ALK_TOOL_TRACE_BINDINGS": json.dumps(
            [
                {
                    "name": "sql_db_query",
                    "module": "langchain_community",
                    "callable": "_run",
                    "instance_name": "sql_db_query",
                }
            ]
        ),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from langchain_community.tools.sql_database.tool import OtherTool, QueryTool\n"
            "tool = QueryTool()\n"
            "print(tool.name)\n"
            "print(tool._run('select name, rides from riders'))\n"
            "print(OtherTool()._run('select 1'))\n",
        ],
        check=True,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.stdout.splitlines() == [
        "sql_db_query",
        "[('Alice', 3)]",
        "ignored",
    ]
    assert [json.loads(line) for line in trace.read_text().splitlines()] == [
        {
            "name": "sql_db_query",
            "arguments": {"query": "select name, rides from riders"},
            "output": [["Alice", 3]],
            "is_error": False,
            "at": json.loads(trace.read_text().splitlines()[0])["at"],
        }
    ]
