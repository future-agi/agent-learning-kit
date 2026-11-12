"""Read-only file tools for backends that have no host CLI behind them.

Claude Code brings its own Read, Glob and Grep. Any other loop that is granted those names gets
these: same names, same core arguments, same read-only stance. They exist so a stage's skill
text ("read the repository, never write to it") means the same thing on every backend.

Write access is not implemented on purpose. The harness's own artifacts go through its tools,
which validate them; the agent under test is somebody's real repository. A backend that wants
write tools is asking to skip the gates, and the answer is no.
"""

from __future__ import annotations

import fnmatch
import os
import re
from pathlib import Path

from .base import ToolSpec

MAX_READ_LINES = 2000
MAX_LINE_CHARS = 2000
MAX_MATCHES = 200
_SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"}


def _ok(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}]}


def _error(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def _resolved(raw: str, cwd: Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else cwd / path


# Where credentials live in a sandbox or a checkout, and what reads them. A session never needs
# any of them: the platform hands secrets to processes as files and environment, not to a model.
#
# One list of globs, read the way gitignore and ripgrep read them: a glob matches a path when it
# matches the path or any folder above it, `*` stands for any run of characters within one name,
# `**/` at the start means "in any folder", and case is ignored. The kit's own tools, the scan's
# tools and the gate match paths against the regex built from it (`_SECRET_PATHS`); Claude Code
# gets the same list as Read deny rules (`secret_read_rules`), which its Grep and Glob turn into
# exclusions. A glob uses only letters, digits and `.-_*/`: the CLI skips a rule holding `[`, and
# the SDK splits its rules on commas and spaces.
SECRET_GLOBS: tuple[str, ...] = (
    "**/.env*",
    "**/*.env",
    "**/*.env.*",
    "**/.claude",
    "**/.ssh",
    "**/.aws",
    "**/.config/gcloud",
    "**/.authoring-credentials",
    "**/.netrc",
    "**/.npmrc",
    "**/.pypirc",
    "**/*credential*.json",
    "**/*secret*.json",
    "**/*token*.json",
    "**/*service-account*.json",
    "**/*service_account*.json",
    "**/*serviceaccount*.json",
    "**/*secret*.yaml",
    "**/*secret*.yml",
    "**/*secret*.toml",
    "**/*credential*.yaml",
    "**/*credential*.yml",
    "**/*credential*.toml",
    "**/*secret*.ini",
    "**/*secret*.cfg",
    "**/*secret*.conf",
    "**/*secret*.properties",
    "**/*secret*.xml",
    "**/*credential*.ini",
    "**/*credential*.cfg",
    "**/*credential*.conf",
    "**/*credential*.properties",
    "**/*credential*.xml",
    "**/*firebase-adminsdk*.json",
    "**/*credential*.csv",
    "**/*accesskeys*.csv",
    "**/rootkey.csv",
    "**/*secret*.txt",
    "**/*credential*.txt",
    "**/secrets/*.txt",
    "**/.secrets",
    "**/.dev.vars",
    "**/.dev.vars.*",
    "**/local.settings.json",
    "**/key.properties",
    "**/keystore.properties",
    "**/*.p8",
    "**/*.ppk",
    "**/.config/gh/hosts.yml",
    "**/.databrickscfg",
    "**/.terraformrc",
    "**/.cargo/credentials",
    "**/.gem/credentials",
    "**/*.pem",
    "**/*.key",
    "**/*.p12",
    "**/*.pfx",
    "**/*.jks",
    "**/*.keystore",
    "**/.kube/config",
    "**/.kube/config.*",
    "**/kubeconfig",
    "**/kubeconfig.yaml",
    "**/kubeconfig.yml",
    "**/*.kubeconfig",
    "**/.docker/config.json",
    "**/.docker/contexts",
    "**/.dockercfg",
    "**/.pgpass",
    "**/.htpasswd",
    "**/.git-credentials",
    "**/.my.cnf",
    "**/.s3cfg",
    "**/.boto",
    "**/.vault-token",
    "**/id_rsa",
    "**/id_dsa",
    "**/id_ecdsa",
    "**/id_ed25519",
    "**/id_ecdsa_sk",
    "**/id_ed25519_sk",
    "**/*.tfstate",
    "**/*.tfstate.backup",
    "**/*.tfstate.*.backup",
    "**/*.tfvars",
    "**/*.tfvars.json",
    "**/*-key.json",
    "**/*_key.json",
    "**/*-sa.json",
)
# Exact names whose `.bak` copy is a credential too. `.kube/config.*` already covers its own.
_WITH_BACKUP: tuple[str, ...] = (
    "**/kubeconfig",
    "**/kubeconfig.yaml",
    "**/kubeconfig.yml",
    "**/*.kubeconfig",
    "**/.docker/config.json",
    "**/.dockercfg",
    "**/.pgpass",
    "**/.htpasswd",
    "**/.git-credentials",
    "**/.my.cnf",
    "**/.s3cfg",
    "**/.boto",
    "**/.vault-token",
    "**/id_rsa",
    "**/id_dsa",
    "**/id_ecdsa",
    "**/id_ed25519",
    "**/id_ecdsa_sk",
    "**/id_ed25519_sk",
)
ALL_SECRET_GLOBS: tuple[str, ...] = SECRET_GLOBS + tuple(f"{glob}.bak" for glob in _WITH_BACKUP)
# The settings files a model citation may name, under its own rule (`model_citation`).
_ENV_GLOBS = ("**/.env*", "**/*.env", "**/*.env.*")
# The platform's own roots, which are paths, not names.
_ROOTS = r"^/proc(/|$)|^/run/futureagi(/|$)|^/run/secrets(/|$)"


def _glob_pattern(glob: str) -> str:
    """The regex for one `**/` glob: the name parts at any depth, as the path or a folder above it."""
    body = glob.removeprefix("**/")
    return "(^|/)" + "".join("[^/]*" if char == "*" else re.escape(char) for char in body) + "(/|$)"


def _compiled(globs: tuple[str, ...]) -> re.Pattern[str]:
    return re.compile("|".join([*(_glob_pattern(glob) for glob in globs), _ROOTS]), re.IGNORECASE)


_SECRET_PATHS = _compiled(ALL_SECRET_GLOBS)
# The same, less those settings files: what a model citation may not name.
_SECRET_BUT_ENV = _compiled(tuple(glob for glob in ALL_SECRET_GLOBS if glob not in _ENV_GLOBS))


def secret_text(text: str, *, env_allowed: bool = False) -> bool:
    """Whether a path, as given and never resolved, names a place on the list. Callers pass a
    path relative to the source, so the folders the source sits in don't count."""
    return bool((_SECRET_BUT_ENV if env_allowed else _SECRET_PATHS).search(text))


def secret_read_rules() -> list[str]:
    """Claude Code's Read deny rules for the list, anchored at the file system's root (`//`), so
    they hold wherever a search starts and whatever the session's folder is."""
    return [f"Read(//{glob})" for glob in ALL_SECRET_GLOBS]


# A search rooted here would reach into where credentials live.
_SECRET_ROOTS = re.compile(r"^/(proc|run/futureagi|run/secrets)/")
SECRET_REFUSAL = (
    "Environment variables, credentials, keys and tokens are kept out of this session on purpose. "
    "Do not read, print or work around this; tell the person you cannot share or handle secrets, "
    "and carry on with the rest of the task."
)


def secret_path(raw: str, cwd: Path | None = None) -> bool:
    """Whether a path leads to where credentials live, judged where it resolves, not how it is written."""
    resolved = os.path.realpath(_resolved(raw, cwd or Path.cwd()))
    return bool(_SECRET_PATHS.search(raw) or _SECRET_PATHS.search(resolved))


def above_secrets(raw: str, cwd: Path | None = None) -> bool:
    """Whether a search rooted here would reach into where credentials live."""
    root = os.path.realpath(_resolved(raw, cwd or Path.cwd()))
    return bool(_SECRET_ROOTS.match(root + "/")) or root in ("/", "/run", os.path.expanduser("~"))


def _walk(root: Path):
    """Every file under root, skipping the trees nobody means when they say 'the repo'."""
    stack = [root]
    while stack:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in _SKIP_DIRS:
                    stack.append(entry)
            else:
                yield entry


def file_tools(cwd: str | None) -> list[ToolSpec]:
    """Read, Glob and Grep rooted at the session's working directory."""
    base = Path(cwd) if cwd else Path.cwd()

    async def read(args: dict) -> dict:
        raw = str(args.get("file_path") or args.get("path") or "")
        if not raw:
            return _error("file_path is required")
        path = _resolved(raw, base)
        if secret_path(raw, base):
            return _error(SECRET_REFUSAL)
        if not path.is_file():
            return _error(f"{path} is not a file that exists")
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return _error(f"could not read {path}: {exc}")
        offset = max(int(args.get("offset") or 1), 1)
        limit = min(int(args.get("limit") or MAX_READ_LINES), MAX_READ_LINES)
        window = lines[offset - 1 : offset - 1 + limit]
        numbered = "\n".join(
            f"{offset + index}\t{line[:MAX_LINE_CHARS]}"
            for index, line in enumerate(window)
        )
        remaining = len(lines) - (offset - 1 + len(window))
        if remaining > 0:
            numbered += f"\n... ({remaining} more lines; call again with offset={offset + len(window)})"
        return _ok(numbered or "(empty file)")

    async def glob(args: dict) -> dict:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return _error("pattern is required")
        root = _resolved(str(args.get("path") or "."), base)
        if secret_path(str(root)) or above_secrets(str(root)):
            return _error(SECRET_REFUSAL)
        if not root.is_dir():
            return _error(f"{root} is not a directory that exists")
        matches = []
        for entry in _walk(root):
            if secret_path(str(entry)):
                continue
            relative = str(entry.relative_to(root))
            if fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(
                entry.name, pattern
            ):
                matches.append(str(entry))
                if len(matches) >= MAX_MATCHES:
                    break
        return _ok("\n".join(matches) or f"no files match {pattern!r} under {root}")

    async def grep(args: dict) -> dict:
        pattern = str(args.get("pattern") or "")
        if not pattern:
            return _error("pattern is required")
        try:
            expression = re.compile(pattern)
        except re.error as exc:
            return _error(f"invalid regular expression: {exc}")
        root = _resolved(str(args.get("path") or "."), base)
        if secret_path(str(root)) or above_secrets(str(root)):
            return _error(SECRET_REFUSAL)
        wanted = str(args.get("glob") or "")
        hits: list[str] = []
        targets = [root] if root.is_file() else list(_walk(root)) if root.is_dir() else []
        if not targets:
            return _error(f"{root} does not exist")
        for entry in targets:
            if secret_path(str(entry)) or (wanted and not fnmatch.fnmatch(entry.name, wanted)):
                continue
            try:
                text = entry.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if expression.search(line):
                    hits.append(f"{entry}:{number}:{line[:400]}")
                    if len(hits) >= MAX_MATCHES:
                        break
            if len(hits) >= MAX_MATCHES:
                break
        return _ok("\n".join(hits) or f"no lines match {pattern!r}")

    return [
        ToolSpec(
            name="Read",
            description=(
                "Read a file. Arguments: file_path (absolute or relative to the working "
                "directory), optional offset (1-based first line) and limit (line count)."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["file_path"],
            },
            handler=read,
        ),
        ToolSpec(
            name="Glob",
            description=(
                "Find files by name pattern, e.g. **/*.py. Arguments: pattern, optional path "
                "to search under."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                },
                "required": ["pattern"],
            },
            handler=glob,
        ),
        ToolSpec(
            name="Grep",
            description=(
                "Search file contents with a regular expression. Arguments: pattern, optional "
                "path (file or directory) and glob to filter file names."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string"},
                    "glob": {"type": "string"},
                },
                "required": ["pattern"],
            },
            handler=grep,
        ),
    ]
