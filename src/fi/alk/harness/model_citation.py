"""Reading the model an agent talks to the person with, and the prompt it is given, from the
agent's own files, while a conversation waits on the answer.

A model is kept only when its name is the value written on the line the reader cites: the
Agents page shows it as fact, and an unknown model is better left empty than guessed. The files
can be built to stall whoever reads them, so every path is resolved within the save's one lookup
budget, every read is bounded in size and decoded as UTF-8 whatever coding line the file carries,
and every check of a line costs at most a few passes over it.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

from .contract import AgentContract
from .runtime_selection import charged_path
from .source_folder import is_within

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
        ".sh", ".bash", ".zsh", ".dockerfile",
    }
)
_YAML_SUFFIXES = frozenset({".yaml", ".yml"})
# Names a setting goes by, never a model: `cfg.get("model")`, `os.getenv("llm")`.
_SETTING_WORDS = frozenset({"model", "llm", "name", "default", "engine", "deployment", "model_name"})
_QUOTES = frozenset("\"'`")
_SETTING_NAME_CHARS = frozenset(string.ascii_uppercase + string.digits + "_")
# A \uXXXX or \xXX escape in a string, as json.dump writes any non-ASCII character.
_CHAR_ESCAPE = re.compile(r"\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2}))")
# How much of a quote the named lines must hold, once spacing is set aside.
_QUOTE_START = 80


def resolved_in_source(root: Path, path: str) -> Path | None:
    """``root / path`` resolved, each lookup charged to the save's budget, or None: outside
    ``root``, a link loop, a NUL, a path the system refuses. ``root`` is already resolved.
    PathLookupsSpent is not caught here: past the budget the save answers with its own message."""
    try:
        resolved = charged_path(root / path)
    except (OSError, RuntimeError, ValueError):
        return None
    return resolved if is_within(resolved, root) else None


def read_source(path: Path, limit: int) -> bytes | None:
    """At most ``limit + 1`` bytes of the file, or None: not a regular file, or not readable.
    The caller judges the size."""
    try:
        # Before the open: opening a FIFO or a device waits on it, or reads without end.
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            return handle.read(limit + 1)
    except (OSError, ValueError):
        return None


def source_lines(raw: bytes) -> list[str]:
    """The file's lines, whatever coding line it carries: no codec the file names gets to run
    on it, and one file has one numbering wherever it is read."""
    return raw.decode("utf-8", errors="replace").splitlines()


def _comment_start(line: str) -> int:
    """Where a comment starts on the line, or its length: a line that is a comment, or the first
    ` #` or ` //`. Quotes aren't tracked, so a ` #` inside a string reads as a comment too; that
    can only leave a model out, never record a wrong one."""
    stripped = line.lstrip()
    if stripped.startswith(("#", "//", "/*", "<!--")):
        return 0
    found = [
        at
        for at in (line.find(" #"), line.find("\t#"), line.find(" //"), line.find(" /*"))
        if at >= 0
    ]
    if (markup := line.find("<!--")) >= 0:
        found.append(markup)
    return min(found) if found else len(line)


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


def _is_a_lookup(line: str, at: int) -> bool:
    """Whether a quoted occurrence starting at ``at`` names what is looked up rather than the
    value: right after a subscript (`config["model-name"]`) or as the first argument of a get
    (`cfg.get("llm.model")`, `os.getenv("model-name")`). Looks at a few characters only."""
    before = at - 2
    while before >= 0 and line[before] in " \t":
        before -= 1
    if before < 1:
        return False
    if line[before] == "[":
        # A subscript follows a name or a closing bracket; a list literal doesn't.
        return line[before - 1].isalnum() or line[before - 1] in "_)]"
    if line[before] != "(":
        return False
    for call in ("getenv", "get"):
        start = before - len(call)
        if start >= 0 and line[start:before] == call:
            return start == 0 or not (line[start - 1].isalnum() or line[start - 1] == "_")
    return False


def named_on(line: str, name: str, *, config: bool, yaml: bool = False) -> bool:
    """Whether ``name`` is the value written on ``line``: whole, outside a comment, and between
    matching quotes (not a key), or in a configuration file written bare after ``:`` or ``=``.
    ``name`` is made only of MODEL_NAME_CHARS, so an occurrence starting inside one that failed
    has a name character just before it, and the search resumes after the failed one. Each step
    looks at a few characters beside the occurrence, so a line costs about one pass."""
    if not name:
        # find("") matches at every position without moving on.
        return False
    comment = _comment_start(line)
    at = line.find(name, 0, comment)
    while at >= 0:
        end = at + len(name)
        before = line[at - 1] if at else ""
        after = line[end] if end < len(line) else ""
        if before not in MODEL_NAME_CHARS and after not in MODEL_NAME_CHARS:
            if before and before in _QUOTES and after == before:
                if not _is_a_key(line, at, end) and not _is_a_lookup(line, at):
                    return True
            elif config and _bare_after_key(line, at, yaml=yaml):
                return True
        at = line.find(name, end, comment)
    return False


def is_config_file(path: Path) -> bool:
    name = path.name.lower()
    return (
        path.suffix.lower() in CONFIG_SUFFIXES
        or name.startswith((".env", "env.", "dockerfile"))
        or name.endswith(".env")
    )


def _reads_as_a_setting(name: str) -> bool:
    """A name shaped like where a model is read from rather than which: OPENAI_MODEL,
    openai_model, model."""
    if _SETTING_NAME_CHARS.issuperset(name) and any(char.isalpha() for char in name):
        return True
    if "_" in name and not any(char.isdigit() or char in "-.:/@" for char in name):
        return True
    return name.lower() in _SETTING_WORDS


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
    raw = read_source(resolved, LARGEST_MODEL_FILE)
    if raw is None or len(raw) > LARGEST_MODEL_FILE:
        return [
            f"model: {shown_path} can't be read as a file of at most 1 MiB; check the path, and "
            "cite the line in the file the name is written in"
        ]
    lines = source_lines(raw)
    if model.line > len(lines):
        return [f"model: line {model.line} is outside {shown_path}, which has {len(lines)} lines"]
    if not named_on(
        lines[model.line - 1],
        name,
        config=is_config_file(resolved),
        yaml=resolved.suffix.lower() in _YAML_SUFFIXES,
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


def _letters(text: str) -> str:
    """The letters and digits of ``text``: what a quote and the lines it was quoted from share
    whatever the quoting, the escapes or the pieces a long string is written in."""
    unescaped = _CHAR_ESCAPE.sub(
        lambda escape: chr(int(escape.group(1) or escape.group(2), 16)), text
    )
    unescaped = unescaped.replace("\\n", " ").replace("\\t", " ").replace("\\r", " ")
    return "".join(filter(str.isalnum, unescaped))


def quote_problem(quote: str, copied: str, named: str) -> str | None:
    """A problem when the lines named for the prompt don't hold the quote given for it: they
    replace the quote, so a wrong range would send unrelated code as the agent's instructions,
    and a short one would send less than was quoted. The start is compared, so a quote that
    trails off still fits."""
    wanted = _letters(quote)
    if not wanted:
        return None
    found = _letters(copied)
    if wanted[:_QUOTE_START] in found and len(found) >= len(wanted):
        return None
    return (
        f"system_prompt_from: the lines named ({named}) don't hold system_prompt_excerpt; name "
        "the lines the quoted prompt is written on"
    )
