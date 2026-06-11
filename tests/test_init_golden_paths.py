"""Golden-path proof: every init preset runs green offline (Phase 2C).

One test per preset: scaffold into tmp_path with no required env, execute the
scaffold's own next-commands in-process via cli.main, and assert each
command's postcondition artifact kind. These tests are the executable form of
the quickstart docs pages.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from agent_learning.cli import main


def _run_scaffold_commands(target_dir: Path) -> list[str]:
    readme = (target_dir / "README.md").read_text(encoding="utf-8")
    section = re.search(
        r"^## (?:Agent Learning Entrypoint|Optimization Lifecycle)\n(.*?)(?=^## |\Z)",
        readme,
        re.M | re.S,
    )
    assert section, "scaffold README has no next-commands section"
    commands = re.findall(r"^- `(agent-learn [^`]+)`", section.group(1), re.M)
    assert commands, "scaffold README lists no next-commands"
    for command in commands:
        argv = command.split()[1:]
        assert main(argv) == 0, f"command failed: {command}"
    return commands


def _assert_artifact(path: Path, kind: str) -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["kind"] == kind, payload["kind"]


@pytest.mark.parametrize(
    ("preset", "artifact", "kind"),
    [
        ("run", "artifacts/run.json", "agent-learning.run.v1"),
        ("redteam", "artifacts/redteam.json", "agent-learning.redteam.v1"),
        ("ci", "artifacts/replay.json", "agent-learning.replay.v1"),
        ("optimize", "artifacts/optimization.json", "agent-learning.optimization.v1"),
        ("all", "artifacts/suite.json", "agent-learning.suite.v1"),
    ],
)
def test_init_preset_golden_path_offline(
    tmp_path, monkeypatch, preset, artifact, kind
):
    for env_name in (
        "AGENT_LEARNING_API_KEY",
        "FUTURE_AGI_API_KEY",
        "FI_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)

    project = tmp_path / f"{preset}-project"
    assert main(["init", str(project), "--preset", preset, "--quiet"]) == 0

    readme = (project / "README.md").read_text(encoding="utf-8")
    assert "## When It Fails" in readme
    assert "missing_engine_modules" in readme

    _run_scaffold_commands(project)
    _assert_artifact(project / artifact, kind)
