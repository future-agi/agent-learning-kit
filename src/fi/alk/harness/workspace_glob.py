"""Workspace member globs as npm reads them: minimatch's braces, extglobs, classes and ``**``.

npm maps a workspace with ``@npmcli/map-workspaces``, which globs the member patterns and hands
the negated ones to the glob as ignores; both match with ``minimatch``. This is the subset of
minimatch a ``workspaces`` field uses, matched against one folder path instead of a walk. A
segment is matched by a memoised walk over its parsed pattern, not a backtracking regular
expression, and every match spends from one ``Budget``, so a manifest cannot make the
question cost more than ``_STATES`` steps. POSIX classes (``[[:alpha:]]``) are read as plain
characters.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from typing import Optional, Union

# A pattern expands to at most this many alternatives.
_EXPANSIONS = 256
# The steps one membership question may take, over every match it makes: a step is a walk state
# or an expanded pattern's character. A real workspace field takes a few hundred.
_STATES = 100_000
_SEQUENCE = re.compile(
    r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?|([A-Za-z])\.\.([A-Za-z])(?:\.\.(-?\d+))?"
)


@dataclass(frozen=True)
class _One:
    """One character: this one, any one in a ``[...]`` class, or any (``?``) when neither."""

    char: str = ""
    within: Optional[re.Pattern[str]] = None

    def accepts(self, char: str) -> bool:
        if self.within is not None:
            return self.within.fullmatch(char) is not None
        return not self.char or self.char == char


@dataclass(frozen=True)
class _Star:
    """``*``: any run of characters."""


@dataclass(frozen=True)
class _Extglob:
    """``@( )``, ``*( )``, ``+( )``, ``?( )`` or ``!( )``, its alternatives as sequence indexes."""

    operator: str
    alternatives: tuple[int, ...]


_Node = Union[_One, _Star, _Extglob]
# A parsed segment: its sequences of nodes, the first the segment's own.
_Sequences = tuple[tuple[_Node, ...], ...]
# None stands for a `**` segment.
_Segments = tuple[Optional[_Sequences], ...]
# Where a walk goes when a sequence ends: (sequence, node, repeating) frames, innermost first.
_Frame = tuple[int, int, bool]
_Frames = tuple[_Frame, ...]


class TooCostly(Exception):
    """The question went past its ``Budget``."""


class Budget:
    """The steps left to one membership question, shared by every match it makes."""

    def __init__(self, steps: int = _STATES) -> None:
        self.left = steps

    def spend(self, steps: int = 1) -> None:
        self.left -= steps
        if self.left < 0:
            raise TooCostly


def matches(
    pattern: str, path: str, budget: Budget, *, dot: bool = False, folder: bool = False
) -> bool:
    """Whether minimatch matches ``path`` (``/``-separated, relative) to ``pattern``. Raises
    ``TooCostly`` when the budget runs out first.

    Without ``dot`` a wildcard does not match a name that starts with ``.``; glob reads its
    ignores with ``dot``. A trailing ``**`` needs one more name, as in minimatch, unless
    ``folder``: glob also tries the folder with a trailing ``/``, which a trailing ``**`` spans.
    """
    parts = tuple(part for part in path.split("/") if part)
    for expanded in islice(_braces(pattern, budget), _EXPANSIONS):
        budget.spend(len(expanded) + 1)
        if _walk(_segments(expanded), parts, dot, folder, budget):
            return True
    return False


def _walk(
    segments: _Segments, parts: tuple[str, ...], dot: bool, folder: bool, budget: Budget
) -> bool:
    def spanned(part: str) -> bool:
        # `**` never spans `.` or `..`, nor a dot folder without `dot`.
        return part not in {".", ".."} and (dot or not part.startswith("."))

    @functools.cache
    def at(p: int, q: int) -> bool:
        budget.spend()
        if p == len(segments):
            return q == len(parts)
        segment = segments[p]
        if segment is None:
            if p == len(segments) - 1:
                return (folder or q < len(parts)) and all(map(spanned, parts[q:]))
            return at(p + 1, q) or (
                q < len(parts) and spanned(parts[q]) and at(p, q + 1)
            )
        return (
            q < len(parts)
            and _segment_matches(segment, parts[q], dot, budget)
            and at(p + 1, q + 1)
        )

    return at(0, 0)


@functools.lru_cache(maxsize=1024)
def _segments(expanded: str) -> _Segments:
    """One brace-free pattern, parsed segment by segment."""
    parts = [part for part in expanded.split("/") if part]
    # glob drops a leading `./`.
    while parts and parts[0] == ".":
        parts.pop(0)
    return tuple(None if part == "**" else _parsed(part) for part in parts)


def _parsed(text: str) -> _Sequences:
    sequences: list[tuple[_Node, ...]] = []

    def parse(text: str) -> int:
        index = len(sequences)
        sequences.append(())
        nodes: list[_Node] = []
        i = 0
        while i < len(text):
            char = text[i]
            if char == "\\" and i + 1 < len(text):
                nodes.append(_One(char=text[i + 1]))
                i += 2
                continue
            if char in "@*+?!" and text[i + 1 : i + 2] == "(":
                close, alternatives = _split(text, i + 1, ")", "|")
                if close >= 0:
                    nodes.append(
                        _Extglob(char, tuple(parse(one) for one in alternatives))
                    )
                    i = close + 1
                    continue
            if char == "*":
                while i < len(text) and text[i] == "*":
                    i += 1
                nodes.append(_Star())
                continue
            if char == "?":
                nodes.append(_One())
            elif char == "[" and (bracket := _class(text, i)) is not None:
                nodes.append(_One(within=bracket[0]))
                i = bracket[1]
                continue
            else:
                nodes.append(_One(char=char))
            i += 1
        sequences[index] = tuple(nodes)
        return index

    parse(text)
    return tuple(sequences)


def _segment_matches(
    sequences: _Sequences, name: str, dot: bool, budget: Budget
) -> bool:
    """Whether one parsed segment matches one name, by a walk memoised on where it stands."""
    if sequences[0][:1] != (_One(char="."),) and (
        name in {".", ".."} or (not dot and name.startswith("."))
    ):
        # A wildcard at the start of a name never matches `.` or `..`, and without `dot` no
        # name that starts with `.`.
        return False
    size = len(name)
    seen: dict[tuple[int, int, bool, _Frames, int], bool] = {}

    def walk(s: int, i: int, repeating: bool, frames: _Frames, pos: int) -> bool:
        key = (s, i, repeating, frames, pos)
        if key in seen:
            return seen[key]
        budget.spend()
        # A repetition that consumes nothing comes back to this key, and fails there.
        seen[key] = False
        nodes = sequences[s]
        node = nodes[i] if i < len(nodes) else None
        if node is None:
            if frames:
                (sequence, index, repeat), outer = frames[0], frames[1:]
                found = walk(sequence, index, repeat, outer, pos)
            else:
                found = pos == size
        elif isinstance(node, _Star):
            found = walk(s, i + 1, False, frames, pos) or (
                pos < size and walk(s, i, False, frames, pos + 1)
            )
        elif isinstance(node, _One):
            found = (
                pos < size
                and node.accepts(name[pos])
                and walk(s, i + 1, False, frames, pos + 1)
            )
        else:
            operator, alternatives = node.operator, node.alternatives
            after: _Frames = ((s, i + 1, False), *frames)
            again: _Frames = ((s, i, True), *frames)
            if operator == "!":
                # Anything but an alternative followed by the rest of the name, as minimatch's
                # lookahead reads it.
                found = not any(
                    walk(one, 0, False, after, pos) for one in alternatives
                ) and any(
                    walk(s, i + 1, False, frames, end) for end in range(pos, size + 1)
                )
            elif repeating or operator == "*":
                found = walk(s, i + 1, False, frames, pos) or any(
                    walk(one, 0, False, again, pos) for one in alternatives
                )
            elif operator == "+":
                found = any(walk(one, 0, False, again, pos) for one in alternatives)
            else:
                found = any(
                    walk(one, 0, False, after, pos) for one in alternatives
                ) or (operator == "?" and walk(s, i + 1, False, frames, pos))
        seen[key] = found
        return found

    try:
        return walk(0, 0, False, (), 0)
    except RecursionError as error:
        # Deeper than the stack: a name and pattern no real workspace has.
        raise TooCostly from error


def _class(text: str, start: int) -> tuple[re.Pattern[str], int] | None:
    """A ``[...]`` class at ``start``, as a one-character expression, and the index after it;
    None when it never closes or does not compile, and the ``[`` is then a plain character."""
    i = start + 1
    negated = text[i : i + 1] in {"!", "^"}
    if negated:
        i += 1
    first = i
    members: list[str] = []
    while i < len(text):
        char = text[i]
        if char == "]" and i > first:
            try:
                return re.compile(
                    f"[{'^' if negated else ''}{''.join(members)}]"
                ), i + 1
            except re.error:
                return None
        if char == "\\" and i + 1 < len(text):
            members.append(re.escape(text[i + 1]))
            i += 2
            continue
        between = char == "-" and members and text[i + 1 : i + 2] not in {"", "]"}
        members.append("-" if between else re.escape(char))
        i += 1
    return None


def _split(text: str, start: int, close: str, separator: str) -> tuple[int, list[str]]:
    """The text between ``text[start]`` and its balanced ``close``, split at top-level
    ``separator``; the close's index, or -1 when it never closes."""
    opener = text[start]
    depth = 0
    pieces: list[str] = []
    piece_start = start + 1
    i = start + 1
    while i < len(text):
        char = text[i]
        if char == "\\":
            i += 2
            continue
        if char == opener:
            depth += 1
        elif char == close:
            if depth == 0:
                pieces.append(text[piece_start:i])
                return i, pieces
            depth -= 1
        elif char == separator and depth == 0:
            pieces.append(text[piece_start:i])
            piece_start = i + 1
        i += 1
    return -1, []


def _braces(pattern: str, budget: Budget) -> Iterator[str]:
    """Brace expansion as minimatch's ``brace-expansion`` does it: ``{a,b}``, nested, and
    ``{1..3}``, ``{01..10..3}`` or ``{a..e}`` sequences. A brace with neither stays literal."""
    i = 0
    while i < len(pattern):
        budget.spend()
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if char == "{" and pattern[i - 1 : i] != "$":
            close, options = _split(pattern, i, "}", ",")
            if close < 0:
                break
            budget.spend(close - i)
            if len(options) < 2:
                options = _sequence(options[0]) or []
            if options:
                head, tail = pattern[:i], pattern[close + 1 :]
                for option in options:
                    yield from _braces(head + option + tail, budget)
                return
        i += 1
    yield pattern


def _sequence(body: str) -> list[str] | None:
    found = _SEQUENCE.fullmatch(body)
    if found is None:
        return None
    if found[1] is not None:
        ends = found[1], found[2]
        # `{01..10}` pads every number to the wider end.
        width = (
            max(map(len, ends)) if any(re.match(r"-?0\d", end) for end in ends) else 0
        )
        numbers = _steps(int(ends[0]), int(ends[1]), found[3])
        return [str(number).zfill(width) for number in numbers]
    return [chr(letter) for letter in _steps(ord(found[4]), ord(found[5]), found[6])]


def _steps(start: int, end: int, step: str | None) -> Iterator[int]:
    size = abs(int(step or 1)) or 1
    if end < start:
        size = -size
    return islice(range(start, end + (1 if size > 0 else -1), size), _EXPANSIONS)
