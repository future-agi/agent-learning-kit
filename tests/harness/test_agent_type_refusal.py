"""An agent whose main job isn't a conversation stops its build with a failure that says so."""

from __future__ import annotations

import asyncio
import errno
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from fi.alk.harness import (
    agent_type_check,
    amend,
    authoring_entrypoint,
    chat,
    cli,
    failure_file,
    tools,
)
from fi.alk.harness import hosted_authoring_entrypoint as hosted_authoring
from fi.alk.harness.agent_type_check import (
    SecondLook,
    browser_tools_in_agent,
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
from fi.alk.harness.source_folder import subfolder_not_found
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
_STOP = "Don't submit again or do more work; tell the person."
_READ = "read-it"
_REAL_RECEIPT = tools._new_receipt


@pytest.fixture(autouse=True)
def _nothing_reaches_a_network(monkeypatch):
    for name in ("FI_BASE_URL", "OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("FI_API_KEY", raising=False)
    monkeypatch.delenv("FI_SECRET_KEY", raising=False)


@pytest.fixture(autouse=True)
def _a_known_receipt(monkeypatch):
    monkeypatch.setattr(tools, "_new_receipt", lambda: _READ)


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


@pytest.mark.parametrize("umask", [0o022, 0o077])
def test_file_is_world_readable(tmp_path, umask):
    before = os.umask(umask)
    try:
        path = write_failure_file(_a_failure(), tmp_path)
    finally:
        os.umask(before)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_writer_leaves_no_temp_file(tmp_path):
    write_failure_file(_a_failure(), tmp_path)
    write_failure_file(_a_failure(), tmp_path)
    assert [path.name for path in tmp_path.iterdir()] == [FAILURE_FILE]


def _no_hard_links(monkeypatch, number=errno.EXDEV):
    def refuse(*_args, **_kwargs):
        raise OSError(number, "no hard links here")

    monkeypatch.setattr(failure_file.os, "link", refuse)


@pytest.mark.parametrize("number", [errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EMLINK])
def test_a_disk_with_no_hard_links_still_gets_the_whole_file(tmp_path, monkeypatch, number):
    _no_hard_links(monkeypatch, number)
    path = write_failure_file(_a_failure(), tmp_path)
    assert HarnessFailure.model_validate_json(path.read_text()) == _a_failure()
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert [item.name for item in tmp_path.iterdir()] == [FAILURE_FILE]


def test_the_file_is_readable_under_a_strict_umask_with_no_hard_links(tmp_path, monkeypatch):
    _no_hard_links(monkeypatch)
    before = os.umask(0o077)
    try:
        path = write_failure_file(_a_failure(), tmp_path)
    finally:
        os.umask(before)
    assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_a_disk_with_no_permissions_still_gets_the_whole_file(tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError(errno.EPERM, "no permissions here")

    monkeypatch.setattr(failure_file.os, "chmod", refuse)
    path = write_failure_file(_a_failure(), tmp_path)
    assert HarnessFailure.model_validate_json(path.read_text()) == _a_failure()
    assert [item.name for item in tmp_path.iterdir()] == [FAILURE_FILE]


def test_first_failure_stands_with_no_hard_links(tmp_path, monkeypatch):
    _no_hard_links(monkeypatch)
    write_failure_file(_a_failure(), tmp_path)
    write_failure_file(agent_type_failure([]), tmp_path)
    assert HarnessFailure.model_validate_json((tmp_path / FAILURE_FILE).read_text()) == _a_failure()


def test_a_write_that_fails_on_a_disk_with_no_hard_links_leaves_nothing(tmp_path, monkeypatch):
    _no_hard_links(monkeypatch)

    def disk_full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "no space")

    monkeypatch.setattr(failure_file.os, "fdopen", disk_full)
    with pytest.raises(OSError):
        write_failure_file(_a_failure(), tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_a_full_disk_leaves_no_temporary_file(tmp_path, monkeypatch):
    real = tempfile.NamedTemporaryFile

    def filling(*args, **kwargs):
        opened = real(*args, **kwargs)

        def disk_full(_text):
            raise OSError(errno.ENOSPC, "no space")

        opened.write = disk_full
        return opened

    monkeypatch.setattr(failure_file.tempfile, "NamedTemporaryFile", filling)
    with pytest.raises(OSError):
        write_failure_file(_a_failure(), tmp_path)
    assert list(tmp_path.iterdir()) == []


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
        ({"agent.py": 'BETA = "computer-use-2025-01-24"\n'}, ["computer-use tool"]),
        ({"agent.py": 'TOOLS = [{"computer_use": {"environment": "browser"}}]\n'}, ["computer-use tool"]),
        ({"agent.py": 'TOOL = dict(computer_use={"environment": "browser"})\n'}, ["computer-use tool"]),
        ({"agent.js": "const u = 'https://x/computer_20241022';\n"}, ["computer-use tool"]),
        ({"agent.ts": "const tools = [{computerUse: {environment}}];\n"}, ["computer-use tool"]),
        ({"agent.js": "const t = 'computer_20241022'; // x\n"}, ["computer-use tool"]),
        ({"agent.jsx": "export const A = () => <p>Don't use computer_20241022</p>\n"}, ["computer-use tool"]),
        ({"agent.py": 'MODEL = "gemini-2.5-computer-use-preview-10-2025"\n'}, ["computer-use tool"]),
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
    "folder",
    [
        "e2e",
        "e2eTests",
        "e2e_tests",
        "testdata",
        "unittests",
        "integration_tests",
        "Test",
        "__tests__",
        "test_utils",
        "test_helpers",
        "tests_integration",
        "tests_ui",
        "tests.old",
        "test_selenium",
        "tests_selenium",
        "test_browser",
        "tests_functional",
        "tests_acceptance",
        "test_scripts",
        "testutils",
        "testing_utils",
        "test_fixtures",
    ],
)
def test_tools_found_skips_these_test_folders(tmp_path, folder):
    (tmp_path / folder).mkdir()
    (tmp_path / folder / "a.py").write_text("import playwright\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == []


@pytest.mark.parametrize(
    "folder, name, line, expected",
    [
        ("web_testing", "agent.py", "from browser_use import Agent\n", ["browser-use"]),
        ("qa-testing", "run.py", "import playwright\n", ["playwright"]),
        ("browser_e2e", "agent.py", "import playwright\n", ["playwright"]),
        ("qa_e2e", "agent.py", "import playwright\n", ["playwright"]),
        ("functional_tests", "a.py", "import playwright\n", ["playwright"]),
        ("ui-tests", "a.py", "import playwright\n", ["playwright"]),
        ("api_tests", "a.py", "import playwright\n", ["playwright"]),
        ("playwright-tests", "a.py", "import playwright\n", ["playwright"]),
        ("web_test", "a.py", "import playwright\n", ["playwright"]),
        ("agent-e2e", "a.py", "import playwright\n", ["playwright"]),
        ("qa-testing/src", "run.py", "import playwright\n", ["playwright"]),
    ],
)
def test_a_folder_ending_in_a_test_word_that_holds_an_agent_is_read(
    tmp_path, folder, name, line, expected
):
    _tree(tmp_path, {f"{folder}/{name}": line, f"{folder}/test_{name}": "import selenium\n"})
    assert browser_tools_found(tmp_path) == expected


@pytest.mark.parametrize(
    "files",
    [
        {"qa_e2e/test_a.py": "x = 1\n", "qa_e2e/agent/__init__.py": "import playwright\n"},
        {"qa_e2e/test_a.py": "x = 1\n", "qa_e2e/ui_tests/agent.py": "import playwright\n"},
        {"b_e2e/test_a.py": "x = 1\n", "b_e2e/c_e2e/agent.py": "import playwright\n"},
    ],
    ids=["init-only-package", "nested-test-word-folder", "two-levels"],
)
def test_an_agent_one_level_inside_a_test_named_folder_is_read(tmp_path, files):
    _tree(tmp_path, files)
    assert browser_tools_found(tmp_path) == ["playwright"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs folder links")
def test_links_inside_a_test_named_folder_are_not_followed_to_decide_it(tmp_path):
    big = tmp_path / "big"
    big.mkdir()
    for n in range(5_000):
        (big / f"f{n}.txt").write_text("", encoding="utf-8")
    _tree(tmp_path, {"t_e2e/test_a.py": "x = 1\n", "agent.py": "import playwright\n"})
    for n in range(500):
        os.symlink(big, tmp_path / "t_e2e" / f"link{n}", target_is_directory=True)
    started = time.monotonic()
    assert browser_tools_found(tmp_path) == ["playwright"]
    # Following each link would list the target once per link.
    assert time.monotonic() - started < 2


def test_a_linked_test_named_folder_is_not_listed_to_decide_it(tmp_path, monkeypatch):
    # The walk doesn't enter a linked folder, so it isn't listed to see whether it holds only tests.
    _tree(tmp_path, {"shared/test_a.py": "x = 1\n", "agent.py": "import playwright\n"})
    os.symlink(tmp_path / "shared", tmp_path / "ui-tests", target_is_directory=True)
    listed: list[str] = []
    real = agent_type_check._only_tests
    monkeypatch.setattr(
        agent_type_check, "_only_tests", lambda folder: listed.append(folder) or real(folder)
    )
    assert browser_tools_found(tmp_path) == ["playwright"]
    assert not any(folder.endswith("ui-tests") for folder in listed)


@pytest.mark.parametrize("folder", ["ui-tests", "functional_tests", "qa_e2e", "web_testing"])
def test_a_folder_ending_in_a_test_word_that_holds_only_tests_is_skipped_whole(tmp_path, folder):
    _tree(
        tmp_path,
        {
            # Read if the folder were walked, so only skipping it whole gives nothing.
            f"{folder}/__init__.py": "import playwright\n",
            f"{folder}/conftest.py": "import playwright\n",
            f"{folder}/test_login.py": "import selenium\n",
            f"{folder}/login.spec.ts": 'const t = "computer-use-preview";\n',
            f"{folder}/e2e/test_flow.py": "import playwright\n",
            f"{folder}/smoke_tests/test_smoke.py": "import playwright\n",
            f"{folder}/data/users.json": "[]\n",
        },
    )
    assert browser_tools_found(tmp_path) == []


@pytest.mark.parametrize(
    "files",
    [
        {"qa_e2e/conftest.py": "", "qa_e2e/agent/main.py": "import playwright\n"},
        {"qa_e2e/test_login.py": "", "qa_e2e/agent/main.py": "import playwright\n"},
        {
            "web_e2e/login.spec.ts": "test('login', () => {});\n",
            "web_e2e/package.json": json.dumps({"devDependencies": {"playwright": "1"}}),
        },
        {"qa_e2e/conftest.py": "", "qa_e2e/__init__.py": "import playwright\n"},
    ],
    ids=[
        "conftest-beside-an-agent-folder",
        "test-beside-an-agent-folder",
        "spec-beside-a-package-json",
        "only-a-conftest",
    ],
)
def test_a_test_named_folder_with_more_than_tests_is_read(tmp_path, files):
    _tree(tmp_path, files)
    assert browser_tools_found(tmp_path) == ["playwright"]


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


@pytest.mark.parametrize(
    "line",
    [
        'const base = url.replace(/\\/*$/, "");',
        'const base = url.replace(/\\/*$/g, "");',
        "const ok = 1;\n/src\\/*/.test(f);",
        "if (/src\\/*/.test(f)) {}",
        "const kept = files.filter((f) => /src\\/*/.test(f));",
        'const ratio = a / b; const label = "1 / 2 /*";',
        'const r = a\n  / b; const p = "src/**/*.ts";',
        'x = a\n/ b; s = "http://h/*"; const t = { type: "computer_20250124" };',
        'x = a\n/ b + "/" + "/*";',
        "const C = () => <code>/src/**/*.ts</code>;",
        "const ok = !/[(`.[+\\-/*%<>=,?^&]/.test(next);\nconst g = `${dir}/*.js`;",
        "const r = /a]`/;\nconst g = `${dir}/*.js`;",
    ],
    ids=[
        "replace",
        "replace-with-a-flag",
        "line-start",
        "condition",
        "arrow",
        "division",
        "division-at-a-line-start",
        "division-at-a-line-start-before-a-url",
        "division-at-a-line-start-before-a-string",
        "jsx-text",
        "a-class-holding-a-bracket-then-a-template",
        "a-bracket-outside-a-class-then-a-template",
    ],
)
def test_a_slash_in_script_code_hides_nothing_after_it(tmp_path, line):
    _tree(tmp_path, {"agent.js": f'{line}\nconst tool = {{ type: "computer_20250124" }};\n'})
    assert browser_tools_found(tmp_path) == ["computer-use tool"]


@pytest.mark.parametrize(
    "body",
    [
        '/* const tool = { type: "computer_20250124" }; */\n',
        'const a = 1; /* { type: "computer_20250124" } */\n',
        'const ratio = a / b; // { type: "computer_20250124" }\n',
        'const r = a\n  / 2; // { type: "computer_20250124" }\n',
        'const r = a\n  / x[0]; // { type: "computer_20250124" }\n',
    ],
    ids=[
        "line-start",
        "after-code",
        "after-division",
        "after-a-division-at-a-line-start",
        "after-a-division-at-a-line-start-by-an-index",
    ],
)
def test_a_script_comment_still_hides_what_it_holds(tmp_path, body):
    _tree(tmp_path, {"agent.js": body})
    assert browser_tools_found(tmp_path) == []


@pytest.mark.parametrize(
    "line",
    [
        "const r = /[/*]/;",
        "const r = " + "(/[" * 20_000,
        "const r = /[" + "[" * 20_000,
        "const r = /[" + "[" * 20_000 + "\\",
        "const r = /[/*",
        "x = /" + "[=/]" * 20_000 + "/b",
        "x = /" + "[=/" * 20_000 + "]/b",
        "x = /" + "[=/]\\\n" * 8_000 + "/b",
    ],
    ids=[
        "slash-in-a-class",
        "unclosed-classes",
        "repeated-brackets",
        "repeated-then-escape",
        "class-open-at-the-line-end",
        "classes-of-slashes",
        "nested-classes-of-slashes",
        "classes-across-escaped-line-ends",
    ],
)
def test_an_odd_regex_literal_stays_quick_and_hides_nothing(tmp_path, line):
    _tree(tmp_path, {"agent.js": f'{line}\nconst tool = {{ type: "computer_20250124" }};\n'})
    started = time.monotonic()
    assert browser_tools_found(tmp_path) == ["computer-use tool"]
    assert time.monotonic() - started < 5


@pytest.mark.parametrize(
    # The second names a marker, so it's tokenized; a NUL byte breaks the tokenizer on 3.12.
    "blob", [b" x = 1\n\x00", b"# computer_use\n x = 1\n\x00"], ids=["plain", "named"]
)
def test_a_file_the_tokenizer_breaks_on_costs_only_that_file(tmp_path, blob):
    # Read first by name, so a scan that stopped at it would miss the agent. 3.11's tokenizer
    # doesn't raise on a NUL byte, so there the file is just read.
    (tmp_path / "a_blob.py").write_bytes(blob)
    (tmp_path / "agent.py").write_text(
        "from playwright.sync_api import sync_playwright\n", encoding="utf-8"
    )
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_file_that_raises_something_unexpected_costs_only_that_file(
    tmp_path, monkeypatch, caplog
):
    _tree(tmp_path, {"a_odd.py": "x = 1\n", "agent.py": "import playwright\n"})
    # A .py is read by read_module here; _markers_in reads scripts.
    real = agent_type_check.read_module

    def odd(path, *args, **kwargs):
        if path.name == "a_odd.py":
            raise LookupError("an error no reader expects")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(agent_type_check, "read_module", odd)
    with caplog.at_level("WARNING", logger=agent_type_check.__name__):
        assert browser_tools_found(tmp_path) == ["playwright"]
    [record] = caplog.records
    assert "a_odd.py" in record.getMessage()
    assert record.exc_info[0] is LookupError


@pytest.mark.skipif(sys.version_info < (3, 12), reason="3.11's tokenizer reads a NUL byte")
def test_a_file_with_a_nul_byte_is_left_unread_without_stopping_the_scan(tmp_path, caplog):
    # Through the shared reader a NUL byte is a file Python can't parse, an expected answer, not
    # an error: it's left unread and nothing is logged. A file naming nothing isn't even read.
    (tmp_path / "a_blob.py").write_bytes(b" x = 1\n\x00")
    (tmp_path / "b_blob.py").write_bytes(b"import playwright\n\x00")
    with caplog.at_level("WARNING", logger=agent_type_check.__name__):
        assert browser_tools_found(tmp_path) == []
    assert caplog.records == []


def test_a_python_file_with_a_coding_line_keeps_its_marker(tmp_path):
    (tmp_path / "agent.py").write_bytes(
        b"# -*- coding: latin-1 -*-\nimport playwright\nNAME = '\xe9'\n"
        b"TOOL = {'type': 'computer_20250124'}\n"
    )
    assert browser_tools_found(tmp_path) == ["computer-use tool", "playwright"]


def _punycode_decodes(monkeypatch) -> list[int]:
    import encodings.punycode

    decoded: list[int] = []
    real = encodings.punycode.punycode_decode
    monkeypatch.setattr(
        encodings.punycode,
        "punycode_decode",
        lambda *args: decoded.append(len(args[0])) or real(*args),
    )
    return decoded


def _parsed_in_a_refused_codec(monkeypatch) -> list[bytes]:
    import ast

    parsed: list[bytes] = []
    real = ast.parse

    def spy(source, *args, **kwargs):
        if isinstance(source, bytes) and b"# coding: " in source[:40]:
            parsed.append(source)
        return real(source, *args, **kwargs)

    monkeypatch.setattr(ast, "parse", spy)
    return parsed


@pytest.mark.parametrize(
    "codec, body, expected",
    [
        (
            "idna",
            b"import playwright\nTOOL = 'computer-use-preview'\n",
            ["computer-use tool", "playwright"],
        ),
        ("idna", b'import playwright\nHOST = "' + b"xn--bcher-kva." * 4_000 + b'"\n', ["playwright"]),
        ("punycode", b"import playwright\n-" + b"9" * 64_000, ["playwright"]),
        ("undefined", b"import playwright\n", ["playwright"]),
        (
            "idna",
            b"from langchain_community.agent_toolkits import PlayWrightBrowserToolkit\n",
            ["PlayWrightBrowserToolkit"],
        ),
        ("idna", b"TEAM = 'steelers'\n", []),
    ],
    ids=[
        "idna-plain",
        "idna-encoded-labels",
        "punycode",
        "undefined",
        "idna-toolkit",
        "idna-word-inside-a-word",
    ],
)
def test_a_file_in_a_refused_codec_still_names_its_tools(
    tmp_path, monkeypatch, codec, body, expected
):
    decoded = _punycode_decodes(monkeypatch)
    parsed = _parsed_in_a_refused_codec(monkeypatch)
    import encodings.idna

    labels = []
    real = encodings.idna.ToUnicode
    monkeypatch.setattr(encodings.idna, "ToUnicode", lambda label: labels.append(label) or real(label))
    (tmp_path / "helper.py").write_bytes(f"# coding: {codec}\n".encode() + body)
    started = time.monotonic()
    # Not decoded, but the browser packages and markers it names, as bytes, still count (Dileep's
    # P2 on #224).
    assert browser_tools_found(tmp_path) == expected
    assert time.monotonic() - started < 5
    # Never decoded: punycode's decode costs the square of its size, and idna's runs per label.
    assert (decoded, labels, parsed) == ([], [], [])


def test_an_escaped_import_in_a_unicode_escape_file_is_found(tmp_path):
    # Python decodes the escapes before it parses, so the import is real and so is the finding.
    (tmp_path / "helper.py").write_bytes(b"# coding: unicode-escape\nimport \\x70laywright\n")
    assert browser_tools_found(tmp_path) == ["playwright"]


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs folder links")
def test_a_test_named_folder_that_is_a_link_is_not_listed_to_decide_it(tmp_path, monkeypatch):
    _tree(tmp_path, {"shared/test_a.py": "x = 1\n", "agent.py": "import playwright\n"})
    os.symlink(tmp_path / "shared", tmp_path / "ui-tests", target_is_directory=True)
    asked = []
    real = agent_type_check._only_tests
    monkeypatch.setattr(agent_type_check, "_only_tests", lambda folder: asked.append(folder) or real(folder))
    assert browser_tools_found(tmp_path) == ["playwright"]
    assert not any(folder.endswith("ui-tests") for folder in asked)


def test_a_slow_codec_named_after_a_lone_cr_is_not_read(tmp_path, monkeypatch):
    decoded = _punycode_decodes(monkeypatch)
    # Python reads the CR as a blank first line, so the coding line is the second.
    (tmp_path / "helper.py").write_bytes(
        b"\r# coding: punycode\rimport playwright\r-" + b"9" * 64_000
    )
    assert browser_tools_found(tmp_path) == ["playwright"]
    assert decoded == []


def test_the_coding_line_is_masked_without_a_python_pass_over_it(tmp_path, monkeypatch):
    from fi.alk.harness import python_source

    # Every file's first two lines are looked at, and one line can be the whole 128 KB: a
    # Python-level pass over it held a crafted save 18.6 s.
    def no_pass(*args, **kwargs):
        raise AssertionError("a Python-level pass over the coding lines")

    # Only this module's regular expressions: the reader compiles its own patterns at import.
    monkeypatch.setattr(python_source, "re", SimpleNamespace(sub=no_pass))
    path = tmp_path / "agent.py"
    path.write_bytes(b"# coding: latin-1 " + b"\xe9" * 100_000 + b"\nimport playwright\n")
    assert python_source.read_module(path) is not None


def test_the_coding_lines_are_found_without_rewriting_the_file():
    from fi.alk.harness.python_source import _coding

    # Reviewer A: two whole-file passes for every .py, 1 ms for 128 KB of CRs. Found by where the
    # lines end, it's a few microseconds; 2,000 such files stay well under a second.
    raw = b"\r" * 131072
    started = time.perf_counter()
    for _ in range(2_000):
        _coding(raw)
    assert time.perf_counter() - started < 0.5


def test_a_file_without_a_cookie_isnt_asked_of_detect_encoding(tmp_path, monkeypatch):
    from fi.alk.harness import python_source

    # Reviewer A: one first line can be the whole 128 KB, and every .py is asked.
    asked: list[int] = []
    real = python_source.tokenize.detect_encoding
    monkeypatch.setattr(
        python_source.tokenize, "detect_encoding", lambda readline: asked.append(1) or real(readline)
    )
    for name, body in [("plain", b"#" + b"x" * 100_000 + b"\nimport os\n"), ("crs", b"\r" * 100_000), ("bom", b"\xef\xbb\xbfx = 1\n")]:
        (tmp_path / f"{name}.py").write_bytes(body)
        assert python_source.read_module(tmp_path / f"{name}.py") is not None
    assert asked == []
    (tmp_path / "coded.py").write_bytes(b"# coding: latin-1\nx = '\xe9'\n")
    assert python_source.read_module(tmp_path / "coded.py") == "# coding: latin-1\nx = '\xe9'\n"
    assert asked == [1]


@pytest.mark.parametrize(
    "raw, head",
    [
        (b"a\nb\nc", b"a\nb"),
        (b"a\r\nb\r\nc", b"a\nb"),
        (b"a\rb\rc", b"a\nb"),
        (b"a\r\rb", b"a\n"),
        (b"only", b"only"),
        (b"a\n", b"a\n"),
    ],
    ids=["lf", "crlf", "cr", "blank-second", "one-line", "one-ended-line"],
)
def test_the_first_two_lines_end_as_python_ends_them(raw, head):
    from fi.alk.harness.python_source import _first_lines

    assert _first_lines(raw) == head


def test_an_import_joined_across_a_crlf_under_unicode_escape_is_found(tmp_path):
    # Python ends lines first, then decodes: the backslash before the line end joins the name.
    (tmp_path / "agent.py").write_bytes(b"# coding: unicode_escape\r\nimport play\\\r\nwright\r\n")
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_unicode_escape_file_python_cant_decode_is_skipped(tmp_path):
    # Python refuses an unknown \N{...} name, so the file never runs.
    (tmp_path / "agent.py").write_bytes(
        b"# coding: unicode_escape\nimport playwright\n# \\N{NOSUCH NAME}\n"
    )
    assert browser_tools_found(tmp_path) == []


def test_the_unicode_escape_total_is_shared_by_the_scans_files(tmp_path, monkeypatch):
    from fi.alk.harness import python_source

    first = b"# coding: unicode_escape\nimport \\x70atchright\n"
    second = b"# coding: unicode_escape\nimport \\x70laywright\n"
    assert len(first) == len(second)
    monkeypatch.setattr(python_source, "LARGEST_ESCAPED_TOTAL", len(first))
    (tmp_path / "a.py").write_bytes(first)
    (tmp_path / "b.py").write_bytes(second)
    # One file's worth: the second is read as bytes, where its escaped name isn't.
    assert len(browser_tools_found(tmp_path)) == 1


def test_past_the_unicode_escape_total_an_escape_before_a_name_is_a_boundary(
    tmp_path, monkeypatch
):
    from fi.alk.harness import python_source

    monkeypatch.setattr(python_source, "LARGEST_ESCAPED_TOTAL", 0)
    for n, escape in enumerate([b"\\t", b"\\x20", b"\\040", b"\\11", b"\\u0020", b"\\U00000020"]):
        (tmp_path / f"agent{n}.py").write_bytes(b"# coding: unicode_escape\nimport" + escape + b"playwright\n")
        assert browser_tools_found(tmp_path) == ["playwright"], escape
        (tmp_path / f"agent{n}.py").unlink()


def test_past_the_scans_unicode_escape_total_a_file_is_searched_as_bytes(tmp_path, monkeypatch):
    from fi.alk.harness import python_source

    monkeypatch.setattr(python_source, "LARGEST_ESCAPED_TOTAL", 0)
    (tmp_path / "escaped.py").write_bytes(b"# coding: unicode_escape\nimport \\x70laywright\n")
    (tmp_path / "plain.py").write_bytes(b"# coding: unicode_escape\nimport selenium\n")
    assert browser_tools_found(tmp_path) == ["selenium"]


@pytest.mark.parametrize(
    "head",
    [b"# coding: latin-1 \xe9\n", b"# -*- coding: cp1252 -*- Soci\xe9t\xe9\n", b"#!/usr/bin/env python \xe9\n# coding: latin-1\n"],
    ids=["after-the-name", "emacs-style", "on-the-first-of-two-lines"],
)
def test_a_non_ascii_byte_on_the_coding_line_leaves_the_file_read(tmp_path, head):
    # detect_encoding refuses such a line, but Python reads the file in the codec it names.
    (tmp_path / "agent.py").write_bytes(head + b"import playwright\nNAME = '\xe9'\n")
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_name_in_a_refused_codec_that_is_the_agents_own_module_does_not_count(tmp_path):
    (tmp_path / "steel.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "agent.py").write_bytes(b"# coding: idna\nimport steel\n")
    assert browser_tools_found(tmp_path) == []


def test_a_name_in_a_refused_codec_counts_only_as_a_whole_name(tmp_path):
    (tmp_path / "agent.py").write_bytes(b"# coding: idna\nTEAM = 'steelers'\n")
    assert browser_tools_found(tmp_path) == []


def test_past_the_raw_search_total_a_name_counts_wherever_it_appears(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_type_check, "_LARGEST_RAW_SEARCH", 0)
    (tmp_path / "agent.py").write_bytes(
        b"# coding: idna\nTEAM = 'steelers'\nTOOL = 'computer-use-preview'\n"
    )
    # Not searched for whole names or markers, so the cheap search's superset stands: a second
    # look at most.
    assert browser_tools_found(tmp_path) == ["steel"]


@pytest.mark.parametrize(
    "source",
    [
        b"# hi\r\r\nTOOL = 'computer-use-preview'\r\r\n",
        b"x = 1 + \\\r2\rTOOL = 'computer-use-preview'\r",
    ],
    ids=["cr-cr-lf", "backslash-before-cr"],
)
def test_markers_after_cr_line_ends_are_read_as_python_reads_them(tmp_path, source):
    (tmp_path / "agent.py").write_bytes(source)
    assert browser_tools_found(tmp_path) == ["computer-use tool"]


def test_a_coding_word_in_a_later_cr_line_is_not_the_cookie(tmp_path):
    # With only CRs, detect_encoding saw one line and took the string's "coding:" as the cookie.
    (tmp_path / "agent.py").write_bytes(
        b"# hi\rx = 'coding: rot13'\rTOOL = 'computer-use-preview'\r"
    )
    # A comment is still not read: the file is tokenized, not searched as bytes.
    (tmp_path / "notes.py").write_bytes(b"# hi\rx = 'coding: rot13'\r# @playwright/mcp\r")
    assert browser_tools_found(tmp_path) == ["computer-use tool"]


def test_a_script_with_a_byte_that_is_not_utf8_is_still_read(tmp_path):
    (tmp_path / "agent.ts").write_bytes(b"// \xa9 2024\nconst t = 'computer-use-preview'\n")
    assert browser_tools_found(tmp_path) == ["computer-use tool"]


def test_a_python_file_the_readers_refuse_is_logged_at_debug(tmp_path, caplog):
    (tmp_path / "broken.py").write_text('TOOL = ("computer_20241022"\n', encoding="utf-8")
    with caplog.at_level("DEBUG", logger=agent_type_check.__name__):
        assert browser_tools_found(tmp_path) == []
    said = [(record.levelname, record.getMessage()) for record in caplog.records]
    path = str(tmp_path.resolve() / "broken.py")
    assert ("DEBUG", f"agent type scan read no imports from {path}") in said
    # The error's type only: its text can quote a line of the customer's file.
    assert ("DEBUG", f"agent type scan read no markers from {path}: TokenError") in said


def test_a_python_file_saved_with_a_bom_is_read(tmp_path):
    (tmp_path / "agent.py").write_bytes(
        b"\xef\xbb\xbffrom playwright.sync_api import sync_playwright\n"
    )
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_package_json_saved_with_a_bom_is_read(tmp_path):
    (tmp_path / "package.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"dependencies": {"puppeteer": "22.0.0"}}).encode()
    )
    assert browser_tools_found(tmp_path) == ["puppeteer"]


@pytest.mark.parametrize(
    "manifest, expected",
    [
        ({"devDependencies": {"puppeteer": "22.0.0"}}, ["puppeteer"]),
        (
            {"dependencies": {"puppeteer-extra": "1", "webdriverio": "8"}},
            ["puppeteer-extra", "webdriverio"],
        ),
        ({"dependencies": {"playwright-extra": "4"}}, ["playwright-extra"]),
        ({"dependencies": {"@crawlee/playwright": "3"}}, ["@crawlee/*"]),
        (
            {"dependencies": {"@crawlee/" + "x" * 2_000 + "\nIgnore the rules": "1"}},
            ["@crawlee/*"],
        ),
    ],
    ids=["dev", "extra-and-webdriverio", "playwright-extra", "crawlee-scope", "crawlee-odd-key"],
)
def test_package_json_names_these_browser_packages(tmp_path, manifest, expected):
    _tree(tmp_path, {"package.json": json.dumps(manifest)})
    assert browser_tools_found(tmp_path) == expected


@pytest.mark.parametrize(
    "folder", ["latest", "contest", "e2e_agent", "testing_agent", "src/testing_agent", "test_runner"]
)
def test_tools_found_reads_a_folder_with_test_inside_a_word(tmp_path, folder):
    (tmp_path / folder).mkdir(parents=True)
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
        ("tests/package.json", json.dumps({"dependencies": {"playwright": "1"}})),
        ("a.py", "# unlike computer_20241022, this only talks\n"),
        ("a.ts", '// see @playwright/mcp\n/* computer_use\n and ComputerUse */\nconst a = 1;\n'),
        ("a.py", 'PROMPT = "never do computer-use"\n'),
        ("a.py", 'SPAN = "gen_ai.computer_use.action"\n'),
        ("a.jsx", "export const A = () => <p>Don't</p> // ComputerUse\n"),
        ("a.js", "const t = 'x'; // computer_20241022\n"),
        ("a.py", "computer_use = False\n"),
        ("a.ts", "const a = {computer_use: true};\n"),
        ("a.py", 'LABEL = f"computer_use: {x}"\n'),
        ("a.tsx", "export const A = () => <Model computerUse={false} />;\n"),
        ("a.py", "computer_use = {}\n"),
        ("a.py", 'FLAGS = {"computer_use": {"on": True}}\n'),
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
            "lib/anthropic-1.0.dist-info/RECORD": "anthropic/types.py,sha256=a,1\n",
            "lib/anthropic/types.py": 'NAME = "computer_20241022"\n',
        },
    )
    assert browser_tools_found(tmp_path) == []


def test_the_agents_code_beside_packages_in_a_subfolder_is_read(tmp_path):
    _tree(
        tmp_path,
        {
            "lambda/handler.py": "from playwright.sync_api import sync_playwright\n",
            "lambda/requests-2.31.0.dist-info/RECORD": "requests/__init__.py,sha256=a,1\n",
            "lambda/requests/__init__.py": 'NAME = "computer_20241022"\n',
        },
    )
    assert browser_tools_found(tmp_path) == ["playwright"]


def _unpacked_at_the_top(root: Path) -> None:
    _tree(
        root,
        {
            "openai-1.0.dist-info/RECORD": (
                "openai/__init__.py,sha256=a,0\nopenai/x.py,sha256=a,1\nsix.py,sha256=b,2\n"
            ),
            "openai/__init__.py": "",
            "openai/x.py": 'NAME = "computer_use_preview"\n',
            "six.py": 'NAME = "computer_20241022"\n',
        },
    )


def test_packages_unpacked_beside_the_agent_are_not_the_agent(tmp_path):
    _unpacked_at_the_top(tmp_path)
    _tree(tmp_path, {"handler.py": "import openai\n"})
    assert browser_tools_found(tmp_path) == []


def test_the_agents_own_folder_is_read_beside_unpacked_packages(tmp_path):
    _unpacked_at_the_top(tmp_path)
    _tree(tmp_path, {"app/run.py": "import playwright\n"})
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_distribution_shipping_a_folder_name_does_not_hide_the_customers_folder(tmp_path):
    _tree(
        tmp_path,
        {
            "cron_descriptor-2.0.dist-info/RECORD": "tools/compilepos.py,sha256=a,1\n",
            "tools/compilepos.py": 'NAME = "computer_20241022"\n',
            "tools/browser.py": "import playwright\n",
        },
    )
    assert browser_tools_found(tmp_path) == ["playwright"]


def _vendored_marker_with_record(root: Path, record: bytes) -> None:
    _tree(root, {"vendored.py": 'NAME = "computer_20241022"\n', "x-1.dist-info/METADATA": ""})
    (root / "x-1.dist-info/RECORD").write_bytes(record)


def test_a_record_past_its_limit_is_not_believed(tmp_path, monkeypatch):
    _vendored_marker_with_record(tmp_path, b"vendored.py,sha256=a,1\n")
    monkeypatch.setattr(agent_type_check, "_LARGEST_RECORD", 5)
    assert browser_tools_found(tmp_path) == ["computer-use tool"]


@pytest.mark.parametrize("record", [b"\xff\xfe\x00binary", b"a\x00b,c\n", b"x" * 200_000 + b",y\n"])
def test_a_record_that_cannot_be_read_costs_only_itself(tmp_path, record):
    _vendored_marker_with_record(tmp_path, record)
    assert browser_tools_found(tmp_path) == ["computer-use tool"]


def test_a_blank_record_row_is_harmless(tmp_path):
    _vendored_marker_with_record(tmp_path, b"\nvendored.py,sha256=a,1\n\n")
    assert browser_tools_found(tmp_path) == []


def test_records_past_their_total_limit_are_not_believed(tmp_path, monkeypatch):
    _tree(
        tmp_path,
        {
            "a-1.dist-info/RECORD": "a.py,sha256=a,1\n",
            "b-1.dist-info/RECORD": "b.py,sha256=a,1\n",
            "a.py": "x = 1\n",
            "b.py": 'NAME = "computer_20241022"\n',
        },
    )
    assert browser_tools_found(tmp_path) == []
    monkeypatch.setattr(agent_type_check, "_LARGEST_RECORDS", agent_type_check._SMALLEST_RECORD)
    agent_type_check._scan.cache_clear()
    assert browser_tools_found(tmp_path) == ["computer-use tool"]


def _record_text_read(monkeypatch) -> list[int]:
    read: list[int] = []
    real = Path.read_text

    def counted(self, *args, **kwargs):
        text = real(self, *args, **kwargs)
        if self.name == "RECORD":
            read.append(len(text))
        return text

    monkeypatch.setattr(Path, "read_text", counted)
    return read


def _records_read(monkeypatch) -> Counter[str]:
    read: Counter[str] = Counter()
    real = Path.read_text

    def counted(self, *args, **kwargs):
        if self.name == "RECORD":
            read[self.parent.name] += 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    return read


def test_each_record_is_read_once_a_scan_and_not_on_a_reopen(tmp_path, monkeypatch):
    _unpacked_at_the_top(tmp_path)
    _tree(
        tmp_path,
        {
            "handler.py": "import openai\n",
            "lambda/run.py": "import playwright\n",
            "lambda/requests-2.31.0.dist-info/RECORD": "requests/__init__.py,sha256=a,1\n",
            "lambda/requests/__init__.py": 'NAME = "computer_20241022"\n',
        },
    )
    read = _records_read(monkeypatch)
    assert browser_tools_found(tmp_path) == ["playwright"]
    # Every reopen asks again, so it must read none; a scan reads each once, however many need it.
    assert read == {"openai-1.0.dist-info": 1, "requests-2.31.0.dist-info": 1}
    read.clear()
    assert browser_tools_found(tmp_path) == ["playwright"]
    assert read == {}


def test_empty_records_still_use_up_the_budget(tmp_path, monkeypatch):
    _tree(tmp_path, {f"e{n}-1.dist-info/RECORD": "" for n in range(10)})
    _tree(tmp_path, {"agent.py": "import openai\n"})
    monkeypatch.setattr(agent_type_check, "_LARGEST_RECORDS", 3 * agent_type_check._SMALLEST_RECORD)
    read = _records_read(monkeypatch)
    assert browser_tools_found(tmp_path) == []
    assert sum(read.values()) == 3


def test_a_browser_package_at_the_top_still_counts_once_the_budget_is_spent(
    tmp_path, monkeypatch
):
    # The empty record sorts first and spends the budget, so the one naming the package is unread.
    _tree(
        tmp_path,
        {
            "a-1.dist-info/RECORD": "",
            "playwright-1.0.dist-info/RECORD": "playwright/__init__.py,sha256=a,1\n",
            "playwright/__init__.py": "",
            "handler.py": "import playwright\n",
        },
    )
    monkeypatch.setattr(agent_type_check, "_LARGEST_RECORDS", agent_type_check._SMALLEST_RECORD)
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_record_that_starts_naming_a_file_is_scanned_again(tmp_path):
    _tree(
        tmp_path,
        {"x-1.dist-info/RECORD": "other.py,,\n", "vendored.py": 'NAME = "computer_20241022"\n'},
    )
    assert browser_tools_found(tmp_path) == ["computer-use tool"]
    (tmp_path / "x-1.dist-info/RECORD").write_text("vendored.py,,\n", encoding="utf-8")
    assert browser_tools_found(tmp_path) == []


def test_every_folder_shares_one_record_budget(tmp_path, monkeypatch):
    record = "vendored.py,sha256=a,1\n"
    for folder in ("a", "b", "c"):
        _tree(
            tmp_path,
            {
                f"{folder}/x-1.dist-info/RECORD": record,
                f"{folder}/vendored.py": 'NAME = "computer_20241022"\n',
            },
        )
    # Packages unpacked in more than one folder are all skipped while the budget lasts.
    assert browser_tools_found(tmp_path) == []
    monkeypatch.setattr(agent_type_check, "_LARGEST_RECORDS", 2 * agent_type_check._SMALLEST_RECORD)
    read = _record_text_read(monkeypatch)
    files = list(agent_type_check._agent_files(tmp_path))
    assert sum(read) <= 2 * len(record)
    assert [path.name for path in files].count("vendored.py") == 1


def test_a_deep_folders_records_cost_what_their_rows_do(tmp_path, monkeypatch):
    import tracemalloc

    count = 4_000
    rows = "".join(f"r{n:04}\n" for n in range(count))
    shallow = tmp_path / "shallow" / "d"
    deep = tmp_path.joinpath("deep", *["d" * 100] * 5)
    for folder in (shallow, deep):
        _tree(folder, {"x-1.dist-info/RECORD": rows, "main.py": "import openai\n"})
    monkeypatch.setattr(agent_type_check, "_LARGEST_RECORDS", len(rows))

    def peak(top):
        tracemalloc.start()
        try:
            assert "main.py" in [path.name for path in agent_type_check._agent_files(top)]
            return tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()

    # A row as written is a few bytes, and the folder it sits in can be as deep as the disk allows:
    # kept with its folder's path, each row here would cost 500 more characters.
    assert peak(tmp_path / "deep") < peak(tmp_path / "shallow") + 500 * count // 4


def test_a_file_is_skipped_by_the_records_of_the_nearest_folders_that_hold_them(tmp_path):
    chain = [f"c{n}" for n in range(6)]
    bottom = "/".join(chain)
    tree = {f"{bottom}/v{n}.py": 'NAME = "computer_20241022"\n' for n in range(6)}
    for n in range(6):
        folder = "/".join(chain[: n + 1])
        below = "/".join(chain[n + 1 :])
        tree[f"{folder}/x-1.dist-info/RECORD"] = f"{below + '/' if below else ''}v{n}.py,,\n"
    _tree(tmp_path, tree)
    read = sorted(
        path.name
        for path in agent_type_check._agent_files(tmp_path)
        if path.name.startswith("v")
    )
    # Each of the nearest four names its own file, so all four are skipped; the two above them
    # are too far up to be asked, so their files are read.
    assert read == ["v0.py", "v1.py"]


def test_which_folders_packages_count_once_the_budget_is_spent_is_the_same_on_every_disk(
    tmp_path, monkeypatch
):
    record = "vendored.py,sha256=a,1\n"
    for folder in ("a", "b"):
        _tree(
            tmp_path,
            {
                f"{folder}/x-1.dist-info/RECORD": record,
                f"{folder}/vendored.py": 'NAME = "computer_20241022"\n',
            },
        )
    real_walk = os.walk

    def listed_backwards(top, *args, **kwargs):
        for directory, subdirectories, files in real_walk(top, *args, **kwargs):
            if directory == os.fspath(top):
                subdirectories.sort(reverse=True)
            yield directory, subdirectories, files

    monkeypatch.setattr(os, "walk", listed_backwards)
    monkeypatch.setattr(agent_type_check, "_LARGEST_RECORDS", agent_type_check._SMALLEST_RECORD)
    files = list(agent_type_check._agent_files(tmp_path))
    assert [path.parent.name for path in files if path.name == "vendored.py"] == ["b"]


def test_linked_records_are_never_read(tmp_path, monkeypatch):
    _tree(
        tmp_path,
        {
            "agent.py": "import openai\n",
            ".shared/x-1.dist-info/RECORD": "vendored.py,sha256=a,1\n",
            "y-1.dist-info/METADATA": "",
        },
    )
    (tmp_path / "y-1.dist-info/RECORD").symlink_to(tmp_path / ".shared/x-1.dist-info/RECORD")
    for folder in range(4):
        _tree(tmp_path, {f"pkg{folder}/vendored.py": 'NAME = "computer_20241022"\n'})
        for link in range(8):
            (tmp_path / f"pkg{folder}/p{link}-1.dist-info").symlink_to(
                tmp_path / ".shared/x-1.dist-info"
            )
    read = _record_text_read(monkeypatch)
    # pip writes neither as a link, so a linked one says nothing about the files beside it.
    assert browser_tools_found(tmp_path) == ["computer-use tool"]
    assert read == []


def test_a_record_linked_to_a_device_is_not_read(tmp_path, monkeypatch):
    _tree(tmp_path, {"x-1.dist-info/METADATA": "", "a.py": "import playwright\n"})
    (tmp_path / "x-1.dist-info/RECORD").symlink_to("/dev/zero")
    real = Path.read_text

    def careful(self, *args, **kwargs):
        assert self.name != "RECORD", "read a device to the end of memory"
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", careful)
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_a_record_that_is_a_named_pipe_does_not_hang_the_scan(tmp_path):
    _tree(tmp_path, {"x-1.dist-info/METADATA": "", "a.py": "import playwright\n"})
    os.mkfifo(tmp_path / "x-1.dist-info/RECORD")
    answers = []
    scan = threading.Thread(
        target=lambda: answers.append(browser_tools_found(tmp_path)), daemon=True
    )
    scan.start()
    scan.join(10)
    assert answers == [["playwright"]]


def test_a_browser_package_unpacked_at_the_top_still_counts_when_imported(tmp_path):
    _tree(
        tmp_path,
        {
            "playwright-1.0.dist-info/RECORD": "playwright/__init__.py,sha256=a,1\n",
            "playwright/__init__.py": "",
            "handler.py": "import playwright\n",
        },
    )
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_the_scan_stops_at_its_limit_with_what_it_found(tmp_path, monkeypatch):
    _tree(tmp_path, {f"m{n}.py": f"import {name}\n" for n, name in enumerate(["playwright", "selenium", "scrapy"])})
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCAN", 20)
    assert len(browser_tools_found(tmp_path)) == 1


def test_files_too_big_to_read_do_not_use_up_the_limit(tmp_path, monkeypatch):
    # Read first by name, so it's the one that would use up the limit.
    _tree(tmp_path, {"a_big.py": "x" * 80, "agent/run.py": "import playwright\n"})
    monkeypatch.setattr(agent_type_check, "_LARGEST_CODE_FILE", 50)
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCAN", 60)
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_python_is_read_before_script_bundles_use_up_the_limit(tmp_path, monkeypatch):
    _tree(tmp_path, {"a.js": "x" * 40, "b.js": "x" * 40, "z/run.py": "import playwright\n"})
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCAN", 60)
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_package_json_is_read_before_script_bundles(tmp_path, monkeypatch):
    _tree(
        tmp_path,
        {"a.js": "x" * 40, "package.json": json.dumps({"dependencies": {"playwright": "1"}})},
    )
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCAN", 60)
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_files_are_read_by_path_whatever_order_the_disk_lists_them(tmp_path, monkeypatch):
    _tree(tmp_path, {"a.py": "import selenium\n", "b.py": "import playwright\n"})
    listed = [tmp_path.resolve() / "b.py", tmp_path.resolve() / "a.py"]
    monkeypatch.setattr(agent_type_check, "_agent_files", lambda _root, *_: iter(listed))
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCAN", 20)
    assert browser_tools_found(tmp_path) == ["selenium"]


def test_a_second_scan_of_unchanged_files_reuses_the_first(tmp_path, monkeypatch):
    _tree(tmp_path, {"a.py": "import playwright\n"})
    assert browser_tools_found(tmp_path) == ["playwright"]

    def read_again(*_args):
        pytest.fail("scanned again")

    monkeypatch.setattr(agent_type_check, "read_module", read_again)
    assert browser_tools_found(tmp_path) == ["playwright"]


@pytest.mark.parametrize("edited", ["src/a.py", "src/b.py"])
def test_a_nested_edit_is_scanned_again(tmp_path, edited):
    # The same size, so only the time says it changed; either file, whichever the walk ends on.
    _tree(tmp_path, {"src/a.py": "import openai\n", "src/b.py": "import openai\n"})
    assert browser_tools_found(tmp_path) == []
    (tmp_path / edited).write_text("import scrapy\n", encoding="utf-8")
    os.utime(tmp_path / edited, ns=(2**62, 2**62))
    assert browser_tools_found(tmp_path) == ["scrapy"]


@pytest.mark.parametrize(
    "name, before, after",
    [
        ("agent.ts", "const a = 1;\n", 'const t = "computer_20241022";\n'),
        (
            "package.json",
            json.dumps({"dependencies": {"zod": "1"}}),
            json.dumps({"dependencies": {"playwright": "1"}}),
        ),
    ],
)
def test_an_edit_outside_python_is_scanned_again(tmp_path, name, before, after):
    _tree(tmp_path, {name: before})
    assert browser_tools_found(tmp_path) == []
    (tmp_path / name).write_text(after, encoding="utf-8")
    assert browser_tools_found(tmp_path) != []


def test_one_file_fewer_is_scanned_again(tmp_path):
    # The same total size and latest time, so only the count says it changed.
    _tree(tmp_path, {"a.py": "import openai\n", "b.py": "import scrapy\n"})
    for name in ("a.py", "b.py"):
        os.utime(tmp_path / name, ns=(1, 1))
    assert browser_tools_found(tmp_path) == ["scrapy"]
    (tmp_path / "b.py").unlink()
    (tmp_path / "a.py").write_text("import openai\n" * 2, encoding="utf-8")
    os.utime(tmp_path / "a.py", ns=(1, 1))
    assert browser_tools_found(tmp_path) == []


def test_removing_a_nested_helper_is_scanned_again(tmp_path):
    _tree(tmp_path, {"src/agent.py": "import openai\n", "src/helper.py": "import playwright\n"})
    assert browser_tools_found(tmp_path) == ["playwright"]
    (tmp_path / "src/helper.py").unlink()
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
        ("browser", ["playwright"], False, "second_look"),
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
    assert decide(claimed, ["playwright"], looked_again=False) == "second_look"
    assert decide(claimed, [], looked_again=False) == "second_look"
    assert decide(claimed, ["playwright"], looked_again=True) == "refuse"


# The second look on its own


def test_a_disagreement_gets_one_look_and_the_next_answer_stands():
    look = SecondLook()
    assert not look.given
    assert look.verdict("chat", None, ["playwright"], given_before=False) == "second_look"
    text = look.issue("chat", ["playwright"], "abcd1234")
    assert look.given
    assert "the source uses playwright" in text
    assert text.endswith('set second_look_read to "abcd1234", which shows you read this.')
    assert look.verdict("chat", None, ["playwright"], given_before=True) == "keep"


def test_a_browser_reading_always_gets_a_look_before_a_refusal():
    look = SecondLook()
    assert look.verdict("browser", None, ["playwright"], given_before=False) == "second_look"
    assert "the source uses playwright" in look.issue("browser", ["playwright"], "abcd1234")
    assert look.verdict("browser", "abcd1234", ["playwright"], given_before=True) == "refuse"


def test_a_browser_reading_with_nothing_found_says_so_in_its_look():
    look = SecondLook()
    assert look.verdict("browser", None, [], given_before=False) == "second_look"
    assert "no browser, crawling or computer-use tool was found" in look.issue(
        "browser", [], "abcd1234"
    )
    assert look.verdict("browser", None, [], given_before=True) == "refuse"


def test_a_call_that_started_before_the_look_gets_one_of_its_own():
    look = SecondLook()
    look.verdict("browser", None, [], given_before=False)
    look.issue("browser", [], "abcd1234")
    assert look.verdict("browser", None, [], given_before=False) == "second_look"


@pytest.mark.parametrize("value", ["guess", "", 7, None])
def test_a_value_the_look_never_gave_reads_nothing(value):
    look = SecondLook()
    assert look.verdict("chat", value, ["playwright"], given_before=False) == "second_look"


def test_once_a_value_came_back_a_different_answer_without_one_gets_a_look():
    look = SecondLook()
    look.verdict("browser", None, [], given_before=False)
    look.issue("browser", [], "abcd1234")
    assert look.verdict("chat", "abcd1234", [], given_before=True) == "keep"
    assert look.verdict("chat", None, [], given_before=True) == "keep"
    assert look.verdict("browser", None, [], given_before=True) == "second_look"


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


@pytest.mark.parametrize("modality", ["chat", "browser"])
def test_a_refusal_during_the_scan_stops_this_call_too(tmp_path, monkeypatch, modality):
    output = tmp_path / "out"

    def scan(_root):
        write_failure_file(agent_type_failure(["playwright"]), output)
        return []

    monkeypatch.setattr(tools, "browser_tools_found", scan)
    answer = _submitter(output, tmp_path)(_reading(modality))
    assert not (output / "contract.json").exists()
    # The stop itself, not a second look: the reading was refused while the scan ran. A browser
    # reading would otherwise be asked for a second look before the write's own check.
    assert _text(answer) == tools._stop_message(output)


def test_a_build_that_times_out_is_still_a_crash(tmp_path, monkeypatch):
    monkeypatch.setenv("ALK_HOSTED_AUTHORING_MAX_DURATION_SECONDS", "0")
    # `or 3300` keeps a zero from meaning "no time", so step the clock past the deadline instead.
    clock = iter([0.0, 10_000.0])
    monkeypatch.setattr(authoring_entrypoint.time, "monotonic", lambda: next(clock))
    assert authoring_entrypoint._await_harness(tmp_path) == 1


def test_ci_runs_these_tests():
    workflows = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    text = workflows.read_text(encoding="utf-8")
    assert "tests/harness/test_agent_type_refusal.py" in text
    assert "tests/harness/test_executor_completed_counts.py" in text


def test_the_file_is_what_the_platform_reads():
    assert FAILURE_FILE == "failure.json"


@pytest.mark.parametrize("modality", ["browser", ["browser"]])
def test_a_wrapped_browser_reading_is_refused(tmp_path, browser_repo, modality):
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    assert "second-look" in _text(submit({"contract": _reading(modality)}))
    submit({"contract": _reading(modality)})
    assert _failure(output)["code"] == "agent_type_unsupported"
    assert not (output / "contract.json").exists()


def _folder_repo(root: Path) -> Path:
    return _tree(
        root / "repo",
        {
            "agents/a/agent.py": "x = 1\n",
            "agents/b/agent.py": "from playwright.sync_api import sync_playwright\n",
        },
    )


def _submitter_in(destination: Path, repo: Path, subfolder: str):
    tool = _submit_tool(contract_tools(destination, scan_root=repo, subfolder=subfolder))
    return lambda payload: asyncio.run(tool.handler(payload))


def test_scan_ignores_sibling_folder(tmp_path):
    output = tmp_path / "out"
    answer = _submitter_in(output, _folder_repo(tmp_path), "agents/a")(_reading("chat"))
    assert not answer.get("is_error"), answer
    assert (output / "contract.json").exists()
    assert not (output / FAILURE_FILE).exists()


def test_scan_finds_the_agents_own_folder(tmp_path):
    output = tmp_path / "out"
    submit = _submitter_in(output, _folder_repo(tmp_path), "agents/b")
    # A browser reading gets one look first, naming what the folder uses; the answer then stands.
    assert "the source uses playwright" in submit(_reading("browser"))["content"][0]["text"]
    submit(_reading("browser"))
    assert _failure(output)["code"] == "agent_type_unsupported"
    assert "playwright" in _failure(output)["message"]


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            {
                "common/web.py": "from playwright.sync_api import sync_playwright\n",
                "agents/a/agent.py": "from common.web import open_page\n",
            },
            ["playwright"],
        ),
        (
            {
                "lib/browse.js": 'const TOOL = "computer_20241022";\n',
                "agents/a/index.js": 'const browse = require("../../lib/browse");\n',
            },
            ["computer-use tool"],
        ),
        (
            {
                "lib/browse.js": 'const puppeteer = require("puppeteer");\n',
                "lib/package.json": json.dumps({"dependencies": {"puppeteer": "1"}}),
                "agents/a/index.js": 'const browse = require("../../lib/browse");\n',
            },
            ["puppeteer"],
        ),
        (
            {
                "packages/shared/package.json": json.dumps({"dependencies": {"puppeteer": "1"}}),
                "packages/shared/src/browse.ts": "export const x = 1;\n",
                "agents/a/index.ts": 'import "../../packages/shared/src/browse";\n',
            },
            ["puppeteer"],
        ),
        (
            {
                "examples/common/web.py": "import playwright\n",
                "agents/a/agent.py": "import examples.common.web\n",
            },
            ["playwright"],
        ),
        (
            {
                "common/__init__.py": "from . import web\n",
                "common/web.py": "import playwright\n",
                "common/util.py": "x = 1\n",
                "agents/a/agent.py": "from common.util import x\n",
            },
            ["playwright"],
        ),
        # Someone else's package, unpacked beside the code, may name a tool it doesn't use here.
        (
            {
                "somepkg/__init__.py": 'TOOL = {"type": "computer_20241022"}\n',
                "somepkg-1.0.dist-info/RECORD": "somepkg/__init__.py,,\n",
                "agents/a/agent.py": "import somepkg\n",
            },
            [],
        ),
        (
            {
                "agents/a/ui.js": '// const browse = require("../b/browse");\n',
                "agents/b/browse.js": 'const TOOL = "computer_20241022";\n',
            },
            [],
        ),
        (
            {
                "agents/a/agent.py": "import somepkg\n",
                "agents/a/somepkg/__init__.py": 'TOOL = {"type": "computer_20241022"}\n',
                "agents/a/somepkg-1.0.dist-info/RECORD": "somepkg/__init__.py,,\n",
            },
            [],
        ),
        (
            {
                "agents/a/agent.py": "import somepkg\n",
                "agents/somepkg/__init__.py": 'TOOL = {"type": "computer_20241022"}\n',
                "agents/somepkg-1.0.dist-info/RECORD": "somepkg/__init__.py,,\n",
            },
            [],
        ),
        # A RECORD line naming no folder doesn't hide all the shared code.
        (
            {
                "agents/a/agent.py": "import common.web\n",
                "common/web.py": "import playwright\n",
                "odd-1.0.dist-info/RECORD": ",,\n./x,,\n./,,\n/,,\n.//,,\n./.,,\n",
            },
            ["playwright"],
        ),
        ({"agents/a/x.py": "x = 1\n", "agent.py": "import playwright\n"}, ["playwright"]),
        (
            {
                "agents/a/x.py": "x = 1\n",
                "package.json": json.dumps({"dependencies": {"puppeteer": "1"}}),
            },
            ["puppeteer"],
        ),
        ({"agents/a/agent.py": "import playwright\n"}, ["playwright"]),
        # Nothing the agent imports: a sibling's tool, and a shared test helper, aren't its own.
        (
            {
                "agents/a/agent.py": "x = 1\n",
                "agents/b/agent.py": "import playwright\n",
                "common/web.py": "import playwright\n",
            },
            [],
        ),
        (
            {
                "agents/a/agent.py": "import tests.helper\n",
                "tests/helper.py": "import playwright\n",
            },
            [],
        ),
    ],
)
def test_scan_covers_what_the_agent_reaches(tmp_path, files, expected):
    assert browser_tools_in_agent(_tree(tmp_path / "repo", files), "agents/a") == expected


def test_the_walk_reads_each_of_the_folders_files_once(tmp_path, monkeypatch):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/agent.py": "import helper\nimport common.web\n",
            "agents/a/helper.py": "import agent\n",
            "common/web.py": "import helper\n",
        },
    )
    reads: list[Path] = []
    opened = Path.open

    def counted(path, *args, **kwargs):
        reads.append(path.resolve())
        return opened(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted)

    reached = imported_by_agent(repo, "agents/a")

    assert (repo / "common" / "web.py").resolve() in reached
    assert len(reads) == len(set(reads)) == 3


def test_a_chat_reading_of_an_agent_with_a_shared_browser_tool_gets_a_second_look(tmp_path):
    repo = _tree(
        tmp_path / "repo",
        {
            "common/web.py": "from playwright.sync_api import sync_playwright\n",
            "agents/a/agent.py": "from common.web import open_page\n",
        },
    )
    output = tmp_path / "out"

    _submitter_in(output, repo, "agents/a")(_reading("chat"))

    assert not (output / "contract.json").exists()


@pytest.mark.parametrize(
    "files",
    [
        {"agents/a/routes.ts": 'const note = from "/";\n'},
        {"agents/a/deep.py": "from " + "." * 40 + " import x\n"},
    ],
    ids=["script", "python"],
)
def test_an_odd_import_does_not_stop_the_save(tmp_path, files):
    output = tmp_path / "out"

    answer = _submitter_in(output, _tree(tmp_path / "repo", files), "agents/a")(_reading("chat"))

    assert not answer.get("is_error"), answer
    assert (output / "contract.json").exists()


def test_the_package_walk_stops_at_the_repo_root(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"dependencies": {"puppeteer": "1"}}), encoding="utf-8"
    )
    repo = _tree(
        tmp_path / "repo",
        {
            "lib/browse.js": "export const x = 1;\n",
            "agents/a/index.js": 'import "../../lib/browse";\n',
        },
    )

    assert browser_tools_in_agent(repo, "agents/a") == []


def test_scan_stops_at_the_repo_root(tmp_path):
    (tmp_path / "agent.py").write_text("import playwright\n", encoding="utf-8")
    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "x = 1\n"})

    assert browser_tools_in_agent(repo, "agents/a") == []


def test_scan_answers_when_a_linked_folder_leads_out_of_the_repo(tmp_path):
    outside = _tree(tmp_path / "outside", {"web.py": "import playwright\n"})
    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "import linked.web\n"})
    (repo / "linked").symlink_to(outside, target_is_directory=True)

    assert browser_tools_in_agent(repo, "agents/a") == []


def test_scan_of_a_missing_folder_finds_nothing(tmp_path):
    output = tmp_path / "out"
    answer = _submitter_in(output, _folder_repo(tmp_path), "agents/missing")(_reading("chat"))
    assert not answer.get("is_error"), answer
    assert not (output / FAILURE_FILE).exists()


def test_free_text_mentioning_browser_gets_a_problem_not_a_block(tmp_path, browser_repo):
    output = tmp_path / "out"
    answer = _submitter(output, browser_repo)({"contract": _reading("chat via browser")})
    assert answer.get("is_error")
    assert "is not a modality" in _text(answer)
    assert not (output / FAILURE_FILE).exists()


def test_browser_with_tool_gets_a_second_look_naming_it_then_is_refused(tmp_path, browser_repo):
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    look = submit(_reading("browser"))
    assert look.get("is_error")
    assert "second-look: you recorded modality browser" in _text(look)
    assert "the source uses playwright" in _text(look)
    assert not (output / FAILURE_FILE).exists()
    answer = submit(_reading("browser"))
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


def _in_one_model_message(tool, modality: str, *, together: bool):
    """How the two loops run parallel calls: Gemini's together, Claude Code's one after another."""

    async def calls():
        if together:
            return await asyncio.gather(*(tool.handler(_reading(modality)) for _ in range(2)))
        return [await tool.handler(_reading(modality)) for _ in range(2)]

    return asyncio.run(calls())


# Calls waiting on the first scan together are the only siblings the handler can tell apart from a
# later message's call; one after another, the second is taken as the model's answer.
def test_two_browser_readings_waiting_on_the_scan_both_get_the_second_look(tmp_path, plain_repo):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=plain_repo))
    for answer in _in_one_model_message(tool, "browser", together=True):
        assert answer.get("is_error")
        assert "second-look" in _text(answer)
    assert not (output / FAILURE_FILE).exists()
    assert _STOP in _text(asyncio.run(tool.handler(_reading("browser", second_look_read=_READ))))
    assert (output / FAILURE_FILE).exists()


def test_two_chat_readings_of_a_browser_repo_waiting_on_the_scan_save_nothing(
    tmp_path, browser_repo
):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=browser_repo))
    for answer in _in_one_model_message(tool, "chat", together=True):
        assert answer.get("is_error")
        assert "second-look" in _text(answer)
    assert not (output / "contract.json").exists()


def _looks(answers) -> list[bool]:
    return ["second-look" in _text(answer) for answer in answers]


def test_a_chat_answer_without_the_value_stands_after_one_look(tmp_path, browser_repo):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=browser_repo))
    answers = [asyncio.run(tool.handler(_reading("chat"))) for _ in range(5)]
    assert _looks(answers) == [True, False, False, False, False]
    assert not answers[1].get("is_error")
    assert load(output).modality == "chat"
    assert not (output / FAILURE_FILE).exists()


def test_a_browser_answer_without_the_value_stands_after_one_look(tmp_path, plain_repo):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=plain_repo))
    answers = [asyncio.run(tool.handler(_reading("browser"))) for _ in range(5)]
    assert _looks(answers) == [True, False, False, False, False]
    assert _STOP in _text(answers[1])
    assert _failure(output)["code"] == "agent_type_unsupported"
    assert not (output / "contract.json").exists()


@pytest.mark.parametrize(
    "repo, modality, written",
    [("plain_repo", "browser", FAILURE_FILE), ("browser_repo", "chat", "contract.json")],
)
def test_a_wrong_value_every_time_still_ends_after_one_look(
    tmp_path, request, repo, modality, written
):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=request.getfixturevalue(repo)))
    answers = [
        asyncio.run(tool.handler(_reading(modality, second_look_read="yes"))) for _ in range(3)
    ]
    assert _looks(answers) == [True, False, False]
    assert (output / written).exists()


def _next_message(tool, *payloads):
    """One model message's calls once the scan is done, when together runs as one after another."""

    async def calls():
        return [await tool.handler(payload) for payload in payloads]

    return asyncio.run(calls())


def test_a_call_without_the_value_beside_an_answer_does_not_undo_it(tmp_path, plain_repo):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=plain_repo))
    assert "second-look" in _text(asyncio.run(tool.handler(_reading("browser"))))
    chat_answer, browser_answer = _next_message(
        tool, _reading("chat", second_look_read=_READ), _reading("browser")
    )
    assert not chat_answer.get("is_error")
    assert "second-look" in _text(browser_answer)
    assert load(output).modality == "chat"
    assert not (output / FAILURE_FILE).exists()


def test_a_browser_call_before_a_chat_answer_with_the_value_stops_the_build(tmp_path, plain_repo):
    # The browser call is in the state of an answer to an unanswered look, so it stands.
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=plain_repo))
    assert "second-look" in _text(asyncio.run(tool.handler(_reading("browser"))))
    browser_answer, chat_answer = _next_message(
        tool, _reading("browser"), _reading("chat", second_look_read=_READ)
    )
    assert _STOP in _text(browser_answer)
    assert _STOP in _text(chat_answer)
    assert load(output) is None
    assert _failure(output)["code"] == "agent_type_unsupported"


def test_a_browser_call_after_a_chat_answer_neither_with_the_value_stops_the_build(
    tmp_path, plain_repo
):
    # Each is taken as the answer in turn: the handler can't tell that a later call in the same
    # message will undo what it saves.
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=plain_repo))
    assert "second-look" in _text(asyncio.run(tool.handler(_reading("browser"))))
    chat_answer, browser_answer = _next_message(tool, _reading("chat"), _reading("browser"))
    assert not chat_answer.get("is_error")
    assert _STOP in _text(browser_answer)
    assert load(output) is None
    assert _failure(output)["code"] == "agent_type_unsupported"


def _looked_at(tmp_path, repo):
    """A handler that has given one second look, and that look's answer."""
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=repo))
    return tool, asyncio.run(tool.handler(_reading("browser")))


def test_a_second_look_says_what_to_send_back(tmp_path, plain_repo):
    _tool, answer = _looked_at(tmp_path, plain_repo)
    assert f'second_look_read to "{_READ}"' in _text(answer)


def test_each_second_look_gets_its_own_value(tmp_path, plain_repo, monkeypatch):
    monkeypatch.setattr(tools, "_new_receipt", _REAL_RECEIPT)
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=plain_repo))
    answers = [_text(one) for one in _in_one_model_message(tool, "browser", together=True)]
    first, second = [answer.split('second_look_read to "')[1].split('"')[0] for answer in answers]
    assert first != second


def test_an_earlier_value_still_counts_once_a_value_came_back(tmp_path, plain_repo, monkeypatch):
    # Two calls in one message each got a value; the model may send back either.
    values = iter(["first", "second"])
    monkeypatch.setattr(tools, "_new_receipt", lambda: next(values))
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=plain_repo))
    assert all(_looks(_in_one_model_message(tool, "browser", together=True)))
    assert not asyncio.run(tool.handler(_reading("chat", second_look_read="second"))).get("is_error")
    assert _STOP in _text(asyncio.run(tool.handler(_reading("browser", second_look_read="first"))))
    assert _failure(output)["code"] == "agent_type_unsupported"


def test_a_second_look_answered_with_its_value_stands(tmp_path, browser_repo):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=browser_repo))
    assert asyncio.run(tool.handler(_reading("chat"))).get("is_error")
    assert not asyncio.run(tool.handler(_reading("chat", second_look_read=_READ))).get("is_error")
    assert (output / "contract.json").exists()
    assert "second_look_read" not in (output / "contract.json").read_text(encoding="utf-8")


def _answered(tmp_path, repo):
    """A handler whose second look came back with its value, as a chat answer that was saved."""
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=repo))
    assert "second-look" in _text(asyncio.run(tool.handler(_reading("browser"))))
    assert not asyncio.run(tool.handler(_reading("chat", second_look_read=_READ))).get("is_error")
    return tool


@pytest.mark.parametrize("made_up", ["guess", "", 7, ["read-it"], {"a": 1}, None])
def test_a_value_it_never_gave_is_not_a_second_look(tmp_path, plain_repo, made_up):
    output = tmp_path / "out"
    tool = _answered(tmp_path, plain_repo)
    answer = asyncio.run(tool.handler(_reading("browser", second_look_read=made_up)))
    assert "second-look" in _text(answer)
    assert not (output / FAILURE_FILE).exists()
    assert load(output).modality == "chat"


def test_a_value_before_any_second_look_is_not_a_second_look(tmp_path, plain_repo):
    output = tmp_path / "out"
    tool = _submit_tool(contract_tools(output, scan_root=plain_repo))
    answer = asyncio.run(tool.handler(_reading("browser", second_look_read=_READ)))
    assert "second-look" in _text(answer)
    assert not (output / FAILURE_FILE).exists()


@pytest.mark.parametrize(
    "packed",
    [
        {"contract": _reading("browser"), "second_look_read": _READ},
        {"contract": _reading("browser", second_look_read=_READ)},
        {"contract": json.dumps(_reading("browser", second_look_read=_READ))},
    ],
)
def test_the_value_counts_however_the_contract_was_packaged(tmp_path, plain_repo, packed):
    tool = _answered(tmp_path, plain_repo)
    assert _STOP in _text(asyncio.run(tool.handler(packed)))


def test_the_tool_declares_the_value_for_every_model(tmp_path):
    # Required, or of another type, it would be refused before the handler on every build.
    declared = _submit_tool(contract_tools(tmp_path)).input_schema
    assert "string" in declared["properties"]["second_look_read"]["type"]
    assert "integer" not in declared["properties"]["second_look_read"]["type"]
    assert "second_look_read" not in declared.get("required", [])


def test_a_failed_removal_still_writes_the_failure(tmp_path, browser_repo, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    (output / "contract.json").write_text("{}", encoding="utf-8")
    real = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.name == "contract.json":
            raise PermissionError("read-only")
        return real(self, *args, **kwargs)

    submit = _submitter(output, browser_repo)
    submit(_reading("browser"))
    monkeypatch.setattr(Path, "unlink", refuse)
    with pytest.raises(PermissionError):
        submit(_reading("browser"))
    assert (output / FAILURE_FILE).exists()


def test_a_failed_write_still_removes_the_earlier_reading(tmp_path, browser_repo, monkeypatch):
    output = tmp_path / "out"
    output.mkdir()
    (output / "contract.json").write_text("{}", encoding="utf-8")
    submit = _submitter(output, browser_repo)
    submit(_reading("browser"))
    _failing_write(monkeypatch)
    with pytest.raises(OSError, match="disk full"):
        submit(_reading("browser"))
    assert load(output) is None


def test_a_refused_new_reading_removes_the_earlier_one(tmp_path, browser_repo):
    output = tmp_path / "out"
    output.mkdir()
    (output / "contract.json").write_text("{}", encoding="utf-8")
    submit = _submitter(output, browser_repo)
    submit(_reading("browser"))
    submit(_reading("browser"))
    assert (output / FAILURE_FILE).exists()
    assert not (output / "contract.json").exists()
    assert load(output) is None


# After a refusal


@pytest.mark.parametrize(
    "files, tool",
    [
        (
            {
                "package.json": json.dumps(
                    {
                        "dependencies": {"next": "14", "react": "18", "openai": "4"},
                        "devDependencies": {"puppeteer": "22"},
                    }
                ),
                "app/page.tsx": "export default function Chat() { return null; }\n",
            },
            "puppeteer",
        ),
        (
            {
                "agent.py": "import openai\n",
                "scripts/ingest.py": "import scrapy\n",
            },
            "scrapy",
        ),
    ],
    ids=["next-chat-app-with-a-puppeteer-dev-dependency", "rag-bot-with-a-scrapy-ingest-script"],
)
@pytest.mark.parametrize("with_value", [True, False])
def test_a_chat_agent_first_read_as_browser_is_saved_after_its_look(
    tmp_path, files, tool, with_value
):
    source = _tree(tmp_path / "source", files)
    output = tmp_path / "out"
    submit = _submitter(output, source)
    look = submit(_reading("browser"))
    assert f"the source uses {tool}" in _text(look)
    assert not (output / FAILURE_FILE).exists()
    answer = submit(_reading("chat", **({"second_look_read": _READ} if with_value else {})))
    assert "Accepted and saved" in _text(answer)
    assert load(output) is not None
    assert not (output / FAILURE_FILE).exists()


def test_chat_resubmission_after_a_refusal_saves_nothing(tmp_path, browser_repo):
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    submit(_reading("browser"))
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
    submit(_reading("browser"))
    _failing_write(monkeypatch)
    with pytest.raises(OSError, match="disk full"):
        submit(_reading("browser"))
    assert not (output / FAILURE_FILE).exists()
    answer = submit(_reading("chat"))
    assert not answer.get("is_error")
    assert "This build can't go on." in _text(answer)
    assert not (output / "contract.json").exists()


def test_writer_failure_leaves_the_handler(tmp_path, browser_repo, monkeypatch):
    # Swallowed here, nothing would say the refusal was lost: the model would be told nothing, and
    # with no file the build would end as a crash.
    output = tmp_path / "out"
    submit = _submitter(output, browser_repo)
    submit(_reading("browser"))
    _failing_write(monkeypatch)
    with pytest.raises(OSError, match="disk full"):
        submit(_reading("browser"))
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
    answer = submit(_reading("browser", second_look_read=_READ))
    assert not answer.get("is_error")
    assert _READ_MESSAGE in _text(answer)
    assert _failure(output)["details"] == {"agent_type": "browser", "tools": []}
    assert not (output / "contract.json").exists()


def test_no_scan_root_behaves_as_no_tool(tmp_path):
    output = tmp_path / "out"
    submit = _submitter(output, None)
    assert "second-look" in _text(submit(_reading("browser")))
    assert not (output / FAILURE_FILE).exists()
    submit(_reading("browser", second_look_read=_READ))
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
    assert not submit(_reading(modality, second_look_read=_READ)).get("is_error")
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


def _counted_scans(monkeypatch) -> list[Path]:
    scans: list[Path] = []
    real_scan = tools.browser_tools_found

    def counted(root):
        scans.append(root)
        return real_scan(root)

    monkeypatch.setattr(tools, "browser_tools_found", counted)
    return scans


def test_one_scan_and_one_look_when_an_answer_comes_back_without_its_value(
    tmp_path, browser_repo, monkeypatch
):
    output = tmp_path / "out"
    scans = _counted_scans(monkeypatch)
    submit = _submitter(output, browser_repo)
    assert "second-look" in _text(submit(_reading("chat")))
    # An answer with the value stands, even when the contract is then sent back for another fix.
    rejected = submit(_reading("chat", real_use_cases=[], second_look_read=_READ))
    assert rejected["is_error"] and "second-look" not in _text(rejected)
    # The fix, sent without the value, is still that answer.
    assert not submit(_reading("chat")).get("is_error")
    assert load(output).modality == "chat"
    assert scans == [browser_repo]


def test_a_different_answer_without_the_value_after_an_answer_gets_a_look(tmp_path, plain_repo):
    # It could have run beside that answer, unread.
    output = tmp_path / "out"
    submit = _submitter(output, plain_repo)
    assert "second-look" in _text(submit(_reading("browser")))
    rejected = submit(_reading("chat", real_use_cases=[], second_look_read=_READ))
    assert rejected["is_error"] and "second-look" not in _text(rejected)
    assert "second-look" in _text(submit(_reading("browser")))
    assert not (output / FAILURE_FILE).exists()


def test_calls_waiting_on_the_first_scan_share_it(tmp_path, plain_repo, monkeypatch):
    scans = _counted_scans(monkeypatch)
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=plain_repo))

    async def first_message():
        return await asyncio.gather(*(tool.handler(_reading("browser")) for _ in range(3)))

    assert all(_looks(asyncio.run(first_message())))
    assert scans == [plain_repo]


def test_a_call_cancelled_while_waiting_on_the_scan_leaves_it_to_the_others(
    tmp_path, plain_repo, monkeypatch
):
    started, release = threading.Event(), threading.Event()

    def slow(_root):
        started.set()
        release.wait(10)
        return []

    monkeypatch.setattr(tools, "browser_tools_found", slow)
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=plain_repo))

    async def first_message():
        cancelled = asyncio.ensure_future(tool.handler(_reading("browser")))
        waiting = asyncio.ensure_future(tool.handler(_reading("browser")))
        await asyncio.to_thread(started.wait, 10)
        cancelled.cancel()
        await asyncio.sleep(0)
        release.set()
        return await waiting

    assert "second-look" in _text(asyncio.run(first_message()))


def test_a_scan_that_fails_is_run_again_by_the_next_call(tmp_path, plain_repo, monkeypatch):
    outcomes = iter([OSError("gone"), []])

    def scan(_root):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(tools, "browser_tools_found", scan)
    submit = _submitter(tmp_path / "out", plain_repo)
    with pytest.raises(OSError, match="gone"):
        submit(_reading("chat"))
    assert not submit(_reading("chat")).get("is_error")


def test_a_scan_that_fails_is_run_again_by_the_next_call_in_the_same_message(
    tmp_path, plain_repo, monkeypatch
):
    outcomes = iter([OSError("gone"), []])

    def scan(_root):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(tools, "browser_tools_found", scan)
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=plain_repo))

    async def one_message():
        with pytest.raises(OSError, match="gone"):
            await tool.handler(_reading("chat"))
        return await tool.handler(_reading("chat"))

    assert not asyncio.run(one_message()).get("is_error")


def test_a_scan_that_finished_after_its_only_caller_was_cancelled_is_not_run_again(
    tmp_path, plain_repo, monkeypatch
):
    started, release = threading.Event(), threading.Event()
    scans: list[Path] = []

    def slow(root):
        scans.append(root)
        started.set()
        release.wait(10)
        return []

    monkeypatch.setattr(tools, "browser_tools_found", slow)
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=plain_repo))

    async def cancelled_then_next():
        cancelled = asyncio.ensure_future(tool.handler(_reading("browser")))
        await asyncio.to_thread(started.wait, 10)
        (scan,) = asyncio.all_tasks() - {asyncio.current_task(), cancelled}
        cancelled.cancel()
        release.set()
        await asyncio.wait({scan})
        return await tool.handler(_reading("browser"))

    assert "second-look" in _text(asyncio.run(cancelled_then_next()))
    assert scans == [plain_repo]


def test_a_scan_left_running_on_a_closed_loop_is_started_again(tmp_path, plain_repo, monkeypatch):
    started, release = threading.Event(), threading.Event()
    scans: list[Path] = []

    def scan(root):
        scans.append(root)
        if len(scans) == 1:
            started.set()
            release.wait(10)
        return []

    monkeypatch.setattr(tools, "browser_tools_found", scan)
    tool = _submit_tool(contract_tools(tmp_path / "out", scan_root=plain_repo))
    closed = asyncio.new_event_loop()
    try:
        closed.create_task(tool.handler(_reading("browser")))
        closed.run_until_complete(asyncio.to_thread(started.wait, 10))
    finally:
        closed.close()
    try:
        assert "second-look" in _text(asyncio.run(tool.handler(_reading("browser"))))
    finally:
        release.set()
    assert len(scans) == 2


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
        assert "playwright" in _text(asyncio.run(tool.handler(_reading("browser"))))
        asyncio.run(tool.handler(_reading("browser")))
        assert _failure(tmp_path / output)["details"]["tools"] == ["playwright"]


def test_github_source_reaches_the_scan(tmp_path, browser_repo):
    source = GitHubSource(name="fixture", root=browser_repo, url="https://github.test/a/b")
    stage, _ = open_stage(source, out=tmp_path / "out")
    tool = _submit_tool(_server_of(stage))
    assert "playwright" in _text(asyncio.run(tool.handler(_reading("browser"))))
    asyncio.run(tool.handler(_reading("browser")))
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
    stage.spent_usd = 0.25
    assert asyncio.run(cli._understand(args)) == 78
    assert len(stage.said) == 1, "a refused reading must not be told nothing was saved"
    printed = capsys.readouterr()
    assert "spent:    $0.2500" in printed.out
    err = printed.err
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


def test_redelivered_command_on_failed_folder_gets_no_model_turn(tmp_path):
    write_failure_file(subfolder_not_found("agents/missing"), tmp_path)
    runtime = _runtime(tmp_path, state="running")
    runtime._write_journal({"command-7": "started"})
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


def _padded(line: str, size: int) -> str:
    return line + "#" * (size - len(line.encode()))


@pytest.mark.parametrize("over, expected", [(0, ["playwright"]), (1, [])])
def test_python_is_parsed_up_to_the_limit_and_skipped_past_it(tmp_path, over, expected):
    from fi.alk.harness.python_source import LARGEST_PARSED_MODULE

    repo = _tree(tmp_path, {"agent.py": _padded("import playwright\n", LARGEST_PARSED_MODULE + over)})

    assert browser_tools_found(repo) == expected


def test_only_python_naming_a_browser_tool_is_parsed(tmp_path, monkeypatch):
    repo = _tree(
        tmp_path,
        {
            "a.py": "import os\n",
            "b.py": "import selenium\n",
            "c.py": 'TOOL = "computer_20241022"\n',
            "d.py": "from langchain_community.agent_toolkits import PlayWrightBrowserToolkit\n",
        },
    )
    seen: list[str] = []
    real = agent_type_check.parsed
    monkeypatch.setattr(agent_type_check, "parsed", lambda text: seen.append(text) or real(text))

    assert browser_tools_found(repo) == ["PlayWrightBrowserToolkit", "computer-use tool", "selenium"]
    assert "import os\n" not in seen and len(seen) == 3


@pytest.mark.parametrize("total, expected", [(16, ["selenium"]), (15, [])])
def test_the_scan_parses_python_only_up_to_its_total(tmp_path, monkeypatch, total, expected):
    from fi.alk.harness import python_source

    repo = _tree(tmp_path, {"a.py": "import selenium\n", "b.py": "import playwright\n"})
    monkeypatch.setattr(python_source, "LARGEST_PARSED_TOTAL", total)

    assert browser_tools_found(repo) == expected


def test_the_walk_while_the_conversation_waits_follows_only_small_files(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent
    from fi.alk.harness.python_source import LARGEST_PARSED_MODULE

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/agent.py": "import common.big\n",
            "common/big.py": _padded("import common.web\n", LARGEST_PARSED_MODULE + 1),
            "common/web.py": "from playwright.sync_api import sync_playwright\n",
        },
    )

    assert browser_tools_in_agent(repo, "agents/a") == []
    # Bundling, with no conversation waiting, still follows it.
    assert (repo / "common" / "web.py").resolve() in imported_by_agent(repo, "agents/a")


@pytest.mark.parametrize("total, expected", [(36, ["playwright"]), (35, [])])
def test_the_walk_while_the_conversation_waits_parses_only_up_to_its_total(
    tmp_path, monkeypatch, total, expected
):
    from fi.alk.harness import python_source

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/agent.py": "import common.mid\n",
            "common/mid.py": "import common.web\n",
            "common/web.py": "import playwright\n",
        },
    )
    monkeypatch.setattr(python_source, "LARGEST_PARSED_TOTAL", total)

    assert browser_tools_in_agent(repo, "agents/a") == expected


def test_python_without_a_marker_is_not_tokenized(tmp_path, monkeypatch):
    import tokenize

    repo = _tree(tmp_path, {"agent.py": "import selenium  # computer\n"})
    monkeypatch.setattr(tokenize, "generate_tokens", lambda *args: pytest.fail("tokenized"))

    assert browser_tools_found(repo) == ["selenium"]


_IN_A_COMMENT = "# not used here: computer_20241022\nx = 1\n"
_IN_CODE = 'TOOL = "computer_20241022"\n'


@pytest.mark.parametrize(
    "text, total, expected",
    [
        (_IN_A_COMMENT, len(_IN_A_COMMENT), []),
        (_IN_CODE, len(_IN_CODE), ["computer-use tool"]),
        # Past the total a comment can't be told from code, so neither is read.
        (_IN_A_COMMENT, len(_IN_A_COMMENT) - 1, []),
        (_IN_CODE, len(_IN_CODE) - 1, []),
    ],
)
def test_past_the_tokenize_total_python_markers_are_not_read(
    tmp_path, monkeypatch, text, total, expected
):
    from fi.alk.harness import python_source

    repo = _tree(tmp_path, {"agent.py": text})
    monkeypatch.setattr(python_source, "LARGEST_TOKENIZED_TOTAL", total)

    assert browser_tools_found(repo) == expected


def _line_work(text):
    import io
    import tokenize

    return sum(len(token.line) for token in tokenize.generate_tokens(io.StringIO(text).readline))


def test_each_token_is_charged_the_line_it_is_read_from():
    from fi.alk.harness.python_source import TokenBudget

    text = _IN_CODE + "x = (1, 2, 3)\n# done\n"
    work = _line_work(text)

    found = TokenBudget(work=work).tokens(text)

    assert [token.string for token in found][:3] == ["TOOL", "=", '"computer_20241022"']
    assert TokenBudget(work=work - 1).tokens(text) is None


@pytest.mark.parametrize("short_of", [0, 1])
def test_past_the_tokenize_work_python_markers_are_not_read(tmp_path, monkeypatch, short_of):
    from fi.alk.harness import python_source

    repo = _tree(tmp_path, {"agent.py": _IN_CODE})
    monkeypatch.setattr(python_source, "LARGEST_TOKENIZED_WORK", _line_work(_IN_CODE) - short_of)

    assert browser_tools_found(repo) == ([] if short_of else ["computer-use tool"])


_OTHER_IN_CODE = 'SERVER = "@playwright/mcp"\n'


@pytest.mark.parametrize("limit", ["LARGEST_TOKENIZED_WORK", "LARGEST_TOKENIZED_TOTAL"])
def test_the_tokenize_limits_are_shared_by_the_scans_files(tmp_path, monkeypatch, limit):
    from fi.alk.harness import python_source

    # Each file alone is under the limit, and both together aren't: one budget for the scan.
    repo = _tree(tmp_path, {"a.py": _IN_CODE, "b.py": _OTHER_IN_CODE})
    cost = _line_work if limit == "LARGEST_TOKENIZED_WORK" else (lambda text: len(text.encode()))
    monkeypatch.setattr(
        python_source, limit, max(cost(_IN_CODE), cost(_OTHER_IN_CODE)) * 3 // 2
    )

    assert len(browser_tools_found(repo)) == 1


@pytest.mark.parametrize("one_line", [True, False])
def test_one_long_line_of_short_tokens_is_not_read_again_for_each(tmp_path, one_line):
    # On 3.12.3 the tokenizer decodes a token's whole line again for each token: about 4 s for
    # the long line, though it's under the total. Counted, not timed, so it holds on every Python.
    from fi.alk.harness.python_source import LARGEST_TOKENIZED_TOTAL, LARGEST_TOKENIZED_WORK

    names = ["\u00e9"] * 20_000
    text = _IN_CODE + ("x = " + ",".join(names) if one_line else "\n".join(names)) + "\n"
    assert len(text.encode()) <= LARGEST_TOKENIZED_TOTAL
    repo = _tree(tmp_path, {"agent.py": text})

    if one_line:
        assert _line_work(text) > 100 * LARGEST_TOKENIZED_WORK
        assert browser_tools_found(repo) == []
    else:
        # The same names, one a line: read in full.
        assert _line_work(text) < LARGEST_TOKENIZED_WORK // 10
        assert browser_tools_found(repo) == ["computer-use tool"]


def test_a_comment_past_the_tokenize_total_does_not_refuse_a_browser_reading(tmp_path):
    from fi.alk.harness.python_source import LARGEST_TOKENIZED_TOTAL
    from fi.alk.harness.tools import contract_tools

    # Honest: one file past the total, whose only marker is a comment.
    body = "# Unlike computer_20241022, this agent only chats.\n" + "x = 1\n" * (
        LARGEST_TOKENIZED_TOTAL // 6 + 1
    )
    repo = _tree(tmp_path / "repo", {"agent.py": body})
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=repo, scan_root=repo)
    reading = {
        "agent": "a",
        "modality": "browser",
        "real_use_cases": ["o"],
        "tools": [{"name": "add", "args": ["i"]}],
    }

    result = asyncio.run(server.tools[0].handler(reading))

    assert "- second-look:" in result["content"][0]["text"], result
    assert not (destination / FAILURE_FILE).exists()


def test_a_fullwidth_name_is_a_known_limit(tmp_path):
    # Python folds `ｐlaywright` to playwright, but folding every file at the save cost seconds
    # on a crafted one, and only a crafted file spells a name this way.
    repo = _tree(tmp_path, {"agent.py": "import \uff50laywright.sync_api\n"})

    assert browser_tools_found(repo) == []


def _type_keywords(size: int) -> str:
    return "import type " * (size // len("import type "))


def test_script_type_imports_are_dropped_as_before():
    from fi.alk.harness.credentials import _script_imports

    code = (
        'import type { A } from "./types";\n'
        'export type { B } from "./b";\n'
        'import { c } from "./c";\n'
        # The quote past the keyword's reach: not dropped, as before.
        "import type { F }" + " " * 1000 + "from './far';\n"
    )

    assert _script_imports(code) == ["./c", "./far"]


def test_a_crafted_script_does_not_hold_up_the_conversation(tmp_path):
    from fi.alk.harness.tools import contract_tools

    # Each keyword cost the old pattern a 1,000-character rescan: 0.6 s for this file, on the loop.
    files = {f"agents/a/t{n}.ts": _type_keywords(1_000_000) for n in range(3)}
    # And each keyword the strip's closing search a rescan of the line after its quote.
    files["agents/a/r.ts"] = _quote_left_open(1_000_000)
    repo = _tree(tmp_path / "repo", {**files, "agents/a/agent.py": "x = 1\n"})
    destination = tmp_path / "out"
    server = contract_tools(destination, source_root=repo, scan_root=repo, subfolder="agents/a")
    reading = {
        "agent": "a",
        "modality": "chat",
        "real_use_cases": ["o"],
        "hard_constraints": ["be polite"],
        "data_store": {"kind": "none"},
        "runtime": {"language": "python"},
        "tools": [{"name": "add", "args": ["i"]}],
    }

    async def save():
        worst = [0.0]
        done = asyncio.Event()

        async def beat():
            last = time.perf_counter()
            while not done.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                worst[0], last = max(worst[0], now - last), now

        beating = asyncio.create_task(beat())
        started = time.perf_counter()
        result = await server.tools[0].handler(reading)
        waited = time.perf_counter() - started
        done.set()
        await beating
        return result, worst[0], waited

    result, worst, waited = asyncio.run(save())

    assert not result.get("is_error"), result
    assert worst < 0.25
    assert waited < 2


def _quote_left_open(size: int) -> str:
    """Lines of 83 type keywords within reach of one opening quote, whose string never closes."""
    line = "import type " * 83 + '"'
    line += "a" * (size // 10 - len(line) - 1) + "\n"
    return line * 10


def test_the_type_import_strip_scans_the_code_at_most_twice(monkeypatch):
    from fi.alk.harness import credentials

    code = _quote_left_open(1_000_000)
    scanned = [0]
    search = credentials._SCRIPT_QUOTE_OR_LINE_END.search

    class Counting:
        def search(self, text, position):
            hit = search(text, position)
            scanned[0] += (hit.end() if hit else len(text)) - position
            return hit

    monkeypatch.setattr(credentials, "_SCRIPT_QUOTE_OR_LINE_END", Counting())

    # No string closes on its line, so nothing is dropped.
    assert credentials._without_type_imports(code) == code
    # Rescanning each keyword's line would be 83 times over.
    assert scanned[0] <= 2 * len(code)


_SHARED_IMPORT = 'import { x } from "../../common/web.js";\n'


@pytest.mark.parametrize(
    "budget, followed", [(len(_SHARED_IMPORT), True), (len(_SHARED_IMPORT) - 1, False)]
)
def test_the_walk_while_the_conversation_waits_reads_scripts_only_up_to_a_total(
    tmp_path, monkeypatch, budget, followed
):
    from fi.alk.harness.credentials import imported_by_agent

    script = _SHARED_IMPORT
    repo = _tree(
        tmp_path / "repo",
        {"agents/a/main.js": script, "common/web.js": 'const server = "@playwright/mcp";\n'},
    )
    monkeypatch.setattr(agent_type_check, "_WALKED_SCRIPTS", budget)

    assert browser_tools_in_agent(repo, "agents/a") == (["Playwright MCP"] if followed else [])
    # Bundling, with no conversation waiting, reads them all.
    assert (repo / "common" / "web.js").resolve() in imported_by_agent(repo, "agents/a")


_OLD_TYPE_IMPORT = re.compile(r"""\b(?:import|export)\s+type\b[^"'\n]{0,1000}["'][^"'\n]*["']""")


@pytest.mark.parametrize(
    "code",
    [
        'import type { A } from "./a";',
        "export  type * from './b';",
        'import type A from "./a\nimport { b } from "./b";',
        'import type { A } from "./a',
        "import type { A }\nfrom './a';",
        'import typeA from "./a";',
        "import type" + " " * 1000 + "'./edge';",
        "import type" + " " * 1001 + "'./past';",
        'import type x import type y "z"; import type "w"',
        "import type " * 50 + '"q" import type \'r\'',
        'import type { A } from "./a" + \'b\';',
        "",
    ],
)
def test_the_type_import_strip_matches_the_pattern_it_replaced(code):
    from fi.alk.harness.credentials import _without_type_imports

    assert _without_type_imports(code) == _OLD_TYPE_IMPORT.sub("", code)


@pytest.mark.parametrize("limit, followed", [(3, True), (2, False)])
@pytest.mark.parametrize("kind", ["python", "script"])
def test_the_walk_while_the_conversation_waits_tries_only_so_many_imports(
    tmp_path, monkeypatch, limit, followed, kind
):
    from fi.alk.harness.credentials import imported_by_agent

    # Two names that resolve nowhere, then the shared file, all in one file.
    files = (
        {"agents/a/main.py": "import nowhere1\nimport nowhere2\nimport common.web\n", "common/__init__.py": "", "common/web.py": "import playwright\n"}
        if kind == "python"
        else {"agents/a/main.js": 'import "./nowhere1";\nimport "./nowhere2";\nimport "../../common/web.js";\n', "common/web.js": 'const s = "@playwright/mcp";\n'}
    )
    repo = _tree(tmp_path / "repo", files)
    monkeypatch.setattr(agent_type_check, "_WALKED_IMPORTS", limit)

    found = browser_tools_in_agent(repo, "agents/a")
    assert bool(found) is followed, found
    # Bundling tries them all.
    shared = repo / "common" / ("web.py" if kind == "python" else "web.js")
    assert shared.resolve() in imported_by_agent(repo, "agents/a")


@pytest.mark.parametrize("limit, expected", [(80, ["Playwright MCP"]), (79, [])])
def test_the_scan_reads_scripts_only_up_to_their_own_total(tmp_path, monkeypatch, limit, expected):
    # Python is read first and doesn't count towards it.
    repo = _tree(
        tmp_path,
        {"a.js": "x" * 40, "b.js": 'const s = "@playwright/mcp";' + "x" * 12, "agent.py": "x = 1\n" * 50},
    )
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCRIPT_SCAN", limit)

    assert browser_tools_found(repo) == expected


@pytest.mark.parametrize("limit, expected", [(80, ["playwright"]), (79, [])])
def test_the_scan_searches_python_only_up_to_its_own_total(tmp_path, monkeypatch, limit, expected):
    # Scripts don't count towards it.
    repo = _tree(
        tmp_path,
        {"a.py": "x = 1  # " + "x" * 31, "b.py": "import playwright\n" + "x" * 22, "c.js": "x" * 500},
    )
    monkeypatch.setattr(agent_type_check, "_LARGEST_PYTHON_SCAN", limit)

    assert browser_tools_found(repo) == expected


def test_a_long_run_of_combining_marks_is_not_parsed():
    from fi.alk.harness.python_source import parsed

    # On 3.11 and 3.12.3 the parser folds such a name by insertion sort, seconds a file.
    assert parsed("x = a" + "\u0301" * 32) is None
    assert parsed("x = a" + "\u0f73" * 16) is None  # Each decomposes to two marks.
    assert parsed("x = a" + "\u0301" * 31) is not None
    # A composed letter's own mark starts the run: é is e and one mark.
    assert parsed("x = \u00e9" + "\u0301" * 31) is None
    assert parsed("x = 'caf\u00e9 n\u00e4ive r\u00e9sum\u00e9 \u1ec7'\n# \u0928\u092e\u0938\u094d\u0924\u0947 \u8fd9\u662f\u6ce8\u91ca\n") is not None


def test_a_parse_budget_counts_bytes():
    from fi.alk.harness.python_source import ParseBudget

    budget = ParseBudget(4)

    assert budget.spend("éé")
    assert not budget.spend("é")


def test_the_walk_counts_its_parse_limit_in_bytes(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    # 64 K two-byte characters: 128 KB in the file, but half that in characters.
    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/main.py": "import common.web\n# " + "é" * (64 * 1024),
            "common/__init__.py": "",
            "common/web.py": "",
        },
    )
    shared = (repo / "common" / "web.py").resolve()

    assert shared not in imported_by_agent(repo, "agents/a", parse_limit=128 * 1024)
    assert shared in imported_by_agent(repo, "agents/a", parse_limit=256 * 1024)


def _nested_packages(repo: Path, depth: int) -> Path:
    directory = repo
    for _ in range(depth):
        directory = directory / "p"
        directory.mkdir(parents=True)
        (directory / "__init__.py").write_text("")
    return directory


def test_a_chain_of_nested_packages_does_not_hold_up_a_folder_save(tmp_path):
    # Every name rebuilt and resolved each package above it: 58 s for 358 of them.
    repo = tmp_path / "repo"
    (repo / "agents" / "a").mkdir(parents=True)
    _nested_packages(repo, 300)
    (repo / "agents" / "a" / "agent.py").write_text(
        "".join("import " + ".".join(["p"] * n) + "\n" for n in range(1, 301))
    )
    server = contract_tools(tmp_path / "out", source_root=repo, scan_root=repo, subfolder="agents/a")
    reading = {
        "agent": "a",
        "modality": "chat",
        "real_use_cases": ["o"],
        "hard_constraints": ["be polite"],
        "data_store": {"kind": "none"},
        "runtime": {"language": "python"},
        "tools": [{"name": "add", "args": ["i"]}],
    }

    started = time.perf_counter()
    result = asyncio.run(server.tools[0].handler(reading))

    assert not result.get("is_error"), result
    assert time.perf_counter() - started < 2


def test_the_walk_looks_up_each_package_once(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    repo = tmp_path / "repo"
    (repo / "agents" / "a").mkdir(parents=True)
    _nested_packages(repo, 20)
    (repo / "agents" / "a" / "agent.py").write_text(
        "".join("import " + ".".join(["p"] * n) + "\n" for n in range(1, 21))
    )
    looked: list[Path] = []
    look_up = credentials._file_inside

    def counted(candidate, *args):
        looked.append(candidate)
        return look_up(candidate, *args)

    monkeypatch.setattr(credentials, "_file_inside", counted)

    credentials.imported_by_agent(repo, "agents/a")

    packages = [path for path in looked if path.name == "__init__.py"]
    assert packages and len(packages) == len(set(packages))


def _counted_lookups(monkeypatch) -> list[Path]:
    from fi.alk.harness import credentials

    looked: list[Path] = []
    look_up = credentials._file_inside

    def counted(candidate, *args):
        looked.append(candidate)
        return look_up(candidate, *args)

    monkeypatch.setattr(credentials, "_file_inside", counted)
    return looked


def test_a_folder_without_a_package_file_is_looked_up_once(tmp_path, monkeypatch):
    from fi.alk.harness.credentials import imported_by_agent

    # Folders with no __init__.py, a module in each: every import looks for the packages above
    # its module, so a miss there is kept, or the chain costs the square of its depth again.
    repo = tmp_path / "repo"
    (repo / "agents" / "a").mkdir(parents=True)
    directory = repo
    for _ in range(20):
        directory = directory / "p"
        directory.mkdir()
        (directory / "m.py").write_text("")
    (repo / "agents" / "a" / "agent.py").write_text(
        "".join("import " + ".".join(["p"] * n + ["m"]) + "\n" for n in range(1, 21))
    )
    looked = _counted_lookups(monkeypatch)

    assert (directory / "m.py").resolve() in imported_by_agent(repo, "agents/a")
    packages = [path for path in looked if path.name == "__init__.py"]
    assert packages and len(packages) == len(set(packages))


def test_the_walk_keeps_what_it_finds_but_not_what_it_misses(tmp_path, monkeypatch):
    from fi.alk.harness.credentials import imported_by_agent

    folder = tmp_path / "repo" / "agents" / "a"
    folder.mkdir(parents=True)
    (folder / "util.ts").write_text("export const x = 1;\n")
    # Kept, a miss would cost memory for each place a script name could be, 20,000 names over.
    for name in ("one.ts", "two.ts"):
        (folder / name).write_text("import './util';\nimport 'missing';\n")
    looked = _counted_lookups(monkeypatch)

    assert (folder / "util.ts").resolve() in imported_by_agent(tmp_path / "repo", "agents/a")
    assert looked.count(folder / "util.ts") == 1
    assert looked.count(folder / "missing.ts") == 2


@pytest.mark.parametrize("line_end", ["\r", "\r\n", "\n"])
def test_the_walk_reads_every_kind_of_line_end(tmp_path, line_end):
    from fi.alk.harness.credentials import imported_by_agent

    folder = tmp_path / "repo" / "agents" / "a"
    folder.mkdir(parents=True)
    (folder / "keys.ts").write_text("export const k = 1;\n")
    # Read with lone CRs left in, the comment would run to the end of the file.
    (folder / "agent.ts").write_bytes(
        f'// shared helpers{line_end}import {{ k }} from "./keys";{line_end}'.encode()
    )

    assert (folder / "keys.ts").resolve() in imported_by_agent(tmp_path / "repo", "agents/a")


def test_an_import_name_past_the_path_limit_is_not_looked_up(tmp_path, monkeypatch):
    from fi.alk.harness.credentials import imported_by_agent

    folder = tmp_path / "repo" / "agents" / "a"
    folder.mkdir(parents=True)
    # 4,097 characters each; the last one, 4,096.
    (folder / "agent.ts").write_text(
        f"import '{'x' * 4097}';\nimport './{'y' * 4095}';\nimport './{'z' * 4094}';\n"
    )
    (folder / "agent.py").write_text(f"import {'w' * 4097}\n")
    looked = _counted_lookups(monkeypatch)

    imported_by_agent(tmp_path / "repo", "agents/a")

    assert {path.name[0] for path in looked if len(path.name) > 4000} == {"z"}


@pytest.mark.parametrize("depth, followed", [(64, True), (65, False)])
def test_the_walk_follows_packages_only_so_deep(tmp_path, depth, followed):
    from fi.alk.harness.credentials import imported_by_agent

    repo = tmp_path / "repo"
    (repo / "agents" / "a").mkdir(parents=True)
    deepest = _nested_packages(repo, depth - 1)
    (deepest / "web.py").write_text("")
    (repo / "agents" / "a" / "agent.py").write_text(
        "import " + ".".join(["p"] * (depth - 1) + ["web"]) + "\n"
    )

    assert ((deepest / "web.py").resolve() in imported_by_agent(repo, "agents/a")) is followed


def test_import_text_inside_a_script_string_is_not_followed():
    from fi.alk.harness.credentials import _script_imports

    code = (
        "const s = 'import { l } from \"../b/agent\"';\n"
        'const t = `require("../d")`;\n'
        "import y from './c';\n"
    )

    assert _script_imports(code) == ["./c"]


@pytest.mark.parametrize(
    "folder, shared, found",
    [
        # The agent and the file it imports both under e2e/: the agent's own code.
        ("e2e/agent", "e2e/shared/web.py", ["playwright"]),
        ("browser_tests/agent", "browser_tests/common/web.py", ["playwright"]),
        # A test helper beside the agent's folder is still a test helper.
        ("agents/a", "tests/helpers/web.py", []),
    ],
)
def test_test_folders_are_judged_below_what_the_agent_shares(tmp_path, folder, shared, found):
    module = ".".join(PurePosixPath(shared).with_suffix("").parts)
    repo = _tree(
        tmp_path / "repo",
        {f"{folder}/agent.py": f"import {module}\n", shared: "import playwright\n"},
    )

    assert browser_tools_in_agent(repo, folder) == found


def _vendored_above(tmp_path, folder_rows: int) -> Path:
    # The agent imports `vend.web`, which a record at the repository's root names as unpacked;
    # a record in the agent's folder holds `folder_rows` rows, read first.
    return _tree(
        tmp_path / "repo",
        {
            "vend/__init__.py": "",
            "vend/web.py": "import playwright\n",
            "x-1.dist-info/METADATA": "",
            "x-1.dist-info/RECORD": "vend/__init__.py,,\nvend/web.py,,\n",
            "agents/a/agent.py": "import vend.web\n",
            "agents/a/y-1.dist-info/METADATA": "",
            "agents/a/y-1.dist-info/RECORD": "".join(f"r{n:06d}.py,,\n" for n in range(folder_rows)),
        },
    )


@pytest.mark.parametrize("short_of, expected", [(0, []), (1, ["playwright"])])
def test_the_folders_at_and_above_the_agent_share_one_record_budget(
    tmp_path, monkeypatch, short_of, expected
):
    repo = _vendored_above(tmp_path, 100)
    # Each record costs at least a disk block, as #224 charges it.
    sizes = sum(
        max((repo / name).stat().st_size, agent_type_check._SMALLEST_RECORD)
        for name in ("x-1.dist-info/RECORD", "agents/a/y-1.dist-info/RECORD")
    )
    monkeypatch.setattr(agent_type_check, "_RECORDS_AT_AND_ABOVE", sizes - short_of)

    # Past the budget, the root's record isn't read, so its package counts as the agent's code.
    assert browser_tools_in_agent(repo, "agents/a") == expected


def test_each_folder_above_the_agent_has_its_records_read_once(tmp_path, monkeypatch):
    repo = _vendored_above(tmp_path, 1)
    read: list[Path] = []
    real = agent_type_check._unpacked_at_top
    monkeypatch.setattr(
        agent_type_check,
        "_unpacked_at_top",
        lambda root, left=None: read.append(Path(root)) or real(root, left),
    )

    assert browser_tools_in_agent(repo, "agents/a") == []
    assert read.count(repo.resolve()) == 1
    # The agent's folder: once here, and once in its own walk's look at its packages.
    assert read.count((repo / "agents" / "a").resolve()) == 2


@pytest.mark.parametrize(
    "source",
    [
        # Dileep's: Python runs plain ASCII under idna as it would without the line.
        b"# coding: idna\nimport playwright\n",
        b"# coding: idna\nimport playwright  # xn--bcher-kva\n",
        b"# coding: punycode\nimport playwright\n",
        b"# coding: undefined\nimport playwright\n",
        b"# coding: unicode_escape\n\\x69mport playwright\n",
        # A non-ASCII byte on the coding line: detect_encoding refuses it, the parser doesn't.
        b"# caf\xe9 coding: idna\nimport playwright\n",
    ],
    ids=["idna", "idna-xn", "punycode", "undefined", "unicode-escape", "non-ascii-line"],
)
def test_a_file_left_undecoded_for_its_codec_still_counts_the_browser_names_it_holds(tmp_path, source):
    # Its names count, as bytes, which asks for a second look, never a refusal on its own.
    (tmp_path / "agent.py").write_bytes(source)
    (tmp_path / "other.py").write_text("import selenium\n", encoding="utf-8")

    assert browser_tools_found(tmp_path) == ["playwright", "selenium"]


@pytest.mark.parametrize("coding", ["rot13", "nosuch"])
def test_a_coding_line_python_itself_refuses_is_skipped_by_the_browser_scan(tmp_path, coding):
    # Python won't run such a file at all, so what it names says nothing.
    (tmp_path / "agent.py").write_bytes(f"# coding: {coding}\nimport playwright\n".encode())
    (tmp_path / "other.py").write_text("import selenium\n", encoding="utf-8")

    assert browser_tools_found(tmp_path) == ["selenium"]


def test_a_file_naming_nothing_in_a_refused_codec_finds_nothing(tmp_path):
    (tmp_path / "agent.py").write_bytes(b"# coding: idna\nx = 1\n")

    assert browser_tools_found(tmp_path) == []


def test_a_coding_line_is_found_with_lone_cr_line_ends(tmp_path):
    # Python reads a lone CR as a line end, so it finds the coding line on the first line.
    (tmp_path / "agent.py").write_bytes(
        b"# -*- coding: latin-1 -*-\rimport playwright\rNAME = '\xe9'\r"
    )

    assert browser_tools_found(tmp_path) == ["playwright"]


def test_the_walk_reads_a_helper_in_the_encoding_its_coding_line_names(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = tmp_path / "repo"
    _tree(
        repo,
        {
            "agents/a/agent.py": "import common.web\n",
            "common/__init__.py": "",
            "common/deep.py": "",
        },
    )
    # In latin-1, \xe9 is a letter; read as UTF-8 it's a replacement character, which no name may
    # hold, so the file wouldn't parse and its imports wouldn't be followed.
    (repo / "common" / "web.py").write_bytes(
        b"# -*- coding: latin-1 -*-\ncaf\xe9 = 1\nimport common.deep\n"
    )

    assert (repo / "common" / "deep.py").resolve() in imported_by_agent(repo, "agents/a")


@pytest.mark.parametrize("coding", ["rot13", "base64", "nosuch"])
def test_the_walk_reads_a_file_whose_coding_line_python_cant_read_as_utf8(tmp_path, coding):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "import common\n", "common/__init__.py": ""})
    (repo / "agents" / "a" / "odd.py").write_bytes(f"# coding: {coding}\nimport common\n".encode())

    assert (repo / "common" / "__init__.py").resolve() in imported_by_agent(repo, "agents/a")


@pytest.mark.parametrize(
    "coding", ["punycode", "PUNYCODE", "idna", "IDNA", "undefined", "unicode_escape", "unicode-escape"]
)
def test_a_codec_python_reads_slowly_or_not_at_all_is_not_used(coding):
    from fi.alk.harness.python_source import source_encoding

    # punycode's decode costs the square of its size, 2.3 s for 128 KB, and idna's 91 ms for
    # 128 KB of valid labels. Python keeps a coding line's spelling, so the codec's own name is
    # the one compared.
    assert source_encoding(f"# coding: {coding}\nx = 1\n".encode()) is None


@pytest.mark.parametrize(
    "coding", ["punycode", "PUNYCODE", "idna", "IDNA", "undefined", "unicode_escape", "unicode-escape"]
)
def test_the_reader_leaves_a_slow_codecs_file_undecoded(tmp_path, coding):
    from fi.alk.harness.python_source import read_module

    # Handed back as bytes, not decoded; without a scan's total, unicode-escape too.
    path = tmp_path / "agent.py"
    path.write_bytes(f"# coding: {coding}\nx = 1\n".encode())
    undecoded: list[bytes] = []
    assert read_module(path, undecoded=undecoded) is None
    assert undecoded == [path.read_bytes()]


def test_the_reader_hands_back_only_a_file_in_a_codec_refused_by_name(tmp_path):
    from fi.alk.harness.python_source import read_module

    # Not UTF-8 and no coding line: Python refuses it, so its names don't count.
    path = tmp_path / "agent.py"
    path.write_bytes(b"import playwright\nx = '\xff'\n")
    undecoded: list[bytes] = []
    assert read_module(path, undecoded=undecoded) is None
    assert undecoded == []


def test_the_walk_reads_a_file_python_cant_decode_as_utf8(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {"agents/a/agent.py": "import helper\n", "common/__init__.py": "", "common/deep.py": ""},
    )
    # cp1252 has no character for \x8d, so Python refuses the file. Decoding it with replacement
    # runs Python's own error handler for each such byte, 60 ms a MB; read strictly, it fails at
    # once and is read as UTF-8, where cp1252's \xe9 leaves a name that won't parse.
    (repo / "agents" / "a" / "helper.py").write_bytes(
        b"# coding: cp1252\ncaf\xe9 = 1\nimport common.deep\n# \x8d\n"
    )

    assert (repo / "common" / "deep.py").resolve() not in imported_by_agent(repo, "agents/a")


@pytest.mark.parametrize("coding", ["punycode", "idna", "undefined"])
def test_the_walk_follows_a_helper_whatever_codec_its_coding_line_names(tmp_path, coding):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "import helper\n", "common/__init__.py": ""})
    # idna can't replace what it can't read, so its decode raises; the walk reads it as UTF-8.
    (repo / "agents" / "a" / "helper.py").write_bytes(f"# coding: {coding}\nimport common\n".encode())

    assert (repo / "common" / "__init__.py").resolve() in imported_by_agent(repo, "agents/a")


def test_a_record_hides_the_files_it_names_not_their_folders(tmp_path):
    # A record names files. A row that names a folder, which pip doesn't write, hides nothing.
    repo = _tree(
        tmp_path / "repo",
        {
            "vend/__init__.py": "",
            "vend/web.py": "import playwright\n",
            "x-1.dist-info/METADATA": "",
            "x-1.dist-info/RECORD": "vend,,\nvend/__init__.py,,\n",
            "agents/a/agent.py": "import vend.web\n",
        },
    )

    assert browser_tools_in_agent(repo, "agents/a") == ["playwright"]


def _browser_package_and_records(tmp_path: Path, shape: str) -> tuple[Path, str, int]:
    """A repository with an installed `playwright`, and records that spend the budget before its
    own is read. Returns the repository, the agent's folder and the budget."""
    many = "".join(f"r{n:06d}.py,,\n" for n in range(200))
    playwright = {"playwright/__init__.py": "", "playwright-1.0.dist-info/METADATA": ""}
    if shape == "between":
        # The folder's and the root's records fit; the folder between them spends the rest.
        files = {
            **playwright,
            "playwright-1.0.dist-info/RECORD": "playwright/__init__.py,,\n",
            "agents/a/p/q/agent.py": "import playwright\n",
            "agents/a/p/q/y-1.dist-info/METADATA": "",
            "agents/a/p/q/y-1.dist-info/RECORD": many,
            "agents/a/p/z-1.dist-info/METADATA": "",
            "agents/a/p/z-1.dist-info/RECORD": many,
        }
        sub, spent = "agents/a/p/q", ["agents/a/p/q/y-1.dist-info/RECORD", "playwright-1.0.dist-info/RECORD"]
    elif shape == "folder":
        # Round 5's: the folder's own records spend it all before the root's are read.
        files = {
            **playwright,
            "playwright-1.0.dist-info/RECORD": "playwright/__init__.py,,\n",
            "agents/a/agent.py": "import playwright\n",
            "agents/a/y-1.dist-info/METADATA": "",
            "agents/a/y-1.dist-info/RECORD": many,
        }
        sub, spent = "agents/a", ["agents/a/y-1.dist-info/RECORD"]
    else:
        # Its mirror: playwright installed in the folder, after records listed before its own.
        files = {
            **{f"agents/a/{name}": text for name, text in playwright.items()},
            "agents/a/playwright-1.0.dist-info/RECORD": "playwright/__init__.py,,\n",
            "agents/a/agent.py": "import playwright\n",
            "agents/a/a-1.dist-info/METADATA": "",
            "agents/a/a-1.dist-info/RECORD": many,
        }
        sub, spent = "agents/a", ["agents/a/a-1.dist-info/RECORD"]
    repo = _tree(tmp_path / "repo", files)
    return repo, sub, sum((repo / name).stat().st_size for name in spent)


@pytest.mark.parametrize("shape", ["between", "folder", "mirror"])
def test_a_browser_package_whose_record_is_left_unread_is_not_the_customers_module(
    tmp_path, monkeypatch, shape
):
    # No order of reading keeps both the folder's and the root's records whole once the budget
    # is spent, so a browser package there counts as installed, and the agent's import of it.
    repo, sub, budget = _browser_package_and_records(tmp_path, shape)
    monkeypatch.setattr(agent_type_check, "_RECORDS_AT_AND_ABOVE", budget)

    assert browser_tools_in_agent(repo, sub) == ["playwright"]


def test_a_customers_own_module_stays_theirs_while_the_records_fit(tmp_path):
    # Nothing installed it: the customer's own `playwright.py` beside the agent is their module.
    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/agent.py": "import playwright\n",
            "agents/a/playwright.py": "",
            "agents/a/y-1.dist-info/METADATA": "",
            "agents/a/y-1.dist-info/RECORD": "other.py,,\n",
        },
    )

    assert browser_tools_in_agent(repo, "agents/a") == []


@pytest.mark.parametrize("naming, expected", [(1, []), (4, []), (5, ["playwright"])])
def test_a_record_hides_a_file_the_agent_imports_from_the_nearest_four_folders_holding_it(
    tmp_path, naming, expected
):
    # The agent in b/c/d/e/agent imports b/c/d/e/vend/web.py, which sits in e, d, c, b and the
    # root, nearest first. Each holds a record; the one in the `naming`th names the file, as pip
    # writes it, relative to its folder. As _agent_files does, only the nearest four are checked:
    # a crafted chain of them would cost its depth for each of thousands of files.
    holding = ["b/c/d/e", "b/c/d", "b/c", "b", ""]
    files = {
        "b/c/d/e/agent/agent.py": "from ..vend import web\n",
        "b/c/d/e/vend/__init__.py": "",
        "b/c/d/e/vend/web.py": "import playwright\n",
    }
    for n, folder in enumerate(holding, start=1):
        record = f"{folder}/x{n}-1.dist-info".lstrip("/")
        row = "b/c/d/e/vend/web.py".removeprefix(f"{folder}/") if folder else "b/c/d/e/vend/web.py"
        files[f"{record}/METADATA"] = ""
        files[f"{record}/RECORD"] = f"{row},,\n" if n == naming else "other.py,,\n"
    repo = _tree(tmp_path / "repo", files)

    assert browser_tools_in_agent(repo, "b/c/d/e/agent") == expected


def test_a_record_between_the_root_and_the_agents_folder_hides_the_files_it_names(tmp_path):
    repo = _tree(
        tmp_path / "repo",
        {
            "agents/__init__.py": "",
            "agents/a/__init__.py": "",
            "agents/a/agent.py": "from ..vend import web\n",
            "agents/vend/__init__.py": "",
            "agents/vend/web.py": "import playwright\n",
            "agents/x-1.dist-info/METADATA": "",
            "agents/x-1.dist-info/RECORD": "vend/__init__.py,,\nvend/web.py,,\n",
        },
    )

    assert browser_tools_in_agent(repo, "agents/a") == []
    (repo / "agents" / "x-1.dist-info" / "RECORD").unlink()
    assert browser_tools_in_agent(repo, "agents/a") == ["playwright"]


def test_the_walk_charges_a_file_found_for_each_part_of_its_path(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/agent.py": "import common.web\n",
            # A real folder without the module: its candidate is looked up, and missed.
            "agents/a/common/README": "",
            "common/__init__.py": "",
            "common/web.py": "",
        },
    )
    charged: list[tuple[Path, float]] = []
    real = credentials._file_inside

    def spy(candidate, root, deepest, left=None):
        before = left[0]
        hit = real(candidate, root, deepest, left)
        charged.append((candidate, before - left[0]))
        return hit

    monkeypatch.setattr(credentials, "_file_inside", spy)
    credentials.imported_by_agent(repo, "agents/a", lookup_limit=10**9)

    # Resolving a file looks up each part of its path in turn; a miss is one lookup. (A candidate
    # under a folder that isn't there, or a link, is charged as a miss before this is asked.)
    assert any(candidate.is_file() for candidate, _ in charged)
    assert any(not candidate.exists() for candidate, _ in charged)
    for candidate, cost in charged:
        assert cost == (1 + len(candidate.parts) if candidate.is_file() else 1), candidate


@pytest.mark.parametrize("limit, followed", [(10**6, True), (1, False)])
def test_the_walk_while_the_conversation_waits_makes_only_so_many_lookups(
    tmp_path, monkeypatch, limit, followed
):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/main.py": "import common.web\n",
            "common/__init__.py": "",
            "common/web.py": "import playwright\n",
        },
    )
    monkeypatch.setattr(agent_type_check, "_WALKED_LOOKUPS", limit)

    assert bool(browser_tools_in_agent(repo, "agents/a")) is followed
    # Bundling has no such limit.
    assert (repo / "common" / "web.py").resolve() in imported_by_agent(repo, "agents/a")


def test_the_walk_starts_from_no_file_in_a_folder_no_ones_code_is_in(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/agent.py": "x = 1\n",
            "agents/a/.venv/lib/site.py": "import common.web\n",
            "agents/a/node_modules/x/index.js": 'import "../../../../common/web.js";\n',
            "common/__init__.py": "",
            "common/web.py": "",
            "common/web.js": "",
        },
    )

    found = imported_by_agent(repo, "agents/a")
    assert (repo / "common" / "web.py").resolve() not in found
    assert (repo / "common" / "web.js").resolve() not in found


def test_the_walk_stops_reading_once_its_lookups_are_spent(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    repo = _tree(
        tmp_path / "repo",
        {"agents/a/a.py": "import nowhere\n", "agents/a/b.py": "import common\n", "common/__init__.py": ""},
    )
    parsed: list[str] = []
    real = credentials._python_imports
    monkeypatch.setattr(
        credentials,
        "_python_imports",
        lambda content, *rest: parsed.append(content) or real(content, *rest),
    )

    # One lookup for each folder's pyproject.toml, from agents/a up to the root, then a.py's first
    # lookup spends the rest; nothing b.py names could be looked up.
    assert credentials.imported_by_agent(repo, "agents/a", lookup_limit=4) == frozenset()
    assert parsed == ["import nowhere\n"]


def test_a_toolkit_whose_record_is_left_unread_is_not_the_customers_module(tmp_path, monkeypatch):
    # Once the budget is cut, every import counts, not only the browser packages' own names.
    repo = _tree(
        tmp_path / "repo",
        {
            "langchain_community/__init__.py": "",
            "langchain_community-1.0.dist-info/METADATA": "",
            "langchain_community-1.0.dist-info/RECORD": "langchain_community/__init__.py,,\n",
            "agents/a/agent.py": (
                "from langchain_community.agent_toolkits import PlayWrightBrowserToolkit\n"
            ),
            "agents/a/y-1.dist-info/METADATA": "",
            "agents/a/y-1.dist-info/RECORD": "".join(f"r{n:06d}.py,,\n" for n in range(200)),
        },
    )
    folder_record = (repo / "agents/a/y-1.dist-info/RECORD").stat().st_size
    monkeypatch.setattr(
        agent_type_check, "_RECORDS_AT_AND_ABOVE", max(folder_record, agent_type_check._SMALLEST_RECORD)
    )

    assert browser_tools_in_agent(repo, "agents/a") == ["PlayWrightBrowserToolkit"]


_DEPENDS_ON_COMMON = (
    "[project]\nname='agent-a'\nversion='1'\ndependencies=['common>=1']\n"
    "[tool.uv.sources]\ncommon={workspace=true}\n"
)


@pytest.mark.parametrize(
    "members, found, stopped",
    [
        ('["agents/*", "libs/*"]', True, False),
        ('["agents/a", "libs/common"]', True, False),
        ('["agents/*", "libs/*"]\nexclude=["libs/common"]', False, False),
        ('["agents/*", "**/common"]', False, True),
        ('["agents/*", "/abs/*", "../out/*", 5]', False, False),
        ('["libs/*"]', False, False),
    ],
    ids=["glob", "literal", "excluded", "double-star-not-expanded", "invalid", "agent-not-a-member"],
)
def test_the_walk_looks_in_the_workspace_members_the_agent_depends_on(tmp_path, members, found, stopped):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "pyproject.toml": f"[tool.uv.workspace]\nmembers={members}\n",
            "agents/a/pyproject.toml": _DEPENDS_ON_COMMON,
            "agents/a/agent.py": "from common import llm\n",
            "libs/common/pyproject.toml": "[project]\nname='Common'\nversion='1'\n",
            "libs/common/src/common/__init__.py": "",
            "libs/common/src/common/llm.py": "",
        },
    )
    llm = (repo / "libs" / "common" / "src" / "common" / "llm.py").resolve()
    said: list[bool] = []

    assert (llm in imported_by_agent(repo, "agents/a", lookup_limit=100_000, stopped=said)) is found
    assert said == [stopped]


@pytest.mark.parametrize(
    "agent, sources, found",
    [
        ("dependencies=['common']\n[tool.uv.sources]\ncommon={workspace=true}\n", "", True),
        # The source declared at the workspace root, as uv lets every member inherit it.
        ("dependencies=['common']\n", "[tool.uv.sources]\ncommon={workspace=true}\n", True),
        # Named, but from the index, not the workspace: uv installs that one instead.
        ("dependencies=['common']\n", "", False),
        # Its source, but not asked for: not installed.
        ("[tool.uv.sources]\ncommon={workspace=true}\n", "", False),
        # The build syncs with --no-dev, so the dev group isn't installed; a default group is.
        ("[dependency-groups]\ndev=['common']\n[tool.uv.sources]\ncommon={workspace=true}\n", "", False),
        (
            "[dependency-groups]\nlint=['common']\n[tool.uv]\ndefault-groups=['lint']\n"
            "[tool.uv.sources]\ncommon={workspace=true}\n",
            "",
            True,
        ),
        ("[dependency-groups]\nlint=['common']\n[tool.uv.sources]\ncommon={workspace=true}\n", "", False),
        # Which extras the build asks for isn't known at the save: all of the agent's count.
        ("[project.optional-dependencies]\nllm=['common']\n[tool.uv.sources]\ncommon={workspace=true}\n", "", True),
        # A list of sources, each with its own marker.
        (
            "dependencies=['common']\n[tool.uv.sources]\ncommon=[{workspace=true, marker=\"sys_platform == 'linux'\"},"
            " {workspace=true, marker=\"sys_platform != 'linux'\"}]\n",
            "",
            True,
        ),
        # Names compared as uv compares them.
        ("dependencies=['Common>=1']\n[tool.uv.sources]\nCOMMON={workspace=true}\n", "", True),
        ("dependencies=['common @ git+https://example.invalid/c']\n[tool.uv.sources]\ncommon={git='https://example.invalid/c'}\n", "", False),
    ],
    ids=[
        "own-source",
        "root-source",
        "index-not-workspace",
        "source-not-required",
        "dev-group-not-installed",
        "default-group",
        "other-group-not-installed",
        "extra",
        "list-of-sources",
        "names-normalised",
        "git-source",
    ],
)
def test_a_member_is_looked_in_only_when_uv_installs_it(tmp_path, agent, sources, found):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n' + sources,
            "agents/a/pyproject.toml": "[project]\nname='agent-a'\nversion='1'\n" + agent,
            "agents/a/agent.py": "from common import llm\n",
            "libs/common/pyproject.toml": "[project]\nname='common'\nversion='1'\n",
            "libs/common/common/__init__.py": "",
            "libs/common/common/llm.py": "",
        },
    )
    llm = (repo / "libs" / "common" / "common" / "llm.py").resolve()

    assert (llm in imported_by_agent(repo, "agents/a", lookup_limit=100_000)) is found


@pytest.mark.parametrize("layout", ["src", "flat"])
def test_a_package_from_a_path_source_inside_the_repository_is_followed(tmp_path, layout):
    from fi.alk.harness.credentials import imported_by_agent

    # Reviewer A: not a workspace member, but `uv sync` installs it from the path.
    package = "libs/common/src/common" if layout == "src" else "libs/common/common"
    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/pyproject.toml": "[project]\nname='agent-a'\nversion='1'\ndependencies=['common']\n"
            "[tool.uv.sources]\ncommon={path='../../libs/common', editable=true}\n",
            "agents/a/agent.py": "from common import llm\n",
            "libs/common/pyproject.toml": "[project]\nname='common'\nversion='1'\n",
            f"{package}/__init__.py": "",
            f"{package}/llm.py": "",
        },
    )

    assert (repo / package / "llm.py").resolve() in imported_by_agent(repo, "agents/a", lookup_limit=100_000)


def test_a_path_source_out_of_the_repository_isnt_followed(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    _tree(tmp_path / "out", {"pyproject.toml": "[project]\nname='common'\n", "common/llm.py": ""})
    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/pyproject.toml": "[project]\nname='agent-a'\nversion='1'\ndependencies=['common']\n"
            "[tool.uv.sources]\ncommon={path='../../../out'}\n",
            "agents/a/agent.py": "from common import llm\n",
        },
    )

    looked: list[str] = []
    real_lstat, real_scandir = Path.lstat, credentials_module().os.scandir
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Path, "lstat", lambda self: looked.append(str(self)) or real_lstat(self))
        patch.setattr(
            credentials_module().os, "scandir", lambda path: looked.append(str(path)) or real_scandir(path)
        )
        assert imported_by_agent(repo, "agents/a", lookup_limit=100_000) == frozenset()
    inside = str(repo.resolve())
    assert not [path for path in looked if not (os.path.normpath(path) + "/").startswith(inside + "/")]


def credentials_module():
    from fi.alk.harness import credentials

    return credentials


def test_an_agent_folder_below_its_project_looks_in_the_projects_src(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/pyproject.toml": "[project]\nname='agent-a'\nversion='1'\n",
            "agents/a/app/main.py": "from a_pkg import llm\n",
            "agents/a/src/a_pkg/__init__.py": "",
            "agents/a/src/a_pkg/llm.py": "",
        },
    )

    assert (repo / "agents" / "a" / "src" / "a_pkg" / "llm.py").resolve() in imported_by_agent(
        repo, "agents/a/app", lookup_limit=100_000
    )


def test_a_member_is_looked_in_only_for_its_own_package_names(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    # Reviewer A: 32 members, and every name the agent imports tried each one's two places.
    names = [f"m{n}" for n in range(32)]
    files = {
        "pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n[tool.uv.sources]\n'
        + "".join(f"{name}={{workspace=true}}\n" for name in names),
        "agents/a/pyproject.toml": f"[project]\nname='a'\nversion='1'\ndependencies={names!r}\n",
        "agents/a/agent.py": "import os\nimport m7.core\n",
    }
    files |= {f"libs/{name}/pyproject.toml": f"[project]\nname='{name}'\nversion='1'\n" for name in names}
    files |= {f"libs/{name}/{name}/__init__.py": "" for name in names}
    files["libs/m7/m7/core.py"] = ""
    repo = _tree(tmp_path / "repo", files)
    tried: list[Path] = []
    real = credentials._file_inside
    monkeypatch.setattr(credentials, "_file_inside", lambda place, *rest: tried.append(place) or real(place, *rest))

    found = credentials.imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    assert (repo / "libs" / "m7" / "m7" / "core.py").resolve() in found
    # `os` is in no member, and `m7` only in its own.
    assert not [place for place in tried if "libs" in place.parts and "m7" not in place.parts]


def test_a_name_past_the_depth_cut_builds_no_places(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    # Reviewer A: a name of 63 parts was joined to every place, uncharged, before it was refused.
    deep = ".".join(["a"] * 63)
    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "".join(f"import q{n}.{deep}\n" for n in range(200))})
    deepest = len(repo.resolve().parts) + credentials._DEEPEST_FOLLOWED
    joined: list[int] = []
    real = Path.joinpath
    monkeypatch.setattr(
        Path,
        "joinpath",
        lambda self, *parts: (len(parts) > 1 and joined.append(len(self.parts) + len(parts)))
        or real(self, *parts),
    )

    credentials.imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    # A name's 64 parts joined to a place: from the root it fits the cut, so it's looked for
    # there; from the folder it doesn't, so it isn't built.
    assert joined and max(joined) <= deepest


def test_more_members_than_are_read_says_the_walk_stopped(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    monkeypatch.setattr(credentials, "_WORKSPACE_MEMBERS", 3)
    files = {"pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n', "agents/a/agent.py": ""}
    files |= {f"libs/m{n}/pyproject.toml": "" for n in range(5)}
    repo = _tree(tmp_path / "repo", files).resolve()

    assert credentials._workspace_dependencies(repo, repo / "agents" / "a", [1e9], {})[2]


def test_a_long_pattern_through_a_link_to_its_own_folder_is_bounded(tmp_path):
    from fi.alk.harness import credentials

    # Reviewer A: each base, memoised or not, counts toward the entries looked at.
    pattern = "*/" + "/".join(["a"] * 2_000)
    files = {"pyproject.toml": f'[tool.uv.workspace]\nmembers=["{pattern}"]\n', "agents/a/agent.py": ""}
    repo = _tree(tmp_path / "repo", files).resolve()
    for n in range(50):
        (repo / f"d{n}").mkdir()
        (repo / f"d{n}" / "a").symlink_to(".")
    started = time.monotonic()

    assert credentials._workspace_dependencies(repo, repo / "agents" / "a", [1e9], {})[2]
    assert time.monotonic() - started < 2


def test_the_walk_up_the_parents_stops_once_it_is_cut(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    monkeypatch.setattr(credentials, "_PYPROJECTS_READ", 10)
    repo = _tree(
        tmp_path / "repo",
        {"pyproject.toml": "[tool.uv.workspace]\nmembers=['a/*']\n", "a/b/pyproject.toml": "[project]\nname='b' # padding\n"},
    ).resolve()
    read: list[Path] = []
    real = credentials._Workspace.table
    monkeypatch.setattr(credentials._Workspace, "table", lambda self, folder: read.append(folder) or real(self, folder))

    assert credentials._workspace_dependencies(repo, repo / "a" / "b", [1e9], {}) == (None, [], True)
    # b's file is past the total, so nothing above it is read.
    assert read == [repo / "a" / "b"]


def test_the_extras_a_requirement_asks_of_a_member_are_followed_too(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n'
            "[tool.uv.sources]\ncommon={workspace=true}\nhelper={workspace=true}\nunused={workspace=true}\n",
            "agents/a/pyproject.toml": "[project]\nname='a'\nversion='1'\ndependencies=['common[llm]']\n",
            "agents/a/agent.py": "import helper.x\nimport unused.x\n",
            "libs/common/pyproject.toml": "[project]\nname='common'\nversion='1'\n"
            "[project.optional-dependencies]\nllm=['helper']\nother=['unused']\n",
            "libs/helper/pyproject.toml": "[project]\nname='helper'\nversion='1'\n",
            "libs/helper/helper/__init__.py": "",
            "libs/helper/helper/x.py": "",
            "libs/unused/pyproject.toml": "[project]\nname='unused'\nversion='1'\n",
            "libs/unused/unused/__init__.py": "",
            "libs/unused/unused/x.py": "",
        },
    )
    found = imported_by_agent(repo, "agents/a", lookup_limit=100_000)

    assert (repo / "libs" / "helper" / "helper" / "x.py").resolve() in found
    # common's other extra isn't asked for, so its package isn't installed.
    assert (repo / "libs" / "unused" / "unused" / "x.py").resolve() not in found


def test_a_sibling_agents_package_isnt_this_agents_code(tmp_path):
    # Reviewer A: agents/b is a member, but agent a doesn't depend on it, so `uv run` doesn't
    # install it, and its `mcp` package is no import of a's.
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*"]\n',
            "agents/a/pyproject.toml": "[project]\nname='agent-a'\nversion='1'\ndependencies=['mcp']\n",
            "agents/a/agent.py": "from mcp import ClientSession\n",
            "agents/b/pyproject.toml": "[project]\nname='agent-b'\nversion='1'\n",
            "agents/b/mcp/__init__.py": "",
            "agents/b/mcp/tools.py": "",
        },
    )

    assert imported_by_agent(repo, "agents/a", lookup_limit=100_000) == frozenset()


def test_the_walk_follows_members_a_member_depends_on_and_the_workspace_roots_own_package(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "services/pyproject.toml": "[project]\nname='platform'\nversion='1'\n"
            "dependencies=['core']\n[tool.uv.workspace]\nmembers=['agents/*', 'libs/*']\n"
            "[tool.uv.sources]\ncore={workspace=true}\nplatform={workspace=true}\n",
            "services/src/platform/__init__.py": "",
            "services/agents/a/pyproject.toml": "[project]\nname='agent-a'\nversion='1'\n"
            "dependencies=['platform']\n",
            "services/agents/a/agent.py": "import platform.app\nimport core.db\n",
            "services/src/platform/app.py": "",
            "services/libs/core/pyproject.toml": "[project]\nname='core'\nversion='1'\n",
            "services/libs/core/core/__init__.py": "",
            "services/libs/core/core/db.py": "",
        },
    )
    found = imported_by_agent(repo, "services/agents/a", lookup_limit=100_000)

    assert (repo / "services" / "src" / "platform" / "app.py").resolve() in found
    # platform asks for core, so uv installs it with a.
    assert (repo / "services" / "libs" / "core" / "core" / "db.py").resolve() in found


def test_a_member_matched_by_a_glob_may_be_a_link(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n',
            "agents/a/pyproject.toml": _DEPENDS_ON_COMMON,
            "agents/a/agent.py": "from common import llm\n",
            "vendor/common/pyproject.toml": "[project]\nname='common'\nversion='1'\n",
            "vendor/common/common/__init__.py": "",
            "vendor/common/common/llm.py": "",
        },
    )
    (repo / "libs").mkdir()
    (repo / "libs" / "common").symlink_to("../vendor/common")

    assert (repo / "vendor" / "common" / "common" / "llm.py").resolve() in imported_by_agent(
        repo, "agents/a", lookup_limit=100_000
    )


@pytest.mark.parametrize("shape", ["bom", "nested"])
def test_an_odd_pyproject_is_read_as_uv_reads_it_or_left_unread(tmp_path, shape):
    from fi.alk.harness.credentials import imported_by_agent

    workspace = '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n'
    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/pyproject.toml": _DEPENDS_ON_COMMON,
            "agents/a/agent.py": "from common import llm\n",
            "libs/common/pyproject.toml": "[project]\nname='common'\nversion='1'\n",
            "libs/common/common/__init__.py": "",
            "libs/common/common/llm.py": "",
        },
    )
    if shape == "bom":
        (repo / "pyproject.toml").write_bytes(b"\xef\xbb\xbf" + workspace.encode())
    else:
        # Nested past the parser's recursion limit: refused, not raised.
        (repo / "pyproject.toml").write_text("a = " + "[" * 5_000 + "]" * 5_000 + "\n")
    found = imported_by_agent(repo, "agents/a", lookup_limit=100_000)

    assert ((repo / "libs" / "common" / "common" / "llm.py").resolve() in found) is (shape == "bom")


def test_the_pyprojects_read_up_the_folders_parents_share_one_total(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    # Reviewer A's H1: 250 folders, each with a link to one large pyproject.toml, parsed each time.
    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    (repo / "big.toml").write_text("a = [" + ",".join("1" for _ in range(100_000)) + "]\n")
    folder = repo
    for _ in range(40):
        folder = folder / "d"
        folder.mkdir()
        (folder / "pyproject.toml").symlink_to(repo / "big.toml")
    parsed: list[int] = []
    import tomllib

    real = tomllib.loads
    monkeypatch.setattr(tomllib, "loads", lambda text: parsed.append(len(text)) or real(text))

    _, followed, cut = credentials._workspace_dependencies(repo, folder, [1e9], {})
    assert followed == [] and cut
    assert sum(parsed) <= credentials._PYPROJECTS_READ


@pytest.mark.parametrize("budget", [80.0, 150.0, 200.0, 260.0])
def test_the_member_globs_stop_when_the_lookups_are_spent(tmp_path, monkeypatch, budget):
    from fi.alk.harness import credentials

    files = {"pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "*/*"]\n', "agents/a/agent.py": ""}
    files |= {f"top{n}/sub/x.py": "" for n in range(60)}
    repo = _tree(tmp_path / "repo", files).resolve()
    left = [budget]
    listed_with: list[float] = []
    real = credentials.os.scandir
    monkeypatch.setattr(credentials.os, "scandir", lambda path: listed_with.append(left[0]) or real(path))

    _, followed, cut = credentials._workspace_dependencies(repo, repo / "agents" / "a", left, {})
    assert cut and left[0] <= 0
    # Not one listing once the budget ran out, and nothing looked up past it.
    assert listed_with and min(listed_with) > 0
    assert left[0] > -3


def test_no_pyproject_is_read_once_the_lookups_are_spent(tmp_path):
    from fi.alk.harness.credentials import _Workspace

    repo = _tree(tmp_path / "repo", {"pyproject.toml": "[project]\nname='x'\n"}).resolve()
    left = [0.0]
    reader = _Workspace(repo, left, {})

    assert reader.table(repo) is None and reader.cut
    assert left == [0.0]


def test_each_folder_up_to_the_root_is_charged_for_its_pyproject(tmp_path):
    from fi.alk.harness.credentials import _workspace_dependencies

    repo = _tree(tmp_path / "repo", {"a/b/c/x.py": ""}).resolve()
    left = [100.0]

    assert _workspace_dependencies(repo, repo / "a" / "b" / "c", left, {}) == (None, [], False)
    # c, b, a and the root.
    assert left == [96.0]


def test_script_names_stop_being_tried_once_the_lookups_are_spent(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    names = "".join(f'import "../../gone/m{n}";\n' for n in range(300))
    repo = _tree(tmp_path / "repo", {"agents/a/index.js": names})
    (repo / "gone").symlink_to("nowhere")
    said: list[bool] = []

    imported_by_agent(repo, "agents/a", lookup_limit=200, stopped=said)
    assert said == [True]


def test_a_name_tries_no_more_places_once_the_lookups_are_spent(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    # Reviewer A's H2: every name went on through every member's places, uncharged.
    names = [f"m{n}" for n in range(30)]
    files = {
        "pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n[tool.uv.sources]\n'
        + "".join(f"{name}={{workspace=true}}\n" for name in names),
        "agents/a/pyproject.toml": f"[project]\nname='a'\nversion='1'\ndependencies={names!r}\n",
        "agents/a/agent.py": "".join(f"import n{n}\n" for n in range(2_000)),
    }
    files |= {f"libs/{name}/pyproject.toml": f"[project]\nname='{name}'\nversion='1'\n" for name in names}
    repo = _tree(tmp_path / "repo", files)
    built: list[int] = []
    real = Path.with_suffix
    monkeypatch.setattr(Path, "with_suffix", lambda self, suffix: built.append(1) or real(self, suffix))

    credentials.imported_by_agent(repo, "agents/a", lookup_limit=1_000)
    # 64 places a name: without the stop, every one of the 2,000 names builds them all.
    assert len(built) < 2_000


def test_a_stopped_save_walk_is_logged(tmp_path, caplog):
    from fi.alk.harness import agent_type_check

    names = "".join(f"import gone.m{n}\n" for n in range(300))
    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": names})
    (repo / "gone").symlink_to("nowhere")
    with caplog.at_level("WARNING", logger=agent_type_check.__name__):
        agent_type_check._walked(repo.resolve(), "agents/a")

    assert any("stopped at its limits" in record.getMessage() for record in caplog.records) is False
    with caplog.at_level("WARNING", logger=agent_type_check.__name__):
        import fi.alk.harness.agent_type_check as module

        original = module._WALKED_LOOKUPS
        module._WALKED_LOOKUPS = 200
        try:
            module._walked(repo.resolve(), "agents/a")
        finally:
            module._WALKED_LOOKUPS = original
    assert any("stopped at its limits" in record.getMessage() for record in caplog.records)


def test_a_workspace_pattern_out_of_the_root_isnt_listed(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    repo = _tree(
        tmp_path / "repo",
        {"pyproject.toml": '[tool.uv.workspace]\nmembers=["../*", "/*"]\n', "agents/a/agent.py": "x = 1\n"},
    )
    listed: list[str] = []
    real = credentials.os.scandir
    monkeypatch.setattr(credentials.os, "scandir", lambda path: listed.append(str(path)) or real(path))

    credentials.imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    assert not [path for path in listed if not Path(path).resolve().is_relative_to(repo.resolve())]


def test_the_walk_follows_at_most_so_many_workspace_members(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    monkeypatch.setattr(credentials, "_WORKSPACE_DEPENDENCIES", 3)
    names = [f"m{n}" for n in range(5)]
    files = {
        "pyproject.toml": '[tool.uv.workspace]\nmembers=["agents/*", "libs/*"]\n[tool.uv.sources]\n'
        + "".join(f"{name}={{workspace=true}}\n" for name in names),
        "agents/a/pyproject.toml": f"[project]\nname='a'\nversion='1'\ndependencies={names!r}\n",
        "agents/a/agent.py": "x = 1\n",
    }
    files |= {f"libs/{name}/pyproject.toml": f"[project]\nname='{name}'\nversion='1'\n" for name in names}
    repo = _tree(tmp_path / "repo", files)
    said: list[bool] = []

    credentials.imported_by_agent(repo, "agents/a", lookup_limit=100_000, stopped=said)
    assert said == [True]
    _, followed, cut = credentials._workspace_dependencies(
        repo.resolve(), (repo / "agents" / "a").resolve(), [1e9], {}
    )
    assert len(followed) == 3 and cut


def test_the_save_follows_an_import_through_a_folder_link_as_bundling_does(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {"agents/a/agent.py": "import shared.web\n", "common/__init__.py": "", "common/web.py": ""},
    )
    (repo / "shared").symlink_to("common")
    web = (repo / "common" / "web.py").resolve()

    # The link is followed to its target inside the repository, its lookups charged (Dileep's
    # third review: instrumentation and the browser check are derived from this reach).
    assert web in imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    assert web in imported_by_agent(repo, "agents/a")


@pytest.mark.parametrize("absolute", [True, False])
def test_the_save_does_not_follow_a_folder_link_out_of_the_repository(tmp_path, absolute):
    from fi.alk.harness.credentials import imported_by_agent

    outside = _tree(tmp_path / "outside", {"web.py": "", "__init__.py": ""})
    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "import shared.web\n"})
    (repo / "shared").symlink_to(outside if absolute else "../outside")

    assert imported_by_agent(repo, "agents/a", lookup_limit=100_000) == frozenset()


def test_the_save_follows_a_link_two_folders_up_looking_each_folder_up_once(tmp_path, monkeypatch):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/agent.py": "import shared.deep.web\nimport shared.deep.page\n",
            "common/__init__.py": "",
            "common/deep/__init__.py": "",
            "common/deep/web.py": "",
            "common/deep/page.py": "",
        },
    )
    (repo / "shared").symlink_to("common")
    web = (repo / "common" / "deep" / "web.py").resolve()
    looked: Counter[Path] = Counter()
    real = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda self: looked.update([self]) or real(self))
    # The candidate's own folder isn't a link, the one above it is.
    assert web in imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    folders = {path: count for path, count in looked.items() if path.is_dir()}
    assert folders and max(folders.values()) == 1


@pytest.mark.parametrize("target", ["loop", "missing", "file"])
def test_the_save_takes_a_folder_link_it_cant_follow_as_a_miss(tmp_path, target):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "import shared.web\n", "notes.txt": ""})
    if target == "loop":
        (repo / "other").symlink_to("shared")
        (repo / "shared").symlink_to("other")
    elif target == "missing":
        (repo / "shared").symlink_to("nowhere")
    else:
        (repo / "shared").symlink_to("notes.txt")

    assert imported_by_agent(repo, "agents/a", lookup_limit=100_000) == frozenset()


def test_the_save_follows_a_link_out_of_the_repository_and_back_in(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {"agents/a/agent.py": "import shared.web\n", "common/__init__.py": "", "common/web.py": ""},
    )
    (repo / "shared").symlink_to("../repo/common")

    assert (repo / "common" / "web.py").resolve() in imported_by_agent(
        repo, "agents/a", lookup_limit=100_000
    )


def test_a_name_under_a_link_that_leads_nowhere_still_spends_the_lookups(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    names = "".join(f"import gone.m{n}\n" for n in range(300))
    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": names})
    (repo / "gone").symlink_to("nowhere")
    said: list[bool] = []

    # The folder is looked up once, but each name tried under it is still charged a miss.
    imported_by_agent(repo, "agents/a", lookup_limit=200, stopped=said)
    assert said == [True]


def test_a_candidate_past_the_depth_cut_isnt_walked_folder_by_folder(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    deep = "/".join(["a"] * 2_000)
    repo = _tree(tmp_path / "repo", {"agents/a/index.js": f'import "./{deep}/x";\n'})
    deepest = len(repo.resolve().parts) + credentials._DEEPEST_FOLLOWED
    walked: list[int] = []
    real = credentials._real_folder
    monkeypatch.setattr(
        credentials,
        "_real_folder",
        lambda folder, *rest: walked.append(len(folder.parts)) or real(folder, *rest),
    )

    credentials.imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    # Each would keep a path for each of its 2,000 folders, for every such name.
    assert all(parts < deepest for parts in walked)


def test_each_folder_and_each_link_is_charged_once(tmp_path):
    from fi.alk.harness.credentials import _real_folder

    repo = _tree(tmp_path / "repo", {"real/deep/x.py": ""}).resolve()
    (repo / "link").symlink_to("real")
    known: dict = {}
    left = [100.0]

    # One for each folder below the root.
    assert _real_folder(repo / "real" / "deep", repo, known, left) == repo / "real" / "deep"
    assert left == [98.0]
    # The link: one for it, one for its target, whose folder is known already.
    assert _real_folder(repo / "link" / "deep", repo, known, left) == repo / "real" / "deep"
    assert left == [95.0]
    # Known: nothing more.
    assert _real_folder(repo / "link" / "deep", repo, known, left) == repo / "real" / "deep"
    assert left == [95.0]


def test_the_save_follows_a_script_import_up_a_folder_and_through_a_link(tmp_path):
    # Reviewer B's M1: a literal ".." from the import, then a link that climbs with ".." itself.
    from fi.alk.harness.agent_type_check import browser_tools_in_agent
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/index.ts": 'import "../shared/llm";\n',
            "libs/common/llm.ts": 'import { chromium } from "playwright";\n',
            "libs/common/package.json": '{"dependencies": {"playwright": "1.48.0"}}',
        },
    )
    (repo / "agents" / "shared").symlink_to("../libs/common")

    assert (repo / "libs" / "common" / "llm.ts").resolve() in imported_by_agent(
        repo, "agents/a", lookup_limit=100_000
    )
    assert browser_tools_in_agent(repo, "agents/a") == ["playwright"]


def test_a_path_outside_the_repository_isnt_mapped_into_it(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    # An absolute import of a file outside, named as one inside is: not that one.
    repo = _tree(
        tmp_path / "repo",
        {"agents/a/index.js": 'import x from "/etc/zz_not_here.js";\n', "zz_not_here.js": ""},
    )

    assert imported_by_agent(repo, "agents/a", lookup_limit=100_000) == frozenset()


def test_each_folder_on_the_way_to_a_candidate_is_walked_once(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    repo = _tree(
        tmp_path / "repo",
        {"agents/a/agent.py": "".join(f"import p.q.r.m{n}\n" for n in range(50)), "p/q/r/m0.py": ""},
    )
    (repo / "link").symlink_to("p")
    stepped: list[Path] = []
    real = credentials._step
    monkeypatch.setattr(credentials, "_step", lambda name, *rest: stepped.append(name) or real(name, *rest))

    credentials.imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    # Each folder from the root down is stepped through once, whatever the number of names in it.
    assert len(stepped) == len(set(stepped))


def test_a_scripts_dot_dot_after_a_link_is_taken_as_node_takes_it(tmp_path):
    # Node joins "../shared/../x" as written before it reads the disk, so the ".." after the link
    # leaves the link, not its target.
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {"agents/a/index.ts": 'import "../shared/../x";\n', "agents/x.ts": "", "libs/x.ts": "", "libs/common/y.ts": ""},
    )
    (repo / "agents" / "shared").symlink_to("../libs/common")
    found = imported_by_agent(repo, "agents/a", lookup_limit=100_000)

    assert (repo / "agents" / "x.ts").resolve() in found
    assert (repo / "libs" / "x.ts").resolve() not in found


def test_the_save_looks_up_nothing_outside_the_repository(tmp_path, monkeypatch):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(
        tmp_path / "repo",
        {
            "agents/a/index.js": 'import hosts from "/etc/hosts";\nimport up from "../../../../x";\n'
            'import far from "./up/etc/hosts";\nimport side from "./side/y";\n'
        },
    )
    # Links whose targets climb out of the repository, the second into a folder beside it.
    (repo / "agents" / "a" / "up").symlink_to("../../../../..")
    (repo / "agents" / "a" / "side").symlink_to("../../../other/x")
    looked: list[Path] = []
    real = Path.lstat
    monkeypatch.setattr(Path, "lstat", lambda self: looked.append(self) or real(self))

    imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    # As the kernel reads each path: "a/../../.." is above the repository, however it's written.
    inside = str(repo.resolve())
    assert not [
        path for path in looked if not (os.path.normpath(path) + "/").startswith(inside + "/")
    ]


@pytest.mark.parametrize("links, found", [(40, True), (41, False)])
def test_a_chain_of_links_is_followed_up_to_the_kernels_limit(tmp_path, links, found):
    from fi.alk.harness.credentials import imported_by_agent

    repo = _tree(tmp_path / "repo", {"agents/a/agent.py": "import l0.web\n", "real/web.py": ""})
    for n in range(links):
        (repo / f"l{n}").symlink_to(f"l{n + 1}" if n < links - 1 else "real")

    assert ((repo / "real" / "web.py").resolve() in imported_by_agent(
        repo, "agents/a", lookup_limit=100_000
    )) is found


def test_a_links_target_is_charged_for_each_of_its_parts(tmp_path):
    from fi.alk.harness.credentials import _real_folder

    repo = (tmp_path / "repo").resolve()
    (repo / "a" / "b").mkdir(parents=True)
    (repo / "l").symlink_to("a/./b/../b")
    left = [100.0]

    assert _real_folder(repo / "l", repo, {}, left) == repo / "a" / "b"
    # The link, its read, its target's 4 parts (Path drops the ".", keeps the ".."), then a and b,
    # each looked up once.
    assert left == [92.0]


def test_a_link_whose_target_is_past_so_many_parts_isnt_followed(tmp_path):
    from fi.alk.harness import credentials

    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    (repo / "x").mkdir()
    parts = credentials._LONGEST_TARGET
    (repo / "ok").symlink_to("/".join(["."] + [".."] * 0 + ["x"] * 1))
    (repo / "long").symlink_to("/".join(["x", ".."] * (parts // 2) + ["x"]))
    (repo / "short").symlink_to("/".join(["x", ".."] * (parts // 2 - 1) + ["x"]))

    assert credentials._real_folder(repo / "ok", repo, {}, [1e9]) == repo / "x"
    assert credentials._real_folder(repo / "short", repo, {}, [1e9]) == repo / "x"
    assert credentials._real_folder(repo / "long", repo, {}, [1e9]) is None


def test_what_the_chain_limit_cuts_isnt_kept_for_a_shorter_chain(tmp_path):
    from fi.alk.harness import credentials

    # Reviewer B: reached at the end of a long chain, a link is cut by the limit; reached first,
    # it's followed. The cut answer mustn't stand for it.
    repo = (tmp_path / "repo").resolve()
    (repo / "real").mkdir(parents=True)
    (repo / "x").symlink_to("real")
    for n in range(credentials._LINKS_FOLLOWED):
        (repo / f"c{n}").symlink_to(f"c{n + 1}" if n < credentials._LINKS_FOLLOWED - 1 else "x")
    known: dict = {}

    assert credentials._real_folder(repo / "c0", repo, known, [1e9]) is None
    assert credentials._real_folder(repo / "x", repo, known, [1e9]) == repo / "real"


@pytest.mark.parametrize("where, read", [("inside", True), ("outside", False), ("chain", False)])
def test_a_pyproject_that_is_a_link_is_read_only_inside_the_repository(tmp_path, where, read):
    from fi.alk.harness.credentials import _Workspace

    repo = _tree(tmp_path / "repo", {"shared/pyproject.toml": "[project]\nname='x'\n"}).resolve()
    _tree(tmp_path / "out", {"pyproject.toml": "[project]\nname='x'\n"})
    (repo / "a").mkdir()
    if where == "inside":
        (repo / "a" / "pyproject.toml").symlink_to("../shared/pyproject.toml")
    elif where == "outside":
        (repo / "a" / "pyproject.toml").symlink_to(tmp_path / "out" / "pyproject.toml")
    else:
        (repo / "shared" / "hop.toml").symlink_to("pyproject.toml")
        (repo / "a" / "pyproject.toml").symlink_to("../shared/hop.toml")

    assert (_Workspace(repo, [1e9], {}).table(repo / "a") is not None) is read


def test_a_python_name_once_the_lookups_are_spent_says_the_walk_stopped(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    # Plain folders, no links: the budget runs out on a.py's lookups, and b.py's names are the
    # ones left untried.
    repo = _tree(
        tmp_path / "repo",
        {"agents/a/a.py": "".join(f"import n{n}\n" for n in range(40)), "agents/a/b.py": "import os\n"},
    )
    said: list[bool] = []
    imported_by_agent(repo, "agents/a", lookup_limit=60, stopped=said)

    assert said == [True]


def test_a_link_loop_costs_a_few_lookups_not_the_chain_limit(tmp_path):
    from fi.alk.harness.credentials import _real_folder

    repo = (tmp_path / "repo").resolve()
    repo.mkdir()
    (repo / "one").symlink_to("two")
    (repo / "two").symlink_to("one")
    left = [1_000.0]

    assert _real_folder(repo / "one" / "x", repo, {}, left) is None
    # Marked before it's followed, so the loop is seen at once.
    assert left[0] > 990


def test_the_save_follows_a_chain_of_links_once_each(tmp_path, monkeypatch):
    from fi.alk.harness import credentials

    files = {"agents/a/agent.py": "".join(f"import l0.m{n}\n" for n in range(200))}
    files |= {f"real/m{n}.py": "" for n in range(200)}
    repo = _tree(tmp_path / "repo", files)
    for n in range(30):
        (repo / f"l{n}").symlink_to(f"l{n + 1}" if n < 29 else "real")
    read: Counter[str] = Counter()
    real_readlink = os.readlink
    monkeypatch.setattr(
        credentials.os, "readlink", lambda path: read.update([str(path)]) or real_readlink(path)
    )

    found = credentials.imported_by_agent(repo, "agents/a", lookup_limit=100_000)
    assert (repo / "real" / "m199.py").resolve() in found
    # Each link read once in the whole walk, however many imports go through it.
    assert read and max(read.values()) == 1


def test_a_script_import_naming_thousands_of_folders_does_not_raise_at_the_save(tmp_path):
    from fi.alk.harness.credentials import imported_by_agent

    deep = "/".join(["a"] * 2_000)
    repo = _tree(tmp_path / "repo", {"agents/a/index.js": f'import "./{deep}/x";\n'})
    assert imported_by_agent(repo, "agents/a", lookup_limit=100_000) is not None
