"""The agent scan's document, `agents.json`, in the shape the platform's reader accepts.

The platform treats a file its reader rejects as a failed reading and retries it, and the retry
would fail the same way. So every row is checked against the reader's rules before it is written,
a row that fails is left out rather than failing the scan, and the whole file is checked once more
before it leaves. The rules mirror the reader's serializer at the commit the fixture names; the
fixture test holds the two together.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .scan_walk import fits_path_rule

if TYPE_CHECKING:
    from .job import HarnessJob
    from .scan_walk import Candidate

logger = logging.getLogger(__name__)

AGENTS_FILE = "agents.json"
SCHEMA_VERSION = "futureagi.agent-scan.v1"
MAX_AGENTS = 50
MAX_BYTES = 1_048_576
ENV_NAMES_KEPT = 50
NAME_MAX = 255
EVIDENCE_MAX = 200
EVIDENCE_KEPT = 5

# The reader's words, in its order. The first ten are the frameworks it names.
FRAMEWORK_NAMES = (
    "langchain",
    "langgraph",
    "livekit",
    "claude_agent_sdk",
    "openai_agents",
    "pydantic_ai",
    "google_adk",
    "crewai",
    "pipecat",
    "deepagents",
    "other",
    "none",
    "unknown",
)
_NAMED = frozenset(FRAMEWORK_NAMES[:10])
_STATUSES = ("found", "none", "error")
_CONFIDENCES = ("high", "medium", "low")
_CONFIDENCE_RANK = {word: rank for rank, word in enumerate(_CONFIDENCES)}

# The reader's patterns, as written there. It trims a value before matching, and a trimmed value
# has no trailing line break, so `fullmatch` here gives the same verdicts as its `search`.
_SUBFOLDER_PATTERN = re.compile(
    r"^(?!/)(?!.*(^|/)\.{1,2}(/|$))[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*$"
)
_ENV_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_CODE_PATTERN = re.compile(r"^[a-z0-9_]+$")

_TOP_KEYS = ("schema_version", "status", "commit_sha", "agents", "truncated", "error")
_AGENT_KEYS = (
    "name",
    "subfolder",
    "entry",
    "framework",
    "confidence",
    "evidence",
    "env_names",
    "estimates",
)
_FRAMEWORK_KEYS = ("name", "version", "label")
_ESTIMATE_KEYS = ("tools", "prompt_files")
_ERROR_KEYS = ("code", "message")

# Removed from names: control, format, surrogate and private-use characters. None of them shows
# as text, and a surrogate can't be written as UTF-8 at all.
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co"})

# A sentence's one free part is a path, shortened to the room its template leaves. Every
# template leaves at least this much, so a path always shows something more than its file name.
_PATH_ROOM = 120
_TEMPLATES = {
    "builds": "Builds with {detail} in {where}.",
    "graph": "Its langgraph.json lists a graph: {where}.",
    "agent_py": "An agent.py using a framework or a model provider: {where}.",
    # The provider's name follows "for", so no article has to agree with it.
    "client": "Creates a client for {detail} in {where}.",
    "model": "Calls a chat model through {detail} in {where}.",
    "project": "Its project file is {where}.",
    "openai_agents_set_aside": (
        "Uses the OpenAI Agents SDK, set aside because a folder named agents/ shadows it."
    ),
    "shares_folder": (
        "Another agent listed here shares this folder, so a build must name its entry."
    ),
    "graphs_may_stop": (
        "This folder's langgraph.json lists more than one graph, so a build may stop."
    ),
    "agent_py_may_stop": "More than one agent.py sits under this folder, so a build may stop.",
    "not_finished": "The framework check for this folder did not finish.",
    "partial": "Not every file under this folder was read in time, so counts may be short.",
    "found_by_ai": "Found by the AI step, not by the code check.",
}
_LONGEST_TEMPLATE = max(len(template) for template in _TEMPLATES.values())
_UNNAMED_PATH = "a file in this folder"
_DETAIL = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_DETAIL_FALLBACK = {
    "builds": "an agent framework",
    "client": "a model provider",
    "model": "an agent framework",
}
_FRAMEWORK_TITLES = {
    "langchain": "LangChain",
    "langgraph": "LangGraph",
    "livekit": "LiveKit Agents",
    "claude_agent_sdk": "the Claude Agent SDK",
    "openai_agents": "the OpenAI Agents SDK",
    "pydantic_ai": "Pydantic AI",
    "google_adk": "Google ADK",
    "crewai": "CrewAI",
    "pipecat": "Pipecat",
    "deepagents": "Deep Agents",
}
# Line numbers past this are a broken fact, and would eat the room the path needs.
_LINE_SHOWN_BELOW = 10_000_000


@dataclass(frozen=True)
class Row:
    candidate: Candidate
    framework: dict[str, Any]
    finished: bool
    found_by_ai: bool


class ScanResultInvalid(Exception):
    """The document as a whole breaks the platform's rules, so writing it would only be retried."""

    def __init__(self, problem: str) -> None:
        super().__init__(
            f"The agent list breaks the platform's rules at {problem}; "
            "this is a fault in the scan, not in the repository."
        )
        self.problem = problem


def _clean_name(text: str) -> str:
    """``text`` without hidden characters, its runs of any kind of space made one space."""
    # Spaces first: a tab or line break is itself a control character, and dropping it before
    # splitting would join the words on either side of it.
    words = (
        "".join(ch for ch in word if unicodedata.category(ch) not in _HIDDEN_CATEGORIES)
        for word in text.split()
    )
    return " ".join(word for word in words if word)


def root_name(job: HarnessJob) -> str:
    """The name for an agent at the repository's root."""
    choices: list[str] = []
    named = job.metadata.get("agent_name")
    if isinstance(named, str):
        choices.append(named)
    repository = job.source.repository
    if isinstance(repository, str):
        choices.append(repository.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git"))
    for choice in choices:
        cleaned = _clean_name(choice)
        if cleaned:
            return cleaned
    return "agent"


def _base_name(subfolder: str, root: str) -> str:
    last = subfolder.rsplit("/", 1)[-1] if subfolder else ""
    for choice in (last, root):
        cleaned = _clean_name(choice)
        if cleaned:
            return cleaned
    return "agent"


def _names(rows: Sequence[Row], root: str) -> list[str]:
    bases = [_base_name(row.candidate.subfolder, root)[:NAME_MAX] for row in rows]
    names = list(bases)
    for suffix_of in (lambda entry: entry.rsplit("/", 1)[-1], lambda entry: entry):
        counts = Counter(names)
        names = [
            _with_suffix(base, f" ({suffix_of(row.candidate.entry)})")
            if counts[name] > 1
            else name
            for base, name, row in zip(bases, names, rows)
        ]
    return names


def _with_suffix(base: str, suffix: str) -> str:
    # The suffix is what tells two agents apart, so the base gives way to it, and a suffix too
    # long on its own keeps its end, where the entry's file name is.
    if len(suffix) >= NAME_MAX:
        return suffix[-NAME_MAX:]
    return base[: NAME_MAX - len(suffix)] + suffix


def _shown_path(path: Any, room: int) -> str:
    if not isinstance(path, str) or not fits_path_rule(path):
        return _UNNAMED_PATH
    if len(path) <= room:
        return path
    parts = path.split("/")
    for start in range(1, len(parts)):
        tail = "…/" + "/".join(parts[start:])
        if len(tail) <= room:
            return tail
    return parts[-1][: max(room - 1, 0)] + "…"


def _detail(kind: str, detail: str) -> str:
    if kind in ("builds", "model") and detail in _FRAMEWORK_TITLES:
        return _FRAMEWORK_TITLES[detail]
    if _DETAIL.fullmatch(detail):
        return detail
    return _DETAIL_FALLBACK.get(kind, "")


def _sentence(kind: str, path: Any = "", line: Any = 0) -> str | None:
    """The fixed sentence for one fact, or ``None`` for a kind with no sentence.

    A path appears only when the platform's path rule accepts it, so no file name that rule
    refuses, and no character that can't be written, ever reaches the document.
    """
    head, _, detail = kind.partition(" ")
    template = _TEMPLATES.get(head)
    if template is None:
        return None
    words = {"detail": _detail(head, detail), "where": ""}
    if "{where}" in template:
        at_line = (
            f", line {line}"
            if isinstance(line, int) and not isinstance(line, bool) and 0 < line < _LINE_SHOWN_BELOW
            else ""
        )
        fixed = len(template.format(**words)) + len(at_line)
        words["where"] = _shown_path(path, EVIDENCE_MAX - fixed) + at_line
    sentence = template.format(**words)
    return sentence if len(sentence) <= EVIDENCE_MAX else sentence[: EVIDENCE_MAX - 1] + "…"


def _framework_name(row: Row) -> str:
    name = row.framework.get("name") if isinstance(row.framework, Mapping) else None
    return name if isinstance(name, str) else ""


def _confidence(row: Row, shares: bool) -> str:
    candidate = row.candidate
    name = _framework_name(row)
    if (
        name in ("unknown", "none")
        or row.found_by_ai
        or shares
        or candidate.build_may_stop
        or candidate.partial
        or not row.finished
        # A chat model alone may be one step of something larger, not an agent.
        or candidate.sign == "model"
    ):
        return "low"
    if name in _NAMED and name in candidate.builds:
        return "high"
    return "medium"


def _order_key(row: Row, shares: bool) -> tuple[bool, int, str, str]:
    candidate = row.candidate
    return (
        candidate.side,
        _CONFIDENCE_RANK[_confidence(row, shares)],
        candidate.subfolder,
        candidate.entry,
    )


def _choice_key(row: Row, models_first: bool) -> tuple[bool, bool, int, str, str]:
    side, rank, subfolder, entry = _order_key(row, False)
    behind = (
        models_first
        and not side
        and not row.found_by_ai
        and row.candidate.sign in ("client", "model")
    )
    return (side, behind, rank, subfolder, entry)


def _evidence(row: Row, shares: bool) -> list[str]:
    candidate = row.candidate
    set_aside_shown = _framework_name(row) != "openai_agents"
    facts = [
        _sentence(kind, path, line)
        for kind, path, line in candidate.evidence
        if kind != "openai_agents_set_aside" or set_aside_shown
    ]
    caveats = [
        _sentence(kind)
        for kind, applies in (
            ("found_by_ai", row.found_by_ai),
            ("shares_folder", shares),
            ("graphs_may_stop", candidate.build_may_stop == "graphs"),
            ("agent_py_may_stop", candidate.build_may_stop == "agent_py"),
            ("not_finished", not row.finished),
            ("partial", candidate.partial),
        )
        if applies
    ]
    # The strongest fact leads; the caveats that explain a low confidence come before the rest.
    known = [sentence for sentence in facts if sentence]
    ordered = known[:1] + [sentence for sentence in caveats if sentence] + known[1:]
    return list(dict.fromkeys(ordered))[:EVIDENCE_KEPT]


def _wire_row(row: Row, *, name: str, shares: bool) -> dict[str, Any]:
    candidate = row.candidate
    framework = row.framework
    return {
        "name": name,
        "subfolder": candidate.subfolder,
        "entry": candidate.entry,
        "framework": dict(framework) if isinstance(framework, Mapping) else framework,
        "confidence": _confidence(row, shares),
        "evidence": _evidence(row, shares),
        "env_names": list(candidate.env_names[:ENV_NAMES_KEPT]),
        "estimates": {"tools": candidate.tools, "prompt_files": candidate.prompt_files},
    }


def _document(
    *,
    status: str,
    commit_sha: str | None,
    agents: list[dict[str, Any]],
    truncated: bool,
    error: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "commit_sha": commit_sha,
        "agents": agents,
        "truncated": truncated,
        "error": error,
    }


def _commit(commit_sha: str | None) -> str | None:
    if not isinstance(commit_sha, str):
        return None
    lowered = commit_sha.lower()
    return lowered if _COMMIT_PATTERN.fullmatch(lowered) else None


def choose_rows(
    rows: Sequence[Row], *, limit: int = MAX_AGENTS, models_first: bool = False
) -> tuple[list[Row], bool]:
    """The rows to write, one per entry and at most ``limit``, and whether any were left out.

    Who shares a folder isn't known until the rows are chosen, so the order here treats every
    row as alone in its folder. With ``models_first``, the model's rows outside side folders
    are chosen before the walk's rows that only call a model: the model was paid to look past
    those, so what it found is the likelier agent, and it must not be the row the cap drops.
    """
    seen: set[str] = set()
    unique: list[Row] = []
    for row in rows:
        if row.candidate.entry in seen:
            continue
        seen.add(row.candidate.entry)
        unique.append(row)
    unique.sort(key=lambda row: _choice_key(row, models_first))
    chosen = unique[: max(limit, 0)]
    return chosen, len(chosen) != len(rows)


def finish(
    *,
    rows: Sequence[Row],
    root_name: str,
    commit_sha: str | None,
    truncated: bool,
    error: tuple[str, str] | None,
    models_first: bool = False,
) -> tuple[bytes, bool]:
    """The bytes of ``agents.json``, and whether a row was left out by the cap or the self-check.

    Rows given with an error win: what was found is worth more than why the scan stopped early,
    and ``truncated`` says it stopped. ``models_first`` changes which rows are chosen, never
    the order they are written in.
    """
    root = _clean_name(root_name) or "agent"
    commit = _commit(commit_sha)
    chosen, dropped = choose_rows(rows, limit=MAX_AGENTS, models_first=models_first)

    kept: list[Row] = []
    for row in chosen:
        name = _base_name(row.candidate.subfolder, root)[:NAME_MAX]
        alone = _wire_row(row, name=name, shares=False)
        problems = wire_problems(
            _document(status="found", commit_sha=None, agents=[alone], truncated=False, error=None)
        )
        if problems:
            logger.warning("agent scan: row left out, problem=%s", problems[0])
            dropped = True
            continue
        kept.append(row)

    folders = Counter(row.candidate.subfolder for row in kept)
    shares = [folders[row.candidate.subfolder] > 1 for row in kept]
    order = sorted(range(len(kept)), key=lambda index: _order_key(kept[index], shares[index]))
    kept = [kept[index] for index in order]
    shares = [shares[index] for index in order]
    names = _names(kept, root)
    agents = [
        _wire_row(row, name=name, shares=shared) for row, name, shared in zip(kept, names, shares)
    ]

    if rows and error is not None:
        logger.info("agent scan: error dropped for rows, code=%s", error[0])
    if agents:
        status, wire_error = "found", None
    elif error is not None and not rows:
        status, wire_error = "error", {"code": error[0], "message": error[1]}
    else:
        status, wire_error = "none", None
    document = _document(
        status=status,
        commit_sha=commit,
        agents=agents,
        truncated=bool(truncated or dropped or (rows and error is not None)),
        error=wire_error,
    )
    problems = wire_problems(document)
    if problems:
        raise ScanResultInvalid(problems[0])
    body = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_BYTES:
        raise ScanResultInvalid("document.too_large")
    logger.info("agent scan: rows written=%d left out=%d", len(agents), len(rows) - len(agents))
    return body, dropped


def _at(where: str, key: str) -> str:
    return f"{where}.{key}" if where else key


def _keys_problems(value: Any, where: str, keys: tuple[str, ...]) -> list[str]:
    if not isinstance(value, dict):
        return [_at(where, "not_an_object")]
    problems = [_at(_at(where, key), "missing") for key in keys if key not in value]
    if set(value) - set(keys):
        problems.append(_at(where, "unknown_keys"))
    return problems


def _text_problems(
    value: Any,
    where: str,
    *,
    longest: int,
    nullable: bool = False,
    blank: bool = False,
    pattern: re.Pattern[str] | None = None,
) -> list[str]:
    if value is None:
        return [] if nullable else [f"{where}.null"]
    if not isinstance(value, str):
        return [f"{where}.type"]
    # The reader trims before every check, so lengths and patterns are judged on the trimmed text.
    text = value.strip()
    if not text:
        return [] if blank else [f"{where}.blank"]
    problems = []
    if len(text) > longest:
        problems.append(f"{where}.too_long")
    if "\x00" in text:
        problems.append(f"{where}.null_byte")
    if any(0xD800 <= ord(ch) <= 0xDFFF for ch in text):
        problems.append(f"{where}.surrogate")
    if pattern is not None and not pattern.fullmatch(text):
        problems.append(f"{where}.pattern")
    return problems


def _choice_problems(value: Any, where: str, choices: tuple[str, ...]) -> list[str]:
    if value is None:
        return [f"{where}.null"]
    if not isinstance(value, str) or value not in choices:
        return [f"{where}.choice"]
    return []


def _count_problems(value: Any, where: str) -> list[str]:
    if not isinstance(value, int) or isinstance(value, bool):
        return [f"{where}.type"]
    return [f"{where}.negative"] if value < 0 else []


def _texts_problems(
    value: Any, where: str, *, most: int, longest: int, pattern: re.Pattern[str] | None = None
) -> list[str]:
    if not isinstance(value, list):
        return [f"{where}.not_a_list"]
    problems = [f"{where}.too_many"] if len(value) > most else []
    for index, item in enumerate(value):
        problems += _text_problems(item, f"{where}[{index}]", longest=longest, pattern=pattern)
    return problems


def _framework_problems(value: Any, where: str) -> list[str]:
    problems = _keys_problems(value, where, _FRAMEWORK_KEYS)
    if not isinstance(value, dict):
        return problems
    if "name" in value:
        problems += _choice_problems(value["name"], f"{where}.name", FRAMEWORK_NAMES)
    if "version" in value:
        problems += _text_problems(value["version"], f"{where}.version", longest=64, nullable=True)
    if "label" in value:
        problems += _text_problems(value["label"], f"{where}.label", longest=128, nullable=True)
    if not problems and (value["name"] == "other") != (value["label"] is not None):
        problems.append(f"{where}.label.other_only")
    return problems


def _agent_problems(agent: Any, where: str) -> list[str]:
    problems = _keys_problems(agent, where, _AGENT_KEYS)
    if not isinstance(agent, dict):
        return problems
    if "name" in agent:
        problems += _text_problems(agent["name"], f"{where}.name", longest=NAME_MAX)
    for key in ("subfolder", "entry"):
        if key in agent:
            problems += _text_problems(
                agent[key],
                f"{where}.{key}",
                longest=512,
                blank=key == "subfolder",
                pattern=_SUBFOLDER_PATTERN,
            )
    if "framework" in agent:
        problems += _framework_problems(agent["framework"], f"{where}.framework")
    if "confidence" in agent:
        problems += _choice_problems(agent["confidence"], f"{where}.confidence", _CONFIDENCES)
    if "evidence" in agent:
        problems += _texts_problems(
            agent["evidence"], f"{where}.evidence", most=EVIDENCE_KEPT, longest=EVIDENCE_MAX
        )
    if "env_names" in agent:
        problems += _texts_problems(
            agent["env_names"],
            f"{where}.env_names",
            most=200,
            longest=128,
            pattern=_ENV_NAME_PATTERN,
        )
    if "estimates" in agent:
        estimates = agent["estimates"]
        problems += _keys_problems(estimates, f"{where}.estimates", _ESTIMATE_KEYS)
        if isinstance(estimates, dict):
            for key in _ESTIMATE_KEYS:
                if key in estimates:
                    problems += _count_problems(estimates[key], f"{where}.estimates.{key}")
    return problems


def _error_problems(error: Any) -> list[str]:
    if error is None:
        return []
    problems = _keys_problems(error, "error", _ERROR_KEYS)
    if not isinstance(error, dict):
        return problems
    if "code" in error:
        problems += _text_problems(error["code"], "error.code", longest=64, pattern=_CODE_PATTERN)
    if "message" in error:
        problems += _text_problems(error["message"], "error.message", longest=2000)
    return problems


def wire_problems(document: dict[str, Any]) -> list[str]:
    """Where ``document`` breaks the platform reader's rules, as problem names, never values.

    An empty list means the reader accepts it. Where the reader is looser than the written
    contract (it ignores unknown keys, and turns numbers into text and ``"true"`` into a boolean),
    this is stricter: the kit never needs that leniency, and relying on it would tie the file to
    the reader's version.
    """
    problems = _keys_problems(document, "", _TOP_KEYS)
    if not isinstance(document, dict):
        return problems
    if "schema_version" in document:
        problems += _choice_problems(
            document["schema_version"], "schema_version", (SCHEMA_VERSION,)
        )
    if "status" in document:
        problems += _choice_problems(document["status"], "status", _STATUSES)
    if "commit_sha" in document:
        problems += _text_problems(
            document["commit_sha"],
            "commit_sha",
            longest=40,
            nullable=True,
            pattern=_COMMIT_PATTERN,
        )
    agents = document.get("agents")
    if "agents" in document:
        if not isinstance(agents, list):
            problems.append("agents.not_a_list")
        elif len(agents) > MAX_AGENTS:
            problems.append("agents.too_many")
        else:
            for index, agent in enumerate(agents):
                problems += _agent_problems(agent, f"agents[{index}]")
    if "truncated" in document and not isinstance(document["truncated"], bool):
        problems.append("truncated.type")
    if "error" in document:
        problems += _error_problems(document["error"])
    if not problems:
        status, error = document["status"], document["error"]
        if status == "found" and (not agents or error is not None):
            problems.append("status.found_needs_agents_and_no_error")
        elif status == "none" and (agents or error is not None):
            problems.append("status.none_needs_no_agents_and_no_error")
        elif status == "error" and (agents or error is None):
            problems.append("status.error_needs_an_error_and_no_agents")
    return problems


def write_agents_json(output: Path, body: bytes) -> Path:
    """Write ``body`` as ``output/agents.json`` whole, so a reader never sees half a file."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    path = output / AGENTS_FILE
    temporary = tempfile.NamedTemporaryFile(
        "wb", dir=output, prefix=".agents-", suffix=".tmp", delete=False
    )
    try:
        with temporary:
            temporary.write(body)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary.name, 0o644)
        os.replace(temporary.name, path)
    except BaseException:
        try:
            os.unlink(temporary.name)
        except OSError:
            pass
        raise
    return path
