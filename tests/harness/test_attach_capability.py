"""Attaching a capability copies it once and never undoes what the vertical added to the copy."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from fi.alk import environments
from fi.alk.environments import attach_capability

ROOT = Path(environments.__file__).parent


def _files(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()
    }


@pytest.fixture
def vertical(tmp_path: Path) -> Path:
    """A package of its own with no capability attached yet."""
    here = tmp_path / "desk"
    (here / "handlers").mkdir(parents=True)
    (here / "tools.json").write_text("[]")
    (here / "policy.json").write_text('{"rules": []}')
    (here / "world.sql").write_text(
        "CREATE TABLE clock (\n    now TEXT PRIMARY KEY\n);\n"
    )
    return here


def test_attaching_again_changes_nothing(vertical: Path) -> None:
    capability = ROOT / "calendar_scheduling_desk"
    attach_capability.attach(vertical, capability)
    attached = _files(vertical)
    assert (
        attached["handlers/create_event.py"]
        == (capability / "handlers/create_event.py").read_bytes()
    )
    attach_capability.attach(vertical, capability)
    assert _files(vertical) == attached


@pytest.mark.parametrize("changed", ["handlers/create_event.py", "tools.json"])
def test_a_diverged_copy_is_not_overwritten(vertical: Path, changed: str) -> None:
    capability = ROOT / "calendar_scheduling_desk"
    attach_capability.attach(vertical, capability)
    attached = _files(vertical)
    path = vertical / changed
    path.write_text(
        path.read_text()
        .replace("Creates an event", "Books an event")
        .replace("def handle", "def  handle")
    )
    hardened = _files(vertical)
    with pytest.raises(attach_capability.Diverged, match="create_event"):
        attach_capability.attach(vertical, capability)
    assert _files(vertical) == hardened
    attach_capability.attach(vertical, capability, overwrite=True)
    assert _files(vertical) == attached


def test_the_command_refuses_a_hardened_vertical(
    monkeypatch, capsys, tmp_path: Path
) -> None:
    for package in ("hr", "slack_channel_digest"):
        shutil.copytree(ROOT / package, tmp_path / package)
    monkeypatch.setattr(
        attach_capability, "__file__", str(tmp_path / "attach_capability.py")
    )
    before = _files(tmp_path)
    with pytest.raises(SystemExit):
        attach_capability.main(["hr", "slack_channel_digest"])
    said = capsys.readouterr().err
    assert "handlers/slack_read_channel.py" in said and "--overwrite" in said
    assert _files(tmp_path) == before


def test_reading_create_statements_is_linear() -> None:
    sql = 'CREATE TABLE a (\n    b TEXT\n);\nCREATE TABLE "c" (d TEXT DEFAULT (1));\n'
    assert list(attach_capability.create_statements(sql)) == ["a", "c"]
    started = time.monotonic()
    assert attach_capability.create_statements("CREATE TABLE A(" + "\n" * 100_000) == {}
    assert time.monotonic() - started < 5
