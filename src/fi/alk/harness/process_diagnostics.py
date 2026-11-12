from __future__ import annotations

import json
import os
import re
import stat
import uuid
from collections.abc import Iterable, Mapping
from pathlib import Path

from fi._diagnostics import sanitize_diagnostic

from .backends.files import _SKIP_DIRS, secret_text

# A string shorter than this is never masked. The files on the secret list also hold plain
# settings (PORT=8080, DEBUG=true, HOST=localhost, region names, 127.0.0.1), and masking those
# would shred every log that mentions them without hiding anything. Generated tokens, API keys
# and passwords are longer than 10; "localhost", "127.0.0.1", "postgres" and "us-east-1" are not.
# The cost is that a 9-character-or-shorter password in a listed file is not masked in a log.
MIN_MASKED_LENGTH = 10
_MAX_MASKED_FILE_BYTES = 64 * 1024  # a credential file is small; a state file is not
_MAX_MASKED_TOTAL_BYTES = 1024 * 1024  # all listed files together
_MAX_WALKED_ENTRIES = 50_000  # directory entries visited, so a huge tree cannot stall a run
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?[A-Za-z_][A-Za-z0-9_.-]*\s*[=:]\s*(.*?)\s*$")
_PLACEHOLDER = "<redacted>"


def _line_values(line: str) -> set[str]:
    """The values a NAME=value, export NAME=value or NAME: value line holds: as written, with
    quotes stripped, and without a trailing comment."""
    match = _ENV_LINE.match(line)
    if not match:
        return set()
    raw = match.group(1)
    values = {raw}
    if raw[:1] in {'"', "'"}:
        quote = raw[0]
        end = raw.find(quote, 1)
        values.add(raw[1:end] if end > 0 else raw[1:])
    else:
        values.add(re.sub(r"\s+#.*$", "", raw).strip())
    return {value.strip() for value in values}


def _json_strings(value) -> set[str]:
    if isinstance(value, str):
        return {value, *(line.strip() for line in value.splitlines())}
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(*(_json_strings(item) for item in items)) if items else set()


def _masks_of(text: str) -> set[str]:
    masks = {text, text.strip()}
    try:
        # A JSON key file: each string in it, since one is printed without its field name.
        masks |= _json_strings(json.loads(text))
    except (ValueError, RecursionError):
        pass
    for line in text.splitlines():
        masks.add(line.strip())
        masks |= _line_values(line)
    return masks


def source_secret_masks(root: Path) -> frozenset[str]:
    """The contents of the files on the secret list in the source, as strings to mask in a log.

    Bounded (file size, total size, entries visited), follows no link out of the root, and judges
    each path by where it resolves, relative to the root. Strings under MIN_MASKED_LENGTH are
    dropped."""
    root = root.resolve()
    masks: set[str] = set()
    total = 0
    visited = 0
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                listing = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in listing:
            visited += 1
            if visited > _MAX_WALKED_ENTRIES:
                return frozenset(mask for mask in masks if len(mask) >= MIN_MASKED_LENGTH)
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in _SKIP_DIRS:
                        stack.append(Path(entry.path))
                    continue
                resolved = Path(os.path.realpath(entry.path))
                if not resolved.is_relative_to(root):
                    continue
                if not secret_text(resolved.relative_to(root).as_posix()):
                    continue
                fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except (OSError, ValueError):
                continue
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_size > _MAX_MASKED_FILE_BYTES
                    or total + info.st_size > _MAX_MASKED_TOTAL_BYTES
                ):
                    continue
                total += info.st_size
                text = stream.read(_MAX_MASKED_FILE_BYTES + 1).decode("utf-8", errors="replace")
            masks |= _masks_of(text)
    return frozenset(mask for mask in masks if len(mask) >= MIN_MASKED_LENGTH)


def mask_source_secrets(text: str, masks: Iterable[str]) -> str:
    """Replace each mask string with the sanitizer's placeholder, longest first, so a whole key
    is masked before its lines."""
    for mask in sorted(masks, key=len, reverse=True):
        text = text.replace(mask, _PLACEHOLDER)
    return text


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
        text = sanitize_diagnostic(
            raw.decode("utf-8", errors="replace"), secrets, limit=65536
        )[-8192:]
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
    directory: Path,
    before: set[Path],
    secrets,
    *,
    process_name: str | None = None,
    masks: Iterable[str] = (),
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
                text = mask_source_secrets(
                    sanitize_diagnostic(
                        stream.read(32768).decode("utf-8", errors="replace"),
                        secrets,
                        limit=32768,
                    ),
                    masks,
                )
            piece = (
                mask_source_secrets(sanitize_diagnostic(name, secrets), masks) + "\n" + text
            )[:remaining]
            pieces.append(piece)
            remaining -= len(piece)
            if not remaining:
                break
    finally:
        os.close(fd)
    return "\n".join(pieces)[:12000]
