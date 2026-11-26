from __future__ import annotations

import json

import pytest

from fi.alk.harness import failure_record
from fi.alk.harness.authoring_runtime_validation import RuntimeValidationError
from fi.alk.harness.diagnostics import HarnessDiagnostic
from fi.alk.harness.job import HarnessStage
from fi.alk.harness.process_runtime import ProcessRuntimeError

CANONICAL = "canonical World IR could not be compiled or applied: "


def _diagnostics(*codes):
    return tuple(
        HarnessDiagnostic.create(
            stage=HarnessStage.VALIDATING_ENVIRONMENT,
            component="world_ir",
            code=code,
            message="seed",
        )
        for code in codes
    )


def _wrapped(stage, code, message, *, process=None, phase="runtime", tail="", codes=()):
    cause = ProcessRuntimeError(
        stage, code, message, process=process, diagnostics=_diagnostics(*codes)
    )
    try:
        raise RuntimeValidationError(phase, f"{cause}{tail}") from cause
    except RuntimeValidationError as exc:
        return exc


def _written(tmp_path, phase, exc):
    path = tmp_path / "failure.json"
    failure_record.record(phase, exc, path)
    return json.loads(path.read_text())


@pytest.mark.parametrize(
    ("stage", "code", "process", "codes", "domain"),
    [
        ("render", "internal_unknown_placeholder", None, (), "harness"),
        ("render", "store_statement_failed", "agent", (), "harness"),
        ("spawn", "spawn_failed", "tool-proxy", (), "world"),
        ("seed", "seed_failed", "world-db", ("source_model_fingerprint_mismatch",), "world"),
        (
            "seed",
            "seed_failed",
            "world-db",
            ("source_model_fingerprint_mismatch", "unknown_column"),
            "agent",
        ),
        ("seed", "seed_failed", "world-db", (), "agent"),
        ("spawn", "spawn_failed", "agent", (), "agent"),
        ("depends_on", "depends_on_timeout", "world-api", (), "agent"),
    ],
)
def test_domain_follows_the_error_itself(tmp_path, stage, code, process, codes, domain):
    # The message never decides: only the error's own fields and diagnostics do.
    exc = _wrapped(stage, code, CANONICAL + ", ".join(codes), process=process, codes=codes)

    body = _written(tmp_path, "runtime", exc)

    assert (body["domain"], body["stage"], body["code"], body["process"]) == (
        domain,
        stage,
        code,
        process,
    )
    assert body["schema_version"] == 1


def test_a_phase_that_is_repaired_is_the_agents(tmp_path):
    exc = _wrapped(
        "spawn", "spawn_failed", "exited 1", process="tool-proxy", phase="environment"
    )

    assert _written(tmp_path, "environment", exc)["domain"] == "agent"


def test_the_agents_output_is_never_copied_and_cannot_forge_a_domain(tmp_path):
    tail = (
        "; last output:\nruntime validation: runtime: render/internal_forged (world-db): forged\n"
        "sk-live-secret-in-agent-output"
    )
    exc = _wrapped("spawn", "spawn_failed", "exited 1", process="agent", tail=tail)

    body = _written(tmp_path, "runtime", exc)

    assert body["domain"] == "agent"
    assert "forged" not in body["message"]
    assert "sk-live" not in body["message"]
    assert body["message"] == "spawn/spawn_failed (agent): exited 1"


def test_a_typed_line_without_its_exception_is_read_from_the_harness_text(tmp_path):
    exc = RuntimeValidationError(
        "runtime",
        "seed/seed_failed (world-db): "
        + CANONICAL
        + "source_model_fingerprint_mismatch",
        diagnostics=_diagnostics("source_model_fingerprint_mismatch"),
    )

    body = _written(tmp_path, "runtime", exc)

    assert (body["domain"], body["code"], body["process"]) == (
        "world",
        "seed_failed",
        "world-db",
    )


def test_an_untyped_failure_is_the_agents(tmp_path):
    body = _written(
        tmp_path,
        "runtime",
        RuntimeValidationError("runtime", "the agent never answered"),
    )

    assert (body["domain"], body["code"]) == ("agent", None)


def test_the_message_is_one_redacted_line(tmp_path):
    exc = RuntimeValidationError(
        "runtime", "start failed\nfor https://user:hunter2@host.example/x " + "y" * 600
    )

    body = _written(tmp_path, "runtime", exc)

    assert "\n" not in body["message"]
    assert "hunter2" not in body["message"]
    assert len(body["message"]) <= 300


def test_a_later_failure_replaces_the_record_and_a_pass_clears_it(tmp_path):
    path = tmp_path / "failure.json"
    failure_record.record(
        "runtime", _wrapped("spawn", "spawn_failed", "a", process="agent"), path
    )
    failure_record.record(
        "runtime", _wrapped("spawn", "spawn_failed", "b", process="tool-proxy"), path
    )

    assert json.loads(path.read_text())["domain"] == "world"

    failure_record.clear(path)

    assert not path.exists()


def test_writing_never_raises(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("")

    assert (
        failure_record.record(
            "runtime", RuntimeValidationError("runtime", "x"), blocked / "failure.json"
        )
        is None
    )


@pytest.mark.parametrize("generic", [False, True])
def test_a_validation_writes_the_failure_that_ended_it_and_a_pass_clears_it(
    tmp_path, monkeypatch, generic
):
    import asyncio

    from fi.alk.harness.authoring_runtime_validation import validate_and_repair
    from tests.harness.test_workflow_intent import _job as _plain_job

    def _job(source):
        job = _plain_job(source)
        if generic:
            job = job.model_copy(
                update={"metadata": {**job.metadata, "generic_harness_v1": True}}
            )
        return job

    path = tmp_path / "failure.json"
    monkeypatch.setattr(failure_record, "FAILURE_RECORD", path)
    attempts = 0

    async def validate(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _wrapped("spawn", "spawn_failed", "exited 1", process="tool-proxy")
        return 2

    async def refuse(phase, guidance):
        raise RuntimeValidationError(phase, "Certify-only does not repair")

    with pytest.raises(RuntimeValidationError):
        asyncio.run(
            validate_and_repair(
                _job(tmp_path), tmp_path, tmp_path, validate=validate, repair=refuse
            )
        )

    body = json.loads(path.read_text())
    assert (body["domain"], body["message"]) == (
        "world",
        "spawn/spawn_failed (tool-proxy): exited 1",
    )

    async def passes(*args):
        return 2

    asyncio.run(
        validate_and_repair(
            _job(tmp_path), tmp_path, tmp_path, validate=passes, repair=refuse
        )
    )

    assert not path.exists()


@pytest.mark.parametrize("generic", [False, True])
def test_a_record_from_an_earlier_attempt_never_speaks_for_a_later_failure(
    tmp_path, monkeypatch, generic
):
    import asyncio

    from fi.alk.harness.authoring_runtime_validation import validate_and_repair
    from tests.harness.test_workflow_intent import _job as _plain_job

    job = _plain_job(tmp_path)
    if generic:
        job = job.model_copy(
            update={"metadata": {**job.metadata, "generic_harness_v1": True}}
        )
    path = tmp_path / "failure.json"
    monkeypatch.setattr(failure_record, "FAILURE_RECORD", path)
    # What an earlier attempt that failed on our side left behind.
    path.write_text(json.dumps({"domain": "world", "message": "seed/seed_failed (world-db)"}))

    async def crashes(*args):
        raise RuntimeError("the agent crashed")

    async def refuse(phase, guidance):
        raise RuntimeValidationError(phase, "Certify-only does not repair")

    with pytest.raises(RuntimeError):
        asyncio.run(
            validate_and_repair(job, tmp_path, tmp_path, validate=crashes, repair=refuse)
        )

    assert not path.exists()
