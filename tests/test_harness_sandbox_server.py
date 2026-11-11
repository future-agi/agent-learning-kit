import asyncio
import hmac
from io import BytesIO
import json
import os

from fastapi import UploadFile
from fastapi.testclient import TestClient
import pytest

from fi.alk.harness.sandbox_server import (
    LocalSandbox,
    LocalSandboxRequest,
    SandboxAdjustmentRequest,
    SandboxRerunRequest,
    _CONTROLLER_TOKEN,
    _presentation_value,
    _process_identity,
    _startup_refusal,
    _worker_failure_retryable,
    create_app,
    main,
)
from fi.alk.harness.secrets import resolve_worker_secrets

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


def test_health_needs_no_token_or_flag(tmp_path, monkeypatch):
    monkeypatch.delenv(_FLAG)
    client = TestClient(create_app(tmp_path / "state"))

    assert client.get("/health").status_code == 200
