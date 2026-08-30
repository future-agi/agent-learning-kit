"""How the bundler decides what to start, for a repository that ships no container metadata.

This path used to require a file literally named `agent.py`, anywhere in the tree, exactly once.
A repository could satisfy every documented requirement and still fail on a filename that no skill
mentions and that the harness itself never asks for: when the environment stage refuses, it asks
the operator to "expose the real implementation as an importable callable or an HTTP service",
which is a statement about seams, not names.

Worse, the answer was already in hand. The understand stage records `runtime.command`, and on the
run that exposed this it had derived the exact executable command, the port, the chat path and the
install step. The bundler globbed for a filename instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fi.alk.harness.bundle_author_v2 import BundleAuthorError, _generated_entrypoint


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


# The runtime block from job 93400f09's contract, which is what a real understand stage produces.
REAL_RUNTIME = {
    "language": "python",
    "version": ">=3.11",
    "install": "pip install -e .",
    "command": ["uvicorn", "notesagent.app:app", "--host", "0.0.0.0", "--port", "8080"],
    "workdir": "",
    "dockerfile": "",
    "compose_file": "",
}


def test_a_declared_command_is_used_as_written(tmp_path):
    root = _repo(
        tmp_path, {"pyproject.toml": "[project]\nname='n'\n", "notesagent/app.py": "app=1\n"}
    )
    component, _, run = _generated_entrypoint(root, REAL_RUNTIME)
    assert component == root
    assert run == [
        "uv", "run", "--no-sync",
        "uvicorn", "notesagent.app:app", "--host", "0.0.0.0", "--port", "8080",
    ]


def test_the_declared_command_beats_a_conventional_filename(tmp_path):
    """The precedence `Runtime.command` already documents: the filename search is what happens
    when no command was proven, so a convention must never outrank a statement of fact."""
    root = _repo(
        tmp_path,
        {"pyproject.toml": "[project]\nname='n'\n", "agent.py": "x=1\n", "notesagent/app.py": "y=1\n"},
    )
    _, _, run = _generated_entrypoint(root, REAL_RUNTIME)
    assert run is not None and "uvicorn" in run
    assert not any(item.endswith("agent.py") for item in run)


def test_a_command_is_rewritten_into_the_environment_the_build_creates(tmp_path):
    """The submitted command assumes its own machine, where its dependencies are on PATH. Here
    they are in a venv that was just created, so an unqualified `uvicorn` resolves to nothing."""
    pyproject = _repo(tmp_path / "a", {"pyproject.toml": "[project]\nname='n'\n"})
    _, _, uv_run = _generated_entrypoint(pyproject, {"command": ["uvicorn", "m:app"]})
    assert uv_run == ["uv", "run", "--no-sync", "uvicorn", "m:app"]

    plain = _repo(tmp_path / "b", {"requirements.txt": "fastapi\n"})
    _, _, venv_run = _generated_entrypoint(plain, {"command": ["uvicorn", "m:app"]})
    assert venv_run == [".venv/bin/uvicorn", "m:app"]
    _, _, python_run = _generated_entrypoint(plain, {"command": ["python", "-m", "pkg"]})
    assert python_run == [".venv/bin/python", "-m", "pkg"]


def test_a_declared_workdir_is_where_the_agent_starts(tmp_path):
    root = _repo(tmp_path, {"svc/pyproject.toml": "[project]\nname='n'\n"})
    component, _, run = _generated_entrypoint(
        root, {"workdir": "svc", "command": ["uvicorn", "m:app"]}
    )
    assert component == root / "svc"
    assert run[0] == "uv"  # resolved against the workdir's manifest, not the root's


def test_a_workdir_that_does_not_exist_says_so(tmp_path):
    with pytest.raises(BundleAuthorError) as raised:
        _generated_entrypoint(_repo(tmp_path, {}), {"workdir": "nope", "command": ["x"]})
    assert "component_missing" in str(raised.value)


def test_agent_py_still_works_when_nothing_is_declared(tmp_path):
    """The old behaviour is kept, demoted to what it always should have been: a fallback."""
    root = _repo(tmp_path, {"agent.py": "x=1\n"})
    component, entry, run = _generated_entrypoint(root, None)
    assert (component, entry, run) == (root, "agent.py", None)


def test_a_conventional_entrypoint_no_longer_has_to_be_called_agent_py(tmp_path):
    root = _repo(tmp_path, {"main.py": "x=1\n"})
    assert _generated_entrypoint(root, {})[1] == "main.py"


def test_a_nested_entrypoint_builds_from_the_directory_owning_the_dependencies(tmp_path):
    """A package layout puts the module under a package while the manifest sits at the root.
    Building from the package directory would find no manifest at all."""
    root = _repo(
        tmp_path, {"pyproject.toml": "[project]\nname='n'\n", "notesagent/app.py": "x=1\n"}
    )
    component, entry, _ = _generated_entrypoint(root, {})
    assert component == root
    assert entry == "notesagent/app.py"


def test_no_entrypoint_at_all_is_not_reported_as_ambiguous(tmp_path):
    """Defect B, and the eleventh instance of this branch's one shape. `len(candidates) != 1`
    called zero candidates "component_ambiguous", telling an operator to disambiguate something
    that does not exist. Ambiguous means too many; none is a different problem with a different
    remedy."""
    with pytest.raises(BundleAuthorError) as raised:
        _generated_entrypoint(_repo(tmp_path, {"pyproject.toml": "[project]\nname='n'\n"}), {})
    message = str(raised.value)
    assert "entrypoint_undeclared" in message
    assert "ambiguous" not in message.lower()
    # It names the remedy, which is the field that would have answered it.
    assert "runtime.command" in message


def test_several_entrypoints_are_reported_as_ambiguous_and_named(tmp_path):
    root = _repo(tmp_path, {"a/main.py": "x=1\n", "b/app.py": "y=1\n"})
    with pytest.raises(BundleAuthorError) as raised:
        _generated_entrypoint(root, {})
    message = str(raised.value)
    assert "entrypoint_ambiguous" in message
    assert "a/main.py" in message and "b/app.py" in message
    assert "runtime.command" in message
