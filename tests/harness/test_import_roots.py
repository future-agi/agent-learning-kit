from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import fi.alk
from fi.alk.harness import chat_call_runner
from fi.alk.harness.contract import AgentContract, Runtime
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.snapshot import save
from fi.alk.harness.world.tools import world_tools


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "repo"
    for name, value in (("a", 1), ("b", 2)):
        (root / "agents" / name).mkdir(parents=True)
        (root / "agents" / name / "helper_flat.py").write_text(f"VALUE = {value}\n")
    # Same flat name at the repository root: the agent's own folder has to win.
    (root / "helper_flat.py").write_text("VALUE = 9\n")
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield root
    for name in [name for name in sys.modules if name.split(".")[0] in {"helper_flat", "agents"}]:
        sys.modules.pop(name, None)


def _contract(subfolder: str) -> AgentContract:
    return AgentContract(
        agent="demo",
        runtime=Runtime(source_subfolder=subfolder, workdir=subfolder) if subfolder else None,
    )


class _Store:
    key = "fake"

    def start(self) -> None:
        pass


def test_import_roots_equal_at_build_certification_and_run(tmp_path, repo, monkeypatch):
    reached: list[tuple[str, str]] = []
    real = GeneratedWorld.reach

    def spy(self, source_root, subfolder=""):
        reached.append((source_root, subfolder))
        real(self, source_root, subfolder)

    monkeypatch.setattr(GeneratedWorld, "reach", spy)
    monkeypatch.setattr(chat_call_runner, "_HostedToolStore", lambda dsn: _Store())
    contract = _contract("agents/a")

    world_tools(contract, tmp_path / "build", source_root=str(repo), deferred_runtime=True)
    runtime = SimpleNamespace(
        metadata={},
        endpoints={"world_db": SimpleNamespace(protocol="postgres", address="unused")},
    )
    chat_call_runner._tool_world(tmp_path / "bundle", contract, runtime, repo)

    assert reached == [(str(repo), "agents/a")] * 2


def test_agent_module_imports_flat_and_qualified(repo):
    world = GeneratedWorld(":memory:", kind="sqlite")

    world.reach(str(repo), "agents/a")

    path = [Path(item) for item in sys.path]
    assert path.index(repo / "agents" / "a") < path.index(repo)
    assert importlib.import_module("helper_flat").VALUE == 1
    assert importlib.import_module("agents.b.helper_flat").VALUE == 2


def test_agent_package_folder_comes_before_its_parent(repo):
    (repo / "agents" / "a" / "__init__.py").write_text("")
    (repo / "agents" / "helper_flat.py").write_text("VALUE = 7\n")

    GeneratedWorld(":memory:", kind="sqlite").reach(str(repo), "agents/a")

    path = [Path(item) for item in sys.path]
    assert path.index(repo / "agents" / "a") < path.index(repo / "agents")
    assert importlib.import_module("helper_flat").VALUE == 1


def test_no_subfolder_import_roots_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    package = tmp_path / "repo" / "pkg"
    inner = package / "inner"
    inner.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (inner / "__init__.py").write_text("")

    GeneratedWorld(":memory:", kind="sqlite").reach(str(inner))

    assert [Path(item) for item in sys.path[:3]] == [tmp_path / "repo", package, inner]


def test_new_process_after_restore_imports_flat(tmp_path, repo):
    world = GeneratedWorld(":memory:", kind="sqlite")
    world.name = "demo"
    world.reach(str(repo), "agents/a")
    snapshot = save(world, tmp_path / "snapshot")
    source = Path(fi.alk.__file__).resolve().parents[2]
    script = f"""
import sys
sys.path.insert(0, {str(source)!r})
from fi.alk.harness.world.runtime import GeneratedWorld
from fi.alk.harness.world.snapshot import restore

GeneratedWorld(":memory:", kind="sqlite").reach({str(repo)!r})
restored = restore({str(snapshot)!r})
restored.reach({str(repo)!r})
import helper_flat
print(restored.source_subfolder, helper_flat.VALUE)
"""

    said = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, check=True
    )

    assert said.stdout.split() == ["agents/a", "1"]
