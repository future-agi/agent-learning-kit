"""The shared way into a reading: what it refuses, the cost it leaves, and its time limit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fi.alk.harness import reading_entry, spend
from fi.alk.harness.job import FailureDomain, HarnessFailure, HarnessJob, HarnessStage
from fi.alk.harness.reading_entry import (
    MESSAGE_LIMIT,
    READING_JOB_INVALID,
    READING_OUTPUT_NOT_EMPTY,
    ReadingMode,
    run_reading_mode,
    write_reading_cost,
    write_reading_failure,
)

_DEFAULT_SECRET_FILES = reading_entry._SECRET_FILES

READ_AGENT_VALUES = {
    "name": "read_agent",
    "time_limit_seconds": 1980.0,
    "timed_out_code": "agent_reading_timed_out",
    "timed_out_retryable": False,
    "crashed_code": "agent_reading_crashed",
}
FIND_AGENTS_VALUES = {
    "name": "find_agents",
    "time_limit_seconds": 1800.0,
    "timed_out_code": "scan_hard_limit_reached",
    "timed_out_retryable": False,
    "crashed_code": "scan_crashed",
}


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
    # The machine's own secrets files must not decide what a test sees masked.
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (tmp_path / "no-secrets.json",))
    spend._stages.clear()
    spend.journal_to(None)
    yield
    spend._stages.clear()
    spend.journal_to(None)


@pytest.fixture(params=[READ_AGENT_VALUES, FIND_AGENTS_VALUES], ids=["read_agent", "find_agents"])
def values(request):
    return request.param


def _job(source: dict | None = None) -> dict:
    return {
        "job_id": "job",
        "run_id": "run",
        "execution": "hosted",
        "source": source
        or {
            "kind": "github",
            "visibility": "public",
            "repository": "acme/agent",
            "commit_sha": "a" * 40,
        },
        "runtime": {"isolation": "dedicated_vm"},
        "agent": {"connector": "auto"},
    }


class _Work:
    """A mode's work that remembers being called; ``action`` stands in for what it does."""

    def __init__(self, code: int = 0, action=None) -> None:
        self.code = code
        self.action = action
        self.calls: list[tuple] = []

    async def __call__(self, job, source, output) -> int:
        self.calls.append((job, source, output))
        if self.action is not None:
            self.action(job, source, output)
        return self.code


def _accept(_parsed) -> None:
    return None


def _mode(values: dict, work, check_job=_accept, **overrides) -> ReadingMode:
    return ReadingMode(check_job=check_job, work=work, **{**values, **overrides})


def _run(mode: ReadingMode, root: Path, job_path: Path | None = None) -> int:
    if job_path is None:
        job_path = root / "job.json"
        job_path.write_text(json.dumps(_job()), encoding="utf-8")
    return run_reading_mode(
        mode, job_path=job_path, source=root / "source", output=root / "reading"
    )


def _failure_in(output: Path) -> dict:
    return json.loads((output / "failure.json").read_text(encoding="utf-8"))


def _cost_in(output: Path) -> dict:
    return json.loads((output / "cost.json").read_text(encoding="utf-8"))


def _shape(failure: dict) -> dict:
    return {key: failure[key] for key in ("domain", "stage", "code", "retryable")}


def _refusal(code: str = "scan_job_invalid") -> HarnessFailure:
    return HarnessFailure(
        domain=FailureDomain.PLATFORM_SYNC,
        stage=HarnessStage.UNDERSTANDING_AGENT,
        code=code,
        message="A scan reads a whole repository, not one folder of it.",
        retryable=False,
        details={"subfolder": "agents/support"},
    )


# The helper ends the process with os._exit at its limit, so those tests run it in a child.
_CHILD = r"""
import asyncio, json, logging, shutil, sys, time
config = json.loads(sys.argv[1])
sys.path[:0] = config["sys_path"]
logging.basicConfig(level=logging.INFO)
from pathlib import Path
from fi.alk.harness import reading_entry, spend
from fi.alk.harness.reading_entry import ReadingMode, run_reading_mode

reading_entry._SECRET_FILES = ()
root = Path(config["root"])


def broken(*_args, **_kwargs):
    raise RuntimeError("this writer is broken")


async def work(job, source, output):
    if config["work"] == "returns":
        return 0
    if config["work"] == "leaves_a_thread":
        (output / "agent.json").write_text('{"agent": "kept"}')
        reading_entry.write_reading_cost(output, complete=True)
        try:
            # A scan given up on by its caller: the thread itself runs on past the limit.
            await asyncio.wait_for(asyncio.to_thread(time.sleep, 30.0), 0.1)
        except TimeoutError:
            pass
        return 0
    if config["work"] in ("completes_then_blocks", "completes_then_raises"):
        spend.record("read-agent", 0.3, turns=1, models={"m"})
        reading_entry.write_reading_cost(output, complete=True)
        if config["work"] == "completes_then_raises":
            raise RuntimeError("the work broke after its last write")
    if config["work"] == "saves_then_blocks":
        # Saved, then stuck where a session's close can hang: past the limit, never returning.
        (output / "contract.json").write_text(json.dumps(config["contract"]))
    if config["work"] == "spoils_the_output_then_blocks":
        # Every write at the limit now fails, and each failure would log.
        shutil.rmtree(output)
        output.write_text("a file, not a folder")
    if config["work"] == "records_then_blocks":
        spend.record("read-agent", 0.21, turns=1, models={"m"})
    if config["work"] == "breaks_the_writers_then_blocks":
        reading_entry.write_reading_failure = broken
        reading_entry.write_reading_cost = broken
    await asyncio.to_thread(time.sleep, config["block_seconds"])
    return 0


extra = {}
if config.get("at_limit") == "read_agent":
    from fi.alk.harness import agent_reading

    extra["at_limit"] = agent_reading._at_the_limit
mode = ReadingMode(check_job=lambda job: None, work=work, **config["mode"], **extra)
(root / "started").write_text(repr(time.time()))
code = run_reading_mode(
    mode, job_path=root / "job.json", source=root / "source", output=root / "reading"
)
time.sleep(config["linger_seconds"])
sys.exit(code)
"""

_BLOCK_SECONDS = 20.0


def _run_in_a_child(root: Path, mode_values: dict, work: str, *, linger: float = 0.0, **more):
    """Returns the finished child and the seconds from the helper's start to the child's end."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "job.json").write_text(json.dumps(_job()), encoding="utf-8")
    config = {
        "sys_path": [entry for entry in sys.path if entry],
        "root": str(root),
        "mode": mode_values,
        "work": work,
        "block_seconds": _BLOCK_SECONDS,
        "linger_seconds": linger,
        **more,
    }
    env = {
        **os.environ,
        "OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9",
        "OTEL_TRACES_EXPORTER": "none",
    }
    env.pop(spend.JOURNAL_ALIAS, None)
    finished = subprocess.run(
        [sys.executable, "-c", _CHILD, json.dumps(config)],
        env=env,
        capture_output=True,
        text=True,
        timeout=_BLOCK_SECONDS + 40,
    )
    ended = time.time()
    started = float((root / "started").read_text(encoding="utf-8"))
    return finished, ended - started


@pytest.mark.parametrize("case", ["missing", "not_json", "not_a_job", "folder_on_an_upload"])
def test_an_unreadable_job_is_refused_before_any_work(tmp_path, values, case):
    job_path = tmp_path / "job.json"
    if case == "not_json":
        job_path.write_text("{not json", encoding="utf-8")
    elif case == "not_a_job":
        job_path.write_text(json.dumps({"hello": "world"}), encoding="utf-8")
    elif case == "folder_on_an_upload":
        upload = {"kind": "archive", "archive_artifact_id": "upload-1"}
        # Without the folder the same upload is a good job, so only the folder is refused.
        HarnessJob.model_validate(_job(upload))
        job_path.write_text(
            json.dumps(_job({**upload, "subfolder": "agents/support"})), encoding="utf-8"
        )
    checked: list = []
    work = _Work()

    code = _run(_mode(values, work, check_job=checked.append), tmp_path, job_path)

    output = tmp_path / "reading"
    assert code == 78
    assert _shape(_failure_in(output)) == {
        "domain": "platform_sync",
        "stage": "understanding_agent",
        "code": READING_JOB_INVALID,
        "retryable": False,
    }
    assert not (output / "cost.json").exists()
    assert checked == []
    assert work.calls == []


_TOKEN = "ghp_REVIEWSECRET1234567890abcdef"


@pytest.mark.parametrize(
    "refused",
    [
        {**_job(), "metadata": {"token": _TOKEN}},
        # pydantic's own message for this one repeats the value whole.
        {**_job(), "schema_version": _TOKEN},
        # A dict's key is in the error's location.
        {**_job(), "agent": {"connector": "auto", "secret_refs": {_TOKEN: "not-an-object"}}},
    ],
    ids=["secret_field", "schema_version", "secret_refs_key"],
)
def test_a_refused_job_leaves_none_of_its_values_anywhere(
    tmp_path, values, capfd, caplog, refused
):
    token = _TOKEN
    job_path = tmp_path / "job.json"
    job_path.write_text(json.dumps(refused), encoding="utf-8")

    with caplog.at_level("DEBUG"):
        assert _run(_mode(values, _Work()), tmp_path, job_path) == 78

    failure = (tmp_path / "reading" / "failure.json").read_text(encoding="utf-8")
    assert json.loads(failure)["code"] == READING_JOB_INVALID
    captured = capfd.readouterr()
    kept = failure + captured.out + captured.err + caplog.text
    assert not [token[at : at + 8] for at in range(len(token) - 7) if token[at : at + 8] in kept]


def test_a_job_nested_past_the_parsers_depth_is_refused(tmp_path, values):
    job_path = tmp_path / "job.json"
    job_path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    work = _Work()

    assert _run(_mode(values, work), tmp_path, job_path) == 78

    failure = _failure_in(tmp_path / "reading")
    assert (failure["code"], failure["message"]) == (
        READING_JOB_INVALID,
        "The job file could not be read as a harness job (RecursionError).",
    )
    assert work.calls == []


def test_the_modes_own_job_check_refuses_before_any_work(tmp_path, values):
    seen: list = []

    def check_job(job):
        seen.append(job)
        return _refusal()

    work = _Work()

    assert _run(_mode(values, work, check_job=check_job), tmp_path) == 78

    output = tmp_path / "reading"
    assert _failure_in(output) == _refusal().model_dump(mode="json")
    assert [type(job) for job in seen] == [HarnessJob]
    assert seen[0].job_id == "job"
    assert not (output / "cost.json").exists()
    assert work.calls == []


def test_a_job_check_that_raises_is_a_crash_before_any_work(tmp_path, values):
    def check_job(_job):
        raise KeyError("metadata")

    work = _Work()

    assert _run(_mode(values, work, check_job=check_job), tmp_path) == 1

    output = tmp_path / "reading"
    failure = _failure_in(output)
    assert _shape(failure) == {
        "domain": "infrastructure",
        "stage": "understanding_agent",
        "code": values["crashed_code"],
        "retryable": True,
    }
    assert "KeyError" in failure["message"]
    assert not (output / "cost.json").exists()
    assert work.calls == []


def test_a_non_empty_output_is_refused_and_left_alone(tmp_path, values):
    old = tmp_path / "old"
    (old / "reading").mkdir(parents=True)
    (old / "reading" / "contract.json").write_bytes(b'{"old": true}')
    stale = tmp_path / "stale"
    (stale / "reading").mkdir(parents=True)
    stale_failure = b'{"code": "from_an_earlier_run"}'
    (stale / "reading" / "failure.json").write_bytes(stale_failure)
    a_file = tmp_path / "a_file"
    a_file.mkdir()
    (a_file / "reading").write_bytes(b"not a folder")
    work = _Work()

    assert _run(_mode(values, work), old) == 78
    assert _run(_mode(values, work), stale) == 78
    assert _run(_mode(values, work), a_file) == 78

    assert (old / "reading" / "contract.json").read_bytes() == b'{"old": true}'
    assert sorted(entry.name for entry in (old / "reading").iterdir()) == [
        "contract.json",
        "failure.json",
    ]
    assert _shape(_failure_in(old / "reading")) == {
        "domain": "platform_sync",
        "stage": "understanding_agent",
        "code": READING_OUTPUT_NOT_EMPTY,
        "retryable": False,
    }
    assert [entry.name for entry in (stale / "reading").iterdir()] == ["failure.json"]
    assert (stale / "reading" / "failure.json").read_bytes() == stale_failure
    assert (a_file / "reading").read_bytes() == b"not a folder"
    assert work.calls == []


def test_cost_starts_at_zero_and_incomplete_before_the_work(tmp_path, values):
    seen: dict = {}

    def look(_job, _source, output):
        seen.update(_cost_in(output))

    assert _run(_mode(values, _Work(action=look)), tmp_path) == 0

    assert seen["total_usd"] == 0
    assert seen["unpriced_turns"] == 0
    assert seen["stages"] == []
    assert seen["complete"] is False


def test_a_failed_first_cost_write_stops_before_the_work(tmp_path, values, monkeypatch):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a folder", encoding="utf-8")
    assert write_reading_cost(blocked, complete=False) is False

    monkeypatch.setattr(reading_entry, "write_reading_cost", lambda output, *, complete: False)
    work = _Work()

    assert _run(_mode(values, work), tmp_path) == 1

    assert work.calls == []
    assert _shape(_failure_in(tmp_path / "reading")) == {
        "domain": "infrastructure",
        "stage": "understanding_agent",
        "code": values["crashed_code"],
        "retryable": True,
    }


@pytest.mark.parametrize("retryable, exit_code", [(False, 78), (True, 1)])
def test_the_time_limit_writes_its_code_and_ends_the_process(
    tmp_path, values, retryable, exit_code
):
    limit = 1.0
    mode_values = {**values, "time_limit_seconds": limit, "timed_out_retryable": retryable}

    finished, elapsed = _run_in_a_child(tmp_path, mode_values, "blocks")

    output = tmp_path / "reading"
    assert finished.returncode == exit_code, finished.stderr
    assert limit <= elapsed <= limit + 5
    assert _shape(_failure_in(output)) == {
        "domain": "infrastructure",
        "stage": "understanding_agent",
        "code": values["timed_out_code"],
        "retryable": retryable,
    }
    assert _cost_in(output)["complete"] is False
    assert "reading started at " in finished.stderr


def test_the_time_limit_ends_the_process_even_when_its_writes_fail(tmp_path):
    limit = 1.0
    mode_values = {**READ_AGENT_VALUES, "time_limit_seconds": limit}

    finished, elapsed = _run_in_a_child(tmp_path, mode_values, "breaks_the_writers_then_blocks")

    assert finished.returncode == 78, finished.stderr
    assert limit <= elapsed <= limit + 5
    # Proof the writers really raised: the limit could leave no failure of its own.
    assert not (tmp_path / "reading" / "failure.json").exists()


def test_the_time_limit_does_not_fire_after_the_work_returns(tmp_path):
    mode_values = {**READ_AGENT_VALUES, "time_limit_seconds": 1.0}

    finished, elapsed = _run_in_a_child(tmp_path, mode_values, "returns", linger=2.0)

    assert finished.returncode == 0, finished.stderr
    assert elapsed >= 2.0
    assert not (tmp_path / "reading" / "failure.json").exists()


def test_a_limit_named_by_the_environment_is_read_when_the_reading_starts(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ALK_TEST_READING_LIMIT", "0.5")
    mode_values = {
        **READ_AGENT_VALUES,
        "time_limit_seconds": 30.0,
        "limit_env": "ALK_TEST_READING_LIMIT",
        "limit_margin_seconds": 0.5,
    }

    finished, elapsed = _run_in_a_child(tmp_path, mode_values, "blocks")

    assert finished.returncode == 78, finished.stderr
    assert 1.0 <= elapsed <= 1.0 + 5
    failure = _failure_in(tmp_path / "reading")
    assert failure["code"] == "agent_reading_timed_out"
    assert "past its 1 s limit" in failure["message"]


def test_a_thread_left_after_the_work_ends_the_process_quietly_at_the_limit(tmp_path):
    mode_values = {**READ_AGENT_VALUES, "time_limit_seconds": 1.0}

    finished, elapsed = _run_in_a_child(tmp_path, mode_values, "leaves_a_thread")

    output = tmp_path / "reading"
    # The work's own code, at the limit, not after the 30 s thread; and nothing written over the
    # files the work left.
    assert finished.returncode == 0, finished.stderr
    assert 1.0 <= elapsed <= 1.0 + 5
    assert not (output / "failure.json").exists()
    assert (output / "agent.json").read_text() == '{"agent": "kept"}'
    assert _cost_in(output) == {**_empty_spend(), "complete": True}


def _empty_spend() -> dict:
    return {"schema": "futureagi.harness-spend.v1", "total_usd": 0.0, "unpriced_turns": 0, "stages": []}


def test_a_contract_saved_before_the_hard_limit_still_ends_with_its_document(tmp_path):
    (tmp_path / "source").mkdir(parents=True)
    contract = {
        "agent": "support",
        "modality": "chat",
        "real_use_cases": ["order"],
        "tools": [{"name": "add", "args": ["item_id"]}],
    }
    mode_values = {**READ_AGENT_VALUES, "time_limit_seconds": 1.0}

    finished, elapsed = _run_in_a_child(
        tmp_path, mode_values, "saves_then_blocks", contract=contract, at_limit="read_agent"
    )

    output = tmp_path / "reading"
    assert finished.returncode == 0, finished.stderr
    assert 1.0 <= elapsed <= 1.0 + 5
    assert not (output / "failure.json").exists()
    assert json.loads((output / "agent.json").read_text())["agent"]
    assert _cost_in(output)["complete"] is False


def test_a_timeout_after_a_complete_cost_marks_it_incomplete(tmp_path):
    mode_values = {**READ_AGENT_VALUES, "time_limit_seconds": 1.0}

    finished, _ = _run_in_a_child(tmp_path, mode_values, "completes_then_blocks")

    assert finished.returncode == 78, finished.stderr
    cost = _cost_in(tmp_path / "reading")
    assert (cost["total_usd"], cost["complete"]) == (0.3, False)
    assert _failure_in(tmp_path / "reading")["code"] == "agent_reading_timed_out"


def test_a_crash_after_a_complete_cost_marks_it_incomplete(tmp_path):
    finished, _ = _run_in_a_child(tmp_path, READ_AGENT_VALUES, "completes_then_raises")

    assert finished.returncode == 1, finished.stderr
    cost = _cost_in(tmp_path / "reading")
    assert (cost["total_usd"], cost["complete"]) == (0.3, False)
    assert _failure_in(tmp_path / "reading")["code"] == "agent_reading_crashed"


def test_the_way_out_at_the_limit_logs_nothing(tmp_path):
    mode_values = {**READ_AGENT_VALUES, "time_limit_seconds": 1.0}

    finished, elapsed = _run_in_a_child(tmp_path, mode_values, "spoils_the_output_then_blocks")

    assert finished.returncode == 78, finished.stderr
    assert elapsed <= 1.0 + 5
    # The writes did fail, and a write that fails logs everywhere else.
    assert (tmp_path / "reading").is_file()
    assert "reading started at " in finished.stderr
    assert "not written" not in finished.stderr


def test_a_crash_writes_its_code_with_no_secret_and_a_capped_message(tmp_path, values):
    def crash(*_args):
        raise RuntimeError("could not fetch https://user:pw@host/repo.git " + "x" * 3000)

    assert _run(_mode(values, _Work(action=crash)), tmp_path) == 1

    output = tmp_path / "reading"
    failure = _failure_in(output)
    assert _shape(failure) == {
        "domain": "infrastructure",
        "stage": "understanding_agent",
        "code": values["crashed_code"],
        "retryable": True,
    }
    assert failure["message"].startswith(
        "RuntimeError: could not fetch https://user:***@host/repo.git "
    )
    assert "pw" not in failure["message"]
    assert MESSAGE_LIMIT == 2000
    assert len(failure["message"]) == MESSAGE_LIMIT
    assert _cost_in(output)["complete"] is False

    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a folder", encoding="utf-8")
    assert write_reading_failure(_refusal(), blocked) is None


def test_a_secret_is_masked_in_the_shapes_a_trace_shows_it(tmp_path, monkeypatch, caplog):
    pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEF\n-----END PRIVATE KEY-----\n"
    credential = json.dumps({"type": "service_account", "private_key": pem})
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"GOOGLE_CREDENTIALS_JSON": credential}), encoding="utf-8")
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (secrets,))

    def crash(*_args):
        raise RuntimeError(f"bad key {pem!r}; line MIIEvQIBADANBgkqhkiG9w0BAQEF")

    with caplog.at_level("INFO", logger=reading_entry.__name__):
        assert _run(_mode(READ_AGENT_VALUES, _Work(action=crash)), tmp_path) == 1

    kept = _failure_in(tmp_path / "reading")["message"] + caplog.text
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEF" not in kept
    assert "BEGIN PRIVATE KEY" not in kept
    # The whole value is still masked first; a value under 8 characters, or in neither file, is
    # one the mask can't know.
    assert reading_entry._read_secret_values((secrets,))[0] in (credential, json.dumps(credential)[1:-1])


def test_each_reading_reads_the_secrets_again(tmp_path, monkeypatch):
    secrets = tmp_path / "secrets.json"
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (secrets,))
    for run, value in enumerate(("first-secret-value", "second-secret-value")):
        secrets.write_text(json.dumps({"KEY": value}), encoding="utf-8")
        root = tmp_path / f"run{run}"
        root.mkdir()

        def crash(*_args, said=value):
            raise RuntimeError(f"refused {said}")

        assert _run(_mode(READ_AGENT_VALUES, _Work(action=crash)), root) == 1
        assert _failure_in(root / "reading")["message"] == "RuntimeError: refused ***"


def test_a_value_from_the_secrets_file_is_masked(tmp_path, monkeypatch, values):
    target = "sk-0123456789abcdefg"
    simulator = "sim-0123456789abcdef"
    assert len(target) == len(simulator) == 20
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"TARGET_KEY": target}), encoding="utf-8")
    simulator_secrets = tmp_path / "simulator-secrets.json"
    simulator_secrets.write_text(json.dumps({"SIMULATOR_KEY": simulator}), encoding="utf-8")
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (secrets, simulator_secrets))

    def crash(*_args):
        raise RuntimeError(f"the provider refused {target} and {simulator}")

    assert _run(_mode(values, _Work(action=crash)), tmp_path) == 1

    assert _failure_in(tmp_path / "reading")["message"] == (
        "RuntimeError: the provider refused *** and ***"
    )
    assert _DEFAULT_SECRET_FILES == (
        Path("/run/futureagi/secrets.json"),
        Path("/run/futureagi/simulator-secrets.json"),
    )


@pytest.mark.parametrize("exit_code", [0, 1, 78])
def test_the_works_exit_code_is_returned(tmp_path, values, exit_code):
    assert _run(_mode(values, _Work(code=exit_code)), tmp_path) == exit_code
    assert not (tmp_path / "reading" / "failure.json").exists()


def test_a_works_own_incomplete_cost_is_kept(tmp_path, values):
    written: list[int] = []

    def account(_job, _source, output):
        spend.record("read-agent", 0.42, turns=1, models={"m"})
        assert write_reading_cost(output, complete=False)
        # Every write moves a new file into place, so a rewrite after the work would show.
        written.append((output / "cost.json").stat().st_ino)

    assert _run(_mode(values, _Work(action=account)), tmp_path) == 0

    output = tmp_path / "reading"
    assert (output / "cost.json").stat().st_ino == written[0]
    cost = _cost_in(output)
    assert cost["total_usd"] == 0.42
    assert cost["complete"] is False


def test_each_turn_after_the_first_write_lands_in_the_cost_file(tmp_path):
    output = tmp_path / "reading"
    assert write_reading_cost(output, complete=False) is True

    spend.record("read-agent", 0.25, turns=1, models={"m"})

    assert _cost_in(output) == {**spend.snapshot(), "complete": False}
    assert sorted(entry.name for entry in output.iterdir()) == ["cost.json"]


def test_cost_is_left_after_a_timeout_and_a_crash(tmp_path):
    def record_then_crash(*_args):
        spend.record("read-agent", 0.21, turns=1, models={"m"})
        raise RuntimeError("the reading broke")

    crashed = tmp_path / "crash"
    crashed.mkdir()
    assert _run(_mode(READ_AGENT_VALUES, _Work(action=record_then_crash)), crashed) == 1
    cost = _cost_in(crashed / "reading")
    assert cost["total_usd"] == 0.21
    assert cost["complete"] is False
    assert _failure_in(crashed / "reading")["code"] == "agent_reading_crashed"

    timed_out = tmp_path / "timeout"
    mode_values = {**READ_AGENT_VALUES, "time_limit_seconds": 1.0}
    finished, _ = _run_in_a_child(timed_out, mode_values, "records_then_blocks")
    assert finished.returncode == 78, finished.stderr
    cost = _cost_in(timed_out / "reading")
    assert cost["total_usd"] == 0.21
    assert cost["complete"] is False
    assert _failure_in(timed_out / "reading")["code"] == "agent_reading_timed_out"


def test_the_checks_run_in_order(tmp_path, values):
    unreadable = tmp_path / "unreadable"
    (unreadable / "reading").mkdir(parents=True)
    (unreadable / "reading" / "old.txt").write_text("old", encoding="utf-8")
    bad_job = unreadable / "job.json"
    bad_job.write_text("{not json", encoding="utf-8")
    refused = tmp_path / "refused"
    (refused / "reading").mkdir(parents=True)
    (refused / "reading" / "old.txt").write_text("old", encoding="utf-8")
    work = _Work()

    assert _run(_mode(values, work), unreadable, bad_job) == 78
    assert _run(_mode(values, work, check_job=lambda _parsed: _refusal()), refused) == 78

    assert _failure_in(unreadable / "reading")["code"] == READING_JOB_INVALID
    assert _failure_in(refused / "reading")["code"] == "scan_job_invalid"
    assert not (unreadable / "reading" / "cost.json").exists()
    assert not (refused / "reading" / "cost.json").exists()
    assert work.calls == []


def test_a_crash_keeps_its_trace_in_the_log_masked_and_out_of_the_file(
    tmp_path, monkeypatch, caplog
):
    secret = "sk-0123456789abcdefg"
    secrets = tmp_path / "secrets.json"
    secrets.write_text(json.dumps({"TARGET_KEY": secret}), encoding="utf-8")
    monkeypatch.setattr(reading_entry, "_SECRET_FILES", (secrets,))

    def crash(*_args):
        raise RuntimeError(f"the provider refused {secret}")

    with caplog.at_level("INFO", logger=reading_entry.__name__):
        assert _run(_mode(READ_AGENT_VALUES, _Work(action=crash)), tmp_path) == 1

    assert "Traceback" not in _failure_in(tmp_path / "reading")["message"]
    assert "reading started at " in caplog.text
    assert "Traceback (most recent call last)" in caplog.text
    assert "RuntimeError: the provider refused ***" in caplog.text
    assert secret not in caplog.text


def test_each_swallowed_error_is_logged_with_its_trace(tmp_path, monkeypatch, caplog):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a folder", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert write_reading_failure(_refusal(), blocked) is None
        assert write_reading_cost(blocked, complete=False) is False
        monkeypatch.setitem(sys.modules, "fi.alk.harness.hosted_entrypoint", None)
        assert reading_entry._read_secret_values((tmp_path / "unread.json",)) == ()

    traced = [
        record.getMessage()
        for record in caplog.records
        if record.levelname == "WARNING" and (record.exc_info or record.exc_text)
    ]
    for said in ("failure file not written", "spend file not written", "secret values not read"):
        assert any(said in line for line in traced), (said, traced)


def test_cost_json_keeps_the_build_keys_and_adds_complete(tmp_path):
    spend.record(
        "read-agent",
        0.125,
        turns=3,
        models={"claude-x"},
        tokens_in=1000,
        tokens_out=200,
        tokens_cached=100,
        tools={"submit_contract": 1},
        refusals={"agent_type": 1},
    )
    output = tmp_path / "reading"

    assert write_reading_cost(output, complete=True) is True

    snapshot = spend.snapshot()
    assert "complete" not in snapshot
    assert _cost_in(output) == {**snapshot, "complete": True}
    assert [entry.name for entry in output.iterdir()] == ["cost.json"]


def test_an_unpriced_turn_marks_the_cost_incomplete(tmp_path):
    spend.record("read-agent", 0.1, turns=1, models={"m"})
    spend.record("read-agent", None, turns=1, models={"m"})
    output = tmp_path / "reading"

    assert write_reading_cost(output, complete=True) is True

    cost = _cost_in(output)
    assert cost["unpriced_turns"] == 1
    assert cost["total_usd"] == 0.1
    assert cost["complete"] is False


def test_a_failed_cost_write_leaves_no_half_file(tmp_path, monkeypatch):
    output = tmp_path / "reading"
    output.mkdir()

    def refuses(*_args, **_kwargs):
        raise OSError("no space left on the device")

    monkeypatch.setattr(os, "replace", refuses)

    assert write_reading_cost(output, complete=False) is False
    assert list(output.iterdir()) == []


def test_a_builds_spend_file_gains_no_complete_key(tmp_path):
    assert write_reading_cost(tmp_path / "reading", complete=True) is True
    build = tmp_path / "authoring" / "cost.json"

    spend.journal_to(build)
    spend.record("understand-agent", 0.1, turns=1, models={"m"})

    assert json.loads(build.read_text(encoding="utf-8")) == spend.snapshot()
