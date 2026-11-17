"""Framework-neutral observation of declared in-process Python tools."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest


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
