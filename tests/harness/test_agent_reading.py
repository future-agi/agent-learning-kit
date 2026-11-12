"""Reading one agent with no build: the files it leaves, what it is charged, and how it stops.

The model is a fake backend behind ``session.resolve``, so the real stage, the reading's own
stage, the contract server and the browser-agent gate all run; only the provider is scripted.
"""

from __future__ import annotations

import argparse
import asyncio
import builtins
import dataclasses
import itertools
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import types
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import fi.alk
from fi.alk.harness import (
    agent_reading,
    authoring_entrypoint,
    reading_entry,
    reading_result,
    scenario_source,
    session,
    spend,
    tools,
)
from fi.alk.harness.backends import StageDone
from fi.alk.harness.backends.base import qualified
from fi.alk.harness.config import chosen_model
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.failure_file import write_failure_file
from fi.alk.harness.framework_detection import detect_framework
from fi.alk.harness.job import (
    FailureDomain,
    HarnessFailure,
    HarnessJob,
    HarnessStage,
    SourceKind,
)
from fi.alk.harness.reading_entry import READING_JOB_INVALID, run_reading_mode
from fi.alk.harness.tools import CONTRACT_SERVER, _checked_contract
from fi.alk.harness.understand import SUBMIT_REMINDER, reading_settings
from fi.alk.providers.agent import build_authored_agent

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "frameworks"
SUPPORT = "agents/support"
SCRAPER = "agents/scraper"
MIB = 1_048_576
CHAT = {
    "agent": "support",
    "modality": "chat",
    "real_use_cases": ["order"],
    "tools": [{"name": "add", "args": ["item_id"]}],
}
# As the gate saves it for a job that names the folder.
IN_SUPPORT = {
    **CHAT,
    "runtime": {"language": "python", "workdir": SUPPORT, "source_subfolder": SUPPORT},
}
_LIMIT_ENV = "ALK_READING_MAX_DURATION_SECONDS"


class _LoudExit:
    """``os`` for the helper under test: its hard limit ending this test process is a failure,
    never the silent exit 0 its backstop gives a finished reading."""

    def __getattr__(self, name):
        return getattr(os, name)

    @staticmethod
    def _exit(_code):
        os.write(2, b"a reading's hard limit ended the test process\n")
        os._exit(97)


@pytest.fixture(autouse=True)
def _fresh_state(monkeypatch, tmp_path):
    monkeypatch.setattr(reading_entry, "os", _LoudExit())
    monkeypatch.delenv(spend.JOURNAL_ALIAS, raising=False)
    monkeypatch.setenv("ALK_HARNESS_MODEL", "gemini-2.5-flash")
    # The machine's own secrets files must not decide what a test sees masked.
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (tmp_path / "no-secrets.json",))
    # A scripted model that never answers must end a test, not hang it.
    monkeypatch.setenv(_LIMIT_ENV, "20")
    spend._stages.clear()
    spend.journal_to(None)
    yield
    spend._stages.clear()
    spend.journal_to(None)


# -- the fake provider --------------------------------------------------------------------------


class _Live:
    """One session of the fake backend; each message sent plays the next scripted turn."""

    def __init__(self, backend: "_Backend", spec) -> None:
        self.backend = backend
        self.spec = spec
        self.interrupted = asyncio.Event()

    async def start(self) -> None:
        if self.backend.saves_on_start is not None:
            _save(self.backend.output, self.backend.saves_on_start)
        if self.backend.start_error is not None:
            raise self.backend.start_error
        if self.backend.start_raises:
            raise FileNotFoundError("the Claude CLI was not found")

    async def stop(self) -> None:
        if self.backend.close_hangs:
            # A session stuck shutting down; only the loop's own teardown ends it.
            await asyncio.Event().wait()
        output = self.backend.output
        self.backend.stopped.append(
            {
                "failure": (output / "failure.json").exists(),
                "agent": (output / "agent.json").exists(),
            }
        )

    async def send(self, message: str) -> None:
        self.backend.sent.append(message)

    async def interrupt(self) -> bool:
        self.backend.interrupts.append(time.monotonic())
        if self.backend.interrupt_raises:
            raise self.backend.interrupt_error(self.backend.interrupt_says)
        self.interrupted.set()
        return True

    def replies(self):
        turn = self.backend.turns.pop(0) if self.backend.turns else _ends()
        return turn(self)


class _Backend:
    default_model = "gemini-2.5-flash"

    def __init__(self, name: str, output: Path, turns: list) -> None:
        self.name = name
        self.output = output
        self.turns = turns
        self.created: list = []
        self.sent: list[str] = []
        self.interrupts: list[float] = []
        self.stopped: list[dict] = []
        self.seen: list = []
        self.interrupt_raises = False
        self.interrupt_says = "the interrupt control request timed out"
        self.interrupt_error: type[BaseException] = RuntimeError
        self.start_raises = False
        self.saves_on_start: dict | None = None
        self.close_hangs = False
        self.start_error: BaseException | None = None
        self.submissions = 0

    def create(self, spec) -> _Live:
        self.created.append(spec)
        return _Live(self, spec)

    def can_drive(self, model: str) -> bool:
        return True


@pytest.fixture
def model(tmp_path, monkeypatch):
    def install(*turns, name: str = "claude", output: Path | None = None) -> _Backend:
        fake = _Backend(name, output or tmp_path / "reading", list(turns))
        monkeypatch.setattr(session, "resolve", lambda *a, **k: fake)
        return fake

    return install


def _done(**fields) -> StageDone:
    return StageDone(**{"outcome": "success", "turns": 1, "cost_usd": 0.0, **fields})


def _ends(**fields):
    async def turn(live):
        yield _done(**fields)

    return turn


async def _gate(live: _Live, arguments: dict) -> None:
    """What the model's call does: the real submit_contract from the reading's own spec."""
    tool = next(
        spec for spec in live.spec.servers[CONTRACT_SERVER].tools if spec.name == "submit_contract"
    )
    output = live.backend.output
    # A first thin submission is sent back once, so the model submits again.
    for _ in range(3):
        live.backend.submissions += 1
        await tool.handler(dict(arguments))
        if (output / "contract.json").exists() or (output / "failure.json").exists():
            return


def _submits(arguments: dict, **fields):
    async def turn(live):
        await _gate(live, arguments)
        yield _done(**fields)

    return turn


def _save(output: Path, contract: dict | None = None, *, text: str | None = None) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "contract.json").write_text(
        text if text is not None else json.dumps(contract), encoding="utf-8"
    )


def _writes(contract: dict | None = None, *, text: str | None = None, **fields):
    """A turn whose contract the gate has already accepted and saved."""

    async def turn(live):
        _save(live.backend.output, contract, text=text)
        yield _done(**fields)

    return turn


async def _quiet(live):
    await asyncio.Event().wait()
    yield _done()


# -- the job, the source and the run ------------------------------------------------------------


def _job(*, subfolder: str | None = None, source: dict | None = None, metadata=None) -> dict:
    job = {
        "job_id": "job",
        "run_id": "run",
        "execution": "hosted",
        "source": source
        or {
            "kind": "github",
            "visibility": "public",
            "repository": "acme/agent",
            "commit_sha": "a" * 40,
            **({"subfolder": subfolder} if subfolder else {}),
        },
        "runtime": {"isolation": "dedicated_vm"},
        "agent": {"connector": "auto"},
    }
    if metadata is not None:
        job["metadata"] = metadata
    return job


def _repo(root: Path) -> Path:
    source = root / "source"
    (source / SUPPORT).mkdir(parents=True)
    (source / SUPPORT / "agent.py").write_text("x = 1\n", encoding="utf-8")
    (source / SCRAPER).mkdir(parents=True)
    (source / SCRAPER / "agent.py").write_text(
        "from playwright.sync_api import sync_playwright\nsync_playwright()\n", encoding="utf-8"
    )
    return source


def _run(
    root: Path,
    job: dict | None = None,
    *,
    source: Path | None = None,
    output=None,
    wait: float = 1.0,
) -> int:
    job_path = root / "job.json"
    job_path.write_text(json.dumps(job or _job()), encoding="utf-8")
    return run_reading_mode(
        agent_reading._read_agent(interrupt_wait=wait),
        job_path=job_path,
        source=source if source is not None else root / "source",
        output=output if output is not None else root / "reading",
    )


def _failure_in(output: Path) -> dict:
    return json.loads((output / "failure.json").read_text(encoding="utf-8"))


def _cost_in(output: Path) -> dict:
    return json.loads((output / "cost.json").read_text(encoding="utf-8"))


def _names(output: Path) -> set[str]:
    return {path.name for path in output.iterdir()}


def _assert_cost(output: Path, total: float, complete: bool) -> None:
    cost = _cost_in(output)
    assert cost["total_usd"] == pytest.approx(total)
    assert cost["complete"] is complete


def _short_idle(monkeypatch, seconds: float = 0.2) -> None:
    real = agent_reading.open_stage

    def opened(*args, **kwargs):
        stage, destination = real(*args, **kwargs)
        stage.spec.idle_timeout_seconds = seconds
        return stage, destination

    monkeypatch.setattr(agent_reading, "open_stage", opened)


def _few_turns(monkeypatch, turns: int) -> None:
    real = agent_reading.open_stage

    def opened(*args, **kwargs):
        stage, destination = real(*args, **kwargs)
        stage.spec.max_turns = turns
        return stage, destination

    monkeypatch.setattr(agent_reading, "open_stage", opened)


def _short_limit(monkeypatch, soft: float = 0.3) -> None:
    monkeypatch.setenv(_LIMIT_ENV, str(soft))


# -- R1 to R9: the files a reading leaves -------------------------------------------------------


def test_a_reading_leaves_the_contract_the_document_and_the_cost(tmp_path, model):
    source = _repo(tmp_path)
    model(_submits(CHAT, cost_usd=0.25))
    output = tmp_path / "reading"

    assert _run(tmp_path, _job(subfolder=SUPPORT)) == 0

    assert _names(output) == {"contract.json", "agent.json", "cost.json"}
    document = json.loads((output / "agent.json").read_text(encoding="utf-8"))
    assert set(document) == {"sdk_version", "agent"}
    assert document["sdk_version"] == fi.alk.__version__
    agent = document["agent"]
    assert agent["extension"]["source"] == {"subfolder": SUPPORT}
    contract = AgentContract.model_validate(
        json.loads((output / "contract.json").read_text(encoding="utf-8"))
    ).model_dump(mode="json")
    framework = detect_framework(source / SUPPORT, repo_root=source, package_dir=None)
    assert agent == build_authored_agent(contract, subfolder=SUPPORT, framework=framework)
    _assert_cost(output, 0.25, True)


@pytest.mark.parametrize(
    ("write", "expected"),
    [
        (
            lambda folder: (folder / "graph.py").write_text(
                "from langgraph.graph import StateGraph\nStateGraph(int)\n", encoding="utf-8"
            ),
            {"name": "langgraph", "version": None},
        ),
        (
            lambda folder: shutil.copy(EXAMPLES / "llamaindex_agent.py", folder),
            {"name": "other", "version": None, "label": "llama_index"},
        ),
    ],
    ids=["langgraph", "other"],
)
def test_the_framework_is_a_builds_shape(tmp_path, model, write, expected):
    source = _repo(tmp_path)
    write(source / SUPPORT)
    model(_writes(IN_SUPPORT))

    assert _run(tmp_path, _job(subfolder=SUPPORT)) == 0

    agent = json.loads((tmp_path / "reading" / "agent.json").read_text(encoding="utf-8"))
    assert agent["agent"]["extension"]["framework"] == expected


def test_with_no_folder_named_the_framework_comes_from_the_contracts_folder(tmp_path, model):
    source = _repo(tmp_path)
    shutil.copy(EXAMPLES / "llamaindex_agent.py", source / SUPPORT)
    # A different framework at the root, so reading the root instead would show.
    (source / "graph.py").write_text(
        "from langgraph.graph import StateGraph\nStateGraph(int)\n", encoding="utf-8"
    )
    model(_writes({**CHAT, "runtime": {"language": "python", "workdir": SUPPORT}}))

    assert _run(tmp_path) == 0

    agent = json.loads((tmp_path / "reading" / "agent.json").read_text(encoding="utf-8"))
    assert agent["agent"]["extension"]["framework"] == {
        "name": "other",
        "version": None,
        "label": "llama_index",
    }


def test_a_browser_agent_is_refused_by_the_real_gate(tmp_path, model):
    _repo(tmp_path)
    fake = model(_submits({"modality": "browser"}, cost_usd=0.37))
    output = tmp_path / "reading"

    assert _run(tmp_path, _job(subfolder=SCRAPER)) == 78

    assert _failure_in(output)["code"] == "agent_type_unsupported"
    # One second look before any refusal: the browser answer to it stands and is refused.
    assert fake.submissions == 2
    assert len(fake.sent) == 1
    assert not (output / "agent.json").exists()
    assert not (output / "contract.json").exists()
    _assert_cost(output, 0.37, True)


def test_a_missing_folder_is_refused_as_a_build_refuses_it(tmp_path, model):
    _repo(tmp_path)
    fake = model()
    output = tmp_path / "reading"

    assert _run(tmp_path, _job(subfolder="agents/missing")) == 78

    failure = _failure_in(output)
    assert (failure["code"], failure["stage"]) == ("subfolder_not_found", "acquiring_source")
    assert fake.created == []
    _assert_cost(output, 0.0, True)


def _no_source(root: Path) -> Path:
    return root / "source"


def _a_file(root: Path) -> Path:
    (root / "source").write_text("not a folder", encoding="utf-8")
    return root / "source"


def _an_empty_folder(root: Path) -> Path:
    (root / "source").mkdir()
    return root / "source"


@pytest.mark.parametrize(
    ("make", "subfolder"),
    [
        (_no_source, None),
        (_a_file, None),
        (_an_empty_folder, None),
        (_no_source, SUPPORT),
    ],
    ids=["missing", "a_file", "empty", "missing_with_a_folder"],
)
def test_a_missing_or_empty_source_is_retryable_and_costs_nothing(
    tmp_path, model, make, subfolder
):
    source = make(tmp_path)
    fake = model()
    output = tmp_path / "reading"

    assert _run(tmp_path, _job(subfolder=subfolder), source=source) == 1

    failure = _failure_in(output)
    assert (failure["code"], failure["domain"], failure["retryable"]) == (
        "reading_source_empty",
        "platform_sync",
        True,
    )
    assert fake.created == []
    _assert_cost(output, 0.0, True)


@pytest.mark.parametrize(
    "kind",
    [SourceKind.REMOTE, SourceKind.PROVIDER],
    ids=["remote", "provider"],
)
def test_only_github_or_an_upload_is_read(tmp_path, model, kind):
    _repo(tmp_path)
    fake = model()
    parsed = HarnessJob.model_validate(_job())

    other = parsed.model_copy(
        update={"source": parsed.source.model_copy(update={"kind": kind})}
    )
    refused = agent_reading._check_job(other)
    assert refused is not None
    assert (refused.code, refused.domain.value, refused.retryable) == (
        READING_JOB_INVALID,
        "platform_sync",
        False,
    )
    for accepted in (SourceKind.GITHUB, SourceKind.ARCHIVE):
        assert (
            agent_reading._check_job(
                parsed.model_copy(
                    update={"source": parsed.source.model_copy(update={"kind": accepted})}
                )
            )
            is None
        )

    if kind is SourceKind.REMOTE:
        remote = _job(source={"kind": "remote", "endpoint": "https://agent.example.com/chat"})
        HarnessJob.model_validate(remote)
        assert _run(tmp_path, remote) == 78
        assert _failure_in(tmp_path / "reading")["code"] == READING_JOB_INVALID
        assert fake.created == []


@pytest.mark.parametrize(
    "agent",
    [
        {"connector": "vapi", "mode": "provider_import", "config": {"assistant_id": "asst-1"}},
        {"connector": "vapi", "mode": "connect_only", "config": {"assistant_id": "asst-1"}},
    ],
    ids=["provider_import", "connect_only"],
)
def test_a_provider_defined_agent_is_not_read_without_a_build(tmp_path, model, agent):
    _repo(tmp_path)
    fake = model()
    output = tmp_path / "reading"

    assert _run(tmp_path, {**_job(), "agent": agent}) == 78

    failure = _failure_in(output)
    assert (failure["code"], failure["domain"], failure["retryable"]) == (
        READING_JOB_INVALID,
        "platform_sync",
        False,
    )
    assert "repository only" in failure["message"]
    assert fake.created == []


# Every connector and mode a job can carry; padded and mixed case as the loader normalises them.
_P_CONNECTORS = [
    "auto",
    "http",
    "websocket",
    "livekit",
    "phone",
    "vapi",
    "retell",
    "retell_chat",
    " LiveKit ",
    "VAPI",
    " VAPI ",
    "Retell_Chat",
]
_P_MODES = [None, "connect_only", "environment_backed", "provider_import"]
# What the build loads a profile for: from the provider, or from the prompt pasted into the job.
_P_REFUSED = {
    ("vapi", "connect_only"),
    ("retell", "connect_only"),
    ("retell_chat", "connect_only"),
    ("phone", "connect_only"),
    ("livekit", "connect_only"),
    ("vapi", "provider_import"),
    ("retell", "provider_import"),
    ("retell_chat", "provider_import"),
}


def _p_agent(connector: str, mode: str | None) -> dict:
    config = {
        "assistant_id": "asst-1",
        "agent_id": "agent-1",
        "agent_name": "lk",
        "target_system_prompt": "Be kind.",
    }
    if connector == "phone":
        config["phone_number"] = "+14155550123"
    if mode == "environment_backed":
        config = {k: v for k, v in config.items() if k not in ("assistant_id", "agent_id")}
    return {"connector": connector, "config": config, **({"mode": mode} if mode else {})}


def _p_admitted():
    """Every (connector, mode) pair a job validates with."""
    for connector, mode in itertools.product(_P_CONNECTORS, _P_MODES):
        try:
            HarnessJob.model_validate({**_job(), "agent": _p_agent(connector, mode)})
        except ValueError:
            continue
        yield connector, mode


@pytest.mark.parametrize("connector,mode", list(_p_admitted()))
def test_the_reading_refuses_exactly_what_the_build_loads_a_profile_for(
    tmp_path, monkeypatch, connector, mode
):
    """Checks the build still uses the shared rule. Both sides call it, so a break inside the rule
    moves them together: the refused-pairs table below is the real guard on the rule itself."""
    monkeypatch.setattr(
        authoring_entrypoint, "inspect_provider_target", lambda provider, **_: {"provider": provider}
    )
    job = HarnessJob.model_validate({**_job(), "agent": _p_agent(connector, mode)})
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"VAPI_API_KEY": "x", "RETELL_API_KEY": "y"}), encoding="utf-8")

    assert (agent_reading._check_job(job) is not None) is (
        authoring_entrypoint._load_provider_import_profile(job, secrets) is not None
    )


@pytest.mark.parametrize("connector,mode", list(_p_admitted()))
def test_the_refused_jobs_are_the_provider_and_pasted_prompt_ones(connector, mode):
    assert _P_REFUSED <= {(c.strip().lower(), m) for c, m in _p_admitted()}
    job = HarnessJob.model_validate({**_job(), "agent": _p_agent(connector, mode)})

    assert (agent_reading._check_job(job) is not None) is (
        (connector.strip().lower(), mode) in _P_REFUSED
    )


def test_a_connect_only_job_without_a_provider_is_read(tmp_path, model):
    _repo(tmp_path)
    fake = model(_writes(CHAT))

    assert _run(tmp_path, {**_job(), "agent": {"connector": "auto", "mode": "connect_only"}}) == 0

    assert (tmp_path / "reading" / "agent.json").exists()
    assert len(fake.created) == 1


def test_a_cancel_before_the_save_is_a_crash_with_its_file(tmp_path, model):
    _repo(tmp_path)

    async def cancelled(live):
        raise asyncio.CancelledError()
        yield _done()

    model(cancelled)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_crashed", True)
    assert failure["message"].startswith("CancelledError")
    _assert_cost(output, 0.0, False)


def test_a_cancel_after_the_save_keeps_the_saved_contract(tmp_path, model):
    _repo(tmp_path)

    async def saves_then_cancelled(live):
        _save(live.backend.output, CHAT)
        raise asyncio.CancelledError()
        yield _done()

    model(saves_then_cancelled)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 0

    assert _names(output) == {"contract.json", "agent.json", "cost.json"}
    _assert_cost(output, 0.0, False)


def test_a_session_whose_close_hangs_still_ends_with_its_saved_contract(
    tmp_path, model, monkeypatch, caplog
):
    _repo(tmp_path)
    _short_limit(monkeypatch)

    async def saves_then_goes_quiet(live):
        _save(live.backend.output, CHAT)
        await asyncio.Event().wait()
        yield _done()

    fake = model(saves_then_goes_quiet)
    fake.close_hangs = True
    output = tmp_path / "reading"
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(_job()), encoding="utf-8")
    # A short hard limit, so a reading stuck on the close ends this test (exit 97), not CI's clock.
    mode = dataclasses.replace(
        agent_reading._read_agent(interrupt_wait=0.3), limit_margin_seconds=5.0
    )

    with caplog.at_level("WARNING", logger=agent_reading.__name__):
        assert run_reading_mode(mode, job_path=job_path, source=tmp_path / "source", output=output) == 0

    assert _names(output) == {"contract.json", "agent.json", "cost.json"}
    # Decided while the close still hung: it never got as far as recording itself.
    assert fake.stopped == []
    assert any("did not close" in record.getMessage() for record in caplog.records)


def test_a_cancel_during_the_stop_is_still_a_timeout(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    _short_limit(monkeypatch)
    fake = model(_quiet)
    fake.interrupt_raises = True
    fake.interrupt_error = asyncio.CancelledError
    output = tmp_path / "reading"

    assert _run(tmp_path, wait=0.3) == 78

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_timed_out", False)


def test_an_invalid_saved_contract_logs_no_values_from_it(tmp_path, model, caplog):
    _repo(tmp_path)
    marker = "CUSTOMER-MARKER-7f3a db_password=hunter2"
    model(_writes(text=json.dumps({**CHAT, "tools": marker})))
    output = tmp_path / "reading"

    with caplog.at_level("WARNING", logger=reading_result.__name__):
        assert _run(tmp_path) == 78

    assert _failure_in(output)["details"] == {"class": "ValidationError", "code": ""}
    [logged] = [record for record in caplog.records if record.name == reading_result.__name__]
    assert logged.exc_text is None
    assert "hunter2" not in caplog.text


def test_the_contract_checked_is_the_contract_used(tmp_path, model, monkeypatch):
    source = _repo(tmp_path)
    (source / "agents/graph").mkdir(parents=True)
    (source / "agents/graph/graph.py").write_text(
        "from langgraph.graph import StateGraph\nStateGraph(int)\n", encoding="utf-8"
    )
    checked = {**CHAT, "runtime": {"language": "python", "workdir": SUPPORT}}
    model(_writes(checked))
    output = tmp_path / "reading"
    real = reading_result.named_framework_folder

    def a_late_save_lands(*args, **kwargs):
        # The session saves again before the folder is chosen: another folder, over the cap.
        (output / "contract.json").write_text(
            json.dumps(
                {
                    **CHAT,
                    "runtime": {"language": "python", "workdir": "agents/graph"},
                    "real_use_cases": ["x" * (6 * MIB)],
                }
            ),
            encoding="utf-8",
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(reading_result, "named_framework_folder", a_late_save_lands)

    assert _run(tmp_path) == 0

    agent = json.loads((output / "agent.json").read_text(encoding="utf-8"))["agent"]
    # The checked contract's folder and framework, not the late one's.
    assert agent == build_authored_agent(
        AgentContract.model_validate(checked).model_dump(mode="json"),
        framework=detect_framework(source / SUPPORT, repo_root=source, package_dir=None),
    )
    assert detect_framework(source / "agents/graph", repo_root=source, package_dir=None) != (
        detect_framework(source / SUPPORT, repo_root=source, package_dir=None)
    )


def test_an_environment_backed_job_is_read(tmp_path):
    job = HarnessJob.model_validate(
        {**_job(), "agent": {"connector": "vapi", "mode": "environment_backed", "config": {}}}
    )

    assert agent_reading._check_job(job) is None


_CLOSE_CHILD = r"""
import asyncio, dataclasses, json, os, signal, sys, time
config = json.loads(sys.argv[1])
sys.path[:0] = config["sys_path"]
from pathlib import Path
from fi.alk.harness import agent_reading, reading_entry, session
from fi.alk.harness.backends import StageDone
from fi.alk.harness.reading_entry import run_reading_mode

reading_entry._SECRET_FILES = ()
root = Path(config["root"])
output = root / "reading"


class Live:
    def __init__(self, spec):
        self.spec = spec

    async def start(self):
        pass

    async def send(self, message):
        pass

    async def interrupt(self):
        if config["interrupt"] == "swallows":
            # Swallows the first cancel, then hangs on; only the loop's shutdown ends it.
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(3600)
        if config["interrupt"] == "sigint":
            # A Ctrl-C that lands while the stop is being delivered.
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.sleep(3600)
        return True

    async def stop(self):
        if config["close"] != "hangs":
            return
        # A close that swallows every cancel, the loop's own shutdown's included.
        while True:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

    async def replies(self):
        if config["contract"] is not None:
            (output / "contract.json").write_text(json.dumps(config["contract"]))
        if config.get("after_save") == "sigint":
            # A Ctrl-C just after the save, before the reading has decided anything.
            os.kill(os.getpid(), signal.SIGINT)
        await asyncio.Event().wait()
        yield StageDone(outcome="success", turns=1, cost_usd=0.0)


class Backend:
    name = "vertex-gemini"
    default_model = "gemini-2.5-flash"

    def create(self, spec):
        return Live(spec)

    def can_drive(self, model):
        return True


session.resolve = lambda *a, **k: Backend()
mode = dataclasses.replace(
    agent_reading._read_agent(interrupt_wait=0.2), limit_margin_seconds=config["margin"]
)
(root / "started").write_text(repr(time.time()))
sys.exit(run_reading_mode(mode, job_path=root / "job.json", source=root / "source", output=output))
"""


def _read_in_a_child(root: Path, *, soft: float, margin: float, **behaviour):
    _repo(root)
    (root / "job.json").write_text(json.dumps(_job()), encoding="utf-8")
    config = {
        "sys_path": [entry for entry in sys.path if entry],
        "root": str(root),
        "contract": CHAT,
        "margin": margin,
        "interrupt": "honours",
        "close": "ends",
        **behaviour,
    }
    env = {
        **os.environ,
        _LIMIT_ENV: str(soft),
        "ALK_HARNESS_MODEL": "gemini-2.5-flash",
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        "OTEL_TRACES_EXPORTER": "none",
    }
    env.pop(spend.JOURNAL_ALIAS, None)
    finished = subprocess.run(
        [sys.executable, "-c", _CLOSE_CHILD, json.dumps(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return finished, float((root / "started").read_text(encoding="utf-8"))


def test_a_close_that_never_ends_still_leaves_the_document_well_inside_the_hard_limit(tmp_path):
    soft, margin = 0.5, 6.0

    finished, started = _read_in_a_child(tmp_path, soft=soft, margin=margin, close="hangs")

    output = tmp_path / "reading"
    assert finished.returncode == 0, finished.stderr
    assert not (output / "failure.json").exists()
    # Decided at soft + interrupt + wait + close (about 0.9 s), long before the hard limit
    # (soft + margin); only the shutdown the close blocks is ended by the limit, with the code.
    documented = (output / "agent.json").stat().st_mtime - started
    assert documented < soft + margin - 3.0
    assert _cost_in(output)["complete"] is False


def test_an_interrupt_that_swallows_its_cancel_does_not_hold_the_reading(tmp_path):
    soft, margin = 0.5, 6.0

    finished, started = _read_in_a_child(tmp_path, soft=soft, margin=margin, interrupt="swallows")

    output = tmp_path / "reading"
    assert finished.returncode == 0, finished.stderr
    assert not (output / "failure.json").exists()
    assert (output / "agent.json").stat().st_mtime - started < soft + margin - 3.0


def test_a_ctrl_c_during_the_stop_passes_through(tmp_path):
    finished, _ = _read_in_a_child(
        tmp_path, soft=0.5, margin=6.0, interrupt="sigint", contract=None
    )

    output = tmp_path / "reading"
    assert finished.returncode in (-signal.SIGINT, 130), finished.stderr
    assert "KeyboardInterrupt" in finished.stderr
    # Not a timeout: an interrupt ends the reading as an interrupt, before any save.
    assert not (output / "failure.json").exists()
    assert _cost_in(output)["complete"] is False


def test_a_ctrl_c_after_the_save_passes_through_and_leaves_the_files(tmp_path):
    finished, _ = _read_in_a_child(tmp_path, soft=5.0, margin=6.0, after_save="sigint")

    output = tmp_path / "reading"
    assert finished.returncode in (-signal.SIGINT, 130), finished.stderr
    assert "KeyboardInterrupt" in finished.stderr
    # The saved contract is left as it is; nothing is written over it or after it.
    assert (output / "contract.json").exists()
    assert not (output / "failure.json").exists()
    assert not (output / "agent.json").exists()


@pytest.mark.skipif(sys.version_info < (3, 11), reason="exception groups arrived in 3.11")
def test_a_group_of_cancels_before_the_session_opens_is_a_crash_with_its_file(tmp_path, model):
    _repo(tmp_path)
    fake = model()
    group = getattr(builtins, "BaseExceptionGroup")
    # The shape an anyio task group gives when a child task is cancelled.
    fake.start_error = group("the session's tasks failed", [asyncio.CancelledError()])
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_crashed", True)
    assert failure["message"].startswith("BaseExceptionGroup")
    _assert_cost(output, 0.0, False)


def test_the_longest_stop_leaves_room_for_the_document_inside_the_margin():
    # The stop's own bounds, exactly: the interrupt, the wait after it, and the close.
    bounds = (
        agent_reading._INTERRUPT_SECONDS,
        agent_reading._INTERRUPT_WAIT_SECONDS,
        agent_reading._CLOSE_SECONDS,
    )
    assert bounds == (10.0, 60.0, 20.0)
    assert sum(bounds) == 90.0
    # And the reading the platform runs waits after the interrupt for exactly that bound.
    assert agent_reading.READ_AGENT.work.func is agent_reading._read
    assert agent_reading.READ_AGENT.work.keywords == {
        "interrupt_wait": agent_reading._INTERRUPT_WAIT_SECONDS
    }
    # Then the saved contract's document, whose framework scan can take over a minute on a
    # crafted repository: at least 150 s of the margin stays for it.
    assert sum(bounds) + 150.0 < agent_reading._HARD_LIMIT_MARGIN_SECONDS
    assert agent_reading.READ_AGENT.limit_margin_seconds == agent_reading._HARD_LIMIT_MARGIN_SECONDS


def test_a_url_the_redaction_cannot_parse_leaves_no_password_anywhere(
    tmp_path, model, capfd, caplog
):
    _repo(tmp_path)
    leaky = "https://admin:hunter2\uff03x@db.example/"
    model(_writes({**CHAT, "real_use_cases": [leaky]}))
    output = tmp_path / "reading"

    with caplog.at_level("DEBUG"):
        assert _run(tmp_path) == 0

    kept = (output / "agent.json").read_text(encoding="utf-8")
    captured = capfd.readouterr()
    assert not (output / "failure.json").exists()
    assert "hunter2" not in kept + captured.out + captured.err + caplog.text


def test_a_failed_document_is_logged_with_its_masked_trace(tmp_path, model, monkeypatch, caplog):
    secret = "sk-document-0123456789"
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"KEY": secret}), encoding="utf-8")
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (secrets,))

    def breaks(*_args, **_kwargs):
        raise RuntimeError(f"the document builder broke on {secret}")

    monkeypatch.setattr(scenario_source, "build_authored_agent", breaks)
    _repo(tmp_path)
    model(_writes(CHAT))

    with caplog.at_level("WARNING", logger=reading_result.__name__):
        assert _run(tmp_path) == 78

    [logged] = [record for record in caplog.records if record.name == reading_result.__name__]
    assert "RuntimeError: the document builder broke on ***" in logged.exc_text
    assert secret not in caplog.text


def test_a_folder_mismatch_is_an_invalid_document(tmp_path, model):
    _repo(tmp_path)
    # Saved with no folder recorded, for a job that names one.
    model(_writes(CHAT, cost_usd=0.11))
    output = tmp_path / "reading"

    assert _run(tmp_path, _job(subfolder=SUPPORT)) == 78

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_document_invalid", False)
    # The build's own code for it, from the build's own document code.
    assert failure["details"] == {
        "class": "AgentBuildError",
        "code": "agent_invalid",
        "detail": "subfolder_mismatch",
    }
    assert (output / "contract.json").exists()
    assert not (output / "agent.json").exists()
    _assert_cost(output, 0.11, True)


def _stored_size(inner: dict) -> int:
    return len(json.dumps(inner, ensure_ascii=False).encode("utf-8"))


def _body_size(inner: dict) -> int:
    return len(
        json.dumps(
            {"sdk_version": fi.alk.__version__, "agent": inner},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )


def _sized(target: int, measure, items: int) -> dict:
    """An inner document that ``measure`` puts at exactly ``target`` bytes."""

    def inner(pad: int) -> dict:
        return {"items": [0] * items, "pad": "a" * pad}

    return inner(target - measure(inner(0)))


@pytest.mark.parametrize(
    ("inner", "passes"),
    [
        # Many separators: over by the platform's stored measure, under by the file's.
        (lambda: _sized(MIB + 1, _stored_size, 300_000), False),
        (lambda: _sized(MIB, _stored_size, 300_000), True),
        (lambda: _sized(MIB + 1, _body_size, 0), False),
    ],
    ids=["stored_over", "stored_exactly", "file_over"],
)
def test_an_oversized_document_is_refused(tmp_path, model, monkeypatch, inner, passes):
    _repo(tmp_path)
    document = inner()
    if not passes and document["items"]:
        assert _body_size(document) <= MIB
    monkeypatch.setattr(scenario_source, "build_authored_agent", lambda *a, **k: document)
    model(_writes(CHAT, cost_usd=0.05))
    output = tmp_path / "reading"

    code = _run(tmp_path)

    if passes:
        assert code == 0
        assert (output / "agent.json").stat().st_size == _body_size(document)
    else:
        assert code == 78
        failure = _failure_in(output)
        assert (failure["code"], failure["retryable"]) == ("agent_reading_too_large", False)
        assert not (output / "agent.json").exists()
        assert (output / "contract.json").exists()
    _assert_cost(output, 0.05, True)


@pytest.mark.parametrize(("size", "passes"), [(5 * MIB + 1, False), (5 * MIB, True)])
def test_an_oversized_contract_is_refused(tmp_path, model, size, passes):
    _repo(tmp_path)
    text = json.dumps(CHAT)
    # Trailing spaces still parse, so only the file's size changes.
    model(_writes(text=text + " " * (size - len(text.encode("utf-8"))), cost_usd=0.05))
    output = tmp_path / "reading"

    code = _run(tmp_path)

    assert (output / "contract.json").stat().st_size == size
    if passes:
        assert code == 0
        assert (output / "agent.json").exists()
    else:
        assert code == 78
        assert _failure_in(output)["code"] == "agent_reading_too_large"
        assert not (output / "agent.json").exists()
    _assert_cost(output, 0.05, True)


# -- R10 to R19: how a turn's ending is read ----------------------------------------------------


def test_no_contract_after_the_reminder_is_incomplete(tmp_path, model):
    _repo(tmp_path)
    # Each turn's own figures, as the Claude backend reports them.
    fake = model(_ends(cost_usd=0.07875), _ends(cost_usd=0.07875))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    assert len(fake.sent) == 2
    assert fake.sent[1] == SUBMIT_REMINDER
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_incomplete", False)
    assert failure["details"] == {"outcome": "success", "turns": 2}
    _assert_cost(output, 0.1575, True)


# Gemini's call limit arrives as a raise, so its own test below drives the real backend; a
# stream that ends with no final answer is the outcome here. One turn, well under the budget,
# so only the outcome can mark it.
_TURN_LIMIT_SHAPES = [
    ("claude", {"outcome": "error_max_turns", "is_error": True}),
    ("vertex-gemini", {"outcome": "max_turns", "is_error": True, "turns": 1}),
]


@pytest.mark.parametrize(("name", "ending"), _TURN_LIMIT_SHAPES, ids=["claude", "gemini"])
def test_the_turn_limit_is_incomplete_not_a_model_error(tmp_path, model, name, ending):
    _repo(tmp_path)
    fake = model(_ends(cost_usd=0.2, **ending), name=name)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    assert len(fake.sent) == 1
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_incomplete", False)
    assert failure["details"]["outcome"] == ending["outcome"]
    _assert_cost(output, 0.2, True)


@pytest.mark.parametrize("status", [408, 409, 429, 500, 529])
def test_a_provider_error_is_retryable(tmp_path, model, status):
    _repo(tmp_path)
    model(_ends(cost_usd=0.3, is_error=True, api_error_status=status, errors=["overloaded"]))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_model_error", True)
    assert failure["details"] == {"status": status}
    assert "overloaded" in failure["message"]
    _assert_cost(output, 0.3, True)


@pytest.mark.parametrize(
    ("name", "ending"),
    [
        ("claude", {"outcome": "success", "is_error": True}),
        # A provider exception, well inside the call budget.
        ("vertex-gemini", {"outcome": "failed", "is_error": True, "turns": 1}),
    ],
    ids=["claude", "gemini"],
)
def test_a_failure_with_no_status_is_a_retryable_model_error(tmp_path, model, name, ending):
    _repo(tmp_path)
    fake = model(_ends(**ending), _ends(**ending), name=name)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    assert len(fake.sent) == 2
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_model_error", True)
    assert failure["details"] == {"status": None}
    # A request cut with no status may be billed without a total reaching the stage.
    assert _cost_in(output)["complete"] is False


@pytest.mark.parametrize("status", [400, 401, 404])
def test_a_refusing_4xx_is_not_retried(tmp_path, model, status):
    _repo(tmp_path)
    model(_ends(cost_usd=0.01, is_error=True, api_error_status=status))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_model_error", False)
    assert failure["details"] == {"status": status}


def test_a_gemini_grpc_code_is_retryable(tmp_path, model):
    _repo(tmp_path)
    model(
        _ends(outcome="failed", is_error=True, api_error_status=14), name="vertex-gemini"
    )
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_model_error", True)
    assert failure["details"] == {"status": 14}


def test_a_failed_opening_gets_no_reminder(tmp_path, model):
    _repo(tmp_path)
    fake = model(_ends(is_error=True, api_error_status=529))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    assert len(fake.sent) == 1
    assert _failure_in(output)["code"] == "agent_reading_model_error"


@pytest.mark.parametrize(("name", "ending"), _TURN_LIMIT_SHAPES, ids=["claude", "gemini"])
def test_a_turn_limit_opening_gets_no_reminder(tmp_path, model, name, ending):
    _repo(tmp_path)
    fake = model(_ends(**ending), name=name)

    _run(tmp_path)

    assert len(fake.sent) == 1


def test_an_error_flag_without_a_status_still_gets_the_reminder(tmp_path, model):
    _repo(tmp_path)
    fake = model(
        _ends(outcome="success", is_error=True, cost_usd=0.1),
        _writes(CHAT, cost_usd=0.1),
    )
    output = tmp_path / "reading"

    assert _run(tmp_path) == 0

    assert fake.sent[1:] == [SUBMIT_REMINDER]
    assert (output / "agent.json").exists()
    # The first turn's error had no status, so its spend may not all be in the total.
    _assert_cost(output, 0.2, False)


# Priced, so the stop's cost is checked against the table and not left at zero.
_PRICED_GEMINI = "gemini-3.7-flash"


def _real_gemini(monkeypatch, reply) -> tuple[list[str], list[str]]:
    """The real Gemini backend and ADK runner; only the model is an in-memory one.

    ``reply(who, calls)`` gives the reply to the agent named ``who``; ``calls`` names every
    agent that has called so far. Returns the messages sent and the calls made.
    """
    from google.adk.models.base_llm import BaseLlm

    from fi.alk.harness.backends import vertex_gemini

    calls: list[str] = []

    class Scripted(BaseLlm):
        async def generate_content_async(self, llm_request, stream=False):
            labels = (llm_request.config.labels or {}) if llm_request.config else {}
            calls.append(labels.get("adk_agent_name", ""))
            yield reply(calls[-1], calls)

    # The session writes these into the environment; set here first, they are put back after.
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("ALK_HARNESS_MODEL", _PRICED_GEMINI)
    monkeypatch.setattr(vertex_gemini, "_llm", lambda name: Scripted(model=name))
    backend = vertex_gemini.VertexGeminiBackend()
    monkeypatch.setattr(session, "resolve", lambda *a, **k: backend)
    sent: list[str] = []
    real_send = vertex_gemini.VertexGeminiSession.send

    async def send(self, message):
        sent.append(message)
        return await real_send(self, message)

    monkeypatch.setattr(vertex_gemini.VertexGeminiSession, "send", send)
    return sent, calls


def _gemini_usage():
    from google.genai import types

    return types.GenerateContentResponseUsageMetadata(
        prompt_token_count=1000, candidates_token_count=10
    )


def _gemini_calls(name: str, args: dict, n: int):
    from google.adk.models.llm_response import LlmResponse
    from google.genai import types

    call = types.FunctionCall(name=name, args=args, id=f"call-{n}")
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(function_call=call)]),
        usage_metadata=_gemini_usage(),
    )


def _gemini_priced(calls: int) -> float:
    # The fake's 1,000 tokens in and 10 out per call, at the table's own rates, kept to the
    # six places the spend record keeps.
    from fi.alk.harness.backends.vertex_gemini import current_rates

    rate_in, rate_out = current_rates(_PRICED_GEMINI)
    return round(calls * (1000 * rate_in + 10 * rate_out) / 1_000_000, 6)


def test_a_gemini_call_limit_is_incomplete_with_no_reminder(tmp_path, model, monkeypatch):
    sent, calls = _real_gemini(
        monkeypatch, lambda who, calls: _gemini_calls("Glob", {"pattern": "*.py"}, len(calls))
    )
    _few_turns(monkeypatch, 3)
    _repo(tmp_path)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    assert len(sent) == 1
    assert len(calls) == 3
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_incomplete", False)
    # The backend's own count reaches the budget when ADK stops the calls.
    assert failure["details"] == {"outcome": "failed", "turns": 3}
    # ADK stops before the call it refuses, so every call made is in the total.
    _assert_cost(output, _gemini_priced(3), True)


def test_a_gemini_call_limit_after_a_malformed_worker_reply_gets_no_reminder(
    tmp_path, model, monkeypatch
):
    from google.adk.models.llm_response import LlmResponse

    def reply(who, calls):
        if who != "agent_reader":
            return _gemini_calls("agent_reader", {"request": "Trace the add tool."}, len(calls))
        if calls.count("agent_reader") == 1:
            # Gemini's malformed call: no content, so the backend doesn't count it as a turn,
            # but ADK still spends a call on it.
            return LlmResponse(
                error_code="MALFORMED_FUNCTION_CALL",
                error_message="Malformed function call",
                usage_metadata=_gemini_usage(),
            )
        return _gemini_calls("Glob", {"pattern": "*.py"}, len(calls))

    sent, calls = _real_gemini(monkeypatch, reply)
    _few_turns(monkeypatch, 6)
    _repo(tmp_path)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    # One budget, not two: no reminder opened a second one.
    assert len(sent) == 1
    assert len(calls) == 6
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_incomplete", False)
    # The backend's count falls short of the budget; ADK's own words mark the stop.
    assert failure["details"]["turns"] < 6
    _assert_cost(output, _gemini_priced(6), True)


def test_an_earlier_failed_turn_is_still_a_model_error(tmp_path, model):
    _repo(tmp_path)
    fake = model(_ends(outcome="success", is_error=True), _ends())
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    assert len(fake.sent) == 2
    assert _failure_in(output)["code"] == "agent_reading_model_error"


def test_a_cut_on_the_last_allowed_call_is_not_a_turn_limit(tmp_path, model):
    _repo(tmp_path)
    fake = model(
        _ends(outcome="failed", is_error=True, turns=70, errors=["connection reset"]),
        _ends(),
        name="vertex-gemini",
    )
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    assert fake.sent[1:] == [SUBMIT_REMINDER]
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_model_error", True)
    # The cut request may be billed without its total reaching the stage.
    assert _cost_in(output)["complete"] is False


@pytest.mark.parametrize(
    "said",
    [
        "Tool 'Max number of llm calls limit of `6` exceeded' not found",
        "invalid literal for int() with base 10: 'Max number of llm calls limit of `6` exceeded'",
        "Max number of llm calls limit of `6` exceeded, said the model",
    ],
    ids=["tool-name", "offset", "trailing"],
)
def test_the_call_limit_phrase_inside_other_text_is_a_model_error(tmp_path, model, said):
    _repo(tmp_path)
    fake = model(
        _ends(outcome="failed", is_error=True, errors=[said]), _ends(), name="vertex-gemini"
    )
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    assert len(fake.sent) == 2
    assert _failure_in(output)["code"] == "agent_reading_model_error"


@pytest.mark.parametrize(
    ("name", "cut", "limit"),
    [
        ("claude", {"outcome": "success", "is_error": True}, _TURN_LIMIT_SHAPES[0][1]),
        (
            "vertex-gemini",
            {"outcome": "failed", "is_error": True},
            _TURN_LIMIT_SHAPES[1][1],
        ),
        (
            "vertex-gemini",
            {"outcome": "failed", "is_error": True},
            {
                "outcome": "failed",
                "is_error": True,
                "errors": ["Max number of llm calls limit of `200` exceeded"],
            },
        ),
    ],
    ids=["claude", "gemini", "gemini-call-limit"],
)
def test_a_turn_limit_on_the_reminder_wins_over_an_earlier_cut(
    tmp_path, model, name, cut, limit
):
    _repo(tmp_path)
    fake = model(_ends(**cut), _ends(**limit), name=name)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    assert len(fake.sent) == 2
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_incomplete", False)
    # The opening's cut may still be billed without its total reaching the stage.
    assert _cost_in(output)["complete"] is False


def test_a_cut_on_the_reminder_leaves_the_cost_incomplete(tmp_path, model):
    _repo(tmp_path)
    fake = model(_ends(cost_usd=0.07875), _ends(outcome="success", is_error=True))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    assert len(fake.sent) == 2
    assert _failure_in(output)["code"] == "agent_reading_model_error"
    assert _cost_in(output)["complete"] is False


# -- R20 to R27: a quiet stream, the soft limit, and what was saved ------------------------------


def test_an_idle_stream_is_not_replayed(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setattr(session, "STAGE_IDLE_RETRIES", 1)
    _short_idle(monkeypatch)
    fake = model(_quiet)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    assert len(fake.created) == 1
    assert fake.interrupts == []
    # Only this stage skips the replay; the process's own setting is left as it was.
    assert session.STAGE_IDLE_RETRIES == 1
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_model_error", True)
    _assert_cost(output, 0.0, False)


def test_a_contract_saved_before_an_idle_stream_is_kept(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    _short_idle(monkeypatch)

    async def saves_then_goes_quiet(live):
        _save(live.backend.output, CHAT)
        await asyncio.Event().wait()
        yield _done()

    model(saves_then_goes_quiet)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 0

    assert (output / "agent.json").exists()
    assert not (output / "failure.json").exists()
    _assert_cost(output, 0.0, False)


def test_the_soft_limit_interrupts_once_and_charges_what_arrives(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    _short_limit(monkeypatch)

    async def answers_the_interrupt(live):
        await live.interrupted.wait()
        yield _done(outcome="error_during_execution", is_error=True, cost_usd=0.42)

    fake = model(answers_the_interrupt)
    output = tmp_path / "reading"

    assert _run(tmp_path, wait=2.0) == 78

    assert len(fake.sent) == 1
    assert len(fake.interrupts) == 1
    assert _failure_in(output)["code"] == "agent_reading_timed_out"
    _assert_cost(output, 0.42, False)


def test_the_soft_limit_marks_cost_incomplete_when_nothing_arrives(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    _short_limit(monkeypatch)
    fake = model(_quiet)
    output = tmp_path / "reading"

    assert _run(tmp_path, wait=0.3) == 78

    assert len(fake.interrupts) == 1
    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_timed_out", False)
    _assert_cost(output, 0.0, False)


def test_an_interrupt_that_raises_is_still_a_timeout(tmp_path, model, monkeypatch, caplog):
    _repo(tmp_path)
    _short_limit(monkeypatch)
    fake = model(_quiet)
    fake.interrupt_raises = True
    output = tmp_path / "reading"

    with caplog.at_level("WARNING", logger=agent_reading.__name__):
        assert _run(tmp_path, wait=0.3) == 78

    assert _failure_in(output)["code"] == "agent_reading_timed_out"
    [logged] = [
        record
        for record in caplog.records
        if record.name == agent_reading.__name__ and "not delivered" in record.getMessage()
    ]
    assert "RuntimeError: the interrupt control request timed out" in logged.exc_text


def test_the_readings_own_log_is_masked(tmp_path, model, monkeypatch, caplog):
    secret = "sk-provider-0123456789"
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"TARGET_KEY": secret}), encoding="utf-8")
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (secrets,))
    _repo(tmp_path)
    _short_limit(monkeypatch)
    fake = model(_quiet)
    fake.interrupt_raises = True
    fake.interrupt_says = f"the provider refused key {secret}"

    with caplog.at_level("WARNING", logger=agent_reading.__name__):
        assert _run(tmp_path, wait=0.3) == 78

    [logged] = [
        record
        for record in caplog.records
        if record.name == agent_reading.__name__ and "not delivered" in record.getMessage()
    ]
    assert "RuntimeError: the provider refused key ***" in logged.exc_text
    assert secret not in caplog.text


def test_a_session_that_breaks_after_the_stop_is_logged_and_still_a_timeout(
    tmp_path, model, monkeypatch, caplog
):
    _repo(tmp_path)
    _short_limit(monkeypatch)

    async def breaks_when_interrupted(live):
        await live.interrupted.wait()
        raise ConnectionResetError("the stream broke after the stop")
        yield _done()

    model(breaks_when_interrupted)
    output = tmp_path / "reading"

    with caplog.at_level("WARNING", logger=agent_reading.__name__):
        assert _run(tmp_path, wait=1.0) == 78

    assert _failure_in(output)["code"] == "agent_reading_timed_out"
    [logged] = [
        record
        for record in caplog.records
        if record.name == agent_reading.__name__ and "ended with an error" in record.getMessage()
    ]
    assert "ConnectionResetError: the stream broke after the stop" in logged.exc_text


def test_a_contract_saved_at_the_soft_limit_is_kept(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    _short_limit(monkeypatch)

    async def saves_when_interrupted(live):
        await live.interrupted.wait()
        _save(live.backend.output, CHAT)
        yield _done(outcome="error_during_execution", is_error=True, cost_usd=0.3)

    model(saves_when_interrupted)
    output = tmp_path / "reading"

    assert _run(tmp_path, wait=2.0) == 0

    assert (output / "agent.json").exists()
    assert not (output / "failure.json").exists()
    _assert_cost(output, 0.3, False)


def test_nothing_is_saved_after_the_soft_limit_decides(tmp_path, model, monkeypatch):
    """A cancel probe: the turn is cancelled and its session closed before the files are read.

    The fake turn waits for the reading's decision itself, not for a time, so it fails only if
    the reading reads its files before the AI's turn is cancelled and its session is closed. On
    correct code the fake never reaches the gate; the ``stopped`` record is the assertion that
    bites.
    """
    _repo(tmp_path)
    _short_limit(monkeypatch, soft=0.3)
    decided = asyncio.Event()
    real_timed_out = agent_reading._timed_out

    def timed_out(output, soft_limit):
        decided.set()
        return real_timed_out(output, soft_limit)

    monkeypatch.setattr(agent_reading, "_timed_out", timed_out)

    async def saves_too_late(live):
        # Ignores the interrupt, and would save only once the reading has decided.
        await decided.wait()
        await _gate(live, CHAT)
        yield _done()

    fake = model(saves_too_late)
    output = tmp_path / "reading"

    assert _run(tmp_path, wait=0.3) == 78

    # The session was closed before either result file could be written.
    assert fake.stopped == [{"failure": False, "agent": False}]
    assert not (output / "contract.json").exists()
    assert _failure_in(output)["code"] == "agent_reading_timed_out"
    # Documents the probe: the turn never reached the gate. Not the assertion that bites.
    assert fake.submissions == 0


# -- a tool call still running when the reading decides ------------------------------------------

CHAT2 = {**CHAT, "agent": "billing", "real_use_cases": ["refund"]}


def _replace_the_contract(output: Path) -> None:
    _save(output, CHAT2)


def _refuse_the_agent(output: Path) -> None:
    (output / "contract.json").unlink(missing_ok=True)
    write_failure_file(
        HarnessFailure(
            domain=FailureDomain.AGENT,
            stage=HarnessStage.UNDERSTANDING_AGENT,
            code="agent_type_unsupported",
            message="late refusal",
            retryable=False,
        ),
        output,
    )


@pytest.mark.parametrize("path", ["finished", "soft_limit", "close_hangs"])
@pytest.mark.parametrize(
    "late_write", [_replace_the_contract, _refuse_the_agent], ids=["replace", "refuse"]
)
def test_a_late_save_after_the_decision_is_cancelled(
    tmp_path, model, monkeypatch, late_write, path
):
    """A tool call the session's close did not stop, run as the SDKs run one: a task of its own.

    Its last wait ends while the reading writes agent.json; what it does then is ``late_write``,
    a save's own last, synchronous step. The reading cancels it before its next turn.
    """
    _repo(tmp_path)
    soft = path != "finished"
    if soft:
        _short_limit(monkeypatch, soft=0.3)
    output = tmp_path / "reading"
    release = asyncio.Event()
    record: dict = {}
    real_document = agent_reading.document

    def document(*args, **kwargs):
        code = real_document(*args, **kwargs)
        release.set()
        return code

    monkeypatch.setattr(agent_reading, "document", document)
    # classify() calls the document through its own module.
    monkeypatch.setattr(reading_result, "document", document)

    async def late():
        try:
            await release.wait()
        except asyncio.CancelledError:
            record["cancelled"] = True
            raise
        late_write(output)
        record["wrote"] = True

    async def turn(live):
        _save(live.backend.output, CHAT)
        record["task"] = asyncio.get_running_loop().create_task(late())
        if soft:
            await asyncio.Event().wait()
        yield _done()

    fake = model(turn)
    fake.close_hangs = path == "close_hangs"

    assert _run(tmp_path, wait=0.3) == 0

    assert record.get("cancelled") is True
    assert "wrote" not in record
    assert json.loads((output / "contract.json").read_text(encoding="utf-8"))["agent"] == "support"
    assert (output / "agent.json").exists()
    assert not (output / "failure.json").exists()


def test_a_late_save_after_a_cancel_that_escapes_the_turn_is_cancelled(
    tmp_path, model, monkeypatch
):
    """The ending where a cancel escapes the turn after the contract was saved (`_read`'s except
    branch). The guard in `finally` covers it too."""
    _repo(tmp_path)
    output = tmp_path / "reading"
    release = asyncio.Event()
    record: dict = {}
    real_document = agent_reading.document

    def document(*args, **kwargs):
        code = real_document(*args, **kwargs)
        release.set()
        return code

    monkeypatch.setattr(agent_reading, "document", document)

    async def late():
        try:
            await release.wait()
        except asyncio.CancelledError:
            record["cancelled"] = True
            raise
        _save(output, CHAT2)
        record["wrote"] = True

    async def turn(live):
        _save(live.backend.output, CHAT)
        record["task"] = asyncio.get_running_loop().create_task(late())
        raise asyncio.CancelledError()
        yield _done()

    model(turn)
    assert _run(tmp_path) == 0
    assert record.get("cancelled") is True
    assert "wrote" not in record
    assert json.loads((output / "contract.json").read_text(encoding="utf-8"))["agent"] == "support"


def test_a_late_save_woken_in_the_deciding_turn_is_cancelled(tmp_path, model):
    """No hook in the document: the late call's wake-up is queued in the same loop turn as the
    reading's own, behind it, by a done callback on the conversation's task."""
    _repo(tmp_path)
    output = tmp_path / "reading"
    record: dict = {}

    async def late(gate):
        try:
            await gate
        except asyncio.CancelledError:
            record["cancelled"] = True
            raise
        _save(output, CHAT2)
        record["wrote"] = True

    async def turn(live):
        _save(live.backend.output, CHAT)
        loop = asyncio.get_running_loop()
        gate = loop.create_future()
        asyncio.current_task().add_done_callback(lambda _t: gate.done() or gate.set_result(None))
        record["task"] = loop.create_task(late(gate))
        yield _done()

    model(turn)

    assert _run(tmp_path, wait=0.3) == 0

    assert "wrote" not in record
    assert json.loads((output / "contract.json").read_text(encoding="utf-8"))["agent"] == "support"


def _reading_spec(tmp_path: Path, job: dict):
    """The reading's own stage spec, opened as `_reading` opens it, and its output folder."""
    source = _repo(tmp_path)
    output = tmp_path / "reading"
    output.mkdir()
    harness_job = HarnessJob.model_validate(job)
    opened = agent_reading.open_stage(
        agent_reading.resolve_source(
            "repo", name="agent", root=source, subfolder=harness_job.source.subfolder
        ),
        out=output,
        **reading_settings(harness_job),
    )[0]
    return opened.spec, output


def _submit_contract(spec):
    return next(t for t in spec.servers[CONTRACT_SERVER].tools if t.name == "submit_contract")


# Saved on its first call: no tools to nudge for, and one hard constraint.
SCAN_PAYLOAD = {k: v for k, v in CHAT.items() if k != "tools"} | {
    "hard_constraints": ["Never share another customer's order."]
}


@pytest.mark.parametrize("cancelled", [True, False], ids=["cancelled", "uncancelled"])
@pytest.mark.parametrize("where", ["scan", "checks", "scan-browser"])
def test_a_cancelled_contract_save_writes_nothing(tmp_path, monkeypatch, where, cancelled):
    """The real save, cancelled at each of its waits, writes nothing.

    The refusal path has nothing more to cancel: a refusal needs a second look, which is given
    only after the scan, and the refusing call reaches its write with no wait after the scan.
    So the scan is its only wait (``scan-browser``).
    """
    subfolder = SCRAPER if where == "scan-browser" else SUPPORT
    spec, output = _reading_spec(tmp_path, _job(subfolder=subfolder))
    tool = _submit_contract(spec)
    entered, release = threading.Event(), threading.Event()
    held = "_checked_contract" if where == "checks" else "_tools_in_agent_folder"
    real = getattr(tools, held)

    def holding(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(tools, held, holding)
    payload = {
        "scan": SCAN_PAYLOAD,
        "checks": CHAT,
        "scan-browser": {**SCAN_PAYLOAD, "modality": "browser"},
    }[where]

    async def run():
        if where == "checks":
            # As the gate sends it: the first, thin call is answered; the second reaches the checks.
            first = await tool.handler(dict(CHAT))
            assert not entered.is_set(), first
        task = asyncio.ensure_future(tool.handler(dict(payload)))
        assert await asyncio.to_thread(entered.wait, 10)
        if cancelled:
            task.cancel()
        # Before the next wait: a save that swallowed the cancel would hold the loop on it.
        release.set()
        (answer,) = await asyncio.gather(task, return_exceptions=True)
        for _ in range(5):
            await asyncio.sleep(0)
        return task, answer

    task, answer = asyncio.run(run())

    if cancelled:
        assert task.cancelled()
        assert not (output / "contract.json").exists()
        assert not (output / "failure.json").exists()
    elif where == "scan-browser":
        assert "second-look" in json.dumps(answer)
        assert not (output / "contract.json").exists()
        assert not (output / "failure.json").exists()
    else:
        assert (output / "contract.json").exists(), answer


def test_the_readings_gemini_tools_run_on_its_loop(tmp_path, monkeypatch):
    """The model calls the real submit_contract; it runs on the reading's own thread and loop.

    So the reading's last cancel reaches it. ADK's tool_thread_pool_config would move it to a
    thread's own loop, but only in live mode in the locked 2.7.1, which a reading never uses.
    """
    from google.adk.models.google_llm import Gemini
    from google.adk.models.llm_response import LlmResponse
    from google.adk.runners import Runner
    from google.genai import types

    from fi.alk.harness.backends.vertex_gemini import VertexGeminiSession

    calls = []

    async def generate(self, llm_request, stream=False):
        calls.append(llm_request)
        if len(calls) == 1:
            assert llm_request.contents[-1].parts[0].text == "Hello"
            call = types.FunctionCall(
                name=qualified(CONTRACT_SERVER, "submit_contract"), args=dict(SCAN_PAYLOAD)
            )
            yield LlmResponse(
                content=types.Content(role="model", parts=[types.Part(function_call=call)])
            )
        else:
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))

    monkeypatch.setattr(Gemini, "generate_content_async", generate)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "global")
    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
    run_configs = []
    real_run_async = Runner.run_async

    # A plain def: the session wraps what it returns in aclosing().
    def run_async(self, *args, **kwargs):
        run_configs.append(kwargs.get("run_config"))
        return real_run_async(self, *args, **kwargs)

    monkeypatch.setattr(Runner, "run_async", run_async)
    where = []
    real_unwrapped = tools.unwrapped

    # The save's first step; later calls come from its checks, on a worker thread.
    def unwrapped(payload):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        where.append((threading.get_ident(), loop))
        return real_unwrapped(payload)

    monkeypatch.setattr(tools, "unwrapped", unwrapped)
    spec, _ = _reading_spec(tmp_path, _job())

    async def run():
        live = VertexGeminiSession(spec, model="gemini-2.5-flash")
        await live.start()
        try:
            await live.send("Hello")
            [event async for event in live.replies()]
        finally:
            await live.stop()
        return threading.get_ident(), asyncio.get_running_loop()

    ident, loop = asyncio.run(run())

    assert where, "submit_contract was never called"
    assert where[0] == (ident, loop)
    # A second tripwire, for a later ADK that reads it outside live mode.
    assert run_configs and run_configs[0] is not None
    assert run_configs[0].tool_thread_pool_config is None


def test_a_saved_contract_wins_over_an_error_flag(tmp_path, model):
    _repo(tmp_path)
    model(_writes(CHAT, is_error=True, cost_usd=0.1))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 0

    assert (output / "agent.json").exists()
    assert not (output / "failure.json").exists()


def test_a_turn_with_no_ending_leaves_the_cost_incomplete(tmp_path, model):
    _repo(tmp_path)

    async def saves_and_never_reports(live):
        _save(live.backend.output, CHAT)
        for nothing in ():
            yield nothing

    fake = model(saves_and_never_reports)
    output = tmp_path / "reading"

    assert _run(tmp_path) == 0

    assert len(fake.sent) == 1
    assert (output / "agent.json").exists()
    _assert_cost(output, 0.0, False)


# -- R28 to R32: what is charged ----------------------------------------------------------------


def test_the_cost_follows_each_turn(tmp_path, model):
    _repo(tmp_path)

    async def reads_the_cost(live):
        cost = _cost_in(live.backend.output)
        live.backend.seen.append((cost["total_usd"], cost["complete"]))
        yield _done(cost_usd=0.1575)

    fake = model(_ends(cost_usd=0.07875), reads_the_cost)

    _run(tmp_path)

    assert fake.seen == [(pytest.approx(0.07875), False)]


def _stage_entry() -> dict:
    [entry] = spend.snapshot()["stages"]
    return entry


def test_claude_turns_are_added_as_the_backend_reports_them(tmp_path, model):
    # The Claude backend reports each turn's own figures (it takes the difference of the CLI's
    # running totals itself), so the reading adds them as reported and never subtracts again.
    _repo(tmp_path)
    model(
        _ends(cost_usd=0.07875, tokens_in=100_000),
        _ends(cost_usd=0.07875, tokens_in=100_000),
    )
    output = tmp_path / "reading"

    _run(tmp_path)

    _assert_cost(output, 0.1575, True)
    assert _stage_entry()["tokens_in"] == 200_000


def test_gemini_counts_are_added_as_reported(tmp_path, model):
    _repo(tmp_path)
    model(
        _ends(cost_usd=0.1, tokens_in=1_000),
        _ends(cost_usd=0.1, tokens_in=1_000),
        name="vertex-gemini",
    )
    output = tmp_path / "reading"

    _run(tmp_path)

    _assert_cost(output, 0.2, True)
    assert _stage_entry()["tokens_in"] == 2_000


class _MessagesStub(BaseHTTPRequestHandler):
    """The Messages API on 127.0.0.1, answering every request with fixed usage."""

    seen: list[str] = []

    def log_message(self, *args) -> None:
        pass

    def _json(self, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_HEAD(self) -> None:
        self.send_response(200)
        self.end_headers()

    def do_GET(self) -> None:
        self._json({"data": [], "has_more": False})

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("content-length") or 0))
        try:
            request = json.loads(raw)
        except ValueError:
            request = {}
        self.seen.append(self.path)
        if "count_tokens" in self.path:
            self._json({"input_tokens": 10})
            return
        model_name = request.get("model", "claude-sonnet-4-5")
        if not request.get("stream"):
            self._json(
                {
                    "id": "msg_x",
                    "type": "message",
                    "role": "assistant",
                    "model": model_name,
                    "content": [{"type": "text", "text": "ok"}],
                    "stop_reason": "end_turn",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 1},
                }
            )
            return
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.end_headers()

        def event(name: str, data: dict) -> None:
            self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
            self.wfile.flush()

        event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_1",
                    "type": "message",
                    "role": "assistant",
                    "model": model_name,
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {
                        "input_tokens": 100_000,
                        "output_tokens": 1,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    },
                },
            },
        )
        event(
            "content_block_start",
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        )
        event(
            "content_block_delta",
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "done"}},
        )
        event("content_block_stop", {"type": "content_block_stop", "index": 0})
        event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 1000},
            },
        )
        event("message_stop", {"type": "message_stop"})


def test_the_claude_cli_reports_running_totals(tmp_path):
    import claude_agent_sdk
    from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage

    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if not cli.is_file():
        pytest.fail(f"the Claude CLI bundled with claude-agent-sdk was not found at {cli}")
    _MessagesStub.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MessagesStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    nowhere = "http://127.0.0.1:9"
    env = {
        "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_address[1]}",
        "ANTHROPIC_API_KEY": "placeholder-not-a-key",
        "ANTHROPIC_AUTH_TOKEN": "",
        # Another test in the same process may leave these set, and the CLI would go there.
        "CLAUDE_CODE_USE_VERTEX": "",
        "CLAUDE_CODE_USE_BEDROCK": "",
        "CLAUDE_CODE_USE_FOUNDRY": "",
        "ANTHROPIC_VERTEX_PROJECT_ID": "",
        "ANTHROPIC_VERTEX_BASE_URL": nowhere,
        "ANTHROPIC_BEDROCK_BASE_URL": nowhere,
        "ANTHROPIC_FOUNDRY_BASE_URL": nowhere,
        "OTEL_EXPORTER_OTLP_ENDPOINT": nowhere,
        "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CONFIG_DIR": str(tmp_path / "config"),
    }
    work = tmp_path / "work"
    work.mkdir()
    options = ClaudeAgentOptions(
        model="claude-sonnet-4-5",
        max_turns=5,
        env=env,
        cwd=str(work),
        allowed_tools=[],
        setting_sources=[],
        cli_path=str(cli),
    )
    results: list = []

    async def two_queries() -> None:
        async with ClaudeSDKClient(options=options) as client:
            for prompt in ("first", "second, as the reminder would be"):
                await client.query(prompt)
                async for message in client.receive_response():
                    if isinstance(message, ResultMessage):
                        results.append(message)

    try:
        asyncio.run(asyncio.wait_for(two_queries(), 120))
    finally:
        server.shutdown()
        server.server_close()

    asked = [path for path in _MessagesStub.seen if "count_tokens" not in path]
    assert len(asked) == 2, asked
    first, second = results
    assert (first.is_error, second.is_error) == (False, False)
    assert first.total_cost_usd > 0
    assert second.total_cost_usd == pytest.approx(2 * first.total_cost_usd)

    def input_tokens(result) -> int:
        return sum(
            int((usage or {}).get("inputTokens", 0))
            for usage in (result.model_usage or {}).values()
        )

    assert [input_tokens(first), input_tokens(second)] == [100_000, 200_000]


def test_a_forbidden_model_is_not_retried(tmp_path, model):
    _repo(tmp_path)
    model(_ends(cost_usd=0.2, models={"claude-sonnet-4-5"}))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_model_error", False)
    # Spent before the stop, and that turn never finished being kept.
    _assert_cost(output, 0.2, False)


# -- R33 to R36: the stage the reading runs -----------------------------------------------------


_EVALS = [{"name": "tone_check", "description": "Checks the tone.", "modality": "chat"}]


class _Opened(Exception):
    pass


@pytest.mark.parametrize(
    ("metadata", "interactive", "expected"),
    [
        (
            {"generic_harness_v1": True, "available_evals": _EVALS},
            False,
            {"available_evals": _EVALS, "require_setup_contract": True, "max_turns": 140},
        ),
        (
            {"available_evals": _EVALS},
            False,
            {"available_evals": _EVALS, "require_setup_contract": False, "max_turns": 70},
        ),
        (
            {"generic_harness_v1": True},
            True,
            {"available_evals": None, "require_setup_contract": False, "max_turns": 70},
        ),
        (
            # Only the flag itself, not a value that merely reads as true.
            {"generic_harness_v1": "true"},
            False,
            {"available_evals": None, "require_setup_contract": False, "max_turns": 70},
        ),
    ],
    ids=["generic", "not_generic", "generic_with_an_operator", "flag_as_text"],
)
def test_the_build_opens_its_reading_as_it_always_has(
    tmp_path, monkeypatch, metadata, interactive, expected
):
    from fi.alk.harness import cli

    job = HarnessJob.model_validate(_job(metadata=metadata))
    assert reading_settings(job, interactive=interactive) == expected
    seen: dict = {}

    def opened(_source, **kwargs):
        seen.update(kwargs)
        raise _Opened

    monkeypatch.setattr(cli, "open_stage", opened)
    args = argparse.Namespace(
        job=job,
        kind="repo",
        name="agent",
        path=str(_repo(tmp_path)),
        out=str(tmp_path / "out"),
        interactive=interactive,
    )

    with pytest.raises(_Opened):
        asyncio.run(cli._understand(args))

    assert {key: seen[key] for key in expected} == expected


@pytest.mark.parametrize(("generic", "turns"), [(False, 70), (True, 140)])
def test_the_reading_opens_as_the_build_does(tmp_path, model, generic, turns):
    _repo(tmp_path)
    fake = model()
    metadata = {"available_evals": _EVALS, **({"generic_harness_v1": True} if generic else {})}

    _run(tmp_path, _job(metadata=metadata))

    [spec] = fake.created
    assert spec.max_turns == turns
    assert spec.model == chosen_model()
    assert spec.thinking is True
    assert "tone_check" in spec.system_prompt
    assert CONTRACT_SERVER in spec.servers


def test_the_reading_opens_with_the_builds_words_and_the_jobs_guidance(tmp_path, model):
    from fi.alk.harness import cli
    from fi.alk.harness.sources import resolve
    from fi.alk.harness.understand import opening

    source = _repo(tmp_path)
    fake = model()
    raw = _job(metadata={"generic_harness_v1": True})

    _run(tmp_path, raw)

    job = HarnessJob.model_validate(raw)
    built = opening(resolve("repo", name=source.name, root=source)) + cli._guidance(
        argparse.Namespace(guidance=job.authoring_guidance())
    )
    assert fake.sent[0] == built
    assert "standalone Dockerfile cannot be built" in fake.sent[0]


def test_a_generic_jobs_reading_refuses_what_the_builds_gate_refuses(tmp_path, model):
    # The build's gate for a generic job: a chat agent must declare where it keeps its state.
    refused = _checked_contract(
        dict(CHAT), source_root=None, require_setup_contract=True, subfolder=None
    )
    assert isinstance(refused, dict)
    assert "data_store" in refused["content"][0]["text"]
    _repo(tmp_path)
    fake = model(_submits(CHAT))
    output = tmp_path / "reading"

    assert _run(tmp_path, _job(metadata={"generic_harness_v1": True})) == 78

    assert fake.submissions == 3
    assert not (output / "contract.json").exists()
    assert _failure_in(output)["code"] == "agent_reading_incomplete"


def test_the_claude_backend_accepts_the_reading_spec(tmp_path, model, monkeypatch):
    from fi.alk.harness.backends.claude import ClaudeBackend

    monkeypatch.setenv("ALK_CLAUDE_GATEWAY_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("ALK_CLAUDE_GATEWAY_API_KEY", "placeholder-not-a-key")
    _repo(tmp_path)
    fake = model()
    _run(tmp_path)
    [spec] = fake.created

    options = ClaudeBackend().create(spec)._options
    try:
        assert options.max_turns == 70
        assert getattr(options, "thinking", None) is not None
        assert any(name.endswith("submit_contract") for name in options.allowed_tools)
    finally:
        prompt = options.system_prompt
        if isinstance(prompt, dict) and prompt.get("path"):
            Path(prompt["path"]).unlink(missing_ok=True)


def test_the_soft_limit_counts_from_the_readings_start(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    _short_limit(monkeypatch, soft=30.0)
    real = agent_reading.open_stage
    now = [1000.0]
    # The reading's own clock, so the time spent opening the stage is told, not slept.
    monkeypatch.setattr(agent_reading, "time", types.SimpleNamespace(monotonic=lambda: now[0]))

    def slow(*args, **kwargs):
        now[0] += 29.9
        return real(*args, **kwargs)

    monkeypatch.setattr(agent_reading, "open_stage", slow)
    fake = model(_quiet)
    started = time.monotonic()

    assert _run(tmp_path, wait=0.2) == 78

    # 0.1 s were left of the 30, not 30 again from the stage's opening.
    [interrupted] = fake.interrupts
    assert interrupted - started < 10.0


# -- R37 to R41: the switch, the folders, the limit and the last write --------------------------


def _never(*_args, **_kwargs):
    raise AssertionError("a reading must not reach the build's setup")


def test_the_switch_runs_only_the_reading(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    monkeypatch.setattr(authoring_entrypoint, "initialize_hosted_compose", _never)
    monkeypatch.setattr(authoring_entrypoint, "_load_provider_import_profile", _never)
    model(_writes(CHAT))
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(_job()), encoding="utf-8")
    output = tmp_path / "reading"

    code = authoring_entrypoint.main(
        [str(job_path), "--source", str(tmp_path / "source"), "--output", str(output), "--read-agent-only"]
    )

    assert code == 0
    assert (output / "agent.json").exists()


def test_the_switch_sends_the_readings_start_to_stderr(tmp_path, model, monkeypatch, capfd):
    _repo(tmp_path)
    # As in the sandbox: nothing has set up logging before the switch runs.
    monkeypatch.setattr(logging.getLogger(), "handlers", [])
    monkeypatch.setattr(reading_entry.logger, "handlers", [])
    monkeypatch.setattr(reading_entry.logger, "level", logging.NOTSET)
    model(_writes(CHAT))
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(_job()), encoding="utf-8")
    output = tmp_path / "reading"

    code = authoring_entrypoint.main(
        [str(job_path), "--source", str(tmp_path / "source"), "--output", str(output), "--read-agent-only"]
    )

    assert code == 0
    assert "reading started at " in capfd.readouterr().err


def test_a_bad_job_through_main_is_refused(tmp_path, model):
    fake = model()
    job_path = tmp_path / "job.json"
    job_path.write_text("{not a job", encoding="utf-8")
    output = tmp_path / "reading"

    code = authoring_entrypoint.main(
        [str(job_path), "--source", str(tmp_path / "source"), "--output", str(output), "--read-agent-only"]
    )

    assert code == 78
    assert _failure_in(output)["code"] == READING_JOB_INVALID
    assert fake.created == []


def _everything_under(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.mark.parametrize("ends", ["succeeds", "fails"])
def test_nothing_is_written_outside_the_output(tmp_path, model, ends):
    source = _repo(tmp_path)
    work = tmp_path / "work"
    (work / "authoring").mkdir(parents=True)
    (work / "authoring" / "kept.txt").write_text("the build's own folder", encoding="utf-8")
    (work / "job.json").write_text(json.dumps(_job(subfolder=SUPPORT)), encoding="utf-8")
    output = work / "reading"
    # The real gate, so the contract lands wherever the reading's own stage writes it.
    model(_submits(CHAT) if ends == "succeeds" else _ends(), output=output)
    before = _everything_under(tmp_path)

    code = run_reading_mode(
        agent_reading.READ_AGENT, job_path=work / "job.json", source=source, output=output
    )

    assert code == (0 if ends == "succeeds" else 78)
    assert (output / "agent.json").exists() is (ends == "succeeds")
    after = {
        name: body
        for name, body in _everything_under(tmp_path).items()
        if not name.startswith("work/reading/")
    }
    assert after == before


@pytest.mark.parametrize("value", ["abc", "0", "-5", "", "nan", "inf"])
def test_a_bad_time_limit_falls_back_to_the_default(monkeypatch, value):
    monkeypatch.setenv(_LIMIT_ENV, value)

    assert agent_reading._soft_limit() == 1800.0
    assert reading_entry._hard_limit(agent_reading.READ_AGENT) == 1800.0 + 300.0


def test_a_good_time_limit_is_read(monkeypatch):
    monkeypatch.setenv(_LIMIT_ENV, "120")

    assert agent_reading._soft_limit() == 120.0
    mode = agent_reading.READ_AGENT
    # The helper's limit is the reading's own plus its margin, and a timeout repeats if retried.
    assert reading_entry._hard_limit(mode) == 120.0 + 300.0
    assert mode.timed_out_retryable is False
    # The contract's codes, as the platform reads them.
    assert (mode.timed_out_code, mode.crashed_code) == (
        "agent_reading_timed_out",
        "agent_reading_crashed",
    )


def test_a_fault_after_a_saved_contract_still_leaves_the_document(tmp_path, model, caplog):
    _repo(tmp_path)
    fake = model()
    # The contract lands, then the session fails to open: the fault the reading raises.
    fake.saves_on_start = CHAT
    fake.start_raises = True
    output = tmp_path / "reading"

    with caplog.at_level("WARNING", logger=agent_reading.__name__):
        assert _run(tmp_path) == 0

    assert _names(output) == {"contract.json", "agent.json", "cost.json"}
    assert fake.sent == []
    _assert_cost(output, 0.0, False)
    # The line names the class; the trace, masked like every line here, keeps the rest.
    [logged] = [
        record
        for record in caplog.records
        if record.name == agent_reading.__name__ and "saved contract" in record.getMessage()
    ]
    assert "FileNotFoundError" in logged.getMessage()
    assert "was not found" not in logged.getMessage()
    assert "FileNotFoundError: the Claude CLI was not found" in logged.exc_text


def test_a_session_that_cannot_open_is_a_crash(tmp_path, model):
    _repo(tmp_path)
    fake = model()
    fake.start_raises = True
    output = tmp_path / "reading"

    assert _run(tmp_path) == 1

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_reading_crashed", True)
    assert "FileNotFoundError" in failure["message"]
    assert fake.sent == []
    _assert_cost(output, 0.0, False)


def test_a_failed_document_write_is_not_retried(tmp_path, model, monkeypatch):
    _repo(tmp_path)
    real = os.replace

    def refuses_the_document(source, destination, *args, **kwargs):
        if Path(destination).name == "agent.json":
            raise OSError("no space left on the device")
        return real(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", refuses_the_document)
    model(_writes(CHAT, cost_usd=0.1))
    output = tmp_path / "reading"

    assert _run(tmp_path) == 78

    failure = _failure_in(output)
    assert (failure["code"], failure["retryable"]) == ("agent_document_invalid", False)
    # No document, and no half-written one left beside it.
    assert _names(output) == {"contract.json", "cost.json", "failure.json"}
    _assert_cost(output, 0.1, True)


# -- TH-9491: the canary (plan v5.1 §W7) ---------------------------------------------------------


def test_a_password_in_the_prompt_reaches_no_file_the_reading_writes(tmp_path, model):
    """The prompt is copied from the source as written, so the URL with credentials is real
    input. Every file under the test folder is then searched; only the source holds it."""
    url = "https://bot:hunter2@hooks.example/v1?key=sk-1#frag"
    source = _repo(tmp_path)
    (source / SUPPORT / "prompt.txt").write_text(
        f"You take orders.\nPost each one to {url} after.\n", encoding="utf-8"
    )
    model(
        _submits(
            {
                **CHAT,
                "one_liner": f"Takes orders and posts them to {url}",
                "system_prompt_from": [
                    {"path": f"{SUPPORT}/prompt.txt", "start_line": 1, "end_line": 2}
                ],
                "hard_constraints": [f"Always post to {url}"],
                "notes": f"The hook is {url}",
            },
            cost_usd=0.1,
        )
    )
    output = tmp_path / "reading"

    assert _run(tmp_path, _job(subfolder=SUPPORT)) == 0

    assert {"contract.json", "agent.json"} <= _names(output)
    contract = json.loads((output / "contract.json").read_text(encoding="utf-8"))
    assert contract["system_prompt_excerpt"] == (
        "You take orders.\nPost each one to https://hooks.example/v1 after."
    )
    holding = sorted(
        str(path.relative_to(tmp_path))
        for path in tmp_path.rglob("*")
        if path.is_file()
        and any(
            secret in path.read_bytes() for secret in (b"hunter2", b"sk-1")
        )
    )
    assert holding == [f"source/{SUPPORT}/prompt.txt"]
