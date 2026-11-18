"""The save writes a tool's module as the agent's own process imports it (TH-8800).

A reading writes a module from the repository root (``examples.harness.x.agent``). With a
subfolder the control process starts in the bound ``workdir``, where that module is ``agent``.
The save rewrites only that case, by file checks and never an import.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from fi.alk.harness.tools import accept_contract

FIXTURES = Path(__file__).parent / "fixtures" / "readings_12nov"
SUBFOLDER = "examples/harness/openai_agents_support"
TOOLS = ("look_up_order", "start_return", "return_policy")


def _entries(module: str, mode: str = "unreachable", names=("look_up_order",)):
    return [
        {"tool": name, "mode": mode, "module": module, "callable": name} for name in names
    ]


def _contract(entries, **runtime):
    return {
        "agent": "pair",
        "modality": "chat",
        "real_use_cases": ["order"],
        "tools": [{"name": entry["tool"], "args": ["order_id"]} for entry in entries],
        "tool_entrypoints": entries,
        **({"runtime": {"language": "python", **runtime}} if runtime else {}),
    }


def _reading(name: str) -> dict:
    """A 12 Nov reading, less the quotes and the model that cite lines of source we don't hold."""
    reading = json.loads((FIXTURES / f"{name}.json").read_text())
    reading.update(system_prompt_excerpt="", system_prompt_from=[], model=None)
    return reading


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "repo"
    for name, text in files.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return root


def _save(tmp_path: Path, repo: Path, entries, subfolder: str | None, **runtime):
    result = accept_contract(
        _contract(entries, **runtime),
        tmp_path / "out",
        source_root=repo,
        subfolder=subfolder,
    )
    assert not result.get("is_error"), result
    saved = json.loads((tmp_path / "out" / "contract.json").read_text())
    return [(one["mode"], one["module"]) for one in saved["tool_entrypoints"]]


DEF = "def look_up_order(order_id):\n    return order_id\n"


@pytest.mark.parametrize("mode", ["import", "construct", "unreachable"])
def test_a_root_relative_module_is_written_as_the_process_imports_it(tmp_path, mode):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    saved = _save(
        tmp_path, repo, _entries(f"{SUBFOLDER.replace('/', '.')}.agent", mode), SUBFOLDER
    )
    assert saved == [(mode, "agent")]


def test_a_service_entry_keeps_its_module(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    entries = _entries(f"{SUBFOLDER.replace('/', '.')}.agent", "service")
    entries[0].update(service="svc", endpoint="/x")
    saved = _save(tmp_path, repo, entries, SUBFOLDER)
    assert saved == [("service", f"{SUBFOLDER.replace('/', '.')}.agent")]


def test_a_package_directory_is_a_file_too(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/tools/__init__.py": DEF})
    saved = _save(
        tmp_path, repo, _entries(f"{SUBFOLDER.replace('/', '.')}.tools"), SUBFOLDER
    )
    assert saved == [("unreachable", "tools")]


def test_a_nested_module_keeps_its_own_dots(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/pkg/inner.py": DEF})
    saved = _save(
        tmp_path, repo, _entries(f"{SUBFOLDER.replace('/', '.')}.pkg.inner"), SUBFOLDER
    )
    assert saved == [("unreachable", "pkg.inner")]


def test_no_subfolder_leaves_a_root_relative_module(tmp_path):
    """A callback process with no subfolder starts at the repository root: the root-relative
    name is the one its frames carry."""
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    module = f"{SUBFOLDER.replace('/', '.')}.agent"
    for mode in ("import", "unreachable"):
        saved = _save(tmp_path / mode, repo, _entries(module, mode), None)
        assert saved == [(mode, module)]


def test_a_workdir_alone_is_not_a_subfolder(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    module = f"{SUBFOLDER.replace('/', '.')}.agent"
    saved = _save(tmp_path, repo, _entries(module), None, workdir=SUBFOLDER)
    assert saved == [("unreachable", module)]


@pytest.mark.parametrize("where", ["root", "above", "import root"])
def test_a_regular_package_keeps_the_package_qualified_name(tmp_path, where):
    """`bench/envs/retail/tools.py` does `from .data import ...`: it only imports as
    `bench.envs.retail.tools`."""
    folder = "bench/envs/retail"
    files = {
        f"{folder}/tools.py": "from .data import ORDERS\n" + DEF,
        f"{folder}/data.py": "ORDERS = {}\n",
    }
    marker = {"root": "__init__.py", "above": "bench/__init__.py", "import root": f"{folder}/__init__.py"}
    files[marker[where]] = ""
    repo = _repo(tmp_path, files)
    saved = _save(tmp_path, repo, _entries("bench.envs.retail.tools", "import"), folder)
    assert saved == [("import", "bench.envs.retail.tools")]


def test_a_folder_between_the_root_and_the_import_root_counts(tmp_path):
    repo = _repo(
        tmp_path, {"a/b/c/agent.py": DEF, "a/b/__init__.py": ""}
    )
    saved = _save(tmp_path, repo, _entries("a.b.c.agent"), "a/b/c")
    assert saved == [("unreachable", "a.b.c.agent")]


def test_an_already_relative_module_is_kept(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    assert _save(tmp_path, repo, _entries("agent"), SUBFOLDER) == [("unreachable", "agent")]


def test_a_module_outside_the_import_root_is_kept(tmp_path):
    repo = _repo(
        tmp_path, {f"{SUBFOLDER}/agent.py": DEF, "shared/helpers.py": DEF}
    )
    assert _save(tmp_path, repo, _entries("shared.helpers", "import"), SUBFOLDER) == [
        ("import", "shared.helpers")
    ]


def test_a_workdir_deeper_than_the_subfolder_is_the_import_root(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/app/agent.py": DEF})
    deep = f"{SUBFOLDER}/app"
    # Relative to the workdir already: kept.
    assert _save(tmp_path / "a", repo, _entries("agent"), SUBFOLDER, workdir=deep) == [
        ("unreachable", "agent")
    ]
    # Rooted at the subfolder, which is not where the process starts: kept as written.
    (repo / SUBFOLDER / "agent.py").write_text(DEF)
    subfolder_rooted = f"{SUBFOLDER.replace('/', '.')}.agent"
    assert _save(
        tmp_path / "b", repo, _entries(subfolder_rooted), SUBFOLDER, workdir=deep
    ) == [("unreachable", subfolder_rooted)]
    # Rooted at the workdir itself: rewritten.
    assert _save(
        tmp_path / "c", repo, _entries(f"{deep.replace('/', '.')}.agent"), SUBFOLDER, workdir=deep
    ) == [("unreachable", "agent")]


def test_a_name_with_no_file_is_kept(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    module = f"{SUBFOLDER.replace('/', '.')}.missing"
    assert _save(tmp_path, repo, _entries(module), SUBFOLDER) == [("unreachable", module)]


def test_a_folder_named_for_the_import_root_beside_an_outer_file(tmp_path):
    """`svc/agent.py` beside `svc/svc/agent.py`: with the process starting in `svc`, the name
    `svc.agent` is already the inner file. It is not rewritten to the outer one."""
    repo = _repo(tmp_path, {"svc/agent.py": DEF, "svc/svc/agent.py": DEF})
    assert _save(tmp_path, repo, _entries("svc.agent"), "svc") == [("unreachable", "svc.agent")]


def test_the_outer_file_alone_is_still_rewritten(tmp_path):
    repo = _repo(tmp_path, {"svc/agent.py": DEF})
    assert _save(tmp_path, repo, _entries("svc.agent"), "svc") == [("unreachable", "agent")]


def test_the_12_nov_agents_sdk_reading_is_rewritten(tmp_path):
    reading = _reading("openai_agents")
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF + "async def agent_callback(i):\n    return {}\n"})
    result = accept_contract(reading, tmp_path / "out", source_root=repo, subfolder=SUBFOLDER)
    assert not result.get("is_error"), result
    saved = json.loads((tmp_path / "out" / "contract.json").read_text())
    assert [(one["mode"], one["module"], one["callable"]) for one in saved["tool_entrypoints"]] == [
        ("unreachable", "agent", name) for name in TOOLS
    ]


def test_the_12_nov_pydantic_ai_reading_is_unchanged(tmp_path):
    reading = _reading("pydantic_ai")
    folder = reading["runtime"]["source_subfolder"]
    before = [(one["mode"], one["module"]) for one in reading["tool_entrypoints"]]
    files = {f"{folder}/agent.py": "".join(
        f"def {one['callable']}(x=None):\n    return x\n" for one in reading["tool_entrypoints"]
    )}
    repo = _repo(tmp_path, files)
    result = accept_contract(reading, tmp_path / "out", source_root=repo, subfolder=folder)
    assert not result.get("is_error"), result
    saved = json.loads((tmp_path / "out" / "contract.json").read_text())
    assert [(one["mode"], one["module"]) for one in saved["tool_entrypoints"]] == before


def test_the_check_costs_the_save_lookups_not_a_walk(tmp_path, monkeypatch):
    """File checks only, charged to the save's lookup budget: no tree walk."""
    from fi.alk.harness import tools

    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    walked = []
    real = Path.rglob
    monkeypatch.setattr(Path, "rglob", lambda self, *a, **k: walked.append(self) or real(self, *a, **k))
    _save(tmp_path, repo, _entries(f"{SUBFOLDER.replace('/', '.')}.agent"), SUBFOLDER)
    assert not walked
    assert hasattr(tools, "path_lookup_budget")


def _handler_imports(repo: Path, subfolder: str, module: str) -> str:
    """What a handler gets: the module imported with the repository root and the subfolder on
    the path, as ``GeneratedWorld.reach`` sets it (never the workdir)."""
    code = (
        "import importlib, sys\n"
        f"sys.path[:0] = [{str(repo / subfolder)!r}, {str(repo)!r}]\n"
        f"print(importlib.import_module({module!r}).where)\n"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", code], capture_output=True, text=True
    )
    return done.stdout.strip() if done.returncode == 0 else done.stderr.strip().splitlines()[-1]


@pytest.mark.parametrize("outer", [False, True])
@pytest.mark.parametrize("mode", ["import", "construct"])
def test_a_workdir_deeper_than_the_subfolder_keeps_an_import_entry(tmp_path, mode, outer):
    """The handler imports from the root and the subfolder, never the workdir, so the name
    written from the root is the one that works."""
    folder, deep = "examples/x", "examples/x/app"
    files = {f"{deep}/tools.py": "where = 'inner'\n"}
    if outer:
        files[f"{folder}/tools.py"] = "where = 'OUTER'\n"
    repo = _repo(tmp_path, files)
    module = "examples.x.app.tools"
    saved = _save(tmp_path, repo, _entries(module, mode), folder, workdir=deep)
    assert saved == [(mode, module)]
    assert _handler_imports(repo, folder, saved[0][1]) == "inner"


def test_a_workdir_that_is_the_subfolder_still_rewrites_an_import_entry(tmp_path):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    saved = _save(
        tmp_path, repo, _entries(f"{SUBFOLDER.replace('/', '.')}.agent", "import"), SUBFOLDER,
        workdir=SUBFOLDER,
    )
    assert saved == [("import", "agent")]


@pytest.mark.parametrize("mode", ["unreachable", "import", "construct"])
@pytest.mark.parametrize("module", [".", "/", "..", "agent\x00", "a..b", "agent-1", "a/b"])
def test_a_module_that_is_not_a_dotted_identifier_is_refused(tmp_path, mode, module):
    repo = _repo(tmp_path, {f"{SUBFOLDER}/agent.py": DEF})
    result = accept_contract(
        _contract(_entries(module, mode)),
        tmp_path / "out",
        source_root=repo,
        subfolder=SUBFOLDER,
    )
    assert result.get("is_error"), result
    said = json.dumps(result)
    assert "module" in said and "dotted" in said
    assert not (tmp_path / "out" / "contract.json").exists()


@pytest.mark.parametrize("mode", ["import", "unreachable"])
def test_a_standard_library_name_is_not_rewritten(tmp_path, mode):
    """`calendar` would import the standard library's, not the tool file."""
    repo = _repo(tmp_path, {"examples/hotel/calendar.py": "def book():\n    return 1\n"})
    saved = _save(tmp_path, repo, _entries("examples.hotel.calendar", mode), "examples/hotel")
    assert saved == [(mode, "examples.hotel.calendar")]


def test_many_entries_naming_one_module_save_within_the_lookup_budget(tmp_path):
    folder = "/".join(f"d{i}" for i in range(12))
    repo = _repo(tmp_path, {f"{folder}/agent.py": DEF})
    module = f"{folder.replace('/', '.')}.agent"
    names = [f"tool_{i}" for i in range(1500)]
    entries = [
        {"tool": name, "mode": "unreachable", "module": module, "callable": name} for name in names
    ]
    result = accept_contract(
        _contract(entries), tmp_path / "out", source_root=repo, subfolder=folder
    )
    assert not result.get("is_error"), str(result)[:300]
    saved = json.loads((tmp_path / "out" / "contract.json").read_text())
    assert {one["module"] for one in saved["tool_entrypoints"]} == {"agent"}
