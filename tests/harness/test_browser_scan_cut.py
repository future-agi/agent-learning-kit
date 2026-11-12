"""A browser scan that leaves code unread or unparsed says so, and the reading gets a second look.

Finding nothing in code the scan didn't read isn't finding nothing: padding read first, or an
honest repository larger than the limits, would otherwise hide a plain ``import playwright``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
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
# Over the size a .py is read at, so never read; it names a tool, so leaving it unread cuts.
_OVERSIZED = "# playwright\n".ljust(LARGEST_PARSED_MODULE + 1, "#")
# A data module of prompt strings over that size, about 265 KB, naming no tool.
_DATA_MODULE = (
    "PROMPTS = [\n" + '    "Tell me about the weather today.",\n' * 6_800 + "]\n"
)


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
        {"package.json": "[" * 100_000},
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


def test_a_large_data_module_is_no_cut(tmp_path):
    # A big data module beside code that imports no tool.
    assert len(_DATA_MODULE.encode()) > 260 * 1024
    repo = _tree(
        tmp_path / "a", {"main.py": "import openai\n", "prompts_data.py": _DATA_MODULE}
    )
    assert browser_scan(repo) == ([], frozenset())
    output = tmp_path / "out"
    answer = _submitter(output, repo)(_reading("chat"))
    assert not answer.get("is_error"), answer
    assert load(output).modality == "chat"


@pytest.mark.parametrize(
    "files, totals",
    [
        pytest.param(
            {"big.py": "#" * (LARGEST_PARSED_MODULE + 1) + "\nimport playwright\n"},
            {},
            id="named-past-the-read-limit",
        ),
        pytest.param(
            {"big.py": "#" * (3 * 1024 * 1024) + "\nimport playwright\n"},
            {},
            id="named-3-mib-in",
        ),
        # Exactly the per-file bound: the needle ends at its last byte.
        pytest.param(
            {
                "big.py": "#" * (agent_type_check._LARGEST_UNREAD_SEARCHED - 19)
                + "\nimport playwright\n"
            },
            {},
            id="named-at-the-bound",
        ),
        # The toolkit's import holds no lowercase `playwright`.
        pytest.param(
            {
                "big.py": "x = 1\n" * 30_000
                + "from langchain_community.agent_toolkits import PlayWrightBrowserToolkit\n"
            },
            {},
            id="toolkit",
        ),
        # Python refuses the coding line until the codec is registered at run time.
        pytest.param(
            {"big.py": b"# coding: nonesuch\n" + _DATA_MODULE.encode()},
            {},
            id="refused-coding",
        ),
        pytest.param(
            {"big.py": "x = 1\n" * 30_000 + "TOOL = 'computer_use_preview'\n"},
            {},
            id="marker",
        ),
        # Python reads it as `import playwright`; its bytes name nothing.
        pytest.param(
            {
                "big.py": b"# coding: utf-7\nimport +AHA-laywright\n"
                + _DATA_MODULE.encode()
            },
            {},
            id="utf-7",
        ),
        pytest.param(
            {
                "big.py": b"# coding: unicode_escape\nimport \\x70laywright\n"
                + _DATA_MODULE.encode()
            },
            {},
            id="unicode-escape",
        ),
        pytest.param(
            {"big.py": _DATA_MODULE},
            {(agent_type_check, "_LARGEST_UNREAD_SEARCHED"): LARGEST_PARSED_MODULE + 1},
            id="past-the-file-bound",
        ),
        pytest.param(
            {"big.py": _DATA_MODULE},
            {(agent_type_check, "_UNREAD_SEARCH_TOTAL"): len(_DATA_MODULE) - 1},
            id="past-the-scan-total",
        ),
        pytest.param(
            {"bundle.js": "const tool = 'computer-use-preview';\n"},
            {(agent_type_check, "_LARGEST_CODE_FILE"): 20},
            id="script-marker",
        ),
        # The packages a manifest lists aren't among the names searched for.
        pytest.param(
            {"package.json": json.dumps({"dependencies": {"puppeteer": "1"}})},
            {(agent_type_check, "_LARGEST_CODE_FILE"): 20},
            id="package-json",
        ),
    ],
)
def test_an_unread_file_that_may_name_a_tool_cuts(tmp_path, monkeypatch, files, totals):
    for (module, name), value in totals.items():
        monkeypatch.setattr(module, name, value)
    repo = _tree(tmp_path / "source", {"main.py": "import openai\n", **files})
    assert browser_scan(repo) == ([], frozenset({"file_size"}))


def test_past_the_search_total_a_file_isnt_opened(tmp_path, monkeypatch):
    repo = _tree(
        tmp_path / "source",
        {"main.py": "import openai\n", "big.py": _DATA_MODULE, "big_too.py": _DATA_MODULE},
    )
    monkeypatch.setattr(agent_type_check, "_UNREAD_SEARCH_TOTAL", len(_DATA_MODULE) - 1)
    opened: list[str] = []
    real = Path.open

    def spy(self, *args, **kwargs):
        opened.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    assert browser_scan(repo) == ([], frozenset({"file_size"}))
    # Neither fits what's left of the total, so neither is read to be searched.
    assert "big.py" not in opened and "big_too.py" not in opened


@pytest.mark.parametrize(
    "files, totals",
    [
        # "computer" in prose starts no tool marker.
        pytest.param(
            {"big.py": _DATA_MODULE.replace("weather today", "computer", 1)},
            {},
            id="prose-computer",
        ),
        pytest.param(
            {"big.py": b"# coding: latin-1\n" + _DATA_MODULE.encode()},
            {},
            id="raw-safe-coding",
        ),
        pytest.param(
            {"bundle.js": "const answer = 42; // past twenty bytes\n"},
            {(agent_type_check, "_LARGEST_CODE_FILE"): 20},
            id="script",
        ),
        # The second file's search spends what's left exactly.
        pytest.param(
            {"big.py": _DATA_MODULE, "big_too.py": _DATA_MODULE},
            {(agent_type_check, "_UNREAD_SEARCH_TOTAL"): 2 * len(_DATA_MODULE)},
            id="at-the-scan-total",
        ),
    ],
)
def test_an_unread_file_that_names_no_tool_is_no_cut(
    tmp_path, monkeypatch, files, totals
):
    for (module, name), value in totals.items():
        monkeypatch.setattr(module, name, value)
    repo = _tree(tmp_path / "source", {"main.py": "import openai\n", **files})
    assert browser_scan(repo) == ([], frozenset())


def _unreadable(monkeypatch, repo: Path) -> None:
    real = Path.open

    def refused(self, *args, **kwargs):
        if self.name == "big.py":
            raise PermissionError("refused")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", refused)


def _sized(size: int):
    real = agent_type_check._file_size
    return lambda path: size if path.name == "big.py" else real(path)


def _not_a_file(monkeypatch, repo: Path) -> None:
    # Read, it would give nothing, and nothing names no tool.
    (repo / "big.py").unlink()
    (repo / "big.py").symlink_to(os.devnull)
    monkeypatch.setattr(
        agent_type_check, "_file_size", _sized(LARGEST_PARSED_MODULE + 1)
    )


def _grown(monkeypatch, repo: Path) -> None:
    # Its size taken under the bound, it's past it when read.
    (repo / "big.py").write_text("x = 1\n" * (5 * 1024 * 1024 // 6), encoding="utf-8")
    monkeypatch.setattr(
        agent_type_check, "_file_size", _sized(LARGEST_PARSED_MODULE + 1)
    )


@pytest.mark.parametrize("make", [_unreadable, _not_a_file, _grown])
def test_an_unread_file_that_cant_be_cleared_cuts(tmp_path, monkeypatch, make):
    repo = _tree(
        tmp_path / "source", {"main.py": "import openai\n", "big.py": _DATA_MODULE}
    )
    make(monkeypatch, repo)
    assert browser_scan(repo) == ([], frozenset({"file_size"}))


def test_the_unread_search_is_charged_to_its_own_total(tmp_path, monkeypatch):
    # Read first by name: charged to the scan's totals, it would leave the agent's file unread.
    repo = _tree(
        tmp_path / "source",
        {"a_data.py": _DATA_MODULE, "z_agent.py": "import playwright\n"},
    )
    monkeypatch.setattr(agent_type_check, "_LARGEST_SCAN", 100)
    monkeypatch.setattr(agent_type_check, "_LARGEST_PYTHON_SCAN", 100)
    assert browser_scan(repo) == (["playwright"], frozenset())
    # The search's total is shared by the scan's files, in the order they're read.
    agent_type_check._scan.cache_clear()
    _tree(repo, {"b_data.py": _DATA_MODULE})
    monkeypatch.setattr(agent_type_check, "_UNREAD_SEARCH_TOTAL", len(_DATA_MODULE))
    assert browser_scan(repo) == (["playwright"], frozenset({"file_size"}))


@pytest.mark.parametrize(
    "manifest, names",
    [
        # A trailing comma: npm refuses it too.
        pytest.param(
            b'{"name": "x", "dependencies": {"react": "1"},}', [], id="trailing-comma"
        ),
        # Node reads a byte that isn't UTF-8 as a replacement character.
        pytest.param(
            b'{"description": "\xff", "dependencies": {"playwright": "1"}}',
            ["playwright"],
            id="not-utf-8-in-a-string",
        ),
        # Read that way, a stray byte before the object is no JSON either.
        pytest.param(
            b'\xff{"dependencies": {"playwright": "1"}}', [], id="not-utf-8-before-it"
        ),
        # JSON.parse reads an integer of any length; json refuses one past 4,300 digits.
        pytest.param(
            b'{"dependencies": {"puppeteer": "22"}, "n": ' + b"1" * 5000 + b"}",
            ["puppeteer"],
            id="a-5000-digit-integer",
        ),
    ],
)
def test_a_package_json_npm_reads_or_refuses_is_no_cut(tmp_path, manifest, names):
    repo = _tree(
        tmp_path / "b", {"main.py": "import openai\n", "package.json": manifest}
    )
    assert browser_scan(repo) == (names, frozenset())


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


_PADDING = "import os\n" + "x = 1\n" * (100 * 1024 // 6)


def _padded(root: Path, **more: str) -> Path:
    # Six modules that import, over the walk's 512 KB parse total, are read before z_main.py.
    return _tree(
        root,
        {
            **{f"agents/a/m_{at}.py": _PADDING for at in range(6)},
            "agents/a/z_main.py": "import common.util\n",
            "common/__init__.py": "",
            **more,
        },
    )


def test_a_file_the_parse_total_skipped_is_line_searched_and_no_cut(tmp_path, caplog):
    # Dileep's C2: z_main.py is never parsed, so common.util used to go unfollowed.
    repo = _padded(tmp_path / "source", **{"common/util.py": "import playwright\n"})
    with caplog.at_level(logging.WARNING, logger=agent_type_check.__name__):
        assert browser_scan(repo, "agents/a") == (["playwright"], frozenset())
    assert any("stopped at its limits" in record.getMessage() for record in caplog.records)


def test_a_name_in_a_file_only_the_line_search_reached_is_found_and_no_cut(tmp_path):
    # common/deep.py is reached only through util.py's import line, and util.py only through
    # z_main.py's: neither was parsed.
    repo = _padded(
        tmp_path / "source",
        **{"common/util.py": "import common.deep\n", "common/deep.py": "import playwright\n"},
    )
    assert browser_scan(repo, "agents/a") == (["playwright"], frozenset())


def test_a_line_search_with_no_room_left_is_a_cut(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_type_check, "_WALKED_SEARCHED", 8)
    repo = _padded(tmp_path / "source", **{"common/util.py": "import playwright\n"})
    assert browser_scan(repo, "agents/a") == ([], frozenset({"import_walk"}))
    answer = _submitter(tmp_path / "out", repo, "agents/a")(_reading("chat"))
    assert _text(answer) == _look(_CUT_CHAT.format(claimed="chat"))
    assert decide("chat", [], looked_again=False, cut=frozenset({"import_walk"})) == "second_look"


def test_a_name_dropped_for_its_depth_alone_is_no_cut(tmp_path, caplog):
    deep = ".".join(f"p{at}" for at in range(80))
    repo = _tree(tmp_path / "source", {"agents/a/agent.py": f"import {deep}\nx = 1\n"})
    with caplog.at_level(logging.WARNING, logger=agent_type_check.__name__):
        assert browser_scan(repo, "agents/a") == ([], frozenset())
    # The walk still logs that it left a name unread.
    assert any(
        "stopped at its limits" in record.getMessage() for record in caplog.records
    )
    answer = _submitter(tmp_path / "out", repo, "agents/a")(_reading("chat"))
    assert not answer.get("is_error"), answer


def test_a_file_python_refuses_alone_is_no_cut(tmp_path, caplog):
    repo = _tree(
        tmp_path / "source",
        {
            "agents/a/agent.py": "import common.util\n",
            "agents/a/broken.py": "import os\ndef (:\n",
            "common/__init__.py": "",
            "common/util.py": "x = 1\n",
        },
    )
    with caplog.at_level(logging.WARNING, logger=agent_type_check.__name__):
        assert browser_scan(repo, "agents/a") == ([], frozenset())
    assert any(
        "stopped at its limits" in record.getMessage() for record in caplog.records
    )
    answer = _submitter(tmp_path / "out", repo, "agents/a")(_reading("chat"))
    assert not answer.get("is_error"), answer


def test_targets_left_in_the_queue_by_the_import_limit_are_a_cut(tmp_path, monkeypatch):
    # Two imports are the walk's whole allowance, so the two modules they reach are queued, not
    # read: common/m2.py may import anything.
    monkeypatch.setattr(agent_type_check, "_WALKED_IMPORTS", 2)
    repo = _tree(
        tmp_path / "source",
        {
            "agents/a/agent.py": "import common.m1\nimport common.m2\n",
            "common/__init__.py": "",
            "common/m1.py": "x = 1\n",
            "common/m2.py": "import common.m3\n",
            "common/m3.py": "import playwright\n",
        },
    )
    assert browser_scan(repo, "agents/a") == ([], frozenset({"import_walk"}))


@pytest.mark.parametrize(
    "line",
    ["x = 1; import common.util", "if True: import common.util", "x = 1;import common.util"],
)
def test_an_import_after_a_semicolon_or_a_colon_is_line_searched_and_no_cut(tmp_path, line):
    # Dileep's padding keeps z_main.py from being parsed; its import isn't at the line's start.
    repo = _padded(
        tmp_path / "source",
        **{"agents/a/z_main.py": f"{line}\n", "common/util.py": "import playwright\n"},
    )
    assert browser_scan(repo, "agents/a") == (["playwright"], frozenset())


def test_a_file_over_the_search_cap_is_not_opened_and_is_a_cut(tmp_path, monkeypatch):
    # Ten sparse 5 MiB files in the agent's folder: each is over the 4 MiB a file is searched at,
    # so none is opened (the bytes used to be read, then dropped uncharged).
    repo = _tree(tmp_path / "source", {"agents/a/agent.py": "x = 1\n"})
    for at in range(10):
        with (repo / f"agents/a/big_{at}.py").open("wb") as handle:
            handle.truncate(5 * 1024 * 1024)
    read = [0]
    real_open = Path.open

    def counting_open(self, mode="r", *args, **kwargs):
        handle = real_open(self, mode, *args, **kwargs)
        if not self.name.startswith("big_"):
            return handle
        real_read = handle.read

        def read_counted(size=-1):
            data = real_read(size)
            read[0] += len(data)
            return data

        handle.read = read_counted
        return handle

    monkeypatch.setattr(Path, "open", counting_open)
    names, cut = browser_scan(repo, "agents/a")
    assert names == [] and "import_walk" in cut
    assert read[0] <= agent_type_check._LARGEST_UNREAD_SEARCHED


def test_ci_runs_this_file():
    workflows = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    )
    assert "tests/harness/test_browser_scan_cut.py" in workflows.read_text(
        encoding="utf-8"
    )
