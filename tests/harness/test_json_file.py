"""A JSON file is written whole or not at all."""

from __future__ import annotations

import json
import os

import pytest

from fi.alk.harness import json_file
from fi.alk.harness.json_file import write_json_atomic


def test_the_file_is_written_whole_in_a_folder_made_for_it(tmp_path):
    path = tmp_path / "made" / "proofs.json"

    write_json_atomic(path, {"b": 1, "a": {"d": 2, "c": 3}})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": {"c": 3, "d": 2}, "b": 1}
    assert path.read_text(encoding="utf-8").index('"a"') < path.read_text(encoding="utf-8").index('"b"')
    assert oct(path.stat().st_mode & 0o777) == oct(0o644)
    assert [one.name for one in path.parent.iterdir()] == ["proofs.json"]


def test_a_failed_write_keeps_the_old_file_and_leaves_nothing_behind(tmp_path, monkeypatch):
    path = tmp_path / "proofs.json"
    write_json_atomic(path, {"kept": True})

    def refuse(_source, _target):
        raise OSError("disk full")

    monkeypatch.setattr(json_file.os, "replace", refuse)
    with pytest.raises(OSError):
        write_json_atomic(path, {"kept": False})

    assert json.loads(path.read_text(encoding="utf-8")) == {"kept": True}
    assert sorted(os.listdir(tmp_path)) == ["proofs.json"]


@pytest.mark.parametrize(("umask", "mode"), [(0o022, 0o644), (0o077, 0o600)], ids=["default", "per-user-isolation"])
def test_the_file_takes_the_mode_a_plain_write_gets_under_the_umask(tmp_path, umask, mode):
    before = os.umask(umask)
    try:
        write_json_atomic(tmp_path / "proofs.json", {})
    finally:
        os.umask(before)
    assert oct((tmp_path / "proofs.json").stat().st_mode & 0o777) == oct(mode)
