"""Framework-neutral observation of declared in-process Python tools."""

import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

# Every case below runs once per tracing mechanism: `sys.monitoring` (Python 3.12+) and the
# profile function that stays as the fallback. `ALK_TOOL_TRACE_MECHANISM` is an internal knob
# that only the profile value acts on; anything else lets the bootstrap choose.
MECHANISMS = [
    pytest.param(
        "monitoring",
        marks=pytest.mark.skipif(
            sys.version_info < (3, 12), reason="sys.monitoring needs Python 3.12"
        ),
    ),
    "profile",
]


@pytest.fixture(autouse=True, params=MECHANISMS)
def mechanism(request, monkeypatch) -> str:
    monkeypatch.setenv("ALK_TOOL_TRACE_MECHANISM", request.param)
    return request.param


# The tracer is standard library only, so the tool-tracing cases can run under another interpreter
# (ALK_TEST_PYTHON=/path/to/python3.13) to check how it reads that version's frames.
_TRACED_PYTHON = os.environ.get("ALK_TEST_PYTHON") or sys.executable


def _version_of(python: str) -> tuple[int, int]:
    major, minor = subprocess.run(
        [python, "-c", "import sys; print(*sys.version_info[:2])"],
        check=True,
        text=True,
        capture_output=True,
    ).stdout.split()
    return int(major), int(minor)


# The version the tracer runs under, which is not the version running pytest when
# ALK_TEST_PYTHON is set.
_TRACED_VERSION = _version_of(_TRACED_PYTHON)


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
            _TRACED_PYTHON,
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
        [_TRACED_PYTHON, "-c", "from agent import search\nsearch('q' * 30000)\n"],
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
            _TRACED_PYTHON,
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
        [_TRACED_PYTHON, "-c", "from agent import export\nexport(7)\n"],
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
            _TRACED_PYTHON,
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
        [_TRACED_PYTHON, "agent.py"],
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
            _TRACED_PYTHON,
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
        [_TRACED_PYTHON, "-c", "from agent import look_up\nlook_up('A1')\n"],
        check=True,
        env=env,
    )

    assert [(r["name"], r["output"]) for r in _records(trace)] == [("look_up", "A1")]


def _run_bindings(
    tmp_path: Path, source: str, bindings: list[dict[str, str]], driver: str
) -> list[dict]:
    (tmp_path / "agent.py").write_text(source)
    trace, env = _trace_environment(tmp_path, bindings)
    subprocess.run([_TRACED_PYTHON, "-c", driver], check=True, env=env, cwd=tmp_path)
    return _records(trace) if trace.exists() else []


def _run_agent(tmp_path: Path, source: str, names: list[str], driver: str) -> list[dict]:
    return _run_bindings(
        tmp_path,
        source,
        [{"name": n, "module": "agent", "callable": n} for n in names],
        driver,
    )


def test_a_generator_tool_is_recorded_once_per_call(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "def stream_rows(n):\n    for i in range(n):\n        yield i\n    return 'done'\n",
        ["stream_rows"],
        "from agent import stream_rows\nassert list(stream_rows(3)) == [0, 1, 2]\n",
    )
    assert [(r["name"], r["arguments"], r["is_error"]) for r in records] == [
        ("stream_rows", {"n": 3}, False)
    ]
    assert records[0]["output"] == "done"


def test_an_async_generator_tool_is_recorded_once_per_call(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "import asyncio\n"
        "async def stream_rows(n):\n"
        "    for i in range(n):\n"
        "        await asyncio.sleep(0.001)\n"
        "        yield i\n",
        ["stream_rows"],
        "import asyncio\nfrom agent import stream_rows\n"
        "async def main():\n    return [i async for i in stream_rows(3)]\n"
        "assert asyncio.run(main()) == [0, 1, 2]\n",
    )
    assert [(r["name"], r["arguments"]) for r in records] == [("stream_rows", {"n": 3})]


def test_a_sync_tool_that_calls_another_bound_tool_records_both_once(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "def inner(x):\n    return x * 2\n"
        "def outer(x):\n    return inner(x) + 1\n",
        ["inner", "outer"],
        "from agent import outer\nassert outer(5) == 11\n",
    )
    assert sorted((r["name"], r["arguments"]["x"], r["output"]) for r in records) == [
        ("inner", 5, 10),
        ("outer", 5, 11),
    ]


def test_an_async_tool_that_awaits_another_bound_tool_records_both_once(
    tmp_path: Path,
) -> None:
    records = _run_agent(
        tmp_path,
        "import asyncio\n"
        "async def inner(x):\n    await asyncio.sleep(0.001)\n    return x * 2\n"
        "async def outer(x):\n    await asyncio.sleep(0.001)\n"
        "    y = await inner(x)\n    await asyncio.sleep(0.001)\n    return y + 1\n",
        ["inner", "outer"],
        "import asyncio\nfrom agent import outer\nassert asyncio.run(outer(5)) == 11\n",
    )
    assert sorted((r["name"], r["arguments"]["x"], r["output"]) for r in records) == [
        ("inner", 5, 10),
        ("outer", 5, 11),
    ]


@pytest.mark.parametrize("module", [".", "/", "..", "agent\x00"])
def test_a_binding_with_an_odd_module_never_raises_into_the_agents_call(
    tmp_path: Path, module: str
) -> None:
    trace, env = _trace_environment(
        tmp_path, [{"name": "look_up", "module": module, "callable": "look_up"}]
    )
    # A frame of the main script carries the callable's name, so the path match is reached.
    result = subprocess.run(
        [_TRACED_PYTHON, "-c", "def look_up(x):\n    return x\nprint(look_up(7))\n"],
        text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "7"
    assert not trace.exists()


RAISED = "the tool raised an exception"


def test_a_sync_tool_that_raises_is_recorded_as_an_error(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "def start_return(order_id):\n    raise ValueError('order not delivered')\n",
        ["start_return"],
        "from agent import start_return\n"
        "try:\n    start_return('A1')\nexcept ValueError:\n    pass\n",
    )
    (record,) = records
    assert record["name"] == "start_return"
    assert record["arguments"] == {"order_id": "A1"}
    assert record["is_error"] is True
    assert record["output"] == RAISED


def test_an_async_tool_that_raises_after_an_await_is_recorded_once_as_an_error(
    tmp_path: Path,
) -> None:
    records = _run_agent(
        tmp_path,
        "import asyncio\n"
        "async def start_return(order_id):\n"
        "    await asyncio.sleep(0.001)\n"
        "    raise KeyError(order_id)\n",
        ["start_return"],
        "import asyncio\nfrom agent import start_return\n"
        "try:\n    asyncio.run(start_return('A1'))\nexcept KeyError:\n    pass\n",
    )
    (record,) = records
    assert (record["is_error"], record["output"]) == (True, RAISED)


def test_a_nested_raise_marks_every_bound_tool_it_unwinds_through(tmp_path: Path) -> None:
    source = (
        "def helper(x):\n    raise RuntimeError('no')\n"
        "def outer(x):\n    return helper(x)\n"
        "def careful(x):\n    try:\n        return helper(x)\n    except RuntimeError:\n"
        "        return 'recovered'\n"
    )
    records = _run_agent(
        tmp_path,
        source,
        ["helper", "outer", "careful"],
        "from agent import outer, careful\n"
        "try:\n    outer(1)\nexcept RuntimeError:\n    pass\n"
        "assert careful(2) == 'recovered'\n",
    )
    seen = sorted((r["name"], r["arguments"]["x"], r["is_error"]) for r in records)
    assert seen == [
        ("careful", 2, False),
        ("helper", 1, True),
        ("helper", 2, True),
        ("outer", 1, True),
    ]


def test_a_normal_return_is_unchanged(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "def note(x):\n    return None\ndef value(x):\n    return x\n",
        ["note", "value"],
        "from agent import note, value\nnote(1)\nvalue(2)\n",
    )
    assert [(r["name"], r["output"], r["is_error"]) for r in records] == [
        ("note", None, False),
        ("value", 2, False),
    ]


def test_generators_are_not_recorded_as_errors(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "import asyncio\n"
        "def rows(n):\n    for i in range(n):\n        yield i\n"
        "async def arows(n):\n    for i in range(n):\n        await asyncio.sleep(0.001)\n        yield i\n",
        ["rows", "arows"],
        "import asyncio\nfrom agent import rows, arows\n"
        "assert list(rows(2)) == [0, 1]\n"
        "async def main():\n    return [i async for i in arows(2)]\n"
        "assert asyncio.run(main()) == [0, 1]\n",
    )
    assert sorted((r["name"], r["is_error"]) for r in records) == [
        ("arows", False),
        ("rows", False),
    ]


def test_the_semantic_reader_grades_a_raised_tool_as_a_refused_call(tmp_path: Path) -> None:
    from fi.alk.harness.run.simulation import _semantic_calls

    _run_agent(
        tmp_path,
        "def start_return(order_id):\n    raise ValueError('x')\n",
        ["start_return"],
        "from agent import start_return\n"
        "try:\n    start_return('A1')\nexcept ValueError:\n    pass\n",
    )
    (call,) = _semantic_calls(tmp_path / "calls.jsonl")
    assert (call.ok, call.refused, call.error) == (False, True, RAISED)


@pytest.mark.parametrize(
    "first_call",
    [
        pytest.param("agent.t(agent.Bad())", id="unprintable-argument"),
        pytest.param("agent.t(2000)", id="deeply-nested-return"),
    ],
)
def test_a_value_that_cannot_be_printed_never_reaches_the_agents_call(
    tmp_path: Path, first_call: str
) -> None:
    """Observation never changes a tool's outcome, and tracing stays on after a failed record."""
    source = (
        "class Bad:\n"
        "    def __str__(self):\n        raise RuntimeError('lazy load')\n"
        "    __repr__ = __str__\n"
        "def t(x):\n"
        "    if x == 2000:\n"
        "        deep = {}\n"
        "        for _ in range(x):\n            deep = {'k': deep}\n"
        "        return deep\n"
        "    return 'ok'\n"
    )
    records = _run_agent(
        tmp_path,
        source,
        ["t"],
        f"import agent\n{first_call}\nassert agent.t('second') == 'ok'\n",
    )
    assert [r["arguments"] for r in records if r["arguments"] == {"x": "second"}] == [
        {"x": "second"}
    ]


GENERATORS = (
    "def bare(n):\n    yield 1\n    yield 2\n"
    "def guarded(n):\n    try:\n        yield 1\n        yield 2\n    finally:\n        pass\n"
)


@pytest.mark.parametrize(
    "driver",
    [
        pytest.param("g = agent.{n}(3); next(g); g.close()", id="closed"),
        pytest.param("g = agent.{n}(3); next(g); del g", id="abandoned"),
        pytest.param("for _ in agent.{n}(3):\n    break", id="break"),
    ],
)
@pytest.mark.parametrize("name", ["bare", "guarded"])
def test_a_generator_closed_early_is_recorded_and_is_not_an_error(
    tmp_path: Path, name: str, driver: str, mechanism: str
) -> None:
    records = _run_agent(
        tmp_path, GENERATORS, [name], "import agent\n" + driver.format(n=name) + "\n"
    )
    seen = [(r["name"], r["arguments"], r["is_error"]) for r in records]
    if mechanism == "profile" and _TRACED_VERSION >= (3, 13):
        # The profile path cannot tell a close from a raise on 3.13 and later (its call event no
        # longer carries the thrown exception, and 3.14 ends the closed frame on a RESUME), so
        # the one record it writes is marked failed. It never writes a second one.
        expected = [(name, {"n": 3}, True)]
    else:
        expected = [(name, {"n": 3}, False)]
    if mechanism == "profile" and _TRACED_VERSION in {(3, 12), (3, 13)} and name == "bare":
        # Early 3.12 and 3.13 releases (3.12.3, 3.13.3) close a generator paused at a `yield`
        # outside any `try` without running its frame and fire no profile or monitoring event, so
        # the profile path sees no end and leaves the call unrecorded; later releases (CI's newest
        # 3.12 and 3.13) run the frame, and it is recorded as on 3.14. Never a wrong record. The
        # monitoring path settles the open call when its generator is gone, so it records it.
        assert seen in ([], expected)
    else:
        assert seen == expected


@pytest.mark.parametrize("later", ["agent.other_tool(1)", "agent.not_a_tool()"])
def test_a_generator_closed_at_a_bare_yield_is_settled_when_it_is_dropped(
    tmp_path: Path, mechanism: str, later: str
) -> None:
    """Where a close fires no event, the open call is settled when its generator is let go, and the
    exception events go off again."""
    if mechanism != "monitoring":
        pytest.skip("only the monitoring path keeps process-wide events on while a call is open")
    source = (
        "def bare(n):\n    yield 1\n    yield 2\n"
        "def other_tool(x):\n    return x\n"
        "def not_a_tool():\n    return 0\n"
    )
    (tmp_path / "agent.py").write_text(source)
    trace, env = _trace_environment(
        tmp_path,
        [{"name": n, "module": "agent", "callable": n} for n in ("bare", "other_tool")],
    )
    result = subprocess.run(
        [
            _TRACED_PYTHON,
            "-c",
            "import sys, agent\n"
            "g = agent.bare(3); next(g); g.close(); del g\n"
            f"{later}\n"
            "tool = next(i for i in range(6) if sys.monitoring.get_tool(i) == 'alk-tool-trace')\n"
            "print(sys.monitoring.get_events(tool))\n",
        ],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    # Only PY_START (1) is left on; PY_UNWIND and PY_THROW are off again.
    assert result.stdout.split()[-1] == "1"
    seen = [(r["name"], r["arguments"], r["is_error"], r["output"]) for r in _records(trace)]
    closed = ("bare", {"n": 3}, False, "the generator was closed before it finished")
    assert seen[0] == closed
    assert seen.count(closed) == 1


CLOSE_THEN_LOOK_UP = (
    "def search(n):\n    yield 1\n    yield 2\n"
    "def look_up(x):\n    return x\n"
)


def _close_driver(body: str) -> str:
    """A traced process that runs ``body`` three times, reading and clearing the trace after each
    step the way the runner does between calls, and prints the tool names each read saw."""
    return (
        "import json, os, sys, agent\n"
        "path = os.environ['HARNESS_TOOL_TRACE']\n"
        "def read():\n"
        "    if not os.path.exists(path):\n        return []\n"
        "    names = [json.loads(l)['name'] + ':' + json.loads(l)['output'] "
        "for l in open(path).read().splitlines()]\n"
        "    open(path, 'w').close()\n"
        "    return names\n"
        "for turn in range(3):\n"
        + "".join(f"    {line}\n" for line in body.splitlines())
    )


def test_a_generator_closed_at_a_bare_yield_is_recorded_in_its_own_calls_trace(
    tmp_path: Path, mechanism: str
) -> None:
    """The close is read right after it, never after the next tool call (the runner clears the
    trace between calls, so a record settled later lands in the next call's evidence)."""
    if mechanism != "monitoring":
        pytest.skip("only the monitoring path settles a close it was not told about")
    (tmp_path / "agent.py").write_text(CLOSE_THEN_LOOK_UP)
    result = subprocess.run(
        [_TRACED_PYTHON, "-c", _close_driver(
            "g = agent.search(3); next(g); g.close(); del g\n"
            "after_close = read()\n"
            "agent.look_up('x')\n"
            "after_look_up = read()\n"
            "print(json.dumps([after_close, after_look_up]))"
        )],
        check=True, text=True, capture_output=True, cwd=tmp_path,
        env=_trace_environment(tmp_path, [
            {"name": n, "module": "agent", "callable": n} for n in ("search", "look_up")
        ])[1],
    )
    closed = "search:the generator was closed before it finished"
    assert [json.loads(line) for line in result.stdout.splitlines()] == [
        [[closed], ["look_up:x"]]
    ] * 3


def test_a_closed_generator_that_is_still_held_never_lands_on_the_next_call(
    tmp_path: Path, mechanism: str
) -> None:
    """Where the close fires no event, a closed generator that is still held is not recorded (as on
    the profile path), and it is never recorded against the call that follows."""
    if mechanism != "monitoring":
        pytest.skip("only the monitoring path settles a close it was not told about")
    (tmp_path / "agent.py").write_text(CLOSE_THEN_LOOK_UP)
    trace, env = _trace_environment(
        tmp_path, [{"name": n, "module": "agent", "callable": n} for n in ("search", "look_up")]
    )
    driver = _close_driver(
        "g = agent.search(3); next(g); g.close(); held.append(g); del g\n"
        "after_close = read()\n"
        "agent.look_up('x')\n"
        "after_look_up = read()\n"
        "print(json.dumps([after_close, after_look_up, list(sys.version_info[:3])]))"
    ).replace("for turn in range(3):", "held = []\nfor turn in range(3):", 1)
    result = subprocess.run(
        [_TRACED_PYTHON, "-c", driver],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    closed = "search:the generator was closed before it finished"
    for after_close, after_look_up, version in map(json.loads, result.stdout.splitlines()):
        assert after_look_up == ["look_up:x"]
        if tuple(version) == (3, 12, 3):
            # Nothing fires at this close, and the generator is still held.
            assert after_close == []
        else:
            assert after_close in ([], [closed])


STREAM_TOOL = "def stream_rows(n):\n    for i in range(n):\n        yield i\n    return 'done'\n"


def test_a_generator_tool_call_scans_for_its_owner_only_where_a_bare_close_fires_nothing(
    tmp_path: Path, mechanism: str
) -> None:
    """`gc.get_referrers` walks every tracked object, so it is the cost of a generator tool call
    that grows with the agent's heap. The tracer probes its own tool id once, when it is
    installed, for whether closing a generator paused at a bare `yield` fires an event, and looks
    for the owner only where it does not and the frame cannot name its generator."""
    if mechanism != "monitoring":
        pytest.skip("only the monitoring path looks for a generator's owner")
    (tmp_path / "agent.py").write_text(STREAM_TOOL)
    trace, env = _trace_environment(
        tmp_path, [{"name": "stream_rows", "module": "agent", "callable": "stream_rows"}]
    )
    result = subprocess.run(
        [
            _TRACED_PYTHON,
            "-c",
            "import gc, sys, sitecustomize, agent\n"
            "scans = []\n"
            "real = gc.get_referrers\n"
            "gc.get_referrers = lambda *a: scans.append(1) or real(*a)\n"
            "assert list(agent.stream_rows(3)) == [0, 1, 2]\n"
            "g = agent.stream_rows(3); next(g); g.close()\n"
            "print(getattr(sitecustomize, '_TOOL_TRACE_NEEDS_OWNER', 'unset'), len(scans), *sys.version_info[:3])\n",
        ],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    needs, scans, *version = result.stdout.split()
    version = tuple(map(int, version))
    if version == (3, 12, 3):
        # This release fires nothing for a close at a bare `yield`, and its frames name no generator.
        assert (needs, int(scans) > 0) == ("True", True)
    elif version >= (3, 14):
        assert (needs, scans) == ("False", "0")
    elif needs == "False":
        assert scans == "0"
    else:
        assert int(scans) > 0
    # The close is recorded either way: by the event, or by the owner when it is let go.
    assert [r["output"] for r in _records(trace)][0] == "done"


def test_a_failed_probe_means_the_owner_is_needed(tmp_path: Path, mechanism: str) -> None:
    if mechanism != "monitoring":
        pytest.skip("only the monitoring path looks for a generator's owner")
    (tmp_path / "agent.py").write_text(STREAM_TOOL)
    trace, env = _trace_environment(
        tmp_path, [{"name": "stream_rows", "module": "agent", "callable": "stream_rows"}]
    )
    # A hook that raises for the probe's own events must not stop the tracer from installing.
    source = (tmp_path / "sitecustomize.py").read_text().replace(
        "                g = probe()\n", "                raise RuntimeError('probe failed')\n"
    )
    assert "probe failed" in source
    (tmp_path / "sitecustomize.py").write_text(source)
    result = subprocess.run(
        [_TRACED_PYTHON, "-c", "import sitecustomize\nprint(sitecustomize._TOOL_TRACE_NEEDS_OWNER)\n"],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    assert result.stdout.strip() == "True"


ARGUMENT_TOOL = "def t(blob, n=1):\n    return 'ok'\n"


def _traced_line(tmp_path: Path, mechanism: str, call: str) -> str:
    """The one line a tool call writes under ``mechanism``, minus its start time."""
    folder = tmp_path / mechanism
    folder.mkdir()
    (folder / "agent.py").write_text(ARGUMENT_TOOL)
    trace, env = _trace_environment(folder, [{"name": "t", "module": "agent", "callable": "t"}])
    env["ALK_TOOL_TRACE_MECHANISM"] = mechanism
    subprocess.run(
        [_TRACED_PYTHON, "-c", f"import agent\n{call}\n"], check=True, env=env, cwd=folder
    )
    (line,) = trace.read_text().splitlines()
    return re.sub(r'"at": [0-9.e+-]+', '"at": 0', line)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param("agent.t('x' * 50_000_000)", id="a-50-mb-string"),
        pytest.param("agent.t([[i, 'row %d' % i] for i in range(300_000)])", id="a-300k-row-list"),
        pytest.param("agent.t('x' * 9_999, 'y' * 3_000)", id="a-string-that-fits-beside-one-that-is-cut"),
        pytest.param(
            "agent.t(['x' * 20_000], {'a': 'k' * 3_000, 'b': 'z' * 30_000})",
            id="a-dict-beside-a-list",
        ),
        pytest.param("agent.t(*['q' * 12_000, 'r' * 12_000])", id="two-cut-values-over-the-line-limit"),
        pytest.param("agent.t(tuple(range(20_000)))", id="a-tuple"),
    ],
)
def test_an_oversize_argument_is_recorded_as_the_profile_path_records_it(
    tmp_path: Path, mechanism: str, call: str
) -> None:
    if mechanism != "monitoring":
        pytest.skip("compares the monitoring path with the profile path")
    profile = _traced_line(tmp_path, "profile", call)
    monitoring = _traced_line(tmp_path, "monitoring", call)
    assert monitoring == profile


def test_a_large_argument_is_not_copied_in_full_at_entry(tmp_path: Path, mechanism: str) -> None:
    """Only what is written is copied: a 50 MB string costs a few KB, not its JSON and its copy."""
    if mechanism != "monitoring":
        pytest.skip("only the monitoring path reads the arguments at entry")
    (tmp_path / "agent.py").write_text(ARGUMENT_TOOL)
    trace, env = _trace_environment(tmp_path, [{"name": "t", "module": "agent", "callable": "t"}])
    result = subprocess.run(
        [
            _TRACED_PYTHON,
            "-c",
            "import resource, sys, agent\n"
            "scale = 1 if sys.platform == 'darwin' else 1024\n"
            "blob = 'x' * 50_000_000\n"
            "before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale\n"
            "agent.t(blob)\n"
            "print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale - before)\n",
        ],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    assert int(result.stdout) < 20_000_000


ASYNC_TOOLS = (
    "import asyncio\n"
    "async def inner():\n    await asyncio.sleep(10)\n"
    "async def direct(q):\n    await asyncio.sleep(10)\n    return 1\n"
    "async def nested(q):\n    await inner()\n    return 1\n"
    "async def guarded(q):\n    try:\n        await asyncio.sleep(10)\n    finally:\n        pass\n"
    "async def reraises(q):\n    try:\n        await asyncio.sleep(10)\n"
    "    except asyncio.CancelledError:\n        await asyncio.sleep(0)\n        raise\n"
    "async def replaces(q):\n    try:\n        await asyncio.sleep(10)\n"
    "    except asyncio.CancelledError:\n        raise ValueError('own')\n"
    "async def fails(q):\n    await asyncio.sleep(0)\n    raise ValueError('own')\n"
)
CANCEL_DRIVER = (
    "import asyncio, agent\n"
    "async def main():\n"
    "    task = asyncio.ensure_future(agent.{n}('x'))\n"
    "    await asyncio.sleep(0.05)\n"
    "    task.cancel()\n"
    "    try:\n        await task\n    except BaseException:\n        pass\n"
    "asyncio.run(main())\n"
)


@pytest.mark.parametrize("name", ["direct", "nested", "guarded", "reraises"])
def test_a_cancelled_coroutine_is_recorded_as_cancelled_not_as_a_raise(
    tmp_path: Path, name: str, mechanism: str
) -> None:
    records = _run_agent(tmp_path, ASYNC_TOOLS, [name], CANCEL_DRIVER.format(n=name))
    if mechanism == "profile" and _TRACED_VERSION >= (3, 13):
        # The profile call event carries None instead of the cancel from 3.13 on, so the
        # fallback reads the cancel as the tool raising: one record, marked failed.
        assert [(r["name"], r["is_error"], r["output"]) for r in records] == [
            (name, True, RAISED)
        ]
        return
    assert [(r["name"], r["is_error"]) for r in records] == [(name, False)]
    assert "cancel" in records[0]["output"]


@pytest.mark.parametrize("name", ["replaces", "fails"])
def test_a_tool_that_raises_its_own_error_is_still_an_error(
    tmp_path: Path, name: str
) -> None:
    records = _run_agent(tmp_path, ASYNC_TOOLS, [name], CANCEL_DRIVER.format(n=name))
    assert [(r["name"], r["is_error"]) for r in records] == [(name, True)]


BAD_EXITS = (
    "class BadExit:\n    def __str__(self):\n        raise SystemExit(3)\n    __repr__ = __str__\n"
    "class BadInt:\n    def __str__(self):\n        raise KeyboardInterrupt\n    __repr__ = __str__\n"
    "def t(x):\n    return 'ok'\n"
)


@pytest.mark.parametrize("bad", ["BadExit", "BadInt"])
def test_an_argument_whose_text_exits_never_ends_the_agent(tmp_path: Path, bad: str) -> None:
    records = _run_agent(
        tmp_path, BAD_EXITS, ["t"],
        f"import agent\nassert agent.t(agent.{bad}()) == 'ok'\nassert agent.t('second') == 'ok'\n",
    )
    assert {"x": "second"} in [r["arguments"] for r in records]
    assert any(r["arguments"] == {"x": f"<unprintable {bad}>"} for r in records)


def test_an_unprintable_argument_is_recorded_as_a_placeholder(tmp_path: Path) -> None:
    source = (
        "class Bad:\n    def __str__(self):\n        raise RuntimeError('lazy load')\n"
        "    __repr__ = __str__\n"
        "def t(x):\n    return 'ok'\n"
    )
    records = _run_agent(
        tmp_path, source, ["t"], "import agent\nagent.t(agent.Bad())\nagent.t('second')\n"
    )
    assert [r["arguments"] for r in records] == [{"x": "<unprintable Bad>"}, {"x": "second"}]


@pytest.mark.parametrize(
    "first_call",
    [
        pytest.param("agent.t(2000)", id="deep-return"),
        pytest.param("agent.t({(1, 2): 'a'})", id="tuple-key-argument"),
        pytest.param("agent.t({1: 'a', 'b': 2})", id="mixed-key-argument"),
    ],
)
def test_a_value_that_cannot_be_encoded_still_records_the_call(
    tmp_path: Path, first_call: str
) -> None:
    source = (
        "def t(x):\n"
        "    if x == 2000:\n"
        "        deep = {}\n"
        "        for _ in range(x):\n            deep = {'k': deep}\n"
        "        return deep\n"
        "    return 'ok'\n"
    )
    records = _run_agent(
        tmp_path, source, ["t"], f"import agent\n{first_call}\nagent.t('second')\n"
    )
    assert [r["name"] for r in records] == ["t", "t"]
    assert records[0]["is_error"] is False
    assert records[1]["arguments"] == {"x": "second"}


def test_a_caught_recursion_error_does_not_turn_tracing_off(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "def t(x):\n    return 'ok'\n",
        ["t"],
        "import agent\n"
        "agent.t('first')\n"
        "def down(n):\n    return down(n + 1)\n"
        "try:\n    down(0)\nexcept RecursionError:\n    pass\n"
        "open('agent.py').close()\n"
        "agent.t('second')\n",
    )
    assert [r["arguments"] for r in records] == [{"x": "first"}, {"x": "second"}]




_CHAT_MARK = "ALK_OPENAI_AGENTS_CHAT_COMPLETIONS"
_STANDIN_AGENTS = (
    "import os\n"
    "def _note(line):\n"
    "    with open(os.environ['STANDIN_CALLS'], 'a') as handle:\n"
    "        handle.write(line + '\\n')\n"
    "_note('module-ran')\n"
    "def set_default_openai_api(api):\n"
    "    _note(api)\n"
    "def set_tracing_export_api_key(key):\n"
    "    with open(os.environ['STANDIN_CALLS'] + '.key', 'a') as handle:\n"
    "        handle.write(key + '\\n')\n"
)


# The SDK's own route to its trace exporter (0.18.3): set_default_openai_key(key) with
# use_for_tracing=True calls set_tracing_export_api_key, which calls
# tracing.processors.default_exporter().set_api_key.
_STANDIN_AGENTS_WITH_EXPORTER = (
    "import os\n"
    "from .tracing import set_tracing_export_api_key, set_tracing_disabled\n"
    "def _note(line):\n"
    "    with open(os.environ['STANDIN_CALLS'], 'a') as handle:\n"
    "        handle.write(line + '\\n')\n"
    "_note('module-ran')\n"
    "def set_default_openai_api(api):\n"
    "    _note(api)\n"
    "def set_default_openai_key(key, use_for_tracing=True):\n"
    "    if use_for_tracing:\n"
    "        set_tracing_export_api_key(key)\n"
)
_STANDIN_TRACING = {
    "__init__.py": (
        "import os\n"
        "from .processors import default_exporter\n"
        "def set_tracing_export_api_key(api_key):\n"
        "    default_exporter().set_api_key(api_key)\n"
        # The SDK reads its switch once, on the first trace it creates, and its manual
        # set_tracing_disabled always takes precedence over it (0.18.3 provider.py:284-306).
        "_env = None\n"
        "_manual = None\n"
        "def set_tracing_disabled(disabled):\n"
        "    global _manual\n"
        "    _manual = disabled\n"
        "def create_trace():\n"
        "    global _env\n"
        "    if _env is None:\n"
        "        _env = os.environ.get('OPENAI_AGENTS_DISABLE_TRACING', 'false').lower() in ('true', '1')\n"
        "    return not (_env if _manual is None else _manual)\n"
    ),
    "processors.py": (
        "import os\n"
        "class _Exporter:\n"
        "    def set_api_key(self, api_key):\n"
        "        with open(os.environ['STANDIN_CALLS'] + '.key', 'a') as handle:\n"
        "            handle.write(api_key + '\\n')\n"
        "    def export(self, items):\n"
        "        self._export_with_deadline(items, deadline=None)\n"
        "    def _export_with_deadline(self, items, deadline):\n"
        "        with open(os.environ['STANDIN_CALLS'] + '.export', 'a') as handle:\n"
        "            handle.write(str(len(items)) + '\\n')\n"
        # A version whose exporter sends through another method.
        "if os.environ.get('STANDIN_NO_DEADLINE'):\n"
        "    del _Exporter._export_with_deadline\n"
        "_exporter = None\n"
        "def default_exporter():\n"
        "    global _exporter\n"
        "    if _exporter is None:\n"
        "        _exporter = _Exporter()\n"
        "    return _exporter\n"
    ),
}
_GATEWAY_KEY = {"OPENAI_API_KEY": "sk-FAKE-GATEWAY"}


def _real_openai_agents_installed() -> bool:
    import importlib.metadata

    try:
        importlib.metadata.distribution("openai-agents")
    except importlib.metadata.PackageNotFoundError:
        return False
    return True


def _chat_style_run(
    tmp_path: Path,
    code: str,
    *,
    mark: bool | str = True,
    dist: bool = True,
    agents_source: str = _STANDIN_AGENTS,
    hook_on_path: bool = True,
    flags: tuple[str, ...] = (),
    exporter: bool = False,
    extra_env: dict[str, str] | None = None,
):
    hook = tmp_path / "hook"
    hook.mkdir()
    bootstrap = (
        Path(__file__).resolve().parents[2]
        / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    )
    shutil.copy2(bootstrap, hook / "sitecustomize.py")
    standin = tmp_path / "standin"
    (standin / "agents").mkdir(parents=True)
    (standin / "agents" / "__init__.py").write_text(agents_source)
    if exporter:
        (standin / "agents" / "tracing").mkdir()
        for name, text in _STANDIN_TRACING.items():
            (standin / "agents" / "tracing" / name).write_text(text)
    if dist:
        info = standin / "openai_agents-0.24.0.dist-info"
        info.mkdir()
        (info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: openai-agents\nVersion: 0.24.0\n"
        )
    calls = tmp_path / "calls.txt"
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            _CHAT_MARK, "HARNESS_TOOL_TRACE", "PYTHONPATH", "OPENAI_API_KEY",
            "AGENTCC_API_KEY", "OPENAI_AGENTS_DISABLE_TRACING",
        }
    }
    env["STANDIN_CALLS"] = str(calls)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(hook), str(standin)] if hook_on_path else [str(standin)]
    )
    if mark:
        env[_CHAT_MARK] = "1" if mark is True else mark
    env.update(extra_env or {})
    result = subprocess.run(
        [sys.executable, *flags, "-c", code],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    lines = calls.read_text().splitlines() if calls.exists() else []
    return result, lines


def _note_line(line: str) -> str:
    return f"open(__import__('os').environ['STANDIN_CALLS'], 'a').write({line!r} + '\\n')\n"


def test_openai_agents_chat_style_is_set_after_the_sdk_ran_and_only_on_import(
    tmp_path: Path,
) -> None:
    code = (
        _note_line("before-import")
        + "import agents\n"
        + _note_line("after-import")
    )
    result, lines = _chat_style_run(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert lines == ["before-import", "module-ran", "chat_completions", "after-import"]


def test_openai_agents_chat_style_is_not_set_at_start_up(tmp_path: Path) -> None:
    result, lines = _chat_style_run(tmp_path, _note_line("started"))
    assert result.returncode == 0, result.stderr
    assert lines == ["started"]


def test_openai_agents_chat_style_needs_the_mark(tmp_path: Path) -> None:
    result, lines = _chat_style_run(tmp_path, "import agents\n", mark=False)
    assert result.returncode == 0, result.stderr
    assert lines == ["module-ran"]


def test_openai_agents_chat_style_needs_the_mark_to_be_exactly_one(
    tmp_path: Path,
) -> None:
    result, lines = _chat_style_run(tmp_path, "import agents\n", mark="true")
    assert result.returncode == 0, result.stderr
    assert lines == ["module-ran"]


def test_openai_agents_chat_style_leaves_a_customer_package_named_agents_alone(
    tmp_path: Path,
) -> None:
    if _real_openai_agents_installed():
        pytest.skip("openai-agents is installed in the test interpreter")
    result, lines = _chat_style_run(tmp_path, "import agents\n", dist=False)
    assert result.returncode == 0, result.stderr
    assert lines == ["module-ran"]


def test_openai_agents_chat_style_failure_is_one_stderr_line_and_the_import_succeeds(
    tmp_path: Path,
) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        "import agents\nprint('imported')\n",
        agents_source="from .tracing import set_tracing_export_api_key\n",
        exporter=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"
    assert result.stderr.splitlines() == [
        "alk: could not set the OpenAI Agents SDK to chat completions: AttributeError"
    ]


def test_openai_agents_trace_export_key_is_a_fixed_placeholder(tmp_path: Path) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        "import agents\n",
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env=_GATEWAY_KEY,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    keys = (tmp_path / "calls.txt.key").read_text().splitlines()
    assert keys == ["alk-tracing-disabled"]


def test_openai_agents_trace_export_key_is_set_when_the_chat_style_setting_fails(
    tmp_path: Path,
) -> None:
    source = _STANDIN_AGENTS_WITH_EXPORTER.replace(
        "def set_default_openai_api(api):\n    _note(api)\n", ""
    )
    assert "set_default_openai_api" not in source
    result, lines = _chat_style_run(
        tmp_path, "import agents\n", agents_source=source, exporter=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.splitlines() == [
        "alk: could not set the OpenAI Agents SDK to chat completions: AttributeError"
    ]
    keys = (tmp_path / "calls.txt.key").read_text().splitlines()
    assert keys == ["alk-tracing-disabled"]


@pytest.mark.parametrize(
    "code",
    [
        "agents.set_default_openai_key('sk-FAKE-GATEWAY')\n",
        "agents.set_tracing_export_api_key('sk-FAKE-GATEWAY')\n",
        "agents.set_tracing_export_api_key('')\n",
    ],
    ids=["default_key", "export_key", "empty_key"],
)
def test_the_gateway_key_never_becomes_the_trace_export_key(tmp_path: Path, code: str) -> None:
    # The SDK's set_default_openai_key and set_default_openai_client hand their key to the trace
    # exporter by default, and an empty key falls back to OPENAI_API_KEY: the gateway key.
    result, lines = _chat_style_run(
        tmp_path,
        "import agents\n" + code,
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env=_GATEWAY_KEY,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    keys = (tmp_path / "calls.txt.key").read_text().splitlines()
    assert keys[-1] == "alk-tracing-disabled"
    assert "sk-FAKE-GATEWAY" not in keys


_EXPORT_A_SPAN = (
    "import agents\n"
    "from agents.tracing.processors import default_exporter\n"
    "default_exporter().export(['span'])\n"
)
# The SDK's batch processor calls this one directly when it flushes with a deadline (at exit).
_EXPORT_A_SPAN_BY_DEADLINE = (
    "import agents\n"
    "from agents.tracing.processors import default_exporter\n"
    "default_exporter()._export_with_deadline(['span'], 1.0)\n"
)


@pytest.mark.parametrize(
    "code", [_EXPORT_A_SPAN, _EXPORT_A_SPAN_BY_DEADLINE], ids=["export", "with_deadline"]
)
def test_the_sdks_default_exporter_sends_nothing_under_the_mark(
    tmp_path: Path, code: str
) -> None:
    # Whatever key it holds, the SDK's own exporter to OpenAI doesn't send on a gateway run; the
    # kit's span observation is a separate processor and isn't affected.
    result, lines = _chat_style_run(
        tmp_path,
        code,
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env=_GATEWAY_KEY,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert not (tmp_path / "calls.txt.export").exists()


def test_the_sdks_default_exporter_is_left_alone_without_the_mark(tmp_path: Path) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        _EXPORT_A_SPAN,
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env=_GATEWAY_KEY,
        mark=False,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "calls.txt.export").read_text().splitlines() == ["1"]


def test_an_exporter_the_agent_builds_itself_sends_nothing_under_the_mark(
    tmp_path: Path,
) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        "import agents\n"
        "from agents.tracing.processors import default_exporter\n"
        "type(default_exporter())().export(['span'])\n",
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env=_GATEWAY_KEY,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "calls.txt.export").exists()


_SWITCH_AFTER_IMPORT = (
    "import os, agents\nprint(os.environ.get('OPENAI_AGENTS_DISABLE_TRACING'))\n"
)


_KIT_SWITCH = {"OPENAI_AGENTS_DISABLE_TRACING": "1", "ALK_SDK_TRACING_OFF_BY_KIT": "1"}
_FIRST_TRACE = "import agents\nfrom agents import tracing\nprint(tracing.create_trace())\n"


def test_the_hook_turns_the_sdks_tracing_back_on_once_its_export_is_local(
    tmp_path: Path,
) -> None:
    # With the export local, this process traces, so the kit's span observation runs. The
    # environment keeps the switch, so a child the hook never reaches (python -I) stays off.
    result, lines = _chat_style_run(
        tmp_path,
        "import os, agents\nfrom agents import tracing\n"
        "print(os.environ.get('OPENAI_AGENTS_DISABLE_TRACING'), tracing.create_trace())\n",
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env={**_GATEWAY_KEY, **_KIT_SWITCH},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1 True"


def test_an_exporter_without_both_send_methods_leaves_tracing_off(tmp_path: Path) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        _FIRST_TRACE,
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env={**_GATEWAY_KEY, **_KIT_SWITCH, "STANDIN_NO_DEADLINE": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
    assert result.stderr.splitlines() == [
        "alk: could not keep the OpenAI Agents SDK trace export local (AttributeError), so its "
        "tracing stays off and its spans aren't observed"
    ]


@pytest.mark.parametrize("mark, traced", [(True, "True"), (False, "False")])
def test_the_first_trace_runs_under_the_mark_and_not_without_it(
    tmp_path: Path, mark: bool, traced: str
) -> None:
    # The SDK caches its switch on its first trace; the hook turns it on before that.
    result, lines = _chat_style_run(
        tmp_path,
        _FIRST_TRACE,
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER + "from . import tracing\n",
        exporter=True,
        extra_env={**_GATEWAY_KEY, **_KIT_SWITCH},
        mark=mark,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == traced


@pytest.mark.parametrize("value", ["1", "true"])
def test_a_customers_own_tracing_switch_is_left_on(tmp_path: Path, value: str) -> None:
    # Without the kit's marker the switch is the customer's: their tracing stays off.
    result, lines = _chat_style_run(
        tmp_path,
        _FIRST_TRACE,
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER + "from . import tracing\n",
        exporter=True,
        extra_env={**_GATEWAY_KEY, "OPENAI_AGENTS_DISABLE_TRACING": value},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_where_the_export_cant_be_kept_local_the_sdks_tracing_stays_off(
    tmp_path: Path,
) -> None:
    # The stand-in has no tracing module to reach, so the build's switch is left on.
    result, lines = _chat_style_run(
        tmp_path,
        _SWITCH_AFTER_IMPORT,
        extra_env={**_GATEWAY_KEY, "OPENAI_AGENTS_DISABLE_TRACING": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"
    assert result.stderr.splitlines() == [
        "alk: could not keep the OpenAI Agents SDK trace export local (KeyError), so its "
        "tracing stays off and its spans aren't observed"
    ]


def test_an_agents_own_trace_export_key_still_wins(tmp_path: Path) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        "import agents\nagents.set_tracing_export_api_key('sk-agent-own')\n",
        agents_source=_STANDIN_AGENTS_WITH_EXPORTER,
        exporter=True,
        extra_env=_GATEWAY_KEY,
    )
    assert result.returncode == 0, result.stderr
    keys = (tmp_path / "calls.txt.key").read_text().splitlines()
    assert keys == ["alk-tracing-disabled", "sk-agent-own"]


def test_openai_agents_trace_export_key_is_left_alone_without_the_mark(
    tmp_path: Path,
) -> None:
    result, lines = _chat_style_run(tmp_path, "import agents\n", mark=False)
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "calls.txt.key").exists()


def test_openai_agents_without_the_trace_key_setter_is_one_stderr_line_and_the_agent_runs(
    tmp_path: Path,
) -> None:
    # The SDK's tracing is there, but not the setter.
    source = _STANDIN_AGENTS.split("def set_tracing_export_api_key")[0] + "from . import tracing\n"
    result, lines = _chat_style_run(
        tmp_path, "import agents\nprint('imported')\n", agents_source=source, exporter=True
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"
    assert lines == ["module-ran", "chat_completions"]
    assert result.stderr.splitlines() == [
        "alk: could not set the OpenAI Agents SDK trace export key: AttributeError"
    ]


def test_openai_agents_own_later_choice_stays_the_last_value(tmp_path: Path) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        "import agents\nagents.set_default_openai_api('responses')\n",
    )
    assert result.returncode == 0, result.stderr
    assert lines == ["module-ran", "chat_completions", "responses"]


def test_openai_agents_import_error_reaches_the_importer_without_the_setting(
    tmp_path: Path,
) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        "try:\n"
        "    import agents\n"
        "except RuntimeError as exc:\n"
        "    print('raised', exc)\n",
        agents_source=(
            _STANDIN_AGENTS + "raise RuntimeError('sdk import failed')\n"
        ),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "raised sdk import failed"
    assert lines == ["module-ran"]
    assert result.stderr == ""


def test_openai_agents_chat_style_is_set_once_across_a_reimport(tmp_path: Path) -> None:
    result, lines = _chat_style_run(
        tmp_path,
        "import sys\nimport agents\ndel sys.modules['agents']\nimport agents\n",
    )
    assert result.returncode == 0, result.stderr
    assert lines == ["module-ran", "chat_completions", "module-ran"]


def test_openai_agents_chat_style_is_set_at_once_when_agents_is_already_imported(
    tmp_path: Path,
) -> None:
    hook = tmp_path / "hook"
    code = (
        "import agents, sys\n"
        + _note_line("before-bootstrap")
        + f"sys.path.insert(0, {str(hook)!r})\n"
        + "import sitecustomize\n"
        + _note_line("after-bootstrap")
        + "del sys.modules['agents']\n"
        + "import agents\n"
    )
    result, lines = _chat_style_run(
        tmp_path, code, hook_on_path=False, flags=("-S",)
    )
    assert result.returncode == 0, result.stderr
    assert lines == [
        "module-ran",
        "before-bootstrap",
        "chat_completions",
        "after-bootstrap",
        "module-ran",
    ]


def test_real_openai_agents_sdk_is_on_chat_completions_with_the_mark(
    tmp_path: Path,
) -> None:
    if not _real_openai_agents_installed():
        pytest.skip("openai-agents is not installed in the test interpreter")
    hook = tmp_path / "hook"
    hook.mkdir()
    bootstrap = (
        Path(__file__).resolve().parents[2]
        / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    )
    shutil.copy2(bootstrap, hook / "sitecustomize.py")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"HARNESS_TOOL_TRACE", "PYTHONPATH"}
    }
    env.update({"PYTHONPATH": str(hook), _CHAT_MARK: "1"})
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agents\n"
            "print(agents.models._openai_shared.get_use_responses_by_default())\n",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"


def test_real_openai_agents_sdk_keeps_the_gateway_key_off_its_trace_export(
    tmp_path: Path,
) -> None:
    # Against the real SDK (CI pins 0.18.3): the exporter class sends nothing, the kit's switch
    # is cleared so the kit's span observation runs, and a gateway key handed to the exporter
    # through set_default_openai_key becomes the placeholder.
    if not _real_openai_agents_installed():
        pytest.skip("openai-agents is not installed in the test interpreter")
    hook = tmp_path / "hook"
    hook.mkdir()
    bootstrap = (
        Path(__file__).resolve().parents[2]
        / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py"
    )
    shutil.copy2(bootstrap, hook / "sitecustomize.py")
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"HARNESS_TOOL_TRACE", "PYTHONPATH"}
    }
    env.update(
        {
            "PYTHONPATH": str(hook),
            _CHAT_MARK: "1",
            "OPENAI_API_KEY": "sk-FAKE-GATEWAY",
            "OPENAI_AGENTS_DISABLE_TRACING": "1",
            "ALK_SDK_TRACING_OFF_BY_KIT": "1",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os, agents\n"
            "from agents.tracing import processors, TracingProcessor, add_trace_processor, trace\n"
            "exporter = processors.default_exporter()\n"
            "print(type(exporter).export.__name__, type(exporter)._export_with_deadline.__name__)\n"
            "print(os.environ.get('OPENAI_AGENTS_DISABLE_TRACING'))\n"
            "agents.set_default_openai_key('sk-FAKE-GATEWAY', use_for_tracing=True)\n"
            "print(exporter.api_key)\n"
            "posts = []\n"
            "exporter._client.post = lambda *a, **k: posts.append(a)\n"
            "seen = []\n"
            "class Seen(TracingProcessor):\n"
            "    def on_trace_start(self, t): seen.append('trace')\n"
            "    def on_trace_end(self, t): pass\n"
            "    def on_span_start(self, s): pass\n"
            "    def on_span_end(self, s): pass\n"
            "    def shutdown(self): pass\n"
            "    def force_flush(self): pass\n"
            "add_trace_processor(Seen())\n"
            "with trace('alk-check'):\n"
            "    pass\n"
            "agents.tracing.get_trace_provider().force_flush()\n"
            "print(seen, len(posts))\n",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # The switch stays in the environment for children; this process traces, a processor sees
    # the trace, and nothing is posted to OpenAI.
    assert result.stdout.splitlines() == [
        "export_nothing export_nothing",
        "1",
        "alk-tracing-disabled",
        "['trace'] 0",
    ]


def test_openai_agents_hook_does_not_hide_a_later_finder_that_refuses_agents(
    tmp_path: Path,
) -> None:
    code = (
        "import sys\n"
        "class Refuse:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'agents':\n"
        "            raise ImportError('refused by the policy finder')\n"
        "sys.meta_path.insert(1, Refuse())\n"
        "import agents\n"
    )
    result, lines = _chat_style_run(tmp_path, code)
    assert result.returncode != 0
    assert "refused by the policy finder" in result.stderr
    assert lines == []


def test_openai_agents_chat_style_survives_an_early_spec_lookup(tmp_path: Path) -> None:
    code = "import importlib.util\nimportlib.util.find_spec('agents')\nimport agents\n"
    result, lines = _chat_style_run(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert lines == ["module-ran", "chat_completions"]


def test_openai_agents_reload_keeps_the_agent_own_choice(tmp_path: Path) -> None:
    code = (
        "import importlib\n"
        "import agents\n"
        "agents.set_default_openai_api('responses')\n"
        "importlib.reload(agents)\n"
    )
    result, lines = _chat_style_run(tmp_path, code)
    assert result.returncode == 0, result.stderr
    assert lines == ["module-ran", "chat_completions", "responses", "module-ran"]


def _load_hook(monkeypatch: pytest.MonkeyPatch, mark: str | None = None):
    import importlib.util

    if mark is None:
        monkeypatch.delenv(_CHAT_MARK, raising=False)
    else:
        monkeypatch.setenv(_CHAT_MARK, mark)
    monkeypatch.delenv("HARNESS_TOOL_TRACE", raising=False)
    for name in ("OPENAI_API_KEY", "AGENTCC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    spec = importlib.util.spec_from_file_location(
        "alk_hook_under_test",
        Path(__file__).resolve().parents[2]
        / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_openai_agents_missing_distribution_is_silent_and_other_errors_are_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.metadata

    hook = _load_hook(monkeypatch)
    monkeypatch.setenv(_CHAT_MARK, "1")

    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "distribution", missing)
    hook._install_openai_agents_chat_style()
    assert capsys.readouterr().err == ""

    def broken(name):
        raise OSError("unreadable metadata")

    monkeypatch.setattr(importlib.metadata, "distribution", broken)
    with pytest.raises(OSError):
        hook._install_openai_agents_chat_style()


def test_openai_agents_setup_failure_is_one_alk_line_and_the_hook_goes_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import importlib.metadata

    def broken(name):
        raise OSError("unreadable metadata")

    monkeypatch.setattr(importlib.metadata, "distribution", broken)
    hook = _load_hook(monkeypatch, mark="1")
    assert capsys.readouterr().err.splitlines() == [
        "alk: could not prepare the OpenAI Agents SDK setting: OSError"
    ]
    assert callable(hook._install_declared_python_tools)


def test_openai_agents_setting_without_reason_writes_the_exception_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    hook = _load_hook(monkeypatch)
    hook._set_openai_agents_chat_style(object())
    hook._set_openai_agents_chat_style(None, "its loader cannot be wrapped")
    # An SDK with neither setter: one line for each setting that could not be made.
    assert capsys.readouterr().err.splitlines() == [
        "alk: could not set the OpenAI Agents SDK trace export key: AttributeError",
        "alk: could not keep the OpenAI Agents SDK trace export local (AttributeError), so "
        "its tracing stays off and its spans aren't observed",
        "alk: could not set the OpenAI Agents SDK to chat completions: AttributeError",
        "alk: could not set the OpenAI Agents SDK to chat completions: "
        "its loader cannot be wrapped",
    ]


class _OldStyleLoaderFinder:
    def find_spec(self, fullname, path=None, target=None):
        import importlib.machinery

        return importlib.machinery.ModuleSpec(fullname, object())


class _RaisingFinder:
    def find_spec(self, fullname, path=None, target=None):
        raise RuntimeError("finder broke")


@pytest.mark.parametrize(
    "other", [_OldStyleLoaderFinder, _RaisingFinder], ids=["old_style_loader", "finder_raises"]
)
def test_where_the_hook_cant_wrap_the_sdk_the_builds_switch_is_left_on(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], other
) -> None:
    hook = _load_hook(monkeypatch)
    monkeypatch.setenv("OPENAI_AGENTS_DISABLE_TRACING", "1")
    finder = hook._OpenAIAgentsFinder()
    monkeypatch.setattr(sys, "meta_path", [finder, other()])
    finder.find_spec("agents")
    assert os.environ.get("OPENAI_AGENTS_DISABLE_TRACING") == "1"
    # A finder that raised may be tried again on a later import: the hook stays armed for it.
    assert finder.armed is (other is _RaisingFinder)


def test_the_loader_wrapper_copies_without_recursing(monkeypatch: pytest.MonkeyPatch) -> None:
    import copy
    import importlib.machinery

    hook = _load_hook(monkeypatch)
    real = importlib.machinery.SourceFileLoader("agents", "/nonexistent/agents/__init__.py")
    wrapper = hook._ChatStyleLoader(hook._OpenAIAgentsFinder(), real)
    copied = copy.copy(wrapper)
    assert copied.name == "agents"


def test_the_loader_wrapper_passes_for_the_sdks_own_loader_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pkg_resources picks its provider by the loader's class.
    import importlib.machinery

    hook = _load_hook(monkeypatch)
    real = importlib.machinery.SourceFileLoader("agents", "/nonexistent/agents/__init__.py")
    wrapper = hook._ChatStyleLoader(hook._OpenAIAgentsFinder(), real)
    assert isinstance(wrapper, importlib.machinery.SourceFileLoader)


def test_the_finder_does_not_walk_itself_again_while_it_walks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A finder ahead of it that walks the list again, or a second copy of the hook, would
    # otherwise call back into it without end.
    hook = _load_hook(monkeypatch)
    calls = []

    class WalksAgain:
        def find_spec(self, fullname, path=None, target=None):
            calls.append(fullname)
            if len(calls) > 20:  # the test's own bound, so the old code fails rather than hangs
                return None
            for finder in list(sys.meta_path):
                if finder is not self:
                    finder.find_spec(fullname, path, target)
            return None

    first = hook._OpenAIAgentsFinder()
    monkeypatch.setattr(sys, "meta_path", [first, WalksAgain(), hook._OpenAIAgentsFinder()])
    assert first.find_spec("agents") is None
    assert len(calls) < 5


def test_a_shared_zip_loader_is_left_unchanged(tmp_path: Path) -> None:
    import zipfile

    archive = tmp_path / "standin.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("agents/__init__.py", _STANDIN_AGENTS)
        bundle.writestr("other_module.py", "VALUE = 1\n")
        bundle.writestr(
            "openai_agents-0.24.0.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: openai-agents\nVersion: 0.24.0\n",
        )
    hook = tmp_path / "hook"
    hook.mkdir()
    shutil.copy2(
        Path(__file__).resolve().parents[2] / "src/fi/alk/harness/livekit_tool_trace_bootstrap.py",
        hook / "sitecustomize.py",
    )
    calls = tmp_path / "calls.txt"
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {_CHAT_MARK, "HARNESS_TOOL_TRACE", "PYTHONPATH", "OPENAI_API_KEY"}
    }
    env.update(
        {
            "PYTHONPATH": os.pathsep.join([str(hook), str(archive)]),
            _CHAT_MARK: "1",
            "STANDIN_CALLS": str(calls),
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agents, other_module\n"
            "print(type(other_module.__loader__).__name__, type(other_module.__spec__.loader).__name__)\n",
        ],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    # The SDK was set, and the zip's other module kept its own, unwrapped loader.
    assert calls.read_text().splitlines() == ["module-ran", "chat_completions"]
    assert result.stdout.split() == ["zipimporter", "zipimporter"]


def test_the_mechanism_in_use_is_the_one_asked_for(tmp_path: Path, mechanism: str) -> None:
    trace, env = _trace_environment(
        tmp_path, [{"name": "t", "module": "agent", "callable": "t"}]
    )
    result = subprocess.run(
        [_TRACED_PYTHON, "-c", "import sitecustomize\nprint(sitecustomize._TOOL_TRACE_MECHANISM)\n"],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    assert result.stdout.strip() == mechanism


def test_a_taken_tool_id_falls_back_to_the_profile_path(
    tmp_path: Path, mechanism: str
) -> None:
    if mechanism != "monitoring":
        pytest.skip("the id is only claimed on the monitoring path")
    (tmp_path / "agent.py").write_text("def t(x):\n    return x\n")
    trace, env = _trace_environment(
        tmp_path, [{"name": "t", "module": "agent", "callable": "t"}]
    )
    # The tracer under another module name, behind a sitecustomize that takes ids 4 and 3 first.
    (tmp_path / "tracer_under_test.py").write_text(
        (tmp_path / "sitecustomize.py").read_text()
    )
    (tmp_path / "sitecustomize.py").write_text(
        "import sys\n"
        "sys.monitoring.use_tool_id(4, 'other-a')\n"
        "sys.monitoring.use_tool_id(3, 'other-b')\n"
        "import tracer_under_test\n"
    )
    result = subprocess.run(
        [
            _TRACED_PYTHON,
            "-c",
            "import tracer_under_test, agent\n"
            "agent.t('A')\nprint(tracer_under_test._TOOL_TRACE_MECHANISM)\n",
        ],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    assert result.stdout.strip() == "profile"
    assert [(r["name"], r["arguments"], r["output"]) for r in _records(trace)] == [
        ("t", {"x": "A"}, "A")
    ]


def test_the_tracer_never_takes_the_reserved_optimizer_id(
    tmp_path: Path, mechanism: str
) -> None:
    if mechanism != "monitoring":
        pytest.skip("the id is only claimed on the monitoring path")
    (tmp_path / "agent.py").write_text("def t(x):\n    return x\n")
    trace, env = _trace_environment(
        tmp_path, [{"name": "t", "module": "agent", "callable": "t"}]
    )
    result = subprocess.run(
        [
            _TRACED_PYTHON,
            "-c",
            "import sys, agent\n"
            "agent.t('A')\n"
            "print(sys.monitoring.get_tool(5), sys.monitoring.OPTIMIZER_ID)\n"
            "print([i for i in range(6) if sys.monitoring.get_tool(i) == 'alk-tool-trace'])\n",
        ],
        check=True, text=True, capture_output=True, env=env, cwd=tmp_path,
    )
    assert result.stdout.splitlines() == ["None 5", "[4]"]


def test_a_list_the_tool_mutates_is_recorded_as_it_was_passed(
    tmp_path: Path, mechanism: str
) -> None:
    if mechanism != "monitoring":
        pytest.skip("the profile path reads the arguments when the call ends")
    records = _run_agent(
        tmp_path,
        "def t(xs, opts):\n    xs.append(9)\n    opts['seen'] = True\n    return len(xs)\n",
        ["t"],
        "import agent\nagent.t([1], {'a': 1})\n",
    )
    assert [(r["arguments"], r["output"]) for r in records] == [
        ({"xs": [1], "opts": {"a": 1}}, 2)
    ]


def test_a_tool_called_on_a_thread_is_recorded(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "def t(x):\n    return x\n",
        ["t"],
        "import threading, agent\n"
        "th = threading.Thread(target=agent.t, args=('on-thread',))\n"
        "th.start()\nth.join()\n",
    )
    assert [(r["arguments"], r["is_error"]) for r in records] == [
        ({"x": "on-thread"}, False)
    ]


def test_a_hot_function_that_is_not_a_tool_is_not_recorded(tmp_path: Path) -> None:
    records = _run_agent(
        tmp_path,
        "def t(x):\n    return x\ndef busy(i):\n    return i + 1\n",
        ["t"],
        "import agent\n"
        "n = 0\nfor _ in range(2000):\n    n = agent.busy(n)\n"
        "agent.t(n)\n",
    )
    assert [(r["name"], r["arguments"]) for r in records] == [("t", {"x": 2000})]


def test_a_tool_defined_inside_a_function_is_matched_by_its_own_name(
    tmp_path: Path,
) -> None:
    records = _run_bindings(
        tmp_path,
        "def make():\n    def lookup(q):\n        return q.upper()\n    return lookup\n"
        "def lookup(q):\n    return 'module level'\n",
        [{"name": "lookup", "module": "agent", "callable": "lookup", "qualname": "lookup"}],
        "import agent\nagent.make()('a')\n",
    )
    assert [(r["arguments"], r["output"]) for r in records] == [({"q": "a"}, "A")]


def test_a_method_is_matched_by_its_qualname_not_by_its_name_alone(tmp_path: Path) -> None:
    records = _run_bindings(
        tmp_path,
        "class Tools:\n    def lookup(self, q):\n        return q\n"
        "class Other:\n    def lookup(self, q):\n        return 'other'\n",
        [{"name": "lookup", "module": "agent", "callable": "lookup", "qualname": "Tools.lookup"}],
        "import agent\nagent.Other().lookup('x')\nagent.Tools().lookup('y')\n",
    )
    assert [(r["arguments"], r["output"]) for r in records] == [({"q": "y"}, "y")]


def test_a_method_inherited_from_a_base_is_matched_by_the_receivers_class(
    tmp_path: Path,
) -> None:
    records = _run_bindings(
        tmp_path,
        "class Base:\n    def lookup(self, q):\n        return q\n"
        "class Child(Base):\n    pass\n"
        "class Sibling(Base):\n    pass\n"
        "class Maker:\n    @classmethod\n    def build(cls, q):\n        return q\n"
        "class Made(Maker):\n    pass\n",
        [
            {"name": "lookup", "module": "agent", "callable": "lookup", "qualname": "Child.lookup"},
            {"name": "build", "module": "agent", "callable": "build", "qualname": "Made.build"},
        ],
        "import agent\n"
        "agent.Sibling().lookup('s')\nagent.Child().lookup('c')\n"
        "agent.Maker.build('m')\nagent.Made.build('d')\n",
    )
    assert [(r["name"], r["arguments"]) for r in records] == [
        ("lookup", {"q": "c"}),
        ("build", {"q": "d"}),
    ]


LATE_ERROR_AFTER_CANCEL = (
    "import asyncio\n"
    "async def catches(q):\n"
    "    try:\n        await asyncio.sleep(10)\n"
    "    except asyncio.CancelledError:\n        pass\n"
    "    try:\n        await asyncio.sleep(0)\n        raise ValueError('own')\n"
    "    finally:\n        pass\n"
)


def test_a_coroutine_that_catches_the_cancel_and_later_ends_on_its_own_error(
    tmp_path: Path, mechanism: str
) -> None:
    records = _run_agent(
        tmp_path, LATE_ERROR_AFTER_CANCEL, ["catches"], CANCEL_DRIVER.format(n="catches")
    )
    ((name, is_error, output),) = [(r["name"], r["is_error"], r["output"]) for r in records]
    assert name == "catches"
    if mechanism == "monitoring":
        # The unwinding exception is known, so an error of its own is an error.
        assert (is_error, output) == (True, RAISED)
    elif _TRACED_VERSION >= (3, 13):
        # From 3.13 the profile path reads every cancel as a raise, this one included.
        assert (is_error, output) == (True, RAISED)
    else:
        # The profile path cannot see which exception ended the frame: it reads the re-raise
        # after a cancel as the cancel. Pinned as known behaviour of the fallback.
        assert (is_error, "cancel" in output) == (False, True)
