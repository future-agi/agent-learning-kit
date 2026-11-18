"""Check the line search against ``ast`` on legal Python files.

``ast.parse`` gives the true set of imports of a file. ``import_lines`` gives what the line search
finds, and whether it said it couldn't be sure (``capped``). A miss is an import ``ast`` sees that
the search neither found nor capped on. The search may find more than ``ast`` does (an import in
text it can't tell from code); it may not find less without saying so.

Run it with another seed or size::

    python -m tests.harness.import_lines_diff --seed 7 --files 5000
    python -m tests.harness.import_lines_diff --seed 7 --files 300 --real ~/some/repo

``--real`` takes folders or files: their ``.py`` files are checked too, those ``ast`` refuses
skipped. Exit status 1 when there is a miss.
"""

from __future__ import annotations

import argparse
import ast
import os
import random
import sys
from pathlib import Path

from fi.alk.harness.import_lines import import_lines

_NAMES = [
    "a",
    "b",
    "os",
    "common",
    "util",
    "x1",
    "mod_y",
    "pkg",
    "sub",
    "playwright",
    "_p",
    "Z",
]
_KEYWORDS_FREE = ["q", "r", "s", "t", "u", "v", "w", "k", "m", "n"]
_STRINGS = [
    "'a # b'",
    '"a ; b"',
    "'a : b'",
    '"it\'s"',
    "'say \"hi\"'",
    "'import os'",
    "'from a import b'",
    r"'esc \' # quote'",
    r'"esc \" ; quote"',
    "''",
    "'\\\\'",
    "'''tri # ; : \" ' import x'''",
    '"""tri\nimport inside\n# hash ; semi : colon\n"""',
    "f'{x} # {y}'",
    'f"{x}: import {y}"',
    "b'by # tes'",
    "r'raw \\ # ;'",
    "'a' 'b'",
    "'''\nfrom a import (\n'''",
    "'(('",
    "'cont \\\nued # import x'",
    '")"',
    "'[{'",
    "'''\n)\n]\n'''",
]


_FOR_ITERS = ["(\n        1, 2)", "x", "[1,\n  2]"]
_PARAMS = ["a", "a: int, b: str = 1", "\n        a", "a,\n    b"]
_BASES = ["", "(B)", "(\n        B)", "(B,\n  metaclass=type)"]
_RETURNS = ["int", "dict[str, int]", "\\\n  int"]


class _Gen:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.tab = rng.random() < 0.15
        self.eol = (
            "\r\n" if rng.random() < 0.15 else ("\r" if rng.random() < 0.03 else "\n")
        )

    def pick(self, items):
        return self.rng.choice(items)

    def chance(self, p: float) -> bool:
        return self.rng.random() < p

    def name(self) -> str:
        return self.pick(_NAMES)

    def dotted(self) -> str:
        parts = ".".join(self.name() for _ in range(self.rng.randint(1, 3)))
        return (
            parts.replace(".", self.pick([".", ".", " . ", ". "]), 1)
            if "." in parts
            else parts
        )

    def alias(self, plain: bool = False) -> str:
        item = self.dotted() if plain else self.name()
        return item + (f" as {self.pick(_KEYWORDS_FREE)}" if self.chance(0.3) else "")

    # An import statement, as lines (a statement may take several).
    def import_stmt(self) -> list[str]:
        kind = self.rng.randint(0, 9)
        if kind <= 2:
            items = [self.alias(True) for _ in range(self.rng.randint(1, 3))]
            if self.chance(0.2) and len(items) > 1:
                return [
                    ", \\".join([items[0], ""]).rstrip(),
                    "    " + ", ".join(items[1:]),
                ]
            return ["import " + ", ".join(items)]
        if kind == 3:
            return ["import" + self.pick(["\t", "  ", " \\\n   "]) + self.alias(True)]
        level = self.pick(["", ".", "..", "...", ". .", " ."])
        module = self.dotted() if self.chance(0.7) or not level.strip() else ""
        from_ = "from" + ("" if level.strip() and self.chance(0.3) else " ")
        head = f"{from_}{level}{self.pick(['', ' ']) if module else ''}{module}"
        gap = "" if (module == "" and level.strip() and self.chance(0.3)) else " "
        if self.chance(0.1) and module:
            return [f"{head} import *"]
        items = [self.alias() for _ in range(self.rng.randint(1, 4))]
        form = self.rng.randint(0, 4)
        if form <= 1:
            return [f"{head}{gap}import {', '.join(items)}"]
        if form == 2:
            return [f"{head} import ({', '.join(items)}{self.pick(['', ','])})"]
        if form == 3:
            lines = [f"{head} import ("]
            for at, item in enumerate(items):
                tail = "," if at < len(items) - 1 or self.chance(0.5) else ""
                note = self.pick(["", "", "  # why", "  # import os; x", "  # )"])
                lines.append(f"    {item}{tail}{note}")
                if self.chance(0.15):
                    lines.append("")
            lines.append(")")
            return lines
        return [
            f"{head} import ({items[0]},",
            "        " + ", ".join(items[1:] or ["z"]) + ")",
        ]

    def expr(self, depth: int = 0) -> str:
        kind = self.rng.randint(0, 11 if depth < 2 else 4)
        if kind == 0:
            return self.pick(["x", "1", "True", "None", "y.z", "-3", "x[0]"])
        if kind == 1:
            return self.pick(_STRINGS[:11])
        if kind == 2:
            return f"{self.pick(_KEYWORDS_FREE)} + 1"
        if kind == 3:
            return "lambda: 0"
        if kind == 4:
            return "lambda q, r=1: q"
        if kind == 5:
            return f"[{self.expr(depth + 1)}, {self.expr(depth + 1)}]"
        if kind == 6:
            return f"{{'k': {self.expr(depth + 1)}, 'j': 2}}"
        if kind == 7:
            return f"f({self.expr(depth + 1)}, key=lambda i: i)"
        if kind == 8:
            return f"x[1:{self.pick(['2', ''])}:{self.pick(['3', ''])}]"
        if kind == 9:
            return f"({self.expr(depth + 1)} if x else {self.expr(depth + 1)})"
        if kind == 10:
            return (
                f"(\n        {self.expr(depth + 1)},\n        {self.expr(depth + 1)})"
            )
        return f"{self.expr(depth + 1)} and {self.expr(depth + 1)}"

    def simple(self) -> list[str]:
        """One simple statement (no ``;``), as lines."""
        kind = self.rng.randint(0, 14)
        if kind <= 3:
            return self.import_stmt()
        if kind == 4:
            return [f"{self.pick(_KEYWORDS_FREE)} = {self.expr()}"]
        if kind == 5:
            s = self.pick(_STRINGS)
            return [f"{self.pick(_KEYWORDS_FREE)} = {s}"]
        if kind == 6:
            return ["pass"]
        if kind == 7:
            return [f"x: int = {self.expr()}"]
        if kind == 8:
            return [f"f(\n    {self.expr()},\n    {self.expr()},\n)"]
        if kind == 9:
            return [
                f"{self.pick(_KEYWORDS_FREE)} = [{self.expr()},\n     {self.expr()}]"
            ]
        if kind == 10:
            return [self.pick(_STRINGS)]
        if kind == 11:
            return ["assert x, " + self.pick(_STRINGS[:10])]
        if kind == 12:
            return [f"x = 1 + \\\n    {self.pick(['2', '3'])}"]
        if kind == 13:
            if self.chance(0.4):
                return ["x = 1; \\", "    " + self.pick(self.import_stmt()[:1])]
            return [f"del {self.pick(_KEYWORDS_FREE)}"]
        return [
            self.pick(["# a comment with import os; from a import b:", "# ((", "# )"])
        ]

    def inline_body(self) -> str | None:
        """A body on the header's line: simple statements joined by ``;`` (single-line ones)."""
        items: list[str] = []
        for _ in range(self.rng.randint(1, 3)):
            lines = self.simple()
            if len(lines) != 1 or "\n" in lines[0] or lines[0].startswith("#"):
                return None
            items.append(lines[0])
        text = "; ".join(items) + self.pick(["", "", ";"])
        return text if "\n" not in text else None

    def cond(self) -> str:
        kind = self.rng.randint(0, 7)
        if kind == 0:
            return "x"
        if kind == 1:
            return f"(x and\n        {self.pick(['y', 'True'])})"
        if kind == 2:
            return f"x == \\\n        {self.pick(['1', '2'])}"
        if kind == 3:
            return "f(\n        1)"
        if kind == 4:
            return self.pick(
                [
                    "x[1:2]",
                    "{'a': 1}",
                    "lambda: 1",
                    "{1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 8: 8, 9: 9}[1]",
                ]
            )
        if kind == 5:
            return "[\n        1, 2][0]"
        if kind == 6:
            return "(\n        True)"
        return f"x in {self.pick(['(1, 2)', '{1: 2}'])}"

    def header_pair(self) -> tuple[str, str]:
        """A compound statement's header (without its colon) and its keyword."""
        kind = self.rng.randint(0, 11)
        if kind <= 1:
            return f"if {self.cond()}", "if"
        if kind == 2:
            return f"while {self.cond()}", "while"
        name = self.pick(_KEYWORDS_FREE)
        if kind == 3:
            return f"for {name} in {self.pick(_FOR_ITERS)}", "for"
        if kind == 4:
            return "with open(\n        'f') as g", "with"
        if kind == 5:
            return "with a as b, \\\n        c as d", "with"
        if kind == 6:
            return f"def {name}({self.pick(_PARAMS)})", "def"
        if kind == 7:
            return f"async def {name}({self.pick(_PARAMS[:1] + _PARAMS[2:])})", "def"
        if kind == 8:
            return f"class {self.pick('CDE')}{self.pick(_BASES)}", "class"
        if kind == 9:
            return f"def {name}(a) -> {self.pick(_RETURNS)}", "def"
        if kind == 10:
            return "try", "try"
        return f"if {self.cond()}", "if"

    def block(self, indent: int, depth: int, count: int) -> list[str]:
        out: list[str] = []
        pad = ("\t" if self.tab else "    ") * indent
        for _ in range(count):
            out.extend(self.statement(indent, depth))
        return out or [pad + "pass"]

    def body(self, indent: int, depth: int, first_line: str) -> list[str]:
        """The header (ending in its colon), then a body inline or indented."""
        pad = ("\t" if self.tab else "    ") * indent
        inline = self.inline_body() if self.chance(0.4) else None
        if inline:
            sep = self.pick([" ", " ", "", "  "])
            return [f"{first_line}:{sep}{inline}"]
        tail = self.pick(["", "", "  # note: import x", "  # ( [ {", "  # ) ]"])
        lines = [f"{first_line}:{tail}"]
        if depth >= 3:
            return lines + [pad + ("\t" if self.tab else "    ") + "pass"]
        return lines + self.block(indent + 1, depth + 1, self.rng.randint(1, 3))

    def statement(self, indent: int, depth: int) -> list[str]:
        pad = ("\t" if self.tab else "    ") * indent
        roll = self.rng.random()
        if roll < 0.5 or depth >= 4:
            lines = self.simple()
            if self.chance(0.12):
                more = self.simple()
                if (
                    len(more) == 1
                    and len(lines) == 1
                    and not more[0].startswith("#")
                    and "\n" not in more[0] + lines[0]
                    and not lines[0].startswith("#")
                ):
                    lines = [lines[0] + self.pick(["; ", " ;", ";"]) + more[0]]
            return [pad + lines[0], *lines[1:]]
        if roll < 0.55:
            return [
                pad + "@dec",
                pad + "@dec2(\n    1)",
                *self.compound(indent, depth, only_def=True),
            ]
        return self.compound(indent, depth)

    def compound(self, indent: int, depth: int, only_def: bool = False) -> list[str]:
        pad = ("\t" if self.tab else "    ") * indent
        header, keyword = self.header_pair()
        while only_def and keyword not in ("def", "class"):
            header, keyword = self.header_pair()
        lines = self.body(indent, depth, pad + header)
        if keyword == "try":
            if self.chance(0.7):
                exc = self.pick(
                    [
                        "",
                        " ImportError",
                        " (A,\n        B)",
                        " (A, B) as e",
                        " Exception as e",
                    ]
                )
                lines += self.body(indent, depth, pad + "except" + exc)
            if self.chance(0.3):
                lines += self.body(indent, depth, pad + "else")
            if (
                self.chance(0.4)
                or lines[-1:]
                and not any(ln.lstrip().startswith("except") for ln in lines)
            ):
                lines += self.body(indent, depth, pad + "finally")
        elif keyword == "if":
            if self.chance(0.3):
                lines += self.body(indent, depth, pad + "elif " + self.cond())
            if self.chance(0.4):
                lines += self.body(indent, depth, pad + "else")
        elif keyword in ("for", "while") and self.chance(0.2):
            lines += self.body(indent, depth, pad + "else")
        if keyword == "if" and self.chance(0.12):
            # A match statement in its place.
            lines = [pad + "match (x,\n          y):"]
            for _ in range(self.rng.randint(1, 2)):
                pat = self.pick(["1", "[a,\n          b]", "{'k': v}", "_", "(1, 2)"])
                head = f"{pad}    case {pat}"
                inline = self.inline_body() if self.chance(0.5) else None
                if inline:
                    lines.append(f"{head}: {inline}")
                else:
                    lines.append(f"{head}:")
                    lines += self.block(indent + 2, depth + 2, 1)
        return lines

    def module(self) -> str:
        lines: list[str] = []
        if self.chance(0.3):
            lines.append(
                self.pick(
                    ['"""doc # ; : import x\nfrom y import (\n"""', "'module doc'"]
                )
            )
        for _ in range(self.rng.randint(1, 8)):
            lines.extend(self.statement(0, 0))
        text = "\n".join(lines) + "\n"
        if self.eol != "\n":
            text = text.replace("\n", self.eol)
        if self.chance(0.05):
            text = "﻿" + text
        return text


def generate(seed: int, count: int) -> list[str]:
    """``count`` legal-looking Python files from ``seed``: all of them parse (those that don't
    are dropped and replaced)."""
    rng = random.Random(seed)
    files: list[str] = []
    attempts = 0
    while len(files) < count and attempts < count * 10:
        attempts += 1
        source = _Gen(rng).module()
        if truth(source) is not None:
            files.append(source)
    return files


def truth(source: str) -> set[tuple[int, str]] | None:
    """The imports ``ast`` sees in ``source``, as targets the way the walk takes them, or None
    when ``ast`` refuses the file."""
    try:
        tree = ast.parse(source.removeprefix("﻿"))
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
    found: set[tuple[int, str]] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.add((node.level, module))
            found.update(
                (node.level, f"{module}.{alias.name}".lstrip("."))
                for alias in node.names
                if alias.name != "*"
            )
    return {(level, name) for level, name in found if name}


def compare(source: str) -> tuple[set[tuple[int, str]], bool] | None:
    """The imports ``ast`` sees that the search neither found nor capped on, and whether it
    capped; None when ``ast`` refuses the file."""
    expected = truth(source)
    if expected is None:
        return None
    capped = [False]
    found = set(import_lines(source, capped))
    return (set() if capped[0] else expected - found), capped[0]


def real_files(paths: list[str]) -> list[Path]:
    skip = {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        "dist",
        "build",
        "site-packages",
    }
    seen: dict[str, Path] = {}
    for given in paths:
        root = Path(given).expanduser()
        if root.is_file():
            seen[str(root.resolve())] = root
            continue
        for folder, dirs, names in os.walk(root):
            dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
            for name in names:
                if name.endswith(".py"):
                    path = Path(folder, name)
                    seen.setdefault(str(path.resolve()), path)
    return sorted(seen.values())


def run(seed: int, files: int, real: list[str], show: int = 5) -> int:
    total = misses = caps = skipped = 0
    shown = 0
    sources = [
        (f"generated #{at}", text) for at, text in enumerate(generate(seed, files))
    ]
    for path in real_files(real):
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                skipped += 1
                continue
            sources.append((str(path), path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            skipped += 1
    generated = sum(1 for label, _ in sources if label.startswith("generated"))
    for label, text in sources:
        outcome = compare(text)
        if outcome is None:
            skipped += 1
            continue
        missed, capped = outcome
        total += 1
        caps += capped
        if missed:
            misses += 1
            if shown < show:
                shown += 1
                print(f"MISS {label}: {sorted(missed)[:5]}")
                print("    " + repr(text[:600]))
    print(
        f"seed={seed} files={total} (generated {generated}, real {total - generated}) "
        f"misses={misses} capped={caps} skipped={skipped}"
    )
    return 1 if misses else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--files", type=int, default=300, help="generated files")
    parser.add_argument(
        "--real", nargs="*", default=[], help="folders or .py files to add"
    )
    parser.add_argument("--show", type=int, default=5, help="misses to print")
    args = parser.parse_args(argv)
    return run(args.seed, args.files, args.real, args.show)


if __name__ == "__main__":
    sys.exit(main())
