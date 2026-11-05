"""Reading a customer's Python while a conversation waits on the answer.

The parser is one call that holds the interpreter until it returns, so running it in a thread
doesn't free the conversation. A crafted file costs the parser, and the tokenizer on 3.12, the
square of its size: about 15 s for one of 1 MiB. So a file is parsed only up to a size whose worst
case is a fraction of a second, and a scan parses only up to a total.
"""

from __future__ import annotations

import ast
import unicodedata
from collections.abc import Iterable
from pathlib import Path

# The worst crafted file this size parses in about 0.3 s on 3.12; an honest one in about 0.03 s.
LARGEST_PARSED_MODULE = 128 * 1024
# What one scan parses in all: two crafted files' worth, about 0.55 s, and far more than an honest
# agent's own files that name what the scan looks for.
LARGEST_PARSED_TOTAL = 256 * 1024
# What one scan tokenizes in all. A crafted file costs the tokenizer the square of its size too,
# about 2.2 s at 128 KB on 3.12.3, so a small total bounds it: about 0.55 s at most.
LARGEST_TOKENIZED_TOTAL = 64 * 1024


class ParseBudget:
    """What is left for one scan to parse, in characters."""

    def __init__(self, total: int | None = None) -> None:
        self.left = self._total() if total is None else total

    @staticmethod
    def _total() -> int:
        return LARGEST_PARSED_TOTAL

    def spend(self, text: str) -> bool:
        """Charge ``text``, and say whether the scan may still parse it."""
        self.left -= len(text)
        return self.left >= 0


class TokenBudget(ParseBudget):
    """What is left for one scan to tokenize, in characters."""

    @staticmethod
    def _total() -> int:
        return LARGEST_TOKENIZED_TOTAL


def read_module(
    path: Path, limit: int = LARGEST_PARSED_MODULE, needles: Iterable[bytes] = ()
) -> str | None:
    """The file as text, or None: not a regular file, larger than ``limit`` bytes, not UTF-8,
    or, given ``needles``, naming none of them."""
    try:
        # A link can name a device or a pipe, which reads without end.
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(limit + 1)
    except (OSError, ValueError):
        return None
    if len(raw) > limit:
        return None
    if needles and not _names_any(raw, needles):
        return None
    try:
        # As Python reads it: a byte-order mark is allowed, and isn't part of the code.
        return raw.decode("utf-8-sig")
    except UnicodeError:
        return None


def _names_any(raw: bytes, needles: Iterable[bytes]) -> bool:
    needles = tuple(needles)
    if any(needle in raw for needle in needles):
        return True
    if raw.isascii():
        return False
    # Python reads a name in its NFKC form, so a fullwidth `ｐlaywright` imports playwright.
    folded = unicodedata.normalize("NFKC", raw.decode("utf-8", "replace")).encode()
    return any(needle in folded for needle in needles)


def parsed(text: str) -> ast.Module | None:
    """The syntax tree, or None for text that won't parse. The caller limits its size.

    A crafted file can run the parser out of its own stack (``MemoryError``) or the interpreter's
    (``RecursionError``); either says no more about the file than a syntax error does.
    """
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None
