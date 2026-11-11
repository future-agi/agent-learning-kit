"""A build handed a reading's raw contract uses it only when it was made from the same inputs.

No model and no network: the reading's record, the build command's check of the handed file,
the builder's install at the opening of its first stage, and the round trip between them.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel, ValidationError

from fi.alk.harness import (
    authoring_entrypoint,
    cli,
    reading_entry,
    reading_result,
    seed_contract,
)
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
    SEED_RESULT,
    differs,
    record_for,
    use_seed,
    with_record,
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

    saved = with_record(job, original)

    assert saved is not None
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


def test_a_record_is_never_parsed_from_a_contract_over_the_cap(monkeypatch):
    parsed: list[int] = []

    def loads(value, *args, **kwargs):
        parsed.append(len(value))
        return json.loads(value, *args, **kwargs)

    monkeypatch.setattr(
        seed_contract, "json", SimpleNamespace(loads=loads, dumps=json.dumps)
    )
    text = json.dumps(CONTRACT).encode()
    raw = text + b" " * (SEED_LIMIT_BYTES + 1 - len(text))

    assert seed_contract.with_record(_job(), raw) is None
    assert parsed == []


def test_a_failed_record_keeps_the_reading(tmp_path, monkeypatch):
    def broken(job, raw):
        raise OSError("the disk refused the write")

    monkeypatch.setattr(seed_contract, "with_record", broken)
    data = json.dumps(CONTRACT).encode()
    reading = _reading(tmp_path, data)

    assert document(_job(), _source(tmp_path), reading) == 0

    assert (reading / "contract.json").read_bytes() == data


def test_a_failed_record_keeps_its_traceback(tmp_path, monkeypatch, caplog):
    def broken(job, raw):
        raise RuntimeError("rec-9")

    monkeypatch.setattr(seed_contract, "with_record", broken)
    reading = _reading(tmp_path, json.dumps(CONTRACT).encode())

    with caplog.at_level(logging.WARNING, logger=reading_result.__name__):
        assert document(_job(), _source(tmp_path), reading) == 0

    [logged] = [r for r in caplog.records if r.name == reading_result.__name__]
    assert logged.getMessage() == "reading record not written"
    # The logger's mask keeps the trace only as masked text.
    assert "Traceback" in logged.exc_text
    assert "RuntimeError: rec-9" in logged.exc_text


LATER = json.dumps({**CONTRACT, "agent": "saved-later"}).encode()


_READ = json.dumps(CONTRACT).encode()


@pytest.mark.parametrize(
    "later",
    [LATER, _READ.replace(b'"support"', b'"Support"', 1)],
    ids=["longer", "same_length"],
)
def test_a_save_after_the_read_is_never_overwritten(tmp_path, monkeypatch, later):
    job = _job()
    reading = _reading(tmp_path, _READ)
    real = seed_contract.with_record

    def saved_meanwhile(job, raw):
        # The session saves again while the record is being made.
        (reading / "contract.json").write_bytes(later)
        return real(job, raw)

    monkeypatch.setattr(seed_contract, "with_record", saved_meanwhile)

    assert document(job, _source(tmp_path), reading) == 0

    assert (reading / "contract.json").read_bytes() == later


@pytest.mark.parametrize("saved_during_the_read", [False, True], ids=["quiet", "saved"])
def test_the_record_is_made_from_the_bytes_the_document_used(
    tmp_path, monkeypatch, saved_during_the_read
):
    job = _job()
    data = json.dumps(CONTRACT, indent=1).encode()
    reading = _reading(tmp_path, data)
    real = seed_contract.with_record
    seen: list[bytes] = []

    def spy(job, raw):
        seen.append(raw)
        return real(job, raw)

    monkeypatch.setattr(seed_contract, "with_record", spy)
    if saved_during_the_read:
        landed: list[bool] = []

        def saved_then_sized(descriptor):
            # Right after the document's read: only a fresh read of the file would see it.
            if not landed:
                landed.append(True)
                (reading / "contract.json").write_bytes(LATER)
            return os.fstat(descriptor)

        monkeypatch.setattr(
            reading_result,
            "os",
            SimpleNamespace(fstat=saved_then_sized, replace=os.replace),
        )

    assert document(job, _source(tmp_path), reading) == 0

    assert seen == [data]
    saved = (reading / "contract.json").read_bytes()
    if saved_during_the_read:
        assert saved == LATER
        assert "saved-later" not in (reading / "agent.json").read_text()
    else:
        assert saved == real(job, data)


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

    saved = with_record(job, data)

    if made_from is None:
        assert record_for(job) is None
        assert saved is None
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


@pytest.mark.parametrize("case", ["no_seed", "refused"])
def test_the_build_command_clears_a_stale_mark(tmp_path, monkeypatch, case):
    calls = _watch_authoring(monkeypatch)
    _marker(tmp_path).parent.mkdir(parents=True)
    _marker(tmp_path).write_bytes(b"")

    if case == "no_seed":
        assert _build(tmp_path, "--await-harness") == 0
        assert calls == ["compose", "await"]
    else:
        assert _build(tmp_path, *_seeded(tmp_path, _elsewhere(tmp_path))) == 78
        assert calls == []

    assert not _marker(tmp_path).exists()


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
    [
        ["--certify-only", "--bundle-output", "x"],
        ["--read-agent-only"],
        ["--find-agents"],
    ],
    ids=["certify_only", "read_agent_only", "find_agents"],
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

    assert use_seed(seed, job, _source(tmp_path), output) == ("used", [])

    assert (output / "contract.json").read_bytes() == seed.read_bytes()
    # What the world stage compares the world it builds against.
    assert load(output).base_environment == CONTRACT["base_environment"]
    assert (output / "generic-harness" / "source-model.json").is_file()


@pytest.mark.parametrize("generic", [False, None], ids=["false", "absent"])
def test_a_job_that_isnt_generic_installs_and_records_the_seed(tmp_path, generic):
    metadata = {"available_evals": EVALS}
    if generic is not None:
        metadata["generic_harness_v1"] = generic
    job = _job(metadata=metadata)
    seed = _place(tmp_path, _seed_bytes(job))
    output = tmp_path / "authoring"

    assert use_seed(seed, job, _source(tmp_path), output) == ("used", [])

    assert (output / "contract.json").read_bytes() == seed.read_bytes()
    assert (output / seed_contract.SEED_INSTALLED).is_file()
    # Nothing else of a generic-harness build is made for it.
    assert not (output / "generic-harness" / "source-model.json").exists()


def test_a_reading_with_no_flag_seeds_a_build_whose_flag_is_false(tmp_path):
    seed = _place(tmp_path, _seed_bytes(_job(metadata={"available_evals": EVALS})))
    build = _job(metadata={"available_evals": EVALS, "generic_harness_v1": False})

    assert use_seed(seed, build, _source(tmp_path), tmp_path / "authoring") == (
        "used",
        [],
    )


@pytest.mark.parametrize("generic", [False, None], ids=["false", "absent"])
def test_a_builder_for_a_job_that_isnt_generic_uses_the_seed(
    tmp_path, monkeypatch, generic
):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    metadata = {"available_evals": EVALS}
    if generic is not None:
        metadata["generic_harness_v1"] = generic
    job = _job(metadata=metadata)
    runtime.job = job.model_dump(mode="json")
    _place(tmp_path, _seed_bytes(job))
    workspace = tmp_path / "authoring"

    assert _open(runtime).contract is not None
    assert _outcome(tmp_path) == {"seed": "used", "differs": []}
    assert (workspace / seed_contract.SEED_INSTALLED).is_file()

    # A restart after a contract edit and a lost outcome still says it was used.
    (workspace / SEED_RESULT).unlink()
    (workspace / "contract.json").write_text(json.dumps(CONTRACT), encoding="utf-8")
    assert _open(_restarted(runtime)).contract is not None
    assert _outcome(tmp_path) == {"seed": "used", "differs": []}


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
    assert use_seed(seed, job, _source(tmp_path), output) == ("mismatch", names)

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
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "authoring") == (
        "mismatch",
        ["reading_inputs"],
    )


def test_a_rejected_contract_reads_again(tmp_path, capsys):
    job = _job()
    seed = _place(tmp_path, _seed_bytes(job, {**CONTRACT, "tools": TOKEN}))
    output = tmp_path / "authoring"

    assert use_seed(seed, job, _source(tmp_path), output) == ("rejected", [])

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

    assert use_seed(seed, job, _source(tmp_path), output) == ("setup_failed", [])

    assert not (output / "contract.json").exists()
    assert not (output / "generic-harness" / "source-model.json").exists()
    assert "class: RuntimeError" in capsys.readouterr().err


def test_a_seed_changed_after_it_was_accepted_reads_again(tmp_path):
    seed = _place(tmp_path, b"not json")
    output = tmp_path / "authoring"

    assert use_seed(seed, _job(), _source(tmp_path), output) == ("unreadable", [])

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
    assert not (workspace / SEED_RESULT).exists()


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


def _outcome(tmp_path: Path) -> dict:
    # The platform's path, spelled out: it is stored with the version but never hashed.
    workspace = tmp_path / "authoring"
    assert not (workspace / "seed-result.json").exists()
    return json.loads((workspace / "generic-harness" / "seed-result.json").read_bytes())


def _real_world(runtime) -> HostedChatRuntime:
    runtime._what_exists = HostedChatRuntime._what_exists.__get__(runtime)
    return runtime


def test_a_later_opening_never_installs(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch, marker=False))
    workspace = tmp_path / "authoring"

    assert _open(runtime).contract is None
    decided = (workspace / SEED_RESULT).read_bytes()
    assert json.loads(decided) == {"seed": "not_accepted", "differs": []}

    # The build command's mark lands after the builder's first opening.
    _marker(tmp_path).write_bytes(b"")
    assert _open(runtime).contract is None

    assert not (workspace / "contract.json").exists()
    assert _marker(tmp_path).is_file()
    assert (workspace / SEED_RESULT).read_bytes() == decided

    # A new process finds only the file to go by.
    assert _open(_restarted(runtime)).contract is None
    assert not (workspace / "contract.json").exists()
    assert (workspace / SEED_RESULT).read_bytes() == decided


def _restarted(runtime) -> HostedChatRuntime:
    """The same workspace as a new process sees it: nothing remembered of the seed."""
    again = object.__new__(HostedChatRuntime)
    again.__dict__.update(
        {
            key: value
            for key, value in vars(runtime).items()
            if not key.startswith("_seed")
        }
    )
    return again


def _from_another_commit(tmp_path, monkeypatch, runtime) -> None:
    _place(tmp_path, _seed_bytes(_job(source={**GITHUB, "commit_sha": OTHER_COMMIT})))


@pytest.mark.parametrize("outcome", ["used", "mismatch"])
def test_the_mark_is_consumed_whatever_the_outcome(tmp_path, monkeypatch, outcome):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    if outcome == "mismatch":
        _from_another_commit(tmp_path, monkeypatch, runtime)

    _open(runtime)

    assert not _marker(tmp_path).exists()
    assert _outcome(tmp_path)["seed"] == outcome


def _as_placed(tmp_path, monkeypatch, runtime) -> None:
    pass


def _not_a_contract(tmp_path, monkeypatch, runtime) -> None:
    _place(tmp_path, _seed_bytes(_job(), {**CONTRACT, "tools": TOKEN}))


def _changed_since(tmp_path, monkeypatch, runtime) -> None:
    _place(tmp_path, b"not json")


def _setup_fails(tmp_path, monkeypatch, runtime) -> None:
    def fails(destination, source, job):
        raise RuntimeError("the source could not be modelled")

    monkeypatch.setattr(cli, "after_contract", fails)


def _job_unreadable(tmp_path, monkeypatch, runtime) -> None:
    runtime.job = {**runtime.job, "execution": "nowhere"}


@pytest.mark.parametrize(
    ("arrange", "expected"),
    [
        (_as_placed, {"seed": "used", "differs": []}),
        (_from_another_commit, {"seed": "mismatch", "differs": ["source"]}),
        (_not_a_contract, {"seed": "rejected", "differs": []}),
        (_changed_since, {"seed": "unreadable", "differs": []}),
        (_setup_fails, {"seed": "setup_failed", "differs": []}),
        (_job_unreadable, {"seed": "error", "differs": []}),
    ],
    ids=["used", "mismatch", "rejected", "unreadable", "setup_failed", "error"],
)
def test_the_outcome_is_written_for_the_platform(
    tmp_path, monkeypatch, arrange, expected
):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    arrange(tmp_path, monkeypatch, runtime)

    opened = _open(runtime)

    used = expected["seed"] == "used"
    assert _outcome(tmp_path) == expected
    assert (opened.contract is not None) == used
    assert (tmp_path / "authoring" / "contract.json").exists() == used


def test_no_outcome_without_a_seed(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch, marker=False))
    _seed_path(tmp_path).unlink()

    assert _open(runtime).contract is None

    assert not (tmp_path / "authoring" / SEED_RESULT).exists()


def test_no_outcome_after_a_refusal(tmp_path, monkeypatch):
    # Outside control-only, so the refusal's own file is all that stops it.
    runtime = _real_world(
        _builder(tmp_path, monkeypatch, marker=False, control_only=False)
    )
    (tmp_path / "authoring" / "failure.json").write_text("{}", encoding="utf-8")

    _open(runtime)

    assert not (tmp_path / "authoring" / SEED_RESULT).exists()


def _install_breaks(tmp_path, monkeypatch, runtime) -> None:
    def breaks(destination, source, job):
        raise RuntimeError("boom-7")

    monkeypatch.setattr(cli, "after_contract", breaks)


def _check_breaks(tmp_path, monkeypatch, runtime) -> None:
    def breaks(body, job):
        raise RuntimeError("boom-7")

    monkeypatch.setattr(seed_contract, "differs", breaks)


class _JobThatBreaks:
    @classmethod
    def model_validate(cls, value):
        raise RuntimeError("boom-7")


def _job_breaks(tmp_path, monkeypatch, runtime) -> None:
    # Imported first, so nothing the opening imports binds the stand-in for good.
    from fi.alk.harness import builder  # noqa: F401
    from fi.alk.harness import job as job_module

    monkeypatch.setattr(job_module, "HarnessJob", _JobThatBreaks)


def _source_model_breaks(tmp_path, monkeypatch, runtime) -> None:
    (tmp_path / "authoring" / "contract.json").write_text(
        json.dumps(CONTRACT), encoding="utf-8"
    )

    def breaks():
        raise RuntimeError("boom-7")

    runtime._after_saved = breaks


@pytest.mark.parametrize(
    "arrange",
    [_install_breaks, _check_breaks, _job_breaks, _source_model_breaks],
    ids=["after_contract", "use_seed", "job", "after_saved"],
)
def test_an_unexpected_failure_keeps_its_traceback(
    tmp_path, monkeypatch, capsys, arrange
):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    arrange(tmp_path, monkeypatch, runtime)

    _open(runtime)

    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "boom-7" in err


def test_a_job_that_fails_validation_logs_no_values(tmp_path, monkeypatch, capfd):
    secret = "sk-JOBTOKEN42"
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    runtime.job = {**runtime.job, "execution": secret}

    _open(runtime)

    err = capfd.readouterr().err
    assert "ValidationError" in err
    assert "Traceback" not in err
    assert secret not in err
    assert _outcome(tmp_path) == {"seed": "error", "differs": []}
    assert secret not in (tmp_path / "authoring" / SEED_RESULT).read_text()


class _Holds(BaseModel):
    value: int


def _holding_values() -> ValidationError:
    try:
        _Holds.model_validate({"value": TOKEN})
    except ValidationError as exc:
        return exc
    raise AssertionError("the value must fail")


def _install_holds_values(tmp_path, monkeypatch, runtime) -> None:
    def fails(destination, source, job):
        raise _holding_values()

    monkeypatch.setattr(cli, "after_contract", fails)


def _check_holds_values(tmp_path, monkeypatch, runtime) -> None:
    def fails(body, job):
        raise _holding_values()

    monkeypatch.setattr(seed_contract, "differs", fails)


def _source_model_holds_values(tmp_path, monkeypatch, runtime) -> None:
    (tmp_path / "authoring" / "contract.json").write_text(
        json.dumps(CONTRACT), encoding="utf-8"
    )

    def fails():
        raise _holding_values()

    runtime._after_saved = fails


def _chained_under_another(tmp_path, monkeypatch, runtime) -> None:
    def fails(destination, source, job):
        try:
            raise _holding_values()
        except ValidationError:
            raise RuntimeError("boom-7")

    monkeypatch.setattr(cli, "after_contract", fails)


@pytest.mark.parametrize(
    "arrange",
    [
        _install_holds_values,
        _check_holds_values,
        _source_model_holds_values,
        _chained_under_another,
    ],
    ids=["after_contract", "use_seed", "after_saved", "chained"],
)
def test_a_validation_error_never_prints_its_values(
    tmp_path, monkeypatch, capfd, arrange
):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    arrange(tmp_path, monkeypatch, runtime)

    _open(runtime)

    err = capfd.readouterr().err
    assert "class: " in err
    assert "Traceback" not in err
    assert TOKEN not in err


def _result_write_fails(monkeypatch, *, times: int) -> list[str]:
    real = os.replace
    failed: list[str] = []

    def replace(src, dst):
        if Path(dst).name == SEED_RESULT.name and len(failed) < times:
            failed.append(str(dst))
            raise OSError(28, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(seed_contract.os, "replace", replace)
    return failed


def test_the_outcome_is_written_whole_or_not_at_all(tmp_path, monkeypatch, capsys):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    workspace = tmp_path / "authoring"
    failed = _result_write_fails(monkeypatch, times=1)

    assert _open(runtime).contract is not None

    assert failed
    assert not (workspace / SEED_RESULT).exists()
    assert not list(workspace.rglob(".seed-*.tmp"))
    err = capsys.readouterr().err
    assert "seed result not written; class: OSError" in err
    assert "Traceback" in err

    # The next opening writes what was decided, and installs nothing again.
    installed = (workspace / "contract.json").read_bytes()
    assert _open(runtime).contract is not None
    assert _outcome(tmp_path) == {"seed": "used", "differs": []}
    assert (workspace / "contract.json").read_bytes() == installed


def test_a_lost_outcome_never_lets_a_reopen_install(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch, marker=False))
    workspace = tmp_path / "authoring"
    _result_write_fails(monkeypatch, times=1)

    assert _open(runtime).contract is None
    assert not (workspace / SEED_RESULT).exists()

    # The build command's mark lands while the reading is under way.
    _marker(tmp_path).write_bytes(b"")
    assert _open(runtime).contract is None

    assert not (workspace / "contract.json").exists()
    assert _outcome(tmp_path) == {"seed": "not_accepted", "differs": []}


def test_a_restart_after_the_install_says_it_was_used(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    workspace = tmp_path / "authoring"
    # The process stopped after the install and before it could say so.
    (workspace / "contract.json").write_bytes(_seed_path(tmp_path).read_bytes())

    assert _open(runtime).contract is not None

    assert _outcome(tmp_path) == {"seed": "used", "differs": []}
    assert not _marker(tmp_path).exists()
    assert runtime.order == ["after_saved", "open"]


def test_a_restart_after_an_edit_and_a_lost_outcome_says_it_was_used(
    tmp_path, monkeypatch
):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    workspace = tmp_path / "authoring"
    _result_write_fails(monkeypatch, times=1)
    assert _open(runtime).contract is not None
    assert (workspace / seed_contract.SEED_INSTALLED).is_file()

    # A contract tool rewrites the contract without the record, then the process stops.
    (workspace / "contract.json").write_text(json.dumps(CONTRACT), encoding="utf-8")
    assert _open(_restarted(runtime)).contract is not None

    assert _outcome(tmp_path) == {"seed": "used", "differs": []}


def test_an_install_that_cannot_be_recorded_is_taken_back(
    tmp_path, monkeypatch, capsys
):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    workspace = tmp_path / "authoring"
    real = os.replace

    def replace(src, dst):
        if Path(dst).name == seed_contract.SEED_INSTALLED.name:
            raise OSError(28, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(seed_contract.os, "replace", replace)

    assert _open(runtime).contract is None

    assert _outcome(tmp_path) == {"seed": "error", "differs": []}
    assert not (workspace / "contract.json").exists()
    assert not (workspace / seed_contract.SEED_INSTALLED).exists()
    assert "Traceback" in capsys.readouterr().err


def test_a_contract_that_cannot_be_compared_is_never_claimed(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    workspace = tmp_path / "authoring"
    (workspace / "contract.json").write_bytes(_seed_path(tmp_path).read_bytes())

    def breaks(path):
        raise RuntimeError("boom-7")

    monkeypatch.setattr(seed_contract, "read_seed", breaks)

    assert _open(runtime).contract is not None

    assert not (workspace / SEED_RESULT).exists()


def test_every_file_is_written_beside_its_place(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch, marker=False))
    workspace = tmp_path / "authoring"
    real = os.replace
    moves: list[tuple[Path, Path]] = []

    def replace(src, dst):
        moves.append((Path(src), Path(dst)))
        return real(src, dst)

    monkeypatch.setattr(seed_contract.os, "replace", replace)

    assert seed_contract.accept_seed(_seed_path(tmp_path), workspace) is None
    assert _open(runtime).contract is not None

    # Moved within one folder, so never across file systems, and never half written.
    assert {dst.name for _, dst in moves} >= {
        SEED_ACCEPTED,
        "contract.json",
        seed_contract.SEED_INSTALLED.name,
        SEED_RESULT.name,
    }
    assert all(src.parent == dst.parent for src, dst in moves)


def test_a_source_model_already_there_survives_a_take_back(tmp_path, monkeypatch):
    job = _job()
    seed = _place(tmp_path, _seed_bytes(job))
    output = tmp_path / "authoring"
    model = output / "generic-harness" / "source-model.json"
    model.parent.mkdir(parents=True)
    model.write_text('{"kept": true}', encoding="utf-8")
    _setup_fails(tmp_path, monkeypatch, None)

    assert use_seed(seed, job, _source(tmp_path), output) == ("setup_failed", [])

    assert model.read_text(encoding="utf-8") == '{"kept": true}'
    assert not (output / "contract.json").exists()


def test_a_group_holding_a_validation_error_prints_nothing(capsys):
    import builtins

    group_type = getattr(builtins, "ExceptionGroup", None)
    if group_type is None:
        pytest.skip("no exception groups before 3.11")

    seed_contract.print_trace(group_type("several", [_holding_values()]))

    err = capsys.readouterr().err
    assert TOKEN not in err
    assert "Traceback" not in err


def test_a_chain_that_loops_still_ends():
    import threading

    first, second = RuntimeError("first"), RuntimeError("second")
    first.__context__, second.__context__ = second, first
    answers: list[bool] = []
    walk = threading.Thread(
        target=lambda: answers.append(seed_contract.holds_values(first)), daemon=True
    )
    walk.start()
    walk.join(timeout=5)

    assert answers == [False]


def test_a_restart_after_a_lost_outcome_says_it_was_used(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    _result_write_fails(monkeypatch, times=1)
    assert _open(runtime).contract is not None
    assert not (tmp_path / "authoring" / SEED_RESULT).exists()

    assert _open(_restarted(runtime)).contract is not None

    assert _outcome(tmp_path) == {"seed": "used", "differs": []}


def test_a_contract_that_is_not_the_seed_is_never_claimed(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    workspace = tmp_path / "authoring"
    seed = _seed_path(tmp_path).read_bytes()
    # The same length, one byte apart: what a reading of its own saved.
    saved = seed.replace(b'"support"', b'"Support"', 1)
    assert saved != seed and len(saved) == len(seed)
    (workspace / "contract.json").write_bytes(saved)

    _open(runtime)

    assert not (workspace / SEED_RESULT).exists()
    assert (workspace / "contract.json").read_bytes() == saved


def _unlink_fails_for(monkeypatch, name: str) -> None:
    real = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == name:
            raise PermissionError(13, "Permission denied")
        return real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_a_mark_that_cannot_be_removed_still_leaves_the_outcome(
    tmp_path, monkeypatch, capsys
):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    _unlink_fails_for(monkeypatch, SEED_ACCEPTED)

    assert _open(runtime).contract is not None

    assert _outcome(tmp_path) == {"seed": "used", "differs": []}
    err = capsys.readouterr().err
    assert "Traceback" in err
    assert "PermissionError" in err


def test_a_contract_that_cannot_be_taken_back_is_used(tmp_path, monkeypatch):
    runtime = _real_world(_builder(tmp_path, monkeypatch))
    _setup_fails(tmp_path, monkeypatch, runtime)
    _unlink_fails_for(monkeypatch, "contract.json")

    assert _open(runtime).contract is not None

    assert _outcome(tmp_path) == {"seed": "used", "differs": []}
    installed = (tmp_path / "authoring" / "contract.json").read_bytes()
    assert installed == _seed_path(tmp_path).read_bytes()


# -- the round trip ----------------------------------------------------------------------------


def test_a_reading_seeds_its_own_build(tmp_path):
    job = _job()
    source = _source(tmp_path)
    reading = _reading(tmp_path, json.dumps(CONTRACT, indent=1).encode())

    assert document(job, source, reading) == 0
    seed = reading / "contract.json"

    assert use_seed(seed, job, source, tmp_path / "build") == ("used", [])
    assert (tmp_path / "build" / "contract.json").read_bytes() == seed.read_bytes()
    other = _job(source={**GITHUB, "commit_sha": OTHER_COMMIT})
    assert differs(json.loads(seed.read_bytes()), other) == ["source"]
    assert use_seed(seed, other, source, tmp_path / "other") == ("mismatch", ["source"])


# -- the build command and the builder together, and the record's edge cases --


def _saved_like_a_reading(tmp_path: Path, body: dict, job: HarnessJob) -> bytes:
    # As the reading's save gate writes it, then the reading's record.
    reading = tmp_path / "reading"
    reading.mkdir()
    dumped = AgentContract.model_validate(body).model_dump()
    saved = json.dumps(dumped, indent=2, ensure_ascii=False).encode("utf-8")
    recorded = with_record(job, saved)
    assert recorded is not None
    (reading / "contract.json").write_bytes(recorded)
    return recorded


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
    assert _outcome(tmp_path) == {"seed": "used", "differs": []}
    assert not _marker(tmp_path).exists()


def test_a_reading_of_the_repository_root_matches(tmp_path):
    job = _job(source={k: v for k, v in GITHUB.items() if k != "subfolder"})
    body = {**CONTRACT, "runtime": {"language": "python", "workdir": "."}}
    raw = _saved_like_a_reading(tmp_path, body, job)
    assert differs(json.loads(raw), job) == []
    source = tmp_path / "source"
    source.mkdir()
    (source / "agent.py").write_text("x = 1\n")
    assert use_seed(
        tmp_path / "reading" / "contract.json", job, source, tmp_path / "out"
    ) == ("used", [])


def test_a_reading_recorded_twice_keeps_one_record(tmp_path):
    job = _job()
    raw = _saved_like_a_reading(tmp_path, CONTRACT, job)
    assert with_record(job, raw) is None
    assert raw.count(b'"reading_inputs"') == 1


@pytest.mark.parametrize("version", [2, True, "1", None])
def test_a_record_of_another_version_reads_again(tmp_path, version):
    job = _job()
    record = {**record_for(job), "version": version}
    seed = _place(tmp_path, _seed_bytes(job, record=record))
    assert differs(json.loads(seed.read_bytes()), job) == ["version"]
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "out") == (
        "mismatch",
        ["version"],
    )


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

    def use(seed, job, source, output):
        handed.append((source, output))
        return "mismatch", []

    monkeypatch.setattr(seed_contract, "use_seed", use)
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
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "out") == ("rejected", [])
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
        return "mismatch", []

    monkeypatch.setattr(seed_contract, "use_seed", slow)
    # Two processes' worth of memory, so only the lock and the file keep them apart.
    openings = [runtime, _restarted(runtime)]
    threads = [threading.Thread(target=one._take_seed) for one in openings]
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
    assert use_seed(seed, job, _source(tmp_path), tmp_path / "out") == ("used", [])
    assert (tmp_path / "out" / "contract.json").read_bytes() == seed.read_bytes()

    # A traceback that can't be printed never turns a failure into a crash.
    def fails(destination, source, job):
        raise RuntimeError("boom-7")

    monkeypatch.setattr(cli, "after_contract", fails)
    again = tmp_path / "again"
    assert use_seed(seed, job, _source(tmp_path), again) == ("setup_failed", [])
    assert not (again / "contract.json").exists()
