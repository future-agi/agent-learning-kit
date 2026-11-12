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
)


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
        if key not in {_CHAT_MARK, "HARNESS_TOOL_TRACE", "PYTHONPATH"}
    }
    env["STANDIN_CALLS"] = str(calls)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(hook), str(standin)] if hook_on_path else [str(standin)]
    )
    if mark:
        env[_CHAT_MARK] = "1" if mark is True else mark
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
        agents_source="x = 1\n",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "imported"
    assert result.stderr.splitlines() == [
        "alk: could not set the OpenAI Agents SDK to chat completions: AttributeError"
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
        "import agents, site, sys\n"
        + _note_line("before-bootstrap")
        + f"site.addsitedir({str(hook)!r})\n"
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
