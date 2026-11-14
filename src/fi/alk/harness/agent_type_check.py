"""Does an agent use a browser, crawling or computer-use tool, and what follows from that.

No code can say for certain what an agent's main job is, so this is one of two opinions: what the
source uses, set against what the model read. It calls no model and runs none of the agent's code,
and it needs no job or session, so a reading can be checked without a build.
"""

from __future__ import annotations

import ast
import codecs
import csv
import fnmatch
import io
import json
import logging
import os
import re
import tokenize
from collections.abc import Iterator, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from .failure_file import FAILURE_FILE
from .instrumentation_evidence import _SKIPPED_DIRECTORIES, _imports
from .job import FailureDomain, HarnessFailure, HarnessStage

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

_TOOLKIT = "PlayWrightBrowserToolkit"
_TEST_FILES = ("test_*.py", "*_test.py", "conftest.py", "*.test.*", "*.spec.*")
_SCRIPT_SUFFIXES = frozenset({".js", ".ts", ".mjs", ".cjs", ".tsx", ".jsx"})
_CODE_SUFFIXES = _SCRIPT_SUFFIXES | {".py"}
_LARGEST_CODE_FILE = 1024 * 1024
_UNREAD_CODECS = frozenset({"idna", "punycode", "undefined"})
# Past this a repository holds the conversation up for longer than the rest of the scan is worth.
_LARGEST_SCAN = 128 * 1024 * 1024
_LARGEST_RECORD = 1024 * 1024
# Per scan, each record read once; a reopen of unchanged files reads none. Each row is parsed and
# kept, so short rows cost the most: a scan through 16 MiB of 7-byte rows takes 0.9 s. A whole
# 411-package environment unpacked in one folder came to 6.4 MiB.
_LARGEST_RECORDS = 16 * 1024 * 1024
# What each record costs at least, a disk block: an empty one still costs its folder and its
# look-ups, so the budget bounds how many are opened too.
_SMALLEST_RECORD = 4 * 1024
_NEAREST_RECORD_FOLDERS = 4
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


def _unread_codec(source: bytes) -> bool:
    """Whether the coding line names a codec the scan won't decode ``source`` in.

    A codec such as rot13 isn't a text encoding, so it can't be read as source. Three text codecs
    are refused by name: punycode's decode costs the square of its size, about 2 s for 128 KB;
    idna's runs in Python for each label; undefined decodes nothing. Python runs an idna file, so
    the readers then look for the names in its bytes instead (``_named_raw``). unicode-escape
    decodes in linear time and its escapes can spell a name, so it is decoded like any other.
    """
    try:
        # A non-ASCII byte on the coding line makes detection fail while the parser still decodes
        # the file in that codec, so it is read here as ASCII: only the codec's name matters.
        encoding, _ = tokenize.detect_encoding(io.BytesIO(re.sub(rb"[\x80-\xff]", b"?", source)).readline)
        info = codecs.lookup(encoding)
    except (SyntaxError, UnicodeError, LookupError):
        # The reader meets the same error and refuses the file itself.
        return False
    return info.name in _UNREAD_CODECS or not getattr(info, "_is_text_encoding", True)


def _lines(source: bytes) -> bytes:
    # Python reads a lone CR as a line end, and detect_encoding and the tokenizer don't: to them a
    # file of only CRs is one line, so a "coding:" anywhere after a first "#" would be its cookie.
    return source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _named_raw(source: bytes) -> tuple[set[str], set[str]]:
    """The browser imports and tool markers whose bytes appear in ``source``, before any decode.

    For a file in a refused codec: its names are counted rather than lost, which can only bring a
    second look, never a refusal. One search over bytes already read.
    """
    # Whole names only: "steelers" in a string is no import of steel.
    imports = {
        name
        for name in BROWSER_IMPORTS
        if re.search(rb"(?<![\w])" + re.escape(name.encode()) + rb"(?![\w])", source)
    }
    text = source.decode("latin-1")
    markers = {
        "Playwright MCP" if "playwright" in hit.group() else "computer-use tool"
        for hit in TOOL_MARKER.finditer(text)
    }
    if _TOOLKIT in text:
        markers.add(_TOOLKIT)
    return imports, markers


def _imports_in(path: Path) -> set[str]:
    # One file at a time: a customer's odd file can run the parser out of memory, and that mustn't
    # take the rest of the scan, or the reading it serves, down with it.
    try:
        if not path.is_file() or path.stat().st_size > _LARGEST_CODE_FILE:
            return set()
        source = path.read_bytes()
        if _unread_codec(_lines(source)):
            return _named_raw(source)[0]
        # From bytes, so a BOM or a coding line is honoured as Python itself would.
        return _imports(ast.parse(source))
    except (OSError, ValueError, SyntaxError, RecursionError, MemoryError):
        logger.debug("agent type scan read no imports from %s", path, exc_info=True)
        return set()


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


def _python_code(source: bytes) -> str:
    # From bytes, as imports are read, so a coding line or a BOM is honoured as Python itself would,
    # and on the same lines Python reads. The caller has refused a codec it won't decode.
    return " ".join(
        token.string
        for token in tokenize.tokenize(io.BytesIO(_lines(source)).readline)
        if token.type not in (tokenize.COMMENT, tokenize.ENCODING)
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
        if path.suffix == ".py":
            source = path.read_bytes()
            if _unread_codec(_lines(source)):
                return _named_raw(source)[1]
            code = _python_code(source)
        else:
            # As Node reads it: a stray byte that isn't UTF-8 becomes a replacement character.
            text = path.read_text(encoding="utf-8", errors="replace")
            code = _SCRIPT_COMMENT_OR_STRING.sub(_script_kept, text)
    except (OSError, ValueError, SyntaxError, tokenize.TokenError):
        logger.debug("agent type scan read no markers from %s", path, exc_info=True)
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
    found: set[str] = set()
    imported: set[str] = set()
    scanned = 0
    left = [_LARGEST_RECORDS]
    at_top: set[str] = set()
    for path in sorted(filter(_is_read, _agent_files(root, left, at_top)), key=_reading_order):
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
            # A skipped file may be the agent's main one, and the reading then finds nothing.
            logger.warning("agent type scan skipped %s", path, exc_info=True)
            continue
    local = _modules_at_top(root, at_top)
    if left[0] < 0:
        # The record the budget left unread may be the one naming a package unpacked at the top,
        # so no folder there shows the agent wrote it: every import counts as the agent's.
        local = set()
    for name in imported:
        top = name.split(".")[0]
        if top in local:
            continue
        if top in BROWSER_IMPORTS:
            found.add(top)
        elif name.endswith(f".{_TOOLKIT}"):
            found.add(_TOOLKIT)
    return tuple(sorted({name.replace("_", "-") for name in found}))


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
