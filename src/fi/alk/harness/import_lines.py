"""The import lines of code the walk didn't parse, found by reading its text.

A parse costs time in proportion to the file and holds the conversation up, so the walk spends
only a total of them. What it can't parse it reads here instead: strings and comments are taken
out, then the lines that are imports are read, with no tree and no backtracking. Every step is
one pass over the text (a regular expression whose pieces can't overlap, or a split), so the cost
follows the size of the file, whatever it holds.

A line search is not a parse. It misses an import built at run time (``importlib.import_module``,
``__import__``, ``exec``) and a name only a decoded codec spells. Where it can't be sure what it
read, it says so (``capped``) and the caller cuts the reading rather than trust the result.
"""

from __future__ import annotations

import re

# The most of one file the walk line-searches, and of all the files it does: a file or a walk
# over these is a cut, not a search.
LARGEST_SEARCHED = 4 * 1024 * 1024
SEARCH_TOTAL = 8 * 1024 * 1024
# How many colons of a compound statement's header the search tries as the start of its body.
MOST_COLONS_TRIED = 8

# A dotted name. The pieces are split by a literal dot, so nothing in it can match two ways.
_NAME = re.compile(r"(?!\d)\w+(?:\.(?!\d)\w+)*")
# A backslash and what it escapes, which no string, comment or quote below then has to read.
_ESCAPE = re.compile(r"\\[^\n]")
# A comment, or a string: a triple-quoted one over any lines, or one that stays on its line
# (a continued line is "\x01\n" by then). A string that is never closed matches nothing, so its
# quote stays in the text, which says the search can't be sure. Each piece is a run of one kind
# of character and then the character that ends it, or a lazy single-character run, so a failed
# match costs the rest of its line (the rest of the file for a triple quote that has no end,
# which no later one can then have either) and none of it is tried again.
_STRING_OR_COMMENT = re.compile(
    r"#[^\n]*"
    r"|'''.*?'''"
    r'|""".*?"""'
    r"|'[^'\n\x01]*(?:\x01\n[^'\n\x01]*)*'"
    r'|"[^"\n\x01]*(?:\x01\n[^"\n\x01]*)*"',
    re.DOTALL,
)
_COMPOUND_START = re.compile(
    r"(?:if|elif|else|for|while|try|except|finally|with|async|def|class|match|case)\b"
)


def _code(content: str, capped: list[bool] | None) -> str:
    """``content`` with its comments and strings taken out and its continued lines joined.

    Given ``capped``, its one item is set when a quote is left over: a string never closed, so
    what follows it was read as code that may be string."""
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = _ESCAPE.sub("\x00\x00", content.removeprefix("\ufeff"))
    # A backslash before a line end: the line goes on. Kept as a marker until the strings and
    # comments are out, since a comment ends at the line, and a string goes on over it.
    content = content.replace("\\\n", "\x01\n")
    code = _STRING_OR_COMMENT.sub(" ", content)
    if capped is not None and ("'" in code or '"' in code):
        capped[0] = True
    return code.replace("\x01\n", " ")


def _statements(code: str, capped: list[bool] | None, most_colons: int) -> list[str]:
    """The pieces of one line of code a statement can start at: each ``;`` segment, and for a
    segment that opens a compound statement (``if True: import x``) the text after each of its
    first ``most_colons`` colons. A piece that is no import is skipped by the caller.
    Given ``capped``, its one item is set when a segment has more colons than are tried and an
    ``import`` after the last one tried: that import isn't searched, so the caller cuts."""
    pieces: list[str] = []
    for segment in code.split(";"):
        segment = segment.strip()
        pieces.append(segment)
        if _COMPOUND_START.match(segment):
            at = -1
            for _ in range(most_colons):
                at = segment.find(":", at + 1)
                if at < 0:
                    break
                pieces.append(segment[at + 1 :].strip())
            else:
                rest = segment[at + 1 :]
                if capped is not None and ":" in rest and "import" in rest:
                    capped[0] = True
    return pieces


def _name(item: str) -> str | None:
    """The dotted name an import item starts with (``a.b`` for ``a . b as c``), or None."""
    *head, tail = item.split(".")
    words = tail.split()
    if not words or (len(words) > 1 and words[1] != "as"):
        return None
    parts = []
    for part in head:
        word = part.split()
        if len(word) != 1:
            return None
        parts.append(word[0])
    name = ".".join([*parts, words[0]])
    return name if _NAME.fullmatch(name) else None


def _is_list_of_names(text: str) -> bool:
    """Whether ``text`` is only what a parenthesised import list holds: names, ``as`` aliases and
    commas. A line of anything else isn't part of one."""
    for item in text.split(","):
        words = item.split()
        if len(words) == 3:
            if words[1] != "as":
                return False
        elif len(words) > 1:
            return False
        if words and not _NAME.fullmatch(words[0]):
            return False
    return True


def _import_keyword(text: str) -> int:
    """Where ``text`` has the word ``import``, or -1: not part of a longer name, so
    ``importlib`` and ``import_module`` aren't it, but ``.import`` is."""
    at = text.find("import")
    while at >= 0:
        end = at + 6
        if (at == 0 or not (text[at - 1].isalnum() or text[at - 1] == "_")) and (
            end == len(text) or not (text[end].isalnum() or text[end] == "_")
        ):
            return at
        at = text.find("import", at + 1)
    return -1


def import_lines(
    content: str,
    capped: list[bool] | None = None,
    most_colons: int = MOST_COLONS_TRIED,
) -> list[tuple[int, str]]:
    """The names a line search finds ``content`` importing, each with its level, in the shape a
    parse gives: ``import a.b, c as d`` and ``from .x import y`` / ``from a.b import c`` at the
    start of a line (indented or not), after a ``;``, or after a compound statement's ``:``,
    however the words are spaced, with a line continued by a backslash joined and a
    parenthesised list read over its lines. Strings and comments hold no import.

    It misses an import built at run time (``importlib.import_module(name)``, ``__import__``)
    and a name only a decoded codec spells. Given ``capped``, its one item is set where the
    search can't be sure what it read, and the caller cuts rather than trust the result: a
    string never closed, a parenthesised list that isn't one or never ends, or an import past the
    colons tried (``_statements``).
    """
    names: list[tuple[int, str]] = []
    lines = _code(content, capped).split("\n")
    at = 0
    while at < len(lines):
        line = lines[at]
        at += 1
        if "import" not in line:
            continue
        for piece in _statements(line, capped, most_colons):
            if piece.startswith("import") and piece[6:7].isspace():
                for item in piece[6:].split(","):
                    if name := _name(item):
                        names.append((0, name))
            elif piece.startswith("from") and (piece[4:5].isspace() or piece[4:5] == "."):
                rest = piece[4:]
                where = _import_keyword(rest)
                head = "".join(rest[:where].split()) if where >= 0 else ""
                level = len(head) - len(head.lstrip("."))
                module = head[level:]
                if not head or (module and not _NAME.fullmatch(module)):
                    continue
                tail = rest[where + 6 :]
                if module:
                    names.append((level, module))
                if "(" in tail:
                    tail = tail.split("(", 1)[1]
                    if ")" in tail:
                        tail = tail.split(")", 1)[0]
                    else:
                        # The list goes on over the next lines, up to the one that closes it. A
                        # line that isn't names isn't part of it: the list was never closed, and
                        # the line is read as the code it is.
                        tail, at = _rest_of_list(tail, lines, at, capped)
                for item in tail.split(","):
                    if name := _name(item):
                        names.append((level, f"{module}.{name}".lstrip(".")))
    return names


def _rest_of_list(
    tail: str, lines: list[str], at: int, capped: list[bool] | None
) -> tuple[str, int]:
    """The text of a parenthesised import list that goes on over ``lines`` from ``at``, and where
    reading resumes. A line that closes it is cut at its ``)``, and what follows is read as a
    line of its own."""
    held = [tail]
    while at < len(lines):
        line = lines[at]
        closes = ")" in line
        inside = line.split(")", 1)[0] if closes else line
        if not _is_list_of_names(inside):
            break
        held.append(inside)
        if closes:
            lines[at] = line.split(")", 1)[1]
            return " , ".join(held), at
        at += 1
    else:
        pass
    if capped is not None:
        capped[0] = True
    return " , ".join(held), at


# The two below belong with the search: the walk reads a script's import lines the same way, from
# its text with the comments taken out, and this module has no import of the scan's.
# Strings are matched first and kept, so "https://..." or "src/**/*.ts" isn't read as a comment.
# An unclosed one ends at its line or the file: a start that could fail makes a customer's odd file
# cost the square of its size, which holds up the build's conversation until it's done.
# A regex literal is kept the same way, so the "/*" in /\/*$/ doesn't hide the rest of the file. A
# slash where a value is expected starts one: after punctuation or at a line's start, never after a
# name or a number, where it divides. Its closing slash must be followed as a regex's is, by flags
# and then no name, quote, "/" or "*": a division that starts a line, or a "/" in JSX text, then
# reads as division, not as a literal that eats a string's opening quote.
# Such a start can fail, so a class holds no "[" and the rest no "]", and a literal that meets one
# is kept to its line's end: a failed literal then can't run over the classes of the slashes after
# it, which would make the line cost its square, and the quotes and backticks it holds can't open a
# string or a template that swaps code and strings for the rest of the file. The "]" also stops a
# literal that runs on through escaped line ends, which would cost the square of their number.
# Kept to its line's end, such a literal can take in a template's opening backtick later on that
# line too; a "/*" inside that template then reads as a comment. That trade is the price of a line
# that can't run away.
SCRIPT_COMMENT_OR_STRING = re.compile(
    r"""("(?:\\.|[^"\\\n])*(?:"|(?=\n)|\\?\Z)|'(?:\\.|[^'\\\n])*(?:'|(?=\n)|\\?\Z)|`(?:\\.|[^`\\])*(?:`|\\?\Z)"""
    r"""|(?<![^\n(,=:\[!&|?{};>])[ \t]*/(?![/*])(?:\\.|\[(?:\\.|[^\]\[\\\n])*(?:\]|(?=\n)|\\?\Z)|[^/\\\n\[\]])*"""
    r"""(?:/(?=[dgimsuvy]*(?![\w$/*"'`]))|(?=\n)|\\?\Z|(?:\[(?=(?:\\.|[^\]\[\\\n])*\[)|\])[^\n]*))"""
    r"""|//[^\n]*|/\*.*?(?:\*/|\Z)""",
    re.DOTALL,
)


def script_kept(hit: re.Match[str]) -> str:
    kept = hit.group(1) or ""
    # A lone apostrophe, as in JSX text, isn't a string, so it can't protect a comment after it.
    if kept.startswith("'") and not kept.endswith("'"):
        return kept.split("//", 1)[0]
    return kept
