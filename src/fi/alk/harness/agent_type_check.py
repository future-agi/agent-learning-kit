"""Does an agent use a browser, crawling or computer-use tool, and what follows from that.

No code can say for certain what an agent's main job is, so this is one of two opinions: what the
source uses, set against what the model read. It calls no model and runs none of the agent's code,
and it needs no job or session, so a reading can be checked without a build.
"""

from __future__ import annotations

import ast
import fnmatch
import io
import json
import os
import re
import tokenize
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any, Literal

from .failure_file import FAILURE_FILE
from .instrumentation_evidence import _SKIPPED_DIRECTORIES, _imports
from .job import FailureDomain, HarnessFailure, HarnessStage

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
    }
)
TOOL_MARKER = re.compile(
    r"\bcomputer[_-]use(?:[_-]preview)?\b|\bComputerUse\b|\bcomputer_\d{8}\b"
    r"|@playwright/mcp|mcp-server-playwright"
)
# Hidden folders and anything named for tests or e2e describe the developer's setup too.
NOT_THE_AGENT = frozenset(
    {"docs", "doc", "examples", "example", "samples", "vendor", "third_party", "evals"}
)
# What the world registry already treats as a browser.
BROWSER_MODALITIES = frozenset({"browser", "computer_use", "cua"})

_TOOLKIT = "PlayWrightBrowserToolkit"
_TEST_FILES = ("test_*.py", "*_test.py", "conftest.py", "*.test.*", "*.spec.*")
_SCRIPT_SUFFIXES = frozenset({".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx"})
_LARGEST_CODE_FILE = 1024 * 1024
# Strings are matched first and kept, so "https://..." or "src/**/*.ts" isn't read as a comment.
# An unclosed one ends at its line or the file: a start that could fail makes a customer's odd file
# cost the square of its size, which holds up the build's conversation until it's done.
_SCRIPT_COMMENT_OR_STRING = re.compile(
    r"""("(?:\\.|[^"\\\n])*(?:"|(?=\n)|\\?\Z)|'(?:\\.|[^'\\\n])*(?:'|(?=\n)|\\?\Z)|`(?:\\.|[^`\\])*(?:`|\\?\Z))"""
    r"""|//[^\n]*|/\*.*?(?:\*/|\Z)""",
    re.DOTALL,
)


# A whole word only: "latest" and "contest" hold real code.
_TEST_FOLDER = re.compile(r"(?:^|[^a-z0-9])(?:tests?|testing|testdata|unittests?|integrationtests?|e2e(?:tests?)?)(?:$|[^a-z0-9])")


def _not_the_agent_dir(name: str) -> bool:
    lowered = name.lower()
    return (
        name in _SKIPPED_DIRECTORIES
        or lowered in NOT_THE_AGENT
        or name.startswith(".")
        or _TEST_FOLDER.search(lowered) is not None
    )


def _not_the_agent_file(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in _TEST_FILES)


def _agent_files(root: Path) -> Iterator[Path]:
    for directory, subdirectories, files in os.walk(root):
        # A folder of unpacked distributions holds other people's code, whose SDKs name these
        # tools whether or not this agent uses them.
        if Path(directory) != root and any(
            name.endswith(".dist-info") for name in subdirectories
        ):
            subdirectories[:] = []
            continue
        subdirectories[:] = [
            name for name in subdirectories if not _not_the_agent_dir(name)
        ]
        for name in files:
            if not _not_the_agent_file(name):
                yield Path(directory) / name


def _packages_used(path: Path) -> set[str]:
    try:
        if not path.is_file():
            return set()
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return set()
    dependencies = manifest.get("dependencies") if isinstance(manifest, dict) else None
    if not isinstance(dependencies, dict):
        return set()
    return BROWSER_PACKAGES.intersection(dependencies)


def _imports_in(path: Path) -> set[str]:
    # One file at a time: a customer's odd file can run the parser out of memory, and that mustn't
    # take the rest of the scan, or the reading it serves, down with it.
    try:
        if not path.is_file() or path.stat().st_size > _LARGEST_CODE_FILE:
            return set()
        return _imports(ast.parse(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, SyntaxError, RecursionError, MemoryError):
        return set()


def _modules_at_top(root: Path) -> set[str]:
    """What sits ahead of an installed package on the import path, so isn't that package."""
    try:
        return {
            entry.stem if entry.is_file() else entry.name
            for entry in root.iterdir()
            if entry.suffix == ".py" or (entry / "__init__.py").is_file()
        }
    except OSError:
        return set()


def _python_code(text: str) -> str:
    return " ".join(
        token.string
        for token in tokenize.generate_tokens(io.StringIO(text).readline)
        if token.type != tokenize.COMMENT
    )


def _markers_in(path: Path) -> set[str]:
    try:
        if not path.is_file() or path.stat().st_size > _LARGEST_CODE_FILE:
            return set()
        text = path.read_text(encoding="utf-8")
        code = (
            _python_code(text)
            if path.suffix == ".py"
            else _SCRIPT_COMMENT_OR_STRING.sub(lambda hit: hit.group(1) or "", text)
        )
    except (OSError, ValueError, SyntaxError, tokenize.TokenError):
        return set()
    return {
        "Playwright MCP" if "playwright" in hit.group() else "computer-use tool"
        for hit in TOOL_MARKER.finditer(code)
    }


def browser_tools_found(source_root: Path | None) -> list[str]:
    """The browser, crawling and computer-use tools the agent's own code uses, by display name.

    Python dependency files aren't read: an agent that uses a tool imports it, and they're where
    dev and test lines live. Config files aren't either: they describe the developer's editor.
    """
    if source_root is None:
        return []
    root = Path(source_root).resolve()
    found: set[str] = set()
    imported: set[str] = set()
    for path in _agent_files(root):
        if path.name == "package.json":
            found |= _packages_used(path)
        elif path.suffix == ".py" or path.suffix in _SCRIPT_SUFFIXES:
            found |= _markers_in(path)
            if path.suffix == ".py":
                imported |= _imports_in(path)
    local = _modules_at_top(root)
    for name in imported:
        top = name.split(".")[0]
        if top in local:
            continue
        if top in BROWSER_IMPORTS:
            found.add(top)
        elif name.endswith(f".{_TOOLKIT}"):
            found.add(_TOOLKIT)
    return sorted({name.replace("_", "-") for name in found})


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
