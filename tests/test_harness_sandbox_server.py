import asyncio
import hmac
from io import BytesIO
import json
import os
import re
import uuid

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import Field, ValidationError
import pytest

from fi.alk.harness import sandbox_server
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.sandbox_server import (
    LocalSandbox,
    LocalSandboxRequest,
    SandboxAdjustmentRequest,
    SandboxPreflightRequest,
    SandboxRerunRequest,
    _CONTROLLER_TOKEN,
    _presentation_value,
    _process_identity,
    _startup_refusal,
    _worker_failure_retryable,
    create_app,
    main,
)
from fi.alk.harness.secrets import SecretResolutionError, resolve_worker_secrets
from fi.simulate.runtime.spec import RuntimeIsolation, RuntimeRequirements, SecretRef

_TOKEN = "ALK_SANDBOX_TOKEN"
_FLAG = "ALK_SANDBOX_ALLOW_UNAUTHENTICATED"
_SENTINEL = "tok-sentinel-7f3e9b"


@pytest.fixture(autouse=True)
def _unauthenticated_development_server(monkeypatch):
    # The server fails closed without a token; most tests here exercise job logic on
    # a tokenless app, and a token exported in the shell must not change them.
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.delenv(_TOKEN, raising=False)


def test_presentation_redaction_handles_prose_with_email_before_url():
    prose = "Contact owner@example.com or see https://example.com/docs"

    assert _presentation_value(prose) == prose
    assert (
        _presentation_value("postgres://user:password@database:5432/app")
        == "postgres://[redacted]@database:5432/app"
    )


def test_local_sandbox_rejects_source_outside_allowed_root(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(allowed))
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post("/v1/jobs", json={"source_path": str(outside)})

    assert response.status_code == 403


def test_local_request_accepts_provider_execution_mode() -> None:
    request = LocalSandboxRequest.model_validate(
        {
            "source_path": "/tmp/provider-source",
            "connector": "retell",
            "connector_mode": "connect_only",
            "connector_config": {"agent_id": "agent_example"},
        }
    )
    assert request.connector_mode.value == "connect_only"


def test_local_job_preserves_provider_execution_mode(tmp_path, monkeypatch) -> None:
    source = tmp_path / "sources" / "provider"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    sandbox = LocalSandbox(tmp_path / "state")

    async def idle(_job, _source):
        return None

    sandbox._execute = idle

    async def submit():
        response = sandbox.submit(
            LocalSandboxRequest(
                source_path=str(source),
                connector="retell",
                connector_mode="connect_only",
                connector_config={"agent_id": "agent_example"},
            )
        )
        await sandbox._tasks[response.job.job_id]
        return response

    response = asyncio.run(submit())
    assert response.job.agent.mode.value == "connect_only"


def test_local_connect_only_provider_requires_no_source_upload(tmp_path) -> None:
    sandbox = LocalSandbox(tmp_path / "state")

    async def idle(_job, _source):
        return None

    sandbox._execute = idle

    async def submit():
        response = sandbox.submit(
            LocalSandboxRequest(
                provider_only=True,
                connector="retell",
                connector_mode="connect_only",
                connector_config={"agent_id": "agent_example"},
            )
        )
        await sandbox._tasks[response.job.job_id]
        return response

    response = asyncio.run(submit())
    assert response.job.source.kind.value == "provider"
    assert (sandbox.jobs_root / response.job.job_id / "provider-source").is_dir()

    with pytest.raises(ValueError, match="provider_only_requires_connect_only_provider"):
        LocalSandboxRequest(provider_only=True, connector="auto")


def test_provider_only_accepts_a_connect_only_livekit_agent():
    request = LocalSandboxRequest(
        provider_only=True,
        connector="livekit",
        connector_mode="connect_only",
        connector_config={
            "agent_name": "returns-agent",
            "target_system_prompt": "You handle returns.",
        },
    )

    assert request.connector == "livekit"


def test_second_sandbox_instance_does_not_orphan_live_controller(tmp_path):
    root = tmp_path / "state"
    first = LocalSandbox(root)
    job = first.jobs_root / "live-job"
    job.mkdir()
    state_path = job / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "stage": "running",
                "controller_pid": os.getpid(),
                "controller_identity": _process_identity(os.getpid()),
                "controller_token": _CONTROLLER_TOKEN,
            }
        ),
        encoding="utf-8",
    )

    LocalSandbox(root)

    assert json.loads(state_path.read_text())["stage"] == "running"


def test_local_sandbox_persists_and_lists_job_without_secrets(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    monkeypatch.setenv("ALK_SANDBOX_TOKEN", "test-token")
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/jobs",
        headers={"Authorization": "Bearer test-token"},
        json={
            "source_path": str(source),
            "scenario_count": 7,
            "secret_refs": {
                "livekit": {
                    "manager": "environment",
                    "key": "LK_URL",
                    "purpose": "livekit_url",
                }
            },
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"]["total_scenarios"] == 7
    assert body["job"]["agent"]["secret_refs"]["livekit"]["key"] == "LK_URL"
    assert (
        client.get("/v1/jobs", headers={"Authorization": "Bearer test-token"}).json()[
            0
        ]["job"]["job_id"]
        == body["job"]["job_id"]
    )


def test_local_sandbox_reports_live_stage_timestamp_and_detail_from_events(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    state_root = tmp_path / "state"
    client = TestClient(create_app(state_root))
    submitted = client.post("/v1/jobs", json={"source_path": str(source)}).json()
    job_id = submitted["job"]["job_id"]
    run_id = submitted["job"]["run_id"]
    event_time = "2026-08-22T01:02:03+00:00"
    events_path = state_root / "artifacts" / run_id / "harness-events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        json.dumps(
            {
                "type": "harness.stage.started",
                "wall_time": event_time,
                "payload": {"stage": "scenarios"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    current = client.get(f"/v1/jobs/{job_id}").json()["status"]

    assert current["stage"] == "generating_scenarios"
    assert current["updated_at"] == "2026-08-22T01:02:03Z"
    assert current["detail"] == "generating and validating scenarios"


def test_local_sandbox_exposes_generated_stage_outputs_and_adjustments(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    sandbox = LocalSandbox(tmp_path / "state")

    async def idle(_job, _source):
        await asyncio.sleep(0)

    sandbox._execute = idle

    async def submit():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        await sandbox._tasks[response.job.job_id]
        return response

    response = asyncio.run(submit())
    output = tmp_path / "state" / "artifacts" / response.job.run_id
    output.mkdir(parents=True)
    (output / "contract.json").write_text(
        json.dumps(
            {
                "agent": "agent",
                "one_liner": "Books appointments",
                "tools": [{"name": "book"}],
                "hard_constraints": ["confirm first"],
                "real_use_cases": ["new booking"],
            }
        ),
        encoding="utf-8",
    )
    (output / "environment.json").write_text(
        json.dumps(
            {
                "source": "/private/source",
                "compose_file": "/private/docker-compose.yml",
                "compose_override_file": "/private/override.yml",
                "services": ["postgres"],
                "overrides": {"DATABASE_URL": "postgres://test"},
                "running": True,
            }
        ),
        encoding="utf-8",
    )
    (output / "scenarios.json").write_text(
        json.dumps([{"name": "booking", "instruction": "Book tomorrow"}]),
        encoding="utf-8",
    )

    adjusted = sandbox.adjust(
        response.job.job_id,
        SandboxAdjustmentRequest(
            instruction="Add 10 more scenarios covering payment failures",
            target_stage="scenarios",
            scenario_delta=10,
        ),
    )

    assert [item["kind"] for item in adjusted.stage_outputs] == [
        "contract",
        "environment",
        "scenarios",
    ]
    assert "source" not in adjusted.stage_outputs[1]["data"]
    assert "compose_override_file" not in adjusted.stage_outputs[1]["data"]
    assert adjusted.status.total_scenarios == 10
    assert adjusted.adjustments[0]["target_stage"] == "scenarios"
    assert adjusted.adjustments[0]["scenario_delta"] == 10
    assert adjusted.adjustments[0]["status"] == "pending"


def test_local_sandbox_reports_committed_scenario_progress_while_worker_runs(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    state_root = tmp_path / "state"
    client = TestClient(create_app(state_root))
    submitted = client.post(
        "/v1/jobs", json={"source_path": str(source), "scenario_count": 3}
    ).json()
    job_id = submitted["job"]["job_id"]
    run_id = submitted["job"]["run_id"]
    events_path = state_root / "artifacts" / run_id / "harness-events.jsonl"
    events_path.parent.mkdir(parents=True, exist_ok=True)
    events_path.write_text(
        json.dumps(
            {
                "type": "harness.stage.started",
                "wall_time": "2026-08-22T01:02:03+00:00",
                "payload": {"stage": "running"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    campaign = state_root / "artifacts" / run_id / "runs" / "run-20260822-010203"
    (campaign / "scenario-one").mkdir(parents=True)
    (campaign / "scenario-one" / "result.json").write_text("{}", encoding="utf-8")
    # Nested results and unfinished scenario directories must not inflate progress.
    (campaign / "scenario-one" / "sdk").mkdir()
    (campaign / "scenario-one" / "sdk" / "result.json").write_text(
        "{}", encoding="utf-8"
    )
    (campaign / "scenario-two").mkdir()

    current = client.get(f"/v1/jobs/{job_id}").json()["status"]

    assert current["stage"] == "running"
    assert current["completed_scenarios"] == 1
    assert current["total_scenarios"] == 3


def test_local_sandbox_exposes_secret_safe_live_stage_outputs(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    state_root = tmp_path / "state"
    sandbox = LocalSandbox(state_root)

    async def no_execution(_job, _source):
        return None

    sandbox._execute = no_execution

    async def submit():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        await sandbox._tasks[response.job.job_id]
        return response

    submitted = asyncio.run(submit())
    run_root = sandbox.artifacts_root / submitted.job.run_id
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "contract.json").write_text(
        json.dumps(
            {
                "one_liner": "Books rides",
                "modality": "voice",
                "tools": [{"name": "book_ride", "args": ["secret"]}],
                "hard_constraints": ["Confirm before booking"],
            }
        ),
        encoding="utf-8",
    )
    (run_root / "environment.json").write_text(
        json.dumps(
            {
                "project": "isolated",
                "services": ["postgres"],
                "overrides": {
                    "DATABASE_URL": "postgresql://user:password@postgres:5432/app"
                },
            }
        ),
        encoding="utf-8",
    )
    (run_root / "scenarios.json").write_text(
        json.dumps(
            [
                {
                    "name": "booking",
                    "instruction": "Book a ride",
                    "use_case": "happy path",
                    "private_reference": "must-not-be-returned",
                }
            ]
        ),
        encoding="utf-8",
    )
    (run_root / "platform.json").write_text(
        json.dumps(
            {
                "run_test_id": "11111111-1111-1111-1111-111111111111",
                "test_execution_id": "22222222-2222-2222-2222-222222222222",
                "url": "https://untrusted.example/ignored",
            }
        ),
        encoding="utf-8",
    )

    response = sandbox.get(submitted.job.job_id).model_dump(mode="json")
    serialized = json.dumps(response["stage_outputs"])

    assert [item["kind"] for item in response["stage_outputs"]] == [
        "contract",
        "environment",
        "scenarios",
        "simulation",
    ]
    assert "book_ride" in serialized
    assert "postgresql://***@postgres:5432/app" in serialized
    assert "password" not in serialized
    assert "must-not-be-returned" not in serialized
    assert (
        "/dashboard/simulate/test/11111111-1111-1111-1111-111111111111/"
        "22222222-2222-2222-2222-222222222222"
    ) in serialized
    assert "untrusted.example" not in serialized


def test_preflight_discovers_connectors_and_missing_credentials_without_values(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    (source / "worker.py").write_text(
        "from livekit import agents\n"
        "import os\n"
        "key = os.environ['LIVEKIT_API_KEY']\n"
        "database = os.environ['DATABASE_URL']\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post("/v1/preflight", json={"source_path": str(source)})

    assert response.status_code == 200
    body = response.json()
    assert body["source_kind"] == "local_repository"
    assert body["ready_to_submit"] is False
    assert body["can_submit_for_authoring"] is True
    assert body["configuration_status"] == "selection_pending"
    assert "livekit" in body["credentials"]["detected_connectors"]
    requirements = {
        item["environment_name"]: item for item in body["credentials"]["requirements"]
    }
    assert requirements["LIVEKIT_API_KEY"]["status"] == "missing"
    assert requirements["DATABASE_URL"]["status"] == "harness_provided"
    assert "value" not in json.dumps(body).lower()


def test_preflight_accepts_non_secret_connector_configuration(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    (source / "worker.py").write_text(
        'import os\nagent = os.environ["REMOTE_AGENT_ID"]\n', encoding="utf-8"
    )
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/preflight",
        json={
            "source_path": str(source),
            "connector_config": {"REMOTE_AGENT_ID": "agent-42"},
        },
    )

    assert response.status_code == 200
    assert response.json()["ready_to_submit"] is True
    requirement = response.json()["credentials"]["requirements"][0]
    assert requirement["status"] == "configured"
    assert "agent-42" not in response.text


def test_preflight_accepts_uploaded_environment_without_returning_values(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    (source / "worker.py").write_text(
        'import os\nkey = os.environ["OPENAI_API_KEY"]\n', encoding="utf-8"
    )
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/preflight",
        json={
            "source_path": str(source),
            "environment_values": {"OPENAI_API_KEY": "never-persist-this"},
        },
    )

    assert response.status_code == 200
    assert response.json()["credentials"]["requirements"][0]["status"] == "configured"
    assert "never-persist-this" not in response.text


def test_generic_preflight_defers_blank_template_only_credentials(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    (source / "agent.py").write_text(
        "def run(value):\n    return value\n", encoding="utf-8"
    )
    (source / ".env.example").write_text(
        "OPENAI_API_KEY=\nTAVILY_API_KEY=\n", encoding="utf-8"
    )
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/preflight",
        json={
            "source_path": str(source),
            "metadata": {"generic_harness_v1": True},
        },
    )

    assert response.status_code == 200
    requirements = {
        item["environment_name"]: item
        for item in response.json()["credentials"]["requirements"]
    }
    assert requirements["OPENAI_API_KEY"]["required"] is False
    assert requirements["TAVILY_API_KEY"]["required"] is False


def test_uploaded_environment_is_mounted_for_worker_but_not_persisted(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    sandbox = LocalSandbox(tmp_path / "state")
    observed = {}

    async def capture(job, _source):
        observed.update(
            resolve_worker_secrets(
                job.agent.secret_refs,
                environment={
                    **os.environ,
                    **sandbox._ephemeral_secrets[job.job_id],
                },
            )
        )
        sandbox._ephemeral_secrets.pop(job.job_id, None)

    sandbox._execute = capture

    async def submit():
        response = sandbox.submit(
            LocalSandboxRequest(
                source_path=str(source),
                environment_values={"OPENAI_API_KEY": "never-persist-this"},
            )
        )
        await sandbox._tasks[response.job.job_id]
        return response

    response = asyncio.run(submit())
    persisted = (
        tmp_path / "state" / "jobs" / response.job.job_id / "job.json"
    ).read_text()

    assert observed == {"OPENAI_API_KEY": "never-persist-this"}
    assert "never-persist-this" not in persisted
    reference = response.job.agent.secret_refs["OPENAI_API_KEY"]
    assert reference.manager == "mounted"
    assert reference.key.startswith("ALK_JOB_")
    assert reference.purpose == "target_provider"


def test_saved_session_rerun_reuses_artifacts_and_keeps_fresh_values_ephemeral(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    sandbox = LocalSandbox(tmp_path / "state")

    async def no_initial_execution(job, _source):
        sandbox._ephemeral_secrets.pop(job.job_id, None)

    sandbox._execute = no_initial_execution

    async def exercise():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        await sandbox._tasks[response.job.job_id]
        job_id = response.job.job_id
        run_root = sandbox.artifacts_root / response.job.run_id
        (run_root / "environment-bundle").mkdir(parents=True)
        for path in (
            run_root / "contract.json",
            run_root / "scenarios.json",
            run_root / "environment-bundle" / "manifest.json",
            run_root / "platform.json",
        ):
            path.write_text("{}", encoding="utf-8")
        state_path = sandbox.jobs_root / job_id / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["stage"] = "completed"
        state_path.write_text(json.dumps(state), encoding="utf-8")

        observed = {}

        async def capture(job, only):
            observed["job"] = job.job_id
            observed["only"] = only
            observed["values"] = dict(sandbox._ephemeral_secrets[job.job_id])
            sandbox._ephemeral_secrets.pop(job.job_id, None)

        sandbox._execute_rerun = capture
        queued = sandbox.rerun(
            job_id,
            SandboxRerunRequest(
                environment_values={"OPENAI_API_KEY": "fresh-never-persist"}
            ),
        )
        await sandbox._tasks[job_id]
        return queued, observed, state_path

    queued, observed, state_path = asyncio.run(exercise())

    assert queued.status.stage.value == "queued"
    assert observed == {
        "job": queued.job.job_id,
        "only": [],
        "values": {"OPENAI_API_KEY": "fresh-never-persist"},
    }
    assert "fresh-never-persist" not in state_path.read_text(encoding="utf-8")
    assert "fresh-never-persist" not in (state_path.parent / "job.json").read_text(
        encoding="utf-8"
    )


def test_uploaded_environment_rejects_runner_control_and_reference_conflicts(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    client = TestClient(create_app(tmp_path / "state"))

    reserved = client.post(
        "/v1/jobs",
        json={
            "source_path": str(source),
            "environment_values": {"DOCKER_HOST": "host-escape"},
        },
    )
    conflict = client.post(
        "/v1/jobs",
        json={
            "source_path": str(source),
            "environment_values": {"OPENAI_API_KEY": "raw"},
            "secret_refs": {
                "OPENAI_API_KEY": {
                    "manager": "environment",
                    "key": "OPENAI_API_KEY",
                    "purpose": "existing reference",
                }
            },
        },
    )

    assert reserved.status_code == 422
    assert conflict.status_code == 422


def test_uploaded_folder_becomes_sandbox_owned_source(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    uploaded = client.post(
        "/v1/sources",
        files=[
            ("files", ("agent.py", b"print('ready')\n", "text/x-python")),
            ("files", ("requirements.txt", b"fastapi\n", "text/plain")),
            ("paths", (None, "agent.py")),
            ("paths", (None, "requirements.txt")),
            ("name", (None, "customer-agent")),
        ],
    )

    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["name"] == "customer-agent"
    assert body["file_count"] == 2
    source_id = body["source_id"]
    source = tmp_path / "state" / "uploads" / source_id
    assert source.joinpath("agent.py").read_text() == "print('ready')\n"

    preflight = client.post("/v1/preflight", json={"source_id": source_id})
    assert preflight.status_code == 200
    assert preflight.json()["source_kind"] == "archive"
    assert preflight.json()["source_label"] == "customer-agent"


def test_uploaded_folder_rejects_traversal_and_embedded_env(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    traversal = client.post(
        "/v1/sources",
        files=[
            ("files", ("escape.py", b"bad", "text/plain")),
            ("paths", (None, "../escape.py")),
        ],
    )
    secret_file = client.post(
        "/v1/sources",
        files=[
            ("files", (".env", b"API_KEY=secret", "text/plain")),
            ("paths", (None, ".env")),
        ],
    )

    assert traversal.status_code == 400
    assert secret_file.status_code == 400
    assert not tmp_path.joinpath("escape.py").exists()


def test_secret_file_upload_is_opaque_one_time_and_job_scoped(tmp_path):
    sandbox = LocalSandbox(tmp_path / "state")
    raw = b'{"type":"service_account","project_id":"customer"}'
    uploaded = asyncio.run(
        sandbox.upload_secret_file(
            UploadFile(filename="customer.json", file=BytesIO(raw)),
            "GOOGLE_APPLICATION_CREDENTIALS",
        )
    )

    assert uploaded.size == len(raw)
    assert uploaded.secret_ref.manager == "mounted"
    assert uploaded.secret_ref.purpose == "target_provider"
    assert raw.decode() not in uploaded.model_dump_json()
    with pytest.raises(Exception, match="reference_invalid"):
        sandbox._claim_secret_files(
            "wrong-alias", {"AWS_SHARED_CREDENTIALS_FILE": uploaded.secret_ref}
        )
    claimed, names = sandbox._claim_secret_files(
        "job-one", {uploaded.environment_name: uploaded.secret_ref}
    )
    claimed_path = next(iter(claimed.values()))
    assert names == {"GOOGLE_APPLICATION_CREDENTIALS"}
    assert open(claimed_path, "rb").read() == raw
    assert oct(os.stat(claimed_path).st_mode & 0o777) == "0o400"
    with pytest.raises(Exception, match="unavailable_or_consumed"):
        sandbox._claim_secret_files(
            "job-two", {uploaded.environment_name: uploaded.secret_ref}
        )

    sandbox._delete_job_secret_files("job-one")
    assert not os.path.exists(claimed_path)


def test_secret_file_endpoint_rejects_invalid_google_json(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/secret-files",
        files={"file": ("credentials.json", b"not-json", "application/json")},
        data={"environment_name": "GOOGLE_APPLICATION_CREDENTIALS"},
    )

    assert response.status_code == 400
    assert "valid JSON object" in response.json()["detail"]


def test_preflight_reports_selection_pending_for_infrastructure_only_compose(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    (source / "compose.yml").write_text(
        "services:\n  postgres:\n    image: postgres:17\n  redis:\n    image: redis:7\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post("/v1/preflight", json={"source_path": str(source)})

    assert response.status_code == 200
    payload = response.json()
    assert payload["packaging"]["ready"] is True
    assert payload["packaging"]["agent_runtime_packaged"] is False
    assert payload["ready_to_submit"] is False
    assert payload["can_submit_for_authoring"] is True
    assert payload["configuration_status"] == "selection_pending"


def test_preflight_accepts_public_github_url_and_requires_app_for_private_repo(
    tmp_path,
):
    client = TestClient(create_app(tmp_path / "state"))

    public = client.post(
        "/v1/preflight",
        json={"github_repository": "https://github.com/org/agent.git"},
    )
    private = client.post(
        "/v1/preflight",
        json={
            "github_repository": "org/private-agent",
            "github_visibility": "private",
        },
    )

    assert public.status_code == 200
    assert public.json()["source_label"] == "org/agent"
    assert public.json()["ready_to_submit"] is True
    assert public.json()["can_submit_for_authoring"] is True
    assert public.json()["checkout_required"] is True
    assert private.json()["ready_to_submit"] is False
    assert private.json()["can_submit_for_authoring"] is False
    requirement = private.json()["credentials"]["requirements"][0]
    assert requirement["accepted_secret_types"] == ["github_app_installation"]


def test_public_github_branch_url_populates_job_ref(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/jobs",
        json={
            "github_repository": "https://github.com/acme/agent/tree/feat/harness",
        },
    )

    assert response.status_code == 200
    source = response.json()["job"]["source"]
    assert source["repository"] == "acme/agent"
    assert source["ref"] == "feat/harness"


def test_private_github_submission_becomes_hosted_job(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/jobs",
        json={
            "github_repository": "customer/private-agent",
            "github_visibility": "private",
            "github_installation_id": "installation-42",
        },
    )

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["execution"] == "hosted"
    assert job["source"]["installation_id"] == "installation-42"
    assert "token" not in json.dumps(job).lower()


def test_job_submission_requires_exactly_one_source(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    none = client.post("/v1/jobs", json={})
    both = client.post(
        "/v1/jobs",
        json={"source_path": str(tmp_path), "github_repository": "org/agent"},
    )

    assert none.status_code == 422
    assert both.status_code == 422


def test_retry_policy_never_retries_agent_or_grading_failures():
    allowed = ["infrastructure", "connectivity"]

    assert _worker_failure_retryable(
        1, {"domain": "infrastructure", "retryable": True}, allowed
    )
    assert not _worker_failure_retryable(
        1, {"domain": "agent", "retryable": True}, allowed
    )
    assert not _worker_failure_retryable(
        1, {"domain": "grading", "retryable": True}, allowed
    )
    assert not _worker_failure_retryable(
        0, {"domain": "infrastructure", "retryable": True}, allowed
    )


def test_acquisition_defers_inventory_and_unselected_packaging_to_authoring(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "fixture"
    source.mkdir(parents=True)
    (source / "server.py").write_text("print('source')")
    (source / "optional.py").write_text(
        "import os\ndef unused(): return os.environ['UNUSED_TOKEN']\n"
    )
    (source / "compose.yaml").write_text("invalid unselected document")
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    launched = []

    class Worker:
        async def wait(self):
            return 0

    async def fake_worker(*args, **kwargs):
        launched.append(args)
        return Worker()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_worker)
    sandbox = LocalSandbox(tmp_path / "state")

    async def submit():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        await sandbox._tasks[response.job.job_id]
        return response

    response = asyncio.run(submit())
    directory = sandbox.jobs_root / response.job.job_id
    assert len(launched) == 1
    assert json.loads((directory / "configuration-admission.json").read_text()) == {
        "status": "selection_pending",
        "missing": [],
    }
    inventory = json.loads((directory / "credentials.json").read_text())
    assert (
        next(
            item
            for item in inventory["requirements"]
            if item["environment_name"] == "UNUSED_TOKEN"
        )["status"]
        == "missing"
    )


@pytest.mark.parametrize("flag", [None, "0", "false", "", "true", "yes", " 1", "1 "])
def test_tokenless_server_refuses_jobs_unless_the_flag_is_exactly_one(
    tmp_path, monkeypatch, caplog, flag
):
    caplog.set_level("DEBUG")
    if flag is None:
        monkeypatch.delenv(_FLAG)
    else:
        monkeypatch.setenv(_FLAG, flag)
    client = TestClient(create_app(tmp_path / "state"))

    response = client.get("/v1/jobs", headers={"Authorization": f"Bearer {_SENTINEL}"})

    assert response.status_code == 503
    assert response.json() == {"detail": "sandbox_token_not_configured"}
    assert _SENTINEL not in response.text
    assert _SENTINEL not in caplog.text


@pytest.mark.parametrize("token", ["", "   ", "tok\udcff"])
def test_blank_or_unusable_token_refuses_jobs_without_the_flag(
    tmp_path, monkeypatch, token
):
    monkeypatch.delenv(_FLAG)
    monkeypatch.setenv(_TOKEN, token)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.get("/v1/jobs", headers={"Authorization": "Bearer "})

    assert response.status_code == 503
    assert response.json() == {"detail": "sandbox_token_not_configured"}


def test_padded_token_is_compared_exactly_at_request_time(tmp_path, monkeypatch):
    monkeypatch.setenv(_TOKEN, f" {_SENTINEL}")
    client = TestClient(create_app(tmp_path / "state"))

    response = client.get("/v1/jobs", headers={"Authorization": f"Bearer {_SENTINEL}"})

    assert response.status_code == 401


def test_every_route_but_health_refuses_without_a_token(tmp_path, monkeypatch):
    # Walks the app itself, so a route added without the guard fails here.
    monkeypatch.delenv(_FLAG)
    app = create_app(tmp_path / "state")
    client = TestClient(app, raise_server_exceptions=False)
    checked = 0
    for route in app.routes:
        if route.path == "/health":
            continue
        path = re.sub(r"\{[^}]+\}", "x", route.path)
        for method in sorted(route.methods - {"HEAD"}):
            response = client.request(method, path)
            assert response.status_code == 503, (method, route.path)
            checked += 1

    assert checked >= 9


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
def test_no_schema_or_docs_pages(tmp_path, monkeypatch, path):
    monkeypatch.setenv(_TOKEN, _SENTINEL)
    client = TestClient(create_app(tmp_path / "state"))

    assert client.get(path).status_code == 404


def test_tokenless_server_serves_jobs_under_the_flag(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    assert client.get("/v1/jobs").status_code == 200


@pytest.mark.parametrize("flag", ["1", None])
@pytest.mark.parametrize(
    ("authorization", "expected"),
    [
        (f"Bearer {_SENTINEL}", 200),
        ("Bearer wrong", 401),
        (None, 401),
        ("Bearer té".encode(), 401),
        (f"Bearer {_SENTINEL}x", 401),
        (f"Bearer {_SENTINEL[:-1]}", 401),
        (_SENTINEL, 401),
    ],
)
def test_set_token_is_required_even_under_the_flag(
    tmp_path, monkeypatch, caplog, flag, authorization, expected
):
    caplog.set_level("DEBUG")
    monkeypatch.setenv(_TOKEN, _SENTINEL)
    if flag is None:
        monkeypatch.delenv(_FLAG)
    else:
        monkeypatch.setenv(_FLAG, flag)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)
    headers = {} if authorization is None else {"Authorization": authorization}

    response = client.get("/v1/jobs", headers=headers)

    assert response.status_code == expected
    if expected == 401:
        assert response.json() == {"detail": "invalid token"}
    assert _SENTINEL not in response.text
    assert _SENTINEL not in caplog.text


def test_token_is_compared_in_constant_time_on_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv(_TOKEN, _SENTINEL)
    real = hmac.compare_digest
    calls = []

    def spy(left, right):
        calls.append((type(left), type(right)))
        return real(left, right)

    monkeypatch.setattr(hmac, "compare_digest", spy)
    client = TestClient(create_app(tmp_path / "state"))

    response = client.get("/v1/jobs", headers={"Authorization": f"Bearer {_SENTINEL}"})

    assert response.status_code == 200
    assert calls == [(bytes, bytes)]


@pytest.mark.parametrize(
    "host",
    [
        "",
        "0.0.0.0",
        "::",
        "localhost",
        "192.168.1.10",
        "127.0.0.1.example.com",
        "127.0.0.1:8788",
    ],
)
def test_flag_refuses_to_start_on_a_non_loopback_host(host):
    reason = _startup_refusal(host, None, True)

    assert reason is not None
    assert _FLAG in reason
    assert "127.0.0.1" in reason and "::1" in reason


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1"])
def test_flag_starts_on_a_loopback_host(host):
    assert _startup_refusal(host, None, True) is None
    assert _startup_refusal(host, "", True) is None


@pytest.mark.parametrize("token", [None, "", "   "])
@pytest.mark.parametrize("host", ["127.0.0.1", "0.0.0.0"])
def test_missing_token_refuses_to_start_without_the_flag(host, token):
    reason = _startup_refusal(host, token, False)

    assert reason is not None
    assert _TOKEN in reason


def test_missing_token_refusal_names_both_sides_and_the_flag():
    reason = _startup_refusal("0.0.0.0", None, False)

    assert reason is not None
    assert _TOKEN in reason
    assert "ALK_HARNESS_SANDBOX_TOKEN" in reason
    assert _FLAG in reason


@pytest.mark.parametrize("allow", [False, True])
@pytest.mark.parametrize("host", ["0.0.0.0", "127.0.0.1", "10.0.0.5", ""])
def test_set_token_starts_on_any_host(host, allow):
    assert _startup_refusal(host, _SENTINEL, allow) is None


@pytest.mark.parametrize(("host", "allow"), [("0.0.0.0", False), ("127.0.0.1", True)])
@pytest.mark.parametrize("token", [f" {_SENTINEL} ", f"{_SENTINEL}\n", "   "])
def test_padded_or_blank_token_refuses_to_start_without_naming_it(host, allow, token):
    reason = _startup_refusal(host, token, allow)

    assert reason is not None
    assert _TOKEN in reason
    assert _SENTINEL not in reason


@pytest.fixture
def stubbed_uvicorn(monkeypatch):
    monkeypatch.delenv(_FLAG, raising=False)
    monkeypatch.delenv(_TOKEN, raising=False)
    monkeypatch.delenv("ALK_SANDBOX_HOST", raising=False)
    calls = []
    monkeypatch.setattr("uvicorn.run", lambda *args, **kwargs: calls.append(kwargs))
    return calls


@pytest.mark.parametrize("token", [None, "", "   ", f" {_SENTINEL} "])
def test_main_refuses_to_start_without_a_usable_token(
    monkeypatch, capsys, caplog, stubbed_uvicorn, token
):
    caplog.set_level("DEBUG")
    if token is not None:
        monkeypatch.setenv(_TOKEN, token)

    with pytest.raises(SystemExit) as exited:
        main()

    assert exited.value.code == 2
    assert stubbed_uvicorn == []
    err = capsys.readouterr().err
    assert _TOKEN in err
    assert _SENTINEL not in err
    assert _SENTINEL not in caplog.text


def test_main_refuses_a_blank_token_even_under_the_flag_on_loopback(
    monkeypatch, stubbed_uvicorn
):
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.setenv("ALK_SANDBOX_HOST", "127.0.0.1")
    monkeypatch.setenv(_TOKEN, "   ")

    with pytest.raises(SystemExit) as exited:
        main()

    assert exited.value.code == 2
    assert stubbed_uvicorn == []


def test_unusable_token_refuses_to_start():
    assert _startup_refusal("127.0.0.1", "tok\udcff", True) is not None


@pytest.mark.parametrize("host", ["10.0.0.5", "0.0.0.0", ""])
def test_main_checks_the_host_it_binds(monkeypatch, stubbed_uvicorn, host):
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.setenv("ALK_SANDBOX_HOST", host)

    with pytest.raises(SystemExit) as exited:
        main()

    assert exited.value.code == 2
    assert stubbed_uvicorn == []


def test_main_starts_with_a_token_on_the_configured_host(
    monkeypatch, caplog, stubbed_uvicorn
):
    caplog.set_level("DEBUG")
    monkeypatch.setenv(_TOKEN, _SENTINEL)
    monkeypatch.setenv("ALK_SANDBOX_HOST", "0.0.0.0")

    main()

    assert [call["host"] for call in stubbed_uvicorn] == ["0.0.0.0"]
    assert "unauthenticated sandbox" not in caplog.text
    assert _SENTINEL not in caplog.text


def test_main_warns_when_starting_unauthenticated_on_loopback(
    monkeypatch, caplog, stubbed_uvicorn
):
    caplog.set_level("WARNING")
    monkeypatch.setenv(_FLAG, "1")

    main()

    assert [call["host"] for call in stubbed_uvicorn] == ["127.0.0.1"]
    assert "unauthenticated sandbox" in caplog.text
    assert "developer's own laptop only" in caplog.text
    assert "including the agent under test" in caplog.text


def test_health_needs_no_token_or_flag(tmp_path, monkeypatch):
    monkeypatch.delenv(_FLAG)
    client = TestClient(create_app(tmp_path / "state"))

    assert client.get("/health").status_code == 200


class _DedicatedVmJob(HarnessJob):
    # Hosted jobs require the dedicated_vm runtime, which submit does not set yet. A
    # subclass rather than a factory, because get() rebuilds jobs with model_validate.
    runtime: RuntimeRequirements = Field(
        default_factory=lambda: RuntimeRequirements(
            isolation=RuntimeIsolation.DEDICATED_VM
        )
    )


class _AwaitingDedicatedIsolation(Exception):
    """The route failed only on the runtime isolation hosted jobs still lack."""


async def _idle_execute(*_args):
    return None


@pytest.fixture
def hosted_sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(sandbox_server, "HarnessJob", _DedicatedVmJob)
    return LocalSandbox(
        tmp_path / "state",
        upload_root=tmp_path / "uploads",
        secret_file_root=tmp_path / "secret-files",
    )


@pytest.fixture
def launched_workers(monkeypatch):
    launched = []

    class Worker:
        async def wait(self):
            return 0

    async def fake_worker(*args, **kwargs):
        launched.append(kwargs["env"])
        return Worker()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_worker)
    return launched


def _ref(manager, key):
    return SecretRef(manager=manager, key=key, purpose="target_provider")


def _job_key(job_id, index):
    return f"ALK_JOB_{job_id.replace('-', '').upper()}_{index}"


def _with_refs(job, refs):
    return job.model_copy(
        update={"agent": job.agent.model_copy(update={"secret_refs": refs})}
    )


async def _upload_archive(sandbox):
    uploaded = await sandbox.upload_source(
        [UploadFile(filename="agent.py", file=BytesIO(b"print('ready')\n"))],
        ["agent.py"],
        "customer-agent",
    )
    return uploaded.source_id


async def _upload_credential(sandbox, name, body=b"credential-body"):
    uploaded = await sandbox.upload_secret_file(
        UploadFile(filename="credential", file=BytesIO(body)), name
    )
    return uploaded.secret_ref.key


async def _parked_submit(sandbox, **fields):
    # Parking _execute keeps the job's directory, state and in-memory values live, so a
    # test can run the real _execute or resolver on a job it crafts afterwards.
    sandbox._execute = _idle_execute
    try:
        response = sandbox.submit(LocalSandboxRequest(**fields))
        await sandbox._tasks.pop(response.job.job_id)
    finally:
        del sandbox._execute
    return response.job


async def _terminal_job(sandbox, **fields):
    job = await _parked_submit(sandbox, **fields)
    run_root = sandbox.artifacts_root / job.run_id
    (run_root / "environment-bundle").mkdir(parents=True)
    for path in (
        run_root / "contract.json",
        run_root / "scenarios.json",
        run_root / "environment-bundle" / "manifest.json",
        run_root / "platform.json",
    ):
        path.write_text("{}", encoding="utf-8")
    state_path = sandbox.jobs_root / job.job_id / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["stage"] = "completed"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    sandbox._execute_rerun = _idle_execute
    return job


def _assert_nothing_left(sandbox):
    assert list(sandbox.jobs_root.iterdir()) == []
    assert list((sandbox.secret_files_root / "jobs").iterdir()) == []


def _assert_failed_before_launch(sandbox, job_id, launched, message):
    state = json.loads((sandbox.jobs_root / job_id / "state.json").read_text())
    assert state["stage"] == "failed"
    assert state["failure"]["code"] == "SecretResolutionError"
    assert state["failure"]["message"] == message
    assert launched == []


@pytest.mark.parametrize(
    ("manager", "key_kind"),
    [
        ("platform-vault", "job"),
        ("mounted", "job"),
        ("platform-vault", "upload"),
        ("environment", "upload"),
        ("Mounted", "upload"),
        ("other", "upload"),
    ],
)
def test_hosted_submit_refuses_incoming_sandbox_prefixed_refs(
    hosted_sandbox, manager, key_kind
):
    sandbox = hosted_sandbox
    sandbox._execute = _idle_execute

    async def submit():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        key = upload if key_kind == "upload" else _job_key(str(uuid.uuid4()), 0)
        with pytest.raises(HTTPException) as refused:
            sandbox.submit(
                LocalSandboxRequest(
                    source_id=source_id, secret_refs={"X": _ref(manager, key)}
                )
            )
        return refused.value

    refused = asyncio.run(submit())

    assert refused.status_code == 400
    assert refused.detail == "sandbox_secret_reference_refused: X"
    _assert_nothing_left(sandbox)


@pytest.mark.parametrize(
    "manager",
    [
        "environment",
        "futureagi",
        "platform-vault",
        "platform-config",
        "Mounted",
        "other",
    ],
)
def test_local_submit_refuses_a_second_alias_for_an_upload(tmp_path, manager):
    sandbox = LocalSandbox(
        tmp_path / "state",
        upload_root=tmp_path / "uploads",
        secret_file_root=tmp_path / "secret-files",
    )
    sandbox._execute = _idle_execute

    async def submit():
        upload = await _upload_credential(sandbox, "GOOD")
        with pytest.raises(HTTPException) as refused:
            sandbox.submit(
                LocalSandboxRequest(
                    provider_only=True,
                    connector="livekit",
                    connector_mode="connect_only",
                    connector_config={
                        "agent_name": "returns-agent",
                        "target_system_prompt": "You handle returns.",
                    },
                    secret_refs={
                        "GOOD": _ref("mounted", upload),
                        "OTHER_VALUE": _ref(manager, upload),
                    },
                )
            )
        return refused.value

    refused = asyncio.run(submit())

    assert refused.status_code == 400
    assert refused.detail == "sandbox_secret_reference_refused: OTHER_VALUE"
    _assert_nothing_left(sandbox)


@pytest.mark.parametrize("source_kind", ["archive", "github"])
def test_hosted_submit_refuses_an_incoming_vault_ref_before_any_claim(
    hosted_sandbox, source_kind
):
    sandbox = hosted_sandbox
    sandbox._execute = _idle_execute

    async def submit():
        upload = await _upload_credential(sandbox, "GOOD")
        source = (
            {"source_id": await _upload_archive(sandbox)}
            if source_kind == "archive"
            else {"github_repository": "acme/agent", "github_commit_sha": "a" * 40}
        )
        with pytest.raises(HTTPException) as refused:
            sandbox.submit(
                LocalSandboxRequest(
                    **source,
                    secret_refs={
                        "GOOD": _ref("mounted", upload),
                        "PLATFORM": _ref(
                            "platform-vault", "harness-organization-secret"
                        ),
                    },
                )
            )
        return refused.value

    refused = asyncio.run(submit())

    assert refused.status_code == 400
    assert refused.detail == "sandbox_vault_reference_unknown: PLATFORM"
    _assert_nothing_left(sandbox)
    # Refused before the claim, so the upload is still there to submit again.
    assert any(sandbox.secret_files_root.joinpath("pending").iterdir())


def test_a_local_vault_ref_is_accepted_at_submit_and_refused_at_resolve(tmp_path):
    # Only hosted jobs refuse it at the door; a local job keeps its old path, failing when its
    # refs are resolved.
    sandbox = LocalSandbox(tmp_path / "state")
    platform = _ref("platform-vault", "harness-organization-secret")

    job = asyncio.run(
        _parked_submit(
            sandbox,
            provider_only=True,
            connector="retell",
            connector_mode="connect_only",
            connector_config={"agent_id": "agent_example"},
            secret_refs={"PLATFORM": platform},
        )
    )

    assert job.execution.value == "local"
    assert job.agent.secret_refs["PLATFORM"] == platform
    with pytest.raises(
        SecretResolutionError, match="sandbox_vault_reference_unknown: PLATFORM"
    ):
        sandbox._resolve_job_secrets(job)


def test_hosted_submit_lets_a_platform_config_ref_through(hosted_sandbox):
    # Only incoming vault refs are refused at the door; other managers keep their own path.
    sandbox = hosted_sandbox
    config_ref = SecretRef(
        manager="platform-config", key="simulator-key", purpose="simulator_provider"
    )

    async def submit():
        return await _parked_submit(
            sandbox,
            source_id=await _upload_archive(sandbox),
            secret_refs={"SIMULATOR": config_ref},
        )

    job = asyncio.run(submit())

    assert job.agent.secret_refs["SIMULATOR"] == config_ref


@pytest.mark.parametrize("source_kind", ["archive", "github"])
def test_hosted_submit_names_its_own_values_platform_vault(hosted_sandbox, source_kind):
    sandbox = hosted_sandbox

    async def submit():
        upload = await _upload_credential(sandbox, "GOOD", b"credential-never-persist")
        source = (
            {"source_id": await _upload_archive(sandbox)}
            if source_kind == "archive"
            else {"github_repository": "acme/agent", "github_commit_sha": "a" * 40}
        )
        job = await _parked_submit(
            sandbox,
            **source,
            environment_values={"OPENAI_API_KEY": "value-never-persist"},
            secret_refs={"GOOD": _ref("mounted", upload)},
        )
        return job, upload

    job, upload = asyncio.run(submit())
    claimed_path = str(sandbox.secret_files_root / "jobs" / job.job_id / "GOOD")
    directory = sandbox.jobs_root / job.job_id
    persisted = (directory / "job.json").read_text() + (
        directory / "state.json"
    ).read_text()

    assert job.execution.value == "hosted"
    assert job.agent.secret_refs == {
        "OPENAI_API_KEY": _ref("platform-vault", _job_key(job.job_id, 0)),
        "GOOD": _ref("platform-vault", upload),
    }
    assert job.metadata["environment_value_names"] == ["GOOD", "OPENAI_API_KEY"]
    assert sandbox._ephemeral_secrets[job.job_id] == {
        _job_key(job.job_id, 0): "value-never-persist",
        upload: claimed_path,
    }
    assert "value-never-persist" not in persisted
    assert "credential-never-persist" not in persisted
    assert claimed_path not in persisted


def test_hosted_submit_still_refuses_a_mounted_server_key(hosted_sandbox):
    sandbox = hosted_sandbox
    sandbox._execute = _idle_execute

    async def submit():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        with pytest.raises(
            ValidationError, match="hosted_secret_manager_unsupported: OTHER_VALUE"
        ):
            sandbox.submit(
                LocalSandboxRequest(
                    source_id=source_id,
                    environment_values={"OPENAI_API_KEY": "value"},
                    secret_refs={
                        "GOOD": _ref("mounted", upload),
                        "OTHER_VALUE": _ref("mounted", "AGENTCC_API_KEY"),
                    },
                )
            )

    asyncio.run(submit())

    _assert_nothing_left(sandbox)


def test_hosted_submit_refuses_a_vault_alias_for_another_aliases_upload(
    hosted_sandbox,
):
    sandbox = hosted_sandbox
    sandbox._execute = _idle_execute

    async def submit():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        with pytest.raises(HTTPException) as refused:
            sandbox.submit(
                LocalSandboxRequest(
                    source_id=source_id,
                    secret_refs={
                        "GOOD": _ref("mounted", upload),
                        "OTHER_VALUE": _ref("platform-vault", upload),
                    },
                )
            )
        return upload, refused.value

    upload, refused = asyncio.run(submit())
    pending = str(uuid.UUID(hex=upload.removeprefix("ALK_SECRET_FILE_")[:32].lower()))

    assert refused.status_code == 400
    assert refused.detail == "sandbox_secret_reference_refused: OTHER_VALUE"
    _assert_nothing_left(sandbox)
    # The refusal precedes the claim, so the upload was never consumed.
    assert (sandbox.secret_files_root / "pending" / pending).is_file()


def test_hosted_execute_resolves_its_own_values(hosted_sandbox, launched_workers):
    sandbox = hosted_sandbox

    async def submit():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        response = sandbox.submit(
            LocalSandboxRequest(
                source_id=source_id,
                environment_values={"OPENAI_API_KEY": "own-value"},
                secret_refs={"GOOD": _ref("mounted", upload)},
            )
        )
        await sandbox._tasks[response.job.job_id]
        return response.job

    job = asyncio.run(submit())
    state = json.loads((sandbox.jobs_root / job.job_id / "state.json").read_text())

    assert state["stage"] == "completed", state.get("detail")
    assert len(launched_workers) == 1
    assert launched_workers[0]["ALK_RUNTIME_VALUE_OPENAI_API_KEY"] == "own-value"
    assert launched_workers[0]["ALK_RUNTIME_VALUE_GOOD"] == str(
        sandbox.secret_files_root / "jobs" / job.job_id / "GOOD"
    )


def test_hosted_execute_never_reads_a_vault_key_from_the_environment(
    hosted_sandbox, launched_workers, monkeypatch
):
    sandbox = hosted_sandbox

    async def execute():
        source_id = await _upload_archive(sandbox)
        job = await _parked_submit(sandbox, source_id=source_id)
        # This job's own key shape, set only in the environment.
        key = _job_key(job.job_id, 9)
        monkeypatch.setenv(key, "server-environment-value")
        await sandbox._execute(
            _with_refs(job, {"X": _ref("platform-vault", key)}),
            sandbox.uploads_root / source_id,
        )
        return job

    job = asyncio.run(execute())

    _assert_failed_before_launch(
        sandbox, job.job_id, launched_workers, "secret_reference_unavailable: X"
    )


@pytest.mark.parametrize(
    ("key_kind", "message"),
    [
        ("upload", "secret_reference_unavailable: X"),
        ("job", "sandbox_vault_reference_unknown: X"),
    ],
)
def test_hosted_execute_never_reads_another_jobs_values(
    hosted_sandbox, launched_workers, key_kind, message
):
    sandbox = hosted_sandbox

    async def execute():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        # The other job stays parked, so its values and claimed file are live throughout.
        other = await _parked_submit(
            sandbox,
            source_id=source_id,
            environment_values={"OPENAI_API_KEY": "other-job-value"},
            secret_refs={"GOOD": _ref("mounted", upload)},
        )
        job = await _parked_submit(sandbox, source_id=source_id)
        key = upload if key_kind == "upload" else _job_key(other.job_id, 0)
        await sandbox._execute(
            _with_refs(job, {"X": _ref("platform-vault", key)}),
            sandbox.uploads_root / source_id,
        )
        return job, other

    job, other = asyncio.run(execute())

    _assert_failed_before_launch(sandbox, job.job_id, launched_workers, message)
    assert other.job_id in sandbox._ephemeral_secrets


@pytest.mark.parametrize("key_kind", ["server_token", "foreign_job", "platform"])
def test_resolver_refuses_vault_keys_it_did_not_mint_for_this_job(
    hosted_sandbox, monkeypatch, key_kind
):
    sandbox = hosted_sandbox
    monkeypatch.setenv("ALK_SANDBOX_TOKEN", "server-token-value")

    async def submit():
        return await _parked_submit(
            sandbox,
            source_id=await _upload_archive(sandbox),
            environment_values={"OPENAI_API_KEY": "own-value"},
        )

    job = asyncio.run(submit())
    foreign = _job_key(str(uuid.uuid4()), 0)
    # Seeded into this job's own map, so only the job-id check can refuse it.
    sandbox._ephemeral_secrets[job.job_id][foreign] = "foreign-value"
    key = {
        "server_token": "ALK_SANDBOX_TOKEN",
        "foreign_job": foreign,
        "platform": "harness-organization-secret",
    }[key_kind]

    with pytest.raises(SecretResolutionError) as refused:
        sandbox._resolve_job_secrets(
            _with_refs(job, {"X": _ref("platform-vault", key)})
        )

    assert str(refused.value) == "sandbox_vault_reference_unknown: X"


def test_resolver_returns_own_values_and_treats_empty_as_missing(hosted_sandbox):
    sandbox = hosted_sandbox

    async def submit():
        return await _parked_submit(
            sandbox,
            source_id=await _upload_archive(sandbox),
            environment_values={"OPENAI_API_KEY": "own-value"},
        )

    job = asyncio.run(submit())

    assert sandbox._resolve_job_secrets(job) == {"OPENAI_API_KEY": "own-value"}
    sandbox._ephemeral_secrets[job.job_id][_job_key(job.job_id, 0)] = ""
    with pytest.raises(SecretResolutionError) as refused:
        sandbox._resolve_job_secrets(job)
    assert str(refused.value) == "secret_reference_unavailable: OPENAI_API_KEY"


def test_resolver_keeps_local_mounted_and_environment_refs(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    monkeypatch.setenv("SANDBOX_TEST_SHELL_VALUE", "shell-value")
    sandbox = LocalSandbox(
        tmp_path / "state",
        upload_root=tmp_path / "uploads",
        secret_file_root=tmp_path / "secret-files",
    )

    async def submit():
        return await _parked_submit(
            sandbox,
            source_path=str(source),
            environment_values={"OPENAI_API_KEY": "local-value"},
            secret_refs={"SHELL": _ref("environment", "SANDBOX_TEST_SHELL_VALUE")},
        )

    job = asyncio.run(submit())
    expected = resolve_worker_secrets(
        job.agent.secret_refs,
        environment={**os.environ, **sandbox._ephemeral_secrets[job.job_id]},
    )

    assert job.execution.value == "local"
    assert job.agent.secret_refs["OPENAI_API_KEY"].manager == "mounted"
    assert sandbox._resolve_job_secrets(job) == expected
    assert expected == {"OPENAI_API_KEY": "local-value", "SHELL": "shell-value"}


def test_hosted_rerun_resolves_a_claimed_file_from_its_claim(hosted_sandbox):
    sandbox = hosted_sandbox

    async def rerun():
        job = await _terminal_job(sandbox, source_id=await _upload_archive(sandbox))
        upload = await _upload_credential(sandbox, "GOOD")
        sandbox.rerun(
            job.job_id,
            SandboxRerunRequest(
                secret_refs={"GOOD": _ref("mounted", upload)},
                environment_values={"OPENAI_API_KEY": "fresh-value"},
            ),
        )
        await sandbox._tasks.pop(job.job_id)
        return job

    job = asyncio.run(rerun())

    assert sandbox._ephemeral_secrets[job.job_id] == {
        "OPENAI_API_KEY": "fresh-value",
        "GOOD": str(sandbox.secret_files_root / "jobs" / job.job_id / "GOOD"),
    }


@pytest.mark.parametrize(
    ("manager", "key_kind"),
    [
        ("mounted", "server"),
        ("environment", "server"),
        ("futureagi", "server"),
        ("platform-vault", "job"),
        ("environment", "upload"),
    ],
)
def test_hosted_rerun_refuses_refs_other_than_its_claimed_files(
    hosted_sandbox, monkeypatch, manager, key_kind
):
    sandbox = hosted_sandbox
    monkeypatch.setenv("AGENTCC_API_KEY", "server-credential-value")

    async def rerun():
        job = await _terminal_job(sandbox, source_id=await _upload_archive(sandbox))
        upload = await _upload_credential(sandbox, "GOOD")
        key = {
            "server": "AGENTCC_API_KEY",
            "job": _job_key(job.job_id, 0),
            "upload": upload,
        }[key_kind]
        state_before = (sandbox.jobs_root / job.job_id / "state.json").read_text()
        with pytest.raises(HTTPException) as refused:
            sandbox.rerun(
                job.job_id,
                SandboxRerunRequest(
                    secret_refs={
                        "GOOD": _ref("mounted", upload),
                        "X": _ref(manager, key),
                    }
                ),
            )
        return job, state_before, refused.value

    job, state_before, refused = asyncio.run(rerun())

    assert refused.status_code == 400
    assert refused.detail == "sandbox_rerun_secret_refused: X"
    assert job.job_id not in sandbox._ephemeral_secrets
    assert job.job_id not in sandbox._tasks
    assert not (sandbox.secret_files_root / "jobs" / job.job_id).exists()
    assert (sandbox.jobs_root / job.job_id / "state.json").read_text() == state_before


def test_local_rerun_still_resolves_a_mounted_server_side_ref(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    monkeypatch.setenv("SANDBOX_TEST_SHELL_VALUE", "shell-value")
    sandbox = LocalSandbox(
        tmp_path / "state",
        upload_root=tmp_path / "uploads",
        secret_file_root=tmp_path / "secret-files",
    )

    async def rerun():
        job = await _terminal_job(sandbox, source_path=str(source))
        sandbox.rerun(
            job.job_id,
            SandboxRerunRequest(
                secret_refs={"X": _ref("mounted", "SANDBOX_TEST_SHELL_VALUE")}
            ),
        )
        await sandbox._tasks.pop(job.job_id)
        return job

    job = asyncio.run(rerun())

    assert sandbox._ephemeral_secrets[job.job_id] == {"X": "shell-value"}


@pytest.mark.xfail(
    strict=True,
    raises=_AwaitingDedicatedIsolation,
    reason="submit does not yet give hosted jobs the dedicated_vm runtime",
)
def test_hosted_github_route_accepts_a_submitted_value(tmp_path, monkeypatch):
    monkeypatch.delenv("ALK_SANDBOX_TOKEN", raising=False)
    monkeypatch.setattr(LocalSandbox, "_execute", _idle_execute)
    client = TestClient(create_app(tmp_path / "state"))

    try:
        response = client.post(
            "/v1/jobs",
            json={
                "github_repository": "acme/agent",
                "github_commit_sha": "a" * 40,
                "environment_values": {"OPENAI_API_KEY": "route-value"},
            },
        )
    except ValidationError as exc:
        if "hosted_isolation_must_be_dedicated_vm" in str(exc):
            raise _AwaitingDedicatedIsolation from exc
        raise

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["execution"] == "hosted"
    assert job["agent"]["secret_refs"]["OPENAI_API_KEY"]["manager"] == "platform-vault"
    assert "route-value" not in response.text


# --- TH-9550: what a job can put in the controller's environment -----------------------------

@pytest.fixture
def local_sandbox(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    (source / "agent.py").write_text("print('ready')\n", encoding="utf-8")
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    sandbox = LocalSandbox(
        tmp_path / "state",
        upload_root=tmp_path / "uploads",
        secret_file_root=tmp_path / "secret-files",
    )
    sandbox.test_source = source
    return sandbox


def _run_local_job(sandbox, refs=None, **fields):
    """Submit a local job, then run the real _execute on it with refs of the test's choosing."""

    async def run():
        job = await _parked_submit(sandbox, source_path=str(sandbox.test_source), **fields)
        if refs is not None:
            job = _with_refs(job, {**job.agent.secret_refs, **refs})
        await sandbox._execute(job, sandbox.test_source)
        return job

    return asyncio.run(run())


def _state(sandbox, job):
    return json.loads((sandbox.jobs_root / job.job_id / "state.json").read_text())


def _only_worker_env(launched):
    assert len(launched) == 1
    return launched[0]


def test_config_connection_names_reach_the_controller_and_the_rest_only_the_runtime(
    local_sandbox, launched_workers
):
    job = _run_local_job(
        local_sandbox,
        connector_config={
            "VAPI_ASSISTANT_ID": "assistant-canary",
            "REMOTE_AGENT_ID": "remote-canary",
            "VAPI_API_BASE_URL": "https://canary.invalid",
            "HTTP_PROXY": "http://canary-proxy.invalid:3128",
            "NO_PROXY": "canary.invalid",
        },
    )

    env = _only_worker_env(launched_workers)
    assert _state(local_sandbox, job)["stage"] == "completed"
    assert env["VAPI_ASSISTANT_ID"] == "assistant-canary"
    for name in ("REMOTE_AGENT_ID", "VAPI_API_BASE_URL", "HTTP_PROXY", "NO_PROXY"):
        assert name not in env
        assert env[f"ALK_RUNTIME_VALUE_{name}"]
    names = env["ALK_RUNTIME_CONFIGURATION_NAMES"].split(",")
    assert {"REMOTE_AGENT_ID", "VAPI_API_BASE_URL", "HTTP_PROXY", "NO_PROXY"} <= set(names)
    assert "VAPI_ASSISTANT_ID" not in names


@pytest.mark.parametrize(
    "name", ["PYTHONPATH", "HARNESS_PLATFORM_URL", "ALK_SANDBOX_TOKEN"]
)
@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest])
def test_reserved_config_keys_are_refused_at_submit_and_preflight(name, request_type):
    with pytest.raises(ValidationError, match=f"connector_config_name_reserved: {name}"):
        request_type(source_path="/tmp/agent", connector_config={name: "canary"})


def test_reserved_config_key_is_a_422_over_http(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    for route in ("/v1/jobs", "/v1/preflight"):
        response = client.post(
            route,
            json={"source_path": "/tmp/agent", "connector_config": {"PYTHONPATH": "x"}},
        )
        assert response.status_code == 422, route
        assert "connector_config_name_reserved: PYTHONPATH" in response.text


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest])
@pytest.mark.parametrize(
    "alias",
    [
        "PYTHONPATH",
        "LD_PRELOAD",
        "HARNESS_VOICE_RUNNER",
        "AGENTCC_API_KEY",
        "GOOGLE_API_KEY",
        "TMPDIR",
        "http_proxy",
        "no_proxy",
        "ALK_SANDBOX_TOKEN",
    ],
)
def test_a_reserved_alias_for_a_controller_variable_is_refused(request_type, alias):
    for manager in ("environment", "mounted", "futureagi"):
        with pytest.raises(ValidationError, match=f"secret_alias_reserved: {alias}"):
            request_type(
                source_path="/tmp/agent",
                secret_refs={alias: _ref(manager, "SOME_NEUTRAL_KEY")},
            )


@pytest.mark.parametrize("alias", ["A=B", " PYTHONPATH", "X\x00", "1BAD", ""])
def test_a_malformed_alias_is_refused_at_submit_preflight_and_rerun(alias):
    for request_type in (LocalSandboxRequest, SandboxPreflightRequest):
        with pytest.raises(ValidationError, match="secret_reference_name_invalid"):
            request_type(
                source_path="/tmp/agent",
                secret_refs={alias: _ref("environment", "SOME_NEUTRAL_KEY")},
            )
    with pytest.raises(ValidationError, match="secret_reference_name_invalid"):
        SandboxRerunRequest(secret_refs={alias: _ref("mounted", "SOME_NEUTRAL_KEY")})


def test_the_rerun_alias_shape_is_a_422_over_http(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    response = client.post(
        "/v1/jobs/anything/rerun",
        json={"secret_refs": {"A=B": {"manager": "mounted", "key": "K", "purpose": "target_provider"}}},
    )

    assert response.status_code == 422


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest])
def test_uploads_and_environment_values_keep_their_reserved_looking_names(request_type):
    request = request_type(
        source_path="/tmp/agent",
        secret_refs={
            "GOOGLE_APPLICATION_CREDENTIALS": _ref(
                "mounted", "ALK_SECRET_FILE_0123456789ABCDEF_ABC123"
            )
        },
        environment_values={
            "GOOGLE_CLOUD_PROJECT": "project-canary",
            "PYTHONUNBUFFERED": "1",
        },
    )

    assert "GOOGLE_APPLICATION_CREDENTIALS" in request.secret_refs


def test_a_mounted_ref_that_is_not_an_upload_cannot_take_a_reserved_alias():
    with pytest.raises(ValidationError, match="secret_alias_reserved: PYTHONPATH"):
        LocalSandboxRequest(
            source_path="/tmp/agent",
            secret_refs={"PYTHONPATH": _ref("mounted", "NOT_AN_UPLOAD_KEY")},
        )


def test_the_jobs_own_allowlisted_values_reach_the_controller_under_their_own_names(
    local_sandbox, launched_workers
):
    _run_local_job(
        local_sandbox,
        environment_values={
            "LIVEKIT_URL": "wss://canary.invalid",
            "VAPI_API_BASE_URL": "https://canary-base.invalid",
            "MY_VALUE": "mine",
        },
    )

    env = _only_worker_env(launched_workers)
    assert env["LIVEKIT_URL"] == "wss://canary.invalid"
    assert env["ALK_RUNTIME_VALUE_LIVEKIT_URL"] == "wss://canary.invalid"
    assert "VAPI_API_BASE_URL" not in env
    assert "MY_VALUE" not in env
    assert env["ALK_RUNTIME_VALUE_VAPI_API_BASE_URL"] == "https://canary-base.invalid"


def test_a_rerun_controller_gets_the_jobs_allowlisted_values(
    local_sandbox, launched_workers
):
    sandbox = local_sandbox

    async def rerun():
        job = await _terminal_job(sandbox, source_path=str(sandbox.test_source))
        del sandbox._execute_rerun
        sandbox.rerun(
            job.job_id,
            SandboxRerunRequest(
                environment_values={
                    "VAPI_API_KEY": "vapi-canary",
                    "VAPI_API_BASE_URL": "https://canary-base.invalid",
                }
            ),
        )
        await sandbox._tasks.pop(job.job_id)

    asyncio.run(rerun())

    env = launched_workers[0]
    assert env["VAPI_API_KEY"] == "vapi-canary"
    assert "VAPI_API_BASE_URL" not in env
    assert env["ALK_RUNTIME_VALUE_VAPI_API_BASE_URL"] == "https://canary-base.invalid"


@pytest.mark.parametrize("manager", ["environment", "mounted", "futureagi"])
def test_a_token_server_never_lets_a_ref_read_its_environment(
    local_sandbox, launched_workers, monkeypatch, manager
):
    monkeypatch.setenv(_TOKEN, "server-token-canary")
    monkeypatch.setenv("LK_URL", "wss://server.invalid")

    job = _run_local_job(local_sandbox, refs={"X": _ref(manager, "LK_URL")})

    _assert_failed_before_launch(
        local_sandbox, job.job_id, launched_workers, "secret_reference_reserved: X"
    )


def test_the_flag_with_a_token_is_not_development_mode(
    local_sandbox, launched_workers, monkeypatch
):
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.setenv(_TOKEN, "server-token-canary")
    monkeypatch.setenv("LK_URL", "wss://server.invalid")

    job = _run_local_job(local_sandbox, refs={"X": _ref("environment", "LK_URL")})

    _assert_failed_before_launch(
        local_sandbox, job.job_id, launched_workers, "secret_reference_reserved: X"
    )


def test_the_flag_without_a_token_on_a_public_host_is_not_development_mode(
    local_sandbox, launched_workers, monkeypatch
):
    monkeypatch.setenv("ALK_SANDBOX_HOST", "0.0.0.0")
    monkeypatch.setenv("LK_URL", "wss://server.invalid")

    job = _run_local_job(local_sandbox, refs={"X": _ref("environment", "LK_URL")})

    _assert_failed_before_launch(
        local_sandbox, job.job_id, launched_workers, "secret_reference_reserved: X"
    )


def test_development_mode_reads_an_ordinary_server_variable(
    local_sandbox, launched_workers, monkeypatch
):
    monkeypatch.setenv("LK_URL", "wss://dev.invalid")

    _run_local_job(local_sandbox, refs={"X": _ref("environment", "LK_URL")})

    assert _only_worker_env(launched_workers)["X"] == "wss://dev.invalid"


@pytest.mark.parametrize(
    "key",
    [
        "HARNESS_PLATFORM_API_KEY",
        "AGENTCC_API_KEY",
        "GITHUB_INSTALLATION_1_TOKEN",
        "ALK_SANDBOX_TOKEN",
        "PYTHONPATH",
    ],
)
def test_development_mode_still_refuses_a_reserved_key_under_a_neutral_alias(
    local_sandbox, launched_workers, monkeypatch, key
):
    monkeypatch.setenv(key, "runner-canary")

    job = _run_local_job(local_sandbox, refs={"X": _ref("environment", key)})

    _assert_failed_before_launch(
        local_sandbox, job.job_id, launched_workers, "secret_reference_reserved: X"
    )


@pytest.mark.parametrize("token", [None, "server-token-canary"])
def test_a_jobs_own_value_resolves_in_both_modes(
    local_sandbox, launched_workers, monkeypatch, token
):
    if token:
        monkeypatch.setenv(_TOKEN, token)

    _run_local_job(local_sandbox, environment_values={"MY_VALUE": "mine"})

    assert _only_worker_env(launched_workers)["ALK_RUNTIME_VALUE_MY_VALUE"] == "mine"


def _local_rerun_refused(sandbox, key):
    async def rerun():
        job = await _terminal_job(sandbox, source_path=str(sandbox.test_source))
        upload = await _upload_credential(sandbox, "GOOD")
        before = (sandbox.jobs_root / job.job_id / "state.json").read_text()
        with pytest.raises(HTTPException) as refused:
            sandbox.rerun(
                job.job_id,
                SandboxRerunRequest(
                    secret_refs={
                        "GOOD": _ref("mounted", upload),
                        "X": _ref("environment", key),
                    }
                ),
            )
        return job, before, refused.value

    return asyncio.run(rerun())


def test_a_token_server_local_rerun_refuses_a_server_ref_and_stores_nothing(
    local_sandbox, monkeypatch
):
    sandbox = local_sandbox
    monkeypatch.setenv(_TOKEN, "server-token-canary")
    monkeypatch.setenv("LK_URL", "wss://server.invalid")

    job, before, refused = _local_rerun_refused(sandbox, "LK_URL")

    assert refused.status_code == 400
    assert refused.detail == "secret_reference_reserved: X"
    assert job.job_id not in sandbox._ephemeral_secrets
    assert job.job_id not in sandbox._tasks
    assert not (sandbox.secret_files_root / "jobs" / job.job_id).exists()
    assert (sandbox.jobs_root / job.job_id / "state.json").read_text() == before


def test_development_mode_local_rerun_refuses_a_runner_key(local_sandbox, monkeypatch):
    sandbox = local_sandbox
    monkeypatch.setenv("HARNESS_PLATFORM_API_KEY", "runner-canary")

    job, _before, refused = _local_rerun_refused(sandbox, "HARNESS_PLATFORM_API_KEY")

    assert (refused.status_code, refused.detail) == (400, "secret_reference_reserved: X")
    assert job.job_id not in sandbox._ephemeral_secrets
    assert not (sandbox.secret_files_root / "jobs" / job.job_id).exists()


def test_a_token_server_local_rerun_still_resolves_a_claimed_upload(
    local_sandbox, monkeypatch
):
    sandbox = local_sandbox
    monkeypatch.setenv(_TOKEN, "server-token-canary")

    async def rerun():
        job = await _terminal_job(sandbox, source_path=str(sandbox.test_source))
        upload = await _upload_credential(sandbox, "GOOD")
        sandbox.rerun(
            job.job_id, SandboxRerunRequest(secret_refs={"GOOD": _ref("mounted", upload)})
        )
        await sandbox._tasks.pop(job.job_id)
        return job

    job = asyncio.run(rerun())

    assert "GOOD" in sandbox._ephemeral_secrets[job.job_id]


def test_no_proxy_setting_from_a_job_reaches_the_controllers_environment(
    local_sandbox, launched_workers, monkeypatch
):
    """The proxy leak end to end: a canary proxy in config must not be read by the controller."""
    import urllib.request

    _run_local_job(
        local_sandbox,
        connector_config={"HTTP_PROXY": "http://canary-proxy.invalid:3128"},
        environment_values={"https_proxy": "http://canary-proxy.invalid:3128"},
    )
    env = _only_worker_env(launched_workers)
    monkeypatch.setattr(os, "environ", dict(env))

    # Only a bare scheme name is a proxy setting; the namespaced copies are inert.
    assert not {"http", "https", "all", "ftp", "no"} & set(
        urllib.request.getproxies_environment()
    )
    with pytest.raises(ValidationError, match="secret_alias_reserved: no_proxy"):
        LocalSandboxRequest(
            source_path="/tmp/agent",
            secret_refs={"no_proxy": _ref("environment", "SOME_NEUTRAL_KEY")},
        )
