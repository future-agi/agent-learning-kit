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
    # A listed name that resolves to an unlisted file is not read, nor is one that leaves the root.
    os.symlink(source / "notes.txt", source / "decoy.pem")
    os.symlink(outside, source / "leaving.env")
    os.symlink(tmp_path, source / "up")
    masks = source_secret_masks(source)
    assert "canary-linked-token-0014" in masks
    assert not any("canary-outside-token-0013" in mask for mask in masks)
    assert not any("plain notes" in mask for mask in masks)


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


def test_the_walk_is_bounded(tmp_path, monkeypatch):
    source = _source(tmp_path, {"big/.env": "KEY=" + "canary-big-" * 10000 + "\n", ".env": "K=canary-small-value-0018\n"})
    masks = source_secret_masks(source)
    assert "canary-small-value-0018" in masks
    assert not any("canary-big-" in mask for mask in masks)
