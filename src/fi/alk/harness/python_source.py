"""Reading a customer's Python while a conversation waits on the answer.

The parser is one call that holds the interpreter until it returns, so running it in a thread
doesn't free the conversation. A crafted file costs the parser, and the tokenizer on 3.12, the
square of its size: about 15 s for one of 1 MiB. So a file is parsed only up to a size whose worst
case is a fraction of a second, and a scan parses only up to a total.
"""

from __future__ import annotations

import ast
import functools
import io
import re
import tokenize
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
# What one scan's tokenizer may read again, in characters. On 3.12.3, the sandbox's Python, the
# tokenizer decodes a token's whole line again for each token, so one long line of short tokens
# costs the square of its length: about 4 s for 64 KB on one line, though the total holds. Each
# token is charged its line. This much is at most about 0.03 s of that work on 3.12.3. Over 5,000
# real files of 4 to 64 KB, 64 KB of code comes to about 0.5 M, and 1.9 M at the most.
LARGEST_TOKENIZED_WORK = 8 * 1024 * 1024


# The parser folds every non-ASCII name to its NFKC form, and on 3.11 and 3.12.3 that sorts a run
# of combining marks by insertion, in one call holding the interpreter: about 3.8 s for one name
# of marks in a 128 KB file. Honest text never runs this many marks together.
_LONGEST_MARK_RUN = 32
_NON_ASCII_RUN = re.compile(r"[^\x00-\x7f]{2,}")


class ParseBudget:
    """What is left for one scan to parse, in UTF-8 bytes, as the files' sizes are counted."""

    def __init__(self, total: int | None = None) -> None:
        self.left = self._total() if total is None else total

    @staticmethod
    def _total() -> int:
        return LARGEST_PARSED_TOTAL

    def spend(self, text: str) -> bool:
        """Charge ``text``, and say whether the scan may still parse it."""
        self.left -= utf8_size(text)
        return self.left >= 0


class TokenBudget(ParseBudget):
    """What is left for one scan to tokenize: its files in UTF-8 bytes, and the lines its tokens
    are read from in characters."""

    def __init__(self, total: int | None = None, work: int | None = None) -> None:
        super().__init__(total)
        self.work_left = LARGEST_TOKENIZED_WORK if work is None else work

    @staticmethod
    def _total() -> int:
        return LARGEST_TOKENIZED_TOTAL

    def tokens(self, text: str) -> list[tokenize.TokenInfo] | None:
        """The tokens of ``text``, or None once the scan's work runs out. Charge the text with
        ``spend`` first. Raises what ``tokenize`` raises."""
        found: list[tokenize.TokenInfo] = []
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            self.work_left -= len(token.line)
            if self.work_left < 0:
                return None
            found.append(token)
        return found


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
    # A literal match: a name spelled in other characters that Python folds to the same one,
    # such as a fullwidth `ｐlaywright`, isn't found.
    if needles and not any(needle in raw for needle in needles):
        return None
    try:
        # As Python reads it: a byte-order mark is allowed, and isn't part of the code.
        return raw.decode("utf-8-sig")
    except UnicodeError:
        return None


def parsed(text: str) -> ast.Module | None:
    """The syntax tree, or None for text that won't parse. The caller limits its size.

    A crafted file can run the parser out of its own stack (``MemoryError``) or the interpreter's
    (``RecursionError``); either says no more about the file than a syntax error does. Nor does
    text holding a long run of combining marks, which the parser would take seconds to fold.
    """
    if _runs_marks(text):
        return None
    try:
        return ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return None


def utf8_size(text: str) -> int:
    """The size of ``text`` in a file, which is what the limits here count."""
    return len(text) if text.isascii() else len(text.encode("utf-8", "surrogatepass"))


def _runs_marks(text: str) -> bool:
    """Whether ``text``, decomposed, holds a run of ``_LONGEST_MARK_RUN`` combining marks.

    Checked in Python one character at a time, so it lets go of the interpreter as it runs, and
    costs what the text's length does: a mark run needs non-ASCII characters next to each other.
    """
    if text.isascii():
        return False
    for stretch in _NON_ASCII_RUN.finditer(text):
        run = 0
        for char in stretch.group():
            marks, trailing = _marks_in(char)
            run = run + trailing if marks else trailing
            if run >= _LONGEST_MARK_RUN:
                return True
    return False


@functools.lru_cache(maxsize=4096)
def _marks_in(char: str) -> tuple[bool, int]:
    """Whether ``char`` decomposes to marks alone, and how many marks end its decomposition."""
    decomposed = unicodedata.normalize("NFKD", char)
    trailing = 0
    for part in reversed(decomposed):
        if not unicodedata.combining(part):
            break
        trailing += 1
    return trailing == len(decomposed), trailing
