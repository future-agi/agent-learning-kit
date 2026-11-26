import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import uuid

from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import Field, ValidationError
import pytest

from fi.alk.harness import call_runner, config, local_certified_runtime, process_runtime, provision, refusals, sandbox_server
from fi.alk.harness.bundle_v2 import ProcessUser, SourceProcess
from fi.alk.harness.job import HarnessJob
from fi.alk.harness.refusals import JobRefused
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
from fi.alk.harness import secrets
from fi.alk.harness.secrets import SecretResolutionError, resolve_worker_secrets
from fi.simulate.runtime.spec import RuntimeIsolation, RuntimeRequirements, SecretRef

# Imported before the worker guard swaps subprocess.Popen for a function: mcp annotates a class
# with subprocess.Popen[bytes] when first imported, which config.provider_env() can reach mid-test.
try:
    import mcp.os.win32.utilities  # noqa: F401
except ImportError:
    pass

pytest_plugins = ["pytester"]

_TOKEN = "ALK_SANDBOX_TOKEN"
_FLAG = "ALK_SANDBOX_ALLOW_UNAUTHENTICATED"
_SENTINEL = "tok-sentinel-7f3e9b"


@pytest.fixture(autouse=True)
def _unauthenticated_development_server(monkeypatch):
    # The server fails closed without a token; most tests here exercise job logic on
    # a tokenless app, and a token exported in the shell must not change them.
    monkeypatch.setenv(_FLAG, "1")
    monkeypatch.delenv(_TOKEN, raising=False)


# A test here never starts a real worker, CLI or container: every such start is swapped for a
# harmless child, whatever the test does. A test that didn't ask for workers (fake_worker) and
# makes one anyway fails at teardown. The guard can't raise at the start, because TestClient and
# _execute's own except swallow it.
# Any argv text naming the package is guarded, and so is a guarded program in any word of it
# (`env docker ...`, `timeout 60 claude ...`, `sh -c "docker ..."`): the names are the real
# console scripts of pyproject `[project.scripts]` plus docker and claude.
_GUARDED = ("fi.alk", "fi/alk/", "fi\\alk\\", f"fi{os.sep}alk{os.sep}")
_GUARDED_PROGRAMS = (
    "docker",
    "docker-compose",
    "claude",
    "alk",
    "agent-learn",
    "alk-harness-worker",
    "alk-harness-sandbox",
)
_HARMLESS = [sys.executable, "-c", "pass"]


@dataclass
class Launch(Mapping):
    argv: str
    env: dict | None

    # A launch also reads as its environment: launch["NAME"], dict(launch), launch.items().
    def __getitem__(self, name):
        return (self.env or {})[name]

    def __iter__(self):
        return iter(self.env or {})

    def __len__(self):
        return len(self.env or {})

    def __eq__(self, other):
        if isinstance(other, Launch):
            return (self.argv, self.env) == (other.argv, other.env)
        if isinstance(other, Mapping):
            return dict(self) == dict(other)
        return NotImplemented

    __hash__ = None


class _WorkerGuard:
    def __init__(self, real_popen):
        self.real_popen = real_popen
        self.real_killpg = os.killpg
        self.expected = False
        self.launches: list[Launch] = []
        self.children: list = []
        self.child = list(_HARMLESS)
        self.on_start = None

    @staticmethod
    def guarded(args, executable=None) -> str | None:
        if isinstance(args, (str, bytes, os.PathLike)):
            text = os.fsdecode(args)
        else:
            text = " ".join(os.fsdecode(part) for part in args)
        # The program can hide in the `executable=` kwarg (argv[0] is then only a label).
        if executable is not None:
            text = f"{os.fsdecode(executable)} {text}"
        # Shell glue without spaces ("cd /tmp&&docker ...", "true;docker ...") joins words.
        words = [
            os.path.basename(word.strip("\"'`()"))
            for word in re.split(r"[\s;&|]+", text)
        ]
        if any(name in text for name in _GUARDED) or any(
            word in _GUARDED_PROGRAMS for word in words
        ):
            return text
        return None

    def popen(self, args, *more, **kwargs):
        text = self.guarded(args, kwargs.get("executable"))
        if text is None:
            return self.real_popen(args, *more, **kwargs)
        self.launches.append(Launch(text, kwargs.get("env")))
        options = {
            name: value
            for name, value in kwargs.items()
            if name not in ("env", "shell", "executable", "cwd", "start_new_session")
        }
        child = self.real_popen(
            list(self.child),
            **options,
            start_new_session=True,
            env={"PATH": os.environ.get("PATH", "")},
        )
        self.children.append(child)
        if self.on_start is not None:
            self.on_start(child)
        return child

    def unexpected(self) -> list[str]:
        return [] if self.expected else [launch.argv for launch in self.launches]

    def close(self) -> None:
        # The real killpg, captured at the start: a test may patch os.killpg.
        for child in self.children:
            try:
                self.real_killpg(child.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


@pytest.fixture(autouse=True)
def worker_guard(monkeypatch):
    guard = _WorkerGuard(subprocess.Popen)
    monkeypatch.setattr(subprocess, "Popen", guard.popen)
    yield guard
    guard.close()
    assert not guard.unexpected(), (
        f"a test started a worker, CLI or container without fake_worker: {guard.unexpected()}"
    )


class _FakeWorker:
    def __init__(self, guard):
        self.guard = guard
        guard.expected = True

    @property
    def launches(self) -> list[Launch]:
        return self.guard.launches

    @property
    def children(self) -> list:
        return self.guard.children

    def sleeping(self, seconds: float = 30) -> "_FakeWorker":
        self.guard.child = [sys.executable, "-c", f"import time; time.sleep({seconds})"]
        return self

    def child(self, argv: list[str]) -> "_FakeWorker":
        self.guard.child = list(argv)
        return self

    def on_start(self, hook) -> "_FakeWorker":
        self.guard.on_start = hook
        return self


@pytest.fixture
def fake_worker(worker_guard):
    return _FakeWorker(worker_guard)


# The names a worker inherits that a test keeps; every other one is removed from this process.
_KEPT_NAMES = ("PATH", "LANG", "LC_ALL", "TMPDIR", "PYTHONPATH", "SSL_CERT_DIR", "SSL_CERT_FILE")


def scrub_environment(monkeypatch, home) -> None:
    for name in secrets.WORKER_ENVIRONMENT_NAMES:
        if name not in _KEPT_NAMES:
            monkeypatch.delenv(name, raising=False)
    # HOME is on the allow-list, so it's set after the deletions.
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


@pytest.fixture(autouse=True)
def _scrubbed_environment(_unauthenticated_development_server, monkeypatch, tmp_path):
    # A second barrier behind the guard: nothing a worker could inherit is the host's.
    scrub_environment(monkeypatch, tmp_path / "home")


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
    monkeypatch.setenv("LK_URL", "wss://fake.invalid")
    # Persisting and listing, not running: left to run, the job reaches its worker's start.
    executed = []

    async def idle(self, _job, _source):
        executed.append(True)

    monkeypatch.setattr(LocalSandbox, "_execute", idle)
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
    assert executed == [True]


def test_local_sandbox_reports_live_stage_timestamp_and_detail_from_events(
    tmp_path, monkeypatch
):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))

    # The stage comes from the event file, not the job. Left to run, the job reaches its
    # worker's start, where the client's loop closing after the request cancels it and waits
    # on a worker half started, or, past it, leaves a real worker running after the test.
    async def idle(self, _job, _source):
        await asyncio.sleep(0)

    monkeypatch.setattr(LocalSandbox, "_execute", idle)
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
    # Progress comes from the files, not the job: left to run, it reaches its worker's start.
    executed = []

    async def idle(self, _job, _source):
        executed.append(True)

    monkeypatch.setattr(LocalSandbox, "_execute", idle)
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
    assert executed == [True]


def test_the_environment_output_names_the_settings_the_kit_skipped(tmp_path):
    # A setting the kit skipped is in the job's result, by name only.
    from fi.alk.harness.sandbox_server import _stage_outputs

    (tmp_path / "environment.json").write_text(
        json.dumps(
            {
                "services": ["agent-runtime"],
                "project": "fagi-harness-1",
                "skipped_setting_names": ["LANG", "NO_PROXY"],
            }
        )
    )

    [environment] = [
        item for item in _stage_outputs(tmp_path) if item["kind"] == "environment"
    ]

    assert environment["data"]["skipped_settings"] == ["LANG", "NO_PROXY"]
    assert "LANG, NO_PROXY" in environment["summary"]


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


class _AwaitingDedicatedIsolation(Exception):
    """The route failed only on the runtime isolation hosted jobs still lack."""


@pytest.mark.xfail(
    strict=True,
    raises=_AwaitingDedicatedIsolation,
    reason="submit does not yet give hosted jobs the dedicated_vm runtime",
)
def test_public_github_branch_url_populates_job_ref(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    try:
        response = client.post(
            "/v1/jobs",
            json={
                "github_repository": "https://github.com/acme/agent/tree/feat/harness",
                "github_commit_sha": "a" * 40,
            },
        )
    except ValidationError as exc:
        if "hosted_isolation_must_be_dedicated_vm" in str(exc):
            raise _AwaitingDedicatedIsolation from exc
        raise
    if (
        response.status_code == 500
        and "hosted_isolation_must_be_dedicated_vm" in response.text
    ):
        raise _AwaitingDedicatedIsolation

    assert response.status_code == 200
    source = response.json()["job"]["source"]
    assert source["repository"] == "acme/agent"
    assert source["ref"] == "feat/harness"


@pytest.mark.xfail(
    strict=True,
    raises=_AwaitingDedicatedIsolation,
    reason="submit does not yet give hosted jobs the dedicated_vm runtime",
)
def test_private_github_submission_becomes_hosted_job(tmp_path):
    client = TestClient(create_app(tmp_path / "state"))

    try:
        response = client.post(
            "/v1/jobs",
            json={
                "github_repository": "customer/private-agent",
                "github_visibility": "private",
                "github_installation_id": "installation-42",
                "github_commit_sha": "a" * 40,
            },
        )
    except ValidationError as exc:
        if "hosted_isolation_must_be_dedicated_vm" in str(exc):
            raise _AwaitingDedicatedIsolation from exc
        raise
    if (
        response.status_code == 500
        and "hosted_isolation_must_be_dedicated_vm" in response.text
    ):
        raise _AwaitingDedicatedIsolation

    assert response.status_code == 200
    job = response.json()["job"]
    assert job["execution"] == "hosted"
    assert job["source"]["installation_id"] == "installation-42"
    assert "token" not in json.dumps(job).lower()


def test_a_github_submission_without_a_commit_sha_is_refused_with_its_code(tmp_path):
    # A GitHub job runs hosted, and a hosted GitHub job needs the commit it checks out. Without
    # one the route used to fail building the job (a 500 with no reason); it says why now.
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post("/v1/jobs", json={"github_repository": "acme/agent"})

    assert response.status_code == 422
    assert "github_commit_sha_required" in response.text


@pytest.mark.parametrize(
    ("repository", "reason"),
    [
        ("https://gitlab.com/acme/agent", "github_repository_invalid"),
        ("https://ghe.acme.test/acme/agent", "github_repository_invalid"),
        (
            "https://github.com/acme/agent/commit/" + "a" * 40,
            "github_repository_url_must_point_to_repository_or_branch",
        ),
    ],
)
def test_a_location_that_is_not_a_github_repository_is_refused_for_that_without_a_sha(
    tmp_path, repository, reason
):
    # The missing commit is not what is wrong with it: adding one would only uncover this.
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post("/v1/jobs", json={"github_repository": repository})

    assert response.status_code == 400
    assert reason in response.text
    assert "github_commit_sha_required" not in response.text


@pytest.mark.parametrize(
    "sha",
    [
        "main",
        "   ",
        "abc1234",
        "z" * 40,
        "g" * 40,
        "a" * 39,
        "a" * 41,
        " " + "a" * 40,
        "a" * 40 + "\n",
    ],
)
def test_a_commit_sha_that_is_not_forty_hex_characters_is_refused_with_its_code(tmp_path, sha):
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post(
        "/v1/jobs", json={"github_repository": "acme/agent", "github_commit_sha": sha}
    )

    assert response.status_code == 422
    assert "github_commit_sha_invalid" in response.text


def test_intake_and_the_job_share_one_commit_sha_rule():
    from fi.alk.harness.job import (
        RepositorySource,
        SourceKind,
        SourceVisibility,
        is_commit_sha,
    )

    assert is_commit_sha("0123456789abcdef0123456789ABCDEF01234567")
    assert not is_commit_sha("main")
    assert not is_commit_sha("")
    # The job model holds the same rule itself, not only the route in front of it.
    with pytest.raises(ValidationError, match="github_commit_sha_invalid"):
        RepositorySource(
            kind=SourceKind.GITHUB,
            repository="acme/agent",
            visibility=SourceVisibility.PUBLIC,
            commit_sha="main",
        )


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        ({"environment_values": {"PATH": "x"}}, "environment_name_reserved"),
        ({"connector_config": {"ALK_JOB_X": "x"}}, "connector_config_name_reserved"),
    ],
)
def test_a_reserved_name_is_reported_before_the_missing_commit(tmp_path, extra, reason):
    # Both are wrong; the reserved name is reported first, so adding a commit does not
    # just uncover the next refusal.
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post("/v1/jobs", json={"github_repository": "acme/agent", **extra})

    assert response.status_code == 422
    assert reason in response.text
    assert "github_commit_sha_required" not in response.text


def _job_model_codes():
    """Every refusal code job.py raises, read from its source: a new one is covered too."""
    import fi.alk.harness.job as job_module

    text = Path(job_module.__file__).read_text()
    heads = re.findall(r'raise ValueError\(\s*f?"([a-z0-9_]+)', text)
    return sorted(set(heads))


def _refusal(code):
    """A real pydantic ValidationError carrying ``code``, as a job.py validator raises it."""
    from pydantic import BaseModel as _Model
    from pydantic import model_validator as _after

    class _Refuses(_Model):
        note: str = ""

        @_after(mode="after")
        def _refuse(self):
            raise ValueError(code)

    try:
        _Refuses(note="submitted-secret-value")
    except ValidationError as exc:
        return exc
    raise AssertionError("the model did not refuse")


def _local_source(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    monkeypatch.setattr(LocalSandbox, "_execute", _idle_execute)
    return source


def test_every_job_model_code_is_classified_once():
    # A new refusal in job.py can't ship without saying whose it is: the caller's to fix, or
    # this sandbox's own configuration.
    import fi.alk.harness.job as job_module

    text = Path(job_module.__file__).read_text()
    # Every refusal is a ValueError opening with its code, so the list above sees it.
    assert len(re.findall(r"raise ValueError\(", text)) == len(
        re.findall(r'raise ValueError\(\s*f?"[a-z0-9_]+', text)
    )
    assert not re.search(r"PydanticCustomError|raise (?!ValueError\()", text)
    codes = set(_job_model_codes())
    submission, configuration = set(refusals.SUBMISSION), set(refusals.CONFIGURATION)

    assert not submission & configuration
    assert codes == submission | configuration


@pytest.mark.parametrize("code", sorted(refusals.SUBMISSION))
def test_a_job_model_refusal_is_a_cleaned_422_naming_its_code(tmp_path, monkeypatch, code):
    # Built inside the route, the job model's ValidationError used to be a bare 500, which
    # the platform reads as "sandbox unavailable". It is the caller's refusal, with its reason.
    source = _local_source(tmp_path, monkeypatch)

    def refuse(*_args, **_kwargs):
        raise _refusal(code)

    monkeypatch.setattr(sandbox_server, "HarnessJob", refuse)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post("/v1/jobs", json={"source_path": str(source)})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert [entry["msg"] for entry in detail] == [f"Value error, {code}"]
    # The shape of FastAPI's own 422 here, minus anything that can carry a submitted value.
    assert all(set(entry) == {"type", "loc", "msg"} for entry in detail)
    assert detail[0]["loc"] == ["body"]
    assert "submitted-secret-value" not in response.text
    # Nothing of the refused job is left behind.
    assert not any((tmp_path / "state" / "jobs").glob("*/job.json"))


@pytest.mark.parametrize("code", sorted(refusals.CONFIGURATION))
def test_a_refusal_of_this_sandboxs_own_configuration_is_a_500_naming_its_code(
    tmp_path, monkeypatch, code
):
    # Nothing in the submission can fix it, so it is not a 422; but it is not "down" either:
    # the code says what is misconfigured, so the platform can say something true.
    source = _local_source(tmp_path, monkeypatch)

    def refuse(*_args, **_kwargs):
        raise _refusal(code)

    monkeypatch.setattr(sandbox_server, "HarnessJob", refuse)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post("/v1/jobs", json={"source_path": str(source)})

    assert response.status_code == 500
    assert response.json() == {
        "detail": [
            {"type": "sandbox_configuration", "loc": ["sandbox"], "msg": f"Value error, {code}"}
        ]
    }
    assert "submitted-secret-value" not in response.text
    assert not any((tmp_path / "state" / "jobs").glob("*/job.json"))


def test_a_submission_and_a_configuration_refusal_together_answer_the_callers_422(caplog):
    # A 5xx is only for a request with no fault of its own: the caller hears about their
    # part, and ours is logged as an error rather than lost behind it.
    from pydantic import BaseModel as _Model
    from pydantic import model_validator as _after

    class _Theirs(_Model):
        @_after(mode="after")
        def _refuse(self):
            raise ValueError("github_commit_sha_invalid")

    class _Ours(_Model):
        @_after(mode="after")
        def _refuse(self):
            raise ValueError("hosted_isolation_must_be_dedicated_vm")

    class _Both(_Model):
        source: _Theirs
        runtime: _Ours

    try:
        _Both(source={}, runtime={})
    except ValidationError as exc:
        both = exc
    assert len(both.errors()) == 2

    with caplog.at_level("ERROR", logger=refusals.__name__):
        refused = JobRefused([(both, None)], job_id="job-1")

    assert refused.status_code == 422
    assert [entry["msg"] for entry in refused.detail] == [
        "Value error, github_commit_sha_invalid"
    ]
    assert all(entry["loc"] == ["body"] for entry in refused.detail)
    logged = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "hosted_isolation_must_be_dedicated_vm" in m and "job-1" in m for m in logged
    )


def test_a_real_configuration_refusal_reaches_the_caller_as_a_500_with_its_code(
    tmp_path, caplog
):
    # A GitHub job runs hosted, and submit doesn't yet give it the isolation a hosted job
    # needs: that is ours to fix, and the caller is told what, not that we are down.
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    with caplog.at_level("ERROR", logger=refusals.__name__):
        response = client.post(
            "/v1/jobs",
            json={"github_repository": "acme/agent", "github_commit_sha": "a" * 40},
        )

    assert response.status_code == 500
    assert response.json()["detail"][0]["type"] == "sandbox_configuration"
    assert "hosted_isolation_must_be_dedicated_vm" in response.text
    # Ours alone, so the platform is not the only one that knows: the sandbox logs it too,
    # with the job it refused.
    logged = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any(
        "hosted_isolation_must_be_dedicated_vm" in m
        and re.search(r"job_id=[0-9a-f-]{36}", m)
        for m in logged
    ), logged


_BAD_TRIAL = {
    "execution_key": "k",
    "scenario_key": "s",
    "scenario_id": "x",
    "trial_index": "two",
    "call_execution_id": "c",
}


def test_two_faults_in_one_submission_come_back_in_one_422(tmp_path, monkeypatch):
    # The connector misses its assistant and the plan has a bad value: one request, both
    # named, rather than one per round trip.
    source = _local_source(tmp_path, monkeypatch)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post(
        "/v1/jobs",
        json={
            "source_path": str(source),
            "connector": "vapi",
            "connector_mode": "provider_import",
            "scenario_count": 1,
            "metadata": {"execution_manifest": [_BAD_TRIAL]},
        },
    )

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert {
        "type": "value_error",
        "loc": ["body"],
        "msg": "Value error, provider_import_requires_assistant_id",
    } in detail
    assert [e for e in detail if e["type"] == "int_parsing"] == [
        {
            "type": "int_parsing",
            "loc": ["body", "metadata", "execution_manifest", 0, "trial_index"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
        }
    ]
    assert len(detail) == 2
    assert not any((tmp_path / "state" / "jobs").glob("*/job.json"))


_GOOD_ENTRY = {**_BAD_TRIAL, "trial_index": 1}


def test_a_part_refused_and_a_plan_of_the_wrong_size_come_back_in_one_422(
    tmp_path, monkeypatch
):
    # The count used to be checked only by the job, after every part had built: the caller
    # fixed the connector and was then told the plan was the wrong size.
    source = _local_source(tmp_path, monkeypatch)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post(
        "/v1/jobs",
        json={
            "source_path": str(source),
            "connector": "vapi",
            "connector_mode": "provider_import",
            "scenario_count": 2,
            "metadata": {"execution_manifest": [_GOOD_ENTRY]},
        },
    )

    assert response.status_code == 422, response.text
    assert sorted(entry["msg"] for entry in response.json()["detail"]) == [
        "Value error, execution_manifest_count_mismatch",
        "Value error, provider_import_requires_assistant_id",
    ]


def test_a_bad_entry_and_a_repeated_key_in_the_plan_come_back_in_one_422(
    tmp_path, monkeypatch
):
    source = _local_source(tmp_path, monkeypatch)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post(
        "/v1/jobs",
        json={
            "source_path": str(source),
            "scenario_count": 2,
            "metadata": {"execution_manifest": [_BAD_TRIAL, _GOOD_ENTRY]},
        },
    )

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == [
        {
            "type": "int_parsing",
            "loc": ["body", "metadata", "execution_manifest", 0, "trial_index"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
        },
        {
            "type": "value_error",
            "loc": ["body"],
            "msg": "Value error, execution_manifest_duplicate_key",
        },
    ]


def test_an_error_without_a_code_names_the_field_it_is_about(tmp_path, monkeypatch):
    source = _local_source(tmp_path, monkeypatch)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post(
        "/v1/jobs",
        json={
            "source_path": str(source),
            "scenario_count": 1,
            "metadata": {"execution_manifest": [_BAD_TRIAL]},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == [
        {
            "type": "int_parsing",
            "loc": ["body", "metadata", "execution_manifest", 0, "trial_index"],
            "msg": "Input should be a valid integer, unable to parse string as an integer",
        }
    ]


def test_an_error_without_a_code_in_a_value_submit_sets_itself_is_ours(
    tmp_path, monkeypatch, caplog
):
    # Submit chooses the artifact policy, so a bad value there (a renamed level, say) is this
    # sandbox's fault: a 500 naming it, not a 422 telling the caller to change what they sent.
    source = _local_source(tmp_path, monkeypatch)
    real = sandbox_server.HarnessArtifactPolicy
    monkeypatch.setattr(
        sandbox_server,
        "HarnessArtifactPolicy",
        lambda **kwargs: real(**{**kwargs, "level": "everything"}),
    )
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    with caplog.at_level("ERROR", logger=refusals.__name__):
        response = client.post("/v1/jobs", json={"source_path": str(source)})

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert [entry["type"] for entry in detail] == ["sandbox_configuration"]
    assert detail[0]["loc"] == ["sandbox"]
    assert any(r.levelname == "ERROR" for r in caplog.records)


def test_a_malformed_commit_is_refused_before_any_upload_is_claimed(hosted_sandbox):
    # The boundary refuses it: the uploaded credential never leaves pending/, so nothing has
    # to be put back.
    sandbox = hosted_sandbox

    async def upload():
        return await _upload_credential(sandbox, "GOOD")

    key = asyncio.run(upload())
    for sha, code in (
        (None, "github_commit_sha_required"),
        ("main", "github_commit_sha_invalid"),
    ):
        with pytest.raises(ValidationError, match=code):
            LocalSandboxRequest(
                github_repository="acme/agent",
                github_commit_sha=sha,
                secret_refs={"GOOD": _ref("mounted", key)},
            )
    assert len(list((sandbox.secret_files_root / "pending").iterdir())) == 1


def test_a_refusal_whose_uploads_cannot_be_put_back_still_answers_and_cleans_up(
    hosted_sandbox, monkeypatch, caplog
):
    # The credential is claimed, the job is refused, and putting the upload back fails: the
    # caller still gets the 422, the claim is still deleted, and the loss is logged.
    sandbox = hosted_sandbox
    sandbox._execute = _idle_execute

    def refuse(**_kwargs):
        raise _refusal("execution_manifest_invalid")

    claims_seen = []

    def stuck(self, job_id, _references):
        claims_seen.append(
            (self.secret_files_root / "jobs" / job_id / "GOOD").is_file()
        )
        raise OSError("pending/ is read-only")

    monkeypatch.setattr(sandbox_server, "HarnessJob", refuse)
    monkeypatch.setattr(LocalSandbox, "_return_job_secret_files", stuck)

    async def submit():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        with caplog.at_level("ERROR", logger=sandbox_server.__name__):
            with pytest.raises(JobRefused) as refused:
                sandbox.submit(
                    LocalSandboxRequest(
                        source_id=source_id,
                        secret_refs={"GOOD": _ref("mounted", upload)},
                    )
                )
        return refused.value

    refused = asyncio.run(submit())

    assert refused.status_code == 422
    assert "execution_manifest_invalid" in str(refused)
    # The upload was claimed before the refusal, so there was a claim to clean up.
    assert claims_seen == [True]
    _assert_nothing_left(sandbox)
    assert not any((sandbox.secret_files_root / "pending").iterdir())
    assert any(
        r.levelname == "ERROR"
        and r.getMessage().startswith("job_refused_uploads_not_returned")
        for r in caplog.records
    )


def test_an_unexpected_failure_building_the_job_is_still_a_500(tmp_path, monkeypatch):
    source = _local_source(tmp_path, monkeypatch)

    def broken(*_args, **_kwargs):
        raise RuntimeError("not a refusal")

    monkeypatch.setattr(sandbox_server, "HarnessJob", broken)
    client = TestClient(create_app(tmp_path / "state"), raise_server_exceptions=False)

    response = client.post("/v1/jobs", json={"source_path": str(source)})

    assert response.status_code == 500
    assert "not a refusal" not in response.text


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
    tmp_path, monkeypatch, fake_worker
):
    source = tmp_path / "sources" / "fixture"
    source.mkdir(parents=True)
    (source / "server.py").write_text("print('source')")
    (source / "optional.py").write_text(
        "import os\ndef unused(): return os.environ['UNUSED_TOKEN']\n"
    )
    (source / "compose.yaml").write_text("invalid unselected document")
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    launched = fake_worker.launches
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


class _Acquirer:
    """Stands in for the clone: a repository holding two agents, counting how often it is asked."""

    calls = 0
    on_acquire = None

    def __init__(self, _token_provider) -> None:
        pass

    async def acquire(self, job, workspace):
        type(self).calls += 1
        checkout = workspace / "repository"
        for name, variable in (("a", "A_ONLY_API_KEY"), ("b", "B_ONLY_API_KEY")):
            (checkout / "agents" / name).mkdir(parents=True)
            (checkout / "agents" / name / "agent.py").write_text(
                f'import os\nKEY = os.environ["{variable}"]\n'
            )
        if type(self).on_acquire is not None:
            type(self).on_acquire(job)
        return checkout


@pytest.fixture
def cloning(monkeypatch):
    from fi.alk.harness import sandbox_server

    _Acquirer.calls = 0
    _Acquirer.on_acquire = None
    monkeypatch.setattr(sandbox_server, "GitHubSourceAcquirer", _Acquirer)
    return sandbox_server


@pytest.fixture
def hosted_github_job(monkeypatch):
    # submit() builds a GitHub job without the dedicated-VM runtime a hosted job requires, so
    # today it refuses every GitHub submission. These tests cover what follows that step.
    from fi.alk.harness import sandbox_server
    from fi.alk.harness.job import RuntimeIsolation, RuntimeRequirements

    class HostedJob(sandbox_server.HarnessJob):
        runtime: RuntimeRequirements = Field(
            default_factory=lambda: RuntimeRequirements(isolation=RuntimeIsolation.DEDICATED_VM)
        )

    monkeypatch.setattr(sandbox_server, "HarnessJob", HostedJob)


def _github_request(subfolder: str, **extra) -> LocalSandboxRequest:
    return LocalSandboxRequest(
        github_repository="acme/agents",
        github_commit_sha="a" * 40,
        github_subfolder=subfolder,
        metadata={"generic_harness_v1": True},
        **extra,
    )


def _executed(sandbox: LocalSandbox, request: LocalSandboxRequest):
    async def run():
        response = sandbox.submit(request)
        await sandbox._tasks[response.job.job_id]
        return response.job.job_id

    job_id = asyncio.run(run())
    return json.loads((sandbox.jobs_root / job_id / "state.json").read_text())


def test_github_subfolder_reaches_source(tmp_path, hosted_github_job):
    sandbox = LocalSandbox(tmp_path / "state")
    seen = {}

    async def idle(job, _source):
        seen["subfolder"] = job.source.subfolder

    sandbox._execute = idle

    async def run():
        response = sandbox.submit(_github_request("agents/a"))
        await sandbox._tasks[response.job.job_id]

    asyncio.run(run())

    assert seen == {"subfolder": "agents/a"}


def test_github_subfolder_requires_repository(tmp_path):
    with pytest.raises(ValueError, match="needs a GitHub repository"):
        LocalSandboxRequest(
            source_path=str(tmp_path),
            github_subfolder="agents/a",
            metadata={"generic_harness_v1": True},
        )
    assert LocalSandboxRequest(source_path=str(tmp_path), github_subfolder="").source_path


def test_github_subfolder_requires_generic(tmp_path):
    with pytest.raises(ValueError, match="needs the generic harness"):
        LocalSandboxRequest(github_repository="acme/agents", github_subfolder="agents/a")


def test_bad_subfolder_fails_once_without_retry(tmp_path, cloning, hosted_github_job):
    sandbox = LocalSandbox(tmp_path / "state")

    state = _executed(sandbox, _github_request("agents/missing"))

    assert _Acquirer.calls == 1
    assert state["stage"] == "failed"
    failure = state["failure"]
    assert (failure["domain"], failure["stage"], failure["code"], failure["retryable"]) == (
        "agent",
        "acquiring_source",
        "subfolder_not_found",
        False,
    )
    assert failure["details"] == {"subfolder": "agents/missing"}


def test_bad_subfolder_keeps_canceled(tmp_path, cloning, hosted_github_job):
    sandbox = LocalSandbox(tmp_path / "state")

    def cancel(job):
        path = sandbox.jobs_root / job.job_id / "state.json"
        state = json.loads(path.read_text())
        state["stage"] = "canceled"
        path.write_text(json.dumps(state))

    _Acquirer.on_acquire = cancel

    state = _executed(sandbox, _github_request("agents/missing"))

    assert state["stage"] == "canceled"
    assert not state.get("failure")


def test_the_credentials_walk_runs_off_the_servers_loop(
    tmp_path, cloning, monkeypatch, hosted_github_job, fake_worker
):
    import threading

    from fi.alk.harness import sandbox_server

    # The walk reads the agent's files at bundling's limits: seconds, on a crafted repository.
    sandbox = LocalSandbox(tmp_path / "state")
    threads: list[threading.Thread] = []
    discover = sandbox_server.discover_credentials

    def recorded(*args, **kwargs):
        threads.append(threading.current_thread())
        return discover(*args, **kwargs)

    def stop(*args, **kwargs):
        raise RuntimeError("stop after the credentials are written")

    monkeypatch.setattr(sandbox_server, "discover_credentials", recorded)
    monkeypatch.setattr(cloning, "resolve_worker_secrets", stop)

    async def run():
        response = sandbox.submit(_github_request("agents/a"))
        await sandbox._tasks[response.job.job_id]

    asyncio.run(run())

    assert threads and threading.main_thread() not in threads


def test_subfolder_scopes_credentials(
    tmp_path, cloning, monkeypatch, hosted_github_job, fake_worker
):
    sandbox = LocalSandbox(tmp_path / "state")

    def stop(*args, **kwargs):
        raise RuntimeError("stop after the credentials are written")

    monkeypatch.setattr(cloning, "resolve_worker_secrets", stop)

    async def run():
        response = sandbox.submit(_github_request("agents/a"))
        await sandbox._tasks[response.job.job_id]
        return response.job.job_id

    job_id = asyncio.run(run())

    manifest = json.loads((sandbox.jobs_root / job_id / "credentials.json").read_text())
    required = {item["environment_name"]: item["required"] for item in manifest["requirements"]}
    assert required["A_ONLY_API_KEY"] is True
    assert required["B_ONLY_API_KEY"] is False


def test_live_contract_output_loses_url_credentials_from_a_raw_contract(tmp_path):
    """B3d (TH-9491, plan v5.1 §W9): a contract.json written directly, not by write_contract,
    as a job from before the fix left it, is cleaned on its way to the live run view."""
    from fi.alk.harness.sandbox_server import _stage_outputs

    url = "https://bot:hunter2@hooks.example/v1?key=sk-1#frag"
    (tmp_path / "contract.json").write_text(
        json.dumps(
            {
                "one_liner": f"Books rides through {url}",
                "modality": "voice",
                "tools": [{"name": "book_ride"}],
                "hard_constraints": [f"Always post to {url}", "Confirm before booking"],
            }
        ),
        encoding="utf-8",
    )

    [contract] = [item for item in _stage_outputs(tmp_path) if item["kind"] == "contract"]

    assert contract["data"]["one_liner"] == "Books rides through https://hooks.example/v1"
    assert contract["data"]["hard_constraints"] == [
        "Always post to https://hooks.example/v1",
        "Confirm before booking",
    ]
    assert "hunter2" not in json.dumps(contract) and "sk-1" not in json.dumps(contract)


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
def launched_workers(fake_worker):
    # Each launch reads as its environment: launched_workers[0]["NAME"].
    return fake_worker.launches


def _ref(manager, key):
    return SecretRef(manager=manager, key=key, purpose="target_provider")


def _controller_ref(manager, key):
    # A connection ref for the controller itself, not the customer agent's own credential.
    return SecretRef(manager=manager, key=key, purpose="livekit_url")


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


def test_a_refused_job_leaves_its_uploaded_credential_for_the_corrected_resubmit(
    hosted_sandbox, monkeypatch
):
    # The refusal is the caller's to fix; fixing it must not need the credential uploaded
    # again, so the claim on it is undone rather than the file deleted.
    sandbox = hosted_sandbox
    sandbox._execute = _idle_execute
    build = sandbox_server.HarnessJob
    refusals_left = [_refusal("execution_manifest_invalid")]

    class _RefusesOnce(build):
        def __init__(self, **kwargs):
            if refusals_left:
                raise refusals_left.pop()
            super().__init__(**kwargs)

    monkeypatch.setattr(sandbox_server, "HarnessJob", _RefusesOnce)

    async def submit_twice():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        request = LocalSandboxRequest(
            source_id=source_id, secret_refs={"GOOD": _ref("mounted", upload)}
        )
        with pytest.raises(JobRefused, match="execution_manifest_invalid"):
            sandbox.submit(request)
        return sandbox.submit(request)

    submitted = asyncio.run(submit_twice())

    claimed = sandbox.secret_files_root / "jobs" / submitted.job.job_id / "GOOD"
    assert claimed.read_bytes() == b"credential-body"
    assert not any((sandbox.secret_files_root / "pending").iterdir())


def test_hosted_submit_still_refuses_a_mounted_server_key(hosted_sandbox):
    sandbox = hosted_sandbox
    sandbox._execute = _idle_execute

    async def submit():
        source_id = await _upload_archive(sandbox)
        upload = await _upload_credential(sandbox, "GOOD")
        with pytest.raises(
            JobRefused, match="hosted_secret_manager_unsupported: OTHER_VALUE"
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
    if (
        response.status_code == 500
        and "hosted_isolation_must_be_dedicated_vm" in response.text
    ):
        raise _AwaitingDedicatedIsolation

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
    return dict(launched[0])


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


_NO_AGENT_NEEDS = [
    "DOCKER_CONFIG", "COMPOSE_FILE", "BASH_ENV", "ENV", "LD_PRELOAD",
    "DYLD_INSERT_LIBRARIES", "GIT_SSH_COMMAND", "SSH_ASKPASS", "NODE_OPTIONS",
]
_AGENT_SETTINGS = [
    "HTTPS_PROXY", "http_proxy", "NO_PROXY", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE",
    # A customer's own credentials under a tool's prefix: git, ssh and docker don't read these.
    "GIT_TOKEN", "GIT_ACCESS_TOKEN", "SSH_PRIVATE_KEY", "DOCKER_PASSWORD", "DOCKER_USERNAME",
]


@pytest.mark.parametrize("name", _NO_AGENT_NEEDS)
@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest])
def test_a_name_no_agent_needs_is_refused_on_both_channels(name, request_type):
    # Runtime values reach the Compose subprocess under their own names, where these steer it.
    with pytest.raises(ValidationError, match=f"connector_config_name_reserved: {name}"):
        request_type(source_path="/tmp/agent", connector_config={name: "canary"})
    with pytest.raises(ValidationError, match=f"environment_name_reserved: {name}"):
        request_type(source_path="/tmp/agent", environment_values={name: "canary"})


def test_a_job_value_does_not_override_the_kits_port_variable(tmp_path, monkeypatch):
    # The kit allocates the published port; a job value of the same name (MY_PORT=1.2.3.4:9, or
    # 0.0.0.0:2375) must not replace it in Compose's environment.
    from fi.alk.harness import provision

    monkeypatch.setenv("ALK_RUNTIME_VALUE_MY_PORT", "1.2.3.4:9")
    calls = []

    def fake_run(command, **kwargs):
        # What Compose sees: the job env file (read now, it is deleted after the call) under the
        # process environment.
        seen = dict(kwargs["env"])
        job_file = [c for i, c in enumerate(command) if i and command[i - 1] == "--env-file"][-1]
        with open(job_file, encoding="utf-8") as handle:
            for line in filter(None, handle.read().split("\n")):
                key, _, quoted = line.partition("=")
                seen.setdefault(key, quoted[1:-1])
        calls.append((dict(kwargs["env"]), seen))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(provision.subprocess, "run", fake_run)
    compose = tmp_path / "compose.yml"
    compose.write_text("services: {}\n")
    environment = provision.ProvisionedEnvironment(
        source=str(tmp_path),
        compose_file=str(compose),
        project="fagi-harness-port",
        runtime_configuration_names=["MY_PORT"],
        port_variables={"MY_PORT": "0"},
    )
    provision._run(environment, "ps")
    process_env, seen = calls[0]
    assert seen["MY_PORT"] == "0"
    assert "MY_PORT" not in process_env


@pytest.mark.parametrize(
    "field", ["environment_values", "connector_config"]
)
def test_a_refused_value_is_not_echoed_in_the_422(tmp_path, field):
    client = TestClient(create_app(tmp_path / "state"))
    response = client.post(
        "/v1/jobs",
        json={"source_path": "/tmp/agent", field: {"DOCKER_CONFIG": "canary-echo-0042"}},
    )
    assert response.status_code == 422
    assert "canary-echo-0042" not in response.text


def test_a_rerun_refuses_a_name_no_agent_needs():
    with pytest.raises(ValidationError, match="environment_name_reserved: DOCKER_CONFIG"):
        SandboxRerunRequest(environment_values={"DOCKER_CONFIG": "x"})


@pytest.mark.parametrize(
    "name",
    ["alk_sandbox_token", "Alk_Anything", "pythonpath", "fi_api_key", "harness_platform_url"],
)
def test_a_runner_name_is_refused_in_any_case(name):
    with pytest.raises(ValidationError, match="name_reserved"):
        LocalSandboxRequest(source_path="/tmp/agent", connector_config={name: "x"})
    with pytest.raises(ValidationError, match="name_reserved"):
        LocalSandboxRequest(source_path="/tmp/agent", environment_values={name: "x"})


def test_a_refused_name_says_why_and_what_to_do(request_type=LocalSandboxRequest):
    with pytest.raises(ValidationError, match="steers a process the runner starts.*another name"):
        request_type(source_path="/tmp/agent", environment_values={"DOCKER_CONFIG": "x"})


@pytest.mark.parametrize("name", _AGENT_SETTINGS)
@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest])
def test_proxy_ca_and_credential_names_stay_agent_settings(name, request_type):
    request_type(source_path="/tmp/agent", connector_config={name: "canary"})
    request_type(source_path="/tmp/agent", environment_values={name: "canary"})


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
        "ANTHROPIC_API_KEY",
    ],
)
def test_a_reserved_alias_for_a_controller_variable_is_refused(request_type, alias):
    for manager in ("environment", "mounted", "futureagi"):
        with pytest.raises(ValidationError, match=f"secret_alias_reserved: {alias}"):
            request_type(
                source_path="/tmp/agent",
                secret_refs={alias: _controller_ref(manager, "SOME_NEUTRAL_KEY")},
            )


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest])
@pytest.mark.parametrize(
    "alias", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "GOOGLE_API_KEY"]
)
def test_the_agents_own_provider_credential_is_accepted(request_type, alias):
    request_type(source_path="/tmp/agent", secret_refs={alias: _ref("environment", "MY_KEY")})


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest])
@pytest.mark.parametrize("alias", ["PYTHONPATH", "HARNESS_PLATFORM_API_KEY", "ALK_SANDBOX_TOKEN"])
def test_the_agents_own_credential_takes_the_environment_value_rule(request_type, alias):
    with pytest.raises(ValidationError, match=f"secret_alias_reserved: {alias}"):
        request_type(source_path="/tmp/agent", secret_refs={alias: _ref("environment", "MY_KEY")})


def test_the_agents_own_claude_key_reaches_the_agent_and_not_the_controller(
    local_sandbox, launched_workers, monkeypatch
):
    monkeypatch.setenv("MY_ANTHROPIC", "customer-claude-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    _run_local_job(local_sandbox, refs={"ANTHROPIC_API_KEY": _ref("environment", "MY_ANTHROPIC")})

    child = _only_worker_env(launched_workers)
    assert child["ALK_RUNTIME_VALUE_ANTHROPIC_API_KEY"] == "customer-claude-key"
    assert "ANTHROPIC_API_KEY" in child["ALK_RUNTIME_CONFIGURATION_NAMES"].split(",")
    assert "ANTHROPIC_API_KEY" not in child


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

    _run_local_job(local_sandbox, refs={"X": _controller_ref("environment", "LK_URL")})

    assert _only_worker_env(launched_workers)["X"] == "wss://dev.invalid"


@pytest.mark.parametrize(
    "key",
    [
        "HARNESS_PLATFORM_API_KEY",
        "AGENTCC_API_KEY",
        "GITHUB_INSTALLATION_1_TOKEN",
        "ALK_SANDBOX_TOKEN",
        "PYTHONPATH",
        # Covered only by the client half of the predicate.
        "CLAUDE_CODE_OAUTH_TOKEN",
        "HTTPS_PROXY",
    ],
)
@pytest.mark.parametrize("manager", ["environment", "mounted", "futureagi"])
def test_development_mode_still_refuses_a_reserved_key_under_a_neutral_alias(
    local_sandbox, launched_workers, monkeypatch, key, manager
):
    monkeypatch.setenv(key, "runner-canary")

    job = _run_local_job(local_sandbox, refs={"X": _ref(manager, key)})

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


def _local_rerun_refused(sandbox, key, manager="environment"):
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
                        "X": _ref(manager, key),
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


@pytest.mark.parametrize("manager", ["environment", "mounted", "futureagi"])
def test_development_mode_local_rerun_refuses_a_runner_key(local_sandbox, monkeypatch, manager):
    sandbox = local_sandbox
    monkeypatch.setenv("HARNESS_PLATFORM_API_KEY", "runner-canary")

    job, _before, refused = _local_rerun_refused(sandbox, "HARNESS_PLATFORM_API_KEY", manager)

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
            secret_refs={"no_proxy": _controller_ref("environment", "SOME_NEUTRAL_KEY")},
        )


# --- TH-9550, amendment 4.1: the customer's own model keys are the agent's, never the controller's

_PROVIDER_NAMES = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
]
# Exception 1a: an environment value for a controller connection name also reaches the controller.
_CONTROLLER_CONNECTION_PROVIDER_NAMES = {"OPENAI_API_KEY", "GEMINI_API_KEY"}
# Exception 1b: a local job's simulator runs on the customer's own keys for these.
_LOCAL_SIMULATOR_FALLBACK_NAMES = {"GOOGLE_API_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"}
_ALIASES_ENV = "ALK_TARGET_PROVIDER_ALIASES"
_ROUTES = ["environment_value", "target_provider_ref"]


def _canary(name):
    return f"canary-{name.lower().replace('_', '-')}-1"


def _reaches_controller(route, name):
    """True where the spec lets the value into the controller under its own name (1a)."""
    return route == "environment_value" and name in _CONTROLLER_CONNECTION_PROVIDER_NAMES


def _provider_fields(route, name, monkeypatch, canary):
    """A job supplying ``name`` as an environment value or as a caller-sent target_provider ref.

    The ref is a dev-mode local ref keyed to a neutral server variable (rule 4 refuses a reserved
    key), on a local job.
    """
    if route == "environment_value":
        return {"environment_values": {name: canary}}
    monkeypatch.setenv("MY_PROVIDER_SOURCE", canary)
    return {"secret_refs": {name: _ref("environment", "MY_PROVIDER_SOURCE")}}


def _assert_holders(environment, name, canary, route, where):
    """The value sits in the runtime namespace, and under its own name only for exception 1a."""
    allowed = {f"ALK_RUNTIME_VALUE_{name}"}
    if _reaches_controller(route, name):
        allowed.add(name)
    holders = {key for key, value in environment.items() if value == canary}
    assert holders == allowed, where
    assert (name in environment) == _reaches_controller(route, name), where


def _assert_no_holder(environment, name, canary, where):
    assert name not in environment, where
    assert canary not in environment.values(), where


def _agent_process_environment(worker_environment, job, tmp_path, monkeypatch):
    """The environment of a stub agent spawned the way ProcessRuntimeProvider spawns one.

    The worker's environment becomes this process's, as it is the worker's. The values are what
    local_certified_runtime hands the provider (``_runtime_values``), loaded through the provider's
    own secrets loader with the purposes from job.json, and selected by a process that claims
    TARGET_PROVIDER. The spawn stops at the runner, which records the environment it was given.
    """
    monkeypatch.setattr(os, "environ", dict(worker_environment))
    work = tmp_path / "agent-work"
    work.mkdir()
    (work / "job.json").write_text(job.model_dump_json(), encoding="utf-8")
    secrets = work / "secrets.json"
    secrets.write_text(
        json.dumps(local_certified_runtime._runtime_values(job)), encoding="utf-8"
    )
    provider = process_runtime.ProcessRuntimeProvider(secrets_path=secrets)
    values, purposes = process_runtime._load_and_delete_secrets(
        provider._secrets_path,
        work_directory=work,
        secret_purpose_map=provider._secret_purpose_map,
    )
    assert not secrets.exists()
    process = SourceProcess(
        name="agent",
        kind="source",
        working_directory=".",
        build_commands=[],
        run_command=["python3", "-c", "pass"],
        environment={},
        secret_purposes=["target_provider"],
        user=ProcessUser.SVC_TOOLS,
        depends_on=[],
    )
    captured = {}

    class Handle:
        def is_running(self):
            return True

        def captured_output(self):
            return ""

        def terminate(self):
            pass

        def interrupt(self):
            pass

        def wait(self, timeout):
            return True

        def kill(self):
            pass

    def runner(argv, *, cwd, env, log_path, user=None, group=None):
        captured.update(env)
        return Handle()

    (tmp_path / "agent-build").mkdir()
    process_runtime.spawn_source_process(
        process,
        build_dir=tmp_path / "agent-build",
        world_dir=tmp_path / "agent-world",
        world_index=0,
        port_plan=process_runtime.PortPlan(
            ordinals={"agent": 0},
            job_shared=frozenset(),
            fixed_ports={},
            effective_instances=1,
            degraded_reason=None,
        ),
        configuration_addresses={},
        secret_values=values,
        secret_purposes=purposes,
        runner=runner,
    )
    return captured


def _compose_session(tmp_path, monkeypatch, services_environment, label):
    """A recorded agent runtime whose Compose config names every variable in the mapping.

    Returns (session, docker_calls); docker_calls records (argv, environment) of every docker
    invocation at provision's subprocess.run, the boundary of this process.
    """
    names = provision._contract_runtime_configuration_names(None)
    source = tmp_path / f"{label}-agent"
    session = tmp_path / f"{label}-session"
    source.mkdir()
    compose = source / "compose.yml"
    compose.write_text("services:\n  agent-runtime:\n    image: example\n", encoding="utf-8")
    provision.ProvisionedEnvironment(
        source=str(source.resolve()),
        compose_file=str(compose),
        project="fagi-harness-byok",
        runtime_services=["agent-runtime"],
        runtime_configuration_names=names,
        running=True,
    ).save(session)
    docker_calls = []

    def fake_run(command, **kwargs):
        # What Compose sees: the job env file (read now, it is deleted after the call) under the
        # process environment. Job values reach Compose only through that file.
        seen = dict(kwargs["env"])
        if "--env-file" in command:
            job_file = [c for i, c in enumerate(command) if i and command[i - 1] == "--env-file"][-1]
            with open(job_file, encoding="utf-8") as handle:
                lines = handle.read().split("\n")
            for line in filter(None, lines):
                key, _, quoted = line.partition("=")
                seen.setdefault(
                    key,
                    re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), quoted[1:-1]),
                )
        docker_calls.append((list(command), seen))
        rendered = {
            "services": {
                "agent-runtime": {"image": "example", "environment": services_environment}
            }
        }
        stdout = json.dumps(rendered) if "config" in command else ""
        return subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(provision.subprocess, "run", fake_run)
    monkeypatch.setattr(
        provision, "_docker", lambda *a, **_k: "running healthy" if a and a[0] == "inspect" else ""
    )
    return session, docker_calls


def _agent_runtime_command(worker_environment, tmp_path, monkeypatch, label="start"):
    """Start the agent's Compose runtime from a worker's environment, up to the docker client."""
    monkeypatch.setattr(os, "environ", dict(worker_environment))
    session, docker_calls = _compose_session(tmp_path, monkeypatch, {}, label)
    provision.start_runtime(session, stable_seconds=0.25)
    return next(call for call in docker_calls if "run" in call[0] and "config" not in call[0])


def _activate(worker_environment, tmp_path, monkeypatch, *, controller_lacks=(), label="activate"):
    """provision.activate_voice_environment in a worker, over a Compose service naming all seven.

    The service environment is rendered with the runtime values under their own names, as Compose
    renders it, so only the name tells the caller's value from the repository's own. Returns the
    worker's os.environ afterwards.
    """
    environment = {
        key: value for key, value in worker_environment.items() if key not in controller_lacks
    }
    monkeypatch.setattr(os, "environ", environment)
    rendered = {
        name: environment[f"ALK_RUNTIME_VALUE_{name}"]
        for name in _PROVIDER_NAMES
        if f"ALK_RUNTIME_VALUE_{name}" in environment
    }
    session, _ = _compose_session(tmp_path, monkeypatch, rendered, label)
    provision.activate_voice_environment(session)
    return environment


def _simulator_call_environment(worker_environment, job, tmp_path, name, canary):
    """The environment a local job's call subprocess gets, built as the worker builds it."""
    context = call_runner.CallRunnerContext(
        job=job,
        bundle_dir=tmp_path,
        work_directory=tmp_path / "call-work",
        evidence_seam=None,
        target_provider_secret_values={name: canary},
        attempt_number=1,
    )
    return call_runner.CallRunnerImpl(None, context, environ=dict(worker_environment))._environ


async def _rerun(sandbox, launched, **rerun_fields):
    """Rerun a terminal job that was submitted with no credentials; return the stage environments."""
    job = await _terminal_job(sandbox, source_path=str(sandbox.test_source))
    del sandbox._execute_rerun
    before = len(launched)
    sandbox.rerun(job.job_id, SandboxRerunRequest(**rerun_fields))
    await sandbox._tasks.pop(job.job_id)
    return launched[before:]


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest, SandboxRerunRequest])
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_submit_preflight_and_rerun_accept_the_customers_own_provider_key(
    request_type, route, name, monkeypatch
):
    fields = _provider_fields(route, name, monkeypatch, "canary-accepted")
    extra = {} if request_type is SandboxRerunRequest else {"source_path": "/tmp/agent"}

    request = request_type(**extra, **fields)

    assert name in {**request.secret_refs, **request.environment_values}


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_the_customers_own_provider_key_reaches_the_agent_and_the_controller_only_by_the_spec(
    local_sandbox, launched_workers, monkeypatch, tmp_path, route, name
):
    canary = _canary(name)
    monkeypatch.delenv(name, raising=False)
    fields = _provider_fields(route, name, monkeypatch, canary)
    expected_aliases = name if route == "target_provider_ref" else ""

    # Submit and preflight, on the real request path; the worker is the real _execute's.
    job = _run_local_job(local_sandbox, **fields)
    local_sandbox.preflight(
        SandboxPreflightRequest(source_path=str(local_sandbox.test_source), **fields)
    )
    assert _state(local_sandbox, job)["stage"] == "completed"
    submit_worker = _only_worker_env(launched_workers)

    # A rerun of another job that had no credentials at submit: the alias arrives only now.
    rerun_workers = asyncio.run(_rerun(local_sandbox, launched_workers, **fields))
    monkeypatch.delenv("MY_PROVIDER_SOURCE", raising=False)

    # The controller process (this one) never holds it, after submit, preflight and rerun.
    _assert_no_holder(dict(os.environ), name, canary, "the server process")
    # The worker holds it in the runtime namespace, and under its name only for exception 1a, and
    # it is told which names the caller sent as target aliases (defined, even when empty).
    assert len(rerun_workers) == 3
    for where, worker in [("submit", submit_worker)] + [("rerun", w) for w in rerun_workers]:
        _assert_holders(worker, name, canary, route, where)
        assert worker[_ALIASES_ENV] == expected_aliases, where
        assert name in worker["ALK_RUNTIME_CONFIGURATION_NAMES"].split(","), where

    # The authoring session inherits the worker's environment with provider_env() over it, so it
    # holds the canary in the runtime namespace (ALK_RUNTIME_VALUE_<name>, the known limit) and,
    # for exception 1a, under its own name; _assert_holders pins exactly that set, so the value
    # is under no other name (a name check would be wrong: config.py sets ANTHROPIC_AUTH_TOKEN
    # and ANTHROPIC_BASE_URL itself).
    authoring = {**submit_worker, **config.provider_env()}
    _assert_holders(authoring, name, canary, route, "authoring")

    # The simulator's call environment has the customer's value under its own name only by 1a or 1b.
    call_environment = _simulator_call_environment(submit_worker, job, tmp_path, name, canary)
    assert (name in call_environment) == (
        _reaches_controller(route, name) or name in _LOCAL_SIMULATOR_FALLBACK_NAMES
    )

    # Activation, with a Compose service that references the name. For 1a the controller is made
    # to lack the name first, so that activation, not the promotion, is what puts it there.
    activated = _activate(
        submit_worker,
        tmp_path,
        monkeypatch,
        controller_lacks={name} if _reaches_controller(route, name) else (),
    )
    _assert_holders(activated, name, canary, route, "activation")

    # The agent has it under its own name: a stub process spawned through the provider's loader,
    # and the Compose runtime's `--env NAME` and docker's own environment.
    process_environment = _agent_process_environment(submit_worker, job, tmp_path, monkeypatch)
    assert process_environment[name] == canary
    argv, docker_environment = _agent_runtime_command(submit_worker, tmp_path, monkeypatch)
    assert docker_environment[name] == canary
    assert any(argv[i : i + 2] == ["--env", name] for i in range(len(argv) - 1))
    assert canary not in " ".join(argv)
    # Neither agent start gave the controller more than it had.
    assert dict(os.environ) == submit_worker

    # The same for the rerun's own workers: an alias sent only on the rerun reaches the agent
    # (activation does not copy it into the controller, Compose hands it to the agent by name).
    for index, rerun_worker in enumerate(rerun_workers):
        activated = _activate(
            rerun_worker,
            tmp_path,
            monkeypatch,
            controller_lacks={name} if _reaches_controller(route, name) else (),
            label=f"rerun-activate-{index}",
        )
        _assert_holders(activated, name, canary, route, f"rerun activation {index}")
        argv, docker_environment = _agent_runtime_command(
            rerun_worker, tmp_path, monkeypatch, label=f"rerun-start-{index}"
        )
        assert docker_environment[name] == canary, index
        assert any(argv[i : i + 2] == ["--env", name] for i in range(len(argv) - 1)), index
        assert canary not in " ".join(argv), index
        assert dict(os.environ) == rerun_worker, index


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "GEMINI_API_KEY"])
def test_an_agent_config_key_with_a_connection_name_reaches_the_controller_only(
    local_sandbox, launched_workers, monkeypatch, tmp_path, name
):
    canary = _canary(name)

    job = _run_local_job(local_sandbox, connector_config={name: canary})

    worker = _only_worker_env(launched_workers)
    assert worker[name] == canary
    agent = _agent_process_environment(worker, job, tmp_path, monkeypatch)
    assert name not in agent
    assert canary not in agent.values()


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "GEMINI_API_KEY"])
def test_a_caller_cannot_make_its_target_alias_an_environment_value_through_metadata(
    local_sandbox, launched_workers, monkeypatch, name
):
    canary = _canary(name)
    monkeypatch.setenv("MY_PROVIDER_SOURCE", canary)

    _run_local_job(
        local_sandbox,
        secret_refs={name: _ref("environment", "MY_PROVIDER_SOURCE")},
        metadata={"environment_value_names": [name], "secret_file_names": []},
    )

    worker = _only_worker_env(launched_workers)
    assert name not in worker
    assert worker[_ALIASES_ENV] == name


def test_the_target_alias_variable_is_defined_when_there_are_none(
    local_sandbox, launched_workers
):
    _run_local_job(local_sandbox)
    rerun_workers = asyncio.run(_rerun(local_sandbox, launched_workers))

    assert _only_worker_env(launched_workers[:1])[_ALIASES_ENV] == ""
    assert [worker[_ALIASES_ENV] for worker in rerun_workers] == ["", "", ""]


@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("name", _PROVIDER_NAMES)
def test_a_rerun_rebuilds_the_target_aliases_from_its_own_refs(
    local_sandbox, launched_workers, monkeypatch, route, name
):
    # The submit sent the name as a target alias; the rerun sends it as an environment value
    # (and the other way round). Each run's set is its own request's.
    canary = _canary(name)
    submitted = "environment_value" if route == "target_provider_ref" else "target_provider_ref"
    monkeypatch.delenv(name, raising=False)

    async def run():
        fields = _provider_fields(submitted, name, monkeypatch, "submit-" + canary)
        job = await _terminal_job(local_sandbox, source_path=str(local_sandbox.test_source), **fields)
        del local_sandbox._execute_rerun
        fields = _provider_fields(route, name, monkeypatch, canary)
        local_sandbox.rerun(job.job_id, SandboxRerunRequest(**fields))
        await local_sandbox._tasks.pop(job.job_id)

    asyncio.run(run())

    for worker in launched_workers:
        _assert_holders(worker, name, canary, route, "rerun")
        assert worker[_ALIASES_ENV] == (name if route == "target_provider_ref" else "")


def test_a_rerun_with_no_held_alias_set_treats_every_held_name_as_the_agents(local_sandbox):
    # Not reachable today (rerun() sets the entry first); if it ever were, the submit's set
    # (empty here) must not stand in for the rerun's, or a name sent only on the rerun is promoted.
    async def run():
        job = await _terminal_job(local_sandbox, source_path=str(local_sandbox.test_source))
        assert sandbox_server._job_target_aliases(job) == frozenset()
        local_sandbox._ephemeral_secrets[job.job_id] = {
            "OPENAI_API_KEY": "canary-a",
            "GEMINI_API_KEY": "canary-b",
        }
        local_sandbox._ephemeral_target_aliases.pop(job.job_id, None)
        return job

    job = asyncio.run(run())

    assert local_sandbox._rerun_target_aliases(job) == {"OPENAI_API_KEY", "GEMINI_API_KEY"}
    local_sandbox._ephemeral_target_aliases[job.job_id] = frozenset({"ONLY_THIS"})
    assert local_sandbox._rerun_target_aliases(job) == {"ONLY_THIS"}


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest, SandboxRerunRequest])
@pytest.mark.parametrize("manager", ["environment", "mounted", "futureagi"])
@pytest.mark.parametrize(
    "name",
    [
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "GOOGLE_API_KEY",
        "OPENAI_BASE_URL",
    ],
)
def test_these_provider_names_as_a_controller_alias_are_still_refused(request_type, manager, name):
    extra = {} if request_type is SandboxRerunRequest else {"source_path": "/tmp/agent"}
    with pytest.raises(ValidationError, match=f"secret_alias_reserved: {name}"):
        request_type(**extra, secret_refs={name: _controller_ref(manager, "SOME_NEUTRAL_KEY")})


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest, SandboxRerunRequest])
@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "GEMINI_API_KEY"])
def test_the_two_connection_names_stay_legal_controller_aliases(request_type, name):
    extra = {} if request_type is SandboxRerunRequest else {"source_path": "/tmp/agent"}

    request = request_type(**extra, secret_refs={name: _controller_ref("environment", "SOME_NEUTRAL_KEY")})

    assert name in request.secret_refs


@pytest.mark.parametrize("token", [None, "server-token-canary"])
@pytest.mark.parametrize("alias", ["X", "ANTHROPIC_API_KEY"])
@pytest.mark.parametrize(
    "key", ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "GOOGLE_API_KEY"]
)
def test_a_target_provider_ref_cannot_read_the_servers_own_provider_key(
    local_sandbox, launched_workers, monkeypatch, token, alias, key
):
    if token:
        monkeypatch.setenv(_TOKEN, token)
    monkeypatch.setenv(key, "runner-canary")

    job = _run_local_job(local_sandbox, refs={alias: _ref("environment", key)})

    _assert_failed_before_launch(
        local_sandbox, job.job_id, launched_workers, f"secret_reference_reserved: {alias}"
    )


@pytest.mark.parametrize("key", ["OPENAI_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL"])
def test_development_mode_lets_a_ref_read_these_server_values(
    local_sandbox, launched_workers, monkeypatch, key
):
    # Deliberate: dev mode reads the server's environment for a name rule 4 does not reserve.
    monkeypatch.setenv(key, "server-canary")

    job = _run_local_job(local_sandbox, refs={"X": _ref("environment", key)})

    assert _state(local_sandbox, job)["stage"] == "completed"
    assert _only_worker_env(launched_workers)["ALK_RUNTIME_VALUE_X"] == "server-canary"


_ALIAS_NAMES_REFUSED_FOR_A_TARGET_REF = [
    "ALK_SANDBOX_TOKEN",
    "ALK_RUNTIME_VALUE_ANTHROPIC_API_KEY",
    "ALK_RUNTIME_CONFIGURATION_NAMES",
    "ALK_TARGET_PROVIDER_ALIASES",
    *sorted(sandbox_server._RUNNER_RESERVED_ENVIRONMENT),
    "PATH",
    "DOCKER_HOST",
    "LD_PRELOAD",
    "HTTPS_PROXY",
    "no_proxy",
    "NODE_OPTIONS",
    "SSL_CERT_FILE",
    "PYTHONPATH",
]


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest, SandboxRerunRequest])
@pytest.mark.parametrize("alias", _ALIAS_NAMES_REFUSED_FOR_A_TARGET_REF)
def test_a_caller_sent_target_provider_alias_cannot_be_an_alk_runner_or_client_name(
    request_type, alias
):
    extra = {} if request_type is SandboxRerunRequest else {"source_path": "/tmp/agent"}
    with pytest.raises(ValidationError, match=f"secret_alias_reserved: {alias}"):
        request_type(**extra, secret_refs={alias: _ref("environment", "MY_KEY")})


_CLIENT_NAMES_AN_UPLOAD_CANNOT_TAKE = ["LD_PRELOAD", "DOCKER_HOST", "PATH", "https_proxy", "NODE_OPTIONS"]


@pytest.mark.parametrize("name", _CLIENT_NAMES_AN_UPLOAD_CANNOT_TAKE)
def test_an_upload_cannot_be_named_for_a_client_name(tmp_path, name):
    sandbox = LocalSandbox(
        tmp_path / "state",
        upload_root=tmp_path / "uploads",
        secret_file_root=tmp_path / "secret-files",
    )

    with pytest.raises(HTTPException) as refused:
        asyncio.run(_upload_credential(sandbox, name))

    assert refused.value.status_code == 400
    assert refused.value.detail == "environment_name is reserved"
    assert not any(path.is_file() for path in (tmp_path / "secret-files").rglob("*"))


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest, SandboxRerunRequest])
@pytest.mark.parametrize("alias", _CLIENT_NAMES_AN_UPLOAD_CANNOT_TAKE)
def test_an_upload_ref_aliased_to_a_client_name_is_refused(request_type, alias):
    extra = {} if request_type is SandboxRerunRequest else {"source_path": "/tmp/agent"}
    with pytest.raises(ValidationError, match=f"secret_alias_reserved: {alias}"):
        request_type(
            **extra,
            secret_refs={alias: _ref("mounted", "ALK_SECRET_FILE_0123456789ABCDEF_ABC123")},
        )


def test_an_ordinary_upload_alias_still_uploads_and_validates(tmp_path):
    sandbox = LocalSandbox(
        tmp_path / "state",
        upload_root=tmp_path / "uploads",
        secret_file_root=tmp_path / "secret-files",
    )

    key = asyncio.run(_upload_credential(sandbox, "GOOGLE_APPLICATION_CREDENTIALS", b"{\"a\": 1}"))

    LocalSandboxRequest(
        source_path="/tmp/agent",
        secret_refs={"GOOGLE_APPLICATION_CREDENTIALS": _ref("mounted", key)},
    )


@pytest.mark.parametrize("request_type", [LocalSandboxRequest, SandboxPreflightRequest, SandboxRerunRequest])
@pytest.mark.parametrize(
    "name", ["HTTPS_PROXY", "NO_PROXY", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "LANG", "LC_ALL"]
)
def test_a_client_name_is_accepted_as_an_environment_value(request_type, name):
    extra = {} if request_type is SandboxRerunRequest else {"source_path": "/tmp/agent"}

    request = request_type(**extra, environment_values={name: "canary-client"})

    assert name in request.environment_values


def test_a_target_provider_ref_under_a_neutral_alias_reaches_only_the_runtime_namespace(
    local_sandbox, launched_workers, monkeypatch
):
    # A neutral alias is the name that would show a target ref kept as a controller variable.
    canary = "canary-neutral-agent-key-1"
    monkeypatch.setenv("MY_AGENT_SOURCE", canary)

    job = _run_local_job(
        local_sandbox, secret_refs={"MY_AGENT_KEY": _ref("environment", "MY_AGENT_SOURCE")}
    )

    worker = _only_worker_env(launched_workers)
    assert _state(local_sandbox, job)["stage"] == "completed"
    assert "MY_AGENT_KEY" not in worker
    assert [key for key, value in worker.items() if value == canary] == [
        "ALK_RUNTIME_VALUE_MY_AGENT_KEY"
    ]
    assert worker[_ALIASES_ENV] == "MY_AGENT_KEY"


@pytest.mark.parametrize("name", ["fi_api_key", "harness_platform_url", "Alk_X"])
def test_a_runner_name_is_refused_in_any_case_as_a_target_alias(name):
    with pytest.raises(ValidationError, match=f"secret_alias_reserved: {name}"):
        LocalSandboxRequest(
            source_path="/tmp/agent", secret_refs={name: _ref("environment", "MY_SOURCE")}
        )


@pytest.mark.parametrize("name", ["fi_api_key", "harness_platform_url"])
def test_a_runner_name_is_refused_in_any_case_for_an_upload(tmp_path, name):
    from fastapi import HTTPException

    sandbox = LocalSandbox(tmp_path / "state")
    with pytest.raises(HTTPException, match="environment_name is reserved"):
        asyncio.run(_upload_credential(sandbox, name))


def test_the_agent_runtime_is_started_without_publishing_its_service_ports(
    tmp_path, monkeypatch
):
    # A job value can stand in for a port variable at the agent runtime's start; that's safe only
    # while the run publishes no service ports.
    command, _environment = _agent_runtime_command({}, tmp_path, monkeypatch, label="ports")
    assert "--no-deps" in command
    assert "-P" not in command and "--service-ports" not in command


def test_a_tool_credential_reaches_the_agent_runtime_env_list(tmp_path, monkeypatch):
    # GIT_TOKEN is the agent's own setting, so it is named in `--env` like MY_KEY; a reserved
    # name that is not a tool credential (DOCKER_CONFIG) stays out.
    worker = {
        "ALK_RUNTIME_CONFIGURATION_NAMES": "GIT_TOKEN,MY_KEY,DOCKER_CONFIG",
        "ALK_RUNTIME_VALUE_GIT_TOKEN": "git-canary",
        "ALK_RUNTIME_VALUE_MY_KEY": "key-canary",
        "ALK_RUNTIME_VALUE_DOCKER_CONFIG": "/steer",
    }
    argv, _environment = _agent_runtime_command(worker, tmp_path, monkeypatch, label="toolcred")
    pairs = [argv[i : i + 2] for i in range(len(argv) - 1)]
    assert ["--env", "GIT_TOKEN"] in pairs
    assert ["--env", "MY_KEY"] in pairs
    assert ["--env", "DOCKER_CONFIG"] not in pairs
    assert "git-canary" not in " ".join(argv)


@pytest.mark.parametrize("name", ["GIT_TOKEN", "DOCKER_PASSWORD", "SSH_PRIVATE_KEY"])
def test_a_tool_credential_is_accepted_as_a_target_ref(name):
    # How the platform's create sends every stored value; the same rule as environment values.
    LocalSandboxRequest(source_path="/tmp/agent", secret_refs={name: _ref("platform-vault", "k")})


def test_a_refused_target_alias_says_why():
    with pytest.raises(ValidationError, match="secret_alias_reserved: DOCKER_CONFIG.*another name"):
        LocalSandboxRequest(
            source_path="/tmp/agent", secret_refs={"DOCKER_CONFIG": _ref("platform-vault", "k")}
        )


# A cancelled or interrupted job: one owner stops its worker and its environment (TH-9519).


def _local_source(tmp_path, monkeypatch):
    source = tmp_path / "sources" / "agent"
    source.mkdir(parents=True)
    (source / "agent.py").write_text("print('ready')\n")
    monkeypatch.setenv("ALK_SANDBOX_SOURCE_ROOTS", str(tmp_path / "sources"))
    return source


@pytest.fixture
def stops(monkeypatch):
    calls = []

    def record(output):
        calls.append(output)
        return False

    monkeypatch.setattr(sandbox_server, "stop", record)
    return calls


async def _until(check, seconds=10.0):
    deadline = asyncio.get_running_loop().time() + seconds
    while not check():
        assert asyncio.get_running_loop().time() < deadline, "timed out waiting"
        await asyncio.sleep(0.02)


def _running(fake_worker, count=1):
    return len(fake_worker.children) >= count and fake_worker.children[-1].poll() is None


def _group_gone(pgid, seconds=2.0):
    # A reparented member is reaped a moment after it dies, so this polls.
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            pass
        time.sleep(0.02)
    return False


def _stage(sandbox, job_id):
    return sandbox.get(job_id).status.stage


def test_a_loop_closing_during_a_worker_start_does_not_hang(
    tmp_path, monkeypatch, fake_worker, stops
):
    import threading

    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    # The loop stops in the same tick as the start, after the real Popen returned: the moment
    # a closing loop used to catch asyncio's own start half done.
    fake_worker.sleeping().on_start(lambda _child: asyncio.get_running_loop().stop())

    async def submit():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        await sandbox._tasks[response.job.job_id]

    runner = asyncio.Runner()
    with pytest.raises(RuntimeError, match="Event loop stopped"):
        runner.run(submit())
    closing = threading.Thread(target=runner.close, daemon=True)
    closing.start()
    closing.join(20)

    assert not closing.is_alive(), "closing the loop hung"
    assert fake_worker.children[0].poll() is not None
    assert len(stops) == 1


_GRANDCHILD = (
    "import os, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
    "time.sleep(30)\n"
)


def _leader(pid_file, *, ignores):
    script = (
        "import signal, subprocess, sys, time\n"
        + ("signal.signal(signal.SIGTERM, signal.SIG_IGN)\n" if ignores else "")
        + f"subprocess.Popen([sys.executable, '-c', {_GRANDCHILD!r}, {str(pid_file)!r}])\n"
        + "time.sleep(30)\n"
    )
    return [sys.executable, "-c", script]


@pytest.mark.parametrize("shape", ["leader-dies", "both-ignore"])
def test_a_stopped_worker_takes_its_whole_group_with_it(
    tmp_path, monkeypatch, fake_worker, stops, shape
):
    monkeypatch.setattr(sandbox_server, "_STOP_GRACE_SECONDS", 0.5)
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    pid_file = tmp_path / "grandchild.pid"
    fake_worker.child(_leader(pid_file, ignores=shape == "both-ignore"))

    async def scenario():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        task = sandbox._tasks[response.job.job_id]
        await _until(lambda: pid_file.exists() and pid_file.read_text())
        task.cancel()
        await asyncio.wait({task}, timeout=5)
        assert task.done()
        return response.job.job_id

    job_id = asyncio.run(scenario())

    assert _group_gone(fake_worker.children[0].pid)
    assert len(stops) == 1
    assert _stage(sandbox, job_id).terminal


def test_a_single_worker_stops_well_inside_its_grace(tmp_path, monkeypatch, fake_worker, stops):
    import time

    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping()

    async def scenario():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        task = sandbox._tasks[response.job.job_id]
        await _until(lambda: _running(fake_worker))
        started = time.monotonic()
        task.cancel()
        await asyncio.wait({task}, timeout=15)
        return time.monotonic() - started

    took = asyncio.run(scenario())

    # The grace is 10 s; a leader that exits at once must not wait for it.
    assert took < 2, took


@pytest.mark.parametrize("route", ["execute", "rerun"])
def test_a_second_cancel_does_not_cut_the_cleanup_short(
    tmp_path, monkeypatch, fake_worker, stops, route
):
    monkeypatch.setattr(sandbox_server, "_STOP_GRACE_SECONDS", 0.5)
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    pid_file = tmp_path / "grandchild.pid"
    fake_worker.child(_leader(pid_file, ignores=True))

    async def scenario():
        if route == "rerun":
            job = await _start_rerun(sandbox, source)
        else:
            job = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job
        task = sandbox._tasks[job.job_id]
        await _until(lambda: pid_file.exists() and pid_file.read_text())
        # As cancel() does: the job is marked canceled first, so its cleanup takes the ordered
        # path (the worker's grace, then stop), which a second cancel could cut short.
        state_path = sandbox.jobs_root / job.job_id / "state.json"
        state = json.loads(state_path.read_text())
        state.update(stage="canceled")
        state_path.write_text(json.dumps(state))
        task.cancel()
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.wait({task}, timeout=5)
        return job.job_id

    job_id = asyncio.run(scenario())

    assert len(stops) == 1
    assert _stage(sandbox, job_id).terminal


def test_cancel_owns_nothing_but_the_state(tmp_path, monkeypatch, fake_worker, stops):
    import time

    source = _local_source(tmp_path, monkeypatch)
    fake_worker.sleeping()
    app = create_app(tmp_path / "state")
    # One client, one loop, kept alive across requests: a plain client closes its loop after each
    # request, which cancels the job before /cancel arrives.
    with TestClient(app) as client:
        job_id = client.post("/v1/jobs", json={"source_path": str(source)}).json()["job"][
            "job_id"
        ]
        deadline = time.monotonic() + 10
        while not _running(fake_worker):
            assert time.monotonic() < deadline
            time.sleep(0.02)

        response = client.post(f"/v1/jobs/{job_id}/cancel")

        assert response.status_code == 200
        assert len(stops) == 1
        assert _stage(app.state.sandbox, job_id).value == "canceled"


async def _start_rerun(sandbox, source):
    """A finished job's rerun, started the way the server does: returns the job."""
    sandbox._execute = _idle_execute
    response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
    await sandbox._tasks.pop(response.job.job_id)
    del sandbox._execute
    job = response.job
    (sandbox.artifacts_root / job.run_id / "environment-bundle").mkdir(parents=True)
    state_path = sandbox.jobs_root / job.job_id / "state.json"
    state = json.loads(state_path.read_text())
    state.update(stage="queued")
    state_path.write_text(json.dumps(state))
    sandbox._tasks[job.job_id] = asyncio.create_task(sandbox._execute_rerun(job, []))
    return job


# The stage the cancel lands in: how many launches it takes, and the launch that sleeps.
_RERUN_STAGES = {"environment up": (1, 1), "simulate": (2, 2), "environment down": (3, 3)}


@pytest.mark.parametrize("stage", list(_RERUN_STAGES))
def test_a_cancelled_rerun_starts_no_further_stage(
    tmp_path, monkeypatch, fake_worker, stops, stage
):
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    launches, sleeper = _RERUN_STAGES[stage]
    died = []

    def sleeps_in_its_stage(_child):
        # The stages before the cancelled one exit at once; the next launch is the sleeper.
        if len(fake_worker.launches) == sleeper - 1:
            fake_worker.sleeping()

    if sleeper == 1:
        fake_worker.sleeping()
    fake_worker.on_start(sleeps_in_its_stage)

    async def scenario():
        job = await _start_rerun(sandbox, source)
        await _until(lambda: _running(fake_worker, launches))
        await sandbox.cancel(job.job_id)
        # Before the guard's teardown, which would kill a leaked child and hide it.
        died.append(fake_worker.children[-1].poll() is not None)
        return job.job_id

    job_id = asyncio.run(scenario())

    assert died == [True], "the running stage was left alive after the cancel"
    assert len(fake_worker.launches) == launches
    assert len(stops) == 1
    assert _stage(sandbox, job_id).value == "canceled"


def test_a_loop_closing_during_a_rerun_start_does_not_hang(tmp_path, monkeypatch, fake_worker, stops):
    import threading

    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping().on_start(lambda _child: asyncio.get_running_loop().stop())

    async def rerun():
        job = await _start_rerun(sandbox, source)
        await sandbox._tasks[job.job_id]

    runner = asyncio.Runner()
    with pytest.raises(RuntimeError, match="Event loop stopped"):
        runner.run(rerun())
    closing = threading.Thread(target=runner.close, daemon=True)
    closing.start()
    closing.join(20)

    assert not closing.is_alive(), "closing the loop hung"
    assert fake_worker.children[0].poll() is not None
    assert len(stops) == 1


def test_cancel_stops_the_environment_of_a_job_with_no_running_task(
    tmp_path, monkeypatch, stops
):
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")

    async def scenario():
        sandbox._execute = _idle_execute
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        task = sandbox._tasks[response.job.job_id]
        await task
        assert task.done()
        await sandbox.cancel(response.job.job_id)
        return response.job.job_id

    job_id = asyncio.run(scenario())

    assert len(stops) == 1
    assert _stage(sandbox, job_id).value == "canceled"


def test_cancel_survives_a_group_it_may_not_signal(tmp_path, monkeypatch, fake_worker, stops):
    monkeypatch.setattr(sandbox_server, "_STOP_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(sandbox_server, "_REAP_SECONDS", 0.1)
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping()

    def refuse(_pgid, _sig):
        raise PermissionError

    async def scenario():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        await _until(lambda: _running(fake_worker))
        monkeypatch.setattr(os, "killpg", refuse)
        await sandbox.cancel(response.job.job_id)
        return response.job.job_id

    job_id = asyncio.run(scenario())

    # The guard's teardown kills the child with the real killpg.
    assert len(stops) == 1
    assert _stage(sandbox, job_id).value == "canceled"


def test_stopping_a_group_it_may_not_signal_returns(monkeypatch, caplog):
    # The cleanup logs _stop_worker's errors and goes on, so only a direct call can show one.
    real_killpg = os.killpg
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    monkeypatch.setattr(sandbox_server, "_STOP_GRACE_SECONDS", 0.1)
    monkeypatch.setattr(sandbox_server, "_REAP_SECONDS", 0.1)

    def refuse(_pgid, _sig):
        raise PermissionError

    monkeypatch.setattr(os, "killpg", refuse)
    try:
        with caplog.at_level("WARNING", logger=sandbox_server.__name__):
            sandbox_server._stop_worker(child)
    finally:
        real_killpg(child.pid, signal.SIGKILL)
        child.wait()

    assert [record.getMessage() for record in caplog.records] == [
        f"sandbox worker {child.pid} not reaped 0.1s after SIGKILL"
    ]


def test_stopping_a_reaped_worker_signals_no_group(monkeypatch):
    # A reaped leader's pid may belong to someone else by now.
    child = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    child.wait()
    calls = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))

    sandbox_server._stop_worker(child)

    assert calls == []


def test_a_group_seen_gone_is_not_killed_afterwards(monkeypatch):
    calls = []
    real_killpg = os.killpg

    def record(pgid, sig):
        calls.append(sig)
        if sig == 0:
            # The group is seen gone. Real timing can't promise this answer: on macOS a probe
            # that lands as the leader dies sees a zombie instead (PermissionError), and the
            # SIGKILL that follows is then right.
            raise ProcessLookupError
        real_killpg(pgid, sig)

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    monkeypatch.setattr(sandbox_server, "_STOP_GRACE_SECONDS", 2.0)
    monkeypatch.setattr(os, "killpg", record)
    try:
        sandbox_server._stop_worker(child)
    finally:
        if child.poll() is None:
            real_killpg(child.pid, signal.SIGKILL)
        child.wait()

    # SIGTERM, then the probe that saw the group gone; nothing is sent after it.
    assert calls == [signal.SIGTERM, 0]


def test_a_group_that_may_not_be_probed_is_killed_afterwards(monkeypatch):
    calls = []
    real_killpg = os.killpg

    def record(pgid, sig):
        calls.append(sig)
        if sig == 0:
            # A zombie leader (macOS) answers the probe with PermissionError: the group is
            # not known to be gone, so the SIGKILL that follows is the designed path.
            raise PermissionError
        real_killpg(pgid, sig)

    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    monkeypatch.setattr(sandbox_server, "_STOP_GRACE_SECONDS", 2.0)
    monkeypatch.setattr(os, "killpg", record)
    try:
        sandbox_server._stop_worker(child)
    finally:
        if child.poll() is None:
            real_killpg(child.pid, signal.SIGKILL)
        child.wait()

    assert calls == [signal.SIGTERM, 0, signal.SIGKILL]


def test_a_cancel_before_the_start_still_cleans_up(tmp_path, monkeypatch, stops):
    import threading

    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    walking = threading.Event()
    release = threading.Event()

    def blocked(*_args, **_kwargs):
        walking.set()
        release.wait(10)
        raise RuntimeError("released")

    monkeypatch.setattr(sandbox_server, "discover_credentials", blocked)

    async def scenario():
        response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
        task = sandbox._tasks[response.job.job_id]
        await _until(walking.is_set)
        task.cancel()
        await asyncio.wait({task}, timeout=5)
        release.set()
        assert task.done()
        return response.job.job_id

    job_id = asyncio.run(scenario())

    assert len(stops) == 1
    assert _stage(sandbox, job_id).terminal


def test_the_server_stops_its_jobs_before_it_exits(tmp_path, monkeypatch, fake_worker, stops):
    source = _local_source(tmp_path, monkeypatch)
    fake_worker.sleeping()
    app = create_app(tmp_path / "state")
    sandbox = app.state.sandbox

    async def scenario():
        async with app.router.lifespan_context(app):
            response = sandbox.submit(LocalSandboxRequest(source_path=str(source)))
            task = sandbox._tasks[response.job.job_id]
            await _until(lambda: _running(fake_worker))
        # Still inside this loop: only the lifespan can have stopped the job by now.
        assert task.done()
        assert fake_worker.children[0].poll() is not None
        assert len(stops) == 1
        return response.job.job_id

    job_id = asyncio.run(scenario())

    state = json.loads((sandbox.jobs_root / job_id / "state.json").read_text())
    assert state["stage"] == "failed"
    assert state["failure"]["code"] == "service_stopped"


@pytest.mark.parametrize("how", ["canceled", "stopped"])
def test_a_failed_environment_cleanup_is_kept_in_the_detail(
    tmp_path, monkeypatch, fake_worker, how
):
    def fails(_output):
        raise sandbox_server.ProvisionError("compose down failed")

    monkeypatch.setattr(sandbox_server, "stop", fails)
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping()

    async def scenario():
        job_id = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        task = sandbox._tasks[job_id]
        await _until(lambda: _running(fake_worker))
        if how == "canceled":
            await sandbox.cancel(job_id)
        else:
            task.cancel()
        await asyncio.wait({task}, timeout=10)
        return job_id

    job_id = asyncio.run(scenario())

    detail = sandbox.get(job_id).status.detail
    assert detail == f"{how}; isolated environment cleanup failed: compose down failed"


def test_an_environment_cleanup_that_hangs_still_ends_the_job(
    tmp_path, monkeypatch, fake_worker
):
    import threading

    monkeypatch.setattr(sandbox_server, "_STOP_OUTPUT_SECONDS", 0.3)
    released = threading.Event()
    monkeypatch.setattr(sandbox_server, "stop", lambda _output: released.wait(10))
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping()

    async def scenario():
        job_id = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        task = sandbox._tasks[job_id]
        await _until(lambda: _running(fake_worker))
        task.cancel()
        await asyncio.wait({task}, timeout=5)
        ended = task.done()
        released.set()
        return ended

    assert asyncio.run(scenario())


def test_cancel_answers_before_the_platforms_timeout_while_cleanup_runs_on(
    tmp_path, monkeypatch, fake_worker
):
    import threading
    import time

    # The platform calls /cancel with a 30 s read timeout; a slow compose down mustn't hold it.
    monkeypatch.setattr(sandbox_server, "_CANCEL_RESPONSE_SECONDS", 0.3)
    released = threading.Event()
    monkeypatch.setattr(sandbox_server, "stop", lambda _output: released.wait(10))
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping()

    async def scenario():
        job_id = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        task = sandbox._tasks[job_id]
        await _until(lambda: _running(fake_worker))
        started = time.monotonic()
        response = await sandbox.cancel(job_id)
        took = time.monotonic() - started
        still_cleaning = not task.done()
        released.set()
        await asyncio.wait({task}, timeout=10)
        return response, took, still_cleaning, job_id

    response, took, still_cleaning, job_id = asyncio.run(scenario())

    assert took < 2, took
    assert still_cleaning
    assert response.status.stage.value == "canceled"
    # The job's own task clears its secrets once its cleanup ends.
    assert job_id not in sandbox._ephemeral_secrets


def _cancel_answered_early(tmp_path, monkeypatch, fake_worker):
    import threading

    monkeypatch.setattr(sandbox_server, "_CANCEL_RESPONSE_SECONDS", 0.3)
    released = threading.Event()
    monkeypatch.setattr(sandbox_server, "stop", lambda _output: released.wait(10))
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping()
    return sandbox, source, released


def test_a_rerun_waits_until_the_cancelled_jobs_cleanup_ends(tmp_path, monkeypatch, fake_worker):
    from fastapi import HTTPException

    sandbox, source, released = _cancel_answered_early(tmp_path, monkeypatch, fake_worker)

    async def scenario():
        job_id = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        old = sandbox._tasks[job_id]
        await _until(lambda: _running(fake_worker))
        answer = await sandbox.cancel(job_id)
        with pytest.raises(HTTPException) as refused:
            sandbox.rerun(job_id, SandboxRerunRequest())
        released.set()
        await asyncio.wait({old}, timeout=10)
        return answer, refused.value

    answer, refused = asyncio.run(scenario())

    assert answer.status.detail == "canceled; environment cleanup still running"
    assert refused.status_code == 409
    assert refused.detail == "harness job is still stopping"


def test_the_old_cleanup_leaves_a_new_runs_secrets_alone(tmp_path, monkeypatch, fake_worker):
    sandbox, source, released = _cancel_answered_early(tmp_path, monkeypatch, fake_worker)

    async def scenario():
        job_id = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        old = sandbox._tasks[job_id]
        await _until(lambda: _running(fake_worker))
        await sandbox.cancel(job_id)
        # A new run of the job holds the job's id by the time the old task's callback runs.
        new = asyncio.get_running_loop().create_future()
        sandbox._tasks[job_id] = new
        sandbox._ephemeral_secrets[job_id] = {"NEW_RUN_KEY": "kept"}
        released.set()
        await asyncio.wait({old}, timeout=10)
        await asyncio.sleep(0)
        new.set_result(None)
        return job_id

    job_id = asyncio.run(scenario())

    assert sandbox._ephemeral_secrets[job_id] == {"NEW_RUN_KEY": "kept"}


def test_a_service_stop_starts_the_environment_cleanup_inside_the_workers_grace(
    tmp_path, monkeypatch, fake_worker
):
    import time

    # docker stop kills the server 10 s after its SIGTERM, the length of the worker's grace, so
    # compose down has to start without waiting for the worker.
    monkeypatch.setattr(sandbox_server, "_STOP_GRACE_SECONDS", 3)
    started_at = []
    monkeypatch.setattr(sandbox_server, "stop", lambda _output: started_at.append(time.monotonic()))
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    pid_file = tmp_path / "grandchild.pid"
    fake_worker.child(_leader(pid_file, ignores=True))

    async def scenario():
        job_id = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        task = sandbox._tasks[job_id]
        await _until(lambda: pid_file.exists() and pid_file.read_text())
        stopped = time.monotonic()
        task.cancel()
        await asyncio.wait({task}, timeout=10)
        return stopped

    stopped = asyncio.run(scenario())

    assert started_at and started_at[0] - stopped < 1.5, started_at


def test_a_rerun_that_lands_as_the_cleanup_ends_keeps_its_secrets(
    tmp_path, monkeypatch, fake_worker
):
    import time

    # The cleanup ends inside cancel()'s wait; a rerun takes the job before cancel() resumes.
    monkeypatch.setattr(sandbox_server, "stop", lambda _output: time.sleep(0.1))
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state")
    fake_worker.sleeping()

    async def scenario():
        job_id = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        old = sandbox._tasks[job_id]
        await _until(lambda: _running(fake_worker))
        new = asyncio.get_running_loop().create_future()

        def rerun_takes_the_job(_done):
            sandbox._tasks[job_id] = new
            sandbox._ephemeral_secrets[job_id] = {"NEW_RUN_KEY": "kept"}

        old.add_done_callback(rerun_takes_the_job)
        await sandbox.cancel(job_id)
        new.set_result(None)
        return job_id

    job_id = asyncio.run(scenario())

    assert sandbox._ephemeral_secrets.get(job_id) == {"NEW_RUN_KEY": "kept"}


def test_a_job_cancelled_while_queued_for_a_slot_loses_its_secrets(
    tmp_path, monkeypatch, fake_worker, stops
):
    # A job queued for the one slot is cancelled before its task reaches its own cleanup.
    source = _local_source(tmp_path, monkeypatch)
    sandbox = LocalSandbox(tmp_path / "state", max_concurrency=1)
    fake_worker.sleeping()

    async def scenario():
        running = sandbox.submit(LocalSandboxRequest(source_path=str(source))).job.job_id
        await _until(lambda: _running(fake_worker))
        queued = sandbox.submit(
            LocalSandboxRequest(
                source_path=str(source), environment_values={"MY_AGENT_KEY": "queued-canary"}
            )
        ).job.job_id
        assert queued in sandbox._ephemeral_secrets
        await sandbox.cancel(queued)
        await sandbox.cancel(running)
        return queued

    queued = asyncio.run(scenario())

    assert queued not in sandbox._ephemeral_secrets


def test_the_server_stops_waiting_for_a_hung_cleanup(tmp_path, monkeypatch, fake_worker):
    import threading
    import time

    monkeypatch.setattr(sandbox_server, "_CANCEL_WAIT_SECONDS", 0.3)
    released = threading.Event()
    monkeypatch.setattr(sandbox_server, "stop", lambda _output: released.wait(10))
    source = _local_source(tmp_path, monkeypatch)
    fake_worker.sleeping()
    app = create_app(tmp_path / "state")
    sandbox = app.state.sandbox

    async def scenario():
        async with app.router.lifespan_context(app):
            sandbox.submit(LocalSandboxRequest(source_path=str(source)))
            await _until(lambda: _running(fake_worker))
            started = time.monotonic()
        took = time.monotonic() - started
        released.set()
        return took

    assert asyncio.run(scenario()) < 3


def test_an_idle_server_shuts_down_cleanly(tmp_path, caplog):
    app = create_app(tmp_path / "state")

    async def scenario():
        async with app.router.lifespan_context(app):
            pass

    with caplog.at_level("WARNING"):
        asyncio.run(scenario())

    assert not [record for record in caplog.records if record.levelname == "ERROR"]


def test_the_guard_swaps_every_worker_start():
    seen = []

    class Started:
        pid = 0

    def recorder(args, *_more, **_kwargs):
        seen.append(args)
        return Started()

    guard = _WorkerGuard(recorder)
    guard.popen([sys.executable, "-m", "fi.alk.harness.sandbox_worker", "job.json"])
    guard.popen("python -m fi.alk.harness.cli simulate", shell=True)
    guard.popen(["docker", "compose", "up"])
    guard.popen([sys.executable, "-m", "fi.alk.harness", "simulate"])
    guard.popen("/usr/bin/env docker compose up", shell=True)
    guard.popen(["timeout", "60", "claude", "-p"])
    guard.popen([sys.executable, "/repo/src/fi/alk/harness/sandbox_worker.py", "job.json"])
    guard.popen(
        [sys.executable, "/venv/lib/python3.12/site-packages/fi/alk/harness/cli.py", "simulate"]
    )
    guard.popen([sys.executable, "/repo/src/fi/alk/harness/run/sdk_voice.py", "case"])
    guard.popen("cd /tmp&&docker compose up", shell=True)
    guard.popen("true;docker compose up", shell=True)
    guard.popen(["worker", "compose", "up"], executable="/usr/bin/docker")
    guard.popen(["git", "status"])

    assert seen == [_HARMLESS] * 12 + [["git", "status"]]
    assert len(guard.unexpected()) == 12


def test_a_test_that_starts_a_worker_unasked_fails(pytester):
    pytester.makepyfile(
        test_inner=(
            "import subprocess, sys\n"
            "from test_harness_sandbox_server import worker_guard  # noqa: F401\n"
            "def test_starts_a_worker():\n"
            "    subprocess.Popen([sys.executable, '-m', 'fi.alk.harness.sandbox_worker']).wait()\n"
        )
    )

    result = pytester.runpytest_inprocess("-p", "no:cacheprovider")

    result.assert_outcomes(passed=1, errors=1)


def test_the_scrub_leaves_no_host_value_for_a_worker(monkeypatch, tmp_path):
    for name in secrets.WORKER_ENVIRONMENT_NAMES:
        monkeypatch.setenv(name, f"fake-{name}")

    scrub_environment(monkeypatch, tmp_path / "scrubbed-home")
    environment = secrets.worker_environment({})

    leaked = {
        name
        for name, value in environment.items()
        if value.startswith("fake-") and name not in _KEPT_NAMES
    }
    assert leaked == set()
    assert environment["HOME"] == str(tmp_path / "scrubbed-home")


def test_the_worker_allow_list_is_the_one_written_here():
    assert secrets.WORKER_ENVIRONMENT_NAMES == {
        "AGENTCC_API_KEY", "AGENTCC_BASE_URL", "ALK_AGENT_MODEL", "ALK_DOCKER_BIND_HOST",
        "ALK_DOCKER_NETWORK", "ALK_DOCKER_PUBLISHED_HOST",
        "ALK_GOOGLE_ADK_EXPLICITLY_APPROVED_BY_USER", "ALK_HARNESS", "ALK_HARNESS_MODEL",
        "ALK_HARNESS_THINKING", "ALK_JUDGE_MODEL", "ALK_RUNNER_CONTAINER", "ALK_USER_MODEL",
        "ALK_VERTEX_LOCATION", "ANTHROPIC_MODEL", "ANTHROPIC_VERTEX_PROJECT_ID",
        "CARTESIA_API_KEY", "CLAUDE_CODE_USE_VERTEX", "CLOUD_ML_REGION", "DOCKER_HOST",
        "FI_API_KEY", "FI_BASE_URL", "FI_SECRET_KEY", "GOOGLE_APPLICATION_CREDENTIALS",
        "GOOGLE_CLOUD_PROJECT", "HARNESS_PLATFORM_API_KEY", "HARNESS_PLATFORM_SECRET_KEY",
        "HARNESS_PLATFORM_URL", "HARNESS_RUNTIME_WEBHOOK_URL", "HARNESS_VOICE_INFRA_RETRIES",
        "HARNESS_WEBHOOK_HOST", "HARNESS_WEBHOOK_PORT", "HARNESS_WEBHOOK_URL", "HOME", "LANG",
        "LC_ALL", "NO_PROXY", "PATH", "PYTHONPATH", "SSL_CERT_DIR", "SSL_CERT_FILE", "TMPDIR",
    }  # fmt: skip
