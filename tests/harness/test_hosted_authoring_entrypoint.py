from __future__ import annotations

import json
from pathlib import Path

import os

import pytest

from fi.alk.harness import hosted_authoring_entrypoint as entrypoint


@pytest.fixture(autouse=True)
def _restored_environment():
    # main() writes os.environ directly (the generation values and the controller mark). Without
    # this, the mark would stay set for every later file in the same pytest session, and turn the
    # local CLI's .env path into the controller's.
    saved = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(saved)


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
    monkeypatch.setattr(entrypoint, "_FAILURE_CAUSE_PATH", tmp_path / "cause.json")
    assert entrypoint.main([]) == 78
    assert json.loads((tmp_path / "cause.json").read_text()) == {"cause": None}


@pytest.mark.parametrize(
    "cause",
    ["agent_exited_before_ready", "dependency_not_ready_in_time"],
)
def test_runtime_validation_exit_records_only_the_closed_cause(
    tmp_path, monkeypatch, cause
):
    from fi.alk.harness.authoring_runtime_validation import RuntimeValidationError
    from fi.alk.harness.job import FailureCause

    monkeypatch.setattr(entrypoint, "_load_values", lambda path: {})
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    cause_path = tmp_path / "cause.json"
    monkeypatch.setattr(entrypoint, "_FAILURE_CAUSE_PATH", cause_path)

    def failed(argv, *, validate_runtime):
        raise RuntimeValidationError(
            "environment",
            "agent: exited before started_check passed: RuntimeError: HELD-BACK CASE",
            cause=FailureCause(cause),
        )

    monkeypatch.setattr(entrypoint, "authoring_main", failed)
    assert entrypoint.main([]) == 78
    assert json.loads(cause_path.read_text()) == {"cause": cause}


def test_runtime_validation_exit_replaces_what_the_agent_left_at_the_cause_path(
    tmp_path, monkeypatch
):
    from fi.alk.harness.authoring_runtime_validation import RuntimeValidationError

    monkeypatch.setattr(entrypoint, "_load_values", lambda path: {})
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    elsewhere = tmp_path / "elsewhere.json"
    elsewhere.write_text("untouched")
    cause_path = tmp_path / "cause.json"
    # A process of the agent planted a link, hoping for a write through it.
    cause_path.symlink_to(elsewhere)
    monkeypatch.setattr(entrypoint, "_FAILURE_CAUSE_PATH", cause_path)

    def failed(argv, *, validate_runtime):
        raise RuntimeValidationError("environment", "invalid")

    monkeypatch.setattr(entrypoint, "authoring_main", failed)
    assert entrypoint.main([]) == 78
    assert not cause_path.is_symlink()
    assert json.loads(cause_path.read_text()) == {"cause": None}
    assert elsewhere.read_text() == "untouched"


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


def _subfolder_job(tmp_path: Path, subfolder: str) -> Path:
    from fi.alk.harness.job import HarnessJob

    job = HarnessJob(
        job_id="job",
        run_id="run",
        execution="hosted",
        source={
            "kind": "github",
            "visibility": "public",
            "repository": "acme/agent",
            "commit_sha": "a" * 40,
            "subfolder": subfolder,
        },
        runtime={"isolation": "dedicated_vm"},
        agent={"connector": "auto"},
    )
    path = tmp_path / "job.json"
    path.write_text(job.model_dump_json(), encoding="utf-8")
    return path


def _watch_authoring(monkeypatch):
    from fi.alk.harness import authoring_entrypoint as authoring

    calls: list[str] = []

    def compose(*_args, **_kwargs):
        calls.append("compose")

    async def auto(_namespace) -> int:
        calls.append("auto")
        return 0

    def await_harness(_output) -> int:
        calls.append("await")
        return 0

    monkeypatch.setattr(authoring, "initialize_hosted_compose", compose)
    monkeypatch.setattr(authoring, "_auto", auto)
    monkeypatch.setattr(authoring, "_await_harness", await_harness)
    monkeypatch.setattr(authoring, "_persist_authored_scenario_count", lambda *a: None)
    return authoring, calls


def _run_authoring(authoring, tmp_path: Path, subfolder: str, *flags: str) -> int:
    source = tmp_path / "source"
    source.mkdir(exist_ok=True)
    (source / "agents" / "a").mkdir(parents=True, exist_ok=True)
    (source / "agents" / "a" / "agent.py").write_text("x = 1\n")
    return authoring.main(
        [
            str(_subfolder_job(tmp_path, subfolder)),
            "--source",
            str(source),
            "--output",
            str(tmp_path / "authoring"),
            *flags,
        ]
    )


def _written_failure(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "authoring" / "failure.json").read_text())


def test_bad_subfolder_writes_failure_and_exits_78_await_mode(tmp_path, monkeypatch):
    authoring, calls = _watch_authoring(monkeypatch)

    assert _run_authoring(authoring, tmp_path, "agents/missing", "--await-harness") == 78

    failure = _written_failure(tmp_path)
    assert {key: failure[key] for key in ("domain", "stage", "code", "retryable")} == {
        "domain": "agent",
        "stage": "acquiring_source",
        "code": "subfolder_not_found",
        "retryable": False,
    }
    assert calls == []


def test_bad_subfolder_writes_failure_and_exits_78_auto_mode(tmp_path, monkeypatch):
    authoring, calls = _watch_authoring(monkeypatch)

    assert _run_authoring(authoring, tmp_path, "agents/missing") == 78

    assert _written_failure(tmp_path)["code"] == "subfolder_not_found"
    assert calls == []


def test_good_subfolder_continues(tmp_path, monkeypatch):
    authoring, calls = _watch_authoring(monkeypatch)

    assert _run_authoring(authoring, tmp_path, "agents/a") == 0

    assert calls == ["compose", "auto"]
    assert not (tmp_path / "authoring" / "failure.json").exists()


def test_the_guest_loads_a_repository_env_only_through_the_allowlist(tmp_path, monkeypatch) -> None:
    from fi.alk.harness.cli import _load_connection_env

    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    secrets.write_text(
        json.dumps({"SIMULATOR_OPENAI_API_KEY": "platform-openai"}), encoding="utf-8"
    )
    gateway.write_text(
        json.dumps({"AGENTCC_API_KEY": "platform-key", "AGENTCC_BASE_URL": "https://gw.invalid"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    for name in (
        "ALK_RUNTIME_CONFIGURATION_NAMES", "HTTPS_PROXY", "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_USE_BEDROCK", "PYTHONPATH", "HARNESS_VOICE_RUNNER", "LIVEKIT_URL",
        "MY_SETTING", "ALK_CLAUDE_GATEWAY_URL", "GOOGLE_CLOUD_LOCATION", "CLOUD_ML_REGION",
    ):
        monkeypatch.delenv(name, raising=False)
    source = tmp_path / "source"
    source.mkdir()
    (source / ".env").write_text(
        "HTTPS_PROXY=http://canary-proxy.invalid\nANTHROPIC_BASE_URL=https://canary.invalid\n"
        "CLAUDE_CODE_USE_BEDROCK=1\nPYTHONPATH=/repo\nHARNESS_VOICE_RUNNER=/repo/run.py\n"
        "ALK_CLAUDE_GATEWAY_URL=https://canary.invalid\nGOOGLE_CLOUD_LOCATION=canary\n"
        "CLOUD_ML_REGION=canary\nAGENTCC_API_KEY=repo-key\nOPENAI_API_KEY=repo-openai\n"
        "LIVEKIT_URL=wss://repo.invalid\nMY_SETTING=mine\n",
        encoding="utf-8",
    )

    def authoring(argv, *, validate_runtime):
        _load_connection_env(source)
        return 0

    monkeypatch.setattr(entrypoint, "authoring_main", authoring)

    assert entrypoint.main([]) == 0

    environ = entrypoint.os.environ
    for name in (
        "HTTPS_PROXY", "ANTHROPIC_BASE_URL", "CLAUDE_CODE_USE_BEDROCK", "PYTHONPATH",
        "HARNESS_VOICE_RUNNER", "ALK_CLAUDE_GATEWAY_URL", "GOOGLE_CLOUD_LOCATION", "MY_SETTING",
    ):
        assert name not in environ, name
    for name in ("ANTHROPIC_BASE_URL", "HARNESS_VOICE_RUNNER", "GOOGLE_CLOUD_LOCATION", "MY_SETTING"):
        assert environ[f"ALK_RUNTIME_VALUE_{name}"] != ""
    assert environ.get("CLOUD_ML_REGION", "") != "canary"
    assert environ["AGENTCC_API_KEY"] == "platform-key"
    assert environ["OPENAI_API_KEY"] == "platform-openai"
    assert environ["LIVEKIT_URL"] == "wss://repo.invalid"


def test_marking_a_controller_keeps_a_manifest_already_there() -> None:
    from fi.alk.harness.secrets import mark_controller_process

    environ = {"ALK_RUNTIME_CONFIGURATION_NAMES": "A,B"}
    mark_controller_process(environ)
    assert environ["ALK_RUNTIME_CONFIGURATION_NAMES"] == "A,B"

    empty: dict[str, str] = {}
    mark_controller_process(empty)
    assert empty == {"ALK_RUNTIME_CONFIGURATION_NAMES": ""}


def test_authoring_masks_every_value_in_spans_but_the_platform_settings(
    tmp_path, monkeypatch
) -> None:
    from unittest import mock

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from fi.alk.harness import observability

    secrets = tmp_path / "secrets.json"
    gateway = tmp_path / "simulator-secrets.json"
    job = tmp_path / "job.json"
    # secrets.json: the customer's declared values, some under names the platform also uses, and
    # an Observe export secret. A SIMULATOR_* model name is the platform's and must survive.
    secrets.write_text(
        json.dumps(
            {
                "RETELL_API_KEY": "retell-customer-key-1234567890",
                "CUSTOMER_DSN": "customer-dsn-value-1234567890",
                "OPENAI_API_KEY": "sk-customer-FAKE-AAAA2222",
                "AGENTCC_API_KEY": "cust-own-agentcc-key-fake-123",
                "GOOGLE_CLOUD_PROJECT": "cust-gcp-proj-fake-778899",
                "FI_API_KEY": "fi-observe-export-key-1234567890",
                "SIMULATOR_LLM_MODEL": "gemini-2.5-flash-lite",
            }
        ),
        encoding="utf-8",
    )
    # The gateway's default values: credentials among models and the Observe project, some under
    # the same names as the customer's, and a short code and a phone number.
    gateway.write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": "sk-platform-FAKE-BBBB2222",
                "AGENTCC_API_KEY": "agentcc-key-1234567890",
                "AGENTCC_BASE_URL": "https://gateway.example.test",
                "ALK_HARNESS_MODEL": "vertex_ai/claude-sonnet-4-5",
                "FI_HARNESS_PROJECT": "hosted-harness-prod",
                "GOOGLE_CLOUD_PROJECT": "platform-project-1",
                "SOME_SHORT_GATEWAY_CODE": "4821",
                "SOME_GATEWAY_PHONE_NUMBER": "+15550104477",
                "SOME_NEW_GATEWAY_VALUE": "unlisted-gateway-value-42",
            }
        ),
        encoding="utf-8",
    )
    job.write_text(json.dumps({"job_id": "job-1", "run_id": "run-1"}), encoding="utf-8")
    monkeypatch.setattr(entrypoint, "_SECRETS_PATH", secrets)
    monkeypatch.setattr(entrypoint, "_SIMULATOR_SECRETS_PATH", gateway)
    monkeypatch.setattr(entrypoint, "_ADC_PATH", tmp_path / "adc.json")
    monkeypatch.setattr(entrypoint, "_TARGET_SECRETS_PATH", tmp_path / "target.json")
    monkeypatch.setattr(
        entrypoint, "authoring_main", lambda argv, *, validate_runtime: 0
    )
    handed: list[tuple[str, ...]] = []

    def begin(job_id, run_id, context=None, secret_values=()):
        handed.append(tuple(secret_values))

    monkeypatch.setattr(observability, "begin", begin)

    with mock.patch.dict(entrypoint.os.environ):
        assert entrypoint.main([str(job)]) == 0

    assert len(handed) == 1
    memory = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource({"project_name": "hosted-harness-prod"})
    )
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer("test").start_as_current_span(
        "llm",
        attributes={
            "gen_ai.request.model": "vertex_ai/claude-sonnet-4-5",
            "llm.model_name": "gemini-2.5-flash-lite",
            "input.value": "a agentcc-key-1234567890 b retell-customer-key-1234567890 "
            "c customer-dsn-value-1234567890 d sk-customer-FAKE-AAAA2222 "
            "e cust-own-agentcc-key-fake-123 f cust-gcp-proj-fake-778899 "
            "g fi-observe-export-key-1234567890 h sk-platform-FAKE-BBBB2222 "
            "i pin 4821. j +15550104477 k 14821 l unlisted-gateway-value-42",
        },
    ):
        pass
    sink = InMemorySpanExporter()
    observability._CleaningExporter(sink, handed[0]).export(memory.get_finished_spans())

    (copy,) = sink.get_finished_spans()
    assert copy.attributes["gen_ai.request.model"] == "vertex_ai/claude-sonnet-4-5"
    assert copy.attributes["llm.model_name"] == "gemini-2.5-flash-lite"
    assert copy.resource.attributes["project_name"] == "hosted-harness-prod"
    assert copy.attributes["input.value"] == (
        "a *** b *** c *** d *** e *** f *** g *** h *** i pin ***. j *** k 14821 l ***"
    )


def test_authoring_masks_a_customer_secret_stored_under_a_platform_setting_name(
    tmp_path, monkeypatch
) -> None:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    from fi.alk.harness import observability

    # Only SIMULATOR_* is closed to customers, so FI_* names in secrets.json can hold theirs.
    customer = {
        "FI_HARNESS_PROJECT": "cust-vault-secret-FAKE-778899",
        "FI_BASE_URL": "cust-base-secret-FAKE-112233",
        "SIMULATOR_LLM_MODEL": "gemini-2.5-flash-lite",
    }
    gateway = {
        "FI_HARNESS_PROJECT": "hosted-harness-prod",
        "FI_BASE_URL": "https://collector.example.test",
        "ALK_HARNESS_MODEL": "vertex_ai/claude-sonnet-4-5",
    }

    handed = entrypoint._span_secret_values(customer, gateway)

    memory = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource({"project_name": "hosted-harness-prod"})
    )
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with provider.get_tracer("test").start_as_current_span(
        "llm",
        attributes={
            "gen_ai.request.model": "vertex_ai/claude-sonnet-4-5",
            "llm.model_name": "gemini-2.5-flash-lite",
            "input.value": "a cust-vault-secret-FAKE-778899 b cust-base-secret-FAKE-112233",
        },
    ):
        pass
    sink = InMemorySpanExporter()
    observability._CleaningExporter(sink, handed).export(memory.get_finished_spans())

    (copy,) = sink.get_finished_spans()
    assert copy.attributes["input.value"] == "a *** b ***"
    assert copy.attributes["gen_ai.request.model"] == "vertex_ai/claude-sonnet-4-5"
    assert copy.attributes["llm.model_name"] == "gemini-2.5-flash-lite"
    assert copy.resource.attributes["project_name"] == "hosted-harness-prod"


def _write_non_github_folder_job(tmp_path: Path, **source) -> Path:
    body = json.loads(_subfolder_job(tmp_path, "agents/a").read_text())
    body["source"] = {"kind": "archive", "archive_artifact_id": "art-1", **source}
    path = tmp_path / "job.json"
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_subfolder_on_a_non_github_source_writes_failure_and_exits_refused(tmp_path, monkeypatch):
    from platform_failure_rules import assert_platform_reads

    authoring, calls = _watch_authoring(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()

    code = authoring.main(
        [
            str(_write_non_github_folder_job(tmp_path, subfolder="agents/a")),
            "--source",
            str(source),
            "--output",
            str(tmp_path / "authoring"),
        ]
    )

    assert code == 78
    failure = _written_failure(tmp_path)
    assert_platform_reads(failure)
    assert failure == {
        "domain": "agent",
        "stage": "acquiring_source",
        "code": "subfolder_requires_github_source",
        "message": failure["message"],
        "retryable": False,
        "details": {"subfolder": "agents/a"},
    }
    assert "GitHub" in failure["message"]
    assert calls == []


def test_other_invalid_job_still_raises_in_authoring(tmp_path, monkeypatch):
    import pydantic

    authoring, _calls = _watch_authoring(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()

    with pytest.raises(pydantic.ValidationError):
        authoring.main(
            [
                str(_write_non_github_folder_job(tmp_path, archive_artifact_id="")),
                "--source",
                str(source),
                "--output",
                str(tmp_path / "authoring"),
            ]
        )
    assert not (tmp_path / "authoring" / "failure.json").exists()


def test_a_job_malformed_otherwise_too_still_raises_in_authoring(tmp_path, monkeypatch):
    # The folder on a non-GitHub source is reported only when it's the job's one fault.
    import pydantic

    authoring, calls = _watch_authoring(monkeypatch)
    source = tmp_path / "source"
    source.mkdir()
    job = _write_non_github_folder_job(tmp_path, subfolder="agents/a")
    body = json.loads(job.read_text())
    body.pop("job_id")
    job.write_text(json.dumps(body), encoding="utf-8")

    with pytest.raises(pydantic.ValidationError):
        authoring.main(
            [str(job), "--source", str(source), "--output", str(tmp_path / "authoring")]
        )
    assert not (tmp_path / "authoring" / "failure.json").exists()
    assert calls == []
