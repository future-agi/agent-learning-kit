"""A bundle's own SQL files, read into statements the way psql reads them and run through the driver as the
restricted login: nothing in them is a client command, so a file can run SQL as that login and nothing else."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

# A bundle's own SQL files run as the restricted login in this process (psycopg), never through psql: nothing in them
# is a client command, so a file can run SQL as that login and nothing else. The file is read into statements the way
# psql reads it (strings, E'' strings, quoted names, dollar quotes, comments, parentheses, BEGIN ATOMIC bodies) and
# each runs on its own, as psql runs it; a `COPY ... FROM STDIN` statement takes the data that follows it up to `\.`.
# pg_dump's own restriction lines are skipped, and a psql command (a backslash where a statement would start) fails
# the build. A misread can only fail the build: whatever reaches the server is SQL, as the same login.
_PG_DUMP_RESTRICT = re.compile(r"\\(?:un)?restrict [A-Za-z0-9]+[ \t]*$")
_COPY_FROM_STDIN = re.compile(r"^\s*COPY\b.*\bFROM\s+STDIN\b", re.IGNORECASE | re.DOTALL)
# As psql reads them: a byte past ASCII is part of a name, and a dollar-quote tag is a name without a `$`.
_DOLLAR = re.compile(r"\$(?:[A-Za-z_\u0080-\U0010ffff][A-Za-z0-9_\u0080-\U0010ffff]*)?\$")
_WORD = re.compile(r"[A-Za-z_]\w*")


def _in_name(char: str) -> bool:
    return char.isascii() and (char.isalnum() or char in "_$") or ord(char) >= 0x80


def bundle_statements(
    text: str, backslash_escapes: Callable[[], bool] = lambda: False
) -> Iterator[tuple[str, str, str, int]]:
    """A bundle SQL file as psql reads it: ("sql", statement, "", line), ("copy", statement, data, line), or
    ("command", text, "", line) for a backslash where a statement would start (pg_dump's restriction lines are left
    out). ``backslash_escapes``: whether the server now reads '' strings with backslash escapes
    (standard_conforming_strings off), asked at each string as psql asks the server's own report, so every way a file
    may change it is followed. Raises ValueError for a COPY with no end-of-data line."""
    i, n, line = 0, len(text), 1
    start, start_line = 0, 1
    started = False  # anything but whitespace and comments since the last statement
    state: str | None = None
    parens = 0
    atomic = 0
    while i < n:
        char = text[i]
        if state is None:
            if char == "\n":
                line += 1
                i += 1
                continue
            if text.startswith("--", i):
                end = text.find("\n", i)
                i = n if end < 0 else end
                continue
            if text.startswith("/*", i):
                state, i = "/*1", i + 2
                continue
            if not started:
                if char.isspace():
                    i += 1
                    continue
                if char == "\\":
                    end = text.find("\n", i)
                    end = n if end < 0 else end
                    command = text[i:end]
                    if not _PG_DUMP_RESTRICT.fullmatch(command):
                        yield "command", command, "", line
                    i = end
                    start = i
                    continue
                started, start, start_line = True, i, line
            if char == "'":
                before = text[i - 1] if i else " "
                earlier = text[i - 2] if i > 1 else " "
                prefixed = before in "eE" and not _in_name(earlier)
                state, i = ("E'" if prefixed or backslash_escapes() else "'"), i + 1
                continue
            if char == '"':
                state, i = '"', i + 1
                continue
            if char == "$":
                tag = _DOLLAR.match(text, i)
                before = text[i - 1] if i else " "
                if tag and not _in_name(before):
                    state, i = tag.group(), tag.end()
                    continue
            if char == "(":
                parens += 1
            elif char == ")":
                parens = max(0, parens - 1)
            elif char.isalpha() or char == "_":
                word = _WORD.match(text, i)
                token = word.group().upper()
                if token == "ATOMIC" and re.search(r"\bBEGIN\s*$", text[start:i], re.IGNORECASE):
                    atomic += 1
                elif atomic and token == "CASE":
                    atomic += 1
                elif atomic and token == "END":
                    atomic -= 1
                i = word.end()
                continue
            elif char == ";" and parens == 0 and atomic == 0:
                statement = text[start : i + 1]
                i += 1
                started = False
                if _COPY_FROM_STDIN.match(statement):
                    newline = text.find("\n", i)
                    data_start = n if newline < 0 else newline + 1
                    line += text.count("\n", i, data_start)
                    data_lines: list[str] = []
                    position = data_start
                    while True:
                        if position >= n:
                            raise ValueError(f"line {start_line}: a COPY ... FROM STDIN has no \\. line to end its data")
                        end = text.find("\n", position)
                        end = n if end < 0 else end
                        row = text[position:end]
                        line += 1
                        position = end + 1
                        if row == "\\.":
                            break
                        data_lines.append(row + "\n")
                    yield "copy", statement[:-1], "".join(data_lines), start_line
                    i, start = position, position
                else:
                    yield "sql", statement, "", start_line
                    start = i
                continue
            i += 1
        elif state == "E'":
            if char == "\\":
                if i + 1 < n and text[i + 1] == "\n":
                    line += 1
                i += 2
            elif char == "'":
                if text.startswith("''", i):
                    i += 2
                else:
                    state, i = None, i + 1
            else:
                line += char == "\n"
                i += 1
        elif state in ("'", '"'):
            closing = text.find(state, i)
            if closing < 0:
                line += text.count("\n", i)
                i = n
                break
            line += text.count("\n", i, closing)
            if text.startswith(state * 2, closing):
                i = closing + 2
                continue
            state, i = None, closing + 1
        elif state.startswith("/*"):
            depth = int(state[2:])
            opening, closing = text.find("/*", i), text.find("*/", i)
            if closing < 0:
                line += text.count("\n", i)
                i = n
                break
            if 0 <= opening < closing:
                line += text.count("\n", i, opening)
                state, i = f"/*{depth + 1}", opening + 2
            else:
                line += text.count("\n", i, closing)
                state, i = (f"/*{depth - 1}" if depth > 1 else None), closing + 2
        else:
            closing = text.find(state, i)
            if closing < 0:
                line += text.count("\n", i)
                i = n
                break
            line += text.count("\n", i, closing)
            i, state = closing + len(state), None
    rest = text[start:]
    if rest.strip() and started:
        yield "sql", rest, "", start_line


class BundleSqlRefused(Exception):
    """What Postgres, or the reader, refused in a bundle's SQL file: the line and what was said."""


def run_bundle_sql(file: Path, *, port: int, dbname: str, user: str, password: str) -> None:
    """Run one of a bundle's SQL files as ``user``, statement by statement as psql runs it (autocommit, no
    parameters), each COPY FROM STDIN through the driver. Raises `BundleSqlRefused`."""
    import psycopg

    text = file.read_text(encoding="utf-8-sig")
    with psycopg.connect(
        host="localhost", port=port, user=user, password=password, dbname=dbname, autocommit=True
    ) as connection:

        def reads_escapes() -> bool:
            return connection.info.parameter_status("standard_conforming_strings") == "off"

        at = 0
        try:
            for kind, statement, data, at in bundle_statements(text, reads_escapes):
                if kind == "command":
                    raise BundleSqlRefused(
                        f"line {at}: {statement.split()[0]} is a psql command, not SQL; psql commands are not run. "
                        "Inline a file's rows as COPY ... FROM STDIN, and list another SQL file in the store's "
                        "migrations or seed files instead of \\i"
                    )
                if kind == "copy":
                    with connection.cursor().copy(statement.encode("utf-8")) as copy:
                        copy.write(data.encode("utf-8"))
                else:
                    connection.execute(statement.encode("utf-8"))
        except ValueError as error:
            raise BundleSqlRefused(str(error)) from error
        except psycopg.Error as error:
            said = getattr(getattr(error, "diag", None), "message_primary", None) or str(error)
            raise BundleSqlRefused(f"line {at}: {said}" if at else said) from error
