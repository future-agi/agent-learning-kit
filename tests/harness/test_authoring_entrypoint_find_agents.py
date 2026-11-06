"""The `--find-agents` switch: it reaches the scan, it can't be given with the reading's switch,
and a repository scanned through it leaves a file the platform's reader accepts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fi.alk.harness import agent_scan, reading_entry, spend
from fi.alk.harness.agent_scan import FIND_AGENTS
from fi.alk.harness.authoring_entrypoint import main
from fi.alk.harness.scan_ai import AiOutcome
from fi.alk.harness.scan_document import wire_problems
from fi.alk.harness.scan_folders import FolderWord

GRAPH = "from langgraph.graph import StateGraph\nStateGraph(dict)\n"
CLIENT = "from openai import OpenAI\nOpenAI()\n"
COMMIT = "CD" * 20


class FakeChecker:
    def __enter__(self) -> FakeChecker:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def check(self, source, subfolder, *, deadline, clock=None) -> FolderWord:
        return FolderWord({"name": "langgraph", "version": None, "label": None}, finished=True)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch, tmp_path):
    monkeypatch.delenv(spend.JOURNAL_ALIAS, raising=False)
    monkeypatch.setattr(spend, "_path", None)
    # The machine's own secrets files must not decide what a test sees masked.
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (tmp_path / "no-secrets.json",))
    monkeypatch.setattr(reading_entry, "_secret_values", None)
    spend._stages.clear()
    yield
    spend._stages.clear()


@pytest.fixture
def fakes(monkeypatch) -> list[dict]:
    """The folder checker and the model step, faked; the model's calls are returned."""
    asked: list[dict] = []

    async def no_model(source, *, hints, seconds, on_turn_end) -> AiOutcome:
        asked.append({"hints": hints})
        return AiOutcome("empty", (), 0, True, False, "fixed words")

    monkeypatch.setattr(agent_scan, "FolderChecker", FakeChecker)
    monkeypatch.setattr(agent_scan, "run_ai_step", no_model)
    return asked


def _write(folder: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return folder


_SOURCES = {
    "github": {
        "kind": "github",
        "visibility": "public",
        "repository": "acme/support-bot",
        "commit_sha": COMMIT,
    },
    "archive": {"kind": "archive", "archive_artifact_id": "upload-1"},
}


def _job_file(tmp_path: Path, kind: str = "github") -> Path:
    path = tmp_path / f"job-{kind}.json"
    path.write_text(
        json.dumps(
            {
                "job_id": "job",
                "run_id": "run",
                "execution": "hosted",
                "source": _SOURCES[kind],
                "runtime": {"isolation": "dedicated_vm"},
                "agent": {"connector": "auto"},
            }
        ),
        encoding="utf-8",
    )
    return path


def _found(output: Path) -> dict:
    document = json.loads((output / "agents.json").read_text(encoding="utf-8"))
    assert wire_problems(document) == []
    return document


def test_the_switch_reaches_the_scan(tmp_path, monkeypatch) -> None:
    calls = []

    def recording(mode, *, job_path, source, output):
        calls.append((mode, job_path, source, output))
        return 0

    monkeypatch.setattr(reading_entry, "run_reading_mode", recording)
    job = _job_file(tmp_path)

    code = main(
        [
            str(job),
            "--source",
            str(tmp_path / "a" / ".." / "source"),
            "--output",
            str(tmp_path / "a" / ".." / "reading"),
            "--find-agents",
        ]
    )

    assert code == 0
    assert calls == [
        (
            FIND_AGENTS,
            job,
            (tmp_path / "source").resolve(),
            (tmp_path / "reading").resolve(),
        )
    ]


def test_the_two_switches_exclude_each_other(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        reading_entry, "run_reading_mode", lambda mode, **kwargs: calls.append(mode) or 0
    )

    with pytest.raises(SystemExit) as stopped:
        main(
            [
                str(_job_file(tmp_path)),
                "--source",
                str(tmp_path / "source"),
                "--output",
                str(tmp_path / "reading"),
                "--read-agent-only",
                "--find-agents",
            ]
        )

    assert stopped.value.code == 2
    assert calls == []


def test_a_github_repo_gives_found(tmp_path, fakes) -> None:
    source = _write(
        tmp_path / "source",
        {"graph.py": GRAPH, "pyproject.toml": '[project]\nname = "bot"\n'},
    )
    output = tmp_path / "reading"

    code = main(
        [
            str(_job_file(tmp_path)),
            "--source",
            str(source),
            "--output",
            str(output),
            "--find-agents",
        ]
    )

    assert code == 0
    document = _found(output)
    assert document["status"] == "found"
    assert document["commit_sha"] == COMMIT.lower()
    assert [(agent["entry"], agent["subfolder"]) for agent in document["agents"]] == [
        ("graph.py", "")
    ]
    assert document["agents"][0]["name"] == "support-bot"
    assert document["truncated"] is False
    assert json.loads((output / "cost.json").read_text(encoding="utf-8"))["complete"] is True
    assert not (output / "failure.json").exists()
    assert fakes == []


def test_fifty_one_agents_give_fifty_and_truncated(tmp_path, fakes) -> None:
    source = _write(tmp_path / "source", {f"bot_{index:02d}.py": CLIENT for index in range(51)})
    output = tmp_path / "reading"

    code = main(
        [
            str(_job_file(tmp_path)),
            "--source",
            str(source),
            "--output",
            str(output),
            "--find-agents",
        ]
    )

    assert code == 0
    document = _found(output)
    assert document["status"] == "found"
    assert len(document["agents"]) == 50
    assert document["truncated"] is True
    assert fakes == []


def test_an_upload_with_nothing_agent_like_is_none(tmp_path, fakes) -> None:
    # Nothing that looks like code, so no model is asked; then plain code, so the model is
    # asked and finds nothing.
    shapes = {
        "quiet": ({"README.md": "# notes\n"}, 0),
        "plain": ({"app/main.py": "def main():\n    return 1\n"}, 1),
    }
    for shape, (files, asked) in shapes.items():
        fakes.clear()
        source = _write(tmp_path / shape, files)
        output = tmp_path / f"reading-{shape}"

        code = main(
            [
                str(_job_file(tmp_path, "archive")),
                "--source",
                str(source),
                "--output",
                str(output),
                "--find-agents",
            ]
        )

        assert code == 0, shape
        document = _found(output)
        assert (document["status"], document["agents"], document["error"]) == ("none", [], None)
        assert document["commit_sha"] is None
        assert len(fakes) == asked, shape
        assert (output / "cost.json").exists()
        assert not (output / "failure.json").exists()
