"""Under per-user isolation the bundle's actor files are the harness's alone: mode 0600, owned by the harness, before
any customer process starts. Grader files and runs without per-user isolation are left as they were."""

from __future__ import annotations

import os
import pwd
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

from fi.alk.harness import hosted_entrypoint as he
from fi.alk.harness import privilege, user_files
from fi.alk.harness.privilege import IsolationMode

ACTOR_FILES = ("actors.json", "actor_rules.json")
GRADER_FILES = ("scenarios.json", "sub_goals.json", "certification.json", "runtime-validation.json")


def _bundle(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name in (*ACTOR_FILES, *GRADER_FILES):
        (root / name).write_text(f'{{"file": "{name}"}}', encoding="utf-8")
        (root / name).chmod(0o644)
    return root


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class _Chown:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int, int]] = []

    def __call__(self, path, uid, gid, *, dir_fd=None) -> None:
        self.calls.append((Path(path).name, uid, gid))
        user_files.chown_no_follow(path, uid, gid, dir_fd=dir_fd)


def test_per_user_isolation_leaves_actor_files_to_the_harness_alone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)
    bundle = _bundle(tmp_path / "bundle")
    chown = _Chown()
    he.close_actor_files(bundle, chown=chown)
    assert chown.calls == [(name, os.geteuid(), os.getegid()) for name in ACTOR_FILES]
    for name in ACTOR_FILES:
        path = bundle / name
        assert _mode(path) == 0o600
        assert path.stat().st_uid == os.geteuid()
        # The harness, as owner, still reads it.
        assert path.read_text(encoding="utf-8") == f'{{"file": "{name}"}}'
    for name in GRADER_FILES:
        assert _mode(bundle / name) == 0o644


def test_without_per_user_isolation_nothing_changes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(privilege, "_mode", IsolationMode.FIXED_USER)
    bundle = _bundle(tmp_path / "bundle")
    chown = _Chown()
    he.close_actor_files(bundle, chown=chown)
    assert chown.calls == []
    assert {name: _mode(bundle / name) for name in (*ACTOR_FILES, *GRADER_FILES)} == dict.fromkeys(
        (*ACTOR_FILES, *GRADER_FILES), 0o644
    )


def test_a_bundle_without_actors_or_with_a_linked_one_is_left_alone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("{}", encoding="utf-8")
    elsewhere.chmod(0o644)
    (bundle / "actors.json").symlink_to(elsewhere)
    chown = _Chown()
    he.close_actor_files(bundle, chown=chown)
    assert chown.calls == []
    assert _mode(elsewhere) == 0o644


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="switching users needs root")
def test_another_os_user_cannot_read_the_actor_files(monkeypatch) -> None:
    try:
        other = pwd.getpwnam("nobody")
    except KeyError:
        pytest.skip("no `nobody` user on this machine")
    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)
    # Every folder above the bundle lets the other user through, so only the files' own modes decide.
    root = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        root.chmod(0o755)
        bundle = _bundle(root / "bundle")
        bundle.chmod(0o755)
        he.close_actor_files(bundle)
        for name in ACTOR_FILES:
            read = subprocess.run(["cat", str(bundle / name)], user=other.pw_uid, capture_output=True, text=True)
            assert read.returncode != 0 and "Permission denied" in read.stderr
        read = subprocess.run(["cat", str(bundle / "scenarios.json")], user=other.pw_uid, capture_output=True)
        assert read.returncode == 0
    finally:
        shutil.rmtree(root)


def test_a_hosted_run_closes_the_actor_files_before_any_world_is_provisioned(monkeypatch) -> None:
    from test_hosted_entrypoint import _build_harness, _run

    monkeypatch.setattr(privilege, "_mode", IsolationMode.PER_USER)
    harness = _build_harness(scenarios=[])
    loaded = harness.deps.bundle_source

    class BundleSource:
        def load(self, *args, **kwargs):
            manifest, directory = loaded.load(*args, **kwargs)
            for name in ACTOR_FILES:
                (directory / name).write_text("{}", encoding="utf-8")
                (directory / name).chmod(0o644)
            seen["bundle"] = directory
            return manifest, directory

    seen: dict[str, object] = {}
    harness.deps.bundle_source = BundleSource()
    monkeypatch.setattr(he, "preflight_bundle", lambda *args, **kwargs: None)
    provision = harness.provisioner.provision

    async def provisioned(*args, **kwargs):
        seen["modes"] = [_mode(seen["bundle"] / name) for name in ACTOR_FILES]
        return await provision(*args, **kwargs)

    monkeypatch.setattr(harness.provisioner, "provision", provisioned)
    assert _run(harness) == he.EXIT_OK
    assert seen["modes"] == [0o600, 0o600]
