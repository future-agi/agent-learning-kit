"""Names the framework an agent's source is built on, from its files alone.

The answer is a label: a wrong name costs little, a lost document or a stuck run
costs a lot. So nothing here runs or imports customer code, and any failure ends
in `unknown`. Files are read with `tokenize`, because it stays bounded on files a
parser would choke on and still reads what follows a syntax error.
"""

from __future__ import annotations

import glob
import io
import json
import logging
import os
import re
import stat
import tokenize
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

from .instrumentation_evidence import _SKIPPED_DIRECTORIES
from .source_folder import agent_folder

logger = logging.getLogger(__name__)

_MAX_LINE_BYTES = 65_536
_MAX_TOTAL_BYTES = 67_108_864
_MAX_FILES = 5_000
_METADATA_BYTES = 65_536
_VERSION_MAX = 64
_MAX_WORK = 100_000_000_000
_WORK_PER_TARGET = 2_000
_MAX_ALIASES = 10_000
# A backslash-continued dotted name has no length limit of its own, and no real
# name comes near this.
_MAX_DOTTED_PARTS = 64

_FILE_SKIPPED = "file_skipped"
_FILE_UNREADABLE = "file_unreadable"
_AGENTS_AMBIGUOUS = "agents_ambiguous"
_SIDE_FOLDER_FRAMEWORK = "side_folder_framework"
_OUTSIDE_IMPORT = "outside_import"
_OTHER_SOURCE = "other_source"
_NO_PYTHON = "no_python"

# These three are common names for a customer's own package below the top.
_TOP_ONLY_SKIPS = frozenset({"build", "dist", "env"})
_ALWAYS_SKIPPED = frozenset(_SKIPPED_DIRECTORIES) - _TOP_ONLY_SKIPS
_SIDE_FOLDERS = frozenset(
    {
        "tests",
        "test",
        "examples",
        "example",
        "samples",
        "sample",
        "demos",
        "demo",
        "docs",
        "doc",
        "evals",
        "eval",
        "scripts",
        "legacy",
        "notebooks",
        "vendor",
        "vendored",
        "third_party",
        "external",
        "contrib",
        "experiments",
        "benchmarks",
        "archive",
    }
)
_OTHER_SOURCE_SUFFIXES = (".ts", ".tsx", ".ipynb")
_OTHER_SOURCE_NAME = "root_agent.yaml"

_VERSION_PATTERN = re.compile(
    r"\d+(\.\d+)*((a|b|rc)\d+)?(\.post\d+)?(\.dev\d+)?(\+[A-Za-z0-9.]+)?",
    re.ASCII | re.IGNORECASE,
)

# 3.11 has neither constant: an f-string is one STRING token there.
_FSTRING_START = getattr(tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(tokenize, "FSTRING_END", None)
_HIDDEN_TOKENS = frozenset({tokenize.COMMENT, tokenize.NL, tokenize.ENCODING})
_OPENING = frozenset("([{")
_CLOSING = frozenset(")]}")


@dataclass(frozen=True)
class Finding:
    framework: str
    strength: Literal["builds", "model", "import"]
    file: str
    line: int


@dataclass(frozen=True)
class FolderFindings:
    findings: tuple[Finding, ...]
    blockers: frozenset[str]
    over_limit: bool


@dataclass(frozen=True)
class FrameworkFolders:
    folder: Path
    repo_root: Path | None = None
    package_dir: Path | None = None


@dataclass(frozen=True)
class _Row:
    result: str
    imports: tuple[str, ...]
    builders: frozenset[str]
    packages: tuple[str, ...]
    label: str | None = None

    @property
    def framework(self) -> str:
        return self.label or self.result


def _named(
    result: str,
    imports: tuple[str, ...],
    builders: tuple[str, ...],
    packages: tuple[str, ...],
) -> _Row:
    return _Row(result, imports, frozenset(builders), packages)


def _other(label: str, imports: tuple[str, ...], packages: tuple[str, ...]) -> _Row:
    return _Row("other", imports, frozenset(), packages, label)


_ROWS = (
    _named(
        "deepagents",
        ("deepagents",),
        ("create_deep_agent", "middleware"),
        ("deepagents",),
    ),
    _named(
        "langgraph",
        ("langgraph", "langgraph_*"),
        (
            "StateGraph",
            "MessageGraph",
            "create_react_agent",
            "entrypoint",
            "create_supervisor",
            "create_swarm",
        ),
        ("langgraph",),
    ),
    _named(
        "langchain",
        ("langchain", "langchain_*"),
        (
            "create_agent",
            "create_react_agent",
            "create_tool_calling_agent",
            "create_openai_tools_agent",
            "create_openai_functions_agent",
            "create_structured_chat_agent",
            "AgentExecutor",
            "initialize_agent",
        ),
        ("langchain", "langchain-core"),
    ),
    _named(
        "livekit",
        ("livekit.agents",),
        ("AgentSession", "Agent", "WorkerOptions", "cli"),
        ("livekit-agents",),
    ),
    _named("pipecat", ("pipecat",), ("Pipeline", "PipelineTask"), ("pipecat-ai",)),
    _named(
        "claude_agent_sdk",
        ("claude_agent_sdk", "claude_code_sdk"),
        ("query", "ClaudeSDKClient"),
        ("claude-agent-sdk", "claude-code-sdk"),
    ),
    _named(
        "openai_agents",
        ("agents",),
        ("Agent", "Runner", "RealtimeAgent", "RealtimeRunner"),
        ("openai-agents",),
    ),
    _named(
        "pydantic_ai", ("pydantic_ai",), ("Agent",), ("pydantic-ai", "pydantic-ai-slim")
    ),
    _named(
        "google_adk",
        ("google.adk",),
        (
            "Agent",
            "LlmAgent",
            "SequentialAgent",
            "ParallelAgent",
            "LoopAgent",
            "BaseAgent",
            "Runner",
            "InMemoryRunner",
        ),
        ("google-adk",),
    ),
    _named("crewai", ("crewai",), ("Crew", "Agent", "CrewBase", "Flow"), ("crewai",)),
    _other("llama_index", ("llama_index",), ("llama-index-core", "llama-index")),
    _other(
        "autogen",
        ("autogen", "autogen_agentchat", "autogen_core", "autogen_ext"),
        ("autogen-agentchat", "pyautogen", "autogen"),
    ),
    _other("semantic_kernel", ("semantic_kernel",), ("semantic-kernel",)),
    _other("haystack", ("haystack",), ("haystack-ai",)),
    _other("agno", ("agno",), ("agno",)),
    _other("beeai_framework", ("beeai_framework",), ("beeai-framework",)),
    _other("dspy", ("dspy",), ("dspy", "dspy-ai")),
    _other("smolagents", ("smolagents",), ("smolagents",)),
    _other("strands", ("strands",), ("strands-agents",)),
)
_NAMED_FRAMEWORKS = frozenset(row.result for row in _ROWS if row.label is None)
_ROW_BY_FRAMEWORK = {row.framework: row for row in _ROWS}


def _index_rows() -> tuple[
    dict[str, list[tuple[_Row, tuple[str, ...]]]], tuple[tuple[str, _Row], ...]
]:
    by_top: dict[str, list[tuple[_Row, tuple[str, ...]]]] = {}
    wildcards: list[tuple[str, _Row]] = []
    for row in _ROWS:
        for prefix in row.imports:
            if prefix.endswith("*"):
                wildcards.append((prefix[:-1], row))
            else:
                parts = tuple(prefix.split("."))
                by_top.setdefault(parts[0], []).append((row, parts))
    return by_top, tuple(wildcards)


_PREFIXES_BY_TOP, _WILDCARD_TOPS = _index_rows()


def _row_match(parts: list[str]) -> tuple[_Row, int] | None:
    """The row a dotted name falls under, and how many of its parts that prefix took."""
    top = parts[0]
    for row, prefix in _PREFIXES_BY_TOP.get(top, ()):
        if tuple(parts[: len(prefix)]) == prefix:
            return row, len(prefix)
    for start, row in _WILDCARD_TOPS:
        if top.startswith(start):
            return row, 1
    return None


def _is_row_top(top: str) -> bool:
    return top in _PREFIXES_BY_TOP or any(
        top.startswith(start) for start, _ in _WILDCARD_TOPS
    )


def _is_chat_model(name: str) -> bool:
    if name == "init_chat_model":
        return True
    return name.startswith(("Chat", "AzureChat")) and not name.startswith(
        ("ChatMessage", "ChatPrompt", "ChatGeneration", "ChatResult")
    )


class _OverBudget(Exception):
    pass


class _Work:
    """What one walk has spent. A count and not a clock, so the answer is the same
    every time. Python 3.12.3 re-decodes a token's whole line for each token, and one
    name can have thousands of targets: neither shows in the size of a file."""

    def __init__(self, limit: int | None = None) -> None:
        self._spent = 0
        # None follows _MAX_WORK; the agent scan gives each file its own, smaller limit.
        self._limit = limit

    def charge(self, amount: int) -> None:
        self._spent += amount
        if self._spent > (_MAX_WORK if self._limit is None else self._limit):
            raise _OverBudget()


def _visible_tokens(data: bytes, work: _Work) -> Iterator[tokenize.TokenInfo]:
    fstring_depth = 0
    for token in tokenize.tokenize(io.BytesIO(data).readline):
        work.charge(len(token.line))
        if token.type == _FSTRING_START:
            fstring_depth += 1
        elif token.type == _FSTRING_END:
            fstring_depth -= 1
        elif not fstring_depth and token.type not in _HIDDEN_TOKENS:
            yield token


def _is_type_checking_header(words: list[str]) -> bool:
    return words == ["if", "TYPE_CHECKING", ":"] or (
        len(words) == 5
        and words[0] == "if"
        and words[2:] == [".", "TYPE_CHECKING", ":"]
    )


def _outside_type_checking(
    tokens: Iterator[tokenize.TokenInfo],
) -> Iterator[tokenize.TokenInfo]:
    """Drops an indented block under `if TYPE_CHECKING:`, because it never runs.

    Its `else` runs, and arrives here as an ordinary statement after the block's
    DEDENT. A one-line `if TYPE_CHECKING: import x` is left in: that costs an
    `unknown`, never a wrong name.
    """
    skipping = 0
    line: list[str] = []
    header: list[str] = []
    for token in tokens:
        if skipping:
            if token.type == tokenize.INDENT:
                skipping += 1
            elif token.type == tokenize.DEDENT:
                skipping -= 1
            continue
        if token.type == tokenize.INDENT and _is_type_checking_header(header):
            skipping = 1
            continue
        if token.type == tokenize.NEWLINE:
            header, line = line, []
        elif token.type not in (tokenize.INDENT, tokenize.DEDENT) and len(line) < 6:
            line.append(token.string)
        yield token


def _ends_statement(token: tokenize.TokenInfo | None) -> bool:
    return (
        token is None
        or token.type in (tokenize.NEWLINE, tokenize.ENDMARKER)
        or token.string == ";"
    )


def _skip_statement(
    token: tokenize.TokenInfo | None, tokens: Iterator[tokenize.TokenInfo]
) -> None:
    while not _ends_statement(token):
        token = next(tokens, None)


def _dotted(
    token: tokenize.TokenInfo | None, tokens: Iterator[tokenize.TokenInfo]
) -> tuple[list[str], tokenize.TokenInfo | None]:
    """Reads `n(.n)*` from `token` on; returns its parts and the first token after it."""
    parts: list[str] = []
    while token is not None and token.type == tokenize.NAME:
        if len(parts) < _MAX_DOTTED_PARTS:
            parts.append(token.string)
        token = next(tokens, None)
        if token is None or token.string != ".":
            break
        token = next(tokens, None)
    return parts, token


def _after_subscript(
    tokens: Iterator[tokenize.TokenInfo],
) -> tokenize.TokenInfo | None:
    """Called just past a `[`; returns the first token after its closing `]`."""
    depth = 1
    token = next(tokens, None)
    while token is not None and depth:
        if token.string == "[":
            depth += 1
        elif token.string == "]":
            depth -= 1
        token = next(tokens, None)
    return token


class _FileScan:
    """One file's two reads. Uses are resolved as they are met and never kept, so
    what this holds grows with the file's imports, not with its length."""

    def __init__(
        self, shadowed: frozenset[str], outside_names: frozenset[str], work: _Work
    ) -> None:
        self.work = work
        self._shadowed = shadowed
        self._outside_names = outside_names
        # Targets stay as parts: a name can be megabytes long, and a joined copy per
        # target would hold far more than the source does.
        self.aliases: dict[str, set[tuple[str, ...]]] = {}
        self._alias_count = 0
        self.found: dict[tuple[str, str], int] = {}
        self.has_row_import = False
        self.imports_outside = False

    def read_imports(self, tokens: Iterator[tokenize.TokenInfo]) -> None:
        # Both are keywords: `import` only ever starts an import, and `_from_import`
        # skips a `yield from` or `raise ... from`, since no `import` follows them.
        for token in tokens:
            if token.type == tokenize.NAME and token.string == "import":
                self._plain_import(token.start[0], tokens)
            elif token.type == tokenize.NAME and token.string == "from":
                self._from_import(token.start[0], tokens)

    def _plain_import(self, line: int, tokens: Iterator[tokenize.TokenInfo]) -> None:
        while True:
            parts, token = _dotted(next(tokens, None), tokens)
            if parts:
                if token is not None and token.string == "as":
                    named = next(tokens, None)
                    token = next(tokens, None)
                    if named is not None and named.type == tokenize.NAME:
                        self._note(parts, named.string, tuple(parts), line)
                else:
                    self._note(parts, parts[0], (parts[0],), line)
            if token is None or token.string != ",":
                _skip_statement(token, tokens)
                return

    def _from_import(self, line: int, tokens: Iterator[tokenize.TokenInfo]) -> None:
        # A relative import starts with a `.` or `...` token, which `_dotted` stops at
        # with no parts.
        module, token = _dotted(next(tokens, None), tokens)
        if token is None or token.string != "import":
            _skip_statement(token, tokens)
            return
        token = next(tokens, None)
        while token is not None and not _ends_statement(token):
            if token.string == "*":
                self._note(module, None, None, line)
            elif token.type == tokenize.NAME:
                imported = [*module, token.string]
                alias = token.string
                token = next(tokens, None)
                if token is not None and token.string == "as":
                    named = next(tokens, None)
                    token = next(tokens, None)
                    if named is None or named.type != tokenize.NAME:
                        continue
                    alias = named.string
                self._note(imported, alias, tuple(imported), line)
                continue
            token = next(tokens, None)

    def _note(
        self,
        imported: list[str],
        alias: str | None,
        target: tuple[str, ...] | None,
        line: int,
    ) -> None:
        top = imported[0]
        if top in self._outside_names:
            self.imports_outside = True
        if top in self._shadowed:
            return
        match = _row_match(imported)
        if match is not None:
            self.has_row_import = True
            self.found.setdefault((match[0].framework, "import"), line)
        if alias is None or target is None or not _is_row_top(top):
            return
        targets = self.aliases.setdefault(alias, set())
        if target not in targets:
            targets.add(target)
            self._alias_count += 1
            if self._alias_count > _MAX_ALIASES:
                raise ValueError("too many aliases")

    def read_uses(self, tokens: Iterator[tokenize.TokenInfo]) -> None:
        depth = 0
        class_depth: int | None = None
        class_header = False
        at_start = True
        decorates = False
        prev = ""
        token = next(tokens, None)
        while token is not None:
            text = token.string
            if (
                token.type == tokenize.NAME
                and text in self.aliases
                and prev not in (".", "def", "class")
            ):
                line = token.start[0]
                decorator = prev == "@" and decorates
                base_position = class_depth == depth and prev in ("(", ",")
                parts, token = _dotted(token, tokens)
                if token is not None and token.string == "[":
                    token = _after_subscript(tokens)
                following = token.string if token is not None else ""
                if decorator:
                    self._resolve(parts, "decorator", line)
                elif following == "(":
                    self._resolve(parts, "call", line)
                elif base_position and following in (",", ")"):
                    self._resolve(parts, "base", line)
                else:
                    self._resolve(parts, "mention", line)
                prev = parts[-1]
                at_start = False
                continue
            if token.type == tokenize.NEWLINE:
                depth = 0
                class_depth = None
                class_header = False
                at_start = True
                prev = ""
            elif token.type not in (tokenize.INDENT, tokenize.DEDENT):
                if text in _OPENING:
                    depth += 1
                    # Depth 1, because `class F[T: (A, B)](Base):` has brackets ahead.
                    if text == "(" and class_header and depth == 1:
                        class_depth = depth
                        class_header = False
                elif text in _CLOSING:
                    depth = max(depth - 1, 0)
                    if class_depth is not None and depth < class_depth:
                        class_depth = None
                elif text == "class" and token.type == tokenize.NAME:
                    class_header = True
                elif text == ":" and depth == 0:
                    class_header = False
                if text == "@":
                    decorates = at_start
                at_start = text == ";"
                prev = text
            token = next(tokens, None)

    def _resolve(self, parts: list[str], kind: str, line: int) -> None:
        targets = self.aliases[parts[0]]
        self.work.charge(_WORK_PER_TARGET * len(targets))
        for target in targets:
            full = [*target, *parts[1:]]
            match = _row_match(full)
            if match is None:
                continue
            row, taken = match
            # `import livekit` isn't under the row, so `livekit.agents.x` is the first
            # sign of it: without this the folder would say `none`.
            self.found.setdefault((row.framework, "import"), line)
            if kind == "mention":
                continue
            if row.label is not None:
                # `other` means "called something from it": a base class or a
                # decorator alone says too little about what the agent runs on.
                if kind == "call":
                    self.found.setdefault((row.framework, "builds"), line)
            elif any(part in row.builders for part in full[taken:]):
                self.found.setdefault((row.framework, "builds"), line)
            if (
                row.result == "langchain"
                and full[0] != "langchain_core"
                and _is_chat_model(full[-1])
            ):
                self.found.setdefault((row.framework, "model"), line)


def _scan_file(data: bytes, scan: _FileScan, *, read_uses: bool) -> bool:
    """Runs the reads; True when one failed. What was found before a failure stands."""
    failed = False
    with warnings.catch_warnings():
        # On 3.12 `tokenize` warns once per line on a `\{`, which floods stderr.
        warnings.simplefilter("ignore")
        try:
            scan.read_imports(_outside_type_checking(_visible_tokens(data, scan.work)))
        except _OverBudget:
            raise
        except Exception:
            failed = True
        if read_uses and scan.aliases:
            try:
                scan.read_uses(_outside_type_checking(_visible_tokens(data, scan.work)))
            except _OverBudget:
                raise
            except Exception:
                failed = True
    return failed


def _read_file(path: str, limit: int) -> bytes:
    # O_NONBLOCK so that opening a named pipe can't hang the build.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("not a regular file")
        chunks: list[bytes] = []
        remaining = limit
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 1_048_576))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _has_long_line(data: bytes) -> bool:
    # A very long line is what makes `tokenize` slow. `find`, because `split`
    # costs hundreds of MiB on a file at the limit.
    start = 0
    while True:
        newline = data.find(b"\n", start)
        end = len(data) if newline < 0 else newline
        if end - start > _MAX_LINE_BYTES:
            return True
        if newline < 0:
            return False
        start = newline + 1


def _module_names(directory: Path) -> set[str]:
    names: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_dir():
                names.add(entry.name)
            elif entry.name.endswith(".py"):
                names.add(entry.name[: -len(".py")])
    return names


def _outside_names(
    folder: Path, repo_root: Path | None, blockers: set[str]
) -> frozenset[str]:
    """Names that, imported from the folder, are shared code sitting outside it."""
    if repo_root is None:
        return frozenset()
    try:
        here = folder.resolve()
        top = repo_root.resolve()
        try:
            on_path = set(here.relative_to(top).parts)
        except ValueError:
            # The usual packaged build: the folder is a copy outside the repo.
            on_path = set()
        return frozenset(_module_names(top) - _module_names(here) - on_path)
    except (OSError, RuntimeError):
        blockers.add(_FILE_SKIPPED)
        return frozenset()


def _own_modules(folder: Path, blockers: set[str]) -> frozenset[str]:
    """Top names that are the customer's, because they sit ahead of the installed one."""
    own: set[str] = set()
    installed_here: set[str] = set()
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                name = entry.name
                if name.endswith(".py"):
                    own.add(name[: -len(".py")])
                elif name.endswith(".dist-info") and "-" in name and entry.is_dir():
                    # `pip install -t .`: the package beside it is the real one.
                    blockers.add(_FILE_SKIPPED)
                    installed_here.add(
                        name[: -len(".dist-info")].rpartition("-")[0].replace("-", "_")
                    )
                elif entry.is_dir() and os.path.isfile(
                    os.path.join(entry.path, "__init__.py")
                ):
                    own.add(name)
    except OSError:
        blockers.add(_FILE_SKIPPED)
    # `agents` is too common a name for its presence to decide; see `_agents_rule`.
    return frozenset(own - installed_here - {"agents"})


def _is_side_part(part: str) -> bool:
    return part in _SIDE_FOLDERS or part.startswith(".")


def _is_side_file(name: str) -> bool:
    return (
        name == "conftest.py"
        or (name.startswith("test_") and name.endswith(".py"))
        or name.endswith("_test.py")
        or name.startswith(".")
    )


def _directories_to_enter(
    directory: str, names: list[str], *, at_top: bool, blockers: set[str]
) -> list[str]:
    kept = []
    for name in names:
        if name in _ALWAYS_SKIPPED or (at_top and name in _TOP_ONLY_SKIPS):
            continue
        if os.path.islink(os.path.join(directory, name)):
            blockers.add(_FILE_SKIPPED)
            continue
        kept.append(name)
    return kept


class _FolderWalk:
    def __init__(
        self,
        shadowed: frozenset[str],
        outside_names: frozenset[str],
        blockers: set[str],
    ) -> None:
        self._shadowed = shadowed
        self._outside_names = outside_names
        self.blockers = blockers
        self.found: dict[tuple[str, str, str], int] = {}
        self.python_seen = 0
        self.bytes_read = 0
        self.has_counted_python = False
        self.agents_in_counted_path = False
        self.over_limit = False
        self._work = _Work()

    def take(self, directory: str, relative: tuple[str, ...], name: str) -> None:
        counted = not (
            any(_is_side_part(part) for part in relative) or _is_side_file(name)
        )
        if not name.endswith(".py"):
            if counted and (
                name.endswith(_OTHER_SOURCE_SUFFIXES) or name == _OTHER_SOURCE_NAME
            ):
                self.blockers.add(_OTHER_SOURCE)
            return
        self.python_seen += 1
        if self.python_seen > _MAX_FILES:
            self.over_limit = True
            return
        if counted:
            self.has_counted_python = True
            if "agents" in relative or name == "agents.py":
                self.agents_in_counted_path = True
        try:
            data = _read_file(
                os.path.join(directory, name), _MAX_TOTAL_BYTES - self.bytes_read + 1
            )
        except Exception:
            if counted:
                self.blockers.add(_FILE_SKIPPED)
            return
        self.bytes_read += len(data)
        if self.bytes_read > _MAX_TOTAL_BYTES:
            self.over_limit = True
            return
        if _has_long_line(data):
            if counted:
                self.blockers.add(_FILE_SKIPPED)
            return
        scan = _FileScan(self._shadowed, self._outside_names, self._work)
        try:
            failed = _scan_file(data, scan, read_uses=counted)
        except _OverBudget:
            self.over_limit = True
            return
        if not counted:
            if scan.has_row_import:
                self.blockers.add(_SIDE_FOLDER_FRAMEWORK)
            return
        if failed:
            self.blockers.add(_FILE_UNREADABLE)
        if scan.imports_outside:
            self.blockers.add(_OUTSIDE_IMPORT)
        file = "/".join((*relative, name))
        for (framework, strength), line in scan.found.items():
            self.found[(file, framework, strength)] = line


def _site_packages(package_dir: Path | None) -> list[str]:
    if package_dir is None:
        return []
    pattern = os.path.join(
        glob.escape(os.fspath(package_dir)), ".venv", "lib", "python*", "site-packages"
    )
    return sorted(path for path in glob.glob(pattern) if os.path.isdir(path))


def _normalised(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _installed(sites: list[str], package: str) -> list[str]:
    """The `*.dist-info` directories of `package`, whatever their version."""
    wanted = _normalised(package)
    matches = []
    for site in sites:
        try:
            names = sorted(os.listdir(site))
        except OSError:
            continue
        for name in names:
            if (
                name.endswith(".dist-info")
                and _normalised(name[: -len(".dist-info")].rpartition("-")[0]) == wanted
            ):
                matches.append(os.path.join(site, name))
    return matches


def _agents_rule(
    walk: _FolderWalk, repo_root: Path | None, package_dir: Path | None
) -> None:
    """`agents` is a common name for a customer's own package, so an import of it
    only counts with evidence that the real one is meant."""
    if not any(framework == "openai_agents" for _, framework, _ in walk.found):
        return
    sites = _site_packages(package_dir)
    if sites:
        keep = bool(_installed(sites, "openai-agents"))
    else:
        keep = not walk.agents_in_counted_path and not (
            repo_root is not None
            and (
                os.path.lexists(repo_root / "agents")
                or os.path.lexists(repo_root / "agents.py")
            )
        )
        if not keep:
            walk.blockers.add(_AGENTS_AMBIGUOUS)
    if not keep:
        walk.found = {
            key: line for key, line in walk.found.items() if key[1] != "openai_agents"
        }


def folder_findings(
    folder: Path, *, repo_root: Path | None = None, package_dir: Path | None = None
) -> FolderFindings:
    folder = Path(folder)
    blockers: set[str] = set()
    walk = _FolderWalk(
        _own_modules(folder, blockers),
        _outside_names(folder, repo_root, blockers),
        blockers,
    )

    def on_walk_error(_: OSError) -> None:
        blockers.add(_FILE_SKIPPED)

    top = os.fspath(folder)
    try:
        for directory, directories, files in os.walk(
            top, followlinks=False, onerror=on_walk_error
        ):
            # Sorted, so that a walk stopped by a limit stops at the same file.
            directories.sort()
            files.sort()
            relative = tuple(directory[len(top) :].split(os.sep)[1:])
            if relative and any(name.endswith(".dist-info") for name in directories):
                # An installed tree, not the customer's code.
                directories[:] = []
                continue
            directories[:] = _directories_to_enter(
                directory, directories, at_top=not relative, blockers=blockers
            )
            for name in files:
                walk.take(directory, relative, name)
                if walk.over_limit:
                    break
            if walk.over_limit:
                break
    except (OSError, RecursionError):
        # A tree deep enough to fail outside `onerror`.
        blockers.add(_FILE_SKIPPED)
    if not walk.has_counted_python:
        blockers.add(_NO_PYTHON)
    _agents_rule(walk, repo_root, package_dir)
    findings = sorted(
        (
            Finding(framework, strength, file, line)  # type: ignore[arg-type]
            for (file, framework, strength), line in walk.found.items()
        ),
        key=lambda f: (f.file, f.line, f.framework, f.strength),
    )
    return FolderFindings(tuple(findings), frozenset(blockers), walk.over_limit)


def _decide(found: FolderFindings) -> tuple[str, str | None]:
    """The result word, and the framework whose row it was read from."""
    if found.over_limit:
        return "unknown", None
    builds = {f.framework for f in found.findings if f.strength == "builds"}
    named = builds & _NAMED_FRAMEWORKS
    # A voice agent often drives a LangChain graph inside: the voice side is the agent.
    if len(named & {"livekit", "pipecat"}) == 1:
        named -= {"langchain", "langgraph", "deepagents"}
    # Each is built on the next, so the higher one is what was chosen.
    if "deepagents" in named:
        named -= {"langgraph", "langchain"}
    if "langgraph" in named:
        named -= {"langchain"}
    if len(named) == 1:
        name = next(iter(named))
        return name, name
    if named:
        return "unknown", None
    labels = builds - _NAMED_FRAMEWORKS
    if len(labels) == 1:
        return "other", next(iter(labels))
    if labels:
        return "unknown", None
    if any(f.strength == "model" for f in found.findings):
        return "langchain", "langchain"
    if any(f.strength == "import" for f in found.findings) or found.blockers:
        return "unknown", None
    return "none", None


def _version_from_metadata(dist_info: str) -> str | None:
    data = _read_file(os.path.join(dist_info, "METADATA"), _METADATA_BYTES)
    for line in data.split(b"\n"):
        if line.startswith(b"Version:"):
            value = line[len(b"Version:") :].strip().decode("ascii")
            if len(value) <= _VERSION_MAX and _VERSION_PATTERN.fullmatch(value):
                return value
            return None
    return None


def _read_version(row: _Row, package_dir: Path | None) -> str | None:
    # A bad version is a missing detail, never a reason to doubt the name.
    try:
        sites = _site_packages(package_dir)
        for package in row.packages:
            matches = _installed(sites, package)
            if matches:
                return _version_from_metadata(matches[0]) if len(matches) == 1 else None
    except Exception:
        return None
    return None


def detect_framework(
    folder: Path | None,
    *,
    repo_root: Path | None = None,
    package_dir: Path | None = None,
    raise_faults: bool = False,
) -> dict[str, Any]:
    """The folder's framework word. A fault in the check is ``unknown`` for a build; the agent
    scan passes ``raise_faults=True`` to see it, so it can log the trace and mark the row
    unfinished instead of reporting a finished ``unknown``."""
    try:
        if folder is None or not Path(folder).is_dir():
            logger.info("framework detection: name=%s blockers=%s", "unknown", [])
            return {"name": "unknown", "version": None}
        found = folder_findings(folder, repo_root=repo_root, package_dir=package_dir)
        name, framework = _decide(found)
        result: dict[str, Any] = {"name": name, "version": None}
        if framework is not None:
            row = _ROW_BY_FRAMEWORK[framework]
            result["version"] = _read_version(row, package_dir)
            if name == "other":
                result["label"] = framework
        logger.info(
            "framework detection: name=%s blockers=%s", name, sorted(found.blockers)
        )
        return result
    except Exception as exc:
        if raise_faults:
            raise
        logger.warning("framework detection failed: class=%s", type(exc).__name__)
        return {"name": "unknown", "version": None}


def _contract_workdir(source: Path, bundle_dir: Path) -> Path | None:
    """`runtime.workdir` from the sealed contract, when it names a real inner folder."""
    try:
        contract = json.loads(
            (bundle_dir / "contract.json").read_text(encoding="utf-8")
        )
        runtime = contract.get("runtime") if isinstance(contract, dict) else None
        workdir = runtime.get("workdir") if isinstance(runtime, dict) else None
        if not isinstance(workdir, str) or not workdir or workdir == ".":
            return None
        candidate = source / workdir
        resolved = candidate.resolve()
        if resolved.is_relative_to(source.resolve()) and resolved.is_dir():
            return candidate
    except (OSError, ValueError, RuntimeError):
        return None
    return None


def named_framework_folder(
    source: Path, bundle_dir: Path, subfolder: str | None
) -> Path | None:
    """The folder the job names, else the sealed contract's own; ``None`` when neither does."""
    if subfolder:
        return agent_folder(source, subfolder)
    return _contract_workdir(source, bundle_dir)


def framework_folders_for(
    job: Any, manifest: Any, *, source: Path, bundle_dir: Path, work_directory: Path
) -> FrameworkFolders:
    runtime = manifest.runtime
    control_service = runtime.control_service if runtime.kind == "process" else None
    folder = named_framework_folder(source, bundle_dir, job.source.subfolder)
    if folder is None:
        folder = source
        if control_service:
            for process in manifest.processes:
                if process.name == control_service:
                    folder = bundle_dir / process.working_directory
                    break
    package_dir = (
        work_directory / "build" / control_service if control_service else None
    )
    return FrameworkFolders(folder, source, package_dir)
