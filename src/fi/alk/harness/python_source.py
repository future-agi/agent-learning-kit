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
import logging
import re
import sys
import tokenize
import unicodedata
from collections.abc import Iterable
from pathlib import Path

logger = logging.getLogger(__name__)

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
# The longest first lines, up to the coding line's end, decoded to ask whether they read as
# themselves: an honest coding line is far shorter, and a longer one is read both ways, charged.
_LONGEST_HEAD_CHECKED = 4096
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
    text codec not proven safe to search as bytes, unicode-escape among them) is charged here, for
    each decode. ``reader`` names the scan in the WARNING its total running out gives."""

    def __init__(self, total: int | None = None, reader: str = "scan") -> None:
        self.total = LARGEST_ESCAPED_TOTAL if total is None else total
        self.left = self.total
        self.reader = reader
        self.decoded = 0

    def spend(self, raw: bytes) -> bool:
        """Charge ``raw``, and say whether the scan may still decode it. The first time the total
        runs out, a WARNING says so, in counts alone: a file past it isn't decoded as Python
        does, so a name its codec spells in other bytes can be missed."""
        self.left -= len(raw)
        if self.left >= 0:
            self.decoded += 1
            return True
        if self.left + len(raw) >= 0:
            logger.warning(
                "%s: decoding Python as Python does stopped at its total of %d bytes, after %d "
                "decodes; a name the rest spell in their codec can be missed",
                self.reader,
                self.total,
                self.decoded,
            )
        return False


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
    also: list[str] | None = None,
) -> str | None:
    """The file as text, or None: not a regular file, larger than ``limit`` bytes, not readable
    in the encoding its coding line or byte-order mark names (UTF-8 without one), or, given
    ``needles``, naming none of them.

    Given ``escapes``, a file in a codec not proven safe to search as bytes (unicode-escape,
    utf-7, hz and others) is decoded as Python does while the scan's total lasts, and a needle
    counts in what it decodes to, or in its bytes when that isn't ASCII. Given ``undecoded``, a
    file that names a needle but isn't read because its coding line names a codec refused by name
    (or unicode-escape past the total) is put there as bytes: Python still runs it (plain ASCII
    under idna as it would without the line), so a scan looking for names can still find them.
    Given ``also``, a file Python reads two ways (``_decoded_readings``) has its second text put
    there, for the caller to read on its own: joined to the first, one text's syntax error would
    sink the other.
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
    taken, text, other = _decoded_readings(raw, coding, escapes)
    if taken:
        if text is None:
            return None
        readings = [text] if other is None else [text, other]
        # The bytes too, as for any other file: the parser folds a fullwidth name the text spells
        # (`ｐlaywright`) to the needle, and a needle in the bytes is what lets such a file in. On
        # ASCII text nothing folds, so whatever a scan can find is in the text itself.
        if needles and not any(
            needle.decode("ascii") in reading or not reading.isascii() and needle in raw
            for reading in readings
            for needle in needles
        ):
            return None
        if other is not None and also is not None:
            also.append(other)
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
    raw: bytes, escapes: EscapeBudget | None, also: list[str] | None = None
) -> tuple[bool, str | None, str | None]:
    """``raw`` read as ``read_module`` reads a file before searching it, for a reader that
    searches no needles: whether it was decoded first, its text (None when Python refuses it),
    and the encoding ``source_encoding`` gives, to read a file that wasn't. The coding line is
    read once for both. ``also`` is as ``read_module``'s."""
    coding = _coding(raw)
    taken, text, other = _decoded_readings(raw, coding, escapes)
    if other is not None and also is not None:
        also.append(other)
    encoding = None if coding is None or coding[1].name in _UNREAD_CODECS else coding[0]
    return taken, text, encoding


def _decoded_readings(
    raw: bytes, coding: tuple[str, codecs.CodecInfo] | None, escapes: EscapeBudget | None
) -> tuple[bool, str | None, str | None]:
    """Whether ``raw``, in the codec ``coding`` names, is decoded before it's searched, its text
    as Python reads it (None when Python refuses it), and a second text, or None.

    A codec not proven safe to search as bytes can spell a name in other bytes, so its file is
    decoded while ``escapes`` lasts. Past it, or with no total, it isn't taken, and the caller
    reads it as any other file.

    An import decodes the whole file, and `python file.py` the lines after the coding line, the
    ones up to it read as they are. The two agree when those lines decode to themselves and the
    file holds no CR: an import ends lines before it decodes, a script after. When they don't
    (`# coding: utf-7 +`, hz's `~{`, an iso2022 escape, a backslash at the end, a CR), the
    script's reading is the text when the whole file doesn't decode, and the second text when
    both do and differ. Each decode is charged.
    """
    if coding is None or escapes is None:
        return False, None, None
    encoding, info = coding
    if (
        info.name in _RAW_SAFE_CODECS
        or info.name in _NEVER_DECODED_FIRST
        # Not a text encoding, such as rot13 or base64: bytes.decode refuses it before reading.
        or not getattr(info, "_is_text_encoding", True)
    ):
        return False, None, None
    if not escapes.spend(raw):
        return False, None, None
    if not _reads_its_cookie(encoding):
        return True, _read_as_a_script(raw, encoding), None
    whole = _read_whole(raw, encoding)
    # An import also ends lines before it decodes, and a script after, so any CR can split the
    # two (a backslash or iso2022_jp_2's single shift before one joins the next line in one only).
    if b"\r" not in raw and _head_reads_as_itself(raw, encoding):
        return True, whole, None
    if not escapes.spend(raw):
        # Past the total, a file the whole decode refuses is read as any file past it is.
        return (True, whole, None) if whole is not None else (False, None, None)
    script = _read_as_a_script(raw, encoding)
    if whole is None:
        return True, script, None
    return True, whole, None if script == whole else script


def _head_reads_as_itself(raw: bytes, encoding: str) -> bool:
    """Whether the lines of ``raw`` up to and including its coding line decode to themselves in
    ``encoding``, leaving its decoder as a fresh one would be: then, in a file with no CR, an
    import and a script read it alike."""
    head = raw[: _cookie_line_end(raw)]
    if len(head) > _LONGEST_HEAD_CHECKED:
        # Not decoded uncharged: read both ways, as a crafted line is, and charged for it.
        return False
    try:
        return head.decode(encoding) == head.decode("latin-1")
    except (UnicodeError, LookupError):
        return False


def _read_whole(raw: bytes, encoding: str) -> str | None:
    """``raw`` as an import reads it, decoded whole, or None when Python refuses it."""
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
        return None
    # Python reads nothing past a NUL its decode makes at a line's start, and refuses the file
    # for one elsewhere: so a name after it is never run.
    return text.partition("\0")[0]


@functools.lru_cache(maxsize=64)
def _reads_its_cookie(encoding: str) -> bool:
    """Whether ``encoding`` decodes an ASCII coding line to that line. Every codec compile() and
    import accept does. cp037 and the other EBCDIC codecs don't, nor UTF-16 and UTF-32, whose
    characters are wider than a byte, and only `python file.py` runs a file in one of those
    (``_read_as_a_script``)."""
    cookie = f"# -*- coding: {encoding} -*-\n"
    try:
        return cookie.encode("ascii").decode(encoding) == cookie
    except (UnicodeError, LookupError):
        return False


def _read_as_a_script(raw: bytes, encoding: str) -> str | None:
    """``raw`` as `python file.py` reads it, or None when Python refuses it: what an import reads
    too unless the coding line, or the codec, doesn't decode the lines up to it to themselves.

    Python reads the lines up to and including the cookie's as they are. It then opens the file
    again in the codec one byte before that line's end, and throws away the first line it reads
    there. In an EBCDIC codec that byte is no line end unless it's a CR, so after an LF the next
    line goes with it; in UTF-16 or UTF-32 it starts a character with the bytes after it. The
    rest is decoded, and its CRs end lines. A blank line stands for a line of the file thrown
    away, so each line keeps its number in the file.
    """
    end = _cookie_line_end(raw)
    head = raw[:end].replace(b"\r\n", b"\n").translate(_CR_TO_LF)
    try:
        rest = raw[end - 1 :].decode(encoding)
    except (UnicodeError, LookupError):
        return None
    ends = [at for at in (rest.find("\n"), rest.find("\r")) if at != -1]
    thrown = len(rest)
    if ends:
        thrown = min(ends) + (2 if rest[min(ends) : min(ends) + 2] == "\r\n" else 1)
    body = rest[thrown:].replace("\r\n", "\n").replace("\r", "\n")
    text = head.decode("utf-8", "replace") + ("\n" if thrown > 1 else "") + body
    if not text.endswith("\n"):
        text += "\n"
    # As for any decoded file: nothing past a NUL is read.
    return text.partition("\0")[0]


def _cookie_line_end(raw: bytes) -> int:
    """Where the line holding the coding line's cookie ends in ``raw``, past its line end: the
    first line's, or the second's when the first names no cookie."""
    end = _line_end(raw, 0)
    part = _cookie_part(raw[:end].rstrip(b"\r\n"))
    if part is not None and part.startswith(b"#coding"):
        return end
    return _line_end(raw, end)


def _line_end(raw: bytes, start: int) -> int:
    """Where the line at ``start`` ends, past its LF, CRLF or lone CR, as Python ends a line."""
    ends = [at for at in (raw.find(b"\n", start), raw.find(b"\r", start)) if at != -1]
    if not ends:
        return len(raw)
    end = min(ends)
    return end + (2 if raw[end : end + 2] == b"\r\n" else 1)


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
    # From 3.14 detect_encoding decodes the lines it's handed in their codec, as Python decodes
    # the real ones, line ends included: each cut line keeps its real length mod 4, so a UTF-16
    # or UTF-32 line decodes in the cut as it does whole.
    # A blank line padded with spaces is still blank.
    parts = [part + b" " * ((len(line) - len(part)) % 4) for part, line in zip(parts, lines_read)]
    cut = b"\n".join(parts) + b"\n"
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
    tree = parsed_or_syntax_error(text)
    return tree if isinstance(tree, ast.Module) else None


def parsed_or_syntax_error(text: str) -> ast.Module | SyntaxError | None:
    """As ``parsed``, but a file that is only not this Python's syntax gives its ``SyntaxError``.

    That file may be another Python's, so a caller can still search its lines. A file
    that runs the parser out of stack or holds a long run of combining marks gives None: such a
    file is crafted, and counts for nothing.
    """
    if _runs_marks(text):
        return None
    try:
        return ast.parse(text)
    except SyntaxError as exc:
        return exc
    except (ValueError, RecursionError, MemoryError):
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


# --- Source written for a newer Python than the one reading it --------------------------------
#
# The kit reads a customer's Python with the Python it runs on: the hosted image's 3.12, or the
# user's own for a local reading. Customers' code may be written for a newer one, and three
# constructs of 3.13 and 3.14 don't parse on older ones. A file is named with the version it needs
# only when those constructs are the whole reason it doesn't parse; any other syntax error is
# named as one.
KIT_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"
_KIT_VERSION = sys.version_info[:2]
_TEMPLATE_PREFIXES = frozenset({"t", "tr", "rt"})
_NOT_AGENT_CODE = frozenset({"tests", "test", "node_modules", "site-packages", "venv", "__pycache__"})
_UNPARSED_LISTED = 50
# The walk reads the agent's whole code, so it has a total of its own: the scans' 256 KB would
# stop it inside an ordinary agent of 80 modules. Each file is still at most 128 KB.
_UNPARSED_WALK_TOTAL = 4 * 1024 * 1024
# And a count, as framework detection has: past it the rest is not checked, and the flag says so.
_UNPARSED_WALK_FILES = 5000

# An edit to a line: (row, start column, end column, replacement), columns in characters.
_Edit = tuple[int, int, int, str]


def newer_syntax(
    text: str, *, budget: TokenBudget | None = None, line: int | None = None
) -> tuple[str, str] | None:
    """``(python, construct)`` for a construct of a Python newer than this one that is the whole
    reason ``text`` doesn't parse here, or None.

    Read from the tokens, which an older Python still makes for these files: a type parameter
    default (3.13, ``class Box[T=int]``), a template string (3.14, ``t'...'``) and an except
    clause naming several exceptions without parentheses (3.14). Each one found is rewritten to
    the form this Python reads (the default dropped, the ``t`` dropped, the list put in
    parentheses) and the file parsed again: only when that parses is the newest construct named.
    Otherwise the file stops for something else too (``print "hi"`` from Python 2), and no newer
    Python reads it either.

    Given ``line``, where this Python's parser stopped, a construct must sit on it before the
    file is parsed again. Given ``budget``, the scan's tokenizing is charged to it, and None is
    the answer once it runs out: a crafted line costs the tokenizer the square of its length.
    """
    try:
        if budget is None:
            raw = list(tokenize.generate_tokens(io.StringIO(text).readline))
        elif not budget.spend(text) or (raw := budget.tokens(text)) is None:
            return None
    except (tokenize.TokenError, SyntaxError, ValueError):
        return None
    tokens = [token for token in raw if token.type not in (tokenize.NL, tokenize.COMMENT)]
    found: list[tuple[str, str]] = []
    edits: list[_Edit] = []
    lines: set[int] = set()
    for index, token in enumerate(tokens):
        after = tokens[index + 1] if index + 1 < len(tokens) else None
        if (
            token.type == tokenize.NAME
            and token.string.lower() in _TEMPLATE_PREFIXES
            and after is not None
            and after.type == tokenize.STRING
            and after.start == token.end
        ):
            made = ("3.14", "a template string")
            # A raw template keeps its r.
            kept = "r" if "r" in token.string.lower() else ""
            change = [(token.start[0], token.start[1], token.end[1], kept)]
        elif token.type == tokenize.NAME and token.string == "except" and (
            listed := _bare_except_list(tokens, index + 1)
        ):
            made = ("3.14", "an except clause without parentheses")
            first, colon = listed
            change = [
                (colon.start[0], colon.start[1], colon.start[1], ")"),
                (first.start[0], first.start[1], first.start[1], "("),
            ]
        elif token.type == tokenize.NAME and token.string in ("class", "def", "type") and (
            defaults := _type_parameter_defaults(tokens, index + 1)
        ):
            made = ("3.13", "a type parameter default")
            change = defaults
        else:
            continue
        if tuple(int(part) for part in made[0].split(".")) <= _KIT_VERSION:
            # This Python reads it.
            continue
        found.append(made)
        edits.extend(change)
        lines.add(token.start[0])
    if not found or (line is not None and line not in lines):
        return None
    rewritten = parsed_or_syntax_error(_apply(text, edits))
    if rewritten is None or isinstance(rewritten, SyntaxError):
        return None
    return max(found)


def _apply(text: str, edits: list[_Edit]) -> str:
    """``text`` with ``edits`` made, the last first so earlier columns stay where they were."""
    rows = text.splitlines(keepends=True)
    for row, start, end, replacement in sorted(edits, reverse=True):
        if 0 < row <= len(rows):
            one = rows[row - 1]
            rows[row - 1] = one[:start] + replacement + one[end:]
    return "".join(rows)


def _bare_except_list(
    tokens: list[tokenize.TokenInfo], start: int
) -> tuple[tokenize.TokenInfo, tokenize.TokenInfo] | None:
    """An except clause's exceptions listed with a comma and no parentheses around the list
    (``except (KeyError), ValueError:`` is one): its first token and its colon, or None. 3.14
    reads one only without ``as``."""
    depth = 0
    listed = False
    for token in tokens[start:]:
        if token.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
            return None
        if token.string in "([{" and token.type == tokenize.OP:
            depth += 1
        elif token.string in ")]}" and token.type == tokenize.OP:
            depth -= 1
        elif depth == 0 and token.string == ":":
            return (tokens[start], token) if listed else None
        elif depth == 0 and token.string == "," and token.type == tokenize.OP:
            listed = True
        elif depth == 0 and token.string == "as":
            return None
        elif token.type == tokenize.NAME and token.string == "except":
            # The next clause: scanning on past it would make a file of excepts quadratic.
            return None
    return None


def _type_parameter_defaults(tokens: list[tokenize.TokenInfo], start: int) -> list[_Edit]:
    """``NAME [ ... = ... ]`` after class, def or type: an edit dropping each default inside its
    type parameters, from its ``=`` to the next ``,`` or the closing ``]``. Empty when there is
    none."""
    if start + 1 >= len(tokens) or tokens[start].type != tokenize.NAME:
        return []
    if tokens[start + 1].string != "[":
        return []
    depth = 0
    edits: list[_Edit] = []
    default: tokenize.TokenInfo | None = None
    for token in tokens[start + 1 :]:
        if token.type in (tokenize.NEWLINE, tokenize.ENDMARKER):
            return []
        closes = depth == 1 and token.type == tokenize.OP and token.string in ",]"
        if closes and default is not None:
            if default.start[0] != token.start[0]:
                # A default over several lines: a single-line edit can't drop it.
                return []
            edits.append((default.start[0], default.start[1], token.start[1], ""))
            default = None
        if token.type == tokenize.OP and token.string in "([{":
            depth += 1
        elif token.type == tokenize.OP and token.string in ")]}":
            depth -= 1
            if depth == 0:
                return edits
        elif depth == 1 and token.type == tokenize.OP and token.string == "=":
            default = token
    return []


def unparsed_note(
    source: Path, subfolder: str | None = None, tool_modules: Iterable[str] = ()
) -> dict[str, object]:
    """What the agent document says about the agent's Python files the kit's own Python can't
    parse: ``{}`` when every one parses, so the document is then the one it always was.

    The agent's files are the ones its readers read: its folder's ``.py`` files, past tests,
    virtual environments and hidden folders; with a folder named, also the code it reaches
    outside it (which the tracing and credential checks read) and the modules ``tool_modules``
    name, found as the tool check finds them (the folder, its ``src``, the root, the root's
    ``src``).

    ``unparsed`` lists each as ``{"path", "python", "reason"}``: ``python`` is the version a
    newer construct needs, or ``""`` for a plain syntax error. Nothing in it is the customer's
    own text but a path the platform's path rule admits, so the parser's message, which can
    repeat the file's code, is left out. ``unparsed_truncated`` is set when the list
    may not be every such file: past ``_UNPARSED_LISTED`` of them, a path outside the rule, a
    file too large or too deep to parse, or files past the walk's parse budget.
    """
    from .scan_walk import fits_path_rule
    from .source_folder import SubfolderNotFound, agent_folder

    root = source.resolve()
    try:
        base = agent_folder(root, subfolder) if subfolder else root
        paths = _agent_files(root, base, subfolder, tool_modules)
    except SubfolderNotFound:
        return {}
    budget = ParseBudget(_UNPARSED_WALK_TOTAL)
    naming = TokenBudget()
    listed: list[dict[str, str]] = []
    cut = len(paths) > _UNPARSED_WALK_FILES
    for path in paths[:_UNPARSED_WALK_FILES]:
        text = read_module(path)
        if text is None:
            # Too large, or not text this walk can decode: not checked. A link or a pipe is never
            # read by any reader, so it says nothing.
            cut = cut or _refused_file(path)
            continue
        if not budget.spend(text):
            cut = True
            break
        tree = parsed_or_syntax_error(text)
        if tree is None:
            # Too deep for the parser, or crafted: not checked.
            cut = True
            continue
        if not isinstance(tree, SyntaxError):
            continue
        name = path.relative_to(root).as_posix()
        if len(listed) >= _UNPARSED_LISTED or not fits_path_rule(name):
            cut = True
            continue
        newer = newer_syntax(text, budget=naming, line=tree.lineno)
        if newer is not None:
            python, construct = newer
            reason = (
                f"{name} uses {construct}, which needs Python {python}; the kit reads "
                f"source with Python {KIT_PYTHON}, so this file was not read"
            )
        else:
            python = ""
            reason = (
                f"{name} doesn't parse on Python {KIT_PYTHON} (line {tree.lineno}), "
                "so this file was not read"
            )
        listed.append({"path": name, "python": python, "reason": reason})
    # A key only when it has something to say: an agent whose files all parse but one went
    # unchecked gets the flag alone, and one with nothing to say gets the document it always got.
    return {
        **({"unparsed": listed} if listed else {}),
        **({"unparsed_truncated": True} if cut else {}),
    }


def _is_file(path: Path) -> bool:
    """A file, or False when the path can't be checked: a name past the system's limit, or a
    folder this process may not read. Such a module is not one the tool check reads either."""
    try:
        return path.is_file()
    except OSError:
        return False


def _agent_files(
    root: Path, base: Path, subfolder: str | None, tool_modules: Iterable[str]
) -> list[Path]:
    """The ``.py`` files the agent's readers read, each once: its folder's first, in sorted
    order, since where the budget runs out decides what is listed. A link that leads out of the
    source is not the agent's file, and isn't read."""
    found = [
        path
        for path in sorted(base.rglob("*.py"))
        if not any(
            part.startswith(".") or part in _NOT_AGENT_CODE
            for part in path.relative_to(root).parts[:-1]
        )
        and _inside(path, root)
    ]
    extra: set[Path] = set()
    # The tool check reads the module each entry names, wherever it is, tests/ included.
    bases = [base, base / "src", root, root / "src"] if subfolder else [root, root / "src"]
    for module in tool_modules:
        if not module or not all(part.isidentifier() for part in module.split(".")):
            continue
        relative = Path(*module.split("."))
        for one in bases:
            for candidate in (relative.with_suffix(".py"), relative / "__init__.py"):
                if _is_file(one / candidate):
                    extra.add(one / candidate)
    if subfolder:
        from .agent_type_check import code_the_agent_reaches
        from .source_folder import SubfolderNotFound

        try:
            extra.update(code_the_agent_reaches(root, subfolder))
        except SubfolderNotFound:
            raise
        except Exception:
            # The note never costs the reading: the folder's own files are still walked.
            logger.warning("unparsed note: code the agent reaches not found", exc_info=True)
    seen = {path.resolve() for path in found}
    for path in sorted(extra):
        resolved = path.resolve()
        if resolved.suffix == ".py" and resolved not in seen and resolved.is_relative_to(root):
            seen.add(resolved)
            found.append(resolved)
    return found


def _inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except (OSError, RuntimeError):
        return False


def _refused_file(path: Path) -> bool:
    """A regular file ``read_module`` refused: too large, or not decodable."""
    try:
        return path.is_file() and not path.is_symlink()
    except OSError:
        return False
