"""A browser scan that leaves code unread or unparsed says so, and the reading gets a second look.

Finding nothing in code the scan didn't read isn't finding nothing: padding read first, or an
honest repository larger than the limits, would otherwise hide a plain ``import playwright``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import pytest

from fi.alk.harness import agent_type_check, python_source, tools
from fi.alk.harness.agent_type_check import (
    SecondLook,
    browser_scan,
    browser_tools_found,
    browser_tools_in_agent,
    decide,
)
from fi.alk.harness.failure_file import FAILURE_FILE
from fi.alk.harness.python_source import LARGEST_PARSED_MODULE
from fi.alk.harness.tools import contract_tools
from fi.alk.harness.understand import load

_READ = "read-it"
_NOT_ACCEPTED = "Not accepted. Fix all of these and call submit_contract again:\n  - "
_RECEIPT = f' When you submit again, set second_look_read to "{_READ}", which shows you read this.'
_CUT_CHAT = (
    "second-look: you recorded modality {claimed}, and the code check stopped before reading all "
    "of the agent's code (it holds more than the check reads, or a file it couldn't read), so it "
    "can't say whether a browser, crawling or computer-use tool is used. A chat or voice agent "
    "can use one in its tests or a helper script. If a person comes to this agent for a "
    "conversation, submit again as is. If its main job is carrying out work on websites or a "
    "computer, submit again as browser, which stops this build as not supported. Decide this "
    "from the source; don't ask anyone."
)
_CUT_BROWSER = (
    "second-look: you recorded modality browser, which stops this build as not supported, and "
    "the code check stopped before reading all of the agent's code (it holds more than the check "
    "reads, or a file it couldn't read), so it can't say whether a browser, crawling or "
    "computer-use tool is used. A chat or voice agent can use one in its tests or a helper "
    "script. Does a person come to this agent for a conversation, or to have it carry out work on "
    "websites or a computer? For a conversation, submit again as chat or voice. If you're sure, "
    "submit again as browser. Decide this from the source; don't ask anyone."
)
_FOUND_CHAT = (
    "second-look: you recorded modality {claimed}, and the source uses playwright. If that's a "
    "helper and a person comes to this agent for a conversation, submit again as is. If its main "
    "job is carrying out work on websites or a computer, submit again as browser, which stops "
    "this build as not supported. Decide this from the source; don't ask anyone."
)
_FOUND_BROWSER = (
    "second-look: you recorded modality browser, which stops this build as not supported, and "
    "the source uses playwright. A chat or voice agent can list one for its tests or use one in "
    "a helper script. Does a person come to this agent for a conversation, or to have it carry "
    "out work on websites or a computer? For a conversation, submit again as chat or voice. If "
    "its main job is that work, submit again as browser. Decide this from the source; don't ask "
    "anyone."
)
_NOTHING_FOUND_BROWSER = (
    "second-look: you recorded modality browser, which stops this build as not supported, and "
    "no browser, crawling or computer-use tool was found in the source. Does a person come to "
    "this agent for a conversation, or to have it carry out work on websites or a computer? For "
    "a conversation, submit again as chat or voice. If you're sure, submit again as browser. "
    "Decide this from the source; don't ask anyone."
)
_FOUND_MESSAGE = (
    "This agent's main job is driving a browser, operating a computer or crawling sites "
    "(found: playwright). Only voice and chat agents are supported for now."
)
_READ_MESSAGE = (
    "This agent's main job was read as driving a browser, operating a computer or crawling "
    "sites. Only voice and chat agents are supported for now."
)
# Over the size a .py is read at, so never read.
_OVERSIZED = "#" * (LARGEST_PARSED_MODULE + 1)


@pytest.fixture(autouse=True)
def _nothing_reaches_a_network(monkeypatch):
    for name in (
        "FI_BASE_URL",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
    ):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("FI_API_KEY", raising=False)
    monkeypatch.delenv("FI_SECRET_KEY", raising=False)


@pytest.fixture(autouse=True)
def _a_known_receipt(monkeypatch):
    monkeypatch.setattr(tools, "_new_receipt", lambda: _READ)


def _tree(root: Path, files: dict[str, str | bytes]) -> Path:
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
    return root


def _reading(modality: str | None = "chat", **fields):
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


def _submitter(destination: Path, scan_root: Path, subfolder: str | None = None):
    server = contract_tools(destination, scan_root=scan_root, subfolder=subfolder)
    tool = next(one for one in server.tools if one.name == "submit_contract")
    return lambda payload: asyncio.run(tool.handler(payload))


def _text(answer) -> str:
    return answer["content"][0]["text"]


def _look(text: str) -> str:
    return _NOT_ACCEPTED + text + _RECEIPT


def _refused_with(output: Path) -> str:
    assert not (output / "contract.json").exists()
    return json.loads((output / FAILURE_FILE).read_text(encoding="utf-8"))["message"]


def _raise_for_agent(monkeypatch):
    real = agent_type_check.read_module

    def odd(path, *args, **kwargs):
        if path.name == "agent.py":
            raise LookupError("an error no reader expects")
        return real(path, *args, **kwargs)

    monkeypatch.setattr(agent_type_check, "read_module", odd)


# Totals set low, so a few bytes reach each limit as a large repository would.
_LIMITS = [
    pytest.param(
        "file_size",
        {"agent.py": "import playwright\n".ljust(LARGEST_PARSED_MODULE + 1, "#")},
        {},
        id="file_size-python",
    ),
    pytest.param(
        "file_size",
        {"agent.js": "const tool = 'computer-use-preview'; // past forty bytes\n"},
        {(agent_type_check, "_LARGEST_CODE_FILE"): 40},
        id="file_size-script",
    ),
    pytest.param(
        "scan_total",
        {"agent.py": "import playwright\n"},
        {(agent_type_check, "_LARGEST_SCAN"): 10},
        id="scan_total",
    ),
    pytest.param(
        "script_total",
        {"agent.js": "const tool = 'computer-use-preview'\n"},
        {(agent_type_check, "_LARGEST_SCRIPT_SCAN"): 10},
        id="script_total",
    ),
    pytest.param(
        "python_total",
        {"agent.py": "import playwright\n"},
        {(agent_type_check, "_LARGEST_PYTHON_SCAN"): 10},
        id="python_total",
    ),
    pytest.param(
        "parse_total",
        {"agent.py": "import playwright\n"},
        {(python_source, "LARGEST_PARSED_TOTAL"): 10},
        id="parse_total",
    ),
    pytest.param(
        "parse_failed",
        {"agent.py": "import playwright\ndef (\n"},
        {},
        id="parse_failed",
    ),
    pytest.param(
        "token_total",
        {"agent.py": "TOOL = 'computer_use_preview'\n"},
        {(python_source, "LARGEST_TOKENIZED_TOTAL"): 10},
        id="token_total-total",
    ),
    pytest.param(
        "token_total",
        {"agent.py": "TOOL = 'computer_use_preview'\n"},
        {(python_source, "LARGEST_TOKENIZED_WORK"): 1},
        id="token_total-work",
    ),
    pytest.param(
        "decode_total",
        {"agent.py": b"# coding: utf-7\nimport playwright\n"},
        {(python_source, "LARGEST_ESCAPED_TOTAL"): 0},
        id="decode_total",
    ),
    pytest.param(
        "search_total",
        {"agent.py": b"# coding: idna\nimport playwright\n"},
        {(agent_type_check, "_LARGEST_RAW_SEARCH"): 0},
        id="search_total",
    ),
    pytest.param(
        "skipped", {"agent.py": "import playwright\n"}, _raise_for_agent, id="skipped"
    ),
    pytest.param(
        "package_unreadable",
        {"package.json": b'\xff{"dependencies": {"playwright": "1"}}'},
        {},
        id="package_unreadable",
    ),
]


@pytest.mark.parametrize("limit, files, totals", _LIMITS)
def test_each_limit_cuts_the_scan(tmp_path, monkeypatch, limit, files, totals):
    _tree(tmp_path, files)
    if callable(totals):
        totals(monkeypatch)
    else:
        for (module, name), value in totals.items():
            monkeypatch.setattr(module, name, value)
    names, cut = browser_scan(tmp_path)
    assert cut == frozenset({limit})
    assert decide("chat", names, looked_again=False, cut=cut) == "second_look"


@pytest.mark.parametrize(
    "files, spent, names",
    [
        # Read and charged, the padding would spend the total before the file that imports.
        pytest.param(
            {
                **{f"a_pad_{at}.py": "x = 1\n" * (126 * 1024 // 6) for at in range(3)},
                "z_agent.py": "import playwright\n",
            },
            False,
            ["playwright"],
            id="padding",
        ),
        pytest.param({"helper.py": "x = 1\n"}, True, [], id="needle-free"),
        pytest.param(
            {"agent.py": b"import playwright\n\xff\n"}, True, [], id="undecodable"
        ),
    ],
)
def test_a_file_that_cant_matter_is_no_cut(tmp_path, monkeypatch, files, spent, names):
    _tree(tmp_path, files)
    if spent:
        monkeypatch.setattr(python_source, "LARGEST_PARSED_TOTAL", 0)
    assert browser_scan(tmp_path) == (names, frozenset())


@pytest.mark.parametrize("claimed", ["chat", "voice", "", "browser"])
@pytest.mark.parametrize("found", [[], ["playwright"]])
@pytest.mark.parametrize("looked_again", [False, True])
def test_nothing_cut_is_todays_verdict_and_text(claimed, found, looked_again):
    browser = claimed == "browser"
    if browser:
        today = "refuse" if looked_again else "second_look"
    else:
        today = "second_look" if found and not looked_again else "keep"
    assert decide(claimed, found, looked_again=looked_again) == today
    assert decide(claimed, found, looked_again=looked_again, cut=frozenset()) == today
    if today != "second_look":
        return
    if browser:
        text = _FOUND_BROWSER if found else _NOTHING_FOUND_BROWSER
    else:
        text = _FOUND_CHAT.format(claimed=claimed or "chat")
    assert SecondLook.prompt(claimed, found) == text
    assert SecondLook.prompt(claimed, found, cut=frozenset()) == text
    assert SecondLook().issue(claimed, found, _READ) == text + _RECEIPT


@pytest.mark.parametrize(
    "files, claimed, text",
    [
        (
            {"agent.py": "import playwright\n"},
            "chat",
            _FOUND_CHAT.format(claimed="chat"),
        ),
        ({"agent.py": "import playwright\n"}, "browser", _FOUND_BROWSER),
        ({"agent.py": "import openai\n"}, "browser", _NOTHING_FOUND_BROWSER),
    ],
)
def test_an_uncut_scan_gives_todays_look(tmp_path, files, claimed, text):
    repo = _tree(tmp_path / "source", files)
    assert browser_scan(repo)[1] == frozenset()
    assert _text(_submitter(tmp_path / "out", repo)(_reading(claimed))) == _look(text)


def test_an_uncut_chat_reading_of_plain_code_is_saved_without_a_look(tmp_path):
    repo = _tree(tmp_path / "source", {"agent.py": "import openai\n"})
    answer = _submitter(tmp_path / "out", repo)(_reading("chat"))
    assert not answer.get("is_error"), answer
    assert load(tmp_path / "out").modality == "chat"


def _cut_repo(root: Path, **more: str) -> Path:
    return _tree(root, {"agent.py": "import openai\n", "big.py": _OVERSIZED, **more})


@pytest.mark.parametrize(
    "steps",
    [
        pytest.param([("chat", None, "look"), ("chat", _READ, "saved")], id="chat"),
        pytest.param([("voice", None, "look"), ("voice", _READ, "saved")], id="voice"),
        pytest.param([(None, None, "look"), (None, _READ, "saved")], id="no-modality"),
        pytest.param(
            [("chat", None, "look"), ("browser", _READ, "refused")], id="then-browser"
        ),
        pytest.param(
            [
                ("web", None, "problem"),
                ("chat", None, "look"),
                ("chat", _READ, "saved"),
            ],
            id="unknown-modality-first",
        ),
    ],
)
def test_a_cut_chat_reading_gets_one_look(tmp_path, steps):
    output = tmp_path / "out"
    submit = _submitter(output, _cut_repo(tmp_path / "source"))
    for modality, receipt, then in steps:
        extra = {} if receipt is None else {"second_look_read": receipt}
        answer = submit(_reading(modality, **extra))
        if then == "problem":
            assert answer["is_error"]
            assert "'web' is not a modality" in _text(answer)
        elif then == "look":
            assert _text(answer) == _look(_CUT_CHAT.format(claimed=modality or "chat"))
        elif then == "saved":
            assert not answer.get("is_error"), answer
            assert load(output).modality == (modality or "chat")
            assert not (output / FAILURE_FILE).exists()
        else:
            assert _refused_with(output) == _READ_MESSAGE


def test_a_cut_reading_gets_a_look_on_the_subfolder_route(tmp_path):
    source = tmp_path / "source"
    _cut_repo(source / "agents" / "a")
    output = tmp_path / "out"
    submit = _submitter(output, source, "agents/a")
    answer = submit(_reading("chat"))
    assert _text(answer) == _look(_CUT_CHAT.format(claimed="chat"))


def test_a_package_json_that_isnt_a_file_is_no_cut(tmp_path):
    repo = _tree(tmp_path / "source", {"agent.py": "import openai\n"})
    (repo / "package.json").symlink_to(repo / "nowhere.json")
    assert browser_scan(repo) == ([], frozenset())


@pytest.mark.parametrize(
    "claimed, text, then, outcome",
    [
        ("chat", _FOUND_CHAT.format(claimed="chat"), "chat", "saved"),
        ("chat", _FOUND_CHAT.format(claimed="chat"), "browser", "refused"),
        ("browser", _FOUND_BROWSER, "chat", "saved"),
        ("browser", _FOUND_BROWSER, "browser", "refused"),
    ],
    ids=[
        "chat-then-chat",
        "chat-then-browser",
        "browser-then-chat",
        "browser-then-browser",
    ],
)
def test_cut_and_found_join_in_one_look(tmp_path, claimed, text, then, outcome):
    repo = _cut_repo(tmp_path / "source", **{"agent.py": "import playwright\n"})
    assert browser_scan(repo) == (["playwright"], frozenset({"file_size"}))
    assert (
        SecondLook.prompt(claimed, ["playwright"], cut=frozenset({"file_size"})) == text
    )
    output = tmp_path / "out"
    submit = _submitter(output, repo)
    assert _text(submit(_reading(claimed))) == _look(text)
    answer = submit(_reading(then, second_look_read=_READ))
    if outcome == "saved":
        assert not answer.get("is_error"), answer
        assert load(output).modality == then
    else:
        assert _refused_with(output) == _FOUND_MESSAGE


@pytest.mark.parametrize("then, outcome", [("browser", "refused"), ("chat", "saved")])
def test_a_cut_browser_claim_drops_nothing_found(tmp_path, then, outcome):
    output = tmp_path / "out"
    submit = _submitter(output, _cut_repo(tmp_path / "source"))
    first = _text(submit(_reading("browser")))
    assert first == _look(_CUT_BROWSER)
    assert "was found" not in first
    answer = submit(_reading(then, second_look_read=_READ))
    if outcome == "saved":
        assert not answer.get("is_error"), answer
        assert load(output).modality == "chat"
    else:
        assert _refused_with(output) == _READ_MESSAGE


@pytest.mark.parametrize(
    "files, totals, said",
    [
        pytest.param(
            {
                "big.py": _OVERSIZED,
                "big_too.py": _OVERSIZED,
                "agent.py": "import playwright\ndef (\n",
            },
            {},
            "agent type scan cut by file_size 2, parse_failed 1",
            id="two-limits",
        ),
        # A break leaves every file after it unread too, the one that broke it among them.
        pytest.param(
            {f"m{at}.py": "import playwright\n" for at in range(4)},
            {(agent_type_check, "_LARGEST_SCAN"): 30},
            "agent type scan cut by scan_total 3",
            id="scan_total",
        ),
        pytest.param(
            {f"m{at}.py": b"# coding: utf-7\nimport playwright\n" for at in range(2)},
            {(python_source, "LARGEST_ESCAPED_TOTAL"): 0},
            "agent type scan cut by decode_total 1",
            id="decode_total",
        ),
        pytest.param({"agent.py": "import playwright\n"}, {}, None, id="uncut"),
    ],
)
def test_the_cut_is_logged_once(tmp_path, monkeypatch, caplog, files, totals, said):
    _tree(tmp_path, files)
    for (module, name), value in totals.items():
        monkeypatch.setattr(module, name, value)
    with caplog.at_level(logging.WARNING, logger=agent_type_check.__name__):
        browser_scan(tmp_path)
        browser_scan(tmp_path)
        browser_tools_found(tmp_path)
        first = [(record.levelno, record.getMessage()) for record in caplog.records]
        caplog.clear()
        agent_type_check._scan.cache_clear()
        browser_scan(tmp_path)
        again = [(record.levelno, record.getMessage()) for record in caplog.records]
    assert first == again == ([] if said is None else [(logging.WARNING, said)])


def _shared_repo(root: Path) -> Path:
    return _tree(
        root,
        {
            "agents/a/agent.py": "import common.web\n",
            "agents/a/big.py": _OVERSIZED,
            "common/__init__.py": "",
            "common/web.py": "import playwright\n",
        },
    )


def test_one_call_walks_once(tmp_path, monkeypatch):
    repo = _shared_repo(tmp_path / "source")
    walks: list[str] = []
    scans: list[int] = []
    real_walk, real_scan = (
        agent_type_check.imported_by_agent,
        agent_type_check._tools_in,
    )

    def walk(root, subfolder, **kwargs):
        walks.append(subfolder)
        return real_walk(root, subfolder, **kwargs)

    def scan(*args, **kwargs):
        scans.append(1)
        return real_scan(*args, **kwargs)

    monkeypatch.setattr(agent_type_check, "imported_by_agent", walk)
    monkeypatch.setattr(agent_type_check, "_tools_in", scan)
    reached: list[Path] = []
    assert browser_scan(repo, "agents/a", reached) == (
        ["playwright"],
        frozenset({"file_size"}),
    )
    assert walks == ["agents/a"]
    assert len(scans) == 1
    assert repo.resolve() / "common" / "web.py" in reached
    assert browser_tools_in_agent(repo, "agents/a") == ["playwright"]

    agent_type_check._scan.cache_clear()
    scans.clear()
    assert browser_scan(repo) == (["playwright"], frozenset({"file_size"}))
    assert browser_scan(repo) == (["playwright"], frozenset({"file_size"}))
    assert browser_tools_found(repo) == ["playwright"]
    assert len(scans) == 1
    assert agent_type_check._scan.cache_info().currsize == 1


def test_no_root_is_no_scan():
    assert browser_scan(None) == ([], frozenset())


def test_the_tickets_padding_gets_a_look(tmp_path):
    # Three 126 KB files naming playwright only in a comment spend the parse total first.
    pad = "# playwright\n" + "# padding\n" * (126 * 1024 // 10)
    repo = _tree(
        tmp_path / "source",
        {
            **{f"a_pad_{at}.py": pad for at in range(3)},
            "z_agent.py": "import playwright\n",
        },
    )
    assert browser_scan(repo) == ([], frozenset({"parse_total"}))
    answer = _submitter(tmp_path / "out", repo)(_reading("chat"))
    assert _text(answer) == _look(_CUT_CHAT.format(claimed="chat"))


def test_a_large_honest_repository_gets_a_look(tmp_path, monkeypatch):
    # Twenty 4 KB modules read before the agent's main file, against a total of 64 KB standing in
    # for the 128 MiB one a 118 MB repository runs out of.
    module = "".join(f"def f{at}():\n    return {at}\n" for at in range(200))
    repo = _tree(
        tmp_path / "source",
        {
            **{f"lib/mod_{at:02}.py": module for at in range(20)},
            "service/main.py": "from playwright.sync_api import sync_playwright\n",
        },
    )
    assert browser_scan(repo) == (["playwright"], frozenset())
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCAN", 64 * 1024)
    agent_type_check._scan.cache_clear()
    assert browser_scan(repo) == ([], frozenset({"scan_total"}))
    answer = _submitter(tmp_path / "out", repo)(_reading("chat"))
    assert _text(answer) == _look(_CUT_CHAT.format(claimed="chat"))


def test_the_walks_stop_is_no_cut(tmp_path, caplog):
    # Over the walk's 512 KB parse total in modules that import, none of them naming a tool.
    module = "import os\n" + "x = 1\n" * (100 * 1024 // 6)
    repo = _tree(
        tmp_path / "source",
        {
            **{f"agents/a/m_{at}.py": module for at in range(6)},
            "agents/a/z_main.py": "import common.util\n",
            "common/__init__.py": "",
            "common/util.py": "x = 1\n",
        },
    )
    with caplog.at_level(logging.WARNING, logger=agent_type_check.__name__):
        assert browser_scan(repo, "agents/a") == ([], frozenset())
    assert any(
        "stopped at its limits" in record.getMessage() for record in caplog.records
    )
    output = tmp_path / "out"
    answer = _submitter(output, repo, "agents/a")(_reading("chat"))
    assert not answer.get("is_error"), answer
    assert load(output).modality == "chat"


def test_ci_runs_this_file():
    workflows = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    )
    assert "tests/harness/test_browser_scan_cut.py" in workflows.read_text(
        encoding="utf-8"
    )
