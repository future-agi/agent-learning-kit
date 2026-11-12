import asyncio
import subprocess
import sys

from fi.alk.harness.process_runtime import (
    ProcessRuntimeProvider,
    PopenProcess,
    SpawnedWorldProcess,
)


def test_close_preserves_safe_real_process_output_and_cleans_work(tmp_path):
    work = tmp_path / "work"
    log = work / "worlds" / "w0" / "agent" / "process.log"
    log.parent.mkdir(parents=True)
    with log.open("wb") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                'import sys; print("stdout marker secret123"); print("stderr marker", file=sys.stderr)',
            ],
            stdout=stream,
            stderr=stream,
        )
        process.wait(timeout=5)
    provider = ProcessRuntimeProvider(
        generic_artifact_root=tmp_path / "artifacts", secrets_path=tmp_path / "secrets"
    )
    provider._secret_values = {"api_key": "secret123"}
    provider._secrets_loaded = True
    provider._world_handles = {
        0: {"agent": SpawnedWorldProcess("agent", PopenProcess(process, log), 0, 0)}
    }
    asyncio.run(provider.close(work_directory=work))
    retained = list((tmp_path / "artifacts").rglob("*.log"))
    assert retained
    text = "\n".join(path.read_text() for path in retained)
    assert "stdout marker" in text and "stderr marker" in text
    assert "secret123" not in text
    assert not (work / "worlds").exists()
    asyncio.run(provider.close(work_directory=work))
    assert list((tmp_path / "artifacts").rglob("*.log")) == retained


def test_retention_drops_split_credentials_and_rejects_symlinks(tmp_path):
    import pytest
    from fi.alk.harness.process_diagnostics import retain_process_log

    work = tmp_path / "work"
    work.mkdir()
    log = work / "process.log"
    secret = "start" + "z" * 100000 + "finish"
    log.write_text(secret + "\nfinal output\nAuthorization: Bearer unknown\n")
    retain_process_log(
        log, work=work, destination=tmp_path / "artifacts", secrets={"token": secret}
    )
    body = next((tmp_path / "artifacts").rglob("*.log")).read_text()
    assert "final output" in body
    assert "finish" not in body and "unknown" not in body
    assert len(body.encode()) <= 32768
    log.unlink()
    log.symlink_to(tmp_path / "private")
    (tmp_path / "private").write_text("must not read")
    with pytest.raises(OSError):
        retain_process_log(
            log, work=work, destination=tmp_path / "artifacts", secrets={}
        )


def test_retention_collapses_repeated_lines_so_the_real_error_survives(tmp_path):
    from fi.alk.harness.process_diagnostics import (
        _collapse_repeated_lines,
        retain_process_log,
    )

    work = tmp_path / "work"
    work.mkdir()
    log = work / "process.log"
    # Any framework's steady chatter, alternating and stamped with changing times.
    chatter = (
        "".join(
            f"12:00:{second:02d} INFO stream {kind} not consumed\n"
            for second in range(60)
            for kind in ("byte", "text")
        )
        * 4
    )
    log.write_text(
        "starting worker\n"
        "ERROR stt: provider returned 402 Payment Required key=secret123\n"
        + chatter
        + "worker idle\n"
    )
    # A secret whose value is a digit must not split lines that differ only in digits.
    retain_process_log(
        log,
        work=work,
        destination=tmp_path / "artifacts",
        secrets={"k": "secret123", "n": "1"},
    )
    body = next((tmp_path / "artifacts").rglob("*.log")).read_text()
    assert "provider returned 402 Payment Required" in body
    assert "secret123" not in body
    assert body.endswith(
        "[earlier copies of the next line omitted: 239]\n"
        "<redacted>2:00:59 INFO stream byte not consumed\n"
        "[earlier copies of the next line omitted: 239]\n"
        "<redacted>2:00:59 INFO stream text not consumed\n"
        "worker idle\n"
    )
    # A repeated real error stays, with its own count; a line that does not repeat is never dropped.
    assert _collapse_repeated_lines("error 402\nb\nerror 402\nc\n") == (
        "b\n[earlier copies of the next line omitted: 1]\nerror 402\nc\n"
    )


def test_collapsing_keeps_each_traceback_whole_and_its_own_error():
    from fi.alk.harness.process_diagnostics import _collapse_repeated_lines

    frames = 'Traceback (most recent call last):\n  File "/app/agent.py", line 10, in run\n    speak()\n'
    log = (
        "INFO start\n"
        + frames
        + "HTTPStatusError: 402 Payment Required\n"
        + "INFO poll 1\n"
        + frames
        + "RuntimeError: session closed\n"
        + "INFO poll 2\n"
        + frames
        + "RuntimeError: session closed\n"
    )
    assert _collapse_repeated_lines(log) == (
        "INFO start\n"
        + frames
        + "HTTPStatusError: 402 Payment Required\n"
        + "[earlier copies of the next line omitted: 1]\n"
        + "INFO poll 2\n"
        + "[earlier copies of the next 4 lines omitted: 1]\n"
        + frames
        + "RuntimeError: session closed\n"
    )


def test_a_chained_traceback_is_one_record_across_its_blank_lines():
    import traceback

    from fi.alk.harness.process_diagnostics import _collapse_repeated_lines, _records

    def inner():
        raise KeyError("api_key")

    def outer(error):
        try:
            inner()
        except KeyError as cause:
            raise error from cause

    printed = []
    for error in (RuntimeError("config load failed"), ValueError("retry also failed")):
        try:
            outer(error)
        except Exception:
            printed.append(traceback.format_exc())
    assert len(_records(printed[0].splitlines(keepends=True))) == 1
    # The same cause under two different errors stays with each of them.
    collapsed = _collapse_repeated_lines(
        "INFO a\n" + printed[0] + "INFO b\n" + printed[1]
    )
    assert collapsed.count("KeyError: 'api_key'") == 2
    assert "RuntimeError: config load failed" in collapsed
    assert "ValueError: retry also failed" in collapsed


def test_records_that_differ_in_a_status_or_exit_code_are_different_errors():
    from fi.alk.harness.process_diagnostics import _collapse_repeated_lines

    for first, second in (
        (
            "ERROR stt: provider returned HTTP 402 Payment Required",
            "ERROR stt: provider returned HTTP 500 Server Error",
        ),
        ("worker exited with code 1", "worker exited with code 137"),
        (
            '{"level":"error","msg":"stt failed","status_code":402,"ts":"12:00:01"}',
            '{"level":"error","msg":"stt failed","status_code":429,"ts":"12:00:09"}',
        ),
    ):
        collapsed = _collapse_repeated_lines(f"{first}\nINFO poll\n{second}\n")
        assert first in collapsed and second in collapsed, (first, second)


def test_records_that_differ_only_in_times_ids_or_counters_still_collapse():
    from fi.alk.harness.process_diagnostics import _collapse_repeated_lines

    log = (
        '{"msg":"stt failed","status_code":402,"ts":"2026-11-09T12:00:01.123Z","req":"a1b2c3d4e5f6"}\n'
        '{"msg":"stt failed","status_code":402,"ts":"2026-11-09T12:00:09.456Z","req":"f6e5d4c3b2a1"}\n'
        "INFO poll 18342 took 0.53s\n"
        "INFO poll 18343 took 0.61s\n"
    )
    collapsed = _collapse_repeated_lines(log)
    assert collapsed.count("stt failed") == 1
    assert collapsed.count("INFO poll") == 1


def test_poll_lines_with_iso_offset_timestamps_collapse_so_the_root_error_survives(tmp_path):
    from fi.alk.harness.process_diagnostics import retain_process_log

    work = tmp_path / "work"
    work.mkdir()
    log = work / "process.log"
    # Milliseconds such as .123 sit next to a numeric offset, so they look like a bare code.
    polls = "".join(
        f"2026-11-09T12:{i // 60 % 60:02d}:{i % 60:02d}.{100 + i % 400:03d}+00:00 INFO poll ok\n"
        for i in range(500)
    )
    log.write_text("ERROR stt: provider returned HTTP 402 Payment Required\n" + polls)
    retain_process_log(log, work=work, destination=tmp_path / "artifacts", secrets={})
    body = next((tmp_path / "artifacts").rglob("*.log")).read_text()
    assert "provider returned HTTP 402 Payment Required" in body
    assert "[earlier copies of the next line omitted: 499]" in body


def test_omission_notes_that_outgrow_the_read_still_keep_the_last_root_error(tmp_path):
    import itertools
    import string

    from fi.alk.harness.process_diagnostics import retain_process_log

    work = tmp_path / "work"
    work.mkdir()
    log = work / "process.log"
    # Short lines that each repeat once: every kept line gains a longer omission note.
    words = ("".join(w) for w in itertools.product(string.ascii_lowercase, repeat=3))
    pairs = "".join(f"{word}\n{word}\n" for word in itertools.islice(words, 5000))
    log.write_text(pairs + "ERROR worker: root cause is a missing api key\n")
    assert log.stat().st_size < 48 * 1024
    retain_process_log(log, work=work, destination=tmp_path / "artifacts", secrets={})
    body = next((tmp_path / "artifacts").rglob("*.log")).read_text()
    assert body.endswith("ERROR worker: root cause is a missing api key\n")


def test_poll_lines_with_letter_and_digit_ids_collapse_so_the_root_error_survives(tmp_path):
    import random
    import string

    from fi.alk.harness.process_diagnostics import retain_process_log

    work = tmp_path / "work"
    work.mkdir()
    log = work / "process.log"
    pick = random.Random(7)
    alphabet = string.ascii_letters + string.digits
    polls = "".join(
        "INFO polling job_"
        + "".join(pick.choice(alphabet) for _ in range(15))
        + str(i % 10)
        + " state=pending\n"
        for i in range(500)
    )
    log.write_text("ERROR stt: provider returned HTTP 402 Payment Required\n" + polls)
    retain_process_log(log, work=work, destination=tmp_path / "artifacts", secrets={})
    body = next((tmp_path / "artifacts").rglob("*.log")).read_text()
    assert "provider returned HTTP 402 Payment Required" in body
    assert "[earlier copies of the next line omitted: 499]" in body


def test_short_names_with_digits_still_tell_two_errors_apart():
    from fi.alk.harness.process_diagnostics import _collapse_repeated_lines

    first = "ModuleNotFoundError: No module named 'boto3client'"
    second = "ModuleNotFoundError: No module named 's3transfer2'"
    collapsed = _collapse_repeated_lines(f"{first}\nINFO poll\n{second}\n")
    assert first in collapsed and second in collapsed


def test_a_nul_byte_in_the_log_does_not_break_collapsing(tmp_path):
    from fi.alk.harness.process_diagnostics import retain_process_log

    work = tmp_path / "work"
    work.mkdir()
    log = work / "process.log"
    log.write_bytes(
        b"binary \x00a\x00 junk\n" * 3 + b"ERROR worker: root cause is a missing api key\n"
    )
    retain_process_log(log, work=work, destination=tmp_path / "artifacts", secrets={})
    body = next((tmp_path / "artifacts").rglob("*.log")).read_text()
    assert body.endswith("ERROR worker: root cause is a missing api key\n")
    assert "[earlier copies of the next line omitted: 2]" in body


def test_retention_write_failure_does_not_prevent_cleanup(tmp_path, monkeypatch):
    from fi.alk.harness import process_diagnostics

    work = tmp_path / "work"
    log = work / "worlds" / "w0" / "agent" / "process.log"
    log.parent.mkdir(parents=True)
    log.write_text("log")
    provider = ProcessRuntimeProvider(secrets_path=tmp_path / "secrets")

    def fail(*args, **kwargs):
        raise OSError("no space")

    monkeypatch.setattr(process_diagnostics, "retain_process_log", fail)
    asyncio.run(provider.close(work_directory=work))
    assert not (work / "worlds").exists()


def test_reset_retains_generation_before_replacement(tmp_path, monkeypatch):
    from fi.alk.harness import process_runtime as pr
    from test_process_runtime import _manifest, _spawn_context

    manifest = _manifest()
    context = _spawn_context(manifest, bundle_dir=tmp_path, work_directory=tmp_path)
    provider = ProcessRuntimeProvider(
        generic_artifact_root=tmp_path / "artifacts", secrets_path=tmp_path / "secrets"
    )
    provider._diagnostic_work_directory = tmp_path
    provider._secrets_loaded = True
    from dataclasses import replace

    context = replace(context, retain_process_log=provider._retain_process_log)
    log = tmp_path / "worlds" / "w0" / "agent" / "process.log"
    log.parent.mkdir(parents=True)
    log.write_text("first generation\n")
    with log.open("ab") as stream:
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                'import sys; print("reset stderr", file=sys.stderr)',
            ],
            stdout=stream,
            stderr=stream,
        )
        process.wait(timeout=5)
    handle = SpawnedWorldProcess("agent", PopenProcess(process, log), 0, 0)

    class StoppedAfterRetention(Exception):
        pass

    def seal(*args, **kwargs):
        assert process.poll() is not None
        assert any(
            "reset stderr" in path.read_text()
            for path in (tmp_path / "artifacts").rglob("*.log")
        )
        raise StoppedAfterRetention

    monkeypatch.setattr(pr, "_seal_world_store", seal)
    import pytest

    for _ in range(2):
        with pytest.raises(StoppedAfterRetention):
            pr.reset_world(
                manifest,
                0,
                context=context,
                baseline=pr.BuildOutput(bundle_digest=manifest.digest, stores=[]),
                job_shared_handles={},
                existing_handles={"agent": handle},
            )
    assert len(list((tmp_path / "artifacts").rglob("*.log"))) == 2


def test_close_captures_output_flushed_during_termination(tmp_path):
    import time

    work = tmp_path / "work"
    log = work / "worlds" / "w0" / "agent" / "process.log"
    log.parent.mkdir(parents=True)
    script = 'import signal,sys,time; signal.signal(signal.SIGTERM, lambda *_: (print("termination marker", flush=True), sys.exit(0))); print("ready", flush=True); time.sleep(60)'
    with log.open("wb") as stream:
        process = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=stream,
            stderr=stream,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 5
        while "ready" not in log.read_text() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "ready" in log.read_text()
        provider = ProcessRuntimeProvider(secrets_path=tmp_path / "secrets")
        provider._secrets_loaded = True
        provider._world_handles = {
            0: {"agent": SpawnedWorldProcess("agent", PopenProcess(process, log), 0, 0)}
        }
        asyncio.run(provider.close(work_directory=work))
        assert process.poll() == 0
        assert any(
            "termination marker" in path.read_text()
            for path in (work / "diagnostics").rglob("*.log")
        )
        assert not (work / "worlds").exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_current_attempt_evidence_excludes_old_symlink_and_large_logs(tmp_path):
    from fi.alk.harness.process_diagnostics import retained_process_evidence

    old = tmp_path / "old.log"
    old.write_text("OLD")
    current = tmp_path / "current.log"
    current.write_text("missing declared dependency token-secret")
    (tmp_path / "link.log").symlink_to(old)
    (tmp_path / "large.log").write_text("x" * 32769)
    evidence = retained_process_evidence(tmp_path, {old}, {"key": "token-secret"})
    assert "missing declared dependency" in evidence
    assert "token-secret" not in evidence
    assert "OLD" not in evidence
    assert "large.log" not in evidence
    assert "link.log" not in evidence
    assert len(evidence) <= 12000


def test_target_evidence_excludes_build_sidecar_and_old_target_logs(tmp_path):
    from fi.alk.harness.process_diagnostics import retained_process_evidence

    token = "a" * 32
    for name, content in (
        (f"-{token}.log", "BUILD install buildpkg[extra]"),
        (f"worlds-w0-db-{token}.log", "SIDECAR install dbpkg[extra]"),
        (f"worlds-w0-web-{token}.log", "TARGET install webpkg[extra]"),
        (f"worlds-w1-web-{token}.log", "OLD TARGET"),
    ):
        (tmp_path / name).write_text(content)
    evidence = retained_process_evidence(
        tmp_path, {tmp_path / f"worlds-w1-web-{token}.log"}, {}, process_name="web"
    )
    assert "TARGET install webpkg[extra]" in evidence
    assert "BUILD" not in evidence
    assert "SIDECAR" not in evidence
    assert "OLD TARGET" not in evidence
