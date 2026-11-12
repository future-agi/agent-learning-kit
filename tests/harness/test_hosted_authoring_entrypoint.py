from __future__ import annotations

import contextlib
import json
import os
import sys
import types
from pathlib import Path
from typing import Any
from unittest import mock

from fi.alk.harness import hosted_authoring_entrypoint as entrypoint

_NAMES_AUTHORING_DOES_NOT_USE = (
    "FI_API_KEY",
    "FI_BASE_URL",
    "FI_HARNESS_PROJECT",
    "FI_SECRET_KEY",
)


def _values_for_unused_names(prefix: str) -> dict[str, str]:
    return {name: f"{prefix}-{name.lower()}" for name in _NAMES_AUTHORING_DOES_NOT_USE}


@contextlib.contextmanager
def _process_environment_without(*names: str):
    """The process environment with `names` removed, restored afterwards: `main` writes the
    values it passes through into it."""
    with mock.patch.dict(os.environ):
        for name in names:
            os.environ.pop(name, None)
        yield


def _record_tracing_registrations(monkeypatch) -> list[dict[str, Any]]:
    """Stand in for the tracing package and record every attempt to start tracing.

    The stand-in offers everything the tracing module imports, so an attempt reaches `register`
    rather than stopping at an import error, and `register` refuses so nothing is exported.
    """
    registered: list[dict[str, Any]] = []

    def register(**kwargs: Any) -> Any:
        registered.append(kwargs)
        raise RuntimeError("tracing is not available in this test")

    package = types.ModuleType("fi_instrumentation")
    package.register = register
    for name in ("FITracer", "using_metadata", "using_session", "using_tags", "using_user"):
        setattr(package, name, lambda *args, **kwargs: None)
    kinds = types.ModuleType("fi_instrumentation.fi_types")
    kinds.ProjectType = types.SimpleNamespace(OBSERVE="observe")
    package.fi_types = kinds
    monkeypatch.setitem(sys.modules, "fi_instrumentation", package)
    monkeypatch.setitem(sys.modules, "fi_instrumentation.fi_types", kinds)
    return registered


def test_platform_simulator_values_ignore_customer_target_credentials() -> None:
    values = entrypoint._platform_simulator_values(
        {
            "ANTHROPIC_API_KEY": "customer-agent-key",
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": "customer-agent-adc",
            "SIMULATOR_GOOGLE_APPLICATION_CREDENTIALS_JSON": "platform-adc",
            "SIMULATOR_GOOGLE_CLOUD_PROJECT": "platform-project",
        }
    )

    assert values == {
        "GOOGLE_APPLICATION_CREDENTIALS_JSON": "platform-adc",
        "GOOGLE_CLOUD_PROJECT": "platform-project",
    }
    assert "ANTHROPIC_API_KEY" not in values


def test_fallback_authoring_reads_gateway_key_without_deleting_control_channel(
    tmp_path, monkeypatch
) -> None:
    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    secrets.write_text("{}", encoding="utf-8")
    gateway.write_text(
        json.dumps(
            {
                "AGENTCC_API_KEY": "platform-key",
                "AGENTCC_BASE_URL": "https://gateway.example.test",
                "ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER": "1",
                "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER": "+15551234567",
                "ALK_CAB_GUEST_POC_PIN": "7682",
                "RETELL_API_KEY": "must-not-be-forwarded",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    monkeypatch.setenv("AGENTCC_API_KEY", "pre-test-key")
    monkeypatch.setenv("AGENTCC_BASE_URL", "https://pre-test.example.test")
    monkeypatch.setattr(
        entrypoint, "authoring_main", lambda argv, *, validate_runtime: 0
    )

    assert entrypoint.main([]) == 0
    assert entrypoint.os.environ["AGENTCC_API_KEY"] == "platform-key"
    assert entrypoint.os.environ["AGENTCC_BASE_URL"] == "https://gateway.example.test"
    assert entrypoint.os.environ["ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER"] == "1"
    assert (
        entrypoint.os.environ["ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER"]
        == "+15551234567"
    )
    assert entrypoint.os.environ["ALK_CAB_GUEST_POC_PIN"] == "7682"
    assert gateway.is_file()
    assert "RETELL_API_KEY" not in entrypoint.os.environ


def test_authoring_hears_the_platform_noise_catalogue(tmp_path, monkeypatch) -> None:
    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    secrets.write_text("{}", encoding="utf-8")
    catalogue = json.dumps([{"environment": "street", "url": "https://clips.example.test/a.wav"}])
    gateway.write_text(
        json.dumps({"ALK_BACKGROUND_NOISE": "1", "ALK_BACKGROUND_NOISE_CATALOG": catalogue}),
        encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    monkeypatch.delenv("ALK_BACKGROUND_NOISE_CATALOG", raising=False)
    monkeypatch.setattr(
        entrypoint, "authoring_main", lambda argv, *, validate_runtime: 0
    )

    assert entrypoint.main([]) == 0
    assert entrypoint.os.environ["ALK_BACKGROUND_NOISE_CATALOG"] == catalogue
    assert gateway.is_file()


def test_fallback_authoring_environment_has_only_the_values_authoring_uses(
    tmp_path, monkeypatch
) -> None:
    """Neither values document adds the names authoring does not use to the process environment,
    and the ones it does use still arrive."""
    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    secrets.write_text(json.dumps(_values_for_unused_names("job")), encoding="utf-8")
    gateway.write_text(
        json.dumps(
            {
                "AGENTCC_API_KEY": "gateway-key-placeholder",
                "AGENTCC_BASE_URL": "https://gateway.example.test",
                "HARNESS_OBSERVABILITY": "off",
                **_values_for_unused_names("simulator"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    seen_by_authoring: list[list[str]] = []

    def authoring_main(argv, *, validate_runtime):
        seen_by_authoring.append(
            sorted(name for name in _NAMES_AUTHORING_DOES_NOT_USE if name in os.environ)
        )
        return 0

    monkeypatch.setattr(entrypoint, "authoring_main", authoring_main)

    with _process_environment_without(
        *_NAMES_AUTHORING_DOES_NOT_USE,
        "AGENTCC_API_KEY",
        "AGENTCC_BASE_URL",
        "HARNESS_OBSERVABILITY",
    ):
        assert entrypoint.main([]) == 0
        left_in_environment = sorted(
            name for name in _NAMES_AUTHORING_DOES_NOT_USE if name in os.environ
        )
        gateway_url = os.environ.get("AGENTCC_BASE_URL")
        tracing_switch = os.environ.get("HARNESS_OBSERVABILITY")

    assert seen_by_authoring == [[]]
    assert left_in_environment == []
    assert gateway_url == "https://gateway.example.test"
    assert tracing_switch == "off"
    assert gateway.is_file()


def test_fallback_authoring_stays_untraced_when_the_values_document_names_values_it_does_not_use(
    tmp_path, monkeypatch
) -> None:
    registered = _record_tracing_registrations(monkeypatch)
    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    job = tmp_path / "job.json"
    secrets.write_text("{}", encoding="utf-8")
    # No tracing switch at all: leaving these names out has to be enough on its own.
    gateway.write_text(
        json.dumps(
            {
                "AGENTCC_API_KEY": "gateway-key-placeholder",
                "AGENTCC_BASE_URL": "https://gateway.example.test",
                **_values_for_unused_names("simulator"),
            }
        ),
        encoding="utf-8",
    )
    # A job document gives authoring a job id, which is what opens a traced session.
    job.write_text(json.dumps({"job_id": "job-1", "run_id": "run-1"}), encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    monkeypatch.setattr(
        entrypoint, "authoring_main", lambda argv, *, validate_runtime: 0
    )
    entrypoint.observability.end()

    with _process_environment_without(
        *_NAMES_AUTHORING_DOES_NOT_USE,
        "AGENTCC_API_KEY",
        "AGENTCC_BASE_URL",
        "HARNESS_OBSERVABILITY",
    ):
        assert entrypoint.main([str(job)]) == 0
        tracing_enabled = entrypoint.observability.enabled()

    assert registered == []
    assert tracing_enabled is False


def test_fallback_authoring_still_passes_the_tracing_switch(tmp_path, monkeypatch) -> None:
    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    secrets.write_text("{}", encoding="utf-8")
    gateway.write_text(json.dumps({"HARNESS_OBSERVABILITY": "off"}), encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    monkeypatch.setattr(
        entrypoint, "authoring_main", lambda argv, *, validate_runtime: 0
    )

    with _process_environment_without("HARNESS_OBSERVABILITY"):
        assert entrypoint.main([]) == 0
        tracing_switch = os.environ.get("HARNESS_OBSERVABILITY")

    assert tracing_switch == "off"
    assert gateway.is_file()


def test_vertex_generation_region_is_not_copied_from_google_location(
    tmp_path, monkeypatch
) -> None:
    adc = json.dumps({"type": "service_account", "project_id": "p"})
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "google.json")
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    entrypoint._configure_generation_environment(
        {
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": adc,
            "GOOGLE_CLOUD_PROJECT": "p",
            "GOOGLE_CLOUD_LOCATION": "us-central1",
        }
    )
    assert entrypoint.os.environ["CLOUD_ML_REGION"] == "us-east5"
    assert entrypoint.os.environ["ANTHROPIC_VERTEX_PROJECT_ID"] == "p"


def test_explicit_claude_vertex_region_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "google.json")
    monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
    entrypoint._configure_generation_environment(
        {
            "GOOGLE_APPLICATION_CREDENTIALS_JSON": json.dumps(
                {"type": "service_account"}
            ),
            "GOOGLE_CLOUD_PROJECT": "p",
            "ANTHROPIC_VERTEX_REGION": "europe-west1",
        }
    )
    assert entrypoint.os.environ["CLOUD_ML_REGION"] == "europe-west1"


def test_exhausted_runtime_repair_returns_nonretryable_exit(tmp_path, monkeypatch):
    from fi.alk.harness.authoring_runtime_validation import RuntimeValidationError

    monkeypatch.setattr(entrypoint, "_load_values", lambda path: {})
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")

    def failed(argv, *, validate_runtime):
        assert validate_runtime is True
        raise RuntimeValidationError("environment", "seed_dependency_unresolved")

    monkeypatch.setattr(entrypoint, "authoring_main", failed)
    assert entrypoint.main([]) == 78


def test_hosted_entrypoint_passes_one_shot_provider_secret_to_authoring(
    monkeypatch, tmp_path
) -> None:
    secrets = tmp_path / "secrets.json"
    target_secrets = tmp_path / "authoring-target-secrets.json"
    secrets.write_text(
        json.dumps(
            {
                "RETELL_API_KEY": "retell-secret",
                "SIMULATOR_GOOGLE_CLOUD_PROJECT": "project",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_TARGET_SECRETS_PATH", target_secrets)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    observed = {}

    def authoring_main(argv, *, validate_runtime):
        observed["argv"] = argv
        observed["validate_runtime"] = validate_runtime
        index = argv.index("--target-secrets")
        path = Path(argv[index + 1])
        observed["values"] = json.loads(path.read_text(encoding="utf-8"))
        return 0

    monkeypatch.setattr(entrypoint, "authoring_main", authoring_main)

    assert entrypoint.main(["job.json", "--source", "source", "--output", "out"]) == 0
    assert observed == {
        "argv": [
            "job.json",
            "--source",
            "source",
            "--output",
            "out",
            "--target-secrets",
            str(target_secrets),
        ],
        "validate_runtime": True,
        "values": {"RETELL_API_KEY": "retell-secret"},
    }
    assert not target_secrets.exists()


def test_early_review_starts_on_the_saved_world_and_retries_a_failure(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from fi.alk.harness import authoring_entrypoint as entry
    from fi.alk.harness import authoring_runtime_validation as validation

    output = tmp_path / "authoring"
    (output / "scenarios" / "a").mkdir(parents=True)
    (output / "manifest.json").write_text("{}")
    (output / "contract.json").write_text(
        json.dumps({"agent": "a", "tools": [{"name": "lookup"}], "data_store": {"kind": "postgres"}})
    )
    calls = []

    async def failing(job, source, staging, review=None):
        calls.append((job.scenario_count, sorted(path.name for path in staging.iterdir())))
        raise RuntimeError("runtime could not start")

    monkeypatch.setattr(validation, "validate_once", failing)
    monkeypatch.setattr(entry.time, "sleep", lambda seconds: None)
    done = threading.Event()
    job = SimpleNamespace(scenario_count=20, model_copy=lambda update: SimpleNamespace(**update))
    worker = threading.Thread(target=entry._review_while_writing, args=(job, tmp_path, output, done))
    worker.start()
    worker.join(timeout=2)
    done.set()
    worker.join(timeout=5)

    assert len(calls) == 2
    assert calls[0] == (0, ["contract.json", "manifest.json", "scenarios"])
    note = output / "generic-harness" / "source-data-review-early-error.txt"
    assert note.read_text().count("runtime could not start") == 2


def test_early_review_never_starts_for_an_agent_without_data(tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace

    from fi.alk.harness import authoring_entrypoint as entry
    from fi.alk.harness import authoring_runtime_validation as validation

    output = tmp_path / "authoring"
    output.mkdir()
    (output / "manifest.json").write_text("{}")
    (output / "contract.json").write_text(
        json.dumps({"agent": "a", "tools": [], "real_use_cases": ["Chat"]})
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("no runtime for an agent without data")

    monkeypatch.setattr(validation, "validate_once", forbidden)
    worker = threading.Thread(
        target=entry._review_while_writing,
        args=(SimpleNamespace(scenario_count=3), tmp_path, output, threading.Event()),
    )
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert not (output / "generic-harness").exists()
