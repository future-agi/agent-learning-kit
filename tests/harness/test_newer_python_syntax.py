"""The kit reads customers' Python on its own interpreter, the hosted image's 3.12. A file
written for 3.13's or 3.14's syntax doesn't parse there; the reading says so, naming the file
and the Python it needs, instead of reading the agent in part without a word (TH-8802)."""

from __future__ import annotations

import sys

import pytest

from fi.alk.harness.python_source import newer_syntax, unparsed_python_files

pytestmark = pytest.mark.skipif(
    sys.version_info >= (3, 13), reason="the kit's own interpreter is 3.12; 3.13 parses these"
)


@pytest.mark.parametrize(
    ("source", "python", "construct"),
    [
        ("class Box[T=int]:\n    item: T\n", "3.13", "a type parameter default"),
        ("def first[T=str](items: list[T]) -> T:\n    return items[0]\n", "3.13",
         "a type parameter default"),
        ("type Pair[T=int] = tuple[T, T]\n", "3.13", "a type parameter default"),
        ("def greet(name):\n    return t'hi {name}'\n", "3.14", "a template string"),
        ("x = T'{a}'\ny = tr'raw {b}'\n", "3.14", "a template string"),
        ("try:\n    pass\nexcept ValueError, TypeError:\n    pass\n", "3.14",
         "an except clause without parentheses"),
    ],
)
def test_a_newer_python_construct_is_named(source, python, construct) -> None:
    assert newer_syntax(source) == (python, construct)


@pytest.mark.parametrize(
    "source",
    [
        "def f(:\n    pass\n",  # a plain syntax error
        "x = t + 'a'\n",  # a name t, then a string: valid
        "class Box[T: int]:\n    pass\n",  # 3.12 syntax, parses
        "try:\n    pass\nexcept (ValueError, TypeError):\n    pass\n",
        "d = {'a': 1}\nprint(d['a'])\n",
    ],
)
def test_nothing_else_is_called_newer_syntax(source) -> None:
    assert newer_syntax(source) is None


def test_the_files_the_kits_python_cannot_parse_are_listed(tmp_path) -> None:
    (tmp_path / "agent").mkdir()
    (tmp_path / "agent" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (tmp_path / "agent" / "box.py").write_text("class Box[T=int]:\n    item: T\n", encoding="utf-8")
    (tmp_path / "agent" / "greet.py").write_text("g = t'hi {n}'\n", encoding="utf-8")
    (tmp_path / "agent" / "broken.py").write_text("def f(:\n", encoding="utf-8")
    # Not the agent's code: left out, as the tool evidence leaves them out.
    for skipped in ("tests", "venv", ".git"):
        (tmp_path / skipped).mkdir()
        (tmp_path / skipped / "new.py").write_text("g = t'x'\n", encoding="utf-8")

    found = unparsed_python_files(tmp_path)

    by_path = {one["path"]: one for one in found}
    assert set(by_path) == {"agent/box.py", "agent/greet.py", "agent/broken.py"}
    assert by_path["agent/box.py"]["python"] == "3.13"
    assert by_path["agent/greet.py"]["python"] == "3.14"
    assert by_path["agent/broken.py"]["python"] is None
    assert by_path["agent/box.py"]["reason"] == (
        "agent/box.py uses a type parameter default, which needs Python 3.13; the kit reads "
        "source with Python 3.12, so this file was not read"
    )
    assert by_path["agent/broken.py"]["reason"].startswith(
        "agent/broken.py doesn't parse on Python 3.12 (line 1: "
    )


def test_a_subfolder_limits_the_files_listed(tmp_path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    (tmp_path / "one" / "a.py").write_text("g = t'x'\n", encoding="utf-8")
    (tmp_path / "two" / "b.py").write_text("g = t'x'\n", encoding="utf-8")

    assert [one["path"] for one in unparsed_python_files(tmp_path, "one")] == ["one/a.py"]
