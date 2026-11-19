"""Asks TH-8805's framework check about one agent folder at a time, in a child the scan can stop.

`detect_framework` bounds its work, not its time, so one slow folder could hold the scan past
its clock. In a child process a check can be abandoned at the deadline by killing it, and a
child the system kills costs that folder's word rather than the whole scan.
"""

from __future__ import annotations

import logging
import traceback
import multiprocessing
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from pathlib import Path
from typing import Any

from .forkserver_preload import PRELOAD
from .framework_detection import detect_framework
from .reading_entry import MaskSecrets
from .source_folder import SubfolderNotFound, agent_folder

logger = logging.getLogger(__name__)
logger.addFilter(MaskSecrets())

# Loaded once in the fork server, so a restarted child doesn't import the kit again.
_PRELOAD = PRELOAD
# A killed child is gone almost at once; the bound only stops a wedged reap holding the scan.
_JOIN_SECONDS = 5.0
# The key of a child's answer that carries a failed check's trace instead of a framework.
_FAILED = "failed"


@dataclass(frozen=True)
class FolderWord:
    framework: dict[str, Any]
    finished: bool


class FolderCheckerBroken(Exception):
    """The child couldn't start, or ended without an answer other than by a signal."""


class ClockRanOut(Exception):
    """The scan's deadline passed before a folder's answer came back."""


def _unknown() -> dict[str, Any]:
    return {"name": "unknown", "version": None, "label": None}


def _answer(source: Path, subfolder: str) -> dict[str, Any]:
    try:
        # The build's own call for a named folder. Nothing is installed during a scan, so
        # there is no package folder to read.
        return detect_framework(
            agent_folder(source, subfolder) if subfolder else source,
            repo_root=source,
            package_dir=None,
            raise_faults=True,
        )
    except SubfolderNotFound:
        # A folder the build would refuse has no word; it is not a reason to end the child.
        return {"name": "unknown", "version": None}
    except Exception:
        # A fault in the check itself. The child stays up for the next folder, and sends the
        # trace so the parent logs it, with only the platform's secret values masked, and marks
        # the row unfinished.
        return {_FAILED: traceback.format_exc()}


def _serve(conn: Connection) -> None:
    """The child's loop: one answer per request, until the parent's end closes."""
    while True:
        try:
            source, subfolder = conn.recv()
            conn.send(_answer(Path(source), subfolder))
        except (EOFError, OSError):
            return


def _framework(answer: Any) -> dict[str, Any]:
    """The answer's framework, in the platform's shape; anything malformed is ``unknown``."""
    name = answer.get("name") if isinstance(answer, dict) else None
    if not isinstance(name, str):
        return _unknown()
    label = answer.get("label") if name == "other" else None
    return {
        "name": name,
        "version": None,
        "label": label if isinstance(label, str) else None,
    }


class FolderChecker:
    """One child process, started on the first check, answering for one folder at a time.

    Answers are kept per folder for the life of the checker, since agents that share a folder
    get the same word and a folder whose check killed one child would likely kill the next.
    """

    pid: int | None

    def __init__(self) -> None:
        self.pid = None
        self._process: BaseProcess | None = None
        self._conn: Connection | None = None
        self._words: dict[tuple[str, str], FolderWord] = {}

    def __enter__(self) -> FolderChecker:
        return self

    def __exit__(self, *exc: object) -> None:
        self._end_child()

    def check(
        self,
        source: Path,
        subfolder: str,
        *,
        deadline: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> FolderWord:
        if deadline - clock() <= 0:
            raise ClockRanOut("The scan's clock ran out before this folder was checked.")
        key = (os.fspath(source), subfolder)
        known = self._words.get(key)
        if known is not None:
            return FolderWord(dict(known.framework), known.finished)
        if self._process is None:
            self._start()
        conn = self._conn
        assert conn is not None
        try:
            conn.send((source, subfolder))
        except OSError:
            return self._keep(key, self._ended())
        # A deadline that passed while the child started still ends it: a late answer isn't taken.
        if not conn.poll(max(deadline - clock(), 0.0)):
            self._end_child()
            logger.warning("folder checker: the clock ran out, child ended")
            raise ClockRanOut("The scan's clock ran out while a folder was being checked.")
        try:
            answer = conn.recv()
        except (EOFError, OSError):
            return self._keep(key, self._ended())
        if isinstance(answer, dict) and _FAILED in answer:
            logger.warning("folder checker: the check failed in the child\n%s", answer[_FAILED])
            return self._keep(key, FolderWord(_unknown(), finished=False))
        return self._keep(key, FolderWord(_framework(answer), finished=True))

    def _keep(self, key: tuple[str, str], word: FolderWord) -> FolderWord:
        self._words[key] = word
        return FolderWord(dict(word.framework), word.finished)

    def _start(self) -> None:
        parent: Connection | None = None
        child: Connection | None = None
        try:
            context = multiprocessing.get_context("forkserver")
            context.set_forkserver_preload(_PRELOAD)
            parent, child = context.Pipe()
            process = context.Process(target=_serve, args=(child,), daemon=True)
            process.start()
        except Exception as error:
            if parent is not None:
                parent.close()
            logger.warning("folder checker: the child could not start", exc_info=True)
            raise FolderCheckerBroken(
                "The folder checker's child process could not start."
            ) from error
        finally:
            # The child has its own copy. Closing ours lets a dead child read as end of file.
            if child is not None:
                child.close()
        self._process, self._conn, self.pid = process, parent, process.pid

    def _ended(self) -> FolderWord:
        """The child stopped answering: a signal costs this folder's word, anything else is broken."""
        process = self._process
        assert process is not None
        code: int | None = None
        try:
            # The exit code is only known once the child is reaped.
            process.join(_JOIN_SECONDS)
            code = process.exitcode
        except (OSError, ValueError, AssertionError):
            # multiprocessing raises these for a child that is already closed or never started.
            logger.warning("folder checker: the child's exit code could not be read", exc_info=True)
            code = None
        self._end_child()
        if code is not None and code < 0:
            logger.warning("folder checker: child ended by signal %d", -code)
            return FolderWord(_unknown(), finished=False)
        logger.warning("folder checker: child ended without an answer, exit code %s", code)
        if code is None:
            raise FolderCheckerBroken(
                "The folder checker's child process stopped answering and was ended."
            )
        raise FolderCheckerBroken(
            "The folder checker's child process ended without an answer, "
            f"with exit code {code}."
        )

    def _end_child(self) -> None:
        """Kills, reaps and forgets the child, if there is one. Never raises."""
        process, conn = self._process, self._conn
        self._process = self._conn = None
        self.pid = None
        if process is not None:
            try:
                process.kill()
                process.join(_JOIN_SECONDS)
                process.close()
            except Exception:
                logger.warning("folder checker: the child could not be ended", exc_info=True)
        if conn is not None:
            try:
                conn.close()
            except OSError:
                logger.debug("folder checker: the pipe was already closed", exc_info=True)
