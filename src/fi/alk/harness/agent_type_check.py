"""Does an agent use a browser, crawling or computer-use tool, and what follows from that.

No code can say for certain what an agent's main job is, so this is one of two opinions: what the
source uses, set against what the model read. It calls no model and runs none of the agent's code,
and it needs no job or session, so a reading can be checked without a build.
"""

from __future__ import annotations

import ast
import csv
import fnmatch
import io
import json
import os
import re
import tokenize
from collections.abc import Iterable, Iterator, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from .credentials import imported_by_agent
from .failure_file import FAILURE_FILE
from .instrumentation_evidence import _SKIPPED_DIRECTORIES, _imports
from .job import FailureDomain, HarnessFailure, HarnessStage
from .source_folder import agent_folder

AGENT_TYPE_UNSUPPORTED = "agent_type_unsupported"
# EX_CONFIG, which the platform doesn't retry even if it can't read the file.
EXIT_REFUSED = 78
BROWSER_IMPORTS = frozenset(
    {
        "playwright",
        "patchright",
        "camoufox",
        "playwright_stealth",
        "selenium",
        "seleniumbase",
        "seleniumwire",
        "undetected_chromedriver",
        "splinter",
        "pyppeteer",
        "pydoll",
        "DrissionPage",
        "zendriver",
        "nodriver",
        "botasaurus",
        "browser_use",
        "stagehand",
        "browserbase",
        "steel",
        "hyperbrowser",
        "skyvern",
        "anchorbrowser",
        "agentql",
        "scrapy",
        "crawl4ai",
        "firecrawl",
        "crawlee",
        "scrapegraphai",
        "apify_client",
        "pyautogui",
        "pywinauto",
        "e2b_desktop",
        "nova_act",
    }
)
BROWSER_PACKAGES = frozenset(
    {
        "playwright",
        "playwright-core",
        "@playwright/mcp",
        "patchright",
        "puppeteer",
        "puppeteer-core",
        "selenium-webdriver",
        "browser-use",
        "stagehand",
        "@browserbasehq/stagehand",
        "@browserbasehq/sdk",
        "crawlee",
        "firecrawl",
        "@mendable/firecrawl-js",
        "apify-client",
        "puppeteer-extra",
        "playwright-extra",
        "webdriverio",
    }
)
# Every package in these scopes drives a browser.
BROWSER_SCOPES = ("@crawlee/",)
# The tools' own API names only: the bare words turn up in prompts and tracing attributes.
TOOL_MARKER = re.compile(
    r"\bcomputer[_-]use[_-]preview\b|\bcomputer-use-20\d\d-\d\d-\d\d\b|\bComputerUse\b"
    r"""|\bcomputer_?[uU]se\b["']?\s*[:=]\s*\{\s*["']?environment\b"""
    r"|\bcomputer_\d{8}\b|@playwright/mcp|mcp-server-playwright"
)
# Hidden folders and folders named for tests or e2e describe the developer's setup too.
NOT_THE_AGENT = frozenset(
    {"docs", "doc", "examples", "example", "samples", "vendor", "third_party", "evals"}
)
# What the world registry already treats as a browser.
BROWSER_MODALITIES = frozenset({"browser", "computer_use", "cua"})

_TOOLKIT = "PlayWrightBrowserToolkit"
_TEST_FILES = ("test_*.py", "*_test.py", "conftest.py", "*.test.*", "*.spec.*")
_SCRIPT_SUFFIXES = frozenset({".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx"})
_LARGEST_CODE_FILE = 1024 * 1024
# Past this a repository holds the conversation up for longer than the rest of the scan is worth.
_LARGEST_SCAN = 128 * 1024 * 1024
_LARGEST_RECORD = 1024 * 1024
_LARGEST_RECORDS = 64 * 1024 * 1024
# Strings are matched first and kept, so "https://..." or "src/**/*.ts" isn't read as a comment.
# An unclosed one ends at its line or the file: a start that could fail makes a customer's odd file
# cost the square of its size, which holds up the build's conversation until it's done.
# A regex literal is kept the same way, so the "/*" in /\/*$/ doesn't hide the rest of the file. A
# slash where a value is expected starts one: after punctuation or at a line's start, never after a
# name or a number, where it divides.
_SCRIPT_COMMENT_OR_STRING = re.compile(
    r"""("(?:\\.|[^"\\\n])*(?:"|(?=\n)|\\?\Z)|'(?:\\.|[^'\\\n])*(?:'|(?=\n)|\\?\Z)|`(?:\\.|[^`\\])*(?:`|\\?\Z)"""
    r"""|(?<![^\n(,=:\[!&|?{};>])[ \t]*/(?![/*])(?:\\.|\[(?:\\.|[^\]\\\n])*(?:\]|(?=\n)|\\?\Z)|[^/\\\n\[])*(?:/|(?=\n)|\\?\Z))"""
    r"""|//[^\n]*|/\*.*?(?:\*/|\Z)""",
    re.DOTALL,
)


# The whole name, its last part, or a test word and a helper word: "functional_tests" and
# "test_utils" hold tests, while "e2e_agent" and "testing_agent" hold agents, and test automation
# is a main kind of browser agent.
_TEST_FOLDER = re.compile(
    r"_*(?:(?:unit|integration|e2e)[_-]?)?(?:tests?|testing|test[_-]?data)_*|e2e"
    r"|.*[_-](?:tests?|testing|test[_-]?data|e2e)_*"
    r"|(?:tests?|testing)[_.-]?(?:utils?|helpers?|integration|ui|unit|e2e|old|fixtures?|support"
    r"|common|lib|mocks?|selenium|browser|functional|acceptance|scripts?)_*"
)


def _not_the_agent_dir(name: str) -> bool:
    lowered = name.lower()
    return (
        name in _SKIPPED_DIRECTORIES
        or lowered in NOT_THE_AGENT
        or name.startswith(".")
        or _TEST_FOLDER.fullmatch(lowered) is not None
    )


def _not_the_agent_file(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in _TEST_FILES)


def _unpacked_at_top(root: Path) -> set[str]:
    """The files ``pip install -t .`` put beside the agent's code, by each distribution's record.

    Exact paths: one distribution shipping ``tools/x.py`` says nothing about the customer's own
    ``tools/``.
    """
    names: set[str] = set()
    try:
        records = sorted(entry for entry in root.iterdir() if entry.name.endswith(".dist-info"))
    except OSError:
        return names
    read = 0
    for entry in records:
        try:
            record = entry / "RECORD"
            # A link to a device or a named pipe reports no size, and reading it never ends.
            if not record.is_file() or record.stat().st_size > _LARGEST_RECORD:
                continue
            read += record.stat().st_size
            if read > _LARGEST_RECORDS:
                break
            names.update(
                row[0] for row in csv.reader(io.StringIO(record.read_text(encoding="utf-8"))) if row
            )
        except (OSError, ValueError, csv.Error):
            continue
    return names


def _agent_files(root: Path) -> Iterator[Path]:
    # Unpacked distributions hold other people's code, whose SDKs name these tools whether or not
    # this agent uses them. Their records name those files wherever they were unpacked, so the
    # agent's own code beside them, as in a Lambda folder, is still read.
    unpacked: set[Path] = set()
    for directory, subdirectories, files in os.walk(root):
        here = Path(directory)
        if any(name.endswith(".dist-info") for name in subdirectories):
            unpacked.update(here / name for name in _unpacked_at_top(here))
        subdirectories[:] = [
            name for name in subdirectories if not _not_the_agent_dir(name)
        ]
        for name in files:
            path = here / name
            if not _not_the_agent_file(name) and path not in unpacked:
                yield path


def _packages_used(path: Path) -> set[str]:
    try:
        if not path.is_file():
            return set()
        # Windows editors often save a BOM, which json refuses.
        manifest = json.loads(path.read_text(encoding="utf-8").removeprefix("\ufeff"))
    except (OSError, ValueError, RecursionError):
        return set()
    if not isinstance(manifest, dict):
        return set()
    names: set[str] = set()
    # A test-automation agent usually lists its browser library as a dev dependency.
    for field in ("dependencies", "devDependencies"):
        listed = manifest.get(field)
        if isinstance(listed, dict):
            names.update(listed)
    return {
        name
        for name in names
        if name in BROWSER_PACKAGES or name.startswith(BROWSER_SCOPES)
    }


def _imports_in(path: Path) -> set[str]:
    # One file at a time: a customer's odd file can run the parser out of memory, and that mustn't
    # take the rest of the scan, or the reading it serves, down with it.
    try:
        if not path.is_file() or path.stat().st_size > _LARGEST_CODE_FILE:
            return set()
        # From bytes, so a BOM or a coding line is honoured as Python itself would.
        return _imports(ast.parse(path.read_bytes()))
    except (OSError, ValueError, SyntaxError, RecursionError, MemoryError):
        return set()


def _modules_at_top(root: Path) -> set[str]:
    """What sits ahead of an installed package on the import path, so isn't that package."""
    unpacked = _unpacked_at_top(root)
    try:
        return {
            entry.stem if entry.is_file() else entry.name
            for entry in root.iterdir()
            if entry.name not in unpacked
            and f"{entry.name}/__init__.py" not in unpacked
            and (entry.suffix == ".py" or (entry / "__init__.py").is_file())
        }
    except OSError:
        return set()


def _python_code(text: str) -> str:
    return " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type != tokenize.COMMENT
    )


def _script_kept(hit: re.Match[str]) -> str:
    kept = hit.group(1) or ""
    # A lone apostrophe, as in JSX text, isn't a string, so it can't protect a comment after it.
    if kept.startswith("'") and not kept.endswith("'"):
        return kept.split("//", 1)[0]
    return kept


def _markers_in(path: Path) -> set[str]:
    try:
        if not path.is_file() or path.stat().st_size > _LARGEST_CODE_FILE:
            return set()
        text = path.read_text(encoding="utf-8")
        code = (
            _python_code(text)
            if path.suffix == ".py"
            else _SCRIPT_COMMENT_OR_STRING.sub(_script_kept, text)
        )
    except (OSError, ValueError, SyntaxError, tokenize.TokenError):
        return set()
    return {
        "Playwright MCP" if "playwright" in hit.group() else "computer-use tool"
        for hit in TOOL_MARKER.finditer(code)
    }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def browser_tools_found(source_root: Path | None) -> list[str]:
    """The browser, crawling and computer-use tools the agent's own code uses, by display name.

    Python dependency files aren't read: an agent that uses a tool imports it, and they're where
    dev and test lines live. Config files aren't either: they describe the developer's editor.
    """
    if source_root is None:
        return []
    root = Path(source_root).resolve()
    return list(_scan(root, _signature(root)))


def _is_read(path: Path) -> bool:
    return path.name == "package.json" or path.suffix == ".py" or path.suffix in _SCRIPT_SUFFIXES


def _reading_order(path: Path) -> tuple[bool, str]:
    # Python and package.json first: script bundles shouldn't use up the limit before them, and the
    # answer shouldn't depend on the order the disk lists files in.
    return path.suffix in _SCRIPT_SUFFIXES, str(path)


def _signature(root: Path) -> tuple[int, int, int]:
    """What changes when the files a scan reads change: a nested edit or a new checkout."""
    latest = count = size = 0
    for path in _agent_files(root):
        if _is_read(path):
            try:
                info = path.stat()
            except OSError:
                continue
            latest = max(latest, info.st_mtime_ns)
            count += 1
            size += info.st_size
    return latest, count, size


@lru_cache(maxsize=8)
def _scan(root: Path, _files: tuple[int, int, int]) -> tuple[str, ...]:
    """One scan per state of the files: every builder reopen in a process asks again."""
    return _tools_in(_agent_files(root), _modules_at_top(root))


def _tools_in(paths: Iterable[Path], local: set[str]) -> tuple[str, ...]:
    found: set[str] = set()
    imported: set[str] = set()
    scanned = 0
    for path in sorted(filter(_is_read, paths), key=_reading_order):
        size = _file_size(path)
        # Never read, so it mustn't use up the limit before the code that is.
        if size > _LARGEST_CODE_FILE:
            continue
        scanned += size
        if scanned > _LARGEST_SCAN:
            break
        # One file at a time: the tokenizer raises more than it documents (SystemError on a NUL
        # byte in 3.12), and one odd file mustn't stop every reading from being checked.
        try:
            if path.name == "package.json":
                found |= _packages_used(path)
            else:
                markers = _markers_in(path)
                names = _imports_in(path) if path.suffix == ".py" else set()
                found |= markers
                imported |= names
        except Exception:
            continue
    for name in imported:
        top = name.split(".")[0]
        if top in local:
            continue
        if top in BROWSER_IMPORTS:
            found.add(top)
        elif name.endswith(f".{_TOOLKIT}"):
            found.add(_TOOLKIT)
    return tuple(sorted({name.replace("_", "-") for name in found}))


def browser_tools_in_agent(source_root: Path, subfolder: str) -> list[str]:
    """What one agent of a repository uses: its folder, what it imports, and the files directly
    in the folders above it. A sibling's browser tool says nothing about this agent.

    Raises ``SubfolderNotFound`` for a folder that can't be used.
    """
    root = Path(source_root).resolve()
    folder = agent_folder(root, subfolder)
    above = [
        path
        for parent in folder.parents
        if parent.is_relative_to(root)
        for path in sorted(parent.iterdir())
        if path.is_file() and not path.is_symlink() and not _not_the_agent_file(path.name)
    ]
    shared = [
        path
        for path in sorted(imported_by_agent(root, subfolder))
        if not _not_the_agent_file(path.name)
        and not any(_not_the_agent_dir(part) for part in path.relative_to(root).parent.parts)
    ]
    # A shared script's dependencies are declared beside it, not in the script.
    manifests = [
        path.parent / "package.json"
        for path in shared
        if path.suffix != ".py" and (path.parent / "package.json").is_file()
    ]
    return list(
        _tools_in(
            dict.fromkeys((*_agent_files(folder), *above, *shared, *manifests)),
            _modules_at_top(folder) | _modules_at_top(root),
        )
    )


def modality_text(value: Any) -> str:
    """The modality as text to compare, with a list's items one per line."""
    if isinstance(value, list):
        value = "\n".join(str(item) for item in value)
    return "" if value is None else str(value).strip().lower()


def is_browser(text: str) -> bool:
    # Whole list items only: free text such as "not a browser" isn't a reading of browser.
    return text in BROWSER_MODALITIES or "browser" in text.split("\n")


def decide(
    claimed: Any, found: Sequence[str], *, looked_again: bool
) -> Literal["refuse", "second_look", "keep"]:
    """What the model read against what the code check found. Where they disagree the model gets
    one more look, and its answer then stands."""
    if is_browser(modality_text(claimed)):
        return "refuse" if found or looked_again else "second_look"
    return "second_look" if found and not looked_again else "keep"


def agent_type_failure(found: Sequence[str]) -> HarnessFailure:
    if found:
        message = (
            "This agent's main job is driving a browser, operating a computer or crawling sites "
            f"(found: {', '.join(found)}). Only voice and chat agents are supported for now."
        )
    else:
        message = (
            "This agent's main job was read as driving a browser, operating a computer or "
            "crawling sites. Only voice and chat agents are supported for now."
        )
    return HarnessFailure(
        domain=FailureDomain.AGENT,
        stage=HarnessStage.UNDERSTANDING_AGENT,
        code=AGENT_TYPE_UNSUPPORTED,
        message=message,
        retryable=False,
        details={"agent_type": "browser", "tools": list(found)},
    )


def failure_message(output: Path) -> str:
    """The message of the failure a build left in ``output``, or nothing if it can't be read."""
    try:
        failure = json.loads((Path(output) / FAILURE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return ""
    message = failure.get("message") if isinstance(failure, dict) else None
    return message if isinstance(message, str) else ""
