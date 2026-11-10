from __future__ import annotations

import multiprocessing.process
import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness import scan_folders
from fi.alk.harness.framework_detection import detect_framework
from fi.alk.harness.scan_folders import (
    ClockRanOut,
    FolderChecker,
    FolderCheckerBroken,
    FolderWord,
)
from fi.alk.harness.source_folder import agent_folder

CREW = "from crewai import Crew\nCrew()\n"
AUTOGEN = "from autogen_agentchat.agents import AssistantAgent\nAssistantAgent()\n"
OPENAI_AGENT = "from agents import Agent\nAgent()\n"
PLAIN = "print('hello')\n"
SITE = ".venv/lib/python3.12/site-packages"
# The first check in a session also starts the fork server, which imports the kit.
FIRST_CHECK_SECONDS = 120.0
# Killing and reaping a stopped child, on a loaded machine.
KILL_MARGIN_SECONDS = 4.0
UNKNOWN = {"name": "unknown", "version": None, "label": None}
FOLDERS = {
    "": None,
    "crew": {"name": "crewai", "version": None, "label": None},
    "autogen": {"name": "other", "version": None, "label": "autogen"},
    "oa": {"name": "openai_agents", "version": None, "label": None},
    "plain": {"name": "none", "version": None, "label": None},
}


def _write(folder: Path, files: dict[str, str]) -> None:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def _repo(tmp_path: Path) -> Path:
    source = tmp_path / "repo"
    _write(
        source,
        {
            "crew/main.py": CREW,
            "autogen/main.py": AUTOGEN,
            "oa/main.py": OPENAI_AGENT,
            "plain/main.py": PLAIN,
            # Installed packages without openai-agents: a check that read them would drop
            # the `agents` import, so the word shows which package folder the child used.
            f"{SITE}/requests-2.0.dist-info/METADATA": "Version: 2.0\n",
            f"oa/{SITE}/requests-2.0.dist-info/METADATA": "Version: 2.0\n",
        },
    )
    return source


def _later(seconds: float) -> float:
    return time.monotonic() + seconds


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


def _kill_if_alive(pid: int | None) -> None:
    if pid is not None and not _gone(pid):
        os.kill(pid, signal.SIGKILL)


def _exits_three(conn: Any) -> None:
    os._exit(3)


def _refuses_to_start(self: Any) -> None:
    raise OSError("no processes left")


def test_each_folder_gets_the_builds_framework_word(tmp_path: Path) -> None:
    source = _repo(tmp_path)
    words: dict[str, FolderWord] = {}
    pids = set()
    with FolderChecker() as checker:
        for subfolder in FOLDERS:
            words[subfolder] = checker.check(
                source, subfolder, deadline=_later(FIRST_CHECK_SECONDS)
            )
            pids.add(checker.pid)

    for subfolder, expected in FOLDERS.items():
        answer = detect_framework(
            agent_folder(source, subfolder) if subfolder else source,
            repo_root=source,
            package_dir=None,
        )
        assert words[subfolder] == FolderWord(
            {"name": answer["name"], "version": None, "label": answer.get("label")},
            finished=True,
        )
        assert set(words[subfolder].framework) == {"name", "version", "label"}
        if expected is not None:
            assert words[subfolder].framework == expected
    assert len(pids) == 1
    assert None not in pids and os.getpid() not in pids


def test_the_clock_ends_a_stopped_child(tmp_path: Path) -> None:
    source = _repo(tmp_path)
    stopped: int | None = None
    try:
        with FolderChecker() as checker:
            checker.check(source, "crew", deadline=_later(FIRST_CHECK_SECONDS))
            stopped = checker.pid
            assert stopped is not None
            os.kill(stopped, signal.SIGSTOP)

            started = time.monotonic()
            with pytest.raises(ClockRanOut):
                checker.check(source, "plain", deadline=started + 1.0)

            assert time.monotonic() - started < 1.0 + KILL_MARGIN_SECONDS
            assert checker.pid is None
            assert _gone(stopped)

            with pytest.raises(ClockRanOut):
                checker.check(source, "autogen", deadline=time.monotonic() - 1.0)
            assert checker.pid is None

            word = checker.check(source, "plain", deadline=_later(FIRST_CHECK_SECONDS))
            assert word == FolderWord(FOLDERS["plain"], finished=True)
            assert checker.pid not in (None, stopped)
    finally:
        _kill_if_alive(stopped)


def test_the_child_is_restarted_after_a_kill(tmp_path: Path) -> None:
    source = _repo(tmp_path)
    killed: int | None = None
    try:
        with FolderChecker() as checker:
            first = checker.check(source, "crew", deadline=_later(FIRST_CHECK_SECONDS))
            assert first == FolderWord(FOLDERS["crew"], finished=True)
            killed = checker.pid
            assert killed is not None
            os.kill(killed, signal.SIGKILL)

            word = checker.check(source, "autogen", deadline=_later(FIRST_CHECK_SECONDS))

            assert word == FolderWord(UNKNOWN, finished=False)
            assert _gone(killed)

            again = checker.check(source, "plain", deadline=_later(FIRST_CHECK_SECONDS))
            assert again == FolderWord(FOLDERS["plain"], finished=True)
            assert checker.pid not in (None, killed)
    finally:
        _kill_if_alive(killed)


def test_a_child_that_cannot_start_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _repo(tmp_path)

    # The target is looked up when the child starts, so the child runs this one and exits 3.
    monkeypatch.setattr(scan_folders, "_serve", _exits_three)
    with FolderChecker() as checker:
        with pytest.raises(FolderCheckerBroken, match="exit code 3"):
            checker.check(source, "crew", deadline=_later(FIRST_CHECK_SECONDS))
        assert checker.pid is None
    monkeypatch.undo()

    monkeypatch.setattr(multiprocessing.process.BaseProcess, "start", _refuses_to_start)
    with FolderChecker() as checker:
        with pytest.raises(FolderCheckerBroken, match="could not start"):
            checker.check(source, "crew", deadline=_later(FIRST_CHECK_SECONDS))
        assert checker.pid is None


def test_exit_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _repo(tmp_path)

    assert FolderChecker().__exit__(None, None, None) is None

    with FolderChecker() as checker:
        checker.check(source, "crew", deadline=_later(FIRST_CHECK_SECONDS))
        ended = checker.pid
        assert ended is not None
    assert checker.pid is None
    assert _gone(ended)
    assert checker.__exit__(None, None, None) is None

    stuck = FolderChecker()
    stuck.check(source, "crew", deadline=_later(FIRST_CHECK_SECONDS))
    alive = stuck.pid
    assert alive is not None
    real_kill = os.kill

    def refuse(pid: int, sig: int) -> None:
        raise PermissionError("not allowed")

    monkeypatch.setattr(os, "kill", refuse)
    try:
        assert stuck.__exit__(None, None, None) is None
        assert stuck.pid is None
    finally:
        monkeypatch.undo()
        try:
            real_kill(alive, signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_a_fault_in_the_check_comes_back_as_a_trace(tmp_path, monkeypatch) -> None:
    """Only a folder the build refuses is a plain ``unknown``; any other fault is sent back
    with its trace, so the parent logs it and marks the row unfinished."""

    def broken(*args, **kwargs):
        raise KeyError("a fault in the check")

    monkeypatch.setattr(scan_folders, "detect_framework", broken)
    answer = scan_folders._answer(tmp_path, "")
    assert set(answer) == {scan_folders._FAILED}
    assert "KeyError" in answer[scan_folders._FAILED]

    assert scan_folders._answer(tmp_path, "no/such/folder") == {"name": "unknown", "version": None}
