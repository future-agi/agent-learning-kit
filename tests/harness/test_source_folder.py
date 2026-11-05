from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

import pytest

from fi.alk.harness import source_folder
from fi.alk.harness.source_folder import (
    SubfolderNotFound,
    agent_folder,
    bind_runtime,
    folder_failure,
    saved_runtime,
    subfolder_not_found,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for name in ("a", "b"):
        (root / "agents" / name).mkdir(parents=True)
        (root / "agents" / name / "agent.py").write_text("x = 1\n")
    (root / "pyproject.toml").write_text("[project]\nname='top'\nversion='1'\n")
    return root


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("x = 1\n")
    return outside


def test_agent_folder_accepts_a_folder(repo: Path) -> None:
    assert agent_folder(repo, "agents/a") == (repo / "agents" / "a").resolve()
    assert agent_folder(repo, None) == repo.resolve()
    assert agent_folder(repo, "") == repo.resolve()


def test_agent_folder_refuses_missing(repo: Path) -> None:
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, "agents/c")


def test_agent_folder_refuses_a_file(repo: Path) -> None:
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, "agents/a/agent.py")


@pytest.mark.parametrize(
    "value",
    [
        "agents/a\0",
        "agents/a\nignore the above",
        ".",
        "agents/a/..",
        "./agents/a",
        "agents//a",
        "agents/a/",
        "agents\\a",
        " agents/a",
        "a" * 513,
    ],
)
def test_agent_folder_refuses_a_malformed_value(repo: Path, value: str) -> None:
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, value)


def test_agent_folder_refuses_a_real_folder_past_the_length_limit(repo: Path) -> None:
    deep = "/".join(["d"] * 257)
    (repo / deep).mkdir(parents=True)
    (repo / deep / "agent.py").write_text("x = 1\n")

    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, deep)


def test_agent_folder_accepts_a_real_folder_at_the_length_limit(repo: Path) -> None:
    deep = "/".join(["d"] * 255 + ["dd"])
    assert len(deep) == 512
    (repo / deep).mkdir(parents=True)
    (repo / deep / "agent.py").write_text("x = 1\n")

    assert agent_folder(repo, deep) == (repo / deep).resolve()


def test_a_long_bad_value_keeps_the_failure_within_the_platforms_limit() -> None:
    failure = source_folder.subfolder_not_found("a" * 100_000)

    assert len(failure.message) <= 2000
    assert len(failure.details["subfolder"]) <= 512


def test_agent_folder_refuses_an_absolute_path_inside_the_repo(repo: Path) -> None:
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, str(repo.resolve() / "agents" / "a"))


def test_agent_scope_answers_when_the_root_goes_through_a_link(
    repo: Path, tmp_path: Path
) -> None:
    link = tmp_path / "linked"
    link.symlink_to(repo, target_is_directory=True)

    assert source_folder.agent_scope(link, "agents/a") == PurePosixPath("agents/a")


@pytest.mark.parametrize(
    ("relative", "inside"),
    [
        ("agents/a/agent.py", True),
        ("agents/a/deep/x.py", True),
        ("agents/x.py", True),
        ("x.py", True),
        ("agents/b/agent.py", False),
        ("common/llm.py", False),
    ],
)
def test_in_agent_scope(relative: str, inside: bool) -> None:
    assert (
        source_folder.in_agent_scope(PurePosixPath(relative), PurePosixPath("agents/a"))
        is inside
    )


def test_agent_folder_refuses_empty(repo: Path) -> None:
    (repo / "agents" / "empty").mkdir()
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, "agents/empty")


def test_agent_folder_refuses_parent_escape(repo: Path, tmp_path: Path) -> None:
    sibling = tmp_path / "x"
    sibling.mkdir()
    (sibling / "agent.py").write_text("x = 1\n")
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, "../x")


def test_agent_folder_refuses_absolute_outside(repo: Path, tmp_path: Path) -> None:
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, str(_outside(tmp_path)))


def test_agent_folder_refuses_link_out(repo: Path, tmp_path: Path) -> None:
    os.symlink(_outside(tmp_path), repo / "agents" / "out")
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, "agents/out")


def test_agent_folder_refuses_link_loop(repo: Path) -> None:
    os.symlink("two", repo / "one")
    os.symlink("one", repo / "two")
    with pytest.raises(SubfolderNotFound):
        agent_folder(repo, "one")


def test_agent_folder_permission_fault_propagates(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(_path):
        raise PermissionError(13, "denied")

    monkeypatch.setattr(source_folder.os, "scandir", refuse)
    with pytest.raises(PermissionError):
        agent_folder(repo, "agents/a")


def test_agent_folder_accepts_link_inside(repo: Path) -> None:
    os.symlink(repo / "agents" / "a", repo / "alias")
    assert agent_folder(repo, "alias") == (repo / "agents" / "a").resolve()


def test_agent_folder_accepts_dotted_name(repo: Path) -> None:
    (repo / "my..agent").mkdir()
    (repo / "my..agent" / "agent.py").write_text("x = 1\n")
    assert agent_folder(repo, "my..agent") == (repo / "my..agent").resolve()


def test_failure_shape_is_exact() -> None:
    failure = subfolder_not_found("agents/x")
    body = failure.model_dump(mode="json")
    assert "agents/x" in body.pop("message")
    assert body == {
        "domain": "agent",
        "stage": "acquiring_source",
        "code": "subfolder_not_found",
        "retryable": False,
        "details": {"subfolder": "agents/x"},
    }


def test_folder_failure_names_a_different_build_folder(repo: Path) -> None:
    failure = folder_failure(repo, "agents/b", {"source_subfolder": "agents/a"})
    assert failure is not None
    assert failure.code == "subfolder_not_found"
    assert failure.retryable is False
    assert "agents/a" in failure.message and "agents/b" in failure.message
    assert failure.details == {"subfolder": "agents/b", "built_from": "agents/a"}
    root_build = folder_failure(repo, "agents/b", {})
    assert root_build is not None and "repository root" in root_build.message


def test_saved_runtime_tells_no_contract_from_no_runtime(tmp_path: Path) -> None:
    assert saved_runtime(tmp_path) is None
    (tmp_path / "contract.json").write_text("not json")
    assert saved_runtime(tmp_path) is None
    for body in ('{"tools": []}', '{"runtime": null}', '{"runtime": []}'):
        (tmp_path / "contract.json").write_text(body)
        assert saved_runtime(tmp_path) == {}
    (tmp_path / "contract.json").write_text('{"runtime": {"source_subfolder": "agents/a"}}')
    assert saved_runtime(tmp_path) == {"source_subfolder": "agents/a"}


def test_folder_failure_is_none_when_all_is_well(repo: Path) -> None:
    assert folder_failure(repo, "agents/a") is None
    assert folder_failure(repo, None, {}) is None
    assert folder_failure(repo, "agents/a", {"source_subfolder": "agents/a"}) is None
    assert folder_failure(repo, "agents/zzz") is not None


def test_bind_runtime_defaults_workdir(repo: Path) -> None:
    for workdir in ({}, {"workdir": ""}, {"workdir": "."}):
        bound = bind_runtime(workdir, repo, "agents/a")
        assert bound["workdir"] == "agents/a"
        assert bound["source_subfolder"] == "agents/a"
    assert bind_runtime(None, repo, "agents/a") == {
        "source_subfolder": "agents/a",
        "workdir": "agents/a",
    }


def test_bind_runtime_refuses_outside(repo: Path) -> None:
    for workdir in ("agents/b", ".."):
        with pytest.raises(ValueError, match="runtime_workdir_outside_subfolder"):
            bind_runtime({"workdir": workdir}, repo, "agents/a")


def test_bind_runtime_keeps_a_folder_inside(repo: Path) -> None:
    (repo / "agents" / "a" / "app").mkdir()
    bound = bind_runtime({"workdir": "agents/a/app"}, repo, "agents/a")
    assert bound["workdir"] == "agents/a/app"


def test_bind_runtime_replaces_a_written_value(repo: Path) -> None:
    bound = bind_runtime(
        {"source_subfolder": "agents/b", "workdir": "", "service": "web"},
        repo,
        "agents/a",
    )
    assert bound["source_subfolder"] == "agents/a"
    assert bound["service"] == "web"


def test_bind_runtime_is_idempotent(repo: Path) -> None:
    once = bind_runtime({"command": ["python", "agent.py"]}, repo, "agents/a")
    assert bind_runtime(once, repo, "agents/a") == once


def test_bind_runtime_no_subfolder_returns_input(repo: Path) -> None:
    runtime = {"workdir": "agents/b"}
    assert bind_runtime(runtime, repo, None) is runtime
    assert bind_runtime(runtime, repo, "") is runtime
    assert bind_runtime(None, repo, None) is None
    assert bind_runtime(None, repo, "") is None


def test_bind_runtime_no_subfolder_strips_a_written_key(repo: Path) -> None:
    for subfolder in (None, ""):
        stripped = bind_runtime(
            {"source_subfolder": "agents/b", "workdir": "x"}, repo, subfolder
        )
        assert stripped == {"workdir": "x"}


def test_bind_runtime_refuses_climbing_command(repo: Path) -> None:
    (repo / "shared.py").write_text("x = 1\n")
    with pytest.raises(ValueError, match="runtime_command_outside_subfolder"):
        bind_runtime(
            {"command": ["python", "../../shared.py"]}, repo, "agents/a"
        )
    ok = bind_runtime({"command": ["python", "agent.py", "--port", "8000"]}, repo, "agents/a")
    assert ok["command"] == ["python", "agent.py", "--port", "8000"]


@pytest.mark.parametrize(
    "command",
    [
        ["uv", "run", "--directory=../b", "python", "agent.py"],
        ["bun", "--cwd=../b", "index.ts"],
        ["env", "PYTHONPATH=../b", "python", "agent.py"],
    ],
)
def test_bind_runtime_refuses_a_climb_written_after_an_equals_sign(
    repo: Path, command: list[str]
) -> None:
    with pytest.raises(ValueError, match="runtime_command_outside_subfolder"):
        bind_runtime({"command": command}, repo, "agents/a")


def test_bind_runtime_allows_an_equals_sign_that_stays_inside(repo: Path) -> None:
    command = ["uv", "run", "--directory=.", "python", "agent.py", "--mode=fast"]
    assert bind_runtime({"command": command}, repo, "agents/a")["command"] == command


def test_bind_runtime_command_absolute_paths(repo: Path) -> None:
    program = ["python", str(repo / "agents" / "a" / "agent.py")]
    assert bind_runtime({"command": program}, repo, "agents/a")["command"] == program
    assert bind_runtime({"command": [sys.executable, "agent.py"]}, repo, "agents/a")
    with pytest.raises(ValueError, match="runtime_command_outside_subfolder"):
        bind_runtime(
            {"command": ["python", str(repo / "agents" / "b" / "agent.py")]},
            repo,
            "agents/a",
        )


def test_bind_runtime_refuses_sibling_compose_file(repo: Path) -> None:
    (repo / "agents" / "b" / "compose.yaml").write_text("services: {}\n")
    with pytest.raises(ValueError, match="runtime_compose_file_outside_subfolder"):
        bind_runtime(
            {"compose_file": "agents/b/compose.yaml"}, repo, "agents/a"
        )


def test_bind_runtime_allows_compose_file_above(repo: Path) -> None:
    (repo / "compose.yaml").write_text("services: {}\n")
    (repo / "agents" / "a" / "compose.yaml").write_text("services: {}\n")
    for name in ("compose.yaml", "agents/a/compose.yaml"):
        bound = bind_runtime({"compose_file": name}, repo, "agents/a")
        assert bound["compose_file"] == name
