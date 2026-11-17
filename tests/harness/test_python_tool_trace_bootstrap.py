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


def test_a_large_tool_result_is_cut_without_breaking_the_trace_line(
    tmp_path: Path,
) -> None:
    (tmp_path / "agent.py").write_text("def search(query):\n    return 'x' * 20000\n")
    trace, env = _trace_environment(
        tmp_path, [{"name": "search", "module": "agent", "callable": "search"}]
    )

    subprocess.run(
        [sys.executable, "-c", "from agent import search\nsearch('q' * 30000)\n"],
        check=True,
        env=env,
    )

    # Every line stays JSON, so the call is still evidence; its values are cut instead.
    (record,) = [json.loads(line) for line in trace.read_text().splitlines()]
    assert record["name"] == "search"
    assert record["truncated"] is True
    assert record["output"] == "x" * 2000 + " [cut from 20000 characters]"
    assert record["arguments"] == {"query": "q" * 2000 + " [cut from 30000 characters]"}


def test_a_cut_keeps_the_type_of_every_value_that_fits(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text(
        "def list_slots(clinic_id, days):\n"
        "    return {'count': 3, 'slots': 'y' * 20000}\n"
    )
    trace, env = _trace_environment(
        tmp_path, [{"name": "list_slots", "module": "agent", "callable": "list_slots"}]
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            "from agent import list_slots\nlist_slots(5, ['mon', 'tue'])\n",
        ],
        check=True,
        env=env,
    )

    # Only the oversize value is cut, so a check still compares the id and the count as numbers.
    (record,) = [json.loads(line) for line in trace.read_text().splitlines()]
    assert record["truncated"] is True
    assert record["arguments"] == {"clinic_id": 5, "days": ["mon", "tue"]}
    assert record["output"] == {
        "count": 3,
        "slots": "y" * 2000 + " [cut from 20000 characters]",
    }


def test_a_record_of_many_small_values_stays_bounded(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text(
        "def export(rows):\n    return {f'k{index}': 'v' * 100 for index in range(200)}\n"
    )
    trace, env = _trace_environment(
        tmp_path, [{"name": "export", "module": "agent", "callable": "export"}]
    )

    subprocess.run(
        [sys.executable, "-c", "from agent import export\nexport(7)\n"],
        check=True,
        env=env,
    )

    # No single value is oversize, so the whole output is cut as text to keep the line bounded.
    (line,) = trace.read_text().splitlines()
    record = json.loads(line)
    assert len(line) < 3000
    assert record["truncated"] is True
    assert record["arguments"] == {"rows": 7}
    assert record["output"].startswith('{"k0": "' + "v" * 100)
    assert record["output"].endswith(" characters]")


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


def _records(trace: Path) -> list[dict]:
    return [json.loads(line) for line in trace.read_text().splitlines()]


def test_an_async_tool_with_two_awaits_records_one_call_with_its_real_result(
    tmp_path: Path,
) -> None:
    (tmp_path / "agent.py").write_text(
        "import asyncio\n"
        "async def start_return(order_id, reason):\n"
        "    await asyncio.sleep(0.01)\n"
        "    await asyncio.sleep(0.01)\n"
        "    return f'Return started for order {order_id}: {reason}.'\n"
    )
    trace, env = _trace_environment(
        tmp_path,
        [{"name": "start_return", "module": "agent", "callable": "start_return"}],
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import asyncio\nfrom agent import start_return\n"
            "asyncio.run(start_return('A1001', 'too small'))\n",
        ],
        check=True,
        env=env,
    )

    (record,) = _records(trace)
    assert record["name"] == "start_return"
    assert record["arguments"] == {"order_id": "A1001", "reason": "too small"}
    assert record["output"] == "Return started for order A1001: too small."
    assert record["is_error"] is False


def test_a_sync_tool_is_still_recorded_once(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text("def look_up(order_id):\n    return order_id\n")
    trace, env = _trace_environment(
        tmp_path, [{"name": "look_up", "module": "agent", "callable": "look_up"}]
    )

    subprocess.run(
        [sys.executable, "-c", "from agent import look_up\nlook_up('A1')\n"],
        check=True,
        env=env,
    )

    assert [(r["name"], r["output"]) for r in _records(trace)] == [("look_up", "A1")]
