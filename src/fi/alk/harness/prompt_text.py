"""The agent's prompt, read from the lines that hold it as the text it is.

A prompt written in Python is copied from its lines as the code that holds it: its quotes, the
name it is given to, the brackets around it. When the lines hold one string made only of plain
literals, the prompt is that string's text instead. Nothing is run: the text is read from the
parsed file. Anything whose text is only known when the agent runs (a name, a call, a ``{}``
in an f-string) stays as written, and so does a text holding a control character other than a
line break or a tab, or one that shows as nothing: the escape as written is safer to store and
show than the character.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import unicodedata
from typing import NamedTuple

from .python_source import LARGEST_PARSED_MODULE, parsed, utf8_size

# What may stand on the named lines beside the one string, comments taken out: the name or
# argument it is given to (``instructions=``, ``PROMPT: str =``), the brackets around it and a
# comma after it.
_AROUND = re.compile(
    r"\s*(?:[A-Za-z_]\w*\s*(?::\s*[A-Za-z_][\w.\[\], ]*)?=\s*)?(?:\(\s*)*(?:\)\s*)*,?\s*"
)
_COMMENT = re.compile(r"#[^\n]*")
# Characters that reorder how text is shown, or that show as nothing, so what a reader sees
# isn't what the model reads: the bidi controls, the zero-width space, the word joiner and the
# invisible operators, the byte order mark, the line and paragraph separators, the Mongolian vowel
# separator, the deprecated format controls and the interlinear annotation marks. The tag block
# (U+E0000 to U+E007F) is checked by range. The zero-width joiner and non-joiner are kept: emoji
# and many scripts need them.
_UNSEEN = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
    "\u200b\u2060\u2061\u2062\u2063\u2064\ufeff\u2028\u2029\u180e"
    "\u206a\u206b\u206c\u206d\u206e\u206f\ufff9\ufffa\ufffb"
)
_TAGS = range(0xE0000, 0xE0080)


def python_tree(lines: list[str]) -> ast.Module | None:
    """The file's syntax tree, numbered as ``lines`` are, or None: too large to parse here, a
    lone carriage return (which Python counts as a line end and the copy does not), or a file
    this Python can't parse."""
    if any("\r" in line for line in lines):
        return None
    text = "\n".join(lines)
    if utf8_size(text) > LARGEST_PARSED_MODULE:
        return None
    return parsed(text)


class PlainStrings(NamedTuple):
    """A file's strings made only of plain literals, walked once, and the strings each line
    holds part of."""

    found: list[tuple[ast.expr, str]]
    by_line: dict[int, list[int]]


def plain_strings(tree: ast.Module) -> PlainStrings:
    """Each plain string in ``tree`` once, for every part of the prompt the file holds."""
    found = _plain_strings(tree)
    by_line: dict[int, list[int]] = {}
    for index, (node, _) in enumerate(found):
        # The outermost strings don't overlap, so the lines they cover add up to about the
        # file's own.
        for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
            by_line.setdefault(line, []).append(index)
    return PlainStrings(found, by_line)


def prompt_text(lines: list[str], start: int, end: int, strings: PlainStrings) -> str | None:
    """The text on lines ``start``-``end`` (counted from 1) when they hold one string made only
    of plain literals, or lie wholly inside one plain multi-line string; None to keep them as
    written."""
    overlapping: set[int] = set()
    for line in range(start, end + 1):
        overlapping.update(strings.by_line.get(line, ()))
        if len(overlapping) > 1:
            return None
    if len(overlapping) != 1:
        return None
    node, value = strings.found[overlapping.pop()]
    if start <= node.lineno and node.end_lineno <= end:
        text = value if _only_around(lines, start, end, node) else None
    elif node.lineno < start and end < node.end_lineno and _one_triple_quoted(lines, node):
        # Inside the string, with none of its quote marks on the lines: they are text already.
        text = "\n".join(lines[start - 1 : end])
    else:
        return None
    return None if text is None or _holds_a_control(text) else text


def _holds_a_control(text: str) -> bool:
    """Whether ``text`` holds a character a store or a page can't be trusted with: a control
    character other than a line break or a tab (NUL among them), half of a surrogate pair, or one
    that reorders how text is shown or shows as nothing."""
    return any(
        character in _UNSEEN
        or ord(character) in _TAGS
        or (unicodedata.category(character) in ("Cc", "Cs") and character not in "\n\t")
        for character in text
    )


def _plain_strings(tree: ast.Module) -> list[tuple[ast.expr, str]]:
    """Each string expression made only of plain literals, with its value, outermost only.
    Nothing here recurses: a file may hold a chain of ``+`` deeper than Python's call stack."""
    found: list[tuple[ast.expr, str]] = []
    waiting: list[ast.AST] = [tree]
    while waiting:
        node = waiting.pop()
        if not isinstance(node, ast.expr):
            waiting.extend(ast.iter_child_nodes(node))
            continue
        operands = _operands(node)
        values = [_literal_value(operand) for operand in operands]
        plain = [value for value in values if value is not None]
        if len(plain) == len(values):
            found.append((node, "".join(plain)))
            continue
        if len(operands) > 1:
            # Joined with something else: each piece is looked at on its own.
            waiting.extend(operands)
        elif not isinstance(node, ast.JoinedStr):
            # An f-string with a {} in it: its pieces are not strings of their own.
            waiting.extend(ast.iter_child_nodes(node))
    return found


def _operands(node: ast.expr) -> list[ast.expr]:
    """What a chain of ``+`` joins, in order; the node alone when it isn't one."""
    operands: list[ast.expr] = []
    waiting = [node]
    while waiting:
        one = waiting.pop()
        if isinstance(one, ast.BinOp) and isinstance(one.op, ast.Add):
            waiting.extend((one.right, one.left))
        else:
            operands.append(one)
    return operands


def _literal_value(node: ast.expr) -> str | None:
    """The value of one plain literal: a string, literals side by side, an f-string with no
    ``{}``. None for anything else."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.JoinedStr):
        pieces = [
            piece.value
            for piece in node.values
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
        ]
        return "".join(pieces) if len(pieces) == len(node.values) else None
    return None


def _column(line: str, offset: int) -> int:
    """The character a parser's UTF-8 byte ``offset`` on ``line`` falls at."""
    return len(line.encode("utf-8", "surrogatepass")[:offset].decode("utf-8", "ignore"))


def _only_around(lines: list[str], start: int, end: int, node: ast.expr) -> bool:
    """Whether the named lines hold nothing beside the string but what ``_AROUND`` allows."""
    first, last = node.lineno - 1, node.end_lineno - 1
    before = lines[first][: _column(lines[first], node.col_offset)]
    after = lines[last][_column(lines[last], node.end_col_offset) :]
    rest = "\n".join([*lines[start - 1 : first], before, after, *lines[last + 1 : end]])
    if "'" in rest or '"' in rest:
        # Another string, or part of one.
        return False
    return _AROUND.fullmatch(_COMMENT.sub("", rest)) is not None


def _one_triple_quoted(lines: list[str], node: ast.expr) -> bool:
    """Whether ``node`` is written as one triple-quoted literal, neither bytes nor an f-string."""
    if not isinstance(node, ast.Constant):
        return False
    first, last = node.lineno - 1, node.end_lineno - 1
    written = "\n".join(
        [
            lines[first][_column(lines[first], node.col_offset) :],
            *lines[first + 1 : last],
            lines[last][: _column(lines[last], node.end_col_offset)],
        ]
    )
    try:
        tokens = [
            token
            for token in tokenize.generate_tokens(io.StringIO(written).readline)
            if token.type
            not in (tokenize.NL, tokenize.NEWLINE, tokenize.ENDMARKER, tokenize.COMMENT)
        ]
    except (tokenize.TokenError, SyntaxError):
        return False
    if len(tokens) != 1 or tokens[0].type != tokenize.STRING:
        return False
    body = tokens[0].string.lstrip("rRuU")
    return body.startswith(('"""', "'''"))
