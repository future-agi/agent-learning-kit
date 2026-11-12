from __future__ import annotations

import os
import re
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Mapping
from pathlib import Path

from fi._diagnostics import sanitize_diagnostic

_DIGITS = str.maketrans("", "", "0123456789")
# Numbers that tell two errors apart: a status, exit or signal code, an HTTP code.
_MEANINGFUL = re.compile(
    r"(?i)(?:status(?:_code)?|code|exit(?:\s+(?:code|status))?|returncode|signal|http(?:/\d\.\d)?)"
    r"[\"'\s:=_]*\d+"
)
# Opaque ids: hex runs that carry a digit, so two copies of one record with different ids match.
_OPAQUE_ID = re.compile(r"\b(?=[0-9a-fA-F-]*\d)[0-9a-fA-F][0-9a-fA-F-]{7,}\b")


_CHAINED = (
    "During handling of the above exception",
    "The above exception was the direct cause",
)


def _records(lines: list[str]) -> list[list[str]]:
    """Split log lines into records: a line with its indented continuation lines, and a Python
    traceback kept whole from its header to its exception line, chained tracebacks included."""

    records: list[list[str]] = []
    in_traceback = False
    for line in lines:
        indented = line[:1] in (" ", "\t")
        if line.startswith("Traceback (most recent call last)"):
            # Python prints a blank line between "...direct cause..." and the next traceback.
            said = [one for one in records[-1] if one.strip()] if records else []
            if said and said[-1].strip().startswith(_CHAINED):
                records[-1].append(line)
            else:
                records.append([line])
            in_traceback = True
        elif records and (indented or not line.strip()):
            records[-1].append(line)
        elif records and line.startswith(_CHAINED):
            records[-1].append(line)
        elif records and in_traceback:
            # The exception line closes the traceback.
            records[-1].append(line)
            in_traceback = False
        else:
            records.append([line])
            in_traceback = False
    return records


def _shape(record: list[str]) -> str:
    """A record with its volatile parts (times, ids, counters) removed and its meaningful numbers
    (status, exit and HTTP codes) kept, so only real copies share a shape."""

    text = _OPAQUE_ID.sub("#", "".join(record))
    kept: list[str] = []

    def hold(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        # A placeholder with no digits, so removing digits cannot touch it.
        return "\0" + chr(0xE000 + len(kept) - 1) + "\0"

    held = _MEANINGFUL.sub(hold, text).translate(_DIGITS)
    return re.sub("\0(.)\0", lambda m: kept[ord(m.group(1)) - 0xE000], held)


def _collapse_repeated_lines(text: str) -> str:
    """Keep a repeated log record only where it last appears, above it the count of its earlier
    copies.

    A healthy process repeats some lines all through its log (stream notices, polling, retries);
    left in, they push the one line that explains a failure out of the retained tail. A record is
    a line with its indented continuation, and a traceback stays whole, so two different errors
    never share or lose frames. Records that differ only in digits (times, ids, counters) count
    as the same record, but a status, exit or HTTP code keeps two errors apart. The kept copy is the
    last one, so its time is the latest, not the first.
    """

    records = _records(text.splitlines(keepends=True))
    shapes = [_shape(record) for record in records]
    last = {shape: index for index, shape in enumerate(shapes)}
    copies = Counter(shapes)
    kept: list[str] = []
    for index, record in enumerate(records):
        if last[shapes[index]] != index:
            continue
        if copies[shapes[index]] > 1:
            what = "line" if len(record) == 1 else f"{len(record)} lines"
            kept.append(
                f"[earlier copies of the next {what} omitted: {copies[shapes[index]] - 1}]\n"
            )
        kept.extend(record)
    return "".join(kept)


def retain_process_log(
    path: Path, *, work: Path, destination: Path, secrets: Mapping[str, str] | None
) -> None:
    relative = path.relative_to(work)
    fd = os.open(work, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in relative.parts[:-1]:
            child = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            os.close(fd)
            fd = child
        log_fd = os.open(
            relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
        )
        with os.fdopen(log_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return
            offset = max(0, info.st_size - 65536)
            stream.seek(offset)
            raw = stream.read(65536)
            if offset:
                # Drop the incomplete first record, including any split credential.
                raw = raw.partition(b"\n")[2]
        # Collapse before redacting: a redacted value can split lines that differ only in digits.
        # The omission notes can make the collapsed text longer than the 64 KB read, so all of it
        # is redacted before the tail is taken.
        collapsed = _collapse_repeated_lines(raw.decode("utf-8", errors="replace"))
        text = sanitize_diagnostic(collapsed, secrets, limit=sys.maxsize)[-8192:]
        if not text:
            return
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination_fd = os.open(
            destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        )
        try:
            try:
                os.mkdir("process-logs", mode=0o700, dir_fd=destination_fd)
            except FileExistsError:
                pass
            root_fd = os.open(
                "process-logs",
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=destination_fd,
            )
            try:
                name = "-".join(relative.parts[:-1])
                target = f"{name}-{uuid.uuid4().hex}.log"
                out = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=root_fd,
                )
                with os.fdopen(out, "w") as stream:
                    stream.write(text)
            finally:
                os.close(root_fd)
        finally:
            os.close(destination_fd)
    finally:
        os.close(fd)


async def upload_process_logs(root: Path, adapter, **kwargs) -> None:
    from .outbound import ArtifactKind

    directory = root / "process-logs"
    if not directory.is_dir() or directory.is_symlink():
        return
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(os.listdir(fd)):
            if not name.endswith(".log"):
                continue
            log_fd = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
            )
            with os.fdopen(log_fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                    continue
                body = stream.read(32768)
            await adapter.upload_artifact(
                name.encode() + b"\n" + body, kind=ArtifactKind.LOG, **kwargs
            )
    finally:
        os.close(fd)


def retained_process_evidence(
    directory: Path, before: set[Path], secrets, *, process_name: str | None = None
) -> str:
    """Read only bounded, sanitized logs retained by the current validation attempt."""
    if not directory.is_dir() or directory.is_symlink():
        return ""
    pieces = []
    remaining = 12000
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in sorted(os.listdir(fd)):
            if not name.endswith(".log") or directory / name in before:
                continue
            if process_name is not None and not re.fullmatch(
                rf"worlds-w\d+-{re.escape(process_name)}-[0-9a-f]{{32}}\.log", name
            ):
                continue
            try:
                log_fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd
                )
            except OSError:
                continue
            with os.fdopen(log_fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 32768:
                    continue
                text = sanitize_diagnostic(
                    stream.read(32768).decode("utf-8", errors="replace"),
                    secrets,
                    limit=32768,
                )
            piece = (sanitize_diagnostic(name, secrets) + "\n" + text)[:remaining]
            pieces.append(piece)
            remaining -= len(piece)
            if not remaining:
                break
    finally:
        os.close(fd)
    return "\n".join(pieces)[:12000]
