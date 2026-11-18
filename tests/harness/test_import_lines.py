"""The line search reads the import lines of a Python file that was never parsed.

It must find every legal spelling of an import, say so (``capped``) where it can't be sure, and
cost time in proportion to the text, whatever the text is.
"""

from __future__ import annotations

import time

import pytest

from fi.alk.harness.credentials import _script_imports, _without_type_imports
from fi.alk.harness.import_lines import import_lines

_FORMS = [
    # Each row: source, names (level, name), whether the search says it can't be sure.
    pytest.param("import os\n", [(0, "os")], False, id="plain"),
    pytest.param(
        "import a.b, c\n", [(0, "a.b"), (0, "c")], False, id="dotted-and-list"
    ),
    pytest.param("import a as b, c as d\n", [(0, "a"), (0, "c")], False, id="as-list"),
    pytest.param("import a . b\n", [(0, "a.b")], False, id="spaces-around-the-dot"),
    pytest.param(
        "import os, \\\ncommon.util\n",
        [(0, "os"), (0, "common.util")],
        False,
        id="list-continued",
    ),
    pytest.param(
        "import os, \\\n   common.util, \\\n   x\n",
        [(0, "os"), (0, "common.util"), (0, "x")],
        False,
        id="list-continued-twice",
    ),
    pytest.param(
        "from common import \\\nutil\n",
        [(0, "common"), (0, "common.util")],
        False,
        id="from-continued",
    ),
    pytest.param(
        "from.common import util\n",
        [(1, "common"), (1, "common.util")],
        False,
        id="no-space-after-from",
    ),
    pytest.param(
        "from .import util\n", [(1, "util")], False, id="no-space-before-import"
    ),
    pytest.param("from.import util\n", [(1, "util")], False, id="no-space-either-side"),
    pytest.param("from . import util\n", [(1, "util")], False, id="dot"),
    pytest.param("from .. import util\n", [(2, "util")], False, id="two-dots"),
    pytest.param("from ... import util\n", [(3, "util")], False, id="ellipsis"),
    pytest.param("from . . import util\n", [(2, "util")], False, id="dots-apart"),
    pytest.param(
        "from ..a.b import c\n", [(2, "a.b"), (2, "a.b.c")], False, id="relative-dotted"
    ),
    pytest.param(
        "from a . b import c\n",
        [(0, "a.b"), (0, "a.b.c")],
        False,
        id="from-spaced-dots",
    ),
    pytest.param(
        "from a import b as c, d as e\n",
        [(0, "a"), (0, "a.b"), (0, "a.d")],
        False,
        id="from-as-list",
    ),
    pytest.param(
        "from a import (b as c,\n d)\n",
        [(0, "a"), (0, "a.b"), (0, "a.d")],
        False,
        id="paren-list-over-lines",
    ),
    pytest.param(
        "from a import (\n    b,  # why\n\n    c,\n)\nimport z\n",
        [(0, "a"), (0, "a.b"), (0, "a.c"), (0, "z")],
        False,
        id="paren-list-with-comments-and-blank-line",
    ),
    pytest.param(
        "from a import (b); import z\n",
        [(0, "a"), (0, "a.b"), (0, "z")],
        False,
        id="after-paren",
    ),
    pytest.param("from a import *\n", [(0, "a")], False, id="star"),
    pytest.param(
        "from importlib import import_module\n",
        [(0, "importlib"), (0, "importlib.import_module")],
        False,
        id="module-named-import-something",
    ),
    pytest.param("import\ta\n", [(0, "a")], False, id="tab-after-import"),
    pytest.param("\timport\ta\n", [(0, "a")], False, id="tab-before-and-after"),
    pytest.param("\x0cimport a\n", [(0, "a")], False, id="form-feed-before"),
    pytest.param("import\x0ca\n", [(0, "a")], False, id="form-feed-after"),
    pytest.param(
        "from\ta\timport\tb\n", [(0, "a"), (0, "a.b")], False, id="tabs-in-from"
    ),
    pytest.param(
        "from a import\x0cb\n", [(0, "a"), (0, "a.b")], False, id="form-feed-in-from"
    ),
    pytest.param(
        "x = 1\rimport a\rimport b\r", [(0, "a"), (0, "b")], False, id="cr-line-ends"
    ),
    pytest.param(
        "x = 1\r\nimport a\r\nimport b\r\n", [(0, "a"), (0, "b")], False, id="crlf"
    ),
    pytest.param(
        "import a\\\r\n, b\r\n", [(0, "a"), (0, "b")], False, id="crlf-continued"
    ),
    pytest.param("﻿import a\n", [(0, "a")], False, id="bom"),
    pytest.param("import café\n", [(0, "café")], False, id="non-ascii-name"),
    pytest.param("x = 1; import a\n", [(0, "a")], False, id="after-semicolon"),
    pytest.param("if True: import a\n", [(0, "a")], False, id="after-colon"),
    pytest.param("else: from . import x\n", [(1, "x")], False, id="after-else"),
    # A comment or a string holds no import, and doesn't hide one.
    pytest.param("import a  # import b\n", [(0, "a")], False, id="comment-after"),
    pytest.param("# import a\n", [], False, id="comment-only"),
    pytest.param(
        'x = "#"; import common.util\n',
        [(0, "common.util")],
        False,
        id="hash-in-string",
    ),
    pytest.param(
        "x = '#'; import common.util\n",
        [(0, "common.util")],
        False,
        id="hash-in-single",
    ),
    pytest.param(
        'x = "a;b"; import common.util\n',
        [(0, "common.util")],
        False,
        id="semicolon-in-string",
    ),
    pytest.param('x = ";"\nimport a\n', [(0, "a")], False, id="semicolon-string-alone"),
    pytest.param(
        'x = "\\"#"; import a\n', [(0, "a")], False, id="escaped-quote-then-hash"
    ),
    pytest.param("x = '\\''; import a\n", [(0, "a")], False, id="escaped-single-quote"),
    pytest.param(
        'x = "\\\\"; import a  # "\n',
        [(0, "a")],
        False,
        id="escaped-backslash-ends-string",
    ),
    pytest.param(
        'x = "ab\\\ncd"; import a\n', [(0, "a")], False, id="string-continued"
    ),
    pytest.param(
        "# note \\\nimport a\n", [(0, "a")], False, id="comment-does-not-continue"
    ),
    pytest.param('x = "import b"\n', [], False, id="import-text-in-a-string"),
    pytest.param('"""\nimport b\n"""\nimport a\n', [(0, "a")], False, id="docstring"),
    pytest.param(
        '"""doc"""; import a\n', [(0, "a")], False, id="import-after-docstring-close"
    ),
    pytest.param(
        "'''\nfrom x import (\n'''\nimport a\n",
        [(0, "a")],
        False,
        id="unclosed-paren-in-docstring",
    ),
    pytest.param(
        "x = '''a\\''''\nimport a\n",
        [(0, "a")],
        False,
        id="triple-ending-in-escaped-quote",
    ),
    pytest.param(
        "x = \"'''\"\nimport a\n", [(0, "a")], False, id="triple-quotes-inside-a-string"
    ),
    pytest.param(
        "# '''\nimport a\n", [(0, "a")], False, id="triple-quotes-inside-a-comment"
    ),
    pytest.param(
        "import_module('x')\nimports = 1\nfrom_x = 1\n", [], False, id="not-keywords"
    ),
    pytest.param("raise X from e\n", [], False, id="raise-from"),
    pytest.param("yield from f()\n", [], False, id="yield-from"),
    # Where the search can't be sure, it says so, and still gives what it found.
    pytest.param(
        "from x import (\nimport common.util\n",
        [(0, "x"), (0, "common.util")],
        True,
        id="unclosed-paren-list",
    ),
    pytest.param(
        "from x import (\n    a,\n",
        [(0, "x"), (0, "x.a")],
        True,
        id="paren-list-open-at-the-end",
    ),
    pytest.param(
        "import a\n'''\nimport b\n",
        [(0, "a"), (0, "b")],
        True,
        id="unclosed-triple-quote",
    ),
    pytest.param("x = 'abc\nimport a\n", [(0, "a")], True, id="unclosed-single-quote"),
    pytest.param(
        "if a: " * 9 + "import common.util\n", [], True, id="past-the-colons-tried"
    ),
    pytest.param(
        "if a: " * 9 + "x = 1\nimport os\n",
        [(0, "os")],
        False,
        id="colons-and-no-import",
    ),
    # Colons inside brackets (hints, slices) are no statement's colon: they are not counted.
    pytest.param(
        "if a[1:2:3:4:5:6:7:8:9]: import common.util\n",
        [(0, "common.util")],
        False,
        id="bracketed-colons-are-not-counted",
    ),
    pytest.param(
        "def f(\n"
        + "".join(f"    p{i}: int,\n" for i in range(10))
        + "    x_import: int,\n):\n    return 1\n",
        [],
        False,
        id="typed-multi-line-def-with-an-import-parameter",
    ),
    pytest.param(
        "class K:\n    def __init__(\n        self,\n        a: int = 1,\n"
        "        b: dict[str, int] | None = None,\n        c: str = 'x',\n"
        "        d: list[int] = [],\n        e: float = 0.0,\n        f: bool = False,\n"
        "        g: bytes = b'',\n        h: set[int] = set(),\n"
        "        rabbitmq_import: str | None = None,\n    ) -> None:\n        pass\n",
        [],
        False,
        id="typed-init-with-a-rabbitmq-import-parameter",
    ),
    pytest.param(
        "def f(\n" + "".join(f"    p{i}: int,\n" for i in range(10)) + "): import x\n",
        [(0, "x")],
        False,
        id="typed-multi-line-def-then-an-import",
    ),
    # A header that runs over lines: the closing line starts with no keyword.
    pytest.param(
        "with open(\n    'f') as g: import x\n",
        [(0, "x")],
        False,
        id="header-with-spans-lines",
    ),
    pytest.param(
        "for x in (\n    1, 2): import x\n",
        [(0, "x")],
        False,
        id="header-for-spans-lines",
    ),
    pytest.param(
        "try:\n    pass\nexcept (A,\n        B): import x\n",
        [(0, "x")],
        False,
        id="header-except-spans-lines",
    ),
    pytest.param(
        "def k(\n    a): import x\n", [(0, "x")], False, id="header-def-spans-lines"
    ),
    pytest.param(
        "async def k(\n    a): import x\n",
        [(0, "x")],
        False,
        id="header-async-def-spans-lines",
    ),
    pytest.param(
        "class C(\n    B): import x\n", [(0, "x")], False, id="header-class-spans-lines"
    ),
    pytest.param(
        "if (True and\n        True): import x\n",
        [(0, "x")],
        False,
        id="header-if-spans-lines",
    ),
    pytest.param(
        "if x:\n    pass\nelif (a and\n      b): import x\n",
        [(0, "x")],
        False,
        id="header-elif-spans-lines",
    ),
    pytest.param(
        "while (\n    True): import x\n",
        [(0, "x")],
        False,
        id="header-while-spans-lines",
    ),
    pytest.param(
        "match (x,\n       y):\n    case [a,\n          b]: import x\n",
        [(0, "x")],
        False,
        id="header-match-case-spans-lines",
    ),
    pytest.param(
        "if (x == ')'\n        ): import y\n",
        [(0, "y")],
        False,
        id="bracket-in-a-string-in-a-header",
    ),
    pytest.param(
        "x = f(\n    1,\n)\nwith open(\n    'f') as g: import a\nimport b\n",
        [(0, "a"), (0, "b")],
        False,
        id="a-closed-call-doesnt-join-the-next-lines",
    ),
    pytest.param("x = (\n    1\n", [], True, id="bracket-never-closed"),
    pytest.param(
        "from a import (b); from c import (\n d\n)\n",
        [(0, "a"), (0, "a.b"), (0, "c"), (0, "c.d")],
        False,
        id="a-list-closed-on-its-line-then-one-that-goes-on",
    ),
    # Since Python 3.12 an f-string can hold its own quote, so a bracket inside the inner string
    # is left in the code and joins the lines after it. A statement inside that run is still read.
    pytest.param(
        's = "a"\na = f"{s.split("(")[0]}"\nimport common.util\nb = f"{s.split(")")[0]}"\n',
        [(0, "common.util")],
        False,
        id="f-string-quote-in-quote-joins-a-run",
    ),
    pytest.param(
        's = "a"\na = f"{s.split("(")[0]}"\nimport common.util\n',
        [(0, "common.util")],
        True,
        id="f-string-quote-in-quote-run-never-closes",
    ),
]


@pytest.mark.parametrize("source, names, unsure", _FORMS)
def test_each_form_is_found_or_the_search_says_it_cant_be_sure(source, names, unsure):
    capped = [False]
    assert import_lines(source, capped) == names
    assert capped[0] is unsure


def test_an_unclosed_paren_list_doesnt_swallow_the_lines_after_it():
    source = "from x import (\n" + "".join(f"import m{at}\n" for at in range(50))
    capped = [False]
    found = import_lines(source, capped)
    assert [name for _, name in found if name.startswith("m")] == [
        f"m{at}" for at in range(50)
    ]
    assert capped[0]


def test_the_search_without_capped_still_runs():
    assert import_lines("from x import (\n") == [(0, "x")]


_N = 100_000
# Each is built to 100 KB in a test, as one line. The bound is generous: what it checks is that
# the cost isn't the square or the cube of the line.
_LINES = {
    "spaces": " " * _N,
    "tabs": "\t" * _N,
    "dots": "." * _N,
    "open-parens": "(" * _N,
    "close-parens": ")" * _N,
    "backslashes": "\\" * _N,
    "hashes": "#" * _N,
    "colons": ":" * _N,
    "semicolons": ";" * _N,
    "commas": "," * _N,
    "single-quotes": "'" * _N,
    "double-quotes": '"' * _N,
    "triple-single": "'''" * (_N // 3),
    "triple-double": '"""' * (_N // 3),
    "escaped-quotes": "'\\" * (_N // 2),
    "escaped-double-quotes": '"\\' * (_N // 2),
    "import-words": "import" * (_N // 6),
    "from-words": "from" * (_N // 4),
    "from-spaced": "from " * (_N // 5),
    "import-spaced": "import " * (_N // 7),
    "from-then-spaces": "from" + " " * _N + "x",
    "from-dot-spaces": "from ." + " " * _N + "x",
    "from-dots-then-spaces": "from " + ". " * (_N // 2) + "x",
    "from-name-then-spaces": "from a" + " " * _N + "x",
    "from-import-then-spaces": "from a import" + " " * _N + "(",
    "import-then-spaces": "import a" + " " * _N + "!",
    "import-name-dots": "import " + "a." * (_N // 2),
    "import-commas": "import " + "a, " * (_N // 3),
    "from-import-commas": "from a import (" + "b, " * (_N // 3),
    "from-import-as": "from a import " + "b as " * (_N // 5),
    "if-colons": "if x: " * (_N // 6),
    "if-colons-then-import": "if " + ":" * _N + " import a",
    "semicolon-imports": "import a;" * (_N // 9),
    "near-matches": "from . import (" * (_N // 15),
    "comment-then-import": "# " * (_N // 2) + "import a",
    "string-then-import": '"' + "a" * _N + '"; import a',
}


@pytest.mark.parametrize("name", list(_LINES))
def test_a_100_kb_line_is_searched_at_once(name):
    line = _LINES[name]
    assert len(line) >= _N - 20
    started = time.perf_counter()
    import_lines(line, [False])
    import_lines(line + "\n" + line + "\nimport a\n", [False])
    # Linear search takes well under a second even on a loaded CI runner; the backtracking
    # pattern this replaced took minutes on the same input.
    assert time.perf_counter() - started < 5.0


def test_a_line_of_50000_spaces_after_from_returns_at_once():
    started = time.perf_counter()
    assert import_lines("from" + " " * 50_000 + "x") == []
    assert time.perf_counter() - started < 5.0


def test_many_lines_that_each_open_a_paren_list_cost_in_proportion():
    # Every one of 20,000 lines could swallow the rest of the file looking for its ")".
    source = "from a import (\n" * 20_000
    started = time.perf_counter()
    capped = [False]
    import_lines(source, capped)
    assert time.perf_counter() - started < 10.0
    assert capped[0]


def test_a_megabyte_of_short_strings_is_searched_in_proportion():
    source = "x = ['a', 'b']\n" * 70_000 + "import a\n"
    started = time.perf_counter()
    assert import_lines(source) == [(0, "a")]
    assert time.perf_counter() - started < 10.0


@pytest.mark.parametrize("name", list(_LINES))
def test_the_script_searches_take_a_100_kb_line_at_once(name):
    line = _LINES[name]
    started = time.perf_counter()
    _script_imports(line)
    _script_imports(line + "\n" + line + "\n")
    _without_type_imports(line)
    # The search is for the walk's scripts; a line holding the keywords is its worst text.
    _script_imports("import type " + line)
    _script_imports("from " + line)
    assert time.perf_counter() - started < 10.0


_MIB = 1024 * 1024


def _distinct(block, size=4 * _MIB):
    """``block(start)`` repeated, each time with new names, to ``size`` characters."""
    parts, total, start = [], 0, 0
    while total < size:
        part = block(start)
        parts.append(part)
        total += len(part)
        start += 1_000
    return "".join(parts)


@pytest.mark.parametrize(
    "block",
    [
        pytest.param(
            lambda at: (
                "from x import (\n"
                + "".join(f"    a{at + n},\n" for n in range(50))
                + ")\n"
            ),
            id="closed-lists",
        ),
        pytest.param(
            lambda at: (
                "from x import (\n" + "".join(f"    a{at + n}\n" for n in range(50))
            ),
            id="lists-never-closed",
        ),
        pytest.param(
            lambda at: "import " + "".join(f"a{at + n}, " for n in range(300)) + "b\n",
            id="long-import-lists",
        ),
    ],
)
def test_names_past_the_limit_are_not_collected(block):
    # The caller keeps this many distinct names and cuts on the rest; work and memory end there.
    source = _distinct(block)
    assert len(source) > 3 * _MIB
    limit = 5_000
    capped = [False]
    started = time.perf_counter()
    found = import_lines(source, capped, limit=limit)
    elapsed = time.perf_counter() - started
    assert len(found) == limit + 1
    assert capped[0]
    # The count above is the check: the limit is 5,000 and a 4 MiB file of each shape holds far
    # more, so the search stops at limit + 1 names. The time bound only keeps a loaded runner
    # from failing it.
    assert elapsed < 10.0


def test_a_limit_the_names_stay_under_changes_nothing():
    capped = [False]
    assert import_lines("import a, b\nimport c\n", capped, limit=3) == [
        (0, "a"),
        (0, "b"),
        (0, "c"),
    ]
    assert capped[0] is False
    assert import_lines("import a, b\nimport c\n", capped, limit=2) == [
        (0, "a"),
        (0, "b"),
        (0, "c"),
    ]
    assert capped[0] is True


def test_a_name_repeated_past_the_limit_is_one_name():
    # The walk keeps distinct names, so a file that imports one module many times isn't a cut.
    capped = [False]
    assert import_lines("import os\n" * 20, capped, limit=5) == [(0, "os")]
    assert capped[0] is False


def test_an_unclosed_list_stops_reading_lines_past_the_limit(monkeypatch):
    # Counted by the lines read, not by time: past the limit the list's lines aren't read.
    from fi.alk.harness import import_lines as search

    read = []
    real = search._is_list_of_names

    def counting(text):
        read.append(text)
        return real(text)

    monkeypatch.setattr(search, "_is_list_of_names", counting)
    source = "from x import (\n" + "".join(f"    a{n}\n" for n in range(10_000))
    capped = [False]
    found = search.import_lines(source, capped, limit=5)
    assert len(found) == 6
    assert capped[0]
    assert len(read) <= 10


_UNCLOSED_ON_ONE_LINE = {
    # Many lists on one line that each go on past it, then a long line with no names.
    "lists-on-one-line": lambda k: (
        "x = 1" + ";from m import (" * k + "\n" + "y " * (8 * k) + "\n"
    ),
    # The same lists inside a run that closes, so the joined line and its first line both hold them.
    "lists-in-a-closed-run": lambda k: (
        "x = ("
        + ";from m import (" * k
        + "\n"
        + "a"
        + ")" * (k + 1)
        + "\n"
        + "y " * (8 * k)
        + "\n"
    ),
    "lists-after-colons": lambda k: (
        "x = 1" + ";if 1: from m import (" * k + "\n" + "y " * (8 * k) + "\n"
    ),
}


@pytest.mark.parametrize("lists", [4_000, 8_000, 16_000], ids=["128k", "256k", "512k"])
@pytest.mark.parametrize("name", list(_UNCLOSED_ON_ONE_LINE))
def test_unclosed_lists_on_one_line_cost_in_proportion(name, lists):
    # Only a line's last statement can go on past it, so a second list that does isn't Python:
    # the search says it can't be sure, and doesn't read the next line again for each list.
    source = _UNCLOSED_ON_ONE_LINE[name](lists)
    assert len(source) > 31 * lists
    capped = [False]
    started = time.perf_counter()
    import_lines(source, capped)
    elapsed = time.perf_counter() - started
    assert capped[0]
    # Read once, the 512k file takes a few hundredths of a second here; read again per list it
    # took over ten, and four times as long for each doubling. The bound leaves a slow runner
    # room many times over.
    assert elapsed < 5.0


@pytest.mark.parametrize("name", list(_UNCLOSED_ON_ONE_LINE))
def test_unclosed_lists_on_one_line_read_the_next_line_once(name, monkeypatch):
    # Counted by the lines read, not by time: a second list left open on a line is a cut.
    from fi.alk.harness import import_lines as search

    read, real = [], search._is_list_of_names
    monkeypatch.setattr(
        search, "_is_list_of_names", lambda t: read.append(t) or real(t)
    )
    capped = [False]
    search.import_lines(_UNCLOSED_ON_ONE_LINE[name](4_000), capped)
    assert capped[0] and len(read) <= 4
