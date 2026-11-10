"""The find-agents mode: what it refuses, the file it leaves on every path, and the cost it leaves."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import math
import os
import shutil
import time
from pathlib import Path

import pytest

from fi.alk.harness import agent_scan, reading_entry, scan_walk, spend
from fi.alk.harness.agent_scan import FIND_AGENTS, _check_job, _hints, _work
from fi.alk.harness.job import (
    FailureDomain,
    HarnessJob,
    HarnessStage,
    RepositorySource,
    SourceKind,
)
from fi.alk.harness.scan_ai import AiOutcome
from fi.alk.harness.scan_document import ScanResultInvalid, wire_problems
from fi.alk.harness.scan_folders import ClockRanOut, FolderCheckerBroken, FolderWord
from fi.alk.harness.scan_walk import Candidate, WalkResult

GRAPH = "from langgraph.graph import StateGraph\nStateGraph(dict)\n"
SCRIPT_AGENT = (
    "import { createReactAgent } from '@langchain/langgraph/prebuilt';\n"
    "export const agent = createReactAgent({ llm, tools: [] });\n"
)
MODEL_HELPER = (
    "from langchain_openai import ChatOpenAI\n"
    "def judge(x):\n"
    "    return ChatOpenAI().invoke(x)\n"
)
# A TypeScript agent the walk can't list, beside a Python file that only calls a model.
SCRIPT_BESIDE_A_HELPER = {"web/src/agent.ts": SCRIPT_AGENT, "backend/judge.py": MODEL_HELPER}
CLIENT = "from openai import OpenAI\nOpenAI()\n"
# Both kinds of file that only call a model: a chat model, and a provider's own client.
HELPERS = {"chat_model": MODEL_HELPER, "client": CLIENT}
PLAIN = "def main():\n    return 1\n"
COMMIT = "AB" * 20
# Plain code outside the side folders and one client inside them: the walk lists only a side
# agent, so the model is asked.
SIDE_ONLY = {"app/main.py": PLAIN, "tests/test_bot.py": CLIENT}
AI_SENTENCE = "Found by the AI step, not by the code check."


class FakeChecker:
    """Answers every folder at once. From call ``fail_at`` on (counted from 1) it raises ``error``."""

    def __init__(self) -> None:
        self.name = "langgraph"
        self.fail_at: int | None = None
        self.error: Exception = ClockRanOut("the clock ran out")
        self.calls: list[str] = []

    def __enter__(self) -> FakeChecker:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def check(self, source, subfolder, *, deadline, clock=None) -> FolderWord:
        self.calls.append(subfolder)
        if self.fail_at is not None and len(self.calls) >= self.fail_at:
            raise self.error
        return FolderWord({"name": self.name, "version": None, "label": None}, finished=True)


class FakeAi:
    """The model step: one turn ends, then ``during`` runs, then ``outcome`` comes back."""

    def __init__(self) -> None:
        self.outcome = _outcome("empty")
        self.during = None
        self.calls: list[dict] = []

    async def __call__(self, source, *, hints, seconds, on_turn_end) -> AiOutcome:
        self.calls.append({"source": source, "hints": hints, "seconds": seconds})
        on_turn_end()
        if self.during is not None:
            self.during()
        return self.outcome


def _outcome(
    kind, pairs=(), *, refused=0, complete=True, retryable=False, uncarried=0
) -> AiOutcome:
    return AiOutcome(kind, tuple(pairs), refused, complete, retryable, "fixed words", uncarried)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch, tmp_path):
    monkeypatch.delenv(spend.JOURNAL_ALIAS, raising=False)
    monkeypatch.setattr(spend, "_path", None)
    # The machine's own secrets files must not decide what a test sees masked.
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (tmp_path / "no-secrets.json",))
    monkeypatch.setattr(agent_scan, "CLOCK", 1200.0)
    monkeypatch.setattr(agent_scan, "AI_SECONDS", 600.0)
    spend._stages.clear()
    yield
    spend._stages.clear()


@pytest.fixture(autouse=True)
def checker(monkeypatch) -> FakeChecker:
    fake = FakeChecker()
    monkeypatch.setattr(agent_scan, "FolderChecker", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def ai(monkeypatch) -> FakeAi:
    fake = FakeAi()
    monkeypatch.setattr(agent_scan, "run_ai_step", fake)
    return fake


def _write(folder: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return folder


def _job(kind: str = "github", **source: object) -> HarnessJob:
    return HarnessJob.model_validate(_job_data(kind, **source))


def _job_data(kind: str = "github", **source: object) -> dict:
    sources = {
        "github": {
            "kind": "github",
            "visibility": "public",
            "repository": "acme/support-bot",
            "commit_sha": COMMIT,
        },
        "archive": {"kind": "archive", "archive_artifact_id": "upload-1"},
    }
    return {
        "job_id": "job",
        "run_id": "run",
        "execution": "hosted",
        "source": {**sources[kind], **source},
        "runtime": {"isolation": "dedicated_vm"},
        "agent": {"connector": "auto"},
    }


def _scan(job: HarnessJob, source: Path, output: Path) -> int:
    return asyncio.run(_work(job, source, output))


def _read_mode(kind: str, source: Path, output: Path) -> int:
    """The whole mode, through the helper that runs every reading, from a job file."""
    job_path = output.parent / f"job-{output.name}.json"
    job_path.write_text(json.dumps(_job_data(kind)), encoding="utf-8")
    return reading_entry.run_reading_mode(
        FIND_AGENTS, job_path=job_path, source=source, output=output
    )


def _read(output: Path, name: str) -> dict:
    return json.loads((output / name).read_text(encoding="utf-8"))


def _agents(output: Path) -> dict:
    document = _read(output, "agents.json")
    assert wire_problems(document) == []
    if document["error"] is not None:
        assert document["error"]["message"].strip()
    return document


def _entries(document: dict) -> list[str]:
    return [agent["entry"] for agent in document["agents"]]


def _candidate(entry: str, subfolder: str, *, side: bool = True) -> Candidate:
    return Candidate(
        entry=entry,
        subfolder=subfolder,
        sign="client",
        side=side,
        builds=(),
        build_may_stop="",
        partial=False,
        env_names=(),
        tools=0,
        prompt_files=0,
        evidence=(),
    )


def _walk(**changes: object) -> WalkResult:
    values: dict = {
        "agents": (),
        "more_than_fifty": False,
        "stopped": "",
        "unread_files": 0,
        "source_outside_side": True,
        "candidate_outside_side": False,
        "script_outside_side": False,
        "only_weak_outside_side": False,
        "paths_left_out": 0,
        "upload_others": 0,
        "upload_others_side": 0,
    }
    values.update(changes)
    return WalkResult(**values)


def _tree(folder: Path, leave_out: Path) -> dict[str, tuple[int, int]]:
    seen = {}
    for directory, directories, files in os.walk(folder, followlinks=False):
        for name in directories + files:
            path = Path(directory, name)
            if path == leave_out or leave_out in path.parents:
                continue
            status = os.lstat(path)
            seen[str(path)] = (status.st_size, status.st_mtime_ns)
    return seen


def test_a_remote_source_or_a_subfolder_is_refused() -> None:
    github = _job()
    jobs = [
        github.model_copy(
            update={
                "source": RepositorySource.model_construct(
                    kind=SourceKind.REMOTE, endpoint="https://agent.example"
                )
            }
        ),
        github.model_copy(
            update={"source": RepositorySource.model_construct(kind=SourceKind.PROVIDER)}
        ),
        _job(subfolder="services/bot"),
    ]

    for job in jobs:
        refused = _check_job(job)
        assert refused is not None
        assert refused.code == "scan_job_invalid"
        assert refused.domain is FailureDomain.PLATFORM_SYNC
        assert refused.stage is HarnessStage.UNDERSTANDING_AGENT
        assert refused.retryable is False
        assert refused.message.strip()


def test_the_scan_job_passes_the_kits_job_check() -> None:
    # Built through HarnessJob's own validation, as the helper parses the job.
    for job in (_job(), _job("archive"), _job(subfolder="")):
        assert _check_job(job) is None


def test_the_mode_settings(monkeypatch) -> None:
    assert FIND_AGENTS.name == "find_agents"
    assert FIND_AGENTS.check_job is _check_job
    assert FIND_AGENTS.work is _work
    assert inspect.iscoroutinefunction(FIND_AGENTS.work)
    # The environment hasn't changed since import, so this is the clock the mode was built with.
    clock = agent_scan._seconds("ALK_SCAN_MAX_SECONDS", 1200.0)
    assert FIND_AGENTS.time_limit_seconds == clock + 600
    assert FIND_AGENTS.timed_out_code == "scan_hard_limit_reached"
    assert FIND_AGENTS.timed_out_retryable is False
    assert FIND_AGENTS.crashed_code == "scan_crashed"

    for name, default in (("ALK_SCAN_MAX_SECONDS", 1200.0), ("ALK_SCAN_AI_MAX_SECONDS", 600.0)):
        monkeypatch.delenv(name, raising=False)
        assert agent_scan._seconds(name, default) == default
        for raw in ("", "abc", "-5", "0", "inf", "nan"):
            monkeypatch.setenv(name, raw)
            assert agent_scan._seconds(name, default) == default
        monkeypatch.setenv(name, "900")
        assert agent_scan._seconds(name, default) == 900.0


def test_a_missing_or_empty_source_is_a_retryable_failure(tmp_path, ai) -> None:
    a_file = _write(tmp_path / "files", {"bot.py": GRAPH}) / "bot.py"
    empty = tmp_path / "empty"
    (empty / "sub").mkdir(parents=True)
    linked = tmp_path / "linked"
    linked.mkdir()
    (linked / "bot.py").symlink_to(a_file)

    for index, source in enumerate((tmp_path / "missing", a_file, empty, linked)):
        output = tmp_path / f"reading-{index}"

        assert _scan(_job(), source, output) == 1

        failure = _read(output, "failure.json")
        assert failure["code"] == "scan_source_missing"
        assert failure["domain"] == "platform_sync"
        assert failure["stage"] == "understanding_agent"
        assert failure["retryable"] is True
        assert not (output / "agents.json").exists()
    assert ai.calls == []


def test_none_when_nothing_looks_like_an_agent(tmp_path, ai) -> None:
    quiet = _write(tmp_path / "quiet", {"README.md": "# notes\n"})

    assert _scan(_job(), quiet, tmp_path / "out-quiet") == 0

    document = _agents(tmp_path / "out-quiet")
    assert (document["status"], document["agents"], document["truncated"]) == ("none", [], False)
    assert ai.calls == []

    plain = _write(tmp_path / "plain", {"app/main.py": PLAIN})
    ai.outcome = _outcome("empty")

    assert _scan(_job(), plain, tmp_path / "out-plain") == 0

    document = _agents(tmp_path / "out-plain")
    assert (document["status"], document["agents"]) == ("none", [])
    assert len(ai.calls) == 1


def test_the_walk_limit_before_any_agent_is_an_error(tmp_path, monkeypatch, ai) -> None:
    source = _write(tmp_path / "source", {"app/main.py": PLAIN})
    monkeypatch.setattr(
        agent_scan, "walk_repo", lambda *args, **kwargs: _walk(stopped="limit", unread_files=2)
    )

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert document["status"] == "error"
    assert document["error"]["code"] == "scan_limit_reached"
    assert document["truncated"] is True
    assert ai.calls == []


def test_the_clock_ends_in_a_file(tmp_path, monkeypatch, ai) -> None:
    source = _write(tmp_path / "source", {"a/graph.py": GRAPH})
    monkeypatch.setattr(agent_scan, "CLOCK", -1.0)

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert document["status"] == "error"
    assert document["error"]["code"] == "scan_timed_out"
    assert ai.calls == []


def test_a_clock_out_during_the_checks_lists_the_agents_unchecked(tmp_path, checker) -> None:
    source = _write(tmp_path / "source", {"a/graph.py": GRAPH, "b/graph.py": GRAPH})
    checker.fail_at = 1

    assert _scan(_job(), source, tmp_path / "first") == 0

    document = _agents(tmp_path / "first")
    assert (document["status"], _entries(document)) == ("found", ["a/graph.py", "b/graph.py"])
    assert document["truncated"] is True
    assert {agent["framework"]["name"] for agent in document["agents"]} == {"unknown"}
    assert {agent["confidence"] for agent in document["agents"]} == {"low"}

    checker.calls.clear()
    checker.fail_at = 2

    assert _scan(_job(), source, tmp_path / "second") == 0

    document = _agents(tmp_path / "second")
    assert (document["status"], _entries(document)) == ("found", ["a/graph.py", "b/graph.py"])
    assert document["agents"][1]["framework"]["name"] == "unknown"
    assert document["truncated"] is True


def test_an_upload_gives_one_agent_at_the_root(tmp_path) -> None:
    source = _write(tmp_path / "source", {"a/graph.py": GRAPH, "b/graph.py": GRAPH})

    assert _scan(_job("archive", commit_sha=COMMIT), source, tmp_path / "upload") == 0

    document = _agents(tmp_path / "upload")
    assert document["status"] == "found"
    assert [agent["subfolder"] for agent in document["agents"]] == [""]
    assert document["truncated"] is True
    assert document["commit_sha"] is None

    assert _scan(_job(), source, tmp_path / "github") == 0

    document = _agents(tmp_path / "github")
    assert sorted(_entries(document)) == ["a/graph.py", "b/graph.py"]
    assert document["commit_sha"] == COMMIT.lower()


def test_an_uploads_ai_row_is_at_the_root(tmp_path, ai) -> None:
    source = _write(tmp_path / "source", {"svc/main.py": PLAIN, "svc/bot.py": PLAIN})
    ai.outcome = _outcome("pairs", [("svc", "svc/main.py"), ("svc", "svc/bot.py")])

    assert _scan(_job("archive"), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert len(ai.calls) == 1
    assert _entries(document) == ["svc/main.py"]
    assert document["agents"][0]["subfolder"] == ""
    assert document["truncated"] is True
    assert document["commit_sha"] is None


def test_the_ai_runs_only_when_nothing_is_found(tmp_path, monkeypatch, ai) -> None:
    side_only = _write(tmp_path / "side-only", SIDE_ONLY)

    monkeypatch.setattr(agent_scan, "CLOCK", 1000.0)
    assert _scan(_job(), side_only, tmp_path / "out-1000") == 0
    assert [call["seconds"] for call in ai.calls] == [600.0]
    assert "test_bot" not in ai.calls[0]["hints"]

    ai.calls.clear()
    monkeypatch.setattr(agent_scan, "CLOCK", 500.0)
    assert _scan(_job(), side_only, tmp_path / "out-500") == 0
    assert len(ai.calls) == 1
    # What's left after the walk, less the 180 s kept for what follows the model.
    assert 300.0 < ai.calls[0]["seconds"] <= 320.0

    ai.calls.clear()
    monkeypatch.setattr(agent_scan, "CLOCK", 300.0)
    assert _scan(_job(), side_only, tmp_path / "out-300") == 0
    assert ai.calls == []

    monkeypatch.setattr(agent_scan, "CLOCK", 1200.0)
    found = _write(tmp_path / "found", {**SIDE_ONLY, "bot/graph.py": GRAPH})
    assert _scan(_job(), found, tmp_path / "out-found") == 0
    assert ai.calls == []
    assert "bot/graph.py" in _entries(_agents(tmp_path / "out-found"))

    only_side = _write(tmp_path / "only-side", {"tests/test_bot.py": CLIENT})
    assert _scan(_job(), only_side, tmp_path / "out-only-side") == 0
    assert ai.calls == []


def test_hints_carry_no_path(tmp_path, monkeypatch, ai) -> None:
    walk = _walk(
        agents=(_candidate("examples/zebra_place/quokka_bot.py", "examples/zebra_place"),),
        unread_files=3,
    )

    hints = _hints(walk)

    for word in ("zebra", "quokka", "examples", "/", ".py"):
        assert word not in hints
    assert "1" in hints and "3" in hints

    source = _write(tmp_path / "source", {"app/main.py": PLAIN})
    monkeypatch.setattr(agent_scan, "walk_repo", lambda *args, **kwargs: walk)

    assert _scan(_job(), source, tmp_path / "out") == 0

    assert ai.calls[0]["hints"] == hints


def test_side_rows_and_ai_rows_together_keep_fifty(tmp_path, checker, ai) -> None:
    files = {f"tests/test_bot_{index:02d}.py": CLIENT for index in range(30)}
    files.update({f"ai/bot_{index:02d}.py": PLAIN for index in range(30)})
    source = _write(tmp_path / "source", files)
    # The last pair repeats a walk entry under a different folder.
    ai.outcome = _outcome(
        "pairs",
        [("ai", f"ai/bot_{index:02d}.py") for index in range(30)] + [("", "tests/test_bot_00.py")],
    )

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert len(document["agents"]) == 50
    assert document["truncated"] is True
    repeated = [agent for agent in document["agents"] if agent["entry"] == "tests/test_bot_00.py"]
    assert [agent["subfolder"] for agent in repeated] == ["tests"]
    assert AI_SENTENCE not in repeated[0]["evidence"]
    # 30 walk rows and 30 new AI rows; the repeated entry is never checked.
    assert len(checker.calls) == 60


def test_a_walk_row_beats_an_ai_row_for_the_same_entry(tmp_path, checker, ai) -> None:
    source = _write(tmp_path / "source", SIDE_ONLY)
    ai.outcome = _outcome("pairs", [("", "tests/test_bot.py")])

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert len(ai.calls) == 1
    assert _entries(document) == ["tests/test_bot.py"]
    assert document["agents"][0]["subfolder"] == "tests"
    assert AI_SENTENCE not in document["agents"][0]["evidence"]
    assert checker.calls == ["tests"]


def test_a_crash_after_the_ai_step_keeps_what_was_paid_for(
    tmp_path, monkeypatch, checker, ai
) -> None:
    source = _write(tmp_path / "source", SIDE_ONLY)
    ai.outcome = _outcome("pairs", [("app", "app/main.py")])
    checker.fail_at = 2
    checker.error = FolderCheckerBroken("the child ended")

    assert _scan(_job(), source, tmp_path / "broken-check") == 0

    document = _agents(tmp_path / "broken-check")
    assert document["status"] == "found"
    assert sorted(_entries(document)) == ["app/main.py", "tests/test_bot.py"]
    assert document["truncated"] is True
    paid = next(agent for agent in document["agents"] if agent["entry"] == "app/main.py")
    assert paid["framework"]["name"] == "unknown"
    assert not (tmp_path / "broken-check" / "failure.json").exists()

    checker.fail_at, checker.calls = None, []

    def out_of_memory(*args, **kwargs):
        raise MemoryError()

    with monkeypatch.context() as patched:
        patched.setattr(agent_scan, "describe_entry", out_of_memory)

        assert _scan(_job(), source, tmp_path / "describe") == 0

    document = _agents(tmp_path / "describe")
    assert (document["status"], _entries(document)) == ("found", ["tests/test_bot.py"])
    assert document["truncated"] is True

    real_write = agent_scan.write_agents_json

    def refuse_found(output, body):
        if b'"status":"found"' in body:
            raise OSError("disk full")
        return real_write(output, body)

    checker.fail_at, checker.calls = 2, []
    with monkeypatch.context() as patched:
        patched.setattr(agent_scan, "write_agents_json", refuse_found)

        assert _scan(_job(), source, tmp_path / "write-fails") == 0

    document = _agents(tmp_path / "write-fails")
    assert document["status"] == "error"
    assert document["error"]["code"] == "scan_failed_after_ai"
    assert document["agents"] == []
    assert not (tmp_path / "write-fails" / "failure.json").exists()

    def refuse_all(output, body):
        raise OSError("disk full")

    checker.calls = []
    with monkeypatch.context() as patched:
        patched.setattr(agent_scan, "write_agents_json", refuse_all)

        with pytest.raises(FolderCheckerBroken):
            _scan(_job(), source, tmp_path / "nothing-writes")

    assert (tmp_path / "nothing-writes" / "cost.json").exists()


def test_an_upload_prefers_a_non_side_ai_row_to_a_side_walk_row(tmp_path, ai) -> None:
    source = _write(tmp_path / "source", SIDE_ONLY)
    ai.outcome = _outcome("pairs", [("app", "app/main.py")])

    assert _scan(_job("archive"), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert _entries(document) == ["app/main.py"]
    assert document["agents"][0]["subfolder"] == ""
    assert document["truncated"] is True


@pytest.mark.parametrize("retryable", [False, True], ids=["refused", "retryable"])
def test_a_model_error_with_walk_rows_keeps_them(tmp_path, ai, retryable) -> None:
    ai.outcome = _outcome("model_error", retryable=retryable)
    with_rows = _write(tmp_path / "with-rows", SIDE_ONLY)

    assert _scan(_job(), with_rows, tmp_path / "out-rows") == 0

    document = _agents(tmp_path / "out-rows")
    assert (document["status"], _entries(document)) == ("found", ["tests/test_bot.py"])
    assert document["truncated"] is True
    assert not (tmp_path / "out-rows" / "failure.json").exists()

    without = _write(tmp_path / "without", {"app/main.py": PLAIN})

    assert _scan(_job(), without, tmp_path / "out-none") == (1 if retryable else 78)

    failure = _read(tmp_path / "out-none", "failure.json")
    assert (failure["code"], failure["retryable"]) == ("scan_model_error", retryable)
    assert not (tmp_path / "out-none" / "agents.json").exists()


def test_an_ai_row_the_clock_leaves_unchecked_is_still_written(tmp_path, checker, ai) -> None:
    source = _write(tmp_path / "source", SIDE_ONLY)
    ai.outcome = _outcome("pairs", [("app", "app/main.py")])
    checker.fail_at = 2

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    paid = next(agent for agent in document["agents"] if agent["entry"] == "app/main.py")
    assert paid["framework"]["name"] == "unknown"
    assert paid["confidence"] == "low"
    assert document["truncated"] is True


def test_cost_is_written_on_every_return_with_complete(tmp_path, monkeypatch, ai) -> None:
    # Driven without the helper, so only the work's own `finally` can have written these.
    assert _scan(_job(), tmp_path / "missing", tmp_path / "missing-out") == 1
    assert _read(tmp_path / "missing-out", "cost.json")["complete"] is True

    found = _write(tmp_path / "found", {"a/graph.py": GRAPH})
    assert _scan(_job(), found, tmp_path / "found-out") == 0
    assert _read(tmp_path / "found-out", "cost.json")["complete"] is True

    side_only = _write(tmp_path / "side-only", SIDE_ONLY)
    during: list[bool] = []
    ai.during = lambda: during.append(_read(tmp_path / "turn-out", "cost.json")["complete"])
    ai.outcome = _outcome("pairs", [("app", "app/main.py")], complete=False)

    assert _scan(_job(), side_only, tmp_path / "turn-out") == 0

    assert during == [False]
    assert _read(tmp_path / "turn-out", "cost.json")["complete"] is False

    ai.during = None
    ai.outcome = _outcome("forbidden")
    assert _scan(_job(), side_only, tmp_path / "forbidden-out") == 78
    assert _read(tmp_path / "forbidden-out", "cost.json")["complete"] is True

    def crash(*args, **kwargs):
        raise RuntimeError("walk broke")

    monkeypatch.setattr(agent_scan, "walk_repo", crash)
    with pytest.raises(RuntimeError):
        _scan(_job(), found, tmp_path / "crash-out")
    assert (tmp_path / "crash-out" / "cost.json").exists()


def test_a_memory_error_ends_in_a_file(tmp_path, monkeypatch, checker, ai) -> None:
    source = _write(tmp_path / "source", SIDE_ONLY)

    def out_of_memory(*args, **kwargs):
        raise MemoryError()

    monkeypatch.setattr(agent_scan, "walk_repo", out_of_memory)

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert document["status"] == "error"
    assert document["error"]["code"] == "scan_out_of_memory"
    assert checker.calls == []
    assert ai.calls == []


def test_a_broken_checker_before_the_ai_is_a_crash(tmp_path, checker, ai) -> None:
    checker.fail_at = 1
    checker.error = FolderCheckerBroken("the child could not start")

    for name, files in (("found", {"a/graph.py": GRAPH}), ("side-only", SIDE_ONLY)):
        output = tmp_path / f"out-{name}"

        assert _scan(_job(), _write(tmp_path / name, files), output) == 1

        failure = _read(output, "failure.json")
        assert (failure["code"], failure["domain"], failure["retryable"]) == (
            "scan_crashed",
            "infrastructure",
            True,
        )
        assert not (output / "agents.json").exists()
    assert ai.calls == []


@pytest.mark.parametrize(
    ("kind", "retryable", "code", "written"),
    [
        ("model_error", True, 1, ("failure", "scan_model_error", True)),
        ("model_error", False, 78, ("failure", "scan_model_error", False)),
        ("forbidden", False, 78, ("failure", "scan_model_forbidden", False)),
        ("no_answer", False, 0, ("error", "scan_ai_no_answer")),
        ("timed_out", False, 0, ("error", "scan_timed_out")),
        ("empty", False, 0, ("none",)),
    ],
)
def test_each_ai_outcome_gives_its_exit_code(tmp_path, ai, kind, retryable, code, written) -> None:
    source = _write(tmp_path / "source", {"app/main.py": PLAIN})
    ai.outcome = _outcome(kind, retryable=retryable)
    output = tmp_path / "out"

    assert _scan(_job(), source, output) == code

    assert len(ai.calls) == 1
    if written[0] == "failure":
        failure = _read(output, "failure.json")
        assert (failure["code"], failure["retryable"]) == written[1:]
        assert failure["domain"] == "infrastructure"
        assert not (output / "agents.json").exists()
    else:
        document = _agents(output)
        assert document["status"] == written[0]
        if written[0] == "error":
            assert document["error"]["code"] == written[1]
        assert not (output / "failure.json").exists()


def test_every_failure_goes_through_the_helper(tmp_path, monkeypatch, checker, ai) -> None:
    ran = tmp_path / "ran"
    source = _write(
        tmp_path / "source",
        {**SIDE_ONLY, "app/main.py": PLAIN + f"open({str(ran)!r}, 'w').close()\n"},
    )
    plain = _write(tmp_path / "plain", {"app/main.py": PLAIN})
    written = []
    real = agent_scan.write_reading_failure

    def recording(failure, output):
        written.append(failure)
        return real(failure, output)

    monkeypatch.setattr(agent_scan, "write_reading_failure", recording)

    def invalid(**kwargs):
        raise ScanResultInvalid("agents.too_many")

    def broken_checker():
        checker.fail_at, checker.error = 1, FolderCheckerBroken("no child")

    def bad_document(patched):
        patched.setattr(agent_scan, "finish", invalid)

    def bad_document_after_ai(patched):
        ai.outcome = _outcome("pairs", [("app", "app/main.py")])
        patched.setattr(agent_scan, "finish", invalid)

    cases = [
        ("missing", tmp_path / "missing", lambda patched: None, 1, "scan_source_missing"),
        ("broken", source, lambda patched: broken_checker(), 1, "scan_crashed"),
        (
            "retryable",
            plain,
            lambda patched: setattr(ai, "outcome", _outcome("model_error", retryable=True)),
            1,
            "scan_model_error",
        ),
        (
            "refused",
            plain,
            lambda patched: setattr(ai, "outcome", _outcome("model_error")),
            78,
            "scan_model_error",
        ),
        (
            "forbidden",
            plain,
            lambda patched: setattr(ai, "outcome", _outcome("forbidden")),
            78,
            "scan_model_forbidden",
        ),
        ("invalid", source, bad_document, 78, "scan_result_invalid"),
        ("invalid-after-ai", source, bad_document_after_ai, 78, "scan_result_invalid"),
    ]
    for name, scanned, arrange, code, failure_code in cases:
        checker.fail_at, checker.calls = None, []
        ai.outcome = _outcome("empty")
        output = tmp_path / f"out-{name}"
        before = _tree(tmp_path, output)
        with monkeypatch.context() as patched:
            arrange(patched)

            assert _scan(_job(), scanned, output) == code

        assert written[-1].code == failure_code
        assert written[-1].stage is HarnessStage.UNDERSTANDING_AGENT
        assert written[-1].message.strip()
        failure = _read(output, "failure.json")
        assert (failure["code"], failure["stage"], failure["message"]) == (
            failure_code,
            "understanding_agent",
            written[-1].message,
        )
        assert not (output / "agents.json").exists()
        # Nothing is written outside the output folder, and the source never runs.
        assert _tree(tmp_path, output) == before

    # A scan that ends in agents.json writes it into the output folder and nowhere else.
    checker.fail_at, checker.calls = None, []
    ai.outcome = _outcome("empty")
    output = tmp_path / "out-found"
    before = _tree(tmp_path, output)

    assert _scan(_job(), source, output) == 0

    assert _agents(output)["status"] == "found"
    assert _tree(tmp_path, output) == before
    assert len(written) == len(cases)
    assert not ran.exists()


def test_a_refused_pair_in_the_kept_submit_sets_truncated(tmp_path, ai) -> None:
    source = _write(tmp_path / "source", {"app/main.py": PLAIN})

    for refused, truncated in ((0, False), (1, True)):
        ai.outcome = _outcome("pairs", [("app", "app/main.py")], refused=refused)
        output = tmp_path / f"out-{refused}"

        assert _scan(_job(), source, output) == 0

        document = _agents(output)
        assert _entries(document) == ["app/main.py"]
        assert document["truncated"] is truncated


def test_a_crash_after_the_ai_step_logs_its_trace_masked(tmp_path, monkeypatch, caplog, ai) -> None:
    source = _write(tmp_path / "source", SIDE_ONLY)
    ai.outcome = _outcome("pairs", [("app", "app/main.py")])
    secret = "FAKE_SECRET_VALUE_123"
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"SIMULATOR_OPENAI_API_KEY": secret}), encoding="utf-8")
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (secrets,))

    def crash(*args, **kwargs):
        raise RuntimeError(f"bad value {secret}")

    monkeypatch.setattr(agent_scan, "describe_entry", crash)
    with caplog.at_level(logging.DEBUG, logger=agent_scan.logger.name):
        assert _scan(_job(), source, tmp_path / "out") == 0

    assert "failed after the model step" in caplog.text
    assert "Traceback" in caplog.text and "RuntimeError" in caplog.text
    assert secret not in caplog.text
    assert _agents(tmp_path / "out")["status"] == "found"


def test_an_uploads_crash_after_the_ai_step_still_writes_one_row(tmp_path, checker, ai) -> None:
    source = _write(tmp_path / "source", {**SIDE_ONLY, "svc/bot.py": PLAIN})
    ai.outcome = _outcome("pairs", [("app", "app/main.py"), ("svc", "svc/bot.py")])
    # The walk's one side row is checked first; the AI rows' check then breaks.
    checker.fail_at = 2
    checker.error = FolderCheckerBroken("the child ended")

    assert _scan(_job("archive"), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert len(document["agents"]) == 1
    assert _entries(document) == ["app/main.py"]
    assert document["agents"][0]["subfolder"] == ""
    assert document["truncated"] is True
    assert document["commit_sha"] is None


def test_an_upload_with_only_side_rows_keeps_the_walks_row(tmp_path, ai) -> None:
    source = _write(tmp_path / "source", {**SIDE_ONLY, "tests/test_other.py": PLAIN})
    ai.outcome = _outcome("pairs", [("tests", "tests/test_other.py")])

    assert _scan(_job("archive"), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert len(ai.calls) == 1
    assert _entries(document) == ["tests/test_bot.py"]
    assert document["agents"][0]["subfolder"] == ""
    assert document["truncated"] is True


def test_hints_count_an_uploads_other_side_files(tmp_path) -> None:
    walk = _walk(
        agents=(_candidate("tests/test_bot.py", ""),),
        upload_others=2,
        upload_others_side=2,
        unread_files=0,
    )

    assert "agent-like files inside them: 3;" in _hints(walk)


def _beside_a_script_agent(helper: str) -> dict[str, str]:
    return {"web/src/agent.ts": SCRIPT_AGENT, "backend/judge.py": HELPERS[helper]}


@pytest.mark.parametrize("helper", sorted(HELPERS))
def test_a_script_agent_beside_a_python_model_helper_is_asked_for(tmp_path, ai, helper) -> None:
    source = _write(tmp_path / "source", _beside_a_script_agent(helper))
    ai.outcome = _outcome("pairs", [("web", "web/src/agent.ts")])
    output = tmp_path / "out"

    assert _read_mode("github", source, output) == 0

    document = _agents(output)
    assert len(ai.calls) == 1
    assert document["status"] == "found"
    assert _entries(document) == ["backend/judge.py", "web/src/agent.ts"]
    assert _read(output, "cost.json")["complete"] is True


# Every suffix spelled out, so a suffix dropped from the walk's list fails its own case.
@pytest.mark.parametrize("suffix", [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"])
def test_an_agent_in_any_script_suffix_beside_a_helper_is_asked_for(tmp_path, ai, suffix) -> None:
    entry = f"web/agent{suffix}"
    source = _write(tmp_path / "source", {entry: SCRIPT_AGENT, "backend/judge.py": MODEL_HELPER})
    ai.outcome = _outcome("pairs", [("web", entry)])
    output = tmp_path / "out"

    assert _read_mode("github", source, output) == 0

    assert len(ai.calls) == 1
    assert _entries(_agents(output)) == ["backend/judge.py", entry]


def test_built_vendored_and_config_scripts_do_not_ask_for_the_model(tmp_path, ai) -> None:
    scripts = {
        "tailwind.config.js": False,
        "eslint.config.mjs": False,
        "vite.config.ts": False,
        "static/js/jquery.min.js": False,
        "web/app.min.js": False,
        "frontend/dist/assets/index-3f9a.js": False,
        "web/build/main.js": False,
        "web/vendor/lib.js": False,
        "types/env.d.ts": False,
        "types/env.d.mts": False,
        "web/src/agent.ts": True,
    }
    for script, asks in scripts.items():
        shape = script.replace("/", "_")
        source = _write(tmp_path / shape, {"app/main.py": CLIENT, script: "export {};\n"})
        output = tmp_path / f"out-{shape}"
        ai.calls.clear()

        assert _read_mode("github", source, output) == 0, script

        assert len(ai.calls) == (1 if asks else 0), script
        assert _entries(_agents(output)) == ["app/main.py"], script


def test_the_model_is_not_asked_past_a_helper_without_a_script_agent_beside_it(
    tmp_path, ai
) -> None:
    shapes = {
        "no_script": ({"backend/judge.py": MODEL_HELPER}, "backend/judge.py"),
        "beside_a_framework_agent": (
            {"web/src/agent.ts": SCRIPT_AGENT, "bot/graph.py": GRAPH},
            "bot/graph.py",
        ),
        "script_only_in_a_side_folder": (
            {"examples/web/agent.ts": SCRIPT_AGENT, "backend/judge.py": MODEL_HELPER},
            "backend/judge.py",
        ),
    }
    for shape, (files, entry) in shapes.items():
        source = _write(tmp_path / shape, files)
        output = tmp_path / f"out-{shape}"

        assert _read_mode("github", source, output) == 0, shape

        document = _agents(output)
        assert ai.calls == [], shape
        assert _entries(document) == [entry], shape
        assert _read(output, "cost.json")["complete"] is True, shape


@pytest.mark.parametrize("helper", sorted(HELPERS))
def test_an_upload_prefers_the_models_row_to_a_python_model_helper(tmp_path, ai, helper) -> None:
    source = _write(tmp_path / "source", _beside_a_script_agent(helper))
    ai.outcome = _outcome("pairs", [("web", "web/src/agent.ts")])
    output = tmp_path / "out"

    assert _read_mode("archive", source, output) == 0

    document = _agents(output)
    assert len(ai.calls) == 1
    assert _entries(document) == ["web/src/agent.ts"]
    assert document["agents"][0]["subfolder"] == ""
    assert document["truncated"] is True


def test_an_uploads_model_row_in_a_side_folder_loses_to_a_python_client(tmp_path, ai) -> None:
    source = _write(
        tmp_path / "source",
        {**_beside_a_script_agent("client"), "examples/agent.ts": SCRIPT_AGENT},
    )
    ai.outcome = _outcome("pairs", [("examples", "examples/agent.ts")])
    output = tmp_path / "out"

    assert _read_mode("archive", source, output) == 0

    document = _agents(output)
    assert len(ai.calls) == 1
    assert _entries(document) == ["backend/judge.py"]
    assert document["truncated"] is True


@pytest.mark.parametrize("helper", sorted(HELPERS))
def test_hints_count_the_model_helpers_outside_side_folders(tmp_path, ai, helper) -> None:
    source = _write(
        tmp_path / "source", {**_beside_a_script_agent(helper), "tests/test_bot.py": CLIENT}
    )

    assert _read_mode("github", source, tmp_path / "out") == 0

    hints = ai.calls[0]["hints"]
    assert "agent-like files inside them: 1;" in hints
    assert "files outside them that only call a model: 1;" in hints
    for word in ("judge", "backend", "web", "/", ".py"):
        assert word not in hints


def test_an_uploads_hints_count_each_unlisted_file_where_it_sits(tmp_path, ai) -> None:
    files = {
        "a/one.py": CLIENT,
        "b/two.py": CLIENT,
        "c/three.py": MODEL_HELPER,
        "tests/test_four.py": CLIENT,
        "web/agent.ts": SCRIPT_AGENT,
    }
    source = _write(tmp_path / "source", files)

    assert _read_mode("archive", source, tmp_path / "out") == 0

    hints = ai.calls[0]["hints"]
    assert "agent-like files inside them: 1;" in hints
    assert "files outside them that only call a model: 3;" in hints


def test_the_models_rows_are_kept_beside_fifty_helpers(tmp_path, checker, ai) -> None:
    fifty = {f"p{index:02d}/main.py": CLIENT for index in range(50)}
    forty_seven = {f"p{index:02d}/main.py": CLIENT for index in range(47)}
    scripts = {f"web{index}/agent.ts": SCRIPT_AGENT for index in range(5)}
    # Each helper in a folder of its own, so every walk row is `medium` and every model row
    # `low`: by confidence alone, the cap would drop the model's rows.
    shapes = {
        "fifty_and_one": (
            {**fifty, "web0/agent.ts": SCRIPT_AGENT},
            [("web0", "web0/agent.ts")],
            ["web0/agent.ts"],
        ),
        "forty_seven_and_five": (
            {**forty_seven, **scripts},
            [(f"web{index}", f"web{index}/agent.ts") for index in range(5)],
            sorted(scripts),
        ),
        # A model row inside a side folder keeps its place behind every walk row.
        "fifty_and_one_in_a_side_folder": (
            {**fifty, "web0/agent.ts": SCRIPT_AGENT, "examples/agent.ts": SCRIPT_AGENT},
            [("examples", "examples/agent.ts")],
            [],
        ),
    }
    for shape, (files, pairs, kept) in shapes.items():
        source = _write(tmp_path / shape, files)
        output = tmp_path / f"out-{shape}"
        ai.calls.clear()
        ai.outcome = _outcome("pairs", pairs)

        assert _read_mode("github", source, output) == 0, shape

        document = _agents(output)
        written = _entries(document)
        assert len(ai.calls) == 1, shape
        assert len(written) == 50, shape
        assert [entry for entry in written if not entry.startswith("p")] == kept, shape
        assert document["truncated"] is True, shape


def test_the_models_row_beside_fifty_helpers_survives_a_crash_after_the_model(
    tmp_path, checker, ai
) -> None:
    files = {f"p{index:02d}/main.py": CLIENT for index in range(50)}
    source = _write(tmp_path / "source", {**files, "web/agent.ts": SCRIPT_AGENT})
    ai.outcome = _outcome("pairs", [("web", "web/agent.ts")])
    # The walk's fifty checks pass; the model row's check crashes.
    checker.fail_at, checker.error = 51, RuntimeError("the checker broke")
    output = tmp_path / "out"

    assert _read_mode("github", source, output) == 0

    document = _agents(output)
    assert len(ai.calls) == 1
    assert "web/agent.ts" in _entries(document)
    assert len(document["agents"]) == 50
    assert document["truncated"] is True


def test_a_langchain_chat_model_needs_no_ai_step(tmp_path, ai) -> None:
    example = Path(__file__).parents[2] / "examples" / "frameworks" / "langchain_agent.py"
    source = tmp_path / "source"
    source.mkdir()
    shutil.copyfile(example, source / "langchain_agent.py")

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert ai.calls == []
    assert document["status"] == "found"
    assert _entries(document) == ["langchain_agent.py"]
    assert document["agents"][0]["confidence"] == "low"


def test_every_hazard_look_gets_the_scans_one_memo_and_its_deadline(
    tmp_path, monkeypatch, ai
) -> None:
    source = _write(tmp_path / "source", {**SIDE_ONLY, "web/main.py": PLAIN})
    ai.outcome = _outcome("pairs", [("app", "app/main.py"), ("web", "web/main.py")])
    looks: list[tuple[str, float, object]] = []
    real = scan_walk.build_hazard

    # The real defaults, so a call that leaves a keyword out is recorded with the default.
    def recording(source, subfolder, *, deadline=math.inf, clock=time.monotonic, hazards=None):
        looks.append((subfolder, deadline, hazards))
        return real(source, subfolder, deadline=deadline, clock=clock, hazards=hazards)

    monkeypatch.setattr(scan_walk, "build_hazard", recording)
    start = time.monotonic()

    assert _scan(_job(), source, tmp_path / "out") == 0

    end = time.monotonic()
    assert len(ai.calls) == 1
    # The walk's side row first, then each row the model found.
    assert [subfolder for subfolder, _, _ in looks] == ["tests", "app", "web"]
    memo = looks[0][2]
    assert isinstance(memo, dict)
    assert all(hazards is memo for _, _, hazards in looks)
    deadlines = {deadline for _, deadline, _ in looks}
    assert len(deadlines) == 1
    [deadline] = deadlines
    assert start + agent_scan.CLOCK <= deadline <= end + agent_scan.CLOCK


def test_a_file_with_a_chat_model_and_a_client_is_a_low_model_row(
    tmp_path, checker, ai
) -> None:
    source = _write(
        tmp_path / "source",
        {
            "bot.py": (
                "from langchain_openai import ChatOpenAI\n"
                "from openai import OpenAI\n"
                "\n"
                "ChatOpenAI()\n"
                "OpenAI()\n"
            )
        },
    )
    checker.name = "langchain"

    assert _scan(_job(), source, tmp_path / "out") == 0

    document = _agents(tmp_path / "out")
    assert ai.calls == []
    [agent] = document["agents"]
    assert agent["confidence"] == "low"
    assert "Calls a chat model through LangChain in bot.py, line 4." in agent["evidence"]
    assert "Creates a client for openai in bot.py, line 5." in agent["evidence"]


def test_an_agent_at_a_path_the_platform_cannot_carry_is_never_reported_as_none(
    tmp_path, ai
) -> None:
    """The vercel/ai-chatbot run: the model found ``app/(chat)/api/chat/route.ts`` and every
    submit was refused, which used to be written as a whole list with no agents."""
    source = _write(tmp_path / "source", {"app/main.py": PLAIN})

    ai.outcome = _outcome("empty", uncarried=1)
    assert _scan(_job(), source, tmp_path / "out-empty") == 0
    document = _agents(tmp_path / "out-empty")
    assert document["agents"] == []
    assert document["truncated"] is True
    assert document["error"]["code"] == "agent_path_unsupported"

    ai.outcome = _outcome("pairs", [("app", "app/main.py")], uncarried=1)
    assert _scan(_job(), source, tmp_path / "out-pairs") == 0
    document = _agents(tmp_path / "out-pairs")
    assert _entries(document) == ["app/main.py"]
    assert document["truncated"] is True
    assert document["error"] is None


@pytest.mark.parametrize("step", ["run", "result"])
def test_a_fault_in_the_walk_is_a_crash_not_a_big_repository(tmp_path, monkeypatch, ai, step) -> None:
    """A bug in the walk must not be told to the person as "the repository is too large"."""
    source = _write(tmp_path / "source", {"app/graph.py": GRAPH})

    def broken(*args, **kwargs):
        raise KeyError("a fault in the kit")

    monkeypatch.setattr(scan_walk._Walk, step, broken)
    output = tmp_path / "out"

    assert _read_mode("github", source, output) == 1

    failure = _read(output, "failure.json")
    assert (failure["code"], failure["retryable"]) == ("scan_crashed", True)
    assert not (output / "agents.json").exists()
    assert len(ai.calls) == 0
