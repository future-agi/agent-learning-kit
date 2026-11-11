"""Reading a customer's Python while a conversation waits on the answer.

The parser is one call that holds the interpreter until it returns, so running it in a thread
doesn't free the conversation. A crafted file costs the parser, and the tokenizer on 3.12, the
square of its size: about 15 s for one of 1 MiB. So a file is parsed only up to a size whose worst
case is a fraction of a second, and a scan parses only up to a total.
"""

from __future__ import annotations

import ast
import codecs
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
# What one scan decodes before searching, in bytes. Python runs a file in a codec that can spell a
# name in other bytes (`import \x70laywright` in unicode-escape, `import +AHA-laywright` in
# utf-7), so such a file is decoded as Python does; but unicode-escape's decode looks up each
# \N{...} name, and 3,000 files of 128 KB held the save 6-7 s with no total. This much decodes in
# about 0.05 s; past it, the file is searched as bytes.
LARGEST_ESCAPED_TOTAL = 4 * 1024 * 1024


# The parser folds every non-ASCII name to its NFKC form, and on 3.11 and 3.12.3 that sorts a run
# of combining marks by insertion, in one call holding the interpreter: about 3.8 s for one name
# of marks in a 128 KB file. Honest text never runs this many marks together.
_LONGEST_MARK_RUN = 32
# Text codecs a coding line may name that aren't read: see _coding.
_UNREAD_CODECS = frozenset({"idna", "punycode", "undefined", "unicode-escape"})
# The codecs a coding line may name in which no byte form spells a name character, NUL, CR or LF
# but its own single byte, nor spells nothing: a needle missing from the raw bytes is missing from
# the text Python reads too. The sweep in test_python_source_codecs proves each one, so a
# codec missing from here is decoded before it's searched.
_RAW_SAFE_CODECS = frozenset(
    {
        "ascii",
        "big5",
        "big5hkscs",
        "charmap",
        "cp1006",
        "cp1125",
        "cp1250",
        "cp1251",
        "cp1252",
        "cp1253",
        "cp1254",
        "cp1255",
        "cp1256",
        "cp1257",
        "cp1258",
        "cp437",
        "cp720",
        "cp737",
        "cp775",
        "cp850",
        "cp852",
        "cp855",
        "cp856",
        "cp857",
        "cp858",
        "cp860",
        "cp861",
        "cp862",
        "cp863",
        "cp864",
        "cp865",
        "cp866",
        "cp869",
        "cp874",
        "cp932",
        "cp949",
        "cp950",
        "euc_jis_2004",
        "euc_jisx0213",
        "euc_jp",
        "euc_kr",
        "gb18030",
        "gb2312",
        "gbk",
        "hp-roman8",
        "iso8859-1",
        "iso8859-10",
        "iso8859-11",
        "iso8859-13",
        "iso8859-14",
        "iso8859-15",
        "iso8859-16",
        "iso8859-2",
        "iso8859-3",
        "iso8859-4",
        "iso8859-5",
        "iso8859-6",
        "iso8859-7",
        "iso8859-8",
        "iso8859-9",
        "johab",
        "koi8-r",
        "koi8-t",
        "koi8-u",
        "kz1048",
        "mac-croatian",
        "mac-cyrillic",
        "mac-greek",
        "mac-iceland",
        "mac-latin2",
        "mac-roman",
        "mac-romanian",
        "mac-turkish",
        "palmos",
        "ptcp154",
        "shift_jis",
        "shift_jis_2004",
        "shift_jisx0213",
        "tis-620",
        "utf-8",
        "utf-8-sig",
    }
)
# Refused by name in _coding: never decoded, whatever the total.
_NEVER_DECODED_FIRST = frozenset({"idna", "punycode", "undefined"})
# Python reads a lone CR as a line end before it decodes, so hz's `~` and a line end join.
_CR_TO_LF = bytes.maketrans(b"\r", b"\n")
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


class EscapeBudget:
    """What is left for one scan to decode before searching, in bytes: every file read that way (a
    text codec not proven safe to search as bytes, unicode-escape among them) is charged here."""

    def __init__(self, total: int | None = None) -> None:
        self.left = LARGEST_ESCAPED_TOTAL if total is None else total

    def spend(self, raw: bytes) -> bool:
        """Charge ``raw``, and say whether the scan may still decode it."""
        self.left -= len(raw)
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
        # Python reads a lone CR as a line end, and the tokenizer doesn't: to it a file of only CRs
        # is one line, so a "#" at its start would make all of it a comment.
        lines = text.replace("\r\n", "\n").replace("\r", "\n")
        for token in tokenize.generate_tokens(io.StringIO(lines).readline):
            self.work_left -= len(token.line)
            if self.work_left < 0:
                return None
            found.append(token)
        return found


def read_module(
    path: Path,
    limit: int = LARGEST_PARSED_MODULE,
    needles: Iterable[bytes] = (),
    undecoded: list[bytes] | None = None,
    escapes: EscapeBudget | None = None,
) -> str | None:
    """The file as text, or None: not a regular file, larger than ``limit`` bytes, not readable
    in the encoding its coding line or byte-order mark names (UTF-8 without one), or, given
    ``needles``, naming none of them.

    Given ``escapes``, a file in a codec not proven safe to search as bytes (unicode-escape,
    utf-7, hz and others) is decoded as Python does while the scan's total lasts, and a needle
    counts in what it decodes to, or in its bytes when that isn't ASCII. Given ``undecoded``, a file that names a needle
    but isn't read because its coding line names a codec refused by name (or unicode-escape past
    the total) is put there as bytes: Python still runs it (plain ASCII under idna as it would
    without the line), so a scan looking for names can still find them.
    """
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
    coding = _coding(raw)
    taken, text = _decoded_first(raw, coding, escapes)
    if taken:
        if text is None:
            return None
        # The bytes too, as for any other file: the parser folds a fullwidth name the text spells
        # (`ｐlaywright`) to the needle, and a needle in the bytes is what lets such a file in. On
        # ASCII text nothing folds, so whatever a scan can find is in the text itself.
        if needles and not any(
            needle.decode("ascii") in text or not text.isascii() and needle in raw
            for needle in needles
        ):
            return None
        return text
    # A literal match: a name spelled in other characters that Python folds to the same one,
    # such as a fullwidth `ｐlaywright`, isn't found.
    if needles and not any(needle in raw for needle in needles):
        return None
    if coding is None:
        return None
    encoding, info = coding
    if info.name in _UNREAD_CODECS:
        if undecoded is not None:
            undecoded.append(raw)
        return None
    try:
        # A codec such as rot13 or base64 isn't a text encoding, so decode refuses it.
        return raw.decode(encoding)
    except (UnicodeError, LookupError):
        return None


def source_encoding(raw: bytes) -> str | None:
    """The encoding Python reads ``raw`` in: a coding line or a byte-order mark names it, and
    it's UTF-8 without one. None for a coding line no codec is found for, or one refused by name.
    A codec that turns text into text, such as rot13, is named: bytes can't be decoded in it, so
    the caller reads the file as UTF-8."""
    coding = _coding(raw)
    if coding is None:
        return None
    encoding, info = coding
    if info.name in _UNREAD_CODECS:
        return None
    return encoding


def decoded_first(
    raw: bytes, escapes: EscapeBudget | None
) -> tuple[bool, str | None, str | None]:
    """``raw`` read as ``read_module`` reads a file before searching it, for a reader that
    searches no needles: whether it was decoded first, its text (None when Python refuses it),
    and the encoding ``source_encoding`` gives, to read a file that wasn't. The coding line is
    read once for both."""
    coding = _coding(raw)
    taken, text = _decoded_first(raw, coding, escapes)
    encoding = None if coding is None or coding[1].name in _UNREAD_CODECS else coding[0]
    return taken, text, encoding


def _decoded_first(
    raw: bytes, coding: tuple[str, codecs.CodecInfo] | None, escapes: EscapeBudget | None
) -> tuple[bool, str | None]:
    """Whether ``raw``, in the codec ``coding`` names, is decoded before it's searched, and its
    text as Python reads it: None when Python refuses it.

    A codec not proven safe to search as bytes can spell a name in other bytes, so its file is
    decoded while ``escapes`` lasts. Past it, or with no total, it isn't taken, and the caller
    reads it as any other file.
    """
    if coding is None or escapes is None:
        return False, None
    encoding, info = coding
    if (
        info.name in _RAW_SAFE_CODECS
        or info.name in _NEVER_DECODED_FIRST
        # Not a text encoding, such as rot13 or base64: bytes.decode refuses it before reading.
        or not getattr(info, "_is_text_encoding", True)
    ):
        return False, None
    if not escapes.spend(raw):
        return False, None
    if b"\r" in raw:
        # Python ends lines before it decodes, so a backslash or hz's `~` before a CRLF joins
        # the lines it ends.
        raw = raw.replace(b"\r\n", b"\n").translate(_CR_TO_LF)
    if not raw.endswith(b"\n"):
        # Python ends the last line too: a file ending in an open escape still runs.
        raw += b"\n"
    try:
        text = raw.decode(encoding)
    except (UnicodeError, LookupError):
        # LookupError only from a codec registered at run time: Python's text codecs don't.
        return True, None
    # Python reads nothing past a NUL its decode makes at a line's start, and refuses the file
    # for one elsewhere: so a name after it is never run.
    return True, text.partition("\0")[0]


def _first_lines(raw: bytes) -> bytes:
    """The first two lines of ``raw``, each ended as Python ends a line (LF, CRLF or a lone CR),
    joined by LF: found where they end, so the rest of the file is never rewritten."""
    lines: list[bytes] = []
    start = 0
    for _ in range(2):
        ends = [at for at in (raw.find(b"\n", start), raw.find(b"\r", start)) if at != -1]
        if not ends:
            lines.append(raw[start:])
            break
        end = min(ends)
        lines.append(raw[start:end])
        start = end + (2 if raw[end : end + 2] == b"\r\n" else 1)
    return b"\n".join(lines)


# What tokenize's cookie pattern, ``coding[:=][ \t]*([-\w.]+)``, reads after a line's "#", in two
# steps that never backtrack: a run of spaces before a missing name cost the square of its length.
_COOKIE_SPACE = re.compile(rb"[ \t]*")
_COOKIE_NAME = re.compile(rb"[-\w.]+", re.ASCII)
# The places on a line that look like a cookie's start but aren't one, at most, before the cookie:
# each is tried in turn, and 3,000 files of 16,000 held the save 2.4 s. An
# honest file has none. What follows a cookie isn't counted: Python reads the file in its codec.
_COOKIE_WORDS = 64


def _cookie_part(line: bytes) -> bytes | None:
    """``line`` as short as says the same to detect_encoding: a comment line becomes "#" and its
    cookie, any other line "x", a blank line nothing; None past ``_COOKIE_WORDS`` failed places.
    detect_encoding's own pattern, ``#.*?coding``, would try each place up to the cookie on the
    whole line, 0.75 ms on a 128 KB line, on every file."""
    mark = line.find(b"#")
    if mark == -1:
        return b"x" if line.strip(b" \t\f") else b""
    if line[:mark].strip(b" \t\f"):
        return b"x"
    # Each place the cookie's word is, first to last, found by bytes.find, each kind looked for
    # again only past the one just tried (a kind the line lacks was read to its end each time: 21 s
    # for 3,000 files); the first with a name after it is the cookie.
    colon, equal, failed = line.find(b"coding:", mark), line.find(b"coding=", mark), 0
    while colon != -1 or equal != -1:
        start = colon if equal == -1 or colon != -1 and colon < equal else equal
        name = _COOKIE_NAME.match(line, _COOKIE_SPACE.match(line, start + 7).end())
        if name:
            return b"#" + line[start : start + 7] + name.group()
        failed += 1
        if failed > _COOKIE_WORDS:
            return None
        if start == colon:
            colon = line.find(b"coding:", start + 1)
        else:
            equal = line.find(b"coding=", start + 1)
    return b"#"


def _coding(raw: bytes) -> tuple[str, codecs.CodecInfo] | None:
    """The codec Python reads ``raw`` in, as detect_encoding names it and its codec info.

    Asked about the first two lines as Python reads them: a lone CR ends a line, which
    detect_encoding doesn't know, and after any byte-order mark each is cut to the ASCII that says
    the same to it (``_cookie_part``), since it refuses a line holding a non-ASCII byte while the
    parser still decodes the file in the codec it names (`# coding: latin-1` then a name in it).
    A line it reads with more failed places than an honest one reads as a refused codec's file.
    Four text codecs are refused by name: punycode's decode costs the square of its size, 2.3 s
    for 128 KB; idna's runs in Python for each label, 91 ms for 128 KB; undefined decodes nothing;
    unicode-escape is decoded only under a scan's total (``read_module``).
    """
    head = _first_lines(raw)
    bom = len(codecs.BOM_UTF8) if head.startswith(codecs.BOM_UTF8) else 0
    if b"coding:" not in head and b"coding=" not in head:
        # No cookie: detect_encoding would only run its pattern over the lines to say so.
        encoding = "utf-8-sig" if bom else "utf-8"
        return encoding, codecs.lookup(encoding)
    # Each line cut to what says the same, ASCII alone, so a non-ASCII byte elsewhere on it can't
    # make detect_encoding refuse it; the second only when it reads one, after a blank line or a
    # comment with no cookie in it.
    lines_read = head[bom:].split(b"\n")
    parts = [_cookie_part(lines_read[0])]
    if len(lines_read) > 1 and parts[0] in (b"", b"#"):
        parts.append(_cookie_part(lines_read[1]))
    if None in parts:
        # Read as a file in a refused codec is: searched raw, never decoded.
        return "undefined", codecs.lookup("undefined")
    cut = b"\n".join(parts)
    lines = io.BytesIO(head[:bom] + cut).readline
    try:
        encoding, _ = tokenize.detect_encoding(lines)
        return encoding, codecs.lookup(encoding)
    except (SyntaxError, UnicodeError, LookupError):
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
