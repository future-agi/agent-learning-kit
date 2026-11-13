"""user_files: where the harness expects a file of its own, it never opens a link or a FIFO, and it
gives a folder away without entering a link."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from fi.alk.harness import user_files


def _within(seconds: float, call):
    """``call``'s result or exception, failing the test if it is still blocked after ``seconds``."""
    outcome: dict[str, Any] = {}

    def target() -> None:
        try:
            outcome["value"] = call()
        except BaseException as exc:  # noqa: BLE001 - handed back to the test
            outcome["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(seconds)
    assert not thread.is_alive(), "blocked opening a FIFO"
    return outcome


# --- writing a log -------------------------------------------------------------------------------


def test_a_fifo_where_a_log_goes_is_replaced_not_waited_on(tmp_path):
    log = tmp_path / "process.log"
    os.mkfifo(log)

    outcome = _within(5, lambda: user_files.open_log(log))

    with outcome["value"] as handle:
        handle.write(b"from the agent")
    assert log.is_file() and log.read_bytes() == b"from the agent"


def test_a_log_of_its_own_is_emptied_in_place(tmp_path):
    log = tmp_path / "process.log"
    log.write_text("the last run\n")
    inode = log.stat().st_ino

    with user_files.open_log(log) as handle:
        handle.write(b"this run\n")

    assert log.read_text() == "this run\n" and log.stat().st_ino == inode


def test_a_log_handed_to_a_child_blocks_like_any_file(tmp_path):
    with user_files.open_log(tmp_path / "process.log") as handle:
        assert os.get_blocking(handle.fileno())


# --- reading one back ----------------------------------------------------------------------------


def test_a_fifo_read_back_fails_like_a_missing_file(tmp_path):
    log = tmp_path / "process.log"
    os.mkfifo(log)

    outcome = _within(5, lambda: user_files.read_text(log))

    assert isinstance(outcome.get("error"), OSError)


@pytest.mark.parametrize("link", [os.symlink, os.link], ids=["symlink", "hard link"])
def test_a_link_read_back_fails_like_a_missing_file(tmp_path, link):
    case = tmp_path / "case.yaml"
    case.write_text("held-back case\n")
    log = tmp_path / "process.log"
    link(case, log)

    with pytest.raises(OSError):
        user_files.read_text(log)


def test_a_file_of_its_own_reads_back_whole(tmp_path):
    log = tmp_path / "process.log"
    log.write_bytes(b"line one\nnot utf-8: \xff\n")

    assert user_files.read_text(log) == "line one\nnot utf-8: �\n"


# --- giving a folder away ------------------------------------------------------------------------


def _recorder(chown_path, calls: list[Path]):
    def chown(path, uid, gid, dir_fd=None):
        calls.append(chown_path(path, dir_fd))

    return chown


def test_a_file_with_another_name_is_never_given_away(tmp_path, chown_path):
    tree = tmp_path / "world"
    tree.mkdir()
    (tree / "own.txt").write_text("")
    harness_file = tmp_path / "harness.txt"
    harness_file.write_text("")
    os.link(harness_file, tree / "linked.txt")
    calls: list[Path] = []

    user_files.own_tree(tree, uid=1, gid=1, chown=_recorder(chown_path, calls))

    assert sorted(calls) == [tree, tree / "own.txt"]


def test_an_entry_removed_while_the_walk_runs_is_passed_over(tmp_path, chown_path):
    tree = tmp_path / "world"
    tree.mkdir()
    for name in ("a", "b"):
        (tree / name).write_text("")
    calls: list[Path] = []

    def chown(path, uid, gid, dir_fd=None):
        target = chown_path(path, dir_fd)
        calls.append(target)
        if target.name in ("a", "b"):
            # The process removes its other file between the walk's listing and its turn.
            (tree / ({"a": "b", "b": "a"}[target.name])).unlink(missing_ok=True)

    user_files.own_tree(tree, uid=1, gid=1, chown=chown)

    assert len(calls) == 2  # the folder and the one file still there


def test_a_folder_given_away_must_not_itself_be_a_link(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / "world").symlink_to(real, target_is_directory=True)

    with pytest.raises(OSError):
        user_files.own_tree(tmp_path / "world", uid=1, gid=1, chown=lambda *a, **k: None)
