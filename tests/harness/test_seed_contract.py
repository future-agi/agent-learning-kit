"""A build handed a reading's raw contract uses it only when it was made from the same inputs.

No model and no network: the reading's record, the build command's check of the handed file,
the builder's install at the opening of its first stage, and the round trip between them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from fi.alk.harness import authoring_entrypoint, cli, reading_entry, seed_contract
from fi.alk.harness.contract import AgentContract
from fi.alk.harness.framework_detection import detect_framework
from fi.alk.harness.hosted_chat_entrypoint import HostedChatRuntime
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.poc_guest_booking import PIN_ENV, TARGET_PHONE_ENV
from fi.alk.harness.reading_result import document
from fi.alk.harness.seed_contract import (
    RECORD_KEY,
    SEED_ACCEPTED,
    SEED_LIMIT_BYTES,
    differs,
    record_for,
    record_reading,
    use_seed,
)
from fi.alk.harness.understand import load
from fi.alk.providers.agent import build_authored_agent

FOLDER = "agents/a"
COMMIT = "0123456789abcdef0123456789abcdef01234567"
OTHER_COMMIT = "f" * 40
TOKEN = "sk-SEEDTOKEN123"
GITHUB = {
    "kind": "github",
    "visibility": "public",
    "repository": "owner/repo",
    "commit_sha": COMMIT,
    "subfolder": FOLDER,
}
EVALS = [{"name": "a"}]
CONTRACT = {
    "agent": "support",
    "modality": "chat",
    "real_use_cases": ["order"],
    "tools": [{"name": "add", "args": ["item_id"]}],
    "runtime": {"language": "python", "workdir": FOLDER, "source_subfolder": FOLDER},
    "base_environment": {
        "orders": [
            {"id": 1, "total": 0.1, "note": "café", "meta": {"tags": ["日本"]}},
            {"id": 2, "total": 12.5, "note": None, "meta": {"gift": None}},
        ],
        "customers": [{"name": "日本", "credit": 0.1, "address": {"city": "Kyoto"}}],
    },
}


# -- the job, the source and the seed ----------------------------------------------------------


def _job(*, execution="hosted", source=None, metadata=None, agent=None) -> HarnessJob:
    return HarnessJob(
        job_id="job",
        run_id="run",
        execution=execution,
        source=source or GITHUB,
        runtime={"isolation": "dedicated_vm"},
        agent=agent or {"connector": "auto"},
        metadata=(
            {"available_evals": EVALS, "generic_harness_v1": True}
            if metadata is None
            else metadata
        ),
    )


def _job_file(tmp_path: Path, job: HarnessJob) -> Path:
    path = tmp_path / "job.json"
    path.write_text(job.model_dump_json(), encoding="utf-8")
    return path


def _source(tmp_path: Path) -> Path:
    source = tmp_path / "source"
    (source / FOLDER).mkdir(parents=True, exist_ok=True)
    (source / FOLDER / "agent.py").write_text("x = 1\n", encoding="utf-8")
    return source


def _seed_bytes(job: HarnessJob, body: dict = CONTRACT, *, record=None) -> bytes:
    """The reading's bytes as it saved them, unlike any re-dump of them, with its record."""
    raw = json.dumps(body, indent=1, ensure_ascii=True).encode()
    if record is False:
        return raw
    made_from = record_for(job) if record is None else record
    return seed_contract._splice(raw, made_from, empty=not body)


def _seed_path(tmp_path: Path) -> Path:
    return tmp_path / "seed" / "contract.json"


def _place(tmp_path: Path, data: bytes) -> Path:
    path = _seed_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _reading(tmp_path: Path, data: bytes) -> Path:
    reading = tmp_path / "reading"
    reading.mkdir(exist_ok=True)
    (reading / "contract.json").write_bytes(data)
    return reading


# -- the record a reading leaves ---------------------------------------------------------------


def test_a_reading_records_what_it_was_made_from(tmp_path):
    job = _job()
    original = json.dumps(CONTRACT, indent=1).encode()
    reading = _reading(tmp_path, original)

    record_reading(job, reading)

    saved = (reading / "contract.json").read_bytes()
    assert record_for(job) == {
        "version": 1,
        "source": {"kind": "github", "repository": "owner/repo", "commit_sha": COMMIT},
        "subfolder": FOLDER,
        "evals_sha256": hashlib.sha256(b'[{"name":"a"}]').hexdigest(),
        "generic_harness_v1": True,
    }
    assert json.loads(saved)[RECORD_KEY] == record_for(job)
    compact = json.dumps(record_for(job), separators=(",", ":")).encode()
    prefix = b'"reading_inputs": ' + compact + b", "
    assert saved[1 : 1 + len(prefix)] == prefix
    assert saved[:1] + saved[1 + len(prefix) :] == original


def test_the_record_never_reaches_the_agent_document(tmp_path):
    job = _job()
    source = _source(tmp_path)
    reading = _reading(tmp_path, json.dumps(CONTRACT).encode())

    assert document(job, source, reading) == 0

    text = (reading / "agent.json").read_text(encoding="utf-8")
    assert RECORD_KEY not in text
    saved = json.loads((reading / "contract.json").read_text(encoding="utf-8"))
    assert saved[RECORD_KEY] == record_for(job)
    contract = AgentContract.model_validate(saved).model_dump(mode="json")
    framework = detect_framework(source / FOLDER, repo_root=source, package_dir=None)
    assert json.loads(text)["agent"] == build_authored_agent(
        contract, subfolder=FOLDER, framework=framework
    )


@pytest.mark.parametrize(
    "size",
    [SEED_LIMIT_BYTES - 10, SEED_LIMIT_BYTES + 1],
    ids=["near_the_cap", "over_the_cap"],
)
def test_the_record_is_skipped_near_the_size_cap(tmp_path, monkeypatch, size):
    job = _job()
    source = _source(tmp_path)
    text = json.dumps(CONTRACT).encode()
    # Trailing spaces still parse, so only the file's size changes.
    data = text + b" " * (size - len(text))
    reading = _reading(tmp_path, data)
    parsed: list[int] = []

    def loads(value, *args, **kwargs):
        parsed.append(len(value))
        return json.loads(value, *args, **kwargs)

    monkeypatch.setattr(
        seed_contract, "json", SimpleNamespace(loads=loads, dumps=json.dumps)
    )

    code = document(job, source, reading)

    assert (reading / "contract.json").read_bytes() == data
    if size < SEED_LIMIT_BYTES:
        assert code == 0
    else:
        assert parsed == []


def test_a_failed_record_keeps_the_reading(tmp_path, monkeypatch):
    def broken(job, output):
        raise OSError("the disk refused the write")

    monkeypatch.setattr(seed_contract, "record_reading", broken)
    data = json.dumps(CONTRACT).encode()
    reading = _reading(tmp_path, data)

    assert document(_job(), _source(tmp_path), reading) == 0

    assert (reading / "contract.json").read_bytes() == data


@pytest.mark.parametrize(
    ("make", "made_from"),
    [
        (
            lambda: _job(
                execution="local",
                source={k: v for k, v in GITHUB.items() if k != "commit_sha"},
            ),
            None,
        ),
        (
            lambda: _job(source={"kind": "archive", "archive_artifact_id": "upload-1"}),
            {"kind": "archive", "archive_artifact_id": "upload-1"},
        ),
        (
            lambda: _job(
                agent={
                    "connector": "vapi",
                    "mode": "connect_only",
                    "config": {"assistant_id": "assistant-1"},
                }
            ),
            None,
        ),
    ],
    ids=["github_without_a_commit", "archive", "connect_only"],
)
def test_no_record_without_a_source_identity(tmp_path, make, made_from):
    job = make()
    data = json.dumps(CONTRACT).encode()
    reading = _reading(tmp_path, data)

    record_reading(job, reading)

    saved = (reading / "contract.json").read_bytes()
    if made_from is None:
        assert record_for(job) is None
        assert saved == data
    else:
        assert record_for(job)["source"] == made_from
        assert record_for(job)["subfolder"] == ""
        assert json.loads(saved)[RECORD_KEY] == record_for(job)


def test_the_record_lowercases_the_commit():
    job = _job(source={**GITHUB, "commit_sha": "AB" * 20})

    assert job.source.commit_sha == "AB" * 20
    assert record_for(job)["source"]["commit_sha"] == "ab" * 20


# -- the build command -------------------------------------------------------------------------


def _watch_authoring(monkeypatch):
    calls: list[str] = []

    def compose(*_args, **_kwargs):
        calls.append("compose")

    async def auto(_namespace) -> int:
        calls.append("auto")
        return 0

    def await_harness(_output) -> int:
        calls.append("await")
        return 0

    monkeypatch.setattr(authoring_entrypoint, "initialize_hosted_compose", compose)
    monkeypatch.setattr(authoring_entrypoint, "_auto", auto)
    monkeypatch.setattr(authoring_entrypoint, "_await_harness", await_harness)
    monkeypatch.setattr(
        authoring_entrypoint, "_persist_authored_scenario_count", lambda *a: None
    )
    return calls


def _build(tmp_path: Path, *flags: str) -> int:
    return authoring_entrypoint.main(
        [
            str(_job_file(tmp_path, _job())),
            "--source",
            str(_source(tmp_path)),
            "--output",
            str(tmp_path / "authoring"),
            *flags,
        ]
    )


def _seeded(tmp_path: Path, seed: Path | None = None) -> tuple[str, ...]:
    return (
        "--await-harness",
        "--seed-contract",
        str(seed if seed is not None else _seed_path(tmp_path)),
    )


def _marker(tmp_path: Path) -> Path:
    return tmp_path / "authoring" / SEED_ACCEPTED


def test_await_mode_accepts_the_seed_at_its_place(tmp_path, monkeypatch):
    calls = _watch_authoring(monkeypatch)
    _place(tmp_path, _seed_bytes(_job()))
    assert not (tmp_path / "authoring").exists()

    assert _build(tmp_path, *_seeded(tmp_path)) == 0

    assert _marker(tmp_path).is_file()
    assert not (tmp_path / "authoring" / "contract.json").exists()
    assert not (tmp_path / "authoring" / "failure.json").exists()
    assert calls == ["compose", "await"]


def _elsewhere(tmp_path: Path) -> Path:
    other = tmp_path / "other" / "contract.json"
    other.parent.mkdir(parents=True)
    other.write_bytes(_seed_bytes(_job()))
    return other


def _nothing(tmp_path: Path) -> Path:
    return _seed_path(tmp_path)


def _a_link(tmp_path: Path) -> Path:
    real = tmp_path / "real.json"
    real.write_bytes(_seed_bytes(_job()))
    path = _seed_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.symlink_to(real)
    return path


def _a_folder(tmp_path: Path) -> Path:
    path = _seed_path(tmp_path)
    path.mkdir(parents=True)
    return path


def _a_pipe(tmp_path: Path) -> Path:
    path = _seed_path(tmp_path)
    path.parent.mkdir(parents=True)
    os.mkfifo(path)
    return path


def _holding(data: bytes):
    return lambda tmp_path: _place(tmp_path, data)


def _over_the_cap(tmp_path: Path) -> Path:
    raw = _seed_bytes(_job())
    return _place(tmp_path, raw + b" " * (SEED_LIMIT_BYTES + 1 - len(raw)))


INVALID = "seed_contract_invalid"


@pytest.mark.parametrize(
    ("make", "code", "details"),
    [
        (_elsewhere, INVALID, {"reason": "wrong_path"}),
        (_nothing, INVALID, {"reason": "unreadable"}),
        (_a_link, INVALID, {"reason": "unreadable"}),
        (_a_folder, INVALID, {"reason": "not_a_file"}),
        (_a_pipe, INVALID, {"reason": "not_a_file"}),
        (_holding(b"\xff\xfe"), INVALID, {"reason": "not_json"}),
        (_holding(b"not json"), INVALID, {"reason": "not_json"}),
        (_holding(b"[]"), INVALID, {"reason": "not_an_object"}),
        (_holding(b"[" * 100000), INVALID, {"reason": "not_json"}),
        (
            _over_the_cap,
            "seed_contract_too_large",
            {"contract_bytes": SEED_LIMIT_BYTES + 1},
        ),
    ],
    ids=[
        "wrong_path",
        "missing",
        "symlink",
        "directory",
        "fifo",
        "not_utf8",
        "not_json",
        "array",
        "too_deep",
        "too_large",
    ],
)
def test_a_bad_seed_is_refused_by_the_build_command(
    tmp_path, monkeypatch, make, code, details
):
    calls = _watch_authoring(monkeypatch)
    seed = make(tmp_path)

    assert _build(tmp_path, *_seeded(tmp_path, seed)) == 78

    failure = json.loads((tmp_path / "authoring" / "failure.json").read_text())
    assert {
        key: failure[key] for key in ("domain", "stage", "code", "retryable", "details")
    } == {
        "domain": "platform_sync",
        "stage": "understanding_agent",
        "code": code,
        "retryable": False,
        "details": details,
    }
    assert calls == []
    assert not _marker(tmp_path).exists()


def test_a_seed_exactly_at_the_cap_is_accepted(tmp_path, monkeypatch):
    _watch_authoring(monkeypatch)
    raw = _seed_bytes(_job())
    _place(tmp_path, raw + b" " * (SEED_LIMIT_BYTES - len(raw)))
    assert _seed_path(tmp_path).stat().st_size == SEED_LIMIT_BYTES

    assert _build(tmp_path, *_seeded(tmp_path)) == 0

    assert _marker(tmp_path).is_file()


def test_a_refusal_holds_none_of_the_seed(tmp_path, monkeypatch, capfd):
    _watch_authoring(monkeypatch)
    _place(tmp_path, b"not json " + TOKEN.encode())

    assert _build(tmp_path, *_seeded(tmp_path)) == 78

    assert TOKEN not in (tmp_path / "authoring" / "failure.json").read_text()
    captured = capfd.readouterr()
    assert TOKEN not in captured.err
    assert TOKEN not in captured.out


def test_adjustments_mode_ignores_the_seed(tmp_path, monkeypatch, capsys):
    calls = _watch_authoring(monkeypatch)

    code = _build(
        tmp_path,
        "--adjustments",
        str(tmp_path / "adjustments.jsonl"),
        "--seed-contract",
        str(tmp_path / "other" / "contract.json"),
    )

    assert code == 0
    assert "auto" in calls
    assert not (tmp_path / "authoring" / "failure.json").exists()
    assert not _marker(tmp_path).exists()
    assert "seed contract: not used" in capsys.readouterr().err


@pytest.mark.parametrize(
    "flags",
    [["--certify-only", "--bundle-output", "x"], ["--read-agent-only"]],
    ids=["certify_only", "read_agent_only"],
)
def test_the_seed_cannot_be_combined(tmp_path, monkeypatch, flags):
    def never(*_args, **_kwargs):
        raise AssertionError("refused before anything runs")

    monkeypatch.setattr(authoring_entrypoint, "_certify_only", never)
    monkeypatch.setattr(reading_entry, "run_reading_mode", never)

    with pytest.raises(SystemExit) as stopped:
        _build(tmp_path, "--seed-contract", str(_seed_path(tmp_path)), *flags)

    assert stopped.value.code == 2


# -- use_seed ----------------------------------------------------------------------------------


def test_a_matching_seed_is_copied_byte_for_byte(tmp_path):
    job = _job()
    seed = _place(tmp_path, _seed_bytes(job))
    output = tmp_path / "authoring"

    assert use_seed(seed, job, _source(tmp_path), output) == "used"

    assert (output / "contract.json").read_bytes() == seed.read_bytes()
    # What the world stage compares the world it builds against.
    assert load(output).base_environment == CONTRACT["base_environment"]
    assert (output / "generic-harness" / "source-model.json").is_file()


@pytest.mark.parametrize(
    ("build", "body", "names"),
    [
        (
            lambda: _job(source={**GITHUB, "commit_sha": OTHER_COMMIT}),
            CONTRACT,
            ["source"],
        ),
        (
            lambda: _job(source={**GITHUB, "repository": "owner/other"}),
            CONTRACT,
            ["source"],
        ),
        (
            lambda: _job(source={**GITHUB, "subfolder": "agents/b"}),
            CONTRACT,
            ["subfolder", "contract_subfolder"],
        ),
        (
            lambda: _job(
                metadata={
                    "available_evals": [{"name": "b"}],
                    "generic_harness_v1": True,
                }
            ),
            CONTRACT,
            ["evals_sha256"],
        ),
        (
            lambda: _job(
                metadata={"available_evals": EVALS, "generic_harness_v1": False}
            ),
            CONTRACT,
            ["generic_harness_v1"],
        ),
        (
            _job,
            {
                **CONTRACT,
                "runtime": {**CONTRACT["runtime"], "source_subfolder": "agents/other"},
            },
            ["contract_subfolder"],
        ),
    ],
    ids=["commit", "repository", "subfolder", "evals", "generic", "contract_folder"],
)
def test_a_mismatched_seed_reads_again(tmp_path, capsys, build, body, names):
    seed = _place(tmp_path, _seed_bytes(_job(), body))
    job = build()
    output = tmp_path / "authoring"

    assert differs(json.loads(seed.read_bytes()), job) == names
    assert use_seed(seed, job, _source(tmp_path), output) == "mismatch"

    assert not (output / "contract.json").exists()
    assert not (output / "failure.json").exists()
    assert f"differs: {', '.join(names)}" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body",
    [
        CONTRACT,
        {
            **CONTRACT,
            "runtime": {**CONTRACT["runtime"], "source_subfolder": "agents/b"},
        },
    ],
    ids=["no_record", "no_record_and_another_folder"],
)
def test_a_seed_without_a_record_reads_again(tmp_path, body):
    job = _job()
    seed = _place(tmp_path, _seed_bytes(job, body, record=False))

    assert differs(json.loads(seed.read_bytes()), job) == ["reading_inputs"]
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "authoring") == "mismatch"


def test_a_rejected_contract_reads_again(tmp_path, capsys):
    job = _job()
    seed = _place(tmp_path, _seed_bytes(job, {**CONTRACT, "tools": TOKEN}))
    output = tmp_path / "authoring"

    assert use_seed(seed, job, _source(tmp_path), output) == "rejected"

    assert not (output / "contract.json").exists()
    assert not (output / "failure.json").exists()
    err = capsys.readouterr().err
    assert "problems:" in err
    assert TOKEN not in err


def test_a_failed_setup_step_reads_again(tmp_path, monkeypatch, capsys):
    def half_done(destination, source, job):
        model = destination / "generic-harness" / "source-model.json"
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_text("{}", encoding="utf-8")
        raise RuntimeError("the source could not be modelled")

    monkeypatch.setattr(cli, "after_contract", half_done)
    job = _job()
    seed = _place(tmp_path, _seed_bytes(job))
    output = tmp_path / "authoring"

    assert use_seed(seed, job, _source(tmp_path), output) == "setup_failed"

    assert not (output / "contract.json").exists()
    assert not (output / "generic-harness" / "source-model.json").exists()
    assert "class: RuntimeError" in capsys.readouterr().err


def test_a_seed_changed_after_it_was_accepted_reads_again(tmp_path):
    seed = _place(tmp_path, b"not json")
    output = tmp_path / "authoring"

    assert use_seed(seed, _job(), _source(tmp_path), output) == "unreadable"

    assert not (output / "failure.json").exists()
    assert not (output / "contract.json").exists()


# -- the builder -------------------------------------------------------------------------------


class _TurnRan(Exception):
    """Reached the point where a turn starts, which is as far as a test without a model goes."""


class _Opened(Exception):
    """A stage was opened, with the contract it was handed."""

    def __init__(self, contract) -> None:
        super().__init__("opened")
        self.contract = contract


def _runtime(workspace: Path, *, state=None, control_only=True) -> HostedChatRuntime:
    runtime = object.__new__(HostedChatRuntime)
    runtime.workspace = workspace
    runtime.job = {"scenario_count": 2}
    emitted: list[tuple[str, dict]] = []

    async def emit(kind, **fields):
        emitted.append((kind, fields))

    context = {"capabilities": {"control_only": control_only}}
    if state is not None:
        context["status"] = {"state": state}
    runtime.client = SimpleNamespace(
        turn_context=context, command_watermark=3, emit=emit
    )
    runtime.emitted = emitted
    runtime.conversation = SimpleNamespace(stage_name="understand", stage=None)
    runtime._nudge = None
    runtime._from_platform = False
    runtime._last_progress, runtime._idle_turns, runtime._opened_with = None, 0, None
    runtime._what_exists = lambda: (False, False)
    runtime._after_saved = lambda: None
    runtime.handled = []

    async def handle(command):
        runtime.handled.append(command)

    runtime._handle = handle
    return runtime


def _command(*, from_platform=True) -> dict:
    return {
        "sequence": 7,
        "message_id": "command-7",
        "kind": "message",
        "payload": {"content": "Build it.", "from_platform": from_platform},
    }


def _handle_command(runtime, command) -> None:
    async def checkpoint():
        raise _TurnRan

    runtime._checkpoint = checkpoint
    asyncio.run(runtime._handle_command(command))


def _builder(
    tmp_path: Path, monkeypatch, *, marker=True, state=None, control_only=True
) -> HostedChatRuntime:
    monkeypatch.delenv(TARGET_PHONE_ENV, raising=False)
    monkeypatch.delenv(PIN_ENV, raising=False)
    job = _job()
    workspace = tmp_path / "authoring"
    workspace.mkdir()
    runtime = _runtime(workspace, state=state, control_only=control_only)
    runtime.job = job.model_dump(mode="json")
    runtime.source = _source(tmp_path)
    _place(tmp_path, _seed_bytes(job))
    if marker:
        (workspace / SEED_ACCEPTED).write_bytes(b"")

    async def overview():
        return None

    runtime.client.overview = overview
    order: list[str] = []

    def after_saved():
        order.append("after_saved")

    def opening(contract):
        order.append("open")
        raise _Opened(contract)

    runtime._after_saved = after_saved
    runtime._open_for_building = opening
    runtime.order = order
    return runtime


def _open(runtime) -> _Opened:
    with pytest.raises(_Opened) as opened:
        asyncio.run(runtime._open_builder())
    return opened.value


def test_opening_the_builder_installs_an_accepted_seed(tmp_path, monkeypatch):
    runtime = _builder(tmp_path, monkeypatch)
    runtime._what_exists = HostedChatRuntime._what_exists.__get__(runtime)

    opened = _open(runtime)

    assert opened.contract is not None
    assert opened.contract.base_environment == CONTRACT["base_environment"]
    installed = (tmp_path / "authoring" / "contract.json").read_bytes()
    assert installed == _seed_path(tmp_path).read_bytes()
    assert runtime.order == ["after_saved", "open"]


def test_a_seed_without_the_marker_is_not_used(tmp_path, monkeypatch):
    runtime = _builder(tmp_path, monkeypatch, marker=False)
    runtime._what_exists = HostedChatRuntime._what_exists.__get__(runtime)

    assert _open(runtime).contract is None

    assert not (tmp_path / "authoring" / "contract.json").exists()


@pytest.mark.parametrize("case", ["contract", "failure", "world", "build_over"])
def test_the_seed_is_left_alone_when(tmp_path, monkeypatch, case):
    runtime = _builder(
        tmp_path,
        monkeypatch,
        state="canceled" if case == "build_over" else None,
        # Outside control-only only the file itself stops it, not the build being over.
        control_only=case != "failure",
    )

    def never(*_args, **_kwargs):
        raise AssertionError("the seed must be left alone")

    monkeypatch.setattr(seed_contract, "use_seed", never)
    workspace = tmp_path / "authoring"
    kept = json.dumps(CONTRACT).encode()
    if case == "contract":
        (workspace / "contract.json").write_bytes(kept)
    elif case == "failure":
        (workspace / "failure.json").write_text("{}", encoding="utf-8")
    elif case == "world":
        runtime._what_exists = lambda: (True, True)

        def built(contract, overview, policy_job):
            raise _Opened(contract)

        runtime._open_built = built

    _open(runtime)

    if case == "contract":
        assert (workspace / "contract.json").read_bytes() == kept
    else:
        assert not (workspace / "contract.json").exists()


def test_a_command_installs_nothing_before_a_stage_opens(tmp_path, monkeypatch):
    runtime = _builder(tmp_path, monkeypatch)

    with pytest.raises(_TurnRan):
        _handle_command(runtime, _command())

    assert not (tmp_path / "authoring" / "contract.json").exists()


def test_a_failed_source_model_step_still_opens_the_world_stage(
    tmp_path, monkeypatch, capsys
):
    runtime = _builder(tmp_path, monkeypatch, marker=False)
    (tmp_path / "authoring" / "contract.json").write_text(
        json.dumps(CONTRACT), encoding="utf-8"
    )

    def failing():
        raise RuntimeError("the source could not be modelled")

    runtime._after_saved = failing

    assert _open(runtime).contract is not None

    assert "class: RuntimeError" in capsys.readouterr().err


# -- the round trip ----------------------------------------------------------------------------


def test_a_reading_seeds_its_own_build(tmp_path):
    job = _job()
    source = _source(tmp_path)
    reading = _reading(tmp_path, json.dumps(CONTRACT, indent=1).encode())

    record_reading(job, reading)
    seed = reading / "contract.json"

    assert use_seed(seed, job, source, tmp_path / "build") == "used"
    assert (tmp_path / "build" / "contract.json").read_bytes() == seed.read_bytes()
    other = _job(source={**GITHUB, "commit_sha": OTHER_COMMIT})
    assert differs(json.loads(seed.read_bytes()), other) == ["source"]
    assert use_seed(seed, other, source, tmp_path / "other") == "mismatch"


# -- after the change review: the whole wire, and what it left unpinned ------------------------


def _saved_like_a_reading(tmp_path: Path, body: dict, job: HarnessJob) -> bytes:
    # As the reading's save gate writes it, then the reading's record.
    reading = tmp_path / "reading"
    reading.mkdir()
    dumped = AgentContract.model_validate(body).model_dump()
    (reading / "contract.json").write_text(
        json.dumps(dumped, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    record_reading(job, reading)
    return (reading / "contract.json").read_bytes()


def test_the_build_command_then_the_builder(tmp_path, monkeypatch):
    calls = _watch_authoring(monkeypatch)
    job = _job()
    raw = _saved_like_a_reading(tmp_path, CONTRACT, job)
    _place(tmp_path, raw)
    assert _build(tmp_path, *_seeded(tmp_path)) == 0
    assert calls == ["compose", "await"]
    workspace = tmp_path / "authoring"
    runtime = _runtime(workspace)
    runtime.job = job.model_dump(mode="json")
    runtime.source = _source(tmp_path)
    runtime._what_exists = HostedChatRuntime._what_exists.__get__(runtime)

    def opening(contract):
        raise _Opened(contract)

    runtime._open_for_building = opening
    opened = _open(runtime)
    assert opened.contract is not None
    assert (workspace / "contract.json").read_bytes() == raw
    assert load(workspace).base_environment == CONTRACT["base_environment"]
    assert (workspace / "generic-harness" / "source-model.json").is_file()


def test_a_reading_of_the_repository_root_matches(tmp_path):
    job = _job(source={k: v for k, v in GITHUB.items() if k != "subfolder"})
    body = {**CONTRACT, "runtime": {"language": "python", "workdir": "."}}
    raw = _saved_like_a_reading(tmp_path, body, job)
    assert differs(json.loads(raw), job) == []
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("x = 1\n")
    assert (
        use_seed(tmp_path / "reading" / "contract.json", job, source, tmp_path / "out")
        == "used"
    )


def test_a_reading_recorded_twice_keeps_one_record(tmp_path):
    job = _job()
    raw = _saved_like_a_reading(tmp_path, CONTRACT, job)
    record_reading(job, tmp_path / "reading")
    assert (tmp_path / "reading" / "contract.json").read_bytes() == raw
    assert raw.count(b'"reading_inputs"') == 1


@pytest.mark.parametrize("version", [2, True, "1", None])
def test_a_record_of_another_version_reads_again(tmp_path, version):
    job = _job()
    record = {**record_for(job), "version": version}
    seed = _place(tmp_path, _seed_bytes(job, record=record))
    assert differs(json.loads(seed.read_bytes()), job) == ["version"]
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "out") == "mismatch"


def test_an_open_reading_is_never_installed_under(tmp_path, monkeypatch):
    from fi.alk.harness.hosted_chat_entrypoint import BuilderConversation

    runtime = _builder(tmp_path, monkeypatch, marker=False)
    runtime._what_exists = HostedChatRuntime._what_exists.__get__(runtime)
    conversation = BuilderConversation(runtime._open_builder)
    said = []

    class Reading:
        async def say(self, message, on_event=None):
            said.append(message)
            return "ok"

    # A person's message opened the reading; the build command's mark comes after it.
    conversation.stage = Reading()
    (tmp_path / "authoring" / SEED_ACCEPTED).write_bytes(b"")
    asyncio.run(conversation.say("Build it."))
    assert said == ["Build it."]
    assert not (tmp_path / "authoring" / "contract.json").exists()


class _AtTheTurn(BaseException):
    """Raised where the turn's message is put together, past every checkpoint."""


def test_a_command_installs_nothing_up_to_its_turn(tmp_path, monkeypatch):
    runtime = _builder(tmp_path, monkeypatch)
    runtime.conversation.stage = None
    runtime.conversation.say = lambda *a, **k: None
    runtime.backend_name = "claude"
    runtime._turn_acknowledged_through = 0
    runtime._tool_names = {}
    runtime._journal_unsuccessful = lambda *a: None

    async def checkpoint():
        return {}

    async def pump(*a, **k):
        return None

    def stop():
        raise _AtTheTurn

    runtime._checkpoint = checkpoint
    runtime._pump_events = pump
    runtime._ledger_note = stop
    with pytest.raises(_AtTheTurn):
        asyncio.run(runtime._handle_command(_command()))
    assert not (tmp_path / "authoring" / "contract.json").exists()


def test_the_builder_hands_over_the_agents_source(tmp_path, monkeypatch):
    runtime = _builder(tmp_path, monkeypatch)
    runtime._what_exists = HostedChatRuntime._what_exists.__get__(runtime)
    handed = []
    monkeypatch.setattr(
        seed_contract,
        "use_seed",
        lambda seed, job, source, output: handed.append((source, output)),
    )
    _open(runtime)
    assert handed == [(runtime.source, runtime.workspace)]


def test_a_seed_that_grows_while_it_is_read_is_refused(tmp_path, monkeypatch):
    seed = _place(tmp_path, b"{" + b" " * SEED_LIMIT_BYTES + b"}")
    real = os.fstat

    def sized_small(descriptor):
        found = real(descriptor)
        return os.stat_result(
            (found.st_mode, *list(found)[1:6], 10, *list(found)[7:10])
        )

    monkeypatch.setattr(seed_contract.os, "fstat", sized_small)
    _, problem, _ = seed_contract.read_seed(seed)
    assert problem is not None and problem.code == "seed_contract_too_large"


def test_a_contract_the_reading_would_not_save_reads_again(tmp_path, capsys):
    job = _job()
    seed = _place(tmp_path, _seed_bytes(job, {**CONTRACT, "agent": ""}))
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "out") == "rejected"
    assert not (tmp_path / "out" / "contract.json").exists()
    assert "problems: 1" in capsys.readouterr().err


def test_one_install_at_a_time(tmp_path, monkeypatch):
    import threading
    import time

    runtime = _builder(tmp_path, monkeypatch)
    runtime._what_exists = HostedChatRuntime._what_exists.__get__(runtime)
    running, most = [0], [0]
    gate = threading.Lock()

    def slow(*_args):
        with gate:
            running[0] += 1
            most[0] = max(most[0], running[0])
        time.sleep(0.2)
        with gate:
            running[0] -= 1
        return "mismatch"

    monkeypatch.setattr(seed_contract, "use_seed", slow)
    threads = [threading.Thread(target=runtime._take_seed) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert most[0] == 1


def test_a_broken_stderr_does_not_undo_a_used_seed(tmp_path, monkeypatch):
    class Closed:
        def write(self, _text):
            raise OSError("closed")

        def flush(self):
            raise OSError("closed")

    job = _job()
    seed = _place(tmp_path, _seed_bytes(job))
    monkeypatch.setattr(seed_contract.sys, "stderr", Closed())
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "out") == "used"
    assert (tmp_path / "out" / "contract.json").read_bytes() == seed.read_bytes()
