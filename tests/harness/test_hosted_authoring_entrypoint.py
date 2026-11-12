from __future__ import annotations

import json
from pathlib import Path

from fi.alk.harness import hosted_authoring_entrypoint as entrypoint


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


def test_authoring_hands_spans_the_customers_values_whole_and_only_the_gateways_credentials(
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
    # the same names as the customer's, and the guest-booking PIN and target number.
    gateway.write_text(
        json.dumps(
            {
                "OPENAI_API_KEY": "sk-platform-FAKE-BBBB2222",
                "AGENTCC_API_KEY": "agentcc-key-1234567890",
                "AGENTCC_BASE_URL": "https://gateway.example.test",
                "ALK_HARNESS_MODEL": "vertex_ai/claude-sonnet-4-5",
                "FI_HARNESS_PROJECT": "hosted-harness-prod",
                "GOOGLE_CLOUD_PROJECT": "platform-project-1",
                "ALK_CAB_GUEST_POC_PIN": "4821",
                "ALK_CAB_GUEST_POC_TARGET_PHONE_NUMBER": "+15550104477",
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
            "i pin 4821. j +15550104477 k 14821",
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
        "a *** b *** c *** d *** e *** f *** g *** h *** i pin ***. j *** k 14821"
    )
