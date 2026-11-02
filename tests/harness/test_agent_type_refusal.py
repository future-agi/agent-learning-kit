"""An agent whose main job isn't a conversation stops its build with a failure that says so."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import (
    amend,
    authoring_entrypoint,
    chat,
    cli,
    tools,
)
from fi.alk.harness import hosted_authoring_entrypoint as hosted_authoring
from fi.alk.harness.agent_type_check import (
    agent_type_failure,
    browser_tools_found,
    decide,
    failure_message,
    is_browser,
    modality_text,
)
from fi.alk.harness.contract import AgentContract, known_modalities
from fi.alk.harness.failure_file import FAILURE_FILE, write_failure_file
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
from fi.alk.harness.job import FailureDomain, HarnessFailure, HarnessJob, HarnessStage
from fi.alk.harness.sources import GitHubSource, RepoSource
from fi.alk.harness.tools import CONTRACT_SERVER, contract_tools
from fi.alk.harness.understand import load, open_stage

_FOUND_MESSAGE = (
    "This agent's main job is driving a browser, operating a computer or crawling sites "
    "(found: playwright). Only voice and chat agents are supported for now."
)
_READ_MESSAGE = (
    "This agent's main job was read as driving a browser, operating a computer or crawling "
    "sites. Only voice and chat agents are supported for now."
)
_FINISHED = ".futureagi-build-finished.json"


@pytest.fixture(autouse=True)
def _nothing_reaches_a_network(monkeypatch):
    for name in ("FI_BASE_URL", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("FI_API_KEY", raising=False)
    monkeypatch.delenv("FI_SECRET_KEY", raising=False)


def _tree(root: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


@pytest.fixture
def browser_repo(tmp_path) -> Path:
    return _tree(tmp_path / "source", {"agent.py": "from playwright.sync_api import sync_playwright\n"})


@pytest.fixture
def plain_repo(tmp_path) -> Path:
    return _tree(tmp_path / "source", {"agent.py": "import openai\n"})


# Complete enough that no nudge fires, so the first answer is about the modality alone.
def _reading(modality="chat", **fields):
    payload = {
        "agent": "fixture",
        "one_liner": "Answers questions.",
        "real_use_cases": ["Answer a question"],
        "tools": [{"name": "lookup", "args": ["query"]}],
        "hard_constraints": ["Stay polite"],
        "system_prompt_excerpt": "You answer questions.",
        **fields,
    }
    if modality is not None:
        payload["modality"] = modality
    return payload


def _submit_tool(server):
    return next(one for one in server.tools if one.name == "submit_contract")


def _submitter(destination: Path, scan_root: Path | None = None):
    tool = _submit_tool(contract_tools(destination, scan_root=scan_root))
    return lambda payload: asyncio.run(tool.handler(payload))


def _text(answer) -> str:
    return answer["content"][0]["text"]


def _failure(destination: Path) -> dict:
    return json.loads((destination / FAILURE_FILE).read_text(encoding="utf-8"))


def _a_failure() -> HarnessFailure:
    return HarnessFailure(
        domain=FailureDomain.AGENT,
        stage=HarnessStage.UNDERSTANDING_AGENT,
        code="first",
        message="the first failure",
        retryable=False,
        details={"key": "value"},
    )


# The writer


def test_writer_creates_folder(tmp_path):
    output = tmp_path / "gone" / "authoring"
    assert write_failure_file(_a_failure(), output) == output / FAILURE_FILE
    assert (output / FAILURE_FILE).is_file()


def test_writer_writes_full_shape(tmp_path):
    write_failure_file(_a_failure(), tmp_path)
    assert _failure(tmp_path) == {
        "domain": "agent",
        "stage": "understanding_agent",
        "code": "first",
        "message": "the first failure",
        "retryable": False,
        "details": {"key": "value"},
    }


def test_first_failure_stands(tmp_path):
    write_failure_file(_a_failure(), tmp_path)
    later = _a_failure().model_copy(update={"code": "later"})
    write_failure_file(later, tmp_path)
    assert _failure(tmp_path)["code"] == "first"


def test_file_is_world_readable(tmp_path):
    path = write_failure_file(_a_failure(), tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_writer_leaves_no_temp_file(tmp_path):
    write_failure_file(_a_failure(), tmp_path)
    write_failure_file(_a_failure(), tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == [FAILURE_FILE]


def test_failure_message_is_empty_when_the_file_cannot_be_read(tmp_path):
    assert failure_message(tmp_path) == ""
    for body in ("{", "[1]", '{"message": 3}'):
        (tmp_path / FAILURE_FILE).write_text(body, encoding="utf-8")
        assert failure_message(tmp_path) == ""


# The code check


@pytest.mark.parametrize(
    "files, expected",
    [
        ({"agent.py": "from playwright.sync_api import sync_playwright\n"}, ["playwright"]),
        ({"agent.py": "import browser_use\n"}, ["browser-use"]),
        (
            {"package.json": json.dumps({"dependencies": {"@playwright/mcp": "1"}})},
            ["@playwright/mcp"],
        ),
        ({"agent.py": 'TOOL = {"type": "computer_20241022"}\n'}, ["computer-use tool"]),
        ({"agent.ts": 'const tool = {type: "computer_use_preview"};\n'}, ["computer-use tool"]),
        (
            {"agent.py": 'from google.genai import types\nTOOL = types.ComputerUse(environment="x")\n'},
            ["computer-use tool"],
        ),
        ({"agent.py": 'ARGS = ["@playwright/mcp@latest"]\n'}, ["Playwright MCP"]),
        (
            {"agent.py": "from langchain_community.agent_toolkits import PlayWrightBrowserToolkit\n"},
            ["PlayWrightBrowserToolkit"],
        ),
        # A string holding "//" isn't a comment, so the code after it still counts.
        (
            {"agent.js": 'const home = "https://example.test"; const t = "computer_20241022";\n'},
            ["computer-use tool"],
        ),
    ],
)
def test_browser_tools_found_finds_what_the_agent_uses(tmp_path, files, expected):
    assert browser_tools_found(_tree(tmp_path, files)) == expected


def test_browser_tools_found_is_sorted_and_names_each_tool_once(tmp_path):
    _tree(
        tmp_path,
        {
            "agent.py": "import browser_use\nimport selenium\n",
            "web/package.json": json.dumps({"dependencies": {"browser-use": "1", "crawlee": "1"}}),
        },
    )
    assert browser_tools_found(tmp_path) == ["browser-use", "crawlee", "selenium"]


@pytest.mark.parametrize(
    "folder", ["e2eTests", "testdata", "unittests", "integrationtests", "Test_A"]
)
def test_tools_found_skips_these_test_folders(tmp_path, folder):
    (tmp_path / folder).mkdir()
    (tmp_path / folder / "a.py").write_text("import playwright\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == []


def test_tools_found_skips_an_upper_case_test_file(tmp_path):
    (tmp_path / "Test_A.py").write_text("import playwright\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == []


def test_tools_found_skips_a_vendored_package_json(tmp_path):
    folder = tmp_path / "node_modules" / "lib"
    folder.mkdir(parents=True)
    (folder / "package.json").write_text(
        json.dumps({"dependencies": {"playwright": "1"}}), encoding="utf-8"
    )
    assert browser_tools_found(tmp_path) == []


@pytest.mark.parametrize(
    "body",
    [
        "`" + "a\\`" * 40_000,
        "'" + "a\\'" * 40_000,
        "`" + "a\\`" * 40_000 + "\\",
        '"' + 'a\\"' * 40_000,
        "'" + "a\\'" * 40_000 + "\\",
        "'" + "a\\'" * 40_000 + "\n",
    ],
    ids=["template", "string", "ends-in-a-backslash", "double", "single-backslash", "single-newline"],
)
def test_an_odd_script_file_stays_quick(tmp_path, body):
    (tmp_path / "a.js").write_text(body, encoding="utf-8")
    started = time.monotonic()
    browser_tools_found(tmp_path)
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("folder", ["latest", "contest"])
def test_tools_found_reads_a_folder_with_test_inside_a_word(tmp_path, folder):
    (tmp_path / folder).mkdir()
    (tmp_path / folder / "a.py").write_text("import playwright\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == ["playwright"]


@pytest.mark.parametrize(
    "name, body",
    [
        ("node_modules/lib/a.py", "import playwright\n"),
        ("venv/lib/a.py", "import playwright\n"),
        ("tests/a.py", "import playwright\n"),
        ("e2e_tests/a.py", "import playwright\n"),
        ("Tests/a.py", "import playwright\n"),
        ("docs/a.py", "import playwright\n"),
        ("examples/a.py", "import playwright\n"),
        ("vendor/a.py", "import playwright\n"),
        ("evals/a.ts", 'const tool = "computer_20241022";\n'),
        (".cursor/a.py", 'ARGS = ["@playwright/mcp"]\n'),
        (".cursor/mcp.json", '{"args": ["@playwright/mcp"]}\n'),
        (".mcp.json", '{"args": ["@playwright/mcp"]}\n'),
        ("conftest.py", "import playwright\n"),
        ("test_e2e.py", "import playwright\n"),
        ("app_test.py", "import playwright\n"),
        ("app.spec.ts", 'const tool = "computer_20241022";\n'),
        ("app.test.js", 'const tool = "computer_20241022";\n'),
        ("pnpm-lock.yaml", "playwright: 1\n@playwright/mcp: 1\n"),
        ("requirements.txt", "playwright\n"),
        ("package.json", json.dumps({"devDependencies": {"playwright": "1"}})),
        ("tests/package.json", json.dumps({"dependencies": {"playwright": "1"}})),
        ("a.py", "# unlike computer_20241022, this only talks\n"),
        ("a.ts", '// see @playwright/mcp\n/* computer_use\n and ComputerUse */\nconst a = 1;\n'),
    ],
)
def test_browser_tools_found_ignores_what_is_not_the_agent(tmp_path, name, body):
    _tree(tmp_path, {"agent.py": "import openai\n", name: body})
    assert browser_tools_found(tmp_path) == []


def test_unpacked_distributions_are_not_the_agent(tmp_path):
    _tree(
        tmp_path,
        {
            "lib/anthropic-1.0.dist-info/METADATA": "x\n",
            "lib/anthropic/types.py": 'NAME = "computer_20241022"\n',
        },
    )
    assert browser_tools_found(tmp_path) == []


def test_a_customers_other_package_names_never_appear(tmp_path):
    _tree(
        tmp_path,
        {"package.json": json.dumps({"dependencies": {"acme-private-sdk": "1", "playwright": "1"}})},
    )
    assert browser_tools_found(tmp_path) == ["playwright"]


@pytest.mark.parametrize(
    "name, body",
    [
        ("package.json", "{"),
        ("package.json", '["playwright"]'),
        ("package.json", json.dumps({"dependencies": ["playwright"]})),
        ("broken.py", 'TOOL = ("computer_20241022"\n'),
    ],
)
def test_malformed_files_are_skipped(tmp_path, name, body):
    _tree(tmp_path, {name: body})
    assert browser_tools_found(tmp_path) == []


def test_undecodable_and_oversized_files_are_skipped(tmp_path):
    (tmp_path / "binary.py").write_bytes(b'NAME = "computer_20241022"\xff\n')
    (tmp_path / "huge.js").write_text(
        'const tool = "computer_20241022";\n' + "x" * (1024 * 1024), encoding="utf-8"
    )
    assert browser_tools_found(tmp_path) == []


def test_no_source_finds_nothing():
    assert browser_tools_found(None) == []


@pytest.mark.parametrize(
    "body",
    [
        "x = " + "-" * 200_000 + "1\n",
        "x = " + "not " * 100_000 + "1\n",
        "x = " + "lambda: " * 50_000 + "1\n",
        "x = 1" + "+1" * 100_000 + "\n",
    ],
    ids=["unary", "not", "lambda", "sum"],
)
def test_a_file_the_parser_cannot_hold_costs_only_that_file(tmp_path, body):
    (tmp_path / "odd.py").write_text(body, encoding="utf-8")
    (tmp_path / "agent.py").write_text("import playwright\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_package_of_the_same_name_at_the_top_is_the_agents_own(tmp_path):
    (tmp_path / "steel").mkdir()
    (tmp_path / "steel" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text("import steel\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == []


def test_a_pipe_named_like_code_is_left_alone(tmp_path):
    os.mkfifo(tmp_path / "x.py")
    (tmp_path / "agent.py").write_text("import playwright\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_module_of_the_same_name_at_the_top_is_the_agents_own(tmp_path):
    (tmp_path / "scrapy.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text("import scrapy\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == []


def test_an_oversized_python_file_is_left_out(tmp_path):
    (tmp_path / "big.py").write_text("import playwright\n" + "x = 1\n" * 200_000, encoding="utf-8")
    assert browser_tools_found(tmp_path) == []


# The decision


@pytest.mark.parametrize(
    "claimed, found, looked_again, expected",
    [
        ("browser", ["playwright"], False, "refuse"),
        ("browser", ["playwright"], True, "refuse"),
        ("browser", [], False, "second_look"),
        ("browser", [], True, "refuse"),
        ("chat", ["playwright"], False, "second_look"),
        ("chat", ["playwright"], True, "keep"),
        ("chat", [], False, "keep"),
        ("chat", [], True, "keep"),
        ("voice", ["playwright"], False, "second_look"),
        ("voice", ["playwright"], True, "keep"),
        (None, ["playwright"], False, "second_look"),
        (None, [], False, "keep"),
    ],
)
def test_decide_rows(claimed, found, looked_again, expected):
    assert decide(claimed, found, looked_again=looked_again) == expected


@pytest.mark.parametrize(
    "claimed",
    [" Browser ", ["browser"], ["browser", "chat"], "computer_use", "cua", "COMPUTER_USE"],
)
def test_decide_normalises_every_form_of_browser(claimed):
    assert decide(claimed, ["playwright"], looked_again=False) == "refuse"
    assert decide(claimed, [], looked_again=False) == "second_look"


def test_modality_text_and_is_browser():
    assert modality_text(None) == ""
    assert modality_text(["Browser", "chat"]) == "browser\nchat"
    assert is_browser("browser\nchat")
    assert not is_browser("chat")
    assert not is_browser("browsers")


def test_unknown_modality_gets_a_problem_not_a_save(tmp_path, plain_repo):
    submit = _submitter(tmp_path / "out", plain_repo)
    answer = submit(_reading("web"))
    assert answer["is_error"]
    assert "'web' is not a modality" in _text(answer)
    assert load(tmp_path / "out") is None
    assert not (tmp_path / "out" / FAILURE_FILE).exists()


# Browser reading, tool found


def test_the_scan_runs_off_the_event_loop(tmp_path, monkeypatch):
    threads = []

    def scan(_root):
        threads.append(threading.current_thread())
        return []

    monkeypatch.setattr(tools, "browser_tools_found", scan)
    _submitter(tmp_path / "out", tmp_path)(_reading("chat"))
    assert threads and threads[0] is not threading.main_thread()


def test_a_refusal_during_the_scan_stops_this_call_too(tmp_path, monkeypatch):
    output = tmp_path / "out"

    def scan(_root):
        write_failure_file(agent_type_failure(["playwright"]), output)
        return []

    monkeypatch.setattr(tools, "browser_tools_found", scan)
    _submitter(output, tmp_path)(_reading("chat"))
    assert not (output / "contract.json").exists()


def test_a_build_that_times_out_is_still_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS", "0")
    # `or 3300` keeps a zero from meaning "no time", so step the clock past the deadline instead.
    clock = iter([0.0, 10_000.0])
    monkeypatch.setattr(authoring_entrypoint.time, "monotonic", lambda: next(clock))
    assert authoring_entrypoint._await_harness(tmp_path) == 1


def test_ci_runs_these_tests():
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    assert "tests/harness/test_agent_type_refusal.py" in workflows.read_text(encoding="utf-8")


def test_the_file_is_what_the_platform_reads():
    assert FAILURE_FILE == "failure.json"


@pytest.mark.parametrize("modality", ["browser", ["browser"]])
def test_a_wrapped_browser_reading_is_refused(tmp_path, browser_repo, modality):
    output = tmp_path / "out"
    _submitter(output, browser_repo)({"contract": _reading(modality)})
    assert _failure(output)["code"] == "agent_type_unsupported"
    assert not (output / "contract.json").exists()


def test_free_text_mentioning_browser_gets_a_problem_not_a_block(tmp_path, browser_repo):
    output = tmp_path / "out"
    answer = _submitter(output, browser_repo)({"contract": _reading("chat via browser")})
    assert answer.get("is_error")
    assert "is not a modality" in _text(answer)
    assert not (output / FAILURE_FILE).exists()


def test_browser_with_tool_is_refused_on_the_first_submission(tmp_path, browser_repo):
    output = tmp_path / "out"
    answer = _submitter(output, browser_repo)(_reading("browser"))
    assert not answer.get("is_error")
    assert _FOUND_MESSAGE in _text(answer)
    assert "Don't submit again or do more work; tell the person." in _text(answer)
    assert _failure(output) == {
        "domain": "agent",
        "stage": "understanding_agent",
        "code": "agent_type_unsupported",
        "message": _FOUND_MESSAGE,
        "retryable": False,
        "details": {"agent_type": "browser", "tools": ["playwright"]},
    }
    assert not (output / "contract.json").exists()


# After a refusal


def test_chat_resubmission_after_a_refusal_saves_nothing(tmp_path, browser_repo):
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    submit(_reading("browser"))
    answer = submit(_reading("chat"))
    assert not answer.get("is_error")
    assert _FOUND_MESSAGE in _text(answer)
    assert not (output / "contract.json").exists()


def test_stop_from_a_fresh_conversation_with_the_file(tmp_path, plain_repo):
    output = tmp_path / "out"
    write_failure_file(_a_failure(), output)
    answer = _submitter(output, plain_repo)(_reading("chat"))
    assert not answer.get("is_error")
    assert "the first failure" in _text(answer)
    assert "Don't submit again" in _text(answer)
    assert not (output / "contract.json").exists()
    assert _failure(output)["code"] == "first"


def test_stop_message_copes_with_an_unreadable_file(tmp_path, plain_repo):
    output = tmp_path / "out"
    output.mkdir()
    (output / FAILURE_FILE).write_text("{", encoding="utf-8")
    answer = _submitter(output, plain_repo)(_reading("chat"))
    assert _text(answer).startswith("This build can't go on.")


# The write fails


def _failing_write(monkeypatch):
    def refuse_to_write(failure, output):
        raise OSError("disk full")

    monkeypatch.setattr(tools, "write_failure_file", refuse_to_write)


def test_writer_failure_blocks_resubmission(tmp_path, browser_repo, monkeypatch):
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    _failing_write(monkeypatch)
    with pytest.raises(OSError, match="disk full"):
        submit(_reading("browser"))
    assert not (output / FAILURE_FILE).exists()
    answer = submit(_reading("chat"))
    assert not answer.get("is_error")
    assert "This build can't go on." in _text(answer)
    assert not (output / "contract.json").exists()


def test_writer_failure_reaches_the_model_as_a_tool_error(tmp_path, browser_repo, monkeypatch):
    from mcp import types

    from fi.alk.harness.backends.claude import _sdk_server

    output = tmp_path / "out"
    instance = _sdk_server(contract_tools(output, scan_root=browser_repo))["instance"]
    handler = instance.request_handlers[types.CallToolRequest]
    _failing_write(monkeypatch)

    async def call():
        return await handler(
            types.CallToolRequest(
                method="tools/call",
                params=types.CallToolRequestParams(
                    name="submit_contract", arguments=_reading("browser")
                ),
            )
        )

    result = asyncio.run(call()).root
    assert result.isError
    assert "disk full" in result.content[0].text
    assert not (output / FAILURE_FILE).exists()


# Browser reading, no tool found


def test_browser_with_no_tool_gets_a_second_look(tmp_path, plain_repo):
    output = tmp_path / "out"
    answer = _submitter(output, plain_repo)(_reading("browser"))
    assert answer["is_error"]
    assert "second-look: you recorded modality browser" in _text(answer)
    assert "Does a person come to this agent for a conversation" in _text(answer)
    assert "don't ask anyone" in _text(answer)
    assert not (output / FAILURE_FILE).exists()
    assert not (output / "contract.json").exists()


def test_second_look_then_chat_saves_as_chat(tmp_path, plain_repo):
    output = tmp_path / "out"
    submit = _submitter(output, plain_repo)
    submit(_reading("browser"))
    answer = submit(_reading("chat"))
    assert not answer.get("is_error")
    assert load(output).modality == "chat"
    assert not (output / FAILURE_FILE).exists()


def test_second_look_then_browser_is_refused_with_no_tools(tmp_path, plain_repo):
    output = tmp_path / "out"
    submit = _submitter(output, plain_repo)
    submit(_reading("browser"))
    answer = submit(_reading("browser"))
    assert not answer.get("is_error")
    assert _READ_MESSAGE in _text(answer)
    assert _failure(output)["details"] == {"agent_type": "browser", "tools": []}
    assert not (output / "contract.json").exists()


def test_no_scan_root_behaves_as_no_tool(tmp_path):
    output = tmp_path / "out"
    submit = _submitter(output, None)
    assert "second-look" in _text(submit(_reading("browser")))
    assert not (output / FAILURE_FILE).exists()
    submit(_reading("browser"))
    assert _failure(output)["details"]["tools"] == []


# Chat or voice reading, tool found


@pytest.mark.parametrize("modality", ["chat", "voice"])
def test_conversation_with_tool_gets_a_second_look_naming_it(tmp_path, browser_repo, modality):
    output = tmp_path / "out"
    answer = _submitter(output, browser_repo)(_reading(modality))
    assert answer["is_error"]
    assert f"second-look: you recorded modality {modality}, and the source uses playwright." in _text(answer)
    assert not (output / "contract.json").exists()
    assert not (output / FAILURE_FILE).exists()


def test_empty_modality_reads_as_chat_in_the_second_look(tmp_path, browser_repo):
    answer = _submitter(tmp_path / "out", browser_repo)(_reading(None))
    assert "you recorded modality chat, and the source uses playwright" in _text(answer)


@pytest.mark.parametrize("modality", ["chat", "voice"])
def test_conversation_with_tool_then_same_answer_saves(tmp_path, browser_repo, modality):
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    submit(_reading(modality))
    assert not submit(_reading(modality)).get("is_error")
    assert load(output).modality == modality
    assert not (output / FAILURE_FILE).exists()


@pytest.mark.parametrize("modality", ["chat", "voice"])
def test_conversation_with_tool_then_browser_is_refused_naming_it(tmp_path, browser_repo, modality):
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    submit(_reading(modality))
    submit(_reading("browser"))
    assert _failure(output)["message"] == _FOUND_MESSAGE
    assert _failure(output)["details"]["tools"] == ["playwright"]
    assert not (output / "contract.json").exists()


# Everything else


@pytest.mark.parametrize("modality", ["chat", "voice"])
def test_conversation_with_no_tool_saves_with_no_second_look(tmp_path, plain_repo, modality):
    output = tmp_path / "out"
    assert not _submitter(output, plain_repo)(_reading(modality)).get("is_error")
    assert load(output).modality == modality


def test_only_one_second_look_and_one_scan(tmp_path, browser_repo, monkeypatch):
    output = tmp_path / "out"
    scans = []
    real_scan = tools.browser_tools_found

    def counted(root):
        scans.append(root)
        return real_scan(root)

    monkeypatch.setattr(tools, "browser_tools_found", counted)
    submit = _submitter(output, browser_repo)
    assert "second-look" in _text(submit(_reading("chat")))
    # A rejected submission after the look doesn't earn another one.
    rejected = submit(_reading("chat", real_use_cases=[]))
    assert rejected["is_error"] and "second-look" not in _text(rejected)
    assert not submit(_reading("chat")).get("is_error")
    assert load(output).modality == "chat"
    assert scans == [browser_repo]


# Every way into the stage reaches the scan


def _server_of(stage):
    return stage.spec.servers[CONTRACT_SERVER]


def test_both_build_modes_reach_the_scan(tmp_path, browser_repo):
    from fi.alk.harness.builder import open_for_building

    source = RepoSource(name="fixture", root=browser_repo)
    staged, _ = open_stage(source, out=tmp_path / "staged")
    conversational = open_for_building(None, out=tmp_path / "conversational", source=source)
    for stage, output in ((staged, "staged"), (conversational, "conversational")):
        tool = _submit_tool(_server_of(stage))
        asyncio.run(tool.handler(_reading("browser")))
        assert _failure(tmp_path / output)["details"]["tools"] == ["playwright"]


def test_github_source_reaches_the_scan(tmp_path, browser_repo):
    source = GitHubSource(name="fixture", root=browser_repo, url="https://github.test/a/b")
    stage, _ = open_stage(source, out=tmp_path / "out")
    asyncio.run(_submit_tool(_server_of(stage)).handler(_reading("browser")))
    assert _failure(tmp_path / "out")["details"]["tools"] == ["playwright"]


# The process waiting on the conversation's build


@pytest.fixture
def no_waiting(monkeypatch):
    def fail(_):
        raise AssertionError("the wait should have ended already")

    monkeypatch.setattr(authoring_entrypoint.time, "sleep", fail)


def test_await_harness_stops_on_the_file(tmp_path, no_waiting, capsys):
    write_failure_file(agent_type_failure(["playwright"]), tmp_path)
    assert authoring_entrypoint._await_harness(tmp_path) == 78
    assert _FOUND_MESSAGE in capsys.readouterr().err


def test_await_harness_failure_wins_over_finished(tmp_path, no_waiting):
    write_failure_file(agent_type_failure(["playwright"]), tmp_path)
    (tmp_path / _FINISHED).write_text("{}", encoding="utf-8")
    assert authoring_entrypoint._await_harness(tmp_path) == 78


def test_await_harness_finished_alone_returns_0(tmp_path, no_waiting):
    (tmp_path / _FINISHED).write_text("{}", encoding="utf-8")
    assert authoring_entrypoint._await_harness(tmp_path) == 0


def test_main_await_harness_with_the_file_never_validates(tmp_path, no_waiting, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    write_failure_file(agent_type_failure(["playwright"]), output)
    job = HarnessJob(
        job_id="refused",
        run_id="refused",
        execution="local",
        source={"kind": "local_repository", "local_path": str(source)},
        agent={"connector": "auto"},
        scenario_count=2,
        metadata={"generic_harness_v1": True},
    )
    job_path = tmp_path / "job.json"
    job_path.write_text(job.model_dump_json(), encoding="utf-8")
    validated = []

    async def validate(*args, **kwargs):
        validated.append(args)

    monkeypatch.setattr(authoring_entrypoint, "_review_while_writing", lambda *args: None)
    monkeypatch.setattr(
        "fi.alk.harness.authoring_runtime_validation.validate_and_repair", validate
    )
    status = authoring_entrypoint.main(
        [str(job_path), "--source", str(source), "--output", str(output), "--await-harness"],
        validate_runtime=True,
    )
    assert status == 78
    assert validated == []


# The unattended and local reading


class _Stage:
    def __init__(self, on_say=None):
        self.said: list[str] = []
        self.on_say = on_say
        self.spent_usd = 0.0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def say(self, message, on_event=None):
        self.said.append(message)
        if self.on_say:
            self.on_say()


def _understand_args(output: Path, **fields) -> SimpleNamespace:
    return SimpleNamespace(
        kind="repo", name="fixture", path=output, out=output, interactive=False, **fields
    )


def _patch_understand(monkeypatch, output: Path, stage) -> None:
    monkeypatch.setattr(
        cli, "resolve", lambda *args, **kwargs: SimpleNamespace(name="fixture", kind="repo")
    )
    monkeypatch.setattr(cli, "open_stage", lambda source, **kwargs: (stage, output))
    monkeypatch.setattr(cli, "opening", lambda _: "")
    monkeypatch.setattr(cli, "_guidance", lambda _: "")


def test_understand_returns_1_with_the_file_and_no_nudge(tmp_path, monkeypatch, capsys):
    stage = _Stage(lambda: write_failure_file(agent_type_failure(["playwright"]), tmp_path))
    _patch_understand(monkeypatch, tmp_path, stage)
    args = _understand_args(tmp_path, job=SimpleNamespace(metadata={}))
    assert asyncio.run(cli._understand(args)) == 78
    assert len(stage.said) == 1, "a refused reading must not be told nothing was saved"
    err = capsys.readouterr().err
    assert _FOUND_MESSAGE in err
    assert str(tmp_path / FAILURE_FILE) in err
    assert "No contract was submitted" not in err


def test_understand_still_nudges_when_nothing_happened(tmp_path, monkeypatch, capsys):
    stage = _Stage()
    _patch_understand(monkeypatch, tmp_path, stage)
    args = _understand_args(tmp_path, job=SimpleNamespace(metadata={}))
    assert asyncio.run(cli._understand(args)) == 1
    assert len(stage.said) == 2
    assert "No contract was submitted" in capsys.readouterr().err


def test_understand_with_no_job_clears_an_old_file_before_reading(tmp_path, monkeypatch):
    write_failure_file(_a_failure(), tmp_path)
    seen = []
    stage = _Stage(lambda: seen.append((tmp_path / FAILURE_FILE).exists()))
    _patch_understand(monkeypatch, tmp_path, stage)
    asyncio.run(cli._understand(_understand_args(tmp_path)))
    assert seen and seen[0] is False


def test_understand_with_a_job_keeps_an_earlier_failure(tmp_path, monkeypatch, capsys):
    write_failure_file(_a_failure(), tmp_path)
    stage = _Stage()
    _patch_understand(monkeypatch, tmp_path, stage)
    args = _understand_args(tmp_path, job=SimpleNamespace(metadata={}))
    assert asyncio.run(cli._understand(args)) == 78
    assert _failure(tmp_path)["code"] == "first"
    assert "the first failure" in capsys.readouterr().err


# A person's own chat


class _Chat:
    def __init__(self, out):
        self.out = out
        self.stage_name = "understand"
        self.spent_usd = 0.0

    async def start(self, on_event=None):
        return None

    async def close(self):
        return None


def _patch_chat(monkeypatch, output: Path) -> None:
    async def nobody_there(_):
        raise EOFError

    monkeypatch.setattr(cli, "open_conversation", lambda **kwargs: _Chat(output))
    monkeypatch.setattr(cli, "_prompt", nobody_there)


def test_chat_returns_1_and_prints_with_the_file(tmp_path, monkeypatch, capsys):
    write_failure_file(agent_type_failure(["playwright"]), tmp_path)
    _patch_chat(monkeypatch, tmp_path)
    args = SimpleNamespace(name="", path="", kind="repo", out=str(tmp_path))
    assert asyncio.run(cli._chat(args)) == 78
    err = capsys.readouterr().err
    assert _FOUND_MESSAGE in err
    assert str(tmp_path / FAILURE_FILE) in err


def test_chat_returns_0_without_the_file(tmp_path, monkeypatch):
    _patch_chat(monkeypatch, tmp_path)
    args = SimpleNamespace(name="", path="", kind="repo", out=str(tmp_path))
    assert asyncio.run(cli._chat(args)) == 0


def test_chat_understand_clears_an_old_file(tmp_path, monkeypatch):
    write_failure_file(_a_failure(), tmp_path)
    seen = []

    class Opened(Exception):
        pass

    def opening_stage(source, **kwargs):
        seen.append((tmp_path / FAILURE_FILE).exists())
        raise Opened

    monkeypatch.setattr(chat.understand_stage, "open_stage", opening_stage)
    conversation = chat.Conversation(
        source=SimpleNamespace(name="fixture"), out=tmp_path, stage_name=chat.UNDERSTAND
    )
    with pytest.raises(Opened):
        asyncio.run(conversation._open(chat.UNDERSTAND))
    assert seen == [False]


# The exit code passes straight up


def test_auto_returns_1_when_understand_does_and_later_stages_do_not_run(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    output = tmp_path / "output"
    later = []

    async def refused(args):
        return 78

    async def stage_after(args):
        later.append("ran")
        return 0

    monkeypatch.setattr(cli, "_understand", refused)
    monkeypatch.setattr(cli, "_build", stage_after)
    monkeypatch.setattr(cli, "_scenarios", stage_after)
    monkeypatch.setattr("fi.alk.harness.provision.stop", lambda _: None)
    args = SimpleNamespace(
        path=str(source),
        out=str(output),
        name="fixture",
        kind="repo",
        count=2,
        model=None,
        authoring_only=True,
        job=HarnessJob(
            job_id="refused",
            run_id="refused",
            execution="local",
            source={"kind": "local_repository", "local_path": str(source)},
            agent={"connector": "auto"},
            scenario_count=2,
            metadata={"generic_harness_v1": True},
        ),
        runtime_readiness=None,
    )
    assert asyncio.run(cli._auto(args)) == 78
    assert later == []


def test_hosted_authoring_main_passes_the_refusal_through(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    secrets.write_text("{}", encoding="utf-8")
    gateway.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(hosted_authoring, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(hosted_authoring, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(hosted_authoring, "_ADC_PATH", tmp_path / "adc.json")
    monkeypatch.setattr(hosted_authoring, "authoring_main", lambda argv, *, validate_runtime: 78)
    assert hosted_authoring.main([]) == 78


# The conversational builder


def _runtime(workspace: Path, *, state=None, control_only=True) -> HostedChatRuntime:
    runtime = object.__new__(HostedChatRuntime)
    runtime.workspace = workspace
    runtime.job = {"scenario_count": 2}
    emitted: list[tuple[str, dict]] = []

    async def emit(kind, **fields):
        emitted.append((kind, fields))

    context = {"capabilities": {"control_only": control_only}}
    if state is not None:
        context["status"] = {"state": state}
    runtime.client = SimpleNamespace(turn_context=context, command_watermark=3, emit=emit)
    runtime.emitted = emitted
    runtime.conversation = SimpleNamespace(stage_name="understand", stage=None)
    runtime._nudge = None
    runtime._from_platform = False
    runtime._last_progress, runtime._idle_turns, runtime._opened_with = None, 0, None
    runtime._what_exists = lambda: (False, False)
    runtime._after_saved = lambda: None
    runtime.handled = []

    async def handle(command):
        runtime.handled.append(command)

    runtime._handle = handle
    return runtime


@pytest.mark.parametrize("state", ["failed", "canceled"])
def test_carry_on_stops_when_the_job_ended(tmp_path, state):
    runtime = _runtime(tmp_path, state=state)
    assert asyncio.run(runtime._carry_on()) is False
    assert runtime.handled == []


def test_carry_on_stops_with_the_file(tmp_path):
    write_failure_file(agent_type_failure(["playwright"]), tmp_path)
    runtime = _runtime(tmp_path, state="running")
    assert asyncio.run(runtime._carry_on()) is False
    assert runtime.handled == []


@pytest.mark.parametrize("state", [None, "running", "completed"])
def test_carry_on_keeps_going_otherwise(tmp_path, state):
    runtime = _runtime(tmp_path, state=state)
    assert asyncio.run(runtime._carry_on()) is True
    assert len(runtime.handled) == 1


class _TurnRan(Exception):
    """Reached the point where a turn starts, which is as far as a test without a model goes."""


def _command(*, from_platform=True) -> dict:
    return {
        "sequence": 7,
        "message_id": "command-7",
        "kind": "message",
        "payload": {"content": "Build it.", "from_platform": from_platform},
    }


def _handle_command(runtime, command) -> None:
    async def checkpoint():
        raise _TurnRan

    runtime._checkpoint = checkpoint
    asyncio.run(runtime._handle_command(command))


def _assert_answered_with_no_turn(runtime) -> None:
    assert [kind for kind, _ in runtime.emitted] == ["turn_completed"]
    fields = runtime.emitted[0][1]
    assert fields["acknowledge_through"] == 7
    assert fields["payload"] == {"command_message_id": "command-7", "ignored": "the build is over"}
    assert runtime._read_journal() == {"command-7": "completed"}


@pytest.mark.parametrize("state", ["failed", "canceled"])
def test_platform_command_on_a_finished_job_runs_no_turn(tmp_path, state):
    runtime = _runtime(tmp_path, state=state)
    _handle_command(runtime, _command())
    _assert_answered_with_no_turn(runtime)


def test_platform_command_with_the_file_runs_no_turn(tmp_path):
    write_failure_file(agent_type_failure(["playwright"]), tmp_path)
    runtime = _runtime(tmp_path, state="running")
    _handle_command(runtime, _command())
    _assert_answered_with_no_turn(runtime)


def test_a_finished_command_replays_on_a_finished_job(tmp_path):
    runtime = _runtime(tmp_path, state="failed")
    runtime._write_journal({"command-7": "completed"})
    _handle_command(runtime, _command())
    assert runtime.emitted[0][1]["payload"] == {"command_message_id": "command-7", "replayed": True}


def test_a_redelivered_command_already_started_runs_no_turn(tmp_path):
    runtime = _runtime(tmp_path, state="failed")
    runtime._write_journal({"command-7": "started"})
    _handle_command(runtime, _command())
    _assert_answered_with_no_turn(runtime)


def test_a_failed_turn_still_fails_again_on_a_finished_job(tmp_path):
    runtime = _runtime(tmp_path, state="failed")
    runtime._write_journal({"command-7": "ended"})
    _handle_command(runtime, _command())
    assert runtime.emitted[0][1]["payload"] == {"command_message_id": "command-7", "outcome": "failed"}


def test_a_persons_message_on_a_failed_job_still_runs_a_turn(tmp_path):
    runtime = _runtime(tmp_path, state="failed")
    with pytest.raises(_TurnRan):
        _handle_command(runtime, _command(from_platform=False))
    assert runtime.emitted == []


def test_post_build_check_still_runs_on_a_completed_job(tmp_path):
    runtime = _runtime(tmp_path, state="completed")
    with pytest.raises(_TurnRan):
        _handle_command(runtime, _command())
    assert runtime.emitted == []


def test_failed_job_outside_control_only_still_runs(tmp_path):
    runtime = _runtime(tmp_path, state="failed", control_only=False)
    with pytest.raises(_TurnRan):
        _handle_command(runtime, _command())
    assert runtime.emitted == []


# Correcting the type mid-build


@pytest.mark.parametrize("why", ["the operator said so", ""])
def test_set_modality_to_browser_changes_nothing(tmp_path, why):
    contract = AgentContract.model_validate(_reading("chat"))
    saved = json.dumps(contract.model_dump())
    path = tmp_path / amend.CONTRACT
    path.write_text(saved, encoding="utf-8")
    done, said = amend.set_modality(contract, tmp_path, modality=" Browser ", why=why)
    assert done is False
    assert said == "browser agents aren't supported, so the type stays chat."
    assert contract.modality == "chat"
    assert contract.amendments == []
    assert path.read_text(encoding="utf-8") == saved
    assert not (tmp_path / FAILURE_FILE).exists()


def test_set_modality_to_voice_still_works(tmp_path):
    contract = AgentContract.model_validate(_reading("chat"))
    done, said = amend.set_modality(contract, tmp_path, modality="voice", why="it takes calls")
    assert done is True
    assert "a call placed to the agent" in said
    assert load(tmp_path).modality == "voice"


# What the model is told


def test_skill_text_asks_the_new_question():
    from fi.alk.harness.config import load_skill

    text = " ".join(load_skill("understand-agent").split())
    assert "does a person come to this agent for a conversation, or to have it carry out that work?" in text
    assert "A coding agent is chat." in text
    assert "a browser it drives" not in text
    assert "through a browser" not in text


def test_schema_description_carries_the_new_question(tmp_path):
    schema = _submit_tool(contract_tools(tmp_path)).input_schema
    description = schema["properties"]["modality"]["description"]
    assert "an agent a person uses to carry out work on websites or a computer is browser" in description
    assert "browser-driving" not in description


def test_set_modality_tool_no_longer_offers_browser():
    from fi.alk.harness.world import tools as world_tools

    source = Path(world_tools.__file__).read_text(encoding="utf-8")
    assert "actually reaches this agent: voice or chat." in source
    assert "voice, chat or browser" not in source


def test_browser_stays_a_known_modality():
    assert "browser" in known_modalities()
    assert "browser" in _submit_tool(contract_tools(Path("unused"))).input_schema["properties"]["modality"]["enum"]
