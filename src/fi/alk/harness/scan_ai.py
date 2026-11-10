"""Asks a model where a repository's agents are, when the plain code check found none it can trust.

The customer pays for every turn here, so the step is shaped around the bill. It says one thing,
once: a second message in a Claude session re-reports the first one's cost, and a reminder after
a turn limit is a fresh paid turn. An answer the model has submitted is kept whatever happens
after it, because it was paid for. The time limit always ends in the same stop sequence, so the
cost of a call in flight is either recorded or marked incomplete, never silently dropped.

The model reads the repository through three tools of this module's own, not the kit's
general file tools. Those search with a regular expression over whole files with no bound,
which one minified line or one named pipe turns into a stalled event loop. These search for
plain text, bound every call by entries, bytes, characters and seconds, and never show a file
the kit treats as a secret.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import stat
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .backends import SessionSpec, StageDone, ToolServer, ToolSpec
from .backends.files import SECRET_REFUSAL, above_secrets, secret_path
from .config import chosen_model, load_skill
from .framework_detection import _ALWAYS_SKIPPED, _TOP_ONLY_SKIPS
from .reading_entry import MaskSecrets
from .scan_walk import fits_path_rule
from .session import Stage, StageIdleTimeout, Turn

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

STAGE_NAME = "find_agents"
MAX_TURNS = 40
IDLE_SECONDS = 90.0
OPEN_SECONDS = 30.0
# The stop sequence. Long enough for a provider to send its last bill, short enough that a
# stuck one can't eat the time the rest of the scan needs.
INTERRUPT_SECONDS = 10.0
SPEND_WAIT_SECONDS = 60.0
CANCEL_SECONDS = 5.0
EXIT_SECONDS = 10.0
TOOL_SECONDS = 20.0

MAX_PAIRS = 50
MAX_ENTRIES = 2_000
MAX_READ_BYTES = 33_554_432
MAX_FILE_BYTES = 1_048_576
MAX_LINE_BYTES = 65_536
MAX_RESULT_CHARS = 8_000
MAX_READ_LINES = 400
# As the kit's general Read cuts a line, so a minified file can't fill a result with one line.
MAX_LINE_CHARS = 2_000
HIT_CHARS = 300
MAX_PATTERN_CHARS = 200
MAX_PATH_CHARS = 4_096
MAX_PATH_PARTS = 256

STOPPED_EARLY = "Stopped early at the search limit: results are incomplete. Narrow the path."
TOOLS_AFTER_SUBMIT = "Already submitted: end your turn."
# ADK's own message when a run spends its call budget, matched whole so that the phrase quoted
# inside some other error never reads as a turn limit.
_CALL_LIMIT = re.compile(r"Max number of llm calls limit of `\d+` exceeded")
SUBMIT_AFTER_SUBMIT = "Already submitted."

_OUTSIDE = "That path is not allowed: give a path inside the repository."
_SKIPPED = "That path is in a folder this step skips: installed, built or generated files."
_MISSING = "No such file or folder in the repository."
_NOT_A_FILE = "That is not a file this tool reads: only regular files up to 1 MiB are read."
_NOT_A_FOLDER = "That is not a folder in the repository."
_BAD_NUMBER = "offset and limit must be whole numbers."
_BAD_PATTERN = "pattern must be text of 1 to 200 characters."
_BAD_TEXT = "text must be plain text of 1 to 200 characters."
_BAD_GLOB = "glob must be text of at most 200 characters."
_BAD_IGNORE_CASE = "ignore_case must be true or false."
_BAD_SUBMIT = 'Give {"agents": [{"folder": ..., "entry": ...}]} with at most 50 agents.'
_TOO_SLOW = "That call took too long and was stopped. Narrow the path."
_FAILED = "That call failed. Try a different path or argument."

# Names the kit's secret filter doesn't cover. A source file named like `secret_manager.py` is
# hidden too; that's the price of never showing a settings file under an unusual name.
_SECRET_NAMES = (
    "*.env",
    "*.env.*",
    "*secret*",
    "*credential*",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
    "*.tfvars",
)

_OPENING = (
    "Find the runnable AI agents in this repository and submit them with "
    "mcp__scan__submit_agents, as your instructions say. A plain code check found none outside "
    "test and example folders, apart from any files that only call a model, which it has "
    "already listed. What it counted: "
)


@dataclass(frozen=True)
class AiOutcome:
    kind: Literal["pairs", "empty", "no_answer", "timed_out", "model_error", "forbidden"]
    pairs: tuple[tuple[str, str], ...]
    refused: int
    complete: bool
    retryable: bool
    message: str
    # Real files the model named whose path the platform's rule can't carry, counted once each
    # over every submit: they are left out, so the list is not whole.
    uncarried: int = 0


class _Refused(Exception):
    """A tool call the tool won't answer, with the fixed words the model is told."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class _ToolFailed(Exception):
    pass


@dataclass
class _Answer:
    """What the model has submitted. Shared by the submit tool and the file tools."""

    called: bool = False
    pairs: tuple[tuple[str, str], ...] = ()
    refused: int = 0
    uncarried: set[str] = field(default_factory=set)

    def finished(self) -> bool:
        return bool(self.pairs)


class _ScanStage(Stage):
    """A stage that keeps its one turn's raw end and says when a turn's cost is known."""

    def __init__(
        self, spec: SessionSpec, *, name: str, on_turn_end: Callable[[], None]
    ) -> None:
        # A replayed quiet stream would be a second paid opening, and its first spend is
        # never recorded.
        super().__init__(spec, name=name, idle_retries=0)
        self.done: list[StageDone] = []
        self._on_turn_end = on_turn_end

    def _events(self, received: Any, turn: Turn) -> list[Any]:
        try:
            return super()._events(received, turn)
        finally:
            # In a finally because the parent raises for a forbidden model after recording
            # its spend, and that turn's cost still has to be noted.
            if isinstance(received, StageDone):
                self.done.append(received)
                try:
                    self._on_turn_end()
                except Exception as exc:
                    # A failed cost note must not turn a paid answer into a retried one.
                    logger.warning(
                        "find_agents: the cost note after the turn failed (%s)",
                        type(exc).__name__,
                    )


# -- the run -----------------------------------------------------------------------------------


async def run_ai_step(
    source: Path, *, hints: str, seconds: float, on_turn_end: Callable[[], None]
) -> AiOutcome:
    """Ask the model once for the repository's agents, within ``seconds``."""
    answer = _Answer()
    spec = SessionSpec(
        system_prompt=load_skill("find-agents"),
        servers={
            "scan_files": rooted_file_tools(source, finished=answer.finished),
            "scan": _submit_server(source, answer),
        },
        builtins=(),
        cwd=str(source),
        max_turns=MAX_TURNS,
        model=chosen_model(),
        ask=None,
        gated=True,
        thinking=False,
        idle_timeout_seconds=IDLE_SECONDS,
    )
    stage = _ScanStage(spec, name=STAGE_NAME, on_turn_end=on_turn_end)
    try:
        outcome = await _run(stage, answer, hints, seconds)
    finally:
        await _leave(stage)
    logger.info(
        "find_agents: %s, %d kept, %d refused, complete=%s",
        outcome.kind,
        len(outcome.pairs),
        outcome.refused,
        outcome.complete,
    )
    return outcome


async def _run(stage: _ScanStage, answer: _Answer, hints: str, seconds: float) -> AiOutcome:
    try:
        await asyncio.wait_for(stage.__aenter__(), OPEN_SECONDS)
    except Exception as exc:
        logger.warning("find_agents: the model session did not open", exc_info=True)
        return AiOutcome(
            "model_error",
            (),
            0,
            False,
            True,
            f"the model session did not open ({type(exc).__name__})",
        )
    task = asyncio.create_task(stage.say(_opening(hints)))
    try:
        finished, _ = await asyncio.wait({task}, timeout=seconds)
        stopped = not finished
        if stopped:
            await _stop(stage, task)
        failure = None if not task.done() or task.cancelled() else task.exception()
    finally:
        if not task.done():
            task.cancel()
    return _outcome(stage, answer, stopped=stopped, failure=failure)


def _opening(hints: str) -> str:
    return _OPENING + (hints or "nothing.")


async def _stop(stage: _ScanStage, task: asyncio.Task[Any]) -> None:
    try:
        await asyncio.wait_for(stage.interrupt_response(), INTERRUPT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("find_agents: the interrupt got no answer in %ss", INTERRUPT_SECONDS)
    except Exception:
        # The session may already be gone; the wait for its bill below still runs.
        logger.warning("find_agents: the interrupt failed", exc_info=True)
    # The turn's bill arrives after the interrupt, if it arrives at all.
    await asyncio.wait({task}, timeout=SPEND_WAIT_SECONDS)
    if not task.done():
        task.cancel()
        await asyncio.wait({task}, timeout=CANCEL_SECONDS)


async def _leave(stage: _ScanStage) -> None:
    try:
        await asyncio.wait_for(stage.__aexit__(None, None, None), EXIT_SECONDS)
    except asyncio.TimeoutError:
        logger.warning("find_agents: the session did not close in %ss", EXIT_SECONDS)
    except Exception:
        # The answer is already kept; a session that fails to close doesn't change it.
        logger.warning("find_agents: the session failed to close", exc_info=True)


def _outcome(
    stage: _ScanStage, answer: _Answer, *, stopped: bool, failure: BaseException | None
) -> AiOutcome:
    complete = bool(stage.done) and not stopped
    if answer.pairs:
        return AiOutcome(
            "pairs", answer.pairs, answer.refused, complete, False, "", len(answer.uncarried)
        )
    if stage.forbidden_models():
        return AiOutcome(
            "forbidden",
            (),
            0,
            complete,
            False,
            "the provider billed a model this kit may not spend on",
        )
    if stopped:
        return AiOutcome(
            "timed_out", (), 0, complete, False, "the model step ran out of time"
        )
    if isinstance(failure, StageIdleTimeout):
        return AiOutcome(
            "model_error",
            (),
            0,
            complete,
            True,
            "the model stream went quiet (StageIdleTimeout)",
        )
    if failure is not None:
        return AiOutcome(
            "model_error",
            (),
            0,
            complete,
            True,
            f"the model step failed ({type(failure).__name__})",
        )
    done = stage.done[-1] if stage.done else None
    if done is not None and done.api_error_status:
        status = done.api_error_status
        said = (
            f" with status {status}"
            if isinstance(status, int) and not isinstance(status, bool)
            else ""
        )
        return AiOutcome(
            "model_error",
            (),
            0,
            complete,
            not _client_error(status),
            f"the model call failed{said}",
        )
    if done is not None and done.is_error and not _turn_limit(done):
        return AiOutcome(
            "model_error", (), 0, complete, True, "the model call ended in an error"
        )
    if answer.called:
        return AiOutcome("empty", (), 0, complete, False, "", len(answer.uncarried))
    return AiOutcome(
        "no_answer",
        (),
        0,
        complete,
        False,
        "the model ended without submitting an answer",
    )


def _turn_limit(done: StageDone) -> bool:
    if done.outcome in ("error_max_turns", "max_turns"):
        return True
    if not done.is_error or done.api_error_status:
        return False
    # Vertex-Gemini's call limit arrives as ADK's exception: failed, no status. The turn count
    # can't say it: a reply with no parts isn't counted, and a status-less cut on the last allowed
    # request reaches the limit without being one. ADK's fixed words are only matched, never
    # repeated.
    return any(_CALL_LIMIT.fullmatch(str(error)) for error in done.errors)


def _client_error(status: Any) -> bool:
    # A request the provider refuses won't be accepted on a retry either, except for these
    # three, which say "not now" rather than "not this".
    return (
        isinstance(status, int)
        and not isinstance(status, bool)
        and 400 <= status <= 499
        and status not in (408, 409, 429)
    )


# -- the tools ---------------------------------------------------------------------------------


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _error(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "is_error": True}


async def _in_thread(work: Callable[..., Any], *args: Any) -> Any:
    """``work(*args)`` in a daemon thread, so a stalled disk can hold neither the loop nor exit."""
    loop = asyncio.get_running_loop()
    answer = loop.create_future()

    def settle(result: Any, error: BaseException | None) -> None:
        # The wait below may have given up and cancelled the future already.
        if answer.done():
            return
        if error is None:
            answer.set_result(result)
        else:
            answer.set_exception(error)

    def run() -> None:
        result: Any = None
        error: BaseException | None = None
        try:
            result = work(*args)
        except _Refused as refused:
            error = refused
        except BaseException:
            error = _ToolFailed()
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:
            pass

    threading.Thread(target=run, name="find-agents-tool", daemon=True).start()
    return await asyncio.wait_for(answer, TOOL_SECONDS)


async def _answered(work: Callable[..., str], *args: Any) -> dict[str, Any]:
    try:
        return _ok(await _in_thread(work, *args))
    except _Refused as refused:
        return _error(refused.message)
    except asyncio.TimeoutError:
        return _error(_TOO_SLOW)
    except Exception:
        # The model is told only that the call failed; the trace is logged masked.
        logger.warning("find_agents: a tool call failed", exc_info=True)
        return _error(_FAILED)


def rooted_file_tools(
    root: Path, *, finished: Callable[[], bool] | None = None
) -> ToolServer:
    """``read``, ``glob`` and ``grep``, confined to ``root`` and bounded on every call.

    ``finished`` says the answer is in; from then on the tools read nothing. ``None`` means
    the answer is never in.
    """
    base = os.path.realpath(str(root))

    async def guarded(work: Callable[[str, dict[str, Any]], str], arguments: Any) -> dict[str, Any]:
        try:
            if finished is not None and finished():
                return _error(TOOLS_AFTER_SUBMIT)
            return await _answered(work, base, arguments)
        except Exception:
            logger.warning("find_agents: a tool call failed", exc_info=True)
            return _error(_FAILED)

    # Each looks its worker up when called, not when built.
    async def read(arguments: dict[str, Any]) -> dict[str, Any]:
        return await guarded(_read_lines, arguments)

    async def glob(arguments: dict[str, Any]) -> dict[str, Any]:
        return await guarded(_glob_files, arguments)

    async def grep(arguments: dict[str, Any]) -> dict[str, Any]:
        return await guarded(_grep_files, arguments)

    return ToolServer(
        name="scan_files",
        tools=[
            ToolSpec(
                name="read",
                description=(
                    "Read one file of the repository. Arguments: path (relative to the "
                    "repository's root), optional offset (first line, from 1) and limit (at "
                    "most 400 lines)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["path"],
                },
                handler=read,
            ),
            ToolSpec(
                name="glob",
                description=(
                    "List the repository's files whose path or name matches a pattern such as "
                    "*.py or **/agent*.ts. Arguments: pattern, optional path of a folder to "
                    "look under."
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
                name="grep",
                description=(
                    "Find lines containing text, matched exactly as written: plain text, not a "
                    "regular expression. Arguments: text, optional path (a file or folder), glob "
                    "(file names to search, such as *.ts) and ignore_case."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "text": {"type": "string"},
                        "path": {"type": "string"},
                        "glob": {"type": "string"},
                        "ignore_case": {"type": "boolean"},
                    },
                    "required": ["text"],
                },
                handler=grep,
            ),
        ],
    )


def _secret_name(name: str) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatchcase(lowered, pattern) for pattern in _SECRET_NAMES)


def _skipped_folder(name: str, *, at_top: bool) -> bool:
    # The same folders the framework check skips, so the model and the code look at one tree.
    return name in _ALWAYS_SKIPPED or (at_top and name in _TOP_ONLY_SKIPS)


def _in_skipped_folder(folders: list[str]) -> bool:
    return any(
        _skipped_folder(name, at_top=index == 0) for index, name in enumerate(folders)
    )


def _usable(status: os.stat_result) -> bool:
    # A hard link can make a file outside the source look like one inside it.
    return (
        stat.S_ISREG(status.st_mode)
        and status.st_nlink == 1
        and status.st_size <= MAX_FILE_BYTES
    )


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _place(root: str, raw: Any) -> tuple[str, str]:
    """The model's ``path`` as (the path as written, joined to the root; where it resolves)."""
    if raw is None or raw == "":
        raw = "."
    if (
        not isinstance(raw, str)
        or "\0" in raw
        or len(raw) > MAX_PATH_CHARS
        or len(raw.split("/")) > MAX_PATH_PARTS
    ):
        raise _Refused(_OUTSIDE)
    joined = raw if os.path.isabs(raw) else os.path.join(root, raw)
    real = os.path.realpath(joined)
    if not _under(real, root):
        raise _Refused(_OUTSIDE)
    if secret_path(raw, Path(root)) or above_secrets(raw, Path(root)):
        raise _Refused(SECRET_REFUSAL)
    if real != root:
        parts = os.path.relpath(real, root).split(os.sep)
        # Refused exactly where the walk would never have shown it.
        if any(_secret_name(part) for part in parts):
            raise _Refused(SECRET_REFUSAL)
        if _in_skipped_folder(parts):
            raise _Refused(_SKIPPED)
    return joined, real


def _hidden(path: str, name: str) -> bool:
    return _secret_name(name) or secret_path(path)


@dataclass
class _Budget:
    entries: int = 0
    read_bytes: int = 0
    stopped: bool = False


def _listed(folder: str, budget: _Budget) -> list[str]:
    names: list[str] = []
    with os.scandir(folder) as entries:
        for entry in entries:
            if budget.entries >= MAX_ENTRIES:
                budget.stopped = True
                break
            budget.entries += 1
            names.append(entry.name)
    return sorted(names)


def _walk(root: str, start: str, budget: _Budget) -> Iterator[tuple[str, str]]:
    """Every usable file under ``start``, as (its path, its path relative to ``root``)."""
    stack = [start]
    while stack and not budget.stopped:
        folder = stack.pop()
        try:
            names = _listed(folder, budget)
        except OSError:
            continue
        below: list[str] = []
        for name in names:
            path = os.path.join(folder, name)
            try:
                status = os.lstat(path)
            except OSError:
                continue
            if _hidden(path, name):
                continue
            if stat.S_ISDIR(status.st_mode):
                if not _skipped_folder(name, at_top=folder == root):
                    below.append(path)
            elif _usable(status) and not name.lower().endswith(".min.js"):
                yield path, os.path.relpath(path, root)
        stack.extend(reversed(below))


def _read_bytes(path: str) -> bytes:
    # O_NONBLOCK so that a file swapped for a named pipe since its lstat can't hang the thread.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not _usable(os.fstat(descriptor)):
            raise _Refused(_NOT_A_FILE)
        chunks: list[bytes] = []
        remaining = MAX_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(descriptor)
    if len(data) > MAX_FILE_BYTES:
        raise _Refused(_NOT_A_FILE)
    return data


def _one_file(joined: str) -> None:
    try:
        status = os.lstat(joined)
    except FileNotFoundError:
        raise _Refused(_MISSING) from None
    if not _usable(status) or os.path.basename(joined).lower().endswith(".min.js"):
        raise _Refused(_NOT_A_FILE)


def _whole(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdecimal() and len(value) <= 9:
        return int(value)
    raise _Refused(_BAD_NUMBER)


def _text(value: Any, *, required: bool, problem: str) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or len(value) > MAX_PATTERN_CHARS or (required and not value):
        raise _Refused(problem)
    return value


def _finished_text(lines: list[str], *, stopped: bool, nothing: str) -> str:
    body = "\n".join(lines)
    if stopped:
        return f"{body}\n{STOPPED_EARLY}" if body else STOPPED_EARLY
    return body or nothing


# Room for the note, so a capped result still ends with it inside the limit.
_ROOM = MAX_RESULT_CHARS - len(STOPPED_EARLY) - 1


def _read_lines(root: str, arguments: dict[str, Any]) -> str:
    joined, real = _place(root, arguments.get("path"))
    if real == root:
        raise _Refused(_NOT_A_FILE)
    offset = max(_whole(arguments.get("offset"), 1), 1)
    limit = min(max(_whole(arguments.get("limit"), MAX_READ_LINES), 1), MAX_READ_LINES)
    _one_file(joined)
    lines = _read_bytes(joined).splitlines()
    if not lines:
        return "(empty file)"
    if offset > len(lines):
        return f"(the file has {len(lines)} lines; start at a lower offset)"
    shown: list[str] = []
    size = 0
    number = offset
    for raw in lines[offset - 1 : offset - 1 + limit]:
        line = f"{number}\t{raw.decode('utf-8', 'replace')[:MAX_LINE_CHARS]}"
        # Room for the continuation line and the note.
        if size + len(line) + 1 > _ROOM - 80:
            shown.append(f"... call again with offset={number}")
            return _finished_text(shown, stopped=True, nothing="")
        shown.append(line)
        size += len(line) + 1
        number += 1
    remaining = len(lines) - (number - 1)
    if remaining > 0:
        shown.append(f"... ({remaining} more lines; call again with offset={number})")
    return "\n".join(shown)


_BRACE = re.compile(r"\{([^{}]*,[^{}]*)\}")
_MAX_FORMS = 32


def _pattern_forms(pattern: str) -> tuple[str, ...]:
    """The pattern as a shell reads it: fnmatch has no ``{a,b}``, and its ``**/`` needs a folder
    in front, so ``**/package.json`` would miss the one at the root. Never more than 32 forms."""
    forms, done = [pattern], []
    while forms and len(done) + len(forms) <= _MAX_FORMS:
        form = forms.pop()
        brace = _BRACE.search(form)
        if brace is None:
            done.append(form)
            continue
        forms.extend(
            form[: brace.start()] + choice + form[brace.end() :]
            for choice in brace.group(1).split(",")
        )
    done.extend(forms)
    grown = []
    for form in done[:_MAX_FORMS]:
        grown.append(form)
        flat = form.replace("/**/", "/")
        while flat.startswith("**/"):
            flat = flat[3:]
        if flat:
            grown.append(flat)
    return tuple(dict.fromkeys(grown))


def _name_matches(relative: str, forms: tuple[str, ...]) -> bool:
    name = relative.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatchcase(relative, form) or fnmatch.fnmatchcase(name, form) for form in forms
    )


def _glob_files(root: str, arguments: dict[str, Any]) -> str:
    pattern = _text(arguments.get("pattern"), required=True, problem=_BAD_PATTERN)
    _, start = _place(root, arguments.get("path"))
    if not os.path.isdir(start):
        raise _Refused(_NOT_A_FOLDER if os.path.exists(start) else _MISSING)
    forms = _pattern_forms(pattern)
    budget = _Budget()
    found: list[str] = []
    size = 0
    capped = False
    for path, relative in _walk(root, start, budget):
        if not _name_matches(os.path.relpath(path, start), forms):
            continue
        if size + len(relative) + 1 > _ROOM:
            capped = True
            break
        found.append(relative)
        size += len(relative) + 1
    return _finished_text(found, stopped=budget.stopped or capped, nothing="No file matches.")


def _grep_files(root: str, arguments: dict[str, Any]) -> str:
    text = _text(arguments.get("text"), required=True, problem=_BAD_TEXT)
    wanted = _text(arguments.get("glob"), required=False, problem=_BAD_GLOB)
    ignore_case = arguments.get("ignore_case")
    if ignore_case is None:
        ignore_case = False
    if not isinstance(ignore_case, bool):
        raise _Refused(_BAD_IGNORE_CASE)
    joined, start = _place(root, arguments.get("path"))
    budget = _Budget()
    if os.path.isdir(start):
        targets: Iterator[tuple[str, str]] = _walk(root, start, budget)
    else:
        _one_file(joined)
        targets = iter([(joined, os.path.relpath(start, root))])
    forms = _pattern_forms(wanted) if wanted else ()
    needle = text.lower() if ignore_case else text
    exact = text.encode("utf-8")
    hits: list[str] = []
    size = 0
    capped = False
    for path, relative in targets:
        if forms and not _name_matches(relative, forms):
            continue
        try:
            if budget.read_bytes + os.lstat(path).st_size > MAX_READ_BYTES:
                budget.stopped = True
                break
            data = _read_bytes(path)
        except (OSError, _Refused):
            continue
        budget.read_bytes += len(data)
        for number, raw in enumerate(data.splitlines(), 1):
            # A line this long is minified or generated, and matching it is what makes a
            # search slow.
            if len(raw) > MAX_LINE_BYTES:
                continue
            if not ignore_case and exact not in raw:
                continue
            line = raw.decode("utf-8", "replace")
            if ignore_case and needle not in line.lower():
                continue
            hit = f"{relative}:{number}:{line[:HIT_CHARS]}"
            if size + len(hit) + 1 > _ROOM:
                capped = True
                break
            hits.append(hit)
            size += len(hit) + 1
        if capped:
            break
    return _finished_text(hits, stopped=budget.stopped or capped, nothing="No line matches.")


# -- the answer --------------------------------------------------------------------------------


def _stripped(path: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if path == prefix.rstrip("/") and prefix != "./":
            return ""
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def _checked_pair(
    root: str, prefixes: tuple[str, ...], item: Any
) -> tuple[str, str] | None:
    if not isinstance(item, dict):
        return None
    folder, entry = item.get("folder"), item.get("entry")
    if not isinstance(folder, str) or not isinstance(entry, str):
        return None
    folder, entry = _stripped(folder, prefixes), _stripped(entry, prefixes)
    if not fits_path_rule(entry):
        return None
    if folder and (not fits_path_rule(folder) or not entry.startswith(folder + "/")):
        return None
    if _in_skipped_folder(entry.split("/")[:-1]):
        return None
    path = os.path.join(root, entry)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
    except OSError:
        return None
    real_folder = os.path.realpath(os.path.join(root, folder)) if folder else root
    if not _under(real_folder, root) or not os.path.isdir(real_folder):
        return None
    if not _under(os.path.realpath(path), real_folder):
        return None
    return folder, entry


def _uncarried_entry(root: str, prefixes: tuple[str, ...], item: Any) -> str | None:
    """A real file the model named, inside the source, whose path breaks only the platform's
    rule (a Next.js ``app/(chat)/...`` route, a space). A missing or outside file is the model's
    mistake and isn't one."""
    if not isinstance(item, dict) or not isinstance(item.get("entry"), str):
        return None
    entry = _stripped(item["entry"], prefixes)
    if not entry or len(entry) > MAX_PATH_CHARS or fits_path_rule(entry):
        return None
    parts = entry.split("/")
    if len(parts) > MAX_PATH_PARTS or {"", ".", ".."} & set(parts):
        return None
    if _in_skipped_folder(parts[:-1]):
        return None
    path = os.path.join(root, entry)
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
    except (OSError, ValueError):
        return None
    if not _under(os.path.realpath(path), root):
        return None
    return entry


def _checked_pairs(
    root: str, prefixes: tuple[str, ...], agents: list[Any]
) -> tuple[tuple[tuple[str, str], ...], int, tuple[str, ...]]:
    kept: list[tuple[str, str]] = []
    entries: set[str] = set()
    uncarried: list[str] = []
    refused = 0
    for item in agents:
        pair = _checked_pair(root, prefixes, item)
        if pair is None:
            refused += 1
            entry = _uncarried_entry(root, prefixes, item)
            if entry is not None:
                uncarried.append(entry)
        elif pair[1] not in entries:
            entries.add(pair[1])
            kept.append(pair)
    return tuple(kept), refused, tuple(uncarried)


def _submit_server(source: Path, answer: _Answer) -> ToolServer:
    root = os.path.realpath(str(source))
    prefixes = tuple(
        dict.fromkeys((str(source).rstrip("/") + "/", root.rstrip("/") + "/", "./"))
    )

    async def submit_agents(arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            if answer.pairs:
                return _ok(SUBMIT_AFTER_SUBMIT)
            agents = arguments.get("agents")
            if not isinstance(agents, list) or len(agents) > MAX_PAIRS:
                return _error(_BAD_SUBMIT)
            kept, refused, uncarried = await _in_thread(
                _checked_pairs, root, prefixes, agents
            )
            # Another call may have finished while this one was checking. Nothing awaits
            # between this check and the set below, so two calls of one reply, run at once,
            # can never both keep their pairs.
            if answer.pairs:
                return _ok(SUBMIT_AFTER_SUBMIT)
            answer.called = True
            answer.uncarried.update(uncarried)
            if kept:
                answer.pairs = kept
                answer.refused = refused
                return _ok(
                    f"Recorded {len(kept)}; {refused} refused. "
                    "Your work is done: end your turn now."
                )
            if uncarried:
                return _ok(
                    f"Recorded 0; {refused} refused. {len(set(uncarried))} name a file whose "
                    "path the platform can't carry: a path may hold only letters, digits, '.', "
                    "'_', '-' and '/'. Those agents can't be listed, so don't submit them "
                    "again. Submit any others, or end your turn."
                )
            return _ok(
                f"Recorded 0; {refused} refused. Each entry must be a file inside the "
                "repository and inside its folder. Submit again with corrected paths, or end "
                "your turn if there are no agents."
            )
        except asyncio.TimeoutError:
            return _error(_TOO_SLOW)
        except Exception:
            logger.warning("find_agents: a tool call failed", exc_info=True)
            return _error(_FAILED)

    return ToolServer(
        name="scan",
        tools=[
            ToolSpec(
                name="submit_agents",
                description=(
                    "Submit the agents you found, once: each a folder (\"\" for the root) and "
                    "the entry file inside it, both relative to the repository's root. An empty "
                    "list means there are none."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "agents": {
                            "type": "array",
                            "maxItems": MAX_PAIRS,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "folder": {"type": "string"},
                                    "entry": {"type": "string"},
                                },
                                "required": ["folder", "entry"],
                            },
                        }
                    },
                    "required": ["agents"],
                },
                handler=submit_agents,
            )
        ],
    )
