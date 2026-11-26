from __future__ import annotations

import json
import os
import re
import stat
import sys
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

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
_MAX_PUBLIC_FILE_BYTES = 16 * 1024 * 1024  # read to tell a large CA bundle from a key file
# Listed by a broad glob (*token*.json) but public: a model's tokenizer files.
_PUBLIC_TOKENIZER_NAMES = frozenset(
    {"tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"}
)
_MAX_WALKED_ENTRIES = 50_000  # directory entries visited, so a huge tree cannot stall a run
_ENV_LINE = re.compile(
    # The value runs to the end of the line and is stripped in code: a lazy value followed by
    # \s*$ backtracks quadratically on a long run of spaces.
    r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)\s*[=:]\s*(?P<value>.*)$"
)
_PLACEHOLDER = "<redacted>"
# Committed placeholders, not secrets: no masks are built from them.
_TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")
# A settings name that holds one of these parts has its value masked whatever the value looks like.
_SECRET_NAME_PARTS = (
    "KEY", "TOKEN", "SECRET", "PASS", "PWD", "AUTH", "CREDENTIAL", "DSN", "WEBHOOK",
)
_ENV_FILE_NAME = re.compile(r"(^|\.)env($|\.)", re.IGNORECASE)
_LONG_RUN = re.compile(r"[A-Za-z0-9+/=_-]{32,}")
_HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")
_PADDED_BASE64 = re.compile(r"[A-Za-z0-9+/]{8,}={1,3}")


def _is_template(name: str) -> bool:
    return name.casefold().endswith(_TEMPLATE_SUFFIXES)


def _is_env_file(name: str) -> bool:
    return bool(_ENV_FILE_NAME.search(name))


def _secret_name(name: str) -> bool:
    upper = name.upper()
    return any(part in upper for part in _SECRET_NAME_PARTS)


def _looks_like_secret(value: str) -> bool:
    """Whether a value, whatever its setting is called, has the shape of a secret: a URL with
    userinfo (user:pw@host), a query string or a token-like path segment (a webhook), or a
    token-like string. Plain words, model names and URLs with none of these
    (https://api.openai.com/v1) do not."""
    if not value or any(char.isspace() for char in value):
        return False
    if "://" in value:
        try:
            parts = urlsplit(value)
        except ValueError:
            return True
        return (
            "@" in parts.netloc
            or bool(parts.query)
            or "=" in parts.fragment
            or _webhook_tail(parts) is not None
            or any(_token_segment(segment) for segment in parts.path.split("/"))
        )
    if len(value) >= 16 and any(c.isalpha() for c in value) and any(c.isdigit() for c in value):
        return True
    if _HEX_RUN.fullmatch(value):
        return True
    return bool(
        _LONG_RUN.fullmatch(value)
        and sum(
            (any(c.islower() for c in value), any(c.isupper() for c in value),
             any(c.isdigit() for c in value))
        )
        >= 2
    )


_WEBHOOK_SEGMENTS = frozenset({"hook", "hooks", "webhook", "webhooks"})


def _webhook_tail(parts) -> str | None:
    """The path after a webhook's marker (a hooks.* host, or a hook/hooks/webhook(s) segment),
    which holds its token, perhaps split over short segments; None for any other URL."""
    segments = [segment for segment in parts.path.split("/")]
    host = (parts.hostname or "").lower()
    for index, segment in enumerate(segments):
        if segment.lower() in _WEBHOOK_SEGMENTS:
            tail = "/".join(segments[index + 1 :]).strip("/")
            return tail or None
    if host.split(".", 1)[0] in _WEBHOOK_SEGMENTS:
        tail = parts.path.strip("/")
        return tail or None
    return None


def _token_segment(segment: str) -> bool:
    """Whether one segment of a URL's path is a token, as a webhook's is. Stricter than a whole
    value's rule, so a model or project name in a path (gpt-4o-mini-2024-07-18,
    Llama-3.1-8B-Instruct, my-project-123456, claude-sonnet-4-20250514) is left for the repair
    to read: a token has one long piece between its separators, a model id has many short ones.
    A webhook's token, whatever its pieces, is caught by its marker (_webhook_tail)."""
    if _HEX_RUN.fullmatch(segment):
        return True
    if max(len(piece) for piece in re.split(r"[-_.]", segment)) < 16:
        return False
    if _LONG_RUN.fullmatch(segment):
        return True
    if len(segment) < 24 or "." in segment:
        return False
    has_digit = any(c.isdigit() for c in segment)
    has_letter = any(c.isalpha() for c in segment)
    mixed_case = any(c.islower() for c in segment) and any(c.isupper() for c in segment)
    return has_letter and (has_digit or (mixed_case and "-" not in segment))


def _line_values(line: str, *, only_secrets: bool = False) -> set[str]:
    """The values a NAME=value, export NAME=value or NAME: value line holds: as written, with
    quotes stripped, and without a trailing comment. With ``only_secrets``, none unless the
    setting's name or the value's shape says it is one."""
    match = _ENV_LINE.match(line)
    if not match:
        return set()
    raw = match.group("value").strip()
    values = {raw}
    if raw[:1] in {'"', "'"}:
        quote = raw[0]
        # The closing quote is the first one not escaped by a backslash: K="ab\"<v>" holds ab"<v>.
        end = 1
        while end < len(raw) and raw[end] != quote:
            end += 2 if raw[end] == "\\" else 1
        inner = raw[1:end] if end < len(raw) else raw[1:]
        values.add(inner)
        unescaped = inner.replace("\\" + quote, quote).replace("\\\\", "\\")
        values.add(unescaped)
        values.update(inner.split("\\" + quote))
        if raw.find(quote, 1) > 0:
            values.add(raw[1 : raw.find(quote, 1)])  # a backslash before a closing quote
    else:
        # A trailing comment starts at the first space or tab before a #; found in one pass.
        comment = re.search(r"[ \t]#", raw)
        values.add((raw[: comment.start()] if comment else raw).strip())
    values = {value.strip() for value in values}
    if only_secrets and not (
        _secret_name(match.group("name"))
        or any(_looks_like_secret(value) for value in values)
    ):
        return set()
    # A value written on one line with \n escapes (a PEM) is logged expanded once dotenv reads it.
    for value in tuple(values):
        if any(escape in value for escape in ("\\n", "\\r", "\\t")):
            expanded = (
                value.replace("\\r\\n", "\n").replace("\\n", "\n")
                .replace("\\r", "\r").replace("\\t", "\t")
            )
            values.add(expanded)
            values.update(line.strip() for line in expanded.splitlines())
    # A secret URL's parts, since a log can print it in pieces or in its canonical form.
    named = not only_secrets or _secret_name(match.group("name"))
    for value in tuple(values):
        if "://" in value:
            values |= _url_masks(value, named=named)
    return values


_DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _url_masks(value: str, *, named: bool = False) -> set[str]:
    """The parts of a URL a log may print apart from it: its token-shaped path segments (and the
    path from the first of them), its query and fragment values, its userinfo password, and the
    URL with the host lowercased and a default port dropped (as httpx logs it). With ``named``
    (the setting's name says secret), every long path segment counts as a token."""
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return set()
    masks: set[str] = set()
    tail = _webhook_tail(parts)
    if tail is not None:
        # The whole tail, with and without its slashes around it, however the log cuts it.
        masks.update({tail, tail + "/", "/" + tail})
    segments = parts.path.split("/")
    for index, segment in enumerate(segments):
        if _token_segment(segment) or (named and len(segment) >= 16):
            masks.add(segment)
            masks.add("/".join(segments[index:]))
    for field in (parts.query, parts.fragment):
        # A value on its own only when it has a secret's shape or its name says secret, so an
        # ordinary word (production, verify-full) stays readable; decoded, and percent-encoded
        # as a log prints it (an Azure SAS sig ends in %3D). The whole field is masked anyway.
        for pair in field.split("&"):
            name, _, raw = pair.partition("=")
            decoded = unquote(raw.replace("+", " "))
            if raw and (
                _secret_name(name) or _looks_like_secret(decoded) or _looks_like_secret(raw)
            ):
                masks.update({raw, decoded})
        if field:
            masks.add(field)
    if parts.query:
        masks.add(parts.path + "?" + parts.query)
    if parts.password and len(parts.password) >= MIN_MASKED_LENGTH:
        masks.update({parts.password, unquote(parts.password)})
    # A username only when it is a token (ghp_...), not an ordinary account name.
    if parts.username and _looks_like_secret(unquote(parts.username)):
        masks.update({parts.username, unquote(parts.username)})
    host = (parts.hostname or "").lower()
    if host:
        netloc = host if port in (None, _DEFAULT_PORTS.get(parts.scheme.lower())) else f"{host}:{port}"
        if "@" in parts.netloc:
            netloc = parts.netloc.rsplit("@", 1)[0] + "@" + netloc
        masks.add(urlunsplit((parts.scheme.lower(), netloc, parts.path, parts.query, parts.fragment)))
    return masks


def _json_strings(value) -> set[str]:
    if isinstance(value, str):
        return {value, *(line.strip() for line in value.splitlines())}
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return set().union(*(_json_strings(item) for item in items)) if items else set()


def _field_masks(text: str, name: str) -> set[str]:
    """The fields of a credentials file that is not NAME=value, since a field is printed alone:
    .pgpass (host:port:db:user:password), .netrc (whitespace-separated words) and .git-credentials
    (a URL per line, whose userinfo holds the password)."""
    kind = name.casefold().lstrip("_.")
    fields: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if kind == "pgpass":
            fields.update(part.strip() for part in line.split(":"))
        elif kind == "netrc":
            fields.update(line.split())
        elif kind == "git-credentials":
            try:
                parts = urlsplit(line)
                fields.update(
                    unquote(field) for field in (parts.password, parts.username) if field
                )
                fields.update(field for field in (parts.password, parts.username) if field)
            except ValueError:
                pass
    return fields


def _masks_of(text: str, *, env: bool = False, names: tuple[str, ...] = ()) -> set[str]:
    """The strings to mask for one listed file. A non-env file (PEM, JSON key, credentials) is
    masked whole, by line and by value. A settings (.env) file is masked whole, and per setting
    only where the name or the value's shape says secret; a line that is not a setting is masked
    like a line of a non-env file."""
    masks = {text, text.strip()}
    for name in names:
        masks |= _field_masks(text, name)
    if not env:
        try:
            # A JSON key file: each string in it, since one is printed without its field name.
            masks |= _json_strings(json.loads(text))
        except (ValueError, RecursionError):
            pass
    for line in text.splitlines():
        if not env:
            masks.add(line.strip())
            masks |= _line_values(line)
            continue
        match = _ENV_LINE.match(line)
        if match and not _PADDED_BASE64.fullmatch(line.strip()):
            masks |= _line_values(line, only_secrets=True)
        else:
            # Not a setting (a PEM line inside a quoted value, a padded base64 line, a comment).
            masks.add(line.strip())
    return masks


def source_secret_masks(root: Path) -> frozenset[str]:
    """The contents of the files on the secret list in the source, as strings to mask in a log.

    Bounded (file size, total size, entries visited), follows no link out of the root, and judges
    each path by its own name and by where it resolves, relative to the root. Strings under
    MIN_MASKED_LENGTH are dropped. It never returns a partial set: a folder it cannot scan, a
    listed file it cannot read or that is over a bound, or a walk past the entry bound raises
    OSError, and the callers then withhold the evidence."""
    root = root.resolve()
    masks: set[str] = set()
    total = 0
    visited = 0
    stack = [root]
    while stack:
        folder = stack.pop()
        with os.scandir(folder) as entries:  # an unreadable folder raises OSError
            listing = sorted(entries, key=lambda entry: entry.name)
        for entry in listing:
            visited += 1
            if visited > _MAX_WALKED_ENTRIES:
                raise OSError("too many entries to mask the source's listed files")
            if entry.is_dir(follow_symlinks=False):
                if entry.name not in _SKIP_DIRS:
                    stack.append(Path(entry.path))
                continue
            own = Path(entry.path).relative_to(root).as_posix()
            try:
                resolved = Path(os.path.realpath(entry.path))
                if not resolved.is_relative_to(root):
                    continue
                if not (secret_text(own) or secret_text(resolved.relative_to(root).as_posix())):
                    continue
            except ValueError:
                continue
            if _is_template(entry.name) or _is_template(resolved.name):
                continue
            try:
                fd = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue  # a dangling link: nothing to read
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    continue
                rest = _public_listed_file(entry.name, resolved.name, stream, info.st_size)
                if rest is not None:
                    # Only what isn't public is masked, and only it counts against the bounds.
                    size = len(rest.encode())
                    if size > _MAX_MASKED_FILE_BYTES or total + size > _MAX_MASKED_TOTAL_BYTES:
                        raise OSError("a listed file is too large to mask")
                    total += size
                    if rest.strip():
                        masks |= _masks_of(rest, names=(entry.name, resolved.name))
                    continue
                if (
                    info.st_size > _MAX_MASKED_FILE_BYTES
                    or total + info.st_size > _MAX_MASKED_TOTAL_BYTES
                ):
                    raise OSError("a listed file is too large to mask")
                total += info.st_size
                text = stream.read(_MAX_MASKED_FILE_BYTES + 1).decode("utf-8", errors="replace")
            masks |= _masks_of(
                text,
                env=_is_env_file(entry.name) or _is_env_file(resolved.name),
                names=(entry.name, resolved.name),
            )
    return frozenset(mask for mask in masks if len(mask) >= MIN_MASKED_LENGTH)


def _public_listed_file(name: str, resolved_name: str, stream, size: int) -> str | None:
    """What of a listed file needs masks, when part of it is public: "" for a model's tokenizer
    file, or the text outside the certificates of a PEM file of certificates only (a CA bundle,
    whose comments stay masked). None for any other file, a PEM holding a key above all."""
    names = {name.casefold(), resolved_name.casefold()}
    if names & _PUBLIC_TOKENIZER_NAMES:
        return ""
    if not any(n.endswith((".pem", ".crt", ".cer")) for n in names):
        return None
    if size > _MAX_PUBLIC_FILE_BYTES:
        return None
    text = stream.read(_MAX_PUBLIC_FILE_BYTES + 1).decode("utf-8", errors="replace")
    stream.seek(0)
    kinds = set(re.findall(r"-----BEGIN ([A-Z0-9 ]+)-----", text))
    if kinds != {"CERTIFICATE"}:
        return None
    return re.sub(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", "", text, flags=re.DOTALL
    )


def mask_source_secrets(text: str, masks: Iterable[str]) -> str:
    """Replace each mask string with the sanitizer's placeholder, longest first, so a whole key
    is masked before its lines."""
    for mask in sorted(masks, key=len, reverse=True):
        text = text.replace(mask, _PLACEHOLDER)
    return text


_DIGITS = str.maketrans("", "", "0123456789")
# Times and addresses: volatile wherever they appear, digits next to letters included (a "T" or a "Z").
_VOLATILE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
    r"|\b\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\b"
    r"|0x[0-9a-fA-F]+"
)
# A name that carries digits (gpt-4, llm-2, s3transfer2): part of what the record is about, not a counter.
_NAME_WITH_DIGITS = re.compile(
    r"(?<![0-9A-Za-z])[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*(?![0-9A-Za-z])"
)
# Numbers that tell two errors apart: a status, exit or signal code, an HTTP code.
_MEANINGFUL = re.compile(
    r"(?i)(?:status(?:_code)?|code|exit(?:\s+(?:code|status))?|returncode|signal|http(?:/\d\.\d)?)"
    r"[\"'\s:=_]*\d+"
)
# Opaque ids, so two copies of one record with different ids match: hex runs that carry a digit
# (uuids, hashes), and letter-and-digit runs of twelve or more (job, request and sandbox ids;
# shorter names such as a module name stay as they are, so two different errors stay apart).
# Each alternative starts only where a run starts, so a long run is scanned once, not once per
# position: the log is written by the tested agent and must not make this slow.
_OPAQUE_ID = re.compile(
    r"(?<![0-9A-Za-z-])(?=[0-9a-fA-F-]*\d)[0-9a-fA-F][0-9a-fA-F-]{7,}\b"
    r"|(?<![A-Za-z0-9])(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{12,}(?![A-Za-z0-9])"
)


_CHAINED = (
    "During handling of the above exception",
    "The above exception was the direct cause",
    # Java and the other JVM languages print an exception's cause and suppressed ones unindented.
    "Caused by:",
    "Suppressed:",
)
# A Go panic's stack: each goroutine's header, then for each frame its function line, unindented, and its file line,
# indented. Both belong to the panic above them.
_GOROUTINE = re.compile(r"goroutine \d+ \[[^\]]*\]:\s*$")


def _records(lines: list[str]) -> list[list[str]]:
    """Split log lines into records: a line with its continuation lines (indented ones, a JVM exception's
    cause, a Go panic's goroutine stacks), and a Python traceback kept whole from its header to its exception
    line, chained tracebacks included."""

    records: list[list[str]] = []
    in_traceback = False
    in_goroutine = False
    for index, line in enumerate(lines):
        indented = line[:1] in (" ", "\t")
        following = lines[index + 1] if index + 1 < len(lines) else ""
        if records and _GOROUTINE.match(line):
            records[-1].append(line)
            in_goroutine = True
            continue
        if (
            records
            and in_goroutine
            and line.rstrip().endswith(")")
            and following[:1] in (" ", "\t")
        ):
            records[-1].append(line)
            continue
        if not (indented or not line.strip()):
            in_goroutine = False
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

    # NUL brackets the placeholders below, so a NUL in the log itself must not reach them.
    text = _VOLATILE.sub("#", _OPAQUE_ID.sub("#", "".join(record).replace("\0", "")))
    kept: list[str] = []

    def hold(match: re.Match[str]) -> str:
        kept.append(match.group(0))
        # A placeholder with no digits, so removing digits cannot touch it.
        return "\0" + chr(0xE000 + len(kept) - 1) + "\0"

    def hold_name(match: re.Match[str]) -> str:
        return (
            hold(match) if any(c.isdigit() for c in match.group(0)) else match.group(0)
        )

    # Only digits that stand alone (counters, durations, sizes) are dropped.
    held = _NAME_WITH_DIGITS.sub(hold_name, _MEANINGFUL.sub(hold, text)).translate(
        _DIGITS
    )
    return re.sub("\0(.)\0", lambda m: kept[ord(m.group(1)) - 0xE000], held)


def _collapse_repeated_lines(text: str) -> str:
    """Keep a repeated log record only where it last appears, above it the count of its earlier
    copies.

    A healthy process repeats some lines all through its log (stream notices, polling, retries);
    left in, they push the one line that explains a failure out of the retained tail. A record is
    a line with its indented continuation, and a traceback stays whole, so two different errors
    never share or lose frames. Records that differ only in digits (times, counters) or opaque ids
    (uuids, hashes, long letter-and-digit ids) count as the same record, but a status, exit or
    HTTP code keeps two errors apart. The kept copy is the
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
