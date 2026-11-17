"""Framework-neutral observation of declared in-process Python tools."""

import json
import os
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


def _run_bindings(
    tmp_path: Path, source: str, bindings: list[dict[str, str]], driver: str
) -> list[dict]:
    (tmp_path / "agent.py").write_text(source)
    trace, env = _trace_environment(tmp_path, bindings)
    subprocess.run([sys.executable, "-c", driver], check=True, env=env, cwd=tmp_path)
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
        [sys.executable, "-c", "def look_up(x):\n    return x\nprint(look_up(7))\n"],
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
def test_l2_a_generator_closed_early_is_recorded_and_is_not_an_error(
    tmp_path: Path, name: str, driver: str
) -> None:
    records = _run_agent(
        tmp_path, GENERATORS, [name], "import agent\n" + driver.format(n=name) + "\n"
    )
    assert [(r["name"], r["arguments"], r["is_error"]) for r in records] == [
        (name, {"n": 3}, False)
    ]


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
def test_l3_a_cancelled_coroutine_is_recorded_as_cancelled_not_as_a_raise(
    tmp_path: Path, name: str
) -> None:
    records = _run_agent(tmp_path, ASYNC_TOOLS, [name], CANCEL_DRIVER.format(n=name))
    assert [(r["name"], r["is_error"]) for r in records] == [(name, False)]
    assert "cancel" in records[0]["output"]


@pytest.mark.parametrize("name", ["replaces", "fails"])
def test_l3_a_tool_that_raises_its_own_error_is_still_an_error(
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
def test_l1_an_argument_whose_text_exits_never_ends_the_agent(tmp_path: Path, bad: str) -> None:
    records = _run_agent(
        tmp_path, BAD_EXITS, ["t"],
        f"import agent\nassert agent.t(agent.{bad}()) == 'ok'\nassert agent.t('second') == 'ok'\n",
    )
    assert {"x": "second"} in [r["arguments"] for r in records]
    assert any(r["arguments"] == {"x": f"<unprintable {bad}>"} for r in records)


def test_l2_an_unprintable_argument_is_recorded_as_a_placeholder(tmp_path: Path) -> None:
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
def test_l3_a_value_that_cannot_be_encoded_still_records_the_call(
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


def test_m2_a_caught_recursion_error_does_not_turn_tracing_off(tmp_path: Path) -> None:
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


def test_the_mechanism_in_use_is_the_one_asked_for(tmp_path: Path, mechanism: str) -> None:
    trace, env = _trace_environment(
        tmp_path, [{"name": "t", "module": "agent", "callable": "t"}]
    )
    result = subprocess.run(
        [sys.executable, "-c", "import sitecustomize\nprint(sitecustomize._TOOL_TRACE_MECHANISM)\n"],
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
    # The tracer under another module name, behind a sitecustomize that takes ids 5 and 4 first.
    (tmp_path / "tracer_under_test.py").write_text(
        (tmp_path / "sitecustomize.py").read_text()
    )
    (tmp_path / "sitecustomize.py").write_text(
        "import sys\n"
        "sys.monitoring.use_tool_id(5, 'other-a')\n"
        "sys.monitoring.use_tool_id(4, 'other-b')\n"
        "import tracer_under_test\n"
    )
    result = subprocess.run(
        [
            sys.executable,
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


def test_m1_a_tool_defined_inside_a_function_is_matched_by_its_own_name(
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


def test_l4_a_method_inherited_from_a_base_is_matched_by_the_receivers_class(
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


def test_l3_a_coroutine_that_catches_the_cancel_and_later_ends_on_its_own_error(
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
    else:
        # The profile path cannot see which exception ended the frame: it reads the re-raise
        # after a cancel as the cancel. Pinned as known behaviour of the fallback.
        assert (is_error, "cancel" in output) == (False, True)
