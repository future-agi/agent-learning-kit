from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from fi.alk.harness import source_data_invariants as invariants


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "agents" / "a" / "tools").mkdir(parents=True)
    (root / "agents" / "b").mkdir(parents=True)
    (root / "db.py").write_text("x = 1\n")
    (root / "agents" / "shared.py").write_text("x = 2\n")
    (root / "agents" / "a" / "tools" / "orders.py").write_text("x = 3\n")
    (root / "agents" / "a" / "agent.py").write_text("x = 4\n")
    (root / "agents" / "b" / "agent.py").write_text("x = 5\n")
    return root


def _authoring(tmp_path: Path, folder: str | None) -> Path:
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    runtime = {"source_subfolder": folder} if folder else {}
    (authoring / "contract.json").write_text(json.dumps({"runtime": runtime}))
    return authoring


class _Stop(Exception):
    pass


def _watch(monkeypatch) -> list[str]:
    asked: list[str] = []

    def spy(root, folder=""):
        asked.append(folder)
        raise _Stop

    monkeypatch.setattr(invariants, "source_files", spy)
    return asked


def test_invariant_review_lists_only_folder_and_above(tmp_path, repo, monkeypatch):
    listed = invariants.source_files(repo, "agents/a")
    assert set(listed) == {
        "db.py",
        "agents/shared.py",
        "agents/a/agent.py",
        "agents/a/tools/orders.py",
    }

    # Both reviews read the folder the contract recorded.
    asked = _watch(monkeypatch)
    authoring = _authoring(tmp_path, "agents/a")
    with pytest.raises(_Stop):
        asyncio.run(invariants.author_invariants(repo, authoring, None))
    with pytest.raises(_Stop):
        asyncio.run(
            invariants.review_while_writing(
                repo, authoring, None, endpoints=None, world_key="w", done=None, expected=1
            )
        )
    assert asked == ["agents/a", "agents/a"]


def test_no_subfolder_lists_whole_repo(tmp_path, repo, monkeypatch):
    assert set(invariants.source_files(repo)) == {
        "db.py",
        "agents/shared.py",
        "agents/a/agent.py",
        "agents/a/tools/orders.py",
        "agents/b/agent.py",
    }

    asked = _watch(monkeypatch)
    with pytest.raises(_Stop):
        asyncio.run(invariants.author_invariants(repo, _authoring(tmp_path, None), None))
    assert asked == [""]


def test_a_linked_folder_is_listed_by_what_it_points_at(repo):
    os.symlink(repo / "agents" / "a", repo / "agents" / "alias")

    assert set(invariants.source_files(repo, "agents/alias")) == set(
        invariants.source_files(repo, "agents/a")
    )


def test_a_folder_that_cannot_be_resolved_is_taken_as_written(repo):
    assert set(invariants.source_files(repo, "agents/gone")) == {"db.py", "agents/shared.py"}
