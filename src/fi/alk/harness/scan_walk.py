"""Finds the files in a repository that look like agents, from its files alone.

One walk reads each file once and runs nothing. Python files go through the framework
check's own per-file reader, so "builds an agent" means what it means to a build, and a
token pass of the scan's own adds what that reader doesn't keep: the repository files a
file imports, provider clients, setting names and tool registrations.

A candidate imported by another, directly or through files that aren't candidates, is
part of the importer's agent when the importer's sign is as strong or stronger, unless
the importer sits in a side folder and the imported file doesn't. Candidates that import
each other are one agent.
"""

from __future__ import annotations

import ast
import codecs
import json
import logging
import math
import os
import posixpath
import re
import time
import tokenize
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal

from .framework_detection import (
    _ALWAYS_SKIPPED,
    _TOP_ONLY_SKIPS,
    _FileScan,
    _has_long_line,
    _is_side_file,
    _is_side_part,
    _OverBudget,
    _own_modules,
    _read_file,
    _scan_file,
    _visible_tokens,
    _Work,
)
from .bundle_author_v2 import _IGNORED_ARTIFACT_PARTS as _BUILD_IGNORED
from .reading_entry import MaskSecrets
from .source_folder import _FOLDER_FORM, _FOLDER_MAX

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

Sign = Literal["framework", "graph", "agent_py", "client", "model"]
Hazard = Literal["", "graphs", "agent_py"]

_FILE_BYTES = 1_048_576
# A hundredth of a folder check's allowance: the crafted files that use a whole one
# take seconds, so one file now costs about a second at most.
_FILE_WORK = 1_000_000_000
_MAX_FILES = 20_000
_MAX_BYTES = 268_435_456
_NAMES_PER_FILE = 200
_IMPORT_DEPTH = 10
_MAX_AGENTS = 50
_DESCRIBE_FILES = 2_000
_DESCRIBE_BYTES = 33_554_432
_ENV_NAME_MAX = 128
_MAX_PARTS = 64
# The prompt count is an estimate, and every file walked is listed, read or not: past this
# many a tree of prompt files costs memory and adds nothing a person would act on.
_MAX_PROMPT_PATHS = 20_000
# How many folder entries one look for a build's hazard visits. The hazard is advice, so a
# tree too big to look through gives none rather than the walk's time.
_HAZARD_ENTRIES = 200_000
# No file system the scan runs on has a path this long, so no file can match.
_MODULE_PATH_MAX = 4_096

_STRENGTH = {"framework": 3, "graph": 2, "agent_py": 1, "client": 0, "model": 0}
# A file that only talks to a model: listed unless a stronger agent shares its project.
_WEAKEST = frozenset({"client", "model"})
_PROJECT_FILES = ("pyproject.toml", "requirements.txt", "setup.py")
_PACKAGE_FILE = "package.json"
_TEMPLATES = (".env.example", ".env.sample", ".env.template")
_LANGGRAPH = "langgraph.json"
_SCRIPT_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
_SOURCE_SUFFIXES = (".py", ".ipynb", *_SCRIPT_SUFFIXES)
# Script files nobody writes an agent in: build output, vendored copies, type stubs and tool
# settings. A Python helper beside only these is no reason to pay for the model.
_BUILT_FOLDERS = frozenset({"dist", "build", "static", "vendor", "node_modules"})
_BUILT_ENDINGS = (".min.js", ".d.ts", ".d.mts", ".d.cts")
_CONFIG_ENDINGS = tuple(".config" + suffix for suffix in _SCRIPT_SUFFIXES)

_CLIENTS = {
    ("OpenAI",): "openai",
    ("AsyncOpenAI",): "openai",
    ("AzureOpenAI",): "openai",
    ("Anthropic",): "anthropic",
    ("AsyncAnthropic",): "anthropic",
    ("genai", "Client"): "google_genai",
    ("Mistral",): "mistral",
    ("cohere", "Client"): "cohere",
    ("cohere", "ClientV2"): "cohere",
    ("Groq",): "groq",
    ("litellm", "completion"): "litellm",
    ("litellm", "acompletion"): "litellm",
}
_PROVIDER_MODULES = (
    ("openai",),
    ("anthropic",),
    ("google", "genai"),
    ("mistralai",),
    ("cohere",),
    ("groq",),
    ("litellm",),
)
_TOOL_DECORATORS = frozenset({"tool", "function_tool"})
_SETTING_CALLS = (["os", "getenv"], ["os", "environ", "get"])
_STRING_PREFIXES = frozenset({"", "r", "u"})

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Only the name is captured: the rest of the line holds a value and is never kept. A second
# `=` straight after the first is base64 padding, the last line of a pasted key, not a setting.
_TEMPLATE_LINE = re.compile(r"[ \t]*(?:export[ \t]+)?([^\s=]+)[ \t]*=(?!=)")
_ENCODED_LENGTH = 16
# An unquoted value this long in base64's alphabets is a pasted key, which may wrap onto the
# lines below it.
_ENCODED_VALUE = re.compile(r"[A-Za-z0-9+/_-]{40,}={0,2}")
_KEY_BEGIN = "-----BEGIN"
_KEY_END = "-----END"
_DOUBLE_QUOTE_CLOSE = re.compile(r'(?<!\\)"')
_BACKTICK_CLOSE = re.compile(r"(?<!\\)`")
_PROCESS_ENV = re.compile(
    r"(?<![\w$.])process\.env(?:\.([A-Za-z_$][\w$]*)"
    r"|\[\s*(?:\"([^\"\\\r\n]*)\"|'([^'\\\r\n]*)')\s*\])"
)


@dataclass(frozen=True)
class Candidate:
    entry: str
    subfolder: str
    sign: Sign
    side: bool
    builds: tuple[str, ...]
    build_may_stop: Hazard
    partial: bool
    env_names: tuple[str, ...]
    tools: int
    prompt_files: int
    evidence: tuple[tuple[str, str, int], ...]


@dataclass(frozen=True)
class WalkResult:
    agents: tuple[Candidate, ...]
    more_than_fifty: bool
    stopped: Literal["", "limit", "clock", "memory"]
    unread_files: int
    source_outside_side: bool
    candidate_outside_side: bool
    # A TypeScript or JavaScript file outside side folders. The walk never makes one a
    # candidate, so an agent written in one is only the model's to find.
    script_outside_side: bool
    # Candidates outside side folders, every one of them only calling a model.
    only_weak_outside_side: bool
    paths_left_out: int
    upload_others: int
    # Of `upload_others`, those inside side folders.
    upload_others_side: int


@dataclass(frozen=True)
class _Facts:
    """What one Python file showed: small tuples, never its tokens."""

    builds: tuple[tuple[str, int], ...]
    # A chat model on its own, which the framework check already counts as its framework.
    models: tuple[tuple[str, int], ...]
    found_any: bool
    openai_agents: int
    provider: bool
    clients: tuple[tuple[str, int], ...]
    env: tuple[str, ...]
    tools: int
    imports: tuple[tuple[int, tuple[str, ...]], ...]


@dataclass(frozen=True)
class _Agent:
    entry: str
    sign: Sign
    side: bool
    subfolder: str


class _Unread(Exception):
    pass


# What reading or parsing one file can raise: that file's problem, never the walk's. Anything
# else is a fault in the kit and reaches the scan's crash path.
_FILE_ERRORS = (
    OSError,
    ValueError,  # UnicodeError and json's decode errors too
    SyntaxError,
    RecursionError,
    tokenize.TokenError,
    _OverBudget,
    _Unread,
)


def fits_path_rule(path: str) -> bool:
    """Whether the platform can carry a non-empty path. The root, ``""``, isn't one."""
    return (
        bool(path)
        and len(path) <= _FOLDER_MAX
        and _FOLDER_FORM.fullmatch(path) is not None
    )


def _good_env_name(name: str) -> bool:
    return len(name) <= _ENV_NAME_MAX and _ENV_NAME.fullmatch(name) is not None


def _join(*parts: str) -> str:
    return "/".join(part for part in parts if part)


def _under(path: str, folder: str) -> bool:
    return not folder or path.startswith(folder + "/")


def _below(path: str, folder: str) -> str:
    return path[len(folder) + 1 :] if folder else path


def _is_side_path(path: str) -> bool:
    parts = path.split("/")
    return any(_is_side_part(part) for part in parts[:-1]) or _is_side_file(parts[-1])


def _is_written_script(path: str) -> bool:
    """A TypeScript or JavaScript file someone wrote, so one an agent may be written in."""
    parts = path.split("/")
    name = parts[-1]
    return (
        name.endswith(_SCRIPT_SUFFIXES)
        and not any(part in _BUILT_FOLDERS for part in parts[:-1])
        and not name.endswith(_BUILT_ENDINGS)
        and not name.endswith(_CONFIG_ENDINGS)
    )


def _has_link(top: str, folder: str) -> bool:
    """Whether reaching ``folder`` passes through a link, or it isn't a real folder."""
    current = top
    for part in folder.split("/") if folder else ():
        current = os.path.join(current, part)
        if os.path.islink(current):
            return True
    return not os.path.isdir(current)


def _folders_to_enter(directory: str, names: list[str], *, at_top: bool) -> list[str]:
    return [
        name
        for name in names
        if name not in _ALWAYS_SKIPPED
        and not (at_top and name in _TOP_ONLY_SKIPS)
        and not os.path.islink(os.path.join(directory, name))
    ]


def _chain(
    token: tokenize.TokenInfo | None, tokens: Iterator[tokenize.TokenInfo]
) -> tuple[list[str], tokenize.TokenInfo | None]:
    """Reads `n(.n)*` from `token` on; returns its parts and the token after it."""
    parts: list[str] = []
    while token is not None and token.type == tokenize.NAME:
        if len(parts) < _MAX_PARTS:
            parts.append(token.string)
        token = next(tokens, None)
        if token is None or token.string != ".":
            break
        token = next(tokens, None)
    return parts, token


def _string_value(text: str) -> str | None:
    """The value of one plain string literal; None for an f-string, bytes, or anything
    too long to be a setting's name."""
    if len(text) > _ENV_NAME_MAX + 8:
        return None
    body = text.lstrip("rRuUbBfFtT")
    if text[: len(text) - len(body)].lower() not in _STRING_PREFIXES:
        return None
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, str) else None


class _OwnReader:
    """The scan's pass over one file's tokens, for what the framework reader drops."""

    def __init__(self) -> None:
        self.imports: dict[tuple[int, tuple[str, ...]], None] = {}
        self.provider = False
        self.clients: dict[str, int] = {}
        self.env: set[str] = set()
        self.tools = 0

    def read(self, tokens: Iterator[tokenize.TokenInfo]) -> None:
        prev = ""
        at_start = True
        token = next(tokens, None)
        while token is not None:
            text = token.string
            if token.type == tokenize.NAME and prev != ".":
                if text == "import":
                    token, prev, at_start = self._plain_import(tokens), "", False
                    continue
                if text == "from":
                    token, prev, at_start = self._from_import(tokens), "", False
                    continue
                if prev not in ("def", "class"):
                    line = token.start[0]
                    parts, token = _chain(token, tokens)
                    token = self._call(parts, token, tokens, line)
                    prev, at_start = parts[-1], False
                    continue
            if text == "@" and at_start:
                parts, token = _chain(next(tokens, None), tokens)
                if parts and parts[-1] in _TOOL_DECORATORS:
                    self.tools = min(self.tools + 1, _NAMES_PER_FILE)
                prev, at_start = (parts[-1] if parts else "@"), False
                continue
            if token.type == tokenize.NEWLINE:
                prev, at_start = "", True
            elif token.type not in (tokenize.INDENT, tokenize.DEDENT):
                prev, at_start = text, text == ";"
            token = next(tokens, None)

    def _call(
        self,
        parts: list[str],
        token: tokenize.TokenInfo | None,
        tokens: Iterator[tokenize.TokenInfo],
        line: int,
    ) -> tokenize.TokenInfo | None:
        following = token.string if token is not None else ""
        if following == "(":
            sdk = _CLIENTS.get(tuple(parts[-2:])) or _CLIENTS.get(tuple(parts[-1:]))
            if sdk is not None and len(self.clients) < _NAMES_PER_FILE:
                self.clients.setdefault(sdk, line)
            if parts in _SETTING_CALLS:
                return self._setting(tokens, (",", ")"))
        elif following == "[" and parts == ["os", "environ"]:
            return self._setting(tokens, ("]",))
        return token

    def _setting(
        self, tokens: Iterator[tokenize.TokenInfo], closers: tuple[str, ...]
    ) -> tokenize.TokenInfo | None:
        # Called just past the bracket. Only a lone literal is a name: what follows a
        # comma is a default, which may be a real value, so it is never looked at.
        literal = next(tokens, None)
        if literal is None or literal.type != tokenize.STRING:
            return literal
        after = next(tokens, None)
        if after is not None and after.string in closers:
            name = _string_value(literal.string)
            if (
                name is not None
                and _good_env_name(name)
                and len(self.env) < _NAMES_PER_FILE
            ):
                self.env.add(name)
        return after

    def _plain_import(
        self, tokens: Iterator[tokenize.TokenInfo]
    ) -> tokenize.TokenInfo | None:
        while True:
            parts, token = _chain(next(tokens, None), tokens)
            if parts:
                self._import(0, parts)
            if token is not None and token.string == "as":
                next(tokens, None)
                token = next(tokens, None)
            if token is None or token.string != ",":
                return token

    def _from_import(
        self, tokens: Iterator[tokenize.TokenInfo]
    ) -> tokenize.TokenInfo | None:
        level = 0
        token = next(tokens, None)
        while token is not None and token.string in (".", "..."):
            level += len(token.string)
            token = next(tokens, None)
        module: list[str] = []
        # `import` is a NAME token too, and `from . import x` has no module before it.
        if token is not None and token.string != "import":
            module, token = _chain(token, tokens)
        # `yield from` and `raise ... from` have no `import` after them.
        if token is None or token.string != "import" or not (module or level):
            return token
        token = next(tokens, None)
        if token is not None and token.string == "(":
            token = next(tokens, None)
        while token is not None:
            if token.string == "*":
                token = next(tokens, None)
            elif token.type == tokenize.NAME:
                # The name may be a module of its own, or something the module defines.
                self._import(level, [*module, token.string])
                token = next(tokens, None)
                if token is not None and token.string == "as":
                    next(tokens, None)
                    token = next(tokens, None)
            else:
                break
            if token is None or token.string != ",":
                break
            token = next(tokens, None)
        self._import(level, module)
        return token

    def _import(self, level: int, parts: list[str]) -> None:
        key = (level, tuple(parts[:_MAX_PARTS]))
        if level == 0 and any(
            key[1][: len(module)] == module for module in _PROVIDER_MODULES
        ):
            self.provider = True
        if (
            len(self.imports) < _NAMES_PER_FILE
            and sum(len(part) + 1 for part in key[1]) <= _MODULE_PATH_MAX
        ):
            self.imports[key] = None


def _python_facts(data: bytes, shadowed: frozenset[str]) -> _Facts:
    """Raises for a file that has to count as unread."""
    if _has_long_line(data):
        raise _Unread()
    work = _Work(_FILE_WORK)
    scan = _FileScan(shadowed, frozenset(), work)
    if _scan_file(data, scan, read_uses=True):
        raise _Unread()
    own = _OwnReader()
    with warnings.catch_warnings():
        # On 3.12 `tokenize` and `literal_eval` warn once per `\{`, which floods stderr.
        warnings.simplefilter("ignore")
        own.read(_visible_tokens(data, work))
    builds = sorted(
        (framework, line)
        for (framework, strength), line in scan.found.items()
        if strength == "builds"
    )
    models = sorted(
        (framework, line)
        for (framework, strength), line in scan.found.items()
        if strength == "model"
    )
    openai_agents = min(
        (
            line
            for (framework, _), line in scan.found.items()
            if framework == "openai_agents"
        ),
        default=0,
    )
    return _Facts(
        builds=tuple(builds),
        models=tuple(models),
        found_any=bool(scan.found),
        openai_agents=openai_agents,
        provider=own.provider,
        clients=tuple(
            sorted(own.clients.items(), key=lambda item: (item[1], item[0]))
        ),
        env=tuple(sorted(own.env)),
        tools=own.tools,
        imports=tuple(own.imports),
    )


def _closes(text: str, quote: str) -> bool:
    if quote == '"':
        # `\\` is a backslash, not an escape, so a quote right after one still closes.
        return _DOUBLE_QUOTE_CLOSE.search(text.replace("\\\\", "")) is not None
    if quote == "`":
        # Node's `.env` reader holds a backtick value open over lines, and closes it as it
        # closes a double-quoted one.
        return _BACKTICK_CLOSE.search(text.replace("\\\\", "")) is not None
    return quote in text


def _looks_encoded(name: str) -> bool:
    # A key wrapped over lines with no quotes and no block markers can leave a line looking
    # like `<text>=`. Setting names are words: short, or one case, or split by `_`. Encoded
    # text has a long run mixing two of lower case, upper case and digits; base64url puts `_`
    # inside it, and base32 has no lower case, so each run between `_` is judged alone.
    for part in name.split("_"):
        kinds = (
            any(char.islower() for char in part)
            + any(char.isupper() for char in part)
            + any(char.isdigit() for char in part)
        )
        if len(part) >= _ENCODED_LENGTH and kinds >= 2:
            return True
    return False


def _continues(value: str) -> bool:
    # An odd run of backslashes at the end: the last one joins the next line to the value,
    # as a shell reads it.
    return (len(value) - len(value.rstrip("\\"))) % 2 == 1


def _template_text(data: bytes) -> str:
    # `utf-8-sig`, so a byte-order mark isn't read as part of the first name. Names are ASCII,
    # so a template saved in another encoding still gives them: UTF-16 says so with its
    # byte-order mark, and Latin-1 reads any bytes at all.
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            return data.decode("utf-16")
        return data.decode("latin-1")


def _template_names(data: bytes) -> set[str]:
    """The setting names in a settings template.

    A line inside a value that spans lines is that value's text and never a name: a quoted
    value left open, a pasted key's block, a line joined by a trailing backslash, or a key
    wrapped over bare lines. A value never closed takes the rest of the file: names lost are
    better than a value kept. So is a name that looks like encoded text.
    """
    names: set[str] = set()
    open_quote = ""
    in_key = False
    continued = False
    # Whether the line above may wrap onto this one: a bare line, which is no setting, or a
    # setting whose unquoted value is a long run of encoded text.
    wraps = False
    # Only `\n` ends a line: no `.env` reader splits on the other breaks `splitlines` knows,
    # such as U+2028 or a form feed.
    for line in _template_text(data).split("\n"):
        line = line.rstrip("\r")
        if continued:
            continued = _continues(line)
            wraps = True
            continue
        if open_quote or in_key:
            if in_key and _KEY_END in line:
                in_key = False
            if open_quote and _closes(line, open_quote):
                open_quote = ""
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            wraps = False
            continue
        # A comment may mention a key's marker; only one before it opens a block.
        begin = line.split(" #", 1)[0].find(_KEY_BEGIN)
        if begin >= 0 and _KEY_END not in line[begin:]:
            in_key = True
        match = _TEMPLATE_LINE.match(line)
        if match is None:
            wraps = True
            continue
        value = line[match.end() :].lstrip(" \t")
        follows_wrap = wraps
        # A pasted key's first line can carry trailing blanks or a note after it.
        wraps = _ENCODED_VALUE.fullmatch(value.split(" #", 1)[0].rstrip(" \t")) is not None
        quoted = value[:1] in ("'", '"', "`")
        if quoted and not _closes(value[1:], value[0]):
            open_quote = value[0]
        continued = not quoted and _continues(value)
        if follows_wrap and not value.strip():
            # `<text>=` straight below a wrapped value is that value's last line, its padding
            # read as a setting with no value.
            continue
        name = match.group(1)
        if _good_env_name(name) and not _looks_encoded(name) and len(names) < _NAMES_PER_FILE:
            names.add(name)
    return names


def _script_names(data: bytes) -> set[str]:
    names: set[str] = set()
    for match in _PROCESS_ENV.finditer(data.decode("utf-8")):
        name = next(group for group in match.groups() if group is not None)
        if _good_env_name(name) and len(names) < _NAMES_PER_FILE:
            names.add(name)
    return names


class _Projects:
    """Which folders hold a project file, looked up once per folder."""

    def __init__(self, top: str, names: tuple[str, ...]) -> None:
        self._top = top
        self._names = names
        self._file: dict[str, str | None] = {}

    def file_in(self, folder: str) -> str | None:
        if folder not in self._file:
            found = None
            for name in self._names:
                path = os.path.join(self._top, folder, name)
                if os.path.isfile(path) and not os.path.islink(path):
                    found = name
                    break
            self._file[folder] = found
        return self._file[folder]

    def nearest(self, folder: str) -> str | None:
        while True:
            if self.file_in(folder) is not None:
                return folder
            if not folder:
                return None
            folder = posixpath.dirname(folder)


def _sign(path: str, facts: _Facts, graph_files: dict[str, str]) -> Sign | None:
    if facts.builds:
        return "framework"
    if path in graph_files:
        return "graph"
    if posixpath.basename(path) == "agent.py" and (facts.found_any or facts.provider):
        return "agent_py"
    if facts.models:
        return "model"
    if facts.clients:
        return "client"
    return None


def _evidence(
    entry: str,
    facts: _Facts | None,
    *,
    graph: str | None,
    agent_py: bool,
    project: tuple[str, str] | None,
    model: bool = False,
) -> tuple[tuple[str, str, int], ...]:
    found: list[tuple[str, str, int]] = []
    if facts is not None:
        found += [(f"builds {name}", entry, line) for name, line in facts.builds]
    if graph is not None:
        found.append(("graph", graph, 0))
    if agent_py:
        found.append(("agent_py", entry, 0))
    if model and facts is not None:
        found += [(f"model {name}", entry, line) for name, line in facts.models]
    if facts is not None:
        found += [(f"client {sdk}", entry, line) for sdk, line in facts.clients]
    if project is not None:
        found.append((f"project {project[1]}", _join(project[0], project[1]), 0))
    if facts is not None and facts.openai_agents:
        found.append(("openai_agents_set_aside", entry, facts.openai_agents))
    return tuple(found)


def _components(nodes: list[str], edges: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components, by Tarjan's method without recursion."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    components: list[list[str]] = []
    for root in nodes:
        if root in index:
            continue
        work = [(root, 0)]
        while work:
            node, position = work.pop()
            if position == 0:
                index[node] = low[node] = len(index)
                stack.append(node)
                on_stack.add(node)
            children = edges.get(node, [])
            descended = False
            for at in range(position, len(children)):
                child = children[at]
                if child not in index:
                    work.append((node, at + 1))
                    work.append((child, 0))
                    descended = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if descended:
                continue
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                components.append(component)
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
    return components


def _source_files(
    top: str, start: str, *, side: bool | None = None
) -> Iterator[tuple[str, str, str]]:
    """Every file below ``start`` the scan may read, as (directory, folder relative to
    ``top``, name), in sorted order so that a walk stopped by a limit stops at the same file.
    Installed trees and the folders a build skips are never entered. ``side`` False gives only
    files outside test, example and other side folders; True gives only the files in them."""
    for directory, directories, files in os.walk(start, followlinks=False):
        directories.sort()
        files.sort()
        relative = directory[len(top) :].split(os.sep)[1:]
        if relative and any(name.endswith(".dist-info") for name in directories):
            # An installed tree, not the customer's code.
            directories[:] = []
            continue
        directories[:] = _folders_to_enter(directory, directories, at_top=not relative)
        if side is False:
            directories[:] = [name for name in directories if not _is_side_part(name)]
        folder = "/".join(relative)
        for name in files:
            if side is None or _is_side_path(_join(folder, name)) is side:
                yield directory, folder, name


class _ShadowedNames:
    """Each project's own module names, read once per project, so an import of the
    customer's own ``agents`` package isn't read as a framework."""

    def __init__(self, top: str, projects: _Projects) -> None:
        self._top = top
        self._projects = projects
        self._by_project: dict[str, frozenset[str]] = {}

    def for_folder(self, folder: str) -> frozenset[str]:
        project = self._projects.nearest(folder) or ""
        if project not in self._by_project:
            self._by_project[project] = _own_modules(
                Path(self._top, project) if project else Path(self._top), set()
            )
        return self._by_project[project]


class _Walk:
    """One walk of the source. Holds every file's facts until the agents are decided."""

    def __init__(
        self,
        source: Path,
        *,
        deadline: float,
        clock: Callable[[], float],
        hazards: dict[str, Hazard] | None = None,
    ) -> None:
        self.source = Path(source)
        self.top = os.fspath(source)
        self.deadline = deadline
        self.clock = clock
        self.hazards: dict[str, Hazard] = {} if hazards is None else hazards
        self.facts: dict[str, _Facts] = {}
        self.graphs: dict[str, str] = {}
        self.templates: dict[str, set[str]] = {}
        self.prompt_paths: list[str] = []
        self.unread = 0
        self.files_read = 0
        self.bytes_read = 0
        self.stopped: Literal["", "limit", "clock", "memory"] = ""
        self.source_outside_side = False
        self.script_outside_side = False
        self.projects = _Projects(self.top, _PROJECT_FILES)
        self.shadowed = _ShadowedNames(self.top, self.projects)
        self._roots: dict[str, list[str]] = {}
        self._edges: dict[str, tuple[str, ...]] = {}

    def run(self) -> None:
        # The customer's own code first, then side folders, so tests and examples can't use
        # up the file cap before the agent is reached.
        for side in (False, True):
            for directory, folder, name in _source_files(self.top, self.top, side=side):
                if self.clock() > self.deadline:
                    self.stopped = "clock"
                    return
                self._take(directory, folder, name)
                if self.stopped:
                    return

    def _take(self, directory: str, folder: str, name: str) -> None:
        path = _join(folder, name)
        full = os.path.join(directory, name)
        if name.endswith(_SOURCE_SUFFIXES) and not _is_side_path(path):
            self.source_outside_side = True
        if _is_written_script(path) and not _is_side_path(path):
            self.script_outside_side = True
        if (
            "prompt" in path.lower()
            and len(self.prompt_paths) < _MAX_PROMPT_PATHS
            and not os.path.islink(full)
        ):
            self.prompt_paths.append(path)
        if not (name.endswith(".py") or name == _LANGGRAPH or name in _TEMPLATES):
            return
        # Every read of one file sits in this one `try`: a file the tokenizer, the JSON
        # reader or the decoder rejects is that file's problem, never the walk's.
        try:
            data = self._read(full)
            if data is None:
                return
            if name.endswith(".py"):
                self.facts[path] = _python_facts(data, self.shadowed.for_folder(folder))
            elif name == _LANGGRAPH:
                self._read_graphs(folder, path, data)
            else:
                self.templates.setdefault(folder, set()).update(_template_names(data))
        except _FILE_ERRORS:
            self.unread += 1
        except MemoryError:
            raise
        except Exception:
            # A reader fault of a kind not foreseen is still this file's problem, never the
            # walk's: the file is unread, and the trace is logged masked.
            logger.warning("agent walk: a file's reader failed", exc_info=True)
            self.unread += 1

    def _read(self, path: str) -> bytes | None:
        if self.files_read >= _MAX_FILES:
            self.stopped = "limit"
            return None
        data = _read_file(path, _FILE_BYTES + 1)
        self.files_read += 1
        self.bytes_read += len(data)
        if self.bytes_read > _MAX_BYTES:
            self.stopped = "limit"
            return None
        if len(data) > _FILE_BYTES:
            raise _Unread()
        return data

    def _read_graphs(self, folder: str, path: str, data: bytes) -> None:
        document = json.loads(data.decode("utf-8"))
        graphs = document.get("graphs") if isinstance(document, dict) else None
        if not isinstance(graphs, dict):
            return
        for declaration in list(graphs.values())[:_NAMES_PER_FILE]:
            if not isinstance(declaration, str) or ":" not in declaration:
                continue
            file_name = declaration.rsplit(":", 1)[0]
            while file_name.startswith("./"):
                file_name = file_name[2:]
            target = posixpath.normpath(_join(folder, file_name))
            if target not in (".", "..") and not target.startswith(("../", "/")):
                self.graphs.setdefault(target, path)

    def _import_roots(self, folder: str) -> list[str]:
        """Where an absolute import from ``folder`` is looked for, nearest first."""
        if folder not in self._roots:
            roots: list[str] = []
            current = folder
            while True:
                if not current or self.projects.file_in(current) is not None:
                    roots += [current, _join(current, "src")]
                if not current:
                    break
                current = posixpath.dirname(current)
            self._roots[folder] = roots
        return self._roots[folder]

    def _module_file(self, base: str, parts: tuple[str, ...]) -> str | None:
        stem = _join(base, *parts)
        for path in (f"{stem}.py" if parts else "", _join(stem, "__init__.py")):
            if path and path in self.facts:
                return path
        return None

    def _imported(self, path: str) -> tuple[str, ...]:
        """The read files ``path`` imports. An import that matches none is ignored."""
        if path not in self._edges:
            folder = posixpath.dirname(path)
            found: dict[str, None] = {}
            for level, parts in self.facts[path].imports:
                target = None
                if level == 0:
                    if parts:
                        for root in self._import_roots(folder):
                            target = self._module_file(root, parts)
                            if target is not None:
                                break
                else:
                    base: str | None = folder
                    for _ in range(level - 1):
                        base = posixpath.dirname(base) if base else None
                        if base is None:
                            break
                    if base is not None:
                        target = self._module_file(base, parts)
                if target is not None and target != path:
                    found[target] = None
            self._edges[path] = tuple(found)
        return self._edges[path]

    def _reached(self, start: str, candidates: dict[str, Sign]) -> list[str]:
        """The candidates ``start`` imports, through files that aren't candidates."""
        reached: list[str] = []
        seen = {start}
        frontier = [start]
        for _ in range(_IMPORT_DEPTH):
            following: list[str] = []
            for path in frontier:
                for target in self._imported(path):
                    if target in seen:
                        continue
                    seen.add(target)
                    if target in candidates:
                        reached.append(target)
                    else:
                        following.append(target)
            frontier = following
            if not frontier:
                break
        return sorted(reached)

    def _absorbs(self, importer: str, imported: str, signs: dict[str, Sign]) -> bool:
        return _STRENGTH[signs[importer]] >= _STRENGTH[signs[imported]] and not (
            _is_side_path(importer) and not _is_side_path(imported)
        )

    def agents(self, signs: dict[str, Sign]) -> list[_Agent]:
        nodes = sorted(signs)
        edges = {
            node: [
                target
                for target in self._reached(node, signs)
                if self._absorbs(node, target, signs)
            ]
            for node in nodes
        }
        component_of: dict[str, int] = {}
        components = _components(nodes, edges)
        for number, members in enumerate(components):
            for member in members:
                component_of[member] = number
        taken = {
            component_of[target]
            for node in nodes
            for target in edges[node]
            if component_of[target] != component_of[node]
        }
        entries = [
            min(members, key=lambda path: (-_STRENGTH[signs[path]], len(path), path))
            for number, members in enumerate(components)
            if number not in taken
        ]
        stronger = [
            (self._project_of(path), _is_side_path(path))
            for path, sign in signs.items()
            if sign not in _WEAKEST
        ]
        kept = []
        for entry in sorted(entries):
            side = _is_side_path(entry)
            if signs[entry] in _WEAKEST:
                project = self._project_of(entry)
                # A side candidate never decides for a file outside side folders.
                if any(
                    other == project and (side or not other_side)
                    for other, other_side in stronger
                ):
                    continue
            kept.append((entry, side))
        return [
            _Agent(entry, signs[entry], side, self._subfolder(entry, side, kept))
            for entry, side in kept
        ]

    def _project_of(self, path: str) -> str:
        return self.projects.nearest(posixpath.dirname(path)) or ""

    def _subfolder(self, entry: str, side: bool, agents: list[tuple[str, bool]]) -> str:
        folder = posixpath.dirname(entry)
        if side:
            return folder
        project = self.projects.nearest(folder)
        if project is None:
            return folder
        # Only agents of this same project count: one in a deeper project of its own
        # doesn't move this agent's folder.
        sharing = sum(
            1
            for other, other_side in agents
            if not other_side and self._project_of(other) == project
        )
        return project if sharing == 1 else folder

    def candidate(
        self, agent: _Agent, listed: list[_Agent], *, upload: bool
    ) -> Candidate:
        subfolder = "" if upload else agent.subfolder
        deeper: list[str] = []
        if not upload:
            # An upload lists one agent, so nothing else claims a folder under the root.
            deeper = [
                other.subfolder
                for other in listed
                if other.subfolder != subfolder and _under(other.subfolder, subfolder)
            ]
        own = [agent.entry] if agent.entry in self.facts else []
        for path in self.facts:
            if (
                path != agent.entry
                and _under(path, subfolder)
                and not any(_under(path, folder) for folder in deeper)
                and not _is_side_path(_below(path, subfolder))
            ):
                own.append(path)
        code = sorted({name for path in own for name in self.facts[path].env})
        templates = self.templates.get(subfolder, set()) | self.templates.get("", set())
        facts = self.facts.get(agent.entry)
        project_file = self.projects.file_in(subfolder)
        return Candidate(
            entry=agent.entry,
            subfolder=subfolder,
            sign=agent.sign,
            # The subfolder is always the entry's folder or one above it, so the
            # entry's path holds every part of it.
            side=agent.side,
            builds=tuple(name for name, _ in facts.builds) if facts else (),
            build_may_stop=build_hazard(
                self.source,
                subfolder,
                deadline=self.deadline,
                clock=self.clock,
                hazards=self.hazards,
            ),
            partial=False,
            env_names=(*code, *sorted(templates - set(code))),
            tools=sum(self.facts[path].tools for path in own),
            prompt_files=_prompt_count(self.prompt_paths, subfolder),
            evidence=_evidence(
                agent.entry,
                facts,
                graph=self.graphs.get(agent.entry),
                agent_py=agent.sign == "agent_py",
                model=agent.sign == "model",
                project=(subfolder, project_file) if project_file else None,
            ),
        )

    def result(self, *, upload: bool) -> WalkResult:
        signs: dict[str, Sign] = {}
        for path, facts in self.facts.items():
            sign = _sign(path, facts, self.graphs)
            if sign is not None:
                signs[path] = sign
        left_out = 0
        agents = []
        for agent in self.agents(signs):
            if fits_path_rule(agent.entry) and (
                not agent.subfolder or fits_path_rule(agent.subfolder)
            ):
                agents.append(agent)
            else:
                left_out += 1
        agents.sort(
            key=lambda a: (a.side, -_STRENGTH[a.sign], a.subfolder, a.entry)
        )
        listed = agents[:1] if upload else agents[:_MAX_AGENTS]
        outside = {sign for path, sign in signs.items() if not _is_side_path(path)}
        return WalkResult(
            agents=tuple(
                self.candidate(agent, listed, upload=upload) for agent in listed
            ),
            more_than_fifty=not upload and len(agents) > _MAX_AGENTS,
            stopped=self.stopped,
            unread_files=self.unread,
            source_outside_side=self.source_outside_side,
            candidate_outside_side=any(not _is_side_path(path) for path in signs),
            script_outside_side=self.script_outside_side,
            only_weak_outside_side=bool(outside) and outside <= _WEAKEST,
            paths_left_out=left_out,
            upload_others=len(agents) - len(listed) if upload else 0,
            upload_others_side=sum(agent.side for agent in agents[len(listed) :])
            if upload
            else 0,
        )


def _prompt_count(paths: list[str], subfolder: str) -> int:
    return sum(
        1
        for path in paths
        if _under(path, subfolder) and "prompt" in _below(path, subfolder).lower()
    )


def _empty_result(walk: _Walk, stopped: Literal["limit", "memory"]) -> WalkResult:
    return WalkResult(
        agents=(),
        more_than_fifty=False,
        stopped=stopped,
        unread_files=walk.unread,
        source_outside_side=walk.source_outside_side,
        candidate_outside_side=False,
        script_outside_side=False,
        only_weak_outside_side=False,
        paths_left_out=0,
        upload_others=0,
        upload_others_side=0,
    )


def walk_repo(
    source: Path,
    *,
    deadline: float,
    upload: bool = False,
    clock: Callable[[], float] = time.monotonic,
    hazards: dict[str, Hazard] | None = None,
) -> WalkResult:
    """``hazards`` is one scan's answers from ``build_hazard``, shared with ``describe_entry``."""
    walk = _Walk(source, deadline=deadline, clock=clock, hazards=hazards)
    try:
        walk.run()
    except MemoryError:
        # Only finished rows survive a memory error, and the walk has none yet.
        walk.facts.clear()
        walk.graphs.clear()
        walk.templates.clear()
        walk.prompt_paths.clear()
        logger.warning("agent walk: stopped=memory unread=%d", walk.unread)
        return _empty_result(walk, "memory")
    # Any other fault here is the kit's, not the repository's: it goes to the scan's crash
    # path, never to "the repository is larger than the scan reads".
    result = walk.result(upload=upload)
    logger.info(
        "agent walk: files_read=%d unread=%d agents=%d left_out=%d stopped=%s",
        walk.files_read,
        result.unread_files,
        len(result.agents),
        result.paths_left_out,
        result.stopped or "no",
    )
    return result


def build_hazard(
    source: Path,
    subfolder: str,
    *,
    deadline: float = math.inf,
    clock: Callable[[], float] = time.monotonic,
    hazards: dict[str, Hazard] | None = None,
) -> Hazard:
    """Why a build of the folder may stop on its own fallback, or ``""``.

    ``hazards`` holds one scan's answers by folder, so a folder many agents share is looked
    through once. The look stops, with ``""``, at the deadline or after ``_HAZARD_ENTRIES``
    entries: it walks folders the scan skips, which the walk's own clock never saw.
    """
    if hazards is not None and subfolder in hazards:
        return hazards[subfolder]
    hazard = _hazard(source, subfolder, deadline=deadline, clock=clock)
    if hazards is not None:
        hazards[subfolder] = hazard
    return hazard


def _hazard(
    source: Path, subfolder: str, *, deadline: float, clock: Callable[[], float]
) -> Hazard:
    top = os.fspath(source)
    if _has_link(top, subfolder):
        return ""
    folder = os.path.join(top, subfolder) if subfolder else top
    try:
        data = _read_file(os.path.join(folder, _LANGGRAPH), _FILE_BYTES + 1)
        document = json.loads(data.decode("utf-8"))
        graphs = document.get("graphs") if isinstance(document, dict) else None
        if isinstance(graphs, dict) and len(graphs) > 1:
            return "graphs"
    except (OSError, ValueError, RecursionError):
        # No readable langgraph.json, so it declares no second graph; the agent.py count follows.
        logger.debug("hazard: langgraph.json not read", exc_info=True)
    # The build counts every `agent.py` below, side folders and the scan's skipped
    # folders included, ignoring only its own short list.
    found = 0
    entries = 0
    # os.walk reports no listing errors unless asked, so nothing here is expected to raise.
    for index, (_, directories, files) in enumerate(os.walk(folder, followlinks=False)):
        # The folder's own listing is one read and always looked at; the clock bounds the
        # folders below it.
        if index and clock() > deadline:
            return ""
        directories[:] = [name for name in directories if name not in _BUILD_IGNORED]
        found += files.count("agent.py")
        if found > 1:
            return "agent_py"
        entries += len(directories) + len(files)
        if entries > _HAZARD_ENTRIES:
            return ""
    return ""


class _Description:
    """The reads behind one AI-found agent, bounded by its own caps and the clock."""

    def __init__(
        self,
        top: str,
        entry: str,
        subfolder: str,
        *,
        deadline: float,
        clock: Callable[[], float],
    ) -> None:
        self.top = top
        self.entry = entry
        self.subfolder = subfolder
        self.deadline = deadline
        self.clock = clock
        self.projects = _Projects(top, _PROJECT_FILES)
        self.facts: dict[str, _Facts] = {}
        self.script_env: set[str] = set()
        self.templates: set[str] = set()
        self.prompt_files = 0
        self.files_read = 0
        self.bytes_read = 0
        # A cap or the clock: the reading stops here.
        self.partial = False
        # One file that couldn't be read or parsed: the counts are short, but the reading
        # goes on, as the walk goes on past an unread file.
        self.short = False
        self.shadowed = _ShadowedNames(top, self.projects)

    def run(self) -> None:
        if _has_link(self.top, self.subfolder):
            # Nothing under it is read, so its counts are short.
            self.partial = True
            return
        for folder in dict.fromkeys((self.subfolder, "")):
            for name in _TEMPLATES:
                if self._out_of_time():
                    return
                data = self._read(os.path.join(self.top, folder, name))
                if self.partial:
                    return
                if data is not None:
                    try:
                        self.templates |= _template_names(data)
                    except _FILE_ERRORS:
                        # Its names are missing, so the row's settings are short.
                        self.short = True
                    except MemoryError:
                        raise
                    except Exception:
                        logger.warning("agent description: a template's reader failed", exc_info=True)
                        self.short = True
        start = os.path.join(self.top, self.subfolder) if self.subfolder else self.top
        for directory, folder, name in _source_files(self.top, start):
            if self._out_of_time():
                return
            self._take(directory, folder, name)
            if self.partial:
                return

    def _out_of_time(self) -> bool:
        if self.clock() > self.deadline:
            self.partial = True
        return self.partial

    def _take(self, directory: str, folder: str, name: str) -> None:
        path = _join(folder, name)
        full = os.path.join(directory, name)
        below = _below(path, self.subfolder)
        if "prompt" in below.lower() and not os.path.islink(full):
            self.prompt_files += 1
        if path != self.entry and _is_side_path(below):
            return
        if not name.endswith((".py", *_SCRIPT_SUFFIXES)):
            return
        data = self._read(full)
        if data is None:
            return
        try:
            if name.endswith(".py"):
                self.facts[path] = _python_facts(data, self.shadowed.for_folder(folder))
            else:
                self.script_env |= _script_names(data)
        except _FILE_ERRORS:
            # Its facts are missing, so the row's counts are short.
            self.short = True
        except MemoryError:
            raise
        except Exception:
            logger.warning("agent description: a file's reader failed", exc_info=True)
            self.short = True

    def _read(self, path: str) -> bytes | None:
        """The file's bytes, or None for a file that can't or mustn't be read; sets
        ``partial`` when a cap is reached."""
        if self.files_read >= _DESCRIBE_FILES:
            self.partial = True
            return None
        try:
            data = _read_file(path, _FILE_BYTES + 1)
        except FileNotFoundError:
            # A settings template that isn't there is the usual case, not a short count.
            return None
        except (OSError, ValueError):
            # Unreadable, a link, or a name the system can't open: not read, so the counts
            # are short.
            self.short = True
            return None
        self.files_read += 1
        self.bytes_read += len(data)
        if self.bytes_read > _DESCRIBE_BYTES:
            self.partial = True
            return None
        return data if len(data) <= _FILE_BYTES else None


def describe_entry(
    source: Path,
    entry: str,
    folder: str,
    *,
    deadline: float,
    upload: bool = False,
    clock: Callable[[], float] = time.monotonic,
    hazards: dict[str, Hazard] | None = None,
) -> Candidate | None:
    """The row for one file the AI step kept; None only when its path is refused."""
    if not fits_path_rule(entry):
        return None
    top = os.fspath(source)
    entry_folder = posixpath.dirname(entry)
    projects = _Projects(top, (*_PROJECT_FILES, _PACKAGE_FILE))
    if upload:
        subfolder = ""
    elif (not folder or fits_path_rule(folder)) and _under(entry, folder):
        subfolder = folder
    else:
        nearest = projects.nearest(entry_folder)
        subfolder = entry_folder if nearest is None else nearest
    reading = _Description(top, entry, subfolder, deadline=deadline, clock=clock)
    try:
        reading.run()
    except MemoryError:
        raise
    except Exception:
        # Runs after the model was paid for, so a fault in the kit keeps what was read and
        # marks the row partial rather than ending in a paid retry. The trace is logged masked.
        logger.warning("agent description stopped", exc_info=True)
        reading.partial = True
    facts = reading.facts.get(entry)
    code = {name for found in reading.facts.values() for name in found.env}
    code |= reading.script_env
    project_file = projects.file_in(subfolder)
    partial = reading.partial or reading.short
    if not partial:
        logger.info("agent description: files_read=%d", reading.files_read)
    else:
        logger.info("agent description: files_read=%d partial=yes", reading.files_read)
    return Candidate(
        entry=entry,
        subfolder=subfolder,
        sign="client",
        side=_is_side_path(entry),
        builds=tuple(name for name, _ in facts.builds) if facts else (),
        build_may_stop=build_hazard(
            source, subfolder, deadline=deadline, clock=clock, hazards=hazards
        ),
        partial=partial,
        env_names=(*sorted(code), *sorted(reading.templates - code)),
        tools=sum(found.tools for found in reading.facts.values()),
        prompt_files=reading.prompt_files,
        evidence=_evidence(
            entry,
            facts,
            graph=None,
            agent_py=False,
            project=(subfolder, project_file) if project_file else None,
        ),
    )
