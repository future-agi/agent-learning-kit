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


def test_a_long_dash_run_written_by_the_agent_collapses_in_linear_time():
    import time

    from fi.alk.harness.process_diagnostics import _collapse_repeated_lines

    started = time.monotonic()
    _collapse_repeated_lines("a-" * 32000 + "\n")
    assert time.monotonic() - started < 1.0


def test_an_underscore_prefixed_hex_id_collapses_like_any_other_id():
    from fi.alk.harness.process_diagnostics import _collapse_repeated_lines

    lines = "".join(f"INFO poll req_{n:010x} state=pending\n" for n in (0x3941907E58, 0x1A2B3C4D5E))
    collapsed = _collapse_repeated_lines(lines + "ERROR root cause\n")
    assert "[earlier copies of the next line omitted: 1]" in collapsed


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


# --- listed files in the source are masked out of the logs the repair model reads ---------------

import json  # noqa: E402
import os  # noqa: E402

import pytest  # noqa: E402

from fi.alk.harness.process_diagnostics import (  # noqa: E402
    MIN_MASKED_LENGTH,
    retained_process_evidence,
    source_secret_masks,
)

_PEM_LINES = [
    "-----BEGIN PRIVATE KEY-----",
    "CANARYKEYLINEONEabcdefghijklmnop0123",
    "CANARYKEYLINETWOqrstuvwxyz9876543210",
    "-----END PRIVATE KEY-----",
]


def _evidence(tmp_path, source, log_text):
    logs = tmp_path / "process-logs"
    logs.mkdir(exist_ok=True)
    (logs / "worlds-w0-agent-" f"{'a' * 32}.log").write_text(log_text)
    return retained_process_evidence(
        logs, set(), {"job_key": "unrelated-job-value"}, masks=source_secret_masks(source)
    )


def _source(tmp_path, files):
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    for name, text in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return source


@pytest.mark.parametrize(
    "line, leaked",
    [
        ('API_TOKEN="canary-token-quoted-0001"', "canary-token-quoted-0001"),
        ("API_TOKEN='canary-token-single-0002'", "canary-token-single-0002"),
        ("export API_TOKEN=canary-token-export-0003", "canary-token-export-0003"),
        ("export API_TOKEN='canary-token-both-0004'", "canary-token-both-0004"),
        ("API_TOKEN=canary-token-comment-0005 # prod key", "canary-token-comment-0005"),
        ('API_TOKEN="canary-token-qcomment-0006"  # prod', "canary-token-qcomment-0006"),
        ("API_TOKEN=canary-token-spaces-0007   ", "canary-token-spaces-0007"),
        ("api_token: canary-token-colon-0008", "canary-token-colon-0008"),
    ],
)
def test_env_style_values_are_masked_from_process_logs(tmp_path, line, leaked):
    source = _source(tmp_path, {".env": f"PORT=8080\n{line}\n"})
    text = _evidence(tmp_path, source, f"starting with key {leaked} ok\nlistening on 8080\n")
    assert leaked not in text
    assert "<redacted>" in text
    assert "listening on 8080" in text


def test_the_raw_value_as_written_is_masked_too(tmp_path):
    source = _source(tmp_path, {".env": 'API_TOKEN="canary-raw-token-0009" # note\n'})
    text = _evidence(tmp_path, source, 'got "canary-raw-token-0009" # note\n')
    assert "canary-raw-token-0009" not in text


def test_a_pem_printed_whole_is_masked(tmp_path):
    pem = "\n".join(_PEM_LINES) + "\n"
    source = _source(tmp_path, {"deploy/service.pem": pem})
    text = _evidence(tmp_path, source, "key dump:\n" + pem + "done\n")
    assert "CANARYKEYLINE" not in text
    assert "done" in text


def test_a_pem_printed_line_by_line_is_masked(tmp_path):
    source = _source(tmp_path, {"deploy/service.pem": "\n".join(_PEM_LINES) + "\n"})
    log = "line1: " + _PEM_LINES[1] + "\nline2 " + _PEM_LINES[2] + "\n"
    text = _evidence(tmp_path, source, log)
    assert "CANARYKEYLINE" not in text


def test_a_json_key_file_is_masked_whole_and_by_line(tmp_path):
    body = '{\n  "private_key": "canary-json-private-key-0010",\n  "client": "canary-json-client-0011"\n}\n'
    source = _source(tmp_path, {"svc-key.json": body})
    text = _evidence(
        tmp_path, source, 'x "private_key": "canary-json-private-key-0010", y canary-json-client-0011'
    )
    assert "canary-json-private-key-0010" not in text
    assert "canary-json-client-0011" not in text


def test_a_value_under_the_minimum_is_left_alone(tmp_path):
    assert MIN_MASKED_LENGTH == 10
    source = _source(
        tmp_path,
        {".env": "ENABLED=1\nDEBUG=true\nPORT=8080\nHOST=localhost\nDB=127.0.0.1\nSECRET=canary-long-value-0012\n"},
    )
    log = "enabled 1 debug true port 8080 host localhost db 127.0.0.1 canary-long-value-0012\n"
    text = _evidence(tmp_path, source, log)
    assert "enabled 1 debug true port 8080 host localhost db 127.0.0.1" in text
    assert "canary-long-value-0012" not in text
    assert "1" not in source_secret_masks(source) and "localhost" not in source_secret_masks(source)


def test_a_listed_file_reached_through_a_link_is_judged_where_it_resolves(tmp_path):
    outside = tmp_path / "outside.env"
    outside.write_text("API_TOKEN=canary-outside-token-0013\n")
    source = _source(
        tmp_path,
        {
            "real/.env": "API_TOKEN=canary-linked-token-0014\n",
            "notes.txt": "plain notes that are not on the list at all\n",
        },
    )
    # A harmless name that resolves to a listed file inside the root is read as that file.
    os.symlink(source / "real" / ".env", source / "settings.txt")
    # A link that leaves the root is not read. (A listed name that links to an unlisted file inside
    # the root is read: see test_a_listed_name_that_links_to_an_unlisted_file_is_masked.)
    os.symlink(outside, source / "leaving.env")
    os.symlink(tmp_path, source / "up")
    masks = source_secret_masks(source)
    assert "canary-linked-token-0014" in masks
    assert not any("canary-outside-token-0013" in mask for mask in masks)


def test_a_file_not_on_the_list_is_left_alone(tmp_path):
    source = _source(
        tmp_path,
        {
            "app.py": "TOKEN = 'canary-source-code-value-0015'\n",
            "config.yaml": "token: canary-yaml-value-0016\n",
        },
    )
    text = _evidence(tmp_path, source, "canary-source-code-value-0015 canary-yaml-value-0016\n")
    assert "canary-source-code-value-0015" in text and "canary-yaml-value-0016" in text


def test_an_ordinary_log_reads_normally(tmp_path):
    source = _source(tmp_path, {".env": "PORT=8080\nAPI_TOKEN=canary-ordinary-token-0017\n"})
    log = "INFO server booting\nWARNING db slow\nTraceback: ImportError: no module named foo\n"
    text = _evidence(tmp_path, source, log)
    assert log.strip() in text
    assert "<redacted>" not in text


def test_a_listed_file_over_the_size_bound_raises(tmp_path):
    source = _source(tmp_path, {"big/.env": "KEY=" + "canary-big-" * 10000 + "\n", ".env": "K=canary-small-value-0018\n"})
    with pytest.raises(OSError):
        source_secret_masks(source)


def test_a_state_file_over_the_size_bound_is_not_skipped(tmp_path):
    source = _source(tmp_path, {"infra/terraform.tfstate": '{"v": "canary-tfstate-value-0201", "pad": "' + "x" * 70000 + '"}'})
    with pytest.raises(OSError):
        source_secret_masks(source)


def test_listed_files_past_the_total_bound_raise(tmp_path, monkeypatch):
    from fi.alk.harness import process_diagnostics

    monkeypatch.setattr(process_diagnostics, "_MAX_MASKED_TOTAL_BYTES", 100)
    source = _source(tmp_path, {"a/.env": "K=canary-total-value-0202\n" * 3, "b/.env": "K=canary-total-value-0203\n" * 3})
    with pytest.raises(OSError):
        source_secret_masks(source)


def test_a_walk_past_the_entry_bound_raises(tmp_path, monkeypatch):
    from fi.alk.harness import process_diagnostics

    monkeypatch.setattr(process_diagnostics, "_MAX_WALKED_ENTRIES", 5)
    source = _source(tmp_path, {f"data/{n}.txt": "x" for n in range(8)} | {"backend/.env": "K=canary-walk-value-0204\n"})
    with pytest.raises(OSError):
        source_secret_masks(source)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every folder")
def test_a_folder_that_cannot_be_scanned_raises(tmp_path):
    source = _source(tmp_path, {"locked/.env": "K=canary-locked-value-0205\n"})
    (source / "locked").chmod(0)
    try:
        with pytest.raises(OSError):
            source_secret_masks(source)
    finally:
        (source / "locked").chmod(0o700)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_a_listed_file_that_cannot_be_read_raises(tmp_path):
    source = _source(tmp_path, {".env": "K=canary-unreadable-value-0206\n"})
    (source / ".env").chmod(0)
    try:
        with pytest.raises(OSError):
            source_secret_masks(source)
    finally:
        (source / ".env").chmod(0o600)


def test_a_listed_name_that_links_to_an_unlisted_file_is_masked(tmp_path):
    source = _source(tmp_path, {"conf/app.txt": "API_TOKEN=canary-linked-name-0207\nPORT=8080\n"})
    os.symlink(source / "conf" / "app.txt", source / ".env")
    text = _evidence(tmp_path, source, "token canary-linked-name-0207 on 8080\n")
    assert "canary-linked-name-0207" not in text and "on 8080" in text


def test_an_unlisted_name_that_links_to_a_listed_file_is_still_masked(tmp_path):
    source = _source(tmp_path, {"real/.env": "API_TOKEN=canary-linked-target-0208\n"})
    os.symlink(source / "real" / ".env", source / "settings.txt")
    assert "canary-linked-target-0208" in source_secret_masks(source)


def test_an_escaped_pem_in_an_env_value_is_masked_once_expanded(tmp_path):
    body = "\\n".join(["-----BEGIN PRIVATE KEY-----", "CANARYESCAPEDLINEONEabcdefghijkl", "CANARYESCAPEDLINETWOmnopqrstuvwx", "-----END PRIVATE KEY-----"])
    source = _source(tmp_path, {".env": f'FIREBASE_PRIVATE_KEY="{body}"\n'})
    expanded = body.replace("\\n", "\n")
    log = "key:\n" + expanded + "\nline " + "CANARYESCAPEDLINETWOmnopqrstuvwx" + "\ndone\n"
    text = _evidence(tmp_path, source, log)
    assert "CANARYESCAPED" not in text and "done" in text
    (tmp_path / "crlf").mkdir()
    (tmp_path / "tab").mkdir()
    crlf = _source(tmp_path / "crlf", {".env": 'SERVICE_KEY="CANARYCRLFLINEONE0123456789\\r\\nCANARYCRLFLINETWO0123456789"\n'})
    assert "CANARYCRLFLINETWO0123456789" in source_secret_masks(crlf)
    tabbed = _source(tmp_path / "tab", {".env": 'SERVICE_KEY="CANARYTABPARTONE01234\\tCANARYTABPARTTWO01234"\n'})
    assert "CANARYTABPARTONE01234\tCANARYTABPARTTWO01234" in source_secret_masks(tabbed)


def test_an_escaped_quote_in_a_quoted_value_is_handled(tmp_path):
    source = _source(tmp_path, {".env": 'K="ab\\"canary-escaped-quote-0209"\nPORT=8080\n'})
    log = 'got ab"canary-escaped-quote-0209 and canary-escaped-quote-0209 on 8080\n'
    text = _evidence(tmp_path, source, log)
    assert "canary-escaped-quote-0209" not in text and "on 8080" in text


def test_a_json_string_that_is_a_multi_line_pem_is_masked_whole_and_by_line(tmp_path):
    pem = "\n".join(_PEM_LINES)
    source = _source(tmp_path, {"svc-key.json": json.dumps({"private_key": pem, "name": "svc"})})
    masks = source_secret_masks(source)
    assert pem in masks
    assert _PEM_LINES[1] in masks and _PEM_LINES[2] in masks
    text = _evidence(tmp_path, source, "line " + _PEM_LINES[2] + " alone\n")
    assert "CANARYKEYLINETWO" not in text


@pytest.mark.parametrize(
    "name, content, printed",
    [
        (".pgpass", "db.internal:5432:app:appuser:canary-pgpass-field-0210\n", "canary-pgpass-field-0210"),
        (".netrc", "machine host.internal login appuser password canary-netrc-field-0211\n", "canary-netrc-field-0211"),
        (".git-credentials", "https://appuser:canary-gitcred-field-0212@git.internal\n", "canary-gitcred-field-0212"),
    ],
)
def test_a_credential_field_printed_alone_is_masked(tmp_path, name, content, printed):
    source = _source(tmp_path, {name: content})
    text = _evidence(tmp_path, source, f"auth failed with {printed} for appuser\n")
    assert printed not in text


def test_a_value_straddling_the_evidence_cut_is_masked_before_it_is_cut(tmp_path):
    value = "canary-straddle-value-0213"
    source = _source(tmp_path, {".env": f"API_TOKEN={value}\n"})
    log = "x" * (12000 - 12) + value + "\n"
    text = _evidence(tmp_path, source, log)
    assert value[:10] not in text and len(text) <= 12000


# --- a repair needs the model name, the base URL and the environment name to stay readable -------

_TYPICAL_ENV = (
    "MODEL=gpt-4o-mini\nOPENAI_BASE_URL=https://api.openai.com/v1\nAPP_ENV=production\n"
    "SERVER_URL=http://localhost:8000\nREGION=us-east-1\nOPENAI_API_KEY=canary-sk-proj-Ab12Cd34Ef56\n"
)
_TYPICAL_LOG = (
    "INFO:     Uvicorn running on http://localhost:8000 (Press CTRL+C to quit)\n"
    "INFO:     Application startup complete in production mode\n"
    "openai.NotFoundError: The model gpt-4o-mini does not exist (base_url=https://api.openai.com/v1)\n"
    "KeyError: 'OPENAI_API_KEY' and key canary-sk-proj-Ab12Cd34Ef56 leaked\n"
)


def test_a_typical_env_masks_the_key_and_leaves_the_diagnostics_readable(tmp_path):
    source = _source(
        tmp_path, {".env": _TYPICAL_ENV, ".env.example": _TYPICAL_ENV.replace("canary-sk-proj-Ab12Cd34Ef56", "your-key-here-1234")}
    )
    text = _evidence(tmp_path, source, _TYPICAL_LOG)
    for readable in ("gpt-4o-mini", "https://api.openai.com/v1", "http://localhost:8000", "production"):
        assert readable in text
    assert "The model gpt-4o-mini does not exist (base_url=https://api.openai.com/v1)" in text
    assert "canary-sk-proj-Ab12Cd34Ef56" not in text
    assert "key <redacted> leaked" in text


@pytest.mark.parametrize("name", [".env.example", ".env.sample", ".env.template", ".env.dist", "prod.env.example", "creds.json.example"])
def test_a_template_file_builds_no_masks(tmp_path, name):
    source = _source(tmp_path, {name: "API_TOKEN=canary-template-token-0101\nDB_PASSWORD=canary-template-pass-0102\n"})
    text = _evidence(tmp_path, source, "canary-template-token-0101 canary-template-pass-0102\n")
    assert source_secret_masks(source) == frozenset()
    assert "canary-template-token-0101" in text and "canary-template-pass-0102" in text


def test_a_secret_named_setting_with_a_plain_word_is_masked(tmp_path):
    source = _source(tmp_path, {".env": "DB_PASSWORD=correcthorsebattery\nAPIKEY=plainwordsecret\nMODEL=plainwordmodel\n"})
    text = _evidence(tmp_path, source, "pw correcthorsebattery key plainwordsecret model plainwordmodel\n")
    assert "correcthorsebattery" not in text and "plainwordsecret" not in text
    assert "model plainwordmodel" in text


def test_a_token_like_value_under_a_plain_name_is_masked(tmp_path):
    source = _source(
        tmp_path,
        {".env": "SESSION_ID=a1b2c3d4e5f6a7b8c9d0\nBLOB=" + "AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGh" + "\nHEX=" + "0" * 32 + "\n"},
    )
    log = "id a1b2c3d4e5f6a7b8c9d0 blob AbCdEfGhIjKlMnOpQrStUvWxYzAbCdEfGh hex " + "0" * 32 + "\n"
    text = _evidence(tmp_path, source, log)
    assert "a1b2c3d4e5f6a7b8c9d0" not in text and "AbCdEfGhIjKl" not in text and "0" * 32 not in text


@pytest.mark.parametrize(
    "value",
    ["postgres://app:canary-pw-0103@db.internal:5432/app", "https://api.example.com/v1?sig=canary0104"],
)
def test_a_url_with_userinfo_or_a_query_is_masked(tmp_path, value):
    source = _source(tmp_path, {".env": f"BACKEND_URL={value}\n"})
    text = _evidence(tmp_path, source, f"connecting to {value} failed\n")
    assert value not in text and "canary" not in text


_SLACK = "https://hooks.slack.com/services/T0FAKE1234/B0FAKE5678/fakeWebhookSecretValue24"
_TELEGRAM = (
    "https://api.telegram.org/bot1234567890:AAHfakeTelegramToken0123456789abcde/sendMessage"
)
_DISCORD = (
    "https://discord.com/api/webhooks/1234567890123/"
    "fake-Discord_Webhook_Token_Value_abcdefghijkl"
)


@pytest.mark.parametrize(
    "line,url",
    [
        (f"SLACK_WEBHOOK_URL={_SLACK}", _SLACK),
        (f"DISCORD_WEBHOOK={_DISCORD}", _DISCORD),
        # Under a name with no secret part: the token in the URL's path still marks it.
        (f"NOTIFY_TARGET={_SLACK}", _SLACK),
        # No webhook marker and no secret name: only the token's shape in the path marks it.
        (f"NOTIFY_BOT={_TELEGRAM}", _TELEGRAM),
    ],
    ids=["slack", "discord", "plain-name", "telegram-path-token"],
)
def test_a_webhook_url_with_its_token_in_the_path_is_masked(tmp_path, line, url):
    source = _source(tmp_path, {".env": line + "\nOPENAI_API_KEY=sk-live-fakefakefake123\n"})
    log = f'HTTP Request: POST {url} "HTTP/1.1 404 Not Found"\n'
    text = _evidence(tmp_path, source, log)
    assert url not in text
    assert "fakeWebhookSecretValue24" not in text and "fake-Discord_Webhook" not in text


@pytest.mark.parametrize(
    "line,logged,token",
    [
        # urllib3 at DEBUG logs the path apart from the host.
        (
            f"SLACK_WEBHOOK_URL={_SLACK}",
            'https://hooks.slack.com:443 "POST /services/T0FAKE1234/B0FAKE5678/fakeWebhookSecretValue24 HTTP/1.1" 404',
            "fakeWebhookSecretValue24",
        ),
        # An access log prints only the path.
        (
            f"NOTIFY_TARGET={_DISCORD}",
            "POST /api/webhooks/1234567890123/fake-Discord_Webhook_Token_Value_abcdefghijkl 404",
            "fake-Discord_Webhook_Token_Value_abcdefghijkl",
        ),
        # httpx lowercases the host and drops the default port before it logs.
        (
            "SLACK_WEBHOOK_URL=https://Hooks.Slack.com/services/T0/B0/fakeWebhookSecretValue24",
            'HTTP Request: POST https://hooks.slack.com/services/T0/B0/fakeWebhookSecretValue24 "404"',
            "fakeWebhookSecretValue24",
        ),
        (
            "DISCORD_WEBHOOK=https://discord.com:443/api/webhooks/1/fake-Discord_Webhook_Token_Value_abcdefghijkl",
            'HTTP Request: POST https://discord.com/api/webhooks/1/fake-Discord_Webhook_Token_Value_abcdefghijkl "404"',
            "fake-Discord_Webhook_Token_Value_abcdefghijkl",
        ),
        # A letters-only token under a webhook name: the name marks it.
        (
            "SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T0/B0/abcdefghijklmnopqrstuvwx",
            "POST /services/T0/B0/abcdefghijklmnopqrstuvwx 404",
            "abcdefghijklmnopqrstuvwx",
        ),
        # urllib3 prints the query percent-encoded: an Azure SAS sig ends in %3D.
        (
            "BLOB_URL=https://acct.blob.core.windows.net/c?sv=2022-11-02&sp=r&sig=AbCd%2BEfGh%2FIjKlMnOpQrStUvWxYz0123456789ab%3D",
            'https://acct.blob.core.windows.net:443 "GET /c?sv=2022-11-02&sp=r&sig=AbCd%2BEfGh%2FIjKlMnOpQrStUvWxYz0123456789ab%3D HTTP/1.1" 200',
            "AbCd%2BEfGh",
        ),
        # A token used as the URL's username.
        (
            "REPO=https://ghp_fakeGithubToken0123456789abcd@github.com/o/r.git",
            "cloning from ghp_fakeGithubToken0123456789abcd@github.com/o/r.git",
            "ghp_fakeGithubToken0123456789abcd",
        ),
        # A token in the fragment.
        (
            "CALLBACK=https://app.example.test/cb#access_token=fakeFragmentToken12345",
            "redirect to https://app.example.test/cb#access_token=fakeFragmentToken12345",
            "fakeFragmentToken12345",
        ),
    ],
    ids=[
        "urllib3-path", "path-only", "host-case", "default-port", "letters-under-name",
        "sas-encoded", "username-token", "fragment",
    ],
)
def test_a_secret_urls_token_is_masked_however_the_log_prints_it(tmp_path, line, logged, token):
    source = _source(tmp_path, {".env": line + "\n"})
    text = _evidence(tmp_path, source, logged + "\n")
    assert token not in text


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1/chat/completions",
        "https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct",
        "https://api.openai.com/v1/models/gpt-4o-mini-2024-07-18",
        "https://generativelanguage.googleapis.com/v1/models/gemini-2.0-flash-001",
        "https://aiplatform.googleapis.com/v1/projects/my-project-123456/locations/us-central1",
        "https://api-inference.huggingface.co/models/meta-llama/Meta-Llama-3-8B-Instruct",
        "https://api.anthropic.com/v1/models/claude-3-5-sonnet-20241022",
        "https://api.anthropic.com/v1/models/claude-sonnet-4-20250514",
        "https://huggingface.co/deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",
        "https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8",
    ],
    ids=[
        "plain", "hf-model", "openai-model", "gemini-model", "vertex-project",
        "meta-llama-3", "claude-3-5", "claude-sonnet-4", "deepseek", "llama-4-maverick",
    ],
)
def test_a_url_with_a_model_or_plain_path_is_left_alone(tmp_path, url):
    # A model-not-found 404 names the model's URL; the repair has to read it.
    source = _source(tmp_path, {".env": f"MODEL_URL={url}\n"})
    text = _evidence(tmp_path, source, f"calling {url}\n")
    assert url in text


def test_ordinary_query_values_and_usernames_stay_readable(tmp_path):
    # Only a value with a secret's shape, or under a secret parameter name, is masked on its own.
    source = _source(
        tmp_path,
        {
            ".env": "FLAGS_URL=https://flags.example.com/api/v1?environment=production&format=json\n"
            "DATABASE_URL=postgresql://application:hunter2pw-longer@db:5432/agent?sslmode=verify-full\n"
        },
    )
    line = "starting in production mode; connecting as application with sslmode verify-full\n"
    text = _evidence(tmp_path, source, line)
    assert "production" in text and "application" in text and "verify-full" in text
    assert "hunter2pw-longer" not in _evidence(tmp_path, source, "password hunter2pw-longer\n")


def test_a_letters_only_webhook_token_under_a_plain_name_is_masked(tmp_path):
    url = "https://hooks.slack.com/services/T0FAKE1234/B0FAKE5678/FakeWebhookSecretValueAbCd"
    source = _source(tmp_path, {".env": f"NOTIFY_TARGET={url}\n"})
    text = _evidence(tmp_path, source, f"POST {url} 404\n")
    assert "FakeWebhookSecretValueAbCd" not in text


def test_a_hooks_path_setting_is_not_taken_for_a_secret(tmp_path):
    source = _source(tmp_path, {".env": "GIT_HOOKS_PATH=.githooks/shared-local\n"})
    text = _evidence(tmp_path, source, "hooks from .githooks/shared-local\n")
    assert ".githooks/shared-local" in text


def test_a_webhook_split_over_short_segments_is_masked(tmp_path):
    url = "https://hooks.zapier.com/hooks/catch/1234567/ab9cdef1/"
    source = _source(tmp_path, {".env": f"NOTIFY_TARGET={url}\n"})
    text = _evidence(tmp_path, source, "POST /hooks/catch/1234567/ab9cdef1/ 404\n")
    assert "1234567/ab9cdef1" not in text


_CERT = (
    "-----BEGIN CERTIFICATE-----\n" + ("MIIBszCCAVmgAwIBAgIUQ0FOQVJZ" * 2 + "\n") * 20
    + "-----END CERTIFICATE-----\n"
)


@pytest.mark.parametrize(
    "path,body",
    [
        ("certs/cacert.pem", _CERT * 60),
        ("models/tokenizer.json", '{"model": {"vocab": {' + ", ".join(f'"w{i}": {i}' for i in range(9000)) + "}}}"),
    ],
    ids=["ca-bundle", "tokenizer"],
)
def test_a_large_public_listed_file_does_not_withhold_the_evidence(tmp_path, path, body):
    # A CA bundle and a tokenizer hold nothing secret, so their size doesn't stop the masks.
    (tmp_path / path).parent.mkdir(parents=True)
    (tmp_path / path).write_text(body)
    (tmp_path / ".env").write_text("API_KEY=canary-public-files-0071\n")
    assert len(body.encode()) > 64 * 1024
    assert "canary-public-files-0071" in source_secret_masks(tmp_path)


def test_a_commented_ca_bundle_does_not_withhold_the_evidence(tmp_path):
    # certifi's and curl's bundles carry # comments between the certificates.
    bundle = "".join(f"# Issuer: CN=Example Root {i}\n# Label: \"Example {i}\"\n" + _CERT for i in range(60))
    (tmp_path / "package" / "certifi").mkdir(parents=True)
    (tmp_path / "package" / "certifi" / "cacert.pem").write_text(bundle)
    (tmp_path / ".env").write_text("API_KEY=canary-public-files-0072\n")
    assert len(bundle.encode()) > 64 * 1024
    assert "canary-public-files-0072" in source_secret_masks(tmp_path)


def test_a_large_pem_holding_a_private_key_still_fails_closed(tmp_path):
    (tmp_path / "certs").mkdir()
    (tmp_path / "certs" / "bundle.pem").write_text(
        _CERT * 60 + "-----BEGIN PRIVATE KEY-----\nCANARYKEY\n-----END PRIVATE KEY-----\n"
    )
    with pytest.raises(OSError):
        source_secret_masks(tmp_path)


def test_a_line_of_an_env_file_that_is_not_a_setting_is_masked_like_a_line(tmp_path):
    source = _source(
        tmp_path,
        {".env": 'PRIVATE_BLOB="-----BEGIN KEY-----\nCANARYPEMLINE0105abcdefghijklmnop\nAAAAB3NzaC1yc2EAAAADAQABAA==\n-----END KEY-----"\n'},
    )
    text = _evidence(tmp_path, source, "line CANARYPEMLINE0105abcdefghijklmnop and AAAAB3NzaC1yc2EAAAADAQABAA==\n")
    assert "CANARYPEMLINE" not in text and "AAAAB3NzaC1yc2E" not in text


def test_a_long_line_of_spaces_builds_the_masks_quickly(tmp_path):
    # One .env line just under the 64 KiB bound, mostly spaces: the masks are built on every
    # failed repair attempt, so building them must stay linear in the line's length.
    import time

    (tmp_path / ".env").write_text("A=x" + " " * 65000 + "y\nAPI_KEY=canary-key-QWERTY-12345\n")
    started = time.perf_counter()
    masks = source_secret_masks(tmp_path)
    took = time.perf_counter() - started
    assert "canary-key-QWERTY-12345" in masks
    assert took < 1.0, took


def test_retained_log_drops_a_derived_world_role_password(tmp_path):
    from fi.alk.harness import process_runtime as pr

    credentials = {
        "db": pr.EngineCredentials("harness", "superpw", world_secret="s" * 64)
    }
    derived = pr.world_role(credentials["db"], 0).password
    work = tmp_path / "work"
    log = work / "worlds" / "w0" / "agent" / "process.log"
    log.parent.mkdir(parents=True)
    log.write_text(f"connect failed postgres://alk_w0:{derived}@127.0.0.1:5432/w0\n")
    provider = ProcessRuntimeProvider(
        generic_artifact_root=tmp_path / "artifacts", secrets_path=tmp_path / "secrets"
    )
    provider._secrets_loaded = True
    provider._diagnostic_work_directory = work
    provider._diagnostic_engine_secrets = pr._engine_diagnostic_secrets(credentials, 2)
    provider._retain_process_log(log)
    text = next((tmp_path / "artifacts").rglob("*.log")).read_text()
    assert derived not in text
    assert "connect failed" in text and "@127.0.0.1:5432/w0" in text
