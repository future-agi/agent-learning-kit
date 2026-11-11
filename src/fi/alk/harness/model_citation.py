"""Reading the model an agent talks to the person with, and the prompt it is given, from the
agent's own files, while a conversation waits on the answer.

A model is kept only when its name is the value written on the line the reader cites: the
Agents page shows it as fact, and an unknown model is better left empty than guessed. The files
can be built to stall whoever reads them, so every path is resolved within the save's one lookup
budget, every read is bounded in size and decoded as UTF-8 whatever coding line the file carries,
and every check of a line costs at most a few passes over it.
"""

from __future__ import annotations

import functools
import html
import logging
import os
import re
import stat
import string
import unicodedata
from pathlib import Path

from .contract import AgentContract
from .runtime_selection import charged_path
from .source_folder import is_within

_LOG = logging.getLogger(__name__)
# The platform refuses the whole agent document past this, for the model's name and provider.
LONGEST_MODEL_FIELD = 255
# One path as written: charged_path doesn't refuse a long path itself.
LONGEST_MODEL_PATH = 4096
# The cited file, as for a prompt file.
LARGEST_MODEL_FILE = 1024 * 1024
# What a model name is made of. A match must have none of these right before or after it, so
# "gpt-4o" is not found inside "gpt-4o-mini".
MODEL_NAME_CHARS = frozenset(string.ascii_letters + string.digits + "._:/@-")
# Files whose values are written bare after a key: `model: gpt-4o`, `MODEL=gpt-4o`,
# `ENV MODEL=gpt-4o`, `export MODEL=gpt-4o`.
CONFIG_SUFFIXES = frozenset(
    {
        ".yaml", ".yml", ".toml", ".json", ".ini", ".cfg", ".conf", ".env", ".properties",
        ".sh", ".bash", ".zsh", ".dockerfile", ".mk", ".prompt",
    }
)
_CONFIG_NAMES = frozenset({"makefile", "gnumakefile", ".flaskenv"})
# Languages where # isn't a comment: C and C++ (#define), JavaScript and TypeScript (#field),
# Java, Go, Rust (#[attr]), Swift (#if), Kotlin, C#, Scala (A#B), Dart, Objective-C, Groovy,
# Gradle, Lua (#t is a length).
_NO_HASH_COMMENT_SUFFIXES = frozenset(
    {
        ".c", ".h", ".cc", ".cpp", ".cxx", ".hpp", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
        ".java", ".go", ".rs", ".swift", ".kt", ".kts", ".cs", ".scala", ".dart", ".m", ".mm",
        ".groovy", ".gradle", ".lua", ".mts", ".cts", ".sc",
    }
)
# Files where # starts a comment only at a line's start or after a space: YAML, shell, Makefile
# and Dockerfile (a#b is a word there), and .env files.
_SPACED_HASH_SUFFIXES = frozenset(
    {".yaml", ".yml", ".sh", ".bash", ".zsh", ".mk", ".dockerfile", ".env"}
)
# Files with no block comments, nor //: there `* ` starts no comment (a Python continuation
# line), and /* and // are text (ls /*, a // 2).
_NO_BLOCK_COMMENT_SUFFIXES = frozenset(
    {".py", ".pyi", ".pyw", ".rb", ".pl", ".r", ".jl", ".ex", ".exs", ".nim", ".lua"}
)
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
# Markup: only there is <!-- a comment that runs on over lines. Elsewhere it is text (a regex,
# a YAML value, a heredoc), and a comment only to its line's end.
_MARKUP_SUFFIXES = frozenset({".html", ".htm", ".xhtml", ".xml", ".svg", ".vue", ".svelte"})
# INI files: a line starting with ; is a comment.
_INI_SUFFIXES = frozenset({".ini", ".cfg", ".conf"})
# Files where -- starts a comment: SQL, Lua, Haskell.
_DASH_COMMENT_SUFFIXES = frozenset({".sql", ".lua", ".hs"})
# Files written for people: they say which model is used; they don't set it.
# And prompt templates (.j2, .hbs): their text is the agent's prompt, which may name a model
# without setting it.
PROSE_SUFFIXES = frozenset(
    {
        ".md", ".markdown", ".mdx", ".mdc", ".rst", ".txt", ".adoc", ".org", ".j2", ".jinja",
        ".jinja2", ".hbs", ".mustache",
    }
)
# Files written for people, by name with no suffix: README, CHANGELOG, LICENSE, CONTRIBUTING
# (README.md is prose by its suffix; readme.py is code).
_PROSE_STEMS = frozenset({"readme", "changelog", "license", "contributing"})
# Words a setting is named with, never a model: `cfg.get("model")`, OPENAI_MODEL, llm.model.
# Not "deployment": an Azure deployment's own name often has it (gpt-4o_deployment).
_SETTING_WORDS = frozenset({"model", "llm", "name", "default", "engine", "provider", "key", "env", "id"})
_QUOTES = frozenset("\"'`")
_SETTING_NAME_CHARS = frozenset(string.ascii_uppercase + string.digits + "_")
_SETTING_PARTS = re.compile(r"[_\-.:/@]")
# An escape in a string: \uXXXX and \xXX (as json.dump writes any non-ASCII character),
# \UXXXXXXXX, \N{NAME} and octal.
_CHAR_ESCAPE = re.compile(
    r"\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|U([0-9a-fA-F]{8})|N\{([A-Za-z0-9 \-]{1,88})\}"
    r"|([0-7]{1,3}))"
)
_NOT_A_LETTER = re.compile(r"[\W_]+")
_SURROGATE = re.compile("[\ud800-\udfff]")
# Full-width letters and digits (３, Ａ) as their ASCII forms: what NFKC would do for them,
# without NFKC's growth elsewhere.
_FULL_WIDTH = {code: code - 0xFEE0 for code in range(0xFF01, 0xFF5F)}
# Scripts written without spaces between words (Thai, Lao, Burmese, Khmer, Japanese kana,
# Chinese and Japanese ideographs): each character is a word, so the gap below applies between
# characters there too.
_UNSPACED = "\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
_UNSPACED_CHAR = re.compile(f"[{_UNSPACED}]")
# Only a letter of those scripts is a word: their vowel signs and tone marks, and ・, are not.
_WORD = re.compile(f"(?![\\W_])[{_UNSPACED}]|[^\\W_{_UNSPACED}]+")
# A quote of fewer letters than this says nothing to check lines against ("n/a", "TBD", "see
# lines"): a few short words are found in almost any lines. A character of an unspaced script
# counts as three.
_SHORTEST_QUOTE = 16
# Fewer letters than this and a quote says nothing at all ("n/a", "TBD", "none"): it isn't held
# and isn't checked.
_SAYS_NOTHING = 6
# Languages with triple-quoted strings ("""...""" or '''...'''): Python, TOML, Kotlin, Scala,
# Swift, Groovy and Gradle, Java and C# text blocks, Julia, Dart, Elixir.
_TRIPLE_SUFFIXES = frozenset(
    {
        ".py", ".pyi", ".pyw", ".toml", ".kt", ".kts", ".scala", ".sc", ".swift", ".groovy",
        ".gradle", ".java", ".cs", ".jl", ".dart", ".ex", ".exs",
    }
)
# Where a quote leaves text out: ... or …, also inside brackets, since only letters are compared.
_ELISION = re.compile(r"\.{3,}|…")
# How many letters of code may sit between two words of a quote in the lines: a string prefix
# (f"), a short comment, `+ NAME +`, a markup tag or a placeholder the quote leaves out.
_WORD_GAP = 24


# How # starts a comment: anywhere (Python, shell, Ruby, TOML...), only at a line's start or
# after a space (YAML, where `url: http://x/#top` and `{a: 1,#b}` are values), or not at all.
_HASH_ANYWHERE, _HASH_SPACED, _HASH_NONE = "anywhere", "spaced", "none"


@functools.cache
def _comment_tokens(
    *, dashes: bool, triple: bool, hash_comments: str, block_comments: bool
) -> re.Pattern[str]:
    """What the line scan stops at: a triple quote (in languages that have them), a
    double-quoted or backticked string, matched whole (to the line's end if it isn't closed); an
    apostrophe, which the scan pairs itself; or a comment marker. # not in $# (a shell count),
    &# (an HTML character) or ${# (a shell length)."""
    alternatives = ['"""', "'''"] if triple else []
    alternatives += [r'"(?:[^"\\]|\\.)*"?', r"`(?:[^`\\]|\\.)*`?", "'"]
    if hash_comments == _HASH_ANYWHERE:
        alternatives.append(r"(?<!\$\{)(?<![$&])#")
    elif hash_comments == _HASH_SPACED:
        alternatives.append(r"(?<![^ \t])#")
    if block_comments:
        # // and /* only where block comments are: not in Python (a // 2), shell or YAML. // not
        # in a URL (http://); /* not in a glob (data/*).
        alternatives += [r"(?<!:)//", r"(?<![\w.*/])/\*"]
    alternatives.append("<!--")
    if dashes:
        # In SQL, Lua and Haskell, -- starts a comment wherever it is.
        alternatives.append("--")
    return re.compile("|".join(alternatives))


# The rest of a string an apostrophe opens, to the apostrophe that closes it.
_REST_OF_SINGLE = re.compile(r"(?:[^'\\]|\\.)*'")
# Python's string prefixes: an apostrophe right after one of these opens a string.
_STRING_PREFIXES = frozenset({"r", "b", "f", "u", "t", "rb", "br", "fr", "rf", "tr", "rt"})


def _opens_a_string(line: str, at: int) -> bool:
    """Whether the apostrophe at ``at`` can open a string: not inside a word (Pair's, it's, x'
    in Haskell) and not after a backslash (it\\'s), unless the word is a string prefix (r'...',
    rb'...'). Looks at three characters at most: a prefix is one or two letters, so three word
    characters before the apostrophe (letters, digits, _) are a word, whatever comes before."""
    start = at
    while start > 0 and at - start < 3 and (line[start - 1].isalnum() or line[start - 1] == "_"):
        start -= 1
    if start == at:
        return at == 0 or line[at - 1] != "\\"
    return line[start:at].lower() in _STRING_PREFIXES


# What closes a block comment left open at a line's end.
_COMMENT_CLOSERS = frozenset({"*/", "-->"})


def _line_scan(
    line: str,
    block: str | None,
    *,
    dashes: bool = False,
    ini: bool = False,
    triple: bool = False,
    hash_comments: str = _HASH_ANYWHERE,
    block_comments: bool = True,
    markup: bool = False,
) -> tuple[int, list[tuple[int, int]], str | None]:
    """One pass over a line: where its comment starts (or its length), the stretches of it that
    are text (inside a triple-quoted string, or a block comment closed on the line), and the
    block still open at its end (a triple quote, or the */ or --> a comment ends at; --> only in
    ``markup``, where <!-- runs on over lines). ``block`` is the one open where the line starts. A line is a comment when it is the `* ` middle of a
    block comment or, in INI, starts with ;. Strings are passed over whole, so a marker or a
    triple quote inside one doesn't count; an apostrophe opens a string only when another closes
    it on the line."""
    inside: list[tuple[int, int]] = []
    at = 0
    if block is not None:
        close = line.find(block)
        if block in _COMMENT_CLOSERS:
            if close < 0:
                return 0, inside, block
            # From the line's start, so the stretch can never be a quoted value.
            inside.append((0, close + len(block)))
        elif close < 0:
            return len(line), [(0, len(line))], block
        else:
            inside.append((0, close))
        at = close + len(block)
    else:
        stripped = line.lstrip()
        if (block_comments and stripped.startswith(("* ", "*\t"))) or (
            ini and stripped.startswith(";")
        ):
            return 0, inside, None
    tokens = _comment_tokens(
        dashes=dashes, triple=triple, hash_comments=hash_comments, block_comments=block_comments
    )
    while (found := tokens.search(line, at)) is not None:
        token, start = found.group(), found.start()
        at = found.end()
        if token in ('"""', "'''"):
            close = line.find(token, at)
            if close < 0:
                inside.append((at, len(line)))
                return len(line), inside, token
            inside.append((at, close))
            at = close + 3
        elif token == "'":
            if _opens_a_string(line, start):
                # Unclosed, it is an apostrophe: every later one is then after a backslash, so
                # none of them opens a string either, and the line is read about once.
                closed = _REST_OF_SINGLE.match(line, at)
                if closed is not None:
                    at = closed.end()
        elif token in ("/*", "<!--"):
            # A block comment closed on the line is passed over: `/* x */ model = "gpt-4o"`.
            closer = "*/" if token == "/*" else "-->"
            close = line.find(closer, at)
            if close < 0:
                if token == "<!--" and not markup:
                    # Outside markup, <!-- is a comment only to the line's end.
                    return start, inside, None
                # The comment runs on to the line that closes it.
                return start, inside, closer
            # From the marker itself, so the stretch can never be a quoted value.
            inside.append((start, close))
            at = close + len(closer)
        elif token[0] not in "\"`":
            return start, inside, None
    return len(line), inside, None


def block_open_before(
    lines: list[str],
    before: int,
    *,
    triple: bool = False,
    dashes: bool = False,
    ini: bool = False,
    hash_comments: str = _HASH_ANYWHERE,
    block_comments: bool = True,
    markup: bool = False,
) -> str | None:
    """The block open where ``lines[before]`` starts (a triple quote, or the */ or --> of a
    block comment), the lines before it read with the same scan as the cited one. A comment is
    carried to later lines only when its closer is on a later line of the file: one that never
    closes doesn't compile, so its opener is text (a regex, a template). A line that could open
    none, outside a block, is passed over without a scan; inside one, only its closer is looked
    for."""
    openers = ['"""', "'''"] if triple else []
    if block_comments:
        openers.append("/*")
    if markup:
        openers.append("<!--")
    # The last line each comment closer is on, looked for once, from the file's end.
    last_closer: dict[str, int] = {}
    block: str | None = None
    for at in range(before):
        line = lines[at]
        if block is None and not any(opener in line for opener in openers):
            continue
        if block is not None and block not in line:
            continue
        _, _, block = _line_scan(
            line,
            block,
            dashes=dashes,
            ini=ini,
            triple=triple,
            hash_comments=hash_comments,
            block_comments=block_comments,
            markup=markup,
        )
        if block in _COMMENT_CLOSERS:
            if block not in last_closer:
                last_closer[block] = next(
                    (i for i in range(len(lines) - 1, -1, -1) if block in lines[i]), -1
                )
            if last_closer[block] <= at:
                block = None
    return block


def resolved_in_source(root: Path, path: str) -> Path | None:
    """``root / path`` resolved, each lookup charged to the save's budget, or None: outside
    ``root``, a link loop, a NUL, a path the system refuses. ``root`` is already resolved.
    PathLookupsSpent is not caught here: past the budget the save answers with its own message."""
    try:
        resolved = charged_path(root / path)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if is_within(resolved, root) else None


_OPEN_FLAGS = (
    os.O_RDONLY
    # A pipe or a device put at the path after it was resolved is opened without waiting on it,
    # then refused below; a link put in the path's last part since isn't followed.
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)


def read_source(path: Path, limit: int) -> bytes | None:
    """At most ``limit + 1`` bytes of the file, or None: not a regular file, or not readable.
    ``path`` is already resolved. The caller judges the size. A file that can't be read is
    logged by the error's kind alone: its path and content are the customer's."""
    try:
        descriptor = os.open(path, _OPEN_FLAGS)
    except (OSError, ValueError) as failed:
        _LOG.info("source file not read (%s)", type(failed).__name__)
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _LOG.info("source file not read (not a regular file)")
            return None
        chunks: list[bytes] = []
        held = 0
        # A read can return less than asked for; what counts is limit + 1 or the end.
        while held <= limit:
            chunk = os.read(descriptor, limit + 1 - held)
            if not chunk:
                break
            chunks.append(chunk)
            held += len(chunk)
        return b"".join(chunks)
    except OSError as failed:
        _LOG.info("source file not read (%s)", type(failed).__name__)
        return None
    finally:
        os.close(descriptor)


def source_lines(raw: bytes) -> list[str]:
    """The file's lines, whatever coding line it carries: no codec the file names gets to run
    on it, and one file has one numbering wherever it is read. Numbered as the reading's Read and
    Grep number them: at each \\n, with a \\r before it dropped. Not at the other characters
    str.splitlines breaks at (a form feed, a lone \\r, U+2028), which would move every line
    after them."""
    lines = raw.decode("utf-8", errors="replace").split("\n")
    if lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def _bare_after_key(line: str, at: int, *, yaml: bool) -> bool:
    """Whether the text before ``at`` ends, past spaces, in ``:`` or ``=`` (or ``-``, a YAML list
    item). Steps back over the spaces only: copying the line before every occurrence would cost
    the square of its length."""
    before = at - 1
    while before >= 0 and line[before] in " \t":
        before -= 1
    return before >= 0 and (line[before] in ":=" or (yaml and line[before] == "-"))


def _is_a_key(line: str, at: int, end: int) -> bool:
    """Whether a quoted occurrence from ``at`` to ``end`` is a key: its closing quote is followed,
    past spaces, by ``:``, as in `{"llm_model": "gpt-4o"}`, and its opening quote doesn't follow a
    ``?``, as the first value of `prod ? "gpt-4o" : "gpt-4o-mini"` does."""
    before = at - 2
    while before >= 0 and line[before] in " \t":
        before -= 1
    if before >= 0 and line[before] == "?":
        return False
    after = end + 1
    while after < len(line) and line[after] in " \t":
        after += 1
    return after < len(line) and line[after] == ":"


# Calls that look a setting up by its name: get, os.getenv, getOrThrow, getProperty, env.str.
# str only as a method (env.str): str("gpt-4o") is a value.
_METHOD_ONLY_CALLS = frozenset({"str"})
_LOOKUP_CALLS = (
    "getenv", "get", "getOrThrow", "getOrDefault", "getProperty", "getString", "getValue", "str",
    "env", "config",
)
_NAME_END = re.compile(r"[A-Za-z0-9_]")


def _is_a_lookup(line: str, at: int) -> bool:
    """Whether a quoted occurrence starting at ``at`` names what is looked up rather than the
    value: right after a subscript (`config["model-name"]`) or as the first argument of a lookup
    call (`cfg.get("llm-model")`, `os.getenv ("model-name")`, `config.get<string>("m-x")`). A
    first argument of any other call is a value: `GenerativeModel("gemini-pro")`. Looks at a few
    characters only."""
    before = at - 2
    while before >= 0 and line[before] in " \t":
        before -= 1
    if before < 1:
        return False
    if line[before] == "[":
        # A subscript follows a name or a closing bracket; a list literal doesn't.
        return line[before - 1].isalnum() or line[before - 1] in "_)]."
    if line[before] != "(":
        return False
    end = before - 1
    while end >= 0 and line[end] in " \t":
        end -= 1
    if end >= 0 and line[end] == ">":
        # A generic type, as in get<string>(: step back over it, a few characters at most.
        opened = line.rfind("<", max(0, end - 40), end)
        if opened < 0:
            return False
        end = opened - 1
    for call in _LOOKUP_CALLS:
        start = end + 1 - len(call)
        if start >= 0 and line[start : end + 1] == call:
            if call in _METHOD_ONLY_CALLS:
                return start > 0 and line[start - 1] == "."
            return start == 0 or not _NAME_END.match(line[start - 1])
    return False


def named_on(
    line: str,
    name: str,
    *,
    config: bool,
    yaml: bool = False,
    dashes: bool = False,
    ini: bool = False,
    triple: bool = False,
    hash_comments: str = _HASH_ANYWHERE,
    block_comments: bool = True,
    block: str | None = None,
) -> bool:
    """Whether ``name`` is the value written on ``line``: whole, outside a comment and outside
    a triple-quoted string or a block comment (``block`` is the triple quote, or the */ or -->,
    open where the line starts), and
    between matching quotes (not a key, not a lookup), or in a configuration file written bare
    after ``:`` or ``=`` or as a shell default, ``${MODEL:-gpt-4o}``. ``name`` is made only of
    MODEL_NAME_CHARS, so an occurrence starting inside one that failed has a name character just
    before it, and the search resumes after the failed one. The line is scanned once, its
    triple-quoted stretches walked alongside the occurrences, and each step looks at a few
    characters beside one, so a line costs about one pass."""
    if not name:
        # find("") matches at every position without moving on.
        return False
    comment, inside, _ = _line_scan(
        line,
        block,
        dashes=dashes,
        ini=ini,
        triple=triple,
        hash_comments=hash_comments,
        block_comments=block_comments,
    )
    stretches = iter(inside)
    stretch = next(stretches, None)
    at = line.find(name, 0, comment)
    while at >= 0:
        end = at + len(name)
        while stretch is not None and stretch[1] <= at:
            stretch = next(stretches, None)
        before = line[at - 1] if at else ""
        after = line[end] if end < len(line) else ""
        # Inside a triple-quoted string or a block comment the line is text, unless the string is
        # the value: MODEL = """gpt-4o""".
        inside_text = stretch is not None and stretch[0] <= at and stretch != (at, end)
        if not inside_text and after not in MODEL_NAME_CHARS:
            if before not in MODEL_NAME_CHARS:
                if before and before in _QUOTES and after == before:
                    if not _is_a_key(line, at, end) and not _is_a_lookup(line, at):
                        return True
                elif config and _bare_after_key(line, at, yaml=yaml):
                    return True
            elif config and after == "}" and line[max(0, at - 2) : at] == ":-":
                # The default of a shell variable: ${MODEL:-gpt-4o}.
                return True
        at = line.find(name, end, comment)
    return False


# Prompt and page templates: what they hold is text, unless the name says they render a
# configuration file (config.yaml.j2), which is then read as that file.
_TEMPLATE_SUFFIXES = frozenset({".j2", ".jinja", ".jinja2", ".hbs", ".mustache"})


def kind_suffix(path: Path) -> str:
    """The suffix that says what a file is: config.yaml.j2 is YAML."""
    suffix = path.suffix.lower()
    if suffix in _TEMPLATE_SUFFIXES:
        inner = Path(path.stem).suffix.lower()
        # Not .prompt: system.prompt.j2 is a Jinja prompt, not a Dotprompt file.
        if inner in CONFIG_SUFFIXES and inner != ".prompt":
            return inner
    return suffix


def is_config_file(path: Path) -> bool:
    name = path.name.lower()
    suffix = kind_suffix(path)
    # env.py and dockerfile_gen.py are code, whatever their names start with.
    code = suffix in _TRIPLE_SUFFIXES | _NO_HASH_COMMENT_SUFFIXES | _NO_BLOCK_COMMENT_SUFFIXES
    return (
        suffix in CONFIG_SUFFIXES
        or name in _CONFIG_NAMES
        or (not code and name.startswith((".env", "env.", "dockerfile")))
    )


def _names_configuration(name: str) -> bool:
    """A file name that is configuration by itself: Makefile, .env, .env.local, Dockerfile,
    Dockerfile.dev."""
    return (
        name in _CONFIG_NAMES
        or name in (".env", "dockerfile")
        or name.startswith((".env.", "dockerfile."))
    )


def is_prose_file(path: Path) -> bool:
    if path.suffix.lower() in _TEMPLATE_SUFFIXES and _names_configuration(Path(path.stem).name.lower()):
        # A template that renders configuration by its name: .env.j2, Dockerfile.j2 (not
        # dockerfile_writer.j2 or env.system.j2, which are prompts).
        return False
    return kind_suffix(path) in PROSE_SUFFIXES or (
        not path.suffix and path.name.lower() in _PROSE_STEMS
    )


def _reads_as_a_setting(name: str) -> bool:
    """A name shaped like where a model is read from rather than which: OPENAI_MODEL,
    openai_model, model-name, llm.model. Model and deployment ids such as GPT4O, O1,
    mistral_large, gpt4o-deployment and gemini-1.5-pro are not."""
    # An environment variable: capitals, digits and underscores, with an underscore.
    if "_" in name and _SETTING_NAME_CHARS.issuperset(name):
        return True
    has_digit = any(char.isdigit() for char in name)
    parts = _SETTING_PARTS.split(name.casefold())
    # A setting's words, alone, in snake case or without a version number: model, llm_model,
    # model-name.
    if ("_" in name or not has_digit) and _SETTING_WORDS.intersection(parts):
        return True
    # A dotted key with no version in it: services.openai.model, llm.provider.
    return "." in name and not has_digit


def model_problems(contract: AgentContract, source_root: Path) -> list[str]:
    """Problems with contract.model, checked against the cited line. On success, strips name and
    provider and stores path as the resolved file relative to the root, in POSIX form. Resolves
    one path (charged to the save's budget) and reads at most one file."""
    model = contract.model
    if model is None:
        return []
    name, provider, path = model.name.strip(), model.provider.strip(), model.path.strip()
    shown_name, shown_path = name[:100], path[:200]
    problems: list[str] = []
    if not name:
        problems.append(
            "model: name is empty; give the model's name as written in the source, or leave "
            "model out"
        )
    elif not MODEL_NAME_CHARS.issuperset(name):
        problems.append(
            "model: a model name is letters, digits and . _ : / @ -; write it as it appears in "
            "the source, or leave model out"
        )
    elif _reads_as_a_setting(name):
        # OPENAI_MODEL in os.getenv("OPENAI_MODEL") is where the model is read from, not which.
        problems.append(
            f"model: {shown_name} reads as the name of a setting, not a model; cite the line its "
            "value is written on, or leave model out"
        )
    if not MODEL_NAME_CHARS.issuperset(provider):
        problems.append(
            "model: a provider is letters, digits and . _ : / @ -; write it as the source names "
            "it, or leave it out"
        )
    if len(name) > LONGEST_MODEL_FIELD or len(provider) > LONGEST_MODEL_FIELD:
        problems.append("model: name and provider are at most 255 characters")
    if not path or model.line < 1:
        problems.append(
            "model: give path and line, the file and the line the model's name is written on"
        )
    if len(path) > LONGEST_MODEL_PATH:
        problems.append("model: path is too long")
    # All of these at once, and before the disk: a citation wrong in its shape costs no lookup.
    if problems:
        return problems
    root = source_root.resolve()
    resolved = resolved_in_source(root, path)
    if resolved is None:
        return [f"model: {shown_path} is not a file in the source"]
    if is_prose_file(resolved):
        # A README, a text file or a prompt template says which model is used; the code or its
        # configuration sets it.
        return [
            f"model: {shown_path} is text (for people, or a prompt), not code or configuration; "
            "cite the line in the agent's code or configuration the model is set on, or leave "
            "model out"
        ]
    raw = read_source(resolved, LARGEST_MODEL_FILE)
    if raw is None or len(raw) > LARGEST_MODEL_FILE:
        return [
            f"model: {shown_path} can't be read as a file of at most 1 MiB; check the path, and "
            "cite the line in the file the name is written in"
        ]
    lines = source_lines(raw)
    if model.line > len(lines):
        return [f"model: line {model.line} is outside {shown_path}, which has {len(lines)} lines"]
    suffix = kind_suffix(resolved)
    dashes, ini = suffix in _DASH_COMMENT_SUFFIXES, suffix in _INI_SUFFIXES
    triple = suffix in _TRIPLE_SUFFIXES
    config = is_config_file(resolved)
    name_lower = resolved.name.lower()
    if suffix in _NO_HASH_COMMENT_SUFFIXES:
        hash_comments = _HASH_NONE
    elif (
        suffix in _SPACED_HASH_SUFFIXES
        or name_lower in _CONFIG_NAMES
        # dockerfile_gen.py and .env_loader.py are Python: # anywhere there.
        or (config and name_lower.startswith((".env", "dockerfile")))
    ):
        hash_comments = _HASH_SPACED
    else:
        hash_comments = _HASH_ANYWHERE
    # Configuration files have no block comments, but JSON read as JSONC (appsettings.json,
    # tsconfig.json) has: outside a string, // and /* can only be comments there.
    block_comments = suffix == ".json" or not (config or suffix in _NO_BLOCK_COMMENT_SUFFIXES)
    if not named_on(
        lines[model.line - 1],
        name,
        config=config,
        yaml=suffix in _YAML_SUFFIXES,
        dashes=dashes,
        ini=ini,
        triple=triple,
        hash_comments=hash_comments,
        block_comments=block_comments,
        # A block open from an earlier line: a triple quote, /* where block comments are, <!--
        # in markup.
        block=block_open_before(
            lines,
            model.line - 1,
            triple=triple,
            dashes=dashes,
            ini=ini,
            hash_comments=hash_comments,
            block_comments=block_comments,
            markup=suffix in _MARKUP_SUFFIXES,
        ),
    ):
        return [
            f"model: the name {shown_name} is not the value written on line {model.line} of "
            f"{shown_path}; cite the line where it is written in quotes, or after : or = in a "
            "configuration file, or leave model out if the name is only set at run time"
        ]
    model.name, model.provider = name, provider
    # The file the path leads to, so a link inside the source is stored as its target.
    model.path = resolved.relative_to(root).as_posix()
    return []


def _escape(found: re.Match[str]) -> str:
    code = found.group(1) or found.group(2) or found.group(3)
    try:
        if code:
            return chr(int(code, 16))
        if found.group(4):
            return unicodedata.lookup(found.group(4))
        return chr(int(found.group(5), 8))
    except (KeyError, ValueError, OverflowError):
        return found.group()


def _comparable(text: str) -> str:
    """``text`` as a quote and the lines it was quoted from both read it: escapes decoded, HTML
    entities decoded, one Unicode form (NFC, which never grows the text much), case set aside."""
    unescaped = _CHAR_ESCAPE.sub(_escape, text)
    if _SURROGATE.search(unescaped):
        # json.dump writes a character past U+FFFF as two escapes, one per half of its pair.
        unescaped = unescaped.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return unicodedata.normalize("NFC", html.unescape(unescaped)).translate(_FULL_WIDTH).casefold()


def _letters(text: str) -> str:
    """The letters and digits of ``text`` once comparable: what a quote and its lines share
    whatever the quoting or the pieces a long string is written in."""
    return _NOT_A_LETTER.sub("", _comparable(text))


def quote_pieces(quote: str) -> list[list[str]]:
    """The quote's words, in the pieces it leaves text out between (..., …). Empty when the
    quote has fewer than _SHORTEST_QUOTE letters: then it says nothing to check lines against."""
    pieces = [_WORD.findall(_comparable(piece)) for piece in _ELISION.split(quote)]
    pieces = [words for words in pieces if words]
    return pieces if _weighed(pieces) >= _SHORTEST_QUOTE else []


def _weighed(pieces: list[list[str]]) -> int:
    """How many letters a quote's words come to, a character of an unspaced script as three."""
    return sum(
        3 if _UNSPACED_CHAR.fullmatch(word) else len(word) for words in pieces for word in words
    )


def says_something(quote: str) -> bool:
    """Whether a quote has enough letters to say anything: "n/a" and "TBD" don't."""
    pieces = [_WORD.findall(_comparable(piece)) for piece in _ELISION.split(quote)]
    return _weighed([words for words in pieces if words]) >= _SAYS_NOTHING


def _holds(found: str, pieces: list[list[str]]) -> bool:
    """Whether every piece's words are in ``found`` in order, each within _WORD_GAP letters of
    the one before, and each piece after the one before. A piece whose first word matches in the
    wrong place is tried again further on. Each search is charged what it reads, against a budget
    of a few times the text, so a crafted file or quote costs a few passes at most."""
    budget = 4 * (len(found) + sum(len(word) for words in pieces for word in words)) + 10_000
    at = 0
    for words in pieces:
        while True:
            start = found.find(words[0], at)
            if start < 0:
                return False
            budget -= start - at + len(words[0])
            end = start + len(words[0])
            for word in words[1:]:
                hit = found.find(word, end, end + _WORD_GAP + len(word))
                if hit < 0:
                    # A false start usually ends here, at its second word: charged that word.
                    budget -= len(word)
                    break
                budget -= _WORD_GAP + len(word)
                end = hit + len(word)
            else:
                at = end
                break
            if budget < 0:
                return False
            at = start + 1
    return True


def _in_order(pieces: list[str], found: str) -> bool:
    """Whether each piece is in ``found``, each after the one before."""
    at = 0
    for piece in pieces:
        start = found.find(piece, at)
        if start < 0:
            return False
        at = start + len(piece)
    return True


def quote_problem(quote: str, copied: str, named: str) -> str | None:
    """A problem when the lines named for the prompt don't hold the quote given for it: they
    replace the quote, so lines that miss any of it (a range that stops short, or the wrong
    lines) would send other text as the agent's instructions. A quote of fewer than
    _SHORTEST_QUOTE letters is looked for whole, with no gaps but at its `...`. Lines that hold more than the quote
    are not caught."""
    pieces = quote_pieces(quote)
    if pieces:
        held = _holds(_letters(copied), pieces)
    else:
        # A quote too short to search for with gaps must be in the lines letter for letter, each
        # piece between its ... after the one before; one that says nothing isn't looked for.
        held = not says_something(quote) or _in_order(
            [letters for piece in _ELISION.split(quote) if (letters := _letters(piece))],
            _letters(copied),
        )
    if held:
        return None
    return (
        f"system_prompt_from: the lines named ({named}) don't hold system_prompt_excerpt; quote "
        "the prompt word for word, with ... where you leave text out, and name the lines it is "
        "written on"
    )
