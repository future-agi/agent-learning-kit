"""Does an agent use a browser, crawling or computer-use tool, and what follows from that.

No code can say for certain what an agent's main job is, so this is one of two opinions: what the
source uses, set against what the model read. It calls no model and runs none of the agent's code,
and it needs no job or session, so a reading can be checked without a build.
"""

from __future__ import annotations

import csv
import fnmatch
import io
import json
import logging
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
from .python_source import (
    LARGEST_PARSED_MODULE,
    EscapeBudget,
    ParseBudget,
    TokenBudget,
    parsed,
    read_module,
)
from .source_folder import agent_folder, is_within

logger = logging.getLogger(__name__)

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

# Every match of TOOL_MARKER starts with one of these, inside one name or string, so a file
# without one has no marker, and isn't tokenized to strip its comments.
_MARKER_START = re.compile(
    r"computer[_-]?[uU]se|ComputerUse|computer_\d{8}|@playwright/mcp|mcp-server-playwright"
)
_TOOLKIT = "PlayWrightBrowserToolkit"
# A whole name: no letter, digit or "_" before or after it. In a unicode-escape file read as bytes,
# an escape such as \t, \x20, \040 or \u0020 just before it counts as a boundary too.
_WHOLE_NAMES = {
    name: re.compile(
        # The name looked for first, so only the places it starts at are tried: eight lookbehinds
        # at every byte held the save 2.8 s (round 3 reviewer A).
        rf"(?={re.escape(name)})(?:(?<!\w)|(?<=\\[abfnrtv])|(?<=\\x[0-9A-Fa-f]{{2}})|(?<=\\[0-7])|(?<=\\[0-7]{{2}})"
        rf"|(?<=\\[0-7]{{3}})|(?<=\\u[0-9A-Fa-f]{{4}})|(?<=\\U[0-9A-Fa-f]{{8}})){re.escape(name)}(?!\w)",
        re.ASCII,
    )
    for name in BROWSER_IMPORTS
}
# A Python file that names none of these holds no browser import or tool marker, so it isn't parsed.
_PYTHON_NAMES = (
    *(name.encode() for name in sorted(BROWSER_IMPORTS)),
    _TOOLKIT.encode(),
    b"omputer",
)
_TEST_FILES = ("test_*.py", "*_test.py", "conftest.py", "*.test.*", "*.spec.*")
_SCRIPT_SUFFIXES = frozenset({".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx"})
_CODE_SUFFIXES = _SCRIPT_SUFFIXES | {".py"}
_LARGEST_CODE_FILE = 1024 * 1024
# What one scan searches with regular expressions in files it can't decode (see _tools_in).
_LARGEST_RAW_SEARCH = 1024 * 1024
# Past this a repository holds the conversation up for longer than the rest of the scan is worth.
_LARGEST_SCAN = 128 * 1024 * 1024
# Scripts are read by regular expressions that call back for every string or comment: 128 MB of
# crafted ones held the save 13 s. An agent's own scripts are far smaller.
_LARGEST_SCRIPT_SCAN = 16 * 1024 * 1024
# And the Python searched for the names it may hold: each file is searched once for each of
# about 35 names, and 128 MB of crafted ones held the save 1.7 s. An agent's own Python is far
# smaller.
_LARGEST_PYTHON_SCAN = 32 * 1024 * 1024
_LARGEST_RECORD = 1024 * 1024
# The scripts the walk reads to follow imports, while the conversation waits; bundling reads 32 MB.
_WALKED_SCRIPTS = 2 * 1024 * 1024
# The import names it tries then.
_WALKED_IMPORTS = 5_000
# And the Python it parses then, each reading of a file charged: twice the other scans' total, so an
# agent's own code of a few hundred KB isn't cut, while crafted files that only this walk parses
# hold the save about 1.3 s on 3.12 and 3.1 s on 3.11.
_WALKED_PARSED = 512 * 1024
# And the lookups on disk it makes then, a file found costing one for each part of its path: a name
# can resolve a package for each of its parts, and 1,000 crafted names took the save 12 s.
_WALKED_LOOKUPS = 100_000
# Per scan, each record read once; a reopen of unchanged files reads none. Each row is parsed and
# kept, so short rows cost the most: a scan through 16 MiB of 7-byte rows takes 0.9 s. A whole
# 411-package environment unpacked in one folder came to 6.4 MiB.
_LARGEST_RECORDS = 16 * 1024 * 1024
# What each record costs at least, a disk block: an empty one still costs its folder and its
# look-ups, so the budget bounds how many are opened too.
_SMALLEST_RECORD = 4 * 1024
_NEAREST_RECORD_FOLDERS = 4
# What the folders at and above an agent's folder may read of records in all, at the save. Each
# row is parsed and kept, so short rows cost the most: 64 MiB of 7-byte rows took 2.0 s on 3.12.3,
# and this is about 0.5 s of them. A whole 411-package environment unpacked in one folder came
# to 6.4 MiB.
_RECORDS_AT_AND_ABOVE = 16 * 1024 * 1024
# Strings are matched first and kept, so "https://..." or "src/**/*.ts" isn't read as a comment.
# An unclosed one ends at its line or the file: a start that could fail makes a customer's odd file
# cost the square of its size, which holds up the build's conversation until it's done.
# A regex literal is kept the same way, so the "/*" in /\/*$/ doesn't hide the rest of the file. A
# slash where a value is expected starts one: after punctuation or at a line's start, never after a
# name or a number, where it divides. Its closing slash must be followed as a regex's is, by flags
# and then no name, quote, "/" or "*": a division that starts a line, or a "/" in JSX text, then
# reads as division, not as a literal that eats a string's opening quote.
# Such a start can fail, so a class holds no "[" and the rest no "]", and a literal that meets one
# is kept to its line's end: a failed literal then can't run over the classes of the slashes after
# it, which would make the line cost its square, and the quotes and backticks it holds can't open a
# string or a template that swaps code and strings for the rest of the file. The "]" also stops a
# literal that runs on through escaped line ends, which would cost the square of their number.
# Kept to its line's end, such a literal can take in a template's opening backtick later on that
# line too; a "/*" inside that template then reads as a comment. That trade is the price of a line
# that can't run away.
_SCRIPT_COMMENT_OR_STRING = re.compile(
    r"""("(?:\\.|[^"\\\n])*(?:"|(?=\n)|\\?\Z)|'(?:\\.|[^'\\\n])*(?:'|(?=\n)|\\?\Z)|`(?:\\.|[^`\\])*(?:`|\\?\Z)"""
    r"""|(?<![^\n(,=:\[!&|?{};>])[ \t]*/(?![/*])(?:\\.|\[(?:\\.|[^\]\[\\\n])*(?:\]|(?=\n)|\\?\Z)|[^/\\\n\[\]])*"""
    r"""(?:/(?=[dgimsuvy]*(?![\w$/*"'`]))|(?=\n)|\\?\Z|(?:\[(?=(?:\\.|[^\]\[\\\n])*\[)|\])[^\n]*))"""
    r"""|//[^\n]*|/\*.*?(?:\*/|\Z)""",
    re.DOTALL,
)


# The whole name, test data, or a test word and a helper word: "integration_tests" and
# "test_utils" hold tests, while "e2e_agent" and "testing_agent" hold agents, and test automation
# is a main kind of browser agent.
_TEST_FOLDER = re.compile(
    r"_*(?:(?:unit|integration|e2e)[_-]?)?(?:tests?|testing|test[_-]?data)_*|e2e"
    r"|.*[_-]test[_-]?data_*"
    r"|(?:tests?|testing)[_.-]?(?:utils?|helpers?|integration|ui|unit|e2e|old|fixtures?|support"
    r"|common|lib|mocks?|selenium|browser|functional|acceptance|scripts?)_*"
)
# A test word as the last part names tests in "functional_tests" but an agent in "web_testing" or
# "qa_e2e", so such a folder is skipped only when its own code is all tests.
_TESTS_LAST = re.compile(r".*[_-](?:tests?|testing|e2e)_*")


def _not_the_agent_dir(name: str, parent: str | None = None) -> bool:
    lowered = name.lower()
    return (
        name in _SKIPPED_DIRECTORIES
        or lowered in NOT_THE_AGENT
        or name.startswith(".")
        or _TEST_FOLDER.fullmatch(lowered) is not None
        or (
            parent is not None
            and _TESTS_LAST.fullmatch(lowered) is not None
            # The walk doesn't enter a linked folder, so what one holds doesn't matter.
            and not os.path.islink(os.path.join(parent, name))
            and _only_tests(os.path.join(parent, name))
        )
    )


def _listed(folder: str) -> tuple[list[str], list[str]]:
    """The code files directly in ``folder``, and the folders in it the walk would enter.

    A ``package.json`` is code here: it lists what the folder's scripts run. A package's
    ``__init__.py`` says nothing either way.
    """
    code: list[str] = []
    folders: list[str] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                if not _not_the_agent_dir(entry.name):
                    folders.append(entry.name)
            elif entry.is_file() and entry.name != "__init__.py" and (
                entry.name == "package.json" or os.path.splitext(entry.name)[1] in _CODE_SUFFIXES
            ):
                code.append(entry.name)
    return code, folders


def _only_tests(folder: str) -> bool:
    """Whether ``folder`` holds tests alone: its own code files are all tests, one of them more
    than a ``conftest.py``, and no folder in it that holds code is other than named for tests.

    A folder of only folders may be the agent's own package. A folder in it holds code if it has
    a code file or a folder of its own: one level is looked at, and deeper is taken as code.
    """
    try:
        code, folders = _listed(folder)
        for name in folders:
            path = os.path.join(folder, name)
            inner, below = _listed(path)
            if _TESTS_LAST.fullmatch(name.lower()) is not None:
                # Tests alone, as the walk would judge it at the top, and nothing deeper.
                if below or (inner and not _tests_alone(inner)):
                    return False
            elif inner or below or os.path.isfile(os.path.join(path, "__init__.py")):
                return False
    except OSError:
        return False
    return _tests_alone(code)


def _tests_alone(code: list[str]) -> bool:
    return any(name.lower() != "conftest.py" for name in code) and all(
        _not_the_agent_file(name) for name in code
    )


def _not_the_agent_file(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in _TEST_FILES)


def _unpacked_at_top(root: Path, left: list[int] | None = None) -> set[str]:
    """The files ``pip install -t .`` put beside the agent's code, by each distribution's record.

    Exact paths: one distribution shipping ``tools/x.py`` says nothing about the customer's own
    ``tools/``. ``left`` is the record bytes still to read, shared across one walk's folders.
    """
    names: set[str] = set()
    try:
        records = sorted(entry for entry in root.iterdir() if entry.name.endswith(".dist-info"))
    except OSError:
        return names
    if left is None:
        left = [_LARGEST_RECORDS]
    for entry in records:
        try:
            record = entry / "RECORD"
            # pip writes neither as a link, and following them reads one record once per link.
            if entry.is_symlink() or record.is_symlink():
                continue
            # A link to a device or a named pipe reports no size, and reading it never ends.
            if not record.is_file() or record.stat().st_size > _LARGEST_RECORD:
                continue
            left[0] -= max(record.stat().st_size, _SMALLEST_RECORD)
            if left[0] < 0:
                break
            names.update(
                row[0] for row in csv.reader(io.StringIO(record.read_text(encoding="utf-8"))) if row
            )
        except (OSError, ValueError, csv.Error):
            continue
    return names


def _agent_files(
    root: Path, left: list[int] | None = None, at_top: set[str] | None = None
) -> Iterator[Path]:
    # Unpacked distributions hold other people's code, whose SDKs name these tools whether or not
    # this agent uses them. Their records name those files wherever they were unpacked, so the
    # agent's own code beside them, as in a Lambda folder, is still read. One budget for the whole
    # walk: a repository can hold any number of folders. Rows stay relative to the folder whose
    # records hold them, as pip wrote them, so what is kept is the rows the budget lets in and that
    # folder's path once: how deep the folder sits doesn't multiply what each row costs.
    # ``at_top`` gets the rows of the records at the top, for a caller that would read them again.
    unpacked: dict[str, set[str]] = {}
    # The folders above this one whose records gave rows, nearest last. The walk goes top down, so
    # leaving a folder is seeing a directory that isn't under it.
    holding: list[str] = []
    if left is None:
        left = [_LARGEST_RECORDS]
    top = os.fspath(root)
    for directory, subdirectories, files in os.walk(root):
        while holding and not directory.startswith(holding[-1] + os.sep):
            del unpacked[holding.pop()]
        if any(name.endswith(".dist-info") for name in subdirectories):
            rows = _unpacked_at_top(Path(directory), left)
            if rows:
                unpacked[directory] = rows
                holding.append(directory)
            if at_top is not None and directory == top:
                at_top |= rows
        subdirectories[:] = [
            name for name in subdirectories if not _not_the_agent_dir(name, directory)
        ]
        # Once the budget is spent, which folders' packages count mustn't depend on the order the
        # disk lists folders in.
        subdirectories.sort()
        # Real layouts nest one or two, as a vendored package's own vendored ones. Checking every
        # file against a crafted chain of them would cost the chain's depth per file; a file the
        # nearest few don't name is read, which only means reading more.
        nearest = holding[-_NEAREST_RECORD_FOLDERS:]
        for name in files:
            path = os.path.join(directory, name)
            if not _not_the_agent_file(name) and not any(
                path[len(top) + 1 :] in unpacked[top] for top in nearest
            ):
                yield Path(path)


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
    found = names & BROWSER_PACKAGES
    # The scope, not the key: the rest of a scoped name is the customer's, of any length, and only
    # names from these lists reach the message, its details and the second look.
    found.update(f"{scope}*" for scope in BROWSER_SCOPES for name in names if name.startswith(scope))
    return found


def _imports_in(text: str, path: Path) -> set[str]:
    # One file at a time: a customer's odd file can run the parser out of memory, and that mustn't
    # take the rest of the scan, or the reading it serves, down with it.
    tree = parsed(text)
    if tree is None:
        logger.debug("agent type scan read no imports from %s", path)
        return set()
    return _imports(tree)


def _largest_read(path: Path) -> int:
    # Python is parsed, which costs a crafted file the square of its size.
    if path.suffix == ".py":
        return min(_LARGEST_CODE_FILE, LARGEST_PARSED_MODULE)
    return _LARGEST_CODE_FILE


def _modules_at_top(root: Path, unpacked: set[str] | None = None) -> set[str]:
    """What sits ahead of an installed package on the import path, so isn't that package.

    ``unpacked`` is the folder's record rows, when the caller has read them already.
    """
    if unpacked is None:
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


def _python_code(tokens: list[tokenize.TokenInfo]) -> str:
    return " ".join(token.string for token in tokens if token.type != tokenize.COMMENT)


def _script_kept(hit: re.Match[str]) -> str:
    kept = hit.group(1) or ""
    # A lone apostrophe, as in JSX text, isn't a string, so it can't protect a comment after it.
    if kept.startswith("'") and not kept.endswith("'"):
        return kept.split("//", 1)[0]
    return kept


def _python_markers(text: str, tokens: TokenBudget, path: Path) -> set[str]:
    if not _MARKER_START.search(text):
        return set()
    if not tokens.spend(text):
        # Past the total, comments can't be told from code, and a marker in one would refuse a
        # browser reading without its second look; so the file's markers aren't read, and the
        # model's reading stands.
        return set()
    try:
        found = tokens.tokens(text)
    except (ValueError, SyntaxError, tokenize.TokenError) as error:
        # The type only: the error can quote a line of the customer's file.
        logger.debug("agent type scan read no markers from %s: %s", path, type(error).__name__)
        return set()
    # Past the work, too, the file's markers aren't read.
    return set() if found is None else _markers(_python_code(found))


def _markers(code: str) -> set[str]:
    return {
        "Playwright MCP" if "playwright" in hit.group() else "computer-use tool"
        for hit in TOOL_MARKER.finditer(code)
    }


def _markers_in(path: Path) -> set[str]:
    """A script's markers; Python's are read by ``_python_markers``."""
    try:
        if not path.is_file() or path.stat().st_size > _LARGEST_CODE_FILE:
            return set()
        # As Node reads it: a stray byte that isn't UTF-8 becomes a replacement character.
        text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError) as error:
        logger.debug("agent type scan read no markers from %s: %s", path, type(error).__name__)
        return set()
    return _markers(_SCRIPT_COMMENT_OR_STRING.sub(_script_kept, text))


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
    """What changes when the files a scan reads change: a nested edit, a new checkout or a record.

    Every reopen asks, so records are looked at, not read: a record that starts or stops naming a
    file changes its own size or time. Files a record names count too, which only rescans more.
    """
    latest = count = size = 0
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = [
            name for name in subdirectories if not _not_the_agent_dir(name, directory)
        ]
        for name in files:
            path = Path(directory, name)
            if name != "RECORD" and (_not_the_agent_file(name) or not _is_read(path)):
                continue
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
    # Each record read once: the walk keeps the top's rows for the modules there.
    left = [_LARGEST_RECORDS]
    at_top: set[str] = set()
    files = list(_agent_files(root, left, at_top))
    # The record the budget left unread may be the one naming a package unpacked at the top,
    # so no folder there shows the agent wrote it: every import counts as the agent's.
    local = set() if left[0] < 0 else _modules_at_top(root, at_top)
    return _tools_in(files, local)


def _tools_in(
    paths: Iterable[Path], local: set[str], first: Path | None = None
) -> tuple[str, ...]:
    """The browser tools ``paths`` import or name, the files in ``first`` read before the rest:
    the totals below cut the files read last, and the agent's own mustn't be those."""
    found: set[str] = set()
    imported: set[str] = set()
    scanned = scripts = python = 0
    budget = ParseBudget()
    tokens = TokenBudget()
    escapes = EscapeBudget()
    searched = ParseBudget(_LARGEST_RAW_SEARCH)
    for path in sorted(
        filter(_is_read, paths),
        key=lambda path: (first is not None and not is_within(path, first), *_reading_order(path)),
    ):
        size = _file_size(path)
        # Never read, so it mustn't use up the limit before the code that is.
        if size > _largest_read(path):
            continue
        scanned += size
        if scanned > _LARGEST_SCAN:
            break
        if path.suffix in _SCRIPT_SUFFIXES:
            scripts += size
            if scripts > _LARGEST_SCRIPT_SCAN:
                continue
        elif path.suffix == ".py":
            python += size
            if python > _LARGEST_PYTHON_SCAN:
                continue
        # One file at a time: the tokenizer raises more than it documents (SystemError on a NUL
        # byte in 3.12), and one odd file mustn't stop every reading from being checked.
        try:
            if path.name == "package.json":
                found |= _packages_used(path)
            elif path.suffix != ".py":
                found |= _markers_in(path)
            elif (
                text := read_module(
                    path, _largest_read(path), _PYTHON_NAMES, undecoded := [], escapes
                )
            ) is not None:
                markers = _python_markers(text, tokens, path)
                names = _imports_in(text, path) if budget.spend(text) else set()
                found |= markers
                imported |= names
            elif undecoded:
                # A codec refused by name, which Python still runs the file in: the browser
                # packages and tool markers it names, as bytes, count. That leads to a second
                # look, never a refusal on its own. #224 does the same.
                raw = undecoded[0].decode("latin-1")
                named = {name for name in BROWSER_IMPORTS if name in raw}
                if _TOOLKIT in raw:
                    found.add(_TOOLKIT)
                # The regular expressions try each place in the text, so only while the scan's
                # total lasts: 1,023 crafted files held the save 9 s. Past it, a name counts
                # wherever it appears, which can only bring a second look.
                if searched.spend(raw):
                    # Whole names only: "steelers" in a string is no import of steel.
                    named = {name for name in named if _WHOLE_NAMES[name].search(raw)}
                    found |= _markers(raw)
                imported |= named
        except Exception:
            # A skipped file may be the agent's main one, and the reading then finds nothing.
            logger.warning("agent type scan skipped %s", path, exc_info=True)
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


def _files_in(folder: Path) -> list[Path]:
    try:
        return [
            path for path in sorted(folder.iterdir()) if path.is_file() and not path.is_symlink()
        ]
    except OSError:
        return []


def _below_shared(path: Path, folder: Path) -> tuple[str, ...]:
    """The folders holding ``path`` below the deepest one it shares with the agent's folder."""
    held, named = path.parent.parts, folder.parts
    common = 0
    while common < min(len(held), len(named)) and held[common] == named[common]:
        common += 1
    return held[common:]


def _walked(root: Path, subfolder: str) -> frozenset[Path]:
    """What the agent's folder imports, followed with the save's limits. A walk a limit stopped
    is logged: what it didn't reach, the save's checks don't see, and bundling's walk, with no
    limit, may."""
    stopped: list[bool] = []
    found = imported_by_agent(
        root,
        subfolder,
        parse_limit=LARGEST_PARSED_MODULE,
        parse_budget=ParseBudget(_WALKED_PARSED),
        script_budget=ParseBudget(_WALKED_SCRIPTS),
        import_limit=_WALKED_IMPORTS,
        lookup_limit=_WALKED_LOOKUPS,
        stopped=stopped,
    )
    if stopped and stopped[0]:
        logger.warning(
            "the save's import walk for %s stopped at its limits; %d files reached",
            subfolder,
            len(found),
        )
    return found


def code_the_agent_reaches(source_root: Path, subfolder: str) -> list[Path]:
    """The files outside the agent's folder that its code runs: the files directly above it,
    less test helpers, and everything its imports lead to, as the credential scan reads them
    (test-named and unpacked packages included, which the browser-tool scan leaves out). It runs
    that scan; the save reuses the scan it already ran.

    Raises ``SubfolderNotFound`` for a folder that can't be used.
    """
    reached: list[Path] = []
    browser_tools_in_agent(source_root, subfolder, reached)
    return reached


def browser_tools_in_agent(
    source_root: Path, subfolder: str, reached: list[Path] | None = None
) -> list[str]:
    """What one agent of a repository uses: its folder, what it imports, and the files directly
    in the folders above it. A sibling's browser tool says nothing about this agent.

    Given ``reached``, it's filled with the files outside the folder the agent reaches: those
    above it, and every file its imports lead to, filtered or not, so another scan can read them
    without walking the imports again.

    Raises ``SubfolderNotFound`` for a folder that can't be used.
    """
    root = Path(source_root).resolve()
    folder = agent_folder(root, subfolder)
    above = [
        path
        for parent in folder.parents
        if is_within(parent, root)
        for path in _files_in(parent)
        if not _not_the_agent_file(path.name)
    ]
    # An import the agent wrote says this code is its own, whatever the folder is called, unless
    # it's a test helper or someone else's package, unpacked beside the agent or above it. One
    # record budget for every folder, each folder's records read once, and rows kept as pip
    # wrote them, relative to that folder: a Path for each row cost ten times as much, and a
    # budget for each folder let a deep folder read many times the limit.
    left = [_RECORDS_AT_AND_ABOVE]
    # Nearest first.
    rows_of = {
        parent: _unpacked_at_top(parent, left)
        for parent in (folder, *(parent for parent in folder.parents if is_within(parent, root)))
    }
    holding = [(parent, rows) for parent, rows in rows_of.items() if rows]

    def unpacked(path: Path) -> bool:
        # The nearest few folders holding the file, as _agent_files checks: a crafted chain of
        # them would cost its depth for each of thousands of files.
        nearest = 0
        for parent, rows in holding:
            if is_within(path, parent):
                below = path.parts[len(parent.parts) :]
                # A record names files, so the file's own row is the one to find: one join per
                # folder, where every prefix cost a join each.
                if "/".join(below) in rows:
                    return True
                nearest += 1
                if nearest == _NEAREST_RECORD_FOLDERS:
                    break
        return False

    walked = sorted(_walked(root, subfolder))
    shared = [
        path
        for path in walked
        if not _not_the_agent_file(path.name)
        and not unpacked(path)
        # Only the folders the agent reaches it through: a whole repository under `e2e/` is
        # still the agent's own code.
        and not any(_TEST_FOLDER.fullmatch(part.lower()) for part in _below_shared(path, folder))
    ]
    local = _modules_at_top(folder, rows_of[folder]) | _modules_at_top(root, rows_of[root])
    if left[0] < 0:
        # Records left unread can't say a package there is installed, not the customer's, and no
        # read order keeps both the folder's and the root's whole: so every import counts, as in
        # the scan with no folder. A browser one is found, which leads to a second look at the
        # model's reading, not a refusal.
        local = set()
    if reached is not None:
        # Everything the walk followed, as the credential scan reads it: a test-named package the
        # agent imports still runs (Dileep's third review). The files above stay filtered: a root
        # conftest.py isn't the agent's code.
        reached[:] = [*above, *walked]
    # A shared script's dependencies are declared in its package, at or above it.
    manifests = [
        parent / "package.json"
        for path in shared
        if path.suffix != ".py"
        for parent in path.parents
        if is_within(parent, root) and (parent / "package.json").is_file()
    ]
    return list(
        _tools_in(
            dict.fromkeys((*_agent_files(folder), *above, *shared, *manifests)),
            local,
            folder,
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
    """What the model read against what the code check found. A browser reading always gets one
    more look first, and so does a reading the check disagrees with; the answer then stands."""
    if is_browser(modality_text(claimed)):
        # Never at once: what was found can be a dev dependency or a helper script of a chat
        # agent, and a refusal can't be retried.
        return "refuse" if looked_again else "second_look"
    return "second_look" if found and not looked_again else "keep"


SECOND_LOOK_READ = "second_look_read"


class SecondLook:
    """The second looks of one reading, across its submissions.

    The model gets one more look before any refusal, and wherever it and the code check disagree;
    its next answer stands. Each look gives a value to send back: calls in one model message run before the model
    reads any answer, together or one after another, so a call holding the value is the only one
    known to have read it.
    """

    def __init__(self) -> None:
        self._receipts: set[str] = set()
        # Whether the latest look has had no value back yet. A model that never sends one back
        # must still be able to answer, or every submission earns another look until the turns run
        # out.
        self._unanswered = False

    @property
    def given(self) -> bool:
        """Whether a look has gone out. A call takes this as it starts, before waiting on the
        scan: one already running when a look went out hasn't read it."""
        return bool(self._receipts)

    def verdict(
        self, claimed: str, receipt: Any, found: Sequence[str], *, given_before: bool
    ) -> Literal["refuse", "second_look", "keep"]:
        # A call holding a value read a look, so its answer stands. Without one, the call after a
        # look that no value came back for is taken as the answer. Once a value came back, a call
        # without one that reads the same stands too; one that reads otherwise may have run beside
        # that answer unread, so it gets a look of its own. A value that came back on a browser
        # answer was refused and stopped every later call, so the answer that came back read chat.
        read_it = isinstance(receipt, str) and receipt in self._receipts
        if read_it:
            self._unanswered = False
        same_answer = not is_browser(claimed)
        return decide(
            claimed,
            found,
            looked_again=read_it or (given_before and (self._unanswered or same_answer)),
        )

    def issue(self, claimed: str, found: Sequence[str], receipt: str) -> str:
        """The look's text, with ``receipt`` as the value to send back."""
        self._receipts.add(receipt)
        self._unanswered = True
        return (
            f"{self.prompt(claimed, found)} When you submit again, set "
            f'{SECOND_LOOK_READ} to "{receipt}", which shows you read this.'
        )

    @staticmethod
    def prompt(claimed: str, found: Sequence[str]) -> str:
        if is_browser(claimed) and found:
            return (
                "second-look: you recorded modality browser, which stops this build as not "
                f"supported, and the source uses {', '.join(found)}. A chat or voice agent can "
                "list one for its tests or use one in a helper script. Does a person come to this "
                "agent for a conversation, or to have it carry out work on websites or a computer? "
                "For a conversation, submit again as chat or voice. If its main job is that work, "
                "submit again as browser. Decide this from the source; don't ask anyone."
            )
        if is_browser(claimed):
            return (
                "second-look: you recorded modality browser, which stops this build as not "
                "supported, and no browser, crawling or computer-use tool was found in the "
                "source. Does a person come to this agent for a conversation, or to have it carry "
                "out work on websites or a computer? For a conversation, submit again as chat or "
                "voice. If you're sure, submit again as browser. Decide this from the source; "
                "don't ask anyone."
            )
        return (
            f"second-look: you recorded modality {claimed or 'chat'}, and the source uses "
            f"{', '.join(found)}. If that's a helper and a person comes to this agent for a "
            "conversation, submit again as is. If its main job is carrying out work on websites "
            "or a computer, submit again as browser, which stops this build as not supported. "
            "Decide this from the source; don't ask anyone."
        )


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
