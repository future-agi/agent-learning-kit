"""A Python file whose codec can spell a name in other bytes is read as Python reads it.

Each row that says Python runs a name checks it with ``compile``, which is what an import runs,
or, in a codec only `python file.py` runs a file in, by running the file with a stub on the path.
"""

from __future__ import annotations

import ast
import codecs
import collections
import encodings
import encodings.aliases
import io
import logging
import os
import pkgutil
import string
import subprocess
import sys
import tokenize
import types
from pathlib import Path

import pytest

from fi.alk.harness import agent_type_check, python_source
from fi.alk.harness.agent_type_check import (
    browser_tools_found,
    browser_tools_in_agent,
    code_the_agent_reaches,
)
from fi.alk.harness.credentials import imported_by_agent
from fi.alk.harness.instrumentation_evidence import detect_instrumentation
from fi.alk.harness.python_source import (
    _RAW_SAFE_CODECS,
    EscapeBudget,
    _coding,
    _decoded_readings,
    read_module,
)

# Python reads a decoded CR as a line end, and a line it starts as blank, only from 3.12.
_FROM_312 = pytest.mark.skipif(
    sys.version_info < (3, 12), reason="Python reads a decoded CR this way from 3.12"
)


def _keeps_a_decoded_cr() -> bool:
    """Whether Python keeps a decoded CR inside a line: 3.13.13 and 3.14.7 end the line there."""
    try:
        compile(
            b'# coding: unicode-escape\nx = "\\r"\n', "<cr>", "exec", dont_inherit=True
        )
    except SyntaxError:
        return False
    return True


# The rows whose premise is a decoded CR Python keeps inside a line: where it ends the line
# there instead, it refuses the file, and the row has nothing left to show.
_KEEPS_A_DECODED_CR = pytest.mark.skipif(
    not _keeps_a_decoded_cr(), reason="this Python ends a line at a decoded CR"
)
_ISO2022 = (
    "iso2022_jp",
    "iso2022_jp_1",
    "iso2022_jp_2",
    "iso2022_jp_2004",
    "iso2022_jp_3",
    "iso2022_jp_ext",
    "iso2022_kr",
)
# The codecs Python accepts on a coding line that spell a name character from other bytes.
_SPELLING_CODECS = frozenset(
    {
        "raw-unicode-escape",
        "unicode-escape",
        "utf-7",
        "hz",
        "mac-arabic",
        "mac-farsi",
        *_ISO2022,
    }
)
_UTF7 = b"# coding: utf-7\n"
_ESCAPED = b"# coding: unicode-escape\n"
_RAW_ESCAPED = b"# coding: raw-unicode-escape\n"


def _in(codec: str, body: bytes) -> bytes:
    return f"# coding: {codec}\n".encode() + body


def _names_python_runs(raw: bytes) -> set[str]:
    """The names the code compiled from ``raw`` uses, its functions' included."""
    names: set[str] = set()
    pending = [compile(raw, "<agent>", "exec", dont_inherit=True)]
    while pending:
        code = pending.pop()
        names.update(code.co_names)
        pending.extend(c for c in code.co_consts if isinstance(c, types.CodeType))
    return names


def _imported(tree: ast.AST) -> set[str]:
    """The top-level modules ``tree`` imports."""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found


def _write(folder: Path, raw: bytes, name: str = "agent.py") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(raw)
    return path


def _shared(root: Path, agent: bytes) -> Path:
    """A repository whose agent, in agents/a, may import common.web, which imports the names
    both scans look for."""
    root = root.resolve()
    _write(root / "agents" / "a", agent)
    _write(root / "common", b"", "__init__.py")
    _write(root / "common", b"import playwright\nimport traceai_openai\n", "web.py")
    return root


def _scan(folder: Path, raw: bytes, name: str) -> object:
    """What the reader that looks for ``name`` makes of a file holding ``raw``: whether the walk
    reaches common/web.py, the instrumentation verdict, or the browser tools."""
    if name == "common.web":
        repo = _shared(folder, raw)
        return repo / "common" / "web.py" in imported_by_agent(repo, "agents/a")
    _write(folder, raw)
    if name.startswith("traceai_"):
        return detect_instrumentation(folder)
    return browser_tools_found(folder)


def _found(name: str) -> object:
    if name == "common.web":
        return True
    return "traceai" if name.startswith("traceai_") else [name.replace("_", "-")]


def _missed(name: str) -> object:
    if name == "common.web":
        return False
    return "none" if name.startswith("traceai_") else []


@pytest.mark.parametrize(
    "raw, name",
    [
        pytest.param(
            _RAW_ESCAPED + b"import \\u0070laywright\n",
            "playwright",
            id="raw-unicode-escape",
        ),
        pytest.param(
            _ESCAPED + b"import \\x70laywright\n", "playwright", id="unicode-escape"
        ),
        pytest.param(_UTF7 + b"import +AHA-laywright\n", "playwright", id="utf-7"),
        *(
            pytest.param(
                _in(codec, b"import play\x1b(Bwright\n"), "playwright", id=codec
            )
            for codec in _ISO2022
        ),
        pytest.param(_in("hz", b"import play~{~}wright\n"), "playwright", id="hz"),
        pytest.param(_in("hz", b"import play~\nwright\n"), "playwright", id="hz-lf"),
        pytest.param(
            b"# coding: hz\r\nimport play~\r\nwright\r\n", "playwright", id="hz-crlf"
        ),
        pytest.param(b"# coding: hz\rimport play~\rwright\r", "playwright", id="hz-cr"),
        # 0xDF is `_` here, and `playwright` before it would be found in the bytes.
        pytest.param(
            _in("mac-arabic", b"import undetected\xdfchromedriver\n"),
            "undetected_chromedriver",
            id="mac-arabic",
        ),
        pytest.param(
            _in("mac-farsi", b"import undetected\xdfchromedriver\n"),
            "undetected_chromedriver",
            id="mac-farsi",
        ),
    ],
)
def test_a_browser_import_spelled_in_a_codec_that_can_spell_one_is_found(
    tmp_path, raw, name
):
    assert name in _names_python_runs(raw)
    _write(tmp_path, raw)
    assert browser_tools_found(tmp_path) == [name.replace("_", "-")]


# The EBCDIC codecs, each by a name 3.14 can decode its coding line in (it refuses `cp424`,
# whose `p` cp424 lacks). compile() and import decode the coding line too, and in these it
# decodes to other characters, so only `python file.py` runs a file in one.
_EBCDIC = {
    "cp037": "cp037",
    "cp1026": "cp1026",
    "cp1140": "cp1140",
    "cp273": "cp273",
    "cp424": "ibm424",
    "cp500": "cp500",
    "cp875": "cp875",
}


def _script_imports(folder: Path, raw: bytes) -> bool:
    """Whether `python main.py`, for a main.py in ``folder`` holding ``raw``, imports
    playwright: a stub on the path says so."""
    stub = folder.parent / f"{folder.name}-stub"
    _write(stub, b"print('stub imported')\n", "playwright.py")
    _write(folder, raw, "main.py")
    run = subprocess.run(
        [sys.executable, "-s", "main.py"],
        cwd=folder,
        env={**os.environ, "PYTHONPATH": str(stub), "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        timeout=60,
    )
    return run.returncode == 0 and b"stub imported" in run.stdout


@pytest.mark.parametrize(
    "raw, lines",
    [
        # Python throws away the line after the coding line; a blank line stands for it.
        *(
            pytest.param(
                _in(name, "x = 0\nimport playwright\n".encode(codec)), [3], id=codec
            )
            for codec, name in sorted(_EBCDIC.items())
        ),
        # A NUL makes the byte before the coding line's end an LF; nothing else is thrown away.
        pytest.param(
            _in("utf_16_le", b"\0" + "x = 0\nimport playwright\n".encode("utf_16_le")),
            [2, 3],
            id="utf-16-le",
        ),
        # Here that byte starts a character with the NUL, which the LF after it ends.
        pytest.param(
            _in("utf_16_be", b"\0" + "\nimport playwright\n".encode("utf_16_be")),
            [3],
            id="utf-16-be",
        ),
    ],
)
def test_a_browser_import_in_a_codec_only_a_script_runs_is_found(tmp_path, raw, lines):
    folder = tmp_path / "agent"
    assert _script_imports(folder, raw)
    assert browser_tools_found(folder) == ["playwright"]
    budget = EscapeBudget()
    text = read_module(folder / "main.py", escapes=budget)
    assert budget.left == python_source.LARGEST_ESCAPED_TOTAL - len(raw)
    # Each line keeps its number in the file.
    assert [node.lineno for node in ast.parse(text).body] == lines


@pytest.mark.parametrize(
    "head",
    [
        "# coding: utf_16_le\n",
        "# coding: utf_16_le\r\n",
        "# -*- coding: utf_16_le -*-\n",
        "#!x\n# coding: utf_16_le\n",
        # A blank first line of one space: padded too, it keeps the pair's length even.
        " \n# coding: utf_16_le\n",
        # An odd number of bytes, which 3.14 can't decode in UTF-16: it refuses the file.
        "# coding:  utf_16_le\n",
    ],
)
def test_a_utf_16_coding_line_of_any_length_is_read(tmp_path, head):
    raw = head.encode() + b"\0" + "x = 0\nimport playwright\n".encode("utf_16_le")
    folder = tmp_path / "agent"
    if not _script_imports(folder, raw):
        pytest.skip("this Python refuses the coding line in UTF-16")
    assert browser_tools_found(folder) == ["playwright"]


@pytest.mark.parametrize(
    "raw",
    [
        b"# coding: utf_16_le\n",
        b"# coding: utf_16_le\r\n",
        b"# -*- coding: utf_16_le -*- \n",
        b"#!xy\n# coding: utf_16_le\n",
        b"\n# coding: utf_16_le\n",
        b" \n# coding: utf_16_le\n",
        b"\t  \n# coding: utf_16_le\n",
    ],
)
def test_detect_encoding_is_handed_lines_as_long_as_the_real_ones_mod_4(
    monkeypatch, raw
):
    # From 3.14 it decodes them in their codec, as Python decodes the real ones: a UTF-16 or
    # UTF-32 line of the wrong length would be refused.
    handed: list[bytes] = []
    real = python_source.tokenize.detect_encoding

    def spied(readline):
        def line():
            got = readline()
            handed.append(got)
            return got

        return real(line)

    monkeypatch.setattr(python_source.tokenize, "detect_encoding", spied)
    if _coding(raw + b"\0x\0") is None:
        # From 3.14 Python refuses some of these files itself, so no coding is right there.
        pytest.skip("this Python refuses this coding line")
    # Python ends each with an LF, a CRLF made one.
    real = [len(line) + 1 for line in raw.replace(b"\r\n", b"\n").split(b"\n")[:-1]]
    assert [len(line) % 4 for line in handed] == [length % 4 for length in real][
        : len(handed)
    ]


def test_a_long_first_line_is_read_both_ways_and_charged_for_both():
    # Asking whether a coding line this long reads as itself would decode it uncharged.
    raw = (
        b"# coding: utf-7 "
        + b"x" * python_source._LONGEST_HEAD_CHECKED
        + b"\nimport os\n"
    )
    budget = EscapeBudget()
    assert _decoded_readings(raw, _coding(raw), budget) == (
        True,
        raw.decode("utf-7"),
        None,
    )
    assert budget.left == python_source.LARGEST_ESCAPED_TOTAL - 2 * len(raw)


@pytest.mark.parametrize(
    "head, body, imports",
    [
        # Python opens the file again one byte before the coding line's end and throws away
        # the line it reads there: the LF, in cp037 no line end, and the line after it.
        pytest.param("# coding: cp037\n", "import playwright\n", False, id="lf"),
        pytest.param("# coding: cp037\r\n", "import playwright\n", False, id="crlf"),
        pytest.param(
            "#!/usr/bin/env python\n# coding: cp037\n",
            "x = 0\nimport playwright\n",
            True,
            id="second-line",
        ),
        # The CR is a line end in cp037 too, so nothing after it is thrown away.
        pytest.param("# coding: cp037\r", "import playwright\n", True, id="cr"),
        pytest.param(
            "# coding: cp037\r", "\nimport playwright\n", True, id="cr-then-lf"
        ),
        # Decoded, a CR ends a line, as Python reads a file it decodes as it goes.
        pytest.param(
            "# coding: cp037\n",
            "x = 0\nimport os\rimport playwright\n",
            True,
            id="body-cr",
        ),
        # The coding line on the second line: its own end is the byte decoded first. A `%`,
        # cp037's LF, on it would end the thrown line there were the first line's end taken.
        pytest.param(
            "#!x\n# coding: cp037 %\n",
            "x = 0\nimport playwright\n",
            True,
            id="second-line-%",
        ),
        pytest.param(
            "#!x\r# coding: cp037 %\n",
            "x = 0\nimport playwright\n",
            True,
            id="second-line-%-after-cr",
        ),
        # A CRLF is one line end: the byte decoded first is its LF, not its CR.
        pytest.param(
            "# coding: cp037\r\n",
            "x = 0\nimport playwright\n",
            True,
            id="crlf-x-first",
        ),
    ],
)
def test_a_script_only_codec_reads_as_python_runs_the_script(
    tmp_path, head, body, imports
):
    raw = head.encode() + body.encode("cp037")
    folder = tmp_path / "agent"
    assert _script_imports(folder, raw) is imports
    assert browser_tools_found(folder) == (["playwright"] if imports else [])


def test_an_ascii_compatible_codec_reads_as_before():
    raw = b"# coding: utf-7\nimport os\nx = '+AA0-'\n"
    budget = EscapeBudget()
    assert _decoded_readings(raw, _coding(raw), budget) == (
        True,
        raw.replace(b"\r\n", b"\n").decode("utf-7"),
        None,
    )
    assert budget.left == python_source.LARGEST_ESCAPED_TOTAL - len(raw)


def test_a_file_whose_second_reading_is_past_the_total_keeps_the_imports_reading():
    # A CR asks for the script's reading too, and the total lasts for the first decode only: the
    # import's reading comes back alone, decoded, the escape on the coding line included.
    raw = b"# coding: unicode-escape  # \\x41\r\nimport playwright\r\n"
    budget = EscapeBudget(len(raw) + 1)
    assert _decoded_readings(raw, _coding(raw), budget) == (
        True,
        raw.replace(b"\r\n", b"\n").decode("unicode-escape"),
        None,
    )


def test_a_coding_line_that_reads_as_another_gives_both_readings():
    # An import reads the coding line's escape, and the decoded CR in the string; a script
    # keeps the escape as written, and ends the string's line at the CR.
    raw = b"# coding: utf-7 +AHA-\r\nimport os\r\nx = '+AA0-'\r\n"
    budget = EscapeBudget()
    assert _decoded_readings(raw, _coding(raw), budget) == (
        True,
        raw.replace(b"\r\n", b"\n").decode("utf-7"),
        "# coding: utf-7 +AHA-\nimport os\nx = '\n'\n",
    )
    # Each decode is charged.
    assert budget.left == python_source.LARGEST_ESCAPED_TOTAL - 2 * len(raw)


def _compiled_imports(raw: bytes, name: str) -> bool:
    """Whether importing the file, which compile() stands for, imports ``name``."""
    try:
        return name in _names_python_runs(raw)
    except (SyntaxError, ValueError):
        return False


# Coding lines in a codec that reads its own name as itself, each leaving its decoder in another
# state: an import decodes the rest in that state, `python file.py` with a fresh decoder.
_CRAFTED_CODING_LINES = [
    pytest.param(b"# coding: utf-7 +\n", id="utf-7-open-shift"),
    pytest.param(b"# coding: utf-7 +\r\n", id="utf-7-open-shift-crlf"),
    pytest.param(b"#!/usr/bin/env python\n# coding: utf-7 +\n", id="utf-7-second-line"),
    pytest.param(b"# coding: hz ~{\n", id="hz-gb-mode"),
    pytest.param(b"# coding: iso2022_jp \x1b$B\n", id="iso2022_jp-escape"),
    pytest.param(b"# coding: iso2022_jp_2 \x1b$A\n", id="iso2022_jp_2-escape"),
    pytest.param(b"# coding: unicode-escape \\\n", id="unicode-escape-joins-the-line"),
    pytest.param(b"# coding: raw-unicode-escape \\u\n", id="raw-unicode-escape-open"),
    # An import ends lines before it decodes, so the backslash joins the next line to the
    # comment; a script decodes first, and a backslash before a CR joins nothing.
    pytest.param(
        b"# coding: unicode-escape \\\r\n", id="unicode-escape-backslash-crlf"
    ),
    pytest.param(b"# coding: unicode-escape \\\r", id="unicode-escape-backslash-cr"),
    pytest.param(
        b"# coding: unicode-escape\r\nx = 1  # \\\r\n",
        id="unicode-escape-backslash-crlf-body",
    ),
    # A single shift reads the byte after it: an import's LF, a script's CR.
    pytest.param(
        b"# coding: iso2022_jp_2\r\n# \x1b.A\x1bN\r\n", id="iso2022_jp_2-single-shift-crlf"
    ),
]


@pytest.mark.parametrize("head", _CRAFTED_CODING_LINES)
@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"import playwright\n", id="plain"),
        # Joined to the coding line, the import's line opens a string nothing closes.
        pytest.param(b'import playwright; x = """\n"""\n', id="string"),
    ],
)
def test_an_import_after_a_crafted_coding_line_is_found(tmp_path, head, body):
    raw = head + body
    folder = tmp_path / "agent"
    if not (_script_imports(folder, raw) or _compiled_imports(raw, "playwright")):
        pytest.skip("this Python runs neither reading")
    assert browser_tools_found(folder) == ["playwright"]


@pytest.mark.parametrize(
    "raw, script",
    [
        # Only an import reads the line end the escape spells; a script reads a comment.
        pytest.param(
            b"# coding: utf-7 +AAo-import playwright\nx = 1\n", False, id="import-only"
        ),
        # Only a script reads the import: an import joins it to the coding line.
        pytest.param(
            b"# coding: unicode-escape \\\nimport playwright\n", True, id="script-only"
        ),
        # An import decodes the `\0` to a NUL and reads no further: only the second text names
        # playwright, or says `import`.
        pytest.param(
            b"# coding: unicode-escape \\0\nimport playwright\n", True, id="nul-head"
        ),
        # After this designation and a shift-out, the next line decodes after a CR and not
        # after an LF: an import, which makes the CR an LF first, refuses the file.
        pytest.param(
            b"# coding: iso2022_kr\n# \x1b$(C\x0e\rimport playwright\n",
            True,
            id="iso2022_kr-shift-out-cr",
        ),
    ],
)
def test_each_reading_of_a_crafted_coding_line_is_searched(tmp_path, raw, script):
    folder = tmp_path / "agent"
    assert _script_imports(folder, raw) is script
    assert _compiled_imports(raw, "playwright") is not script
    assert browser_tools_found(folder) == ["playwright"]


# Only a script reads what follows each: an import joins it to the coding line, or reads no
# further than the NUL the `\0` decodes to, where no `import` is left either.
_SCRIPT_ONLY_HEADS = [
    pytest.param(b"# coding: unicode-escape \\\n", id="joined"),
    pytest.param(b"# coding: unicode-escape \\0\n", id="nul"),
]


@pytest.mark.parametrize("head", _SCRIPT_ONLY_HEADS)
def test_the_other_readers_search_both_readings(tmp_path, head):
    traced = head + b"import traceai_openai\n"
    assert "traceai_openai" not in _names_python_runs(traced)
    _write(tmp_path / "traced", traced)
    assert detect_instrumentation(tmp_path / "traced") == "traceai"
    agent = head + b"import common.web\n"
    assert "common" not in _names_python_runs(agent)
    repo = _shared(tmp_path / "walk", agent)
    assert repo / "common" / "web.py" in imported_by_agent(repo, "agents/a")


def test_a_browser_marker_only_the_second_reading_holds_is_found(tmp_path):
    _write(tmp_path, b'# coding: unicode-escape \\0\nMCP = "@playwright/mcp"\n')
    assert browser_tools_found(tmp_path) == ["Playwright MCP"]


def _parse_charges(monkeypatch) -> list[str]:
    """The texts each ParseBudget, and not a TokenBudget, is charged for."""
    charged: list[str] = []
    real = python_source.ParseBudget.spend

    def spend(self, text):
        if type(self) is python_source.ParseBudget:
            charged.append(text)
        return real(self, text)

    monkeypatch.setattr(python_source.ParseBudget, "spend", spend)
    return charged


@pytest.mark.parametrize(
    "reader, name",
    [
        ("browser", b"playwright"),
        ("instrumentation", b"traceai_openai"),
        ("walk", b"common.web"),
    ],
)
def test_each_reading_is_charged_to_the_parse_total(
    tmp_path, monkeypatch, reader, name
):
    # Each reader searches for its own names: the file must name one to be read at all.
    raw = b"# coding: unicode-escape \\0\nimport " + name + b"\n"
    _, first, second = _decoded_readings(raw, _coding(raw), EscapeBudget())
    assert second is not None
    charged = _parse_charges(monkeypatch)
    repo = _shared(tmp_path, raw)
    if reader == "browser":
        browser_tools_found(repo / "agents" / "a")
    elif reader == "instrumentation":
        detect_instrumentation(repo / "agents" / "a")
    else:
        imported_by_agent(repo, "agents/a", parse_budget=python_source.ParseBudget())
    assert first in charged
    assert second in charged


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            _RAW_ESCAPED + b"import \\u0074raceai_openai\n", id="raw-unicode-escape"
        ),
        pytest.param(_UTF7 + b"import +AHQ-raceai_openai\n", id="utf-7"),
        pytest.param(_in("mac-arabic", b"import traceai\xdfopenai\n"), id="mac-arabic"),
        pytest.param(
            _in("iso2022_jp", b"import trace\x1b(Bai_openai\n"), id="iso2022_jp"
        ),
        pytest.param(_in("hz", b"import trace~{~}ai_openai\n"), id="hz"),
    ],
)
def test_traceai_spelled_in_another_codec_is_found(tmp_path, raw):
    assert "traceai_openai" in _names_python_runs(raw)
    _write(tmp_path, raw)
    assert detect_instrumentation(tmp_path) == "traceai"


@pytest.mark.parametrize(
    "agent",
    [
        # hz joins a `~` and the line end after it, once Python has made the CRLF an LF.
        pytest.param(b"# coding: hz\r\nimport commo~\r\nn.web\r\n", id="hz-crlf"),
        pytest.param(_ESCAPED + b"import \\x63ommon.web\n", id="unicode-escape"),
        # Decoded whole, the NUL refuses the parse; Python reads up to it.
        pytest.param(_UTF7 + b"import +AGM-ommon.web\n+AAA-\n", id="utf-7"),
    ],
)
def test_the_walk_follows_an_import_spelled_in_another_codec(tmp_path, agent):
    assert "common.web" in _names_python_runs(agent)
    repo = _shared(tmp_path, agent)
    assert repo / "common" / "web.py" in imported_by_agent(repo, "agents/a")
    assert browser_tools_in_agent(repo, "agents/a") == ["playwright"]
    reached = code_the_agent_reaches(repo, "agents/a")
    assert detect_instrumentation(repo / "agents" / "a", reached) == "traceai"


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(
            _in("iso2022_jp", b"import play\x1b(Bwright\n#\x1b"), id="iso2022_jp"
        ),
        pytest.param(_in("hz", b"import play~{~}wright\n#~"), id="hz"),
        pytest.param(_ESCAPED + b"import \\x70laywright\n#\\", id="unicode-escape"),
    ],
)
def test_a_file_ending_in_an_open_sequence_is_read(tmp_path, raw):
    assert "playwright" in _names_python_runs(raw)
    _write(tmp_path, raw)
    assert browser_tools_found(tmp_path) == ["playwright"]


@pytest.mark.parametrize(
    "raw, found",
    [
        pytest.param(
            _UTF7 + b"import +AHA-laywright\n+AAA-\nimport os\n",
            True,
            id="utf-7-before",
        ),
        pytest.param(
            _UTF7 + b"import os\n+AAA-\nimport +AHA-laywright\n",
            False,
            id="utf-7-after",
        ),
        pytest.param(
            _RAW_ESCAPED + b"import \\u0070laywright\n\\u0000\nimport os\n",
            True,
            id="raw-unicode-escape-before",
        ),
        pytest.param(
            _RAW_ESCAPED + b"import os\n\\u0000\nimport playwright\n",
            False,
            id="raw-unicode-escape-after",
        ),
        pytest.param(
            _ESCAPED + b"import \\x70laywright\n\\x00\nimport os\n",
            True,
            id="unicode-escape-before",
        ),
        pytest.param(
            _ESCAPED + b"import os\n\\x00\nimport playwright\n",
            False,
            id="unicode-escape-after",
        ),
    ],
)
def test_a_decoded_nul_ends_the_text(tmp_path, raw, found):
    assert ("playwright" in _names_python_runs(raw)) is found
    _write(tmp_path, raw)
    assert browser_tools_found(tmp_path) == (["playwright"] if found else [])


@pytest.mark.parametrize(
    "raw, name",
    [
        # A line that starts with a decoded CR, before a token that can't start a statement.
        pytest.param(
            _UTF7 + b"import playwright\n+AA0-1\n", "playwright", id="cr-then-1"
        ),
        pytest.param(
            _UTF7 + b"import playwright\n+AA0-@dec\ndef f(): pass\n",
            "playwright",
            id="cr-then-decorator",
        ),
        pytest.param(
            _ESCAPED + b"import playwright\n\\r(1)\n",
            "playwright",
            id="ue-cr-then-call",
        ),
        pytest.param(
            _UTF7 + b"import traceai_openai\n+AA0-1\n",
            "traceai_openai",
            id="cr-then-1-traceai",
        ),
        pytest.param(
            _UTF7 + b"import common.web\n+AA0-1\n", "common.web", id="cr-then-1-walk"
        ),
        # A decoded CR in a comment, then code.
        pytest.param(
            _UTF7 + b"x = f  # c+AA0-(1); import playwright\n",
            "playwright",
            id="comment-cr",
        ),
        pytest.param(
            _ESCAPED + b"x = f  # c\\r(1); import playwright\n",
            "playwright",
            id="ue-comment-cr",
        ),
        pytest.param(
            _UTF7 + b"x = f  # c+AA0-(1); import traceai_openai\n",
            "traceai_openai",
            id="comment-cr-traceai",
        ),
        # A line of blanks and a `\`, then a line that starts with a decoded CR.
        pytest.param(
            _UTF7 + b"import playwright\n    \\\n    +AA0-1\n",
            "playwright",
            id="continued-blank-then-cr",
        ),
        pytest.param(
            _RAW_ESCAPED + b"        \\\n\\u000d-c\nimport playwright\n",
            "playwright",
            id="rue-continued-blank-then-cr",
        ),
        # A `\` before a decoded CR and an LF: one line end, as Python 3.12 reads it.
        pytest.param(
            _UTF7 + b"x = 'a\\+AA0-\nb'\nimport playwright\n",
            "playwright",
            id="string-continued",
        ),
        pytest.param(
            _UTF7 + b"x = 'a\\+AA0-\nb'\nimport traceai_openai\n",
            "traceai_openai",
            id="string-continued-traceai",
        ),
        pytest.param(
            _UTF7 + b"import os, \\+AA0-\nplaywright\n",
            "playwright",
            id="line-continued",
        ),
        pytest.param(
            _ESCAPED + b"import os, \\\\\\r\nplaywright\n",
            "playwright",
            id="ue-line-continued",
        ),
        pytest.param(
            _ESCAPED + b"import \\\\\\r\n\\x70laywright\n",
            "playwright",
            id="ue-line-continued-escaped-name",
        ),
        pytest.param(
            _RAW_ESCAPED + b"import os, \\u005c\\u000d\n\\u0070laywright\n",
            "playwright",
            id="rue-line-continued",
        ),
        pytest.param(
            _UTF7 + b"import \\+AA0-\n+AHA-laywright\n",
            "playwright",
            id="line-continued-spelled-name",
        ),
    ],
)
@_FROM_312
def test_a_decoded_cr_reads_as_todays_code_reads_it(tmp_path, raw, name):
    assert name in _names_python_runs(raw)
    assert _scan(tmp_path, raw, name) == _found(name)


@pytest.mark.parametrize(
    "raw, name",
    [
        # Python keeps the CR in the string; the parser ends the line there.
        pytest.param(
            _ESCAPED + b'x = "\\r"\nimport playwright\n',
            "playwright",
            id="cr-in-string",
            marks=_KEEPS_A_DECODED_CR,
        ),
        # Python ends the comment at the CR and reads on; the parser keeps it in the comment.
        pytest.param(
            _UTF7 + b"import os  # hi+AA0-, playwright\n",
            "playwright",
            id="comment-cr",
            marks=(_FROM_312, _KEEPS_A_DECODED_CR),
        ),
        pytest.param(
            _ESCAPED + b"x = 1  # hi\\r; import playwright\n",
            "playwright",
            id="ue-comment-cr",
            marks=(_FROM_312, _KEEPS_A_DECODED_CR),
        ),
        # Python reads a line a decoded CR starts as blank, inside the block; the parser as
        # a line at column 0, which ends it.
        pytest.param(
            _UTF7 + b"def f():\n    a = 1\n+AA0-(1)\n    import +AHA-laywright\n",
            "playwright",
            id="cr-in-a-block",
            marks=(_FROM_312, _KEEPS_A_DECODED_CR),
        ),
        pytest.param(
            _UTF7
            + b"try:\n    import +AHA-laywright\n+AA0-[0]\nexcept ImportError:\n    pass\n",
            "playwright",
            id="cr-in-a-try",
            marks=(_FROM_312, _KEEPS_A_DECODED_CR),
        ),
        pytest.param(
            _UTF7
            + b"try:\n    import +AHQ-raceai_openai\n+AA0-[0]\nexcept ImportError:\n    pass\n",
            "traceai_openai",
            id="cr-in-a-try-traceai",
            marks=(_FROM_312, _KEEPS_A_DECODED_CR),
        ),
        pytest.param(
            _UTF7 + b"+AA0-\\+AA0-\n import playwright\n",
            "playwright",
            id="cr-then-continued",
            marks=(_FROM_312, _KEEPS_A_DECODED_CR),
        ),
        # More failed cookie places than _coding reads before the cookie: read as a refused
        # codec's file, searched as bytes.
        pytest.param(
            b"#" + b"coding:#" * 65 + b" coding: utf-7\nimport +AHA-laywright\n",
            "playwright",
            id="cookie-after-65-failed-places",
        ),
    ],
)
def test_the_known_misses_stay_unread(tmp_path, raw, name):
    assert name in _names_python_runs(raw)
    assert _scan(tmp_path, raw, name) == _missed(name)


@pytest.mark.parametrize(
    "hidden, scan, found, missed",
    [
        pytest.param(
            _UTF7 + b"import +AHA-laywright\n",
            browser_tools_found,
            ["playwright"],
            [],
            id="browser",
        ),
        pytest.param(
            _UTF7 + b"import +AHQ-raceai_openai\n",
            detect_instrumentation,
            "traceai",
            "none",
            id="instrumentation",
        ),
    ],
)
def test_decode_first_files_share_the_scans_total(
    tmp_path, monkeypatch, hidden, scan, found, missed
):
    # Names nothing either scan looks for, but is decoded first all the same.
    first = _UTF7 + b"import os\n# " + b"-" * 40 + b"\n"
    # No larger, so a total of its own would let it through.
    assert len(hidden) <= len(first)
    for total, expected in ((len(first), missed), (len(first) + len(hidden), found)):
        monkeypatch.setattr(python_source, "LARGEST_ESCAPED_TOTAL", total)
        folder = tmp_path / str(total)
        # Both scans read in name order: a.py first.
        _write(folder, first, "a.py")
        _write(folder, hidden, "b.py")
        assert scan(folder) == expected


def test_past_the_total_a_file_reads_as_today(tmp_path, monkeypatch):
    monkeypatch.setattr(python_source, "LARGEST_ESCAPED_TOTAL", 0)
    _write(tmp_path / "plain", _UTF7 + b"import playwright\n")
    _write(tmp_path / "spelled", _UTF7 + b"import +AHA-laywright\n")
    assert browser_tools_found(tmp_path / "plain") == ["playwright"]
    assert browser_tools_found(tmp_path / "spelled") == []


def _tool_check(folder: Path) -> object:
    """The tool check over three named modules, m0 to m2, each a file in ``folder``."""
    from fi.alk.harness.contract import AgentContract
    from fi.alk.harness.source_tool_evidence import tool_evidence_problems

    entries = [
        {"tool": f"t{n}", "mode": "import", "module": f"m{n}", "callable": "f"}
        for n in range(3)
    ]
    contract = AgentContract.model_validate({"agent": "a", "tool_entrypoints": entries})
    return tool_evidence_problems(contract, folder)


@pytest.mark.parametrize(
    "reader, scan, names",
    [
        (
            "browser scan",
            browser_tools_found,
            ["a_pad00.py", "a_pad01.py", "a_pad02.py"],
        ),
        ("instrumentation scan", detect_instrumentation, ["a0.py", "a1.py", "a2.py"]),
        ("tool check", _tool_check, ["m0.py", "m1.py", "m2.py"]),
        (
            "import walk",
            lambda folder: imported_by_agent(
                folder.parent.parent.resolve(), "agents/a"
            ),
            ["a0.py", "a1.py", "a2.py"],
        ),
    ],
)
def test_the_total_running_out_is_logged_once_in_counts(
    tmp_path, monkeypatch, caplog, reader, scan, names
):
    pad = _UTF7 + b"x = 1\n" * 10
    monkeypatch.setattr(python_source, "LARGEST_ESCAPED_TOTAL", 2 * len(pad))
    folder = tmp_path / "agents" / "a"
    # Read in name order: two pads fill the total, and the rest are past it.
    for name in names:
        _write(folder, pad, name)
    _write(folder, _UTF7 + b"import +AHA-laywright\n", "z_main.py")
    with caplog.at_level(logging.WARNING, logger=python_source.__name__):
        scan(folder)
    logged = [r for r in caplog.records if r.name == python_source.__name__]
    assert [(r.levelno, r.args) for r in logged] == [
        (logging.WARNING, (reader, 2 * len(pad), 2))
    ]
    # Counts alone: never a path or the code. Past the total some readers skip a file, and
    # others search its bytes: either way a name can be missed.
    message = logged[0].getMessage()
    assert message.endswith("a name the rest spell in their codec can be missed")
    assert all(part not in message for part in ("a_pad", "z_main", "laywright", "tmp"))


def test_a_total_that_lasts_logs_nothing(caplog):
    budget = EscapeBudget(10)
    with caplog.at_level(logging.WARNING, logger=python_source.__name__):
        assert budget.spend(b"x" * 10)
    assert caplog.records == []


@pytest.mark.parametrize(
    "codec, word",
    [
        ("latin-1", "café"),
        ("cp1252", "€"),
        ("shift_jis", "日本"),
        ("gb18030", "中文"),
        ("euc_kr", "한국"),
    ],
)
def test_a_raw_safe_codec_reads_as_today(tmp_path, codec, word):
    # CRLF and no final LF: decoding first would end both lines with an LF.
    raw = f"# coding: {codec}\r\nimport playwright\r\nNAME = '{word}'".encode(codec)
    budget = EscapeBudget()
    assert _decoded_readings(raw, _coding(raw), budget)[:2] == (False, None)
    path = _write(tmp_path, raw)
    assert read_module(path, needles=(b"playwright",), escapes=budget) == raw.decode(
        codec
    )
    assert budget.left == python_source.LARGEST_ESCAPED_TOTAL


def test_without_a_total_every_codec_reads_as_today(tmp_path):
    unended = _UTF7 + b"import +AHA-laywright"
    assert _decoded_readings(unended, _coding(unended), None)[:2] == (False, None)
    assert read_module(_write(tmp_path / "utf-7", unended)) == unended.decode("utf-7")
    escaped = _ESCAPED + b"import \\x70laywright\n"
    assert read_module(_write(tmp_path / "unicode-escape", escaped)) is None


@pytest.mark.parametrize("codec", ["rot13", "base64"])
def test_a_non_text_codec_is_never_charged(tmp_path, codec):
    raw = _in(codec, b"import playwright\n")
    try:
        tokenize.detect_encoding(io.BytesIO(raw).readline)
    except (SyntaxError, LookupError):
        # From 3.14 detect_encoding decodes the coding line in its codec, which refuses it.
        pytest.skip(f"this Python refuses a {codec} coding line before reading it")
    assert _coding(raw) is not None
    budget = EscapeBudget()
    assert read_module(_write(tmp_path, raw), escapes=budget) is None
    assert budget.left == python_source.LARGEST_ESCAPED_TOTAL


def test_a_text_codec_whose_decode_raises_lookup_error_gives_none():
    def decode(data, errors="strict"):
        raise LookupError("no table")

    stub = codecs.CodecInfo(codecs.utf_8_encode, decode, name="lookup-failing")

    def search(name):
        return stub if name == "lookup_failing" else None

    codecs.register(search)
    try:
        coding = ("lookup-failing", codecs.lookup("lookup-failing"))
        assert _decoded_readings(b"x = 1\n", coding, EscapeBudget())[:2] == (True, None)
    finally:
        codecs.unregister(search)


def test_the_walk_keeps_the_decoded_reading_beside_an_escaped_cr(tmp_path):
    # Only the decoded reading spells the import; the escaped CR makes the walk read both.
    agent = _ESCAPED + b"X = 1  # \\r\nimport \\x63ommon.web\n"
    assert "common.web" in _names_python_runs(agent)
    repo = _shared(tmp_path, agent)
    assert repo / "common" / "web.py" in imported_by_agent(repo, "agents/a")


def test_a_file_read_two_ways_is_charged_for_each_reading(tmp_path):
    pad = b"# " + b"x" * 76 + b"\n"
    agent = _ESCAPED + b'SEP = "\\r"\nimport common.web\n' + pad * 600
    repo = _shared(tmp_path, agent)
    _write(repo / "agents" / "a", b"import common.tools\n" + pad * 600, "b.py")
    _write(repo / "common", b"", "tools.py")
    stopped: list[bool] = []
    budget = python_source.ParseBudget()
    found = imported_by_agent(
        repo,
        "agents/a",
        parse_limit=python_source.LARGEST_PARSED_MODULE,
        parse_budget=budget,
        stopped=stopped,
    )
    # Each reading is charged as it's parsed: the decoded one, where `\\r` is one character, and
    # the UTF-8 one, where it's two.
    assert repo / "common" / "tools.py" in found
    others = [repo / "agents" / "a" / "b.py", repo / "common" / "web.py"]
    assert budget.left == python_source.LARGEST_PARSED_TOTAL - (
        2 * len(agent) - 1 + sum(p.stat().st_size for p in others)
    )
    # The decoded reading, the one Python runs, is refused: its CR splits the string. The walk
    # can't be sure it followed what Python runs, so it says it stopped.
    assert stopped == [True]


def test_the_union_is_for_unicode_escape_only(tmp_path):
    # Read as UTF-8, `+ACM-` is arithmetic and the import is code; decoded, it's a `#`.
    agent = _UTF7 + b'"""+AA0-"""\nx = 1 +ACM- 2; import common.web\n'
    assert "common" not in _names_python_runs(agent)
    repo = _shared(tmp_path, agent)
    assert repo / "common" / "web.py" not in imported_by_agent(repo, "agents/a")


def test_a_failed_decode_is_still_charged():
    bad = _UTF7 + b"import os\n# +\xff\n"
    budget = EscapeBudget()
    assert _decoded_readings(bad, _coding(bad), budget)[:2] == (True, None)
    assert budget.left == python_source.LARGEST_ESCAPED_TOTAL - len(bad)


@pytest.mark.parametrize(
    "agent",
    [
        pytest.param(
            _ESCAPED + b"import common.web  # see \\r, x\n",
            id="comment",
            marks=_KEEPS_A_DECODED_CR,
        ),
        pytest.param(
            _ESCAPED + b'SEP = "\\r"\nimport common.web\n',
            id="string",
            marks=_KEEPS_A_DECODED_CR,
        ),
        pytest.param(
            _ESCAPED + b"CR = b'\\r'\nimport common.web\n",
            id="bytes",
            marks=_KEEPS_A_DECODED_CR,
        ),
        # A triple-quoted string holds a line end, so Python runs this one either way.
        pytest.param(
            _ESCAPED + b'"""\\r"""\nimport \\x63ommon.web\n', id="and-an-escaped-import"
        ),
        pytest.param(
            _ESCAPED + b'SEP = "\\r"  # caf\xe9\nimport common.web\n',
            id="and-a-latin-1-byte",
            marks=_KEEPS_A_DECODED_CR,
        ),
    ],
)
def test_the_walk_still_follows_a_unicode_escape_file_with_an_escaped_cr(
    tmp_path, agent
):
    # Decoded, the CR breaks the parse; read as UTF-8 as well, the file is followed.
    assert "common.web" in _names_python_runs(agent)
    repo = _shared(tmp_path, agent)
    assert repo / "common" / "web.py" in imported_by_agent(repo, "agents/a")
    assert browser_tools_in_agent(repo, "agents/a") == ["playwright"]
    reached = code_the_agent_reaches(repo, "agents/a")
    assert detect_instrumentation(repo / "agents" / "a", reached) == "traceai"


def test_a_cookie_with_many_coding_words_after_it_is_still_decoded_first(tmp_path):
    raw = b"# coding: utf-7" + b" coding" * 65 + b"\nimport +AHA-laywright\n"
    assert "playwright" in _names_python_runs(raw)
    _write(tmp_path, raw)
    assert browser_tools_found(tmp_path) == ["playwright"]


def test_the_walk_skips_a_python_file_it_cant_decode(tmp_path):
    # The bad byte in a comment, so read as UTF-8 the import would still parse.
    agent = _UTF7 + b"import common.web\n# +\xff\n"
    with pytest.raises(SyntaxError):
        compile(agent, "<agent>", "exec", dont_inherit=True)
    repo = _shared(tmp_path, agent)
    assert repo / "common" / "web.py" not in imported_by_agent(repo, "agents/a")


def test_the_walk_reads_scripts_as_before(tmp_path):
    folder = tmp_path.resolve() / "agents" / "a"
    _write(folder, b"", "util.js")
    _write(folder, b"const u = require('./util')\n", "index.js")
    assert folder / "util.js" in imported_by_agent(tmp_path, "agents/a")
    # A coding line means nothing to a script: utf-7 would spell `./util` here.
    _write(folder, _UTF7 + b"const u = require('./u+AHQ-il')\n", "index.js")
    assert folder / "util.js" not in imported_by_agent(tmp_path, "agents/a")


def test_the_walks_total_is_spent_across_one_walk(tmp_path, monkeypatch):
    first = _ESCAPED + b"import os\n# " + b"-" * 20 + b"\n"
    # Past the total it's read as UTF-8, where the escape spells nothing.
    second = _ESCAPED + b"import \\x63ommon.web\n"
    assert len(second) <= len(first)
    repo = _shared(tmp_path, b"")
    folder = repo / "agents" / "a"
    # The walk reads its folder's files in name order.
    _write(folder, first, "a.py")
    _write(folder, second, "b.py")
    for total, followed in ((len(first), False), (len(first) + len(second), True)):
        monkeypatch.setattr(python_source, "LARGEST_ESCAPED_TOTAL", total)
        assert (
            repo / "common" / "web.py" in imported_by_agent(repo, "agents/a")
        ) is followed


def _punycode_decodes(monkeypatch) -> list[bytes]:
    import encodings.punycode

    decoded: list[bytes] = []
    real = encodings.punycode.punycode_decode
    monkeypatch.setattr(
        encodings.punycode,
        "punycode_decode",
        lambda *args: decoded.append(bytes(args[0])) or real(*args),
    )
    return decoded


def test_the_walk_never_decodes_a_refused_codec(tmp_path, monkeypatch):
    import encodings.idna

    decoded = _punycode_decodes(monkeypatch)
    labels: list[bytes] = []
    real = encodings.idna.ToUnicode
    monkeypatch.setattr(
        encodings.idna, "ToUnicode", lambda label: labels.append(label) or real(label)
    )
    repo = _shared(tmp_path, b"# coding: punycode\nimport common.web\n-" + b"9" * 64)
    _write(
        repo / "agents" / "a",
        b"# coding: idna\nimport common.web\nHOST = 'xn--bcher-kva.example'\n",
        "host.py",
    )
    imported_by_agent(repo, "agents/a")
    # From 3.14 detect_encoding decodes the coding line it's handed in its codec: _coding hands
    # it the line cut to its cookie, padded with spaces and ended with an LF, a few bytes. The
    # file's body is never decoded.
    assert all(line.rstrip(b" \n") == b"#coding:punycode" for line in decoded)
    assert labels == []


def test_the_walk_reads_each_coding_line_once(tmp_path, monkeypatch):
    asked: list[bytes] = []
    real = python_source._coding
    monkeypatch.setattr(
        python_source, "_coding", lambda raw: asked.append(raw) or real(raw)
    )
    repo = _shared(tmp_path, b"import common.web\n")
    _write(repo / "common", b"# a package\n", "__init__.py")
    _write(repo / "agents" / "a", b"const u = require('./util')\n", "index.js")
    _write(repo / "agents" / "a", b"", "util.js")
    imported_by_agent(repo, "agents/a")
    python = [
        repo / "agents" / "a" / "agent.py",
        repo / "common" / "__init__.py",
        repo / "common" / "web.py",
    ]
    assert sorted(asked) == sorted(path.read_bytes() for path in python)


def test_the_walk_parses_each_files_own_readings(tmp_path, monkeypatch):
    # The second text a crafted coding line gives belongs to its own file: a later file never
    # parses it again.
    from fi.alk.harness import credentials

    parsed: list[str] = []
    real = credentials._python_imports
    monkeypatch.setattr(
        credentials,
        "_python_imports",
        lambda reading, *args: parsed.append(reading) or real(reading, *args),
    )
    repo = _shared(tmp_path, b"")
    _write(repo / "agents" / "a", b"# coding: unicode-escape \\0\nimport common.web\n", "a.py")
    _write(repo / "agents" / "a", b"import os\n", "b.py")
    assert repo / "common" / "web.py" in imported_by_agent(repo, "agents/a")
    # a.py is read twice, the decoded text cut at its NUL and the text a script reads; b.py once.
    assert sum("unicode-escape" in reading for reading in parsed) == 2
    assert sum("import os" in reading for reading in parsed) == 1


def test_ci_runs_this_file():
    workflows = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "sdk-smoke.yml"
    )
    assert "tests/harness/test_python_source_codecs.py" in workflows.read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    "raw",
    [
        # A fullwidth `ｐ`, which the parser folds to `p`; the needle is in a comment's bytes.
        pytest.param(_UTF7 + b"import +/1A-laywright\n# +playwrightA-\n", id="utf-7"),
        pytest.param(
            b"# coding: hz\nimport ~{#p#l#a#y#w#r#i#g#h#t~}\n# ~{playwright~}\n",
            id="hz",
        ),
    ],
)
def test_a_raw_needle_still_lets_the_file_in(tmp_path, raw):
    assert "playwright" in _names_python_runs(raw)
    _write(tmp_path, raw)
    assert browser_tools_found(tmp_path) == ["playwright"]


# Every byte form that could join, end or start a name when it decodes.
_NAME_OR_LINE = frozenset(string.ascii_letters + string.digits + "_." + "\0\r\n")
_INCOMPLETE = frozenset({"incomplete multibyte sequence", "unexpected end of data"})
_DEEPEST_FORM = 4
# Complete forms by length, for the codecs whose long forms are enumerated by structure.
_UTF8_FORMS = {
    # Unicode's scalar values by length: U+0000-U+007F, U+0080-U+07FF, U+0800-U+FFFF less the
    # 2,048 surrogates, and U+10000-U+10FFFF. utf-8-sig's byte-order mark is a three-byte form.
    1: 0x80,
    2: 0x800 - 0x80,
    3: 0x10000 - 0x800 - 0x800,
    4: 0x110000 - 0x10000,
}
_FORMS = {
    "utf-8": _UTF8_FORMS,
    "utf-8-sig": _UTF8_FORMS,
    # gb18030 spells each scalar value once: ASCII in one byte, 126 leads (81-FE) by 190 trails
    # (40-7E, 80-FE) in two, and in four the rest of the plane below U+10000 (less the
    # surrogates) and every value above it.
    "gb18030": {
        1: 0x80,
        2: 126 * 190,
        4: (0x10000 - 0x800 - 0x80 - 126 * 190) + (0x110000 - 0x10000),
    },
    # CPython's KS X 1001 table (_codecs_kr.c): 2,350 Hangul, 4,888 Hanja and 987 others in two
    # bytes (the filler, A4 D4, starts the eight-byte form instead; the euro and registered signs
    # came in 1998), and in eight the 19 * 21 * 28 modern syllables the make-up forms compose.
    "euc_kr": {1: 0x80, 2: 2350 + 4888 + 987, 8: 19 * 21 * 28},
}


def _accepted_codecs() -> set[str]:
    """Every codec Python accepts on a coding line, by its codec info's name."""
    names = set(encodings.aliases.aliases) | set(encodings.aliases.aliases.values())
    names |= {module.name for module in pkgutil.iter_modules(encodings.__path__)}
    accepted: set[str] = set()
    for name in names - {"aliases"}:
        try:
            codec = codecs.lookup(name).name
            compile(
                f"# coding: {name}\nx = 1\n".encode(),
                "<coding>",
                "exec",
                dont_inherit=True,
            )
        except (LookupError, SyntaxError):
            continue
        accepted.add(codec)
    return accepted


def _read(codec: str, form: bytes) -> tuple[str, str | None]:
    """Whether ``form`` is complete, incomplete or refused in ``codec``, and its text."""
    try:
        return "complete", form.decode(codec)
    except UnicodeDecodeError as error:
        if error.reason in _INCOMPLETE and error.end == len(form):
            return "incomplete", None
        return "refused", None


class _Sweep:
    """Every complete byte form of one codec, found depth-first from each first byte, and those
    that could spell a name from other bytes."""

    def __init__(self, codec: str) -> None:
        self.codec = codec
        self.forms: collections.Counter[int] = collections.Counter()
        self.unsafe: list[str] = []

    def walk(self, prefix: bytes = b"") -> None:
        for byte in range(256):
            form = prefix + bytes((byte,))
            if self._by_structure(form):
                continue
            state, text = _read(self.codec, form)
            if state == "complete":
                self._check(form, text)
            elif state == "incomplete":
                if len(form) == _DEEPEST_FORM:
                    self.unsafe.append(f"{form.hex()}: still incomplete")
                else:
                    self.walk(form)

    def _by_structure(self, form: bytes) -> bool:
        # These decoders ask for a whole long form before they read its bytes, or would cost a
        # tree of every byte at each place, so their long forms are built from their ranges.
        if (
            self.codec in ("utf-8", "utf-8-sig")
            and len(form) == 1
            and 0xE0 <= form[0] <= 0xF4
        ):
            self._utf8(form, 3 if form[0] < 0xF0 else 4)
        elif self.codec == "gb18030" and len(form) == 2 and 0x30 <= form[1] <= 0x39:
            for third in range(0x81, 0xFF):
                for fourth in range(0x30, 0x3A):
                    self._leaf(form + bytes((third, fourth)))
        elif self.codec == "euc_kr" and form == b"\xa4\xd4":
            for initial in range(0xA1, 0xFF):
                for medial in range(0xA1, 0xFF):
                    for final in range(0xA1, 0xFF):
                        self._leaf(
                            bytes(
                                (0xA4, 0xD4, 0xA4, initial, 0xA4, medial, 0xA4, final)
                            )
                        )
        else:
            return False
        return True

    def _utf8(self, prefix: bytes, length: int) -> None:
        # Only a continuation byte, 80-BF, can follow a lead.
        for byte in range(0x80, 0xC0):
            form = prefix + bytes((byte,))
            if len(form) == length:
                self._leaf(form)
            elif _read(self.codec, form)[0] == "incomplete":
                self._utf8(form, length)

    def _leaf(self, form: bytes) -> None:
        state, text = _read(self.codec, form)
        if state == "complete":
            self._check(form, text)
        elif state == "incomplete":
            self.unsafe.append(f"{form.hex()}: incomplete at its full length")

    def _check(self, form: bytes, text: str | None) -> None:
        self.forms[len(form)] += 1
        if len(form) == 1 and text == chr(form[0]):
            return
        if not text:
            # A byte-order mark is read only at the file's start, before any name.
            if not (self.codec == "utf-8-sig" and form == codecs.BOM_UTF8):
                self.unsafe.append(f"{form.hex()}: decodes to nothing")
        elif not _NAME_OR_LINE.isdisjoint(text):
            self.unsafe.append(f"{form.hex()}: decodes to {text!r}")


def _outside_the_structure() -> list[str]:
    """Each form with a byte the structural ranges leave out that isn't refused: a decoder that
    took one would have forms the sweep never tried."""
    taken: list[str] = []
    for lead in range(0x81, 0xFF):
        for digit in range(0x30, 0x3A):
            for third in (*range(0x00, 0x81), 0xFF):
                for fourth in (0x30, 0x39):
                    form = bytes((lead, digit, third, fourth))
                    if _read("gb18030", form)[0] != "refused":
                        taken.append(f"gb18030 {form.hex()}")
            for fourth in (*range(0x00, 0x30), *range(0x3A, 0x100)):
                form = bytes((lead, digit, 0x81, fourth))
                if _read("gb18030", form)[0] != "refused":
                    taken.append(f"gb18030 {form.hex()}")
    # Each place of a make-up form after the filler: A4, or a jamo byte of A1-FE.
    syllable = bytes((0xA4, 0xD4, 0xA4, 0xA1, 0xA4, 0xBF, 0xA4, 0xA1))
    assert _read("euc_kr", syllable)[0] == "complete"
    for place in range(2, 8):
        allowed = {0xA4} if place % 2 == 0 else set(range(0xA1, 0xFF))
        for byte in sorted(set(range(256)) - allowed):
            form = syllable[:place] + bytes((byte,)) + syllable[place + 1 :]
            if _read("euc_kr", form)[0] != "refused":
                taken.append(f"euc_kr {form.hex()}")
    return taken


def test_every_raw_safe_codec_is_proven():
    accepted = _accepted_codecs()
    assert _RAW_SAFE_CODECS <= accepted
    assert len(_RAW_SAFE_CODECS) == 81
    assert _RAW_SAFE_CODECS.isdisjoint(_SPELLING_CODECS)
    unsafe: dict[str, list[str]] = {}
    forms: dict[str, dict[int, int]] = {}
    for codec in sorted(_RAW_SAFE_CODECS):
        sweep = _Sweep(codec)
        sweep.walk()
        if sweep.unsafe:
            unsafe[codec] = sweep.unsafe[:5]
        if codec in _FORMS:
            forms[codec] = dict(sweep.forms)
    assert unsafe == {}
    assert forms == _FORMS
    assert _outside_the_structure() == []
    # The escape codecs refuse a lone `\` rather than call it incomplete, so the sweep can't see
    # them; the disjointness above keeps them out. Every other spelling codec it must flag.
    for codec in sorted(_SPELLING_CODECS - {"unicode-escape", "raw-unicode-escape"}):
        sweep = _Sweep(codec)
        sweep.walk()
        assert sweep.unsafe, codec


def test_the_accepted_codecs_are_exactly_the_three_groups():
    # A codec a new Python adds fails here until it's put in one group or the other.
    assert _accepted_codecs() == _RAW_SAFE_CODECS | _SPELLING_CODECS | {"idna"}


def _codec_names() -> list[str]:
    """Every name a coding line may give a codec in."""
    names = set(encodings.aliases.aliases) | set(encodings.aliases.aliases.values())
    names |= {module.name for module in pkgutil.iter_modules(encodings.__path__)}
    return sorted(names - {"aliases"})


def _compiles(name: str) -> bool:
    try:
        compile(
            f"# coding: {name}\nx = 1\n".encode(), "<coding>", "exec", dont_inherit=True
        )
    except (LookupError, SyntaxError, ValueError):
        return False
    return True


def _script_only_codecs(folder: Path) -> dict[str, bytes]:
    """Every codec `python file.py` runs an import in whose coding line compile() refuses, by
    its codec info's name, with the file it ran. Python decodes such a file from the byte
    before the coding line's end and throws the first line it reads away, so the body is tried
    after each count of NUL bytes that could make that byte part of a whole character."""
    found: dict[str, bytes] = {}
    for name in _codec_names():
        try:
            codec = codecs.lookup(name)
            if not getattr(codec, "_is_text_encoding", True):
                # bytes.decode refuses it, as Python does on a coding line.
                continue
            encoder = codecs.getincrementalencoder(name)()
            # Past its first call an encoder writes no byte-order mark.
            encoder.encode("")
            body = encoder.encode("\nimport playwright\n", final=True)
            unit = len("\n\n".encode(name)) - len("\n".encode(name))
        except (LookupError, UnicodeError):
            continue
        if codec.name in found or _compiles(name):
            continue
        for pad in range(unit):
            raw = _in(name, b"\0" * pad + body)
            if _script_imports(folder, raw):
                found[codec.name] = raw
                break
    return found


# What a script runs a file in on 3.12 to 3.14 besides the EBCDIC codecs. Which of these runs
# varies by version: 3.14 also decodes the coding line in its codec, and refuses it when that
# fails.
_WIDE_OR_PUNYCODE = frozenset(
    {"utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "punycode"}
)


def test_every_codec_a_script_runs_is_read_as_it_runs(tmp_path):
    ran = _script_only_codecs(tmp_path / "probe")
    # A codec a new Python runs a script in fails here until it's put in one group or the other.
    assert set(_EBCDIC) <= set(ran) <= set(_EBCDIC) | _WIDE_OR_PUNYCODE
    for codec, raw in sorted(ran.items()):
        # punycode is refused by name and searched as bytes, where its letters are as written.
        _write(tmp_path / codec, raw)
        assert browser_tools_found(tmp_path / codec) == ["playwright"], codec
    # Every codec compile() accepts reads as before; the others are read as a script reads them.
    for name in _codec_names():
        if _compiles(name):
            assert python_source._reads_its_cookie(name), name
    for codec in set(ran) - {"punycode"}:
        assert not python_source._reads_its_cookie(codec), codec


# Each holds a needle in bytes its codec decodes to other characters, beside an import of
# playwright Python runs: the bytes let the file in, and the text read is Python's. Sorted by
# their bytes; two need Python 3.12's reading of a decoded CR.
_DECOYS = [
    (
        b"# coding: hz\n"
        b"# ~{playwright~}\n"
        b"import os, \\\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"import ~{playwright~}\n"
    ),
    (
        b"# coding: hz\n"
        b"import os, \\\r\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"import os\r\r\n"
        b"x = 1  # ~{playwright~}\r\n"
    ),
    (
        b"# coding: hz\n"
        b"try:\r    import ~{#p#l#a#y#w#r#i#g#h#t~}\rexcept E:\r    pass\rx = 1\rtry:\r    import ~{playwright~}\rexcept E:\r    pass"
    ),
    (
        b"# coding: hz\n"
        b"x = f  # c~{playwright~}(1); import ~{playwright~}\n"
        b"import os\n"
        b"def f():\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
    ),
    (
        b"# coding: hz\r\n"
        b"def f():\r\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"x = f  # c~{playwright~}(1); import ~{#p#l#a#y#w#r#i#g#h#t~}\r\n"
    ),
    (
        b"# coding: hz\r\n"
        b"import os, \\\r\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"x = f  # c~{#p#l#a#y#w#r#i#g#h#t~}(1); import ~{playwright~}\r\n"
    ),
    (
        b"# coding: hz\r\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"x = 1  # ~{playwright~}\r\r\n"
        b"import ~{playwright~}\r\r\n"
        b"import os, \\\r\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\r\n"
    ),
    (
        b"# coding: hz\r\n"
        b"try:\n"
        b"    import ~{playwright~}\n"
        b"except E:\n"
        b"    pass\n"
        b"def g():\n"
        b"    ~{playwright~}\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
    ),
    (
        b"# coding: hz\rdef f():\r\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"~{playwright~}"
    ),
    b"# coding: hz\rdef f():\r    import ~{#p#l#a#y#w#r#i#g#h#t~}\rimport ~{playwright~}  # ~{playwright~}\rdef f():\r    import ~{playwright~}\r",
    (
        b"# coding: hz\rdef g():\n"
        b"    ~{playwright~}\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
    ),
    (
        b"# coding: hz\rimport os  # ~{#p#l#a#y#w#r#i#g#h#t~}, ~{playwright~}\n"
        b"# ~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"try:\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"except E:\n"
        b"    pass\n"
        b"x = 1  # ~{#p#l#a#y#w#r#i#g#h#t~}"
    ),
    b"# coding: hz\rimport ~{#p#l#a#y#w#r#i#g#h#t~}  # ~{playwright~}",
    (
        b"# coding: hz\rimport ~{playwright~}  # ~\n"
        b"\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}  # ~{playwright~}\n"
        b"~\n"
        b"\n"
    ),
    (
        b"# coding: hz\rtry:\r\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\r\n"
        b"except E:\r\n"
        b"    pass\r\r\n"
        b"    \\\r\n"
        b"~{playwright~}1\r\n"
    ),
    b"# coding: hz\rx = '~{playwright~}'\rimport ~{#p#l#a#y#w#r#i#g#h#t~}",
    (
        b"# coding: hz\rx = 1\n"
        b"def f():\n"
        b"    import ~{playwright~}\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
    ),
    b"# coding: hz\rx = 1\rx = 1\rimport os\rdef f():\r    import ~{playwright~}\rtry:\r    import ~{#p#l#a#y#w#r#i#g#h#t~}\rexcept E:\r    pass\r",
    (
        b"# coding: hz\rx = 1  # ~~\r\r\n"
        b"import os\r\r\n"
        b"~{playwright~}\r\r\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}  # ~{#p#l#a#y#w#r#i#g#h#t~}\r\n"
    ),
    (
        b"# coding: iso2022_kr\r\n"
        b"x = 1\n"
        b"\x1b$)C\n"
        b"try:\n"
        b"    import \x0e#p#l#a#y#w#r#i#g#h#t\x0f\n"
        b"except E:\n"
        b"    pass\n"
        b"import \x0eplaywright\x0f"
    ),
    (
        b"# coding: utf-7\n"
        b"def g():\r\n"
        b"    +AAo-\r\n"
        b"    import +/1A-laywright\r\r\n"
        b"try:\r\n"
        b"    import +playwrightA-\r\n"
        b"except E:\r\n"
        b"    pass\r\n"
    ),
    (b"# coding: utf-7\ndef g():\r    +playwrightA-\r    import +/1A-laywright\r"),
    (
        b"# coding: utf-7\n"
        b"if a:\n"
        b"    pass\n"
        b"import +/1A-laywright\n"
        b"x = '+-'\n"
        b"import +playwrightA-  # +/1A-laywright\n"
    ),
    (b"# coding: utf-7\nimport os, \\\r+/1A-laywright\r+-+playwrightA-"),
    (
        b"# coding: utf-7\n"
        b"x = 1  # +-\rtry:\r    import +/1A-laywright\rexcept E:\r    pass\rx = '+-'\rtry:\r    import +playwrightA-\rexcept E:\r    pass"
    ),
    (b"# coding: utf-7\r\nimport +/1A-laywright\nx = 1  # +playwrightA-"),
    (b"# coding: utf-7\r\nimport +/1A-laywright  # +playwrightA-"),
    (
        b"# coding: utf-7\r\n"
        b"import os  # +-, +playwrightA-\r\r\n"
        b"import os, \\\r\n"
        b"+/1A-laywright\r\n"
    ),
    (b"# coding: utf-7\r\nimport os, \\\r+/1A-laywright\rx = '+playwrightA-'\r"),
    (
        b"# coding: utf-7\r\n"
        b"x = 1\rif a:\r    pass\rimport +/1A-laywright  # +/1A-laywright\rx = '+playwrightA-'\r"
    ),
    b"# coding: utf-7\r# +playwrightA-\rtry:\r    import +/1A-laywright\rexcept E:\r    pass+AHA",
    (b"# coding: utf-7\r# -\nx = 1\nimport +/1A-laywright  # +playwrightA-\n"),
    b"# coding: utf-7\r+playwrightA-x = 1\rtry:\r    import +/1A-laywright\rexcept E:\r    pass\rx = 1  # +playwrightA-\r",
    (
        b"# coding: utf-7\rif a:\n"
        b"    pass\n"
        b"import +/1A-laywright\n"
        b"'''+AHA'''\n"
        b"import os, \\\n"
        b"+playwrightA-+playwrightA-"
    ),
    b"# coding: utf-7\rimport +/1A-laywright\rtry:\r    import +playwrightA-\rexcept E:\r    pass\r",
    (
        b"# coding: utf-7\rtry:\r\n"
        b"    import +/1A-laywright\r\n"
        b"except E:\r\n"
        b"    pass\r\r\n"
        b"x = 1  # +playwrightA-"
    ),
    (
        b"# coding: utf-7\rx = f  # c+AA0ACg-(1); import +playwrightA-\r\r\n"
        b"try:\r\n"
        b"    import +/1A-laywright\r\n"
        b"except E:\r\n"
        b"    pass\r\r\n"
        b"x = 1\r\n"
    ),
    (
        b"# coding=hz\n"
        b"def f():\r\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"import os\r\r\n"
        b"import os, \\\r\n"
        b"~{playwright~}\r\r\n"
        b"x = 1"
    ),
    (
        b"# coding=hz\n"
        b"def g():\r    ~{playwright~}\r    import ~{#p#l#a#y#w#r#i#g#h#t~}\r"
    ),
    (
        b"# coding=hz\n"
        b"import os, \\\r\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"'''~~'''\r\r\n"
        b"def f():\r\n"
        b"    import ~{playwright~}\r\r\n"
        b"x = 1"
    ),
    (
        b"# coding=hz\n"
        b"import os, \\\r~{playwright~}\rimport ~{playwright~}\rimport ~{#p#l#a#y#w#r#i#g#h#t~}\r"
    ),
    (
        b"# coding=hz\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}\rtry:\r    import ~{playwright~}\rexcept E:\r    pass\r"
    ),
    (
        b"# coding=hz\n"
        b"import ~{playwright~}\n"
        b"import os, \\\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"if a:\n"
        b"    pass\n"
        b"if a:\n"
        b"    pass"
    ),
    (
        b"# coding=hz\n"
        b"import ~{playwright~}\r\r\n"
        b"import os, \\\r\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\r\n"
    ),
    (
        b"# coding=hz\n"
        b"x = 1\n"
        b"~{playwright~}\n"
        b"try:\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"except E:\n"
        b"    pass\n"
    ),
    pytest.param(
        (
            b"# coding=utf-7\n"
            b"+playwrightA-\r'''+-'''\rimport os, \\\r+/1A-laywright+AA0ACg-"
        ),
        marks=_FROM_312,
    ),
    pytest.param(
        (
            b"# coding=utf-7\n"
            b"def f():\n"
            b"    import +playwrightA-\n"
            b"+AA0-\n"
            b"def g():\n"
            b"    +AA0ACg-\n"
            b"    import +/1A-laywright\n"
        ),
        marks=_FROM_312,
    ),
    (
        b"# coding=utf-7\n"
        b"if a:\n"
        b"    x = 1\n"
        b"+AAo-\n"
        b"    import +/1A-laywright\n"
        b"def f():\n"
        b"    import +playwrightA-\n"
    ),
    (
        b"# coding=utf-7\n"
        b"import +playwrightA-  # +playwrightA-\r\r\n"
        b"import os\r\r\n"
        b"try:\r\n"
        b"    import +/1A-laywright\r\n"
        b"except E:\r\n"
        b"    pass\r\r\n"
        b"'''+AHA'''\r\n"
    ),
    (
        b"# coding=utf-7\n"
        b"import os, \\\n"
        b"+/1A-laywright\n"
        b"+/1A-laywrightx = 1\n"
        b"# +AAo-\n"
        b"def f():\n"
        b"    import +/1A-laywright+playwrightA-"
    ),
    (
        b"# coding=utf-7\n"
        b"x = 1  # +-\r\r\n"
        b"+/1A-laywright\r\r\n"
        b"'''+playwrightA-'''\r\r\n"
        b"try:\r\n"
        b"    import +/1A-laywright\r\n"
        b"except E:\r\n"
        b"    pass"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"# ~{playwright~}\r\r\n"
        b"try:\r\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\r\n"
        b"except E:\r\n"
        b"    pass\r\r\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"# ~{playwright~}\rimport ~{#p#l#a#y#w#r#i#g#h#t~}\r~{#p#l#a#y#w#r#i#g#h#t~}"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"if a:\r    pass\rimport ~{#p#l#a#y#w#r#i#g#h#t~}\rimport ~{#p#l#a#y#w#r#i#g#h#t~}~{playwright~}"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"import os, \\\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"import os, \\\n"
        b"~{playwright~}\n"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"'''~{playwright~}'''\n"
        b"x = 1\n"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"def f():\n"
        b"    import ~{playwright~}\n"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"import ~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"x = 1~\n"
        b"\n"
        b"import os, \\\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}\n"
        b"x = '~\n"
        b"'\n"
        b"import os, \\\n"
        b"~{playwright~}\n"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"import ~{playwright~}\r\r\n"
        b"def f():\r\n"
        b"    import ~{playwright~}\r\r\n"
        b"def f():\r\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"try:\n"
        b"    import ~{playwright~}\n"
        b"except E:\n"
        b"    pass\n"
        b"# ~~\n"
        b"import os, \\\n"
        b"~{#p#l#a#y#w#r#i#g#h#t~}"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: hz -*-\n"
        b"x = f  # c~{playwright~}(1); import ~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"def f():\r\n"
        b"    import ~{#p#l#a#y#w#r#i#g#h#t~}\r\r\n"
        b"x = 1"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: iso2022_kr -*-\n"
        b"x = 1\x1b$)C\rtry:\r    import \x0eplaywright\x0f\rexcept E:\r    pass\rtry:\r    import \x0eplaywright\x0f\rexcept E:\r    pass\rimport \x0e#p#l#a#y#w#r#i#g#h#t\x0f  # \x1b\r"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: utf-7 -*-\n"
        b"+/1A-laywright\r\r\n"
        b"def g():\r\n"
        b"    +playwrightA-\r\n"
        b"    import +/1A-laywright"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: utf-7 -*-\n"
        b"def f():\r\n"
        b"    import +playwrightA-\r\r\n"
        b"import +/1A-laywright  # +/1A-laywright\r\n"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: utf-7 -*-\n"
        b"if a:\r\n"
        b"    pass\r\r\n"
        b"def g():\r\n"
        b"    +playwrightA-\r\n"
        b"    import +/1A-laywright\r\r\n"
        b"x = 1+AAo-"
    ),
    (
        b"#!/usr/bin/env python\n"
        b"# -*- coding: utf-7 -*-\n"
        b"import +playwrightA-  # +/1A-laywright\n"
        b"import +/1A-laywright  # +-"
    ),
]


@pytest.mark.parametrize(
    "raw", _DECOYS, ids=[f"decoy-{n:02}" for n in range(len(_DECOYS))]
)
def test_no_decoy_case_regresses(tmp_path, raw):
    assert "playwright" in _imported(ast.parse(raw))
    path = _write(tmp_path, raw)
    text = read_module(
        path, needles=agent_type_check._PYTHON_NAMES, escapes=EscapeBudget()
    )
    assert text is not None
    assert "playwright" in _imported(ast.parse(text))


def test_a_file_the_whole_decode_refuses_at_the_totals_edge_is_read_as_past_it(tmp_path):
    # The total lasts for the first decode and not the second: the file is read as any file
    # past the total is, not dropped.
    raw = b"# coding: iso2022_kr\n# \x1b$(C\x0e\rimport playwright\n"
    path = _write(tmp_path, raw)
    text = read_module(path, needles=(b"playwright",), escapes=EscapeBudget(len(raw) + 1))
    assert text is not None and "import playwright" in text
